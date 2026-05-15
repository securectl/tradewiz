"""
Smart Money Tracker Engine — Tracks top hedge funds and notable traders via SEC filings.

Data sources:
- SEC EDGAR 13F filings (quarterly institutional holdings, free API)
- Finnhub institutional holdings API (free tier, 60 req/min)
- SEC EDGAR latest filings RSS for near-real-time 13F detection

Top 20 hedge funds and top 20 traders are pre-configured.
Holdings are fetched daily and stored historically in the database.
"""

import os
import json
import logging
import time
import threading
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from db import query, query_one, execute, IS_POSTGRES

P = "%s" if IS_POSTGRES else "?"

# ─── Top 20 Hedge Funds (by AUM / influence) ────────────────────
TOP_HEDGE_FUNDS = [
    {"name": "Berkshire Hathaway", "cik": "0001067983", "aum": 350, "desc": "Warren Buffett's conglomerate"},
    {"name": "Bridgewater Associates", "cik": "0001350694", "aum": 124, "desc": "Ray Dalio's macro fund"},
    {"name": "Citadel Advisors", "cik": "0001423053", "aum": 66, "desc": "Ken Griffin's multi-strategy fund"},
    {"name": "Renaissance Technologies", "cik": "0001037389", "aum": 55, "desc": "Jim Simons' quant fund"},
    {"name": "Two Sigma Investments", "cik": "0001179392", "aum": 52, "desc": "Quant/AI-driven fund"},
    {"name": "D.E. Shaw", "cik": "0001009207", "aum": 50, "desc": "Quant/systematic strategies"},
    {"name": "Millennium Management", "cik": "0001547949", "aum": 48, "desc": "Israel Englander's multi-strategy"},
    {"name": "Elliott Management", "cik": "0001048445", "aum": 45, "desc": "Paul Singer's activist fund"},
    {"name": "Point72 Asset Management", "cik": "0001603466", "aum": 32, "desc": "Steve Cohen's fund"},
    {"name": "Tiger Global Management", "cik": "0001167483", "aum": 30, "desc": "Chase Coleman's tech-focused fund"},
    {"name": "Pershing Square", "cik": "0001336528", "aum": 18, "desc": "Bill Ackman's concentrated fund"},
    {"name": "Appaloosa Management", "cik": "0001656456", "aum": 14, "desc": "David Tepper's event-driven fund"},
    {"name": "Baupost Group", "cik": "0001061768", "aum": 25, "desc": "Seth Klarman's value fund"},
    {"name": "Viking Global Investors", "cik": "0001103804", "aum": 28, "desc": "Andreas Halvorsen's long/short fund"},
    {"name": "Third Point", "cik": "0001040273", "aum": 15, "desc": "Dan Loeb's event-driven fund"},
    {"name": "Coatue Management", "cik": "0001535392", "aum": 22, "desc": "Philippe Laffont's tech fund"},
    {"name": "Lone Pine Capital", "cik": "0001061165", "aum": 16, "desc": "Stephen Mandel's growth fund"},
    {"name": "Greenlight Capital", "cik": "0001079114", "aum": 4, "desc": "David Einhorn's value/activist fund"},
    {"name": "Icahn Capital", "cik": "0001412093", "aum": 15, "desc": "Carl Icahn's activist fund"},
    {"name": "Soros Fund Management", "cik": "0001029160", "aum": 20, "desc": "George Soros' macro fund"},
]

# ─── Top 20 Notable Traders / Investors ──────────────────────────
TOP_TRADERS = [
    {"name": "Warren Buffett", "entity": "Berkshire Hathaway", "style": "Value"},
    {"name": "Ray Dalio", "entity": "Bridgewater Associates", "style": "Macro"},
    {"name": "Ken Griffin", "entity": "Citadel Advisors", "style": "Multi-Strategy"},
    {"name": "Jim Simons", "entity": "Renaissance Technologies", "style": "Quantitative"},
    {"name": "Steve Cohen", "entity": "Point72 Asset Management", "style": "Multi-Strategy"},
    {"name": "Bill Ackman", "entity": "Pershing Square", "style": "Concentrated Activist"},
    {"name": "David Tepper", "entity": "Appaloosa Management", "style": "Event-Driven"},
    {"name": "Carl Icahn", "entity": "Icahn Capital", "style": "Activist"},
    {"name": "George Soros", "entity": "Soros Fund Management", "style": "Macro"},
    {"name": "Seth Klarman", "entity": "Baupost Group", "style": "Deep Value"},
    {"name": "Dan Loeb", "entity": "Third Point", "style": "Event-Driven Activist"},
    {"name": "David Einhorn", "entity": "Greenlight Capital", "style": "Value/Short"},
    {"name": "Chase Coleman", "entity": "Tiger Global Management", "style": "Growth/Tech"},
    {"name": "Philippe Laffont", "entity": "Coatue Management", "style": "Tech/Growth"},
    {"name": "Andreas Halvorsen", "entity": "Viking Global Investors", "style": "Long/Short"},
    {"name": "Israel Englander", "entity": "Millennium Management", "style": "Multi-Strategy"},
    {"name": "Paul Singer", "entity": "Elliott Management", "style": "Activist/Distressed"},
    {"name": "Nancy Pelosi", "entity": "Congress (House)", "style": "Political Insider"},
    {"name": "Michael Burry", "entity": "Scion Asset Management", "style": "Value/Contrarian"},
    {"name": "Cathie Wood", "entity": "ARK Invest", "style": "Disruptive Innovation"},
]

# Additional CIKs for traders not in hedge fund list
EXTRA_CIKS = {
    "Scion Asset Management": "0001649339",   # Michael Burry
    "ARK Invest": "0001697748",               # Cathie Wood (ARK ETF Trust)
}

SEC_HEADERS = {
    "User-Agent": "TradewizMarket research@tradewiz.market",
    "Accept": "application/json",
}

_cache = {}
_CACHE_TTL = 3600  # 1 hour


def _get_cached(key):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def _set_cached(key, data):
    _cache[key] = (data, time.time())


# ─── SEC EDGAR 13F Fetcher ──────────────────────────────────────

def _fetch_13f_holdings(cik, fund_name):
    """Fetch latest 13F holdings for a fund from SEC EDGAR.

    Uses the EDGAR API to get the most recent 13F-HR filing and parse its holdings.
    """
    try:
        # Pad CIK to 10 digits
        padded_cik = cik.lstrip("0").zfill(10)

        # Step 1: Get filing index
        url = f"https://data.sec.gov/submissions/CIK{padded_cik}.json"
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if resp.status_code != 200:
            logger.warning(f"EDGAR API failed for {fund_name} (CIK {cik}): {resp.status_code}")
            return []

        data = resp.json()
        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        accessions = filings.get("accessionNumber", [])
        dates = filings.get("filingDate", [])

        # Find most recent 13F-HR filing
        filing_idx = None
        for i, form in enumerate(forms):
            if form in ("13F-HR", "13F-HR/A"):
                filing_idx = i
                break

        if filing_idx is None:
            logger.info(f"No 13F filing found for {fund_name}")
            return []

        accession = accessions[filing_idx].replace("-", "")
        filing_date = dates[filing_idx]
        accession_formatted = accessions[filing_idx]

        # Step 2: Fetch the information table (XML holdings)
        table_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{accession}/infotable.xml"
        # Try common filenames for the holdings table
        holdings = []
        for filename in ["infotable.xml", "primary_doc.xml"]:
            try:
                table_resp = requests.get(
                    f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{accession}/{filename}",
                    headers=SEC_HEADERS, timeout=30,
                )
                if table_resp.status_code == 200 and "<infoTable" in table_resp.text:
                    holdings = _parse_13f_xml(table_resp.text, fund_name, filing_date)
                    break
            except Exception:
                continue

        # If XML didn't work, try the JSON filing index to find the right file
        # Some funds use numeric filenames (50240.xml) instead of infotable.xml
        if not holdings:
            try:
                index_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{accession}/index.json"
                idx_resp = requests.get(index_url, headers=SEC_HEADERS, timeout=15)
                if idx_resp.status_code == 200:
                    idx_data = idx_resp.json()
                    # Sort XML files by size descending — the holdings table is usually the largest XML
                    xml_files = [
                        item for item in idx_data.get("directory", {}).get("item", [])
                        if item.get("name", "").endswith(".xml") and item.get("name") != "primary_doc.xml"
                    ]
                    xml_files.sort(key=lambda x: int(x.get("size", "0").replace(",", "") or 0), reverse=True)

                    for item in xml_files:
                        fname = item.get("name", "")
                        xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{accession}/{fname}"
                        xml_resp = requests.get(xml_url, headers=SEC_HEADERS, timeout=30)
                        if xml_resp.status_code == 200 and ("<infoTable" in xml_resp.text or "<nameOfIssuer" in xml_resp.text.lower()):
                            holdings = _parse_13f_xml(xml_resp.text, fund_name, filing_date)
                            if holdings:
                                logger.info(f"[Smart Money] Found holdings in {fname} for {fund_name}")
                                break
            except Exception:
                pass

        return holdings

    except Exception as e:
        logger.warning(f"13F fetch failed for {fund_name}: {e}")
        return []


def _parse_13f_xml(xml_text, fund_name, filing_date):
    """Parse 13F-HR XML information table into holdings list."""
    import re
    holdings = []

    # Extract each infoTable entry — SEC uses various XML structures
    entries = re.findall(r'<infoTable>(.*?)</infoTable>', xml_text, re.DOTALL | re.IGNORECASE)
    if not entries:
        entries = re.findall(r'<ns1:infoTable>(.*?)</ns1:infoTable>', xml_text, re.DOTALL | re.IGNORECASE)
    if not entries:
        # Some files have the holdings as children of <informationTable>
        # Split by nameOfIssuer blocks instead
        entries = re.findall(r'(<(?:\w+:)?nameOfIssuer>.*?(?:</(?:\w+:)?shrsOrPrnAmt>|</(?:\w+:)?putCall>))', xml_text, re.DOTALL | re.IGNORECASE)
    if not entries:
        entries = re.findall(r'<informationTable>(.*?)</informationTable>', xml_text, re.DOTALL | re.IGNORECASE)

    for entry in entries:
        try:
            name_match = re.search(r'<(?:ns1:)?nameOfIssuer>(.*?)</(?:ns1:)?nameOfIssuer>', entry, re.IGNORECASE)
            cusip_match = re.search(r'<(?:ns1:)?cusip>(.*?)</(?:ns1:)?cusip>', entry, re.IGNORECASE)
            value_match = re.search(r'<(?:ns1:)?value>(.*?)</(?:ns1:)?value>', entry, re.IGNORECASE)
            shares_match = re.search(r'<(?:ns1:)?sshPrnamt>(.*?)</(?:ns1:)?sshPrnamt>', entry, re.IGNORECASE)
            type_match = re.search(r'<(?:ns1:)?sshPrnamtType>(.*?)</(?:ns1:)?sshPrnamtType>', entry, re.IGNORECASE)
            class_match = re.search(r'<(?:ns1:)?titleOfClass>(.*?)</(?:ns1:)?titleOfClass>', entry, re.IGNORECASE)
            opt_match = re.search(r'<(?:ns1:)?putCall>(.*?)</(?:ns1:)?putCall>', entry, re.IGNORECASE)

            company = name_match.group(1).strip() if name_match else "Unknown"
            cusip = cusip_match.group(1).strip() if cusip_match else ""
            value_thousands = int(value_match.group(1).strip()) if value_match else 0
            shares = int(shares_match.group(1).strip().replace(",", "")) if shares_match else 0
            share_type = type_match.group(1).strip() if type_match else "SH"
            title_class = class_match.group(1).strip() if class_match else ""
            put_call = opt_match.group(1).strip() if opt_match else ""

            # Try to find ticker from company name (basic mapping)
            ticker = _cusip_to_ticker(cusip, company)

            holdings.append({
                "company_name": company,
                "ticker": ticker,
                "cusip": cusip,
                "shares": shares,
                "value_usd": value_thousands * 1000,  # SEC reports in thousands
                "share_type": share_type,
                "title_class": title_class,
                "put_call": put_call,
                "filing_date": filing_date,
                "fund_name": fund_name,
            })
        except Exception:
            continue

    return holdings


# Simple CUSIP → Ticker cache (built from common holdings)
_CUSIP_TICKER_MAP = {}

def _cusip_to_ticker(cusip, company_name):
    """Convert CUSIP to ticker symbol. Uses cache + name heuristics."""
    if cusip in _CUSIP_TICKER_MAP:
        return _CUSIP_TICKER_MAP[cusip]

    # Common company → ticker mapping (top holdings)
    name_lower = company_name.lower()
    known = {
        "apple": "AAPL", "microsoft": "MSFT", "amazon": "AMZN", "alphabet": "GOOGL",
        "meta platforms": "META", "nvidia": "NVDA", "tesla": "TSLA", "berkshire": "BRK.B",
        "jpmorgan": "JPM", "visa": "V", "unitedhealth": "UNH", "johnson": "JNJ",
        "walmart": "WMT", "procter": "PG", "mastercard": "MA", "exxon": "XOM",
        "home depot": "HD", "chevron": "CVX", "abbvie": "ABBV", "coca-cola": "KO",
        "pfizer": "PFE", "bank of america": "BAC", "costco": "COST", "merck": "MRK",
        "broadcom": "AVGO", "salesforce": "CRM", "netflix": "NFLX", "adobe": "ADBE",
        "amd": "AMD", "advanced micro": "AMD", "cisco": "CSCO", "intel": "INTC",
        "disney": "DIS", "nike": "NKE", "paypal": "PYPL", "uber": "UBER",
        "palantir": "PLTR", "snowflake": "SNOW", "crowdstrike": "CRWD",
        "coinbase": "COIN", "shopify": "SHOP", "spotify": "SPOT",
        "spdr": "SPY", "ishares": "IVV", "vanguard": "VTI",
    }
    for pattern, ticker in known.items():
        if pattern in name_lower:
            _CUSIP_TICKER_MAP[cusip] = ticker
            return ticker

    # Try yfinance lookup as fallback
    try:
        import yfinance as yf
        # Use first word of company name
        search_term = company_name.split()[0]
        if len(search_term) <= 5 and search_term.isupper():
            _CUSIP_TICKER_MAP[cusip] = search_term
            return search_term
    except Exception:
        pass

    _CUSIP_TICKER_MAP[cusip] = company_name[:8].upper()
    return company_name[:8].upper()


# ─── Database Operations ────────────────────────────────────────

def _ensure_entity(name, entity_type, cik=None, description=None, aum=None):
    """Insert or get whale_entities record. Returns entity_id."""
    row = query_one(
        f"SELECT id FROM whale_entities WHERE name = {P} AND entity_type = {P}",
        (name, entity_type),
    )
    if row:
        # Update last_updated
        now_fn = "NOW()" if IS_POSTGRES else "datetime('now')"
        execute(
            f"UPDATE whale_entities SET last_updated = {now_fn}, "
            f"aum_billions = COALESCE({P}, aum_billions) WHERE id = {P}",
            (aum, row["id"]),
        )
        return row["id"]

    execute(
        f"INSERT INTO whale_entities (name, entity_type, cik, description, aum_billions) "
        f"VALUES ({P}, {P}, {P}, {P}, {P})",
        (name, entity_type, cik, description, aum),
    )
    row = query_one(
        f"SELECT id FROM whale_entities WHERE name = {P} AND entity_type = {P}",
        (name, entity_type),
    )
    return row["id"] if row else None


def _store_holdings(entity_id, holdings, source="sec_13f"):
    """Store holdings snapshot and detect changes from previous filing."""
    if not holdings:
        return

    # Get previous holdings for this entity to calculate changes
    prev = query(
        f"SELECT ticker, shares, value_usd FROM whale_holdings "
        f"WHERE entity_id = {P} AND filing_date = ("
        f"  SELECT MAX(filing_date) FROM whale_holdings WHERE entity_id = {P}"
        f")",
        (entity_id, entity_id),
    )
    prev_map = {r["ticker"]: r for r in (prev or [])}

    filing_date = holdings[0].get("filing_date", datetime.now().strftime("%Y-%m-%d"))

    # Check if we already have this filing date
    existing = query_one(
        f"SELECT id FROM whale_holdings WHERE entity_id = {P} AND filing_date = {P} LIMIT 1",
        (entity_id, filing_date),
    )
    if existing:
        return  # Already stored this filing

    # Calculate total portfolio value for % calculation
    total_value = sum(h.get("value_usd", 0) for h in holdings)

    for h in holdings:
        ticker = h.get("ticker", "")
        shares = h.get("shares", 0)
        value = h.get("value_usd", 0)
        pct = (value / total_value * 100) if total_value > 0 else 0

        # Calculate change from previous
        prev_holding = prev_map.get(ticker)
        if prev_holding:
            change_shares = shares - int(prev_holding.get("shares", 0) or 0)
            prev_shares = int(prev_holding.get("shares", 0) or 1)
            change_pct = (change_shares / prev_shares * 100) if prev_shares > 0 else 0
            if change_shares > 0:
                action = "INCREASED"
            elif change_shares < 0:
                action = "REDUCED"
            else:
                action = "HELD"
        else:
            change_shares = shares
            change_pct = 100
            action = "NEW"

        execute(
            f"INSERT INTO whale_holdings "
            f"(entity_id, ticker, company_name, shares, value_usd, pct_of_portfolio, "
            f"change_shares, change_pct, action, filing_date, source) "
            f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P})",
            (entity_id, ticker, h.get("company_name", ""), shares, value,
             round(pct, 2), change_shares, round(change_pct, 1), action,
             filing_date, source),
        )

        # Record significant activity
        if action in ("NEW", "INCREASED", "REDUCED") and abs(change_shares) > 0:
            execute(
                f"INSERT INTO whale_activity "
                f"(entity_id, ticker, action, shares, value_usd, filing_date, source) "
                f"VALUES ({P},{P},{P},{P},{P},{P},{P})",
                (entity_id, ticker, action, change_shares, value, filing_date, source),
            )


# ─── Main Refresh Logic ─────────────────────────────────────────

def refresh_all_funds():
    """Refresh 13F holdings for all tracked hedge funds. Called daily."""
    logger.info("[Smart Money] Starting daily refresh of institutional holdings...")
    refreshed = 0

    for fund in TOP_HEDGE_FUNDS:
        try:
            entity_id = _ensure_entity(
                fund["name"], "hedge_fund", fund["cik"], fund.get("desc"), fund.get("aum"),
            )
            if not entity_id:
                continue

            holdings = _fetch_13f_holdings(fund["cik"], fund["name"])
            if holdings:
                _store_holdings(entity_id, holdings)
                refreshed += 1
                logger.info(f"[Smart Money] {fund['name']}: {len(holdings)} holdings stored")
            else:
                logger.info(f"[Smart Money] {fund['name']}: no new 13F data")

            # Rate limit: SEC asks for 10 req/sec max
            time.sleep(0.5)

        except Exception as e:
            logger.warning(f"[Smart Money] Failed to refresh {fund['name']}: {e}")

    # Also refresh extra CIKs (Burry, Cathie Wood)
    for name, cik in EXTRA_CIKS.items():
        try:
            entity_id = _ensure_entity(name, "hedge_fund", cik)
            holdings = _fetch_13f_holdings(cik, name)
            if holdings:
                _store_holdings(entity_id, holdings)
                refreshed += 1
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"[Smart Money] Failed to refresh {name}: {e}")

    logger.info(f"[Smart Money] Refresh complete: {refreshed} funds updated")
    return refreshed


# ─── API Query Functions ─────────────────────────────────────────

def _derive_smart_money_signal(latest_filing_date, recent_activity, hot_tickers, new_positions, signals):
    """Single decision: ROTATE_IN / ROTATE_OUT / SIDELINE / WAIT_STALE.

    Same coarse-decision pattern as the Trump signal — users get one call to action,
    not a wall of holdings data.
    """
    from datetime import datetime as _dt

    n_activity = len(recent_activity or [])
    n_hot = len(hot_tickers or [])
    n_new = len(new_positions or [])
    sigs = signals or []
    accum = [s for s in sigs if s.get("signal") == "ACCUMULATING"]
    distrib = [s for s in sigs if s.get("signal") == "DISTRIBUTING"]
    n_accum = len(accum)
    n_distrib = len(distrib)

    # Filing age — 13F is naturally 45-day lagged; >120 days = stale cycle
    days_since_latest = None
    if latest_filing_date:
        try:
            d = str(latest_filing_date)[:10]
            ts = _dt.strptime(d, "%Y-%m-%d")
            days_since_latest = (_dt.now() - ts).days
        except Exception:
            pass

    # ── WAIT_STALE: data has aged out ──────────────────────────────
    is_stale = (
        (days_since_latest is not None and days_since_latest > 120)
        or (n_activity < 3 and n_hot < 3 and n_new < 2)
    )
    if is_stale:
        age_str = f"{days_since_latest}d" if days_since_latest is not None else "unknown"
        return {
            "action": "WAIT_STALE",
            "label": "WAIT — DATA STALE",
            "color": "#7e57c2",
            "headline": f"Latest 13F filing is {age_str} old — institutional positioning data is between cycles. Use Trump/screener for short-term signals.",
            "confidence": 60,
            "reasons": [
                f"Latest filing date: {str(latest_filing_date)[:10] if latest_filing_date else 'unknown'} ({age_str} ago)",
                f"Only {n_activity} recent activity entries, {n_hot} hot tickers, {n_new} new positions",
                "13F lag is 45 days minimum — data is meaningful only when filings refresh quarterly",
            ],
            "top_picks": [],
            "warnings": [],
            "next_watch": "Next 13F refresh cycle (typically ~45 days after each calendar quarter end).",
        }

    # Top picks (ACCUMULATING with most fund convergence)
    accum_sorted = sorted(accum, key=lambda s: (-int(s.get("fund_count", 0)), -float(s.get("total_value", 0))))
    distrib_sorted = sorted(distrib, key=lambda s: (-int(s.get("fund_count", 0)), -float(s.get("total_value", 0))))

    def _slim(s):
        return {
            "ticker": s.get("ticker"),
            "fund_count": int(s.get("fund_count", 0)),
            "buys": int(s.get("buys", 0)),
            "sells": int(s.get("sells", 0)),
            "total_value": round(float(s.get("total_value", 0)) / 1e6, 1),  # $M
        }

    top_picks = [_slim(s) for s in accum_sorted[:5]]
    warnings = [_slim(s) for s in distrib_sorted[:5]]

    # ── ROTATE_IN: convergence on the buy side ─────────────────────
    if n_accum >= 5 and n_accum >= n_distrib * 2:
        tickers = ", ".join(s.get("ticker", "") for s in accum_sorted[:3])
        return {
            "action": "ROTATE_IN",
            "label": "ROTATE IN",
            "color": "#00c896",
            "headline": f"Multi-fund convergence — whales accumulating {tickers}. Consider follow-on positions.",
            "confidence": min(90, 55 + n_accum * 3),
            "reasons": [
                f"{n_accum} tickers showing institutional accumulation vs {n_distrib} distribution",
                f"Top conviction: {accum_sorted[0].get('ticker')} ({accum_sorted[0].get('fund_count')} funds buying)",
                "Buy/sell flow ratio favors accumulation 2:1 or better",
            ],
            "top_picks": top_picks,
            "warnings": warnings,
            "next_watch": "Watch for follow-through in price action on the top picks; size in over 2-3 weeks.",
        }

    # ── ROTATE_OUT: distribution dominant ──────────────────────────
    if n_distrib >= 5 and n_distrib >= n_accum * 2:
        tickers = ", ".join(s.get("ticker", "") for s in distrib_sorted[:3])
        return {
            "action": "ROTATE_OUT",
            "label": "ROTATE OUT",
            "color": "#ff4757",
            "headline": f"Whale distribution dominant on {tickers} — trim exposure before institutional selling weight catches up.",
            "confidence": min(90, 55 + n_distrib * 3),
            "reasons": [
                f"{n_distrib} tickers showing distribution vs {n_accum} accumulation",
                f"Heaviest selling: {distrib_sorted[0].get('ticker')} ({distrib_sorted[0].get('fund_count')} funds reducing)",
                "Sell/buy flow ratio favors distribution 2:1 or worse",
            ],
            "top_picks": top_picks,
            "warnings": warnings,
            "next_watch": "Check portfolio overlap with the warning list — exit the weakest hands first.",
        }

    # ── Lean signals when convergence is real but not 2x dominant ──
    if n_accum >= 3 and n_accum > n_distrib:
        return {
            "action": "ROTATE_IN",
            "label": "LEAN ROTATE IN",
            "color": "#26a69a",
            "headline": "Selective accumulation — pockets of conviction, not a broad rotation. Cherry-pick the strongest names.",
            "confidence": 60,
            "reasons": [
                f"{n_accum} accumulating, {n_distrib} distributing — buy side leads but not 2:1",
                f"Top: {accum_sorted[0].get('ticker')} ({accum_sorted[0].get('fund_count')} funds)",
                "Treat as watchlist, not portfolio reshuffle",
            ],
            "top_picks": top_picks,
            "warnings": warnings,
            "next_watch": "Wait for more 13F filings to confirm trend before sizing up.",
        }
    if n_distrib >= 3 and n_distrib > n_accum:
        return {
            "action": "ROTATE_OUT",
            "label": "LEAN ROTATE OUT",
            "color": "#ff8c42",
            "headline": "Distribution building — early warning. Trim weakest names; don't dump good ones.",
            "confidence": 60,
            "reasons": [
                f"{n_distrib} distributing, {n_accum} accumulating — sell side leads but not 2:1",
                f"Heaviest: {distrib_sorted[0].get('ticker')} ({distrib_sorted[0].get('fund_count')} funds)",
                "Selective trimming, not wholesale exits",
            ],
            "top_picks": top_picks,
            "warnings": warnings,
            "next_watch": "Monitor next 2-3 13F filings for confirmation.",
        }

    # ── SIDELINE: mixed ────────────────────────────────────────────
    return {
        "action": "SIDELINE",
        "label": "STAY SIDELINE",
        "color": "#ffc837",
        "headline": "Whale activity mixed — no clear rotation signal. Hold positions, don't chase any single name.",
        "confidence": 55,
        "reasons": [
            f"{n_accum} accumulating, {n_distrib} distributing — no dominance",
            f"{n_hot} hot tickers across funds, {n_new} new positions",
            "Wait for convergence (3+ funds same direction) before acting",
        ],
        "top_picks": top_picks,
        "warnings": warnings,
        "next_watch": "Watch for new 13F filings creating ≥5-fund agreement on a ticker.",
    }


def get_whale_summary(days=30):
    """Get summary of all tracked entities and their latest activity.

    Args:
        days: filter window — 7 (1 week), 14 (2 weeks), 30 (1 month), 90 (quarter)
    """
    cache_key = f"whale_summary_{days}"
    cached = _get_cached(cache_key)
    if cached:
        return {**cached, "cached": True}

    # Date filters: activity uses created_at (when we fetched), but
    # filing_date is relative to the LATEST filing (not today) since 13F
    # data is inherently lagged 45+ days
    if IS_POSTGRES:
        date_filter = f"created_at >= NOW() - INTERVAL '{days} days'"
        # filing_filter: show filings within N days of the MOST RECENT filing date
        filing_filter = (
            f"filing_date >= (SELECT (MAX(filing_date)::date - INTERVAL '{days} days')::date::text FROM whale_holdings)"
        )
    else:
        date_filter = f"created_at >= datetime('now', '-{days} days')"
        filing_filter = (
            f"filing_date >= date((SELECT MAX(filing_date) FROM whale_holdings), '-{days} days')"
        )

    entities = query(
        "SELECT * FROM whale_entities ORDER BY aum_billions DESC NULLS LAST"
    ) or []

    # Get recent activity within the time window
    recent_activity = query(
        f"SELECT wa.*, we.name as fund_name, we.entity_type "
        f"FROM whale_activity wa JOIN whale_entities we ON wa.entity_id = we.id "
        f"WHERE wa.{date_filter} "
        f"ORDER BY wa.created_at DESC LIMIT 50"
    ) or []

    # Hot tickers within time window
    hot_tickers = query(
        f"SELECT ticker, "
        f"SUM(CASE WHEN action IN ('NEW','INCREASED') THEN 1 ELSE 0 END) as buy_count, "
        f"SUM(CASE WHEN action = 'REDUCED' THEN 1 ELSE 0 END) as sell_count, "
        f"SUM(value_usd) as total_value, "
        f"COUNT(DISTINCT entity_id) as fund_count, "
        f"MIN(filing_date) as first_filing, "
        f"MAX(filing_date) as last_filing "
        f"FROM whale_holdings WHERE {filing_filter} "
        f"GROUP BY ticker ORDER BY fund_count DESC, total_value DESC LIMIT 30"
    ) or []

    # New positions within time window
    new_positions = query(
        f"SELECT wh.*, we.name as fund_name FROM whale_holdings wh "
        f"JOIN whale_entities we ON wh.entity_id = we.id "
        f"WHERE wh.action = 'NEW' AND wh.{date_filter} "
        f"ORDER BY wh.value_usd DESC LIMIT 20"
    ) or []

    # Enrich traders with entity_id for drill-down
    entity_map = {e["name"]: dict(e) for e in entities}
    enriched_traders = []
    for t in TOP_TRADERS:
        trader = dict(t)
        entity = entity_map.get(t["entity"])
        if entity:
            trader["entity_id"] = entity["id"]
            trader["aum"] = entity.get("aum_billions")
            h_count = query_one(
                f"SELECT COUNT(*) as cnt FROM whale_holdings WHERE entity_id = {P}",
                (entity["id"],),
            )
            trader["holdings_count"] = int(h_count["cnt"]) if h_count else 0
        enriched_traders.append(trader)

    # Get latest filing date across all holdings for freshness indicator
    latest_filing = query_one("SELECT MAX(filing_date) as latest FROM whale_holdings")
    latest_filing_date = latest_filing["latest"] if latest_filing else None
    oldest_filing = query_one("SELECT MIN(filing_date) as oldest FROM whale_holdings")
    oldest_filing_date = oldest_filing["oldest"] if oldest_filing else None

    # Compute the same signals the /signals endpoint returns, so we can derive
    # one actionable call-to-action card up front
    sm_signals = get_smart_money_signals(days=days)
    actionable_signal = _derive_smart_money_signal(
        latest_filing_date,
        recent_activity,
        hot_tickers,
        new_positions,
        sm_signals,
    )

    result = {
        "entities": [dict(e) for e in entities],
        "recent_activity": [_serialize_row(a) for a in recent_activity],
        "hot_tickers": [_serialize_row(t) for t in hot_tickers],
        "new_positions": [_serialize_row(p) for p in new_positions],
        "top_traders": enriched_traders,
        "entity_count": len(entities),
        "days": days,
        "latest_filing_date": str(latest_filing_date) if latest_filing_date else None,
        "oldest_filing_date": str(oldest_filing_date) if oldest_filing_date else None,
        "data_note": "13F filings are reported quarterly (45-day lag). Latest data reflects Q4 2025 / Q1 2026 holdings.",
        "actionable_signal": actionable_signal,
        "timestamp": datetime.now().isoformat(),
        "cached": False,
    }

    _set_cached(cache_key, result)
    return result


def _serialize_row(r):
    """Convert a DB row to a plain dict with string dates."""
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def get_entity_holdings(entity_id):
    """Get latest holdings for a specific fund/entity."""
    holdings = query(
        f"SELECT * FROM whale_holdings WHERE entity_id = {P} "
        f"AND filing_date = (SELECT MAX(filing_date) FROM whale_holdings WHERE entity_id = {P}) "
        f"ORDER BY value_usd DESC",
        (entity_id, entity_id),
    ) or []
    return [dict(h) for h in holdings]


def get_ticker_whale_activity(ticker):
    """Get all institutional activity for a specific ticker."""
    activity = query(
        f"SELECT wa.*, we.name as fund_name, we.entity_type "
        f"FROM whale_activity wa JOIN whale_entities we ON wa.entity_id = we.id "
        f"WHERE wa.ticker = {P} ORDER BY wa.created_at DESC LIMIT 50",
        (ticker,),
    ) or []
    return [dict(a) for a in activity]


def get_smart_money_signals(days=30):
    """Identify tickers where multiple funds are building positions (convergence signal)."""
    if IS_POSTGRES:
        filing_filter = f"filing_date >= (SELECT (MAX(filing_date)::date - INTERVAL '{days} days')::date::text FROM whale_holdings)"
    else:
        filing_filter = f"filing_date >= date((SELECT MAX(filing_date) FROM whale_holdings), '-{days} days')"

    signals = query(
        f"SELECT ticker, company_name, "
        f"COUNT(DISTINCT entity_id) as fund_count, "
        f"SUM(CASE WHEN action IN ('NEW','INCREASED') THEN 1 ELSE 0 END) as buys, "
        f"SUM(CASE WHEN action = 'REDUCED' THEN 1 ELSE 0 END) as sells, "
        f"SUM(value_usd) as total_value, "
        f"MIN(filing_date) as first_date, "
        f"MAX(filing_date) as last_date "
        f"FROM whale_holdings WHERE action IN ('NEW','INCREASED','REDUCED') "
        f"AND {filing_filter} "
        f"GROUP BY ticker, company_name "
        f"HAVING COUNT(DISTINCT entity_id) >= 2 "
        f"ORDER BY fund_count DESC, total_value DESC LIMIT 20"
    ) or []

    results = []
    for s in signals:
        buys = int(s.get("buys", 0) or 0)
        sells = int(s.get("sells", 0) or 0)
        if buys > sells:
            signal = "ACCUMULATING"
            color = "#00c896"
        elif sells > buys:
            signal = "DISTRIBUTING"
            color = "#ff4757"
        else:
            signal = "MIXED"
            color = "#ffc837"

        results.append({
            "ticker": s["ticker"],
            "company": s.get("company_name", ""),
            "fund_count": int(s["fund_count"]),
            "buys": buys,
            "sells": sells,
            "total_value": float(s.get("total_value", 0) or 0),
            "signal": signal,
            "color": color,
            "first_date": str(s.get("first_date", "") or ""),
            "last_date": str(s.get("last_date", "") or ""),
        })

    return results


# ─── Background Refresh (APScheduler or manual) ─────────────────

_refresh_thread = None

def start_daily_refresh():
    """Start background thread for daily refresh. Called on app startup."""
    global _refresh_thread
    if _refresh_thread and _refresh_thread.is_alive():
        return

    def _loop():
        # Wait 60 seconds before first refresh to let Gunicorn fully boot
        time.sleep(60)
        while True:
            try:
                refresh_all_funds()
            except Exception as e:
                logger.warning(f"[Smart Money] Daily refresh error: {e}")
            # Sleep 24 hours
            time.sleep(86400)

    _refresh_thread = threading.Thread(target=_loop, daemon=True, name="smart-money-refresh")
    _refresh_thread.start()
    logger.info("[Smart Money] Daily refresh thread started")
