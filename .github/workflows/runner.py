import urllib.parse
import urllib.request
import json
import os
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.header import Header


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

# 不想收到通知的商品
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
# 基本功能
# ==========================================================

def now():
    return datetime.now(
        TAIWAN_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")


def product_allowed(name, price):
    upper_name = name.upper()

    # 排除不要的商品
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

    # 必須屬於戰鬥陀螺相關商品
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


# ==========================================================
# 讀取過去商品紀錄
# ==========================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)

    except Exception as error:
        print(
            "⚠️ 無法讀取歷史紀錄：",
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
        product_id = item.get("Id")

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

        product_url = (
            "https://24h.pchome.com.tw/"
            "prod/"
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
# 判斷新品 / 降價
# ==========================================================

def check_changes(
    products,
    old_state,
):
    alerts = []

    new_state = {}

    for product in products:
        product_id = product["id"]

        new_state[product_id] = {
            "store": product["store"],
            "name": product["name"],
            "price": product["price"],
            "url": product["url"],
            "last_seen": now(),
        }

        # 第一次看到這個商品
        if product_id not in old_state:

            alert = product.copy()

            alert["reason"] = (
                "🆕 發現新商品"
            )

            alerts.append(alert)

            continue

        old_price = old_state[
            product_id
        ].get("price")

        new_price = product.get(
            "price"
        )

        # 如果舊價格或新價格不存在
        # 就不做價格比較
        if (
            old_price is None
            or new_price is None
        ):
            continue

        # 降價
        if new_price < old_price:

            difference = (
                old_price
                - new_price
            )

            alert = product.copy()

            alert["reason"] = (
                "💰 商品降價"
            )

            alert["old_price"] = (
                old_price
            )

            alert["difference"] = (
                difference
            )

            alerts.append(alert)

    return alerts, new_state


# ==========================================================
# Email 通知
# ==========================================================

def send_email(alerts):
    sender = os.environ.get(
        "EMAIL_SENDER"
    )

    password = os.environ.get(
        "EMAIL_PASSWORD"
    )

    receiver = os.environ.get(
        "EMAIL_RECEIVER"
    )

    if not sender:
        print(
            "⚠️ EMAIL_SENDER 尚未設定"
        )
        return False

    if not password:
        print(
            "⚠️ EMAIL_PASSWORD 尚未設定"
        )
        return False

    if not receiver:
        print(
            "⚠️ EMAIL_RECEIVER 尚未設定"
        )
        return False

    body = []

    body.append(
        "🔥 戰鬥陀螺雷達通知"
    )

    body.append("")
    body.append(
        "檢查時間：" + now()
    )

    body.append("")
    body.append(
        "發現 "
        + str(len(alerts))
        + " 個異動"
    )

    body.append("")
    body.append(
        "=" * 50
    )

    for product in alerts:
        body.append("")
        body.append(
            product["reason"]
        )

        body.append("")
        body.append(
            "🏪 賣場："
            + product["store"]
        )

        body.append(
            "🌀 商品："
            + product["name"]
        )

        if product.get(
            "price"
        ) is not None:

            body.append(
                "💰 現價：NT$"
                + format(
                    product["price"],
                    ",",
                )
            )

        if product.get(
            "old_price"
        ) is not None:

            body.append(
                "原價：NT$"
                + format(
                    product["old_price"],
                    ",",
                )
            )

        if product.get(
            "difference"
        ) is not None:

            body.append(
                "⬇️ 降價：NT$"
                + format(
                    product[
                        "difference"
                    ],
                    ",",
                )
            )

        body.append(
            "🔗 購買："
            + product["url"]
        )

        body.append("")
        body.append(
            "-" * 50
        )

    message = MIMEText(
        "\n".join(body),
        "plain",
        "utf-8",
    )

    message["Subject"] = Header(
        "🔥 戰鬥陀螺發現新品 / 降價",
        "utf-8",
    )

    message["From"] = sender
    message["To"] = receiver

    try:
        with smtplib.SMTP_SSL(
            "smtp.gmail.com",
            465,
            timeout=30,
        ) as server:

            server.login(
                sender,
                password,
            )

            server.sendmail(
                sender,
                [receiver],
                message.as_string(),
            )

        print()
        print(
            "📧 Email 通知已寄出"
        )

        return True

    except Exception as error:
        print()
        print(
            "❌ Email 寄送失敗：",
            error,
        )

        return False


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

    old_state = load_state()

    print(
        "📚 歷史商品紀錄：",
        len(old_state),
        "筆",
    )

    all_products = {}

    # 搜尋所有關鍵字
    for keyword in KEYWORDS:

        products = search_pchome(
            keyword
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

    # 價格排序
    products.sort(
        key=lambda item: (
            item["price"]
            if item["price"]
            is not None
            else 999999
        )
    )

    print()
    print(
        "=" * 60
    )

    print(
        "📦 本次找到",
        len(products),
        "筆不重複商品"
    )

    print(
        "=" * 60
    )

    # 顯示商品
    for product in products:

        print()

        print(
            "🌀",
            product["name"],
        )

        if product[
            "price"
        ] is not None:

            print(
                "💰 NT$",
                format(
                    product["price"],
                    ",",
                ),
                sep="",
            )

        print(
            "🔗",
            product["url"],
        )

    # 判斷變化
    alerts, new_state = (
        check_changes(
            products,
            old_state,
        )
    )

    print()
    print(
        "=" * 60
    )

    print(
        "🚨 本次需要通知：",
        len(alerts),
        "筆"
    )

    # 有異動才寄 Email
    if alerts:

        for alert in alerts:

            print()
            print(
                alert["reason"]
            )

            print(
                alert["name"]
            )

        send_email(
            alerts[:20]
        )

    else:

        print()
        print(
            "😴 沒有新品或降價，不寄 Email"
        )

    # 更新紀錄
    save_state(
        new_state
    )

    print()
    print(
        "💾 商品狀態已儲存"
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
