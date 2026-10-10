"""
One-off utility: lists all Delta products whose symbol or description looks
gold/precious-metal related, so you can find the EXACT symbol string the API
expects (site marketing copy like "XAUUSD" doesn't always match the real
product symbol).

Usage:
    python find_symbol.py gold
    python find_symbol.py eth
    python find_symbol.py        (lists ALL perpetual futures symbols)
"""
import sys
import requests
import config as cfg


def main():
    keyword = sys.argv[1].lower() if len(sys.argv) > 1 else None

    url = f"{cfg.BASE_URL}/v2/products"
    params = {"contract_types": "perpetual_futures", "page_size": 500}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    payload = r.json()
    products = payload.get("result", [])

    print(f"Fetched {len(products)} perpetual futures products from {cfg.BASE_URL}\n")

    matches = []
    for p in products:
        symbol = p.get("symbol", "")
        desc = (p.get("description") or "")
        underlying = (p.get("underlying_asset") or {}).get("symbol", "") if isinstance(p.get("underlying_asset"), dict) else ""
        haystack = f"{symbol} {desc} {underlying}".lower()
        if keyword is None or keyword in haystack:
            matches.append((symbol, desc, p.get("id")))

    if not matches:
        print(f"No products matched '{keyword}'. Try a different keyword or run with no argument to list everything.")
        return

    print(f"{'SYMBOL':<20} {'PRODUCT_ID':<12} DESCRIPTION")
    print("-" * 70)
    for symbol, desc, pid in sorted(matches):
        print(f"{symbol:<20} {str(pid):<12} {desc}")


if __name__ == "__main__":
    main()
