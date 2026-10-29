import urllib.parse
import urllib.request
import json
from datetime import datetime, timezone, timedelta
from html import escape


KEYWORDS = [
    "BEYBLADE X",
    "戰鬥陀螺",
    "CX-",
    "UX-",
    "BX-",
]

MIN_PRICE = 100
MAX_PRICE = 5000

EXCLUDE_WORDS = [
    "二手",
    "中古",
    "盜版",
    "副廠",
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
]

TAIWAN_TZ = timezone(timedelta(hours=8))


def now():
    return datetime.now(
        TAIWAN_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")


def product_allowed(name, price):
    upper_name = name.upper()

    for word in EXCLUDE_WORDS:
        if word.upper() in upper_name:
            return False

    target_words = [
        "BEYBLADE",
        "戰鬥陀螺",
        "CX-",
        "UX-",
        "BX-",
    ]

    if not any(
        word.upper() in upper_name
        for word in target_words
    ):
        return False

    if price is not None:
        if price < MIN_PRICE:
            return False

        if price > MAX_PRICE:
            return False

    return True


def search_pchome(keyword):
    base_url = (
        "https://ecshweb.pchome.com.tw/"
        "search/v3.3/all/results"
    )

    params = urllib.parse.urlencode({
        "q": keyword,
        "page": 1,
        "sort": "sale/dc",
    })

    url = base_url + "?" + params

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/130 Safari/537.36"
            ),
            "Referer": "https://24h.pchome.com.tw/",
        },
    )

    print("搜尋 PChome：", keyword)

    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:

            data = json.loads(
                response.read().decode("utf-8")
            )

    except Exception as error:
        print("PChome 搜尋失敗：", error)
        return []

    products = []

    for item in data.get("prods", []):
        product_id = item.get("Id")
        name = item.get("name", "")
        price = item.get("price")

        if not product_id:
            continue

        try:
            price = int(price)
        except (TypeError, ValueError):
            price = None

        if not product_allowed(name, price):
            continue

        products.append({
            "store": "PChome 24h",
            "name": name,
            "price": price,
            "url": (
                "https://24h.pchome.com.tw/prod/"
                + product_id
            ),
        })

    return products


def generate_html(products):
    update_time = now()

    cards = ""

    for product in products:
        name = escape(product["name"])
        store = escape(product["store"])
        url = escape(
            product["url"],
            quote=True,
        )

        if product["price"] is not None:
            price = (
                "NT$"
                + format(product["price"], ",")
            )
        else:
            price = "價格未提供"

        cards += f"""
        <div class="card">
            <div class="store">{store}</div>

            <div class="name">
                {name}
            </div>

            <div class="price">
                {price}
            </div>

            <a
                class="buy"
                href="{url}"
                target="_blank"
                rel="noopener noreferrer"
            >
                立即查看商品
            </a>
        </div>
        """

    if not cards:
        cards = """
        <div class="empty">
            目前沒有找到符合條件的戰鬥陀螺商品。
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<meta
    http-equiv="refresh"
    content="300"
>

<title>戰鬥陀螺開賣雷達</title>

<style>

* {{
    box-sizing: border-box;
}}

body {{
    margin: 0;
    background: #0b1020;
    color: white;
    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        "Microsoft JhengHei",
        sans-serif;
}}

.header {{
    background:
        linear-gradient(
            135deg,
            #e53935,
            #ff9800
        );
    padding: 28px 18px;
    text-align: center;
}}

.header h1 {{
    margin: 0;
    font-size: 30px;
}}

.header p {{
    margin: 10px 0 0;
    opacity: 0.95;
}}

.container {{
    max-width: 900px;
    margin: auto;
    padding: 18px;
}}

.info {{
    background: #151d34;
    padding: 16px;
    border-radius: 14px;
    margin-bottom: 18px;
}}

.count {{
    font-size: 22px;
    font-weight: bold;
    color: #ffca28;
}}

.card {{
    background: #151d34;
    border: 1px solid #273453;
    border-radius: 16px;
    padding: 18px;
    margin-bottom: 14px;
    box-shadow:
        0 4px 12px
        rgba(0, 0, 0, 0.25);
}}

.store {{
    display: inline-block;
    padding: 5px 10px;
    background: #1976d2;
    border-radius: 20px;
    font-size: 14px;
    margin-bottom: 10px;
}}

.name {{
    font-size: 18px;
    line-height: 1.5;
    font-weight: 600;
}}

.price {{
    color: #ffca28;
    font-size: 26px;
    font-weight: bold;
    margin: 14px 0;
}}

.buy {{
    display: block;
    width: 100%;
    padding: 13px;
    border-radius: 10px;
    background: #e53935;
    color: white;
    text-decoration: none;
    text-align: center;
    font-weight: bold;
}}

.buy:hover {{
    background: #ff5252;
}}

.empty {{
    background: #151d34;
    padding: 30px;
    border-radius: 14px;
    text-align: center;
}}

.footer {{
    text-align: center;
    color: #8892a6;
    font-size: 13px;
    padding: 20px;
}}

</style>

</head>

<body>

<div class="header">

    <h1>🔥 戰鬥陀螺開賣雷達</h1>

    <p>
        Beyblade X 商品監控
    </p>

</div>

<div class="container">

    <div class="info">

        <div class="count">
            📦 找到 {len(products)} 筆商品
        </div>

        <div>
            🕐 最後更新：{update_time}
        </div>

        <div>
            📡 目前監控：PChome 24h
        </div>

    </div>

    {cards}

</div>

<div class="footer">
    戰鬥陀螺商品資訊僅供快速查詢，
    實際庫存與價格請以購物網站為準。
</div>

</body>

</html>
"""

    with open(
        "index.html",
        "w",
        encoding="utf-8",
    ) as file:
        file.write(html)


def main():
    print("=" * 60)
    print("🔥 戰鬥陀螺開賣雷達")
    print("更新時間：", now())
    print("=" * 60)

    all_products = {}

    for keyword in KEYWORDS:
        products = search_pchome(keyword)

        print(
            "找到",
            len(products),
            "筆符合條件商品",
        )

        for product in products:
            all_products[
                product["url"]
            ] = product

    products = list(
        all_products.values()
    )

    products.sort(
        key=lambda item: (
            item["price"]
            if item["price"] is not None
            else 999999
        )
    )

    print()
    print(
        "共找到",
        len(products),
        "筆商品"
    )

    generate_html(products)

    print("index.html 已產生")
    print("監控完成")


if __name__ == "__main__":
    main()
