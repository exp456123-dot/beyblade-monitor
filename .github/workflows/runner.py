import asyncio
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright

from monitor import (
    SHOPEE_STORES,
    search_eslite,
    search_momo_funbox,
    search_shopee_store,
)


# ============================================================
# 檔案設定
# ============================================================

STATE_FILE = Path("seen_products.json")
NOTIFICATION_FILE = Path("notification.md")
RESULT_FILE = Path("latest_products.json")
SUMMARY_FILE = Path("run_summary.txt")

TIME_ZONE = ZoneInfo("Asia/Taipei")

# 第一次執行時，只建立目前商品基準，不通知。
# 這可以避免第一次執行就把所有舊商品都建立成 Issue。
NOTIFY_ON_FIRST_RUN = False

# 已消失的商品保留幾天紀錄。
# 保留紀錄可避免舊商品短暫下架後再次出現時重複通知。
KEEP_HISTORY_DAYS = 180


# ============================================================
# 共用功能
# ============================================================

def taipei_now():
    return datetime.now(TIME_ZONE)


def clean_text(value):
    if value is None:
        return ""

    return " ".join(str(value).split()).strip()


def normalize_product_url(url):
    """
    清除不影響商品身分的追蹤參數。

    momo 的 i_code 必須保留。
    蝦皮網址通常利用 shopid、itemid 或網址本身辨識商品。
    """
    if not url:
        return ""

    url = url.strip()

    try:
        parts = urlsplit(url)

        query_items = parse_qsl(
            parts.query,
            keep_blank_values=True,
        )

        keep_parameters = {
            "i_code",
            "goodsCode",
            "goods_code",
            "itemid",
            "shopid",
        }

        filtered_query = [
            (key, value)
            for key, value in query_items
            if key in keep_parameters
        ]

        normalized_query = urlencode(
            filtered_query
        )

        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path.rstrip("/"),
                normalized_query,
                "",
            )
        )

    except Exception:
        return url.split("#")[0].strip()


def make_product_id(product):
    """
    以賣場和標準化網址建立穩定商品 ID。
    只有網址相同才會視為同一件商品。
    """
    store = clean_text(
        product.get("store")
    )

    url = normalize_product_url(
        product.get("url", "")
    )

    identity = f"{store}|{url}"

    return hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()


def normalize_product(product):
    price = product.get("price")

    try:
        if price is not None:
            price = int(price)
    except (ValueError, TypeError):
        price = None

    return {
        "id": make_product_id(product),
        "store": clean_text(
            product.get("store")
        ),
        "name": clean_text(
            product.get("name")
        ),
        "price": price,
        "price_source": clean_text(
            product.get("price_source")
        ),
        "url": normalize_product_url(
            product.get("url", "")
        ),
        "available": bool(
            product.get("available", True)
        ),
    }


def deduplicate_products(products):
    """
    同一個商品可能由不同搜尋關鍵字找到，
    使用商品 ID 移除重複資料。
    """
    unique_products = {}

    for product in products:
        normalized = normalize_product(
            product
        )

        if not normalized["url"]:
            continue

        if not normalized["name"]:
            continue

        product_id = normalized["id"]

        existing = unique_products.get(
            product_id
        )

        if existing is None:
            unique_products[
                product_id
            ] = normalized
            continue

        # 若後來的資料有正確價格，就補上價格。
        if (
            existing.get("price") is None
            and normalized.get("price")
            is not None
        ):
            unique_products[
                product_id
            ] = normalized

    return list(
        unique_products.values()
    )


def load_state():
    if not STATE_FILE.exists():
        return {
            "initialized": False,
            "products": {},
            "last_run": None,
        }

    try:
        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(data, dict):
            raise ValueError(
                "狀態檔案格式錯誤"
            )

        data.setdefault(
            "initialized",
            False,
        )

        data.setdefault(
            "products",
            {},
        )

        data.setdefault(
            "last_run",
            None,
        )

        return data

    except Exception as error:
        print(
            "無法讀取 seen_products.json："
            f"{error}"
        )

        print(
            "為避免大量誤通知，本次視為首次執行。"
        )

        return {
            "initialized": False,
            "products": {},
            "last_run": None,
        }


def save_json(path, data):
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def format_price(price):
    if price is None:
        return "商品頁未能辨識"

    return f"NT${price:,}"


def github_escape(value):
    """
    避免 GitHub Actions 輸出格式受到特殊字元影響。
    """
    return (
        str(value)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def write_github_output(
    has_new_products,
    issue_title,
    scan_failed,
):
    github_output = os.getenv(
        "GITHUB_OUTPUT"
    )

    if not github_output:
        return

    with open(
        github_output,
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            "has_new_products="
            f"{str(has_new_products).lower()}\n"
        )

        file.write(
            "issue_title="
            f"{github_escape(issue_title)}\n"
        )

        file.write(
            "scan_failed="
            f"{str(scan_failed).lower()}\n"
        )


# ============================================================
# 通知內容
# ============================================================

def build_notification(
    new_products,
    scan_time,
):
    lines = [
        "# 戰鬥陀螺新商品開賣通知",
        "",
        (
            f"檢查時間："
            f"{scan_time.strftime('%Y-%m-%d %H:%M:%S')}"
            "（台灣時間）"
        ),
        "",
        (
            f"本次發現 **{len(new_products)} 筆**"
            "新的可購買商品。"
        ),
        "",
    ]

    products_by_store = {}

    for product in new_products:
        store = (
            product.get("store")
            or "未知賣場"
        )

        products_by_store.setdefault(
            store,
            [],
        ).append(product)

    for store_name, store_products in (
        products_by_store.items()
    ):
        lines.extend([
            f"## {store_name}",
            "",
        ])

        for product in store_products:
            name = product["name"]
            price_text = format_price(
                product.get("price")
            )
            price_source = (
                product.get("price_source")
                or "未提供"
            )
            url = product["url"]

            lines.extend([
                f"### {name}",
                "",
                f"- 價格：**{price_text}**",
                "- 狀態：可購買",
                (
                    f"- 價格來源："
                    f"{price_source}"
                ),
                f"- {url}",
                "",
            ])

    lines.extend([
        "---",
        "",
        (
            "這是 GitHub Actions 自動建立的"
            "戰鬥陀螺商品監測通知。"
        ),
        (
            "同一個商品網址原則上只會通知一次。"
        ),
        "",
        (
            "商品價格與庫存可能隨時變動，"
            "請以賣場商品頁顯示為準。"
        ),
    ])

    return "\n".join(lines)


def build_summary(
    all_products,
    new_products,
    scan_time,
    initialized_before,
):
    store_counts = {}

    for product in all_products:
        store = product.get(
            "store",
            "未知賣場",
        )

        store_counts[store] = (
            store_counts.get(store, 0)
            + 1
        )

    lines = [
        "戰鬥陀螺監測執行結果",
        "=" * 40,
        (
            "執行時間："
            f"{scan_time.strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        (
            "執行模式："
            + (
                "正式監測"
                if initialized_before
                else "首次建立基準"
            )
        ),
        (
            f"目前可購買商品："
            f"{len(all_products)} 筆"
        ),
        (
            f"本次新增商品："
            f"{len(new_products)} 筆"
        ),
        "",
        "各賣場數量：",
    ]

    if not store_counts:
        lines.append(
            "- 本次沒有確認到可購買商品"
        )
    else:
        for store, count in sorted(
            store_counts.items()
        ):
            lines.append(
                f"- {store}：{count} 筆"
            )

    return "\n".join(lines)


# ============================================================
# 更新商品歷史
# ============================================================

def update_state(
    old_state,
    current_products,
    scan_time,
):
    initialized_before = bool(
        old_state.get("initialized")
    )

    old_products = old_state.get(
        "products",
        {},
    )

    now_iso = scan_time.isoformat()

    new_products = []

    for product in current_products:
        product_id = product["id"]
        previous = old_products.get(
            product_id
        )

        if previous is None:
            if (
                initialized_before
                or NOTIFY_ON_FIRST_RUN
            ):
                new_products.append(product)

            old_products[product_id] = {
                **product,
                "first_seen": now_iso,
                "last_seen": now_iso,
                "notified": (
                    initialized_before
                    or NOTIFY_ON_FIRST_RUN
                ),
            }

        else:
            first_seen = previous.get(
                "first_seen",
                now_iso,
            )

            notified = bool(
                previous.get(
                    "notified",
                    True,
                )
            )

            old_products[product_id] = {
                **product,
                "first_seen": first_seen,
                "last_seen": now_iso,
                "notified": notified,
            }

    new_state = {
        "initialized": True,
        "last_run": now_iso,
        "products": old_products,
    }

    return (
        new_state,
        new_products,
        initialized_before,
    )


# ============================================================
# 執行所有賣場搜尋
# ============================================================

async def scan_all_stores():
    all_products = []
    failed_stores = []

    async with async_playwright() as playwright:
        browser = (
            await playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features="
                    "AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
        )

        try:
            try:
                momo_products = (
                    await search_momo_funbox(
                        browser
                    )
                )

                all_products.extend(
                    momo_products
                )

            except Exception as error:
                failed_stores.append(
                    "momo Funbox"
                )

                print(
                    "momo Funbox 執行失敗："
                    f"{error}"
                )

            try:
                eslite_products = (
                    await search_eslite(
                        browser
                    )
                )

                all_products.extend(
                    eslite_products
                )

            except Exception as error:
                failed_stores.append(
                    "誠品線上"
                )

                print(
                    "誠品線上執行失敗："
                    f"{error}"
                )

            for store in SHOPEE_STORES:
                store_name = store["store"]

                try:
                    shopee_products = (
                        await search_shopee_store(
                            browser,
                            store,
                        )
                    )

                    all_products.extend(
                        shopee_products
                    )

                except Exception as error:
                    failed_stores.append(
                        store_name
                    )

                    print(
                        f"{store_name} 執行失敗："
                        f"{error}"
                    )

        finally:
            await browser.close()

    return (
        deduplicate_products(
            all_products
        ),
        failed_stores,
    )


# ============================================================
# 主程式
# ============================================================

async def main():
    scan_time = taipei_now()

    print("=" * 76)
    print("戰鬥陀螺持續監測器")
    print(
        "台灣時間："
        f"{scan_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    print("=" * 76)

    # 清除上次的通知檔案。
    # 若本次沒有新商品，就不應保留舊通知。
    if NOTIFICATION_FILE.exists():
        NOTIFICATION_FILE.unlink()

    old_state = load_state()

    current_products, failed_stores = (
        await scan_all_stores()
    )

    print()
    print(
        "本次確認到 "
        f"{len(current_products)} 筆"
        "可購買商品。"
    )

    if failed_stores:
        print(
            "本次搜尋失敗的賣場："
            + "、".join(failed_stores)
        )

    # 如果所有賣場都失敗，不更新狀態，
    # 避免錯誤資料覆蓋原本商品歷史。
    if len(failed_stores) == (
        2 + len(SHOPEE_STORES)
    ):
        message = (
            "所有賣場搜尋皆失敗，"
            "本次不更新商品資料。"
        )

        print(message)

        SUMMARY_FILE.write_text(
            message + "\n",
            encoding="utf-8",
        )

        write_github_output(
            has_new_products=False,
            issue_title="",
            scan_failed=True,
        )

        raise RuntimeError(message)

    (
        new_state,
        new_products,
        initialized_before,
    ) = update_state(
        old_state,
        current_products,
        scan_time,
    )

    save_json(
        RESULT_FILE,
        {
            "scan_time": (
                scan_time.isoformat()
            ),
            "failed_stores": failed_stores,
            "products": current_products,
        },
    )

    save_json(
        STATE_FILE,
        new_state,
    )

    summary = build_summary(
        current_products,
        new_products,
        scan_time,
        initialized_before,
    )

    SUMMARY_FILE.write_text(
        summary + "\n",
        encoding="utf-8",
    )

    print()
    print(summary)

    if not initialized_before:
        print()
        print(
            "首次執行已完成。"
            "本次只建立商品基準，"
            "不會建立通知 Issue。"
        )

    if new_products:
        notification = build_notification(
            new_products,
            scan_time,
        )

        NOTIFICATION_FILE.write_text(
            notification + "\n",
            encoding="utf-8",
        )

        issue_title = (
            "戰鬥陀螺開賣通知："
            f"發現 {len(new_products)} 筆新商品 "
            f"{scan_time.strftime('%m/%d %H:%M')}"
        )

        print()
        print(
            f"發現 {len(new_products)} 筆"
            "新商品，準備建立通知。"
        )

        for product in new_products:
            print(
                f"- {product['store']} / "
                f"{product['name']} / "
                f"{format_price(product.get('price'))}"
            )

        write_github_output(
            has_new_products=True,
            issue_title=issue_title,
            scan_failed=False,
        )

    else:
        print()
        print(
            "本次沒有新增商品，"
            "不建立通知。"
        )

        write_github_output(
            has_new_products=False,
            issue_title="",
            scan_failed=False,
        )


if __name__ == "__main__":
    asyncio.run(main())
