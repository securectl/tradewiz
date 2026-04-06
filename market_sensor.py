"""
Market Sensor — Pre-scan market health check for trading bots.

Fetches broad market indicators (BTC/ETH for crypto, SPY/QQQ/VIX for stocks),
uses LLM to classify overall market health as HEALTHY / CAUTION / DANGER.
Results are cached for 30 minutes to avoid repeated LLM calls.
"""

import os
import json
import logging
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Config
BOT_SENSOR_ENABLED = os.getenv("BOT_SENSOR_ENABLED", "1") == "1"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_BOT_SENTIMENT = os.getenv("LLM_BOT_SENTIMENT", "google/gemini-2.5-flash")

CACHE_TTL = 1800  # 30 minutes

# Cache: {"crypto": {...}, "stock": {...}}
_cache = {}


def check_market_health(market_type: str) -> dict:
    """
    Check overall market health before scanning individual assets.

    Args:
        market_type: "crypto" or "stock"

    Returns:
        dict with status (HEALTHY/CAUTION/DANGER), reasoning, key_indicators, timestamp, cached
    """
    if not BOT_SENSOR_ENABLED:
        return {
            "status": "HEALTHY",
            "market_type": market_type,
            "reasoning": "Market sensor disabled",
            "key_indicators": {},
            "timestamp": datetime.now().isoformat(),
            "cached": False,
        }

    # Check cache
    cached = _cache.get(market_type)
    if cached and (time.time() - cached["_cache_time"]) < CACHE_TTL:
        result = {k: v for k, v in cached.items() if k != "_cache_time"}
        result["cached"] = True
        return result

    try:
        if market_type == "crypto":
            indicators = _fetch_crypto_indicators()
        else:
            indicators = _fetch_stock_indicators()

        if not indicators:
            logger.warning(f"Market sensor: Failed to fetch {market_type} indicators — defaulting HEALTHY")
            return _default_result(market_type, "Failed to fetch market data")

        result = _assess_health_llm(market_type, indicators)

        # Cache the result
        result["_cache_time"] = time.time()
        _cache[market_type] = result

        out = {k: v for k, v in result.items() if k != "_cache_time"}
        out["cached"] = False
        return out

    except Exception as e:
        logger.error(f"Market sensor error ({market_type}): {e}")
        return _default_result(market_type, f"Sensor error: {e}")


def _default_result(market_type: str, reasoning: str) -> dict:
    return {
        "status": "HEALTHY",
        "market_type": market_type,
        "reasoning": reasoning,
        "key_indicators": {},
        "timestamp": datetime.now().isoformat(),
        "cached": False,
    }


def _fetch_crypto_indicators() -> dict:
    """Fetch BTC + ETH price action and calculate key metrics."""
    try:
        import yfinance as yf

        indicators = {}
        for symbol, name in [("BTC-USD", "btc"), ("ETH-USD", "eth")]:
            ticker = yf.Ticker(symbol)

            # 5-day hourly data for short-term momentum
            df_5d = ticker.history(period="5d", interval="1h")
            if df_5d.empty or len(df_5d) < 10:
                continue

            current = float(df_5d["Close"].iloc[-1])
            open_1d = float(df_5d["Close"].iloc[-24]) if len(df_5d) >= 24 else float(df_5d["Close"].iloc[0])
            open_5d = float(df_5d["Close"].iloc[0])

            change_1d = ((current - open_1d) / open_1d) * 100
            change_5d = ((current - open_5d) / open_5d) * 100

            # Simple RSI approximation from recent closes
            closes = df_5d["Close"].values[-14:]
            if len(closes) >= 2:
                deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
                gains = [d for d in deltas if d > 0]
                losses = [-d for d in deltas if d < 0]
                avg_gain = sum(gains) / len(deltas) if gains else 0
                avg_loss = sum(losses) / len(deltas) if losses else 0.001
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            else:
                rsi = 50

            # Volume trend
            recent_vol = df_5d["Volume"].iloc[-24:].mean() if len(df_5d) >= 24 else df_5d["Volume"].mean()
            older_vol = df_5d["Volume"].iloc[:-24].mean() if len(df_5d) > 24 else recent_vol
            vol_ratio = recent_vol / older_vol if older_vol > 0 else 1.0

            indicators[f"{name}_price"] = round(current, 2)
            indicators[f"{name}_change_24h"] = round(change_1d, 2)
            indicators[f"{name}_change_5d"] = round(change_5d, 2)
            indicators[f"{name}_rsi"] = round(rsi, 1)
            indicators[f"{name}_vol_ratio"] = round(vol_ratio, 2)

        return indicators if indicators else None

    except Exception as e:
        logger.error(f"Failed to fetch crypto indicators: {e}")
        return None


def _fetch_stock_indicators() -> dict:
    """Fetch SPY + QQQ + VIX price action and calculate key metrics."""
    try:
        import yfinance as yf

        indicators = {}
        for symbol, name in [("SPY", "spy"), ("QQQ", "qqq")]:
            ticker = yf.Ticker(symbol)
            df_5d = ticker.history(period="5d", interval="1h")
            if df_5d.empty or len(df_5d) < 5:
                continue

            current = float(df_5d["Close"].iloc[-1])
            open_1d = float(df_5d["Close"].iloc[-7]) if len(df_5d) >= 7 else float(df_5d["Close"].iloc[0])
            open_5d = float(df_5d["Close"].iloc[0])

            change_1d = ((current - open_1d) / open_1d) * 100
            change_5d = ((current - open_5d) / open_5d) * 100

            indicators[f"{name}_price"] = round(current, 2)
            indicators[f"{name}_change_1d"] = round(change_1d, 2)
            indicators[f"{name}_change_5d"] = round(change_5d, 2)

        # VIX
        try:
            vix = yf.Ticker("^VIX")
            vix_df = vix.history(period="5d")
            if not vix_df.empty:
                indicators["vix"] = round(float(vix_df["Close"].iloc[-1]), 2)
                vix_prev = float(vix_df["Close"].iloc[0])
                indicators["vix_change_5d"] = round(((indicators["vix"] - vix_prev) / vix_prev) * 100, 2)
        except Exception:
            pass

        return indicators if indicators else None

    except Exception as e:
        logger.error(f"Failed to fetch stock indicators: {e}")
        return None


def _assess_health_llm(market_type: str, indicators: dict) -> dict:
    """Use LLM to classify market health based on indicators."""
    if not OPENROUTER_API_KEY:
        # No API key — use rule-based fallback
        return _assess_health_rules(market_type, indicators)

    if market_type == "crypto":
        context = (
            f"Bitcoin: ${indicators.get('btc_price', '?')} (24h: {indicators.get('btc_change_24h', '?')}%, "
            f"5d: {indicators.get('btc_change_5d', '?')}%, RSI: {indicators.get('btc_rsi', '?')})\n"
            f"Ethereum: ${indicators.get('eth_price', '?')} (24h: {indicators.get('eth_change_24h', '?')}%, "
            f"5d: {indicators.get('eth_change_5d', '?')}%, RSI: {indicators.get('eth_rsi', '?')})"
        )
    else:
        context = (
            f"SPY: ${indicators.get('spy_price', '?')} (1d: {indicators.get('spy_change_1d', '?')}%, "
            f"5d: {indicators.get('spy_change_5d', '?')}%)\n"
            f"QQQ: ${indicators.get('qqq_price', '?')} (1d: {indicators.get('qqq_change_1d', '?')}%, "
            f"5d: {indicators.get('qqq_change_5d', '?')}%)\n"
            f"VIX: {indicators.get('vix', '?')} (5d change: {indicators.get('vix_change_5d', '?')}%)"
        )

    prompt = (
        f"You are a market health sensor for an automated trading bot. "
        f"Classify the current {market_type} market condition.\n\n"
        f"Market Data:\n{context}\n\n"
        f"Classify as exactly one of:\n"
        f"- HEALTHY: Normal conditions, safe to trade\n"
        f"- CAUTION: Elevated risk, only high-confidence trades should proceed\n"
        f"- DANGER: Severe downturn or crash conditions, ALL trades should be blocked\n\n"
        f"Respond ONLY with valid JSON:\n"
        f'{{"status": "HEALTHY|CAUTION|DANGER", "reasoning": "brief 1-sentence explanation"}}'
    )

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_BOT_SENTIMENT,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.1,
            },
            timeout=15,
        )

        if resp.status_code != 200:
            logger.warning(f"Market sensor LLM returned {resp.status_code}")
            return _assess_health_rules(market_type, indicators)

        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Parse JSON from response (handle markdown code blocks)
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        parsed = json.loads(content)
        status = parsed.get("status", "HEALTHY").upper()
        if status not in ("HEALTHY", "CAUTION", "DANGER"):
            status = "HEALTHY"

        return {
            "status": status,
            "market_type": market_type,
            "reasoning": parsed.get("reasoning", "LLM assessment"),
            "key_indicators": indicators,
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.warning(f"Market sensor LLM error: {e} — falling back to rules")
        return _assess_health_rules(market_type, indicators)


def _assess_health_rules(market_type: str, indicators: dict) -> dict:
    """Rule-based fallback when LLM is unavailable."""
    status = "HEALTHY"
    reasons = []

    if market_type == "crypto":
        btc_24h = indicators.get("btc_change_24h", 0)
        eth_24h = indicators.get("eth_change_24h", 0)
        btc_5d = indicators.get("btc_change_5d", 0)

        # PANIC: catastrophic crash — auto-liquidate all positions
        if btc_24h < -15 or eth_24h < -20 or btc_5d < -25:
            status = "PANIC"
            reasons.append(f"MARKET CRASH: BTC {btc_24h:+.1f}%, ETH {eth_24h:+.1f}% (24h), BTC {btc_5d:+.1f}% (5d) — AUTO-LIQUIDATING")
        elif btc_24h < -8 or eth_24h < -10:
            status = "DANGER"
            reasons.append(f"Severe drop: BTC {btc_24h:+.1f}%, ETH {eth_24h:+.1f}% (24h)")
        elif btc_24h < -5 or eth_24h < -7 or btc_5d < -12:
            status = "CAUTION"
            reasons.append(f"Elevated risk: BTC {btc_24h:+.1f}%/24h, {btc_5d:+.1f}%/5d")
    else:
        spy_1d = indicators.get("spy_change_1d", 0)
        qqq_1d = indicators.get("qqq_change_1d", 0)
        vix = indicators.get("vix", 20)

        # PANIC: market crash — auto-liquidate all positions
        if spy_1d < -5 or qqq_1d < -6 or vix > 45:
            status = "PANIC"
            reasons.append(f"MARKET CRASH: SPY {spy_1d:+.1f}%, QQQ {qqq_1d:+.1f}%, VIX {vix} — AUTO-LIQUIDATING")
        elif spy_1d < -3 or qqq_1d < -4 or vix > 35:
            status = "DANGER"
            reasons.append(f"Severe: SPY {spy_1d:+.1f}%, QQQ {qqq_1d:+.1f}%, VIX {vix}")
        elif spy_1d < -2 or qqq_1d < -2.5 or vix > 28:
            status = "CAUTION"
            reasons.append(f"Elevated: SPY {spy_1d:+.1f}%, QQQ {qqq_1d:+.1f}%, VIX {vix}")

    return {
        "status": status,
        "market_type": market_type,
        "reasoning": "; ".join(reasons) if reasons else "Market conditions normal (rule-based)",
        "key_indicators": indicators,
        "timestamp": datetime.now().isoformat(),
    }
