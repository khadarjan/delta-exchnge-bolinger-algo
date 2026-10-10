"""
Handles talking to Delta Exchange for product lookup, position sizing, and
placing the entry + reduce-only stop-loss + reduce-only take-profit orders
that make up one bracketed trade.
"""
import logging
import requests
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce
import config as cfg

log = logging.getLogger("executor")


def get_client() -> DeltaRestClient:
    if not cfg.API_KEY or not cfg.API_SECRET:
        raise RuntimeError("DELTA_API_KEY / DELTA_API_SECRET not set in environment.")
    return DeltaRestClient(base_url=cfg.BASE_URL, api_key=cfg.API_KEY, api_secret=cfg.API_SECRET)


def get_product_id(symbol: str) -> int:
    """Look up the numeric product_id for a symbol (needed for every order call)."""
    url = f"{cfg.BASE_URL}/v2/products/{symbol}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("success"):
        raise RuntimeError(f"Could not resolve product_id for {symbol}: {payload}")
    return payload["result"]["id"]


def place_bracket_trade(client: DeltaRestClient, product_id: int, side: str,
                         size: int, sl_price: float, tp_price: float) -> dict:
    """
    side: "BUY" or "SELL" (the entry direction)
    Places a market entry, then a reduce-only stop-market SL and a reduce-only
    limit TP on the opposite side.
    Returns dict with entry_order, sl_order, tp_order responses.
    """
    entry_side = "buy" if side == "BUY" else "sell"
    exit_side = "sell" if side == "BUY" else "buy"

    log.info(f"Placing MARKET {entry_side.upper()} entry, size={size}, product_id={product_id}")
    entry_order = client.place_order(
        product_id=product_id,
        size=size,
        side=entry_side,
        order_type=OrderType.MARKET,
    )

    log.info(f"Placing reduce-only STOP ({exit_side}) at {sl_price}")
    sl_order = client.place_stop_order(
        product_id=product_id,
        size=size,
        side=exit_side,
        order_type=OrderType.MARKET,
        stop_price=str(round(sl_price, 2)),
        reduce_only="true",
    )

    log.info(f"Placing reduce-only TAKE-PROFIT limit ({exit_side}) at {tp_price}")
    tp_order = client.place_order(
        product_id=product_id,
        size=size,
        side=exit_side,
        order_type=OrderType.LIMIT,
        limit_price=str(round(tp_price, 2)),
        reduce_only="true",
        time_in_force=TimeInForce.GTC,
    )

    return {"entry_order": entry_order, "sl_order": sl_order, "tp_order": tp_order}


def has_open_position(client: DeltaRestClient, product_id: int) -> bool:
    pos = client.get_position(product_id)
    result = pos.get("result") if isinstance(pos, dict) else pos
    if not result:
        return False
    size = result.get("size", 0) if isinstance(result, dict) else 0
    return size != 0
