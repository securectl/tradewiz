"""
Screener API routes — extracted from app.py.
/api/screener, /api/qullamaggie, and /api/screener/hot-sectors
"""

import json
from flask import Blueprint, jsonify, request

from shared.helpers import NumpyEncoder
from decorators import login_required
from ai_validator import is_configured
from analysis_engine import qullamaggie_scan
from screener import run_screener, get_hot_sectors

bp = Blueprint("screener", __name__)


@bp.route("/api/screener", methods=["POST"])
@login_required
def api_screener():
    data = request.get_json() or {}
    category = data.get("category", "lowcap")
    min_price = float(data.get("min_price", 2.0))
    max_price = float(data.get("max_price", 15.0))
    limit = int(data.get("limit", 20))
    min_price = max(0.5, min(min_price, 50))
    max_price = max(min_price + 0.5, min(max_price, 100))
    limit = max(5, min(limit, 50))

    if not is_configured():
        return jsonify({"error": "OpenRouter API key not configured.", "candidates_scanned": 0, "opportunities": [], "risky": [], "avoided": 0}), 200

    sectors = data.get("sectors", [])
    try:
        result = run_screener(min_price, max_price, limit, category=category, sectors=sectors)
        result_json = json.dumps(result, cls=NumpyEncoder, default=str)
        from flask import current_app
        return current_app.response_class(response=result_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": f"Screener failed: {str(e)}"}), 500


@bp.route("/api/qullamaggie", methods=["POST"])
@login_required
def api_qullamaggie():
    data = request.get_json() or {}
    category = data.get("category", "all")
    from screener import LOWCAP_TICKERS, MIDCAP_TICKERS, LARGECAP_TICKERS
    if category == "lowcap":
        tickers = LOWCAP_TICKERS
    elif category == "midcap":
        tickers = MIDCAP_TICKERS
    elif category == "largecap":
        tickers = LARGECAP_TICKERS
    else:
        tickers = list(set(LOWCAP_TICKERS + MIDCAP_TICKERS + LARGECAP_TICKERS))
    try:
        results = qullamaggie_scan(tickers)
        result_json = json.dumps({"results": results, "scanned": len(tickers)}, cls=NumpyEncoder, default=str)
        from flask import current_app
        return current_app.response_class(response=result_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": f"Qullamaggie scan failed: {str(e)}", "results": [], "scanned": 0}), 500


@bp.route("/api/screener/hot-sectors")
@login_required
def api_hot_sectors():
    period = request.args.get("period", "1mo")
    valid = ["1w", "2w", "1mo", "3mo", "6mo", "1y"]
    if period not in valid:
        period = "1mo"
    return jsonify(get_hot_sectors(period))
