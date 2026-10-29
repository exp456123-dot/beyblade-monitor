import asyncio
import re
from datetime import datetime
from urllib.parse import quote, urljoin

from playwright.async_api import async_playwright


# ============================================================
# 搜尋設定
# ============================================================

SEARCH_KEYWORDS = [
    "BEYBLADE X",
    "戰鬥陀螺",
    "CX-",
    "UX-",
    "BX-",
]

PRODUCT_WORDS = [
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
    "美國版",
    "海外直送",
    "海外賣家",
    "日版",
    "日本版",
    "韓版",
    "漫畫",
    "電子書",
    "小說",
    "童書",
    "雜誌",
    "書籍",
    "攻略本",
    "附錄",
    "ガイド",
    "GUIDE",
    "VOL.",
    "ISBN",
    "貼紙",
    "收納盒",
    "收納包",
    "展示盒",
    "保護套",
    "零件拆賣",
    "單售零件",
]

UNAVAILABLE_WORDS = [
    "已售完",
    "售完",
    "已絕版",
    "缺貨",
    "暫無庫存",
    "補貨中",
    "可訂購時通知我",
    "貨到通知",
    "到貨通知",
    "停止銷售",
    "商品已下架",
    "暫停販售",
]

AVAILABLE_WORDS = [
    "加入購物車",
    "放入購物車",
    "立即購買",
    "直接購買",
    "我要購買",
]

MIN_PRICE = 100
MAX_PRICE = 5000

# 搜尋頁最多保留多少筆候選商品
MAX_CANDIDATES_PER_STORE = 30

# 商品詳情頁同時檢查數量
DETAIL_CONCURRENCY = 4

# 商品詳情頁載入上限
DETAIL_TIMEOUT_MS = 30000

SHOPEE_STORES = [
    {
        "store": "蝦皮 Funbox",
        "shop_url": "https://shopee.tw/funbox5120",
    },
    {
        "store": "蝦皮 玩具e哥",
        "shop_url": "https://shopee.tw/toyego",
    },
]


# ============================================================
# 共用文字功能
# ============================================================

def clean_text(text):
    if not text:
        return ""

    return re.sub(r"\s+", " ", text).strip()


def parse_price(text):
    if not text:
        return None

    patterns = [
        r"NT\$\s*([\d,]+)",
        r"NTD\s*([\d,]+)",
        r"\$\s*([\d,]+)",
        r"售價\s*[:：]?\s*([\d,]+)",
        r"特價\s*[:：]?\s*([\d,]+)",
        r"優惠價\s*[:：]?\s*([\d,]+)",
        r"網路價\s*[:：]?\s*([\d,]+)",
        r"促銷價\s*[:：]?\s*([\d,]+)",
        r"會員價\s*[:：]?\s*([\d,]+)",
    ]

    prices = []

    for pattern in patterns:
        matches = re.findall(
            pattern,
            text,
            re.IGNORECASE,
        )

        for match in matches:
            try:
                price = int(
                    match.replace(",", "")
                )

                if MIN_PRICE <= price <= MAX_PRICE:
                    prices.append(price)

            except ValueError:
                continue

    if not prices:
        return None

    # 當頁面同時顯示原價和優惠價時，優先採用最低有效價格
    return min(prices)


def parse_numeric_price(value):
    if not value:
        return None

    match = re.search(
        r"([\d,]+(?:\.\d+)?)",
        str(value),
    )

    if not match:
        return None

    try:
        price = int(
            float(
                match.group(1).replace(",", "")
            )
        )

        if MIN_PRICE <= price <= MAX_PRICE:
            return price

    except ValueError:
        pass

    return None


def is_beyblade_product(name, full_text=""):
    combined = clean_text(
        f"{name} {full_text}"
    )

    upper_text = combined.upper()

    has_product_word = any(
        word.upper() in upper_text
        for word in PRODUCT_WORDS
    )

    has_excluded_word = any(
        word.upper() in upper_text
        for word in EXCLUDE_WORDS
    )

    return (
        has_product_word
        and not has_excluded_word
    )


def contains_unavailable_word(text):
    upper_text = clean_text(text).upper()

    return any(
        word.upper() in upper_text
        for word in UNAVAILABLE_WORDS
    )


def contains_available_word(text):
    upper_text = clean_text(text).upper()

    return any(
        word.upper() in upper_text
        for word in AVAILABLE_WORDS
    )


def normalize_url(base_url, href):
    if not href:
        return ""

    href = href.strip()

    if href.startswith("//"):
        href = "https:" + href

    elif href.startswith("/"):
        href = urljoin(
            base_url,
            href,
        )

    elif not href.startswith("http"):
        href = urljoin(
            base_url,
            href,
        )

    # momo 商品編號位於網址參數中，不可移除
    if "momoshop.com.tw" in href:
        return href.split("#")[0]

    return (
        href
        .split("?")[0]
        .split("#")[0]
    )


def add_product(
    products,
    product,
    seen_urls,
):
    url = product.get("url", "")

    if not url:
        return

    if url in seen_urls:
        return

    seen_urls.add(url)
    products.append(product)


def print_products(
    store_name,
    products,
):
    print()
    print("=" * 76)
    print(f"【{store_name}】")
    print(
        f"找到 {len(products)} 筆"
        "目前可購買的戰鬥陀螺商品"
    )
    print("=" * 76)

    if not products:
        print("目前沒有確認到可購買的商品。")
        return

    for index, product in enumerate(
        products,
        start=1,
    ):
        print()
        print(
            f"{index}. {product['name']}"
        )

        price = product.get("price")

        if price is not None:
            print(
                f"   價格：NT${price:,}"
            )
        else:
            print("   價格：商品頁未能辨識")

        print("   狀態：可購買")
        print(
            f"   連結：{product['url']}"
        )


# ============================================================
# Playwright 共用功能
# ============================================================

async def create_page(browser):
    page = await browser.new_page(
        viewport={
            "width": 1440,
            "height": 1200,
        },
        locale="zh-TW",
        user_agent=(
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/130.0.0.0 "
            "Safari/537.36"
        ),
    )

    await page.set_extra_http_headers({
        "Accept-Language":
            "zh-TW,zh;q=0.9,en;q=0.8",
    })

    return page


async def scroll_page(
    page,
    times=4,
):
    for _ in range(times):
        await page.mouse.wheel(
            0,
            1000,
        )

        await page.wait_for_timeout(
            800
        )


async def extract_card_text(link):
    texts = []

    try:
        link_text = clean_text(
            await link.inner_text()
        )

        if link_text:
            texts.append(link_text)

    except Exception:
        pass

    for level in [1, 2, 3, 4]:
        try:
            card = link.locator(
                f"xpath=ancestor::*[{level}]"
            )

            if await card.count() > 0:
                text = clean_text(
                    await card.first.inner_text()
                )

                if text:
                    texts.append(text)

        except Exception:
            continue

    if not texts:
        return ""

    return max(
        texts,
        key=len,
    )


def find_product_name(
    link_text,
    card_text,
):
    link_text = clean_text(link_text)

    if len(link_text) >= 5:
        return link_text[:300]

    possible_lines = []

    for line in card_text.splitlines():
        line = clean_text(line)

        if len(line) < 5:
            continue

        if re.fullmatch(
            r"(?:NT\$|\$)?\s*[\d,]+(?:元)?",
            line,
        ):
            continue

        if line in [
            "加入購物車",
            "立即購買",
            "查看商品",
            "查看更多",
        ]:
            continue

        possible_lines.append(line)

    if not possible_lines:
        return ""

    return max(
        possible_lines,
        key=len,
    )[:300]


# ============================================================
# 詳情頁價格與庫存讀取
# ============================================================

async def read_meta_price(page):
    meta_selectors = [
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
        'meta[itemprop="price"]',
        '[itemprop="price"]',
    ]

    for selector in meta_selectors:
        try:
            element = page.locator(
                selector
            ).first

            if await element.count() == 0:
                continue

            value = await element.get_attribute(
                "content"
            )

            if not value:
                value = await element.get_attribute(
                    "value"
                )

            if not value:
                value = await element.get_attribute(
                    "data-price"
                )

            if not value:
                value = clean_text(
                    await element.inner_text()
                )

            price = parse_numeric_price(value)

            if price is not None:
                return price

        except Exception:
            continue

    return None


async def read_selector_price(
    page,
    selectors,
):
    for selector in selectors:
        try:
            locator = page.locator(selector)

            count = min(
                await locator.count(),
                10,
            )

            for index in range(count):
                element = locator.nth(index)

                text = clean_text(
                    await element.inner_text()
                )

                price = parse_price(text)

                if price is not None:
                    return price

                content = await element.get_attribute(
                    "content"
                )

                price = parse_numeric_price(
                    content
                )

                if price is not None:
                    return price

        except Exception:
            continue

    return None


async def inspect_product_page(
    browser,
    candidate,
    semaphore,
):
    async with semaphore:
        page = await create_page(browser)

        try:
            await page.goto(
                candidate["url"],
                wait_until="domcontentloaded",
                timeout=DETAIL_TIMEOUT_MS,
            )

            await page.wait_for_timeout(
                3500
            )

            # 稍微捲動，觸發延遲載入的價格與購物按鈕
            await page.mouse.wheel(
                0,
                500,
            )

            await page.wait_for_timeout(
                1000
            )

            body_text = clean_text(
                await page.locator(
                    "body"
                ).inner_text()
            )

            if contains_unavailable_word(
                body_text
            ):
                print(
                    "缺貨或不可購買："
                    f"{candidate['name']}"
                )
                return None

            available = contains_available_word(
                body_text
            )

            # 搜尋商品頁上的可用購物按鈕
            button_selectors = [
                'button:has-text("加入購物車")',
                'button:has-text("放入購物車")',
                'button:has-text("立即購買")',
                'a:has-text("加入購物車")',
                'a:has-text("立即購買")',
            ]

            for selector in button_selectors:
                try:
                    buttons = page.locator(
                        selector
                    )

                    count = await buttons.count()

                    for index in range(count):
                        button = buttons.nth(index)

                        if (
                            await button.is_visible()
                            and await button.is_enabled()
                        ):
                            available = True
                            break

                    if available:
                        break

                except Exception:
                    continue

            price = await read_meta_price(
                page
            )

            if price is None:
                if candidate["store"] == "momo Funbox":
                    price_selectors = [
                        ".price",
                        ".money",
                        ".specialPrice",
                        ".goodsPrice",
                        ".priceTxt",
                        ".prdPrice",
                        '[class*="price"]',
                        '[id*="price"]',
                    ]

                elif candidate["store"] == "誠品線上":
                    price_selectors = [
                        ".price",
                        ".sale-price",
                        ".product-price",
                        ".discount-price",
                        '[class*="price"]',
                        '[id*="price"]',
                    ]

                else:
                    price_selectors = [
                        '[class*="price"]',
                        '[id*="price"]',
                    ]

                price = await read_selector_price(
                    page,
                    price_selectors,
                )

            if price is None:
                price = parse_price(
                    body_text
                )

            # 必須有可購買按鈕才視為開賣。
            # 價格讀不到時仍保留，避免漏掉已上架商品。
            if not available:
                print(
                    "未確認購物按鈕："
                    f"{candidate['name']}"
                )
                return None

            result = candidate.copy()
            result["price"] = price
            result["available"] = True

            print(
                "確認可購買："
                f"{candidate['name']}"
                f" / 價格：{price}"
            )

            return result

        except Exception as error:
            print(
                "商品頁檢查失敗："
                f"{candidate['name']} / "
                f"{error}"
            )

            return None

        finally:
            await page.close()


async def inspect_candidates(
    browser,
    candidates,
):
    semaphore = asyncio.Semaphore(
        DETAIL_CONCURRENCY
    )

    tasks = [
        inspect_product_page(
            browser,
            candidate,
            semaphore,
        )
        for candidate in candidates
    ]

    if not tasks:
        return []

    results = await asyncio.gather(
        *tasks
    )

    return [
        result
        for result in results
        if result is not None
    ]


# ============================================================
# momo 商品編號與網址
# ============================================================

async def get_momo_goods_code(
    link,
    href,
):
    if href:
        patterns = [
            r"[?&]i_code=(\d+)",
            r"[?&]goodsCode=(\d+)",
            r"[?&]goods_code=(\d+)",
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                href,
                re.IGNORECASE,
            )

            if match:
                return match.group(1)

    attributes = [
        "data-goodscode",
        "data-goods-code",
        "data-goodsno",
        "data-goods-no",
        "data-icode",
        "data-i-code",
    ]

    for attribute in attributes:
        try:
            value = await link.get_attribute(
                attribute
            )

            if value:
                match = re.search(
                    r"\d+",
                    value,
                )

                if match:
                    return match.group(0)

        except Exception:
            continue

    try:
        parent = link.locator(
            "xpath=ancestor::*["
            "@data-goodscode or "
            "@data-goods-code or "
            "@data-goodsno or "
            "@data-goods-no or "
            "@data-icode or "
            "@data-i-code"
            "][1]"
        )

        if await parent.count() > 0:
            for attribute in attributes:
                value = (
                    await parent.first.get_attribute(
                        attribute
                    )
                )

                if value:
                    match = re.search(
                        r"\d+",
                        value,
                    )

                    if match:
                        return match.group(0)

    except Exception:
        pass

    try:
        onclick = await link.get_attribute(
            "onclick"
        )

        if onclick:
            onclick_patterns = [
                r"i_code[=:'\"\s]+(\d+)",
                r"goodsCode[=:'\"\s]+(\d+)",
                r"GoodsDetail\.jsp\?i_code=(\d+)",
            ]

            for pattern in onclick_patterns:
                match = re.search(
                    pattern,
                    onclick,
                    re.IGNORECASE,
                )

                if match:
                    return match.group(1)

    except Exception:
        pass

    return None


def build_momo_url(
    href,
    goods_code,
):
    if goods_code:
        return (
            "https://www.momoshop.com.tw/"
            "goods/GoodsDetail.jsp"
            f"?i_code={goods_code}"
        )

    product_url = normalize_url(
        "https://www.momoshop.com.tw",
        href,
    )

    if (
        product_url.endswith(
            "GoodsDetail.jsp"
        )
        or product_url.endswith(
            "GoodsDetail.jsp?"
        )
    ):
        return ""

    return product_url


# ============================================================
# momo Funbox 搜尋
# ============================================================

async def search_momo_funbox(browser):
    store_name = "momo Funbox"

    candidates = []
    seen_urls = set()

    for keyword in SEARCH_KEYWORDS:
        page = await create_page(browser)

        search_keyword = (
            f"Funbox {keyword}"
        )

        url = (
            "https://www.momoshop.com.tw/"
            "search/searchShop.jsp"
            f"?keyword={quote(search_keyword)}"
        )

        print()
        print(
            f"搜尋 {store_name}："
            f"{search_keyword}"
        )

        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(
                5000
            )

            await scroll_page(
                page,
                times=4,
            )

            links = page.locator(
                'a[href*="GoodsDetail"], '
                'a[href*="/goods/"], '
                'a[href*="goods.momo"], '
                'a[data-goodscode], '
                'a[data-goods-code], '
                'a[data-goodsno]'
            )

            link_count = await links.count()

            print(
                f"讀取到 {link_count} 個"
                "可能的 momo 商品連結"
            )

            for index in range(
                min(link_count, 250)
            ):
                if (
                    len(candidates)
                    >= MAX_CANDIDATES_PER_STORE
                ):
                    break

                link = links.nth(index)

                try:
                    href = (
                        await link.get_attribute(
                            "href"
                        )
                    )

                    goods_code = (
                        await get_momo_goods_code(
                            link,
                            href,
                        )
                    )

                    product_url = build_momo_url(
                        href,
                        goods_code,
                    )

                    if not product_url:
                        continue

                    if product_url in seen_urls:
                        continue

                    link_text = clean_text(
                        await link.inner_text()
                    )

                    card_text = (
                        await extract_card_text(
                            link
                        )
                    )

                    full_text = clean_text(
                        f"{link_text} "
                        f"{card_text}"
                    )

                    name = find_product_name(
                        link_text,
                        card_text,
                    )

                    if not name:
                        continue

                    if not is_beyblade_product(
                        name,
                        full_text,
                    ):
                        continue

                    add_product(
                        candidates,
                        {
                            "store": store_name,
                            "name": name,
                            "price": None,
                            "url": product_url,
                        },
                        seen_urls,
                    )

                except Exception:
                    continue

        except Exception as error:
            print(
                f"{store_name} "
                f"搜尋失敗：{error}"
            )

        finally:
            await page.close()

    print(
        f"{store_name} 候選商品："
        f"{len(candidates)} 筆"
    )

    return await inspect_candidates(
        browser,
        candidates,
    )


# ============================================================
# 誠品線上搜尋
# ============================================================

async def search_eslite(browser):
    store_name = "誠品線上"

    candidates = []
    seen_urls = set()

    eslite_required_words = [
        "戰鬥陀螺",
        "CX-",
        "UX-",
        "BX-",
    ]

    for keyword in SEARCH_KEYWORDS:
        page = await create_page(browser)

        url = (
            "https://www.eslite.com/Search"
            f"?keyword={quote(keyword)}"
        )

        print()
        print(
            f"搜尋 {store_name}："
            f"{keyword}"
        )

        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(
                5000
            )

            await scroll_page(
                page,
                times=4,
            )

            links = page.locator(
                'a[href*="/product/"]'
            )

            link_count = (
                await links.count()
            )

            print(
                f"讀取到 {link_count} 個"
                "可能的誠品商品連結"
            )

            for index in range(
                min(link_count, 200)
            ):
                if (
                    len(candidates)
                    >= MAX_CANDIDATES_PER_STORE
                ):
                    break

                link = links.nth(index)

                try:
                    href = (
                        await link.get_attribute(
                            "href"
                        )
                    )

                    if not href:
                        continue

                    product_url = normalize_url(
                        "https://www.eslite.com",
                        href,
                    )

                    if product_url in seen_urls:
                        continue

                    link_text = clean_text(
                        await link.inner_text()
                    )

                    card_text = (
                        await extract_card_text(
                            link
                        )
                    )

                    full_text = clean_text(
                        f"{link_text} "
                        f"{card_text}"
                    )

                    name = find_product_name(
                        link_text,
                        card_text,
                    )

                    if not name:
                        continue

                    has_toy_code = any(
                        word.upper()
                        in full_text.upper()
                        for word
                        in eslite_required_words
                    )

                    if not has_toy_code:
                        continue

                    if not is_beyblade_product(
                        name,
                        full_text,
                    ):
                        continue

                    add_product(
                        candidates,
                        {
                            "store": store_name,
                            "name": name,
                            "price": None,
                            "url": product_url,
                        },
                        seen_urls,
                    )

                except Exception:
                    continue

        except Exception as error:
            print(
                f"{store_name} "
                f"搜尋失敗：{error}"
            )

        finally:
            await page.close()

    print(
        f"{store_name} 候選商品："
        f"{len(candidates)} 筆"
    )

    return await inspect_candidates(
        browser,
        candidates,
    )


# ============================================================
# 蝦皮指定賣場搜尋
# ============================================================

async def search_shopee_store(
    browser,
    store,
):
    store_name = store["store"]
    shop_url = store["shop_url"]

    products = []
    seen_urls = set()

    for keyword in SEARCH_KEYWORDS:
        page = await create_page(browser)

        url = (
            f"{shop_url}/search"
            f"?keyword={quote(keyword)}"
        )

        print()
        print(
            f"搜尋 {store_name}："
            f"{keyword}"
        )
        print(
            f"指定賣場：{shop_url}"
        )

        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(
                8000
            )

            await scroll_page(
                page,
                times=5,
            )

            links = page.locator(
                'a[href*="-i."], '
                'a[href*="/product/"]'
            )

            link_count = (
                await links.count()
            )

            print(
                f"讀取到 {link_count} 個"
                "可能的蝦皮商品連結"
            )

            for index in range(
                min(link_count, 200)
            ):
                if (
                    len(products)
                    >= MAX_CANDIDATES_PER_STORE
                ):
                    break

                link = links.nth(index)

                try:
                    href = (
                        await link.get_attribute(
                            "href"
                        )
                    )

                    if not href:
                        continue

                    product_url = normalize_url(
                        "https://shopee.tw",
                        href,
                    )

                    if product_url in seen_urls:
                        continue

                    link_text = clean_text(
                        await link.inner_text()
                    )

                    card_text = (
                        await extract_card_text(
                            link
                        )
                    )

                    full_text = clean_text(
                        f"{link_text} {card_text}"
                    )

                    name = find_product_name(
                        link_text,
                        card_text,
                    )

                    if not name:
                        continue

                    if not is_beyblade_product(
                        name,
                        full_text,
                    ):
                        continue

                    if contains_unavailable_word(
                        full_text
                    ):
                        continue

                    price = parse_price(
                        full_text
                    )

                    add_product(
                        products,
                        {
                            "store": store_name,
                            "name": name,
                            "price": price,
                            "url": product_url,
                        },
                        seen_urls,
                    )

                except Exception:
                    continue

        except Exception as error:
            print(
                f"{store_name} "
                f"搜尋失敗：{error}"
            )

        finally:
            await page.close()

    return products


# ============================================================
# 主程式
# ============================================================

async def main():
    print("=" * 76)
    print("戰鬥陀螺指定賣場開賣搜尋器")
    print(
        "檢查時間：",
        datetime.now(),
    )
    print("=" * 76)

    print()
    print("搜尋範圍：")
    print("1. momo Funbox")
    print("2. 誠品線上")
    print("3. 蝦皮 Funbox")
    print("4. 蝦皮 玩具e哥")
    print()
    print(
        "PChome 及其他賣場"
        "不在搜尋範圍內。"
    )

    momo_products = []
    eslite_products = []
    shopee_results = {}

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
            momo_products = (
                await search_momo_funbox(
                    browser
                )
            )

            eslite_products = (
                await search_eslite(
                    browser
                )
            )

            for store in SHOPEE_STORES:
                products = (
                    await search_shopee_store(
                        browser,
                        store,
                    )
                )

                shopee_results[
                    store["store"]
                ] = products

        finally:
            await browser.close()

    print_products(
        "momo Funbox",
        momo_products,
    )

    print_products(
        "誠品線上",
        eslite_products,
    )

    for (
        store_name,
        products,
    ) in shopee_results.items():
        print_products(
            store_name,
            products,
        )

    total = (
        len(momo_products)
        + len(eslite_products)
        + sum(
            len(products)
            for products
            in shopee_results.values()
        )
    )

    print()
    print("=" * 76)
    print(
        "全部指定賣場共確認 "
        f"{total} 筆目前可購買商品"
    )
    print("搜尋完成")
    print("=" * 76)


if __name__ == "__main__":
    asyncio.run(main())
