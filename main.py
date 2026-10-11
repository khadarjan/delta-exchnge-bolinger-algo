"""
Main entrypoint. Runs the BBAWE (Band Pierce / Band Break Exhaustion) strategy on the 2H and 4H
candles of every symbol in config.SYMBOLS (default: ETHUSD + PAXGUSD). Each symbol keeps its own
state file and its own single-open-position limit.

ENTRY_MODE (config / env):
  INTRABAR (default) - when C1 closes the setup is "armed"; during the next candle (C2) the bot
                       enters the moment C2 breaks C1's high+buffer (buy) / low-buffer (sell),
                       like the Pine arrow on the live bar. No break by C2's close -> dropped.
  CLOSE              - wait for C2 to close, then enter at market (same signal, later fill).

DRY_RUN is TRUE by default: it logs the order it WOULD send and places nothing.
Set DRY_RUN=false in your env file to trade for real.

Run with:  python main.py
Stop with: Ctrl+C  (or create a file named STOP in this folder - checked each loop)
"""
import logging
import os
import sys
import time

import config as cfg
import state as st
from bb_signal import fetch_candles, compute_signal, armed_setups, RES_SECONDS
from executor import (get_client, get_product_id, place_bracket_trade, has_open_position,
                      get_mark_price, round_to_tick, set_leverage, cancel_leftover_orders)

STOP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "STOP")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(cfg.LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


# --------------------------------------------------------------------------
# Shared entry routine (both modes). Returns "done" | "skip" | "retry"
#   done  - order placed (or dry-run logged)
#   skip  - a guard says never take this setup
#   retry - price drifted too far from the trigger right now; INTRABAR may try again this candle
# --------------------------------------------------------------------------
def _enter(client, symbol, tf, product_id, s, tag, setup, intrabar):
    side, sl, tp, trigger = setup["signal"], setup["sl"], setup["tp"], setup["entry"]

    # guard: one position per symbol (covers a 2H and a 4H setup on the same symbol)
    if s["open_position"] is not None:
        log.warning("%s skipped: algo already tracks an open position.", tag)
        return "skip"
    if client is not None and has_open_position(client, product_id):
        log.warning("%s skipped: exchange reports an open position already.", tag)
        return "skip"

    # guard: the market entry must still sit between SL and TP
    mark = get_mark_price(symbol)
    if not ((sl < mark < tp) if side == "BUY" else (tp < mark < sl)):
        log.warning("%s skipped: mark %.2f is not between SL %.2f and TP %.2f.", tag, mark, sl, tp)
        return "skip"

    # guard (intrabar): don't chase - price must still be close to the trigger level
    if intrabar:
        risk = abs(trigger - sl)
        drift = abs(mark - trigger)
        if risk > 0 and drift > cfg.ENTRY_MAX_DRIFT_RISK_FRAC * risk:
            log.info("%s waiting: mark %.2f is %.2f from trigger %.2f (max %.2f).",
                     tag, mark, drift, trigger, cfg.ENTRY_MAX_DRIFT_RISK_FRAC * risk)
            return "retry"

    sl_s, tp_s = round_to_tick(symbol, sl), round_to_tick(symbol, tp)
    size = cfg.ORDER_SIZES.get(symbol, 1)

    if cfg.DRY_RUN:
        log.info("[DRY RUN] %s would place MARKET %s size=%s mark=%.2f trigger=%.2f SL=%s TP=%s",
                 tag, side, size, mark, trigger, sl_s, tp_s)
        return "done"

    set_leverage(client, product_id)
    orders = place_bracket_trade(client, product_id, side, size, sl_s, tp_s)
    s["open_position"] = {
        "side": side, "tf": tf, "setup": setup["setup_type"], "mode": cfg.ENTRY_MODE,
        "entry": trigger, "sl": sl_s, "tp": tp_s, "size": size,
        "sl_order_id": orders.get("sl_order_id"), "tp_order_id": orders.get("tp_order_id"),
    }
    st.save_state(symbol, s)
    log.info("%s trade placed and recorded in state: %s", tag, s["open_position"])
    return "done"


# --------------------------------------------------------------------------
# CLOSE mode: act once C2 has closed
# --------------------------------------------------------------------------
def run_close_mode(client, symbol, tf, product_id, s):
    df = fetch_candles(symbol, tf, cfg.CANDLE_LOOKBACK)          # closed candles only
    if len(df) < cfg.BB_LENGTH + 2:
        log.warning("[%s %s] Not enough candle history yet for Bollinger warm-up.", symbol, tf)
        return s

    latest_closed_ts = float(df.iloc[-1]["timestamp"])
    if s["last_processed"].get(tf) == latest_closed_ts:
        return s

    log.info("[%s %s] New closed candle @ %s. Evaluating signal...", symbol, tf, latest_closed_ts)
    result = compute_signal(df)
    s["last_processed"][tf] = latest_closed_ts       # mark BEFORE any order: at-most-once per candle
    st.save_state(symbol, s)

    if not (result["signal"] and result.get("bar_index") == len(df) - 1):
        log.info("[%s %s] No fresh signal on this candle.", symbol, tf)
        return s

    tag = f"[{symbol} {tf}] {result['signal']} ({result['setup_type']})"
    log.info("%s entry=%.2f sl=%.2f tp=%.2f", tag, result["entry"], result["sl"], result["tp"])

    delay_min = (time.time() - (latest_closed_ts + RES_SECONDS[tf])) / 60
    if delay_min > cfg.MAX_SIGNAL_DELAY_MIN:
        log.warning("%s skipped: signal closed %.0f min ago (limit %s min).", tag, delay_min, cfg.MAX_SIGNAL_DELAY_MIN)
        return s

    _enter(client, symbol, tf, product_id, s, tag, result, intrabar=False)
    return s


# --------------------------------------------------------------------------
# INTRABAR mode: arm on C1's close, enter during C2 the moment the level breaks
# --------------------------------------------------------------------------
def run_intrabar_mode(client, symbol, tf, product_id, s):
    step = RES_SECONDS[tf]
    df = fetch_candles(symbol, tf, cfg.CANDLE_LOOKBACK, include_forming=True)
    forming = None
    if float(df.iloc[-1]["timestamp"]) + step > time.time():
        forming = df.iloc[-1]
        closed = df.iloc[:-1].reset_index(drop=True)
    else:
        closed = df
    if len(closed) < cfg.BB_LENGTH + 2:
        log.warning("[%s %s] Not enough candle history yet for Bollinger warm-up.", symbol, tf)
        return s

    c1_ts = float(closed.iloc[-1]["timestamp"])

    # a new candle closed -> it is the new C1 candidate; whatever was armed before is now expired
    armed = s["armed"].get(tf)
    if armed is None or armed["c1_ts"] != c1_ts:
        setups = armed_setups(closed)
        armed = s["armed"][tf] = {"c1_ts": c1_ts, "setups": setups}
        st.save_state(symbol, s)
        if setups:
            log.info("[%s %s] ARMED by candle @ %s: %s", symbol, tf, c1_ts,
                     [(x["signal"], x["setup_type"], round(x["entry"], 2)) for x in setups])
        else:
            log.info("[%s %s] New closed candle @ %s: nothing armed.", symbol, tf, c1_ts)

    if not armed["setups"] or s["entered"].get(tf) == c1_ts or forming is None:
        return s
    if float(forming["timestamp"]) != c1_ts + step:      # forming candle must be exactly C2
        return s

    for setup in armed["setups"]:
        broke = (float(forming["high"]) > setup["entry"]) if setup["signal"] == "BUY" \
            else (float(forming["low"]) < setup["entry"])
        if not broke:
            continue
        tag = f"[{symbol} {tf}] {setup['signal']} ({setup['setup_type']}) INTRABAR"
        log.info("%s C2 broke trigger %.2f (high=%.2f low=%.2f)", tag, setup["entry"],
                 float(forming["high"]), float(forming["low"]))
        status = _enter(client, symbol, tf, product_id, s, tag, setup, intrabar=True)
        if status != "retry":
            s["entered"][tf] = c1_ts                      # once per C1, even if skipped by a guard
            st.save_state(symbol, s)
        break
    return s


def position_closed_externally(client, symbol, product_id, s) -> bool:
    if client is None or s["open_position"] is None:
        return False
    if not has_open_position(client, product_id):
        log.info("[%s] Tracked position no longer open on exchange (SL/TP hit). Clearing state.", symbol)
        cancel_leftover_orders(client, product_id, s["open_position"])   # remove the surviving SL/TP
        s["open_position"] = None
        return True
    return False


def main():
    log.info("Starting BBAWE algo for symbols=%s timeframes=%s mode=%s on %s | DRY_RUN=%s",
             cfg.SYMBOLS, cfg.TIMEFRAMES, cfg.ENTRY_MODE, cfg.BASE_URL, cfg.DRY_RUN)
    if cfg.ENTRY_MODE not in ("INTRABAR", "CLOSE"):
        raise RuntimeError(f"ENTRY_MODE must be INTRABAR or CLOSE, got {cfg.ENTRY_MODE!r}")

    if cfg.API_KEY and cfg.API_SECRET:
        client = get_client()
    elif cfg.DRY_RUN:
        client = None
        log.warning("No API keys set - DRY_RUN will skip exchange position checks.")
    else:
        raise RuntimeError("DELTA_API_KEY / DELTA_API_SECRET not set - cannot trade live.")

    product_ids, states = {}, {}
    for symbol in cfg.SYMBOLS:
        product_ids[symbol] = get_product_id(symbol)
        states[symbol] = st.load_state(symbol)
        log.info("Resolved product_id=%s for %s (order size=%s)",
                 product_ids[symbol], symbol, cfg.ORDER_SIZES.get(symbol))

    run_tf = run_intrabar_mode if cfg.ENTRY_MODE == "INTRABAR" else run_close_mode
    pause = cfg.INTRABAR_POLL_SECONDS if cfg.ENTRY_MODE == "INTRABAR" else cfg.POLL_SECONDS

    while True:
        if os.path.exists(STOP_FILE):
            log.info("STOP file detected - shutting down cleanly.")
            break

        for symbol in cfg.SYMBOLS:
            product_id = product_ids[symbol]
            s = states[symbol]
            try:
                position_closed_externally(client, symbol, product_id, s)
                for tf in cfg.TIMEFRAMES:
                    try:
                        s = run_tf(client, symbol, tf, product_id, s)
                    except Exception as e:
                        log.exception("[%s %s] Error: %s", symbol, tf, e)
                states[symbol] = s
                st.save_state(symbol, s)
            except Exception as e:
                log.exception("[%s] Error in main loop: %s", symbol, e)

        time.sleep(pause)


if __name__ == "__main__":
    main()