"""Buffett "copy & paste" tracker — Berkshire Hathaway's portfolio, ranked by
weight, with a $-amount → shares calculator so a user can mirror his allocation.

Data is the 13F already collected in ``whale_holdings`` (entity = Berkshire,
CIK 0001067983). That raw data has three problems we fix here:

* **Tickers are mangled** by the generic 13F parser (issuer name's first token —
  "AMERICAN" for AXP, "COCA" for KO). We remap from the reliable ``company_name``
  via ``_ISSUER_TICKER``.
* **Duplicate rows** — a filing carries many sub-account lines per name (AAPL ×12).
  We aggregate by company and sum ``pct_of_portfolio``.
* **``value_usd`` is mis-scaled** (shows $45T for Amex), so we ignore it and use
  the trustworthy weight % plus a **live price** for the calculator.
"""

import logging
import time

from db import query, query_one, IS_POSTGRES

logger = logging.getLogger(__name__)

P = "%s" if IS_POSTGRES else "?"

BERKSHIRE_CIK = "0001067983"

_cache = {}
_CACHE_TTL = 3600  # 1 hour — holdings change quarterly; price is approximate for sizing

# Reliable issuer-name → ticker map for Berkshire's holdings. Keyed by the 13F
# ``company_name`` (upper-cased, normalized). Unmapped names fall back to the
# stored ticker so a newly-added name still shows (just possibly mis-tickered).
_ISSUER_TICKER = {
    "APPLE INC": "AAPL",
    "AMERICAN EXPRESS CO": "AXP",
    "COCA COLA CO": "KO",
    "BANK AMERICA CORP": "BAC",
    "CHEVRON CORPORATION": "CVX",
    "OCCIDENTAL PETE CORP": "OXY",
    "ALPHABET INC": "GOOGL",
    "CHUBB LTD SWITZ": "CB",
    "MOODYS CORP": "MCO",
    "KRAFT HEINZ CO": "KHC",
    "DAVITA INC": "DVA",
    "KROGER CO": "KR",
    "SIRIUSXM HOLDINGS INC": "SIRI",
    "DELTA AIR LINES INC": "DAL",
    "VERISIGN INC": "VRSN",
    "LIBERTY LIVE HOLDINGS INC": "LLYVK",
    "CAPITAL ONE FINL CORP": "COF",
    "NEW YORK TIMES CO MTN BE": "NYT",
    "ALLY FINL INC": "ALLY",
    "LENNAR CORP": "LEN",
    "NUCOR CORP": "NUE",
    "LOUISIANA PAC CORP": "LPX",
    "CONSTELLATION BRANDS INC": "STZ",
    "NVR INC": "NVR",
    "MACYS INC": "M",
    "JEFFERIES FINANCIAL GROUP IN": "JEF",
    "VISA INC": "V",
    "MASTERCARD INC": "MA",
    "AMAZON COM INC": "AMZN",
    "T-MOBILE US INC": "TMUS",
    "CITIGROUP INC": "C",
    "AON PLC": "AON",
    "HP INC": "HPQ",
    "CHARTER COMMUNICATIONS INC": "CHTR",
}

# Raw 13F action -> short, plain label for the UI.
_ACTION_LABEL = {
    "NEW": "NEW", "INCREASED": "ADD", "ADDED": "ADD",
    "REDUCED": "TRIM", "DECREASED": "TRIM",
    "HELD": "HELD", "UNCHANGED": "HELD",
    "EXITED": "EXIT", "SOLD": "EXIT", "SOLD_OUT": "EXIT",
}


def _ticker_for(company_name, raw_ticker):
    key = " ".join((company_name or "").upper().split())
    return _ISSUER_TICKER.get(key) or (raw_ticker or "").upper() or "—"


def _fetch_prices(tickers):
    """Best-effort current price per ticker via yfinance. Missing -> None."""
    prices = {}
    if not tickers:
        return prices
    try:
        import yfinance as yf
        data = yf.Tickers(" ".join(tickers))
        for t in tickers:
            try:
                fi = data.tickers[t].fast_info
                px = float(fi.get("lastPrice", 0) or fi.get("previousClose", 0) or 0)
                prices[t] = round(px, 2) if px > 0 else None
            except Exception:
                prices[t] = None
    except Exception as e:
        logger.warning(f"[Buffett] price fetch failed: {e}")
    return prices


def get_buffett_holdings(force_refresh=False):
    """Cleaned, weight-ranked Berkshire portfolio for the mirror + calculator.

    Returns ``{as_of, holdings:[{rank, ticker, company, weight, action, price}],
    holdings_count, source, note}`` or ``{error: ...}``."""
    now = time.time()
    if not force_refresh and "buffett" in _cache:
        data, ts = _cache["buffett"]
        if now - ts < _CACHE_TTL:
            return data

    ent = query_one(f"SELECT id FROM whale_entities WHERE cik = {P}", (BERKSHIRE_CIK,))
    if not ent:
        ent = query_one(
            f"SELECT id FROM whale_entities WHERE LOWER(name) LIKE {P}", ("%berkshire%",))
    if not ent:
        return {"error": "Berkshire Hathaway not found in tracked institutions."}
    eid = ent["id"]

    latest = query_one(
        f"SELECT MAX(filing_date) AS d FROM whale_holdings WHERE entity_id = {P}", (eid,))
    as_of = latest["d"] if latest else None
    if not as_of:
        return {"error": "No Berkshire 13F holdings on file yet."}

    rows = query(
        f"SELECT company_name, ticker, pct_of_portfolio, action "
        f"FROM whale_holdings WHERE entity_id = {P} AND filing_date = {P}",
        (eid, as_of),
    ) or []

    # Aggregate sub-account rows by company: sum weight, keep the action of the
    # heaviest line (most representative of the overall position change).
    agg = {}
    for r in rows:
        name = r["company_name"] or "—"
        wt = float(r["pct_of_portfolio"] or 0)
        a = agg.setdefault(name, {"weight": 0.0, "ticker": r["ticker"],
                                  "action": r["action"], "top": -1.0})
        a["weight"] += wt
        if wt > a["top"]:
            a["top"] = wt
            a["action"] = r["action"]

    holdings = []
    for name, a in agg.items():
        if a["weight"] <= 0:
            continue
        holdings.append({
            "ticker": _ticker_for(name, a["ticker"]),
            "company": name.title(),
            "weight": round(a["weight"], 2),
            "action": _ACTION_LABEL.get((a["action"] or "").upper(), "HELD"),
        })
    holdings.sort(key=lambda h: -h["weight"])

    prices = _fetch_prices([h["ticker"] for h in holdings if h["ticker"] != "—"])
    for i, h in enumerate(holdings, 1):
        h["rank"] = i
        h["price"] = prices.get(h["ticker"])

    result = {
        "as_of": str(as_of)[:10],
        "holdings": holdings,
        "holdings_count": len(holdings),
        "source": "SEC 13F (Berkshire Hathaway, CIK 0001067983)",
        "note": ("13F filings are reported ~45 days after quarter-end and cover "
                 "U.S.-listed long positions only. Weights mirror Berkshire's "
                 "reported allocation; prices are live for sizing."),
        "timestamp": now,
    }
    _cache["buffett"] = (result, now)
    return result
