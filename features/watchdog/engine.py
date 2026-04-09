"""
Watchdog Trader Engine — Market regime scoring, swing signal generation, and sentiment blending.

Combines 3 axes into a composite regime:
  Axis 1: Market Structure (SPY/QQQ trends, VIX) — 45%
  Axis 2: Sentiment (Trump mood) — 25%
  Axis 3: Technical (RSI, MACD, MAs on SPY) — 30%

Regime: RISK-ON / NEUTRAL / RISK-OFF / DANGER

Signal generation uses 5 swing strategies (VCP, HTF, Breakout, Earnings Gap, Trend Pullback)
on a default watchlist of ETFs.
"""

import json
import logging
import time
import numpy as np
import yfinance as yf
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────
DEFAULT_WATCHLIST = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "GLD", "TLT"]
CACHE_TTL = 1800  # 30 minutes

_cache = {}

def _get_cached(key):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None

def _set_cached(key, data):
    _cache[key] = (data, time.time())


# ─── Axis 1: Market Structure Score (0-100) ──────────────────

def _score_market_structure():
    """Score market structure using SPY/QQQ price action and VIX."""
    score = 50  # Start neutral
    indicators = {}

    try:
        from market_sensor import check_market_health
        health = check_market_health("stock")
        ki = health.get("key_indicators", {})
        indicators["health_status"] = health.get("status", "UNKNOWN")
        indicators["spy_price"] = ki.get("spy_price", 0)
        indicators["spy_1d"] = ki.get("spy_1d_change", 0)
        indicators["spy_5d"] = ki.get("spy_5d_change", 0)
        indicators["vix"] = ki.get("vix", 20)
        indicators["vix_5d"] = ki.get("vix_5d_change", 0)
        indicators["qqq_price"] = ki.get("qqq_price", 0)
        indicators["qqq_1d"] = ki.get("qqq_1d_change", 0)
    except Exception as e:
        logger.warning(f"Market sensor failed: {e}")
        return 50, indicators

    # Fetch SPY daily for SMA50/SMA200
    try:
        spy_df = yf.download("SPY", period="1y", interval="1d", progress=False, timeout=15)
        if spy_df is not None and len(spy_df) >= 50:
            close = spy_df["Close"].values.flatten()
            spy_price = float(close[-1])
            sma50 = float(np.mean(close[-50:]))
            sma200 = float(np.mean(close[-200:])) if len(close) >= 200 else float(np.mean(close))
            indicators["spy_sma50"] = round(sma50, 2)
            indicators["spy_sma200"] = round(sma200, 2)

            if spy_price > sma50:
                score += 20
            if spy_price > sma200:
                score += 15
    except Exception as e:
        logger.warning(f"SPY yfinance failed: {e}")

    # SPY momentum
    if indicators.get("spy_1d", 0) > 0:
        score += 10
    if indicators.get("spy_5d", 0) > 0:
        score += 10

    # QQQ trend
    try:
        qqq_df = yf.download("QQQ", period="3mo", interval="1d", progress=False, timeout=15)
        if qqq_df is not None and len(qqq_df) >= 50:
            qqq_close = qqq_df["Close"].values.flatten()
            qqq_sma50 = float(np.mean(qqq_close[-50:]))
            if float(qqq_close[-1]) > qqq_sma50:
                score += 15
            indicators["qqq_sma50"] = round(qqq_sma50, 2)
    except Exception:
        pass

    # VIX scoring
    vix = indicators.get("vix", 20)
    if vix < 20:
        score += 20
    elif vix < 25:
        score += 10
    elif vix > 30:
        score -= 10

    # VIX declining = fear subsiding
    if indicators.get("vix_5d", 0) < 0:
        score += 10

    return max(0, min(100, score)), indicators


# ─── Axis 2: Sentiment Score (0-100) ─────────────────────────

def _score_sentiment():
    """Score sentiment from Trump mood data."""
    score = 50
    mood_data = {}

    try:
        from trump_mood import get_trump_mood
        trump = get_trump_mood()
        mood = trump.get("mood", 0)
        mood_data = {
            "mood": mood,
            "label": trump.get("label", "NEUTRAL"),
            "trend": trump.get("pattern", {}).get("trend", "stable"),
            "trade_signals": trump.get("trade_signals", {}),
        }

        # Map mood (-100 to +100) → score (0 to 100)
        score = max(0, min(100, (mood + 100) / 2))

        # Adjust by trend
        trend = trump.get("pattern", {}).get("trend", "stable")
        if trend == "improving":
            score = min(100, score + 10)
        elif trend == "deteriorating":
            score = max(0, score - 10)

    except Exception as e:
        logger.warning(f"Trump mood failed: {e}")

    return round(score, 1), mood_data


# ─── Axis 3: Technical Score (0-100) ─────────────────────────

def _score_technical(spy_df=None):
    """Score technical indicators on SPY daily."""
    score = 50

    try:
        if spy_df is None:
            spy_df = yf.download("SPY", period="1y", interval="1d", progress=False, timeout=15)
        if spy_df is None or len(spy_df) < 50:
            return 50, {}

        from analysis_engine import calculate_indicators
        ind = calculate_indicators(spy_df)

        rsi = ind.get("rsi_14", 50)
        macd_hist = ind.get("macd_histogram", 0)
        macd_cross = ind.get("macd_bullish_cross", False)
        sma20 = ind.get("sma_20", 0)
        sma50 = ind.get("sma_50", 0)
        bb_upper = ind.get("bb_upper", 0)
        current_price = float(spy_df["Close"].iloc[-1])

        tech_indicators = {
            "rsi": round(rsi, 1) if rsi else 50,
            "macd_histogram": round(macd_hist, 3) if macd_hist else 0,
            "macd_bullish_cross": bool(macd_cross),
            "sma20": round(sma20, 2) if sma20 else 0,
            "sma50": round(sma50, 2) if sma50 else 0,
        }

        # RSI scoring
        if rsi and rsi > 60:
            score += 20  # Bullish momentum
        elif rsi and 40 <= rsi <= 60:
            score += 15  # Neutral healthy
        elif rsi and rsi < 40:
            score += 5   # Oversold, cautious

        # MACD
        if macd_hist and macd_hist > 0:
            score += 20
        if macd_cross:
            score += 10

        # Price vs MAs
        if sma20 and current_price > sma20:
            score += 10
        if sma50 and current_price > sma50:
            score += 10

        # Overextended check
        if bb_upper and current_price > bb_upper:
            score -= 5

        return max(0, min(100, score)), tech_indicators

    except Exception as e:
        logger.warning(f"Technical scoring failed: {e}")
        return 50, {}


# ─── Composite Regime ────────────────────────────────────────

REGIME_WEIGHTS = {"market": 0.45, "sentiment": 0.25, "technical": 0.30}

REGIME_LABELS = {
    70: ("RISK-ON", "#00c896"),
    50: ("NEUTRAL", "#ffc837"),
    30: ("RISK-OFF", "#ff8c42"),
    0:  ("DANGER", "#ff4757"),
}

def get_regime():
    """Compute composite market regime from 3 axes.

    Returns dict with regime label, composite score, axis breakdowns,
    market indicators, and timestamp.
    """
    cached = _get_cached("regime")
    if cached:
        return {**cached, "cached": True}

    market_score, market_ind = _score_market_structure()
    sentiment_score, sentiment_data = _score_sentiment()
    technical_score, tech_ind = _score_technical()

    composite = (
        REGIME_WEIGHTS["market"] * market_score +
        REGIME_WEIGHTS["sentiment"] * sentiment_score +
        REGIME_WEIGHTS["technical"] * technical_score
    )

    # Override: PANIC from market_sensor forces DANGER
    if market_ind.get("health_status") == "PANIC":
        composite = min(composite, 20)

    # Determine regime label
    regime = "DANGER"
    color = "#ff4757"
    for threshold, (label, c) in sorted(REGIME_LABELS.items(), reverse=True):
        if composite >= threshold:
            regime = label
            color = c
            break

    # Trading allowed?
    trade_allowed = regime in ("RISK-ON", "NEUTRAL")
    trade_reason = ""
    if not trade_allowed:
        if regime == "DANGER":
            trade_reason = "Market in DANGER regime — all new positions blocked"
        else:
            trade_reason = "Market in RISK-OFF — only defensive/hedge positions recommended"

    result = {
        "regime": regime,
        "color": color,
        "composite_score": round(composite, 1),
        "axes": {
            "market": round(market_score, 1),
            "sentiment": round(sentiment_score, 1),
            "technical": round(technical_score, 1),
        },
        "trade_allowed": trade_allowed,
        "trade_reason": trade_reason,
        "market": market_ind,
        "sentiment": sentiment_data,
        "technical": tech_ind,
        "timestamp": datetime.now().isoformat(),
        "cached": False,
    }

    _set_cached("regime", result)
    return result


# ─── Swing Signal Generation ─────────────────────────────────

def _detect_swing_setup(ticker, df):
    """Detect swing trade setups using 5 strategies.
    Extracted from stock_engine.py _generate_swing_signal() to keep watchdog decoupled.

    Returns dict with signal details or None.
    """
    if df is None or len(df) < 50:
        return None

    try:
        current_price = float(df["Close"].iloc[-1])
        close = df["Close"].values.flatten()
        high = df["High"].values.flatten()
        low = df["Low"].values.flatten()
        volume = df["Volume"].values.flatten()
        n = len(df)

        sma_10 = float(np.mean(close[-10:])) if n >= 10 else 0
        sma_20 = float(np.mean(close[-20:])) if n >= 20 else 0
        sma_50 = float(np.mean(close[-50:])) if n >= 50 else 0
        sma_200 = float(np.mean(close[-200:])) if n >= 200 else float(np.mean(close))
        ema_20 = float(pd.Series(close).ewm(span=20).mean().iloc[-1]) if n >= 20 else 0
        avg_vol = float(np.mean(volume[-20:])) if n >= 20 else float(np.mean(volume))
        current_vol = float(volume[-1])
        rel_vol = current_vol / avg_vol if avg_vol > 0 else 0

        # RSI 14
        deltas = np.diff(close[-15:]) if n >= 15 else np.diff(close)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains) if len(gains) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0.001
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - (100 / (1 + rs))

        # ATR 14
        if n >= 15:
            tr = np.maximum(high[-14:] - low[-14:],
                           np.maximum(np.abs(high[-14:] - close[-15:-1]),
                                      np.abs(low[-14:] - close[-15:-1])))
            atr = float(np.mean(tr))
        else:
            atr = float(np.mean(high - low))

        high_20 = float(np.max(high[-20:])) if n >= 20 else float(np.max(high))

        indicators = {
            "price": round(current_price, 2),
            "rsi": round(rsi, 1),
            "sma50": round(sma_50, 2),
            "sma200": round(sma_200, 2),
            "ema20": round(ema_20, 2),
            "atr": round(atr, 2),
            "rel_volume": round(rel_vol, 2),
        }

        # --- 1. VCP (Volatility Contraction Pattern) ---
        if sma_10 > 0:
            dist_to_sma10 = abs(current_price - sma_10) / sma_10
            if dist_to_sma10 < 0.03 and n >= 20:
                recent_range = float(np.max(high[-5:]) - np.min(low[-5:]))
                prior_range = float(np.max(high[-15:-5]) - np.min(low[-15:-5]))
                recent_vol = float(np.mean(volume[-5:]))
                prior_vol = float(np.mean(volume[-15:-5]))
                if prior_range > 0 and recent_range < prior_range * 0.75 and recent_vol < prior_vol * 0.85:
                    return {
                        "ticker": ticker,
                        "signal": "BUY",
                        "confidence": 75,
                        "strategy": "VCP",
                        "reasoning": f"VCP: price within {dist_to_sma10:.1%} of SMA10, range contracting, volume declining",
                        "indicators": indicators,
                        "stop_loss": round(current_price - 2 * atr, 2),
                        "take_profit": round(current_price + 3 * 2 * atr, 2),
                    }

        # --- 2. HTF (High Tight Flag) ---
        if n >= 30:
            low_30 = float(np.min(low[-30:]))
            high_since_low = float(np.max(high[-30:]))
            run_pct = (high_since_low - low_30) / low_30 * 100 if low_30 > 0 else 0
            pullback_pct = (high_since_low - current_price) / high_since_low * 100 if high_since_low > 0 else 0
            if run_pct >= 30 and pullback_pct < 15 and pullback_pct > 1:
                return {
                    "ticker": ticker,
                    "signal": "BUY",
                    "confidence": 72,
                    "strategy": "HTF",
                    "reasoning": f"HTF: {run_pct:.0f}% run from 30d low, pullback {pullback_pct:.1f}% (tight consolidation)",
                    "indicators": indicators,
                    "stop_loss": round(current_price - 2 * atr, 2),
                    "take_profit": round(current_price + 3 * 2 * atr, 2),
                }

        # --- 3. Breakout ---
        if current_price > high_20 * 0.998 and rel_vol >= 1.5:
            return {
                "ticker": ticker,
                "signal": "BUY",
                "confidence": 70,
                "strategy": "Breakout",
                "reasoning": f"Breakout: price ${current_price:.2f} above 20d high ${high_20:.2f} with {rel_vol:.1f}x avg volume",
                "indicators": indicators,
                "stop_loss": round(current_price - 2 * atr, 2),
                "take_profit": round(current_price + 3 * 2 * atr, 2),
            }

        # --- 4. Earnings Gap ---
        for i in range(1, min(6, n)):
            prev_close = float(close[-(i+1)])
            day_open = float(df["Open"].values.flatten()[-i])
            day_vol = float(volume[-i])
            if prev_close > 0:
                gap_pct = (day_open - prev_close) / prev_close * 100
                if gap_pct >= 5 and day_vol > avg_vol * 2.0:
                    gap_level = day_open
                    if current_price >= gap_level * 0.97:
                        return {
                            "ticker": ticker,
                            "signal": "BUY",
                            "confidence": 68,
                            "strategy": "Earnings Gap",
                            "reasoning": f"Earnings Gap: {gap_pct:.1f}% gap up {i}d ago with {day_vol/avg_vol:.1f}x volume, holding above gap",
                            "indicators": indicators,
                            "stop_loss": round(gap_level * 0.95, 2),
                            "take_profit": round(current_price + 3 * 2 * atr, 2),
                        }

        # --- 5. Trend Pullback ---
        if ema_20 > 0 and sma_50 > sma_200:
            dist_to_ema20 = abs(current_price - ema_20) / ema_20
            ema_20_prev = float(pd.Series(close).ewm(span=20).mean().iloc[-6]) if n >= 25 else ema_20
            ema_rising = ema_20 > ema_20_prev
            if dist_to_ema20 < 0.02 and ema_rising and 40 <= rsi <= 60:
                return {
                    "ticker": ticker,
                    "signal": "BUY",
                    "confidence": 65,
                    "strategy": "Trend Pullback",
                    "reasoning": f"Trend Pullback: within {dist_to_ema20:.1%} of rising EMA20, SMA50 > SMA200, RSI={rsi:.1f}",
                    "indicators": indicators,
                    "stop_loss": round(ema_20 - atr, 2),
                    "take_profit": round(current_price + 3 * atr, 2),
                }

        # No setup found — return WATCH signal with indicators
        # Determine if bearish
        signal = "WATCH"
        reasoning = "No active swing setup — monitoring"
        confidence = 30

        if rsi > 70:
            signal = "SELL"
            reasoning = f"Overbought: RSI={rsi:.1f}, consider taking profits"
            confidence = 60
        elif current_price < sma_50 and current_price < sma_200:
            signal = "AVOID"
            reasoning = f"Below SMA50 (${sma_50:.2f}) and SMA200 (${sma_200:.2f}) — downtrend"
            confidence = 55

        return {
            "ticker": ticker,
            "signal": signal,
            "confidence": confidence,
            "strategy": "—",
            "reasoning": reasoning,
            "indicators": indicators,
            "stop_loss": None,
            "take_profit": None,
        }

    except Exception as e:
        logger.warning(f"Swing signal error for {ticker}: {e}")
        return {
            "ticker": ticker,
            "signal": "ERROR",
            "confidence": 0,
            "strategy": "—",
            "reasoning": f"Data error: {str(e)[:80]}",
            "indicators": {},
            "stop_loss": None,
            "take_profit": None,
        }


def get_signals(watchlist=None):
    """Generate swing signals for all watchlist tickers.

    Returns list of signal dicts with regime context.
    """
    cached = _get_cached("signals")
    if cached:
        return {"signals": cached, "cached": True}

    if watchlist is None:
        watchlist = DEFAULT_WATCHLIST

    regime = get_regime()
    signals = []

    for ticker in watchlist:
        try:
            df = yf.download(ticker, period="1y", interval="1d", progress=False, timeout=15)
            setup = _detect_swing_setup(ticker, df)
            if setup:
                # Adjust signal based on regime
                if regime["regime"] == "DANGER" and setup["signal"] == "BUY":
                    setup["signal"] = "HEDGE"
                    setup["reasoning"] = f"[DANGER regime override] {setup['reasoning']}"
                    setup["confidence"] = max(20, setup["confidence"] - 30)
                elif regime["regime"] == "RISK-OFF" and setup["signal"] == "BUY":
                    setup["confidence"] = max(30, setup["confidence"] - 15)
                    setup["reasoning"] = f"[RISK-OFF: reduced confidence] {setup['reasoning']}"

                signals.append(setup)
        except Exception as e:
            logger.warning(f"Signal generation failed for {ticker}: {e}")
            signals.append({
                "ticker": ticker, "signal": "ERROR", "confidence": 0,
                "strategy": "—", "reasoning": str(e)[:80],
                "indicators": {}, "stop_loss": None, "take_profit": None,
            })

    # Sort: BUY first, then by confidence
    order = {"BUY": 0, "HEDGE": 1, "SELL": 2, "WATCH": 3, "AVOID": 4, "ERROR": 5}
    signals.sort(key=lambda s: (order.get(s["signal"], 9), -s.get("confidence", 0)))

    _set_cached("signals", signals)
    return {
        "signals": signals,
        "regime": regime["regime"],
        "composite_score": regime["composite_score"],
        "timestamp": datetime.now().isoformat(),
        "cached": False,
    }


def get_sentiment():
    """Get combined sentiment view — Trump mood + market health."""
    cached = _get_cached("sentiment")
    if cached:
        return {**cached, "cached": True}

    try:
        from trump_mood import get_trump_mood
        trump = get_trump_mood()
    except Exception:
        trump = {"mood": 0, "label": "NO DATA", "trade_signals": {"buy": [], "avoid": []}}

    try:
        from market_sensor import check_market_health
        health = check_market_health("stock")
    except Exception:
        health = {"status": "UNKNOWN", "reasoning": "Market sensor unavailable"}

    mood = trump.get("mood", 0)
    health_status = health.get("status", "UNKNOWN")

    if mood >= 10 and health_status in ("HEALTHY",):
        combined = "BULLISH"
    elif mood >= 0 and health_status in ("HEALTHY", "CAUTION"):
        combined = "CAUTIOUSLY_BULLISH"
    elif mood <= -10 and health_status in ("DANGER", "PANIC"):
        combined = "BEARISH"
    elif mood <= 0 and health_status in ("CAUTION", "DANGER"):
        combined = "CAUTIOUSLY_BEARISH"
    else:
        combined = "MIXED"

    result = {
        "trump": {
            "mood": trump.get("mood", 0),
            "label": trump.get("label", "NEUTRAL"),
            "color": trump.get("color", "#ffc837"),
            "pattern": trump.get("pattern", {}),
            "trade_signals": trump.get("trade_signals", {}),
            "posts_analyzed": trump.get("posts_analyzed", 0),
        },
        "market_health": {
            "status": health_status,
            "reasoning": health.get("reasoning", ""),
        },
        "combined_sentiment": combined,
        "timestamp": datetime.now().isoformat(),
        "cached": False,
    }

    _set_cached("sentiment", result)
    return result


def get_health():
    """Return engine health status and data freshness."""
    freshness = {}

    for key, label in [("regime", "Market Regime"), ("signals", "Signal Board"), ("sentiment", "Sentiment")]:
        if key in _cache:
            _, ts = _cache[key]
            age = time.time() - ts
            freshness[key] = {
                "label": label,
                "age_seconds": round(age),
                "fresh": age < CACHE_TTL,
                "last_updated": datetime.fromtimestamp(ts).isoformat(),
            }
        else:
            freshness[key] = {"label": label, "age_seconds": None, "fresh": False, "last_updated": None}

    # Include auto-trader status if running
    trader_status = "stopped"
    if _auto_trader and _auto_trader.get("running"):
        trader_status = "running"

    return {
        "engine_status": "healthy",
        "auto_trader": trader_status,
        "cache_ttl": CACHE_TTL,
        "data_freshness": freshness,
        "watchlist": DEFAULT_WATCHLIST,
        "timestamp": datetime.now().isoformat(),
    }


# ─── Auto-Trader (Full Auto Execution) ──────────────────────
# Background thread that scans watchlist, generates signals, and executes
# trades via Alpaca broker. Supports paper and live modes.

import threading
from db import query, query_one, execute, IS_POSTGRES

_P = "%s" if IS_POSTGRES else "?"
_auto_trader = {}  # {user_id: ..., running: bool, thread: Thread}

# Config keys with defaults
WD_DEFAULTS = {
    "wd_enabled": "0",
    "wd_mode": "paper",           # "paper" or "live"
    "wd_scan_interval": "300",    # 5 min
    "wd_max_positions": "3",
    "wd_max_position_pct": "15",  # % of equity per position
    "wd_daily_loss_limit": "500",
    "wd_min_confidence": "65",    # min signal confidence to trade
    "wd_watchlist": json.dumps(DEFAULT_WATCHLIST),
    "wd_kill_switch": "0",
}


def _wd_config(user_id, key, default=None):
    """Read watchdog config from bot_config table."""
    try:
        row = query_one(
            f"SELECT value FROM bot_config WHERE user_id = {_P} AND key = {_P}",
            (user_id, key),
        )
        return row["value"] if row else (default or WD_DEFAULTS.get(key, ""))
    except Exception:
        return default or WD_DEFAULTS.get(key, "")


def _wd_log(user_id, level, msg):
    """Log watchdog event to bot_log table."""
    try:
        execute(
            f"INSERT INTO bot_log (user_id, level, message, source) VALUES ({_P},{_P},{_P},'watchdog')",
            (user_id, level, msg[:500]),
        )
    except Exception:
        pass
    getattr(logger, level, logger.info)(f"[WD:{user_id}] {msg}")


def _wd_daily_pnl(user_id):
    """Get today's realized PnL from watchdog trades (in bot_trades table)."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        row = query_one(
            f"SELECT COALESCE(SUM(pnl), 0) as total FROM bot_trades "
            f"WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'closed' AND DATE(closed_at) = {_P}",
            (user_id, today),
        )
        return float(row["total"]) if row else 0
    except Exception:
        return 0


def _wd_open_count(user_id):
    """Count open watchdog positions (in bot_trades table)."""
    try:
        row = query_one(
            f"SELECT COUNT(*) as cnt FROM bot_trades WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'open'",
            (user_id,),
        )
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0


def _wd_has_position(user_id, ticker):
    """Check if already holding this ticker (in bot_trades table)."""
    try:
        row = query_one(
            f"SELECT id FROM bot_trades WHERE user_id = {_P} AND coin = {_P} AND asset_type = 'watchdog' AND status = 'open'",
            (user_id, ticker),
        )
        return row is not None
    except Exception:
        return False


def _wd_risk_check(user_id, ticker, size_usd, balance):
    """Check all risk gates before opening a position."""
    # Kill switch
    if _wd_config(user_id, "wd_kill_switch") == "1":
        return {"allowed": False, "reason": "Kill switch active"}

    # Enabled check
    if _wd_config(user_id, "wd_enabled") != "1":
        return {"allowed": False, "reason": "Watchdog auto-trader not enabled"}

    # Daily loss limit
    daily_limit = float(_wd_config(user_id, "wd_daily_loss_limit", "500"))
    daily_pnl = _wd_daily_pnl(user_id)
    if daily_pnl <= -daily_limit:
        # Activate kill switch
        try:
            from shared.helpers import _upsert_bot_config
            _upsert_bot_config(user_id, "wd_kill_switch", "1")
        except Exception:
            pass
        return {"allowed": False, "reason": f"Daily loss limit breached (${daily_pnl:.2f})"}

    # Max positions
    max_pos = int(_wd_config(user_id, "wd_max_positions", "3"))
    if _wd_open_count(user_id) >= max_pos:
        return {"allowed": False, "reason": f"Max {max_pos} positions reached"}

    # Position size check
    max_pct = float(_wd_config(user_id, "wd_max_position_pct", "15"))
    max_size = balance * (max_pct / 100) if balance > 0 else 5000
    if size_usd > max_size:
        return {"allowed": False, "reason": f"Position ${size_usd:.0f} exceeds {max_pct}% limit (${max_size:.0f})"}

    # Duplicate check
    if _wd_has_position(user_id, ticker):
        return {"allowed": False, "reason": f"Already holding {ticker}"}

    return {"allowed": True, "reason": "All gates passed"}


def _wd_execute_trade(user_id, signal, broker_client, mode):
    """Execute a single trade — paper or live via Alpaca."""
    ticker = signal["ticker"]
    stop_loss = signal.get("stop_loss")
    take_profit = signal.get("take_profit")
    price = signal.get("indicators", {}).get("price", 0)
    strategy = signal.get("strategy", "")
    reasoning = signal.get("reasoning", "")

    if not price or price <= 0:
        return

    # Determine position size (default: $2000 or max_position_pct of equity)
    try:
        balance_info = broker_client.get_balance()
        equity = float(balance_info.get("equity", 0))
    except Exception:
        equity = 0

    max_pct = float(_wd_config(user_id, "wd_max_position_pct", "15"))
    size_usd = min(equity * (max_pct / 100), 5000) if equity > 0 else 2000
    shares = max(1, int(size_usd / price))

    # Risk check
    risk = _wd_risk_check(user_id, ticker, shares * price, equity)
    if not risk["allowed"]:
        _wd_log(user_id, "info", f"BLOCKED {ticker}: {risk['reason']}")
        return

    regime = get_regime().get("regime", "UNKNOWN")

    if mode == "live":
        # Execute via Alpaca
        try:
            order = broker_client.place_order(
                symbol=ticker, side="buy", qty=shares,
                stop_loss=stop_loss, take_profit=take_profit,
            )
            if not order.get("success"):
                _wd_log(user_id, "error", f"Order failed {ticker}: {order.get('error', '?')}")
                return

            order_id = order.get("order_id", "")
            _wd_log(user_id, "info", f"LIVE TRADE: BUY {shares} {ticker} @ ${price:.2f} | SL ${stop_loss} TP ${take_profit} | Order: {order_id}")
        except Exception as e:
            _wd_log(user_id, "error", f"Alpaca order error {ticker}: {e}")
            return
    else:
        order_id = f"paper_{int(time.time())}"
        _wd_log(user_id, "info", f"PAPER TRADE: BUY {shares} {ticker} @ ${price:.2f} | SL ${stop_loss} TP ${take_profit}")

    # Record in unified bot_trades table
    try:
        execute(
            f"""INSERT INTO bot_trades
                (user_id, coin, side, size, entry_price, stop_loss, take_profit,
                 status, strategy, asset_type, signal_reason, blofin_order_id,
                 regime_at_entry, direction_bias)
                VALUES ({_P},{_P},'buy',{_P},{_P},{_P},{_P},'open',{_P},'watchdog',{_P},{_P},{_P},'long')""",
            (user_id, ticker, shares, price,
             stop_loss, take_profit,
             strategy, reasoning,
             order_id, regime),
        )
    except Exception as e:
        _wd_log(user_id, "error", f"DB insert failed {ticker}: {e}")


def _wd_check_exits(user_id, broker_client, mode):
    """Check open watchdog positions in bot_trades for stop-loss, take-profit, or time-based exits."""
    try:
        open_trades = query(
            f"SELECT * FROM bot_trades WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'open'",
            (user_id,),
        )
    except Exception:
        return

    for trade in open_trades:
        try:
            ticker = trade["coin"]
            entry_price = trade["entry_price"]
            sl = trade.get("stop_loss")
            tp = trade.get("take_profit")

            # Get current price
            current_price = yf.Ticker(ticker).info.get("regularMarketPrice")
            if not current_price:
                continue

            exit_reason = None
            if sl and current_price <= sl:
                exit_reason = f"Stop-loss hit (${sl:.2f})"
            elif tp and current_price >= tp:
                exit_reason = f"Take-profit hit (${tp:.2f})"
            else:
                # Time exit: 14 days max for swing
                opened = trade.get("opened_at", "")
                try:
                    if opened:
                        from datetime import timedelta
                        opened_dt = datetime.fromisoformat(str(opened).replace("Z", ""))
                        if (datetime.now() - opened_dt).days >= 14:
                            exit_reason = "Time exit: 14-day max hold"
                except Exception:
                    pass

            if not exit_reason:
                continue

            # Execute exit
            if mode == "live":
                try:
                    broker_client.close_position(ticker)
                except Exception as e:
                    _wd_log(user_id, "error", f"Close position failed {ticker}: {e}")
                    continue

            # Calculate PnL
            multiplier = 1 if trade.get("side", "long") in ("long", "buy") else -1
            pnl = (current_price - entry_price) * trade["size"] * multiplier
            pnl_pct = (current_price - entry_price) / entry_price * 100 * multiplier
            fee = round(trade["size"] * 0.01, 4)

            execute(
                f"""UPDATE bot_trades
                    SET exit_price = {_P}, pnl = {_P}, pnl_pct = {_P}, fee = {_P},
                        status = 'closed',
                        closed_at = {'NOW()' if IS_POSTGRES else "datetime('now')"}
                    WHERE id = {_P}""",
                (current_price, round(pnl - fee, 2), round(pnl_pct, 2), fee, trade["id"]),
            )

            _wd_log(user_id, "info",
                f"CLOSED {ticker}: {exit_reason} | PnL ${pnl:.2f} ({pnl_pct:+.1f}%) | Mode: {mode}")

        except Exception as e:
            _wd_log(user_id, "warning", f"Exit check error {trade.get('ticker', '?')}: {e}")


def _wd_scan_loop(user_id):
    """Main auto-trading scan loop. Runs in background thread."""
    _wd_log(user_id, "info", "Auto-trader started")

    while _auto_trader.get("running"):
        try:
            mode = _wd_config(user_id, "wd_mode", "paper")
            interval = int(_wd_config(user_id, "wd_scan_interval", "300"))
            min_conf = int(_wd_config(user_id, "wd_min_confidence", "65"))

            # Check if still enabled
            if _wd_config(user_id, "wd_enabled") != "1":
                _wd_log(user_id, "info", "Auto-trader disabled via config — stopping")
                break

            if _wd_config(user_id, "wd_kill_switch") == "1":
                _wd_log(user_id, "warning", "Kill switch active — skipping cycle")
                time.sleep(60)
                continue

            # Initialize broker
            broker_client = None
            if mode == "live":
                try:
                    from stock_bot.broker_client import AlpacaClient
                    from shared.helpers import _uid
                    # Load user's Alpaca keys
                    keys_row = query_one(
                        f"SELECT key_name, encrypted_value FROM api_keys WHERE user_id = {_P} AND provider = 'alpaca'",
                        (user_id,),
                    )
                    if keys_row:
                        import os
                        from cryptography.fernet import Fernet
                        fernet = Fernet(os.getenv("ENCRYPTION_KEY", "").encode())
                        api_key = fernet.decrypt(keys_row["encrypted_value"].encode()).decode()
                        # Get secret key
                        secret_row = query_one(
                            f"SELECT encrypted_value FROM api_keys WHERE user_id = {_P} AND provider = 'alpaca' AND key_name = 'secret_key'",
                            (user_id,),
                        )
                        secret_key = fernet.decrypt(secret_row["encrypted_value"].encode()).decode() if secret_row else None
                        broker_client = AlpacaClient(api_key=api_key, secret_key=secret_key, paper=False)
                    else:
                        _wd_log(user_id, "error", "No Alpaca API keys — falling back to paper mode")
                        mode = "paper"
                except Exception as e:
                    _wd_log(user_id, "error", f"Broker init failed: {e} — falling back to paper")
                    mode = "paper"

            if mode == "paper":
                try:
                    from stock_bot.broker_client import AlpacaClient
                    broker_client = AlpacaClient(paper=True)
                except Exception:
                    broker_client = None

            # 1. Check exits on open positions
            if broker_client:
                _wd_check_exits(user_id, broker_client, mode)

            # 2. Get regime
            regime = get_regime()
            if regime["regime"] == "DANGER":
                _wd_log(user_id, "warning", f"DANGER regime (composite: {regime['composite_score']}) — no new trades")
                time.sleep(interval)
                continue

            # 3. Get signals
            watchlist_str = _wd_config(user_id, "wd_watchlist", json.dumps(DEFAULT_WATCHLIST))
            try:
                watchlist = json.loads(watchlist_str)
            except Exception:
                watchlist = DEFAULT_WATCHLIST

            # Clear signal cache so we get fresh data
            _cache.pop("signals", None)
            signals_data = get_signals(watchlist)

            # 4. Execute BUY signals that meet confidence threshold
            buy_signals = [s for s in signals_data.get("signals", [])
                          if s.get("signal") == "BUY" and s.get("confidence", 0) >= min_conf]

            for sig in buy_signals[:2]:  # Max 2 new trades per cycle
                _wd_execute_trade(user_id, sig, broker_client, mode)

            _wd_log(user_id, "info",
                f"Scan complete: regime={regime['regime']} | signals={len(buy_signals)} BUY | "
                f"open={_wd_open_count(user_id)} positions | mode={mode}")

        except Exception as e:
            _wd_log(user_id, "error", f"Scan cycle error: {e}")

        # Sleep for scan interval
        interval = int(_wd_config(user_id, "wd_scan_interval", "300"))
        for _ in range(interval):
            if not _auto_trader.get("running"):
                break
            time.sleep(1)

    _wd_log(user_id, "info", "Auto-trader stopped")
    _auto_trader["running"] = False


def start_auto_trader(user_id):
    """Start the watchdog auto-trading loop in a background thread."""
    global _auto_trader

    if _auto_trader.get("running"):
        return {"ok": False, "error": "Auto-trader already running"}

    _auto_trader = {"user_id": user_id, "running": True}
    t = threading.Thread(target=_wd_scan_loop, args=(user_id,), daemon=True, name="watchdog-trader")
    _auto_trader["thread"] = t
    t.start()
    return {"ok": True, "message": f"Auto-trader started (mode: {_wd_config(user_id, 'wd_mode', 'paper')})"}


def stop_auto_trader():
    """Stop the watchdog auto-trading loop."""
    global _auto_trader

    if not _auto_trader.get("running"):
        return {"ok": False, "error": "Auto-trader not running"}

    _auto_trader["running"] = False
    return {"ok": True, "message": "Auto-trader stopping..."}


def get_auto_trader_status(user_id):
    """Get current auto-trader status."""
    mode = _wd_config(user_id, "wd_mode", "paper")
    return {
        "running": _auto_trader.get("running", False),
        "mode": mode,
        "kill_switch": _wd_config(user_id, "wd_kill_switch") == "1",
        "enabled": _wd_config(user_id, "wd_enabled") == "1",
        "config": {
            "scan_interval": int(_wd_config(user_id, "wd_scan_interval", "300")),
            "max_positions": int(_wd_config(user_id, "wd_max_positions", "3")),
            "daily_loss_limit": float(_wd_config(user_id, "wd_daily_loss_limit", "500")),
            "min_confidence": int(_wd_config(user_id, "wd_min_confidence", "65")),
            "max_position_pct": float(_wd_config(user_id, "wd_max_position_pct", "15")),
        },
    }
