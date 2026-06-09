"""Call-option activity engine — which call contracts are seeing rising vs
falling volume for a given stock.

Signal definition (chosen with the user): per call contract we compare today's
**volume against its open interest** (vol/OI). A high ratio means today's
trading is large relative to the standing position — fresh buying, "increasing"
activity. A low ratio on a contract that still carries meaningful open interest
means the position is stale and interest is waning — "decreasing" activity.
This is a single-snapshot signal, so it needs no history table and works the
moment it's called.

Data source: Webull first (via the ``shared.webull_options`` seam), falling
back to yfinance — the same chain source that already powers
``features/watchdog/options_flow.py``. Results are memoized per symbol with the
module-level TTL+lock idiom used across the app (see ``screener._options_cache``
and ``shared/llm_cache``).
"""

import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# Classification thresholds (vol / open-interest ratio)
SURGE_RATIO = 0.5     # vol/OI >= this -> "increasing" (fresh buying)
STALE_RATIO = 0.15    # vol/OI < this  -> "decreasing" (waning), if OI is meaningful
MIN_OI = 50           # ignore illiquid contracts for the "decreasing" bucket
MIN_VOL = 1           # contract must have traded to count anywhere
TOP_N = 12            # max rows per bucket

# Read thresholds on the aggregate vol/OI
ACCUMULATE_RATIO = 0.40
DISTRIBUTE_RATIO = 0.12

_CACHE = {}                       # symbol -> (result|None, ts)
_CACHE_TTL = 300                  # 5 min for a successful read
_CACHE_MISS_TTL = 60              # retry sooner after a failed/empty read
_LOCK = threading.Lock()


def _moneyness(strike, price):
    """ITM / ATM / OTM for a *call* relative to spot. ATM within ±1.5%."""
    if not price or price <= 0:
        return "—"
    diff = (strike - price) / price
    if abs(diff) <= 0.015:
        return "ATM"
    return "ITM" if strike < price else "OTM"


def _fetch_calls_yf(symbol):
    """Fallback: pull the nearest-2-expiration call chains from yfinance.

    Returns (price, [call dicts]) or (0, []) on failure."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return 0.0, []
        try:
            info = ticker.fast_info
            price = float(info.get("lastPrice", 0) or info.get("previousClose", 0) or 0)
        except Exception:
            price = 0.0
        calls = []
        for exp_date in expirations[:2]:
            try:
                chain = ticker.option_chain(exp_date)
                df = chain.calls
                if df is None or df.empty:
                    continue
                df = df.copy()
                df["volume"] = df["volume"].fillna(0).astype(int)
                df["openInterest"] = df["openInterest"].fillna(0).astype(int)
                for _, r in df.iterrows():
                    calls.append({
                        "strike": float(r["strike"]),
                        "expiry": exp_date,
                        "volume": int(r["volume"]),
                        "open_interest": int(r["openInterest"]),
                        "last": round(float(r.get("lastPrice") or 0), 2),
                    })
            except Exception as e:
                logger.debug(f"yf call chain failed for {symbol} {exp_date}: {e}")
        return price, calls
    except Exception as e:
        logger.warning(f"yfinance call fetch failed for {symbol}: {e}")
        return 0.0, []


def _classify(symbol, price, calls):
    """Pure function: turn a raw call list into the activity payload.

    Split out from fetching so it's unit-testable without any network."""
    total_vol = 0
    total_oi = 0
    enriched = []
    for c in calls:
        vol = int(c.get("volume") or 0)
        oi = int(c.get("open_interest") or 0)
        if vol < MIN_VOL:
            continue
        total_vol += vol
        total_oi += oi
        ratio = (vol / oi) if oi > 0 else float(vol)  # no OI yet => brand-new interest
        enriched.append({
            "strike": round(float(c.get("strike") or 0), 2),
            "expiry": str(c.get("expiry") or "")[:10],
            "volume": vol,
            "open_interest": oi,
            "vol_oi": round(ratio, 3),
            "last": round(float(c.get("last") or 0), 2),
            "moneyness": _moneyness(float(c.get("strike") or 0), price),
        })

    increasing = sorted(
        [c for c in enriched if c["vol_oi"] >= SURGE_RATIO],
        key=lambda c: -c["vol_oi"],
    )[:TOP_N]
    decreasing = sorted(
        [c for c in enriched
         if c["open_interest"] >= MIN_OI and c["vol_oi"] < STALE_RATIO],
        key=lambda c: -c["open_interest"],
    )[:TOP_N]

    agg_ratio = (total_vol / total_oi) if total_oi > 0 else 0.0
    if agg_ratio >= ACCUMULATE_RATIO:
        read, color = "ACCUMULATING", "#00c896"
    elif agg_ratio and agg_ratio < DISTRIBUTE_RATIO:
        read, color = "DISTRIBUTING", "#ff4757"
    else:
        read, color = "NEUTRAL", "#ffc837"

    return {
        "symbol": symbol.upper(),
        "price": round(float(price or 0), 2),
        "totals": {
            "call_volume": total_vol,
            "call_oi": total_oi,
            "vol_oi_ratio": round(agg_ratio, 3),
            "contracts": len(enriched),
            "increasing_count": len(increasing),
            "decreasing_count": len(decreasing),
        },
        "read": read,
        "read_color": color,
        "increasing": increasing,
        "decreasing": decreasing,
        "timestamp": datetime.utcnow().isoformat(),
    }


def get_call_activity(symbol, force_refresh=False):
    """Return the call-activity payload for ``symbol`` (cached).

    Tries Webull (seam) then yfinance. Returns ``{"error": ...}`` when no chain
    is available so the route can surface a clean message. Successful reads are
    cached for ``_CACHE_TTL``; empty/error reads only briefly so they recover."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"error": "no symbol"}

    now = time.time()
    if not force_refresh:
        with _LOCK:
            hit = _CACHE.get(sym)
        if hit:
            res, ts = hit
            ttl = _CACHE_TTL if (res and not res.get("error")) else _CACHE_MISS_TTL
            if now - ts < ttl:
                return res

    source = "yfinance"
    price, calls = 0.0, []
    try:
        from shared.webull_options import fetch_call_chain
        wb = fetch_call_chain(sym)
        if wb and wb.get("calls"):
            source = "webull"
            price, calls = wb.get("price", 0.0), wb["calls"]
    except Exception as e:
        logger.debug(f"Webull seam error for {sym}: {e}")

    if not calls:
        price, calls = _fetch_calls_yf(sym)

    if not calls:
        result = {"error": f"No options data available for {sym}", "symbol": sym}
    else:
        result = _classify(sym, price, calls)
        result["source"] = source

    with _LOCK:
        _CACHE[sym] = (result, now)
    return result
