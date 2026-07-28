"""Earnings-calendar API — the weekly "most anticipated reports" board.

Single GET endpoint backing the Earnings Calendar tab. See
``engine.get_earnings_week`` for how the board is assembled and cached.
"""

import logging

from flask import Blueprint, jsonify, request

from decorators import login_required
from features.earnings_calendar.engine import get_earnings_week

logger = logging.getLogger(__name__)
bp = Blueprint("earnings_calendar", __name__)


@bp.route("/api/earnings/calendar")
@login_required
def api_earnings_calendar():
    """Weekly earnings board: ?week=0 (offset, 0=current) &wide=1 &refresh=1.

    Always 200 with the payload so the frontend renders a clean empty state
    rather than throwing. ``week`` shifts the window by whole weeks (negative =
    past). ``wide=1`` expands the universe to large + mid cap.
    """
    try:
        week = int(request.args.get("week", 0))
    except (TypeError, ValueError):
        week = 0
    week = max(-8, min(8, week))       # bound how far the board can scan
    wide = request.args.get("wide") in ("1", "true", "yes")
    force = request.args.get("refresh") in ("1", "true", "yes")
    data = get_earnings_week(week_offset=week, wide=wide, force_refresh=force)
    return jsonify(data)
