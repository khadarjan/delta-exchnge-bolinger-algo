"""
Central config. Secrets come from environment / .env — never hardcode keys here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Exchange / Auth ──────────────────────────────────────────
API_KEY = os.getenv("DELTA_API_KEY", "")
API_SECRET = os.getenv("DELTA_API_SECRET", "")

# Production-India by default (change to Production-Global if your account is global)
# Production-India : https://api.india.delta.exchange
# Production-Global: https://api.delta.exchange
BASE_URL = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")

SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "ETHUSD,XAUUSD").split(",") if s.strip()]
# ETHUSD = Ethereum perpetual. XAUUSD = Gold perpetual on Delta India.
# Confirm the exact gold symbol string in-app (Derivatives -> search "Gold") before running live —
# it may differ on your account (e.g. XAUUSD vs a dated future).
RESOLUTION = "4h"                             # candle timeframe — must match strategy TF

# ── Strategy Parameters (mirrors the Pine Script v5 logic) ──
EMA_LEN = 50
RR = 2.0                    # reward:risk multiple
MAX_WICK_PCT = 30.0         # C1 total wick must be <= this % of candle range
BUFFER_POINTS = 5.0         # breakout must clear C1 high/low by this much
MIN_TARGET_POINTS = 100.0   # floor for TP distance
MAX_SL_POINTS = 100.0       # ceiling for SL distance (clamped, not skipped)

# ── Position sizing / risk (per symbol — ETH and Gold have very different
#    contract sizes/prices, so don't share one blanket order size) ─────────
def _order_size_for(symbol: str) -> int:
    env_key = f"ORDER_SIZE_{symbol}"
    return int(os.getenv(env_key, os.getenv("ORDER_SIZE", "1")))

ORDER_SIZES = {sym: _order_size_for(sym) for sym in SYMBOLS}
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
MAX_CONCURRENT_POSITIONS_PER_SYMBOL = 1             # each symbol only ever holds one position

# ── Execution ─────────────────────────────────────────────
POLL_SECONDS = 60           # how often to check for a new closed 4H candle
CANDLE_LOOKBACK = 200        # candles pulled per fetch (enough for EMA50 warmup)

# ── Files ─────────────────────────────────────────────────
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
LOG_FILE = os.path.join(os.path.dirname(__file__), "algo.log")
