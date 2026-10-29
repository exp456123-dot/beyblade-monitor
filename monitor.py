import urllib.parse
import urllib.request
import json
from datetime import datetime


KEYWORDS = [
    "BEYBLADE X",
    "戰鬥陀螺",
    "CX-",
    "UX-",
]


def search_pchome(keyword):
    base_url = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"

    params = urllib.parse.urlencode({
        "q": keyword,
        "page": 1,
        "sort": "sale/dc"
    })

    url = base_url + "?" + params

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://24h.pchome.com.tw/"
        }
    )

    print()
    print("搜尋 PChome：", keyword)

    try:
        with urllib.request.urlopen(
            request,
            timeout=30
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

        product_url = (
            "https://24h.pchome.com.tw/prod/"
            + product_id
        )

        products.append({
            "name": name,
            "price": price,
            "url": product_url
        })

    return products


def main():

    print("=" * 60)
    print("🔥 戰鬥陀螺開賣監控器")
    print("檢查時間：", datetime.now())
    print("=" * 60)

    all_products = {}

    for keyword in KEYWORDS:

        products = search_pchome(keyword)

        print(
            "找到",
            len(products),
            "筆搜尋結果"
        )

        for product in products:
            all_products[
                product["url"]
            ] = product

    print()
    print("=" * 60)
    print(
        "PChome 共找到",
        len(all_products),
        "筆不重複商品"
    )
    print("=" * 60)

    for product in list(
        all_products.values()
    )[:30]:

        print()
        print("商品：", product["name"])
        print("價格：NT$", product["price"])
        print("連結：", product["url"])

    print()
    print("=" * 60)
    print("✅ PChome 檢查完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
