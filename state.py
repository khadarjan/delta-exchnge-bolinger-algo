"""
Tiny JSON-backed state store so the algo doesn't duplicate signals or lose
track of an open position if it restarts. One file per symbol.
Tracks the last processed candle PER TIMEFRAME (2h and 4h are evaluated separately).
"""
import copy
import json
import os
import config as cfg

DEFAULT_STATE = {
    "last_processed": {},       # {"2h": ts, "4h": ts} - last closed candle evaluated per timeframe (CLOSE mode)
    "armed": {},                # {"2h": {"c1_ts": ts, "setups": [...]}} - setups waiting for C2 (INTRABAR mode)
    "entered": {},              # {"2h": c1_ts} - setup for that C1 already acted on (INTRABAR mode)
    "open_position": None,      # {"side","entry","sl","tp","size","tf","sl_order_id","tp_order_id"}
}


def _state_file(symbol: str) -> str:
    base_dir = os.path.dirname(cfg.STATE_FILE)
    return os.path.join(base_dir, f"state_{symbol}.json")


def load_state(symbol: str) -> dict:
    path = _state_file(symbol)
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULT_STATE)
    with open(path, "r") as f:
        s = json.load(f)
    # migrate old single-timeframe files (they only had last_processed_timestamp)
    s.setdefault("last_processed", {})
    s.setdefault("armed", {})
    s.setdefault("entered", {})
    s.setdefault("open_position", None)
    s.pop("last_processed_timestamp", None)
    return s


def save_state(symbol: str, state: dict) -> None:
    tmp = _state_file(symbol) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, _state_file(symbol))      # atomic: never leaves a half-written file