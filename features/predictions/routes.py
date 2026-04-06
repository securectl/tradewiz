"""
Prediction Markets API routes — Polymarket + Kalshi data.
"""

from flask import Blueprint, jsonify, request
from decorators import login_required, subscription_required
from prediction_markets import get_prediction_markets, get_poly_sentiment_for_pulse

bp = Blueprint("predictions", __name__)


@bp.route("/api/predictions")
@login_required
@subscription_required("pro")
def api_predictions():
    """Get prediction market data (Polymarket + Kalshi combined)."""
    force = request.args.get("force", "").lower() == "true"
    data = get_prediction_markets(force_refresh=force)
    return jsonify(data)


@bp.route("/api/predictions/sentiment")
@login_required
@subscription_required("pro")
def api_predictions_sentiment():
    """Quick sentiment gauge from prediction markets."""
    return jsonify(get_poly_sentiment_for_pulse())
