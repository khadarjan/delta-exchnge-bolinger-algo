"""
Simple test to verify the backtest logic works.
Run this once you have Python working: python simple_backtest_test.py
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
    print("SIMPLE BACKTEST TEST RUN")
    print("=" * 70)
    
    try:
        # Test with a small date range first
        print("\nTesting with small date range: 2025-01 to 2025-02")
        trades = load_trades("ETHUSD", "2025-01", "2025-02")
        print(f"✓ Loaded {len(trades):,} trades")
        
        candles = aggregate_ohlcv(trades, cfg.RESOLUTION)
        print(f"✓ Generated {len(candles)} {cfg.RESOLUTION} candles")
        
        # Run simulation (using candle approximation for speed)
        result = simulate_trades(candles, None)
        print(f"✓ Simulation complete: {result['metrics']['total_trades']} trades executed")
        
        # Print the report
        print_report(result, "ETHUSD")
        
        print("\n✅ Backtest test completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()