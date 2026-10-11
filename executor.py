"""
Delta Exchange helpers: product lookup, prices, and the entry + reduce-only SL + TP bracket.

Fixes vs the original:
  * place_stop_order() in delta_rest_client has NO reduce_only argument, so the old call raised
    TypeError right after the market entry filled (position left open with no SL/TP).
    The SL is now built with create_order(), exactly like your working test_bracket.py.
  * if the SL still fails after the entry, the position is closed at once (never left naked).
  * a failed TP does not abort: the SL is already protecting the trade.
  * prices are rounded to the product's tick size (not a fixed 2 decimals).
  * leftover reduce-only SL/TP orders are cancelled by ID once the position is gone.
"""
import logging
from decimal import Decimal

import requests
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce
import config as cfg

log = logging.getLogger("executor")
_product_cache = {}


def get_client() -> DeltaRestClient:
    if not cfg.API_KEY or not cfg.API_SECRET:
        raise RuntimeError("DELTA_API_KEY / DELTA_API_SECRET not set in environment.")
    return DeltaRestClient(base_url=cfg.BASE_URL, api_key=cfg.API_KEY, api_secret=cfg.API_SECRET)


def _product(symbol: str) -> dict:
    if symbol not in _product_cache:
        r = requests.get(f"{cfg.BASE_URL}/v2/products/{symbol}", timeout=15)
        r.raise_for_status()
        payload = r.json()
        if not payload.get("success"):
            raise RuntimeError(f"Could not resolve product for {symbol}: {payload}")
        _product_cache[symbol] = payload["result"]
    return _product_cache[symbol]


def get_product_id(symbol: str) -> int:
    """Look up the numeric product_id for a symbol (needed for every order call)."""
    return _product(symbol)["id"]


def get_mark_price(symbol: str) -> float:
    r = requests.get(f"{cfg.BASE_URL}/v2/tickers/{symbol}", timeout=15)
    r.raise_for_status()
    return float(r.json()["result"]["mark_price"])


def round_to_tick(symbol: str, price: float) -> str:
    tick = Decimal(str(_product(symbol)["tick_size"]))
    return format((Decimal(str(price)) / tick).quantize(Decimal("1")) * tick, "f")


def has_open_position(client: DeltaRestClient, product_id: int) -> bool:
    pos = client.get_position(product_id)
    result = pos.get("result") if isinstance(pos, dict) and "result" in pos else pos
    if not result:
        return False
    size = result.get("size", 0) if isinstance(result, dict) else 0
    return abs(int(size or 0)) != 0


def set_leverage(client: DeltaRestClient, product_id: int):
    try:
        client.set_leverage(product_id, str(cfg.LEVERAGE))
    except Exception as e:
        log.warning("set_leverage failed (account leverage stays as is): %s", e)


def _order_id(resp):
    if isinstance(resp, dict):
        return (resp.get("result") or resp).get("id")
    return None


def place_bracket_trade(client: DeltaRestClient, product_id: int, side: str,
                        size: int, sl_price, tp_price) -> dict:
    """
    side: "BUY" or "SELL" (entry direction). Market entry, then reduce-only stop-market SL
    and reduce-only limit TP. sl_price / tp_price should already be tick-rounded.
    """
    entry_side = "buy" if side == "BUY" else "sell"
    exit_side = "sell" if side == "BUY" else "buy"

    log.info("Placing MARKET %s entry, size=%s, product_id=%s", entry_side.upper(), size, product_id)
    entry_order = client.place_order(product_id=product_id, size=size, side=entry_side,
                                     order_type=OrderType.MARKET)

    # -- stop loss (same payload as your test_bracket.py). If it fails we are naked: flatten. --
    try:
        log.info("Placing reduce-only STOP (%s) at %s", exit_side, sl_price)
        sl_order = client.create_order({
            "product_id": product_id,
            "size": int(size),
            "side": exit_side,
            "order_type": "market_order",
            "stop_order_type": "stop_loss_order",
            "stop_price": str(sl_price),
            "reduce_only": "true",
        })
    except Exception:
        log.error("SL placement FAILED after entry - closing position at market")
        try:
            client.place_order(product_id=product_id, size=size, side=exit_side,
                               order_type=OrderType.MARKET, reduce_only="true")
        except Exception:
            log.critical("EMERGENCY CLOSE FAILED - position may be open WITHOUT a stop. "
                         "Fix manually on Delta NOW (product_id=%s)", product_id)
        raise

    # -- take profit: if this fails the SL is already protecting the trade --
    tp_order = None
    try:
        log.info("Placing reduce-only TAKE-PROFIT limit (%s) at %s", exit_side, tp_price)
        tp_order = client.place_order(product_id=product_id, size=size, side=exit_side,
                                      order_type=OrderType.LIMIT, limit_price=str(tp_price),
                                      reduce_only="true", time_in_force=TimeInForce.GTC)
    except Exception as e:
        log.error("TP placement failed (SL is in place, trade has no target): %s", e)

    return {"entry_order": entry_order, "sl_order": sl_order, "tp_order": tp_order,
            "sl_order_id": _order_id(sl_order), "tp_order_id": _order_id(tp_order)}


def cancel_leftover_orders(client: DeltaRestClient, product_id: int, tracked: dict):
    """The SL and TP are separate orders; when one fills the other stays on the book and could
    close your NEXT position. Cancel the two orders this bot placed, by ID."""
    for key in ("sl_order_id", "tp_order_id"):
        oid = (tracked or {}).get(key)
        if not oid:
            continue
        try:
            client.cancel_order(product_id, oid)
            log.info("Cancelled leftover order %s (%s)", oid, key)
        except Exception as e:     # already filled / cancelled is expected
            log.info("Leftover order %s not cancelled (%s)", oid, e)