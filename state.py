"""
Tiny JSON-backed state store so the algo doesn't duplicate signals or lose
track of an open position if it restarts. One file per symbol.
"""
import json
import os
import config as cfg

DEFAULT_STATE = {
    "last_processed_timestamp": None,   # timestamp of the last candle we evaluated
    "open_position": None,              # {"side","entry","sl","tp","sl_order_id","tp_order_id","size"}
}


def _state_file(symbol: str) -> str:
    base_dir = os.path.dirname(cfg.STATE_FILE)
    return os.path.join(base_dir, f"state_{symbol}.json")


def load_state(symbol: str) -> dict:
    path = _state_file(symbol)
    if not os.path.exists(path):
        return dict(DEFAULT_STATE)
    with open(path, "r") as f:
        return json.load(f)


def save_state(symbol: str, state: dict) -> None:
    with open(_state_file(symbol), "w") as f:
        json.dump(state, f, indent=2)

