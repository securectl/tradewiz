"""Call-option activity API — which call contracts are gaining vs losing volume.

Single GET endpoint backing the "Option Calls" tab. Data comes from Webull
(via the seam) with a yfinance fallback; see ``engine.get_call_activity``.
"""

import logging

from flask import Blueprint, jsonify, request

from decorators import login_required
from features.options_calls.engine import get_call_activity, get_options_flow

logger = logging.getLogger(__name__)
bp = Blueprint("options_calls", __name__)


@bp.route("/api/options/calls")
@login_required
def api_options_calls():
    """Call activity for one stock: ?symbol=AAPL[&refresh=1].

    Returns increasing / decreasing call buckets plus aggregate read. Always
    200 with the payload (including a soft ``error`` field when no chain is
    available) so the frontend renders a clean empty state rather than a throw.
    """
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    force = request.args.get("refresh") in ("1", "true", "yes")
    data = get_call_activity(symbol, force_refresh=force)
    return jsonify(data)


@bp.route("/api/options/flow")
@login_required
def api_options_flow():
    """Directional flow for one stock: ?symbol=AAPL[&refresh=1].

    Returns four ranked buckets — Long Calls, Naked Calls, Naked Puts, Long Puts
    — each with the top contracts (#1/#2/#3) by volume. Long vs naked is an
    estimate from the bid/ask aggressor heuristic (see engine). Always 200 with
    a soft ``error`` field so the frontend renders a clean empty state.
    """
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    force = request.args.get("refresh") in ("1", "true", "yes")
    return jsonify(get_options_flow(symbol, force_refresh=force))
