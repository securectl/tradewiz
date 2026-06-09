"""Smart Money Tracker API routes — institutional holdings, whale activity, signals."""

import json
import logging
from datetime import datetime
from flask import Blueprint, jsonify, request
from decorators import login_required, subscription_required
from shared.helpers import NumpyEncoder

logger = logging.getLogger(__name__)
bp = Blueprint("smart_money", __name__)


@bp.route("/api/smart-money/summary")
@login_required
@subscription_required("pro")
def api_smart_money_summary():
    """Get overview of all tracked hedge funds, recent activity, and hot tickers."""
    from features.smart_money.engine import get_whale_summary
    days = request.args.get("days", 30, type=int)
    days = min(max(days, 7), 365)
    return jsonify(get_whale_summary(days=days))


@bp.route("/api/smart-money/sector-flow")
@login_required
def api_sector_flow():
    """Sector-level options money flow (calls vs puts) across the 11 SPDR sector
    ETFs — which sectors have money flowing in vs being sold. Market data, so
    login-gated (not pro-gated) since the dashboard surfaces it to all users."""
    from features.smart_money.sector_flow import get_sector_options_flow
    force = request.args.get("refresh") in ("1", "true", "yes")
    return jsonify(get_sector_options_flow(force_refresh=force))


@bp.route("/api/smart-money/holdings/<int:entity_id>")
@login_required
@subscription_required("pro")
def api_smart_money_holdings(entity_id):
    """Get latest holdings for a specific fund."""
    from features.smart_money.engine import get_entity_holdings
    holdings = get_entity_holdings(entity_id)
    return jsonify({"entity_id": entity_id, "holdings": holdings, "count": len(holdings)})


@bp.route("/api/smart-money/ticker/<ticker>")
@login_required
@subscription_required("pro")
def api_smart_money_ticker(ticker):
    """Get all institutional activity for a specific ticker."""
    from features.smart_money.engine import get_ticker_whale_activity
    activity = get_ticker_whale_activity(ticker.upper())
    return jsonify({"ticker": ticker.upper(), "activity": activity, "count": len(activity)})


@bp.route("/api/smart-money/signals")
@login_required
@subscription_required("pro")
def api_smart_money_signals():
    """Get convergence signals — tickers where multiple funds are building positions."""
    from features.smart_money.engine import get_smart_money_signals
    days = request.args.get("days", 30, type=int)
    days = min(max(days, 7), 365)
    signals = get_smart_money_signals(days=days)
    return jsonify({"signals": signals, "count": len(signals), "days": days, "timestamp": datetime.now().isoformat()})


@bp.route("/api/smart-money/refresh", methods=["POST"])
@login_required
@subscription_required("pro")
def api_smart_money_refresh():
    """Trigger a manual refresh of institutional holdings."""
    from features.smart_money.engine import refresh_all_funds
    import threading
    # Run in background to not block
    t = threading.Thread(target=refresh_all_funds, daemon=True)
    t.start()
    return jsonify({"ok": True, "message": "Refresh started in background. Check back in a few minutes."})
