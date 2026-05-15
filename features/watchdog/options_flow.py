"""
Options Flow Tracker — Real-time Call vs Put volume monitoring for SPY, QQQ, /NQ.

Uses yfinance options chain data to calculate put/call ratios and detect sentiment shifts.
SSE endpoint streams updates to the frontend in real-time.

Key metrics:
- Call volume, Put volume, Put/Call ratio
- Shift detection: when ratio crosses thresholds or changes direction
- Historical tracking for trend analysis
"""

import json
import logging
import threading
import time
import queue
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Tracked symbols
TRACKED_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA"]
SYMBOL_LABELS = {
    "SPY": "SPY (S&P 500)",
    "QQQ": "QQQ (Nasdaq-100)",
    "IWM": "IWM (Russell 2000)",
    "DIA": "DIA (Dow 30)",
}

# Thresholds for shift detection
BEARISH_THRESHOLD = 1.2    # Put/Call ratio above this = bearish shift
BULLISH_THRESHOLD = 0.7    # Put/Call ratio below this = bullish shift
SHIFT_SENSITIVITY = 0.15   # Minimum ratio change to trigger a shift notification

# In-memory state
_flow_cache = {}        # symbol -> latest flow data
_flow_history = {}      # symbol -> list of recent snapshots (max 100)
_sse_listeners = []     # list of queue.Queue for SSE subscribers
_listener_lock = threading.Lock()
_scan_thread = None
_scan_running = False

# Notification callbacks
_notification_callbacks = []


def _mid_price(row):
    """Bid-ask midpoint per Reddit-post methodology. Falls back to lastPrice
    when the quote is one-sided or stale."""
    try:
        bid = float(row.get("bid") or 0)
        ask = float(row.get("ask") or 0)
        if bid > 0 and ask > 0 and ask >= bid:
            return (bid + ask) / 2.0
        last = float(row.get("lastPrice") or 0)
        return last if last > 0 else 0.0
    except Exception:
        return 0.0


def _fetch_options_flow(symbol):
    """Fetch options chain and compute premium-weighted flow metrics.

    Methodology mirrors the Reddit r/options "calculate call and put prices"
    approach: use bid-ask midpoint (not lastPrice), compute dollar-weighted
    aggregates, surface the top contracts by premium, and bucket dollar volume
    by strike for a distribution view.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return None

        # Get current stock price first — needed for strike distribution range
        try:
            info = ticker.fast_info
            current_price = float(info.get("lastPrice", 0) or info.get("previousClose", 0))
        except Exception:
            current_price = 0

        total_call_volume = 0
        total_put_volume = 0
        total_call_oi = 0
        total_put_oi = 0
        total_call_value = 0.0   # dollar premium = vol × mid × 100
        total_put_value = 0.0
        weighted_call_price_num = 0.0  # Σ (vol × mid)
        weighted_put_price_num = 0.0
        weighted_call_price_den = 0    # Σ vol
        weighted_put_price_den = 0

        # Strike-keyed buckets (only retain strikes within ±10% of spot)
        strike_buckets: dict[float, dict[str, float]] = {}
        contracts_pool: list[dict] = []

        # Use nearest 2 expirations — captures most volume
        for exp_date in expirations[:2]:
            try:
                chain = ticker.option_chain(exp_date)
                for side, df in (("call", chain.calls), ("put", chain.puts)):
                    if df is None or df.empty:
                        continue
                    df = df.copy()
                    df["volume"] = df["volume"].fillna(0).astype(int)
                    df["openInterest"] = df["openInterest"].fillna(0).astype(int)
                    df["mid"] = df.apply(_mid_price, axis=1)
                    df["dollar_vol"] = df["volume"] * df["mid"] * 100.0

                    # Aggregates
                    side_vol = int(df["volume"].sum())
                    side_oi = int(df["openInterest"].sum())
                    side_value = float(df["dollar_vol"].sum())
                    weighted_num = float((df["volume"] * df["mid"]).sum())

                    if side == "call":
                        total_call_volume += side_vol
                        total_call_oi += side_oi
                        total_call_value += side_value
                        weighted_call_price_num += weighted_num
                        weighted_call_price_den += side_vol
                    else:
                        total_put_volume += side_vol
                        total_put_oi += side_oi
                        total_put_value += side_value
                        weighted_put_price_num += weighted_num
                        weighted_put_price_den += side_vol

                    # Strike distribution: only strikes within ±10% of spot
                    if current_price > 0:
                        lo = current_price * 0.90
                        hi = current_price * 1.10
                        near = df[(df["strike"] >= lo) & (df["strike"] <= hi)]
                        for _, r in near.iterrows():
                            k = float(r["strike"])
                            bucket = strike_buckets.setdefault(k, {"call_dollar_vol": 0.0, "put_dollar_vol": 0.0})
                            bucket[f"{side}_dollar_vol"] += float(r["dollar_vol"])

                    # Top contracts pool — keep only rows with non-trivial volume to bound size
                    for _, r in df[df["volume"] > 0].iterrows():
                        contracts_pool.append({
                            "type": side,
                            "strike": float(r["strike"]),
                            "expiry": exp_date,
                            "volume": int(r["volume"]),
                            "oi": int(r["openInterest"]),
                            "mid": round(float(r["mid"]), 2),
                            "dollar_vol": float(r["dollar_vol"]),
                        })
            except Exception as e:
                logger.debug(f"Failed to fetch chain for {symbol} exp {exp_date}: {e}")

        total_volume = total_call_volume + total_put_volume
        pc_ratio = total_put_volume / total_call_volume if total_call_volume > 0 else 1.0
        pc_oi_ratio = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0
        call_pct = total_call_volume / total_volume * 100 if total_volume > 0 else 50
        put_pct = total_put_volume / total_volume * 100 if total_volume > 0 else 50

        avg_call_price = (weighted_call_price_num / weighted_call_price_den) if weighted_call_price_den > 0 else 0.0
        avg_put_price = (weighted_put_price_num / weighted_put_price_den) if weighted_put_price_den > 0 else 0.0
        net_premium = total_call_value - total_put_value

        # Sentiment now considers *premium* lead, not just contract count.
        # Premium override fires when net_premium is ≥10% of total $volume —
        # comparing dollars to dollars, never to raw contract counts.
        total_value = total_call_value + total_put_value
        premium_lean = (net_premium / total_value) if total_value > 0 else 0.0
        if pc_ratio >= BEARISH_THRESHOLD or premium_lean <= -0.10:
            sentiment = "BEARISH"
            sentiment_color = "#ff4757"
        elif pc_ratio <= BULLISH_THRESHOLD or premium_lean >= 0.10:
            sentiment = "BULLISH"
            sentiment_color = "#00c896"
        else:
            sentiment = "NEUTRAL"
            sentiment_color = "#ffc837"

        # Top 10 contracts by dollar premium across both sides
        contracts_pool.sort(key=lambda c: c["dollar_vol"], reverse=True)
        top_contracts = [
            {**c, "dollar_vol": round(c["dollar_vol"], 0)}
            for c in contracts_pool[:10]
        ]

        # Strike distribution sorted ascending; strip empty buckets
        strike_distribution = [
            {
                "strike": round(k, 2),
                "call_dollar_vol": round(v["call_dollar_vol"], 0),
                "put_dollar_vol": round(v["put_dollar_vol"], 0),
            }
            for k, v in sorted(strike_buckets.items())
            if v["call_dollar_vol"] > 0 or v["put_dollar_vol"] > 0
        ]

        return {
            "symbol": symbol,
            "label": SYMBOL_LABELS.get(symbol, symbol),
            "call_volume": total_call_volume,
            "put_volume": total_put_volume,
            "total_volume": total_volume,
            "call_oi": total_call_oi,
            "put_oi": total_put_oi,
            "pc_ratio": round(pc_ratio, 3),
            "pc_oi_ratio": round(pc_oi_ratio, 3),
            "call_pct": round(call_pct, 1),
            "put_pct": round(put_pct, 1),
            "call_value": round(total_call_value, 0),
            "put_value": round(total_put_value, 0),
            "avg_call_price": round(avg_call_price, 2),
            "avg_put_price": round(avg_put_price, 2),
            "net_premium": round(net_premium, 0),
            "top_contracts": top_contracts,
            "strike_distribution": strike_distribution,
            "sentiment": sentiment,
            "sentiment_color": sentiment_color,
            "price": current_price,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.warning(f"Options flow fetch failed for {symbol}: {e}")
        return None


def _detect_shift(symbol, new_data):
    """Detect if there's a meaningful shift in call/put sentiment.

    Returns shift info dict if shift detected, None otherwise.
    """
    history = _flow_history.get(symbol, [])
    if not history or not new_data:
        return None

    prev = history[-1]
    prev_ratio = prev.get("pc_ratio", 1.0)
    new_ratio = new_data.get("pc_ratio", 1.0)
    ratio_change = new_ratio - prev_ratio

    # Check for meaningful shift
    if abs(ratio_change) < SHIFT_SENSITIVITY:
        return None

    # Determine shift type
    if new_ratio >= BEARISH_THRESHOLD and prev_ratio < BEARISH_THRESHOLD:
        shift_type = "BEARISH_SHIFT"
        message = f"{symbol}: Puts overtaking Calls — P/C ratio crossed {BEARISH_THRESHOLD} ({prev_ratio:.2f} → {new_ratio:.2f})"
        severity = "warning"
    elif new_ratio <= BULLISH_THRESHOLD and prev_ratio > BULLISH_THRESHOLD:
        shift_type = "BULLISH_SHIFT"
        message = f"{symbol}: Calls dominating Puts — P/C ratio dropped below {BULLISH_THRESHOLD} ({prev_ratio:.2f} → {new_ratio:.2f})"
        severity = "info"
    elif ratio_change > SHIFT_SENSITIVITY:
        shift_type = "PUT_SURGE"
        message = f"{symbol}: Put volume surging — P/C ratio +{ratio_change:.2f} ({prev_ratio:.2f} → {new_ratio:.2f})"
        severity = "warning" if new_ratio > 1.0 else "info"
    elif ratio_change < -SHIFT_SENSITIVITY:
        shift_type = "CALL_SURGE"
        message = f"{symbol}: Call volume surging — P/C ratio {ratio_change:.2f} ({prev_ratio:.2f} → {new_ratio:.2f})"
        severity = "info" if new_ratio < 1.0 else "warning"
    else:
        return None

    return {
        "type": shift_type,
        "symbol": symbol,
        "message": message,
        "severity": severity,
        "prev_ratio": prev_ratio,
        "new_ratio": new_ratio,
        "change": round(ratio_change, 3),
        "timestamp": datetime.now().isoformat(),
    }


def _broadcast_sse(event, data):
    """Send SSE event to all connected listeners."""
    with _listener_lock:
        dead = []
        for q in _sse_listeners:
            try:
                q.put_nowait((event, data))
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_listeners.remove(q)


def subscribe_sse():
    """Create a new SSE listener queue."""
    q = queue.Queue(maxsize=50)
    with _listener_lock:
        _sse_listeners.append(q)
    return q


def unsubscribe_sse(q):
    """Remove an SSE listener."""
    with _listener_lock:
        if q in _sse_listeners:
            _sse_listeners.remove(q)


def _scan_loop():
    """Background scanner: fetch options flow data every 60 seconds."""
    global _scan_running
    _scan_running = True
    logger.info("[Options Flow] Scanner started")

    while _scan_running:
        for symbol in TRACKED_SYMBOLS:
            try:
                data = _fetch_options_flow(symbol)
                if data:
                    # Detect shifts before updating cache
                    shift = _detect_shift(symbol, data)

                    # Update cache
                    _flow_cache[symbol] = data

                    # Update history (keep last 100)
                    _flow_history.setdefault(symbol, []).append(data)
                    if len(_flow_history[symbol]) > 100:
                        _flow_history[symbol] = _flow_history[symbol][-100:]

                    # Broadcast update
                    _broadcast_sse("flow_update", data)

                    # Broadcast shift notification
                    if shift:
                        _broadcast_sse("shift", shift)
                        logger.info(f"[Options Flow] SHIFT: {shift['message']}")

                        # Log to db
                        try:
                            from db import execute, IS_POSTGRES
                            _P = "%s" if IS_POSTGRES else "?"
                            execute(
                                f"INSERT INTO bot_log (user_id, level, message, source) "
                                f"VALUES ('system', {_P}, {_P}, 'options_flow')",
                                (shift["severity"], shift["message"][:500]),
                            )
                        except Exception:
                            pass

            except Exception as e:
                logger.warning(f"[Options Flow] Scan error for {symbol}: {e}")

        # Sleep 30 seconds between scans for near-real-time updates
        for _ in range(30):
            if not _scan_running:
                break
            time.sleep(1)

    logger.info("[Options Flow] Scanner stopped")


def start_scanner():
    """Start the background options flow scanner."""
    global _scan_thread, _scan_running
    if _scan_thread and _scan_thread.is_alive():
        return {"status": "already_running"}

    _scan_running = True
    _scan_thread = threading.Thread(target=_scan_loop, daemon=True, name="options-flow-scanner")
    _scan_thread.start()
    return {"status": "started"}


def stop_scanner():
    """Stop the background scanner."""
    global _scan_running
    _scan_running = False
    return {"status": "stopped"}


def get_current_flow():
    """Get latest options flow data for all tracked symbols."""
    result = {}
    for symbol in TRACKED_SYMBOLS:
        if symbol in _flow_cache:
            result[symbol] = _flow_cache[symbol]
        else:
            # Fetch on demand if cache is empty
            data = _fetch_options_flow(symbol)
            if data:
                _flow_cache[symbol] = data
                _flow_history.setdefault(symbol, []).append(data)
                result[symbol] = data
    return result


def get_flow_history(symbol, limit=50):
    """Get historical flow data for a symbol."""
    return _flow_history.get(symbol, [])[-limit:]


def is_scanner_running():
    return _scan_running and _scan_thread and _scan_thread.is_alive()
