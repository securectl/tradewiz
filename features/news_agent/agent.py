"""
News agent — continuously ingests financial news + Reddit via RSS (feedparser),
tags each item with tickers, sectors, and sentiment, stores it in the
`news_articles` table with 30-day retention, and derives trending stocks/sectors.

Feeds the analyzer, bots, screener, and the landing-dashboard "Trending" card.
A single global background scanner polls every POLL_INTERVAL seconds (started
unconditionally from _auto_start_bots, like the options-flow scanner). Every feed
fetch and parse is wrapped so one bad feed never breaks the loop.
"""

import logging
import re
import threading
import time
from datetime import datetime, timedelta

from db import IS_POSTGRES, execute, query

logger = logging.getLogger(__name__)
P = "%s" if IS_POSTGRES else "?"

POLL_INTERVAL = 600          # 10 min between full sweeps
RETENTION_DAYS = 30
_UA = "Mozilla/5.0 (compatible; TradeWizNewsAgent/1.0; +https://tradewiz.market)"

# ── Feed catalog ────────────────────────────────────────────────────────
# category ∈ market | stocks | sector | reddit | crypto | macro
FEEDS = [
    {"name": "Yahoo Finance", "category": "market",
     "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "CNBC Top News", "category": "market",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
    {"name": "CNBC Markets", "category": "market",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"},
    {"name": "CNBC Economy", "category": "macro",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"},
    {"name": "MarketWatch Top", "category": "market",
     "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
    {"name": "MarketWatch RealTime", "category": "stocks",
     "url": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"},
    {"name": "Seeking Alpha", "category": "stocks",
     "url": "https://seekingalpha.com/market_currents.xml"},
    {"name": "NASDAQ Markets", "category": "stocks",
     "url": "https://www.nasdaq.com/feed/rssoutbound?category=Markets"},
    {"name": "Investing.com", "category": "market",
     "url": "https://www.investing.com/rss/news_25.rss"},
    {"name": "CoinDesk", "category": "crypto",
     "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Cointelegraph", "category": "crypto",
     "url": "https://cointelegraph.com/rss"},
    # Reddit (RSS; needs a UA or Reddit 429s)
    {"name": "r/stocks", "category": "reddit",
     "url": "https://www.reddit.com/r/stocks/.rss"},
    {"name": "r/wallstreetbets", "category": "reddit",
     "url": "https://www.reddit.com/r/wallstreetbets/.rss"},
    {"name": "r/investing", "category": "reddit",
     "url": "https://www.reddit.com/r/investing/.rss"},
    {"name": "r/StockMarket", "category": "reddit",
     "url": "https://www.reddit.com/r/StockMarket/.rss"},
    {"name": "r/options", "category": "reddit",
     "url": "https://www.reddit.com/r/options/.rss"},
]

# ── Ticker → sector map (built by inverting sector groups) ──────────────
_SECTOR_GROUPS = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "ORCL", "ADBE", "CSCO",
                   "INTC", "QCOM", "TXN", "IBM", "NOW", "INTU", "AMAT", "MU", "PLTR",
                   "SMCI", "ARM", "DELL", "PANW", "SNOW", "ANET"],
    "Communication Services": ["GOOGL", "GOOG", "META", "NFLX", "DIS", "T", "VZ", "TMUS", "CMCSA"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "BKNG",
                               "TGT", "F", "GM", "RIVN", "LULU", "ABNB"],
    "Consumer Staples": ["WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "MDLZ", "CL"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "SPGI",
                   "V", "MA", "PYPL", "COF", "USB"],
    "Healthcare": ["UNH", "LLY", "JNJ", "MRK", "ABBV", "PFE", "TMO", "ABT", "DHR", "BMY",
                   "AMGN", "GILD", "CVS", "ISRG", "VRTX"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "OXY", "MPC", "PSX"],
    "Industrials": ["CAT", "BA", "HON", "GE", "UNP", "RTX", "LMT", "DE", "UPS", "MMM"],
    "Utilities": ["NEE", "DUK", "SO", "D"],
    "Real Estate": ["PLD", "AMT", "EQIX"],
    "Materials": ["LIN", "APD", "SHW", "FCX"],
    "Crypto": ["BTC-USD", "ETH-USD", "COIN", "MSTR", "MARA", "RIOT", "HOOD"],
}
TICKER_SECTOR = {t: sec for sec, ts in _SECTOR_GROUPS.items() for t in ts}

# Sector detected from headline keywords even when no ticker is named.
_SECTOR_KEYWORDS = {
    "Technology": ["semiconductor", "chip", "chipmaker", " ai ", "artificial intelligence",
                   "cloud", "software", "gpu", "data center"],
    "Energy": ["oil", "crude", "natural gas", "opec", "energy price", "gasoline"],
    "Financials": ["bank", "interest rate", "federal reserve", " the fed", "bond yield",
                   "treasury yield", "rate cut", "rate hike"],
    "Healthcare": ["fda", "drug", "biotech", "pharma", "clinical trial", "vaccine"],
    "Crypto": ["bitcoin", "ethereum", "crypto", "blockchain", "stablecoin"],
    "Consumer Discretionary": ["retail sales", "consumer spending", "electric vehicle", " ev "],
    "Real Estate": ["housing", "mortgage", "home sales", "real estate"],
    "Industrials": ["aerospace", "defense", "manufacturing"],
}

# Words that look like tickers but aren't — never match these as bare symbols.
_STOPWORDS = {
    "CEO", "CFO", "USA", "GDP", "CPI", "PPI", "IPO", "ETF", "SEC", "FED", "FDA", "EPS",
    "USD", "EUR", "AI", "IT", "ON", "BE", "GO", "SO", "OR", "AT", "BY", "UP", "OUT",
    "ALL", "ARE", "FOR", "NEW", "NOW", "THE", "AND", "BUT", "CAN", "GET", "HAS", "WAS",
    "WILL", "YOU", "PM", "AM", "US", "UK", "EU", "Q1", "Q2", "Q3", "Q4", "YOY", "TV",
    "OK", "NYSE", "ATH", "DD", "YOLO", "HOLD", "BUY", "PT", "WSB",
}


def _ticker_universe():
    try:
        from screener import LARGECAP_TICKERS, MIDCAP_TICKERS, LOWCAP_TICKERS
        uni = set(LARGECAP_TICKERS) | set(MIDCAP_TICKERS) | set(LOWCAP_TICKERS)
    except Exception:
        uni = set()
    uni |= set(TICKER_SECTOR.keys())
    return uni


_UNIVERSE = None
_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
_WORD = re.compile(r"\b([A-Z]{3,5})\b")


def _extract_tickers(text):
    """Cashtags ($AAPL, any) plus bare 3–5 letter symbols that are in our
    universe and not common-word false positives."""
    global _UNIVERSE
    if _UNIVERSE is None:
        _UNIVERSE = _ticker_universe()
    found = set()
    for m in _CASHTAG.findall(text or ""):
        sym = m.upper()
        if sym in _UNIVERSE or len(sym) >= 2:
            found.add(sym)
    for m in _WORD.findall(text or ""):
        sym = m.upper()
        if sym in _UNIVERSE and sym not in _STOPWORDS:
            found.add(sym)
    return sorted(found)


def _extract_sectors(text, tickers):
    sectors = set()
    for t in tickers:
        s = TICKER_SECTOR.get(t)
        if s:
            sectors.add(s)
    low = " " + (text or "").lower() + " "
    for sec, kws in _SECTOR_KEYWORDS.items():
        if any(kw in low for kw in kws):
            sectors.add(sec)
    return sorted(sectors)


def _sentiment(text):
    try:
        from shared.news_fetcher import _classify_sentiment
        return _classify_sentiment(text or "")
    except Exception:
        return "neutral"


def _parse_published(entry):
    for key in ("published_parsed", "updated_parsed"):
        tp = entry.get(key)
        if tp:
            try:
                return datetime(*tp[:6]).isoformat()
            except Exception:
                pass
    return datetime.utcnow().isoformat()


def _insert(article):
    """Insert one article, ignoring URL duplicates. Returns True if inserted."""
    cols = "(source, category, title, summary, url, published_at, tickers, sectors, sentiment)"
    vals = (article["source"], article["category"], article["title"][:500],
            article["summary"][:2000], article["url"], article["published_at"],
            article["tickers"], article["sectors"], article["sentiment"])
    ph = ", ".join([P] * 9)
    conflict = "ON CONFLICT (url) DO NOTHING" if IS_POSTGRES else "OR IGNORE"
    if IS_POSTGRES:
        sql = f"INSERT INTO news_articles {cols} VALUES ({ph}) {conflict}"
    else:
        sql = f"INSERT {conflict} INTO news_articles {cols} VALUES ({ph})"
    try:
        execute(sql, vals)
        return True
    except Exception as e:
        logger.debug(f"news insert failed: {e}")
        return False


def poll_feeds():
    """Fetch every feed once, tag + store new items, then prune old rows.
    Returns the number of new articles inserted."""
    import socket
    import feedparser

    # Bound each fetch so one slow/hung feed can't stall the whole sweep.
    _prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(15)
    inserted = 0
    try:
      for feed in FEEDS:
        try:
            parsed = feedparser.parse(feed["url"], agent=_UA)
            for entry in (parsed.entries or [])[:40]:
                url = (entry.get("link") or "").strip()
                title = (entry.get("title") or "").strip()
                if not url or not title:
                    continue
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", "") or "")[:2000]
                text = f"{title}. {summary}"
                tickers = _extract_tickers(text)
                sectors = _extract_sectors(text, tickers)
                if _insert({
                    "source": feed["name"], "category": feed["category"],
                    "title": title, "summary": summary, "url": url,
                    "published_at": _parse_published(entry),
                    "tickers": ",".join(tickers), "sectors": ",".join(sectors),
                    "sentiment": _sentiment(text),
                }):
                    inserted += 1
        except Exception as e:
            logger.warning(f"[NEWS] feed '{feed['name']}' failed: {e}")
        # Yield between feeds so XML parsing can't starve the web worker (the
        # container healthcheck pings /healthz on a short timeout; a CPU-bound
        # parse loop with no yields can trip it and get the container killed).
        time.sleep(0.5)
    finally:
        socket.setdefaulttimeout(_prev_timeout)
    _prune()
    if inserted:
        logger.info(f"[NEWS] ingested {inserted} new articles")
    return inserted


def _prune():
    cutoff = (datetime.utcnow() - timedelta(days=RETENTION_DAYS)).isoformat()
    try:
        execute(f"DELETE FROM news_articles WHERE created_at < {P}", (cutoff,))
    except Exception as e:
        logger.debug(f"news prune failed: {e}")


# ── Query helpers (consumed by routes + analyzer/bot/screener) ──────────
def recent(limit=50, ticker=None, category=None, source=None, hours=None):
    where, params = [], []
    if ticker:
        where.append(f"tickers LIKE {P}")
        params.append(f"%{ticker.upper()}%")
    if category:
        where.append(f"category = {P}")
        params.append(category)
    if source:
        where.append(f"source = {P}")
        params.append(source)
    if hours:
        cutoff = (datetime.utcnow() - timedelta(hours=int(hours))).isoformat()
        where.append(f"COALESCE(published_at, created_at) >= {P}")
        params.append(cutoff)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(int(limit))
    rows = query(
        f"SELECT source, category, title, summary, url, published_at, tickers, "
        f"sectors, sentiment FROM news_articles {clause} "
        f"ORDER BY COALESCE(published_at, created_at) DESC LIMIT {P}", tuple(params))
    return [dict(r) for r in (rows or [])]


def ticker_signal(ticker, hours=48):
    """Compact news-buzz signal for one ticker — consumed by the screener/bots.
    Returns {mentions, reddit_mentions, sentiment_score (-1..1), buzz}."""
    if not ticker:
        return {"mentions": 0, "reddit_mentions": 0, "sentiment_score": 0.0, "buzz": False}
    cutoff = (datetime.utcnow() - timedelta(hours=int(hours))).isoformat()
    rows = query(
        f"SELECT sentiment, category FROM news_articles "
        f"WHERE tickers LIKE {P} AND COALESCE(published_at, created_at) >= {P}",
        (f"%{ticker.upper()}%", cutoff))
    rows = rows or []
    sents = [r["sentiment"] or "neutral" for r in rows]
    reddit = sum(1 for r in rows if r["category"] == "reddit")
    return {"mentions": len(rows), "reddit_mentions": reddit,
            "sentiment_score": _sent_score(sents), "buzz": len(rows) >= 3}


def _sent_score(sentiments):
    b = sentiments.count("bullish")
    r = sentiments.count("bearish")
    n = len(sentiments) or 1
    return round((b - r) / n, 2)


def trending(hours=24, limit=10):
    """Aggregate ticker + sector mentions (and net sentiment) over the window
    to surface what's trending in the news/Reddit feed."""
    cutoff = (datetime.utcnow() - timedelta(hours=int(hours))).isoformat()
    rows = query(
        f"SELECT tickers, sectors, sentiment, category FROM news_articles "
        f"WHERE COALESCE(published_at, created_at) >= {P}", (cutoff,))
    tick_ct, tick_sent, tick_reddit = {}, {}, {}
    sec_ct, sec_sent = {}, {}
    for r in (rows or []):
        sent = r["sentiment"] or "neutral"
        for t in (r["tickers"] or "").split(","):
            t = t.strip()
            if not t:
                continue
            tick_ct[t] = tick_ct.get(t, 0) + 1
            tick_sent.setdefault(t, []).append(sent)
            if r["category"] == "reddit":
                tick_reddit[t] = tick_reddit.get(t, 0) + 1
        for s in (r["sectors"] or "").split(","):
            s = s.strip()
            if not s:
                continue
            sec_ct[s] = sec_ct.get(s, 0) + 1
            sec_sent.setdefault(s, []).append(sent)

    stocks = sorted(
        [{"ticker": t, "mentions": c, "reddit_mentions": tick_reddit.get(t, 0),
          "sentiment_score": _sent_score(tick_sent[t]),
          "sector": TICKER_SECTOR.get(t)} for t, c in tick_ct.items()],
        key=lambda x: x["mentions"], reverse=True)[:limit]
    sectors = sorted(
        [{"sector": s, "mentions": c, "sentiment_score": _sent_score(sec_sent[s])}
         for s, c in sec_ct.items()],
        key=lambda x: x["mentions"], reverse=True)[:limit]
    return {"hours": int(hours), "stocks": stocks, "sectors": sectors,
            "total_articles": len(rows or [])}


# ── Background scanner ──────────────────────────────────────────────────
_scanner_thread = None
_scanner_running = False


def is_scanner_running():
    return _scanner_running


def _scanner_loop():
    global _scanner_running
    _scanner_running = True
    logger.info("[NEWS] scanner started")
    # Defer the first heavy sweep so app startup + healthcheck settle first.
    for _ in range(45):
        if not _scanner_running:
            return
        time.sleep(1)
    while _scanner_running:
        try:
            poll_feeds()
        except Exception as e:
            logger.error(f"[NEWS] scan cycle error: {e}")
        for _ in range(POLL_INTERVAL):
            if not _scanner_running:
                break
            time.sleep(1)


def start_scanner():
    global _scanner_thread, _scanner_running
    if _scanner_running:
        return
    _scanner_thread = threading.Thread(target=_scanner_loop, daemon=True, name="news-agent")
    _scanner_thread.start()


def stop_scanner():
    global _scanner_running
    _scanner_running = False
