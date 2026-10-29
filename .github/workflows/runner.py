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
            locator = link
