"""
Watchdog Trader Engine — Aggressive day trader for low/mid-cap stocks with
large-cap-breakout overlay. Volume + RSI base, LLM gates risk/reward.

Combines 3 axes into a composite regime:
  Axis 1: Market Structure (SPY/QQQ trends, VIX) — 45%
  Axis 2: Sentiment (Trump mood) — 25%
  Axis 3: Technical (RSI, MACD, MAs on SPY) — 30%

Regime: RISK-ON / NEUTRAL / RISK-OFF / DANGER

Signal generation uses 5 swing strategies (VCP, HTF, Breakout, Earnings Gap, RSI
Reversal). Default universe is low+mid-cap stocks; large-caps are admitted only
when qullamaggie_scan flags a major breakout (score ≥ 7).
"""

import json
import logging
import time
import numpy as np
import yfinance as yf
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)


def _default_watchlist():
    """Low+mid-cap default universe. Resolved lazily so a missing screener
    import doesn't break module load (tests, partial deploys)."""
    try:
        from screener import LOWCAP_TICKERS, MIDCAP_TICKERS
        return list(dict.fromkeys(LOWCAP_TICKERS + MIDCAP_TICKERS))
    except Exception:
        return ["AMD", "PLTR", "SOFI", "RIVN", "LCID", "F", "GM", "BAC", "WFC", "T"]


# ─── Config ──────────────────────────────────────────────────
DEFAULT_WATCHLIST = _default_watchlist()
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


# ─── Large-cap breakout overlay ──────────────────────────────

# Score floor for "major breakout" — Qullamaggie scoring is 1-10. 6+ filters
# to setups with MA alignment, RS in top quartile, and volume confirmation.
# Lowered from 7 to 6 (May 2026) — score=7 was too strict, missing daily
# breakouts like ORCL/COHR-type names that ran with MA stack + volume but
# fell short of the perfect-setup threshold.
_LARGECAP_BREAKOUT_MIN_SCORE = 6
_LARGECAP_BREAKOUT_MAX = 8  # cap how many we promote per cycle (was 5)
_LARGECAP_CACHE_KEY = "watchdog:largecap_breakouts"
_LARGECAP_CACHE_TTL = 3600  # 1 hour — qullamaggie_scan is expensive


def _get_largecap_breakouts():
    """Return a list of large-cap tickers currently in major breakout setups
    per qullamaggie_scan. Cached 1h to avoid hammering yfinance. Empty list
    on any failure — never raises."""
    cached = _cache.get(_LARGECAP_CACHE_KEY)
    if cached and time.time() - cached[1] < _LARGECAP_CACHE_TTL:
        return cached[0]
    try:
        from screener import LARGECAP_TICKERS
        from analysis_engine import qullamaggie_scan
        results = qullamaggie_scan(LARGECAP_TICKERS) or []
        out = [
            r["ticker"] for r in results
            if r.get("ticker") and (r.get("score") or 0) >= _LARGECAP_BREAKOUT_MIN_SCORE
        ][:_LARGECAP_BREAKOUT_MAX]
        _cache[_LARGECAP_CACHE_KEY] = (out, time.time())
        return out
    except Exception as e:
        logger.warning(f"largecap breakout scan failed: {e}")
        _cache[_LARGECAP_CACHE_KEY] = ([], time.time())
        return []


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

def _detect_swing_setup(ticker, df, entry_style=None):
    """Detect swing trade setups using 5 strategies.
    Extracted from stock_engine.py _generate_swing_signal() to keep watchdog decoupled.

    entry_style hints which setup to prefer:
      "reversal" — try RSI<30 mean-reversion first (for oversold/bottom-forming sources)
      "momentum" / None — existing momentum strategies (VCP, HTF, Breakout, etc.)

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

        # --- 0. RSI Reversal (mean-reversion bounce) ---
        # Active for "reversal" sources (oversold screener / bottom-forming).
        # Falling-knife filter: price must be within 6% of EMA20 and not in freefall vs SMA50.
        if entry_style == "reversal" and rsi < 30 and ema_20 > 0 and sma_50 > 0:
            dist_to_ema20 = (current_price - ema_20) / ema_20
            dist_to_sma50 = (current_price - sma_50) / sma_50
            if dist_to_ema20 > -0.06 and dist_to_sma50 > -0.12:
                return {
                    "ticker": ticker,
                    "signal": "BUY",
                    "confidence": 72,
                    "strategy": "RSI Reversal",
                    "reasoning": f"RSI={rsi:.1f} oversold + within {abs(dist_to_ema20):.1%} of EMA20 — bounce setup",
                    "indicators": indicators,
                    "stop_loss": round(current_price - 1.5 * atr, 2),
                    "take_profit": round(current_price * 1.05, 2),  # +5% — middle of 4-6% band
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


def _get_persistent_lowmidcap_picks(min_days=2, days_window=7):
    """Find low/mid-cap tickers that appeared in 2+ daily screener scans.

    Per user spec (Apr 2026): "if they show up 2-3 times daily tag as opp
    and then trade those in watchdog". Persistence over time is the signal,
    not today's snapshot verdict — small-caps often look RISKY on any given
    day. Excludes obvious knives.

    Returns list of dicts shaped like _get_screener_candidates entries so
    they can be merged in directly.
    """
    # Local import — module's top-level imports `query` further down
    # (line ~1038) so we can't rely on it at module load time.
    from db import query as _q, IS_POSTGRES as _PG
    query = _q
    IS_POSTGRES = _PG
    try:
        if IS_POSTGRES:
            rows = query(
                "SELECT ticker, category, COUNT(DISTINCT scan_date) AS days, "
                "MAX(scan_date) AS last_seen, "
                "ROUND(AVG(confidence)::numeric, 0) AS avg_confidence, "
                "MAX(verdict) AS latest_verdict, MAX(summary) AS summary "
                "FROM screener_results "
                "WHERE category IN ('lowcap', 'midcap') "
                "AND scan_date >= (CURRENT_DATE - INTERVAL '7 days')::text "
                f"GROUP BY ticker, category HAVING COUNT(DISTINCT scan_date) >= {min_days} "
                "ORDER BY days DESC, avg_confidence DESC LIMIT 25"
            )
        else:
            rows = query(
                "SELECT ticker, category, COUNT(DISTINCT scan_date) AS days, "
                "MAX(scan_date) AS last_seen, "
                "ROUND(AVG(confidence), 0) AS avg_confidence, "
                "MAX(verdict) AS latest_verdict, MAX(summary) AS summary "
                "FROM screener_results "
                "WHERE category IN ('lowcap', 'midcap') "
                "AND scan_date >= date('now', '-7 days') "
                f"GROUP BY ticker, category HAVING COUNT(DISTINCT scan_date) >= {min_days} "
                "ORDER BY days DESC, avg_confidence DESC LIMIT 25"
            )
    except Exception as e:
        logger.warning(f"Persistent low/mid-cap query failed: {e}")
        return []

    out = []
    for r in rows or []:
        verdict = (r.get("latest_verdict") or "").upper()
        # Skip explicit knives — persistence is bullish, but a "FALLING KNIFE"
        # verdict means today's data still looks like a downtrend.
        if "FALLING KNIFE" in verdict or verdict in ("AVOID",):
            continue
        days = int(r.get("days") or 0)
        avg_conf = float(r.get("avg_confidence") or 0)
        # Confidence boost scales with persistence: 2d=+5, 3d=+10, 4+=+15
        boost = min(15, (days - 1) * 5) if days >= 2 else 0
        out.append({
            "ticker": r["ticker"],
            "screener_confidence": min(95, int(avg_conf + boost)),
            "screener_verdict": f"PERSISTENT-{days}D",
            "screener_summary": (r.get("summary") or "")[:200],
            "category": r["category"],
            "entry_style": "momentum",  # default; signal logic still vets
            "days_tracked": days,
            "first_seen": None,
            "price_trend": None,
            "screener_status": "persistent",
        })
    return out


def _get_screener_candidates():
    """Pull high-confidence candidates from the screener to expand watchdog's ticker universe.

    Fetches today's cached screener results from multiple categories and returns
    tickers with confidence >= 70. These are AI-vetted opportunities that the
    watchdog can then apply swing signal analysis to.
    """
    cached = _get_cached("screener_candidates")
    if cached:
        return cached

    candidates = []
    try:
        from screener import run_screener
        # Each category is tagged with the entry style it implies:
        #   momentum = ride strength (RSI 50-70 sweet spot)
        #   reversal = oversold mean-reversion bounce (RSI<30 + structure)
        # NOTE (Apr 2026): largecap removed from this list — large-caps now
        # only qualify via _get_largecap_breakouts() (qullamaggie score ≥ 7).
        # lowcap added so the watchdog actually trades the small-caps the
        # screener identifies, per the user's "low/mid-cap day-trader" spec.
        category_styles = [
            ("gainers", "momentum"),
            ("ai", "momentum"),
            ("lowcap", "momentum"),
            ("midcap", "momentum"),
            ("oversold", "reversal"),
        ]
        # Categories that REQUIRE persistence (≥2 daily appearances) to be
        # admitted, per the user's "show up 2-3 times daily → tag as opp"
        # rule. First-time picks can still pass via a higher conf bar.
        persistence_required = {"lowcap", "midcap"}
        for cat, entry_style in category_styles:
            try:
                result = run_screener(category=cat, limit=10, timeframe="1d")
                # For oversold, also include items still in "tracking" status that have
                # appeared 3+ days running — persistent oversold = real bottom forming,
                # not an intraday dip. Confidence threshold is relaxed for these.
                pool = list(result.get("opportunities", []))
                if cat == "oversold":
                    for t in result.get("tracking", []):
                        if t.get("days_tracked", 1) >= 3 and t.get("verdict") != "FALLING KNIFE":
                            pool.append(t)

                for opp in pool:
                    ticker = opp.get("ticker", "")
                    conf = opp.get("confidence", 0)
                    days = int(opp.get("days_tracked", 1) or 1)
                    # Persistent oversold (3-4+ days) qualifies at conf>=60; rest need >=70
                    min_conf = 60 if (cat == "oversold" and days >= 3) else 70
                    # Low/mid-cap: require 2+ days OR exceptional first-day conf (>=80)
                    if cat in persistence_required and days < 2 and conf < 80:
                        continue
                    if ticker and conf >= min_conf and ticker not in [c["ticker"] for c in candidates]:
                        # Boost confidence for persistence — 2 days = +3, 3 = +5, 4+ = +10
                        if days >= 4:
                            boost = 10
                        elif days >= 3:
                            boost = 5
                        elif days >= 2:
                            boost = 3
                        else:
                            boost = 0
                        candidates.append({
                            "ticker": ticker,
                            "screener_confidence": min(95, conf + boost),
                            "screener_verdict": opp.get("verdict", ""),
                            "screener_summary": opp.get("summary", "")[:200],
                            "category": cat,
                            "entry_style": entry_style,
                            "bottom_signal": opp.get("bottom_signal_strength"),
                            "days_tracked": days,
                            "first_seen": opp.get("first_seen"),
                            "price_trend": opp.get("price_trend"),
                            "screener_status": opp.get("status"),
                        })
            except Exception as e:
                logger.warning(f"Screener pull failed for {cat}: {e}")
    except Exception as e:
        logger.warning(f"Screener import failed: {e}")

    # Merge in persistent low/mid-cap picks (≥2 daily appearances) per the
    # user's spec — "show up 2-3 times daily → tag as opp → trade in watchdog".
    # These come from the screener_results history table and bypass today's
    # snapshot verdict; persistence is the signal.
    try:
        persistent = _get_persistent_lowmidcap_picks(min_days=2)
        existing_tickers = {c["ticker"] for c in candidates}
        for p in persistent:
            if p["ticker"] not in existing_tickers:
                candidates.append(p)
                existing_tickers.add(p["ticker"])
    except Exception as e:
        logger.warning(f"Persistent low/mid-cap merge failed: {e}")

    # Cap at top 15 by confidence (raised from 10 to make room for persistent picks)
    candidates.sort(key=lambda c: -c["screener_confidence"])
    candidates = candidates[:15]
    _set_cached("screener_candidates", candidates)
    return candidates


def _llm_vet_watchdog_candidate(ticker, setup, screener_info, regime):
    """Use LLM to evaluate whether a screener-sourced candidate is a good swing trade.

    Combines screener AI verdict with technical swing setup for a final decision.
    Falls back to rule-based logic if LLM is unavailable.
    """
    try:
        import os, requests
        # DB override > env > default. Default resolves to ollama/gpt-oss:20b
        # via shared.llm_config DEFAULTS — Ollama Cloud handles the gating tier.
        try:
            from shared.llm_config import get_model
            model = get_model("watchdog_gating",
                              os.getenv("OPENROUTER_MODEL_RESEARCH", "ollama/gpt-oss:20b"))
        except Exception:
            model = os.getenv("OPENROUTER_MODEL_RESEARCH", "ollama/gpt-oss:20b")

        days = screener_info.get("days_tracked", 1)
        persistence_line = (
            f"\nPERSISTENCE: tracked oversold for {days} days (first_seen={screener_info.get('first_seen', 'N/A')}, "
            f"trend={screener_info.get('price_trend', 'N/A')}, status={screener_info.get('screener_status', 'N/A')})"
            if screener_info.get("category") == "oversold" else ""
        )
        prompt = f"""You are a swing trade analyst. Evaluate this candidate for a 1-5 day swing trade.

TICKER: {ticker}
SCREENER VERDICT: {screener_info.get('screener_verdict', 'N/A')} (confidence: {screener_info.get('screener_confidence', 0)}%)
SCREENER REASONING: {screener_info.get('screener_summary', 'N/A')}
CATEGORY: {screener_info.get('category', 'N/A')}{persistence_line}

TECHNICAL SETUP:
- Signal: {setup.get('signal', 'N/A')}
- Strategy: {setup.get('strategy', 'N/A')}
- Confidence: {setup.get('confidence', 0)}%
- RSI: {setup.get('indicators', {}).get('rsi', 'N/A')}
- Price vs SMA50: {setup.get('indicators', {}).get('price', 0)} vs {setup.get('indicators', {}).get('sma50', 0)}
- Relative Volume: {setup.get('indicators', {}).get('rel_volume', 'N/A')}

MARKET REGIME: {regime.get('regime', 'UNKNOWN')} (score: {regime.get('composite_score', 0)})

Respond in JSON only:
{{"approve": true/false, "confidence": 0-100, "reasoning": "one sentence"}}"""

        # ── Ollama Cloud branch ───────────────────────────────────────────
        if model.startswith("ollama/"):
            from shared.runtime_config import get_setting as _rt_get
            ollama_key = _rt_get("ollama_api_key", "", env_aliases=("OLLAMA_API_KEY",))
            if not ollama_key:
                return (setup.get("signal") == "BUY" and
                        setup.get("confidence", 0) >= 65 and
                        screener_info.get("screener_confidence", 0) >= 75)
            ollama_url = _rt_get("ollama_url", "https://ollama.com", env_aliases=("OLLAMA_URL",))
            bare_model = model[len("ollama/"):]
            resp = requests.post(
                f"{ollama_url}/api/chat",
                headers={"Authorization": f"Bearer {ollama_key}",
                         "Content-Type": "application/json"},
                json={"model": bare_model,
                      "messages": [{"role": "user", "content": prompt}],
                      "stream": False, "format": "json",
                      "options": {"temperature": 0.1, "num_predict": 200}},
                timeout=30,
            )
            if resp.status_code == 200:
                content = resp.json().get("message", {}).get("content", "")
                import re
                match = re.search(r'\{[^}]+\}', content)
                if match:
                    result = json.loads(match.group())
                    return result.get("approve", False) and result.get("confidence", 0) >= 60
            # fall through to rule-based fallback below
        else:
            # ── OpenRouter branch (unchanged shape) ───────────────────────
            api_key = os.getenv("OPENROUTER_API_KEY", "")
            if not api_key:
                return setup.get("signal") == "BUY" and setup.get("confidence", 0) >= 65
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.1, "max_tokens": 200},
                timeout=30,
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                import re
                match = re.search(r'\{[^}]+\}', content)
                if match:
                    result = json.loads(match.group())
                    return result.get("approve", False) and result.get("confidence", 0) >= 60
    except Exception as e:
        logger.warning(f"LLM vet failed for {ticker}: {e}")

    # Fallback: approve if both screener confidence >= 75 and technical BUY
    return (setup.get("signal") == "BUY" and
            setup.get("confidence", 0) >= 65 and
            screener_info.get("screener_confidence", 0) >= 75)


def get_signals(watchlist=None):
    """Generate swing signals for all watchlist tickers + screener candidates.

    Returns list of signal dicts with regime context.
    Cache key includes the watchlist so different users get their own signals.
    Screener-sourced candidates are LLM-vetted before inclusion.
    """
    if watchlist is None:
        watchlist = DEFAULT_WATCHLIST

    # Cache key includes sorted watchlist so custom lists don't serve stale data
    cache_key = "signals:" + ",".join(sorted(watchlist))
    cached = _get_cached(cache_key)
    if cached:
        return {"signals": cached, "cached": True}

    regime = get_regime()
    signals = []

    # 1. Scan user's watchlist — batched yfinance download to avoid the
    # per-ticker loop. With 180+ low/mid-cap tickers, sequential downloads
    # took ~3 minutes on cold load; one batched call is ~10s.
    batched = {}
    if watchlist:
        try:
            tickers_str = " ".join(watchlist)
            data = yf.download(
                tickers_str, period="1y", interval="1d",
                progress=False, threads=True, group_by="ticker", timeout=30,
            )
            if not data.empty:
                if len(watchlist) == 1:
                    # Single-ticker download returns a flat frame, not multi-index
                    batched[watchlist[0]] = data
                else:
                    for t in watchlist:
                        try:
                            sub = data[t].dropna(how="all") if t in data else None
                            if sub is not None and not sub.empty:
                                batched[t] = sub
                        except Exception:
                            continue
        except Exception as e:
            logger.warning(f"Batched yfinance download failed: {e} — falling back to per-ticker")

    for ticker in watchlist:
        try:
            df = batched.get(ticker)
            if df is None or df.empty:
                # Skip rather than retry per-ticker. Tickers missing from the
                # batched download are usually delisted; per-ticker retry just
                # burns the full yfinance timeout for nothing (60s+ on 4 dead
                # tickers was the cause of the multi-minute load).
                logger.debug(f"Skipping {ticker}: no data in batched download")
                continue
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

    # 2. Pull screener candidates and generate signals for them
    existing_tickers = set(s["ticker"] for s in signals)
    try:
        screener_candidates = _get_screener_candidates()
        # Drop already-covered tickers and cap at top 15 to bound worst-case latency
        new_cands = [c for c in screener_candidates if c["ticker"] not in existing_tickers][:15]
        # Batch download all remaining candidate tickers in one call
        cand_batched = {}
        if new_cands:
            try:
                cand_str = " ".join(c["ticker"] for c in new_cands)
                cand_data = yf.download(
                    cand_str, period="1y", interval="1d",
                    progress=False, threads=True, group_by="ticker", timeout=30,
                )
                if not cand_data.empty:
                    if len(new_cands) == 1:
                        cand_batched[new_cands[0]["ticker"]] = cand_data
                    else:
                        for c in new_cands:
                            t = c["ticker"]
                            try:
                                sub = cand_data[t].dropna(how="all") if t in cand_data else None
                                if sub is not None and not sub.empty:
                                    cand_batched[t] = sub
                            except Exception:
                                continue
            except Exception as e:
                logger.warning(f"Batched candidate download failed: {e}")

        # First pass: build setups for all candidates (cheap, in-memory).
        # Then parallel-LLM-vet the survivors so we don't wait sequentially.
        # Sequential: 10 candidates × ~6s LLM = 60s. Parallel @5 workers ≈ 15s.
        pending = []  # list of (cand, setup) pairs needing LLM vet
        for cand in new_cands:
            ticker = cand["ticker"]
            df = cand_batched.get(ticker)
            if df is None or df.empty:
                continue
            try:
                setup = _detect_swing_setup(ticker, df, entry_style=cand.get("entry_style"))
            except Exception as e:
                logger.warning(f"Screener candidate {ticker} setup failed: {e}")
                continue
            if not setup or setup["signal"] not in ("BUY", "WATCH"):
                continue
            pending.append((cand, setup))

        vet_results = {}  # ticker -> bool
        if pending:
            try:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                    fut_to_ticker = {
                        ex.submit(_llm_vet_watchdog_candidate, c["ticker"], s, c, regime): c["ticker"]
                        for c, s in pending
                    }
                    for fut in concurrent.futures.as_completed(fut_to_ticker, timeout=30):
                        t = fut_to_ticker[fut]
                        try:
                            vet_results[t] = bool(fut.result(timeout=10))
                        except Exception as e:
                            logger.warning(f"LLM vet failed for {t}: {e}")
                            vet_results[t] = False
            except Exception as e:
                logger.warning(f"Parallel LLM vet pool failed: {e} — defaulting to no candidates")

        for cand, setup in pending:
            ticker = cand["ticker"]
            try:
                if vet_results.get(ticker):
                    # Boost confidence slightly since screener AI already approved
                    if setup["signal"] == "BUY":
                        setup["confidence"] = min(95, setup["confidence"] + 5)
                    days_note = f" ({cand.get('days_tracked', 1)}d tracked)" if cand.get("days_tracked", 1) > 1 else ""
                    setup["reasoning"] = f"[Screener: {cand['screener_verdict']}{days_note}] {setup['reasoning']}"
                    setup["source"] = "screener"
                    setup["days_tracked"] = cand.get("days_tracked", 1)
                    setup["first_seen"] = cand.get("first_seen")
                    setup["price_trend"] = cand.get("price_trend")

                    # Apply regime adjustments
                    if regime["regime"] == "DANGER" and setup["signal"] == "BUY":
                        setup["signal"] = "HEDGE"
                        setup["reasoning"] = f"[DANGER regime override] {setup['reasoning']}"
                        setup["confidence"] = max(20, setup["confidence"] - 30)
                    elif regime["regime"] == "RISK-OFF" and setup["signal"] == "BUY":
                        setup["confidence"] = max(30, setup["confidence"] - 15)
                        setup["reasoning"] = f"[RISK-OFF: reduced confidence] {setup['reasoning']}"

                    signals.append(setup)
                    existing_tickers.add(ticker)
            except Exception as e:
                logger.warning(f"Screener candidate {ticker} signal failed: {e}")
    except Exception as e:
        logger.warning(f"Screener integration failed: {e}")

    # Sort: BUY first, then by confidence
    order = {"BUY": 0, "HEDGE": 1, "SELL": 2, "WATCH": 3, "AVOID": 4, "ERROR": 5}
    signals.sort(key=lambda s: (order.get(s["signal"], 9), -s.get("confidence", 0)))

    _set_cached(cache_key, signals)
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
    "wd_max_total_exposure_pct": "50",  # cap on sum of open positions (day-trader → conservative)
    "wd_daily_loss_limit": "1000",
    "wd_min_confidence": "65",    # min signal confidence to trade
    "wd_watchlist": json.dumps(DEFAULT_WATCHLIST),
    "wd_kill_switch": "0",
    # Exit tuning — let winners run instead of bailing at ~1%. Trade review
    # (May 2026) found 48% of trades exited via an RSI>60/+0.5% rule at +0.97%
    # avg while the 4-7% target was hit twice in 96 trades. Trailing stop +
    # raised RSI bar fix this; catastrophic floor caps tail losses.
    "wd_hard_floor_pct": "5",          # absolute -% kill regardless of stop level
    "wd_trail_arm_pct": "2.5",         # start trailing once up this much
    "wd_trail_giveback_pct": "1.5",    # trail this far below peak (arm..tight band)
    "wd_trail_tight_pct": "4.0",       # past this gain, tighten the trail
    "wd_trail_tight_giveback_pct": "1.0",  # tighter giveback in the 4-7% band
    "wd_rsi_exit_min": "75",           # only momentum-exit when truly overbought
    "wd_rsi_exit_min_pnl": "3.0",      # ...and only on a real winner (was 0.5%)
    "wd_min_candidate_score": "55",    # don't execute weak top-2 candidates
}


def _wd_config(user_id, key, default=None):
    """Read watchdog config — user-specific row wins, falls back to
    admin-set global row (user_id=0), then to WD_DEFAULTS."""
    try:
        row = query_one(
            f"SELECT value FROM bot_config WHERE user_id IN ({_P}, 0) AND key = {_P} "
            f"ORDER BY CASE WHEN user_id = 0 THEN 1 ELSE 0 END LIMIT 1",
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


def _wd_open_value(user_id):
    """Sum of notional value (size * entry_price) of open watchdog positions."""
    try:
        row = query_one(
            f"SELECT COALESCE(SUM(size * entry_price), 0) as val FROM bot_trades "
            f"WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'open'",
            (user_id,),
        )
        return float(row["val"]) if row else 0.0
    except Exception:
        return 0.0


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
    daily_limit = float(_wd_config(user_id, "wd_daily_loss_limit", "1000"))
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

    # Total exposure cap — caps sum of open positions as % of equity. Default
    # 50% (lower than other bots) since watchdog is a day trader and we don't
    # want it fully loaded going into the EOD close window.
    max_exp_pct = float(_wd_config(user_id, "wd_max_total_exposure_pct", "50"))
    if balance > 0 and max_exp_pct > 0:
        deployed = _wd_open_value(user_id)
        projected = deployed + size_usd
        cap = balance * (max_exp_pct / 100)
        if projected > cap:
            return {"allowed": False, "reason": f"Total exposure ${projected:.0f} would exceed {max_exp_pct}% cap (${cap:.0f}) — already deployed ${deployed:.0f}"}

    # Duplicate check
    if _wd_has_position(user_id, ticker):
        return {"allowed": False, "reason": f"Already holding {ticker}"}

    return {"allowed": True, "reason": "All gates passed"}


def _check_pullback_entry(ticker, now_et=None):
    """Intraday pullback gate. Block entries that would buy the opening high.

    User rule (May 2026): "open trade when there is pullback after opening,
    never open on open high." Fetches today's 5-minute bars and requires:
      1. Intraday high made >15 minutes ago (no entries on the very bar that
         set the high — this is the opening-high spike filter).
      2. Current price 0.4-4% below intraday high (real pullback, but not a
         dump — >4% off-high means the breakout has failed).
      3. Current price still above the session 9-EMA on 5m (trend still up).

    Returns dict {allowed: bool, reason: str, intraday: dict}. On data fetch
    failure: allow with a warning (don't block the engine if intraday data
    is briefly unavailable — daily-signal entry is still better than no
    entry at all).
    """
    try:
        import pytz
        from datetime import timedelta as _td
        et = pytz.timezone("US/Eastern")
        if now_et is None:
            now_et = datetime.now(pytz.utc).astimezone(et)

        # Pre-market and very first 5m bar: don't trade. Need at least one
        # closed 5m bar past the open to evaluate "opening high".
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        if now_et < market_open + _td(minutes=20):
            return {
                "allowed": False,
                "reason": f"Too early ({now_et.strftime('%H:%M ET')}) — wait at least 20m for first pullback",
                "intraday": {},
            }

        df = yf.download(ticker, period="1d", interval="5m", progress=False, timeout=15)
        if df is None or df.empty or len(df) < 3:
            return {
                "allowed": True,
                "reason": "No intraday data — falling back to daily signal",
                "intraday": {"warning": "no_data"},
            }

        high = df["High"].values.flatten()
        close = df["Close"].values.flatten()
        current = float(close[-1])
        intraday_high = float(np.max(high))
        high_idx = int(np.argmax(high))
        bars_since_high = (len(high) - 1) - high_idx  # 0 = high is current bar
        minutes_since_high = bars_since_high * 5

        pct_off_high = (intraday_high - current) / intraday_high * 100 if intraday_high > 0 else 0

        # 9-EMA on 5m close
        ema9 = float(pd.Series(close).ewm(span=9, adjust=False).mean().iloc[-1])

        intraday = {
            "intraday_high": round(intraday_high, 2),
            "current": round(current, 2),
            "pct_off_high": round(pct_off_high, 2),
            "minutes_since_high": minutes_since_high,
            "ema9_5m": round(ema9, 2),
        }

        # Gate 1: Opening-high spike — high was set in the last 15 minutes
        if minutes_since_high < 15:
            return {
                "allowed": False,
                "reason": f"Opening-high block: high set {minutes_since_high}m ago (need ≥15m for pullback)",
                "intraday": intraday,
            }

        # Gate 2: Pullback depth must be in the 0.4-4% band
        if pct_off_high < 0.4:
            return {
                "allowed": False,
                "reason": f"At/near intraday high ({pct_off_high:.2f}% off) — wait for pullback",
                "intraday": intraday,
            }
        if pct_off_high > 4.0:
            return {
                "allowed": False,
                "reason": f"Too far off intraday high ({pct_off_high:.2f}%) — breakout failing, skip",
                "intraday": intraday,
            }

        # Gate 3: Must still be holding above 5m 9-EMA (trend intact)
        if current < ema9:
            return {
                "allowed": False,
                "reason": f"Below 5m 9-EMA (${current:.2f} < ${ema9:.2f}) — trend broken",
                "intraday": intraday,
            }

        return {
            "allowed": True,
            "reason": f"Pullback OK: {pct_off_high:.1f}% off high ({minutes_since_high}m ago), above 9-EMA",
            "intraday": intraday,
        }

    except Exception as e:
        logger.warning(f"Pullback gate error for {ticker}: {e}")
        return {
            "allowed": True,
            "reason": f"Pullback check error — falling back to daily signal: {str(e)[:80]}",
            "intraday": {"warning": "error"},
        }


def _wd_execute_trade(user_id, signal, broker_client, mode):
    """Execute a single trade — paper or live via Alpaca."""
    ticker = signal["ticker"]
    stop_loss = signal.get("stop_loss")
    take_profit = signal.get("take_profit")
    price = signal.get("indicators", {}).get("price", 0)
    strategy = signal.get("strategy", "")
    reasoning = signal.get("reasoning", "")
    candidate = signal.get("thunderbot_candidate")

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

    # ThunderBot pullback-risk gate: LLM scores pullback risk and returns
    # a size multiplier + hard stop %. Applied BEFORE share calc so the risk
    # gate flows through the existing risk_check ($ exposure limits).
    if candidate is not None:
        gate = _llm_pullback_risk_gate(ticker, candidate)
        size_usd *= gate["size_mult"]
        stop_loss = round(price * (1 - gate["hard_stop_pct"] / 100), 2)
        # Take-profit: 2x risk (so a 2% stop targets +4%, a 3% stop targets +6%)
        take_profit = round(price * (1 + gate["hard_stop_pct"] * 2 / 100), 2)
        _wd_log(user_id, "info",
            f"PULLBACK-GATE {ticker}: risk={gate['risk_score']} size×{gate['size_mult']:.2f} "
            f"stop=-{gate['hard_stop_pct']:.1f}% src={gate['source']} | {gate['reasoning']}")

    # Score-based sizing: concentrate capital in higher-conviction setups so
    # the strongest pole+flag candidates (the proven edge) get the most size.
    # score 50→0.7x, 70→~1.0x, 90→1.25x (clamped). The position-% cap and risk
    # check still bound the absolute size downstream.
    score = float(signal.get("confidence", 50) or 50)
    score_mult = max(0.6, min(1.25, 0.7 + (score - 50) * 0.01375))
    size_usd *= score_mult

    shares = max(1, int(size_usd / price))

    # Risk check
    risk = _wd_risk_check(user_id, ticker, shares * price, equity)
    if not risk["allowed"]:
        _wd_log(user_id, "info", f"BLOCKED {ticker}: {risk['reason']}")
        return

    # Pullback gate: never enter on the opening-high spike. Requires intraday
    # high to be 15+ minutes old, current price 0.4-4% below it, and still
    # above the 5m 9-EMA. See _check_pullback_entry for full rules.
    pb = _check_pullback_entry(ticker)
    if not pb["allowed"]:
        _wd_log(user_id, "info", f"PULLBACK BLOCK {ticker}: {pb['reason']}")
        return
    _wd_log(user_id, "info", f"{ticker}: {pb['reason']}")

    # Quick backtest: validate signal against recent price history
    try:
        from shared.backtest import quick_backtest
        df = yf.download(ticker, period="1y", interval="1d", progress=False, timeout=15)
        if df is not None and not df.empty:
            sl_pct = abs(price - stop_loss) / price * 100 if price > 0 and stop_loss else 2.0
            tp_pct = abs(take_profit - price) / price * 100 if price > 0 and take_profit else 5.0
            bt = quick_backtest(
                df, side="buy", strategy=strategy,
                stop_loss_pct=sl_pct, take_profit_pct=tp_pct,
                lookback=5, max_hold_bars=14,
            )
            _wd_log(user_id, "info", f"{ticker}: {bt['detail']}")
            if not bt["pass"]:
                _wd_log(user_id, "info", f"BLOCKED {ticker}: Backtest FAILED — signal would have lost money recently")
                return
    except Exception as e:
        _wd_log(user_id, "warning", f"{ticker}: Backtest check failed: {e}")

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


def _quick_rsi(ticker, period="1mo", interval="1d"):
    """Compute current RSI14 for a ticker. Cached 5 min so exit checks don't refetch every cycle."""
    cache_key = f"rsi:{ticker}:{interval}"
    if cache_key in _cache:
        val, ts = _cache[cache_key]
        if time.time() - ts < 300:
            return val
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, timeout=10)
        if df is None or len(df) < 15:
            return None
        close = df["Close"].values.flatten()
        deltas = np.diff(close[-15:])
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = float(np.mean(gains)) if len(gains) else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) else 0.001
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi = 100.0 - (100.0 / (1.0 + rs))
        _cache[cache_key] = (rsi, time.time())
        return rsi
    except Exception:
        return None


def _wd_decide_exit(trade, current_price, rsi, cfg):
    """Pure exit-decision logic for one open position.

    Returns (exit_reason | None, new_stop_loss | None). new_stop_loss is the
    ratcheted trailing stop to persist when we are NOT exiting (None = leave
    the stored stop unchanged). Kept free of DB/yfinance so it is unit-testable.

    Priority (highest first):
      0.  EOD force-close (cfg['eod'])
      0b. Overnight carry — opened a prior session (cfg['overnight'])
      1.  Catastrophic floor — pnl <= -hard_floor_pct, regardless of stop level
      2.  Trailing stop ratchet (long only): arms at trail_arm_pct, tightens
          past trail_tight_pct. Raises the stored stop; never lowers it.
      3.  Stop-loss hit (possibly the freshly-ratcheted trailing stop)
      4.  +7% hard cap (don't get greedy)
      5.  RSI overbought safety — only when truly overbought AND already a real
          winner (raised from RSI>60/+0.5%, which was strangling winners at ~1%)
      6.  Original take-profit level
    """
    entry_price = trade["entry_price"]
    multiplier = 1 if trade.get("side", "long") in ("long", "buy") else -1
    pnl_pct = (current_price - entry_price) / entry_price * 100 * multiplier
    sl = trade.get("stop_loss")
    tp = trade.get("take_profit")
    new_stop = None

    # 0. EOD force-close — day-trader rule: nothing carries overnight.
    if cfg.get("eod"):
        return f"EOD close: day-trader rule (3:55 PM ET, P&L {pnl_pct:+.2f}%)", None

    # 0b. Overnight carry — EOD close was missed (bot down over the close).
    if cfg.get("overnight"):
        return f"Overnight carry exit: opened prior session (P&L {pnl_pct:+.2f}%)", None

    # 1. Catastrophic floor — backstop in case the stop is wider than the floor
    # or price gapped past it. Caps tail losses like the -12.4% VCX blowup.
    floor = cfg["hard_floor_pct"]
    if floor > 0 and pnl_pct <= -floor:
        return f"Catastrophic stop: {pnl_pct:+.2f}% (≤ -{floor:.1f}% hard floor)", None

    # 2. Trailing stop ratchet (long only). Locks in profit once the trade is
    # working, then keeps rising so winners can run toward the 7% cap.
    if multiplier == 1 and pnl_pct >= cfg["trail_arm_pct"]:
        giveback = cfg["trail_giveback_pct"]
        if pnl_pct >= cfg["trail_tight_pct"]:
            giveback = cfg["trail_tight_giveback_pct"]
        candidate_stop = round(current_price * (1 - giveback / 100), 2)
        if sl is None or candidate_stop > sl:
            new_stop = candidate_stop
            sl = candidate_stop  # use the tightened stop for this cycle's check

    # 3. Stop-loss (possibly the freshly-ratcheted trailing stop)
    if sl and current_price <= sl:
        tag = "Trailing stop" if new_stop is not None else "Stop-loss"
        return f"{tag} hit (${sl:.2f}, P&L {pnl_pct:+.2f}%)", new_stop

    # 4. +7% hard cap
    if pnl_pct >= 7.0:
        return f"Quick-trade hard exit: +{pnl_pct:.1f}% (7% cap reached)", new_stop

    # 5. RSI overbought safety — truly overbought AND a real winner only
    if (rsi is not None and rsi > cfg["rsi_exit_min"]
            and pnl_pct >= cfg["rsi_exit_min_pnl"]):
        return (f"RSI overbought ({rsi:.1f}>{cfg['rsi_exit_min']:.0f}): "
                f"locking +{pnl_pct:.1f}% gain"), new_stop

    # 6. Original take-profit
    if tp and current_price >= tp:
        return f"Take-profit hit (${tp:.2f})", new_stop

    return None, new_stop


def _wd_check_exits(user_id, broker_client, mode):
    """Check open watchdog positions for exits.

    Reads config + live price, then delegates the decision to the pure
    _wd_decide_exit helper. Persists any ratcheted trailing stop so it carries
    across scan cycles.
    """
    try:
        open_trades = query(
            f"SELECT * FROM bot_trades WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'open'",
            (user_id,),
        )
    except Exception:
        return

    # Exit-tuning config (resolved once per cycle)
    base_cfg = {
        "hard_floor_pct": float(_wd_config(user_id, "wd_hard_floor_pct", "5")),
        "trail_arm_pct": float(_wd_config(user_id, "wd_trail_arm_pct", "2.5")),
        "trail_giveback_pct": float(_wd_config(user_id, "wd_trail_giveback_pct", "1.5")),
        "trail_tight_pct": float(_wd_config(user_id, "wd_trail_tight_pct", "4.0")),
        "trail_tight_giveback_pct": float(_wd_config(user_id, "wd_trail_tight_giveback_pct", "1.0")),
        "rsi_exit_min": float(_wd_config(user_id, "wd_rsi_exit_min", "75")),
        "rsi_exit_min_pnl": float(_wd_config(user_id, "wd_rsi_exit_min_pnl", "3.0")),
    }

    # EOD / overnight flags (ET-based, shared by all trades this cycle)
    eod_now = False
    today_et = None
    try:
        import pytz
        _et = pytz.timezone("US/Eastern")
        _now_et = datetime.now(pytz.utc).astimezone(_et)
        today_et = _now_et.date()
        if _now_et.weekday() < 5:  # Mon=0..Fri=4
            _eod_cutoff = _now_et.replace(hour=15, minute=55, second=0, microsecond=0)
            eod_now = _now_et >= _eod_cutoff
    except Exception:
        pass

    for trade in open_trades:
        try:
            ticker = trade["coin"]
            entry_price = trade["entry_price"]

            # Get current price
            current_price = yf.Ticker(ticker).info.get("regularMarketPrice")
            if not current_price:
                continue

            multiplier = 1 if trade.get("side", "long") in ("long", "buy") else -1
            current_pnl_pct = (current_price - entry_price) / entry_price * 100 * multiplier

            cfg = dict(base_cfg)
            cfg["eod"] = eod_now
            # Overnight: opened on a prior calendar date (any carry, not just 24h)
            cfg["overnight"] = False
            opened = trade.get("opened_at", "")
            try:
                if opened and today_et is not None:
                    opened_dt = datetime.fromisoformat(str(opened).replace("Z", ""))
                    cfg["overnight"] = opened_dt.date() < today_et
            except Exception:
                pass

            # Only spend an RSI fetch when the RSI-exit rule could actually fire
            rsi = _quick_rsi(ticker) if current_pnl_pct >= cfg["rsi_exit_min_pnl"] else None

            exit_reason, new_stop = _wd_decide_exit(trade, current_price, rsi, cfg)

            # Persist a ratcheted trailing stop when we're holding (not exiting)
            if new_stop is not None and not exit_reason:
                try:
                    execute(
                        f"UPDATE bot_trades SET stop_loss = {_P} WHERE id = {_P}",
                        (new_stop, trade["id"]),
                    )
                    _wd_log(user_id, "info",
                        f"TRAIL {ticker}: stop → ${new_stop:.2f} (P&L {current_pnl_pct:+.2f}%)")
                except Exception as e:
                    _wd_log(user_id, "warning", f"Trail-stop update failed {ticker}: {e}")

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

            # 3. Buying window check: 9:45 AM - 1:00 PM ET (8:45 AM - 12:00 PM CST)
            # Only open NEW positions during this window — exits run anytime
            import pytz
            et = pytz.timezone("US/Eastern")
            now_et = datetime.now(pytz.utc).astimezone(et)
            buy_window_start = now_et.replace(hour=9, minute=45, second=0, microsecond=0)
            buy_window_end = now_et.replace(hour=13, minute=0, second=0, microsecond=0)
            in_buy_window = buy_window_start <= now_et <= buy_window_end

            if not in_buy_window:
                _wd_log(user_id, "info",
                    f"Outside buy window (9:45-13:00 ET, current: {now_et.strftime('%H:%M ET')}) — exits only, no new trades")

            # 4. Identify ThunderBot candidates (RSI + Volume + Bull Flag).
            # Cached per 15-min cohort so this scan-loop iteration shares the
            # candidate list with the /api/watchdog/candidates UI endpoint.
            cand_data = identify_thunderbot_candidates(user_id)
            candidates = cand_data.get("candidates", [])

            # 5. Execute on top candidates only during buy window.
            # No min_conf gate — the candidate score already encodes that, and
            # the pullback-risk gate inside _wd_execute_trade sizes/stops each
            # trade based on LLM judgment.
            if in_buy_window:
                executed = 0
                min_score = int(_wd_config(user_id, "wd_min_candidate_score", "55"))
                for cand in candidates:
                    if executed >= 2:  # Max 2 new trades per cycle
                        break
                    # Conviction floor — don't trade a weak top-2 candidate just
                    # because it ranked. Cuts the EOD-flat dead trades.
                    if cand.get("score", 0) < min_score:
                        _wd_log(user_id, "info",
                            f"SKIP {cand['ticker']}: score {cand.get('score', 0)} < {min_score} floor")
                        continue
                    # Build a signal dict compatible with _wd_execute_trade.
                    # entry_price seeds the position-sizing math; stop/take
                    # are set by the pullback-risk gate at execution time.
                    sig = {
                        "ticker": cand["ticker"],
                        "signal": "BUY",
                        "confidence": cand.get("score", 50),
                        "strategy": "ThunderBot: RSI+Vol+BullFlag",
                        "reasoning": " | ".join(cand.get("reason_chips", [])),
                        "indicators": {
                            "price": cand["price"],
                            "rsi": cand.get("rsi"),
                            "rel_volume": cand.get("rel_volume"),
                        },
                        "stop_loss": None,    # set by pullback gate
                        "take_profit": None,  # set by pullback gate
                        "thunderbot_candidate": cand,  # passed through for gate
                    }
                    _wd_execute_trade(user_id, sig, broker_client, mode)
                    executed += 1

                _wd_log(user_id, "info",
                    f"Scan complete: regime={regime['regime']} | candidates={len(candidates)} | "
                    f"executed={executed} | open={_wd_open_count(user_id)} | mode={mode} | IN BUY WINDOW")
            else:
                _wd_log(user_id, "info",
                    f"Scan complete: regime={regime['regime']} | candidates={len(candidates)} "
                    f"(monitoring) | open={_wd_open_count(user_id)} | mode={mode} | outside buy window")

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

    # Buy window check (9:45 AM - 1:00 PM ET = 8:45 AM - 12:00 PM CST).
    # Three-state classification for the UI badge:
    #   open          → green   (in window, >15 min until close)
    #   closing_soon  → amber   (in window, ≤15 min until 13:00 ET)
    #   closed        → red     (before 9:45 or after 13:00)
    try:
        import pytz
        from datetime import timedelta as _td
        et = pytz.timezone("US/Eastern")
        now_et = datetime.now(pytz.utc).astimezone(et)
        buy_window_start = now_et.replace(hour=9, minute=45, second=0, microsecond=0)
        buy_window_end = now_et.replace(hour=13, minute=0, second=0, microsecond=0)
        in_buy_window = buy_window_start <= now_et <= buy_window_end
        if in_buy_window:
            mins_to_close = (buy_window_end - now_et).total_seconds() / 60
            buy_window_state = "closing_soon" if mins_to_close <= 15 else "open"
        else:
            buy_window_state = "closed"
            mins_to_close = 0
        current_time_et = now_et.strftime("%H:%M ET")
    except Exception:
        in_buy_window = False
        buy_window_state = "closed"
        mins_to_close = 0
        current_time_et = "?"

    return {
        "running": _auto_trader.get("running", False),
        "mode": mode,
        "kill_switch": _wd_config(user_id, "wd_kill_switch") == "1",
        "enabled": _wd_config(user_id, "wd_enabled") == "1",
        "in_buy_window": in_buy_window,
        "buy_window_state": buy_window_state,
        "buy_window_minutes_to_close": round(mins_to_close, 1),
        "current_time_et": current_time_et,
        "buy_window": "9:45-13:00 ET (8:45-12:00 CST)",
        "profit_exit": "4-7%",
        "config": {
            "scan_interval": int(_wd_config(user_id, "wd_scan_interval", "300")),
            "max_positions": int(_wd_config(user_id, "wd_max_positions", "3")),
            "daily_loss_limit": float(_wd_config(user_id, "wd_daily_loss_limit", "1000")),
            "min_confidence": int(_wd_config(user_id, "wd_min_confidence", "65")),
            "max_position_pct": float(_wd_config(user_id, "wd_max_position_pct", "15")),
            "max_total_exposure_pct": float(_wd_config(user_id, "wd_max_total_exposure_pct", "50")),
        },
    }


# ─── Custom Watchlist Management ─────────────────────────────

def get_user_watchlist(user_id):
    """Get user's custom watchlist (falls back to DEFAULT_WATCHLIST)."""
    wl_str = _wd_config(user_id, "wd_watchlist", json.dumps(DEFAULT_WATCHLIST))
    try:
        return json.loads(wl_str)
    except Exception:
        return DEFAULT_WATCHLIST


def set_user_watchlist(user_id, tickers):
    """Save user's custom watchlist."""
    from shared.helpers import _upsert_bot_config
    _upsert_bot_config(user_id, "wd_watchlist", json.dumps(tickers))
    # Clear all signal caches so new watchlist gets scanned fresh
    stale_keys = [k for k in _cache if k.startswith("signals:")]
    for k in stale_keys:
        _cache.pop(k, None)


# ─── Balance & P/L Tracking ──────────────────────────────────

def get_watchdog_balance(user_id):
    """Get comprehensive balance and P&L data for the watchdog dashboard."""
    today = datetime.now().strftime("%Y-%m-%d")

    # Paper balance tracking — start with $100k paper, adjust by realized P&L
    paper_start = 100000.0
    total_realized = 0
    total_fees = 0

    try:
        row = query_one(
            f"SELECT COALESCE(SUM(pnl), 0) as total_pnl, COALESCE(SUM(fee), 0) as total_fees, "
            f"COUNT(*) as total_trades, "
            f"SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, "
            f"SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses "
            f"FROM bot_trades WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'closed'",
            (user_id,),
        )
        if row:
            total_realized = float(row["total_pnl"] or 0)
            total_fees = float(row["total_fees"] or 0)
            total_trades = int(row["total_trades"] or 0)
            wins = int(row["wins"] or 0)
            losses = int(row["losses"] or 0)
        else:
            total_trades = wins = losses = 0
    except Exception:
        total_trades = wins = losses = 0

    # Today's P&L
    today_pnl = _wd_daily_pnl(user_id)

    # This week's P&L
    try:
        from datetime import timedelta
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
        row = query_one(
            f"SELECT COALESCE(SUM(pnl), 0) as wpnl FROM bot_trades "
            f"WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'closed' AND DATE(closed_at) >= {_P}",
            (user_id, week_start),
        )
        week_pnl = float(row["wpnl"]) if row else 0
    except Exception:
        week_pnl = 0

    # This month's P&L
    try:
        month_start = datetime.now().strftime("%Y-%m-01")
        row = query_one(
            f"SELECT COALESCE(SUM(pnl), 0) as mpnl FROM bot_trades "
            f"WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'closed' AND DATE(closed_at) >= {_P}",
            (user_id, month_start),
        )
        month_pnl = float(row["mpnl"]) if row else 0
    except Exception:
        month_pnl = 0

    # Unrealized P&L from open positions
    unrealized = 0
    open_count = 0
    try:
        open_trades = query(
            f"SELECT * FROM bot_trades WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'open'",
            (user_id,),
        )
        open_count = len(open_trades)
        for t in open_trades:
            try:
                cp = yf.Ticker(t["coin"]).info.get("regularMarketPrice")
                if cp and t.get("entry_price"):
                    mult = 1 if t.get("side", "long") in ("long", "buy") else -1
                    unrealized += (cp - t["entry_price"]) * t.get("size", 0) * mult
            except Exception:
                pass
    except Exception:
        pass

    # Best / worst trade
    try:
        best = query_one(
            f"SELECT coin, pnl, pnl_pct FROM bot_trades "
            f"WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'closed' ORDER BY pnl DESC LIMIT 1",
            (user_id,),
        )
        worst = query_one(
            f"SELECT coin, pnl, pnl_pct FROM bot_trades "
            f"WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'closed' ORDER BY pnl ASC LIMIT 1",
            (user_id,),
        )
    except Exception:
        best = worst = None

    # Streak
    try:
        recent = query(
            f"SELECT pnl FROM bot_trades WHERE user_id = {_P} AND asset_type = 'watchdog' AND status = 'closed' "
            f"ORDER BY closed_at DESC LIMIT 20",
            (user_id,),
        )
        streak = 0
        streak_type = "none"
        for t in recent:
            pnl = t.get("pnl", 0) or 0
            if streak == 0:
                streak_type = "win" if pnl > 0 else "loss"
                streak = 1
            elif (pnl > 0 and streak_type == "win") or (pnl < 0 and streak_type == "loss"):
                streak += 1
            else:
                break
    except Exception:
        streak = 0
        streak_type = "none"

    paper_balance = paper_start + total_realized
    daily_goal = float(_wd_config(user_id, "wd_daily_loss_limit", "1000"))

    # Funds deployed across open positions (notional value)
    deployed = _wd_open_value(user_id)
    total_equity_val = paper_balance + unrealized
    deployed_pct = round(deployed / total_equity_val * 100, 1) if total_equity_val > 0 else 0
    max_exp_pct = float(_wd_config(user_id, "wd_max_total_exposure_pct", "50"))

    return {
        "paper_balance": round(paper_balance, 2),
        "total_realized_pnl": round(total_realized, 2),
        "total_unrealized_pnl": round(unrealized, 2),
        "total_equity": round(total_equity_val, 2),
        "funds_deployed_usd": round(deployed, 2),
        "funds_deployed_pct": deployed_pct,
        "funds_available_pct": round(max(0, 100 - deployed_pct), 1),
        "max_total_exposure_pct": max_exp_pct,
        "max_position_pct": float(_wd_config(user_id, "wd_max_position_pct", "15")),
        "today_pnl": round(today_pnl, 2),
        "week_pnl": round(week_pnl, 2),
        "month_pnl": round(month_pnl, 2),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(total_trades, 1) * 100, 1),
        "open_positions": open_count,
        "daily_goal": daily_goal,
        "daily_goal_pct": round(today_pnl / daily_goal * 100, 1) if daily_goal > 0 else 0,
        "total_fees": round(total_fees, 2),
        "best_trade": {"ticker": best["coin"], "pnl": round(best["pnl"], 2), "pct": round(best["pnl_pct"], 2)} if best else None,
        "worst_trade": {"ticker": worst["coin"], "pnl": round(worst["pnl"], 2), "pct": round(worst["pnl_pct"], 2)} if worst else None,
        "streak": streak,
        "streak_type": streak_type,
    }


# ─── Multi-Timeframe Pattern Analysis ────────────────────────

def analyze_patterns(ticker):
    """Analyze weekly, daily, and monthly patterns for a ticker.

    Returns trend direction, day-of-week patterns, monthly seasonality,
    support/resistance levels, and predicted direction.
    """
    try:
        df = yf.download(ticker, period="1y", interval="1d", progress=False, timeout=15)
        if df is None or len(df) < 60:
            return {"error": f"Insufficient data for {ticker}"}

        close = df["Close"].values.flatten()
        high = df["High"].values.flatten()
        low = df["Low"].values.flatten()
        volume = df["Volume"].values.flatten()
        current_price = float(close[-1])

        result = {
            "ticker": ticker,
            "current_price": round(current_price, 2),
        }

        # --- Day-of-week patterns (which days tend to be up/down) ---
        df_copy = df.copy()
        df_copy["returns"] = df_copy["Close"].pct_change()
        df_copy["day_of_week"] = df_copy.index.dayofweek
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        day_patterns = []
        for d in range(5):
            day_data = df_copy[df_copy["day_of_week"] == d]["returns"].dropna()
            if len(day_data) > 5:
                avg_ret = float(day_data.mean()) * 100
                up_pct = float((day_data > 0).sum() / len(day_data)) * 100
                day_patterns.append({
                    "day": day_names[d],
                    "avg_return_pct": round(avg_ret, 3),
                    "up_pct": round(up_pct, 1),
                    "best": day_names[d] if avg_ret > 0 else None,
                })
        result["day_of_week"] = day_patterns
        # Best and worst day
        if day_patterns:
            best_day = max(day_patterns, key=lambda x: x["avg_return_pct"])
            worst_day = min(day_patterns, key=lambda x: x["avg_return_pct"])
            result["best_day"] = best_day["day"]
            result["worst_day"] = worst_day["day"]

        # --- Monthly seasonality (last 12 months) ---
        df_copy["month"] = df_copy.index.month
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        monthly_patterns = []
        for m in range(1, 13):
            month_data = df_copy[df_copy["month"] == m]["returns"].dropna()
            if len(month_data) > 2:
                monthly_patterns.append({
                    "month": month_names[m - 1],
                    "avg_return_pct": round(float(month_data.mean()) * 100, 3),
                    "up_pct": round(float((month_data > 0).sum() / len(month_data)) * 100, 1),
                })
        result["monthly"] = monthly_patterns

        # --- Weekly trend (5-day rolling) ---
        weekly_returns = []
        for i in range(0, min(12, len(close) // 5)):
            start_idx = -(i + 1) * 5
            end_idx = -i * 5 if i > 0 else None
            week_slice = close[start_idx:end_idx] if end_idx else close[start_idx:]
            if len(week_slice) >= 2:
                ret = (float(week_slice[-1]) - float(week_slice[0])) / float(week_slice[0]) * 100
                weekly_returns.append(round(ret, 2))
        weekly_returns.reverse()
        result["weekly_returns"] = weekly_returns
        weeks_up = sum(1 for r in weekly_returns if r > 0)
        result["weekly_up_ratio"] = round(weeks_up / max(len(weekly_returns), 1) * 100, 1)

        # --- Multi-timeframe trend correlation ---
        # Daily trend (20-day)
        sma_20 = float(np.mean(close[-20:])) if len(close) >= 20 else current_price
        daily_trend = "UP" if current_price > sma_20 else "DOWN"

        # Weekly trend (50-day)
        sma_50 = float(np.mean(close[-50:])) if len(close) >= 50 else current_price
        weekly_trend = "UP" if current_price > sma_50 else "DOWN"

        # Monthly trend (200-day)
        sma_200 = float(np.mean(close[-200:])) if len(close) >= 200 else float(np.mean(close))
        monthly_trend = "UP" if current_price > sma_200 else "DOWN"

        result["trends"] = {
            "daily": {"direction": daily_trend, "sma20": round(sma_20, 2), "dist_pct": round((current_price - sma_20) / sma_20 * 100, 2)},
            "weekly": {"direction": weekly_trend, "sma50": round(sma_50, 2), "dist_pct": round((current_price - sma_50) / sma_50 * 100, 2)},
            "monthly": {"direction": monthly_trend, "sma200": round(sma_200, 2), "dist_pct": round((current_price - sma_200) / sma_200 * 100, 2)},
        }

        # Trend alignment score (-3 to +3)
        alignment = (1 if daily_trend == "UP" else -1) + (1 if weekly_trend == "UP" else -1) + (1 if monthly_trend == "UP" else -1)
        result["trend_alignment"] = alignment
        if alignment == 3:
            result["trend_verdict"] = "STRONG UPTREND"
        elif alignment == 2:
            result["trend_verdict"] = "UPTREND"
        elif alignment == 1:
            result["trend_verdict"] = "MILD BULLISH"
        elif alignment == 0:
            result["trend_verdict"] = "MIXED / SIDEWAYS"
        elif alignment == -1:
            result["trend_verdict"] = "MILD BEARISH"
        elif alignment == -2:
            result["trend_verdict"] = "DOWNTREND"
        else:
            result["trend_verdict"] = "STRONG DOWNTREND"

        # --- Support & Resistance levels ---
        # Recent swing lows (support) and swing highs (resistance)
        lookback = min(60, len(close))
        recent_close = close[-lookback:]
        recent_high = high[-lookback:]
        recent_low = low[-lookback:]

        supports = []
        resistances = []
        for i in range(2, len(recent_low) - 2):
            if recent_low[i] < recent_low[i-1] and recent_low[i] < recent_low[i-2] and recent_low[i] < recent_low[i+1] and recent_low[i] < recent_low[i+2]:
                supports.append(round(float(recent_low[i]), 2))
            if recent_high[i] > recent_high[i-1] and recent_high[i] > recent_high[i-2] and recent_high[i] > recent_high[i+1] and recent_high[i] > recent_high[i+2]:
                resistances.append(round(float(recent_high[i]), 2))

        # Keep nearest 3 levels
        supports = sorted(set(supports), reverse=True)[:3]
        resistances = sorted(set(resistances))[-3:]

        result["support_levels"] = supports
        result["resistance_levels"] = resistances

        # --- Daily range analysis (for intraday opportunity sizing) ---
        if len(high) >= 20 and len(low) >= 20:
            daily_ranges = (high[-20:] - low[-20:]) / low[-20:] * 100
            result["avg_daily_range_pct"] = round(float(np.mean(daily_ranges)), 2)
            result["max_daily_range_pct"] = round(float(np.max(daily_ranges)), 2)

        # --- Predicted direction (weighted scoring) ---
        pred_score = 0
        # Trend alignment (+/- 30)
        pred_score += alignment * 10
        # Recent momentum (5d)
        ret_5d = (float(close[-1]) - float(close[-6])) / float(close[-6]) * 100 if len(close) >= 6 else 0
        pred_score += max(-20, min(20, ret_5d * 5))
        # Volume trend
        if len(volume) >= 10:
            vol_ratio = float(np.mean(volume[-5:])) / float(np.mean(volume[-10:-5]))
            if vol_ratio > 1.2:
                pred_score += 10 if ret_5d > 0 else -10
        # Weekly consistency
        if len(weekly_returns) >= 4:
            recent_weeks_up = sum(1 for r in weekly_returns[-4:] if r > 0)
            pred_score += (recent_weeks_up - 2) * 5

        pred_score = max(-100, min(100, pred_score))
        if pred_score > 30:
            result["predicted_direction"] = "LIKELY UP"
        elif pred_score > 10:
            result["predicted_direction"] = "LEAN BULLISH"
        elif pred_score > -10:
            result["predicted_direction"] = "NEUTRAL"
        elif pred_score > -30:
            result["predicted_direction"] = "LEAN BEARISH"
        else:
            result["predicted_direction"] = "LIKELY DOWN"
        result["prediction_score"] = round(pred_score, 1)

        return result

    except Exception as e:
        logger.warning(f"Pattern analysis error for {ticker}: {e}")
        return {"error": str(e)[:100], "ticker": ticker}


# ─── Intraday Dip-Recovery Opportunity Detection ─────────────

def detect_intraday_opportunities(watchlist=None):
    """Scan watchlist for intraday dip-recovery opportunities.

    Detects stocks that have dropped from their open/prev close but are recovering —
    potential quick in/out trades (like APLD 25.07 → 26.33).

    Returns list of opportunities with entry zones, recovery %, and confidence.
    """
    if watchlist is None:
        watchlist = DEFAULT_WATCHLIST

    opportunities = []

    for ticker in watchlist:
        try:
            tk = yf.Ticker(ticker)
            info = tk.info
            current_price = info.get("regularMarketPrice")
            prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
            day_low = info.get("regularMarketDayLow") or info.get("dayLow")
            day_high = info.get("regularMarketDayHigh") or info.get("dayHigh")
            day_open = info.get("regularMarketOpen") or info.get("open")
            avg_volume = info.get("averageDailyVolume10Day", 0)
            current_volume = info.get("regularMarketVolume", 0)

            if not all([current_price, prev_close, day_low, day_high, day_open]):
                continue

            # How far did it drop from open to day low?
            drop_from_open = (day_open - day_low) / day_open * 100 if day_open > 0 else 0
            # How far has it recovered from the day low?
            recovery_from_low = (current_price - day_low) / day_low * 100 if day_low > 0 else 0
            # Change from previous close
            change_from_close = (current_price - prev_close) / prev_close * 100 if prev_close > 0 else 0
            # Day range position (0 = at low, 100 = at high)
            day_range = day_high - day_low
            range_position = ((current_price - day_low) / day_range * 100) if day_range > 0 else 50
            # Relative volume
            rel_vol = current_volume / avg_volume if avg_volume > 0 else 0

            # Get 5-min data for intraday bounce detection
            try:
                intra_df = yf.download(ticker, period="1d", interval="5m", progress=False, timeout=10)
                if intra_df is not None and len(intra_df) >= 10:
                    intra_close = intra_df["Close"].values.flatten()
                    intra_low = intra_df["Low"].values.flatten()

                    # Find where the intraday low occurred
                    low_idx = int(np.argmin(intra_low))
                    bars_since_low = len(intra_low) - 1 - low_idx
                    # Recovery velocity (% recovery per 5-min bar)
                    recovery_velocity = recovery_from_low / max(bars_since_low, 1)

                    # Check for V-recovery pattern: rapid drop then rapid rise
                    is_v_recovery = (drop_from_open >= 1.5 and recovery_from_low >= drop_from_open * 0.5
                                     and bars_since_low >= 3)
                else:
                    bars_since_low = 0
                    recovery_velocity = 0
                    is_v_recovery = False
            except Exception:
                bars_since_low = 0
                recovery_velocity = 0
                is_v_recovery = False

            # Score the opportunity
            opp_score = 0
            reasons = []

            # Significant drop that's recovering
            if drop_from_open >= 2.0 and recovery_from_low >= 1.0:
                opp_score += 30
                reasons.append(f"Dropped {drop_from_open:.1f}% from open, recovered {recovery_from_low:.1f}%")

            # V-recovery pattern
            if is_v_recovery:
                opp_score += 25
                reasons.append("V-recovery pattern detected")

            # Currently in lower half of day range but recovering
            if 20 <= range_position <= 60 and recovery_from_low > 0.5:
                opp_score += 15
                reasons.append(f"Recovering in mid-range ({range_position:.0f}% of day range)")

            # Volume confirmation
            if rel_vol >= 1.3:
                opp_score += 10
                reasons.append(f"Elevated volume ({rel_vol:.1f}x avg)")

            # Still below prev close (buy-the-dip zone)
            if change_from_close < -1.0 and recovery_from_low > 0.5:
                opp_score += 15
                reasons.append(f"Still {change_from_close:.1f}% below yesterday's close")

            # Fast recovery velocity
            if recovery_velocity > 0.1:
                opp_score += 10
                reasons.append(f"Fast recovery ({recovery_velocity:.2f}%/bar)")

            # Only report meaningful opportunities
            if opp_score >= 30:
                # ATR-based stops for quick trades
                try:
                    df_d = yf.download(ticker, period="1mo", interval="1d", progress=False, timeout=10)
                    if df_d is not None and len(df_d) >= 14:
                        h = df_d["High"].values.flatten()
                        l = df_d["Low"].values.flatten()
                        c = df_d["Close"].values.flatten()
                        tr = np.maximum(h[-14:] - l[-14:], np.maximum(np.abs(h[-14:] - c[-15:-1]), np.abs(l[-14:] - c[-15:-1])))
                        atr = float(np.mean(tr))
                    else:
                        atr = current_price * 0.02
                except Exception:
                    atr = current_price * 0.02

                opportunities.append({
                    "ticker": ticker,
                    "current_price": round(current_price, 2),
                    "day_low": round(day_low, 2),
                    "day_high": round(day_high, 2),
                    "prev_close": round(prev_close, 2),
                    "drop_from_open_pct": round(drop_from_open, 2),
                    "recovery_pct": round(recovery_from_low, 2),
                    "change_from_close_pct": round(change_from_close, 2),
                    "range_position": round(range_position, 1),
                    "rel_volume": round(rel_vol, 2),
                    "score": opp_score,
                    "is_v_recovery": is_v_recovery,
                    "reasons": reasons,
                    "suggested_entry": round(current_price, 2),
                    "suggested_stop": round(current_price - atr * 0.75, 2),
                    "suggested_target": round(current_price + atr * 1.5, 2),
                    "risk_reward": round(1.5 / 0.75, 1),
                })

        except Exception as e:
            logger.warning(f"Intraday scan error {ticker}: {e}")

    # Sort by score descending
    opportunities.sort(key=lambda x: -x["score"])
    return {
        "opportunities": opportunities,
        "scanned": len(watchlist),
        "found": len(opportunities),
        "timestamp": datetime.now().isoformat(),
    }


# ─── LLM Daily Low Prediction ───────────────────────────────

def predict_daily_low(ticker):
    """Use LLM to predict today's daily low based on volume pressure,
    candlestick patterns, and support levels.

    Fetches real market data, formats it for the LLM, and returns a
    structured prediction with entry zone, confidence, and reasoning.
    """
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        current_price = info.get("regularMarketPrice", 0)
        day_low = info.get("regularMarketDayLow", 0)
        day_high = info.get("regularMarketDayHigh", 0)
        day_open = info.get("regularMarketOpen", 0)
        prev_close = info.get("previousClose", 0)
        volume = info.get("regularMarketVolume", 0)
        avg_volume = info.get("averageDailyVolume10Day", 0)

        # Get daily candles (last 30 days)
        df_daily = yf.download(ticker, period="1mo", interval="1d", progress=False, timeout=15)
        if df_daily is None or len(df_daily) < 10:
            return {"error": "Insufficient daily data"}

        close_d = df_daily["Close"].values.flatten()
        high_d = df_daily["High"].values.flatten()
        low_d = df_daily["Low"].values.flatten()
        open_d = df_daily["Open"].values.flatten()
        vol_d = df_daily["Volume"].values.flatten()

        # Build last 10 candles summary
        candles = []
        for i in range(-min(10, len(df_daily)), 0):
            o, h, l, c, v = float(open_d[i]), float(high_d[i]), float(low_d[i]), float(close_d[i]), float(vol_d[i])
            body = abs(c - o)
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - l
            candle_type = "bullish" if c > o else "bearish" if c < o else "doji"
            date_str = str(df_daily.index[i].date()) if hasattr(df_daily.index[i], 'date') else str(df_daily.index[i])[:10]
            candles.append(f"  {date_str}: O={o:.2f} H={h:.2f} L={l:.2f} C={c:.2f} Vol={v/1e6:.1f}M "
                          f"({candle_type}, body={body:.2f}, upper_wick={upper_wick:.2f}, lower_wick={lower_wick:.2f})")

        # ATR
        if len(df_daily) >= 14:
            tr = np.maximum(high_d[-14:] - low_d[-14:],
                           np.maximum(np.abs(high_d[-14:] - close_d[-15:-1]),
                                      np.abs(low_d[-14:] - close_d[-15:-1])))
            atr = float(np.mean(tr))
        else:
            atr = float(np.mean(high_d - low_d))

        # Support levels
        supports = []
        for i in range(2, len(low_d) - 2):
            if low_d[i] < low_d[i-1] and low_d[i] < low_d[i-2] and low_d[i] < low_d[i+1] and low_d[i] < low_d[i+2]:
                supports.append(round(float(low_d[i]), 2))
        supports = sorted(set(supports), reverse=True)[:5]

        # RSI
        deltas = np.diff(close_d[-15:])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains) if len(gains) else 0
        avg_loss = np.mean(losses) if len(losses) else 0.001
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = round(100 - (100 / (1 + rs)), 1)

        # Get 5-min intraday data
        intra_summary = ""
        try:
            df_5m = yf.download(ticker, period="1d", interval="5m", progress=False, timeout=10)
            if df_5m is not None and len(df_5m) >= 5:
                intra_close = df_5m["Close"].values.flatten()
                intra_vol = df_5m["Volume"].values.flatten()
                intra_low = df_5m["Low"].values.flatten()

                # Volume pressure: are sellers dominating?
                total_bars = len(df_5m)
                down_bars = sum(1 for i in range(1, total_bars) if intra_close[i] < intra_close[i-1])
                up_bars = total_bars - 1 - down_bars
                vol_on_down = sum(float(intra_vol[i]) for i in range(1, total_bars) if intra_close[i] < intra_close[i-1])
                vol_on_up = sum(float(intra_vol[i]) for i in range(1, total_bars) if intra_close[i] >= intra_close[i-1])
                sell_pressure = round(vol_on_down / max(vol_on_down + vol_on_up, 1) * 100, 1)

                intra_summary = (
                    f"\nIntraday 5-min data ({total_bars} bars):\n"
                    f"  Up bars: {up_bars} | Down bars: {down_bars}\n"
                    f"  Sell pressure: {sell_pressure}% of volume on down bars\n"
                    f"  Intraday low so far: ${float(np.min(intra_low)):.2f}\n"
                    f"  Current vs intraday low: {((current_price - float(np.min(intra_low))) / float(np.min(intra_low)) * 100):.2f}% above low\n"
                )
        except Exception:
            pass

        rel_vol = round(volume / avg_volume, 2) if avg_volume > 0 else 0

        # Get market context (VIX, SPY, QQQ)
        market_context = ""
        try:
            spy_info = yf.Ticker("SPY").info
            qqq_info = yf.Ticker("QQQ").info
            vix_info = yf.Ticker("^VIX").info
            spy_price = spy_info.get("regularMarketPrice", 0)
            spy_change = round((spy_price - spy_info.get("previousClose", spy_price)) / spy_info.get("previousClose", spy_price) * 100, 2) if spy_info.get("previousClose") else 0
            qqq_price = qqq_info.get("regularMarketPrice", 0)
            qqq_change = round((qqq_price - qqq_info.get("previousClose", qqq_price)) / qqq_info.get("previousClose", qqq_price) * 100, 2) if qqq_info.get("previousClose") else 0
            vix_level = vix_info.get("regularMarketPrice", 0)
            vix_prev = vix_info.get("previousClose", vix_level)
            vix_change = round((vix_level - vix_prev) / vix_prev * 100, 2) if vix_prev else 0

            market_context = (
                f"\nMARKET CONTEXT (correlate with {ticker}):\n"
                f"  SPY: ${spy_price:.2f} ({'+' if spy_change >= 0 else ''}{spy_change}% today)\n"
                f"  QQQ (Nasdaq): ${qqq_price:.2f} ({'+' if qqq_change >= 0 else ''}{qqq_change}% today)\n"
                f"  VIX: {vix_level:.2f} ({'+' if vix_change >= 0 else ''}{vix_change}% today) — "
                f"{'LOW FEAR' if vix_level < 20 else 'MODERATE FEAR' if vix_level < 25 else 'HIGH FEAR' if vix_level < 30 else 'EXTREME FEAR'}\n"
                f"  Market direction: {'RISK-ON (VIX low, SPY up)' if vix_level < 20 and spy_change > 0 else 'RISK-OFF (VIX elevated or market down)' if vix_level > 25 or spy_change < -0.5 else 'NEUTRAL'}\n"
            )
        except Exception as e:
            logger.warning(f"Market context fetch failed: {e}")

        prompt = f"""Analyze {ticker} and predict today's daily low price based on volume pressure, candlestick patterns, support levels, and market correlation.

CURRENT MARKET DATA:
  Price: ${current_price:.2f} | Open: ${day_open:.2f} | Day Low: ${day_low:.2f} | Day High: ${day_high:.2f}
  Prev Close: ${prev_close:.2f} | Volume: {volume/1e6:.1f}M | Avg Volume: {avg_volume/1e6:.1f}M | Rel Vol: {rel_vol}x
  RSI(14): {rsi} | ATR(14): ${atr:.2f}

LAST 10 DAILY CANDLES (oldest → newest):
{chr(10).join(candles)}
{intra_summary}
SUPPORT LEVELS: {', '.join(f'${s}' for s in supports) if supports else 'none detected'}
{market_context}
INSTRUCTIONS:
1. Analyze the candlestick patterns — look for hammers, dojis, engulfing patterns, shooting stars
2. Assess selling pressure vs buying pressure from volume distribution
3. Identify where the daily low is likely to form based on support levels and ATR
4. Determine if the stock is likely to dip further or has already found its low
5. Correlate with VIX, SPY, and Nasdaq — if market is selling off, the stock likely has more downside; if market is recovering, the stock low may already be in
6. Factor in VIX level: high VIX = wider stop loss needed, expect bigger intraday swings
7. Identify the best entry zone for a quick in-and-out trade (buy the dip, sell the recovery)

Respond in JSON only:
{{
  "predicted_low": <float price>,
  "predicted_low_range": [<float low_end>, <float high_end>],
  "confidence": <int 0-100>,
  "has_bottomed": <bool - has it likely already hit daily low?>,
  "entry_zone": [<float buy_low>, <float buy_high>],
  "target_price": <float>,
  "stop_loss": <float>,
  "volume_pressure": "<HEAVY_SELLING|MODERATE_SELLING|NEUTRAL|MODERATE_BUYING|HEAVY_BUYING>",
  "candle_pattern": "<detected pattern name or 'none'>",
  "key_support": <float nearest support>,
  "reasoning": "<2-3 sentence explanation>"
}}"""

        try:
            from ai_validator import _call_openrouter, LLM_PATTERN, is_configured
            if not is_configured():
                return {"error": "LLM not configured", "ticker": ticker}

            response = _call_openrouter(
                LLM_PATTERN,
                [
                    {"role": "system", "content": "You are an expert intraday trader specializing in identifying daily lows through volume analysis, candlestick patterns, and support/resistance. You only respond in valid JSON. Focus on capital preservation — better to miss a trade than lose money."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                max_tokens=1024,
                timeout=30,
            )

            # Parse JSON from response
            import re
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                prediction = json.loads(json_match.group())
                prediction["ticker"] = ticker
                prediction["current_price"] = round(current_price, 2)
                prediction["day_low"] = round(day_low, 2)
                prediction["atr"] = round(atr, 2)
                prediction["rsi"] = rsi
                prediction["rel_volume"] = rel_vol
                prediction["timestamp"] = datetime.now().isoformat()
                return prediction
            else:
                return {"error": "LLM returned invalid response", "ticker": ticker, "raw": response[:200]}

        except Exception as e:
            logger.warning(f"LLM daily low prediction failed for {ticker}: {e}")
            # Fallback: rule-based prediction
            predicted_low = max(
                current_price - atr * 0.5,
                supports[0] if supports else current_price * 0.97,
            )
            return {
                "ticker": ticker,
                "predicted_low": round(predicted_low, 2),
                "predicted_low_range": [round(predicted_low - atr * 0.2, 2), round(predicted_low + atr * 0.2, 2)],
                "confidence": 40,
                "has_bottomed": current_price <= day_low * 1.002,
                "entry_zone": [round(predicted_low, 2), round(predicted_low + atr * 0.3, 2)],
                "target_price": round(current_price + atr * 1.0, 2),
                "stop_loss": round(predicted_low - atr * 0.5, 2),
                "volume_pressure": "NEUTRAL",
                "candle_pattern": "none (rule-based fallback)",
                "key_support": supports[0] if supports else round(current_price * 0.97, 2),
                "reasoning": f"Rule-based fallback: predicted low near ${predicted_low:.2f} based on ATR and nearest support. LLM unavailable.",
                "current_price": round(current_price, 2),
                "day_low": round(day_low, 2),
                "atr": round(atr, 2),
                "rsi": rsi,
                "rel_volume": rel_vol,
                "fallback": True,
                "timestamp": datetime.now().isoformat(),
            }

    except Exception as e:
        logger.error(f"Daily low prediction error for {ticker}: {e}")
        return {"error": str(e)[:100], "ticker": ticker}


# ─── ThunderBot Identified-Candidates pipeline ──────────────────────────
#
# User directive 2026-05-14: ThunderBot should only act on RSI + Volume +
# Bull-Flag setups with LLM-gated pullback risk. Three weeks of multi-strategy
# signals didn't make money. New flow:
#   1. Every 15 min during the buy window, scan low/mid-cap universe (+ large-
#      cap breakout overlay) on 5-minute candles.
#   2. Filter to high relative volume + sustained price movement.
#   3. Require a bull-flag pattern (pole + consolidation flag) on recent bars.
#   4. Pre-market data feeds the first scan as a tiebreaker.
#   5. Top 5-6 survivors become the "Identified Candidates" — only these are
#      eligible for execution. Other strategies (VCP/HTF/Breakout/etc.) stay
#      in the codebase for screener/manual use but the auto-trader ignores them.
#   6. LLM pullback-risk gate runs per candidate at execution time: returns a
#      size multiplier and hard-stop %. Engine applies both.

THUNDERBOT_MAX_CANDIDATES = 6
THUNDERBOT_REFRESH_SEC = 900  # 15 min — matches user's "rolling 15-min" ask

# Entry-conviction gates. Raised (May 2026) after the trade review found 26
# positions force-closed flat at EOD — low-conviction entries that went
# nowhere. A real volume surge + actual intraday movement filters those out.
_TB_MIN_REL_VOL = 1.35       # was 1.2 — require volume pacing ahead of normal
_TB_MIN_INTRA_RANGE = 1.0    # was 0.8 — skip stocks that aren't really moving
_THUNDERBOT_CACHE_KEY = "thunderbot:candidates"
_THUNDERBOT_SCAN_INFLIGHT = {"running": False, "started_at": 0}


def _detect_bull_flag(df_5m):
    """Detect a bull-flag pattern on 5-minute candles.

    Returns dict {detected: bool, pole_pct: float, flag_bars: int,
                  flag_range_pct: float, flag_drift_pct: float, reason: str}.

    Heuristic — last ~12 5m bars (60 min):
      - Pole: prior 3-6 bars show >0.8% net gain (sustained up-move).
      - Flag: most recent 3-6 bars consolidate inside a tight range (≤2.2%
        high-to-low) with a slight downward drift (-1.0% to +0.4% net) — NOT
        a sharp reversal.
      - Volume sanity: flag-section volume below pole-section volume (real
        consolidation, not distribution).

    Tuned for intraday day-trading on small/mid-caps. The original 1.2%+ pole
    threshold (2026-05-14 initial drop) rejected nearly every real-world setup
    in the first hour of trading because few stocks make a 1.2% sustained run
    in just 6 5m bars (30 min). Relaxed to 0.8% based on observed yfinance
    data. Returns detected=False with a reason string on any miss.
    """
    try:
        if df_5m is None or df_5m.empty or len(df_5m) < 8:
            return {"detected": False, "reason": "insufficient bars"}

        close = df_5m["Close"].values.flatten().astype(float)
        high = df_5m["High"].values.flatten().astype(float)
        low = df_5m["Low"].values.flatten().astype(float)
        vol = df_5m["Volume"].values.flatten().astype(float) if "Volume" in df_5m else np.zeros(len(close))

        # Use last 10-12 bars; split into pole (older half) + flag (newer half)
        n = min(12, len(close))
        section = close[-n:]
        pole = section[: n // 2]
        flag_close = section[n // 2:]
        flag_high = high[-(n - n // 2):]
        flag_low = low[-(n - n // 2):]

        pole_pct = (pole[-1] - pole[0]) / pole[0] * 100 if pole[0] > 0 else 0
        if pole_pct < 0.8:
            return {"detected": False, "pole_pct": round(pole_pct, 2),
                    "reason": f"weak pole ({pole_pct:.2f}% < 0.8%)"}

        flag_range_pct = (flag_high.max() - flag_low.min()) / flag_close[0] * 100 if flag_close[0] > 0 else 99
        if flag_range_pct > 2.2:
            return {"detected": False, "pole_pct": round(pole_pct, 2),
                    "flag_range_pct": round(flag_range_pct, 2),
                    "reason": f"flag too wide ({flag_range_pct:.2f}% > 2.2%)"}

        flag_drift_pct = (flag_close[-1] - flag_close[0]) / flag_close[0] * 100 if flag_close[0] > 0 else 0
        # Flag should drift sideways or mildly down (-1.0% to +0.4%). A strong
        # continuation up means we missed the entry; sharp drop means failure.
        if flag_drift_pct < -1.0 or flag_drift_pct > 0.4:
            return {"detected": False, "pole_pct": round(pole_pct, 2),
                    "flag_drift_pct": round(flag_drift_pct, 2),
                    "reason": f"flag drift out of band ({flag_drift_pct:.2f}%)"}

        # Volume sanity (soft): if we have data, flag avg should be < pole avg.
        # Don't reject solely on volume — yfinance 5m volume is noisy.
        try:
            pole_v = vol[-n:-(n - n // 2)]
            flag_v = vol[-(n - n // 2):]
            vol_ratio = float(flag_v.mean() / pole_v.mean()) if pole_v.mean() > 0 else 1.0
        except Exception:
            vol_ratio = 1.0

        return {
            "detected": True,
            "pole_pct": round(pole_pct, 2),
            "flag_bars": int(n - n // 2),
            "flag_range_pct": round(flag_range_pct, 2),
            "flag_drift_pct": round(flag_drift_pct, 2),
            "flag_pole_vol_ratio": round(vol_ratio, 2),
            "reason": f"pole +{pole_pct:.1f}%, flag {flag_range_pct:.1f}% range, drift {flag_drift_pct:+.1f}%",
        }
    except Exception as e:
        return {"detected": False, "reason": f"detector error: {e}"}


def _premarket_move_pct(ticker):
    """Return % change from prior close to last pre-market trade.

    Uses yfinance prepost data on a 1m interval over 2d, then takes the most
    recent pre-market bar (before 9:30 ET) vs the prior session's last regular
    close. Returns 0 on any failure — pre-market data is a tiebreaker, not a
    gate, so missing data must never block the ID scan.
    """
    try:
        df = yf.download(ticker, period="2d", interval="1m", prepost=True,
                         progress=False, timeout=10)
        if df is None or df.empty:
            return 0.0
        # yfinance returns rows with tz-aware index when prepost=True
        import pytz
        et = pytz.timezone("US/Eastern")
        idx = df.index.tz_convert(et) if df.index.tz else df.index
        # Take last regular-hours close on day-1 as baseline
        df2 = df.copy()
        df2.index = idx
        last = df2.iloc[-1]
        last_t = df2.index[-1].time()
        # If now is during regular hours, we want the open vs prior close — same code path is fine.
        prior_close_rows = df2[df2.index.normalize() < df2.index[-1].normalize()]
        if prior_close_rows.empty:
            return 0.0
        prior_close = float(prior_close_rows.iloc[-1]["Close"])
        cur = float(last["Close"])
        if prior_close <= 0:
            return 0.0
        return round((cur - prior_close) / prior_close * 100, 2)
    except Exception:
        return 0.0


def _score_thunderbot_candidate(ticker, df_5m, df_daily, premarket_pct=0.0,
                                 now_et=None):
    """Score a single ticker for the ThunderBot identified-candidate pool.

    Returns either a dict with score (0-100) + components, or a dict
    {"reject": True, "reason": str, "stage": str} when a gate filters it out
    so the caller can build diagnostic rejection counts.

    Gating rules:
      - RSI must be in 40-72 band (no extreme oversold/overbought; sweet spot
        50-65 gets the highest score).
      - Time-of-day-adjusted relative volume ≥ 1.2x. Compares today's partial
        volume to the same fraction of a typical session — so we're asking
        "is volume PACING ahead of normal", not "is today's partial total
        bigger than full-day average" (which is impossible early in the day).
      - Intraday range ≥ 0.8% so far today (otherwise it's not moving).
      - Bull flag detected on the 5m frame.

    Components rolled into the final score:
      RSI sweet-spot   30 pts (peak at RSI 55, falls off either side)
      Rel volume       30 pts (capped: 2.5x pace = full marks)
      Bull-flag pole   25 pts (linear: 0.8% pole = 0, 3% pole = full)
      Pre-market move  15 pts (positive pre-market adds; negative subtracts)
    """
    try:
        if df_daily is None or df_daily.empty or len(df_daily) < 20:
            return {"reject": True, "stage": "daily_data", "reason": "insufficient daily history"}
        close = df_daily["Close"].values.flatten().astype(float)
        vol = df_daily["Volume"].values.flatten().astype(float)

        # RSI14 on daily close
        delta = np.diff(close)
        gains = np.where(delta > 0, delta, 0)
        losses = np.where(delta < 0, -delta, 0)
        if len(gains) < 14:
            return {"reject": True, "stage": "rsi_data", "reason": "not enough bars for RSI"}
        avg_gain = gains[-14:].mean()
        avg_loss = losses[-14:].mean()
        rs = avg_gain / avg_loss if avg_loss > 0 else 999
        rsi = 100 - (100 / (1 + rs))
        if rsi < 40 or rsi > 72:
            return {"reject": True, "stage": "rsi_band", "reason": f"RSI {rsi:.0f} outside 40-72",
                    "rsi": round(float(rsi), 1)}

        # Time-of-day-adjusted relative volume.
        # Trading day is 9:30-16:00 ET = 390 minutes. Estimate the fraction of
        # the day elapsed so far; compare today's volume against that fraction
        # of the 20-day average daily volume. A stock pacing at normal rate
        # will score 1.0x; a real volume surge scores well above 1.5x.
        import pytz
        if now_et is None:
            et = pytz.timezone("US/Eastern")
            now_et = datetime.now(pytz.utc).astimezone(et)
        market_open_min = 9 * 60 + 30
        market_close_min = 16 * 60
        now_min = now_et.hour * 60 + now_et.minute
        elapsed_min = max(1, min(market_close_min - market_open_min, now_min - market_open_min))
        day_fraction = elapsed_min / (market_close_min - market_open_min)
        avg_vol_20d = float(vol[-20:].mean()) if vol[-20:].mean() > 0 else 0
        expected_so_far = max(1.0, avg_vol_20d * day_fraction)
        today_vol = float(vol[-1])
        rel_vol = today_vol / expected_so_far if expected_so_far > 0 else 0
        if rel_vol < _TB_MIN_REL_VOL:
            return {"reject": True, "stage": "rel_volume",
                    "reason": f"rel-vol pace {rel_vol:.2f}x < {_TB_MIN_REL_VOL}x", "rel_volume": round(rel_vol, 2)}

        # Intraday range from 5m frame (need real price movement, not a dead stock)
        if df_5m is None or df_5m.empty or len(df_5m) < 4:
            return {"reject": True, "stage": "intraday_data", "reason": "no/short 5m frame"}
        intra_high = float(df_5m["High"].max())
        intra_low = float(df_5m["Low"].min())
        intra_open = float(df_5m["Open"].iloc[0])
        intra_range_pct = (intra_high - intra_low) / intra_open * 100 if intra_open > 0 else 0
        if intra_range_pct < _TB_MIN_INTRA_RANGE:
            return {"reject": True, "stage": "intra_range",
                    "reason": f"intraday range {intra_range_pct:.2f}% < {_TB_MIN_INTRA_RANGE}%",
                    "intra_range_pct": round(intra_range_pct, 2)}

        # Bull flag is the hard gate
        flag = _detect_bull_flag(df_5m)
        if not flag.get("detected"):
            return {"reject": True, "stage": "bull_flag",
                    "reason": flag.get("reason", "no bull flag"), "bull_flag": flag}

        # ─── Composite score ───────────────────────────────────
        # Weighted toward pole strength (May 2026): the proven edge is tight
        # high-pole flags (HTF-style), so pole gets 30 pts and RSI 25.
        # RSI sweet-spot: peak at 55, falls off either side
        rsi_score = max(0, 25 - abs(rsi - 55) * 1.0)
        # Rel volume: linear up to 2.5x pace = full marks
        vol_score = min(30, max(0, (rel_vol - _TB_MIN_REL_VOL) / 1.15 * 30))
        # Pole strength: 0.8% = 0pts, 3.0% = 30pts (linear)
        pole_score = min(30, max(0, (flag["pole_pct"] - 0.8) / 2.2 * 30))
        # Pre-market move bonus/penalty
        pm_score = max(-5, min(15, premarket_pct * 2.5))  # +1% = +2.5pts, +6% = +15pts

        score = int(round(rsi_score + vol_score + pole_score + pm_score))

        current_price = float(df_5m["Close"].iloc[-1])

        return {
            "ticker": ticker,
            "score": score,
            "price": round(current_price, 2),
            "rsi": round(float(rsi), 1),
            "rel_volume": round(rel_vol, 2),
            "intra_range_pct": round(intra_range_pct, 2),
            "premarket_pct": premarket_pct,
            "bull_flag": flag,
            "reason_chips": [
                f"RSI {rsi:.0f}",
                f"Vol {rel_vol:.1f}x",
                f"Flag (+{flag['pole_pct']:.1f}% pole)",
                f"Range {intra_range_pct:.1f}%",
            ] + ([f"PM {premarket_pct:+.1f}%"] if abs(premarket_pct) >= 0.5 else []),
        }
    except Exception as e:
        logger.debug(f"ThunderBot scoring {ticker} failed: {e}")
        return {"reject": True, "stage": "error", "reason": str(e)[:80]}


def _thunderbot_universe(user_id):
    """The candidate universe for ThunderBot: user watchlist + large-cap
    breakout overlay. Same as the existing scan-loop universe, kept in sync."""
    try:
        wl_str = _wd_config(user_id, "wd_watchlist", json.dumps(DEFAULT_WATCHLIST))
        watchlist = json.loads(wl_str)
    except Exception:
        watchlist = list(DEFAULT_WATCHLIST)
    breakouts = _get_largecap_breakouts()
    if breakouts:
        watchlist = list(dict.fromkeys(list(watchlist) + breakouts))
    return watchlist


def _latest_cached_candidates(user_id):
    """Find the most recent cached candidate payload for this user, regardless
    of cohort. Used by the UI route to return *something* immediately instead
    of blocking on a 50s yfinance scan."""
    prefix = f"{_THUNDERBOT_CACHE_KEY}:{user_id}:"
    best = None
    best_ts = 0
    for k, (payload, ts) in _cache.items():
        if k.startswith(prefix) and ts > best_ts:
            best = payload
            best_ts = ts
    return best


def get_thunderbot_candidates_async(user_id):
    """Non-blocking candidate fetch for UI consumption.

    Returns immediately with one of:
      - Cached current-cohort payload (cached=True) — fresh.
      - Most-recent prior-cohort payload (cached=True, stale=True) — show last
        snapshot while background refresh runs.
      - Pending payload (pending=True, candidates=[]) — first call, nothing
        cached yet. UI shows a "scanning..." state.

    Kicks off a background scan if there's no fresh cache for the current
    cohort and no scan is already running. The 50s yfinance batch download
    never blocks the request — only one inflight scan at a time per process.
    """
    import pytz, threading
    et = pytz.timezone("US/Eastern")
    now_et = datetime.now(pytz.utc).astimezone(et)
    cohort_min = (now_et.minute // 15) * 15
    cohort = now_et.replace(minute=cohort_min, second=0, microsecond=0).strftime("%Y-%m-%d_%H:%M")
    cache_key = f"{_THUNDERBOT_CACHE_KEY}:{user_id}:{cohort}"

    cached = _cache.get(cache_key)
    if cached and time.time() - cached[1] < THUNDERBOT_REFRESH_SEC:
        data = dict(cached[0])
        data["cached"] = True
        return data

    # No fresh cache — kick off a background refresh if one isn't running
    if not _THUNDERBOT_SCAN_INFLIGHT["running"]:
        _THUNDERBOT_SCAN_INFLIGHT["running"] = True
        _THUNDERBOT_SCAN_INFLIGHT["started_at"] = time.time()

        def _bg():
            try:
                identify_thunderbot_candidates(user_id, force_refresh=True)
            except Exception as e:
                logger.warning(f"ThunderBot bg scan failed: {e}")
            finally:
                _THUNDERBOT_SCAN_INFLIGHT["running"] = False

        threading.Thread(target=_bg, daemon=True, name="thunderbot-bg-scan").start()

    # Return the most recent stale payload if any, else a pending placeholder
    prior = _latest_cached_candidates(user_id)
    if prior is not None:
        data = dict(prior)
        data["cached"] = True
        data["stale"] = True
        data["refreshing"] = True
        return data

    return {
        "candidates": [],
        "scanned": 0,
        "rejections": {},
        "rejection_samples": [],
        "next_refresh_sec": THUNDERBOT_REFRESH_SEC,
        "scan_started_at": now_et.isoformat(),
        "cohort": cohort,
        "cached": False,
        "pending": True,
        "refreshing": True,
    }


def identify_thunderbot_candidates(user_id, force_refresh=False):
    """Run the 15-min rolling candidate identification scan.

    Returns dict:
      {
        "candidates": [ {ticker, score, ..., reason_chips}, ... ],  # ≤6
        "scanned": int,
        "next_refresh_sec": int,
        "scan_started_at": iso,
        "cohort": str,           # e.g. "2026-05-14_10:00"
        "cached": bool,
      }

    This call does the full 30-50s yfinance batch download and SHOULD NOT be
    called from a request thread — use get_thunderbot_candidates_async for UI.
    Caches the result keyed by 15-min cohort window. force_refresh=True
    bypasses cache (used by the scan loop on first run after start).
    """
    import pytz
    et = pytz.timezone("US/Eastern")
    now_et = datetime.now(pytz.utc).astimezone(et)

    # Cohort window: floor minutes to 15-min buckets
    cohort_min = (now_et.minute // 15) * 15
    cohort = now_et.replace(minute=cohort_min, second=0, microsecond=0).strftime("%Y-%m-%d_%H:%M")
    cache_key = f"{_THUNDERBOT_CACHE_KEY}:{user_id}:{cohort}"

    if not force_refresh:
        cached = _cache.get(cache_key)
        if cached and time.time() - cached[1] < THUNDERBOT_REFRESH_SEC:
            data = dict(cached[0])
            data["cached"] = True
            return data

    universe = _thunderbot_universe(user_id)
    if not universe:
        empty = {"candidates": [], "scanned": 0, "rejections": {},
                 "rejection_samples": [], "next_refresh_sec": THUNDERBOT_REFRESH_SEC,
                 "scan_started_at": now_et.isoformat(), "cohort": cohort, "cached": False}
        # Cache the empty result so subsequent async-fetch calls within the
        # same cohort don't re-trigger the bg scan.
        _cache[cache_key] = (empty, time.time())
        return empty

    # Batch download daily (for RSI + rel-vol context) and 5m (for bull-flag + intraday range)
    daily_batched = {}
    intraday_batched = {}
    try:
        daily = yf.download(" ".join(universe), period="2mo", interval="1d",
                            progress=False, threads=True, group_by="ticker", timeout=30)
        if not daily.empty:
            if len(universe) == 1:
                daily_batched[universe[0]] = daily
            else:
                for t in universe:
                    try:
                        sub = daily[t].dropna(how="all") if t in daily else None
                        if sub is not None and not sub.empty:
                            daily_batched[t] = sub
                    except Exception:
                        continue
    except Exception as e:
        logger.warning(f"ThunderBot daily download failed: {e}")

    try:
        intraday = yf.download(" ".join(universe), period="1d", interval="5m",
                               progress=False, threads=True, group_by="ticker", timeout=30)
        if not intraday.empty:
            if len(universe) == 1:
                intraday_batched[universe[0]] = intraday
            else:
                for t in universe:
                    try:
                        sub = intraday[t].dropna(how="all") if t in intraday else None
                        if sub is not None and not sub.empty:
                            intraday_batched[t] = sub
                    except Exception:
                        continue
    except Exception as e:
        logger.warning(f"ThunderBot 5m download failed: {e}")

    # Pre-market data: only fetch for the first cohort of the day (before 10:00 ET)
    # to keep latency reasonable. Later cohorts skip pre-market lookup.
    fetch_premarket = now_et.hour < 10
    scored = []
    rejections = {"no_data": 0, "rsi_band": 0, "rel_volume": 0, "intra_range": 0,
                  "bull_flag": 0, "rsi_data": 0, "daily_data": 0, "intraday_data": 0,
                  "error": 0}
    rejection_samples = []  # keep first 3 rejections per stage for UI

    for t in universe:
        df_d = daily_batched.get(t)
        df_5 = intraday_batched.get(t)
        if df_d is None or df_5 is None:
            rejections["no_data"] += 1
            continue
        pm = _premarket_move_pct(t) if fetch_premarket else 0.0
        result = _score_thunderbot_candidate(t, df_5, df_d, premarket_pct=pm, now_et=now_et)
        if result is None:
            continue
        if result.get("reject"):
            stage = result.get("stage", "error")
            rejections[stage] = rejections.get(stage, 0) + 1
            if len(rejection_samples) < 12:
                rejection_samples.append({"ticker": t, "stage": stage,
                                          "reason": result.get("reason", "")})
            continue
        scored.append(result)

    scored.sort(key=lambda c: -c["score"])
    top = scored[:THUNDERBOT_MAX_CANDIDATES]

    payload = {
        "candidates": top,
        "scanned": len(universe),
        "rejections": rejections,
        "rejection_samples": rejection_samples,
        "next_refresh_sec": THUNDERBOT_REFRESH_SEC,
        "scan_started_at": now_et.isoformat(),
        "cohort": cohort,
        "cached": False,
    }
    _cache[cache_key] = (payload, time.time())
    return payload


def _llm_pullback_risk_gate(ticker, candidate, df_5m=None):
    """Score pullback risk for a ThunderBot candidate and return execution knobs.

    Returns:
      {
        "allow": bool,
        "risk_score": int (0-100, higher = more pullback risk),
        "size_mult": float (0.3-1.0, scales the position),
        "hard_stop_pct": float (1.5-3.0, tightest stop ThunderBot will use),
        "reasoning": str,
        "source": "llm" | "fallback",
      }

    Hard stop is ALWAYS applied (max -3% from entry); the LLM picks how tight
    within the 1.5-3.0% band. Size multiplier scales the calculated $ size.
    LLM-down on failure: a rule-based fallback returns sane defaults so the
    engine never blocks on LLM outages.
    """
    fallback = {
        "allow": True,
        "risk_score": 35,
        "size_mult": 0.7,
        "hard_stop_pct": 2.0,
        "reasoning": "Rule-based default (LLM unavailable)",
        "source": "fallback",
    }
    try:
        import os, requests, re
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            return fallback
        try:
            from shared.llm_config import get_model
            model = get_model("watchdog_gating",
                              os.getenv("OPENROUTER_MODEL_RESEARCH", "google/gemini-2.0-flash-001"))
        except Exception:
            model = os.getenv("OPENROUTER_MODEL_RESEARCH", "google/gemini-2.0-flash-001")

        flag = candidate.get("bull_flag", {})
        prompt = f"""You are a day-trading risk analyst. Score the pullback risk for this BULL-FLAG long entry.

TICKER: {ticker}
PRICE: ${candidate.get('price', 0)}
RSI(14): {candidate.get('rsi', '?')}
RELATIVE VOLUME: {candidate.get('rel_volume', '?')}x
INTRADAY RANGE TODAY: {candidate.get('intra_range_pct', '?')}%
PRE-MARKET MOVE: {candidate.get('premarket_pct', 0):+.1f}%
BULL FLAG: pole +{flag.get('pole_pct', 0)}%, flag drift {flag.get('flag_drift_pct', 0):+.1f}%, range {flag.get('flag_range_pct', 0)}%

The question: how likely is a MASSIVE pullback (>3% drawdown) in the next 60 min after entry? Consider extension from the pole, RSI proximity to overbought, and whether the flag drift suggests distribution.

Respond JSON only:
{{"risk_score": 0-100, "size_mult": 0.3-1.0, "hard_stop_pct": 1.5-3.0, "reasoning": "one sentence"}}

GUIDELINES:
- risk_score >= 70: high pullback risk → size_mult 0.3-0.5, hard_stop 1.5-2.0
- risk_score 40-69: moderate → size_mult 0.5-0.8, hard_stop 2.0-2.5
- risk_score < 40: clean setup → size_mult 0.8-1.0, hard_stop 2.5-3.0
"""

        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1, "max_tokens": 200},
            timeout=20,
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            match = re.search(r'\{[^}]+\}', content)
            if match:
                d = json.loads(match.group())
                risk = max(0, min(100, int(d.get("risk_score", 50))))
                mult = max(0.3, min(1.0, float(d.get("size_mult", 0.7))))
                stop = max(1.5, min(3.0, float(d.get("hard_stop_pct", 2.0))))
                # Allow always — the gate is sizing + stop, not a hard skip,
                # because the user wants the bot to ACT (memory: bots should
                # actively trade). High risk just means small + tight.
                return {
                    "allow": True,
                    "risk_score": risk,
                    "size_mult": mult,
                    "hard_stop_pct": stop,
                    "reasoning": str(d.get("reasoning", ""))[:140],
                    "source": "llm",
                }
    except Exception as e:
        logger.warning(f"ThunderBot pullback-risk LLM failed for {ticker}: {e}")
    return fallback
