import asyncio
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import (
    parse_qsl,
    quote,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

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
HTML_FILE = "index.html"

REQUEST_TIMEOUT = 30

TAIWAN_TIMEZONE = timezone(
    timedelta(hours=8)
)

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
    """
    移除多餘追蹤參數。

    momo 的商品編號放在 i_code 或 goodsCode 參數中，
    所以必須保留，否則連結會只剩 GoodsDetail.jsp。
    """

    if not url:
        return ""

    url = html.unescape(url)
    parts = urlsplit(url)

    hostname = parts.netloc.lower()

    if "momoshop.com.tw" in hostname:
        original_query = dict(
            parse_qsl(
                parts.query,
                keep_blank_values=True,
            )
        )

        kept_query = {}

        for key in [
            "i_code",
            "goodsCode",
        ]:
            if key in original_query:
                kept_query[key] = original_query[key]

        return urlunsplit((
            parts.scheme or "https",
            parts.netloc,
            parts.path,
            urlencode(kept_query),
            "",
        ))

    return urlunsplit((
        parts.scheme or "https",
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

        elif (
            not current.get("available", False)
            and product.get("available", False)
        ):
            unique[key] = product

    return list(
        unique.values()
    )


# =========================================================
# PChome
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
# momo
# =========================================================

async def search_momo(
    browser,
    keyword,
):
    products = []
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
            'a[href*="GoodsDetail.jsp"], '
            'a[href*="goods.momo.com.tw/goods.momo"]'
        )

        count = await links.count()
        processed = set()

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

                href = await link.get_attribute(
                    "href"
                )

                if not href:
                    continue

                href = html.unescape(href)

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

                product_id = (
                    code_match.group(1)
                )

                if product_id in processed:
                    continue

                processed.add(
                    product_id
                )

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

                if (
                    "i_code="
                    not in href
                    and "GoodsDetail.jsp"
                    in href
                ):
                    separator = (
                        "&"
                        if "?" in href
                        else "?"
                    )

                    href = (
                        href
                        + separator
                        + "i_code="
                        + product_id
                    )

                products.append(
                    make_product(
                        store="momo購物網",
                        product_id=product_id,
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
            "⚠️ momo 搜尋失敗：",
            error,
        )

    finally:
        await page.close()

    return products


# =========================================================
# 蝦皮
# =========================================================

async def search_shopee(
    browser,
    keyword,
):
    products = []
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
            8000
        )

        await scroll_page(
            page,
            4,
        )

        links = page.locator(
            'a[href*="-i."]'
        )

        count = await links.count()
        processed = set()

        for index in range(
            min(count, 200)
        ):
            if (
                len(products)
                >= MAX_ITEMS_PER_STORE
            ):
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

                processed.add(
                    product_id
                )

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

                products.append(
                    make_product(
                        store="蝦皮購物",
                        product_id=product_id,
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
            "⚠️ 蝦皮搜尋失敗：",
            error,
        )

    finally:
        await page.close()

    return products


# =========================================================
# 誠品
# =========================================================

async def search_eslite(
    browser,
    keyword,
):
    products = []
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

                product_link_count = (
                    await page.locator(
                        'a[href*="/product/"]'
                    ).count()
                )

                if product_link_count > 0:
                    loaded = True
                    break

            except Exception:
                continue

        if not loaded:
            print(
                "⚠️ 誠品搜尋頁沒有找到商品連結"
            )

            return products

        await scroll_page(
            page,
            3,
        )

        links = page.locator(
            'a[href*="/product/"]'
        )

        count = await links.count()
        processed = set()

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

                processed.add(
                    product_id
                )

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

                products.append(
                    make_product(
                        store="誠品線上",
                        product_id=product_id,
                        name=name,
                        price=price,
                        url=clean_url,
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
            "⚠️ 誠品搜尋失敗：",
            error,
        )

    finally:
        await page.close()

    return products


# =========================================================
# 商品異動判斷
# =========================================================

def check_changes(
    products,
    old_state,
):
    alerts = []
    new_state = {}
    current_keys = set()

    for product in products:
        key = product_key(
            product
        )

        current_keys.add(
            key
        )

        old_product = old_state.get(
            key
        )

        state_entry = {
            "store": product["store"],
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "url": product["url"],
            "available": product[
                "available"
            ],
            "last_seen": now(),
        }

        if old_product is None:
            state_entry[
                "first_seen"
            ] = now()

            if product["available"]:
                alert = product.copy()
                alert[
                    "reason"
                ] = "🆕 發現新商品"

                alerts.append(
                    alert
                )

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

                alert[
                    "reason"
                ] = "🔥 商品恢復供貨"

                alerts.append(
                    alert
                )

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

                alert[
                    "reason"
                ] = "💰 商品降價"

                alert[
                    "old_price"
                ] = old_price

                alert[
                    "difference"
                ] = (
                    old_price
                    - new_price
                )

                alerts.append(
                    alert
                )

        new_state[key] = (
            state_entry
        )

    # 保留本次沒有抓到的舊資料
    for key, old_product in (
        old_state.items()
    ):
        if key not in current_keys:
            new_state[key] = (
                old_product
            )

    return alerts, new_state


# =========================================================
# 產生 GitHub Pages 網頁
# =========================================================

def generate_html(state):
    products = list(
        state.values()
    )

    products.sort(
        key=lambda product: (
            0
            if product.get(
                "available",
                False,
            )
            else 1,
            product.get(
                "store",
                "",
            ),
            product.get(
                "price"
            )
            if product.get(
                "price"
            ) is not None
            else 999999,
            product.get(
                "name",
                "",
            ),
        )
    )

    total_count = len(products)

    available_count = sum(
        1
        for product in products
        if product.get(
            "available",
            False,
        )
    )

    store_counts = {}

    for product in products:
        store = product.get(
            "store",
            "未知賣場",
        )

        store_counts[store] = (
            store_counts.get(
                store,
                0,
            )
            + 1
        )

    cards = []

    for product in products:
        store = html.escape(
            str(
                product.get(
                    "store",
                    "",
                )
            )
        )

        name = html.escape(
            str(
                product.get(
                    "name",
                    "",
                )
            )
        )

        product_url = html.escape(
            str(
                product.get(
                    "url",
                    "",
                )
            ),
            quote=True,
        )

        price = product.get(
            "price"
        )

        if price is None:
            price_text = "價格未辨識"
        else:
            price_text = (
                f"NT${price:,}"
            )

        available = product.get(
            "available",
            False,
        )

        if available:
            status_text = "有貨或可購買"
            status_class = "available"
        else:
            status_text = "疑似缺貨"
            status_class = "unavailable"

        last_seen = html.escape(
            str(
                product.get(
                    "last_seen",
                    "",
                )
            )
        )

        cards.append(
            f"""
            <article class="product-card"
                     data-store="{store}"
                     data-name="{name}"
                     data-available="{str(available).lower()}">

                <div class="card-top">
                    <span class="store">{store}</span>
                    <span class="status {status_class}">
                        {status_text}
                    </span>
                </div>

                <h2>{name}</h2>

                <div class="price">
                    {price_text}
                </div>

                <div class="last-seen">
                    最後確認：{last_seen}
                </div>

                <a class="buy-button"
                   href="{product_url}"
                   target="_blank"
                   rel="noopener noreferrer">
                    前往商品頁
                </a>
            </article>
            """
        )

    store_summary = "・".join(
        (
            html.escape(store)
            + " "
            + str(count)
            + " 筆"
        )
        for store, count
        in sorted(
            store_counts.items()
        )
    )

    page = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">

    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">

    <title>戰鬥陀螺開賣監控器</title>

    <style>
        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            background: #f4f6f9;
            color: #172033;
            font-family:
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                "Microsoft JhengHei",
                sans-serif;
        }}

        header {{
            color: white;
            background:
                linear-gradient(
                    135deg,
                    #101c3b,
                    #244fad
                );
            padding: 36px 20px;
        }}

        .header-inner {{
            width: min(1200px, 100%);
            margin: 0 auto;
        }}

        h1 {{
            margin: 0 0 10px;
            font-size: 30px;
        }}

        .subtitle {{
            margin: 0;
            opacity: 0.9;
            line-height: 1.7;
        }}

        main {{
            width: min(1200px, 100%);
            margin: 0 auto;
            padding: 24px 16px 60px;
        }}

        .summary {{
            display: grid;
            grid-template-columns:
                repeat(
                    auto-fit,
                    minmax(180px, 1fr)
                );
            gap: 14px;
            margin-bottom: 20px;
        }}

        .summary-card {{
            background: white;
            border-radius: 14px;
            padding: 18px;
            box-shadow:
                0 4px 18px
                rgba(27, 39, 72, 0.08);
        }}

        .summary-card strong {{
            display: block;
            font-size: 28px;
            color: #244fad;
            margin-top: 4px;
        }}

        .controls {{
            display: grid;
            grid-template-columns:
                minmax(0, 1fr)
                220px
                180px;
            gap: 12px;
            margin-bottom: 20px;
        }}

        input,
        select {{
            width: 100%;
            border: 1px solid #ccd3df;
            border-radius: 10px;
            background: white;
            padding: 12px 14px;
            font-size: 16px;
        }}

        .products {{
            display: grid;
            grid-template-columns:
                repeat(
                    auto-fill,
                    minmax(280px, 1fr)
                );
            gap: 16px;
        }}

        .product-card {{
            display: flex;
            flex-direction: column;
            background: white;
            border-radius: 16px;
            padding: 18px;
            min-height: 260px;
            box-shadow:
                0 4px 18px
                rgba(27, 39, 72, 0.08);
            border:
                1px solid
                rgba(36, 79, 173, 0.08);
        }}

        .card-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
        }}

        .store {{
            color: #244fad;
            font-weight: 700;
        }}

        .status {{
            border-radius: 999px;
            padding: 5px 9px;
            font-size: 13px;
            font-weight: 700;
        }}

        .available {{
            color: #08783f;
            background: #dcf8e9;
        }}

        .unavailable {{
            color: #9a2c2c;
            background: #fde5e5;
        }}

        .product-card h2 {{
            font-size: 17px;
            line-height: 1.55;
            margin: 16px 0;
            flex-grow: 1;
        }}

        .price {{
            color: #dc3f32;
            font-size: 24px;
            font-weight: 800;
            margin-bottom: 10px;
        }}

        .last-seen {{
            color: #697386;
            font-size: 13px;
            margin-bottom: 14px;
        }}

        .buy-button {{
            display: block;
            text-align: center;
            text-decoration: none;
            color: white;
            background: #244fad;
            border-radius: 10px;
            padding: 12px;
            font-weight: 700;
        }}

        .buy-button:hover {{
            background: #193880;
        }}

        .empty {{
            display: none;
            text-align: center;
            background: white;
            border-radius: 14px;
            padding: 40px;
            color: #697386;
        }}

        footer {{
            text-align: center;
            color: #697386;
            padding: 25px;
            font-size: 14px;
        }}

        @media (max-width: 760px) {{
            .controls {{
                grid-template-columns: 1fr;
            }}

            h1 {{
                font-size: 24px;
            }}
        }}
    </style>
</head>

<body>
    <header>
        <div class="header-inner">
            <h1>🔥 戰鬥陀螺開賣監控器</h1>

            <p class="subtitle">
                監控 PChome、momo、蝦皮與誠品<br>
                最後更新：{html.escape(now())}
            </p>
        </div>
    </header>

    <main>
        <section class="summary">
            <div class="summary-card">
                商品紀錄
                <strong>{total_count}</strong>
            </div>

            <div class="summary-card">
                有貨或可購買
                <strong>{available_count}</strong>
            </div>

            <div class="summary-card">
                賣場
                <strong>{len(store_counts)}</strong>
            </div>
        </section>

        <p>
            {store_summary}
        </p>

        <section class="controls">
            <input
                id="searchInput"
                type="search"
                placeholder="搜尋 CX、UX、BX 或商品名稱">

            <select id="storeFilter">
                <option value="">全部賣場</option>
                <option value="PChome 24h">PChome 24h</option>
                <option value="momo購物網">momo購物網</option>
                <option value="蝦皮購物">蝦皮購物</option>
                <option value="誠品線上">誠品線上</option>
            </select>

            <select id="stockFilter">
                <option value="">全部狀態</option>
                <option value="true">有貨或可購買</option>
                <option value="false">疑似缺貨</option>
            </select>
        </section>

        <section
            id="productList"
            class="products">
            {''.join(cards)}
        </section>

        <div
            id="emptyMessage"
            class="empty">
            沒有符合目前篩選條件的商品。
        </div>
    </main>

    <footer>
        本頁由 GitHub Actions 自動更新。
        商品價格與庫存請以購物網站實際頁面為準。
    </footer>

    <script>
        const searchInput =
            document.getElementById("searchInput");

        const storeFilter =
            document.getElementById("storeFilter");

        const stockFilter =
            document.getElementById("stockFilter");

        const cards =
            Array.from(
                document.querySelectorAll(
                    ".product-card"
                )
            );

        const emptyMessage =
            document.getElementById(
                "emptyMessage"
            );

        function applyFilters() {{
            const keyword =
                searchInput.value
                    .trim()
                    .toLowerCase();

            const selectedStore =
                storeFilter.value;

            const selectedStock =
                stockFilter.value;

            let visibleCount = 0;

            cards.forEach((card) => {{
                const name =
                    card.dataset.name
                        .toLowerCase();

                const store =
                    card.dataset.store;

                const available =
                    card.dataset.available;

                const keywordMatch =
                    !keyword
                    || name.includes(keyword)
                    || store
                        .toLowerCase()
                        .includes(keyword);

                const storeMatch =
                    !selectedStore
                    || store === selectedStore;

                const stockMatch =
                    !selectedStock
                    || available === selectedStock;

                const visible =
                    keywordMatch
                    && storeMatch
                    && stockMatch;

                card.style.display =
                    visible
                    ? "flex"
                    : "none";

                if (visible) {{
                    visibleCount += 1;
                }}
            }});

            emptyMessage.style.display =
                visibleCount === 0
                ? "block"
                : "none";
        }}

        searchInput.addEventListener(
            "input",
            applyFilters
        );

        storeFilter.addEventListener(
            "change",
            applyFilters
        );

        stockFilter.addEventListener(
            "change",
            applyFilters
        );
    </script>
</body>
</html>
"""

    with open(
        HTML_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        file.write(page)

    print(
        "🌐 已產生",
        HTML_FILE,
    )


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

    if product["price"] is None:
        print(
            "💰 價格：未能辨識"
        )
    else:
        print(
            "💰 價格：NT$",
            format(
                product["price"],
                ",",
            ),
            sep="",
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


# =========================================================
# 主程式
# =========================================================

async def main():
    print("=" * 70)
    print("🔥 戰鬥陀螺四站監控器")
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
        browser = (
            await playwright.chromium.launch(
                headless=True,
                args=[
                    (
                        "--disable-blink-features="
                        "AutomationControlled"
                    ),
                    "--disable-dev-shm-usage",
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
        display_product(
            product
        )

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
            display_alert(
                alert
            )

    else:
        print()
        print(
            "😴 沒有新品、恢復供貨或降價。"
        )

    save_state(
        new_state
    )

    print()
    print(
        "💾 已更新",
        STATE_FILE,
    )

    generate_html(
        new_state
    )

    print()
    print("=" * 70)
    print("✅ 四站監控完成")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(
        main()
    )
