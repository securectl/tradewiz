"""
Trump Mood Gauge — Tracks presidential social media & statements to predict market impact.
Sources: Truth Social (via TrumpsTruth.org RSS), GDELT news, White House briefings.
Uses 3-day pattern analysis to gauge how markets will react.

Market-moving keywords are scored and weighted to produce a -100 to +100 mood index.
"""

import json
import logging
import time
import re
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ─── Cache ────────────────────────────────────────────────────
_cache = {}
CACHE_TTL = 3600  # 1 hour
AI_PREDICTION_TTL = 43200  # 12 hours — shared across all users

def _get_cached(key, ttl=None):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < (ttl or CACHE_TTL):
            return data
    return None

def _set_cached(key, data):
    _cache[key] = (data, time.time())


# ─── Keyword Scoring Matrix ──────────────────────────────────
# Positive scores = bullish for markets, negative = bearish
# Based on historical market reactions to Trump statements

BULLISH_KEYWORDS = {
    # Trade/tariff de-escalation
    "deal": 3, "agreement": 3, "trade deal": 5, "negotiate": 2, "talks going well": 5,
    "great relationship": 3, "tremendous progress": 3, "pause tariff": 5, "reduce tariff": 5,
    "remove tariff": 5, "tariff delay": 4, "exemption": 3, "waiver": 3,
    # Tax/stimulus
    "tax cut": 5, "tax reform": 4, "stimulus": 4, "infrastructure": 3, "jobs": 2,
    "record economy": 3, "booming": 3, "greatest economy": 4, "strong economy": 3,
    # Deregulation
    "deregulation": 3, "cut regulation": 3, "executive order": 2, "unlock": 2,
    # Market positive
    "stock market": 2, "record high": 4, "all time high": 4, "markets up": 3,
    "dow": 2, "s&p": 2, "nasdaq": 2, "rally": 2,
    # Crypto positive
    "bitcoin": 2, "crypto": 2, "digital assets": 3, "strategic reserve": 4,
    # Fed dovish
    "rate cut": 3, "lower rates": 3, "fed should cut": 3,
}

BEARISH_KEYWORDS = {
    # Trade war escalation
    "tariff": -3, "tariffs": -3, "new tariff": -5, "raise tariff": -5, "increase tariff": -5,
    "reciprocal": -3, "reciprocal tariff": -5, "trade war": -5, "sanctions": -3, "ban": -2, "restrict": -2,
    "retaliate": -4, "punish": -3, "penalty": -3, "surcharge": -4,
    "liberation day": -5, "tariff rate": -4, "import tax": -4, "duty": -3, "duties": -3,
    "10%": -2, "20%": -3, "25%": -4, "34%": -5, "46%": -5, "50%": -5, "100%": -5, "145%": -5,
    "baseline tariff": -4, "universal tariff": -5, "global tariff": -5, "across the board": -3,
    "trade deficit": -2, "unfair trade": -3, "cheating": -2, "ripping us off": -3,
    "threaten": -3, "threatening": -3,
    # Geopolitical / Military tension
    "china": -1, "china tariff": -4, "china trade": -3, "mexico": -1, "mexico tariff": -3,
    "eu tariff": -3, "canada tariff": -3, "nato": -1,
    "war": -4, "military": -4, "threat": -3, "shutdown": -3, "emergency": -3,
    # Iran conflict (major oil/market mover)
    "iran": -4, "iran deal": -3, "hormuz": -5, "strait": -3,
    "bomb": -5, "bombing": -5, "strike": -4, "strikes": -4, "airstrike": -5,
    "destroy": -5, "destroying": -5, "destruction": -5,
    "invasion": -5, "invade": -5, "attack": -4, "attacked": -4,
    "ceasefire": -2, "retaliation": -5, "escalation": -5, "escalate": -4,
    "hell": -4, "all hell": -5, "reign down": -5, "nothing left": -5,
    "nuclear": -5, "missile": -5, "missiles": -5, "troops": -4,
    "bridge": -2, "power plant": -3, "infrastructure": -1,
    "regime": -2, "new regime": -2, "too late": -3, "before it is too late": -4,
    "oil shock": -5, "oil price": -3, "oil spike": -5,
    # General military / geopolitical
    "deploy": -3, "deployed": -3, "carrier": -3, "navy": -2, "pentagon": -2,
    "conflict": -4, "tensions": -3, "hostile": -3, "aggression": -4,
    # Market negative
    "crash": -4, "bubble": -3, "overvalued": -3, "disaster": -3, "catastrophe": -4,
    "selloff": -4, "sell-off": -4, "plunge": -4, "tumble": -3, "tank": -3,
    # Fed hawkish pressure
    "rate hike": -3, "inflation": -2, "fed is wrong": -2,
    # Uncertainty
    "maybe": -1, "we'll see": -2, "considering": -1, "looking at": -1,
    "announce soon": -2, "big announcement": -2,
}

# Geopolitical context words — if these appear, suppress bullish "deal" scoring
WAR_CONTEXT_WORDS = {
    "iran", "military", "bomb", "bombing", "destroy", "destroying", "war",
    "attack", "strike", "strikes", "airstrike", "hell", "missile", "troops",
    "invasion", "nuclear", "hormuz", "bridge", "ceasefire", "regime",
    "navy", "carrier", "pentagon", "conflict",
}

# Countries/sectors that amplify tariff/geopolitical impact
TARIFF_AMPLIFIERS = {
    "china": 2.0, "eu": 1.5, "europe": 1.5, "mexico": 1.3, "canada": 1.3,
    "iran": 2.0, "russia": 1.5, "north korea": 1.5,
    "auto": 1.5, "semiconductor": 1.8, "steel": 1.3, "aluminum": 1.2,
    "pharmaceutical": 1.4, "tech": 1.5, "oil": 1.8, "energy": 1.3,
}


# ─── Policy Signal Map (for backtrack detection) ─────────────
# Maps policy areas to the keywords that indicate activity in that area.
# "bearish" keywords = escalation, "bullish" keywords = de-escalation / reversal

POLICY_SIGNAL_MAP = {
    "china_tariffs": {
        "escalation": ["china tariff", "china trade", "china", "145%", "34%"],
        "de_escalation": ["trade deal", "negotiate", "talks going well", "pause tariff", "exemption", "waiver", "reduce tariff", "remove tariff", "tariff delay"],
    },
    "eu_tariffs": {
        "escalation": ["eu tariff", "europe"],
        "de_escalation": ["trade deal", "negotiate", "exemption", "pause tariff"],
    },
    "mexico_tariffs": {
        "escalation": ["mexico tariff", "mexico"],
        "de_escalation": ["trade deal", "negotiate", "exemption", "pause tariff"],
    },
    "canada_tariffs": {
        "escalation": ["canada tariff", "canada"],
        "de_escalation": ["trade deal", "negotiate", "exemption", "pause tariff"],
    },
    "general_tariffs": {
        "escalation": ["tariff", "tariffs", "new tariff", "baseline tariff", "universal tariff", "reciprocal tariff", "global tariff", "raise tariff", "increase tariff"],
        "de_escalation": ["pause tariff", "tariff delay", "exemption", "waiver", "reduce tariff", "remove tariff", "trade deal"],
    },
    "iran_conflict": {
        "escalation": ["iran", "bomb", "bombing", "airstrike", "hormuz", "missile", "strike", "destroy"],
        "de_escalation": ["iran deal", "negotiate", "ceasefire", "talks going well", "deal"],
    },
    "crypto_policy": {
        "escalation": ["ban", "restrict"],
        "de_escalation": ["bitcoin", "crypto", "digital assets", "strategic reserve"],
    },
    "tax_policy": {
        "escalation": [],
        "de_escalation": ["tax cut", "tax reform", "stimulus"],
    },
    "fed_policy": {
        "escalation": ["rate hike", "fed is wrong", "inflation"],
        "de_escalation": ["rate cut", "lower rates", "fed should cut"],
    },
}

# Known reversal keyword pairs: (escalation keyword, de-escalation keyword)
REVERSAL_PAIRS = [
    ("new tariff", "pause tariff"), ("new tariff", "tariff delay"), ("new tariff", "exemption"),
    ("raise tariff", "reduce tariff"), ("raise tariff", "remove tariff"), ("raise tariff", "pause tariff"),
    ("increase tariff", "reduce tariff"), ("trade war", "trade deal"), ("trade war", "negotiate"),
    ("reciprocal tariff", "pause tariff"), ("reciprocal tariff", "exemption"),
    ("universal tariff", "pause tariff"), ("global tariff", "pause tariff"),
    ("tariff", "pause tariff"), ("tariff", "tariff delay"), ("tariff", "exemption"),
    ("sanctions", "trade deal"), ("ban", "exemption"),
    ("bomb", "ceasefire"), ("airstrike", "ceasefire"), ("strike", "ceasefire"),
    ("rate hike", "rate cut"),
]

BACKTRACK_CACHE_TTL = 21600        # 6 hours
BACKTRACK_PREDICTION_TTL = 43200   # 12 hours

# ─── Sector Signal Map ──────────────────────────────────────
# Maps policy areas / keywords to affected market sectors + tickers
# Used to derive BUY / AVOID signals from Trump rhetoric

SECTOR_SIGNAL_MAP = {
    "china_tariffs": {
        "avoid": [
            {"sector": "Semiconductors", "tickers": ["NVDA", "AMD", "AVGO", "QCOM", "TSM"], "reason": "China tariffs hit chip supply chains"},
            {"sector": "Consumer Electronics", "tickers": ["AAPL", "DELL", "HPQ"], "reason": "Manufacturing exposure to China"},
            {"sector": "Industrials (China-exposed)", "tickers": ["CAT", "DE", "BA"], "reason": "Revenue exposure to China trade"},
        ],
        "buy": [
            {"sector": "Domestic Manufacturing", "tickers": ["X", "NUE", "STLD"], "reason": "Tariffs protect domestic steel/mfg"},
            {"sector": "US Reshoring", "tickers": ["GE", "HON", "RTX"], "reason": "Onshoring benefits from China decoupling"},
        ],
    },
    "eu_tariffs": {
        "avoid": [
            {"sector": "European Luxury/Auto", "tickers": ["RACE", "POAHY", "BMWYY"], "reason": "EU tariffs hit European imports"},
            {"sector": "US Exporters to EU", "tickers": ["BA", "GE", "KO"], "reason": "EU retaliation risk on US exports"},
        ],
        "buy": [
            {"sector": "Domestic Auto", "tickers": ["F", "GM"], "reason": "Tariffs reduce EU auto competition"},
        ],
    },
    "mexico_tariffs": {
        "avoid": [
            {"sector": "Auto (Mexico supply chain)", "tickers": ["GM", "F", "STLA"], "reason": "Parts sourced from Mexico"},
            {"sector": "Agriculture", "tickers": ["ADM", "BG", "DAR"], "reason": "Mexico retaliation on US ag exports"},
        ],
        "buy": [
            {"sector": "Domestic Auto Parts", "tickers": ["AXL", "BWA", "DAN"], "reason": "Reshoring auto supply chain"},
        ],
    },
    "canada_tariffs": {
        "avoid": [
            {"sector": "Energy (Canadian oil)", "tickers": ["SU", "CNQ", "IMO"], "reason": "Tariffs on Canadian crude imports"},
            {"sector": "Lumber/Materials", "tickers": ["WFG", "WY"], "reason": "Canadian lumber tariff impact"},
        ],
        "buy": [
            {"sector": "US Energy Producers", "tickers": ["XOM", "CVX", "OXY"], "reason": "Domestic energy benefits from import tariffs"},
        ],
    },
    "general_tariffs": {
        "avoid": [
            {"sector": "Retail/Consumer", "tickers": ["WMT", "TGT", "COST", "AMZN"], "reason": "Broad tariffs raise import costs"},
            {"sector": "Technology", "tickers": ["AAPL", "MSFT", "GOOGL"], "reason": "Global supply chain disruption"},
        ],
        "buy": [
            {"sector": "Domestic Materials", "tickers": ["NUE", "X", "CLF"], "reason": "Steel/aluminum tariff protection"},
            {"sector": "Treasury/Bonds", "tickers": ["TLT", "IEF", "SHY"], "reason": "Flight to safety on trade war fears"},
        ],
    },
    "iran_conflict": {
        "avoid": [
            {"sector": "Airlines", "tickers": ["DAL", "UAL", "AAL", "LUV"], "reason": "Oil price spike + travel disruption risk"},
            {"sector": "Consumer Discretionary", "tickers": ["SBUX", "MCD", "NKE"], "reason": "Consumer pullback on geopolitical fear"},
        ],
        "buy": [
            {"sector": "Defense", "tickers": ["LMT", "RTX", "NOC", "GD", "BA"], "reason": "Military escalation boosts defense spending"},
            {"sector": "Oil & Energy", "tickers": ["XOM", "CVX", "OXY", "COP"], "reason": "Oil prices surge on Strait of Hormuz risk"},
            {"sector": "Gold/Safe Haven", "tickers": ["GLD", "GDX", "NEM", "GOLD"], "reason": "Flight to safety on war risk"},
        ],
    },
    "crypto_policy": {
        "avoid": [],
        "buy": [
            {"sector": "Crypto & Blockchain", "tickers": ["COIN", "MARA", "RIOT", "MSTR"], "reason": "Pro-crypto policy / strategic reserve"},
        ],
    },
    "tax_policy": {
        "avoid": [],
        "buy": [
            {"sector": "Small-Cap Growth", "tickers": ["IWM", "SCHA"], "reason": "Tax cuts disproportionately help small caps"},
            {"sector": "Banks/Financials", "tickers": ["JPM", "BAC", "GS", "MS"], "reason": "Deregulation + tax cuts boost financials"},
        ],
    },
    "fed_policy": {
        "avoid": [
            {"sector": "REITs (on rate hike)", "tickers": ["VNQ", "O", "SPG"], "reason": "Higher rates pressure rate-sensitive sectors"},
        ],
        "buy": [
            {"sector": "Growth Tech (on rate cut)", "tickers": ["QQQ", "ARKK", "SOFI"], "reason": "Rate cuts boost growth stock valuations"},
            {"sector": "Homebuilders (on rate cut)", "tickers": ["DHI", "LEN", "TOL"], "reason": "Lower rates drive housing demand"},
        ],
    },
}


def _derive_trade_signals(top_signals, mood, pattern):
    """Derive actionable BUY/AVOID sector signals from current Trump rhetoric.

    Analyzes top_signals against POLICY_SIGNAL_MAP and SECTOR_SIGNAL_MAP to produce
    sector-level recommendations with confidence and reasoning.

    Returns: {"buy": [...], "avoid": [...], "summary": str}
    """
    buy_signals = []
    avoid_signals = []
    active_policies = set()

    if not top_signals:
        return {"buy": [], "avoid": [], "summary": "No active signals detected."}

    # Identify which policy areas are active and their direction
    for sig in top_signals:
        kw = sig.get("text", "").lower().replace(" (ultimatum)", "")
        sig_type = sig.get("type", "")
        sig_score = abs(sig.get("score", 0))

        for policy, dirs in POLICY_SIGNAL_MAP.items():
            matched = False
            direction = None

            for esc_kw in dirs["escalation"]:
                if esc_kw in kw:
                    direction = "escalation"
                    matched = True
                    break
            if not matched:
                for de_kw in dirs["de_escalation"]:
                    if de_kw in kw:
                        direction = "de_escalation"
                        matched = True
                        break

            if not matched:
                continue

            active_policies.add(policy)
            sector_data = SECTOR_SIGNAL_MAP.get(policy, {})

            if direction == "escalation":
                # Escalation → AVOID the exposed sectors, BUY the beneficiaries
                for item in sector_data.get("avoid", []):
                    avoid_signals.append({
                        "sector": item["sector"],
                        "tickers": item["tickers"],
                        "reason": item["reason"],
                        "policy": policy,
                        "signal": kw,
                        "strength": min(sig_score / 5, 1.0),  # normalize to 0-1
                    })
                for item in sector_data.get("buy", []):
                    buy_signals.append({
                        "sector": item["sector"],
                        "tickers": item["tickers"],
                        "reason": item["reason"],
                        "policy": policy,
                        "signal": kw,
                        "strength": min(sig_score / 5, 1.0),
                    })
            else:
                # De-escalation → previously-hit sectors become BUY, beneficiaries fade
                for item in sector_data.get("avoid", []):
                    buy_signals.append({
                        "sector": item["sector"],
                        "tickers": item["tickers"],
                        "reason": f"De-escalation: {item['reason'].replace('hit', 'recovering from')}",
                        "policy": policy,
                        "signal": kw,
                        "strength": min(sig_score / 5, 1.0) * 0.8,  # slightly less confident on reversal
                    })

    # Deduplicate by sector, keep highest strength
    def _dedup(signals):
        seen = {}
        for s in signals:
            key = s["sector"]
            if key not in seen or s["strength"] > seen[key]["strength"]:
                seen[key] = s
        return sorted(seen.values(), key=lambda x: x["strength"], reverse=True)

    buy_signals = _dedup(buy_signals)
    avoid_signals = _dedup(avoid_signals)

    # Remove sectors that appear in both buy and avoid (conflicting signals)
    buy_sectors = {s["sector"] for s in buy_signals}
    avoid_sectors = {s["sector"] for s in avoid_signals}
    conflict = buy_sectors & avoid_sectors
    if conflict:
        buy_signals = [s for s in buy_signals if s["sector"] not in conflict]
        avoid_signals = [s for s in avoid_signals if s["sector"] not in conflict]

    # Build summary
    trend = pattern.get("trend", "stable") if pattern else "stable"
    if mood >= 10 and trend == "improving":
        summary = "Bullish rhetoric + improving trend — favor risk-on positions."
    elif mood >= 10:
        summary = "Positive rhetoric detected — lean into beneficiary sectors."
    elif mood <= -10 and trend == "deteriorating":
        summary = "Bearish rhetoric escalating — reduce exposure to threatened sectors."
    elif mood <= -10:
        summary = "Negative rhetoric detected — watch threatened sectors for further downside."
    else:
        summary = "Mixed signals — selective positioning recommended."

    return {
        "buy": buy_signals[:6],
        "avoid": avoid_signals[:6],
        "summary": summary,
        "active_policies": list(active_policies),
    }


# ─── Data Fetchers ────────────────────────────────────────────

def _fetch_truth_social(days=3):
    """Fetch Trump's Truth Social posts via TrumpsTruth.org RSS."""
    posts = []
    try:
        import feedparser
        end = datetime.now()
        start = end - timedelta(days=days)
        url = f"https://www.trumpstruth.org/feed?start_date={start.strftime('%Y-%m-%d')}&end_date={end.strftime('%Y-%m-%d')}"

        feed = feedparser.parse(url)
        for entry in feed.entries[:50]:
            text = entry.get("title", "") + " " + entry.get("description", "")
            # Strip HTML tags
            text = re.sub(r'<[^>]+>', '', text).strip()
            if not text or text == "[No Title]":
                continue
            pub_date = entry.get("published", "")
            posts.append({
                "source": "truth_social",
                "text": text[:500],
                "date": pub_date,
                "url": entry.get("link", ""),
            })
    except ImportError:
        logger.warning("feedparser not installed — pip install feedparser")
    except Exception as e:
        logger.warning(f"Truth Social fetch failed: {e}")
    return posts


def _fetch_gdelt_trump_news(days=3):
    """Fetch Trump-related market news from GDELT."""
    posts = []
    try:
        url = (
            f"https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query=trump%20(tariff%20OR%20market%20OR%20trade%20OR%20economy%20OR%20crypto)"
            f"%20sourcelang:english&mode=artlist&maxrecords=30&format=json&timespan={days}d"
        )
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            for article in data.get("articles", [])[:30]:
                title = article.get("title", "")
                posts.append({
                    "source": "news",
                    "text": title[:300],
                    "date": article.get("seendate", ""),
                    "url": article.get("url", ""),
                })
    except Exception as e:
        logger.warning(f"GDELT fetch failed: {e}")

    # Fallback: Google News RSS if GDELT fails or returns nothing
    if not posts:
        posts = _fetch_google_news_trump(days)

    return posts


def _fetch_google_news_trump(days=3):
    """Fallback: Fetch Trump tariff/trade news from Google News RSS."""
    posts = []
    try:
        import feedparser
        queries = [
            "trump+tariff+market",
            "trump+trade+war",
            "trump+iran+military",
            "trump+economy+stocks",
        ]
        for q in queries:
            feed = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")
            for entry in feed.entries[:10]:
                text = entry.get("title", "")
                text = re.sub(r'<[^>]+>', '', text).strip()
                if not text:
                    continue
                pub_date = entry.get("published", "")
                posts.append({
                    "source": "news",
                    "text": text[:300],
                    "date": pub_date,
                    "url": entry.get("link", ""),
                })
            if len(posts) >= 20:
                break
    except ImportError:
        logger.warning("feedparser not installed for Google News fallback")
    except Exception as e:
        logger.warning(f"Google News fetch failed: {e}")
    return posts[:30]


def _fetch_whitehouse(days=3):
    """Fetch White House briefings/statements RSS."""
    posts = []
    try:
        import feedparser
        feed = feedparser.parse("https://www.whitehouse.gov/briefings-statements/feed/")
        cutoff = datetime.now() - timedelta(days=days)
        for entry in feed.entries[:20]:
            text = entry.get("title", "")
            pub = entry.get("published_parsed")
            if pub:
                entry_date = datetime(*pub[:6])
                if entry_date < cutoff:
                    continue
            posts.append({
                "source": "whitehouse",
                "text": text[:300],
                "date": entry.get("published", ""),
                "url": entry.get("link", ""),
            })
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"White House fetch failed: {e}")
    return posts


# ─── Scoring Engine ───────────────────────────────────────────

def _score_text(text):
    """Score a single post/article for market impact. Returns (score, signals)."""
    text_lower = text.lower()
    score = 0
    signals = []
    amplifier = 1.0

    # Detect war/military context — suppresses bullish "deal" signals
    is_war_context = any(w in text_lower for w in WAR_CONTEXT_WORDS)

    # Check for tariff/geopolitical amplifiers (country/sector mentions increase impact)
    for keyword, mult in TARIFF_AMPLIFIERS.items():
        if keyword in text_lower:
            amplifier = max(amplifier, mult)

    # Score bullish keywords — but suppress in war/military context
    for keyword, points in BULLISH_KEYWORDS.items():
        if keyword in text_lower:
            # "deal" / "negotiate" in war context = ultimatum, not bullish
            if is_war_context and keyword in ("deal", "trade deal", "negotiate", "talks going well", "great relationship", "tremendous progress"):
                # Flip: treat as bearish ultimatum
                adjusted = -abs(points) * amplifier
                score += adjusted
                if abs(adjusted) >= 3:
                    signals.append({"text": f"{keyword} (ultimatum)", "score": round(adjusted, 1), "type": "bearish"})
            else:
                adjusted = points * amplifier if points > 0 else points
                score += adjusted
                if abs(adjusted) >= 3:
                    signals.append({"text": keyword, "score": round(adjusted, 1), "type": "bullish"})

    # Score bearish keywords
    for keyword, points in BEARISH_KEYWORDS.items():
        if keyword in text_lower:
            adjusted = points * amplifier if points < 0 else points
            score += adjusted
            if abs(adjusted) >= 3:
                signals.append({"text": keyword, "score": round(adjusted, 1), "type": "bearish"})

    # ALL CAPS posts amplify score by 1.3x (Trump uses caps for emphasis)
    caps_ratio = sum(1 for c in text if c.isupper()) / max(len(text), 1)
    if caps_ratio > 0.5:
        score *= 1.3

    # Exclamation marks amplify (strong sentiment either direction)
    exclamations = text.count("!")
    if exclamations >= 3:
        score *= 1.2

    # War context overall amplifier — geopolitical posts have outsized market impact
    if is_war_context:
        score *= 1.5

    return round(score, 1), signals


def _compute_3day_pattern(scored_posts):
    """Compute 3-day rolling pattern from scored posts.
    Returns trend direction and acceleration."""
    if not scored_posts:
        return {"trend": "neutral", "acceleration": 0, "day_scores": []}

    now = datetime.now()
    day_buckets = {0: [], 1: [], 2: []}  # 0=today, 1=yesterday, 2=day before

    for post in scored_posts:
        date_str = post.get("date", "")
        try:
            if "T" in date_str:
                post_date = datetime.fromisoformat(date_str.replace("Z", ""))
            else:
                post_date = datetime.strptime(date_str[:19], "%Y%m%d%H%M%S") if date_str[:8].isdigit() else now
            days_ago = (now - post_date).days
            if days_ago in day_buckets:
                day_buckets[days_ago].append(post["score"])
        except Exception:
            day_buckets[0].append(post["score"])

    day_avgs = []
    for d in [2, 1, 0]:  # oldest to newest
        scores = day_buckets[d]
        day_avgs.append(round(sum(scores) / max(len(scores), 1), 1))

    # Trend: compare today vs 2 days ago
    if len(day_avgs) >= 3:
        delta = day_avgs[2] - day_avgs[0]  # today minus 2 days ago
        accel = day_avgs[2] - day_avgs[1]  # today minus yesterday
    else:
        delta = 0
        accel = 0

    if delta > 3:
        trend = "improving"
    elif delta < -3:
        trend = "deteriorating"
    else:
        trend = "stable"

    return {
        "trend": trend,
        "acceleration": round(accel, 1),
        "day_scores": day_avgs,  # [2 days ago, yesterday, today]
    }


# ─── Public API ───────────────────────────────────────────────

def get_trump_mood():
    """Get the Trump Mood Index — a -100 to +100 gauge of presidential market sentiment.

    Returns dict with:
        mood: overall score (-100 to +100)
        label: BULLISH / LEAN BULL / NEUTRAL / LEAN BEAR / BEARISH
        color: hex color for UI
        pattern: 3-day trend analysis
        top_signals: most impactful keywords detected
        posts_analyzed: count
        sources: breakdown by source
    """
    cached = _get_cached("trump_mood")
    if cached:
        return {**cached, "cached": True}

    # Fetch from all sources
    truth_posts = _fetch_truth_social(3)
    news_posts = _fetch_gdelt_trump_news(3)
    wh_posts = _fetch_whitehouse(3)

    all_posts = truth_posts + news_posts + wh_posts

    if not all_posts:
        # Don't cache NO DATA — retry on next call
        return {
            "mood": 0, "label": "NO DATA", "color": "#636b7e",
            "description": "Could not fetch Trump statements — will retry",
            "pattern": {"trend": "unknown", "acceleration": 0, "day_scores": []},
            "top_signals": [], "posts_analyzed": 0,
            "sources": {"truth_social": 0, "news": 0, "whitehouse": 0},
            "cached": False,
        }

    # Score each post
    all_signals = []
    scored_posts = []
    total_score = 0

    for post in all_posts:
        score, signals = _score_text(post["text"])
        post["score"] = score
        scored_posts.append(post)
        total_score += score
        all_signals.extend(signals)

    # Normalize to -100 to +100 range
    # Cap raw score and normalize
    raw = total_score / max(len(all_posts), 1)
    mood = max(-100, min(100, raw * 5))  # Scale factor: 5x avg score

    # 3-day pattern
    pattern = _compute_3day_pattern(scored_posts)

    # Adjust mood slightly based on trend
    if pattern["trend"] == "improving":
        mood = min(100, mood + 5)
    elif pattern["trend"] == "deteriorating":
        mood = max(-100, mood - 5)

    # Label and color
    if mood >= 30:
        label, color = "BULLISH", "#00c896"
        desc = "Trump rhetoric is market-positive — trade deals, tax cuts, deregulation"
    elif mood >= 10:
        label, color = "LEAN BULL", "#8bc34a"
        desc = "Slightly positive tone — cautious optimism on trade/economy"
    elif mood >= -10:
        label, color = "NEUTRAL", "#ffc837"
        desc = "Mixed signals — no strong directional bias from statements"
    elif mood >= -30:
        label, color = "LEAN BEAR", "#ff8c42"
        desc = "Negative tone — tariff threats, trade tension, uncertainty"
    else:
        label, color = "BEARISH", "#ff4757"
        desc = "Aggressive rhetoric — new tariffs, trade escalation, market risk"

    # Top signals (deduplicated, sorted by absolute score)
    seen = set()
    top_signals = []
    for sig in sorted(all_signals, key=lambda x: abs(x["score"]), reverse=True):
        if sig["text"] not in seen:
            seen.add(sig["text"])
            top_signals.append(sig)
        if len(top_signals) >= 5:
            break

    # Recent notable posts (top 3 by absolute score)
    notable = sorted(scored_posts, key=lambda x: abs(x["score"]), reverse=True)[:3]

    # Derive actionable BUY/AVOID sector signals from current rhetoric
    trade_signals = _derive_trade_signals(top_signals, mood, pattern)

    result = {
        "mood": round(mood, 1),
        "label": label,
        "color": color,
        "description": desc,
        "pattern": pattern,
        "top_signals": top_signals,
        "trade_signals": trade_signals,
        "notable_posts": [{
            "text": p["text"][:200],
            "source": p["source"],
            "score": p["score"],
        } for p in notable],
        "posts_analyzed": len(all_posts),
        "sources": {
            "truth_social": len(truth_posts),
            "news": len(news_posts),
            "whitehouse": len(wh_posts),
        },
        "updated_at": datetime.now().isoformat(),
        "cached": False,
    }

    # Persist snapshot to database
    _save_to_db(result)

    _set_cached("trump_mood", result)
    return result


# ─── Database Persistence ────────────────────────────────────

def _save_to_db(result):
    """Save a trump mood snapshot to the database."""
    try:
        from db import execute, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        execute(
            f"""INSERT INTO trump_mood_history
                (mood, label, description, pattern_trend, pattern_acceleration,
                 day_scores, top_signals, notable_posts, posts_analyzed,
                 src_truth, src_news, src_whitehouse)
                VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P})""",
            (
                result["mood"],
                result["label"],
                result["description"],
                result["pattern"]["trend"],
                result["pattern"]["acceleration"],
                json.dumps(result["pattern"].get("day_scores", [])),
                json.dumps(result.get("top_signals", [])),
                json.dumps(result.get("notable_posts", [])),
                result["posts_analyzed"],
                result["sources"]["truth_social"],
                result["sources"]["news"],
                result["sources"]["whitehouse"],
            ),
        )
    except Exception as e:
        logger.warning(f"Failed to save trump mood to DB: {e}")


def get_mood_history(days=30):
    """Get historical mood snapshots from the database."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        conn = get_db()
        try:
            if IS_POSTGRES:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    "SELECT * FROM trump_mood_history WHERE created_at >= NOW() - INTERVAL '%s days' ORDER BY created_at DESC",
                    (days,),
                )
                rows = cur.fetchall()
                cur.close()
            else:
                cur = conn.execute(
                    "SELECT * FROM trump_mood_history WHERE created_at >= datetime('now', '-' || ? || ' days') ORDER BY created_at DESC",
                    (days,),
                )
                rows = [dict(r) for r in cur.fetchall()]
            # Parse JSON fields
            for r in rows:
                for field in ("day_scores", "top_signals", "notable_posts"):
                    if r.get(field) and isinstance(r[field], str):
                        try:
                            r[field] = json.loads(r[field])
                        except Exception:
                            pass
                if r.get("created_at"):
                    r["created_at"] = str(r["created_at"])
            return rows
        finally:
            put_db(conn)
    except Exception as e:
        logger.warning(f"Failed to load trump mood history: {e}")
        return []


# ─── AI Prediction ───────────────────────────────────────────

def get_ai_prediction(current_mood=None, force=False):
    """Get Trump AI prediction — shared across all users, generated at most every 12 hours.

    - Default (force=False): returns cached (memory → DB). Never makes an LLM call.
    - force=True: makes a new LLM call only if >12h since last generation.
      If <12h, returns existing cache even when forced.
    """
    # 1. Check 12h in-memory cache (fastest, shared across all users in this worker)
    cached = _get_cached("trump_ai_prediction", ttl=AI_PREDICTION_TTL)
    if cached:
        return {**cached, "cached": True}

    # 2. Check DB for latest prediction (survives restarts, shared globally)
    db_pred = _load_prediction_from_db()
    if db_pred:
        # Check if it's still within 12h
        try:
            gen_time = datetime.fromisoformat(db_pred.get("generated_at", ""))
            age_seconds = (datetime.now() - gen_time).total_seconds()
            if age_seconds < AI_PREDICTION_TTL:
                db_pred["cached"] = True
                _set_cached("trump_ai_prediction", db_pred)
                return db_pred
        except Exception:
            pass

    # 3. Only generate a new prediction if user explicitly requested it
    if not force:
        if db_pred:
            db_pred["cached"] = True
            db_pred["stale"] = True
            return db_pred
        return {"error": "No prediction yet. Click Generate to create one.", "cached": False}

    if current_mood is None:
        current_mood = get_trump_mood()

    # Build context from recent history
    history = get_mood_history(7)
    history_summary = ""
    if history:
        for h in history[:10]:
            history_summary += f"- {h.get('created_at', '?')}: mood={h['mood']} ({h['label']}), trend={h.get('pattern_trend','?')}, posts={h.get('posts_analyzed',0)}\n"

    # Build signal summary
    signals_text = ""
    for sig in current_mood.get("top_signals", []):
        signals_text += f"  - '{sig['text']}' (score: {sig['score']}, {sig['type']})\n"

    notable_text = ""
    for p in current_mood.get("notable_posts", []):
        notable_text += f"  - [{p['source']}] \"{p['text'][:150]}\" (score: {p['score']})\n"

    prompt = f"""You are a political-economic analyst specializing in predicting market-moving actions by the US President.

CURRENT STATE (as of {datetime.now().strftime('%Y-%m-%d %H:%M')}):
- Trump Mood Index: {current_mood['mood']} / 100 ({current_mood['label']})
- 3-Day Pattern: {current_mood['pattern']['trend']} (acceleration: {current_mood['pattern']['acceleration']})
- Day scores (2d ago → today): {current_mood['pattern'].get('day_scores', [])}
- Posts analyzed: {current_mood['posts_analyzed']}

TOP SIGNALS DETECTED:
{signals_text or '  (none)'}

NOTABLE RECENT POSTS/NEWS:
{notable_text or '  (none)'}

7-DAY MOOD HISTORY:
{history_summary or '  (no history yet)'}

Based on this data, provide your analysis in this JSON format:
{{
  "prediction": "What Trump is most likely to do next (1-3 sentences, be specific about tariffs, trade actions, policy moves)",
  "confidence": 0.0-1.0,
  "timeframe": "when this is likely to happen (e.g. 'next 24-48 hours', 'this week')",
  "market_impact": {{
    "direction": "bullish|bearish|neutral",
    "severity": "low|medium|high|extreme",
    "sectors_affected": ["list of sectors most affected"],
    "explanation": "Why this matters for markets (1-2 sentences)"
  }},
  "trade_implications": {{
    "stocks": "What stock traders should watch for / do",
    "crypto": "What crypto traders should watch for / do",
    "hedging": "Suggested hedging strategies if any"
  }},
  "rhetoric_trajectory": "Is Trump escalating, de-escalating, or pivoting? What's the pattern? (1-2 sentences)",
  "wildcard_risk": "What unexpected move could surprise markets? (1 sentence)"
}}

Be direct, specific, and actionable. Focus on what he WILL DO, not what he said. Ground your analysis in his historical pattern of rhetoric → action."""

    try:
        import os
        import requests as _req
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key or api_key == "your_openrouter_api_key_here":
            return {"error": "OpenRouter API key not configured", "cached": False}

        model = os.getenv("LLM_RESEARCH", "anthropic/claude-sonnet-4-6")

        resp = _req.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5001",
                "X-Title": "AI Stock Analyst - Trump Predictor",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a political-economic analyst. Always respond with valid JSON only, no markdown."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 1500,
                "temperature": 0.3,
            },
            timeout=45,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        # Parse JSON from response
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        prediction = json.loads(content)
        prediction["model"] = model
        prediction["generated_at"] = datetime.now().isoformat()
        prediction["cached"] = False

        # Save prediction to latest DB row
        _save_prediction_to_db(prediction)

        _set_cached("trump_ai_prediction", prediction)
        return prediction

    except json.JSONDecodeError as e:
        logger.warning(f"AI prediction JSON parse failed: {e}")
        return {"error": "Failed to parse AI prediction", "raw": content[:500] if 'content' in dir() else "", "cached": False}
    except Exception as e:
        logger.warning(f"AI prediction failed: {e}")
        return {"error": f"AI prediction failed: {str(e)}", "cached": False}


def _save_prediction_to_db(prediction):
    """Save AI prediction to the most recent mood history row."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        conn = get_db()
        try:
            pred_json = json.dumps(prediction)
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"UPDATE trump_mood_history SET ai_prediction = {P} WHERE id = (SELECT MAX(id) FROM trump_mood_history)",
                    (pred_json,),
                )
                cur.close()
            else:
                conn.execute(
                    f"UPDATE trump_mood_history SET ai_prediction = {P} WHERE id = (SELECT MAX(id) FROM trump_mood_history)",
                    (pred_json,),
                )
            conn.commit()
        finally:
            put_db(conn)
    except Exception as e:
        logger.warning(f"Failed to save AI prediction to DB: {e}")


def _load_prediction_from_db():
    """Load the most recent AI prediction from the database (global, not per-user)."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        conn = get_db()
        try:
            if IS_POSTGRES:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    "SELECT ai_prediction FROM trump_mood_history WHERE ai_prediction IS NOT NULL ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
                cur.close()
            else:
                r = conn.execute(
                    "SELECT ai_prediction FROM trump_mood_history WHERE ai_prediction IS NOT NULL ORDER BY id DESC LIMIT 1"
                ).fetchone()
                row = dict(r) if r else None
            if row and row.get("ai_prediction"):
                return json.loads(row["ai_prediction"])
        finally:
            put_db(conn)
    except Exception as e:
        logger.warning(f"Failed to load AI prediction from DB: {e}")
    return None


# ─── Backtrack Detection Engine ──────────────────────────────

def _classify_signals_by_policy(top_signals):
    """Tag a list of signals to their policy areas and direction (escalation/de_escalation)."""
    tagged = []  # [{policy_area, direction, keyword, score}]
    if not top_signals:
        return tagged
    for sig in top_signals:
        kw = sig.get("text", "").lower().replace(" (ultimatum)", "")
        score = sig.get("score", 0)
        for policy, dirs in POLICY_SIGNAL_MAP.items():
            for esc_kw in dirs["escalation"]:
                if esc_kw in kw:
                    tagged.append({"policy": policy, "direction": "escalation", "keyword": kw, "score": score})
                    break
            for de_kw in dirs["de_escalation"]:
                if de_kw in kw:
                    tagged.append({"policy": policy, "direction": "de_escalation", "keyword": kw, "score": score})
                    break
    return tagged


def detect_backtracks(days=90):
    """Detect policy reversals from mood history using rule-based analysis.

    Scans mood snapshots for polarity flips within each policy area:
    - Bearish cluster followed by bullish cluster (or vice versa)
    - Known reversal keyword pairs
    - Mood swing > 15 points between stance and backtrack

    Returns list of detected backtrack dicts.
    """
    cached = _get_cached("backtracks", ttl=BACKTRACK_CACHE_TTL)
    if cached is not None:
        return cached

    history = get_mood_history(days)
    if not history or len(history) < 2:
        return []

    # Sort oldest first for chronological scanning
    history = sorted(history, key=lambda r: r.get("created_at", ""))

    # Build per-policy timeline: [{date, mood, direction, keywords, row_idx}]
    policy_timelines = {}
    for idx, row in enumerate(history):
        signals = row.get("top_signals", [])
        if isinstance(signals, str):
            try:
                signals = json.loads(signals)
            except Exception:
                signals = []
        tagged = _classify_signals_by_policy(signals)
        for t in tagged:
            policy = t["policy"]
            if policy not in policy_timelines:
                policy_timelines[policy] = []
            policy_timelines[policy].append({
                "date": row.get("created_at", ""),
                "mood": row.get("mood", 0),
                "direction": t["direction"],
                "keyword": t["keyword"],
                "score": t["score"],
                "row_idx": idx,
            })

    backtracks = []
    seen_keys = set()  # dedup: (policy, initial_date_str)

    for policy, timeline in policy_timelines.items():
        if len(timeline) < 2:
            continue

        # Scan for polarity flips: escalation followed by de_escalation (or vice versa)
        i = 0
        while i < len(timeline):
            entry = timeline[i]
            if entry["direction"] != "escalation":
                i += 1
                continue

            # Look ahead for a de_escalation in the same policy
            for j in range(i + 1, len(timeline)):
                other = timeline[j]
                if other["direction"] != "de_escalation":
                    continue

                # Check time gap
                try:
                    d1 = datetime.fromisoformat(entry["date"].replace("Z", "")) if entry["date"] else None
                    d2 = datetime.fromisoformat(other["date"].replace("Z", "")) if other["date"] else None
                    if d1 and d2:
                        gap_days = abs((d2 - d1).days)
                    else:
                        gap_days = 0
                except Exception:
                    gap_days = 0

                if gap_days > 30 or gap_days == 0:
                    continue

                mood_swing = other["mood"] - entry["mood"]

                # Require meaningful mood swing (> 15 points)
                if abs(mood_swing) < 15:
                    continue

                dedup_key = (policy, entry["date"][:10] if entry["date"] else "")
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                # Check for known reversal keyword pairs (boosts confidence)
                pair_match = False
                for esc_kw, de_kw in REVERSAL_PAIRS:
                    if esc_kw in entry["keyword"] and de_kw in other["keyword"]:
                        pair_match = True
                        break

                confidence = 0.6
                if pair_match:
                    confidence += 0.2
                if abs(mood_swing) > 30:
                    confidence += 0.1
                confidence = min(confidence, 0.95)

                bt = {
                    "policy_area": policy,
                    "initial_stance": f"{entry['keyword']} (mood: {entry['mood']:.0f})",
                    "initial_date": entry["date"],
                    "initial_mood": entry["mood"],
                    "backtrack_stance": f"{other['keyword']} (mood: {other['mood']:.0f})",
                    "backtrack_date": other["date"],
                    "backtrack_mood": other["mood"],
                    "days_to_reversal": gap_days,
                    "mood_swing": round(mood_swing, 1),
                    "market_impact": json.dumps({
                        "direction": "bullish" if mood_swing > 0 else "bearish",
                        "severity": "high" if abs(mood_swing) > 30 else "medium" if abs(mood_swing) > 20 else "low",
                    }),
                    "detection_method": "rule",
                    "confidence": round(confidence, 2),
                }
                backtracks.append(bt)
                break  # Found a match for this escalation, move on

            i += 1

    # Save new backtracks to DB
    for bt in backtracks:
        _save_backtrack(bt)

    _set_cached("backtracks", backtracks)
    return backtracks


def _save_backtrack(bt):
    """Save a detected backtrack to the database (dedup by policy_area + initial_date)."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        conn = get_db()
        try:
            # Dedup check: same policy + same initial date (within same day)
            init_date_prefix = str(bt["initial_date"])[:10] if bt.get("initial_date") else ""
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT id FROM trump_backtracks WHERE policy_area = {P} AND CAST(initial_date AS TEXT) LIKE {P} LIMIT 1",
                    (bt["policy_area"], f"{init_date_prefix}%"),
                )
                existing = cur.fetchone()
                cur.close()
            else:
                existing = conn.execute(
                    f"SELECT id FROM trump_backtracks WHERE policy_area = {P} AND CAST(initial_date AS TEXT) LIKE {P} LIMIT 1",
                    (bt["policy_area"], f"{init_date_prefix}%"),
                ).fetchone()

            if existing:
                return  # Already recorded

            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"""INSERT INTO trump_backtracks
                        (policy_area, initial_stance, initial_date, initial_mood,
                         backtrack_stance, backtrack_date, backtrack_mood,
                         days_to_reversal, mood_swing, market_impact, detection_method, confidence)
                        VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P})""",
                    (bt["policy_area"], bt["initial_stance"], bt["initial_date"], bt.get("initial_mood"),
                     bt["backtrack_stance"], bt["backtrack_date"], bt.get("backtrack_mood"),
                     bt.get("days_to_reversal"), bt.get("mood_swing"), bt.get("market_impact"),
                     bt.get("detection_method", "rule"), bt.get("confidence", 0.5)),
                )
                cur.close()
            else:
                conn.execute(
                    f"""INSERT INTO trump_backtracks
                        (policy_area, initial_stance, initial_date, initial_mood,
                         backtrack_stance, backtrack_date, backtrack_mood,
                         days_to_reversal, mood_swing, market_impact, detection_method, confidence)
                        VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P})""",
                    (bt["policy_area"], bt["initial_stance"], bt["initial_date"], bt.get("initial_mood"),
                     bt["backtrack_stance"], bt["backtrack_date"], bt.get("backtrack_mood"),
                     bt.get("days_to_reversal"), bt.get("mood_swing"), bt.get("market_impact"),
                     bt.get("detection_method", "rule"), bt.get("confidence", 0.5)),
                )
            conn.commit()
        finally:
            put_db(conn)
    except Exception as e:
        logger.warning(f"Failed to save backtrack: {e}")


def get_backtracks(days=90, force=False):
    """Get detected backtracks with stats. Runs detection if force=True or no cache."""
    if not force:
        cached = _get_cached("backtracks_result", ttl=BACKTRACK_CACHE_TTL)
        if cached:
            return {**cached, "cached": True}

    # Run detection (also caches raw backtracks internally)
    if force:
        _cache.pop("backtracks", None)
    detected = detect_backtracks(days)

    # Also load any previously saved backtracks from DB
    db_backtracks = _load_backtracks_from_db(days)

    # Merge: use DB as source of truth, add any newly detected
    all_backtracks = db_backtracks if db_backtracks else detected

    stats = _compute_backtrack_stats(all_backtracks)

    result = {
        "backtracks": all_backtracks,
        "stats": stats,
        "cached": False,
    }
    _set_cached("backtracks_result", result)
    return result


def _load_backtracks_from_db(days=90):
    """Load backtracks from the database."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        conn = get_db()
        try:
            if IS_POSTGRES:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    "SELECT * FROM trump_backtracks WHERE created_at >= NOW() - INTERVAL '%s days' ORDER BY backtrack_date DESC",
                    (days,),
                )
                rows = cur.fetchall()
                cur.close()
            else:
                cur = conn.execute(
                    "SELECT * FROM trump_backtracks WHERE created_at >= datetime('now', '-' || ? || ' days') ORDER BY backtrack_date DESC",
                    (days,),
                )
                rows = [dict(r) for r in cur.fetchall()]

            for r in rows:
                if r.get("market_impact") and isinstance(r["market_impact"], str):
                    try:
                        r["market_impact"] = json.loads(r["market_impact"])
                    except Exception:
                        pass
                if r.get("created_at"):
                    r["created_at"] = str(r["created_at"])
                if r.get("initial_date"):
                    r["initial_date"] = str(r["initial_date"])
                if r.get("backtrack_date"):
                    r["backtrack_date"] = str(r["backtrack_date"])
            return rows
        finally:
            put_db(conn)
    except Exception as e:
        logger.warning(f"Failed to load backtracks from DB: {e}")
        return []


def _compute_backtrack_stats(backtracks):
    """Compute summary statistics from backtrack records."""
    if not backtracks:
        return {"total": 0, "avg_days": 0, "median_days": 0, "top_areas": [], "avg_mood_swing": 0}

    days_list = [bt.get("days_to_reversal", 0) for bt in backtracks if bt.get("days_to_reversal")]
    swings = [abs(bt.get("mood_swing", 0)) for bt in backtracks if bt.get("mood_swing")]

    avg_days = round(sum(days_list) / max(len(days_list), 1), 1)
    sorted_days = sorted(days_list)
    median_days = sorted_days[len(sorted_days) // 2] if sorted_days else 0

    # Count by policy area
    area_counts = {}
    for bt in backtracks:
        area = bt.get("policy_area", "unknown")
        area_counts[area] = area_counts.get(area, 0) + 1
    top_areas = sorted(area_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "total": len(backtracks),
        "avg_days": avg_days,
        "median_days": median_days,
        "top_areas": [{"area": a, "count": c} for a, c in top_areas],
        "avg_mood_swing": round(sum(swings) / max(len(swings), 1), 1),
    }


# ─── Backtrack LLM Prediction ───────────────────────────────

def get_backtrack_prediction(force=False):
    """LLM-powered prediction of when Trump will next reverse course on active policies.

    Uses historical backtrack data + current mood/signals to predict probability,
    timeframe, and market impact of upcoming policy reversals.

    Shared globally — cached for 12h. Only makes LLM call when force=True.
    """
    # 1. Check in-memory cache
    cached = _get_cached("backtrack_prediction", ttl=BACKTRACK_PREDICTION_TTL)
    if cached:
        return {**cached, "cached": True}

    # 2. Check DB for latest prediction
    db_pred = _load_backtrack_prediction_from_db()
    if db_pred:
        try:
            gen_time = datetime.fromisoformat(db_pred.get("generated_at", ""))
            age_seconds = (datetime.now() - gen_time).total_seconds()
            if age_seconds < BACKTRACK_PREDICTION_TTL:
                db_pred["cached"] = True
                _set_cached("backtrack_prediction", db_pred)
                return db_pred
        except Exception:
            pass

    # 3. Only generate if forced
    if not force:
        if db_pred:
            db_pred["cached"] = True
            db_pred["stale"] = True
            return db_pred
        return {"error": "No backtrack prediction yet. Click Predict to generate.", "cached": False}

    # Gather context
    current_mood = get_trump_mood()
    backtracks_result = get_backtracks(90)
    backtracks = backtracks_result.get("backtracks", [])
    stats = backtracks_result.get("stats", {})

    # Build history context
    backtrack_history = ""
    if backtracks:
        for bt in backtracks[:15]:
            impact = bt.get("market_impact", {})
            if isinstance(impact, str):
                try:
                    impact = json.loads(impact)
                except Exception:
                    impact = {}
            backtrack_history += (
                f"  - {bt.get('policy_area','?')}: \"{bt.get('initial_stance','')}\" → "
                f"\"{bt.get('backtrack_stance','')}\" | {bt.get('days_to_reversal',0)} days | "
                f"mood swing: {bt.get('mood_swing',0)} | impact: {impact.get('direction','?')} {impact.get('severity','?')}\n"
            )

    signals_text = ""
    for sig in current_mood.get("top_signals", []):
        signals_text += f"  - '{sig['text']}' (score: {sig['score']}, {sig['type']})\n"

    notable_text = ""
    for p in current_mood.get("notable_posts", []):
        notable_text += f"  - [{p['source']}] \"{p['text'][:150]}\" (score: {p['score']})\n"

    prompt = f"""You are a political-economic analyst specializing in predicting when the US President will REVERSE COURSE on stated policies. You focus on the pattern of: announce harsh policy → walk it back days/weeks later.

CURRENT STATE (as of {datetime.now().strftime('%Y-%m-%d %H:%M')}):
- Trump Mood Index: {current_mood['mood']} / 100 ({current_mood['label']})
- 3-Day Pattern: {current_mood['pattern']['trend']} (acceleration: {current_mood['pattern']['acceleration']})
- Day scores (2d ago → today): {current_mood['pattern'].get('day_scores', [])}

TOP SIGNALS RIGHT NOW:
{signals_text or '  (none)'}

NOTABLE RECENT POSTS/NEWS:
{notable_text or '  (none)'}

HISTORICAL BACKTRACKS DETECTED ({stats.get('total', 0)} total):
{backtrack_history or '  (none detected yet)'}

BACKTRACK STATISTICS:
- Average days to reversal: {stats.get('avg_days', 'N/A')}
- Median days to reversal: {stats.get('median_days', 'N/A')}
- Average mood swing at reversal: {stats.get('avg_mood_swing', 'N/A')}
- Most reversed policy areas: {', '.join(a['area'] + ' (' + str(a['count']) + 'x)' for a in stats.get('top_areas', [])[:3]) or 'N/A'}

Based on the historical reversal patterns and current rhetoric, provide your prediction in this JSON format:
{{
  "active_policies": [
    {{
      "policy": "human-readable policy name (e.g. 'China Tariffs')",
      "policy_area": "machine key (e.g. 'china_tariffs')",
      "current_stance": "What Trump is currently saying/doing on this",
      "backtrack_probability": 0.0-1.0,
      "estimated_days": number of days until likely reversal,
      "estimated_timeframe": "human-readable (e.g. 'within 1-2 weeks')",
      "reasoning": "Why this is likely to be reversed, based on historical patterns + current signals",
      "trigger_signals": ["what to watch for that would signal imminent reversal"],
      "market_impact_if_reversed": {{
        "direction": "bullish|bearish|neutral",
        "severity": "low|medium|high|extreme",
        "sectors": ["most affected sectors"]
      }}
    }}
  ],
  "overall_insight": "2-3 sentences about Trump's reversal patterns — the rhythm, the tells, what drives backtracks",
  "next_likely_reversal": "policy_area most likely to flip next",
  "overall_backtrack_probability": 0.0-1.0,
  "confidence": 0.0-1.0
}}

Be specific and data-driven. Ground every prediction in the historical backtrack data. If no backtracks exist yet, use Trump's well-documented pattern of announcing extreme positions then negotiating back. Focus on ACTIVE policies that show current signals — don't speculate on dormant areas."""

    try:
        import os
        import requests as _req
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key or api_key == "your_openrouter_api_key_here":
            return _fallback_backtrack_prediction(stats, backtracks)

        model = os.getenv("LLM_RESEARCH", "anthropic/claude-sonnet-4-6")

        resp = _req.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5001",
                "X-Title": "AI Stock Analyst - Backtrack Predictor",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a political-economic analyst specializing in policy reversal prediction. Always respond with valid JSON only, no markdown."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 2000,
                "temperature": 0.3,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        prediction = json.loads(content)
        prediction["model"] = model
        prediction["generated_at"] = datetime.now().isoformat()
        prediction["cached"] = False
        prediction["stats"] = stats

        _save_backtrack_prediction_to_db(prediction)
        _set_cached("backtrack_prediction", prediction)
        return prediction

    except json.JSONDecodeError as e:
        logger.warning(f"Backtrack prediction JSON parse failed: {e}")
        return _fallback_backtrack_prediction(stats, backtracks)
    except Exception as e:
        logger.warning(f"Backtrack prediction failed: {e}")
        return _fallback_backtrack_prediction(stats, backtracks)


def _fallback_backtrack_prediction(stats, backtracks):
    """Rule-based fallback when LLM is unavailable."""
    result = {
        "active_policies": [],
        "overall_insight": f"Rule-based analysis: {stats.get('total', 0)} backtracks detected. Average reversal takes {stats.get('avg_days', 'N/A')} days with average mood swing of {stats.get('avg_mood_swing', 'N/A')} points.",
        "next_likely_reversal": stats["top_areas"][0]["area"] if stats.get("top_areas") else "unknown",
        "overall_backtrack_probability": 0.5,
        "confidence": 0.3,
        "stats": stats,
        "generated_at": datetime.now().isoformat(),
        "cached": False,
        "fallback": True,
    }
    return result


def _save_backtrack_prediction_to_db(prediction):
    """Save backtrack prediction to the most recent backtrack row."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        conn = get_db()
        try:
            # Store as a special row in trump_backtracks with policy_area='__prediction__'
            pred_json = json.dumps(prediction)
            if IS_POSTGRES:
                cur = conn.cursor()
                # Upsert: delete old prediction row and insert new one
                cur.execute("DELETE FROM trump_backtracks WHERE policy_area = '__prediction__'")
                cur.execute(
                    f"""INSERT INTO trump_backtracks
                        (policy_area, initial_stance, initial_date, backtrack_stance, backtrack_date,
                         market_impact, detection_method, confidence)
                        VALUES ('__prediction__', 'prediction', NOW(), {P}, NOW(), {P}, 'llm', {P})""",
                    (prediction.get("overall_insight", "")[:500], pred_json, prediction.get("confidence", 0.5)),
                )
                cur.close()
            else:
                conn.execute("DELETE FROM trump_backtracks WHERE policy_area = '__prediction__'")
                conn.execute(
                    f"""INSERT INTO trump_backtracks
                        (policy_area, initial_stance, initial_date, backtrack_stance, backtrack_date,
                         market_impact, detection_method, confidence)
                        VALUES ('__prediction__', 'prediction', datetime('now'), {P}, datetime('now'), {P}, 'llm', {P})""",
                    (prediction.get("overall_insight", "")[:500], pred_json, prediction.get("confidence", 0.5)),
                )
            conn.commit()
        finally:
            put_db(conn)
    except Exception as e:
        logger.warning(f"Failed to save backtrack prediction: {e}")


def _load_backtrack_prediction_from_db():
    """Load the most recent backtrack prediction from DB."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        conn = get_db()
        try:
            if IS_POSTGRES:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    "SELECT market_impact FROM trump_backtracks WHERE policy_area = '__prediction__' ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
                cur.close()
            else:
                r = conn.execute(
                    "SELECT market_impact FROM trump_backtracks WHERE policy_area = '__prediction__' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                row = dict(r) if r else None

            if row and row.get("market_impact"):
                return json.loads(row["market_impact"])
        finally:
            put_db(conn)
    except Exception as e:
        logger.warning(f"Failed to load backtrack prediction from DB: {e}")
    return None
