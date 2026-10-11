"""
Quick standalone runner: fetches the latest CLOSED 2H and 4H candles for each symbol in
config.SYMBOLS and prints the BBAWE signal the most recently closed candle produced, if any.
Places NO orders.
"""
import config as cfg
from bb_signal import fetch_candles, compute_signal

for symbol in cfg.SYMBOLS:
    for tf in cfg.TIMEFRAMES:
        print(f"\n=== {symbol} ({tf}) ===")
        try:
            df = fetch_candles(symbol, tf, cfg.CANDLE_LOOKBACK)
        except Exception as e:
            print(f"  fetch failed: {e}")
            continue

        result = compute_signal(df)
        if not result["signal"] or result.get("bar_index") != len(df) - 1:
            print("  No signal on the latest closed candle.")
            continue

        print(f"  Signal:     {result['signal']}")
        print(f"  Setup type: {result['setup_type']}")
        print(f"  Entry:      {result['entry']}")
        print(f"  SL:         {result['sl']}")
        print(f"  TP:         {result['tp']}")
        print(f"  C1 index:   {result['c1_index']}  |  Bar index: {result['bar_index']}")