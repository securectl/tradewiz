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


# ─── MACD trend cross ───────────────────────────────────────────────────

def _ema(arr, span):
    return pd.Series(arr, dtype=float).ewm(span=span, adjust=False).mean().values


def macd_trend_cross(close, high, low, volume, n):
    """MACD (12/26/9) bullish crossover taken only *with the trend* — price
    above its 50-day MA. Momentum entry: 7% stop, +15% target."""
    if n < 60:
        return None
    macd = _ema(close, 12) - _ema(close, 26)
    signal = _ema(macd, 9)
    # Bullish cross on the latest bar (was at/below signal, now above)
    if not (macd[-2] <= signal[-2] and macd[-1] > signal[-1]):
        return None
    sma50 = float(np.mean(close[-50:]))
    price = float(close[-1])
    if price < sma50:      # trend filter — no counter-trend crosses
        return None
    return {
        "entry_price": price,
        "stop_loss": round(price * 0.93, 2),
        "take_profit": round(price * 1.15, 2),
    }


# ─── Trend pullback to the 20-EMA ───────────────────────────────────────

def ema_pullback_bounce(close, high, low, volume, n):
    """Buy the dip in an established uptrend: 50-MA above 200-MA, price pulled
    back to the 20-EMA (dipped into it last bar) and closing back up. A
    continuation entry, not a bottom-fish: 6% stop, +12% target."""
    if n < 200:
        return None
    sma50 = float(np.mean(close[-50:]))
    sma200 = float(np.mean(close[-200:]))
    if sma50 <= sma200:                 # need an uptrend
        return None
    ema20 = _ema(close, 20)
    price = float(close[-1])
    dist = (price - ema20[-1]) / ema20[-1] if ema20[-1] else -1
    if not (-0.02 <= dist <= 0.03):     # near the 20-EMA, not extended/broken
        return None
    # Prior bar dipped to/through the EMA and today closes green
    if not (close[-1] > close[-2] and low[-2] <= ema20[-2] * 1.01):
        return None
    return {
        "entry_price": price,
        "stop_loss": round(price * 0.94, 2),
        "take_profit": round(price * 1.12, 2),
    }


# ─── "Top 5 that actually work" (liquidityfinder.com ranking) ───────────
# Concepts from the ranked article: Smart-Money liquidity sweeps, price action
# at key levels, breakout & retest, Fibonacci retracement, indicator momentum.
# The article gives concepts, not exact stops/targets; the mechanical rules
# below are our disclosed implementation. SMC/price-action here are simplified
# proxies of the discretionary originals (the liquidity-sweep-and-reclaim and
# rejection-wick cores), since order blocks / fair-value-gaps aren't mechanical.

def _stoch_k(close, high, low, period=14):
    if len(close) < period:
        return None
    hh = float(np.max(high[-period:]))
    ll = float(np.min(low[-period:]))
    if hh - ll < 1e-9:
        return 50.0
    return (float(close[-1]) - ll) / (hh - ll) * 100.0


def smc_liquidity_sweep(close, high, low, volume, n):
    """SMC liquidity sweep: price dips below a recent swing low (grabs sell-side
    liquidity / stop hunt) then reclaims it — a failed breakdown that traps
    sellers. Stop below the sweep wick, target 2R."""
    if n < 60:
        return None
    prior_low = float(np.min(low[-20:-1]))          # swing low before today
    price = float(close[-1])
    if not (float(low[-1]) < prior_low and price > prior_low):   # swept + reclaimed
        return None
    if price < float(np.mean(close[-50:])) * 0.90:  # avoid deep-downtrend knives
        return None
    stop = round(float(low[-1]) * 0.985, 2)
    risk = price - stop
    if risk <= 0:
        return None
    return {"entry_price": price, "stop_loss": stop,
            "take_profit": round(price + 2 * risk, 2)}


def price_action_key_level(close, high, low, volume, n):
    """Price action at a key level: price tests the 20-day support and prints a
    rejection candle (long lower wick, close in the upper 40% of the range).
    Stop below the wick, target the recent range high (cap at 3R)."""
    if n < 40:
        return None
    support = float(np.min(low[-20:-1]))
    price = float(close[-1])
    hi = float(high[-1]); lo = float(low[-1])
    rng = hi - lo
    if rng < 1e-9:
        return None
    if not (lo <= support * 1.015 and (price - lo) / rng >= 0.6):  # tested + rejected
        return None
    resistance = float(np.max(high[-20:]))
    stop = round(lo * 0.985, 2)
    if price <= stop or resistance <= price:
        return None
    return {"entry_price": price, "stop_loss": stop,
            "take_profit": round(min(resistance, price + 3 * (price - stop)), 2)}


def breakout_retest(close, high, low, volume, n):
    """Breakout & retest: price broke above a prior range high in the last few
    bars, pulled back to retest that level, and is holding above it. Enter the
    hold, stop below the level, target 2R."""
    if n < 60:
        return None
    resistance = float(np.max(high[-45:-5]))         # the level that broke
    price = float(close[-1])
    broke = float(np.max(close[-5:])) > resistance
    retested = float(low[-1]) <= resistance * 1.01
    if not (broke and retested and price > resistance):
        return None
    stop = round(resistance * 0.97, 2)
    risk = price - stop
    if risk <= 0:
        return None
    return {"entry_price": price, "stop_loss": stop,
            "take_profit": round(price + 2 * risk, 2)}


def fibonacci_retracement(close, high, low, volume, n):
    """Fibonacci retracement continuation: in an uptrend, price pulls back into
    the 50–61.8% retrace of the recent swing and bounces. Stop below the 78.6%
    level, target the swing high."""
    if n < 60:
        return None
    sma50 = float(np.mean(close[-50:]))
    sma200 = float(np.mean(close[-200:])) if n >= 200 else sma50
    if sma50 <= sma200:                              # uptrend only
        return None
    swing_high = float(np.max(high[-40:]))
    swing_low = float(np.min(low[-40:]))
    rng = swing_high - swing_low
    if rng < 1e-9:
        return None
    lvl_50 = swing_high - 0.5 * rng
    lvl_618 = swing_high - 0.618 * rng
    lvl_786 = swing_high - 0.786 * rng
    price = float(close[-1]); lo = float(low[-1])
    if not (lvl_786 <= lo <= lvl_50 and price > lvl_618):  # dipped into zone + bounced
        return None
    stop = round(lvl_786 * 0.99, 2)
    if price <= stop or swing_high <= price:
        return None
    return {"entry_price": price, "stop_loss": stop,
            "take_profit": round(swing_high, 2)}


def momentum_rsi_stoch(close, high, low, volume, n):
    """Indicator momentum: RSI crossing up through 50, a bullish stochastic
    (rising, not yet overbought), and price above the 20-day MA. 6% stop, +12%."""
    if n < 60:
        return None
    rsi_now = _simple_rsi(close, 14)
    rsi_prev = _simple_rsi(close[:-1], 14)
    k_now = _stoch_k(close, high, low, 14)
    k_prev = _stoch_k(close[:-1], high[:-1], low[:-1], 14)
    if None in (rsi_now, rsi_prev, k_now, k_prev):
        return None
    price = float(close[-1])
    sma20 = float(np.mean(close[-20:]))
    if not (rsi_prev < 50 <= rsi_now and k_now > k_prev and k_now < 80 and price > sma20):
        return None
    return {"entry_price": price, "stop_loss": round(price * 0.94, 2),
            "take_profit": round(price * 1.12, 2)}


# ─── Registry ───────────────────────────────────────────────────────────

STRATEGIES = {
    # Original engine strategies (kept for the universe backtester + tests)
    "stage2": stage2_breakout,
    "stage2_breakout_only": stage2_breakout_only_real_breakouts,
    "rsi_composite": rsi_composite_oversold,
    "macd_trend_cross": macd_trend_cross,
    "ema_pullback": ema_pullback_bounce,
    # Article "top 5 that actually work"
    "smc_liquidity_sweep": smc_liquidity_sweep,
    "price_action_key_level": price_action_key_level,
    "breakout_retest": breakout_retest,
    "fibonacci_retracement": fibonacci_retracement,
    "momentum_rsi_stoch": momentum_rsi_stoch,
}

# Curated "top strategies" surfaced by the analyzer Backtest button — the five
# ranked in liquidityfinder.com's "top 5 trading strategies that actually work".
TOP_STRATEGIES = [
    {"key": "smc_liquidity_sweep", "label": "Smart Money (Liquidity Sweep)",
     "desc": "SMC: price sweeps a swing low to grab liquidity, then reclaims it — a trapped-seller reversal."},
    {"key": "price_action_key_level", "label": "Price Action at Key Level",
     "desc": "Rejection candle (long lower wick) off the 20-day support / key level."},
    {"key": "breakout_retest", "label": "Breakout & Retest",
     "desc": "Break of a prior range high, pull back to retest the level, enter the hold."},
    {"key": "fibonacci_retracement", "label": "Fibonacci Retracement",
     "desc": "Uptrend pullback into the 50–61.8% fib zone that bounces; target the swing high."},
    {"key": "momentum_rsi_stoch", "label": "Indicator Momentum (RSI+Stoch)",
     "desc": "RSI crossing up through 50 with a bullish stochastic and price above the 20-day MA."},
]


def get_strategy(name: str):
    fn = STRATEGIES.get(name)
    if not fn:
        raise ValueError(f"unknown strategy: {name}. Choose one of: {list(STRATEGIES.keys())}")
    return fn
