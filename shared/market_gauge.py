"""Market-condition risk gauge — a single BUY / HOLD / SELL read on the overall
market built from SPY, the Nasdaq (QQQ proxy for /NQ), VIX, and Fear & Greed.

Purpose (per the user): a risk lens that helps users limit downside — when the
broad tape deteriorates (falling indices on heavy *selling* volume, spiking VIX,
fear) the gauge turns defensive; when conditions are risk-on (indices rising on
*buying* volume, low VIX, greed) it leans BUY. It also feeds the stock analyzer
so per-stock verdicts are macro-aware (see analysis_engine.generate_recommendation
and ai_validator._build_data_summary).

This is momentum/risk-on framing, not contrarian: greed + calm = favorable,
fear + volatility = defensive. Each component degrades gracefully — a failed
fetch contributes 0 rather than breaking the gauge.

Data: shared.yf_fetch.get_history (cached, rate-limit resilient). Nasdaq uses
QQQ because /NQ futures (NQ=F) and ^IXIC are unreliable through the yfinance/FMP
fallback path the rest of the app standardizes on.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_CACHE = {}            # "gauge" -> (result, ts)
_TTL = 900             # 15 min
_LOCK = threading.Lock()

# Component weight caps (composite is clamped to [-100, 100])
_CAP_SPY = 35
_CAP_NDX = 25
_CAP_VIX = 25
_CAP_FG = 15

BUY_AT = 25
SELL_AT = -25


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _index_component(symbol, cap):
    """Score one equity index by trend + volume direction. Returns (score, meta)
    or (None, {error}) when data is unavailable."""
    try:
        from shared.yf_fetch import get_history
        df = get_history(symbol, period="1mo", interval="1d", ttl=900)
    except Exception as e:
        logger.debug(f"gauge: {symbol} fetch failed: {e}")
        df = None
    if df is None or len(df) < 6:
        return None, {"symbol": symbol, "error": "unavailable"}

    close = df["Close"]
    vol = df["Volume"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    chg_1d = (last / prev - 1) * 100 if prev else 0.0
    chg_5d = (last / float(close.iloc[-6]) - 1) * 100 if float(close.iloc[-6]) else 0.0
    sma20 = float(close.tail(20).mean())

    score = 0.0
    # Trend vs the 20-day mean
    score += cap * 0.35 if last > sma20 else -cap * 0.35
    # 5-day momentum (≈3 pts per %, capped)
    score += _clamp(chg_5d * 3.0, -cap * 0.4, cap * 0.4)

    # Volume direction — "large buying/selling volume indicates market direction"
    vol_avg = float(vol.tail(20).mean()) if len(vol) >= 20 else float(vol.mean() or 0)
    rel_vol = (float(vol.iloc[-1]) / vol_avg) if vol_avg else 1.0
    if rel_vol >= 1.3 and chg_1d > 0:
        vol_dir = "STRONG BUYING"
        score += cap * 0.3
    elif rel_vol >= 1.3 and chg_1d < 0:
        vol_dir = "STRONG SELLING"
        score -= cap * 0.3
    else:
        vol_dir = "NORMAL"

    score = _clamp(score, -cap, cap)
    return round(score, 1), {
        "symbol": symbol,
        "price": round(last, 2),
        "change_1d": round(chg_1d, 2),
        "change_5d": round(chg_5d, 2),
        "above_sma20": bool(last > sma20),
        "rel_vol": round(rel_vol, 2),
        "vol_direction": vol_dir,
    }


def _vix_component(cap):
    try:
        from shared.yf_fetch import get_history
        df = get_history("^VIX", period="1mo", interval="1d", ttl=900)
    except Exception as e:
        logger.debug(f"gauge: VIX fetch failed: {e}")
        df = None
    if df is None or df.empty:
        return None, {"error": "unavailable"}
    v = float(df["Close"].iloc[-1])
    if v < 15:
        score, level = cap, "very low"
    elif v < 20:
        score, level = cap * 0.5, "low"
    elif v < 25:
        score, level = 0.0, "moderate"
    elif v < 30:
        score, level = -cap * 0.5, "elevated"
    elif v < 35:
        score, level = -cap * 0.8, "high"
    else:
        score, level = -float(cap), "extreme"
    return round(score, 1), {"value": round(v, 2), "level": level}


def _fear_greed(vix_value=None):
    """Compact Fear & Greed fetch: CNN first, Alternative.me (VIX-blended) fallback.
    Mirrors the app.py market-pulse logic but self-contained. Returns dict or None."""
    try:
        import requests as _req
    except Exception:
        return None
    try:
        r = _req.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                     timeout=3, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        if r.status_code == 200:
            fg = r.json().get("fear_and_greed", {})
            sc = fg.get("score")
            if sc is not None:
                return {"score": round(float(sc)), "rating": fg.get("rating") or "", "source": "cnn"}
    except Exception:
        pass
    try:
        r = _req.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if r.status_code == 200:
            items = r.json().get("data", [])
            if items:
                sc = int(items[0].get("value", 50))
                adj = 0
                if vix_value is not None:
                    v = vix_value
                    adj = 15 if v < 17 else 5 if v < 20 else 0 if v < 23 else -15 if v < 30 else -25
                blended = max(0, min(100, sc + adj))
                rating = ("Extreme Greed" if blended >= 75 else "Greed" if blended >= 55
                          else "Neutral" if blended >= 45 else "Fear" if blended >= 25 else "Extreme Fear")
                return {"score": blended, "rating": rating, "source": "blended", "crypto_raw": sc}
    except Exception:
        pass
    return None


def _fg_component(fg, cap):
    if not fg or fg.get("score") is None:
        return None, {"error": "unavailable"}
    s = fg["score"]
    if s >= 75:
        score = cap
    elif s >= 55:
        score = cap * 0.5
    elif s >= 45:
        score = 0.0
    elif s >= 25:
        score = -cap * 0.5
    else:
        score = -float(cap)
    return round(score, 1), {"score": s, "rating": fg.get("rating", ""), "source": fg.get("source")}


def get_market_gauge(force_refresh=False):
    """Return the market risk gauge (cached 15 min).

    Shape::
        {stance: BUY|HOLD|SELL, score: -100..100, color, label,
         components: {spy, nasdaq, vix, fear_greed}, reasons: [...],
         timestamp, cached}
    """
    now = time.time()
    if not force_refresh:
        with _LOCK:
            hit = _CACHE.get("gauge")
        if hit and now - hit[1] < _TTL:
            return {**hit[0], "cached": True}

    components = {}
    reasons = []
    total = 0.0
    weighted_caps = 0.0

    spy_s, spy_m = _index_component("SPY", _CAP_SPY)
    components["spy"] = spy_m
    if spy_s is not None:
        total += spy_s
        weighted_caps += _CAP_SPY
        reasons.append(f"SPY {spy_m['change_5d']:+.1f}% (5d), volume {spy_m['vol_direction'].lower()}")

    ndx_s, ndx_m = _index_component("QQQ", _CAP_NDX)
    components["nasdaq"] = ndx_m
    if ndx_s is not None:
        total += ndx_s
        weighted_caps += _CAP_NDX
        reasons.append(f"Nasdaq (QQQ) {ndx_m['change_5d']:+.1f}% (5d)")

    vix_s, vix_m = _vix_component(_CAP_VIX)
    components["vix"] = vix_m
    if vix_s is not None:
        total += vix_s
        weighted_caps += _CAP_VIX
        reasons.append(f"VIX {vix_m['value']} ({vix_m['level']})")

    vix_val = vix_m.get("value") if isinstance(vix_m, dict) else None
    fg = _fear_greed(vix_val)
    fg_s, fg_m = _fg_component(fg, _CAP_FG)
    components["fear_greed"] = fg_m
    if fg_s is not None:
        total += fg_s
        weighted_caps += _CAP_FG
        reasons.append(f"Fear & Greed {fg_m['score']} ({fg_m['rating']})")

    # Normalize to a -100..100 scale relative to the caps that actually resolved,
    # so a missing component doesn't drag the score toward 0.
    if weighted_caps > 0:
        score = int(round(_clamp(total / weighted_caps * 100, -100, 100)))
        available = True
    else:
        score = 0
        available = False
        reasons.append("Market data unavailable — defaulting to HOLD")

    if not available:
        stance, color, label = "HOLD", "#ffc837", "Hold / Neutral"
    elif score >= BUY_AT:
        stance, color, label = "BUY", "#00c896", "Risk-On / Buy"
    elif score <= SELL_AT:
        stance, color, label = "SELL", "#ff4757", "Risk-Off / Defensive"
    else:
        stance, color, label = "HOLD", "#ffc837", "Hold / Neutral"

    result = {
        "stance": stance,
        "score": score,
        "color": color,
        "label": label,
        "available": available,
        "components": components,
        "reasons": reasons,
        "timestamp": _iso_now(),
        "cached": False,
    }
    with _LOCK:
        _CACHE["gauge"] = (result, now)
    return result


def _iso_now():
    from datetime import datetime
    return datetime.utcnow().isoformat()


def summarize_for_llm(gauge):
    """One compact text block describing market condition for an LLM prompt.
    Returns '' when no gauge is available."""
    if not gauge or not gauge.get("available"):
        return ""
    c = gauge.get("components", {})
    spy = c.get("spy", {})
    ndx = c.get("nasdaq", {})
    vix = c.get("vix", {})
    fg = c.get("fear_greed", {})
    lines = [f"MARKET CONDITION: {gauge['stance']} (gauge {gauge['score']:+d}, {gauge['label']})"]
    if spy and not spy.get("error"):
        lines.append(f"  SPY {spy.get('change_5d')}% 5d, volume {spy.get('vol_direction')}")
    if ndx and not ndx.get("error"):
        lines.append(f"  Nasdaq/QQQ {ndx.get('change_5d')}% 5d, volume {ndx.get('vol_direction')}")
    if vix and not vix.get("error"):
        lines.append(f"  VIX {vix.get('value')} ({vix.get('level')})")
    if fg and not fg.get("error"):
        lines.append(f"  Fear & Greed {fg.get('score')} ({fg.get('rating')})")
    lines.append("  Weigh this macro backdrop: be more selective/defensive when the gauge is SELL, "
                 "more constructive when BUY.")
    return "\n".join(lines)
