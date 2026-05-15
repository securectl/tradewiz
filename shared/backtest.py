"""
Quick Pre-Trade Backtester — validates a signal against recent price history.

Before opening a trade, simulates what would have happened if the same signal
(strategy + direction + SL/TP) fired at each of the last N bars. If most
hypothetical trades would have lost money, the signal is flagged as weak.

This is a fast, lightweight check — not a full strategy backtest. It uses the
same DataFrame already fetched for signal generation, so no extra API calls.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)


def quick_backtest(df, side, strategy, stop_loss_pct, take_profit_pct, lookback=5, max_hold_bars=12):
    """Run a quick backtest of a signal over recent bars.

    Args:
        df: pandas DataFrame with OHLCV data (same used for signal generation)
        side: 'buy' or 'sell' (or 'long')
        strategy: strategy name (for logging)
        stop_loss_pct: stop loss as % distance from entry (e.g., 2.0 for 2%)
        take_profit_pct: take profit as % distance from entry (e.g., 5.0 for 5%)
        lookback: how many recent bars to simulate entries at (default 5)
        max_hold_bars: max bars to hold before forced exit (default 12)

    Returns:
        dict with:
            "pass": bool — True if backtest suggests signal is viable
            "win_rate": float — % of simulated trades that hit TP before SL
            "avg_pnl_pct": float — average P&L % across simulated trades
            "trades": int — number of simulated trades
            "detail": str — human-readable summary
    """
    result = {
        "pass": True,
        "win_rate": 0,
        "avg_pnl_pct": 0,
        "trades": 0,
        "detail": "No backtest data",
    }

    if df is None or df.empty or len(df) < lookback + max_hold_bars + 5:
        result["detail"] = "Insufficient data for backtest"
        return result

    try:
        closes = df["Close"].values.astype(float)
        highs = df["High"].values.astype(float)
        lows = df["Low"].values.astype(float)
        n = len(closes)

        is_long = side in ("buy", "long")

        wins = 0
        losses = 0
        pnls = []

        # Simulate entries at each of the last `lookback` completed bars
        # (excluding the very last bar which is the current signal bar)
        start_idx = max(0, n - lookback - 1 - max_hold_bars)
        end_idx = n - max_hold_bars - 1

        if end_idx <= start_idx:
            result["detail"] = "Not enough forward bars for simulation"
            return result

        for entry_idx in range(end_idx - lookback, end_idx):
            if entry_idx < 0 or entry_idx >= n:
                continue

            entry_price = closes[entry_idx]
            if entry_price <= 0:
                continue

            if is_long:
                sl_price = entry_price * (1 - stop_loss_pct / 100)
                tp_price = entry_price * (1 + take_profit_pct / 100)
            else:
                sl_price = entry_price * (1 + stop_loss_pct / 100)
                tp_price = entry_price * (1 - take_profit_pct / 100)

            # Walk forward bar by bar
            exit_price = None
            for j in range(1, min(max_hold_bars + 1, n - entry_idx)):
                bar_idx = entry_idx + j
                bar_high = highs[bar_idx]
                bar_low = lows[bar_idx]
                bar_close = closes[bar_idx]

                if is_long:
                    # Check SL first (conservative)
                    if bar_low <= sl_price:
                        exit_price = sl_price
                        break
                    if bar_high >= tp_price:
                        exit_price = tp_price
                        break
                else:
                    if bar_high >= sl_price:
                        exit_price = sl_price
                        break
                    if bar_low <= tp_price:
                        exit_price = tp_price
                        break

            # If no SL/TP hit, exit at last bar's close
            if exit_price is None:
                last_bar = min(entry_idx + max_hold_bars, n - 1)
                exit_price = closes[last_bar]

            # Calculate P&L
            if is_long:
                pnl_pct = (exit_price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - exit_price) / entry_price * 100

            pnls.append(pnl_pct)
            if pnl_pct > 0:
                wins += 1
            else:
                losses += 1

        total = wins + losses
        if total == 0:
            result["detail"] = "No simulated trades"
            return result

        win_rate = wins / total * 100
        avg_pnl = np.mean(pnls)

        # Decision: pass if win rate >= 30% OR avg P&L is positive
        # (30% WR with 3:1 R:R is profitable)
        passed = win_rate >= 30 or avg_pnl > 0

        result.update({
            "pass": passed,
            "win_rate": round(win_rate, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "trades": total,
            "detail": f"Backtest {strategy}: {wins}W/{losses}L ({win_rate:.0f}% WR), avg P&L {avg_pnl:+.2f}% over {total} simulated trades",
        })

        return result

    except Exception as e:
        logger.debug(f"Backtest failed: {e}")
        result["detail"] = f"Backtest error: {str(e)[:80]}"
        return result
