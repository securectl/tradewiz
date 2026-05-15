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


@bp.route("/api/screener/history")
@login_required
def api_screener_history():
    """Get historical screener results for trend analysis.
    ?category=lowcap&days=30  — all results for a category over N days
    ?ticker=AAPL&days=30      — all appearances of a ticker across categories
    ?category=lowcap&ticker=AAPL&days=30 — specific combo
    """
    from db import query, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"

    category = request.args.get("category")
    ticker = request.args.get("ticker")
    days = min(int(request.args.get("days", 30)), 90)

    conditions = [f"scan_date >= {P}"]
    params = []

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    params.append(cutoff)

    if category:
        conditions.append(f"category = {P}")
        params.append(category)
    if ticker:
        conditions.append(f"ticker = {P}")
        params.append(ticker.upper())

    where = " AND ".join(conditions)
    rows = query(
        f"SELECT category, scan_date, ticker, price, verdict, confidence, summary, sector, name, market_cap "
        f"FROM screener_results WHERE {where} ORDER BY scan_date DESC, confidence DESC LIMIT 500",
        tuple(params),
    )

    results = [{
        "category": r["category"], "scan_date": r["scan_date"], "ticker": r["ticker"],
        "price": r["price"], "verdict": r["verdict"], "confidence": r["confidence"],
        "summary": r.get("summary", ""), "sector": r.get("sector"), "name": r.get("name"),
        "market_cap": r.get("market_cap"),
    } for r in rows]

    return jsonify({"results": results, "count": len(results), "days": days})


@bp.route("/api/screener/trending")
@login_required
def api_screener_trending():
    """Find stocks appearing in multiple scans over recent days — persistent signals.
    Returns tickers sorted by number of appearances (most persistent first).
    """
    from db import query, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"

    days = min(int(request.args.get("days", 7)), 30)
    category = request.args.get("category")

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    if category:
        rows = query(
            f"SELECT ticker, name, sector, COUNT(DISTINCT scan_date) as appearances, "
            f"MAX(scan_date) as last_seen, MIN(scan_date) as first_seen, "
            f"ROUND(AVG(confidence)::numeric, 1) as avg_confidence, "
            f"MAX(verdict) as latest_verdict, ROUND(AVG(price)::numeric, 2) as avg_price "
            f"FROM screener_results WHERE category = {P} AND scan_date >= {P} "
            f"GROUP BY ticker, name, sector HAVING COUNT(DISTINCT scan_date) >= 2 "
            f"ORDER BY appearances DESC, avg_confidence DESC LIMIT 50"
            if IS_POSTGRES else
            f"SELECT ticker, name, sector, COUNT(DISTINCT scan_date) as appearances, "
            f"MAX(scan_date) as last_seen, MIN(scan_date) as first_seen, "
            f"ROUND(AVG(confidence), 1) as avg_confidence, "
            f"MAX(verdict) as latest_verdict, ROUND(AVG(price), 2) as avg_price "
            f"FROM screener_results WHERE category = {P} AND scan_date >= {P} "
            f"GROUP BY ticker, name, sector HAVING COUNT(DISTINCT scan_date) >= 2 "
            f"ORDER BY appearances DESC, avg_confidence DESC LIMIT 50",
            (category, cutoff),
        )
    else:
        rows = query(
            f"SELECT ticker, name, sector, COUNT(DISTINCT scan_date) as appearances, "
            f"COUNT(DISTINCT category) as categories, "
            f"MAX(scan_date) as last_seen, MIN(scan_date) as first_seen, "
            f"ROUND(AVG(confidence)::numeric, 1) as avg_confidence, "
            f"MAX(verdict) as latest_verdict, ROUND(AVG(price)::numeric, 2) as avg_price "
            f"FROM screener_results WHERE scan_date >= {P} "
            f"GROUP BY ticker, name, sector HAVING COUNT(DISTINCT scan_date) >= 2 "
            f"ORDER BY appearances DESC, categories DESC, avg_confidence DESC LIMIT 50"
            if IS_POSTGRES else
            f"SELECT ticker, name, sector, COUNT(DISTINCT scan_date) as appearances, "
            f"COUNT(DISTINCT category) as categories, "
            f"MAX(scan_date) as last_seen, MIN(scan_date) as first_seen, "
            f"ROUND(AVG(confidence), 1) as avg_confidence, "
            f"MAX(verdict) as latest_verdict, ROUND(AVG(price), 2) as avg_price "
            f"FROM screener_results WHERE scan_date >= {P} "
            f"GROUP BY ticker, name, sector HAVING COUNT(DISTINCT scan_date) >= 2 "
            f"ORDER BY appearances DESC, categories DESC, avg_confidence DESC LIMIT 50",
            (cutoff,),
        )

    # PostgreSQL ROUND(numeric, n) returns Decimal — Flask's JSON encoder
    # serializes Decimal as a string, which breaks `.toFixed()` on the client.
    # Coerce to float so the client sees a JS Number.
    def _f(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    results = [{
        "ticker": r["ticker"], "name": r.get("name"), "sector": r.get("sector"),
        "appearances": r["appearances"], "categories": r.get("categories", 1),
        "last_seen": r["last_seen"], "first_seen": r["first_seen"],
        "avg_confidence": _f(r.get("avg_confidence")), "latest_verdict": r.get("latest_verdict"),
        "avg_price": _f(r.get("avg_price")),
    } for r in rows]

    return jsonify({"trending": results, "count": len(results), "days": days})
