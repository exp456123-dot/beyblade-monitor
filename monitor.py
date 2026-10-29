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

# 商品至少要包含其中一個關鍵字
PRODUCT_WORDS = [
    "BEYBLADE",
    "戰鬥陀螺",
    "CX-",
    "UX-",
    "BX-",
]

# 排除不想看到的商品
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
    "貼紙",
    "收納盒",
    "收納包",
    "展示盒",
    "保護套",
    "零件拆賣",
    "單售零件",
]

# 缺貨或不可購買文字
UNAVAILABLE_WORDS = [
    "已售完",
    "售完",
    "已絕版",
    "缺貨",
    "暫無庫存",
    "補貨中",
    "可訂購時通知我",
    "貨到通知",
    "停止銷售",
]

# 商品合理價格
MIN_PRICE = 100
MAX_PRICE = 5000

# 各賣場最多顯示幾筆
MAX_RESULTS_PER_STORE = 30

# 指定蝦皮賣場
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
# 共用功能
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
        r"\$\s*([\d,]+)",
        r"售價\s*([\d,]+)",
        r"特價\s*([\d,]+)",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)

        for match in matches:
            try:
                price = int(match.replace(",", ""))

                if MIN_PRICE <= price <= MAX_PRICE:
                    return price
            except ValueError:
                continue

    return None


def is_beyblade_product(name, full_text=""):
    combined = clean_text(f"{name} {full_text}")
    upper_text = combined.upper()

    has_product_word = any(
        word.upper() in upper_text
        for word in PRODUCT_WORDS
    )

    has_excluded_word = any(
        word.upper() in upper_text
        for word in EXCLUDE_WORDS
    )

    return has_product_word and not has_excluded_word


def is_available(full_text):
    upper_text = full_text.upper()

    return not any(
        word.upper() in upper_text
        for word in UNAVAILABLE_WORDS
    )


def normalize_url(base_url, href):
    if not href:
        return ""

    href = href.strip()

    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = urljoin(base_url, href)
    elif not href.startswith("http"):
        href = urljoin(base_url, href)

    # 去除常見追蹤參數
    return href.split("?")[0]


def add_product(products, product, seen_urls):
    url = product.get("url", "")

    if not url or url in seen_urls:
        return

    seen_urls.add(url)
    products.append(product)


def print_products(store_name, products):
    print()
    print("=" * 75)
    print(f"【{store_name}】")
    print(f"找到 {len(products)} 筆可購買的戰鬥陀螺商品")
    print("=" * 75)

    if not products:
        print("目前沒有取得符合條件的商品。")
        return

    for index, product in enumerate(products, start=1):
        print()
        print(f"{index}. {product['name']}")

        if product.get("price") is not None:
            print(f"   價格：NT${product['price']:,}")
        else:
            print("   價格：未能從搜尋頁辨識")

        print(f"   連結：{product['url']}")


async def create_page(browser):
    page = await browser.new_page(
        viewport={
            "width": 1440,
            "height": 1200,
        },
        locale="zh-TW",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
    )

    await page.set_extra_http_headers({
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    })

    return page


async def scroll_page(page, times=4):
    for _ in range(times):
        await page.mouse.wheel(0, 1000)
        await page.wait_for_timeout(1000)


async def extract_card_text(link):
    texts = []

    try:
        link_text = clean_text(await link.inner_text())

        if link_text:
            texts.append(link_text)
    except Exception:
        pass

    # 嘗試從商品連結外層取得完整商品卡文字
    ancestor_levels = [1, 2, 3, 4]

    for level in ancestor_levels:
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

    return max(texts, key=len)


def find_product_name(link_text, card_text):
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

    return max(possible_lines, key=len)[:300]


# ============================================================
# momo Funbox 搜尋
# ============================================================

async def search_momo_funbox(browser):
    store_name = "momo Funbox"
    products = []
    seen_urls = set()

    for keyword in SEARCH_KEYWORDS:
        page = await create_page(browser)

        search_keyword = f"Funbox {keyword}"

        url = (
            "https://www.momoshop.com.tw/search/"
            f"searchShop.jsp?keyword={quote(search_keyword)}"
        )

        print(f"\n搜尋 {store_name}：{search_keyword}")

        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(5000)
            await scroll_page(page)

            links = page.locator(
                'a[href*="goods.momo"], '
                'a[href*="/goods/"], '
                'a[href*="GoodsDetail"]'
            )

            link_count = await links.count()

            print(f"讀取到 {link_count} 個可能的 momo 商品連結")

            for index in range(min(link_count, 150)):
                if len(products) >= MAX_RESULTS_PER_STORE:
                    break

                link = links.nth(index)

                try:
                    href = await link.get_attribute("href")

                    if not href:
                        continue

                    product_url = normalize_url(
                        "https://www.momoshop.com.tw",
                        href,
                    )

                    if product_url in seen_urls:
                        continue

                    link_text = clean_text(
                        await link.inner_text()
                    )

                    card_text = await extract_card_text(link)
                    full_text = clean_text(
                        f"{link_text} {card_text}"
                    )

                    # momo 只接受商品卡中有 Funbox 的商品
                    if "FUNBOX" not in full_text.upper():
                        continue

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

                    if not is_available(full_text):
                        continue

                    price = parse_price(full_text)

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
            print(f"{store_name} 搜尋失敗：{error}")

        finally:
            await page.close()

    return products


# ============================================================
# 誠品線上搜尋
# ============================================================

async def search_eslite(browser):
    store_name = "誠品線上"
    products = []
    seen_urls = set()

    for keyword in SEARCH_KEYWORDS:
        page = await create_page(browser)

        url = (
            "https://www.eslite.com/Search"
            f"?keyword={quote(keyword)}"
        )

        print(f"\n搜尋 {store_name}：{keyword}")

        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(5000)
            await scroll_page(page)

            links = page.locator(
                'a[href*="/product/"]'
            )

            link_count = await links.count()

            print(f"讀取到 {link_count} 個可能的誠品商品連結")

            for index in range(min(link_count, 150)):
                if len(products) >= MAX_RESULTS_PER_STORE:
                    break

                link = links.nth(index)

                try:
                    href = await link.get_attribute("href")

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

                    card_text = await extract_card_text(link)
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

                    if not is_available(full_text):
                        continue

                    price = parse_price(full_text)

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
            print(f"{store_name} 搜尋失敗：{error}")

        finally:
            await page.close()

    return products


# ============================================================
# 蝦皮指定賣場搜尋
# ============================================================

async def search_shopee_store(browser, store):
    store_name = store["store"]
    shop_url = store["shop_url"]

    products = []
    seen_urls = set()

    for keyword in SEARCH_KEYWORDS:
        page = await create_page(browser)

        # 在指定店家網址後加入搜尋參數
        url = (
            f"{shop_url}/search"
            f"?keyword={quote(keyword)}"
        )

        print(f"\n搜尋 {store_name}：{keyword}")
        print(f"指定賣場：{shop_url}")

        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(8000)
            await scroll_page(page, times=5)

            # 蝦皮商品網址通常包含 -i. 或 /product/
            links = page.locator(
                'a[href*="-i."], '
                'a[href*="/product/"]'
            )

            link_count = await links.count()

            print(f"讀取到 {link_count} 個可能的蝦皮商品連結")

            for index in range(min(link_count, 200)):
                if len(products) >= MAX_RESULTS_PER_STORE:
                    break

                link = links.nth(index)

                try:
                    href = await link.get_attribute("href")

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

                    card_text = await extract_card_text(link)
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

                    if not is_available(full_text):
                        continue

                    price = parse_price(full_text)

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
            print(f"{store_name} 搜尋失敗：{error}")

            # GitHub Actions 發生錯誤時保留畫面
            safe_name = re.sub(
                r"[^a-zA-Z0-9_-]",
                "_",
                store_name,
            )

            try:
                await page.screenshot(
                    path=f"{safe_name}_error.png",
                    full_page=False,
                )
            except Exception:
                pass

        finally:
            await page.close()

    return products


# ============================================================
# 主程式
# ============================================================

async def main():
    print("=" * 75)
    print("戰鬥陀螺指定賣場搜尋器")
    print("檢查時間：", datetime.now())
    print("=" * 75)

    print()
    print("搜尋範圍：")
    print("1. momo Funbox")
    print("2. 誠品線上")
    print("3. 蝦皮 Funbox")
    print("4. 蝦皮 玩具e哥")
    print()
    print("PChome 及其他賣場不在搜尋範圍內。")

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
            momo_products = await search_momo_funbox(
                browser
            )

            eslite_products = await search_eslite(
                browser
            )

            shopee_results = {}

            for store in SHOPEE_STORES:
                products = await search_shopee_store(
                    browser,
                    store,
                )

                shopee_results[store["store"]] = products

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

    for store_name, products in shopee_results.items():
        print_products(
            store_name,
            products,
        )

    total = (
        len(momo_products)
        + len(eslite_products)
        + sum(
            len(products)
            for products in shopee_results.values()
        )
    )

    print()
    print("=" * 75)
    print(f"全部指定賣場共找到 {total} 筆可購買商品")
    print("搜尋完成")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(main())
