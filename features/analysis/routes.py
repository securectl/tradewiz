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


@bp.route("/api/analyze/<ticker>/news")
@login_required
def api_news(ticker):
    """Recent news + social mentions for a ticker (last 24h by default).

    Query params:
        hours=<int>   Lookback window (default 24, max 72)
        force=1       Bypass 5-minute cache
    """
    from shared.news_fetcher import fetch_news
    try:
        hours = min(72, max(1, int(request.args.get("hours", 24))))
    except ValueError:
        hours = 24
    force = request.args.get("force") in ("1", "true", "yes")
    return jsonify(fetch_news(ticker, hours=hours, force=force))


@bp.route("/api/analyze/<ticker>/liquidity")
@login_required
def api_liquidity(ticker):
    """Compact liquidity snapshot — cash, debt, ratios, runway. Pulled from
    the same yfinance fundamentals as the full analysis but without the
    heavy LLM/technical pipeline."""
    try:
        f = fetch_fundamentals(ticker.strip().upper())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not f or f.get("error"):
        return jsonify({"error": (f or {}).get("error", "no data")}), 404

    cash = f.get("total_cash") or 0
    debt = f.get("total_debt") or 0
    cash_to_debt = f.get("cash_to_debt")
    current_ratio = f.get("current_ratio")
    runway = f.get("cash_runway_months")
    fcf = f.get("free_cf") or 0
    op_cf = f.get("operating_cf") or 0
    market_cap = f.get("market_cap") or 0

    # Health score: simple composite — cash > debt + positive FCF + current ratio > 1
    health = 50
    if cash_to_debt is not None:
        if cash_to_debt >= 1.0: health += 20
        elif cash_to_debt >= 0.5: health += 10
        elif cash_to_debt < 0.2: health -= 15
    if current_ratio is not None:
        if current_ratio >= 1.5: health += 10
        elif current_ratio < 1.0: health -= 10
    if fcf and fcf > 0: health += 10
    if op_cf and op_cf < 0: health -= 10
    health = max(0, min(100, health))

    if health >= 70: rating, color = "STRONG", "#00c896"
    elif health >= 50: rating, color = "OK", "#ffc837"
    elif health >= 30: rating, color = "WEAK", "#ff8c42"
    else: rating, color = "RISK", "#ff4757"

    return jsonify({
        "ticker": ticker.upper(),
        "total_cash": cash,
        "total_debt": debt,
        "net_cash": cash - debt,
        "cash_to_debt": round(cash_to_debt, 2) if cash_to_debt is not None else None,
        "current_ratio": round(current_ratio, 2) if current_ratio is not None else None,
        "debt_to_equity": f.get("de_ratio"),
        "operating_cf": op_cf,
        "free_cf": fcf,
        "fcf_yield_pct": f.get("fcf_yield"),
        "cash_runway_months": round(runway, 1) if runway is not None else None,
        "market_cap": market_cap,
        "health_score": health,
        "rating": rating,
        "color": color,
    })


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
