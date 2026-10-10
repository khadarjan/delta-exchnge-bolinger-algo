"""
Quick backtest test with limited data for fast execution.
"""
import pandas as pd
import numpy as np
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfg
from backtest import load_trades, aggregate_ohlcv, simulate_trades, print_report

def main():
    print("=" * 70)
    print("QUICK BACKTEST TEST RUN")
    print("=" * 70)

    try:
        # Test with a very small date range first
        print("\nLoading data for 2025-01-01 to 2025-01-03...")
        # Load full month then filter to first few days for speed
        trades = load_trades("ETHUSD", "2025-01", "2025-01")
        print(f"Loaded {len(trades):,} total trades for January 2025")

        # Filter to first 3 days only
        start_date = trades['timestamp'].min()
        end_date = start_date + pd.Timedelta(days=3)
        trades = trades[(trades['timestamp'] >= start_date) & (trades['timestamp'] < end_date)]
        print(f"Filtered to {len(trades):,} trades from {start_date} to {end_date}")

        if len(trades) == 0:
            print("ERROR: No data in selected date range")
            return

        print("Aggregating into 4h candles...")
        candles = aggregate_ohlcv(trades, cfg.RESOLUTION)
        print(f"Generated {len(candles)} candles")

        # Run simulation (using candle approximation for speed)
        print("Running trade simulation...")
        result = simulate_trades(candles, None)  # No tick data for speed

        # Print the report
        print_report(result, "ETHUSD")

        # Save detailed results to CSV
        output_dir = os.path.dirname(os.path.abspath(__file__))
        if result["trades"]:
            trades_csv = os.path.join(output_dir, f"quick_backtest_ETHUSD_trades.csv")
            pd.DataFrame(result["trades"]).to_csv(trades_csv, index=False)
            print(f"\nTrade log saved to: {trades_csv}")

        if result["equity_curve"]:
            equity_csv = os.path.join(output_dir, f"quick_backtest_ETHUSD_equity.csv")
            pd.DataFrame(result["equity_curve"]).to_csv(equity_csv, index=False)
            print(f"Equity curve saved to: {equity_csv}")

        print("\n✅ Quick backtest completed successfully!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()