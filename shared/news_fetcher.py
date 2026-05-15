"""Multi-source news fetcher for ticker analysis.

Sources (no paid APIs required):
  - yfinance Ticker.news (RSS aggregation, free)
  - GDELT 2.0 doc API (open, no key)
  - Reddit JSON search (r/stocks, r/wallstreetbets, r/investing, r/StockMarket, r/options)
  - X/Twitter via Nitter RSS mirrors (free, best-effort — mirrors are
    frequently rate-limited or down; failures are silently skipped)

All fetches are filtered to the last N hours (default 24) and deduped by URL.
Per-ticker cache is 5 minutes — `?force=1` bypasses cache.

Configuration:
  NITTER_MIRRORS  Optional comma-separated list of mirror hosts (no scheme),
                  e.g. "nitter.net,nitter.privacydev.net". Tried in order
                  until one returns a usable RSS feed.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[list, float]] = {}
_CACHE_TTL = 300  # 5 min


# ─── yfinance press releases / wire stories ─────────────────────────────

def _fetch_yfinance_news(ticker: str, hours: int = 24) -> list[dict]:
    out = []
    try:
        import yfinance as yf
        items = yf.Ticker(ticker).news or []
    except Exception as e:
        logger.warning(f"yfinance news for {ticker} failed: {e}")
        return out

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for it in items:
        try:
            content = it.get("content") or it
            published_raw = (content.get("pubDate")
                             or content.get("displayTime")
                             or it.get("providerPublishTime"))
            published = None
            if isinstance(published_raw, (int, float)):
                published = datetime.fromtimestamp(published_raw, tz=timezone.utc)
            elif isinstance(published_raw, str):
                try:
                    published = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                except Exception:
                    pass
            if not published:
                continue
            if published < cutoff:
                continue

            url = (content.get("canonicalUrl") or {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else None
            url = url or content.get("link") or it.get("link") or ""
            title = content.get("title") or it.get("title") or ""
            summary = content.get("summary") or content.get("description") or ""
            provider = (content.get("provider") or {}).get("displayName") if isinstance(content.get("provider"), dict) else None
            provider = provider or content.get("publisher") or it.get("publisher") or "yahoo"

            out.append({
                "title": title[:200],
                "url": url,
                "source": str(provider)[:60],
                "type": "news",
                "published_at": published.isoformat(),
                "summary": (summary or "")[:300],
            })
        except Exception:
            continue
    return out


# ─── GDELT 2.0 doc search ───────────────────────────────────────────────

def _fetch_gdelt_news(ticker: str, hours: int = 24) -> list[dict]:
    out = []
    try:
        # GDELT supports timespan (e.g., 24h) and query
        query = f'"{ticker}" stock'
        url = (
            "https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query={quote_plus(query)}"
            f"&mode=ArtList&format=json&maxrecords=20"
            f"&timespan={hours}h&sort=hybridrel"
        )
        resp = requests.get(url, timeout=10, headers={"User-Agent": "tradewiz/1.0"})
        if resp.status_code != 200:
            return out
        data = resp.json()
        for art in data.get("articles", []):
            try:
                seen = art.get("seendate", "")  # YYYYMMDDHHMMSS
                if len(seen) >= 14:
                    published = datetime.strptime(seen[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                else:
                    continue
                out.append({
                    "title": art.get("title", "")[:200],
                    "url": art.get("url", ""),
                    "source": art.get("domain", "gdelt")[:60],
                    "type": "news",
                    "published_at": published.isoformat(),
                    "summary": "",
                })
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"GDELT for {ticker} failed: {e}")
    return out


# ─── Reddit JSON (no auth needed for public search) ─────────────────────

REDDIT_SUBS = ["stocks", "wallstreetbets", "investing", "StockMarket", "options"]
_REDDIT_HEADERS = {"User-Agent": "tradewiz/1.0 (multi-source ticker news)"}


def _fetch_reddit(ticker: str, hours: int = 24) -> list[dict]:
    out = []
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    sub_q = "+".join(REDDIT_SUBS)
    # Reddit's `restrict_sr=on&sort=new&t=day` confines to a multi-sub search
    url = (
        f"https://www.reddit.com/r/{sub_q}/search.json"
        f"?q={quote_plus(ticker)}&restrict_sr=on&sort=new&t=day&limit=25"
    )
    try:
        resp = requests.get(url, timeout=10, headers=_REDDIT_HEADERS)
        if resp.status_code != 200:
            return out
        data = resp.json()
        for child in data.get("data", {}).get("children", []):
            try:
                d = child.get("data", {})
                created = float(d.get("created_utc", 0))
                if created < cutoff_ts:
                    continue
                title = d.get("title", "")[:200]
                if ticker.upper() not in title.upper():
                    # Reddit search is fuzzy; require explicit ticker mention
                    selftext = (d.get("selftext") or "")[:1000]
                    if ticker.upper() not in selftext.upper():
                        continue
                published = datetime.fromtimestamp(created, tz=timezone.utc)
                out.append({
                    "title": title,
                    "url": "https://reddit.com" + d.get("permalink", ""),
                    "source": f"r/{d.get('subreddit', '?')}",
                    "type": "social",
                    "published_at": published.isoformat(),
                    "score": int(d.get("score", 0)),
                    "comments": int(d.get("num_comments", 0)),
                    "summary": (d.get("selftext") or "")[:300],
                })
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Reddit for {ticker} failed: {e}")
    return out


# ─── X / Twitter via Nitter RSS (best-effort, free) ─────────────────────

# Default mirror list. Public Nitter instances churn — keep this fresh, or
# override via NITTER_MIRRORS env var. Tried in order until one responds.
_DEFAULT_NITTER_MIRRORS = [
    "nitter.net",
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.tiekoetter.com",
]
_NITTER_HEADERS = {"User-Agent": "tradewiz/1.0 (+rss)"}


def _nitter_mirrors() -> list[str]:
    raw = os.getenv("NITTER_MIRRORS", "").strip()
    if not raw:
        return list(_DEFAULT_NITTER_MIRRORS)
    out = []
    for m in raw.split(","):
        m = m.strip().rstrip("/")
        if not m:
            continue
        m = m.removeprefix("https://").removeprefix("http://")
        out.append(m)
    return out


def _fetch_x(ticker: str, hours: int = 24) -> list[dict]:
    """Best-effort fetch of recent X/Twitter posts mentioning the ticker via
    Nitter RSS. Returns [] when all mirrors fail — never raises."""
    try:
        import feedparser  # parsing RSS/Atom; pinned in requirements.txt
    except Exception as e:
        logger.warning(f"feedparser unavailable, skipping X: {e}")
        return []

    out: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    # Cashtag bias — `$TICKER` is the convention on X for equities
    query = f"${ticker} OR {ticker}"

    for host in _nitter_mirrors():
        url = f"https://{host}/search/rss?f=tweets&q={quote_plus(query)}"
        try:
            resp = requests.get(url, timeout=6, headers=_NITTER_HEADERS)
            if resp.status_code != 200 or not resp.content:
                continue
            feed = feedparser.parse(resp.content)
            entries = getattr(feed, "entries", None) or []
            if not entries:
                continue
            for e in entries[:30]:
                try:
                    # feedparser exposes published_parsed as a struct_time in UTC
                    pub = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
                    if not pub:
                        continue
                    published = datetime(*pub[:6], tzinfo=timezone.utc)
                    if published < cutoff:
                        continue
                    title = (getattr(e, "title", "") or "")[:200]
                    if ticker.upper() not in title.upper():
                        continue  # mirrors sometimes return loose matches
                    link = getattr(e, "link", "") or ""
                    # Rewrite nitter link → x.com so the user lands on the real post
                    canonical = link.replace(f"https://{host}", "https://x.com")
                    author = getattr(e, "author", "") or ""
                    out.append({
                        "title": title,
                        "url": canonical,
                        "source": author or "x.com",
                        "type": "social",
                        "published_at": published.isoformat(),
                        "summary": (getattr(e, "summary", "") or "")[:300],
                    })
                except Exception:
                    continue
            if out:
                break  # first mirror that returned usable items wins
        except Exception as e:
            logger.debug(f"Nitter mirror {host} failed for {ticker}: {e}")
            continue
    return out


# ─── Sentiment classification (rule-based, no LLM) ──────────────────────

_BULL_PATTERNS = re.compile(
    r"\b("
    r"beats?|topp(?:ed|ing|s)|crush(?:ed|es|ing)|smash(?:ed|es)|"
    r"raise[ds]?\s+(?:guidance|outlook|target)|guidance\s+raise[ds]?|"
    r"upgrade[ds]?|price\s+target\s+(?:raise[ds]?|hike[ds]?|lifte[ds]?|increase[ds]?)|"
    r"buy\s+rating|outperform|overweight|"
    r"fda\s+approval|approved\s+by\s+fda|cleared\s+by\s+fda|"
    r"partnership|deal\s+with|wins?\s+contract|awarded|landmark\s+deal|"
    r"acquir(?:ed|es|ing)|takeover\s+bid|merger|"
    r"surge[ds]?|soar(?:ed|s|ing)?|rall(?:y|ied|ies|ying)|jump(?:ed|s|ing)?|"
    r"breakout|all[-\s]?time\s+high|52[-\s]?week\s+high|record\s+high|"
    r"insider\s+buying|insider\s+purchase|stock\s+buyback|share\s+repurchase|"
    r"strong\s+demand|record\s+(?:revenue|sales|profit|earnings)|"
    r"dividend\s+(?:increase|hike|raise)|new\s+contract"
    r")\b",
    re.IGNORECASE,
)

_BEAR_PATTERNS = re.compile(
    r"\b("
    r"miss(?:es|ed)?|disappoint(?:s|ed|ing)?|short(?:fall|s)|"
    r"cut\s+(?:guidance|outlook|target)|guidance\s+cut|lowered?\s+(?:guidance|outlook)|"
    r"downgrade[ds]?|price\s+target\s+(?:cut|reduce[ds]?|lowered?|slashed)|"
    r"sell\s+rating|underperform|underweight|"
    r"lawsuit|class\s+action|sued|investigation|sec\s+probe|fraud|"
    r"recall(?:ed|s)?|halt(?:ed|s)?|trading\s+halted|"
    r"dilution|secondary\s+offering|going\s+concern|bankruptcy|chapter\s+11|"
    r"plunge[ds]?|tumble[ds]?|slump(?:ed|s)?|crash(?:ed|es|ing)?|sink(?:s|ing)?|"
    r"52[-\s]?week\s+low|all[-\s]?time\s+low|"
    r"insider\s+selling|insider\s+sold|"
    r"layoffs?|job\s+cuts?|restructuring\s+charge|"
    r"warn(?:ed|ing|s)|profit\s+warning"
    r")\b",
    re.IGNORECASE,
)


def _classify_sentiment(text: str) -> str:
    """Return 'bullish', 'bearish', or 'neutral' based on keyword presence.

    Counts pattern hits in both directions; whichever side has more wins,
    ties resolve to 'neutral'. Cheap regex pass — no LLM call. Designed
    for headline+summary text (~200-500 chars).
    """
    if not text:
        return "neutral"
    bull_hits = len(_BULL_PATTERNS.findall(text))
    bear_hits = len(_BEAR_PATTERNS.findall(text))
    if bull_hits > bear_hits:
        return "bullish"
    if bear_hits > bull_hits:
        return "bearish"
    return "neutral"


# ─── Aggregation ────────────────────────────────────────────────────────

def fetch_news(ticker: str, hours: int = 24, force: bool = False) -> dict:
    """Fetch news + social mentions for a ticker, last N hours.

    Returns:
        {
          "ticker": str,
          "hours": int,
          "items": [...],          # combined, sorted newest first
          "by_source": {...},      # counts
          "fetched_at": ISO,
          "cached": bool,
        }
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return {"ticker": "", "items": [], "hours": hours, "by_source": {}, "fetched_at": datetime.now().isoformat(), "cached": False}

    cache_key = f"{ticker}:{hours}"
    if not force:
        cached = _CACHE.get(cache_key)
        if cached and time.time() - cached[1] < _CACHE_TTL:
            data = dict(cached[0])
            data["cached"] = True
            return data

    items: list[dict] = []
    items.extend(_fetch_yfinance_news(ticker, hours))
    items.extend(_fetch_gdelt_news(ticker, hours))
    items.extend(_fetch_reddit(ticker, hours))
    items.extend(_fetch_x(ticker, hours))

    # Dedup by URL
    seen_urls = set()
    deduped = []
    for it in items:
        url = it.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(it)

    # Sort newest first
    deduped.sort(key=lambda x: x.get("published_at", ""), reverse=True)

    # Annotate sentiment + tally source/sentiment buckets
    by_source = {"news": 0, "social": 0}
    by_sentiment = {"bullish": 0, "neutral": 0, "bearish": 0}
    for it in deduped:
        t = it.get("type", "news")
        by_source[t] = by_source.get(t, 0) + 1
        sentiment = _classify_sentiment(f"{it.get('title','')} {it.get('summary','')}")
        it["sentiment"] = sentiment
        by_sentiment[sentiment] = by_sentiment.get(sentiment, 0) + 1

    result = {
        "ticker": ticker,
        "hours": hours,
        "items": deduped[:30],
        "by_source": by_source,
        "by_sentiment": by_sentiment,
        "fetched_at": datetime.now().isoformat(),
        "cached": False,
    }
    _CACHE[cache_key] = (result, time.time())
    return result


# Subset of bull patterns that represent material, tradeable catalysts —
# stronger signal than generic positive language. Used to filter news down
# to "active catalysts" for LLM weighting.
_HIGH_SIGNAL_CATALYSTS = re.compile(
    r"\b("
    r"beats?|topp(?:ed|ing|s)|"
    r"raise[ds]?\s+(?:guidance|outlook|target)|guidance\s+raise[ds]?|"
    r"upgrade[ds]?|price\s+target\s+(?:raise[ds]?|hike[ds]?|lifte[ds]?|increase[ds]?)|"
    r"fda\s+approval|approved\s+by\s+fda|cleared\s+by\s+fda|"
    r"acquir(?:ed|es|ing)|takeover\s+bid|merger|"
    r"partnership|wins?\s+contract|awarded|landmark\s+deal|"
    r"breakout|all[-\s]?time\s+high|record\s+high|"
    r"insider\s+buying|stock\s+buyback|share\s+repurchase"
    r")\b",
    re.IGNORECASE,
)


def active_catalysts(ticker: str, hours: int = 24, limit: int = 5) -> list[str]:
    """High-signal bullish catalysts for LLM injection.

    Filters fetched news to items whose sentiment is bullish AND match a
    material catalyst pattern (earnings beat, guidance raise, upgrade, FDA
    approval, M&A, contract win, breakout). Returns compact strings with the
    matched catalyst phrase up front so the LLM can weight them clearly.

    Empty list when no catalysts found — never raises.
    """
    try:
        data = fetch_news(ticker, hours=hours)
    except Exception:
        return []
    out: list[str] = []
    now = datetime.now(timezone.utc)
    for it in data.get("items", []):
        if it.get("sentiment") != "bullish":
            continue
        text = f"{it.get('title','')} {it.get('summary','')}"
        m = _HIGH_SIGNAL_CATALYSTS.search(text)
        if not m:
            continue
        try:
            published = datetime.fromisoformat(it.get("published_at", ""))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            age_h = (now - published).total_seconds() / 3600
            age_label = f"{int(age_h)}h ago" if age_h >= 1 else f"{int(age_h * 60)}m ago"
        except Exception:
            age_label = ""
        catalyst = m.group(0).upper()
        title = (it.get("title") or "")[:140]
        out.append(f"[{catalyst}, {age_label}, {it.get('source','?')}] {title}")
        if len(out) >= limit:
            break
    return out


def headlines_for_llm(ticker: str, hours: int = 24, limit: int = 8) -> list[str]:
    """Compact list of recent headlines suitable for inclusion in an LLM prompt.

    Returns short strings like "[r/wallstreetbets, 4h ago] {title}" — at most
    `limit` items. Empty list when no news. Used to inject context into the
    AI validator without bloating tokens.
    """
    try:
        data = fetch_news(ticker, hours=hours)
    except Exception:
        return []
    out = []
    now = datetime.now(timezone.utc)
    for it in data.get("items", [])[:limit]:
        try:
            published = datetime.fromisoformat(it.get("published_at", ""))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            age_h = (now - published).total_seconds() / 3600
            age_label = f"{int(age_h)}h ago" if age_h >= 1 else f"{int(age_h * 60)}m ago"
        except Exception:
            age_label = ""
        src = it.get("source", "?")
        title = (it.get("title") or "")[:140]
        sentiment = it.get("sentiment") or "neutral"
        tag = {"bullish": "↑", "bearish": "↓", "neutral": "·"}[sentiment]
        out.append(f"[{src}, {age_label}, {tag}] {title}")
    return out
