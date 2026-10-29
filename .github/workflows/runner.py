import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import requests
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

MIN_PRICE = 100
MAX_PRICE = 5000

MAX_PRODUCTS_PER_SEARCH = 30

STATE_FILE = "seen_products.json"

TAIWAN_TIMEZONE = timezone(
    timedelta(hours=8)
)

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
    "美版",
    "貼紙",
    "收納",
    "收納盒",
    "展示盒",
    "保護套",
    "漫畫",
    "電子書",
    "小說",
    "童書",
    "書包",
    "紙陀螺",
    "訂金",
    "尾款",
]

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

    try:
        parts = urlsplit(url)

        return urlunsplit((
            parts.scheme,
            parts.netloc,
            parts.path,
            "",
            "",
        ))

    except Exception:
        return url.split("?")[0]


def parse_price(text):
    if not text:
        return None

    patterns = [
        r"NT\$\s*([\d,]+)",
        r"\$\s*([\d,]+)",
        r"售價\s*([\d,]+)",
        r"優惠價\s*([\d,]+)",
        r"([\d,]+)\s*元",
    ]

    possible_prices = []

    for pattern in patterns:
        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        for match in matches:
            try:
                price = int(
                    match.replace(",", "")
                )

                if MIN_PRICE <= price <= MAX_PRICE:
                    possible_prices.append(price)

            except ValueError:
                continue

    if possible_prices:
        return min(possible_prices)

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


def build_product(
    store,
    name,
    price,
    url,
):
    url = normalize_url(url)
    name = clean_text(name)

    return {
        "key": f"{store}|{url}",
        "store": store,
        "name": name,
        "price": price,
        "url": url,
    }


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

        return {}

    except Exception as error:
        print(
            f"無法讀取 {STATE_FILE}：{error}"
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
        )


def deduplicate_products(products):
    unique = {}

    for product in products:
        key = product.get("key")

        if not key:
            continue

        if key not in unique:
            unique[key] = product
            continue

        old_price = unique[key].get("price")
        new_price = product.get("price")

        if old_price is None and new_price is not None:
            unique[key] = product

    return list(unique.values())


# =========================================================
# PChome
# =========================================================

def search_pchome(keyword):
    store = "PChome 24h"

    api_url = (
        "https://ecshweb.pchome.com.tw/"
        "search/v3.3/all/results"
    )

    params = {
        "q": keyword,
        "page": 1,
        "sort": "sale/dc",
    }

    products = []

    try:
        response = requests.get(
            api_url,
            params=params,
            headers={
                **HEADERS,
                "Referer": (
                    "https://24h.pchome.com.tw/"
                ),
            },
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        for item in data.get(
            "prods",
            [],
        )[:MAX_PRODUCTS_PER_SEARCH]:

            product_id = item.get("Id")

            name = clean_text(
                item.get("name", "")
            )

            price = item.get("price")

            if not product_id:
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

            product_url = (
                "https://24h.pchome.com.tw/"
                f"prod/{product_id}"
            )

            products.append(
                build_product(
                    store,
                    name,
                    price,
                    product_url,
                )
            )

        return products, True

    except Exception as error:
        print(
            f"   PChome 搜尋失敗：{error}"
        )

        return [], False


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


async def scroll_page(page):
    for _ in range(4):
        await page.mouse.wheel(
            0,
            1000,
        )

        await page.wait_for_timeout(
            1000
        )


async def get_card_text(link):
    texts = []

    try:
        link_text = clean_text(
            await link.inner_text()
        )

        if link_text:
            texts.append(link_text)

    except Exception:
        pass

    ancestors = [
        "xpath=ancestor::li[1]",
        "xpath=ancestor::article[1]",
        "xpath=ancestor::div[1]",
        "xpath=ancestor::div[2]",
        "xpath=ancestor::div[3]",
    ]

    for selector in ancestors:
        try:
            locator = link.locator(
                selector
            )

            if await locator.count() == 0:
                continue

            candidate = clean_text(
                await locator.first.inner_text()
            )

            if candidate:
                texts.append(candidate)

        except Exception:
            continue

    if not texts:
        return ""

    return max(
        texts,
        key=len,
    )


def guess_product_name(text):
    if not text:
        return ""

    lines = [
        clean_text(line)
        for line in re.split(
            r"[\r\n]+",
            text,
        )
        if clean_text(line)
    ]

    excluded_lines = [
        "加入購物車",
        "立即購買",
        "查看商品",
        "已售出",
        "已售完",
        "暫無庫存",
        "缺貨中",
    ]

    candidates = []

    for line in lines:
        if line in excluded_lines:
            continue

        if re.fullmatch(
            r"(?:NT\$|\$)?\s*[\d,]+",
            line,
        ):
            continue

        if len(line) < 5:
            continue

        candidates.append(line)

    if not candidates:
        return clean_text(text)[:180]

    target_candidates = [
        line
        for line in candidates
        if any(
            word.upper() in line.upper()
            for word in TARGET_WORDS
        )
    ]

    if target_candidates:
        return max(
            target_candidates,
            key=len,
        )[:180]

    return max(
        candidates,
        key=len,
    )[:180]


# =========================================================
# momo
# =========================================================

async def search_momo(
    browser,
    keyword,
):
    store = "momo購物網"

    page = await create_page(
        browser
    )

    search_url = (
        "https://www.momoshop.com.tw/"
        "search/searchShop.jsp"
        f"?keyword={quote(keyword)}"
        "&_isFuzzy=0&searchType=1"
    )

    products = []

    try:
        await page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await page.wait_for_timeout(
            5000
        )

        await scroll_page(page)

        links = page.locator(
            'a[href*="GoodsDetail.jsp"], '
            'a[href*="goods.momo"], '
            'a[href*="i_code="]'
        )

        count = min(
            await links.count(),
            150,
        )

        seen_urls = set()

        for index in range(count):
            if len(products) >= MAX_PRODUCTS_PER_SEARCH:
                break

            try:
                link = links.nth(index)

                href = await link.get_attribute(
                    "href"
                )

                if not href:
                    continue

                if href.startswith("//"):
                    href = "https:" + href

                elif href.startswith("/"):
                    href = (
                        "https://www.momoshop.com.tw"
                        + href
                    )

                elif not href.startswith("http"):
                    href = (
                        "https://www.momoshop.com.tw/"
                        + href.lstrip("/")
                    )

                href = normalize_url(href)

                if href in seen_urls:
                    continue

                seen_urls.add(href)

                card_text = await get_card_text(
                    link
                )

                name = guess_product_name(
                    card_text
                )

                price = parse_price(
                    card_text
                )

                if not product_allowed(
                    name,
                    price,
                ):
                    continue

                unavailable_words = [
                    "已售完",
                    "暫無庫存",
                    "補貨中",
                    "停止販售",
                ]

                if any(
                    word in card_text
                    for word in unavailable_words
                ):
                    continue

                products.append(
                    build_product(
                        store,
                        name,
                        price,
                        href,
                    )
                )

            except Exception:
                continue

        return products, True

    except Exception as error:
        print(
            f"   momo 搜尋失敗：{error}"
        )

        return [], False

    finally:
        await page.close()


# =========================================================
# 蝦皮
# =========================================================

async def search_shopee(
    browser,
    keyword,
):
    store = "蝦皮購物"

    page = await create_page(
        browser
    )

    search_url = (
        "https://shopee.tw/search"
        f"?keyword={quote(keyword)}"
    )

    products = []

    try:
        await page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await page.wait_for_timeout(
            8000
        )

        await scroll_page(page)

        page_text = clean_text(
            await page.locator(
                "body"
            ).inner_text()
        )

        blocked_words = [
            "驗證您的身分",
            "請進行驗證",
            "captcha",
            "異常流量",
        ]

        if any(
            word.lower() in page_text.lower()
            for word in blocked_words
        ):
            print(
                "   蝦皮出現驗證或流量限制"
            )

            return [], False

        links = page.locator(
            'a[href*="-i."], '
            'a[href*="/product/"]'
        )

        count = min(
            await links.count(),
            200,
        )

        seen_urls = set()

        for index in range(count):
            if len(products) >= MAX_PRODUCTS_PER_SEARCH:
                break

            try:
                link = links.nth(index)

                href = await link.get_attribute(
                    "href"
                )

                if not href:
                    continue

                if href.startswith("//"):
                    href = "https:" + href

                elif href.startswith("/"):
                    href = (
                        "https://shopee.tw"
                        + href
                    )

                if not href.startswith("http"):
                    continue

                href = normalize_url(href)

                if href in seen_urls:
                    continue

                seen_urls.add(href)

                card_text = await get_card_text(
                    link
                )

                name = guess_product_name(
                    card_text
                )

                price = parse_price(
                    card_text
                )

                if not product_allowed(
                    name,
                    price,
                ):
                    continue

                products.append(
                    build_product(
                        store,
                        name,
                        price,
                        href,
                    )
                )

            except Exception:
                continue

        return products, True

    except Exception as error:
        print(
            f"   蝦皮搜尋失敗：{error}"
        )

        return [], False

    finally:
        await page.close()


# =========================================================
# 誠品
# =========================================================

async def search_eslite(
    browser,
    keyword,
):
    store = "誠品線上"

    page = await create_page(
        browser
    )

    search_url = (
        "https://www.eslite.com/Search"
        f"?keyword={quote(keyword)}"
    )

    products = []

    try:
        await page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await page.wait_for_timeout(
            5000
        )

        await scroll_page(page)

        links = page.locator(
            'a[href*="/product/"]'
        )

        count = min(
            await links.count(),
            150,
        )

        seen_urls = set()

        for index in range(count):
            if len(products) >= MAX_PRODUCTS_PER_SEARCH:
                break

            try:
                link = links.nth(index)

                href = await link.get_attribute(
                    "href"
                )

                if not href:
                    continue

                if href.startswith("//"):
                    href = "https:" + href

                elif href.startswith("/"):
                    href = (
                        "https://www.eslite.com"
                        + href
                    )

                elif not href.startswith("http"):
                    href = (
                        "https://www.eslite.com/"
                        + href.lstrip("/")
                    )

                href = normalize_url(href)

                if href in seen_urls:
                    continue

                seen_urls.add(href)

                card_text = await get_card_text(
                    link
                )

                name = guess_product_name(
                    card_text
                )

                price = parse_price(
                    card_text
                )

                if not product_allowed(
                    name,
                    price,
                ):
                    continue

                unavailable_words = [
                    "已售完",
                    "已絕版",
                    "暫無庫存",
                    "缺貨中",
                    "停止銷售",
                ]

                if any(
                    word in card_text
                    for word in unavailable_words
                ):
                    continue

                products.append(
                    build_product(
                        store,
                        name,
                        price,
                        href,
                    )
                )

            except Exception:
                continue

        return products, True

    except Exception as error:
        print(
            f"   誠品搜尋失敗：{error}"
        )

        return [], False

    finally:
        await page.close()


# =========================================================
# 比對歷史紀錄
# =========================================================

def compare_state(
    products,
    old_state,
    successful_stores,
):
    current_products = {
        product["key"]: product
        for product in products
    }

    new_state = dict(old_state)

    alerts = []

    # 更新本次找到的商品
    for key, product in current_products.items():
        old_product = old_state.get(key)

        state_item = {
            "store": product["store"],
            "name": product["name"],
            "price": product["price"],
            "url": product["url"],
            "active": True,
            "last_seen": now(),
        }

        if old_product:
            state_item["first_seen"] = (
                old_product.get(
                    "first_seen",
                    now(),
                )
            )
        else:
            state_item["first_seen"] = now()

        new_state[key] = state_item

        # 第一次發現
        if old_product is None:
            alert = product.copy()
            alert["reason"] = "NEW"
            alerts.append(alert)
            continue

        # 之前沒有出現，現在重新出現
        if not old_product.get(
            "active",
            True,
        ):
            alert = product.copy()
            alert["reason"] = "RESTOCK"
            alerts.append(alert)

        old_price = old_product.get(
            "price"
        )

        new_price = product.get(
            "price"
        )

        if (
            isinstance(old_price, int)
            and isinstance(new_price, int)
            and new_price < old_price
        ):
            alert = product.copy()
            alert["reason"] = "PRICE_DROP"
            alert["old_price"] = old_price
            alert["difference"] = (
                old_price - new_price
            )
            alerts.append(alert)

    # 只有網站本次成功檢查，才把消失商品標成 inactive
    for key, old_product in old_state.items():
        store = old_product.get("store")

        if store not in successful_stores:
            continue

        if key in current_products:
            continue

        inactive_item = dict(
            old_product
        )

        inactive_item["active"] = False
        inactive_item["last_missing"] = now()

        new_state[key] = inactive_item

    return alerts, new_state


# =========================================================
# 顯示結果
# =========================================================

def show_alert(alert):
    reason = alert.get("reason")

    reason_text = {
        "NEW": "🆕 發現新商品",
        "RESTOCK": "🔥 商品重新出現／可能補貨",
        "PRICE_DROP": "💰 商品降價",
    }.get(
        reason,
        "商品異動",
    )

    print()
    print("=" * 70)
    print(reason_text)
    print(f"賣場：{alert['store']}")
    print(f"商品：{alert['name']}")

    if alert.get("price") is not None:
        print(
            "現價：NT$"
            + format(
                alert["price"],
                ",",
            )
        )

    if alert.get("old_price") is not None:
        print(
            "原價：NT$"
            + format(
                alert["old_price"],
                ",",
            )
        )

    if alert.get("difference") is not None:
        print(
            "降價：NT$"
            + format(
                alert["difference"],
                ",",
            )
        )

    print(f"連結：{alert['url']}")
    print(f"時間：{now()}")
    print("=" * 70)


# =========================================================
# 主程式
# =========================================================

async def main():
    print("=" * 70)
    print("戰鬥陀螺四站監控器")
    print(f"台灣時間：{now()}")
    print("=" * 70)

    old_state = load_state()

    print(
        f"歷史商品紀錄：{len(old_state)} 筆"
    )

    all_products = []

    successful_stores = set()

    # PChome
    print()
    print("【PChome 24h】")

    pchome_success = True

    for keyword in KEYWORDS:
        products, success = search_pchome(
            keyword
        )

        print(
            f"   {keyword}：{len(products)} 筆"
        )

        all_products.extend(products)

        if not success:
            pchome_success = False

    if pchome_success:
        successful_stores.add(
            "PChome 24h"
        )

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
            # momo
            print()
            print("【momo購物網】")

            momo_success = True

            for keyword in KEYWORDS:
                products, success = await search_momo(
                    browser,
                    keyword,
                )

                print(
                    f"   {keyword}：{len(products)} 筆"
                )

                all_products.extend(products)

                if not success:
                    momo_success = False

            if momo_success:
                successful_stores.add(
                    "momo購物網"
                )

            # 蝦皮
            print()
            print("【蝦皮購物】")

            shopee_success = True

            for keyword in KEYWORDS:
                products, success = await search_shopee(
                    browser,
                    keyword,
                )

                print(
                    f"   {keyword}：{len(products)} 筆"
                )

                all_products.extend(products)

                if not success:
                    shopee_success = False

            if shopee_success:
                successful_stores.add(
                    "蝦皮購物"
                )

            # 誠品
            print()
            print("【誠品線上】")

            eslite_success = True

            for keyword in KEYWORDS:
                products, success = await search_eslite(
                    browser,
                    keyword,
                )

                print(
                    f"   {keyword}：{len(products)} 筆"
                )

                all_products.extend(products)

                if not success:
                    eslite_success = False

            if eslite_success:
                successful_stores.add(
                    "誠品線上"
                )

        finally:
            await browser.close()

    products = deduplicate_products(
        all_products
    )

    products.sort(
        key=lambda item: (
            item["store"],
            item["price"]
            if item["price"] is not None
            else 999999,
            item["name"],
        )
    )

    print()
    print("=" * 70)
    print(
        f"本次共找到 {len(products)} 筆不重複商品"
    )

    print(
        "成功完成的平台："
        + (
            "、".join(
                sorted(successful_stores)
            )
            if successful_stores
            else "無"
        )
    )

    alerts, new_state = compare_state(
        products,
        old_state,
        successful_stores,
    )

    print(
        f"本次商品異動：{len(alerts)} 筆"
    )
    print("=" * 70)

    if alerts:
        for alert in alerts:
            show_alert(alert)

    else:
        print()
        print(
            "沒有發現新品、重新出現或降價商品。"
        )

    save_state(new_state)

    print()
    print(
        f"商品狀態已儲存至 {STATE_FILE}"
    )

    print("=" * 70)
    print("本次監控完成")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
