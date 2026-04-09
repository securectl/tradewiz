"""Watchdog Trader API routes — market regime, signals, sentiment, paper trading."""

import json
import logging
from datetime import datetime
from flask import Blueprint, jsonify, request
from decorators import login_required, subscription_required, bot_access_required
from db import query, query_one, execute, IS_POSTGRES
from shared.helpers import _uid, NumpyEncoder

logger = logging.getLogger(__name__)
bp = Blueprint("watchdog", __name__)
P = "%s" if IS_POSTGRES else "?"


@bp.route("/api/watchdog/summary")
@login_required
@bot_access_required("watchdog")
def api_watchdog_summary():
    """Market regime summary — composite score, axes, top signals."""
    from features.watchdog.engine import get_regime, get_signals

    regime = get_regime()
    signals_data = get_signals()
    top_signals = [s for s in signals_data.get("signals", []) if s.get("signal") == "BUY"][:3]

    return jsonify({
        "regime": regime["regime"],
        "color": regime["color"],
        "composite_score": regime["composite_score"],
        "axes": regime["axes"],
        "trade_allowed": regime["trade_allowed"],
        "trade_reason": regime["trade_reason"],
        "spy": {
            "price": regime["market"].get("spy_price"),
            "change_1d": regime["market"].get("spy_1d"),
            "change_5d": regime["market"].get("spy_5d"),
            "sma50": regime["market"].get("spy_sma50"),
            "sma200": regime["market"].get("spy_sma200"),
        },
        "vix": regime["market"].get("vix"),
        "trump_mood": {
            "mood": regime["sentiment"].get("mood", 0),
            "label": regime["sentiment"].get("label", "NEUTRAL"),
        },
        "top_signals": top_signals,
        "timestamp": regime["timestamp"],
        "cached": regime.get("cached", False),
    })


@bp.route("/api/watchdog/signals")
@login_required
@bot_access_required("watchdog")
def api_watchdog_signals():
    """Full signal board for all watchlist tickers."""
    from features.watchdog.engine import get_signals
    watchlist = request.args.get("watchlist")
    if watchlist:
        watchlist = [t.strip().upper() for t in watchlist.split(",")]
    result = get_signals(watchlist)
    return jsonify(result)


@bp.route("/api/watchdog/sentiment")
@login_required
@bot_access_required("watchdog")
def api_watchdog_sentiment():
    """Combined sentiment — Trump mood + market health."""
    from features.watchdog.engine import get_sentiment
    return jsonify(get_sentiment())


@bp.route("/api/watchdog/health")
@login_required
@bot_access_required("watchdog")
def api_watchdog_health():
    """Engine health status and data freshness."""
    from features.watchdog.engine import get_health
    return jsonify(get_health())


# ─── Auto-Trader Controls ────────────────────────────────────

@bp.route("/api/watchdog/auto/status")
@login_required
@bot_access_required("watchdog")
def api_watchdog_auto_status():
    """Get auto-trader status and config."""
    from features.watchdog.engine import get_auto_trader_status
    return jsonify(get_auto_trader_status(_uid()))


@bp.route("/api/watchdog/auto/start", methods=["POST"])
@login_required
@bot_access_required("watchdog")
def api_watchdog_auto_start():
    """Start the auto-trader."""
    from features.watchdog.engine import start_auto_trader
    from shared.helpers import _upsert_bot_config
    uid = _uid()
    _upsert_bot_config(uid, "wd_enabled", "1")
    return jsonify(start_auto_trader(uid))


@bp.route("/api/watchdog/auto/stop", methods=["POST"])
@login_required
@bot_access_required("watchdog")
def api_watchdog_auto_stop():
    """Stop the auto-trader."""
    from features.watchdog.engine import stop_auto_trader
    from shared.helpers import _upsert_bot_config
    _upsert_bot_config(_uid(), "wd_enabled", "0")
    return jsonify(stop_auto_trader())


@bp.route("/api/watchdog/auto/config", methods=["POST"])
@login_required
@bot_access_required("watchdog")
def api_watchdog_auto_config():
    """Update auto-trader configuration."""
    from shared.helpers import _upsert_bot_config
    uid = _uid()
    data = request.get_json() or {}

    allowed_keys = {
        "wd_mode", "wd_scan_interval", "wd_max_positions", "wd_max_position_pct",
        "wd_daily_loss_limit", "wd_min_confidence", "wd_watchlist", "wd_kill_switch",
    }

    updated = []
    for key, value in data.items():
        if key in allowed_keys:
            _upsert_bot_config(uid, key, str(value))
            updated.append(key)

    return jsonify({"ok": True, "updated": updated})


@bp.route("/api/watchdog/auto/kill", methods=["POST"])
@login_required
@bot_access_required("watchdog")
def api_watchdog_auto_kill():
    """Toggle kill switch."""
    from features.watchdog.engine import stop_auto_trader
    from shared.helpers import _upsert_bot_config
    uid = _uid()
    data = request.get_json() or {}
    active = data.get("active", True)
    _upsert_bot_config(uid, "wd_kill_switch", "1" if active else "0")
    if active:
        stop_auto_trader()
    return jsonify({"ok": True, "kill_switch": active})


@bp.route("/api/watchdog/paper-trades", methods=["GET"])
@login_required
@bot_access_required("watchdog")
def api_watchdog_paper_trades_get():
    """Get watchdog trades from unified bot_trades table (asset_type='watchdog')."""
    uid = _uid()
    status = request.args.get("status")
    limit = min(int(request.args.get("limit", 50)), 200)

    conditions = [f"user_id = {P}", "asset_type = 'watchdog'"]
    params = [uid]

    if status:
        conditions.append(f"status = {P}")
        params.append(status)

    where = " AND ".join(conditions)
    trades = query(
        f"SELECT * FROM bot_trades WHERE {where} ORDER BY opened_at DESC LIMIT {P}",
        tuple(params) + (limit,),
    )

    # Compute unrealized PnL for open trades
    open_trades = [t for t in trades if t.get("status") == "open"]
    total_unrealized = 0
    for t in open_trades:
        try:
            import yfinance as yf
            ticker_info = yf.Ticker(t["coin"]).info
            current_price = ticker_info.get("regularMarketPrice") or ticker_info.get("previousClose", 0)
            if current_price and t.get("entry_price"):
                multiplier = 1 if t.get("side", "long") in ("long", "buy") else -1
                unrealized = (current_price - t["entry_price"]) * t.get("size", 0) * multiplier
                t["current_price"] = round(current_price, 2)
                t["unrealized_pnl"] = round(unrealized, 2)
                t["unrealized_pct"] = round((current_price - t["entry_price"]) / t["entry_price"] * 100 * multiplier, 2)
                total_unrealized += unrealized
        except Exception:
            t["current_price"] = None
            t["unrealized_pnl"] = None

    # Alias fields for frontend compatibility
    for t in trades:
        t["ticker"] = t.get("coin", "")
        t["shares"] = t.get("size", 0)
        for f in ("opened_at", "closed_at", "created_at"):
            if t.get(f):
                t[f] = str(t[f])

    # Summary
    closed = [t for t in trades if t.get("status") == "closed"]
    total_realized = sum(t.get("pnl", 0) or 0 for t in closed)
    wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)

    return jsonify({
        "trades": trades,
        "summary": {
            "open_count": len(open_trades),
            "closed_count": len(closed),
            "total_unrealized_pnl": round(total_unrealized, 2),
            "total_realized_pnl": round(total_realized, 2),
            "win_rate": round(wins / max(len(closed), 1) * 100, 1),
            "total_trades": len(closed),
        },
    })


@bp.route("/api/watchdog/paper-trades", methods=["POST"])
@login_required
@bot_access_required("watchdog")
def api_watchdog_paper_trades_post():
    """Open or close a paper trade."""
    uid = _uid()
    data = request.get_json() or {}
    action = data.get("action")

    if action == "open":
        return _open_paper_trade(uid, data)
    elif action == "close":
        return _close_paper_trade(uid, data)
    else:
        return jsonify({"error": "Invalid action. Use 'open' or 'close'."}), 400


def _open_paper_trade(uid, data):
    """Open a new paper trade."""
    ticker = (data.get("ticker") or "").upper()
    side = data.get("side", "long")
    shares = float(data.get("shares", 0))
    stop_loss = data.get("stop_loss")
    take_profit = data.get("take_profit")
    reasoning = data.get("reasoning", "")
    strategy = data.get("strategy", "")

    if not ticker or shares <= 0:
        return jsonify({"error": "Ticker and positive shares required"}), 400

    # Get current price
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        entry_price = info.get("regularMarketPrice") or info.get("previousClose")
        if not entry_price:
            return jsonify({"error": f"Could not fetch price for {ticker}"}), 400
    except Exception as e:
        return jsonify({"error": f"Price fetch failed: {str(e)[:80]}"}), 400

    # Get current regime
    try:
        from features.watchdog.engine import get_regime
        regime = get_regime().get("regime", "UNKNOWN")
    except Exception:
        regime = "UNKNOWN"

    # Check for existing open position in same ticker
    existing = query_one(
        f"SELECT id FROM bot_trades WHERE user_id = {P} AND coin = {P} AND asset_type = 'watchdog' AND status = 'open'",
        (uid, ticker),
    )
    if existing:
        return jsonify({"error": f"Already have an open watchdog trade for {ticker}"}), 400

    trade_id = execute(
        f"""INSERT INTO bot_trades
            (user_id, coin, side, size, entry_price, stop_loss, take_profit, status,
             strategy, asset_type, signal_reason, regime_at_entry, direction_bias)
            VALUES ({P},{P},{P},{P},{P},{P},{P},'open',{P},'watchdog',{P},{P},'long')""",
        (uid, ticker, side, shares, entry_price,
         float(stop_loss) if stop_loss else None,
         float(take_profit) if take_profit else None,
         strategy, reasoning, regime),
    )

    return jsonify({
        "ok": True,
        "message": f"Paper trade opened: {side.upper()} {shares} {ticker} @ ${entry_price:.2f}",
        "trade": {
            "id": trade_id,
            "ticker": ticker,
            "side": side,
            "shares": shares,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "regime_at_entry": regime,
        },
    })


def _close_paper_trade(uid, data):
    """Close an existing paper trade."""
    trade_id = data.get("trade_id")
    notes = data.get("notes", "")

    if not trade_id:
        return jsonify({"error": "trade_id required"}), 400

    trade = query_one(
        f"SELECT * FROM bot_trades WHERE id = {P} AND user_id = {P} AND asset_type = 'watchdog' AND status = 'open'",
        (trade_id, uid),
    )
    if not trade:
        return jsonify({"error": "Trade not found or already closed"}), 404

    ticker = trade["coin"]

    # Get exit price
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        exit_price = info.get("regularMarketPrice") or info.get("previousClose")
        if not exit_price:
            return jsonify({"error": f"Could not fetch exit price for {ticker}"}), 400
    except Exception as e:
        return jsonify({"error": f"Price fetch failed: {str(e)[:80]}"}), 400

    # Calculate PnL
    multiplier = 1 if trade.get("side", "long") in ("long", "buy") else -1
    pnl = (exit_price - trade["entry_price"]) * trade["size"] * multiplier
    pnl_pct = (exit_price - trade["entry_price"]) / trade["entry_price"] * 100 * multiplier
    fee = round(trade["size"] * 0.01, 4)

    execute(
        f"""UPDATE bot_trades
            SET exit_price = {P}, pnl = {P}, pnl_pct = {P}, fee = {P}, status = 'closed',
                closed_at = {'NOW()' if IS_POSTGRES else "datetime('now')"}
            WHERE id = {P} AND user_id = {P}""",
        (exit_price, round(pnl - fee, 2), round(pnl_pct, 2), fee, trade_id, uid),
    )

    return jsonify({
        "ok": True,
        "message": f"Closed {ticker}: {'profit' if pnl > 0 else 'loss'} ${abs(pnl):.2f} ({pnl_pct:+.2f}%)",
        "trade": {
            "id": trade_id,
            "ticker": ticker,
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        },
    })
