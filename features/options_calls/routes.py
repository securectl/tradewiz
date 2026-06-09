"""Call-option activity API — which call contracts are gaining vs losing volume.

Single GET endpoint backing the "Option Calls" tab. Data comes from Webull
(via the seam) with a yfinance fallback; see ``engine.get_call_activity``.
"""

import logging

from flask import Blueprint, jsonify, request

from decorators import login_required
from features.options_calls.engine import get_call_activity

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
