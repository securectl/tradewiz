"""
Multi-Cap Stock Screener — AI-Vetted Opportunities
Supports Low-Cap ($2-$15), Mid-Cap ($15-$100), Large-Cap ($50+), ETFs,
Metals & Mining, Crypto, and AI (Artificial Intelligence), Top Gainers, and Top Losers.
Each category uses tailored AI vetting prompts.

Top Gainers/Losers scans ~2000 tickers across S&P 500, Russell 2000, NASDAQ 100
and existing curated lists with configurable timeframes (1d, 1w, 3mo).

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
    LLM_SCREENER, LLM_SUPERVISOR,
)
from shared.prompts.screener import (
    LOWCAP_SYSTEM, LOWCAP_USER_TEMPLATE,
    MIDCAP_SYSTEM, MIDCAP_USER_TEMPLATE,
    LARGECAP_SYSTEM, LARGECAP_USER_TEMPLATE,
    ETF_SYSTEM, ETF_USER_TEMPLATE,
    METALS_MINING_SYSTEM, METALS_MINING_USER_TEMPLATE,
    CRYPTO_SYSTEM, CRYPTO_USER_TEMPLATE,
    AI_SYSTEM, AI_USER_TEMPLATE,
    GAINERS_SYSTEM, GAINERS_USER_TEMPLATE,
    LOSERS_SYSTEM, LOSERS_USER_TEMPLATE,
    HOT_SECTORS_SYSTEM, HOT_SECTORS_USER_TEMPLATE,
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

# ─── Extended Universe for Top Gainers / Top Losers ──────────

# S&P 500 tickers (representative sample ~200)
SP500_TICKERS = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B", "UNH", "JNJ",
    "V", "XOM", "JPM", "PG", "MA", "HD", "CVX", "MRK", "ABBV", "LLY",
    "PEP", "KO", "COST", "AVGO", "WMT", "TMO", "MCD", "CSCO", "ACN", "ABT",
    "DHR", "CRM", "NKE", "TXN", "PM", "LIN", "NEE", "BMY", "UNP", "RTX",
    "QCOM", "ORCL", "LOW", "HON", "AMGN", "INTC", "INTU", "SCHW", "GS", "BLK",
    "ADP", "SBUX", "MDLZ", "GILD", "ADI", "ISRG", "VRTX", "NOW", "CME", "AMT",
    "GE", "DE", "SYK", "MO", "DUK", "SO", "CL", "REGN", "ZTS", "TMUS",
    "CB", "PLD", "CI", "MMC", "FISV", "HUM", "ICE", "AON", "SLB", "EMR",
    "APD", "USB", "FDX", "WM", "GM", "F", "AIG", "PNC", "TGT", "CMG",
    "PXD", "MPC", "PSA", "CCI", "SRE", "NSC", "MCK", "EW", "CARR", "FCX",
    "KMB", "AEP", "D", "OKE", "SPG", "DXCM", "HCA", "ALL", "AZO", "IDXX",
    "FTNT", "PCAR", "TEL", "MCHP", "GPN", "BK", "CTAS", "MSCI", "CDNS", "SNPS",
    "SHW", "A", "ON", "EL", "KEYS", "DAL", "GIS", "HSY", "DD", "DOW",
    "KDP", "HPQ", "WBA", "DVN", "HAL", "VLO", "MRO", "APA", "CZR", "WYNN",
    "MGM", "LVS", "NCLH", "RCL", "CCL", "UAL", "AAL", "ABNB", "BKNG", "MAR",
    "HLT", "LUV", "ALK", "DRI", "YUM", "SBUX", "WEN", "QSR", "CMG", "DPZ",
    "PANW", "ZS", "CRWD", "OKTA", "NET", "DDOG", "SNOW", "MDB", "TEAM", "WDAY",
    "TTD", "PINS", "SNAP", "UBER", "LYFT", "DASH", "COIN", "SQ", "PYPL", "SHOP",
    "ENPH", "FSLR", "SEDG", "RUN", "NEE", "AES", "CEG", "VST", "PCG", "EIX",
    "ET", "EPD", "KMI", "WMB", "LNG", "PSX", "VLO", "MPC", "HES", "COP",
]

# Russell 2000 top components (~300 representative)
RUSSELL2000_TOP = [
    "SMCI", "CELH", "CAVA", "DUOL", "WING", "BROS", "TOST", "DV", "GLBE", "FOUR",
    "CWAN", "BRZE", "GTLB", "CFLT", "ESTC", "DOCN", "PATH", "MNDY", "PCOR", "BILL",
    "HUBS", "PAYC", "WEX", "EVTC", "OLLI", "FIVE", "PLAY", "BURL", "BOOT", "DECK",
    "CROX", "SKX", "ON", "DIOD", "AMBA", "SLAB", "CRUS", "SWKS", "QRVO", "WOLF",
    "IRDM", "GSAT", "ASTS", "RKLB", "LUNR", "RDW", "SPCE", "JOBY", "ACHR", "EVTL",
    "IONQ", "RGTI", "QUBT", "ANET", "SMCI", "PSTG", "NTAP", "NTNX", "BOX", "ZUO",
    "TENB", "QLYS", "RPD", "VRNS", "S", "KNBE", "PRCH", "TASK", "OPFI", "RELY",
    "PAYO", "PSFE", "PAGS", "STNE", "NU", "AFRM", "SOFI", "UPST", "LC", "TREE",
    "TGTX", "RVMD", "AXSM", "KURA", "IMVT", "VRNA", "DAWN", "GERN", "INVA", "PRTA",
    "DCPH", "NVCR", "INSP", "GMED", "NVST", "ALGN", "TFX", "NEOG", "OMIC", "TWST",
    "BWEN", "SHLS", "ARRY", "STEM", "MAXN", "RUN", "NOVA", "AMPS", "EVGO", "CHPT",
    "BLNK", "CLNE", "FCEL", "PLUG", "BE", "MP", "LAC", "PLL", "GATO", "FSM",
    "EXK", "MAG", "SSRM", "CDE", "ORLA", "AG", "DRD", "GFI", "HMY", "BTG",
    "UEC", "DNN", "UUUU", "NXE", "CCJ", "SQM", "ALB", "WPM", "FNV", "RGLD",
    "HIMS", "CLAR", "BIRD", "BARK", "WOOF", "CHWY", "SFIX", "REAL", "PRPL", "LOVE",
    "DTC", "TLRY", "CGC", "ACB", "CRON", "GOEV", "LCID", "RIVN", "NKLA", "WKHS",
    "REE", "FUBO", "IQ", "CURI", "GENI", "ALLT", "MAPS", "CEVA", "ZETA", "BIGC",
    "AI", "BBAI", "SOUN", "PLTR", "PRCT", "IIPR", "SAFE", "LAND", "GOOD", "KTOS",
    "BKSY", "AXON", "TT", "NDSN", "RBC", "FSS", "GNRC", "AZTA", "DKNG", "PENN",
    "RSI", "GNOG", "CHDN", "BYD", "CZR", "WYNN", "RRR", "DNUT", "LOCO", "JACK",
    "TXRH", "CAKE", "RUTH", "BJRI", "ARCO", "SHAK", "CARG", "CVNA", "ANGI", "OPEN",
    "RDFN", "GRPN", "FVRR", "UPWK", "FIVERR", "ETSY", "W", "OSTK", "WISH", "POSH",
    "CPNG", "SE", "GRAB", "BABA", "JD", "PDD", "NIO", "LI", "XPEV", "BIDU",
    "BILI", "TME", "TIGR", "FUTU", "ZH", "MNSO", "TUYA", "GDS", "VNET", "KC",
    "VIR", "VERV", "EDIT", "NTLA", "CRSP", "BEAM", "PCVX", "MRNA", "BNTX", "NVAX",
    "DLO", "LSPD", "GLOB", "DLOCF", "CAAP", "MELI", "STNE", "PAGS", "XP", "VTEX",
    "INTA", "SWI", "RPM", "GMS", "SITE", "SUM", "VMC", "MLM", "CRH", "EXP",
    "ASPN", "BLDR", "TREX", "AZEK", "AWI", "DOOR", "FBIN", "MAS", "OC", "BLD",
    "POWL", "GVA", "PRIM", "MTZ", "FIX", "EMCOR", "APG", "AIT", "MSA", "MIDD",
    "WTS", "RXO", "XPO", "SAIA", "ODFL", "JBHT", "WERN", "LSTR", "KNX", "SNDR",
]

# NASDAQ 100 extra tickers not in other lists
NASDAQ100_EXTRA = [
    "ASML", "LRCX", "AMAT", "KLAC", "ARM", "MRVL", "ADI", "NXPI", "MPWR", "GFS",
    "ANSS", "PTC", "FROG", "MANH", "VRSK", "ILMN", "BIIB", "REGN", "SGEN", "DXCM",
    "EXC", "XEL", "WEC", "ED", "AWK", "TRGP", "FANG", "CTRA", "EOG", "OXY",
    "ADP", "PAYX", "FI", "GPN", "BR", "WTW", "CPRT", "ROST", "ORLY", "TSCO",
    "MKTX", "CBOE", "NDAQ", "CME", "SPGI", "MSCI", "MCO", "INFO", "FDS", "VRSK",
]


def _get_full_universe() -> list:
    """Build deduplicated ticker universe (~2000 tickers) for top movers scan.
    Excludes crypto tickers (ending in -USD)."""
    all_tickers = (
        LOWCAP_TICKERS + MIDCAP_TICKERS + LARGECAP_TICKERS + ETF_TICKERS +
        METALS_MINING_TICKERS + AI_TICKERS + SP500_TICKERS +
        RUSSELL2000_TOP + NASDAQ100_EXTRA
    )
    seen = set()
    universe = []
    for t in all_tickers:
        if t.endswith("-USD"):
            continue
        t_upper = t.upper()
        if t_upper not in seen:
            seen.add(t_upper)
            universe.append(t_upper)
    return universe


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


def scan_top_movers(direction: str = "gainers", period: str = "1d",
                    limit: int = 20) -> list:
    """Scan full universe for top gainers or losers over a given period.

    Args:
        direction: 'gainers' or 'losers'
        period: '1d', '1w', or '3mo'
        limit: max results to return (enriched with fundamentals)
    """
    if not HAS_YFINANCE:
        return []

    period_map = {"1d": "2d", "1w": "5d", "3mo": "3mo"}
    yf_period = period_map.get(period, "2d")

    universe = _get_full_universe()
    movers = []
    batch_size = 100

    try:
        for i in range(0, len(universe), batch_size):
            batch = universe[i:i + batch_size]
            try:
                tickers_str = " ".join(batch)
                data = yf.download(tickers_str, period=yf_period, progress=False, threads=True)
                if data.empty:
                    continue

                close_data = data.get("Close")
                if close_data is None:
                    continue

                if len(batch) == 1:
                    col = close_data.dropna()
                    if len(col) >= 2:
                        first_price = float(col.iloc[0])
                        last_price = float(col.iloc[-1])
                        if first_price > 0:
                            pct_change = ((last_price - first_price) / first_price) * 100
                            movers.append({
                                "ticker": batch[0], "price": round(last_price, 2),
                                "pct_change": round(pct_change, 2),
                            })
                else:
                    for ticker in batch:
                        try:
                            if ticker not in close_data.columns:
                                continue
                            col = close_data[ticker].dropna()
                            if len(col) < 2:
                                continue
                            first_price = float(col.iloc[0])
                            last_price = float(col.iloc[-1])
                            if first_price > 0:
                                pct_change = ((last_price - first_price) / first_price) * 100
                                movers.append({
                                    "ticker": ticker, "price": round(last_price, 2),
                                    "pct_change": round(pct_change, 2),
                                })
                        except (KeyError, IndexError, TypeError):
                            continue
            except Exception:
                continue
    except Exception:
        pass

    # Sort: descending for gainers, ascending for losers
    if direction == "gainers":
        movers.sort(key=lambda x: x["pct_change"], reverse=True)
    else:
        movers.sort(key=lambda x: x["pct_change"])

    # Take top N*3 for enrichment, then trim to limit
    top_candidates = movers[:limit * 3]

    # Enrich with fundamentals, filter out micro-caps < $100M
    enriched = []
    for c in top_candidates:
        try:
            t = yf.Ticker(c["ticker"])
            info = t.info or {}
            mkt_cap = info.get("marketCap", 0) or 0
            if mkt_cap < 100_000_000:
                continue
            enriched.append({
                "ticker": c["ticker"],
                "price": c["price"],
                "pct_change": c["pct_change"],
                "market_cap": mkt_cap,
                "name": info.get("shortName", info.get("longName", c["ticker"])),
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", "Unknown"),
                "volume": info.get("averageVolume", 0),
                "forward_pe": info.get("forwardPE"),
                "revenue_growth": info.get("revenueGrowth"),
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
        {"role": "system", "content": LOWCAP_SYSTEM},
        {"role": "user", "content": LOWCAP_USER_TEMPLATE.format(summary=summary)},
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
        {"role": "system", "content": MIDCAP_SYSTEM},
        {"role": "user", "content": MIDCAP_USER_TEMPLATE.format(summary=summary)},
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
        {"role": "system", "content": LARGECAP_SYSTEM},
        {"role": "user", "content": LARGECAP_USER_TEMPLATE.format(summary=summary)},
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
        {"role": "system", "content": ETF_SYSTEM},
        {"role": "user", "content": ETF_USER_TEMPLATE.format(summary=summary)},
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
        {"role": "system", "content": METALS_MINING_SYSTEM},
        {"role": "user", "content": METALS_MINING_USER_TEMPLATE.format(summary=summary)},
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
        {"role": "system", "content": CRYPTO_SYSTEM},
        {"role": "user", "content": CRYPTO_USER_TEMPLATE.format(summary=summary)},
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
        {"role": "system", "content": AI_SYSTEM},
        {"role": "user", "content": AI_USER_TEMPLATE.format(summary=summary)},
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20)
    result = _parse_json_response(raw, "screener_ai_vet")
    result["ticker"] = ticker
    result["price"] = candidate.get("price")
    result["market_cap"] = candidate.get("market_cap")
    result["name"] = candidate.get("name")
    result["sector"] = candidate.get("sector")
    return result


def vet_mover_candidate(candidate: dict, direction: str = "gainers",
                        period: str = "1d") -> dict:
    """AI vetting for top gainers/losers — momentum or recovery analysis."""
    if not is_configured():
        return {"error": "AI not configured", "ticker": candidate.get("ticker")}

    ticker = candidate.get("ticker", "???")
    pct_change = candidate.get("pct_change", 0)
    period_labels = {"1d": "1 day", "1w": "1 week", "3mo": "3 months"}
    period_label = period_labels.get(period, "1 day")

    summary = f"""STOCK: {ticker}
Name: {candidate.get('name', 'N/A')}
Price: ${candidate.get('price', 0)}
Change ({period_label}): {pct_change:+.2f}%
Market Cap: ${candidate.get('market_cap', 0):,.0f}
Sector: {candidate.get('sector', 'N/A')} | Industry: {candidate.get('industry', 'N/A')}
Avg Volume: {candidate.get('volume', 0):,.0f}
Forward P/E: {candidate.get('forward_pe', 'N/A')}
Revenue Growth: {candidate.get('revenue_growth', 'N/A')}
Profit Margins: {candidate.get('profit_margins', 'N/A')}"""

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        summary += f"""
52W Low: ${info.get('fiftyTwoWeekLow', 'N/A')} | 52W High: ${info.get('fiftyTwoWeekHigh', 'N/A')}
Beta: {info.get('beta', 'N/A')}
Short Ratio: {info.get('shortRatio', 'N/A')}
Analyst Target: ${info.get('targetMeanPrice', 'N/A')}"""
    except Exception:
        pass

    if direction == "gainers":
        system_msg = GAINERS_SYSTEM
        user_msg = GAINERS_USER_TEMPLATE.format(pct_change=pct_change, period_label=period_label, summary=summary)
    else:
        system_msg = LOSERS_SYSTEM
        user_msg = LOSERS_USER_TEMPLATE.format(pct_change=pct_change, period_label=period_label, summary=summary)

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20)
    result = _parse_json_response(raw, f"screener_{direction}_vet")
    result["ticker"] = ticker
    result["price"] = candidate.get("price")
    result["pct_change"] = pct_change
    result["market_cap"] = candidate.get("market_cap")
    result["name"] = candidate.get("name")
    result["sector"] = candidate.get("sector")
    return result


# ─── Main Screener Pipeline ───────────────────────────────────

def _parallel_vet(candidates: list, vet_fn, batch_size: int = 5) -> list:
    """Run AI vetting in parallel batches."""
    # Capture thread-local LLM user context for propagation into child threads
    try:
        from rate_limiter import get_llm_user, set_llm_user
        _ctx_uid, _ctx_src = get_llm_user()
    except Exception:
        _ctx_uid, _ctx_src = None, None

    def _run_with_context(c):
        if _ctx_uid:
            set_llm_user(_ctx_uid, _ctx_src)
        return vet_fn(c)

    vetted = []
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = {executor.submit(_run_with_context, c): c for c in batch}
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
        {"role": "system", "content": HOT_SECTORS_SYSTEM},
        {"role": "user", "content": HOT_SECTORS_USER_TEMPLATE.format(label=label)},
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
                 sectors: list = None, timeframe: str = "1d") -> dict:
    """
    Full screener pipeline: scan -> parallel AI vet -> categorize -> return.
    Supports categories: lowcap, midcap, largecap, etf, metals_mining, crypto, ai, gainers, losers.
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
    elif category == "gainers":
        candidates = scan_top_movers("gainers", timeframe, limit=limit)
        vet_fn = lambda c: vet_mover_candidate(c, "gainers", timeframe)
        positive = ["MOMENTUM BUY"]
        cautious = ["WATCH"]
    elif category == "losers":
        candidates = scan_top_movers("losers", timeframe, limit=limit)
        vet_fn = lambda c: vet_mover_candidate(c, "losers", timeframe)
        positive = ["RECOVERY BUY"]
        cautious = ["WATCH"]
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

    # Supervisor post-filter — only if LLM_SUPERVISOR is configured
    if LLM_SUPERVISOR:
        try:
            from ai_validator import _validate_supervisor
            filtered_opps = []
            supervisor_vetoed = 0
            for opp in result["opportunities"]:
                sv = _validate_supervisor(
                    f"{opp.get('ticker')}: verdict={opp.get('verdict')}, confidence={opp.get('confidence')}, summary={opp.get('summary', '')}",
                    {"verdict": opp.get("verdict"), "confidence": opp.get("confidence"),
                     "ticker": opp.get("ticker"), "category": category},
                    layer="screener",
                )
                if sv.get("override") is True:
                    supervisor_vetoed += 1
                else:
                    opp["supervisor"] = "approved"
                    filtered_opps.append(opp)
            result["opportunities"] = filtered_opps
            result["avoided"] += supervisor_vetoed
        except Exception:
            pass  # Graceful degradation

    return {
        "candidates_scanned": len(candidates),
        "opportunities": result["opportunities"],
        "risky": result["risky"],
        "avoided": result["avoided"],
        "category": category,
        "timestamp": datetime.now().isoformat(),
    }
