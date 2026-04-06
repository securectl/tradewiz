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

    result = {
        "mood": round(mood, 1),
        "label": label,
        "color": color,
        "description": desc,
        "pattern": pattern,
        "top_signals": top_signals,
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
