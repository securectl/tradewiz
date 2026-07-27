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
from datetime import datetime, timedelta

RETENTION_DAYS = 30   # how long the daily snapshots are kept

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


def _fetch_chain_yf(symbol):
    """Pull nearest-2-expiration CALL **and PUT** chains from yfinance, including
    bid/ask so trade direction can be estimated.

    Returns (price, [calls], [puts]); each contract dict carries strike, expiry,
    volume, open_interest, last, bid, ask. (0.0, [], []) on failure."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return 0.0, [], []
        try:
            info = ticker.fast_info
            price = float(info.get("lastPrice", 0) or info.get("previousClose", 0) or 0)
        except Exception:
            price = 0.0
        calls, puts = [], []
        for exp_date in expirations[:2]:
            try:
                chain = ticker.option_chain(exp_date)
                for df, bucket in ((chain.calls, calls), (chain.puts, puts)):
                    if df is None or df.empty:
                        continue
                    df = df.copy()
                    df["volume"] = df["volume"].fillna(0).astype(int)
                    df["openInterest"] = df["openInterest"].fillna(0).astype(int)
                    for _, r in df.iterrows():
                        bucket.append({
                            "strike": float(r["strike"]),
                            "expiry": exp_date,
                            "volume": int(r["volume"]),
                            "open_interest": int(r["openInterest"]),
                            "last": round(float(r.get("lastPrice") or 0), 2),
                            "bid": round(float(r.get("bid") or 0), 2),
                            "ask": round(float(r.get("ask") or 0), 2),
                        })
            except Exception as e:
                logger.debug(f"yf chain failed for {symbol} {exp_date}: {e}")
        return price, calls, puts
    except Exception as e:
        logger.warning(f"yfinance chain fetch failed for {symbol}: {e}")
        return 0.0, [], []


# ── Directional flow (Long/Naked × Calls/Puts) ──────────────────────────
# The options chain can't tell us if a print was bought or sold, so we estimate
# the aggressor from where the last trade sits in the bid/ask spread (the
# standard options-flow heuristic): a fill near the ASK was lifted (bought,
# "long"); near the BID it was hit (sold/written, "naked"). It's an ESTIMATE.

FLOW_TOP = 3              # ranked rows kept per bucket (#1 / #2 / #3)
_FLOW_EDGE = 0.30         # how far past mid (fraction of half-spread) to call a side
_FLOW_CACHE = {}          # symbol -> (result, ts)


def _moneyness_for(strike, price, is_call):
    """ITM / ATM / OTM relative to spot, for a call or a put. ATM within ±1.5%."""
    if not price or price <= 0:
        return "—"
    diff = (strike - price) / price
    if abs(diff) <= 0.015:
        return "ATM"
    if is_call:
        return "ITM" if strike < price else "OTM"
    return "ITM" if strike > price else "OTM"


def _direction(last, bid, ask):
    """Estimate aggressor side from the print's position in the spread.

    Returns (side, confidence) with side in {'buy', 'sell', 'neutral'} and
    confidence in [0, 1]. 'neutral' when the quote is missing/crossed or the
    print sits mid-market (no conviction either way)."""
    last = float(last or 0)
    bid = float(bid or 0)
    ask = float(ask or 0)
    if last <= 0 or bid <= 0 or ask <= 0 or ask < bid:
        return "neutral", 0.0
    half = (ask - bid) / 2.0
    if half <= 0:
        return "neutral", 0.0
    mid = (bid + ask) / 2.0
    pos = (last - mid) / half          # -1 at bid, 0 mid, +1 at ask
    if pos >= _FLOW_EDGE:
        return "buy", round(min(1.0, pos), 2)
    if pos <= -_FLOW_EDGE:
        return "sell", round(min(1.0, -pos), 2)
    return "neutral", 0.0


# bucket -> (display label, directional bias)
_FLOW_BUCKETS = {
    "long_calls":  ("Long Calls", "bullish"),
    "naked_calls": ("Naked Calls", "bearish"),
    "naked_puts":  ("Naked Puts", "bullish"),
    "long_puts":   ("Long Puts", "bearish"),
}


def classify_flow(symbol, price, calls, puts):
    """Bucket every traded, directional contract into long/naked × call/put and
    rank the top ``FLOW_TOP`` of each by volume. Pure (no network)."""
    raw = {k: [] for k in _FLOW_BUCKETS}

    def _enrich(c, is_call):
        vol = int(c.get("volume") or 0)
        if vol < MIN_VOL:
            return None
        side, conf = _direction(c.get("last"), c.get("bid"), c.get("ask"))
        if side == "neutral":
            return None
        strike = round(float(c.get("strike") or 0), 2)
        last = round(float(c.get("last") or 0), 2)
        return {
            "strike": strike,
            "expiry": str(c.get("expiry") or "")[:10],
            "type": "call" if is_call else "put",
            "volume": vol,
            "open_interest": int(c.get("open_interest") or 0),
            "last": last,
            "bid": round(float(c.get("bid") or 0), 2),
            "ask": round(float(c.get("ask") or 0), 2),
            "side": side,
            "confidence": conf,
            "notional": round(vol * last * 100, 0),
            "moneyness": _moneyness_for(strike, price, is_call),
        }, side

    for c in calls:
        r = _enrich(c, True)
        if r:
            row, side = r
            raw["long_calls" if side == "buy" else "naked_calls"].append(row)
    for p in puts:
        r = _enrich(p, False)
        if r:
            row, side = r
            raw["long_puts" if side == "buy" else "naked_puts"].append(row)

    bull = bear = 0.0
    buckets = {}
    for key, (label, bias) in _FLOW_BUCKETS.items():
        ranked = sorted(raw[key], key=lambda x: -x["volume"])[:FLOW_TOP]
        for i, row in enumerate(ranked, 1):
            row["rank"] = i
        notional = sum(x["notional"] for x in raw[key])
        if bias == "bullish":
            bull += notional
        else:
            bear += notional
        buckets[key] = {"label": label, "bias": bias,
                        "total_notional": round(notional, 0),
                        "contract_count": len(raw[key]), "rows": ranked}

    total = bull + bear
    if not total:
        lean = "NO SIGNAL"
    elif bull >= bear * 1.2:
        lean = "BULLISH"
    elif bear >= bull * 1.2:
        lean = "BEARISH"
    else:
        lean = "MIXED"
    return {
        "symbol": symbol.upper(),
        "price": round(float(price or 0), 2),
        "buckets": buckets,
        "summary": {"bullish_notional": round(bull, 0),
                    "bearish_notional": round(bear, 0), "lean": lean},
        "note": ("Long vs naked is ESTIMATED from where each trade printed in the "
                 "bid/ask spread (near ask = bought/long, near bid = sold/naked). "
                 "Single-leg heuristic — not confirmed buy/sell intent."),
        "timestamp": datetime.utcnow().isoformat(),
    }


def get_options_flow(symbol, force_refresh=False):
    """Cached directional-flow payload (4 ranked buckets) for ``symbol``."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"error": "no symbol"}
    now = time.time()
    if not force_refresh:
        with _LOCK:
            hit = _FLOW_CACHE.get(sym)
        if hit and now - hit[1] < _CACHE_TTL:
            return hit[0]
    price, calls, puts = _fetch_chain_yf(sym)
    if not calls and not puts:
        result = {"error": f"No options chain available for {sym}", "symbol": sym}
    else:
        result = classify_flow(sym, price, calls, puts)
    with _LOCK:
        _FLOW_CACHE[sym] = (result, now)
    return result


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

    # "Most interest" = the single most-actively-traded call (highest volume).
    # Flag it in-place so it renders bold/highlighted wherever it appears, and
    # surface a copy at the top level for the highlight banner.
    top_contract = None
    if enriched:
        top = max(enriched, key=lambda c: c["volume"])
        top["top"] = True
        top_contract = dict(top)

    agg_ratio = (total_vol / total_oi) if total_oi > 0 else 0.0
    if agg_ratio >= ACCUMULATE_RATIO:
        read, color = "ACCUMULATING", "#00c896"
    elif agg_ratio and agg_ratio < DISTRIBUTE_RATIO:
        read, color = "DISTRIBUTING", "#ff4757"
    else:
        read, color = "NEUTRAL", "#ffc837"

    return {
        "top_contract": top_contract,
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


def _record_snapshot(result, source):
    """Upsert one daily snapshot for this symbol and purge rows older than
    RETENTION_DAYS. One row per (symbol, day); intra-day refetches update it.
    Never raises — tracking is best-effort and must not break the request."""
    try:
        from db import execute, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        sym = result["symbol"]
        day = datetime.utcnow().strftime("%Y-%m-%d")
        t = result.get("totals") or {}
        tc = result.get("top_contract") or {}
        now_iso = datetime.utcnow().isoformat()
        execute(
            f"INSERT INTO options_call_snapshots "
            f"(symbol, snap_date, price, call_volume, call_oi, vol_oi_ratio, read, "
            f"top_strike, top_expiry, top_volume, contracts, increasing_count, "
            f"decreasing_count, source, updated_at) "
            f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P}) "
            f"ON CONFLICT(symbol, snap_date) DO UPDATE SET "
            f"price=excluded.price, call_volume=excluded.call_volume, call_oi=excluded.call_oi, "
            f"vol_oi_ratio=excluded.vol_oi_ratio, read=excluded.read, top_strike=excluded.top_strike, "
            f"top_expiry=excluded.top_expiry, top_volume=excluded.top_volume, "
            f"contracts=excluded.contracts, increasing_count=excluded.increasing_count, "
            f"decreasing_count=excluded.decreasing_count, source=excluded.source, "
            f"updated_at=excluded.updated_at",
            (sym, day, result.get("price", 0), t.get("call_volume", 0), t.get("call_oi", 0),
             t.get("vol_oi_ratio", 0), result.get("read"), tc.get("strike"), tc.get("expiry"),
             tc.get("volume", 0), t.get("contracts", 0), t.get("increasing_count", 0),
             t.get("decreasing_count", 0), source, now_iso),
        )
        cutoff = (datetime.utcnow() - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
        execute(
            f"DELETE FROM options_call_snapshots WHERE symbol={P} AND snap_date < {P}",
            (sym, cutoff),
        )
    except Exception as e:
        logger.debug(f"snapshot record skipped for {result.get('symbol')}: {e}")


def get_call_history(symbol, days=RETENTION_DAYS):
    """Return the last ``days`` of daily snapshots for ``symbol`` (oldest first).
    Empty list on any failure so the view degrades gracefully."""
    try:
        from db import query, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        sym = (symbol or "").strip().upper()
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = query(
            f"SELECT snap_date, price, call_volume, call_oi, vol_oi_ratio, read, "
            f"top_strike, top_expiry, top_volume "
            f"FROM options_call_snapshots WHERE symbol={P} AND snap_date >= {P} "
            f"ORDER BY snap_date", (sym, cutoff),
        ) or []
        return [{
            "date": str(r["snap_date"])[:10],
            "price": round(float(r["price"] or 0), 2),
            "call_volume": int(r["call_volume"] or 0),
            "call_oi": int(r["call_oi"] or 0),
            "vol_oi_ratio": round(float(r["vol_oi_ratio"] or 0), 3),
            "read": r.get("read"),
            "top_strike": float(r["top_strike"]) if r.get("top_strike") is not None else None,
            "top_expiry": str(r.get("top_expiry") or "")[:10],
            "top_volume": int(r["top_volume"] or 0),
        } for r in rows]
    except Exception as e:
        logger.debug(f"history fetch failed for {symbol}: {e}")
        return []


# Source priority for the *displayed* numbers; the others cross-validate it.
SOURCE_PRIORITY = ["webull", "alpaca", "yfinance"]


def _has_signal(calls):
    """True if any contract carries real volume or open interest.

    Some feeds (e.g. Alpaca's free/IEX options tier) return the full chain with
    prices but **zero volume and zero OI on every contract**. Such a source is
    useless for both the displayed read and cross-validation, so we drop it —
    otherwise it would win source-priority and the whole view shows "no data"
    even when yfinance has usable numbers."""
    return any((int(c.get("volume") or 0) > 0 or int(c.get("open_interest") or 0) > 0)
               for c in (calls or []))


def _gather_sources(sym):
    """Return {source_name: {price, calls}} for every source with usable data.
    Each adapter is a best-effort seam — failures/unavailability/empty (all-zero
    volume+OI) sources are omitted so cross-validation works with whatever real
    data is present."""
    out = {}
    try:
        from shared.webull_options import fetch_call_chain as _wb
        wb = _wb(sym)
        if wb and _has_signal(wb.get("calls")):
            out["webull"] = {"price": wb.get("price", 0), "calls": wb["calls"]}
    except Exception as e:
        logger.debug(f"Webull source error for {sym}: {e}")
    try:
        from shared.alpaca_options import fetch_call_chain as _al
        al = _al(sym)
        if al and _has_signal(al.get("calls")):
            out["alpaca"] = {"price": al.get("price", 0), "calls": al["calls"]}
        elif al and al.get("calls"):
            logger.debug(f"Alpaca returned {len(al['calls'])} calls for {sym} "
                         f"with no volume/OI — dropping as a display source")
    except Exception as e:
        logger.debug(f"Alpaca source error for {sym}: {e}")
    try:
        price, calls = _fetch_calls_yf(sym)
        if calls:
            out["yfinance"] = {"price": price, "calls": calls}
    except Exception as e:
        logger.debug(f"yfinance source error for {sym}: {e}")
    return out


def _chain_index(calls):
    """Map calls by (strike, expiry) -> {volume, open_interest}."""
    idx = {}
    for c in calls:
        k = (round(float(c.get("strike") or 0), 2), str(c.get("expiry") or "")[:10])
        idx[k] = {"volume": int(c.get("volume") or 0),
                  "open_interest": int(c.get("open_interest") or 0)}
    return idx


def _agree(a, b, field):
    """Do two sources agree on a field? Within 5 absolute or 25% relative.
    Returns None when both are 0 (nothing meaningful to compare)."""
    av, bv = a.get(field, 0), b.get(field, 0)
    if not av and not bv:
        return None
    diff = abs(av - bv)
    if diff <= 5:
        return True
    return (diff / max(av, bv, 1)) <= 0.25


def _cross_validate(primary_name, sources):
    """Compare the primary chain against the other sources contract-by-contract.
    Returns (summary_dict, per_contract_dict keyed by (strike, expiry))."""
    indexes = {name: _chain_index(s["calls"]) for name, s in sources.items()}
    names = list(sources.keys())
    others = [n for n in names if n != primary_name]
    primary_idx = indexes[primary_name]

    vol_cmp = vol_ok = oi_cmp = oi_ok = 0
    per_contract = {}
    for k, pv in primary_idx.items():
        present = [primary_name] + [n for n in others if k in indexes[n]]
        entry = {"sources": present, "divergent": False}
        for n in others:
            ov = indexes[n].get(k)
            if not ov:
                continue
            va = _agree(pv, ov, "volume")
            if va is not None:
                vol_cmp += 1
                if va:
                    vol_ok += 1
                else:
                    entry["divergent"] = True
            oa = _agree(pv, ov, "open_interest")
            if oa is not None:
                oi_cmp += 1
                if oa:
                    oi_ok += 1
                else:
                    entry["divergent"] = True
        per_contract[k] = entry

    def _pct(a, b):
        return round(a / b * 100, 1) if b else None

    if len(names) > 1:
        note = "Cross-checked across " + ", ".join(names)
    else:
        missing = [s for s in ("webull", "alpaca") if s not in names]
        note = (f"Single source ({names[0]}) — "
                f"{'/'.join(missing)} options data unavailable")
    summary = {
        "sources": names,
        "primary": primary_name,
        "multi_source": len(names) > 1,
        "volume_compared": vol_cmp,
        "volume_agreement_pct": _pct(vol_ok, vol_cmp),
        "oi_compared": oi_cmp,
        "oi_agreement_pct": _pct(oi_ok, oi_cmp),
        "divergent_contracts": sum(1 for e in per_contract.values() if e["divergent"]),
        "note": note,
    }
    return summary, per_contract


def _annotate_sources(result, per_contract):
    """Tag each displayed contract (+ top_contract) with which sources reported
    it and whether the sources diverged, so the UI can flag disagreement."""
    for bucket in ("increasing", "decreasing"):
        for c in result.get(bucket, []):
            pc = per_contract.get((round(c["strike"], 2), c["expiry"][:10]))
            if pc:
                c["sources"] = pc["sources"]
                c["divergent"] = pc["divergent"]
    tc = result.get("top_contract")
    if tc:
        pc = per_contract.get((round(tc["strike"], 2), tc["expiry"][:10]))
        if pc:
            tc["sources"] = pc["sources"]
            tc["divergent"] = pc["divergent"]


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

    # Gather every source that responds, pick a primary by priority, then
    # cross-validate the rest against it.
    sources = _gather_sources(sym)
    if not sources:
        result = {"error": f"No options data available for {sym}", "symbol": sym}
    else:
        primary = next(n for n in SOURCE_PRIORITY if n in sources)
        price = sources[primary].get("price") or sources.get("yfinance", {}).get("price", 0)
        result = _classify(sym, price, sources[primary]["calls"])
        result["source"] = primary
        validation, per_contract = _cross_validate(primary, sources)
        result["validation"] = validation
        _annotate_sources(result, per_contract)
        # Persist today's snapshot (daily dedupe) then attach the 30-day trend.
        _record_snapshot(result, primary)
        result["history"] = get_call_history(sym)

    with _LOCK:
        _CACHE[sym] = (result, now)
    return result
