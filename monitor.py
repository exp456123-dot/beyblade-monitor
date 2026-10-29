import asyncio
import json
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
    "BEYBLADE X",
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

PRICE_IGNORE_WORDS = [
    "分期",
    "每期",
    "折價券",
    "折扣碼",
    "折抵",
    "運費",
    "回饋",
    "購物金",
    "紅利",
    "點數",
    "最高折",
    "現省",
    "滿額",
    "加購",
]

MIN_PRICE = 100
MAX_PRICE = 5000

MAX_CANDIDATES_PER_STORE = 30
DETAIL_CONCURRENCY = 4
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
# 共用文字與價格功能
# ============================================================

def clean_text(text):
    if not text:
        return ""

    return re.sub(r"\s+", " ", str(text)).strip()


def is_valid_price(price):
    return (
        isinstance(price, int)
        and MIN_PRICE <= price <= MAX_PRICE
    )


def parse_numeric_price(value):
    if value is None:
        return None

    text = str(value).strip()

    match = re.search(
        r"([\d,]+(?:\.\d+)?)",
        text,
    )

    if not match:
        return None

    try:
        price = int(
            float(
                match.group(1).replace(",", "")
            )
        )

        if is_valid_price(price):
            return price

    except (ValueError, TypeError):
        pass

    return None


def extract_prices(text):
    if not text:
        return []

    text = clean_text(text)

    patterns = [
        r"優惠價\s*[:：]?\s*(?:NT\$|NTD|\$)?\s*([\d,]+)",
        r"促銷價\s*[:：]?\s*(?:NT\$|NTD|\$)?\s*([\d,]+)",
        r"特價\s*[:：]?\s*(?:NT\$|NTD|\$)?\s*([\d,]+)",
        r"網路價\s*[:：]?\s*(?:NT\$|NTD|\$)?\s*([\d,]+)",
        r"會員價\s*[:：]?\s*(?:NT\$|NTD|\$)?\s*([\d,]+)",
        r"售價\s*[:：]?\s*(?:NT\$|NTD|\$)?\s*([\d,]+)",
        r"商品價\s*[:：]?\s*(?:NT\$|NTD|\$)?\s*([\d,]+)",
        r"NT\$\s*([\d,]+)",
        r"NTD\s*([\d,]+)",
        r"\$\s*([\d,]+)",
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

                if is_valid_price(price):
                    prices.append(price)

            except (ValueError, TypeError):
                continue

    return list(dict.fromkeys(prices))


def parse_price(text):
    """
    只適合讀取單一價格元件或商品卡片。
    不要用來掃描整個商品頁 body。
    """
    prices = extract_prices(text)

    if not prices:
        return None

    return prices[0]


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

            price_source = product.get(
                "price_source"
            )

            if price_source:
                print(
                    f"   價格來源：{price_source}"
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

    raw_lines = re.split(
        r"[\r\n]+",
        str(card_text),
    )

    for line in raw_lines:
        line = clean_text(line)

        if len(line) < 5:
            continue

        if re.fullmatch(
            r"(?:NT\$|NTD|\$)?\s*[\d,]+(?:元)?",
            line,
            re.IGNORECASE,
        ):
            continue

        if line in [
            "加入購物車",
            "放入購物車",
            "立即購買",
            "直接購買",
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
# JSON-LD 價格讀取
# ============================================================

def find_json_prices(data):
    results = []

    if isinstance(data, dict):
        data_type = data.get("@type", "")

        if isinstance(data_type, list):
            type_names = [
                str(item).lower()
                for item in data_type
            ]
        else:
            type_names = [
                str(data_type).lower()
            ]

        is_product_or_offer = any(
            item in [
                "offer",
                "aggregateoffer",
                "product",
            ]
            for item in type_names
        )

        if is_product_or_offer:
            for key in [
                "price",
                "lowPrice",
                "salePrice",
            ]:
                price = parse_numeric_price(
                    data.get(key)
                )

                if price is not None:
                    results.append(price)

        price_specification = data.get(
            "priceSpecification"
        )

        if isinstance(
            price_specification,
            dict,
        ):
            price = parse_numeric_price(
                price_specification.get("price")
            )

            if price is not None:
                results.append(price)

        offers = data.get("offers")

        if offers is not None:
            results.extend(
                find_json_prices(offers)
            )

        for key, value in data.items():
            if key in [
                "offers",
                "priceSpecification",
            ]:
                continue

            if isinstance(
                value,
                (dict, list),
            ):
                results.extend(
                    find_json_prices(value)
                )

    elif isinstance(data, list):
        for item in data:
            results.extend(
                find_json_prices(item)
            )

    return results


async def read_json_ld_price(page):
    scripts = page.locator(
        'script[type="application/ld+json"]'
    )

    try:
        count = min(
            await scripts.count(),
            20,
        )
    except Exception:
        return None

    all_prices = []

    for index in range(count):
        try:
            raw_text = await scripts.nth(
                index
            ).text_content()

            if not raw_text:
                continue

            raw_text = raw_text.strip()

            if raw_text.startswith("<!--"):
                raw_text = raw_text.replace(
                    "<!--",
                    "",
                    1,
                )

            if raw_text.endswith("-->"):
                raw_text = raw_text[:-3]

            data = json.loads(
                raw_text.strip()
            )

            all_prices.extend(
                find_json_prices(data)
            )

        except (
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            continue

        except Exception:
            continue

    valid_prices = [
        price
        for price in all_prices
        if is_valid_price(price)
    ]

    if not valid_prices:
        return None

    return min(valid_prices)


# ============================================================
# Meta 與價格元件讀取
# ============================================================

async def read_meta_price(page):
    meta_selectors = [
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
        'meta[name="twitter:data1"]',
        'meta[itemprop="price"]',
        '[itemprop="price"][content]',
    ]

    for selector in meta_selectors:
        try:
            elements = page.locator(
                selector
            )

            count = min(
                await elements.count(),
                10,
            )

            for index in range(count):
                element = elements.nth(index)

                values = [
                    await element.get_attribute(
                        "content"
                    ),
                    await element.get_attribute(
                        "value"
                    ),
                    await element.get_attribute(
                        "data-price"
                    ),
                ]

                for value in values:
                    price = parse_numeric_price(
                        value
                    )

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
            locator = page.locator(
                selector
            )

            count = min(
                await locator.count(),
                20,
            )

            for index in range(count):
                element = locator.nth(index)

                try:
                    if not await element.is_visible():
                        continue
                except Exception:
                    continue

                element_text = clean_text(
                    await element.inner_text()
                )

                if any(
                    word in element_text
                    for word in PRICE_IGNORE_WORDS
                ):
                    continue

                attribute_values = [
                    await element.get_attribute(
                        "content"
                    ),
                    await element.get_attribute(
                        "data-price"
                    ),
                    await element.get_attribute(
                        "value"
                    ),
                ]

                for value in attribute_values:
                    price = parse_numeric_price(
                        value
                    )

                    if price is not None:
                        return price

                price = parse_price(
                    element_text
                )

                if price is not None:
                    return price

        except Exception:
            continue

    return None


def get_price_selectors(store_name):
    if store_name == "momo Funbox":
        return [
            "#goodsInfo .specialPrice",
            "#goodsInfo .goodsPrice",
            "#goodsInfo .priceTxt",
            "#goodsInfo .prdPrice",
            "#goodsInfo .price",
            ".prdnoteArea .specialPrice",
            ".prdnoteArea .goodsPrice",
            ".prdnoteArea .price",
            ".prdnoteArea .money",
            ".goodsInfoArea .specialPrice",
            ".goodsInfoArea .goodsPrice",
            ".goodsInfoArea .price",
            ".goodsInfoArea .money",
            ".goodsPrice",
            ".specialPrice",
            ".priceTxt",
            ".prdPrice",
            '[itemprop="price"]',
        ]

    if store_name == "誠品線上":
        return [
            ".product-detail .sale-price",
            ".product-detail .discount-price",
            ".product-detail .product-price",
            ".product-detail .price",
            ".product-info .sale-price",
            ".product-info .discount-price",
            ".product-info .product-price",
            ".product-info .price",
            ".sale-price",
            ".discount-price",
            ".product-price",
            '[itemprop="price"]',
        ]

    if store_name.startswith("蝦皮"):
        return [
            '[data-testid="product-price"]',
            '[data-testid*="price"]',
            'main [class*="product"] [class*="price"]',
            'main [class*="price"]',
            '[itemprop="price"]',
        ]

    return [
        '[itemprop="price"]',
        '[data-testid*="price"]',
        'main [class*="price"]',
    ]


# ============================================================
# 商品頁庫存與價格檢查
# ============================================================

async def has_available_button(page):
    button_selectors = [
        'button:has-text("加入購物車")',
        'button:has-text("放入購物車")',
        'button:has-text("立即購買")',
        'button:has-text("直接購買")',
        'button:has-text("我要購買")',
        'a:has-text("加入購物車")',
        'a:has-text("放入購物車")',
        'a:has-text("立即購買")',
        'a:has-text("直接購買")',
        'a:has-text("我要購買")',
    ]

    for selector in button_selectors:
        try:
            buttons = page.locator(
                selector
            )

            count = min(
                await buttons.count(),
                20,
            )

            for index in range(count):
                button = buttons.nth(index)

                try:
                    if not await button.is_visible():
                        continue

                    disabled = (
                        await button.get_attribute(
                            "disabled"
                        )
                    )

                    aria_disabled = (
                        await button.get_attribute(
                            "aria-disabled"
                        )
                    )

                    class_name = (
                        await button.get_attribute(
                            "class"
                        )
                        or ""
                    )

                    if disabled is not None:
                        continue

                    if (
                        aria_disabled
                        and aria_disabled.lower()
                        == "true"
                    ):
                        continue

                    if (
                        "disabled"
                        in class_name.lower()
                    ):
                        continue

                    return True

                except Exception:
                    continue

        except Exception:
            continue

    return False


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

            available = await has_available_button(
                page
            )

            if not available:
                print(
                    "未確認可用購物按鈕："
                    f"{candidate['name']}"
                )
                return None

            price = None
            price_source = ""

            # 第一順位：JSON-LD 結構化資料
            price = await read_json_ld_price(
                page
            )

            if price is not None:
                price_source = "JSON-LD"

            # 第二順位：商品價格 Meta
            if price is None:
                price = await read_meta_price(
                    page
                )

                if price is not None:
                    price_source = "Meta"

            # 第三順位：商品頁主價格元件
            if price is None:
                price_selectors = (
                    get_price_selectors(
                        candidate["store"]
                    )
                )

                price = await read_selector_price(
                    page,
                    price_selectors,
                )

                if price is not None:
                    price_source = "商品價格元件"

            # 最後才使用搜尋結果卡片價格
            if price is None:
                candidate_price = candidate.get(
                    "price"
                )

                if is_valid_price(
                    candidate_price
                ):
                    price = candidate_price
                    price_source = "搜尋結果卡片"

            result = candidate.copy()
            result["price"] = price
            result["available"] = True
            result["price_source"] = price_source

            if price is not None:
                print(
                    "確認可購買："
                    f"{candidate['name']} / "
                    f"價格：NT${price:,} / "
                    f"來源：{price_source}"
                )
            else:
                print(
                    "確認可購買："
                    f"{candidate['name']} / "
                    "價格未能辨識"
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
        if (
            len(candidates)
            >= MAX_CANDIDATES_PER_STORE
        ):
            break

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

                    card_price = parse_price(
                        full_text
                    )

                    add_product(
                        candidates,
                        {
                            "store": store_name,
                            "name": name,
                            "price": card_price,
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
        if (
            len(candidates)
            >= MAX_CANDIDATES_PER_STORE
        ):
            break

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

                    card_price = parse_price(
                        full_text
                    )

                    add_product(
                        candidates,
                        {
                            "store": store_name,
                            "name": name,
                            "price": card_price,
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

    candidates = []
    seen_urls = set()

    for keyword in SEARCH_KEYWORDS:
        if (
            len(candidates)
            >= MAX_CANDIDATES_PER_STORE
        ):
            break

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

                    card_price = parse_price(
                        full_text
                    )

                    add_product(
                        candidates,
                        {
                            "store": store_name,
                            "name": name,
                            "price": card_price,
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
# 主程式
# ============================================================

async def main():
    print("=" * 76)
    print("戰鬥陀螺指定賣場開賣搜尋器")
    print(
        "檢查時間：",
        datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
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
