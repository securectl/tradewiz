"""Strategy adapters for the backtest engine.

Each strategy is exposed as a callable matching the backtest detector signature:
    fn(close, high, low, volume, n) -> setup dict | None

Where setup must contain at minimum:
    entry_price (float), stop_loss (float), take_profit (float)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ─── Stage 2 Recovery Breakout ──────────────────────────────────────────

def stage2_breakout(close, high, low, volume, n):
    """Adapter for analysis_engine._detect_stage2_breakout.

    Position-trade rules: 8% stop, +20% first target, will be trailed by engine.
    """
    from analysis_engine import _detect_stage2_breakout
    setup = _detect_stage2_breakout(close, high, low, volume, n)
    if not setup:
        return None
    # The engine wants explicit stop/target — override the detector's tight stop
    # with position-trade scale (8% / +25%)
    entry = float(close[-1])
    return {
        "entry_price": entry,
        "stop_loss": round(entry * 0.92, 2),
        "take_profit": round(entry * 1.25, 2),
        "phase": setup.get("phase"),
    }


def stage2_breakout_only_real_breakouts(close, high, low, volume, n):
    """Stage 2 but only the 'breakout' phase (price already above base resistance).

    Filters out 'basing' and 'loaded' phases — only takes confirmed breakouts.
    Higher win rate, fewer trades.
    """
    from analysis_engine import _detect_stage2_breakout
    setup = _detect_stage2_breakout(close, high, low, volume, n)
    if not setup or setup.get("phase") != "breakout":
        return None
    entry = float(close[-1])
    return {
        "entry_price": entry,
        "stop_loss": round(entry * 0.92, 2),
        "take_profit": round(entry * 1.30, 2),
    }


# ─── Multi-TF RSI confluence buy ────────────────────────────────────────

def _simple_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains)) if len(gains) else 0
    avg_loss = float(np.mean(losses)) if len(losses) else 0.001
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def rsi_composite_oversold(close, high, low, volume, n):
    """Multi-TF RSI confluence: composite (D+W+M)/3 < 35, daily < 40,
    structure intact (price within 8% of SMA20).

    This is what claude_bot now uses for entries — encoded for the backtester.
    """
    if n < 200:
        return None
    closes_s = pd.Series(close)
    rsi_d = _simple_rsi(close, 14)
    if rsi_d is None or rsi_d >= 40:
        return None

    # Weekly via resample of last 60 weeks (~280 daily bars)
    if n >= 280:
        # Use last 280 days, group every 5 (Mon-Fri) closes
        ws = close[-280:][::-1][::5][::-1]
        rsi_w = _simple_rsi(ws, 14)
    else:
        rsi_w = None

    # Monthly via every-21-day sample
    if n >= 21 * 16:
        ms = close[-(21 * 16):][::-1][::21][::-1]
        rsi_m = _simple_rsi(ms, 14)
    else:
        rsi_m = None

    present = [v for v in (rsi_d, rsi_w, rsi_m) if v is not None]
    if not present:
        return None
    composite = sum(present) / len(present)
    if composite >= 35:
        return None

    # Structure: price within 8% of SMA20
    sma20 = float(np.mean(close[-20:]))
    price = float(close[-1])
    dist = (price - sma20) / sma20 if sma20 > 0 else -1
    if dist < -0.08:
        return None  # falling knife

    # 8% stop, +12% target — mean-reversion bounce, not a breakout
    return {
        "entry_price": price,
        "stop_loss": round(price * 0.92, 2),
        "take_profit": round(price * 1.12, 2),
    }


# ─── Registry ───────────────────────────────────────────────────────────

STRATEGIES = {
    "stage2": stage2_breakout,
    "stage2_breakout_only": stage2_breakout_only_real_breakouts,
    "rsi_composite": rsi_composite_oversold,
}


def get_strategy(name: str):
    fn = STRATEGIES.get(name)
    if not fn:
        raise ValueError(f"unknown strategy: {name}. Choose one of: {list(STRATEGIES.keys())}")
    return fn
