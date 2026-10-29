import json
import os
from datetime import datetime, timedelta, timezone


# =========================================================
# 基本設定
# =========================================================

STATE_FILE = "seen_products.json"
OUTPUT_DIRECTORY = "docs"
OUTPUT_FILE = os.path.join(
    OUTPUT_DIRECTORY,
    "products.json",
)

TAIWAN_TIMEZONE = timezone(
    timedelta(hours=8)
)


# =========================================================
# 共用功能
# =========================================================

def now():
    return datetime.now(
        TAIWAN_TIMEZONE
    ).strftime("%Y-%m-%d %H:%M:%S")


def load_state():
    if not os.path.exists(STATE_FILE):
        print(
            "⚠️ 找不到 seen_products.json，"
            "將建立空白網頁資料。"
        )
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

        print(
            "⚠️ seen_products.json 格式不是物件。"
        )
        return {}

    except Exception as error:
        print(
            "❌ 讀取 seen_products.json 失敗：",
            error,
        )
        return {}


def normalize_product(key, item):
    if not isinstance(item, dict):
        return None

    store = str(
        item.get("store", "未知賣場")
    ).strip()

    product_id = str(
        item.get("id", key)
    ).strip()

    name = str(
        item.get("name", "未命名商品")
    ).strip()

    url = str(
        item.get("url", "")
    ).strip()

    price = item.get("price")

    try:
        if price is not None:
            price = int(price)
    except (TypeError, ValueError):
        price = None

    available = item.get(
        "available",
        True,
    )

    if isinstance(available, str):
        available = (
            available.lower()
            not in [
                "false",
                "0",
                "no",
                "unavailable",
            ]
        )
    else:
        available = bool(available)

    return {
        "key": key,
        "store": store,
        "id": product_id,
        "name": name,
        "price": price,
        "url": url,
        "available": available,
        "first_seen": item.get(
            "first_seen",
            "",
        ),
        "last_seen": item.get(
            "last_seen",
            "",
        ),
    }


def sort_products(product):
    available_order = (
        0 if product["available"] else 1
    )

    price = product["price"]

    if price is None:
        price = 999999999

    return (
        available_order,
        product["store"],
        price,
        product["name"],
    )


def build_summary(products):
    stores = {}

    available_count = 0
    unavailable_count = 0

    for product in products:
        store = product["store"]

        if store not in stores:
            stores[store] = {
                "total": 0,
                "available": 0,
                "unavailable": 0,
            }

        stores[store]["total"] += 1

        if product["available"]:
            stores[store]["available"] += 1
            available_count += 1
        else:
            stores[store]["unavailable"] += 1
            unavailable_count += 1

    return {
        "total": len(products),
        "available": available_count,
        "unavailable": unavailable_count,
        "stores": stores,
    }


def main():
    print("=" * 60)
    print("🌐 建立戰鬥陀螺雷達網頁資料")
    print("=" * 60)

    state = load_state()

    products = []

    for key, item in state.items():
        product = normalize_product(
            key,
            item,
        )

        if product is not None:
            products.append(product)

    products.sort(
        key=sort_products
    )

    data = {
        "title": "戰鬥陀螺雷達",
        "updated_at": now(),
        "summary": build_summary(
            products
        ),
        "products": products,
    }

    os.makedirs(
        OUTPUT_DIRECTORY,
        exist_ok=True,
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(
        "✅ 已產生：",
        OUTPUT_FILE,
    )

    print(
        "📦 商品總數：",
        data["summary"]["total"],
    )

    print(
        "🔥 有貨：",
        data["summary"]["available"],
    )

    print(
        "⚪ 疑似缺貨：",
        data["summary"]["unavailable"],
    )

    print(
        "🕐 更新時間：",
        data["updated_at"],
    )


if __name__ == "__main__":
    main()
