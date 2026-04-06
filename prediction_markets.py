"""
Prediction Markets — Polymarket + Kalshi data aggregation.
Fetches public market data (no auth required) for sentiment, major movers, and event tracking.
Cached for 10 minutes to respect rate limits.
"""

import logging
import time
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

# ─── Cache ────────────────────────────────────────────────────
_cache = {}
CACHE_TTL = 600  # 10 minutes

def _get_cached(key):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None

def _set_cached(key, data):
    _cache[key] = (data, time.time())


# ─── Polymarket ───────────────────────────────────────────────

POLY_BASE = "https://gamma-api.polymarket.com"

def _fetch_polymarket_events(limit=30):
    """Fetch top active Polymarket events by 24h volume."""
    try:
        resp = requests.get(f"{POLY_BASE}/events", params={
            "limit": limit,
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
        }, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Polymarket API returned {resp.status_code}")
            return []
        return resp.json() if isinstance(resp.json(), list) else []
    except Exception as e:
        logger.warning(f"Polymarket fetch failed: {e}")
        return []


def _parse_poly_event(event):
    """Parse a Polymarket event into a standardized format."""
    markets = event.get("markets", [])
    if not markets:
        return None

    # Primary market (first one, usually the main Yes/No)
    m = markets[0]
    try:
        prices = eval(m.get("outcomePrices", "[]")) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices", [])
        outcomes = eval(m.get("outcomes", "[]")) if isinstance(m.get("outcomes"), str) else m.get("outcomes", [])
    except Exception:
        prices = []
        outcomes = []

    yes_price = float(prices[0]) if len(prices) > 0 else 0
    no_price = float(prices[1]) if len(prices) > 1 else 0

    # Price changes
    day_change = float(m.get("oneDayPriceChange", 0) or 0)
    week_change = float(m.get("oneWeekPriceChange", 0) or 0)

    return {
        "source": "polymarket",
        "id": event.get("id"),
        "title": event.get("title", m.get("question", "")),
        "category": _classify_category(event.get("title", ""), event.get("category", "")),
        "raw_category": event.get("category", ""),
        "yes_price": round(yes_price * 100, 1),  # Convert to percentage
        "no_price": round(no_price * 100, 1),
        "volume_24h": float(m.get("volume24hr", 0) or 0),
        "volume_total": float(event.get("volume", 0) or 0),
        "liquidity": float(event.get("liquidity", 0) or 0),
        "day_change": round(day_change * 100, 1),  # percentage points
        "week_change": round(week_change * 100, 1),
        "outcomes": outcomes,
        "num_markets": len(markets),
        "image": event.get("image") or m.get("image", ""),
        "slug": event.get("slug", ""),
        "url": f"https://polymarket.com/event/{event.get('slug', '')}",
    }


# ─── Kalshi ───────────────────────────────────────────────────

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

def _fetch_kalshi_events(limit=30):
    """Fetch top active Kalshi events."""
    try:
        resp = requests.get(f"{KALSHI_BASE}/events", params={
            "limit": limit,
            "status": "open",
            "with_nested_markets": "true",
        }, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Kalshi API returned {resp.status_code}")
            return []
        data = resp.json()
        return data.get("events", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.warning(f"Kalshi fetch failed: {e}")
        return []


def _parse_kalshi_event(event):
    """Parse a Kalshi event into standardized format."""
    markets = event.get("markets", [])
    if not markets:
        return None

    # Find market with highest volume
    best = max(markets, key=lambda m: float(m.get("volume_24h_fp", 0) or 0), default=markets[0])

    yes_bid = float(best.get("yes_bid_dollars", 0) or 0)
    yes_ask = float(best.get("yes_ask_dollars", 0) or 0)
    yes_price = (yes_bid + yes_ask) / 2 if yes_bid and yes_ask else yes_bid or yes_ask
    last_price = float(best.get("last_price_dollars", 0) or 0)
    prev_price = float(best.get("previous_price_dollars", 0) or 0)
    day_change = (last_price - prev_price) if last_price and prev_price else 0

    total_vol = sum(float(m.get("volume_24h_fp", 0) or 0) for m in markets)

    return {
        "source": "kalshi",
        "id": event.get("event_ticker"),
        "title": event.get("title", best.get("title", "")),
        "category": _classify_category(event.get("title", ""), event.get("category", "")),
        "raw_category": event.get("category", ""),
        "yes_price": round(yes_price * 100, 1),
        "no_price": round((1 - yes_price) * 100, 1),
        "volume_24h": total_vol,
        "volume_total": sum(float(m.get("volume_fp", 0) or 0) for m in markets),
        "liquidity": sum(float(m.get("liquidity_dollars", 0) or 0) for m in markets),
        "day_change": round(day_change * 100, 1),
        "week_change": 0,  # Kalshi doesn't provide this directly
        "outcomes": [best.get("yes_sub_title", "Yes"), best.get("no_sub_title", "No")],
        "num_markets": len(markets),
        "image": "",
        "slug": event.get("event_ticker", ""),
        "url": f"https://kalshi.com/events/{event.get('event_ticker', '')}",
    }


# ─── Category Classification ─────────────────────────────────

CATEGORY_KEYWORDS = {
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol", "defi", "nft", "token", "blockchain", "altcoin", "memecoin", "stablecoin"],
    "oil_energy": ["oil", "crude", "wti", "brent", "opec", "natural gas", "energy", "gasoline", "petroleum"],
    "politics": ["trump", "biden", "election", "president", "congress", "senate", "governor", "democrat", "republican", "gop", "house", "impeach", "cabinet", "scotus", "supreme court", "political", "vance", "desantis"],
    "economy": ["fed", "federal reserve", "rate cut", "rate hike", "interest rate", "inflation", "cpi", "gdp", "unemployment", "recession", "tariff", "trade war", "debt ceiling", "treasury"],
    "markets": ["s&p", "spy", "nasdaq", "dow", "stock market", "ipo", "earnings", "market cap", "index", "russell", "volatility", "vix"],
    "geopolitics": ["war", "ukraine", "russia", "china", "taiwan", "nato", "iran", "israel", "gaza", "sanctions", "missile", "military", "ceasefire", "peace"],
    "tech": ["ai", "artificial intelligence", "openai", "google", "apple", "microsoft", "meta", "tesla", "nvidia", "chips", "semiconductor", "tiktok", "regulation"],
    "climate": ["climate", "hurricane", "earthquake", "wildfire", "weather", "temperature", "flood", "storm"],
}

def _classify_category(title, raw_category=""):
    """Classify an event into our categories based on title keywords."""
    text = (title + " " + raw_category).lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[cat] = score
    if scores:
        return max(scores, key=scores.get)
    return "other"


# ─── Public API ───────────────────────────────────────────────

def get_prediction_markets(force_refresh=False):
    """Get combined prediction market data from Polymarket + Kalshi.
    Returns categorized events sorted by 24h volume."""

    if not force_refresh:
        cached = _get_cached("prediction_markets")
        if cached:
            return {**cached, "cached": True}

    # Fetch from both sources in parallel-ish (sequential for simplicity)
    poly_events = _fetch_polymarket_events(40)
    kalshi_events = _fetch_kalshi_events(40)

    all_events = []

    for e in poly_events:
        parsed = _parse_poly_event(e)
        if parsed and parsed["yes_price"] > 0:
            all_events.append(parsed)

    for e in kalshi_events:
        parsed = _parse_kalshi_event(e)
        if parsed and parsed["yes_price"] > 0:
            all_events.append(parsed)

    # Sort by 24h volume
    all_events.sort(key=lambda x: x["volume_24h"], reverse=True)

    # Group by category
    by_category = {}
    for e in all_events:
        cat = e["category"]
        by_category.setdefault(cat, []).append(e)

    # Major movers: biggest absolute day_change
    movers = sorted(
        [e for e in all_events if abs(e["day_change"]) >= 2],
        key=lambda x: abs(x["day_change"]),
        reverse=True
    )[:10]

    # Sentiment gauge: aggregate political/economic/market events
    sentiment = _compute_sentiment(all_events)

    result = {
        "events": all_events[:50],  # Top 50 by volume
        "by_category": {k: v[:10] for k, v in by_category.items()},  # Top 10 per category
        "movers": movers,
        "sentiment": sentiment,
        "sources": {
            "polymarket": len([e for e in all_events if e["source"] == "polymarket"]),
            "kalshi": len([e for e in all_events if e["source"] == "kalshi"]),
        },
        "total": len(all_events),
        "updated_at": datetime.now().isoformat(),
        "cached": False,
    }

    _set_cached("prediction_markets", result)
    return result


def _compute_sentiment(events):
    """Compute an overall market sentiment from prediction market data.

    Logic:
    - Bullish signals: high YES on positive economic events, low YES on recession/crash
    - Bearish signals: high YES on recession/crash, low YES on positive outcomes
    """
    bullish_score = 0
    bearish_score = 0
    signals = []

    for e in events:
        title = e["title"].lower()
        yes = e["yes_price"]

        # Recession/crash indicators (high YES = bearish)
        if any(kw in title for kw in ["recession", "crash", "bear market", "downturn", "default"]):
            if yes > 50:
                bearish_score += 2
                signals.append({"text": f"Recession risk: {yes}% YES", "type": "bearish"})
            elif yes > 30:
                bearish_score += 1
            else:
                bullish_score += 1

        # Rate cuts (high YES = generally bullish for equities)
        elif any(kw in title for kw in ["rate cut", "lower rate", "cut rate"]):
            if yes > 60:
                bullish_score += 1
                signals.append({"text": f"Rate cut expected: {yes}%", "type": "bullish"})

        # Rate hikes (bearish for equities)
        elif any(kw in title for kw in ["rate hike", "raise rate", "higher rate"]):
            if yes > 50:
                bearish_score += 1
                signals.append({"text": f"Rate hike risk: {yes}%", "type": "bearish"})

        # S&P/market targets (bullish if high probability of going up)
        elif any(kw in title for kw in ["s&p above", "s&p 500 above", "spy above", "new high", "all-time high", "ath"]):
            if yes > 60:
                bullish_score += 2
                signals.append({"text": f"Market upside: {yes}%", "type": "bullish"})
            elif yes < 30:
                bearish_score += 1

        # BTC targets
        elif "btc" in title or "bitcoin" in title:
            if any(kw in title for kw in ["above", "over", "reach", "hit"]) and yes > 55:
                bullish_score += 1
                signals.append({"text": f"BTC bullish: {yes}%", "type": "bullish"})

        # Tariff/trade war (generally bearish)
        elif any(kw in title for kw in ["tariff", "trade war", "sanctions"]):
            if yes > 60:
                bearish_score += 1
                signals.append({"text": f"Trade tension: {yes}%", "type": "bearish"})

        # Geopolitical risk (bearish)
        elif any(kw in title for kw in ["war", "invasion", "escalat", "nuclear", "conflict"]):
            if yes > 40:
                bearish_score += 1

        # Positive catalysts
        elif any(kw in title for kw in ["stimulus", "infrastructure", "spending bill"]):
            if yes > 50:
                bullish_score += 1

        # Big movers contribute to overall direction
        if abs(e["day_change"]) >= 5:
            if e["day_change"] > 0:
                bullish_score += 0.5
            else:
                bearish_score += 0.5

    # Compute net score
    net = bullish_score - bearish_score
    total = max(bullish_score + bearish_score, 1)

    if net >= 3:
        mood = "BULLISH"
        color = "#00c896"
        desc = "Prediction markets lean bullish — bettors expect upside"
    elif net >= 1:
        mood = "LEAN BULL"
        color = "#8bc34a"
        desc = "Slight bullish tilt in prediction markets"
    elif net >= -1:
        mood = "NEUTRAL"
        color = "#ffc837"
        desc = "Mixed signals — no clear directional consensus"
    elif net >= -3:
        mood = "LEAN BEAR"
        color = "#ff8c42"
        desc = "Slight bearish tilt — elevated risk priced in"
    else:
        mood = "BEARISH"
        color = "#ff4757"
        desc = "Prediction markets lean bearish — bettors pricing risk"

    return {
        "mood": mood,
        "color": color,
        "description": desc,
        "score": round(net, 1),
        "bullish_signals": round(bullish_score, 1),
        "bearish_signals": round(bearish_score, 1),
        "signals": signals[:5],  # Top 5 signals
    }


def get_poly_sentiment_for_pulse():
    """Quick sentiment gauge for the market pulse strip (lightweight)."""
    data = get_prediction_markets()
    return data.get("sentiment", {"mood": "—", "color": "#636b7e", "description": "", "score": 0})
