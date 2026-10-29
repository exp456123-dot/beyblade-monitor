import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# =========================================================
# 使用者設定
# =========================================================

KEYWORDS = [
    "BEYBLADE X",
    "戰鬥陀螺 CX",
    "戰鬥陀螺 UX",
    "戰鬥陀螺 BX",
]

TARGET_WORDS = [
    "BEYBLADE",
    "戰鬥陀螺",
    "CX-",
    "UX-",
    "BX-",
]

EXCLUDE_WORDS = [
    "二手",
    "中古",
    "盜版",
    "副廠",
    "仿品",
    "貼紙",
    "收納",
    "展示盒",
    "保護套",
    "漫畫",
    "電子書",
    "小說",
    "童書",
    "書包",
    "訂金",
    "尾款",
    "代購",
]

UNAVAILABLE_WORDS = [
    "已售完",
    "售罄",
    "缺貨",
    "暫無庫存",
    "補貨中",
    "停止銷售",
    "已絕版",
]

MIN_PRICE = 100
MAX_PRICE = 5000

MAX_ITEMS_PER_STORE = 30

STATE_FILE = "seen_products.json"

TAIWAN_TIMEZONE = timezone(
    timedelta(hours=8)
)

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


# =========================================================
# 共用功能
# =========================================================

def now():
    return datetime.now(
        TAIWAN_TIMEZONE
    ).strftime("%Y-%m-%d %H:%M:%S")


def clean_text(text):
    return re.sub(
        r"\s+",
        " ",
        text or "",
    ).strip()


def normalize_url(url):
    if not url:
        return ""

    parts = urlsplit(url)

    # momo商品編號位於i_code查詢參數中，必須保留
    if "momoshop.com.tw" in parts.netloc:
        query_items = []

        for item in parts.query.split("&"):
            if item.startswith("i_code="):
                query_items.append(item)

            elif item.startswith("goodsCode="):
                query_items.append(item)

        return urlunsplit((
            parts.scheme,
            parts.netloc,
            parts.path,
            "&".join(query_items),
            "",
        ))

    # 其他網站移除追蹤參數
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        "",
        "",
    ))


def parse_price(text):
    if not text:
        return None

    patterns = [
        r"NT\$\s*([\d,]+)",
        r"\$\s*([\d,]+)",
        r"售價[:：]?\s*([\d,]+)",
        r"價格[:：]?\s*([\d,]+)",
    ]

    for pattern in patterns:
        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        for value in matches:
            try:
                price = int(
                    value.replace(",", "")
                )

                if MIN_PRICE <= price <= MAX_PRICE:
                    return price

            except ValueError:
                continue

    return None


def product_allowed(name, price=None):
    upper_name = clean_text(name).upper()

    if not upper_name:
        return False

    if any(
        word.upper() in upper_name
        for word in EXCLUDE_WORDS
    ):
        return False

    if not any(
        word.upper() in upper_name
        for word in TARGET_WORDS
    ):
        return False

    if price is not None:
        if price < MIN_PRICE:
            return False

        if price > MAX_PRICE:
            return False

    return True


def is_unavailable(text):
    return any(
        word in text
        for word in UNAVAILABLE_WORDS
    )


def make_product(
    store,
    product_id,
    name,
    price,
    url,
    available=True,
):
    return {
        "store": store,
        "id": str(product_id),
        "name": clean_text(name),
        "price": price,
        "url": normalize_url(url),
        "available": bool(available),
    }


def product_key(product):
    return (
        product["store"]
        + "|"
        + product["id"]
    )


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        if isinstance(data, dict):
            return data

    except Exception as error:
        print(
            "⚠️ 讀取歷史紀錄失敗：",
            error,
        )

    return {}


def save_state(state):
    with open(
        STATE_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


def deduplicate_products(products):
    unique = {}

    for product in products:
        key = product_key(product)

        if key not in unique:
            unique[key] = product
            continue

        current = unique[key]

        if (
            current.get("price") is None
            and product.get("price") is not None
        ):
            unique[key] = product

    return list(unique.values())


# =========================================================
# PChome 監控
# =========================================================

def search_pchome(keyword):
    products = []

    api_url = (
        "https://ecshweb.pchome.com.tw/"
        "search/v3.3/all/results"
    )

    params = {
        "q": keyword,
        "page": 1,
        "sort": "sale/dc",
    }

    headers = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://24h.pchome.com.tw/",
    }

    try:
        response = requests.get(
            api_url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()
        data = response.json()

        for item in data.get(
            "prods",
            [],
        )[:MAX_ITEMS_PER_STORE]:

            product_id = item.get("Id")
            name = clean_text(
                item.get("name", "")
            )
            price = item.get("price")

            if not product_id or not name:
                continue

            try:
                price = int(price)

            except (TypeError, ValueError):
                price = None

            if not product_allowed(
                name,
                price,
            ):
                continue

            url = (
                "https://24h.pchome.com.tw/prod/"
                + str(product_id)
            )

            products.append(
                make_product(
                    store="PChome 24h",
                    product_id=product_id,
                    name=name,
                    price=price,
                    url=url,
                    available=True,
                )
            )

    except Exception as error:
        print(
            "⚠️ PChome 搜尋失敗：",
            error,
        )

    return products


# =========================================================
# Playwright 共用功能
# =========================================================

async def create_page(browser):
    page = await browser.new_page(
        viewport={
            "width": 1440,
            "height": 1000,
        },
        locale="zh-TW",
        user_agent=HEADERS["User-Agent"],
    )

    await page.set_extra_http_headers({
        "Accept-Language": (
            "zh-TW,zh;q=0.9,en;q=0.8"
        ),
    })

    return page


async def scroll_page(page, times=3):
    for _ in range(times):
        await page.mouse.wheel(
            0,
            1000,
        )

        await page.wait_for_timeout(
            1000
        )


async def get_card_text(link):
    best_text = ""

    selectors = [
        "xpath=ancestor::li[1]",
        "xpath=ancestor::article[1]",
        "xpath=ancestor::div[1]",
        "xpath=ancestor::div[2]",
        "xpath=ancestor::div[3]",
    ]

    for selector in selectors:
        try:
            card = link.locator(
                selector
            )

            if await card.count() == 0:
                continue

            text = clean_text(
                await card.first.inner_text(
                    timeout=3000
                )
            )

            if len(text) > len(best_text):
                best_text = text

        except Exception:
            continue

    return best_text


def guess_name(text, link_text=""):
    link_text = clean_text(link_text)

    if (
        len(link_text) >= 6
        and product_allowed(
            link_text,
            None,
        )
    ):
        return link_text

    lines = [
        clean_text(line)
        for line in re.split(
            r"[\r\n]+",
            text or "",
        )
        if clean_text(line)
    ]

    candidates = []

    for line in lines:
        if len(line) < 6:
            continue

        if re.fullmatch(
            r"(?:NT\$|\$)?[\d,]+",
            line,
        ):
            continue

        if any(
            word in line
            for word in [
                "加入購物車",
                "立即購買",
                "已售出",
                "查看詳情",
            ]
        ):
            continue

        if product_allowed(
            line,
            None,
        ):
            candidates.append(line)

    if candidates:
        return max(
            candidates,
            key=len,
        )

    return link_text


# =========================================================
# momo監控
# =========================================================

async def search_momo(browser, keyword):
    products = []
    page = await create_page(browser)

    search_url = (
        "https://www.momoshop.com.tw/"
        "search/searchShop.jsp"
        f"?keyword={quote(keyword)}"
        "&_isFuzzy=0&searchType=1"
    )

    try:
        await page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await page.wait_for_timeout(5000)
        await scroll_page(page, 3)

        links = page.locator(
            'a[href*="GoodsDetail.jsp"], '
            'a[href*="goods.momo.com.tw/goods.momo"]'
        )

        count = await links.count()
        processed = set()

        for index in range(
            min(count, 150)
        ):
            if len(products) >= MAX_ITEMS_PER_STORE:
                break

            try:
                link = links.nth(index)

                href = await link.get_attribute(
                    "href"
                )

                if not href:
                    continue

                href = urljoin(
                    "https://www.momoshop.com.tw/",
                    href,
                )

                code_match = re.search(
                    r"(?:i_code=|goodsCode=)(\d+)",
                    href,
                )

                if not code_match:
                    code_match = re.search(
                        r"/(\d{6,})(?:[/?]|$)",
                        href,
                    )

                if not code_match:
                    continue

                product_id = code_match.group(1)

                if product_id in processed:
                    continue

                processed.add(product_id)

                link_text = clean_text(
                    await link.inner_text(
                        timeout=3000
                    )
                )

                card_text = await get_card_text(
                    link
                )

                combined_text = clean_text(
                    link_text + "\n" + card_text
                )

                name = guess_name(
                    combined_text,
                    link_text,
                )

                price = parse_price(
                    combined_text
                )

                if not product_allowed(
                    name,
                    price,
                ):
                    continue

                products.append(
                    make_product(
                        store="momo購物網",
                        product_id=product_id,
                        name=name,
                        price=price,
                        url=href,
                        available=not is_unavailable(
                            combined_text
                        ),
                    )
                )

            except Exception:
                continue

    except Exception as error:
        print(
            "⚠️ momo搜尋失敗：",
            error,
        )

    finally:
        await page.close()

    return products


# =========================================================
# 蝦皮監控
# =========================================================

async def search_shopee(browser, keyword):
    products = []
    page = await create_page(browser)

    search_url = (
        "https://shopee.tw/search"
        f"?keyword={quote(keyword)}"
    )

    try:
        await page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await page.wait_for_timeout(8000)
        await scroll_page(page, 4)

        links = page.locator(
            'a[href*="-i."]'
        )

        count = await links.count()
        processed = set()

        for index in range(
            min(count, 200)
        ):
            if len(products) >= MAX_ITEMS_PER_STORE:
                break

            try:
                link = links.nth(index)

                href = await link.get_attribute(
                    "href"
                )

                if not href:
                    continue

                href = urljoin(
                    "https://shopee.tw/",
                    href,
                )

                match = re.search(
                    r"-i\.(\d+)\.(\d+)",
                    href,
                )

                if not match:
                    continue

                shop_id = match.group(1)
                item_id = match.group(2)

                product_id = (
                    shop_id
                    + "-"
                    + item_id
                )

                if product_id in processed:
                    continue

                processed.add(product_id)

                link_text = clean_text(
                    await link.inner_text(
                        timeout=3000
                    )
                )

                card_text = await get_card_text(
                    link
                )

                combined_text = clean_text(
                    link_text + "\n" + card_text
                )

                name = guess_name(
                    combined_text,
                    link_text,
                )

                price = parse_price(
                    combined_text
                )

                if not product_allowed(
                    name,
                    price,
                ):
                    continue

                products.append(
                    make_product(
                        store="蝦皮購物",
                        product_id=product_id,
                        name=name,
                        price=price,
                        url=href,
                        available=not is_unavailable(
                            combined_text
                        ),
                    )
                )

            except Exception:
                continue

    except Exception as error:
        print(
            "⚠️ 蝦皮搜尋失敗：",
            error,
        )

    finally:
        await page.close()

    return products


# =========================================================
# 誠品監控
# =========================================================

async def search_eslite(browser, keyword):
    products = []
    page = await create_page(browser)

    search_urls = [
        (
            "https://www.eslite.com/"
            f"Search?keyword={quote(keyword)}"
        ),
        (
            "https://www.eslite.com/"
            f"search?keyword={quote(keyword)}"
        ),
    ]

    loaded = False

    try:
        for search_url in search_urls:
            try:
                await page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                await page.wait_for_timeout(
                    5000
                )

                if await page.locator(
                    'a[href*="/product/"]'
                ).count() > 0:
                    loaded = True
                    break

            except Exception:
                continue

        if not loaded:
            print(
                "⚠️ 誠品搜尋頁沒有找到商品連結"
            )
            return products

        await scroll_page(page, 3)

        links = page.locator(
            'a[href*="/product/"]'
        )

        count = await links.count()
        processed = set()

        for index in range(
            min(count, 150)
        ):
            if len(products) >= MAX_ITEMS_PER_STORE:
                break

            try:
                link = links.nth(index)

                href = await link.get_attribute(
                    "href"
                )

                if not href:
                    continue

                href = urljoin(
                    "https://www.eslite.com/",
                    href,
                )

                clean_url = normalize_url(
                    href
                )

                match = re.search(
                    r"/product/([^/?#]+)",
                    clean_url,
                )

                product_id = (
                    match.group(1)
                    if match
                    else clean_url
                )

                if product_id in processed:
                    continue

                processed.add(product_id)

                link_text = clean_text(
                    await link.inner_text(
                        timeout=3000
                    )
                )

                card_text = await get_card_text(
                    link
                )

                combined_text = clean_text(
                    link_text + "\n" + card_text
                )

                name = guess_name(
                    combined_text,
                    link_text,
                )

                price = parse_price(
                    combined_text
                )

                if not product_allowed(
                    name,
                    price,
                ):
                    continue

                products.append(
                    make_product(
                        store="誠品線上",
                        product_id=product_id,
                        name=name,
                        price=price,
                        url=clean_url,
                        available=not is_unavailable(
                            combined_text
                        ),
                    )
                )

            except Exception:
                continue

    except Exception as error:
        print(
            "⚠️ 誠品搜尋失敗：",
            error,
        )

    finally:
        await page.close()

    return products


# =========================================================
# 商品異動判斷
# =========================================================

def check_changes(products, old_state):
    alerts = []
    new_state = {}

    current_keys = set()

    for product in products:
        key = product_key(product)
        current_keys.add(key)

        old_product = old_state.get(key)

        state_entry = {
            "store": product["store"],
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "url": product["url"],
            "available": product["available"],
            "last_seen": now(),
        }

        if old_product is None:
            state_entry["first_seen"] = now()

            if product["available"]:
                alert = product.copy()
                alert["reason"] = "🆕 發現新商品"
                alerts.append(alert)

        else:
            state_entry["first_seen"] = (
                old_product.get(
                    "first_seen",
                    now(),
                )
            )

            old_available = old_product.get(
                "available",
                True,
            )

            new_available = product.get(
                "available",
                True,
            )

            if (
                not old_available
                and new_available
            ):
                alert = product.copy()
                alert["reason"] = "🔥 商品恢復供貨"
                alerts.append(alert)

            old_price = old_product.get(
                "price"
            )

            new_price = product.get(
                "price"
            )

            if (
                old_price is not None
                and new_price is not None
                and new_price < old_price
            ):
                alert = product.copy()
                alert["reason"] = "💰 商品降價"
                alert["old_price"] = old_price
                alert["difference"] = (
                    old_price - new_price
                )
                alerts.append(alert)

        new_state[key] = state_entry

    # 保留本次沒有抓到的歷史商品，
    # 避免網站偶爾抓取失敗時把紀錄刪掉。
    for key, old_product in old_state.items():
        if key not in current_keys:
            new_state[key] = old_product

    return alerts, new_state


# =========================================================
# 顯示商品與通知
# =========================================================

def display_product(product):
    print()
    print("-" * 70)
    print("🏪 賣場：", product["store"])
    print("🌀 商品：", product["name"])

    if product["price"] is not None:
        print(
            "💰 價格：NT$",
            format(
                product["price"],
                ",",
            ),
            sep="",
        )
    else:
        print(
            "💰 價格：未能辨識"
        )

    print(
        "📦 狀態：",
        (
            "有貨或可購買"
            if product["available"]
            else "疑似缺貨"
        ),
    )

    print(
        "🔗 網址：",
        product["url"],
    )


def display_alert(alert):
    print()
    print("=" * 70)
    print(alert["reason"])
    print("=" * 70)

    print(
        "🏪 賣場：",
        alert["store"],
    )

    print(
        "🌀 商品：",
        alert["name"],
    )

    if alert.get("price") is not None:
        print(
            "💰 現價：NT$",
            format(
                alert["price"],
                ",",
            ),
            sep="",
        )

    if alert.get("old_price") is not None:
        print(
            "原價：NT$",
            format(
                alert["old_price"],
                ",",
            ),
            sep="",
        )

    if alert.get("difference") is not None:
        print(
            "⬇️ 降價：NT$",
            format(
                alert["difference"],
                ",",
            ),
            sep="",
        )

    print(
        "🔗 購買：",
        alert["url"],
    )

    print("=" * 70)


# =========================================================
# 主程式
# =========================================================

async def main():
    print("=" * 70)
    print("🔥 戰鬥陀螺四站開賣監控器")
    print("🇹🇼 台灣時間：", now())
    print("=" * 70)

    old_state = load_state()

    print(
        "📚 已儲存歷史商品：",
        len(old_state),
        "筆",
    )

    all_products = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        try:
            for keyword in KEYWORDS:
                print()
                print("#" * 70)
                print("🔎 搜尋關鍵字：", keyword)
                print("#" * 70)

                pchome_products = await asyncio.to_thread(
                    search_pchome,
                    keyword,
                )

                print(
                    "PChome：",
                    len(pchome_products),
                    "筆",
                )

                all_products.extend(
                    pchome_products
                )

                momo_products = await search_momo(
                    browser,
                    keyword,
                )

                print(
                    "momo：",
                    len(momo_products),
                    "筆",
                )

                all_products.extend(
                    momo_products
                )

                shopee_products = await search_shopee(
                    browser,
                    keyword,
                )

                print(
                    "蝦皮：",
                    len(shopee_products),
                    "筆",
                )

                all_products.extend(
                    shopee_products
                )

                eslite_products = await search_eslite(
                    browser,
                    keyword,
                )

                print(
                    "誠品：",
                    len(eslite_products),
                    "筆",
                )

                all_products.extend(
                    eslite_products
                )

        finally:
            await browser.close()

    products = deduplicate_products(
        all_products
    )

    products.sort(
        key=lambda product: (
            product["store"],
            (
                product["price"]
                if product["price"] is not None
                else 999999
            ),
            product["name"],
        )
    )

    print()
    print("=" * 70)
    print(
        "📦 本次共找到",
        len(products),
        "筆不重複商品",
    )
    print("=" * 70)

    for product in products:
        display_product(product)

    alerts, new_state = check_changes(
        products,
        old_state,
    )

    print()
    print("=" * 70)
    print(
        "🚨 本次異動通知：",
        len(alerts),
        "筆",
    )
    print("=" * 70)

    if alerts:
        for alert in alerts:
            display_alert(alert)
    else:
        print()
        print(
            "😴 沒有新品、恢復供貨或降價。"
        )

    save_state(new_state)

    print()
    print(
        "💾 已更新",
        STATE_FILE,
    )

    print()
    print("=" * 70)
    print("✅ 四站監控完成")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
