import urllib.parse
import urllib.request
import json
import os
from datetime import datetime, timezone, timedelta


# ==========================================================
# 使用者設定
# ==========================================================

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

STATE_FILE = "seen_products.json"

TAIWAN_TZ = timezone(
    timedelta(hours=8)
)


# ==========================================================
# 時間
# ==========================================================

def now():
    return datetime.now(
        TAIWAN_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")


# ==========================================================
# 商品過濾
# ==========================================================

def product_allowed(name, price):
    upper_name = name.upper()

    # 排除不要的商品
    for word in EXCLUDE_WORDS:
        if word.upper() in upper_name:
            return False

    # 至少符合其中一個
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

    # 價格範圍
    if price is not None:

        if price < MIN_PRICE:
            return False

        if price > MAX_PRICE:
            return False

    return True


# ==========================================================
# 讀取商品歷史紀錄
# ==========================================================

def load_state():

    if not os.path.exists(
        STATE_FILE
    ):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            return json.load(
                file
            )

    except Exception as error:

        print(
            "⚠️ 歷史紀錄讀取失敗：",
            error,
        )

        return {}


# ==========================================================
# 儲存商品紀錄
# ==========================================================

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


# ==========================================================
# PChome 搜尋
# ==========================================================

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

    url = (
        base_url
        + "?"
        + params
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/130 Safari/537.36"
            ),
            "Referer": (
                "https://24h.pchome.com.tw/"
            ),
        },
    )

    print()
    print(
        "🔎 PChome 搜尋：",
        keyword,
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:

            data = json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    except Exception as error:

        print(
            "❌ PChome 搜尋失敗：",
            error,
        )

        return []

    products = []

    for item in data.get(
        "prods",
        [],
    ):

        product_id = item.get(
            "Id"
        )

        name = item.get(
            "name",
            "",
        )

        price = item.get(
            "price"
        )

        if not product_id:
            continue

        try:

            price = int(
                price
            )

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

        product_url = (
            "https://24h.pchome.com.tw/prod/"
            + product_id
        )

        products.append({
            "store": "PChome 24h",
            "id": product_id,
            "name": name,
            "price": price,
            "url": product_url,
        })

    return products


# ==========================================================
# 判斷新品與價格變化
# ==========================================================

def check_changes(
    products,
    old_state,
):

    alerts = []

    new_state = {}

    for product in products:

        product_id = (
            product["id"]
        )

        new_state[
            product_id
        ] = {
            "store": product[
                "store"
            ],
            "name": product[
                "name"
            ],
            "price": product[
                "price"
            ],
            "url": product[
                "url"
            ],
            "last_seen": now(),
        }

        # ----------------------
        # 新商品
        # ----------------------

        if product_id not in old_state:

            alert = (
                product.copy()
            )

            alert[
                "reason"
            ] = "🆕 發現新商品"

            alerts.append(
                alert
            )

            continue

        # ----------------------
        # 價格比較
        # ----------------------

        old_price = old_state[
            product_id
        ].get(
            "price"
        )

        new_price = product.get(
            "price"
        )

        if (
            old_price is None
            or new_price is None
        ):
            continue

        # ----------------------
        # 降價
        # ----------------------

        if new_price < old_price:

            alert = (
                product.copy()
            )

            alert[
                "reason"
            ] = "💰 發現降價"

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

    return (
        alerts,
        new_state,
    )


# ==========================================================
# 顯示警報
# ==========================================================

def show_alert(
    product
):

    print()
    print(
        "🔥" * 25
    )

    print(
        product[
            "reason"
        ]
    )

    print(
        "🏪 賣場：",
        product[
            "store"
        ],
        sep="",
    )

    print(
        "🌀 商品：",
        product[
            "name"
        ],
        sep="",
    )

    if product.get(
        "price"
    ) is not None:

        print(
            "💰 現價：NT$",
            format(
                product[
                    "price"
                ],
                ",",
            ),
            sep="",
        )

    if product.get(
        "old_price"
    ) is not None:

        print(
            "💵 原價：NT$",
            format(
                product[
                    "old_price"
                ],
                ",",
            ),
            sep="",
        )

    if product.get(
        "difference"
    ) is not None:

        print(
            "⬇️ 降價：NT$",
            format(
                product[
                    "difference"
                ],
                ",",
            ),
            sep="",
        )

    print(
        "🔗 購買連結：",
        product[
            "url"
        ],
        sep="",
    )

    print(
        "🔥" * 25
    )


# ==========================================================
# 主程式
# ==========================================================

def main():

    print(
        "=" * 60
    )

    print(
        "🔥 戰鬥陀螺開賣監控器"
    )

    print(
        "🇹🇼 台灣時間：",
        now(),
    )

    print(
        "=" * 60
    )

    old_state = (
        load_state()
    )

    print(
        "📚 已記錄商品：",
        len(old_state),
        "筆",
    )

    all_products = {}

    # ======================================================
    # 搜尋
    # ======================================================

    for keyword in KEYWORDS:

        products = (
            search_pchome(
                keyword
            )
        )

        print(
            "   找到",
            len(products),
            "筆符合條件商品",
        )

        for product in products:

            all_products[
                product["id"]
            ] = product

    products = list(
        all_products.values()
    )

    # 價格低到高
    products.sort(
        key=lambda item: (
            item["price"]
            if item["price"]
            is not None
            else 999999
        )
    )

    # ======================================================
    # 顯示搜尋結果摘要
    # ======================================================

    print()
    print(
        "=" * 60
    )

    print(
        "📦 本次找到：",
        len(products),
        "筆不重複商品",
    )

    print(
        "=" * 60
    )

    # ======================================================
    # 判斷異動
    # ======================================================

    alerts, new_state = (
        check_changes(
            products,
            old_state,
        )
    )

    print()
    print(
        "🚨 本次發現：",
        len(alerts),
        "個異動",
    )

    # ======================================================
    # 顯示警報
    # ======================================================

    if alerts:

        for alert in alerts:

            show_alert(
                alert
            )

    else:

        print()
        print(
            "😴 沒有發現新品或降價"
        )

    # ======================================================
    # 儲存最新狀態
    # ======================================================

    save_state(
        new_state
    )

    print()
    print(
        "💾 商品資料已更新"
    )

    print(
        "📚 目前記錄：",
        len(new_state),
        "筆",
    )

    print()
    print(
        "=" * 60
    )

    print(
        "✅ 本次監控完成"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":
    main()
