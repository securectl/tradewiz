"""
Congress Trades API — politician stock holdings and trade disclosures.
"""

from flask import Blueprint, jsonify, request
from decorators import login_required, subscription_required
from congress_trades import get_congress_trades

bp = Blueprint("congress", __name__)


@bp.route("/api/congress/trades")
@login_required
@subscription_required("pro")
def api_congress_trades():
    """Search congressional stock trades.
    Query params: chamber, name, state, ticker, days, year"""
    chamber = request.args.get("chamber")  # house, senate, or None
    name = request.args.get("name", "").strip() or None
    state = request.args.get("state", "").strip() or None
    ticker = request.args.get("ticker", "").strip() or None
    days = int(request.args.get("days", 90))
    year = request.args.get("year")
    year = int(year) if year else None

    if days not in (7, 14, 30, 60, 90, 180, 365):
        days = 90
    if chamber and chamber not in ("house", "senate"):
        chamber = None

    data = get_congress_trades(
        chamber=chamber, name=name, state=state,
        ticker=ticker, days=days, year=year,
    )
    return jsonify(data)
