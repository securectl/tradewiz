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
    OVERSOLD_SYSTEM, OVERSOLD_USER_TEMPLATE,
    OVERSOLD_VALIDATOR_SYSTEM, OVERSOLD_VALIDATOR_USER_TEMPLATE,
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
    "WOLF", "AXTI", "INDI", "NVTS", "POWI", "SITM", "ALGM",
    # AI Infra / Optical / Networking (the rocket names — Nov '25/'26 leaders)
    "NBIS", "COHR", "AAOI", "LITE", "FN", "CIEN", "ANET", "CRDO",
    "ALAB", "VRT", "PSTG", "SMCI", "ARM", "TSM",
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

def _money_flow_from_download(data, ticker, single):
    """Compute money flow (CMF/MFI signal) for one ticker from a batched
    yf.download frame. `single` is True when the batch held one ticker (columns
    are flat Series rather than per-ticker DataFrames). Never raises — returns a
    neutral/placeholder dict when OHLCV is missing or history is too short."""
    from shared.money_flow import compute_money_flow
    import pandas as pd

    none_mf = {"cmf": None, "mfi": None, "mf_signal": None, "mf_label": "—"}
    try:
        cols = {}
        for field in ("High", "Low", "Close", "Volume"):
            block = data.get(field)
            if block is None:
                return none_mf
            if single:
                cols[field] = block
            elif ticker in block.columns:
                cols[field] = block[ticker]
            else:
                return none_mf
        df = pd.DataFrame(cols).dropna()
        return compute_money_flow(df)
    except Exception:
        return none_mf


def _money_flow_fields(mf):
    """Pick just the three persisted/displayed keys from a money-flow dict."""
    return {"cmf": mf.get("cmf"), "mfi": mf.get("mfi"), "mf_signal": mf.get("mf_signal")}


def _apply_alpaca_money_flow(candidates):
    """Fill/override each candidate's money-flow fields from Alpaca daily bars.
    No-op when Alpaca is unconfigured or for symbols it can't serve (the
    yfinance-derived values computed during scanning remain). Never raises."""
    try:
        from shared.alpaca_data import is_configured, money_flow_map
        if not is_configured():
            return
        tickers = [c.get("ticker") for c in candidates if c.get("ticker")]
        mf_by_sym = money_flow_map(tickers)
        if not mf_by_sym:
            return
        for c in candidates:
            mf = mf_by_sym.get((c.get("ticker") or "").upper())
            if mf and mf.get("mf_signal") is not None:
                c.update(_money_flow_fields(mf))
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"alpaca money-flow pass skipped: {e}")


def _mf_prompt_line(candidate):
    """One-line money-flow context appended to LLM vetting prompts so the model
    can weigh accumulation vs distribution / profit-taking. Empty when no
    signal (insufficient history) so the prompt is unchanged in that case."""
    sig = candidate.get("mf_signal")
    if not sig:
        return ""
    meaning = {
        "IN": "money flowing in (accumulation)",
        "OUT": "money flowing out (distribution / selling pressure)",
        "PROFIT_TAKING": "money leaving while price is near its highs — possible top / profit-taking",
        "NEUTRAL": "balanced money flow",
    }.get(sig, "")
    parts = []
    if candidate.get("cmf") is not None:
        parts.append(f"CMF {candidate['cmf']:+.2f}")
    if candidate.get("mfi") is not None:
        parts.append(f"MFI {candidate['mfi']:.0f}")
    detail = f" ({', '.join(parts)})" if parts else ""
    return f"\nMoney Flow: {meaning}{detail}."


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
                # 1mo (not 5d) so there are enough bars for the money-flow
                # indicators; only the last close is used for price/filtering.
                data = yf.download(tickers_str, period="1mo", progress=False, threads=True)
                if data.empty:
                    continue

                close_data = data.get("Close")
                if close_data is None:
                    continue
                single = len(batch) == 1

                if single:
                    last_price = close_data.dropna().iloc[-1] if not close_data.dropna().empty else None
                    if last_price:
                        price = float(last_price)
                        if (min_price is None or price >= min_price) and (max_price is None or price <= max_price):
                            mf = _money_flow_from_download(data, batch[0], single)
                            candidates.append({"ticker": batch[0], "price": round(price, 2),
                                               **_money_flow_fields(mf)})
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
                                mf = _money_flow_from_download(data, ticker, single)
                                candidates.append({"ticker": ticker, "price": round(price, 2),
                                                   **_money_flow_fields(mf)})
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
                **_money_flow_fields(c),
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
                **_money_flow_fields(c),
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
                **_money_flow_fields(c),
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
                **_money_flow_fields(c),
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
                **_money_flow_fields(c),
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
                **_money_flow_fields(c),
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
                **_money_flow_fields(c),
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
    # Sessions of lookback for the pct-change window per timeframe.
    lookback_map = {"1d": 1, "1w": 5, "3mo": 63}
    lookback = lookback_map.get(period, 1)

    universe = _get_full_universe()

    # Discovery: prefer Alpaca (reliable, not IP-throttled like Yahoo's bulk
    # download); fall back to yfinance when Alpaca is unconfigured.
    movers = _alpaca_movers(universe, lookback)
    if not movers:
        if not HAS_YFINANCE:
            return []
        movers = _yf_movers(universe, period)

    # Sort: descending for gainers, ascending for losers
    movers.sort(key=lambda x: x["pct_change"], reverse=(direction == "gainers"))

    # Take top N*3 for enrichment, then trim to limit
    top_candidates = movers[:limit * 3]

    # Enrich with fundamentals. Tolerant of a throttled/empty yfinance .info:
    # the universe is pre-curated, so when fundamentals are unavailable we keep
    # the candidate (unknown market cap) rather than dropping the whole scan.
    enriched = []
    for c in top_candidates:
        info = {}
        try:
            info = yf.Ticker(c["ticker"]).info or {}
        except Exception:
            info = {}
        mkt_cap = info.get("marketCap", 0) or 0
        if mkt_cap and mkt_cap < 100_000_000:   # known micro-cap → skip; unknown (0) → keep
            continue
        enriched.append({
            **_money_flow_fields(c),
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
    return enriched


def _alpaca_movers(universe, lookback):
    """Discover movers via Alpaca daily bars (one batched, cached fetch for the
    whole universe). Returns [{ticker, price, pct_change, cmf, mfi, mf_signal}].
    Empty list when Alpaca is unconfigured so the caller falls back to yfinance."""
    try:
        from shared.alpaca_data import is_configured, get_daily_bars
        from shared.money_flow import compute_money_flow
        if not is_configured():
            return []
        bars = get_daily_bars(universe, days=max(lookback + 5, 70))
        movers = []
        for ticker, df in bars.items():
            try:
                closes = df["Close"]
                if len(closes) < 2:
                    continue
                last = float(closes.iloc[-1])
                ref = float(closes.iloc[-(lookback + 1)]) if len(closes) > lookback else float(closes.iloc[0])
                if ref <= 0:
                    continue
                pct = (last - ref) / ref * 100
                mf = compute_money_flow(df)
                movers.append({
                    "ticker": ticker, "price": round(last, 2),
                    "pct_change": round(pct, 2), **_money_flow_fields(mf),
                })
            except Exception:
                continue
        return movers
    except Exception:
        return []


def _yf_movers(universe, period):
    """Fallback movers discovery via yfinance bulk download (subject to Yahoo
    rate limiting). Same shape as _alpaca_movers."""
    period_map = {"1d": "2d", "1w": "5d", "3mo": "3mo"}
    yf_period = period_map.get(period, "2d")
    movers = []
    batch_size = 100
    try:
        for i in range(0, len(universe), batch_size):
            batch = universe[i:i + batch_size]
            try:
                data = yf.download(" ".join(batch), period=yf_period, progress=False, threads=True)
                if data.empty:
                    continue
                close_data = data.get("Close")
                if close_data is None:
                    continue
                single = len(batch) == 1
                cols = [batch[0]] if single else [t for t in batch if t in close_data.columns]
                for ticker in cols:
                    try:
                        col = (close_data if single else close_data[ticker]).dropna()
                        if len(col) < 2:
                            continue
                        first_price, last_price = float(col.iloc[0]), float(col.iloc[-1])
                        if first_price <= 0:
                            continue
                        mf = _money_flow_from_download(data, ticker, single)
                        movers.append({
                            "ticker": ticker, "price": round(last_price, 2),
                            "pct_change": round(((last_price - first_price) / first_price) * 100, 2),
                            **_money_flow_fields(mf),
                        })
                    except (KeyError, IndexError, TypeError):
                        continue
            except Exception:
                continue
    except Exception:
        pass
    return movers


def scan_oversold_candidates(limit: int = 30) -> list:
    """Scan universe for stocks oversold in the past 1 month.

    2-stage approach to stay under timeout:
      Stage 1: Quick 5-day scan of full universe — find stocks down >5% in 5 days (fast).
      Stage 2: Download 1-month data ONLY for those ~100-150 losers — compute RSI-14.
    This cuts the heavy download by ~90% vs scanning all tickers with 1mo data."""
    if not HAS_YFINANCE:
        return []

    import logging
    import numpy as np

    log = logging.getLogger(__name__)
    universe = _get_full_universe()
    log.info(f"[OVERSOLD] Stage 1: Quick 5-day prefilter on {len(universe)} tickers...")

    # ── Stage 1: Fast 5-day prefilter ──
    # Download 5d data (very fast) and find tickers that dropped >3%
    losers_5d = []
    batch_size = 200

    try:
        for i in range(0, len(universe), batch_size):
            batch = universe[i:i + batch_size]
            try:
                tickers_str = " ".join(batch)
                data = yf.download(tickers_str, period="5d", progress=False, threads=True)
                if data.empty:
                    continue

                close_data = data.get("Close")
                if close_data is None:
                    continue

                tickers_to_check = batch if len(batch) == 1 else [t for t in batch if t in close_data.columns]

                for ticker in tickers_to_check:
                    try:
                        col = close_data[ticker].dropna() if len(batch) > 1 else close_data.dropna()
                        if len(col) < 2:
                            continue
                        first = float(col.iloc[0])
                        last = float(col.iloc[-1])
                        if first > 0:
                            pct = (last - first) / first * 100
                            if pct < -3:  # Down >3% in 5 days = potential oversold
                                losers_5d.append(ticker)
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
            except Exception:
                continue
    except Exception:
        pass

    log.info(f"[OVERSOLD] Stage 1 found {len(losers_5d)} tickers down >3% in 5 days")

    if not losers_5d:
        return []

    # Cap to avoid still being too slow
    losers_5d = losers_5d[:300]

    # ── Stage 2: 1-month RSI calculation on prefiltered losers only ──
    log.info(f"[OVERSOLD] Stage 2: Computing RSI-14 on {len(losers_5d)} candidates...")
    oversold = []
    batch_size_2 = 100

    try:
        for i in range(0, len(losers_5d), batch_size_2):
            batch = losers_5d[i:i + batch_size_2]
            try:
                tickers_str = " ".join(batch)
                data = yf.download(tickers_str, period="1mo", progress=False, threads=True)
                if data.empty:
                    continue

                close_data = data.get("Close")
                volume_data = data.get("Volume")
                if close_data is None:
                    continue

                tickers_to_check = batch if len(batch) == 1 else [t for t in batch if t in close_data.columns]

                for ticker in tickers_to_check:
                    try:
                        if len(batch) == 1:
                            col = close_data.dropna()
                            vol_col = volume_data.dropna() if volume_data is not None else None
                        else:
                            col = close_data[ticker].dropna()
                            vol_col = volume_data[ticker].dropna() if volume_data is not None and ticker in volume_data.columns else None

                        if len(col) < 14:
                            continue

                        prices = col.values.astype(float)
                        deltas = np.diff(prices)
                        gains = np.where(deltas > 0, deltas, 0)
                        losses = np.where(deltas < 0, -deltas, 0)

                        avg_gain = np.mean(gains[-14:])
                        avg_loss = np.mean(losses[-14:])

                        if avg_loss == 0:
                            continue
                        rs = avg_gain / avg_loss
                        rsi = 100 - (100 / (1 + rs))

                        if rsi >= 35:
                            continue

                        last_price = float(prices[-1])
                        first_price = float(prices[0])
                        pct_change_1mo = ((last_price - first_price) / first_price) * 100

                        sma_20 = float(np.mean(prices[-20:])) if len(prices) >= 20 else None
                        sma_10 = float(np.mean(prices[-10:])) if len(prices) >= 10 else None
                        avg_vol = float(np.mean(vol_col.values[-14:])) if vol_col is not None and len(vol_col) >= 14 else 0

                        mf = _money_flow_from_download(data, ticker, len(batch) == 1)

                        oversold.append({
                            "ticker": ticker,
                            "price": float(round(last_price, 2)),
                            "rsi_14": float(round(rsi, 1)),
                            "pct_change_1mo": float(round(pct_change_1mo, 2)),
                            "sma_20": float(round(sma_20, 2)) if sma_20 else None,
                            "sma_10": float(round(sma_10, 2)) if sma_10 else None,
                            "avg_volume": int(avg_vol),
                            **_money_flow_fields(mf),
                        })
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
            except Exception:
                continue
    except Exception:
        pass

    log.info(f"[OVERSOLD] Stage 2 found {len(oversold)} tickers with RSI < 35")

    oversold.sort(key=lambda x: x["rsi_14"])

    # ── Stage 3: Enrich top candidates with fundamentals (parallel) ──
    top_candidates = oversold[:limit * 3]

    def _enrich(c):
        try:
            t = yf.Ticker(c["ticker"])
            info = t.info or {}
            mkt_cap = info.get("marketCap", 0) or 0
            if mkt_cap < 100_000_000:
                return None
            return {
                **c,
                "market_cap": mkt_cap,
                "name": info.get("shortName", info.get("longName", c["ticker"])),
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", "Unknown"),
                "forward_pe": info.get("forwardPE"),
                "revenue_growth": info.get("revenueGrowth"),
                "profit_margins": info.get("profitMargins"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "short_ratio": info.get("shortRatio"),
                "analyst_target": info.get("targetMeanPrice"),
            }
        except Exception:
            return None

    enriched = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_enrich, c): c for c in top_candidates}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                enriched.append(result)
                if len(enriched) >= limit:
                    break

    enriched.sort(key=lambda x: x["rsi_14"])
    log.info(f"[OVERSOLD] Returning {len(enriched)} enriched oversold candidates")
    return enriched[:limit]


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
        {"role": "user", "content": LOWCAP_USER_TEMPLATE.format(summary=summary + _mf_prompt_line(candidate))},
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20, role="screener")
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
        {"role": "user", "content": MIDCAP_USER_TEMPLATE.format(summary=summary + _mf_prompt_line(candidate))},
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20, role="screener")
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
        {"role": "user", "content": LARGECAP_USER_TEMPLATE.format(summary=summary + _mf_prompt_line(candidate))},
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20, role="screener")
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
        {"role": "user", "content": ETF_USER_TEMPLATE.format(summary=summary + _mf_prompt_line(candidate))},
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20, role="screener")
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
        {"role": "user", "content": METALS_MINING_USER_TEMPLATE.format(summary=summary + _mf_prompt_line(candidate))},
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20, role="screener")
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
        {"role": "user", "content": CRYPTO_USER_TEMPLATE.format(summary=summary + _mf_prompt_line(candidate))},
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20, role="screener")
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
        {"role": "user", "content": AI_USER_TEMPLATE.format(summary=summary + _mf_prompt_line(candidate))},
    ]

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20, role="screener")
    result = _parse_json_response(raw, "screener_ai_vet")
    result["ticker"] = ticker
    result["price"] = candidate.get("price")
    result["market_cap"] = candidate.get("market_cap")
    result["name"] = candidate.get("name")
    result["sector"] = candidate.get("sector")
    return result


def vet_oversold_candidate(candidate: dict) -> dict:
    """Dual-LLM oversold vetting: LLM #1 tags bottom signals, LLM #2 validates."""
    if not is_configured():
        return {"error": "AI not configured", "ticker": candidate.get("ticker")}

    ticker = candidate.get("ticker", "???")
    rsi = candidate.get("rsi_14", "N/A")
    pct_change = candidate.get("pct_change_1mo", 0)

    summary = f"""STOCK: {ticker}
Name: {candidate.get('name', 'N/A')}
Price: ${candidate.get('price', 0)}
RSI-14: {rsi}
1-Month Change: {pct_change:+.2f}%
Market Cap: ${candidate.get('market_cap', 0):,.0f}
Sector: {candidate.get('sector', 'N/A')} | Industry: {candidate.get('industry', 'N/A')}
Avg Volume (14d): {candidate.get('avg_volume', 0):,.0f}
SMA-10: ${candidate.get('sma_10', 'N/A')} | SMA-20: ${candidate.get('sma_20', 'N/A')}
Forward P/E: {candidate.get('forward_pe', 'N/A')}
Revenue Growth: {candidate.get('revenue_growth', 'N/A')}
Profit Margins: {candidate.get('profit_margins', 'N/A')}
52W Low: ${candidate.get('fifty_two_week_low', 'N/A')} | 52W High: ${candidate.get('fifty_two_week_high', 'N/A')}
Short Ratio: {candidate.get('short_ratio', 'N/A')}
Analyst Target: ${candidate.get('analyst_target', 'N/A')}"""

    # Enrich with additional yfinance data
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        summary += f"""
Beta: {info.get('beta', 'N/A')}
Debt/Equity: {info.get('debtToEquity', 'N/A')}
Current Ratio: {info.get('currentRatio', 'N/A')}
Insider Ownership: {info.get('heldPercentInsiders', 'N/A')}
Inst Ownership: {info.get('heldPercentInstitutions', 'N/A')}"""
    except Exception:
        pass

    # ── LLM #1: Tag / Identify bottom signals ──
    messages_tag = [
        {"role": "system", "content": OVERSOLD_SYSTEM},
        {"role": "user", "content": OVERSOLD_USER_TEMPLATE.format(summary=summary + _mf_prompt_line(candidate))},
    ]

    raw_tag = _call_openrouter(LLM_SCREENER, messages_tag, max_tokens=768, timeout=20, role="screener")
    tag_result = _parse_json_response(raw_tag, "screener_oversold_tag")

    initial_verdict = tag_result.get("verdict", "WATCH")
    initial_confidence = tag_result.get("confidence", 50)
    initial_summary = tag_result.get("summary", "")
    rsi_assessment = tag_result.get("rsi_assessment", "N/A")
    bottom_signal = tag_result.get("bottom_signal_strength", "N/A")
    decline_reason = tag_result.get("decline_reason", "N/A")

    # ── LLM #2: Validate / Challenge the initial call ──
    messages_validate = [
        {"role": "system", "content": OVERSOLD_VALIDATOR_SYSTEM},
        {"role": "user", "content": OVERSOLD_VALIDATOR_USER_TEMPLATE.format(
            ticker=ticker,
            initial_verdict=initial_verdict,
            initial_confidence=initial_confidence,
            initial_summary=initial_summary,
            rsi_assessment=rsi_assessment,
            bottom_signal_strength=bottom_signal,
            decline_reason=decline_reason,
            summary=summary,
        )},
    ]

    raw_validate = _call_openrouter(LLM_SUPERVISOR if LLM_SUPERVISOR else LLM_SCREENER,
                                     messages_validate, max_tokens=768, timeout=20,
                                     role="supervisor" if LLM_SUPERVISOR else "screener")
    val_result = _parse_json_response(raw_validate, "screener_oversold_validate")

    # Merge results — validator gets final say
    validated = val_result.get("validated", True)
    final_verdict = val_result.get("adjusted_verdict", initial_verdict) if validated else "FALLING KNIFE"
    final_confidence = val_result.get("adjusted_confidence", initial_confidence)

    result = {
        "ticker": ticker,
        "price": candidate.get("price"),
        "rsi_14": rsi,
        "pct_change_1mo": pct_change,
        "market_cap": candidate.get("market_cap"),
        "name": candidate.get("name"),
        "sector": candidate.get("sector"),
        "verdict": final_verdict,
        "confidence": final_confidence,
        # From tagger
        "rsi_assessment": rsi_assessment,
        "bottom_signal_strength": bottom_signal,
        "decline_reason": decline_reason,
        "support_level": tag_result.get("support_level"),
        "key_indicators": tag_result.get("key_indicators", []),
        "recovery_catalysts": tag_result.get("recovery_catalysts", []),
        "downside_remaining_pct": tag_result.get("downside_remaining_pct"),
        "estimated_bounce_pct": tag_result.get("estimated_bounce_pct"),
        "position_limit_pct": val_result.get("final_position_limit_pct", tag_result.get("position_limit_pct", 3)),
        # From validator
        "validated": validated,
        "challenge_points": val_result.get("challenge_points", []),
        "validation_notes": val_result.get("validation_notes", ""),
        "risk_flags": val_result.get("risk_flags", []),
        "summary": val_result.get("summary", initial_summary),
        # Risks from both
        "risks": tag_result.get("risks", []) + val_result.get("risk_flags", []),
    }
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

    summary += _mf_prompt_line(candidate)
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

    raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=768, timeout=20, role="screener")
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
        result = vet_fn(c)
        # vet_fn returns only the LLM verdict; carry the money-flow fields
        # computed during scanning through to the vetted result (for storage +
        # frontend) for every category.
        if isinstance(result, dict) and isinstance(c, dict):
            for k in ("cmf", "mfi", "mf_signal"):
                if result.get(k) is None and c.get(k) is not None:
                    result[k] = c.get(k)
        return result

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
        raw = _call_openrouter(LLM_SCREENER, messages, max_tokens=1024, timeout=25, role="screener")
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


def _oversold_background_scan(limit=20):
    """Heavy oversold scan — runs in background thread, stores results in DB."""
    import logging
    log = logging.getLogger(__name__)

    try:
        from db import query, execute, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
    except ImportError:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    log.info("[OVERSOLD-BG] Background scan started...")

    try:
        candidates = scan_oversold_candidates(limit=limit)
        if not candidates:
            log.info("[OVERSOLD-BG] No candidates found")
            return

        _apply_alpaca_money_flow(candidates)
        vetted = _parallel_vet(candidates, vet_oversold_candidate)

        for v in vetted:
            ticker = v.get("ticker", "???")
            price = v.get("price", 0)
            rsi = v.get("rsi_14")
            verdict = v.get("verdict", "WATCH")
            confidence = v.get("confidence", 50)

            # Look up history
            history = []
            try:
                history = query(
                    f"SELECT scan_date, price, rsi_14, status FROM oversold_daily "
                    f"WHERE ticker = {P} AND scan_date < {P} ORDER BY scan_date DESC LIMIT 5",
                    (ticker, today),
                )
            except Exception:
                pass

            first_seen = history[-1]["scan_date"] if history else today
            days_tracked = len(history) + 1

            # Consolidation detection
            status = "tracking"
            price_trend = "new"

            if len(history) >= 3:
                recent_prices = [float(h["price"]) for h in history[:3]]
                recent_prices.reverse()
                recent_prices.append(float(price))

                changes = []
                for i in range(1, len(recent_prices)):
                    if recent_prices[i - 1] > 0:
                        changes.append((recent_prices[i] - recent_prices[i - 1]) / recent_prices[i - 1] * 100)

                if changes:
                    declining_days = sum(1 for c in changes if c < -1.0)
                    stable_days = sum(1 for c in changes if -1.0 <= c <= 1.5)
                    rising_days = sum(1 for c in changes if c > 1.5)

                    if declining_days >= 2 and changes[-1] < -0.5:
                        status, price_trend = "tracking", "falling"
                    elif stable_days >= 2 or (rising_days >= 1 and declining_days <= 1):
                        status = "consolidating"
                        price_trend = "stabilizing" if stable_days >= 2 else "bouncing"
                    elif changes[-1] > 0 and len(history) >= 4:
                        status, price_trend = "consolidating", "first_bounce"
                    else:
                        status, price_trend = "tracking", "mixed"
            elif len(history) >= 1:
                prev_price = float(history[0]["price"])
                if prev_price > 0:
                    chg = (float(price) - prev_price) / prev_price * 100
                    price_trend = "falling" if chg < -1.0 else "stabilizing" if chg > -1.0 else "flat"
                status = "tracking"

            if verdict == "FALLING KNIFE":
                status = "tracking"
            if status == "consolidating" and confidence < 45:
                status = "tracking"

            # Store in oversold_daily
            try:
                if IS_POSTGRES:
                    execute(
                        f"INSERT INTO oversold_daily (ticker, scan_date, price, rsi_14, pct_change_1mo, "
                        f"market_cap, sector, name, ai_verdict, ai_confidence, ai_summary, "
                        f"bottom_signal_strength, decline_reason, validated, status, days_tracked, "
                        f"first_seen, price_trend) "
                        f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P}) "
                        f"ON CONFLICT (ticker, scan_date) DO UPDATE SET "
                        f"price=EXCLUDED.price, rsi_14=EXCLUDED.rsi_14, ai_verdict=EXCLUDED.ai_verdict, "
                        f"ai_confidence=EXCLUDED.ai_confidence, ai_summary=EXCLUDED.ai_summary, "
                        f"status=EXCLUDED.status, days_tracked=EXCLUDED.days_tracked, price_trend=EXCLUDED.price_trend",
                        (ticker, today, price, rsi, v.get("pct_change_1mo"),
                         v.get("market_cap"), v.get("sector"), v.get("name"),
                         verdict, confidence, v.get("summary", ""),
                         v.get("bottom_signal_strength"), v.get("decline_reason"),
                         v.get("validated", False), status, days_tracked, first_seen, price_trend),
                    )
                else:
                    execute(
                        f"INSERT OR REPLACE INTO oversold_daily (ticker, scan_date, price, rsi_14, "
                        f"pct_change_1mo, market_cap, sector, name, ai_verdict, ai_confidence, "
                        f"ai_summary, bottom_signal_strength, decline_reason, validated, status, "
                        f"days_tracked, first_seen, price_trend) "
                        f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P})",
                        (ticker, today, price, rsi, v.get("pct_change_1mo"),
                         v.get("market_cap"), v.get("sector"), v.get("name"),
                         verdict, confidence, v.get("summary", ""),
                         v.get("bottom_signal_strength"), v.get("decline_reason"),
                         v.get("validated", False), status, days_tracked, first_seen, price_trend),
                    )
            except Exception as e:
                log.warning(f"[OVERSOLD-BG] Failed to store {ticker}: {e}")

        # Also store in screener_results for history UI
        all_for_cache = []
        for v in vetted:
            all_for_cache.append(v)
        _store_screener_results("oversold", all_for_cache)

        log.info(f"[OVERSOLD-BG] Scan complete — {len(vetted)} tickers stored")
    except Exception as e:
        log.error(f"[OVERSOLD-BG] Scan failed: {e}")


# Track if a background scan is already running
_oversold_scan_running = False


def _run_oversold_pipeline(limit: int = 20, sectors: list = None) -> dict:
    """Oversold pipeline — always serves from DB, triggers background scan if needed.

    Never blocks the web request with heavy yfinance downloads.
    - If today's data exists in DB → serve immediately
    - If not → kick off background thread, serve most recent available data
    """
    import logging
    import threading
    log = logging.getLogger(__name__)

    try:
        from db import query, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
    except ImportError:
        return {"candidates_scanned": 0, "opportunities": [], "risky": [],
                "tracking": [], "avoided": 0, "category": "oversold",
                "timestamp": datetime.now().isoformat(), "error": "DB not available"}

    today = datetime.now().strftime("%Y-%m-%d")

    # ── Try today's data first ──
    cached = []
    try:
        cached = query(
            f"SELECT * FROM oversold_daily WHERE scan_date = {P} ORDER BY rsi_14 ASC",
            (today,),
        )
    except Exception:
        pass

    # ── If no today data, try most recent available (any date) ──
    scan_label = today
    if not cached:
        try:
            cached = query(
                "SELECT * FROM oversold_daily WHERE scan_date = "
                f"(SELECT MAX(scan_date) FROM oversold_daily) ORDER BY rsi_14 ASC",
                (),
            )
            if cached:
                scan_label = cached[0].get("scan_date", "unknown")
                log.info(f"[OVERSOLD] No today data, serving from {scan_label} ({len(cached)} tickers)")
        except Exception:
            pass

    # ── Trigger background scan if no today data ──
    global _oversold_scan_running
    if scan_label != today and not _oversold_scan_running:
        _oversold_scan_running = True
        def _bg():
            global _oversold_scan_running
            try:
                _oversold_background_scan(limit=limit)
            finally:
                _oversold_scan_running = False
        t = threading.Thread(target=_bg, daemon=True)
        t.start()
        log.info("[OVERSOLD] Background scan triggered")

    # ── Build response from DB data ──
    if not cached:
        scanning_msg = "Oversold scan is running in the background. Results will appear shortly — refresh in 2-3 minutes." if _oversold_scan_running else "No oversold data yet. Scan runs daily at 10 AM EST."
        return {
            "candidates_scanned": 0, "opportunities": [], "risky": [],
            "tracking": [], "avoided": 0, "category": "oversold",
            "timestamp": datetime.now().isoformat(),
            "error": scanning_msg, "scan_running": _oversold_scan_running,
        }

    all_results = []
    for r in cached:
        all_results.append({
            "ticker": r["ticker"], "price": r["price"], "rsi_14": r["rsi_14"],
            "pct_change_1mo": r["pct_change_1mo"], "market_cap": r["market_cap"],
            "sector": r.get("sector", "Unknown"), "name": r.get("name", r["ticker"]),
            "verdict": r.get("ai_verdict", "WATCH"), "confidence": r.get("ai_confidence", 50),
            "summary": r.get("ai_summary", ""), "bottom_signal_strength": r.get("bottom_signal_strength"),
            "decline_reason": r.get("decline_reason"), "validated": r.get("validated", False),
            "status": r.get("status", "tracking"), "days_tracked": r.get("days_tracked", 1),
            "first_seen": r.get("first_seen", today), "price_trend": r.get("price_trend"),
        })

    if sectors:
        all_results = [r for r in all_results if r.get("sector", "") in sectors]

    # Money flow from Alpaca (cached) — oversold_daily doesn't persist it, so
    # attach on read; feeds both this response and the screener_results store.
    _apply_alpaca_money_flow(all_results)

    # Ensure in screener_results for past scans UI
    _store_screener_results("oversold", all_results)

    # Split into categories
    opportunities, tracking, risky = [], [], []
    avoided = 0
    for r in all_results:
        if r.get("status") == "consolidating" and r.get("verdict") in ("BOTTOM FORMING", "WATCH"):
            opportunities.append(r)
        elif r.get("verdict") == "FALLING KNIFE":
            avoided += 1
        elif r.get("status") == "tracking":
            tracking.append(r)
        else:
            risky.append(r)

    return {
        "candidates_scanned": len(all_results),
        "opportunities": opportunities,
        "risky": risky,
        "tracking": tracking,
        "avoided": avoided,
        "category": "oversold",
        "timestamp": datetime.now().isoformat(),
        "scan_date": scan_label,
        "scan_running": _oversold_scan_running,
    }


def _store_screener_results(category, results_list):
    """Store vetted screener results in screener_results table for history + caching."""
    try:
        from db import execute, IS_POSTGRES
        import json as _json
        P = "%s" if IS_POSTGRES else "?"
        today = datetime.now().strftime("%Y-%m-%d")

        for r in results_list:
            ticker = r.get("ticker", "???")
            # Store full result as JSON for future reconstruction
            data_blob = _json.dumps({k: v for k, v in r.items()
                                     if k not in ("ticker", "price", "verdict", "confidence",
                                                   "summary", "sector", "name", "market_cap")},
                                    default=str)
            try:
                if IS_POSTGRES:
                    execute(
                        f"INSERT INTO screener_results (category, scan_date, ticker, price, verdict, "
                        f"confidence, summary, sector, name, market_cap, data_json) "
                        f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P}) "
                        f"ON CONFLICT (category, scan_date, ticker) DO UPDATE SET "
                        f"price=EXCLUDED.price, verdict=EXCLUDED.verdict, confidence=EXCLUDED.confidence, "
                        f"summary=EXCLUDED.summary, data_json=EXCLUDED.data_json",
                        (category, today, ticker, r.get("price"), r.get("verdict"),
                         r.get("confidence"), r.get("summary", ""), r.get("sector"),
                         r.get("name"), r.get("market_cap"), data_blob),
                    )
                else:
                    execute(
                        f"INSERT OR REPLACE INTO screener_results (category, scan_date, ticker, price, "
                        f"verdict, confidence, summary, sector, name, market_cap, data_json) "
                        f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P})",
                        (category, today, ticker, r.get("price"), r.get("verdict"),
                         r.get("confidence"), r.get("summary", ""), r.get("sector"),
                         r.get("name"), r.get("market_cap"), data_blob),
                    )
            except Exception:
                continue
    except Exception:
        pass


def _load_cached_screener(category, sectors=None):
    """Load today's cached screener results from DB. Returns (results, found) tuple."""
    try:
        from db import query, IS_POSTGRES
        import json as _json
        P = "%s" if IS_POSTGRES else "?"
        today = datetime.now().strftime("%Y-%m-%d")

        rows = query(
            f"SELECT * FROM screener_results WHERE category = {P} AND scan_date = {P} ORDER BY confidence DESC",
            (category, today),
        )
        if not rows or len(rows) == 0:
            return [], False

        results = []
        for r in rows:
            entry = {
                "ticker": r["ticker"], "price": r["price"], "verdict": r["verdict"],
                "confidence": r["confidence"], "summary": r.get("summary", ""),
                "sector": r.get("sector"), "name": r.get("name"), "market_cap": r.get("market_cap"),
            }
            # Merge extra data from JSON blob
            if r.get("data_json"):
                try:
                    extra = _json.loads(r["data_json"])
                    entry.update(extra)
                except Exception:
                    pass
            results.append(entry)

        # Filter by sectors if specified
        if sectors:
            results = [r for r in results if r.get("sector", "") in sectors]

        return results, True
    except Exception:
        return [], False


def run_screener(min_price: float = 2.0, max_price: float = 15.0,
                 limit: int = 20, category: str = "lowcap",
                 sectors: list = None, timeframe: str = "1d") -> dict:
    """
    Full screener pipeline: scan -> parallel AI vet -> categorize -> return.
    Supports categories: lowcap, midcap, largecap, etf, metals_mining, crypto, ai, gainers, losers, oversold.

    Cache-first: checks screener_results DB for today's scan before running AI.
    Results are stored for history tracking and shared across all users.
    """
    if not is_configured():
        return {
            "error": "OpenRouter API key not configured.",
            "candidates_scanned": 0, "opportunities": [], "risky": [],
            "avoided": 0, "timestamp": datetime.now().isoformat(),
        }

    # ── Cache-first: serve today's results from DB if available ──
    # Gainers/losers with different timeframes get a composite cache key
    cache_key = f"{category}_{timeframe}" if category in ("gainers", "losers") else category
    cached_results, cache_hit = _load_cached_screener(cache_key, sectors)
    if cache_hit and cached_results:
        # Reconstruct the categorized response from cached data
        positive_map = {
            "lowcap": ["OPPORTUNITY"], "midcap": ["OPPORTUNITY"], "largecap": ["STRONG GROWTH"],
            "etf": ["STRONG BUY"], "metals_mining": ["OPPORTUNITY"], "crypto": ["BULLISH"],
            "ai": ["OPPORTUNITY"], "gainers_1d": ["MOMENTUM BUY"], "gainers_1w": ["MOMENTUM BUY"],
            "gainers_3mo": ["MOMENTUM BUY"], "losers_1d": ["RECOVERY BUY"],
            "losers_1w": ["RECOVERY BUY"], "losers_3mo": ["RECOVERY BUY"],
        }
        cautious_map = {
            "lowcap": ["RISKY"], "midcap": ["RISKY"], "largecap": ["STEADY"],
            "etf": ["ACCUMULATE"], "metals_mining": ["RISKY"], "crypto": ["NEUTRAL"],
            "ai": ["RISKY"], "gainers_1d": ["WATCH"], "gainers_1w": ["WATCH"],
            "gainers_3mo": ["WATCH"], "losers_1d": ["WATCH"],
            "losers_1w": ["WATCH"], "losers_3mo": ["WATCH"],
        }
        pos = positive_map.get(cache_key, ["OPPORTUNITY"])
        caut = cautious_map.get(cache_key, ["RISKY"])
        opps = [r for r in cached_results if r.get("verdict") in pos]
        risky = [r for r in cached_results if r.get("verdict") in caut]
        return {
            "candidates_scanned": len(cached_results),
            "opportunities": opps,
            "risky": risky,
            "avoided": len(cached_results) - len(opps) - len(risky),
            "category": category,
            "timestamp": datetime.now().isoformat(),
            "from_cache": True,
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
    elif category == "oversold":
        # Oversold uses a dedicated pipeline with daily tracking + consolidation detection
        return _run_oversold_pipeline(limit=limit, sectors=sectors)
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

    # Money flow: prefer Alpaca (reliable, not IP-throttled like Yahoo). Override
    # the yfinance-derived values from scanning where Alpaca has a signal; leave
    # the yfinance fallback in place for symbols Alpaca can't serve / unconfigured.
    _apply_alpaca_money_flow(candidates)

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

    # ── Store results for history tracking (shared across all users) ──
    all_vetted = result["opportunities"] + result["risky"]
    store_key = f"{category}_{timeframe}" if category in ("gainers", "losers") else category
    _store_screener_results(store_key, all_vetted)

    return {
        "candidates_scanned": len(candidates),
        "opportunities": result["opportunities"],
        "risky": result["risky"],
        "avoided": result["avoided"],
        "category": category,
        "timestamp": datetime.now().isoformat(),
    }
