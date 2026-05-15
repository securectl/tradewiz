"""Claude Trading Bot API routes — start/stop/status/config + watchlist view."""

import logging

from flask import Blueprint, jsonify, request

from db import IS_POSTGRES
from decorators import bot_access_required, login_required
from shared.helpers import _uid

logger = logging.getLogger(__name__)
bp = Blueprint("claude_bot", __name__)
P = "%s" if IS_POSTGRES else "?"


@bp.route("/api/claude_bot/status")
@login_required
@bot_access_required("watchdog")
def api_status():
    from claude_bot.bot_engine import status
    return jsonify(status(_uid()))


@bp.route("/api/claude_bot/start", methods=["POST"])
@login_required
@bot_access_required("watchdog")
def api_start():
    from claude_bot.bot_engine import start
    from shared.helpers import _upsert_bot_config
    uid = _uid()
    _upsert_bot_config(uid, "cb_enabled", "1")
    _upsert_bot_config(uid, "cb_kill_switch", "0")
    return jsonify(start(uid))


@bp.route("/api/claude_bot/stop", methods=["POST"])
@login_required
@bot_access_required("watchdog")
def api_stop():
    from claude_bot.bot_engine import stop
    from shared.helpers import _upsert_bot_config
    _upsert_bot_config(_uid(), "cb_enabled", "0")
    return jsonify(stop())


@bp.route("/api/claude_bot/config", methods=["POST"])
@login_required
@bot_access_required("watchdog")
def api_config():
    """Update one or more config keys. Body: {"key": "value", ...}"""
    from shared.helpers import _upsert_bot_config
    data = request.get_json(silent=True) or {}
    allowed = {
        "cb_scan_interval", "cb_max_positions", "cb_max_position_pct",
        "cb_max_total_exposure_pct",
        "cb_daily_loss_limit", "cb_min_confidence", "cb_take_profit_pct",
        "cb_scalp_stop_pct", "cb_hard_stop_pct", "cb_reentry_size_factor",
        "cb_reentry_max_attempts", "cb_reentry_window_days", "cb_kill_switch",
        "cb_broker", "cb_mode",
    }
    updated = {}
    for k, v in data.items():
        if k in allowed:
            _upsert_bot_config(_uid(), k, str(v))
            updated[k] = str(v)
    return jsonify({"ok": True, "updated": updated})


@bp.route("/api/claude_bot/watchlist")
@login_required
@bot_access_required("watchdog")
def api_watchlist():
    from claude_bot.bot_engine import reentry_watchlist
    return jsonify({"watchlist": reentry_watchlist(_uid())})


@bp.route("/api/claude_bot/logs")
@login_required
@bot_access_required("watchdog")
def api_logs():
    from claude_bot.bot_engine import get_logs
    limit = int(request.args.get("limit", 100))
    return jsonify({"logs": get_logs(_uid(), limit=limit)})


@bp.route("/api/claude_bot/balance")
@login_required
@bot_access_required("watchdog")
def api_balance():
    from claude_bot.bot_engine import get_balance
    return jsonify(get_balance(_uid()))


@bp.route("/api/claude_bot/oversold-tracker")
@login_required
@bot_access_required("watchdog")
def api_oversold_tracker():
    from claude_bot.bot_engine import get_oversold_tracker
    limit = int(request.args.get("limit", 30))
    return jsonify({"tickers": get_oversold_tracker(limit=limit)})


@bp.route("/api/claude_bot/trades/<int:trade_id>/close", methods=["POST"])
@login_required
@bot_access_required("watchdog")
def api_close_trade(trade_id):
    """Manual close for a Claude Bot open trade. Marks the position closed at
    the current yfinance mark price; eligible_reentry is False so we don't
    immediately re-add it to the watchlist."""
    from db import query_one
    from claude_bot.bot_engine import _close_trade
    uid = _uid()
    r = query_one(
        f"SELECT * FROM bot_trades WHERE id = {P} AND user_id = {P} "
        f"AND asset_type = 'claude' AND status = 'open'",
        (trade_id, uid),
    )
    if not r:
        return jsonify({"ok": False, "error": "Open Claude Bot trade not found"}), 404
    try:
        import yfinance as yf
        fi = yf.Ticker(r["coin"]).fast_info
        lp = fi.get("last_price") if isinstance(fi, dict) else getattr(fi, "last_price", None)
        if not lp:
            return jsonify({"ok": False, "error": "Could not fetch current price"}), 500
        # Sanity guardrail mirrors _close_trade — reject implausible mark vs entry
        entry = float(r.get("entry_price") or 0)
        if entry > 0 and abs(float(lp) / entry - 1.0) > 1.0:
            return jsonify({
                "ok": False,
                "error": f"Refused: implausible mark ${float(lp):.2f} vs entry ${entry:.2f}. "
                         f"Likely yfinance bad data — try again in a moment.",
            }), 422
        _close_trade(uid, dict(r), float(lp), "Manual close", eligible_reentry=False)
        return jsonify({"ok": True, "message": f"Closed {r['coin']} @ ${float(lp):.2f}"})
    except Exception as e:
        logger.exception("Manual close failed")
        return jsonify({"ok": False, "error": str(e)}), 500
