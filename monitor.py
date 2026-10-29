import asyncio
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import (
    quote,
    urljoin,
    urlsplit,
    urlunsplit,
)

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
    "雜誌",
    "MOOK",
    "ISBN",
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
    "無庫存",
    "sold out",
    "out of stock",
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
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en;q=0.8"
    ),
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

    # momo商品編號存在查詢參數中，
    # i_code或goodsCode必須保留。
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
        r"NTD\s*([\d,]+)",
        r"\$\s*([\d,]+)",
        r"售價[:：]?\s*([\d,]+)",
        r"價格[:：]?\s*([\d,]+)",
        r'"price"\s*:\s*"?([\d,.]+)',
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
                    float(
                        value.replace(",", "")
                    )
                )

                if (
                    MIN_PRICE
                    <= price
                    <= MAX_PRICE
                ):
                    return price

            except ValueError:
                continue

    return None


def product_allowed(name, price=None):
    upper_name = clean_text(
        name
    ).upper()

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
    lower_text = (
        text or ""
    ).lower()

    return any(
        word.lower() in lower_text
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
# 搜尋引擎RSS備援
# =========================================================

def bing_rss_search(
    query,
    maximum=20,
):
    results = []

    search_url = (
        "https://www.bing.com/search"
        "?format=rss&q="
        + quote(query)
    )

    headers = {
        **HEADERS,
        "Accept": (
            "application/rss+xml,"
            "application/xml,"
            "text/xml,*/*"
        ),
    }

    try:
        response = requests.get(
            search_url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        root = ET.fromstring(
            response.content
        )

        items = root.findall(
            ".//item"
        )

        for item in items[:maximum]:
            title = clean_text(
                html.unescape(
                    item.findtext(
                        "title",
                        default="",
                    )
                )
            )

            url = clean_text(
                item.findtext(
                    "link",
                    default="",
                )
            )

            description = clean_text(
                html.unescape(
                    item.findtext(
                        "description",
                        default="",
                    )
                )
            )

            if not title or not url:
                continue

            results.append({
                "title": title,
                "url": url,
                "description": description,
            })

    except Exception as error:
        print(
            "   ⚠️ 備援搜尋失敗：",
            error,
        )

    return results


# =========================================================
# JSON-LD商品資料
# =========================================================

def extract_json_ld_products(page_text):
    products = []

    soup = BeautifulSoup(
        page_text,
        "html.parser",
    )

    scripts = soup.find_all(
        "script",
        attrs={
            "type": "application/ld+json"
        },
    )

    for script in scripts:
        raw = script.string

        if not raw:
            continue

        try:
            data = json.loads(raw)

        except Exception:
            continue

        candidates = []

        if isinstance(data, dict):
            candidates.append(data)

            graph = data.get("@graph")

            if isinstance(graph, list):
                candidates.extend(graph)

        elif isinstance(data, list):
            candidates.extend(data)

        for candidate in candidates:
            if not isinstance(
                candidate,
                dict,
            ):
                continue

            candidate_type = candidate.get(
                "@type",
                "",
            )

            if isinstance(
                candidate_type,
                list,
            ):
                is_product = (
                    "Product"
                    in candidate_type
                )

            else:
                is_product = (
                    candidate_type
                    == "Product"
                )

            if not is_product:
                continue

            name = clean_text(
                candidate.get(
                    "name",
                    "",
                )
            )

            url = clean_text(
                candidate.get(
                    "url",
                    "",
                )
            )

            offers = candidate.get(
                "offers",
                {},
            )

            if isinstance(offers, list):
                offers = (
                    offers[0]
                    if offers
                    else {}
                )

            price = None
            availability_text = ""

            if isinstance(offers, dict):
                raw_price = offers.get(
                    "price"
                )

                try:
                    price = int(
                        float(raw_price)
                    )

                except (
                    TypeError,
                    ValueError,
                ):
                    price = None

                availability_text = str(
                    offers.get(
                        "availability",
                        "",
                    )
                )

                if not url:
                    url = clean_text(
                        offers.get(
                            "url",
                            "",
                        )
                    )

            available = not any(
                word
                in availability_text.lower()
                for word in [
                    "outofstock",
                    "soldout",
                    "discontinued",
                ]
            )

            products.append({
                "name": name,
                "price": price,
                "url": url,
                "available": available,
            })

    return products


# =========================================================
# PChome監控
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
        "Accept": (
            "application/json,"
            "text/plain,*/*"
        ),
        "Referer": (
            "https://24h.pchome.com.tw/"
        ),
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

            except (
                TypeError,
                ValueError,
            ):
                price = None

            if not product_allowed(
                name,
                price,
            ):
                continue

            url = (
                "https://24h.pchome.com.tw/"
                "prod/"
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
            "⚠️ PChome搜尋失敗：",
            error,
        )

    return products


# =========================================================
# Playwright共用功能
# =========================================================

async def create_page(browser):
    page = await browser.new_page(
        viewport={
            "width": 1440,
            "height": 1000,
        },
        locale="zh-TW",
        user_agent=HEADERS[
            "User-Agent"
        ],
    )

    await page.set_extra_http_headers({
        "Accept-Language": (
            "zh-TW,zh;q=0.9,en;q=0.8"
        ),
    })

    return page


async def scroll_page(
    page,
    times=3,
):
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


def guess_name(
    text,
    link_text="",
):
    link_text = clean_text(
        link_text
    )

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

async def search_momo(
    browser,
    keyword,
):
    products = []
    processed = set()

    page = await create_page(
        browser
    )

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

        await page.wait_for_timeout(
            5000
        )

        await scroll_page(
            page,
            3,
        )

        links = page.locator(
            'a[href*="GoodsDetail.jsp"],'
            'a[href*="goods.momo.com.tw/'
            'goods.momo"]'
        )

        count = await links.count()

        for index in range(
            min(count, 150)
        ):
            if (
                len(products)
                >= MAX_ITEMS_PER_STORE
            ):
                break

            try:
                link = links.nth(index)

                href = (
                    await link.get_attribute(
                        "href"
                    )
                )

                if not href:
                    continue

                href = urljoin(
                    "https://www.momoshop.com.tw/",
                    href,
                )

                code_match = re.search(
                    r"(?:i_code=|goodsCode=)"
                    r"(\d+)",
                    href,
                )

                if not code_match:
                    code_match = re.search(
                        r"/(\d{6,})(?:[/?]|$)",
                        href,
                    )

                if not code_match:
                    continue

                product_id = (
                    code_match.group(1)
                )

                if product_id in processed:
                    continue

                link_text = clean_text(
                    await link.inner_text(
                        timeout=3000
                    )
                )

                card_text = (
                    await get_card_text(
                        link
                    )
                )

                combined_text = clean_text(
                    link_text
                    + "\n"
                    + card_text
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

                processed.add(
                    product_id
                )

                products.append(
                    make_product(
                        store="momo購物網",
                        product_id=(
                            product_id
                        ),
                        name=name,
                        price=price,
                        url=href,
                        available=(
                            not is_unavailable(
                                combined_text
                            )
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

async def search_shopee(
    browser,
    keyword,
):
    products = []
    processed = set()

    print(
        "   蝦皮：嘗試直接載入搜尋頁"
    )

    page = await create_page(
        browser
    )

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

        await page.wait_for_timeout(
            10000
        )

        page_title = clean_text(
            await page.title()
        )

        current_url = page.url

        try:
            body_text = clean_text(
                await page.locator(
                    "body"
                ).inner_text(
                    timeout=5000
                )
            )

        except Exception:
            body_text = ""

        print(
            "   蝦皮頁面標題：",
            page_title[:100],
        )

        print(
            "   蝦皮目前網址：",
            current_url,
        )

        blocked_words = [
            "驗證",
            "captcha",
            "機器人",
            "異常流量",
            "登入",
            "verify",
            "access denied",
        ]

        blocked = any(
            word.lower()
            in (
                page_title
                + " "
                + body_text[:1000]
            ).lower()
            for word in blocked_words
        )

        if blocked:
            print(
                "   ⚠️ 蝦皮可能顯示"
                "登入或人機驗證"
            )

        await scroll_page(
            page,
            5,
        )

        selectors = [
            'a[href*="-i."]',
            'a[href*="/product/"]',
        ]

        links = None
        count = 0

        for selector in selectors:
            candidate = page.locator(
                selector
            )

            candidate_count = (
                await candidate.count()
            )

            print(
                "   蝦皮 selector",
                selector,
                "找到",
                candidate_count,
                "個連結",
            )

            if candidate_count > 0:
                links = candidate
                count = candidate_count
                break

        if links is not None:
            for index in range(
                min(count, 200)
            ):
                if (
                    len(products)
                    >= MAX_ITEMS_PER_STORE
                ):
                    break

                try:
                    link = links.nth(
                        index
                    )

                    href = (
                        await link.get_attribute(
                            "href"
                        )
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
                        match = re.search(
                            r"/product/"
                            r"(\d+)/(\d+)",
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

                    link_text = clean_text(
                        await link.inner_text(
                            timeout=3000
                        )
                    )

                    card_text = (
                        await get_card_text(
                            link
                        )
                    )

                    combined_text = clean_text(
                        link_text
                        + "\n"
                        + card_text
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

                    processed.add(
                        product_id
                    )

                    products.append(
                        make_product(
                            store="蝦皮購物",
                            product_id=(
                                product_id
                            ),
                            name=name,
                            price=price,
                            url=href,
                            available=(
                                not is_unavailable(
                                    combined_text
                                )
                            ),
                        )
                    )

                except Exception:
                    continue

    except Exception as error:
        print(
            "   ⚠️ 蝦皮直接搜尋失敗：",
            error,
        )

    finally:
        await page.close()

    # 直接搜尋沒有資料時，使用搜尋索引備援。
    if not products:
        print(
            "   蝦皮直接搜尋為0，"
            "啟用備援搜尋"
        )

        query = (
            "site:shopee.tw "
            + keyword
            + " -直播"
        )

        indexed_results = (
            await asyncio.to_thread(
                bing_rss_search,
                query,
                30,
            )
        )

        print(
            "   蝦皮備援搜尋取得：",
            len(indexed_results),
            "筆",
        )

        for result in indexed_results:
            if (
                len(products)
                >= MAX_ITEMS_PER_STORE
            ):
                break

            href = result["url"]

            if "shopee.tw" not in href:
                continue

            match = re.search(
                r"-i\.(\d+)\.(\d+)",
                href,
            )

            if not match:
                match = re.search(
                    r"/product/"
                    r"(\d+)/(\d+)",
                    href,
                )

            if not match:
                continue

            product_id = (
                match.group(1)
                + "-"
                + match.group(2)
            )

            if product_id in processed:
                continue

            name = clean_text(
                result["title"]
            )

            combined_text = clean_text(
                result["title"]
                + " "
                + result["description"]
            )

            price = parse_price(
                combined_text
            )

            if not product_allowed(
                name,
                price,
            ):
                continue

            processed.add(
                product_id
            )

            products.append(
                make_product(
                    store="蝦皮購物",
                    product_id=product_id,
                    name=name,
                    price=price,
                    url=href,
                    # 備援索引無法可靠證明
                    # 蝦皮即時庫存。
                    available=True,
                )
            )

    return products


# =========================================================
# 誠品監控
# =========================================================

async def search_eslite(
    browser,
    keyword,
):
    products = []
    processed = set()

    print(
        "   誠品：使用搜尋頁與備援索引"
    )

    page = await create_page(
        browser
    )

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

    try:
        for search_url in search_urls:
            try:
                await page.goto(
                    search_url,
                    wait_until=(
                        "domcontentloaded"
                    ),
                    timeout=60000,
                )

                await page.wait_for_timeout(
                    8000
                )

                await scroll_page(
                    page,
                    5,
                )

                page_title = clean_text(
                    await page.title()
                )

                print(
                    "   誠品搜尋頁標題：",
                    page_title[:100],
                )

                links = page.locator(
                    'a[href*="/product/"]'
                )

                count = (
                    await links.count()
                )

                print(
                    "   誠品搜尋頁商品連結：",
                    count,
                    "個",
                )

                for index in range(
                    min(count, 200)
                ):
                    if (
                        len(products)
                        >= MAX_ITEMS_PER_STORE
                    ):
                        break

                    try:
                        link = links.nth(
                            index
                        )

                        href = (
                            await link.get_attribute(
                                "href"
                            )
                        )

                        if not href:
                            continue

                        href = urljoin(
                            "https://www.eslite.com/",
                            href,
                        )

                        match = re.search(
                            r"/product/([^/?#]+)",
                            href,
                        )

                        if not match:
                            continue

                        product_id = (
                            match.group(1)
                        )

                        if (
                            product_id
                            in processed
                        ):
                            continue

                        link_text = clean_text(
                            await link.inner_text(
                                timeout=3000
                            )
                        )

                        card_text = (
                            await get_card_text(
                                link
                            )
                        )

                        combined_text = (
                            clean_text(
                                link_text
                                + "\n"
                                + card_text
                            )
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

                        processed.add(
                            product_id
                        )

                        products.append(
                            make_product(
                                store=(
                                    "誠品線上"
                                ),
                                product_id=(
                                    product_id
                                ),
                                name=name,
                                price=price,
                                url=href,
                                available=(
                                    not is_unavailable(
                                        combined_text
                                    )
                                ),
                            )
                        )

                    except Exception:
                        continue

                if products:
                    break

            except Exception as error:
                print(
                    "   誠品搜尋頁錯誤：",
                    error,
                )

    finally:
        await page.close()

    # 搜尋頁沒有資料時，使用索引備援。
    if not products:
        print(
            "   誠品搜尋頁為0，"
            "啟用備援搜尋"
        )

        query = (
            "site:eslite.com/product "
            + keyword
        )

        indexed_results = (
            await asyncio.to_thread(
                bing_rss_search,
                query,
                40,
            )
        )

        print(
            "   誠品備援搜尋取得：",
            len(indexed_results),
            "筆",
        )

        for result in indexed_results:
            if (
                len(products)
                >= MAX_ITEMS_PER_STORE
            ):
                break

            href = result["url"]

            if (
                "eslite.com/product/"
                not in href
            ):
                continue

            match = re.search(
                r"/product/([^/?#]+)",
                href,
            )

            if not match:
                continue

            product_id = match.group(1)

            if product_id in processed:
                continue

            try:
                response = (
                    await asyncio.to_thread(
                        requests.get,
                        href,
                        headers=HEADERS,
                        timeout=(
                            REQUEST_TIMEOUT
                        ),
                    )
                )

                response.raise_for_status()

                page_text = response.text

                soup = BeautifulSoup(
                    page_text,
                    "html.parser",
                )

                title = ""

                og_title = soup.find(
                    "meta",
                    attrs={
                        "property": "og:title"
                    },
                )

                if og_title:
                    title = clean_text(
                        og_title.get(
                            "content",
                            "",
                        )
                    )

                if (
                    not title
                    and soup.title
                ):
                    title = clean_text(
                        soup.title.get_text()
                    )

                if not title:
                    title = clean_text(
                        result["title"]
                    )

                page_plain_text = clean_text(
                    soup.get_text(
                        " ",
                        strip=True,
                    )
                )

                price = None

                og_price = soup.find(
                    "meta",
                    attrs={
                        "property": (
                            "product:price:"
                            "amount"
                        )
                    },
                )

                if og_price:
                    try:
                        price = int(
                            float(
                                og_price.get(
                                    "content",
                                    "",
                                )
                            )
                        )

                    except (
                        TypeError,
                        ValueError,
                    ):
                        price = None

                json_products = (
                    extract_json_ld_products(
                        page_text
                    )
                )

                if json_products:
                    json_product = (
                        json_products[0]
                    )

                    if json_product.get(
                        "name"
                    ):
                        title = (
                            json_product["name"]
                        )

                    if (
                        json_product.get(
                            "price"
                        )
                        is not None
                    ):
                        price = (
                            json_product[
                                "price"
                            ]
                        )

                if price is None:
                    price = parse_price(
                        page_plain_text
                    )

                if not product_allowed(
                    title,
                    price,
                ):
                    continue

                non_toy_words = [
                    "中文書",
                    "日文MOOK",
                    "漫畫",
                    "首刷限定版",
                    "雜誌",
                    "ISBN",
                    "電子書",
                ]

                if any(
                    word in page_plain_text
                    for word in non_toy_words
                ):
                    if (
                        "兒童玩具"
                        not in page_plain_text
                        and "玩具"
                        not in page_plain_text
                    ):
                        continue

                available = (
                    not is_unavailable(
                        page_plain_text
                    )
                )

                processed.add(
                    product_id
                )

                products.append(
                    make_product(
                        store="誠品線上",
                        product_id=product_id,
                        name=title,
                        price=price,
                        url=href,
                        available=available,
                    )
                )

            except Exception as error:
                print(
                    "   誠品商品頁讀取失敗：",
                    product_id,
                    error,
                )

    return products


# =========================================================
# 商品變化判斷
# =========================================================

def check_changes(
    products,
    old_state,
):
    alerts = []
    new_state = {}

    current_keys = set()

    for product in products:
        key = product_key(product)

        current_keys.add(key)

        old_product = old_state.get(
            key
        )

        state_entry = {
            "store": product["store"],
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "url": product["url"],
            "available": (
                product["available"]
            ),
            "last_seen": now(),
        }

        if old_product is None:
            state_entry[
                "first_seen"
            ] = now()

            if product["available"]:
                alert = product.copy()

                alert["reason"] = (
                    "🆕 發現新商品"
                )

                alerts.append(alert)

        else:
            state_entry[
                "first_seen"
            ] = old_product.get(
                "first_seen",
                now(),
            )

            old_available = (
                old_product.get(
                    "available",
                    True,
                )
            )

            new_available = (
                product.get(
                    "available",
                    True,
                )
            )

            if (
                not old_available
                and new_available
            ):
                alert = product.copy()

                alert["reason"] = (
                    "🔥 商品恢復供貨"
                )

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

                alert["reason"] = (
                    "💰 商品降價"
                )

                alert[
                    "old_price"
                ] = old_price

                alert[
                    "difference"
                ] = (
                    old_price
                    - new_price
                )

                alerts.append(alert)

        new_state[key] = state_entry

    # 當網站暫時抓取失敗時，
    # 保留舊資料，避免商品突然從網頁消失。
    for key, old_product in (
        old_state.items()
    ):
        if key not in current_keys:
            new_state[key] = old_product

    return alerts, new_state


# =========================================================
# 顯示結果
# =========================================================

def display_product(product):
    print()
    print("-" * 70)

    print(
        "🏪 賣場：",
        product["store"],
    )

    print(
        "🌀 商品：",
        product["name"],
    )

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
            "有貨 / 可購買"
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

    print(
        alert["reason"]
    )

    print("=" * 70)

    print(
        "🏪 賣場：",
        alert["store"],
    )

    print(
        "🌀 商品：",
        alert["name"],
    )

    if alert.get(
        "price"
    ) is not None:
        print(
            "💰 現價：NT$",
            format(
                alert["price"],
                ",",
            ),
            sep="",
        )

    if alert.get(
        "old_price"
    ) is not None:
        print(
            "原價：NT$",
            format(
                alert["old_price"],
                ",",
            ),
            sep="",
        )

    if alert.get(
        "difference"
    ) is not None:
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

    print(
        "🔥 戰鬥陀螺四站開賣監控器"
    )

    print(
        "🇹🇼 台灣時間：",
        now(),
    )

    print("=" * 70)

    old_state = load_state()

    print(
        "📚 已儲存歷史商品：",
        len(old_state),
        "筆",
    )

    all_products = []

    async with async_playwright() as playwright:
        browser = (
            await playwright.chromium.launch(
                headless=True,
                args=[
                    (
                        "--disable-blink-features"
                        "=AutomationControlled"
                    ),
                    (
                        "--disable-dev-shm-usage"
                    ),
                    "--no-sandbox",
                ],
            )
        )

        try:
            for keyword in KEYWORDS:
                print()
                print("#" * 70)

                print(
                    "🔎 搜尋關鍵字：",
                    keyword,
                )

                print("#" * 70)

                pchome_products = (
                    await asyncio.to_thread(
                        search_pchome,
                        keyword,
                    )
                )

                print(
                    "PChome：",
                    len(pchome_products),
                    "筆",
                )

                all_products.extend(
                    pchome_products
                )

                momo_products = (
                    await search_momo(
                        browser,
                        keyword,
                    )
                )

                print(
                    "momo：",
                    len(momo_products),
                    "筆",
                )

                all_products.extend(
                    momo_products
                )

                shopee_products = (
                    await search_shopee(
                        browser,
                        keyword,
                    )
                )

                print(
                    "蝦皮：",
                    len(shopee_products),
                    "筆",
                )

                all_products.extend(
                    shopee_products
                )

                eslite_products = (
                    await search_eslite(
                        browser,
                        keyword,
                    )
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
                if product["price"]
                is not None
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

    print(
        "✅ 四站監控完成"
    )

    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
