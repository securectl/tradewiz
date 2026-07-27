"""Earnings-calendar engine — a weekly "most anticipated reports" board.

Backs the Earnings Calendar tab (design modelled on the DominantX "Earnings
Whispers" board). For the requested week we lay out every name in a large/mid-cap
universe that reports earnings Mon–Fri, grouped by **session** (Before Open /
After Close / TBD) and annotated with three signals computed live from price:

* **RRG quadrant** — relative-rotation vs SPY: Leading (LE) / Weakening (WE) /
  Improving (IM) / Lagging (LAG). Computed from the stock/SPY price *ratio*:
  its level vs a 50-day mean (RS-Ratio proxy) and its short vs medium mean
  (RS-Momentum proxy). This is the LE/WE/IM/LAG badge in the design.
* **Near highs** — price within ``NEAR_HIGH_PCT`` of its 52-week high (green dot).
* **DX Score (0–100)** — a composite momentum / relative-strength rank, min-max
  scaled across the week's reporters so the board sorts strongest-first.

Data sources (all best-effort, graceful-degradation):
* Prices: one batched ``yfinance.download`` for the whole universe + SPY (6mo
  daily). One network round-trip, not one-per-symbol.
* Earnings dates + session + company name: per-ticker ``yfinance`` fan-out
  (``ThreadPoolExecutor``), the same idiom as ``alerts.find_upcoming_earnings``.

The fully-assembled weekly payload is memoized in-process (TTL + lock, the
``options_calls`` idiom) and mirrored to ``earnings_calendar_snapshots`` as a
JSON blob so a container restart serves the last board instantly instead of
re-crunching the universe on the first request.
"""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────
NEAR_HIGH_PCT = 0.03        # within 3% of the 52-week high => "near highs"
HIST_PERIOD = "6mo"         # price history pulled for RRG + momentum
MOMENTUM_DAYS = 63          # ~3 trading months for the momentum leg of DX score
RS_LONG = 50                # RS-Ratio lookback (ratio vs its 50d mean)
RS_MOM_FAST = 10            # RS-Momentum fast mean
RS_MOM_SLOW = 30            # RS-Momentum slow mean
BENCH = "SPY"               # relative-strength benchmark
MAX_WORKERS = 12
EARN_TIMEOUT = 45           # seconds to wait on the earnings-date fan-out

_CACHE = {}                 # cache_key -> (payload, ts)
_CACHE_TTL = 3 * 3600       # 3h for a good board
_CACHE_MISS_TTL = 300       # retry a failed/empty board sooner
_LOCK = threading.Lock()
_NAME_CACHE = {}            # symbol -> company name (persists for process life)

# RRG quadrant -> (short badge, full label, order for "strength" sort tiebreak)
QUADRANTS = {
    "LE": ("LE", "Leading"),
    "WE": ("WE", "Weakening"),
    "IM": ("IM", "Improving"),
    "LAG": ("LAG", "Lagging"),
}
_SESSION_LABELS = {"bmo": "Before Open", "amc": "After Close", "tbd": "Session TBD"}
_DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


# ── Universe ────────────────────────────────────────────────────────────
def _universe(wide=False):
    """De-duplicated large-cap (default) or large+mid-cap (``wide``) universe."""
    try:
        from screener import LARGECAP_TICKERS, MIDCAP_TICKERS
        base = list(LARGECAP_TICKERS)
        if wide:
            base += list(MIDCAP_TICKERS)
    except Exception as e:
        logger.warning("[earnings] universe import failed: %s", e)
        base = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]
    seen, out = set(), []
    for t in base:
        t = str(t).strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ── Week window ─────────────────────────────────────────────────────────
def _week_window(week_offset=0, today=None):
    """Monday..Friday of the requested week. ``week_offset`` shifts by weeks."""
    today = today or datetime.now().date()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    friday = monday + timedelta(days=4)
    return monday, friday


# ── Earnings date + session + name (per-ticker, threaded) ───────────────
def _session_from_ts(ts):
    """Best-effort BMO/AMC/TBD from an earnings timestamp's hour."""
    try:
        hour = ts.hour
        minute = getattr(ts, "minute", 0)
        if hour == 0 and minute == 0:
            return "tbd"          # midnight => date-only, no session encoded
        if hour < 12:
            return "bmo"
        if hour >= 15:
            return "amc"
        return "tbd"
    except Exception:
        return "tbd"


def _fetch_earnings_meta(symbol):
    """(symbol, date, session, name) for the next report, or (symbol, None, ...).

    Best-effort: tries ``get_earnings_dates`` (carries a timestamp we can read a
    session from) then falls back to ``.calendar`` (date only => TBD). Company
    name is cached across calls."""
    import yfinance as yf
    tk = yf.Ticker(symbol)
    ed, session = None, "tbd"
    # Preferred: earnings_dates carries a tz-aware timestamp (session hint).
    try:
        df = tk.get_earnings_dates(limit=12)
        if df is not None and not df.empty:
            today = datetime.now().date()
            for idx in df.index:
                try:
                    d = idx.date()
                except Exception:
                    continue
                if d >= today:
                    ed, session = d, _session_from_ts(idx)
                    break
    except Exception:
        pass
    # Fallback: .calendar (date only).
    if ed is None:
        try:
            cal = tk.calendar
            raw = None
            if isinstance(cal, dict):
                v = cal.get("Earnings Date")
                raw = v[0] if isinstance(v, (list, tuple)) and v else v
            else:
                try:
                    raw = cal.loc["Earnings Date"][0]
                except Exception:
                    raw = None
            if raw is not None:
                ed = raw.date() if hasattr(raw, "date") else raw
                session = "tbd"
        except Exception:
            pass
    # Company name (cached).
    name = _NAME_CACHE.get(symbol)
    if name is None:
        try:
            info = getattr(tk, "info", {}) or {}
            name = info.get("shortName") or info.get("longName") or symbol
        except Exception:
            name = symbol
        _NAME_CACHE[symbol] = name
    return symbol, ed, session, name


def _gather_earnings(universe, monday, friday):
    """{symbol: (date, session, name)} for names reporting Mon..Fri this week."""
    out = {}
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(_fetch_earnings_meta, t): t for t in universe}
            try:
                for f in as_completed(futs, timeout=EARN_TIMEOUT):
                    try:
                        sym, d, session, name = f.result()
                    except Exception:
                        continue
                    if d and monday <= d <= friday:
                        out[sym] = (d, session, name)
            except Exception:
                logger.info("[earnings] earnings fan-out timed out; using partial")
    except Exception as e:
        logger.warning("[earnings] earnings fan-out failed: %s", e)
    return out


# ── Price signals (batched) ─────────────────────────────────────────────
def _download_prices(symbols):
    """Batched daily-close frame {symbol: close_series} for symbols + benchmark.

    One ``yf.download`` for the whole set. Returns {} on failure so the board
    still renders (badges/scores just fall back to neutral)."""
    try:
        import yfinance as yf
        tickers = list(dict.fromkeys(list(symbols) + [BENCH]))
        data = yf.download(tickers, period=HIST_PERIOD, interval="1d",
                           auto_adjust=True, progress=False, threads=True,
                           group_by="ticker")
        closes = {}
        if data is None or len(data) == 0:
            return {}
        # Multi-ticker => columns are a (ticker, field) MultiIndex; single => flat.
        for t in tickers:
            try:
                if hasattr(data.columns, "levels") and t in data.columns.get_level_values(0):
                    s = data[t]["Close"].dropna()
                elif "Close" in getattr(data, "columns", []):
                    s = data["Close"].dropna()
                else:
                    continue
                if len(s) > 0:
                    closes[t] = s
            except Exception:
                continue
        return closes
    except Exception as e:
        logger.warning("[earnings] price download failed: %s", e)
        return {}


def _rrg_quadrant(stock, bench):
    """RRG quadrant + (rs_ratio, rs_mom) from the stock/benchmark price ratio."""
    try:
        df = stock.to_frame("s").join(bench.to_frame("b"), how="inner").dropna()
        if len(df) < RS_LONG + 2:
            return "LAG", 100.0, 100.0
        ratio = df["s"] / df["b"]
        sma_long = ratio.rolling(RS_LONG).mean().iloc[-1]
        rs_ratio = 100.0 * ratio.iloc[-1] / sma_long if sma_long else 100.0
        fast = ratio.rolling(RS_MOM_FAST).mean().iloc[-1]
        slow = ratio.rolling(RS_MOM_SLOW).mean().iloc[-1]
        rs_mom = 100.0 * fast / slow if slow else 100.0
        if rs_ratio >= 100 and rs_mom >= 100:
            q = "LE"
        elif rs_ratio >= 100 and rs_mom < 100:
            q = "WE"
        elif rs_ratio < 100 and rs_mom >= 100:
            q = "IM"
        else:
            q = "LAG"
        return q, round(rs_ratio, 2), round(rs_mom, 2)
    except Exception:
        return "LAG", 100.0, 100.0


def _price_signals(symbol, closes):
    """Per-symbol dict of price-derived signals, or None if no usable history."""
    s = closes.get(symbol)
    bench = closes.get(BENCH)
    if s is None or len(s) < 30 or bench is None:
        return None
    price = float(s.iloc[-1])
    hi_52 = float(s.tail(252).max())
    near_high = bool(hi_52 > 0 and price >= (1 - NEAR_HIGH_PCT) * hi_52)
    proximity = price / hi_52 if hi_52 else 0.0          # 0..1, higher = nearer high
    # 3-month momentum, and relative strength vs benchmark over the same window.
    def _ret(series):
        n = min(MOMENTUM_DAYS, len(series) - 1)
        base = float(series.iloc[-1 - n])
        cur = float(series.iloc[-1])
        return (cur / base - 1.0) if base else 0.0

    mom = _ret(s)
    bench_mom = _ret(bench)
    rs_vs_bench = mom - bench_mom
    quadrant, rs_ratio, rs_mom = _rrg_quadrant(s, bench)
    # Raw composite for the DX score (scaled to 0..100 later, across the board).
    raw = 0.45 * rs_vs_bench + 0.30 * mom + 0.25 * (proximity - 1.0)
    return {
        "price": round(price, 2),
        "near_highs": near_high,
        "quadrant": quadrant,
        "rs_ratio": rs_ratio,
        "rs_mom": rs_mom,
        "_raw": raw,
    }


def _scale_scores(rows):
    """Min-max scale each row's ``_raw`` to a 0..100 integer DX score in place."""
    raws = [r["_raw"] for r in rows if r.get("_raw") is not None]
    if not raws:
        for r in rows:
            r["score"] = 50
        return
    lo, hi = min(raws), max(raws)
    span = hi - lo
    for r in rows:
        raw = r.get("_raw")
        if raw is None or span <= 0:
            r["score"] = 50
        else:
            r["score"] = int(round(100 * (raw - lo) / span))
        r.pop("_raw", None)


# ── Assembly ────────────────────────────────────────────────────────────
def _build_week(week_offset=0, wide=False, today=None):
    """Crunch the whole board for the requested week (no cache)."""
    monday, friday = _week_window(week_offset, today=today)
    universe = _universe(wide=wide)
    earnings = _gather_earnings(universe, monday, friday)      # slow leg
    reporters = list(earnings.keys())

    closes = _download_prices(reporters) if reporters else {}

    rows = []
    for sym in reporters:
        d, session, name = earnings[sym]
        sig = _price_signals(sym, closes) or {
            "price": 0.0, "near_highs": False, "quadrant": "LAG",
            "rs_ratio": 100.0, "rs_mom": 100.0, "_raw": None,
        }
        badge, label = QUADRANTS.get(sig["quadrant"], ("LAG", "Lagging"))
        rows.append({
            "symbol": sym,
            "name": name or sym,
            "date": d.isoformat(),
            "dow_idx": d.weekday(),
            "session": session,
            "price": sig["price"],
            "near_highs": sig["near_highs"],
            "quadrant": badge,
            "quadrant_label": label,
            "rs_ratio": sig["rs_ratio"],
            "rs_mom": sig["rs_mom"],
            "_raw": sig.get("_raw"),
        })
    _scale_scores(rows)

    # Group into 5 day columns, each split by session, sorted strongest-first.
    days = []
    counts = {"LE": 0, "WE": 0, "IM": 0, "LAG": 0, "near_highs": 0, "total": len(rows)}
    for r in rows:
        counts[r["quadrant"]] = counts.get(r["quadrant"], 0) + 1
        if r["near_highs"]:
            counts["near_highs"] += 1
    for i in range(5):
        day = monday + timedelta(days=i)
        day_rows = [r for r in rows if r["dow_idx"] == i]
        sessions = {}
        for key in ("bmo", "amc", "tbd"):
            bucket = sorted([r for r in day_rows if r["session"] == key],
                            key=lambda r: -r["score"])
            sessions[key] = {"label": _SESSION_LABELS[key], "rows": bucket}
        days.append({
            "date": day.isoformat(),
            "dow": _DOW[i],
            "label": f"{_DOW[i]} {day.day}",
            "count": len(day_rows),
            "sessions": sessions,
        })

    return {
        "week_start": monday.isoformat(),
        "week_end": friday.isoformat(),
        "week_label": f"Week of {monday.strftime('%b %-d, %Y')}",
        "days": days,
        "counts": counts,
        "wide": wide,
        "generated_at": datetime.utcnow().isoformat(),
        "legend": [
            {"key": "LE", "label": "Leading"},
            {"key": "WE", "label": "Weakening"},
            {"key": "IM", "label": "Improving"},
            {"key": "LAG", "label": "Lagging"},
        ],
        "note": ("RRG quadrant is relative rotation vs SPY; DX Score is a "
                 "momentum / relative-strength composite scaled 0–100 across "
                 "this week's reporters. Sessions (Before Open / After Close) "
                 "are best-effort from yfinance and default to TBD."),
    }


# ── Persistence (best-effort snapshot mirror) ───────────────────────────
def _save_snapshot(week_start, payload):
    try:
        from db import execute, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        execute(
            f"INSERT INTO earnings_calendar_snapshots (week_start, payload, updated_at) "
            f"VALUES ({P},{P},{P}) "
            f"ON CONFLICT(week_start) DO UPDATE SET "
            f"payload=excluded.payload, updated_at=excluded.updated_at",
            (week_start, json.dumps(payload), datetime.utcnow().isoformat()),
        )
    except Exception as e:
        logger.debug("[earnings] snapshot save skipped: %s", e)


def _load_snapshot(week_start):
    try:
        from db import query_one, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        row = query_one(
            f"SELECT payload FROM earnings_calendar_snapshots WHERE week_start={P}",
            (week_start,),
        )
        if row and row.get("payload"):
            data = json.loads(row["payload"])
            data["from_snapshot"] = True
            return data
    except Exception as e:
        logger.debug("[earnings] snapshot load skipped: %s", e)
    return None


# ── Public entry point ──────────────────────────────────────────────────
def get_earnings_week(week_offset=0, wide=False, force_refresh=False):
    """Cached weekly earnings board for ``week_offset`` (0 = current week)."""
    try:
        week_offset = int(week_offset)
    except Exception:
        week_offset = 0
    wide = bool(wide)
    key = (week_offset, wide)
    now = time.time()

    if not force_refresh:
        with _LOCK:
            hit = _CACHE.get(key)
        if hit:
            payload, ts = hit
            good = payload and payload.get("counts", {}).get("total", 0) > 0
            ttl = _CACHE_TTL if good else _CACHE_MISS_TTL
            if now - ts < ttl:
                return payload

    payload = _build_week(week_offset=week_offset, wide=wide)

    # If we crunched an empty board, fall back to the last good snapshot.
    if payload.get("counts", {}).get("total", 0) == 0:
        monday, _ = _week_window(week_offset)
        snap = _load_snapshot(monday.isoformat())
        if snap:
            payload = snap
    else:
        _save_snapshot(payload["week_start"], payload)

    with _LOCK:
        _CACHE[key] = (payload, now)
    return payload
