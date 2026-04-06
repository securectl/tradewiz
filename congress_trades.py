"""
Congress Trades — Track politician stock holdings and trades.
Sources: House Clerk PTR PDFs (parsed for actual trades), GDELT news for Senate.
Provides search/filter by chamber, party, ticker, date range.
"""

import os
import io
import re
import json
import logging
import time
import zipfile
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timedelta

QUIVER_API_URL = "https://api.quiverquant.com/beta/live/congresstrading"

logger = logging.getLogger(__name__)

_cache = {}
CACHE_TTL = 3600  # 1 hour

def _cached(key):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None

def _set_cache(key, data):
    _cache[key] = (data, time.time())


HOUSE_CLERK_BASE = "https://disclosures-clerk.house.gov"


# ─── PDF Trade Extraction ─────────────────────────────────────

def _parse_ptr_pdf(pdf_bytes):
    """Parse a House PTR PDF to extract individual stock trades.
    Returns list of {asset, ticker, type, date, amount_low, amount_high}.
    House PTR PDFs have a specific format where trade data is in the page text."""
    trades = []
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                trades.extend(_parse_ptr_text(text))
    except ImportError:
        logger.warning("pdfplumber not installed — cannot parse PTR PDFs")
    except Exception as e:
        logger.warning(f"PDF parse error: {e}")
    return trades


def _parse_ptr_row(row):
    """Parse a single row from a PTR PDF table."""
    # Clean nulls
    cells = [str(c).strip() if c else "" for c in row]
    full = " ".join(cells).lower()

    # Skip header rows
    if any(h in full for h in ["transaction", "asset", "owner", "notification", "description"]):
        if "purchase" not in full and "sale" not in full:
            return None

    # Find transaction type
    tx_type = None
    for cell in cells:
        cl = cell.lower()
        if "purchase" in cl or "buy" in cl:
            tx_type = "Purchase"
        elif "sale" in cl and "partial" in cl:
            tx_type = "Sale (Partial)"
        elif "sale" in cl:
            tx_type = "Sale (Full)"
        elif "exchange" in cl:
            tx_type = "Exchange"

    if not tx_type:
        return None

    # Find asset name and ticker
    asset = ""
    ticker = ""
    for cell in cells:
        # Look for ticker pattern: (AAPL) or [AAPL] or AAPL -
        t_match = re.search(r'[\(\[]([\w]{1,5})[\)\]]', cell)
        if t_match:
            ticker = t_match.group(1).upper()
        # Asset description is usually the longest cell
        if len(cell) > len(asset) and not cell.replace(",", "").replace("$", "").replace(" ", "").isdigit():
            if cell.lower() not in ["purchase", "sale", "sale (full)", "sale (partial)", "exchange"]:
                asset = cell

    # Find amount range
    amount_low = 0
    amount_high = 0
    for cell in cells:
        # Match patterns like "$1,001 - $15,000" or "$15,001-$50,000"
        amt_match = re.search(r'\$?([\d,]+)\s*[-–]\s*\$?([\d,]+)', cell)
        if amt_match:
            amount_low = int(amt_match.group(1).replace(",", ""))
            amount_high = int(amt_match.group(2).replace(",", ""))
            break

    # Find date
    trade_date = ""
    for cell in cells:
        d_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', cell)
        if d_match:
            trade_date = d_match.group(1)
            break

    if not asset and not ticker:
        return None

    return {
        "asset": asset[:100],
        "ticker": ticker,
        "type": tx_type,
        "date": trade_date,
        "amount_low": amount_low,
        "amount_high": amount_high,
    }


def _parse_ptr_text(text):
    """Parse trades from PTR PDF text. Format example:
    'GSK plc American Depositary Shares S 07/28/2025 08/11/2025 $1,001 - $15,000'
    '(GSK) [ST]'
    or:
    'Apple Inc. P 01/15/2025 02/01/2025 $50,001 - $100,000'
    '(AAPL) [ST]'
    Transaction codes: P=Purchase, S=Sale, S(P)=Sale(Partial), E=Exchange
    """
    trades = []
    full_text = text.replace("\n", " ")

    # Pattern: asset name ... TX_CODE ... date ... date ... $amount - $amount ... (TICKER) [type]
    # The key pattern is: a transaction code (P, S, SP, S(P), E) followed by date patterns and amount
    pattern = re.compile(
        r'([A-Za-z][\w\s\.\,\&\-\']+?)\s+'       # Asset name
        r'(P|S|SP|S\s*\(P(?:artial)?\)|E|Purchase|Sale)\s+'  # Transaction type
        r'(\d{2}/\d{2}/\d{4})\s+'                  # Transaction date
        r'(\d{2}/\d{2}/\d{4})\s+'                  # Notification date
        r'\$([\d,]+)\s*[-–]\s*\$([\d,]+)',             # Amount range ($X - $Y)
        re.IGNORECASE
    )

    for match in pattern.finditer(full_text):
        asset = match.group(1).strip()
        tx_code = match.group(2).strip().upper()
        trade_date = match.group(3)
        amount_low = int(match.group(5).replace(",", ""))
        amount_high = int(match.group(6).replace(",", ""))

        # Map transaction codes
        if tx_code in ("P", "PURCHASE"):
            tx_type = "Purchase"
        elif tx_code in ("S", "SALE"):
            tx_type = "Sale (Full)"
        elif "PARTIAL" in tx_code or tx_code == "SP":
            tx_type = "Sale (Partial)"
        elif tx_code == "E":
            tx_type = "Exchange"
        else:
            tx_type = tx_code

        # Look for ticker in parentheses near the match
        start_pos = match.end()
        nearby = full_text[start_pos:start_pos + 80]
        ticker_match = re.search(r'\(([A-Z]{1,6})\)', nearby)
        # Also check before the match
        if not ticker_match:
            before = full_text[max(0, match.start() - 30):match.start()]
            ticker_match = re.search(r'\(([A-Z]{1,6})\)', before)
        ticker = ticker_match.group(1) if ticker_match else ""

        # Clean asset name (remove trailing whitespace, truncate)
        asset = re.sub(r'\s+', ' ', asset).strip()[:100]

        trades.append({
            "asset": asset,
            "ticker": ticker,
            "type": tx_type,
            "date": trade_date,
            "amount_low": amount_low,
            "amount_high": amount_high,
        })

    return trades


def _fetch_and_parse_ptr(doc_id, year):
    """Download and parse a single PTR PDF."""
    cache_key = f"ptr_{doc_id}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    try:
        url = f"{HOUSE_CLERK_BASE}/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return []
        trades = _parse_ptr_pdf(resp.content)
        _set_cache(cache_key, trades)
        return trades
    except Exception as e:
        logger.warning(f"Failed to fetch/parse PTR {doc_id}: {e}")
        return []


# ─── House Clerk Index ────────────────────────────────────────

def _fetch_house_index(year=None):
    """Fetch House PTR filing index from official clerk."""
    if year is None:
        year = datetime.now().year

    cached = _cached(f"house_index_{year}")
    if cached:
        return cached

    filings = []
    try:
        url = f"{HOUSE_CLERK_BASE}/public_disc/financial-pdfs/{year}FD.zip"
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            return []

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_files = [n for n in zf.namelist() if n.endswith('.xml')]
            if not xml_files:
                return []

            with zf.open(xml_files[0]) as f:
                tree = ET.parse(f)
                root = tree.getroot()

                for member in root.iter('Member'):
                    ft = _txt(member, 'FilingType')
                    if ft != 'P':
                        continue

                    filings.append({
                        "name": f"{_txt(member, 'First')} {_txt(member, 'Last')}".strip(),
                        "state": _txt(member, 'StateDst')[:2],
                        "district": _txt(member, 'StateDst')[2:],
                        "chamber": "House",
                        "filing_date": _txt(member, 'FilingDate'),
                        "doc_id": _txt(member, 'DocID'),
                        "year": year,
                    })

        _set_cache(f"house_index_{year}", filings)
        logger.info(f"House index: {len(filings)} PTR filings for {year}")
    except Exception as e:
        logger.error(f"House index fetch failed: {e}")
    return filings


def _txt(elem, tag):
    child = elem.find(tag)
    return child.text.strip() if child is not None and child.text else ''


# ─── Senate via News ──────────────────────────────────────────

# Only actual CURRENT US Senators (not House members)
KNOWN_SENATORS = {
    "Tuberville": {"full": "Tommy Tuberville", "party": "R", "state": "AL"},
    "Hagerty": {"full": "Bill Hagerty", "party": "R", "state": "TN"},
    "Ricketts": {"full": "Pete Ricketts", "party": "R", "state": "NE"},
    "Mullin": {"full": "Markwayne Mullin", "party": "R", "state": "OK"},
    "Britt": {"full": "Katie Britt", "party": "R", "state": "AL"},
    "Ossoff": {"full": "Jon Ossoff", "party": "D", "state": "GA"},
    "Hickenlooper": {"full": "John Hickenlooper", "party": "D", "state": "CO"},
    "Sullivan": {"full": "Dan Sullivan", "party": "R", "state": "AK"},
    "Cassidy": {"full": "Bill Cassidy", "party": "R", "state": "LA"},
    "Cruz": {"full": "Ted Cruz", "party": "R", "state": "TX"},
    "Rubio": {"full": "Marco Rubio", "party": "R", "state": "FL"},
    "Warner": {"full": "Mark Warner", "party": "D", "state": "VA"},
    "King": {"full": "Angus King", "party": "I", "state": "ME"},
    "McConnell": {"full": "Mitch McConnell", "party": "R", "state": "KY"},
    "Fetterman": {"full": "John Fetterman", "party": "D", "state": "PA"},
    "Scott": {"full": "Tim Scott", "party": "R", "state": "SC"},
    "Wicker": {"full": "Roger Wicker", "party": "R", "state": "MS"},
    "Marshall": {"full": "Roger Marshall", "party": "R", "state": "KS"},
    "Blackburn": {"full": "Marsha Blackburn", "party": "R", "state": "TN"},
    "Capito": {"full": "Shelley Moore Capito", "party": "R", "state": "WV"},
    "Hoeven": {"full": "John Hoeven", "party": "R", "state": "ND"},
    "Daines": {"full": "Steve Daines", "party": "R", "state": "MT"},
    "Lummis": {"full": "Cynthia Lummis", "party": "R", "state": "WY"},
    "Booker": {"full": "Cory Booker", "party": "D", "state": "NJ"},
    "Tester": {"full": "Jon Tester", "party": "D", "state": "MT"},
}

# Known House members to EXCLUDE from Senate results
KNOWN_HOUSE_MEMBERS = {
    "pelosi", "moore", "alford", "taylor", "delaney", "mcclain", "letlow",
    "biggs", "allen", "cohen", "donalds", "cisneros", "morrison",
}

# Company name → ticker map for extracting tickers from headlines
COMPANY_TICKERS = {
    "nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT", "amazon": "AMZN",
    "google": "GOOGL", "alphabet": "GOOGL", "meta": "META", "tesla": "TSLA",
    "amd": "AMD", "palantir": "PLTR", "coinbase": "COIN", "netflix": "NFLX",
    "disney": "DIS", "walmart": "WMT", "jpmorgan": "JPM", "goldman": "GS",
    "bank of america": "BAC", "intel": "INTC", "salesforce": "CRM",
    "broadcom": "AVGO", "qualcomm": "QCOM", "adobe": "ADBE",
}

def _fetch_senate_news():
    """Fetch Senate trade disclosures from multiple news sources.
    Uses targeted GDELT queries + RSS feeds since Senate EFD is Cloudflare-blocked."""
    cached = _cached("senate_news_trades")
    if cached:
        return cached

    trades = []

    # ── GDELT: targeted senator trade queries ──
    queries = [
        '(senator OR "senate") (stock OR "stock trade" OR disclosure OR bought OR sold) (filing OR "periodic transaction") sourcelang:english',
        '("Tuberville" OR "Pelosi" OR "Hagerty" OR "Ossoff" OR "Hickenlooper") (stock OR trade OR bought OR sold) sourcelang:english',
        '"senate" "stock act" (trade OR purchase OR sale) sourcelang:english',
    ]

    for q in queries:
        try:
            url = f"https://api.gdeltproject.org/api/v2/doc/doc?query={requests.utils.quote(q)}&mode=artlist&maxrecords=30&format=json&timespan=30d"
            resp = requests.get(url, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                for article in data.get("articles", []):
                    title = article.get("title", "")
                    if not title:
                        continue

                    # Extract senator name
                    name = ""
                    for senator in KNOWN_SENATORS:
                        if senator.lower() in title.lower():
                            name = senator
                            break
                    if not name:
                        name_match = re.search(
                            r'(?:Sen(?:ator)?\.?\s+)([\w]+(?:\s+[\w]+)?)',
                            title, re.IGNORECASE
                        )
                        if name_match:
                            name = name_match.group(1)

                    # Extract ticker
                    ticker = ""
                    # Common tickers in headlines
                    ticker_match = re.search(r'\b(AAPL|MSFT|NVDA|GOOGL|META|AMZN|TSLA|AMD|PLTR|COIN|SPY|QQQ)\b', title)
                    if ticker_match:
                        ticker = ticker_match.group(1)

                    # Determine buy/sell
                    tl = title.lower()
                    is_buy = any(w in tl for w in ["bought", "purchase", "buy", "buys", "acquired"])
                    is_sell = any(w in tl for w in ["sold", "sale", "sell", "sells", "dump", "unloaded"])
                    tx_type = "Purchase" if is_buy else "Sale" if is_sell else "Disclosure"

                    # Deduplicate by URL
                    url_val = article.get("url", "")
                    if any(t.get("url") == url_val for t in trades):
                        continue

                    trades.append({
                        "name": name,
                        "chamber": "Senate",
                        "state": "",
                        "ticker": ticker,
                        "type": tx_type,
                        "headline": title[:200],
                        "url": url_val,
                        "date": article.get("seendate", "")[:10],
                        "source": "news",
                        "amount_low": 0,
                        "amount_high": 0,
                    })
        except Exception as e:
            logger.warning(f"Senate GDELT query failed: {e}")
            continue

    # ── QuiverQuant API (free structured Senate trade data) ──
    try:
        resp = requests.get(QUIVER_API_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            qdata = resp.json()
            senate_count = 0
            for t in qdata:
                chamber_raw = t.get("House", "")
                if chamber_raw != "Senate":
                    continue

                name = t.get("Representative", "")
                ticker = t.get("Ticker", "")
                tx = t.get("Transaction", "")
                tx_type = "Purchase" if "purchase" in tx.lower() else "Sale" if "sale" in tx.lower() else tx

                # Amount parsing from Range field (e.g. "$1,001 - $15,000")
                amount_low, amount_high = 0, 0
                amount_str = t.get("Range", "")
                if amount_str:
                    amt_match = re.search(r'\$?([\d,]+)\s*[-–]\s*\$?([\d,]+)', amount_str.replace("$", ""))
                    if amt_match:
                        amount_low = int(amt_match.group(1).replace(",", ""))
                        amount_high = int(amt_match.group(2).replace(",", ""))

                trades.append({
                    "name": name,
                    "chamber": "Senate",
                    "party": t.get("Party", ""),
                    "state": "",
                    "ticker": ticker[:6] if ticker else "",
                    "asset": t.get("Description", "") or "",
                    "type": tx_type,
                    "date": t.get("TransactionDate", ""),
                    "filing_date": t.get("ReportDate", ""),
                    "amount_low": amount_low,
                    "amount_high": amount_high,
                    "source": "quiver_api",
                    "url": "",
                    "headline": f"{name} {tx_type} {ticker}" if ticker else f"{name} {tx_type}",
                })
                senate_count += 1
            logger.info(f"QuiverQuant Senate: {senate_count} trades")
    except Exception as e:
        logger.warning(f"QuiverQuant Senate fetch failed: {e}")

    # ── Google News RSS (fast, reliable) — Senate-specific queries ──
    news_rss_feeds = [
        "https://news.google.com/rss/search?q=senator+stock+trade+disclosure+filing&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=senate+%22periodic+transaction%22+OR+%22stock+act%22+disclosure&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=Tuberville+OR+Hagerty+OR+Ricketts+OR+Ossoff+OR+Hickenlooper+stock+trade&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=Britt+OR+Mullin+OR+Cassidy+OR+Lummis+stock+disclosure&hl=en-US&gl=US&ceid=US:en",
    ]
    try:
        import feedparser
        for feed_url in news_rss_feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:20]:
                    title = entry.get("title", "")
                    if not title:
                        continue
                    tl = title.lower()

                    # Must be about stock trading/disclosure, not just policy
                    if not any(kw in tl for kw in ["stock", "trade", "bought", "sold", "disclosure", "filing", "transaction", "shares"]):
                        continue

                    # Skip pure opinion/policy articles (no specific trade info)
                    if any(skip in tl for skip in ["opinion |", "should congress", "ban stock", "rein in", "it's time for"]):
                        continue

                    # ── Extract senator name (ONLY match known senators) ──
                    name = ""
                    party = ""
                    state = ""
                    for last_name, info in KNOWN_SENATORS.items():
                        if last_name.lower() in tl:
                            name = info["full"]
                            party = info["party"]
                            state = info["state"]
                            break

                    # If no known senator matched, try "Sen." / "Senator" pattern
                    if not name:
                        nm = re.search(r'(?:Sen(?:ator)?\.?\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', title)
                        if nm:
                            candidate = nm.group(1)
                            # Reject if it's a known House member
                            if candidate.split()[0].lower() not in KNOWN_HOUSE_MEMBERS:
                                name = candidate

                    # Skip if headline is about a known House member (not Senate)
                    if any(hm in tl for hm in KNOWN_HOUSE_MEMBERS):
                        if not name:  # Only skip if we didn't already match a senator
                            continue

                    # ── Extract ticker from company names AND symbols ──
                    ticker = ""
                    # Check explicit ticker symbols
                    tk = re.search(r'\b(AAPL|MSFT|NVDA|GOOGL|META|AMZN|TSLA|AMD|PLTR|COIN|SPY|QQQ|INTC|BAC|JPM|GS|WMT|DIS|NFLX|CRM|AVGO|QCOM|ADBE)\b', title)
                    if tk:
                        ticker = tk.group(1)
                    else:
                        # Check company names
                        for company, tkr in COMPANY_TICKERS.items():
                            if company in tl:
                                ticker = tkr
                                break

                    # ── Transaction type ──
                    is_buy = any(w in tl for w in ["bought", "purchase", "buy", "buys", "acquired"])
                    is_sell = any(w in tl for w in ["sold", "sale", "sell", "sells", "dump", "unloaded"])
                    tx_type = "Purchase" if is_buy else "Sale" if is_sell else "Disclosure"

                    # Deduplicate
                    url_val = entry.get("link", "")
                    if any(t.get("url") == url_val for t in trades):
                        continue

                    # Parse date
                    pub = entry.get("published", "")
                    trade_date = ""
                    if pub:
                        try:
                            from email.utils import parsedate_to_datetime
                            dt = parsedate_to_datetime(pub)
                            trade_date = dt.strftime("%Y-%m-%d")
                        except Exception:
                            trade_date = pub[:10]

                    trades.append({
                        "name": name,
                        "chamber": "Senate",
                        "party": party,
                        "state": state,
                        "ticker": ticker,
                        "type": tx_type,
                        "headline": title[:200],
                        "url": url_val,
                        "date": trade_date,
                        "source": "google_news",
                        "amount_low": 0,
                        "amount_high": 0,
                    })
            except Exception:
                continue
    except ImportError:
        pass

    # ── Reddit RSS feeds ──
    rss_feeds = [
        "https://www.reddit.com/r/StockMarket/search.rss?q=senator+stock+trade&sort=new&t=month",
        "https://www.reddit.com/r/wallstreetbets/search.rss?q=congress+stock&sort=new&t=month",
    ]
    try:
        import feedparser
        for feed_url in rss_feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:10]:
                    title = entry.get("title", "")
                    if not any(kw in title.lower() for kw in ["senator", "senate", "congress", "pelosi", "tuberville"]):
                        continue
                    trades.append({
                        "name": "",
                        "chamber": "Senate",
                        "state": "",
                        "ticker": "",
                        "type": "Discussion",
                        "headline": title[:200],
                        "url": entry.get("link", ""),
                        "date": entry.get("published", "")[:10],
                        "source": "reddit",
                        "amount_low": 0,
                        "amount_high": 0,
                    })
            except Exception:
                continue
    except ImportError:
        pass

    _set_cache("senate_news_trades", trades)
    logger.info(f"Senate news: {len(trades)} items from GDELT + RSS")
    return trades


# ─── Public API ───────────────────────────────────────────────

def get_congress_trades(chamber=None, name=None, state=None, ticker=None,
                        days=90, year=None):
    """Get congressional trade filings with actual trade data from PDFs."""

    if year is None:
        year = datetime.now().year

    cutoff = datetime.now() - timedelta(days=days)

    # ── House filings + PDF parsing ──
    all_trades = []
    if chamber in (None, "house"):
        index = _fetch_house_index(year)
        if days > 180:
            index += _fetch_house_index(year - 1)

        # Filter by date
        for f in index:
            try:
                fd = datetime.strptime(f["filing_date"], "%m/%d/%Y")
                if fd < cutoff:
                    continue
            except (ValueError, KeyError):
                pass

            # Filter by name
            if name and name.lower() not in f.get("name", "").lower():
                continue
            # Filter by state
            if state and f.get("state", "").upper() != state.upper():
                continue

            # Parse the top PTR PDFs (limit to 30 to avoid timeout)
            if len(all_trades) < 500:
                pdf_trades = _fetch_and_parse_ptr(f["doc_id"], f["year"])
                for t in pdf_trades:
                    # Filter by ticker if specified
                    if ticker and ticker.upper() != t.get("ticker", "").upper():
                        continue
                    all_trades.append({
                        **t,
                        "name": f["name"],
                        "state": f["state"],
                        "district": f["district"],
                        "chamber": "House",
                        "filing_date": f["filing_date"],
                        "doc_id": f["doc_id"],
                        "pdf_url": f"{HOUSE_CLERK_BASE}/public_disc/ptr-pdfs/{f['year']}/{f['doc_id']}.pdf",
                    })

    # ── Senate (news-based) ──
    senate_trades = []
    if chamber in (None, "senate"):
        senate_news = _fetch_senate_news()
        for t in senate_news:
            if name and name.lower() not in t.get("name", "").lower():
                continue
            if ticker and ticker.upper() != t.get("ticker", "").upper():
                continue
            senate_trades.append(t)

    # ── Aggregate stats ──
    combined = all_trades + senate_trades

    # Include structured senate trades (from QuiverQuant) in stats alongside House
    structured_trades = all_trades + [t for t in senate_trades if t.get("source") == "quiver_api"]

    # Top buys and sells
    buys = [t for t in structured_trades if t.get("type", "").lower().startswith("purchase")]
    sells = [t for t in structured_trades if "sale" in t.get("type", "").lower()]
    buys.sort(key=lambda x: x.get("amount_high", 0), reverse=True)
    sells.sort(key=lambda x: x.get("amount_high", 0), reverse=True)

    # Ticker frequency
    ticker_counts = {}
    for t in structured_trades:
        tk = t.get("ticker", "")
        if tk:
            ticker_counts.setdefault(tk, {"buys": 0, "sells": 0, "total_high": 0})
            if "purchase" in t.get("type", "").lower():
                ticker_counts[tk]["buys"] += 1
            elif "sale" in t.get("type", "").lower():
                ticker_counts[tk]["sells"] += 1
            ticker_counts[tk]["total_high"] += t.get("amount_high", 0)

    hot_tickers = sorted(ticker_counts.items(), key=lambda x: x[1]["total_high"], reverse=True)[:15]

    # Member stats
    member_counts = {}
    for t in structured_trades:
        n = t.get("name", "Unknown")
        member_counts[n] = member_counts.get(n, 0) + 1
    top_traders = sorted(member_counts.items(), key=lambda x: x[1], reverse=True)[:15]

    # State stats
    state_counts = {}
    for t in structured_trades:
        s = t.get("state", "??")
        state_counts[s] = state_counts.get(s, 0) + 1
    top_states = sorted(state_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    def _fmt_amount(low, high):
        if high >= 1000000:
            return f"${low/1000:.0f}K - ${high/1000000:.1f}M"
        elif high >= 1000:
            return f"${low/1000:.0f}K - ${high/1000:.0f}K"
        return f"${low} - ${high}" if high > 0 else ""

    return {
        "trades": combined[:300],
        "top_buys": [{**t, "amount_str": _fmt_amount(t.get("amount_low", 0), t.get("amount_high", 0))} for t in buys[:20]],
        "top_sells": [{**t, "amount_str": _fmt_amount(t.get("amount_low", 0), t.get("amount_high", 0))} for t in sells[:20]],
        "hot_tickers": [{"ticker": tk, **data} for tk, data in hot_tickers],
        "senate_news": senate_trades[:15],
        "stats": {
            "total_trades": len(combined),
            "house_trades": len(all_trades),
            "senate_news": len(senate_trades),
            "unique_members": len(member_counts),
            "total_buys": len(buys),
            "total_sells": len(sells),
            "top_traders": [{"name": n, "trades": c} for n, c in top_traders],
            "top_states": [{"state": s, "trades": c} for s, c in top_states],
        },
        "filters": {"chamber": chamber, "name": name, "state": state, "ticker": ticker, "days": days, "year": year},
        "sources": ["House Clerk PTR PDFs (disclosures-clerk.house.gov)", "QuiverQuant (Senate trades)", "GDELT News (Senate proxy)"],
        "updated_at": datetime.now().isoformat(),
        "cached": False,
        "_note": "Senate trades from QuiverQuant API + news. House data from official PTR PDF filings.",
    }
