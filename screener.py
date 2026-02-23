"""
Multi-Cap Stock Screener — AI-Vetted Opportunities
Supports Low-Cap ($2-$15), Mid-Cap ($15-$100), Large-Cap ($50+), ETFs,
Metals & Mining, Crypto, and AI (Artificial Intelligence).
Each category uses tailored AI vetting prompts.

Risk Warning: Small-cap stocks carry elevated risk.
Never allocate more than 2% of account per position for low-caps.
"""

import concurrent.futures
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Try importing yfinance
try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

from ai_validator import (
    is_configured, _call_openrouter, _parse_json_response,
    LLM_SCREENER,
)

# ─── Curated Ticker Lists ─────────────────────────────────────

# Low-Cap ($2-$15, $50M-$2B market cap)
LOWCAP_TICKERS = [
    # Tech / Software
    "SOUN", "BBAI", "GENI", "ALLT", "MAPS", "CEVA", "ZETA", "BRZE",
    "CWAN", "ASTS", "RKLB", "LUNR", "RDW", "MNTS", "ASTR",
    # Biotech / Health
    "MDXH", "TGTX", "RVMD", "AXSM", "KURA", "MGTA", "IMVT",
    "VRNA", "DAWN", "GERN", "INVA", "PRTA", "DCPH", "AMRN",
    # Energy / Clean
    "BWEN", "SHLS", "ARRY", "STEM", "BE", "PLUG", "FCEL",
    "CLNE", "RUN", "NOVA", "AMPS", "EVGO", "CHPT", "BLNK",
    # Finance / Fintech
    "OPFI", "RELY", "PAYO", "PRCH", "TASK", "TOST", "PSFE",
    "OLO", "PAGS", "STNE", "NU", "AFRM",
    # Industrial / Materials
    "MP", "LAC", "LTHM", "PLL", "GATO", "FSM", "AG",
    "ORLA", "SSRM", "MAG", "EXK", "CDE",
    # Consumer / Retail
    "PRPL", "LOVE", "DTC", "HIMS", "CLAR", "BIRD",
    "BARK", "WOOF", "CHWY", "SFIX", "RENT", "REAL",
    # Cannabis / Misc
    "TLRY", "CGC", "ACB", "CRON", "HEXO",
    # EV / Auto
    "GOEV", "LCID", "RIVN", "FSR", "NKLA", "WKHS", "REE",
    # Telecom / Media
    "GSAT", "IRDM", "LUMN", "IQ", "FUBO", "CURI",
    # Real Estate / REITs (small)
    "IIPR", "SAFE", "LAND", "GOOD",
    # Space / Defense
    "SPCE", "VORB", "AJRD", "KTOS", "BKSY",
    # AI / Data
    "AI", "PLTR", "BIGC", "BRDS", "PRCT",
]

# Mid-Cap ($15-$100, $2B-$20B market cap)
MIDCAP_TICKERS = [
    # Cybersecurity
    "CRWD", "ZS", "FTNT", "PANW", "S", "TENB", "QLYS", "RPD", "VRNS",
    # Cloud / SaaS
    "DDOG", "NET", "SNOW", "BILL", "HUBS", "PCOR", "MNDY", "CFLT",
    "GTLB", "PATH", "ESTC", "DOCN", "BRZE", "TOST", "CWAN",
    # Semiconductors
    "MRVL", "ON", "SWKS", "QRVO", "SLAB", "DIOD", "AMBA", "CRUS",
    # Healthcare
    "DXCM", "ISRG", "VEEV", "ALGN", "TFX", "NVCR", "INSP", "GMED",
    # Financial Tech
    "FOUR", "GLBE", "PAYC", "WEX", "EVTC", "NUVEI",
    # Industrial Tech
    "AZTA", "GNRC", "TT", "NDSN", "RBC", "FSS",
    # Consumer Growth
    "DKNG", "PINS", "SNAP", "DUOL", "BROS", "CAVA", "WING",
    # Data / Analytics
    "MDB", "PLTR", "DOMO", "ALTR", "TYL", "GWRE",
    # Digital Advertising
    "TTD", "MGNI", "IAS", "DV",
    # Misc Growth
    "CELH", "AXON", "ENPH", "SEDG", "RIVN", "JOBY",
]

# Large-Cap ($50+, $20B+ market cap, growth-focused)
LARGECAP_TICKERS = [
    # Mega-Cap Tech
    "NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA",
    # Semiconductors
    "AVGO", "AMD", "QCOM", "TXN", "INTC", "MU", "LRCX", "AMAT", "KLAC",
    # Software / Cloud
    "CRM", "NOW", "ADBE", "ORCL", "INTU", "SNPS", "CDNS", "WDAY", "TEAM",
    # Internet / Consumer
    "UBER", "SHOP", "SQ", "COIN", "MELI", "SE", "BKNG", "ABNB", "DASH",
    # Fintech
    "V", "MA", "PYPL", "GS", "MS",
    # Healthcare Innovation
    "LLY", "ABBV", "TMO", "DHR", "ISRG", "BSX", "MDT",
    # Industrial / EV
    "CAT", "DE", "HON", "GE", "LMT", "RTX",
    # Energy Transition
    "NEE", "ENPH", "FSLR",
    # Communications
    "NFLX", "DIS", "CMCSA", "SPOT", "ROKU",
    # Chinese Tech
    "BABA", "PDD", "JD", "BIDU", "NIO", "LI", "XPEV",
]

# ETFs (Growth-focused)
ETF_TICKERS = [
    # Broad Growth
    "QQQ", "VGT", "XLK", "IGV", "VONG", "IWF", "SPYG", "VUG",
    # ARK Innovation
    "ARKK", "ARKW", "ARKF", "ARKG", "ARKQ",
    # Semiconductors
    "SOXX", "SMH", "PSI", "SOXQ",
    # Cybersecurity
    "CIBR", "HACK", "BUG",
    # AI / Robotics
    "BOTZ", "ROBO", "AIQ", "IRBO",
    # Cloud
    "WCLD", "CLOU", "SKYY",
    # Clean Energy
    "TAN", "ICLN", "PBW", "QCLN",
    # EV / Battery
    "DRIV", "IDRV", "LIT", "KARS",
    # Blockchain / Fintech
    "BLOK", "FINX", "ARKF",
    # Biotech / Genomics
    "XBI", "IBB", "ARKG",
    # International Growth
    "EEM", "KWEB", "INDA", "VWO",
    # Thematic
    "MOON", "UFO", "BETZ", "HERO",
]

# Metals & Mining
METALS_MINING_TICKERS = [
    # Gold miners
    "NEM", "GOLD", "AEM", "KGC", "AGI", "BTG", "AU", "HMY", "EGO", "DRD", "GFI",
    # Silver miners
    "AG", "PAAS", "HL", "CDE", "FSM", "EXK", "MAG", "SSRM",
    # Copper / Base metals
    "FCX", "SCCO", "TECK", "RIO", "BHP", "VALE",
    # Lithium / Battery
    "LAC", "PLL", "SQM", "ALB",
    # Uranium
    "CCJ", "UEC", "DNN", "UUUU", "NXE",
    # Diversified / Royalty / Streaming
    "MP", "WPM", "FNV", "RGLD",
]

# Crypto (yfinance format)
CRYPTO_TICKERS = [
    # Major
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "AVAX-USD", "DOT-USD", "MATIC-USD", "LINK-USD",
    # DeFi
    "UNI-USD", "AAVE-USD", "MKR-USD",
    # Layer 2 / Alt
    "ARB-USD", "OP-USD", "NEAR-USD", "FTM-USD", "ATOM-USD",
    # Meme / Other
    "DOGE-USD", "SHIB-USD",
]

CRYPTO_SECTOR_MAP = {
    "BTC-USD": "Layer 1", "ETH-USD": "Layer 1", "SOL-USD": "Layer 1", "ADA-USD": "Layer 1",
    "AVAX-USD": "Layer 1", "DOT-USD": "Layer 1", "NEAR-USD": "Layer 1", "ATOM-USD": "Layer 1",
    "BNB-USD": "Infrastructure", "LINK-USD": "Infrastructure", "XRP-USD": "Infrastructure",
    "UNI-USD": "DeFi", "AAVE-USD": "DeFi", "MKR-USD": "DeFi",
    "ARB-USD": "Layer 2", "OP-USD": "Layer 2", "MATIC-USD": "Layer 2", "FTM-USD": "Layer 2",
    "DOGE-USD": "Meme", "SHIB-USD": "Meme",
}

# AI (Artificial Intelligence)
AI_TICKERS = [
    # Pure-play AI
    "AI", "PLTR", "PATH", "BBAI", "SOUN", "UPST",
    # AI Infrastructure / Chips
    "NVDA", "AMD", "AVGO", "MRVL", "ARM", "SMCI", "ANET",
    # AI Cloud / Software
    "MSFT", "GOOG", "AMZN", "META", "CRM", "NOW", "SNOW", "DDOG", "MDB", "ESTC",
    # AI Tools / Platforms
    "DUOL", "ADBE", "HUBS", "DOCN", "GTLB", "CFLT",
    # AI Robotics / Hardware
    "ISRG", "IONQ", "RGTI", "QUBT",
    # AI Security
    "CRWD", "ZS", "S",
]


# ─── Scan Functions ────────────────────────────────────────────

def _batch_scan_tickers(tickers: list, min_price: float = None, max_price: float = None,
                        limit: int = 30) -> list:
    """Generic batch price scanner. Optionally filters by price range."""
    if not HAS_YFINANCE:
        return []

    candidates = []
    batch_size = 50

    try:
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i + batch_size]
            try:
                tickers_str = " ".join(batch)
                data = yf.download(tickers_str, period="5d", progress=False, threads=True)
                if data.empty:
                    continue

                close_data = data.get("Close")
                if close_data is None:
                    continue

                if len(batch) == 1:
                    last_price = close_data.dropna().iloc[-1] if not close_data.dropna().empty else None
                    if last_price:
                        price = float(last_price)
                        if (min_price is None or price >= min_price) and (max_price is None or price <= max_price):
                            candidates.append({"ticker": batch[0], "price": round(price, 2)})
                else:
                    for ticker in batch:
                        try:
                            if ticker not in close_data.columns:
                                continue
                            col = close_data[ticker].dropna()
                            if col.empty:
                                continue
                            price = float(col.iloc[-1])
                            if (min_price is None or price >= min_price) and (max_price is None or price <= max_price):
                                candidates.append({"ticker": ticker, "price": round(price, 2)})
                        except (KeyError, IndexError, TypeError):
                            continue
            except Exception:
                continue

            if len(candidates) >= limit * 2:
                break
    except Exception:
        pass

    return candidates


def scan_lowcap_candidates(min_price: float = 2.0, max_price: float = 15.0,
                           max_market_cap: float = 2e9, limit: int = 30) -> list:
    """Scan for low-cap stock candidates in the $2-$15 range."""
    candidates = _batch_scan_tickers(LOWCAP_TICKERS[:200], min_price, max_price, limit)

    # Enrich with fundamentals, filter by market cap
    enriched = []
    for c in candidates[:limit * 2]:
        try:
            t = yf.Ticker(c["ticker"])
            info = t.info or {}
            mkt_cap = info.get("marketCap", 0) or 0
            if mkt_cap > max_market_cap or mkt_cap < 50_000_000:
                continue
            enriched.append({
                "ticker": c["ticker"], "price": c["price"], "market_cap": mkt_cap,
                "name": info.get("shortName", info.get("longName", c["ticker"])),
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", "Unknown"),
                "volume": info.get("averageVolume", 0),
            })
            if len(enriched) >= limit:
                break
        except Exception:
            continue
    return enriched


def scan_midcap_candidates(min_price: float = 15.0, max_price: float = 100.0,
                           limit: int = 30) -> list:
    """Scan for mid-cap stock candidates ($15-$100, $2B-$20B market cap)."""
    candidates = _batch_scan_tickers(MIDCAP_TICKERS, min_price, max_price, limit)

    enriched = []
    for c in candidates[:limit * 2]:
        try:
            t = yf.Ticker(c["ticker"])
            info = t.info or {}
            mkt_cap = info.get("marketCap", 0) or 0
            if mkt_cap > 20e9 or mkt_cap < 2e9:
                continue
            enriched.append({
                "ticker": c["ticker"], "price": c["price"], "market_cap": mkt_cap,
                "name": info.get("shortName", info.get("longName", c["ticker"])),
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", "Unknown"),
                "volume": info.get("averageVolume", 0),
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "forward_pe": info.get("forwardPE"),
            })
            if len(enriched) >= limit:
                break
        except Exception:
            continue
    return enriched


def scan_largecap_candidates(limit: int = 30) -> list:
    """Scan for large-cap growth stocks ($50+, $20B+ market cap)."""
    candidates = _batch_scan_tickers(LARGECAP_TICKERS, min_price=50.0, limit=limit)

    enriched = []
    for c in candidates[:limit * 2]:
        try:
            t = yf.Ticker(c["ticker"])
            info = t.info or {}
            mkt_cap = info.get("marketCap", 0) or 0
            if mkt_cap < 20e9:
                continue
            enriched.append({
                "ticker": c["ticker"], "price": c["price"], "market_cap": mkt_cap,
                "name": info.get("shortName", info.get("longName", c["ticker"])),
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", "Unknown"),
                "volume": info.get("averageVolume", 0),
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "forward_pe": info.get("forwardPE"),
                "trailing_pe": info.get("trailingPE"),
                "profit_margins": info.get("profitMargins"),
            })
            if len(enriched) >= limit:
                break
        except Exception:
            continue
    return enriched


def scan_etf_candidates(limit: int = 30) -> list:
    """Scan for growth-focused ETFs."""
    candidates = _batch_scan_tickers(ETF_TICKERS, limit=limit)

    enriched = []
    seen = set()
    for c in candidates[:limit * 2]:
        if c["ticker"] in seen:
            continue
        seen.add(c["ticker"])
        try:
            t = yf.Ticker(c["ticker"])
            info = t.info or {}
            enriched.append({
                "ticker": c["ticker"], "price": c["price"],
                "name": info.get("shortName", info.get("longName", c["ticker"])),
                "sector": info.get("category", info.get("sector", "ETF")),
                "industry": info.get("category", "ETF"),
                "volume": info.get("averageVolume", 0),
                "market_cap": info.get("totalAssets", 0) or 0,  # AUM for ETFs
                "expense_ratio": info.get("annualReportExpenseRatio"),
                "ytd_return": info.get("ytdReturn"),
                "three_year_return": info.get("threeYearAverageReturn"),
            })
            if len(enriched) >= limit:
                break
        except Exception:
            continue
    return enriched


def scan_metals_mining_candidates(limit: int = 30) -> list:
    """Scan metals & mining stocks."""
    candidates = _batch_scan_tickers(METALS_MINING_TICKERS, limit=limit)

    enriched = []
    for c in candidates[:limit * 2]:
        try:
            t = yf.Ticker(c["ticker"])
            info = t.info or {}
            enriched.append({
                "ticker": c["ticker"], "price": c["price"],
                "market_cap": info.get("marketCap", 0) or 0,
                "name": info.get("shortName", info.get("longName", c["ticker"])),
                "sector": info.get("sector", "Basic Materials"),
                "industry": info.get("industry", "Mining"),
                "volume": info.get("averageVolume", 0),
                "revenue_growth": info.get("revenueGrowth"),
                "forward_pe": info.get("forwardPE"),
                "profit_margins": info.get("profitMargins"),
            })
            if len(enriched) >= limit:
                break
        except Exception:
            continue
    return enriched


def scan_crypto_candidates(limit: int = 20) -> list:
    """Scan crypto tickers via yfinance."""
    candidates = _batch_scan_tickers(CRYPTO_TICKERS, limit=limit)

    enriched = []
    for c in candidates[:limit * 2]:
        try:
            t = yf.Ticker(c["ticker"])
            info = t.info or {}
            enriched.append({
                "ticker": c["ticker"], "price": c["price"],
                "market_cap": info.get("marketCap", 0) or 0,
                "name": info.get("shortName", info.get("longName", c["ticker"])),
                "sector": CRYPTO_SECTOR_MAP.get(c["ticker"], "Unknown"),
                "industry": "Cryptocurrency",
                "volume": info.get("averageVolume", 0),
            })
            if len(enriched) >= limit:
                break
        except Exception:
            continue
    return enriched


def scan_ai_candidates(limit: int = 30) -> list:
    """Scan AI / Artificial Intelligence stocks."""
    candidates = _batch_scan_tickers(AI_TICKERS, limit=limit)

    enriched = []
    for c in candidates[:limit * 2]:
        try:
            t = yf.Ticker(c["ticker"])
            info = t.info or {}
            enriched.append({
                "ticker": c["ticker"], "price": c["price"],
                "market_cap": info.get("marketCap", 0) or 0,
                "name": info.get("shortName", info.get("longName", c["ticker"])),
                "sector": info.get("sector", "Technology"),
                "industry": info.get("industry", "AI/ML"),
                "volume": info.get("averageVolume", 0),
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "forward_pe": info.get("forwardPE"),
                "profit_margins": info.get("profitMargins"),
            })
            if len(enriched) >= limit:
                break
        except Exception:
            continue
    return enriched


# ─── AI Vet Functions ──────────────────────────────────────────

def vet_candidate(candidate: dict) -> dict:
    """Run AI vetting on a single low-cap candidate (risk-focused)."""
    if not is_configured():
        return {"error": "AI not configured", "ticker": candidate.get("ticker")}

    ticker = candidate.get("ticker", "???")
    summary = f"""STOCK: {ticker}
Name: {candidate.get('name', 'N/A')}
Price: ${candidate.get('price', 0)}
Market Cap: ${candidate.get('market_cap', 0):,.0f}
Sector: {candidate.get('sector', 'N/A')} | Industry: {candidate.get('industry', 'N/A')}
Avg Volume: {candidate.get('volume', 0):,.0f}"""

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        bs = t.balance_sheet
        cash = debt = None
        if bs is not None and not bs.empty:
            cash = bs.iloc[0].get("Cash And Cash Equivalents", None)
            debt = bs.iloc[0].get("Total Debt", None)
        summary += f"""
Revenue: ${info.get('totalRevenue', 'N/A')}
Net Income: ${info.get('netIncomeToCommon', 'N/A')}
FCF: ${info.get('freeCashflow', 'N/A')}
Cash: ${cash if cash else 'N/A'}
Debt: ${debt if debt else 'N/A'}
P/E Forward: {info.get('forwardPE', 'N/A')}
Short Ratio: {info.get('shortRatio', 'N/A')}
52W Low: ${info.get('fiftyTwoWeekLow', 'N/A')} | 52W High: ${info.get('fiftyTwoWeekHigh', 'N/A')}
Insider Ownership: {info.get('heldPercentInsiders', 'N/A')}
Inst Ownership: {info.get('heldPercentInstitutions', 'N/A')}"""
    except Exception:
        pass

    messages = [
        {"role": "system", "content": "You are a small-cap risk analyst. Focus on: dilution risk, liquidity, bankruptcy, insider activity, reverse splits, pump-and-dump. Respond with ONLY valid JSON."},
        {"role": "user", "content": f"""Vet this low-cap stock for investment. Risk management is #1. Never allocate >2% per position for small-caps.

{summary}

Check: 1) Dilution risk (share offerings, ATM programs) 2) Liquidity (can you exit?) 3) Bankruptcy/restructuring risk 4) Insider selling 5) Reverse split history 6) Pump-and-dump signals 7) Revenue trajectory 8) Cash runway

Respond with ONLY this JSON:
{{"verdict":"OPPORTUNITY","confidence":65,"survival_12m":80,"growth_catalysts":["catalyst1"],"red_flags":["flag1"],"dilution_risk":"LOW","liquidity_risk":"LOW","fair_value":0.00,"upside_pct":25,"risk_score":40,"position_limit_pct":2,"summary":"1-2 sentences"}}

verdict must be one of: OPPORTUNITY, RISKY, AVOID"""}
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20)
    result = _parse_json_response(raw, "screener_vet")
    result["ticker"] = ticker
    result["price"] = candidate.get("price")
    result["market_cap"] = candidate.get("market_cap")
    result["name"] = candidate.get("name")
    result["sector"] = candidate.get("sector")
    return result


def vet_midcap_candidate(candidate: dict) -> dict:
    """AI vetting for mid-cap — balanced growth/value focus."""
    if not is_configured():
        return {"error": "AI not configured", "ticker": candidate.get("ticker")}

    ticker = candidate.get("ticker", "???")
    summary = f"""STOCK: {ticker}
Name: {candidate.get('name', 'N/A')}
Price: ${candidate.get('price', 0)}
Market Cap: ${candidate.get('market_cap', 0):,.0f}
Sector: {candidate.get('sector', 'N/A')} | Industry: {candidate.get('industry', 'N/A')}
Avg Volume: {candidate.get('volume', 0):,.0f}
Revenue Growth: {candidate.get('revenue_growth', 'N/A')}
Earnings Growth: {candidate.get('earnings_growth', 'N/A')}
Forward P/E: {candidate.get('forward_pe', 'N/A')}"""

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        summary += f"""
Revenue: ${info.get('totalRevenue', 'N/A')}
Net Income: ${info.get('netIncomeToCommon', 'N/A')}
FCF: ${info.get('freeCashflow', 'N/A')}
Profit Margins: {info.get('profitMargins', 'N/A')}
Operating Margins: {info.get('operatingMargins', 'N/A')}
ROE: {info.get('returnOnEquity', 'N/A')}
Debt/Equity: {info.get('debtToEquity', 'N/A')}
52W Low: ${info.get('fiftyTwoWeekLow', 'N/A')} | 52W High: ${info.get('fiftyTwoWeekHigh', 'N/A')}
Inst Ownership: {info.get('heldPercentInstitutions', 'N/A')}"""
    except Exception:
        pass

    messages = [
        {"role": "system", "content": "You are a mid-cap growth/value analyst. Balance growth potential with valuation discipline. Respond with ONLY valid JSON."},
        {"role": "user", "content": f"""Evaluate this mid-cap stock for investment. Look for strong growth at reasonable valuations.

{summary}

Evaluate: 1) Revenue growth sustainability 2) Earnings trajectory 3) Competitive position 4) Valuation vs growth rate 5) Margin expansion potential 6) Institutional interest 7) Key risks 8) Total addressable market

Respond with ONLY this JSON:
{{"verdict":"OPPORTUNITY","confidence":65,"growth_catalysts":["catalyst1"],"red_flags":["flag1"],"revenue_growth_trend":"ACCELERATING","earnings_momentum":"POSITIVE","moat_strength":"MODERATE","fair_value":0.00,"upside_pct":25,"risk_score":40,"position_limit_pct":5,"summary":"1-2 sentences"}}

verdict must be one of: OPPORTUNITY, RISKY, AVOID"""}
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20)
    result = _parse_json_response(raw, "screener_midcap_vet")
    result["ticker"] = ticker
    result["price"] = candidate.get("price")
    result["market_cap"] = candidate.get("market_cap")
    result["name"] = candidate.get("name")
    result["sector"] = candidate.get("sector")
    return result


def vet_largecap_candidate(candidate: dict) -> dict:
    """AI vetting for large-cap — growth-focused."""
    if not is_configured():
        return {"error": "AI not configured", "ticker": candidate.get("ticker")}

    ticker = candidate.get("ticker", "???")
    summary = f"""STOCK: {ticker}
Name: {candidate.get('name', 'N/A')}
Price: ${candidate.get('price', 0)}
Market Cap: ${candidate.get('market_cap', 0):,.0f}
Sector: {candidate.get('sector', 'N/A')} | Industry: {candidate.get('industry', 'N/A')}
Avg Volume: {candidate.get('volume', 0):,.0f}
Revenue Growth: {candidate.get('revenue_growth', 'N/A')}
Earnings Growth: {candidate.get('earnings_growth', 'N/A')}
Forward P/E: {candidate.get('forward_pe', 'N/A')}
Trailing P/E: {candidate.get('trailing_pe', 'N/A')}
Profit Margins: {candidate.get('profit_margins', 'N/A')}"""

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        summary += f"""
Revenue: ${info.get('totalRevenue', 'N/A')}
Net Income: ${info.get('netIncomeToCommon', 'N/A')}
FCF: ${info.get('freeCashflow', 'N/A')}
Operating Margins: {info.get('operatingMargins', 'N/A')}
ROE: {info.get('returnOnEquity', 'N/A')}
PEG Ratio: {info.get('pegRatio', 'N/A')}
Beta: {info.get('beta', 'N/A')}
Analyst Target: ${info.get('targetMeanPrice', 'N/A')}
Recommendation: {info.get('recommendationKey', 'N/A')}"""
    except Exception:
        pass

    messages = [
        {"role": "system", "content": "You are a large-cap growth investor. Focus on revenue acceleration, earnings growth trajectory, market share gains, competitive moat, and TAM expansion. Respond with ONLY valid JSON."},
        {"role": "user", "content": f"""Evaluate this large-cap stock for growth investing. Focus on whether this company is accelerating growth and can sustain market leadership.

{summary}

Evaluate: 1) Revenue acceleration vs deceleration 2) Earnings growth trajectory 3) Market share gains 4) Competitive moat strength 5) TAM expansion opportunities 6) Margin expansion potential 7) Growth catalysts in next 12 months 8) Valuation relative to growth rate

Respond with ONLY this JSON:
{{"verdict":"STRONG GROWTH","confidence":70,"revenue_growth_trend":"ACCELERATING","earnings_momentum":"STRONG","moat_strength":"WIDE","growth_catalysts":["catalyst1"],"risks":["risk1"],"fair_value":0.00,"upside_pct":15,"position_limit_pct":10,"summary":"1-2 sentences"}}

verdict must be one of: STRONG GROWTH, STEADY, SLOWING"""}
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20)
    result = _parse_json_response(raw, "screener_largecap_vet")
    result["ticker"] = ticker
    result["price"] = candidate.get("price")
    result["market_cap"] = candidate.get("market_cap")
    result["name"] = candidate.get("name")
    result["sector"] = candidate.get("sector")
    return result


def vet_etf_candidate(candidate: dict) -> dict:
    """AI vetting for ETFs — growth-focused thematic analysis."""
    if not is_configured():
        return {"error": "AI not configured", "ticker": candidate.get("ticker")}

    ticker = candidate.get("ticker", "???")
    expense = candidate.get("expense_ratio")
    expense_str = f"{expense:.2%}" if expense else "N/A"
    ytd = candidate.get("ytd_return")
    ytd_str = f"{ytd:.1%}" if ytd else "N/A"
    three_yr = candidate.get("three_year_return")
    three_yr_str = f"{three_yr:.1%}" if three_yr else "N/A"
    aum = candidate.get("market_cap", 0)

    summary = f"""ETF: {ticker}
Name: {candidate.get('name', 'N/A')}
Price: ${candidate.get('price', 0)}
AUM: ${aum:,.0f}
Category: {candidate.get('sector', 'N/A')}
Avg Volume: {candidate.get('volume', 0):,.0f}
Expense Ratio: {expense_str}
YTD Return: {ytd_str}
3-Year Avg Return: {three_yr_str}"""

    messages = [
        {"role": "system", "content": "You are a growth ETF analyst. Focus on sector momentum, thematic tailwinds, expense efficiency, liquidity, and holdings quality. Respond with ONLY valid JSON."},
        {"role": "user", "content": f"""Evaluate this ETF for growth investing. Focus on whether this ETF captures strong secular growth themes.

{summary}

Evaluate: 1) Sector/theme momentum 2) Thematic tailwinds vs headwinds 3) Expense ratio efficiency 4) Liquidity (AUM, volume) 5) Holdings quality and concentration 6) Growth catalysts 7) Correlation to overall growth theme 8) Risk-adjusted returns

Respond with ONLY this JSON:
{{"verdict":"STRONG BUY","confidence":70,"sector_momentum":"STRONG","thematic_strength":"HIGH","expense_efficiency":"GOOD","top_holdings_quality":"HIGH","growth_catalysts":["catalyst1"],"risks":["risk1"],"target_allocation_pct":5,"summary":"1-2 sentences"}}

verdict must be one of: STRONG BUY, ACCUMULATE, HOLD"""}
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20)
    result = _parse_json_response(raw, "screener_etf_vet")
    result["ticker"] = ticker
    result["price"] = candidate.get("price")
    result["market_cap"] = aum
    result["name"] = candidate.get("name")
    result["sector"] = candidate.get("sector")
    result["expense_ratio"] = expense_str
    result["ytd_return"] = ytd_str
    return result


def vet_metals_mining_candidate(candidate: dict) -> dict:
    """AI vetting for metals & mining — commodity-focused."""
    if not is_configured():
        return {"error": "AI not configured", "ticker": candidate.get("ticker")}

    ticker = candidate.get("ticker", "???")
    summary = f"""STOCK: {ticker}
Name: {candidate.get('name', 'N/A')}
Price: ${candidate.get('price', 0)}
Market Cap: ${candidate.get('market_cap', 0):,.0f}
Sector: {candidate.get('sector', 'N/A')} | Industry: {candidate.get('industry', 'N/A')}
Avg Volume: {candidate.get('volume', 0):,.0f}"""

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        summary += f"""
Revenue: ${info.get('totalRevenue', 'N/A')}
Net Income: ${info.get('netIncomeToCommon', 'N/A')}
FCF: ${info.get('freeCashflow', 'N/A')}
Profit Margins: {info.get('profitMargins', 'N/A')}
Operating Margins: {info.get('operatingMargins', 'N/A')}
Debt/Equity: {info.get('debtToEquity', 'N/A')}
52W Low: ${info.get('fiftyTwoWeekLow', 'N/A')} | 52W High: ${info.get('fiftyTwoWeekHigh', 'N/A')}"""
    except Exception:
        pass

    messages = [
        {"role": "system", "content": "You are a metals & mining analyst. Focus on commodity price sensitivity, production costs, reserve life, and jurisdictional risk. Respond with ONLY valid JSON."},
        {"role": "user", "content": f"""Evaluate this metals/mining stock for investment.

{summary}

Evaluate: 1) Commodity price sensitivity and outlook 2) Production growth trajectory 3) Reserve quality and mine life 4) Cost structure (AISC for gold/silver, C1 for copper) 5) Balance sheet / debt load 6) Political/jurisdictional risk 7) Exploration pipeline 8) Dividend sustainability (if any)

Respond with ONLY this JSON:
{{"verdict":"OPPORTUNITY","confidence":65,"commodity_outlook":"BULLISH","production_trend":"GROWING","reserve_quality":"STRONG","growth_catalysts":["catalyst1"],"risks":["risk1"],"fair_value":0.00,"upside_pct":25,"risk_score":40,"position_limit_pct":5,"summary":"1-2 sentences"}}

verdict must be one of: OPPORTUNITY, RISKY, AVOID"""}
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20)
    result = _parse_json_response(raw, "screener_metals_vet")
    result["ticker"] = ticker
    result["price"] = candidate.get("price")
    result["market_cap"] = candidate.get("market_cap")
    result["name"] = candidate.get("name")
    result["sector"] = candidate.get("sector")
    return result


def vet_crypto_candidate(candidate: dict) -> dict:
    """AI vetting for crypto — network/tokenomics-focused."""
    if not is_configured():
        return {"error": "AI not configured", "ticker": candidate.get("ticker")}

    ticker = candidate.get("ticker", "???")
    summary = f"""CRYPTO: {ticker}
Name: {candidate.get('name', 'N/A')}
Price: ${candidate.get('price', 0)}
Market Cap: ${candidate.get('market_cap', 0):,.0f}
Category: {candidate.get('sector', 'N/A')}
Avg Volume: {candidate.get('volume', 0):,.0f}"""

    messages = [
        {"role": "system", "content": "You are a crypto analyst. Focus on network adoption, tokenomics, developer activity, regulatory risk, and market cycle positioning. Respond with ONLY valid JSON."},
        {"role": "user", "content": f"""Evaluate this cryptocurrency for investment.

{summary}

Evaluate: 1) Network adoption and usage metrics 2) Developer activity / ecosystem growth 3) TVL and DeFi metrics (if applicable) 4) Tokenomics (supply schedule, inflation, staking yield) 5) Market cycle position (accumulation, markup, distribution) 6) BTC correlation and narrative strength 7) Regulatory risk / exchange listing breadth 8) Transaction volume trends

Respond with ONLY this JSON:
{{"verdict":"BULLISH","confidence":65,"network_strength":"STRONG","adoption_trend":"GROWING","tokenomics_rating":"GOOD","growth_catalysts":["catalyst1"],"risks":["risk1"],"fair_value":0.00,"upside_pct":25,"summary":"1-2 sentences"}}

verdict must be one of: BULLISH, NEUTRAL, BEARISH"""}
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20)
    result = _parse_json_response(raw, "screener_crypto_vet")
    result["ticker"] = ticker
    result["price"] = candidate.get("price")
    result["market_cap"] = candidate.get("market_cap")
    result["name"] = candidate.get("name")
    result["sector"] = candidate.get("sector")
    return result


def vet_ai_candidate(candidate: dict) -> dict:
    """AI vetting for AI/Artificial Intelligence stocks."""
    if not is_configured():
        return {"error": "AI not configured", "ticker": candidate.get("ticker")}

    ticker = candidate.get("ticker", "???")
    summary = f"""STOCK: {ticker}
Name: {candidate.get('name', 'N/A')}
Price: ${candidate.get('price', 0)}
Market Cap: ${candidate.get('market_cap', 0):,.0f}
Sector: {candidate.get('sector', 'N/A')} | Industry: {candidate.get('industry', 'N/A')}
Avg Volume: {candidate.get('volume', 0):,.0f}
Revenue Growth: {candidate.get('revenue_growth', 'N/A')}
Earnings Growth: {candidate.get('earnings_growth', 'N/A')}
Forward P/E: {candidate.get('forward_pe', 'N/A')}"""

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        summary += f"""
Revenue: ${info.get('totalRevenue', 'N/A')}
Net Income: ${info.get('netIncomeToCommon', 'N/A')}
FCF: ${info.get('freeCashflow', 'N/A')}
Profit Margins: {info.get('profitMargins', 'N/A')}
Operating Margins: {info.get('operatingMargins', 'N/A')}
ROE: {info.get('returnOnEquity', 'N/A')}
52W Low: ${info.get('fiftyTwoWeekLow', 'N/A')} | 52W High: ${info.get('fiftyTwoWeekHigh', 'N/A')}"""
    except Exception:
        pass

    messages = [
        {"role": "system", "content": "You are an AI/Artificial Intelligence sector analyst. Focus on AI revenue exposure, competitive moat in AI, GPU/compute positioning, and valuation relative to AI growth trajectory. Respond with ONLY valid JSON."},
        {"role": "user", "content": f"""Evaluate this AI-related stock for investment.

{summary}

Evaluate: 1) AI revenue as % of total revenue 2) AI product pipeline and roadmap 3) Competitive moat in AI (data, models, distribution) 4) GPU/compute dependency and costs 5) Partnership ecosystem (cloud, enterprise) 6) Enterprise AI adoption rate 7) Valuation relative to AI growth trajectory 8) Key risks (competition, regulation, commoditization)

Respond with ONLY this JSON:
{{"verdict":"OPPORTUNITY","confidence":65,"ai_exposure":"HIGH","growth_trajectory":"ACCELERATING","competitive_position":"STRONG","growth_catalysts":["catalyst1"],"risks":["risk1"],"fair_value":0.00,"upside_pct":25,"risk_score":40,"position_limit_pct":5,"summary":"1-2 sentences"}}

verdict must be one of: OPPORTUNITY, RISKY, AVOID"""}
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20)
    result = _parse_json_response(raw, "screener_ai_vet")
    result["ticker"] = ticker
    result["price"] = candidate.get("price")
    result["market_cap"] = candidate.get("market_cap")
    result["name"] = candidate.get("name")
    result["sector"] = candidate.get("sector")
    return result


# ─── Main Screener Pipeline ───────────────────────────────────

def _parallel_vet(candidates: list, vet_fn, batch_size: int = 5) -> list:
    """Run AI vetting in parallel batches."""
    vetted = []
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = {executor.submit(vet_fn, c): c for c in batch}
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    if result.get("verdict"):
                        vetted.append(result)
                except Exception:
                    continue
    return vetted


def _categorize_results(vetted: list, positive_verdicts: list, cautious_verdicts: list) -> dict:
    """Sort vetted results into opportunities/risky/avoided."""
    opportunities = []
    risky = []
    avoided = 0

    positive_set = {v.upper() for v in positive_verdicts}
    cautious_set = {v.upper() for v in cautious_verdicts}

    for v in vetted:
        verdict = v.get("verdict", "").upper()
        if verdict in positive_set:
            opportunities.append(v)
        elif verdict in cautious_set:
            risky.append(v)
        else:
            avoided += 1

    opportunities.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    risky.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    return {"opportunities": opportunities, "risky": risky, "avoided": avoided}


def get_hot_sectors(period: str = "1mo") -> dict:
    """Use LLM to identify trending sectors/themes for a given time period."""
    if not is_configured():
        return {"error": "AI not configured", "period": period, "sectors": []}

    period_labels = {
        "1w": "past week", "2w": "past 2 weeks", "1mo": "past month",
        "3mo": "past 3 months", "6mo": "past 6 months", "1y": "past year",
    }
    label = period_labels.get(period, "past month")

    messages = [
        {"role": "system", "content": "You are a senior market strategist. Return ONLY valid JSON."},
        {"role": "user", "content": f"""Identify the top 6 hottest stock market sectors/themes over the {label}.
Return JSON: {{"sectors": [
  {{"rank":1,"name":"...","trend":"bullish|bearish|neutral",
    "momentum":"strong|moderate|fading",
    "catalysts":["...","..."],
    "top_tickers":["SYM1","SYM2","SYM3"],
    "outlook":"1-2 sentence forward outlook"}}
]}}
Consider: sector rotation, earnings trends, macro catalysts, fund flows, regulatory changes."""}
    ]

    try:
        raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=1024, timeout=25)
        parsed = _parse_json_response(raw, "hot_sectors")
        return {
            "period": period,
            "sectors": parsed.get("sectors", []),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "error": f"Hot sectors failed: {str(e)}",
            "period": period,
            "sectors": [],
            "timestamp": datetime.now().isoformat(),
        }


def run_screener(min_price: float = 2.0, max_price: float = 15.0,
                 limit: int = 20, category: str = "lowcap",
                 sectors: list = None) -> dict:
    """
    Full screener pipeline: scan -> parallel AI vet -> categorize -> return.
    Supports categories: lowcap, midcap, largecap, etf.
    """
    if not is_configured():
        return {
            "error": "OpenRouter API key not configured.",
            "candidates_scanned": 0, "opportunities": [], "risky": [],
            "avoided": 0, "timestamp": datetime.now().isoformat(),
        }

    # Scan based on category
    if category == "midcap":
        candidates = scan_midcap_candidates(min_price, max_price, limit=limit)
        vet_fn = vet_midcap_candidate
        positive = ["OPPORTUNITY"]
        cautious = ["RISKY"]
    elif category == "largecap":
        candidates = scan_largecap_candidates(limit=limit)
        vet_fn = vet_largecap_candidate
        positive = ["STRONG GROWTH"]
        cautious = ["STEADY"]
    elif category == "etf":
        candidates = scan_etf_candidates(limit=limit)
        vet_fn = vet_etf_candidate
        positive = ["STRONG BUY"]
        cautious = ["ACCUMULATE"]
    elif category == "metals_mining":
        candidates = scan_metals_mining_candidates(limit=limit)
        vet_fn = vet_metals_mining_candidate
        positive = ["OPPORTUNITY"]
        cautious = ["RISKY"]
    elif category == "crypto":
        candidates = scan_crypto_candidates(limit=limit)
        vet_fn = vet_crypto_candidate
        positive = ["BULLISH"]
        cautious = ["NEUTRAL"]
    elif category == "ai":
        candidates = scan_ai_candidates(limit=limit)
        vet_fn = vet_ai_candidate
        positive = ["OPPORTUNITY"]
        cautious = ["RISKY"]
    else:  # lowcap
        candidates = scan_lowcap_candidates(min_price, max_price, limit=limit)
        vet_fn = vet_candidate
        positive = ["OPPORTUNITY"]
        cautious = ["RISKY"]

    # Filter by sector if specified
    if sectors and category == "etf":
        candidates = [c for c in candidates if c.get("sector", "") in sectors or c.get("industry", "") in sectors]
    elif sectors and category == "crypto":
        # For crypto, sector comes from CRYPTO_SECTOR_MAP
        candidates = [c for c in candidates if c.get("sector", "") in sectors]
    elif sectors:
        candidates = [c for c in candidates if c.get("sector", "") in sectors]

    if not candidates:
        return {
            "candidates_scanned": 0, "opportunities": [], "risky": [],
            "avoided": 0, "timestamp": datetime.now().isoformat(),
            "category": category,
            "error": "No candidates found. Try adjusting filters.",
        }

    vetted = _parallel_vet(candidates, vet_fn)
    result = _categorize_results(vetted, positive, cautious)

    return {
        "candidates_scanned": len(candidates),
        "opportunities": result["opportunities"],
        "risky": result["risky"],
        "avoided": result["avoided"],
        "category": category,
        "timestamp": datetime.now().isoformat(),
    }
