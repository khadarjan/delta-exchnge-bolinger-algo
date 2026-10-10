"""
Main entrypoint. Runs the same 4H EMA50 strategy independently across every
symbol in config.SYMBOLS (default: ETHUSD + XAUUSD). Each symbol keeps its
own state file and its own single-open-position limit — a signal on Gold
never affects ETH and vice versa.

Run with:  python main.py
Stop with: Ctrl+C  (or create a file named STOP in this folder — checked each loop)
"""
import logging
import os
import time
import sys

import config as cfg
import state as st
from signal_engine import fetch_candles, compute_signal
from executor import get_client, get_product_id, place_bracket_trade, has_open_position

STOP_FILE = os.path.join(os.path.dirname(__file__), "STOP")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(cfg.LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


def run_once_for_symbol(client, symbol, product_id, s):
    df = fetch_candles(symbol, cfg.RESOLUTION, cfg.CANDLE_LOOKBACK)
    if len(df) < cfg.EMA_LEN + 2:
        log.warning("[%s] Not enough candle history yet for EMA warmup.", symbol)
        return s

    # Drop the last row if it's still the currently-forming (unclosed) candle.
    now = time.time()
    seconds_map = {"4h": 14400}
    step = seconds_map.get(cfg.RESOLUTION, 14400)
    last_ts = df.iloc[-1]["timestamp"]
    if last_ts + step > now:
        df = df.iloc[:-1]

    if df.empty:
        return s

    latest_closed_ts = df.iloc[-1]["timestamp"]
    if s["last_processed_timestamp"] == latest_closed_ts:
        return s  # already handled this candle, nothing new

    log.info("[%s] New closed candle @ %s. Evaluating signal...", symbol, latest_closed_ts)
    result = compute_signal(df)
    s["last_processed_timestamp"] = latest_closed_ts

    if result["signal"] and result.get("bar_index") == len(df) - 1:
        log.info("[%s] Signal: %s entry=%.2f sl=%.2f tp=%.2f",
                  symbol, result["signal"], result["entry"], result["sl"], result["tp"])

        if s["open_position"] is not None:
            log.warning("[%s] Signal fired but algo already tracks an open position — skipping.", symbol)
        elif has_open_position(client, product_id):
            log.warning("[%s] Signal fired but exchange reports an open position already — skipping.", symbol)
        else:
            size = cfg.ORDER_SIZES.get(symbol, 1)
            orders = place_bracket_trade(
                client, product_id, result["signal"], size,
                result["sl"], result["tp"],
            )
            s["open_position"] = {
                "side": result["signal"],
                "entry": result["entry"],
                "sl": result["sl"],
                "tp": result["tp"],
                "size": size,
                "orders": orders,
            }
            log.info("[%s] Trade placed and recorded in state: %s", symbol, s["open_position"])
    else:
        log.info("[%s] No fresh signal on this candle.", symbol)

    return s


def position_closed_externally(client, symbol, product_id, s) -> bool:
    if s["open_position"] is None:
        return False
    if not has_open_position(client, product_id):
        log.info("[%s] Tracked position no longer open on exchange (SL/TP hit). Clearing state.", symbol)
        s["open_position"] = None
        return True
    return False


def main():
    log.info("Starting 4H EMA50 algo for symbols=%s on %s", cfg.SYMBOLS, cfg.BASE_URL)
    client = get_client()

    product_ids = {}
    states = {}
    for symbol in cfg.SYMBOLS:
        product_ids[symbol] = get_product_id(symbol)
        states[symbol] = st.load_state(symbol)
        log.info("Resolved product_id=%s for %s (order size=%s)",
                 product_ids[symbol], symbol, cfg.ORDER_SIZES.get(symbol))

    while True:
        if os.path.exists(STOP_FILE):
            log.info("STOP file detected — shutting down cleanly.")
            break

        for symbol in cfg.SYMBOLS:
            product_id = product_ids[symbol]
            s = states[symbol]
            try:
                position_closed_externally(client, symbol, product_id, s)
                s = run_once_for_symbol(client, symbol, product_id, s)
                states[symbol] = s
                st.save_state(symbol, s)
            except Exception as e:
                log.exception("[%s] Error in main loop: %s", symbol, e)

        time.sleep(cfg.POLL_SECONDS)


if __name__ == "__main__":
    main()
