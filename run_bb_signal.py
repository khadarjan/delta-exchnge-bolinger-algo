"""
Quick standalone runner: fetches the latest candles for each symbol in
config.SYMBOLS and prints whatever BBAWE (Band Pierce / Band Break
Exhaustion) signal the most recently closed candle produced, if any.

This does NOT place any orders — it's just for checking the signal logic
works and seeing what it outputs. Wire it into your execution loop
(state file, order placement) once you're happy with it.
"""
import config as cfg
from bb_signal import fetch_candles, compute_signal

for symbol in cfg.SYMBOLS:
    print(f"\n=== {symbol} ({cfg.RESOLUTION}) ===")
    try:
        df = fetch_candles(symbol, cfg.RESOLUTION, cfg.CANDLE_LOOKBACK)
    except Exception as e:
        print(f"  fetch failed: {e}")
        continue

    result = compute_signal(df)

    if result["signal"] is None:
        print("  No signal on the latest closed candle.")
    else:
        print(f"  Signal:     {result['signal']}")
        print(f"  Setup type: {result['setup_type']}")
        print(f"  Entry:      {result['entry']}")
        print(f"  SL:         {result['sl']}")
        print(f"  TP:         {result['tp']}")
        print(f"  C1 index:   {result['c1_index']}  |  Bar index: {result['bar_index']}")
