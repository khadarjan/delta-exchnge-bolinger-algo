"""
Standalone bracket test — NOT part of the main algo loop.

Places a market entry PLUS a reduce-only stop-loss and a reduce-only
take-profit, exactly like the real algo does when a signal fires. Lets you
confirm all three orders actually land on Delta before trusting main.py to
do it unattended.

Usage:
    python test_bracket.py <symbol> <buy|sell> <size_in_lots> <sl_points> <tp_points>

Example (ETH currently ~1927, risking/targeting in price points, not %):
    python test_bracket.py ETHUSD buy 1 15 30
    -> entry at market, SL = entry - 15, TP = entry + 30 (for a buy)

    python test_bracket.py PAXGUSD sell 1 20 40
    -> entry at market, SL = entry + 20, TP = entry - 40 (for a sell)
"""
import sys
import json
import time
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce
import config as cfg
from executor import get_client, get_product_id, has_open_position


def get_last_price(product_id: int) -> float:
    """Pull a fresh reference price via the ticker so SL/TP are computed from
    something close to the actual fill, not a stale value."""
    import requests
    url = f"{cfg.BASE_URL}/v2/tickers"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    for t in r.json().get("result", []):
        if t.get("product_id") == product_id:
            return float(t.get("close") or t.get("mark_price"))
    raise RuntimeError("Could not find ticker for product_id")


def main():
    if len(sys.argv) != 6:
        print(__doc__)
        sys.exit(1)

    symbol = sys.argv[1].upper()
    side = sys.argv[2].lower()
    size = int(sys.argv[3])
    sl_points = float(sys.argv[4])
    tp_points = float(sys.argv[5])

    if side not in ("buy", "sell"):
        print("side must be 'buy' or 'sell'")
        sys.exit(1)

    client = get_client()
    product_id = get_product_id(symbol)

    print(f"Symbol: {symbol}  product_id={product_id}")

    if has_open_position(client, product_id):
        print("❌ You already have an open position on this product. "
              "Flatten it first (opposite-side test_order.py) before testing a new bracket.")
        sys.exit(1)

    ref_price = get_last_price(product_id)
    if side == "buy":
        sl_price = ref_price - sl_points
        tp_price = ref_price + tp_points
    else:
        sl_price = ref_price + sl_points
        tp_price = ref_price - tp_points

    print(f"Reference price : {ref_price}")
    print(f"Side            : {side}")
    print(f"Size            : {size} lot(s)")
    print(f"Planned SL      : {sl_price:.2f}  ({sl_points} pts away)")
    print(f"Planned TP      : {tp_price:.2f}  ({tp_points} pts away)")
    print("\nNote: actual entry fill may differ slightly from the reference price above "
          "(slippage), so real SL/TP distance may not be exact.")

    confirm = input("\nThis places 3 REAL orders (entry + SL + TP) if pointed at production. Type 'yes' to continue: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)

    entry_side = "buy" if side == "buy" else "sell"
    exit_side = "sell" if side == "buy" else "buy"

    print("\n1) Placing MARKET entry...")
    entry_order = client.place_order(
        product_id=product_id, size=size, side=entry_side, order_type=OrderType.MARKET,
    )
    print(json.dumps(entry_order, indent=2))

    entry_result = entry_order.get("result", entry_order) if isinstance(entry_order, dict) else entry_order
    fill_price = entry_result.get("average_fill_price")
    print(f"\nEntry filled at: {fill_price}")

    time.sleep(1)  # brief pause before placing exits

    print("\n2) Placing reduce-only STOP-LOSS...")
    sl_order_dict = {
        "product_id": product_id,
        "size": size,
        "side": exit_side,
        "order_type": "market_order",
        "stop_order_type": "stop_loss_order",
        "stop_price": str(round(sl_price, 2)),
        "reduce_only": "true",
    }
    sl_order = client.create_order(sl_order_dict)
    print(json.dumps(sl_order, indent=2))

    print("\n3) Placing reduce-only TAKE-PROFIT...")
    tp_order = client.place_order(
        product_id=product_id, size=size, side=exit_side, order_type=OrderType.LIMIT,
        limit_price=str(round(tp_price, 2)), reduce_only="true", time_in_force=TimeInForce.GTC,
    )
    print(json.dumps(tp_order, indent=2))

    print("\n--- DONE ---")
    print("Go check the Delta UI: you should see 1 filled position, "
          "1 resting reduce-only stop order (SL), and 1 resting reduce-only limit order (TP).")
    print("If either SL or TP failed to place, the position is now UNPROTECTED — "
          "check the errors above and close manually if needed.")


if __name__ == "__main__":
    main()