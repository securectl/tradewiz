"""
Analysis API routes — analyze, validate, predict, AI status, Qullamaggie scan.
Extracted from app.py.
"""

import json
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, current_app
from shared.helpers import _uid, NumpyEncoder, P
from decorators import login_required, llm_rate_limit
from rate_limiter import set_llm_user
from analysis_engine import analyze_ticker, fetch_fundamentals, fetch_fundamentals_crypto, qullamaggie_scan, fetch_stock_data, calculate_indicators
from ai_validator import validate_setup, predict_12month, is_configured
from db import execute

bp = Blueprint("analysis", __name__)


@bp.route("/api/analyze", methods=["POST"])
@login_required
def api_analyze():
    """Analyze a stock ticker."""
    data = request.get_json()
    ticker = data.get("ticker", "").strip().upper()
    period = data.get("period", "6mo")
    interval = data.get("interval", "1d")

    if not ticker:
        return jsonify({"error": "Ticker symbol is required"}), 400

    valid_periods = ["ytd", "1mo", "3mo", "6mo", "1y", "2y"]
    valid_intervals = ["1h", "4h", "1d", "1wk"]
    if period not in valid_periods:
        period = "6mo"
    if interval not in valid_intervals:
        interval = "1d"

    if interval in ["1h", "4h"] and period not in ["1mo", "3mo"]:
        period = "3mo"

    yf_interval = "60m" if interval == "1h" else interval
    if interval == "4h":
        yf_interval = "1d"

    try:
        result = analyze_ticker(ticker, period, yf_interval)
        result_json = json.dumps(result, cls=NumpyEncoder, default=str)

        # Save to history
        uid = _uid()
        expires = datetime.now() + timedelta(days=3)
        execute(
            f"INSERT INTO searches (user_id, ticker, period, interval_val, result_json, expires_at) "
            f"VALUES ({P}, {P}, {P}, {P}, {P}, {P})",
            (uid, ticker, period, interval, result_json, expires.isoformat()),
        )

        return current_app.response_class(response=result_json, status=200, mimetype='application/json')

    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500


@bp.route("/api/validate", methods=["POST"])
@login_required
@llm_rate_limit(call_source="validate", call_count=3)
def api_validate():
    """Run AI validation on a completed analysis."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Analysis data is required"}), 400

    if not is_configured():
        return jsonify({
            "configured": False,
            "error": "OpenRouter API key not configured.",
        }), 200

    try:
        set_llm_user(_uid(), "validate")
        fast_mode = data.pop("fast_mode", None)
        result = validate_setup(data, fast_mode=fast_mode)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"AI validation failed: {str(e)}"}), 500


@bp.route("/api/predict", methods=["POST"])
@login_required
@llm_rate_limit(call_source="predict", call_count=4)
def api_predict():
    """Run 12-month investment prediction."""
    data = request.get_json()
    ticker = data.get("ticker", "").strip().upper()

    if not ticker:
        return jsonify({"error": "Ticker symbol is required"}), 400

    if not is_configured():
        return jsonify({
            "configured": False,
            "error": "OpenRouter API key not configured.",
        }), 200

    try:
        set_llm_user(_uid(), "predict")
        fast_mode = data.get("fast_mode")
        is_crypto = ticker.endswith('-USD') and not ticker.startswith('USD')
        if is_crypto:
            fundamentals = fetch_fundamentals_crypto(ticker)
        else:
            fundamentals = fetch_fundamentals(ticker)
        # Fetch OHLCV + compute technical indicators for price action analysis
        indicators = None
        df = None
        try:
            df = fetch_stock_data(ticker, period="1y", interval="1d")
            if df is not None and len(df) > 0:
                indicators = calculate_indicators(df)
        except Exception:
            pass  # Graceful degradation — prediction works without technicals
        result = predict_12month(fundamentals, indicators=indicators, df=df, fast_mode=fast_mode)
        result_json = json.dumps(result, cls=NumpyEncoder, default=str)
        return current_app.response_class(response=result_json, status=200, mimetype='application/json')
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Prediction failed: {str(e)}"}), 500


@bp.route("/api/ai-status")
def api_ai_status():
    """Check if AI validation is available."""
    return jsonify({"configured": is_configured()})


@bp.route("/api/qullamaggie", methods=["POST"])
@login_required
@llm_rate_limit(call_source="qullamaggie", call_count=1)
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
        return current_app.response_class(response=result_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": f"Qullamaggie scan failed: {str(e)}", "results": [], "scanned": 0}), 500
