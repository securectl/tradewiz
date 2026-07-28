"""Sector-level options money flow — which sectors are seeing call buying
(money flowing in) vs put buying / selling.

For each of the 11 SPDR sector ETFs we read the premium-weighted call vs put
options flow and classify the sector as MONEY IN (net call premium), SELLING
(net put premium), or NEUTRAL. Powers the Smart Money "Sector Flow" sub-tab and
a dashboard card.

Data source mirrors the rest of the options stack: try the Webull/Alpaca seams
(calls+puts), fall back to the yfinance premium-weighted reader
(features.watchdog.options_flow._fetch_options_flow) which already computes
call/put dollar premium for any symbol. Today the vendor seams are unavailable
so this runs on yfinance.

Computing 11 ETF chains is slow (seconds), so the result is cached for 30 min
and refreshed on a background thread — the request never blocks on the fetch
(important on the single-worker prod setup). The first call returns
``computing: True`` with an empty board; the frontend re-fetches shortly after.
"""

import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# SPDR sector ETF -> sector name
SECTOR_ETFS = [
    ("XLK", "Technology"),
    ("XLF", "Financials"),
    ("XLE", "Energy"),
    ("XLV", "Healthcare"),
    ("XLI", "Industrials"),
    ("XLY", "Consumer Discretionary"),
    ("XLP", "Consumer Staples"),
    ("XLU", "Utilities"),
    ("XLB", "Materials"),
    ("XLRE", "Real Estate"),
    ("XLC", "Communication Services"),
]

# Net call-vs-put dollar-premium thresholds for the flow signal
NET_IN = 250_000
NET_OUT = -250_000

_TTL = 1800  # 30 min
_cache = {"data": None, "ts": 0.0}
_lock = threading.Lock()
_refreshing = False


def _classify(net_premium, pc_ratio=None):
    """Sector flow signal from **net dollar premium** (call$ − put$) — where the
    money actually flows. Kept coherent on a single axis: we do NOT let the
    volume-based put/call ratio flip the signal (call$ > put$ with a high P/C
    can both be true — lots of cheap puts vs fewer expensive calls — and mixing
    the two produced contradictory labels). P/C is surfaced separately as
    context, not as a trigger. This matters because the read drives buy/sell."""
    if net_premium >= NET_IN:
        return "MONEY IN", "#00c896"
    if net_premium <= NET_OUT:
        return "SELLING", "#ff4757"
    return "NEUTRAL", "#ffc837"


def _fetch_sector(etf):
    """One sector's options flow via the seam→yfinance path. None on failure."""
    # Vendor seams first (calls+puts); both return None today so we fall back.
    try:
        from features.watchdog.options_flow import _fetch_options_flow
        return _fetch_options_flow(etf)
    except Exception as e:
        logger.debug(f"sector flow fetch failed for {etf}: {e}")
        return None


def _compute():
    sectors = []
    for etf, name in SECTOR_ETFS:
        of = _fetch_sector(etf)
        if not of:
            continue
        net = float(of.get("net_premium") or 0)
        pc = of.get("pc_ratio")
        signal, color = _classify(net)
        # Honesty flag: dollar flow says one thing but volume P/C says the other.
        divergent = bool(pc is not None and (
            (signal == "MONEY IN" and pc > 1.5) or (signal == "SELLING" and pc < 0.67)))
        sectors.append({
            "sector": name, "etf": etf,
            "call_value": float(of.get("call_value") or 0),
            "put_value": float(of.get("put_value") or 0),
            "net_premium": round(net, 0),
            "pc_ratio": pc,
            "pc_divergent": divergent,
            "call_volume": int(of.get("call_volume") or 0),
            "put_volume": int(of.get("put_volume") or 0),
            "sentiment": of.get("sentiment"),
            "flow_signal": signal,
            "color": color,
        })

    money_in = sorted([s for s in sectors if s["flow_signal"] == "MONEY IN"],
                      key=lambda s: -s["net_premium"])
    selling = sorted([s for s in sectors if s["flow_signal"] == "SELLING"],
                     key=lambda s: s["net_premium"])
    total_net = sum(s["net_premium"] for s in sectors)
    tilt = "RISK-ON" if total_net > 0 else "RISK-OFF" if total_net < 0 else "NEUTRAL"
    return {
        "sectors": sorted(sectors, key=lambda s: -s["net_premium"]),
        "money_in": money_in,
        "selling": selling,
        "tilt": tilt,
        "total_net_premium": round(total_net, 0),
        "top_inflow": money_in[0]["sector"] if money_in else None,
        "top_outflow": selling[0]["sector"] if selling else None,
        "count": len(sectors),
        "source": "yfinance",
        "timestamp": datetime.utcnow().isoformat(),
    }


def _refresh():
    global _refreshing
    try:
        data = _compute()
        with _lock:
            _cache["data"] = data
            _cache["ts"] = time.time()
        logger.info(f"[sector-flow] refreshed {data['count']} sectors, tilt={data['tilt']}")
    except Exception as e:
        logger.warning(f"[sector-flow] refresh failed: {e}")
    finally:
        _refreshing = False


def get_sector_options_flow(force_refresh=False):
    """Cached sector options-flow board. Never blocks on the fetch: if the cache
    is stale/missing it kicks a background refresh and returns the stale board
    (or ``computing: True`` with an empty board on the very first call)."""
    global _refreshing
    now = time.time()
    with _lock:
        data, ts = _cache["data"], _cache["ts"]
    fresh = data is not None and (now - ts) < _TTL

    if fresh and not force_refresh:
        return {**data, "cached": True, "computing": False}

    start = False
    with _lock:
        if not _refreshing:
            _refreshing = True
            start = True
    if start:
        threading.Thread(target=_refresh, daemon=True, name="sector-flow").start()

    if data is not None:
        return {**data, "cached": True, "stale": True, "computing": True}
    return {"sectors": [], "money_in": [], "selling": [], "tilt": "—",
            "count": 0, "computing": True, "timestamp": datetime.utcnow().isoformat()}
