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

SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "ETHUSD,PAXGUSD").split(",") if s.strip()]
# ETHUSD = Ethereum perpetual (product_id 3136). PAXGUSD = Gold perpetual (product_id 123006) -
# both resolved in your algo.log; "XAUUSD" did not resolve there.

# BBAWE trades on these timeframes ONLY (Pine dashboard rows 2H and 4H)
TIMEFRAMES = ["2h", "4h"]
RESOLUTION = "4h"                             # legacy: only the old EMA50 signal_engine/main used it

# ── BBAWE parameters (mirror the Pine inputs; bb_signal.py reads these) ──
BB_USE_EMA = False          # Pine: Use EMA for Bollinger Band
BB_LENGTH = 20              # Pine: Bollinger Length
BB_MULT = 2.0               # Pine: Base Multiplier
BB_BREAK_BUFFER = 5.0       # Pine: Break Buffer (points, absolute price units)

# ── Strategy Parameters ──
EMA_LEN = 50                # legacy EMA50 strategy (signal_engine.py) - unused by BBAWE
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
POLL_SECONDS = 60           # how often to check for a new closed 2H/4H candle
CANDLE_LOOKBACK = 300        # candles pulled per fetch (BB warm-up)

# DRY_RUN defaults to TRUE: signals are logged and the exact order that WOULD be sent is
# printed, but nothing is placed. Set DRY_RUN=false in _env/.env to trade for real.
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() != "false"
# Skip a signal if its confirming candle closed more than this many minutes ago
# (e.g. the bot was offline) - a market entry hours late is not the backtested trade.
MAX_SIGNAL_DELAY_MIN = float(os.getenv("MAX_SIGNAL_DELAY_MIN", "15"))

# ── Files ─────────────────────────────────────────────────
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
LOG_FILE = os.path.join(os.path.dirname(__file__), "algo.log")


# ── Entry mode ─────────────────────────────────────────────
# INTRABAR (default): after C1 closes, watch the next candle (C2) and enter the moment it breaks
#                     C1's high+buffer (buy) / low-buffer (sell) - like the Pine arrow on the live bar.
#                     If C2 closes without breaking, the setup is dropped.
# CLOSE:              wait for C2 to CLOSE, then enter at market (same signal, later, worse fill).
ENTRY_MODE = os.getenv("ENTRY_MODE", "INTRABAR").strip().upper()
INTRABAR_POLL_SECONDS = float(os.getenv("INTRABAR_POLL_SECONDS", "15"))   # how often C2 is checked
# INTRABAR safety: skip if price has run away from the trigger by more than this fraction of the
# trade's risk (SL distance) - e.g. 0.5 = don't chase more than half a stop-distance past the level.
ENTRY_MAX_DRIFT_RISK_FRAC = float(os.getenv("ENTRY_MAX_DRIFT_RISK_FRAC", "0.5"))