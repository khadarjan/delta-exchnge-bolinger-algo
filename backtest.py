"""
Backtest the 4H EMA50 C1/C2 strategy using Delta Exchange trade-level tick data.

Data format (from CSV zips):
    product_symbol, price, size, timestamp, buyer_role

Pipeline:
    1. Load & aggregate trades into 4H OHLCV candles
    2. Run signal_engine.compute_signal() on the candle series
    3. Simulate trade execution with SL/TP hit logic
    4. Report performance metrics

Usage:
    python backtest.py [--symbol ETHUSD] [--start 2025-01] [--end 2025-06]
"""
import argparse
import csv
import glob
import io
import os
import sys
import zipfile

import pandas as pd
import numpy as np

# Add project root to path so we can import config & signal_engine
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfg
from signal_engine import compute_signal


# ── 1. Data Loading ──────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_data")


def load_trades(symbol: str, start_month: str = None, end_month: str = None) -> pd.DataFrame:
    """
    Load trade-level tick data from all monthly zip files for a given symbol.
    
    Parameters
    ----------
    symbol : str
        e.g. "ETHUSD"
    start_month : str, optional
        e.g. "2025-01" — inclusive
    end_month : str, optional
        e.g. "2025-06" — inclusive
    
    Returns
    -------
    pd.DataFrame with columns: timestamp, price, size, buyer_role
    """
    pattern = os.path.join(DATA_DIR, f"futures-trades-monthly-{symbol}-*.csv.zip")
    files = sorted(glob.glob(pattern))
    
    if not files:
        raise FileNotFoundError(f"No trade data files found for {symbol} at {pattern}")
    
    all_rows = []
    for fpath in files:
        # Extract month from filename for filtering
        fname = os.path.basename(fpath)
        # e.g. futures-trades-monthly-ETHUSD-2025-01.csv.zip
        parts = fname.replace(".csv.zip", "").split("-")
        month_str = f"{parts[-2]}-{parts[-1]}"  # "2025-01"
        
        if start_month and month_str < start_month:
            continue
        if end_month and month_str > end_month:
            continue
        
        with zipfile.ZipFile(fpath) as z:
            csv_filename = z.namelist()[0]
            with z.open(csv_filename) as f:
                reader = csv.DictReader(io.TextIOWrapper(f))
                for row in reader:
                    all_rows.append({
                        "timestamp": row["timestamp"],
                        "price": float(row["price"]),
                        "size": float(row["size"]),
                        "buyer_role": row["buyer_role"],
                    })
    
    if not all_rows:
        raise ValueError(f"No trade data loaded for {symbol} in range {start_month}–{end_month}")
    
    df = pd.DataFrame(all_rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ── 2. OHLCV Aggregation ─────────────────────────────────────

def aggregate_ohlcv(trades: pd.DataFrame, resolution: str = "4h") -> pd.DataFrame:
    """
    Aggregate trade-level tick data into OHLCV candles.
    
    Parameters
    ----------
    trades : pd.DataFrame
        Must have columns: timestamp, price, size
    resolution : str
        Candle resolution (e.g. "4h", "1h", "1d")
    
    Returns
    -------
    pd.DataFrame with columns: timestamp, open, high, low, close, volume
    """
    # Round timestamps to the candle boundary
    trades = trades.copy()
    trades["candle_ts"] = trades["timestamp"].dt.floor(resolution)
    
    def agg_group(group):
        return pd.Series({
            "open": group.iloc[0]["price"],
            "high": group["price"].max(),
            "low": group["price"].min(),
            "close": group.iloc[-1]["price"],
            "volume": group["size"].sum(),
        })
    
    candles = trades.groupby("candle_ts", sort=True).apply(agg_group).reset_index()
    candles = candles.rename(columns={"candle_ts": "timestamp"})
    
    # Convert timestamp to unix seconds (matching signal_engine expectation)
    candles["timestamp"] = candles["timestamp"].astype(np.int64) // 10**9
    
    return candles


# ── 3. Trade Simulation ──────────────────────────────────────

def simulate_trades(candles: pd.DataFrame, trades_df: pd.DataFrame = None) -> dict:
    """
    Run the signal engine over the candle series and simulate trade execution
    with SL/TP hit logic using tick-level data for precise fills.
    
    Parameters
    ----------
    candles : pd.DataFrame
        OHLCV candle data with columns: timestamp, open, high, low, close, volume
    trades_df : pd.DataFrame, optional
        Tick-level trade data for precise SL/TP hit simulation.
        If None, uses candle OHLC for approximate hit detection.
    
    Returns
    -------
    dict with trade log and performance metrics
    """
    df = candles.copy()
    df["ema50"] = df["close"].ewm(span=cfg.EMA_LEN, adjust=False).mean()
    
    trades_log = []
    equity_curve = []
    balance = 0.0  # PnL in points
    wins = 0
    losses = 0
    
    long_armed = False
    short_armed = False
    c1_high = c1_low = None
    breakout_long = breakout_short = None
    c1_index = None
    c1_timestamp = None
    
    start_idx = cfg.EMA_LEN
    
    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        high, low, close, open_ = row.high, row.low, row.close, row.open
        ema = row.ema50
        candle_ts = row.timestamp
        
        # ── Check if we have an active position ──
        # If we have an open trade, check if SL or TP gets hit on this candle
        if trades_log and trades_log[-1].get("exit_time") is None:
            active_trade = trades_log[-1]
            
            # Check for SL/TP hit using tick data if available, else candle OHLC
            if trades_df is not None:
                # Use tick data for precise hit detection
                tick_window = trades_df[
                    (trades_df["timestamp"] >= active_trade["entry_time"]) &
                    (trades_df["timestamp"] <= candle_ts)
                ]
                if not tick_window.empty:
                    tick_prices = tick_window["price"].values
                    if active_trade["side"] == "BUY":
                        # Long: SL below, TP above
                        sl_hit = tick_prices.min() <= active_trade["sl"]
                        tp_hit = tick_prices.max() >= active_trade["tp"]
                    else:
                        # Short: SL above, TP below
                        sl_hit = tick_prices.max() >= active_trade["sl"]
                        tp_hit = tick_prices.min() <= active_trade["tp"]
                else:
                    sl_hit = False
                    tp_hit = False
            else:
                # Approximate using candle OHLC
                if active_trade["side"] == "BUY":
                    sl_hit = low <= active_trade["sl"]
                    tp_hit = high >= active_trade["tp"]
                else:
                    sl_hit = high >= active_trade["sl"]
                    tp_hit = low <= active_trade["tp"]
            
            if sl_hit:
                active_trade["exit_time"] = candle_ts
                active_trade["exit_price"] = active_trade["sl"]
                active_trade["pnl"] = active_trade["sl"] - active_trade["entry_price"] if active_trade["side"] == "BUY" else active_trade["entry_price"] - active_trade["sl"]
                active_trade["exit_reason"] = "SL"
                balance += active_trade["pnl"]
                if active_trade["pnl"] > 0:
                    wins += 1
                else:
                    losses += 1
                equity_curve.append({"time": candle_ts, "balance": balance})
            elif tp_hit:
                active_trade["exit_time"] = candle_ts
                active_trade["exit_price"] = active_trade["tp"]
                active_trade["pnl"] = active_trade["tp"] - active_trade["entry_price"] if active_trade["side"] == "BUY" else active_trade["entry_price"] - active_trade["tp"]
                active_trade["exit_reason"] = "TP"
                balance += active_trade["pnl"]
                if active_trade["pnl"] > 0:
                    wins += 1
                else:
                    losses += 1
                equity_curve.append({"time": candle_ts, "balance": balance})
        
        # ── Step 1: this candle acts as C2 → check buffered breakout ──
        buy_signal = long_armed and high > breakout_long
        sell_signal = short_armed and low < breakout_short
        
        if buy_signal and (not trades_log or trades_log[-1].get("exit_time") is not None):
            entry = breakout_long
            risk = min(entry - c1_low, cfg.MAX_SL_POINTS)
            sl = entry - risk
            tgt = max(cfg.RR * risk, cfg.MIN_TARGET_POINTS)
            tp = entry + tgt
            
            trade = {
                "entry_time": candle_ts,
                "entry_price": entry,
                "side": "BUY",
                "sl": sl,
                "tp": tp,
                "risk": risk,
                "target": tgt,
                "c1_index": c1_index,
                "c2_index": i,
                "exit_time": None,
                "exit_price": None,
                "pnl": None,
                "exit_reason": None,
            }
            trades_log.append(trade)
            equity_curve.append({"time": candle_ts, "balance": balance})
        
        if sell_signal and (not trades_log or trades_log[-1].get("exit_time") is not None):
            entry = breakout_short
            risk = min(c1_high - entry, cfg.MAX_SL_POINTS)
            sl = entry + risk
            tgt = max(cfg.RR * risk, cfg.MIN_TARGET_POINTS)
            tp = entry - tgt
            
            trade = {
                "entry_time": candle_ts,
                "entry_price": entry,
                "side": "SELL",
                "sl": sl,
                "tp": tp,
                "risk": risk,
                "target": tgt,
                "c1_index": c1_index,
                "c2_index": i,
                "exit_time": None,
                "exit_price": None,
                "pnl": None,
                "exit_reason": None,
            }
            trades_log.append(trade)
            equity_curve.append({"time": candle_ts, "balance": balance})
        
        # setup consumed either way (pass or fail)
        long_armed = False
        short_armed = False
        
        # ── Step 2: does THIS candle qualify as a new C1? ──
        candle_range = high - low
        body = abs(close - open_)
        wick_pct = 0.0 if candle_range == 0 else (candle_range - body) / candle_range * 100
        
        is_green = close > open_
        is_red = close < open_
        touched_from_below = low <= ema
        touched_from_above = high >= ema
        
        bull_c1_ok = (close > ema) and touched_from_below and is_green and (wick_pct <= cfg.MAX_WICK_PCT)
        bear_c1_ok = (close < ema) and touched_from_above and is_red and (wick_pct <= cfg.MAX_WICK_PCT)
        
        if bull_c1_ok:
            c1_high, c1_low = high, low
            breakout_long = high + cfg.BUFFER_POINTS
            long_armed = True
            c1_index = i
            c1_timestamp = candle_ts
        
        if bear_c1_ok:
            c1_high, c1_low = high, low
            breakout_short = low - cfg.BUFFER_POINTS
            short_armed = True
            c1_index = i
            c1_timestamp = candle_ts
    
    # Close any still-open trades at the open trades at the last candle
    if trades_log and trades_log[-1].get("exit_time") is None:
        active_trade = trades_log[-1]
        last_candle = df.iloc[-1]
        active_trade["exit_time"] = last_candle.timestamp
        active_trade["exit_price"] = last_candle.close
        active_trade["pnl"] = last_candle.close - active_trade["entry_price"] if active_trade["side"] == "BUY" else active_trade["entry_price"] - last_candle.close
        active_trade["exit_reason"] = "END_OF_DATA"
        balance += active_trade["pnl"]
        if active_trade["pnl"] > 0:
            wins += 1
        else:
            losses += 1
        equity_curve.append({"time": last_candle.timestamp, "balance": balance})
    
    # Compute metrics
    total_trades = len(trades_log)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    
    # Sharpe-like ratio (using trade PnLs)
    if total_trades > 0:
        pnls = [t["pnl"] for t in trades_log if t["pnl"] is not None]
        avg_pnl = np.mean(pnls) if pnls else 0
        std_pnl = np.std(pnls) if len(pnls) > 1 else 1
        sharpe = (avg_pnl / std_pnl) * np.sqrt(365) if std_pnl > 0 else 0
    else:
        avg_pnl = 0
        sharpe = 0
    
    # Max drawdown from equity curve
    if equity_curve:
        eq_df = pd.DataFrame(equity_curve)
        eq_df["peak"] = eq_df["balance"].cummax()
        eq_df["drawdown"] = eq_df["peak"] - eq_df["balance"]
        max_dd = eq_df["drawdown"].max()
    else:
        max_dd = 0
    
    # Profit factor
    if total_trades > 0 and pnls:
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')
    else:
        profit_factor = 0.0
    
    metrics = {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "total_pnl_points": round(balance, 2),
        "avg_pnl_per_trade": round(avg_pnl, 2),
        "max_drawdown_points": round(max_dd, 2),
        "sharpe_annualized": round(sharpe, 2),
        "profit_factor": profit_factor,
    }
    
    return {
        "trades": trades_log,
        "equity_curve": equity_curve,
        "metrics": metrics,
        "candles": df,
    }


# ── 4. Reporting ─────────────────────────────────────────────

def print_report(result: dict, symbol: str):
    """Print a formatted backtest report."""
    m = result["metrics"]
    trades = result["trades"]
    
    print("=" * 70)
    print(f"  BACKTEST REPORT — {symbol}")
    print(f"  Strategy: 4H EMA{cfg.EMA_LEN} C1/C2 Breakout")
    print(f"  Config:   RR={cfg.RR}, MaxWick={cfg.MAX_WICK_PCT}%, "
          f"Buffer={cfg.BUFFER_POINTS}, MinTP={cfg.MIN_TARGET_POINTS}, MaxSL={cfg.MAX_SL_POINTS}")
    print("=" * 70)
    print(f"  Total Trades      : {m['total_trades']}")
    print(f"  Wins / Losses     : {m['wins']} / {m['losses']}")
    print(f"  Win Rate          : {m['win_rate_pct']}%")
    print(f"  Total PnL (pts)   : {m['total_pnl_points']}")
    print(f"  Avg PnL / Trade   : {m['avg_pnl_per_trade']}")
    print(f"  Max Drawdown (pts): {m['max_drawdown_points']}")
    print(f"  Profit Factor     : {m['profit_factor']}")
    print(f"  Sharpe (annual)   : {m['sharpe_annualized']}")
    print("-" * 70)
    
    if trades:
        print(f"\n  Recent Trades (last 10):")
        print(f"  {'#':<4} {'Side':<6} {'Entry':<10} {'SL':<10} {'TP':<10} {'Exit':<10} {'PnL':<10} {'Reason':<12}")
        print(f"  {'-'*72}")
        for idx, t in enumerate(trades[-10:]):
            pnl_str = f"{t['pnl']:.2f}" if t['pnl'] is not None else "OPEN"
            print(f"  {idx+1:<4} {t['side']:<6} {t['entry_price']:<10.2f} "
                  f"{t['sl']:<10.2f} {t['tp']:<10.2f} "
                  f"{t['exit_price'] if t['exit_price'] else 'N/A':<10} "
                  f"{pnl_str:<10} {t['exit_reason'] or 'OPEN':<12}")
    
    print("=" * 70)


# ── 5. Main ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtest 4H EMA50 C1/C2 strategy")
    parser.add_argument("--symbol", default="ETHUSD", help="Symbol to backtest (e.g. ETHUSD)")
    parser.add_argument("--start", default=None, help="Start month (e.g. 2025-01)")
    parser.add_argument("--end", default=None, help="End month (e.g. 2025-06)")
    parser.add_argument("--tick-data", action="store_true", default=True,
                        help="Use tick-level data for precise SL/TP simulation (default: True)")
    parser.add_argument("--no-tick", action="store_false", dest="tick_data",
                        help="Use candle OHLC approximation instead of tick data")
    args = parser.parse_args()
    
    print(f"Loading trade data for {args.symbol}...")
    trades = load_trades(args.symbol, args.start, args.end)
    print(f"  Loaded {len(trades):,} trades from {trades['timestamp'].min()} to {trades['timestamp'].max()}")
    
    print(f"Aggregating into {cfg.RESOLUTION} candles...")
    candles = aggregate_ohlcv(trades, cfg.RESOLUTION)
    print(f"  Generated {len(candles)} candles")
    
    print(f"Running signal engine & trade simulation...")
    tick_df = trades if args.tick_data else None
    result = simulate_trades(candles, tick_df)
    
    print_report(result, args.symbol)
    
    # Save detailed results to CSV
    output_dir = os.path.dirname(os.path.abspath(__file__))
    trades_csv = os.path.join(output_dir, f"backtest_{args.symbol}_trades.csv")
    equity_csv = os.path.join(output_dir, f"backtest_{args.symbol}_equity.csv")
    
    if result["trades"]:
        pd.DataFrame(result["trades"]).to_csv(trades_csv, index=False)
        print(f"\n  Trade log saved to: {trades_csv}")
    
    if result["equity_curve"]:
        pd.DataFrame(result["equity_curve"]).to_csv(equity_csv, index=False)
        print(f"  Equity curve saved to: {equity_csv}")


if __name__ == "__main__":
    main()
