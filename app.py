"""
AI Stock Analyst — Web Application
Flask server with multi-user auth, PostgreSQL/SQLite, and trading bots.
"""

import json
import os
import numpy as np
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, redirect, url_for
from flask_login import current_user
from dotenv import load_dotenv

load_dotenv()

from analysis_engine import analyze_ticker, fetch_fundamentals, fetch_fundamentals_crypto, qullamaggie_scan
from ai_validator import validate_setup, predict_12month, is_configured
from screener import run_screener, get_hot_sectors
from status_checker import (
    start_background_checker, run_check_cycle, purge_old_checks,
    SERVICES, run_all_checks, store_check_results,
)
from crypto_bot.bot_engine import get_bot
from crypto_bot.blofin_client import COIN_MAP, DEFAULT_COINS
from crypto_bot.crypto_validator import get_trending_crypto
from stock_bot.stock_engine import get_stock_bot
from stock_bot.broker_client import STOCK_MAP, DEFAULT_STOCKS, is_market_open, get_stock_info, validate_ticker

from db import get_db, put_db, IS_POSTGRES, query, query_one, execute, execute_many
from auth import auth_bp, init_auth
from decorators import login_required, admin_required, trader_required


class NumpyEncoder(json.JSONEncoder):
    """Handle numpy types and NaN in JSON serialization."""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            val = float(obj)
            if val != val:  # NaN check
                return None
            return val
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


app = Flask(__name__)
app.json.sort_keys = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key-change-me")

# Initialize auth
init_auth(app)
app.register_blueprint(auth_bp)

# SQL placeholder
P = "%s" if IS_POSTGRES else "?"


def _uid():
    """Get current user ID, or None if not authenticated."""
    if current_user.is_authenticated:
        return current_user.id
    return None


def _require_uid():
    """Get current user ID, raise 401 if not authenticated."""
    if not current_user.is_authenticated:
        return None
    return current_user.id


# ─── Init DB (for local SQLite — migrations.py handles PostgreSQL) ─────────

def init_db():
    """Run migrations to set up tables."""
    from migrations import run_migrations
    run_migrations()
    purge_old_checks(90)


def cleanup_expired():
    """Remove searches older than 3 days."""
    execute(f"DELETE FROM searches WHERE expires_at < {P}", (datetime.now().isoformat(),))


# ─── Routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    return render_template("index.html")


@app.route("/api/me")
@login_required
def api_me():
    """Return current user info + roles."""
    return jsonify(current_user.to_dict())


# ─── Analysis API ────────────────────────────────────────────────────

@app.route("/api/analyze", methods=["POST"])
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

        return app.response_class(response=result_json, status=200, mimetype='application/json')

    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500


@app.route("/api/validate", methods=["POST"])
@login_required
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
        fast_mode = data.pop("fast_mode", None)
        result = validate_setup(data, fast_mode=fast_mode)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"AI validation failed: {str(e)}"}), 500


@app.route("/api/predict", methods=["POST"])
@login_required
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
        fast_mode = data.get("fast_mode")
        is_crypto = ticker.endswith('-USD') and not ticker.startswith('USD')
        if is_crypto:
            fundamentals = fetch_fundamentals_crypto(ticker)
        else:
            fundamentals = fetch_fundamentals(ticker)
        result = predict_12month(fundamentals, fast_mode=fast_mode)
        result_json = json.dumps(result, cls=NumpyEncoder, default=str)
        return app.response_class(response=result_json, status=200, mimetype='application/json')
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Prediction failed: {str(e)}"}), 500


@app.route("/api/ai-status")
def api_ai_status():
    """Check if AI validation is available."""
    return jsonify({"configured": is_configured()})


# ─── Search History ──────────────────────────────────────────────────

@app.route("/api/history")
@login_required
def api_history():
    """Get search history (user-scoped)."""
    cleanup_expired()
    uid = _uid()
    rows = query(
        f"SELECT id, ticker, period, interval_val, created_at, expires_at FROM searches "
        f"WHERE user_id = {P} ORDER BY created_at DESC LIMIT 50",
        (uid,),
    )
    return jsonify([{
        "id": r["id"], "ticker": r["ticker"], "period": r["period"],
        "interval": r["interval_val"], "created_at": r["created_at"],
        "expires_at": r["expires_at"],
    } for r in rows])


@app.route("/api/history/<int:search_id>")
@login_required
def api_history_detail(search_id):
    """Get a specific search result."""
    cleanup_expired()
    uid = _uid()
    row = query_one(f"SELECT * FROM searches WHERE id = {P} AND user_id = {P}", (search_id, uid))
    if not row:
        return jsonify({"error": "Search not found or expired"}), 404
    return jsonify(json.loads(row["result_json"]))


@app.route("/api/history/<int:search_id>", methods=["DELETE"])
@login_required
def api_history_delete(search_id):
    """Delete a search result."""
    uid = _uid()
    execute(f"DELETE FROM searches WHERE id = {P} AND user_id = {P}", (search_id, uid))
    return jsonify({"ok": True})


# ─── Tracker / Journal API ─────────────────────────────────────────────

def get_week_start(dt=None):
    if dt is None:
        dt = datetime.now()
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


@app.route("/api/journal", methods=["GET"])
@login_required
def api_journal_list():
    uid = _uid()
    ticker = request.args.get("ticker", "").strip().upper()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    offset = (page - 1) * per_page

    if ticker:
        rows = query(
            f"SELECT * FROM journal_entries WHERE user_id = {P} AND ticker = {P} ORDER BY created_at DESC LIMIT {P} OFFSET {P}",
            (uid, ticker, per_page, offset),
        )
    else:
        rows = query(
            f"SELECT * FROM journal_entries WHERE user_id = {P} ORDER BY created_at DESC LIMIT {P} OFFSET {P}",
            (uid, per_page, offset),
        )

    return jsonify([{
        "id": r["id"], "ticker": r["ticker"], "notes": r["notes"],
        "action": r["action"], "entry_price": r["entry_price"],
        "exit_price": r["exit_price"], "shares": r["shares"],
        "pnl": r["pnl"], "created_at": r["created_at"],
    } for r in rows])


@app.route("/api/journal", methods=["POST"])
@login_required
def api_journal_add():
    uid = _uid()
    data = request.get_json()
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "Ticker is required"}), 400

    notes = data.get("notes", "")
    action = data.get("action")
    entry_price = data.get("entry_price")
    exit_price = data.get("exit_price")
    shares = data.get("shares")

    pnl = None
    if action == "SELL" and entry_price is not None and exit_price is not None and shares is not None:
        pnl = round((exit_price - entry_price) * shares, 2)

    conn = get_db()
    try:
        if IS_POSTGRES:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"INSERT INTO journal_entries (user_id, ticker, notes, action, entry_price, exit_price, shares, pnl) "
                f"VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}) RETURNING id",
                (uid, ticker, notes, action, entry_price, exit_price, shares, pnl),
            )
            entry_id = cur.fetchone()["id"]
            cur.close()
        else:
            cur = conn.execute(
                f"INSERT INTO journal_entries (user_id, ticker, notes, action, entry_price, exit_price, shares, pnl) "
                f"VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P})",
                (uid, ticker, notes, action, entry_price, exit_price, shares, pnl),
            )
            entry_id = cur.lastrowid

        if pnl is not None:
            week = get_week_start()
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO weekly_goals (user_id, week_start, target_amount, actual_amount) "
                    f"VALUES ({P}, {P}, 0, {P}) ON CONFLICT(user_id, week_start) DO UPDATE SET actual_amount = weekly_goals.actual_amount + {P}",
                    (uid, week, pnl, pnl),
                )
                cur.close()
            else:
                conn.execute(
                    f"INSERT INTO weekly_goals (user_id, week_start, target_amount, actual_amount) "
                    f"VALUES ({P}, {P}, 0, {P}) ON CONFLICT(user_id, week_start) DO UPDATE SET actual_amount = actual_amount + {P}",
                    (uid, week, pnl, pnl),
                )

        conn.commit()
    finally:
        put_db(conn)

    return jsonify({"ok": True, "id": entry_id, "pnl": pnl})


@app.route("/api/journal/<int:entry_id>", methods=["DELETE"])
@login_required
def api_journal_delete(entry_id):
    uid = _uid()
    row = query_one(f"SELECT pnl, created_at FROM journal_entries WHERE id = {P} AND user_id = {P}", (entry_id, uid))

    conn = get_db()
    try:
        if row and row["pnl"] is not None:
            entry_dt = datetime.fromisoformat(str(row["created_at"]))
            week = get_week_start(entry_dt)
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"UPDATE weekly_goals SET actual_amount = actual_amount - {P} WHERE user_id = {P} AND week_start = {P}",
                    (row["pnl"], uid, week),
                )
                cur.close()
            else:
                conn.execute(
                    f"UPDATE weekly_goals SET actual_amount = actual_amount - {P} WHERE user_id = {P} AND week_start = {P}",
                    (row["pnl"], uid, week),
                )
        if IS_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM journal_entries WHERE id = {P} AND user_id = {P}", (entry_id, uid))
            cur.close()
        else:
            conn.execute(f"DELETE FROM journal_entries WHERE id = {P} AND user_id = {P}", (entry_id, uid))
        conn.commit()
    finally:
        put_db(conn)
    return jsonify({"ok": True})


@app.route("/api/goals", methods=["GET"])
@login_required
def api_goals_get():
    uid = _uid()
    conn = get_db()
    try:
        if IS_POSTGRES:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT key, value FROM account_config WHERE user_id = {P}", (uid,))
            config_rows = cur.fetchall()
            cur.close()
        else:
            config_rows = conn.execute(f"SELECT key, value FROM account_config WHERE user_id = {P}", (uid,)).fetchall()
            config_rows = [dict(r) for r in config_rows]

        config = {r["key"]: r["value"] for r in config_rows}
        starting = float(config.get("starting_balance", 25000))
        current = float(config.get("current_balance", 25000))
        weekly_target = float(config.get("weekly_target", 500))

        week = get_week_start()
        week_row = query_one(
            f"SELECT * FROM weekly_goals WHERE user_id = {P} AND week_start = {P}", (uid, week)
        )
        weekly_actual = week_row["actual_amount"] if week_row else 0

        now = datetime.now()
        month_start = (now - timedelta(days=now.weekday() + 21)).strftime("%Y-%m-%d")
        month_row = query_one(
            f"SELECT SUM(actual_amount) as total FROM weekly_goals WHERE user_id = {P} AND week_start >= {P}",
            (uid, month_start),
        )
        monthly_actual = month_row["total"] if month_row and month_row["total"] else 0
        monthly_target = round(weekly_target * 4.33, 2)

        milestones_def = [50000, 100000, 250000, 500000, 750000, 1000000, 1250000, 1500000]
        milestones = [{"amount": m, "reached": current >= m, "label": f"${m:,.0f}"} for m in milestones_def]
        progress_pct = min(100, round((current - starting) / (1500000 - starting) * 100, 2)) if current > starting else 0
    finally:
        put_db(conn)

    return jsonify({
        "starting_balance": starting,
        "current_balance": current,
        "weekly_target": weekly_target,
        "weekly_actual": weekly_actual,
        "weekly_pct": round(weekly_actual / weekly_target * 100, 1) if weekly_target else 0,
        "week_start": week,
        "monthly_target": monthly_target,
        "monthly_actual": monthly_actual,
        "monthly_pct": round(monthly_actual / monthly_target * 100, 1) if monthly_target else 0,
        "milestones": milestones,
        "progress_pct": progress_pct,
    })


@app.route("/api/goals", methods=["POST"])
@login_required
def api_goals_update():
    uid = _uid()
    data = request.get_json()
    weekly_target = data.get("weekly_target")
    if weekly_target is None:
        return jsonify({"error": "weekly_target is required"}), 400

    if IS_POSTGRES:
        execute(
            f"INSERT INTO account_config (user_id, key, value) VALUES ({P}, 'weekly_target', {P}) "
            f"ON CONFLICT(user_id, key) DO UPDATE SET value = {P}",
            (uid, str(weekly_target), str(weekly_target)),
        )
    else:
        execute(
            f"INSERT INTO account_config (user_id, key, value) VALUES ({P}, 'weekly_target', {P}) "
            f"ON CONFLICT(user_id, key) DO UPDATE SET value = {P}",
            (uid, str(weekly_target), str(weekly_target)),
        )
    return jsonify({"ok": True})


@app.route("/api/goals/balance", methods=["POST"])
@login_required
def api_goals_balance():
    uid = _uid()
    data = request.get_json()
    balance = data.get("balance")
    if balance is None:
        return jsonify({"error": "balance is required"}), 400

    if IS_POSTGRES:
        execute(
            f"INSERT INTO account_config (user_id, key, value) VALUES ({P}, 'current_balance', {P}) "
            f"ON CONFLICT(user_id, key) DO UPDATE SET value = {P}",
            (uid, str(balance), str(balance)),
        )
    else:
        execute(
            f"INSERT INTO account_config (user_id, key, value) VALUES ({P}, 'current_balance', {P}) "
            f"ON CONFLICT(user_id, key) DO UPDATE SET value = {P}",
            (uid, str(balance), str(balance)),
        )
    return jsonify({"ok": True})


# ─── Status Service API ────────────────────────────────────────────────

@app.route("/api/status")
@login_required
def api_status():
    conn = get_db()
    try:
        services_out = []
        overall = "operational"
        for svc_key, svc_info in SERVICES.items():
            if IS_POSTGRES:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    f"SELECT status, response_time_ms, error_message, checked_at FROM service_checks WHERE service_name = {P} ORDER BY checked_at DESC LIMIT 1",
                    (svc_key,),
                )
                latest = cur.fetchone()
                cutoff = (datetime.now() - timedelta(days=90)).isoformat()
                cur.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P}", (svc_key, cutoff))
                total = cur.fetchone()["cnt"]
                cur.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND status = 'operational'", (svc_key, cutoff))
                ok = cur.fetchone()["cnt"]
                cur.close()
            else:
                latest = conn.execute(
                    f"SELECT status, response_time_ms, error_message, checked_at FROM service_checks WHERE service_name = {P} ORDER BY checked_at DESC LIMIT 1",
                    (svc_key,),
                ).fetchone()
                if latest:
                    latest = dict(latest)
                cutoff = (datetime.now() - timedelta(days=90)).isoformat()
                total = dict(conn.execute(
                    f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P}", (svc_key, cutoff)
                ).fetchone())["cnt"]
                ok = dict(conn.execute(
                    f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND status = 'operational'", (svc_key, cutoff)
                ).fetchone())["cnt"]

            uptime_pct = round(ok / total * 100, 2) if total > 0 else 100.0
            status = latest["status"] if latest else "unknown"
            if status == "outage":
                overall = "outage"
            elif status == "degraded" and overall != "outage":
                overall = "degraded"

            services_out.append({
                "key": svc_key, "name": svc_info["name"],
                "category": svc_info["category"], "status": status,
                "response_time_ms": latest["response_time_ms"] if latest else None,
                "error_message": latest["error_message"] if latest else None,
                "checked_at": latest["checked_at"] if latest else None,
                "uptime_pct": uptime_pct,
            })
    finally:
        put_db(conn)
    return jsonify({"overall": overall, "services": services_out})


@app.route("/api/status/uptime/<service>")
@login_required
def api_status_uptime(service):
    conn = get_db()
    try:
        days = []
        for i in range(89, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            day_start = f"{day} 00:00:00"
            day_end = f"{day} 23:59:59"
            if IS_POSTGRES:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND checked_at <= {P}", (service, day_start, day_end))
                total = cur.fetchone()["cnt"]
                cur.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND checked_at <= {P} AND status != 'operational'", (service, day_start, day_end))
                failed = cur.fetchone()["cnt"]
                cur.close()
            else:
                total = dict(conn.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND checked_at <= {P}", (service, day_start, day_end)).fetchone())["cnt"]
                failed = dict(conn.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND checked_at <= {P} AND status != 'operational'", (service, day_start, day_end)).fetchone())["cnt"]
            uptime_pct = round((total - failed) / total * 100, 1) if total > 0 else 100.0
            days.append({"date": day, "uptime_pct": uptime_pct, "total": total, "failed": failed})
    finally:
        put_db(conn)
    return jsonify(days)


@app.route("/api/status/incidents")
@login_required
def api_status_incidents():
    rows = query("SELECT * FROM service_incidents ORDER BY created_at DESC LIMIT 50")
    return jsonify([{
        "id": r["id"], "service_name": r["service_name"],
        "service_display": SERVICES.get(r["service_name"], {}).get("name", r["service_name"]),
        "incident_type": r["incident_type"], "started_at": r["started_at"],
        "resolved_at": r["resolved_at"], "duration_seconds": r["duration_seconds"],
        "error_message": r["error_message"],
    } for r in rows])


@app.route("/api/status/check", methods=["POST"])
@login_required
def api_status_force_check():
    run_check_cycle()
    return jsonify({"ok": True, "message": "Health check completed"})


# ─── Screener ────────────────────────────────────────────────────────

@app.route("/api/screener", methods=["POST"])
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
        return app.response_class(response=result_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": f"Screener failed: {str(e)}"}), 500


@app.route("/api/qullamaggie", methods=["POST"])
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
        return app.response_class(response=result_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": f"Qullamaggie scan failed: {str(e)}", "results": [], "scanned": 0}), 500


@app.route("/api/screener/hot-sectors")
@login_required
def api_hot_sectors():
    period = request.args.get("period", "1mo")
    valid = ["1w", "2w", "1mo", "3mo", "6mo", "1y"]
    if period not in valid:
        period = "1mo"
    return jsonify(get_hot_sectors(period))


# ─── Auto Trading Bot API ─────────────────────────────────────────────

@app.route("/api/bot/status")
@trader_required
def api_bot_status():
    bot = get_bot()
    return jsonify(bot.get_status())


@app.route("/api/bot/start", methods=["POST"])
@trader_required
def api_bot_start():
    bot = get_bot()
    if bot.is_running:
        return jsonify({"ok": False, "error": "Bot is already running"}), 400
    bot.start()
    return jsonify({"ok": True, "message": "Bot started"})


@app.route("/api/bot/stop", methods=["POST"])
@trader_required
def api_bot_stop():
    bot = get_bot()
    bot.stop()
    return jsonify({"ok": True, "message": "Bot stopped"})


@app.route("/api/bot/kill", methods=["POST"])
@trader_required
def api_bot_kill():
    bot = get_bot()
    bot.kill()
    return jsonify({"ok": True, "message": "Kill switch activated — bot stopped, positions closed"})


@app.route("/api/bot/trades")
@trader_required
def api_bot_trades():
    uid = _uid()
    status = request.args.get("status")
    asset_type = request.args.get("asset_type")
    limit = int(request.args.get("limit", 50))

    clauses = [f"user_id = {P}"]
    params = [uid]
    if status:
        clauses.append(f"status = {P}")
        params.append(status)
    if asset_type:
        clauses.append(f"asset_type = {P}")
        params.append(asset_type)

    where = " WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = query(f"SELECT * FROM bot_trades{where} ORDER BY opened_at DESC LIMIT {P}", params)

    return jsonify([{
        "id": r["id"], "coin": r["coin"], "side": r["side"],
        "size": r["size"], "entry_price": r["entry_price"],
        "exit_price": r["exit_price"], "pnl": r["pnl"],
        "pnl_pct": r["pnl_pct"], "status": r["status"],
        "signal_reason": r["signal_reason"], "strategy": r["strategy"],
        "asset_type": r["asset_type"], "direction_bias": r["direction_bias"],
        "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
        "opened_at": r["opened_at"], "closed_at": r["closed_at"],
        "blofin_order_id": r["blofin_order_id"],
    } for r in rows])


@app.route("/api/bot/trades/<int:trade_id>")
@trader_required
def api_bot_trade_detail(trade_id):
    uid = _uid()
    r = query_one(f"SELECT * FROM bot_trades WHERE id = {P} AND user_id = {P}", (trade_id, uid))
    if not r:
        return jsonify({"error": "Trade not found"}), 404
    return jsonify({
        "id": r["id"], "coin": r["coin"], "side": r["side"],
        "size": r["size"], "entry_price": r["entry_price"],
        "exit_price": r["exit_price"], "pnl": r["pnl"],
        "pnl_pct": r["pnl_pct"], "status": r["status"],
        "signal_reason": r["signal_reason"], "strategy": r["strategy"],
        "asset_type": r["asset_type"], "direction_bias": r["direction_bias"],
        "validation_result": json.loads(r["validation_result"]) if r["validation_result"] else None,
        "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
        "opened_at": r["opened_at"], "closed_at": r["closed_at"],
        "blofin_order_id": r["blofin_order_id"],
    })


@app.route("/api/bot/pnl")
@trader_required
def api_bot_pnl():
    uid = _uid()
    days = int(request.args.get("days", 30))
    rows = query(f"SELECT * FROM bot_daily_pnl WHERE user_id = {P} ORDER BY date DESC LIMIT {P}", (uid, days))
    pnl_data = [{
        "date": r["date"], "total_pnl": r["total_pnl"],
        "trade_count": r["trade_count"], "win_count": r["win_count"], "loss_count": r["loss_count"],
    } for r in rows]
    pnl_data.reverse()
    return jsonify(pnl_data)


@app.route("/api/bot/pnl-summary")
@trader_required
def api_bot_pnl_summary():
    return jsonify(_pnl_summary("crypto"))


def _pnl_summary(asset_type):
    uid = _uid()
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")

    base = (
        f"SELECT COALESCE(SUM(pnl),0) as total, COUNT(*) as trades, "
        f"SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins "
        f"FROM bot_trades WHERE status='closed' AND user_id={P} AND asset_type={P}"
    )

    def _q(extra_where, params):
        row = query_one(f"{base} {extra_where}", params)
        return {"pnl": round(row["total"], 2), "trades": row["trades"], "wins": row["wins"] or 0}

    return {
        "day": _q(f"AND DATE(closed_at)={P}", (uid, asset_type, today)),
        "week": _q(f"AND closed_at>={P}", (uid, asset_type, week_ago)),
        "month": _q(f"AND closed_at>={P}", (uid, asset_type, month_ago)),
        "year": _q(f"AND closed_at>={P}", (uid, asset_type, year_ago)),
        "all": _q("", (uid, asset_type)),
    }


@app.route("/api/bot/platform", methods=["GET", "POST"])
@trader_required
def api_bot_platform():
    uid = _uid()
    if request.method == "GET":
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'platform'", (uid,))
        platform = row["value"] if row else "blofin"
        # Check user's encrypted keys
        from crypto_utils import decrypt
        keys_status = {}
        for kn in ["api_key", "api_secret", "passphrase"]:
            kr = query_one(
                f"SELECT encrypted_value FROM user_api_keys WHERE user_id = {P} AND provider = 'blofin' AND key_name = {P}",
                (uid, kn),
            )
            keys_status[f"has_{kn}"] = kr is not None
        return jsonify({
            "platform": platform,
            **keys_status,
            "configured": all(keys_status.values()),
        })

    # POST — save platform + keys
    data = request.get_json()
    platform = data.get("platform", "blofin")
    _upsert_bot_config(uid, "platform", platform)

    api_key = data.get("api_key", "").strip()
    api_secret = data.get("api_secret", "").strip()
    passphrase = data.get("passphrase", "").strip()

    from crypto_utils import encrypt
    for kn, val in [("api_key", api_key), ("api_secret", api_secret), ("passphrase", passphrase)]:
        if val:
            _upsert_api_key(uid, "blofin", kn, encrypt(val))

    return jsonify({"ok": True, "platform": platform})


@app.route("/api/bot/test-connection", methods=["POST"])
@trader_required
def api_bot_test_connection():
    try:
        bot = get_bot()
        bot.client._initialized = False
        bot.client._client = None
        balance = bot.client.get_balance()
        if "error" in balance:
            return jsonify({"ok": False, "error": balance["error"]})
        return jsonify({
            "ok": True,
            "balance": balance.get("total_equity", 0),
            "message": f"Connected! Balance: ${balance.get('total_equity', 0):,.2f}",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/bot/check-permissions", methods=["POST"])
@trader_required
def api_bot_check_permissions():
    try:
        bot = get_bot()
        result = bot.client.check_trade_permission()
        return jsonify(result)
    except Exception as e:
        return jsonify({"has_trade_permission": False, "message": str(e)})


@app.route("/api/bot/test-trade", methods=["POST"])
@trader_required
def api_bot_test_trade():
    try:
        bot = get_bot()
        data = request.get_json() or {}
        coin = data.get("coin", "BTC-USDT")
        side = data.get("side", "buy")
        coin_info = COIN_MAP.get(coin)
        if not coin_info:
            return jsonify({"ok": False, "error": f"Unknown coin: {coin}"})
        inst_id = coin_info["blofin"]
        info = bot.client.get_instrument_info(inst_id)
        min_contracts = info["min_size"]
        result = bot.client.place_order(inst_id=inst_id, side=side, size=min_contracts, size_in_contracts=True)
        if result.get("success"):
            return jsonify({
                "ok": True,
                "message": f"Test trade placed: {side} {min_contracts} contracts {inst_id}",
                "order_id": result.get("order_id", ""),
                "raw": str(result.get("raw", "")),
            })
        else:
            return jsonify({"ok": False, "error": result.get("error", "Unknown error"), "code": result.get("code", "")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/bot/coins", methods=["GET", "POST"])
@trader_required
def api_bot_coins():
    uid = _uid()
    if request.method == "GET":
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'selected_coins'", (uid,))
        selected = json.loads(row["value"]) if row else list(DEFAULT_COINS)
        coins = [{"key": k, "name": v["name"], "blofin": v["blofin"], "selected": k in selected} for k, v in COIN_MAP.items()]
        return jsonify({"coins": coins, "selected": selected})

    data = request.get_json()
    selected = data.get("selected", [])
    valid = [c for c in selected if c in COIN_MAP]
    if not valid:
        return jsonify({"error": "At least one valid coin must be selected"}), 400
    _upsert_bot_config(uid, "selected_coins", json.dumps(valid))
    return jsonify({"ok": True, "selected": valid})


@app.route("/api/bot/config", methods=["GET", "POST"])
@trader_required
def api_bot_config():
    uid = _uid()
    if request.method == "GET":
        rows = query(f"SELECT key, value FROM bot_config WHERE user_id = {P}", (uid,))
        return jsonify({r["key"]: r["value"] for r in rows})

    data = request.get_json()
    allowed_keys = {"max_position_pct", "daily_loss_limit", "max_open_positions", "scan_interval_sec", "daily_goal", "trading_mode", "direction_bias"}
    for k, v in data.items():
        if k in allowed_keys:
            _upsert_bot_config(uid, k, str(v))
    return jsonify({"ok": True})


@app.route("/api/bot/log")
@trader_required
def api_bot_log():
    uid = _uid()
    level = request.args.get("level")
    limit = int(request.args.get("limit", 100))

    if level:
        rows = query(
            f"SELECT * FROM bot_log WHERE user_id = {P} AND level = {P} ORDER BY created_at DESC LIMIT {P}",
            (uid, level, limit),
        )
    else:
        rows = query(
            f"SELECT * FROM bot_log WHERE user_id = {P} ORDER BY created_at DESC LIMIT {P}",
            (uid, limit),
        )
    return jsonify([{
        "id": r["id"], "level": r["level"], "message": r["message"],
        "details": r["details"], "created_at": r["created_at"],
    } for r in rows])


@app.route("/api/bot/trending")
@trader_required
def api_bot_trending():
    uid = _uid()
    try:
        result = get_trending_crypto()
        added_coins = []
        try:
            market_data = result.get("market_data", [])
            high_vol_tickers = [d["ticker"] for d in market_data if d.get("vol_surge", 0) >= 1.5 and d["ticker"] in COIN_MAP]
            if high_vol_tickers:
                row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'selected_coins'", (uid,))
                current_selected = json.loads(row["value"]) if row else list(DEFAULT_COINS)
                for ticker in high_vol_tickers:
                    if ticker not in current_selected:
                        current_selected.append(ticker)
                        added_coins.append(ticker)
                if added_coins:
                    _upsert_bot_config(uid, "selected_coins", json.dumps(current_selected))
        except Exception as e:
            print(f"[WARNING] Failed to auto-add trending coins: {e}")

        result["auto_added_coins"] = added_coins
        result_json = json.dumps(result, cls=NumpyEncoder, default=str)
        return app.response_class(response=result_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": f"Trending analysis failed: {str(e)}"}), 500


# ─── Stock Trading Bot API ─────────────────────────────────────────────

@app.route("/api/stock-bot/status")
@trader_required
def api_stock_bot_status():
    bot = get_stock_bot()
    return jsonify(bot.get_status())


@app.route("/api/stock-bot/start", methods=["POST"])
@trader_required
def api_stock_bot_start():
    bot = get_stock_bot()
    if bot.is_running:
        return jsonify({"ok": False, "error": "Stock bot is already running"}), 400
    bot.start()
    return jsonify({"ok": True, "message": "Stock bot started"})


@app.route("/api/stock-bot/stop", methods=["POST"])
@trader_required
def api_stock_bot_stop():
    bot = get_stock_bot()
    bot.stop()
    return jsonify({"ok": True, "message": "Stock bot stopped"})


@app.route("/api/stock-bot/kill", methods=["POST"])
@trader_required
def api_stock_bot_kill():
    bot = get_stock_bot()
    bot.kill()
    return jsonify({"ok": True, "message": "Kill switch activated — stock bot stopped, positions closed"})


@app.route("/api/stock-bot/trades")
@trader_required
def api_stock_bot_trades():
    uid = _uid()
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))

    clauses = [f"user_id = {P}", "asset_type = 'stock'"]
    params = [uid]
    if status:
        clauses.append(f"status = {P}")
        params.append(status)

    where = " WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = query(f"SELECT * FROM bot_trades{where} ORDER BY opened_at DESC LIMIT {P}", params)

    return jsonify([{
        "id": r["id"], "coin": r["coin"], "side": r["side"],
        "size": r["size"], "entry_price": r["entry_price"],
        "exit_price": r["exit_price"], "pnl": r["pnl"],
        "pnl_pct": r["pnl_pct"], "status": r["status"],
        "signal_reason": r["signal_reason"], "strategy": r["strategy"],
        "asset_type": r["asset_type"], "direction_bias": r["direction_bias"],
        "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
        "opened_at": r["opened_at"], "closed_at": r["closed_at"],
        "blofin_order_id": r["blofin_order_id"],
    } for r in rows])


@app.route("/api/stock-bot/trades/<int:trade_id>")
@trader_required
def api_stock_bot_trade_detail(trade_id):
    uid = _uid()
    r = query_one(f"SELECT * FROM bot_trades WHERE id = {P} AND user_id = {P} AND asset_type = 'stock'", (trade_id, uid))
    if not r:
        return jsonify({"error": "Trade not found"}), 404
    return jsonify({
        "id": r["id"], "coin": r["coin"], "side": r["side"],
        "size": r["size"], "entry_price": r["entry_price"],
        "exit_price": r["exit_price"], "pnl": r["pnl"],
        "pnl_pct": r["pnl_pct"], "status": r["status"],
        "signal_reason": r["signal_reason"], "strategy": r["strategy"],
        "asset_type": r["asset_type"], "direction_bias": r["direction_bias"],
        "validation_result": json.loads(r["validation_result"]) if r["validation_result"] else None,
        "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
        "opened_at": r["opened_at"], "closed_at": r["closed_at"],
    })


@app.route("/api/stock-bot/trades/<int:trade_id>/close", methods=["POST"])
@trader_required
def api_stock_bot_close_trade(trade_id):
    uid = _uid()
    r = query_one(
        f"SELECT * FROM bot_trades WHERE id = {P} AND user_id = {P} AND asset_type = 'stock' AND status = 'open'",
        (trade_id, uid),
    )
    if not r:
        return jsonify({"ok": False, "error": "Open stock trade not found"}), 404
    bot = get_stock_bot()
    current_price = bot.client.get_ticker_price(r["coin"])
    if current_price <= 0:
        return jsonify({"ok": False, "error": "Could not get current price"}), 500
    bot._close_trade(r, current_price, "Manual close")
    return jsonify({"ok": True, "message": f"Trade {trade_id} closed at ${current_price:,.2f}"})


@app.route("/api/bot/trades/<int:trade_id>/close", methods=["POST"])
@trader_required
def api_bot_close_trade(trade_id):
    uid = _uid()
    r = query_one(f"SELECT * FROM bot_trades WHERE id = {P} AND user_id = {P} AND status = 'open'", (trade_id, uid))
    if not r:
        return jsonify({"ok": False, "error": "Open trade not found"}), 404
    bot = get_bot()
    coin_info = COIN_MAP.get(r["coin"])
    current_price = bot.client.get_ticker_price(coin_info["blofin"]) if coin_info else 0
    if current_price <= 0:
        return jsonify({"ok": False, "error": "Could not get current price"}), 500
    bot._close_trade(r, current_price, "Manual close")
    return jsonify({"ok": True, "message": f"Trade {trade_id} closed at ${current_price:,.2f}"})


@app.route("/api/stock-bot/pnl")
@trader_required
def api_stock_bot_pnl():
    uid = _uid()
    days = int(request.args.get("days", 30))
    rows = query(f"SELECT * FROM stock_daily_pnl WHERE user_id = {P} ORDER BY date DESC LIMIT {P}", (uid, days))
    pnl_data = [{
        "date": r["date"], "total_pnl": r["total_pnl"],
        "trade_count": r["trade_count"], "win_count": r["win_count"], "loss_count": r["loss_count"],
    } for r in rows]
    pnl_data.reverse()
    return jsonify(pnl_data)


@app.route("/api/stock-bot/pnl-summary")
@trader_required
def api_stock_bot_pnl_summary():
    return jsonify(_pnl_summary("stock"))


@app.route("/api/stock-bot/config", methods=["GET", "POST"])
@trader_required
def api_stock_bot_config():
    uid = _uid()
    if request.method == "GET":
        rows = query(f"SELECT key, value FROM bot_config WHERE user_id = {P} AND key LIKE 'stock_%%'", (uid,))
        return jsonify({r["key"]: r["value"] for r in rows})

    data = request.get_json()
    allowed_keys = {
        "stock_max_position_pct", "stock_daily_loss_limit", "stock_max_open_positions",
        "stock_scan_interval_sec", "stock_daily_goal", "stock_direction_bias",
        "stock_extended_hours",
    }
    for k, v in data.items():
        if k in allowed_keys:
            _upsert_bot_config(uid, k, str(v))
    return jsonify({"ok": True})


@app.route("/api/stock-bot/stocks", methods=["GET", "POST"])
@trader_required
def api_stock_bot_stocks():
    uid = _uid()
    if request.method == "GET":
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'stock_selected_stocks'", (uid,))
        selected = json.loads(row["value"]) if row else list(DEFAULT_STOCKS)
        custom_row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'stock_custom_tickers'", (uid,))
        custom_tickers = json.loads(custom_row["value"]) if custom_row else {}
        stocks = []
        seen = set()
        for key, info in STOCK_MAP.items():
            stocks.append({"key": key, "name": info["name"], "selected": key in selected})
            seen.add(key)
        for sym, name in custom_tickers.items():
            if sym not in seen:
                stocks.append({"key": sym, "name": name, "selected": sym in selected, "custom": True})
                seen.add(sym)
        for sym in selected:
            if sym not in seen:
                stocks.append({"key": sym, "name": sym, "selected": True, "custom": True})
        return jsonify({"stocks": stocks, "selected": selected})

    data = request.get_json()
    selected = data.get("selected", [])
    valid = [s.upper().strip() for s in selected if s.strip()]
    if not valid:
        return jsonify({"error": "At least one stock must be selected"}), 400
    _upsert_bot_config(uid, "stock_selected_stocks", json.dumps(valid))
    return jsonify({"ok": True, "selected": valid})


@app.route("/api/stock-bot/stocks/add", methods=["POST"])
@trader_required
def api_stock_bot_stocks_add():
    uid = _uid()
    data = request.get_json()
    symbol = (data.get("symbol") or "").upper().strip()
    if not symbol:
        return jsonify({"error": "Symbol is required"}), 400
    if len(symbol) > 10:
        return jsonify({"error": "Invalid symbol"}), 400
    if symbol in STOCK_MAP:
        return jsonify({"valid": True, "symbol": symbol, "name": STOCK_MAP[symbol]["name"], "already_known": True})
    result = validate_ticker(symbol)
    if not result["valid"]:
        return jsonify(result), 400

    row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'stock_custom_tickers'", (uid,))
    custom = json.loads(row["value"]) if row else {}
    custom[symbol] = result["name"]
    _upsert_bot_config(uid, "stock_custom_tickers", json.dumps(custom))
    return jsonify({"valid": True, "symbol": symbol, "name": result["name"], "price": result.get("price", 0)})


@app.route("/api/stock-bot/log")
@trader_required
def api_stock_bot_log():
    uid = _uid()
    level = request.args.get("level")
    limit = int(request.args.get("limit", 100))

    clauses = [f"user_id = {P}", "source = 'stock'"]
    params = [uid]
    if level:
        clauses.append(f"level = {P}")
        params.append(level)

    where = " WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = query(f"SELECT * FROM bot_log{where} ORDER BY created_at DESC LIMIT {P}", params)

    return jsonify([{
        "id": r["id"], "level": r["level"], "message": r["message"],
        "details": r["details"], "created_at": r["created_at"],
    } for r in rows])


@app.route("/api/stock-bot/broker", methods=["GET", "POST"])
@trader_required
def api_stock_bot_broker():
    uid = _uid()
    if request.method == "GET":
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'stock_broker'", (uid,))
        broker = row["value"] if row else "alpaca"

        keys_status = {}
        if broker == "webull":
            for kn in ["app_key", "app_secret", "account_id"]:
                kr = query_one(
                    f"SELECT encrypted_value FROM user_api_keys WHERE user_id = {P} AND provider = 'webull' AND key_name = {P}",
                    (uid, kn),
                )
                keys_status[f"has_{kn}"] = kr is not None
        else:
            for kn in ["api_key", "secret_key"]:
                kr = query_one(
                    f"SELECT encrypted_value FROM user_api_keys WHERE user_id = {P} AND provider = 'alpaca' AND key_name = {P}",
                    (uid, kn),
                )
                keys_status[f"has_{kn}"] = kr is not None

        return jsonify({"broker": broker, **keys_status, "configured": all(keys_status.values())})

    data = request.get_json()
    broker = data.get("broker", "alpaca")
    _upsert_bot_config(uid, "stock_broker", broker)

    from crypto_utils import encrypt
    api_key = data.get("api_key", "").strip()
    api_secret = data.get("api_secret", "").strip()
    account_id = data.get("account_id", "").strip()

    if broker == "webull":
        if api_key:
            _upsert_api_key(uid, "webull", "app_key", encrypt(api_key))
        if api_secret:
            _upsert_api_key(uid, "webull", "app_secret", encrypt(api_secret))
        if account_id:
            _upsert_api_key(uid, "webull", "account_id", encrypt(account_id))
    else:
        if api_key:
            _upsert_api_key(uid, "alpaca", "api_key", encrypt(api_key))
        if api_secret:
            _upsert_api_key(uid, "alpaca", "secret_key", encrypt(api_secret))

    try:
        bot = get_stock_bot()
        if not bot.is_running:
            bot.switch_broker(broker)
    except Exception:
        pass

    return jsonify({"ok": True, "broker": broker})


@app.route("/api/stock-bot/test-connection", methods=["POST"])
@trader_required
def api_stock_bot_test_connection():
    try:
        bot = get_stock_bot()
        bot.client._initialized = False
        if hasattr(bot.client, '_client'):
            bot.client._client = None
        balance = bot.client.get_balance()
        if "error" in balance:
            return jsonify({"ok": False, "error": balance["error"]})
        broker_label = bot.broker_name.capitalize()
        return jsonify({
            "ok": True,
            "balance": balance.get("total_equity", 0),
            "message": f"Connected to {broker_label}! Balance: ${balance.get('total_equity', 0):,.2f}",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/stock-bot/market-hours")
@trader_required
def api_stock_bot_market_hours():
    return jsonify(is_market_open())


# ─── Settings ──────────────────────────────────────────────────────────

@app.route("/api/settings")
@login_required
def api_settings_get():
    """Return settings. User keys are per-user, system LLM config is admin-only."""
    uid = _uid()

    # Check which user keys are configured
    user_keys = {}
    for provider, key_names in [
        ("blofin", ["api_key", "api_secret", "passphrase"]),
        ("alpaca", ["api_key", "secret_key"]),
        ("webull", ["app_key", "app_secret", "account_id"]),
    ]:
        for kn in key_names:
            kr = query_one(
                f"SELECT encrypted_value FROM user_api_keys WHERE user_id = {P} AND provider = {P} AND key_name = {P}",
                (uid, provider, kn),
            )
            user_keys[f"{provider}_{kn}"] = {"configured": kr is not None, "masked_value": "****" if kr else ""}

    result = {"user_keys": user_keys}

    # System config (LLM models) — visible to all, editable by admin
    import ai_validator as av
    import crypto_bot.crypto_validator as cv

    result["llm_models"] = {
        "LLM_RESEARCH": av.LLM_RESEARCH,
        "LLM_RESEARCH_FAST": av.LLM_RESEARCH_FAST,
        "LLM_PATTERN": av.LLM_PATTERN,
        "LLM_PREDICTION": av.LLM_PREDICTION,
        "LLM_SCREENER": av.LLM_SCREENER,
        "LLM_BOT_SENTIMENT": cv.LLM_BOT_SENTIMENT,
        "LLM_BOT_RISK": cv.LLM_BOT_RISK,
    }
    result["llm_settings"] = {
        "LLM_MAX_TOKENS": av.LLM_MAX_TOKENS,
        "LLM_TEMPERATURE": av.LLM_TEMPERATURE,
        "LLM_FAST_MODE": av.LLM_FAST_MODE,
    }
    result["openrouter_configured"] = av.is_configured()

    return jsonify(result)


@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings_save():
    """Save user API keys (encrypted per user)."""
    uid = _uid()
    data = request.get_json()

    from crypto_utils import encrypt

    # User broker keys
    user_keys = data.get("user_keys", {})
    key_map = {
        "BLOFIN_API_KEY": ("blofin", "api_key"),
        "BLOFIN_API_SECRET": ("blofin", "api_secret"),
        "BLOFIN_PASSPHRASE": ("blofin", "passphrase"),
        "ALPACA_API_KEY": ("alpaca", "api_key"),
        "ALPACA_SECRET_KEY": ("alpaca", "secret_key"),
        "WEBULL_APP_KEY": ("webull", "app_key"),
        "WEBULL_APP_SECRET": ("webull", "app_secret"),
        "WEBULL_ACCOUNT_ID": ("webull", "account_id"),
    }

    updated = []
    for field, (provider, key_name) in key_map.items():
        val = user_keys.get(field, "").strip()
        if val:
            _upsert_api_key(uid, provider, key_name, encrypt(val))
            updated.append(field)

    return jsonify({"ok": True, "updated": updated})


# ─── Admin API ─────────────────────────────────────────────────────────

@app.route("/api/admin/config", methods=["GET", "POST"])
@admin_required
def api_admin_config():
    """Admin-only: manage system LLM/OpenRouter config."""
    if request.method == "GET":
        import ai_validator as av
        import crypto_bot.crypto_validator as cv

        def mask(val):
            if not val or val == "your_openrouter_api_key_here":
                return ""
            if len(val) <= 8:
                return "****"
            return val[:4] + "****" + val[-4:]

        return jsonify({
            "openrouter_key": mask(av.OPENROUTER_API_KEY),
            "openrouter_configured": av.is_configured(),
            "llm_models": {
                "LLM_RESEARCH": av.LLM_RESEARCH,
                "LLM_RESEARCH_FAST": av.LLM_RESEARCH_FAST,
                "LLM_PATTERN": av.LLM_PATTERN,
                "LLM_PREDICTION": av.LLM_PREDICTION,
                "LLM_SCREENER": av.LLM_SCREENER,
                "LLM_BOT_SENTIMENT": cv.LLM_BOT_SENTIMENT,
                "LLM_BOT_RISK": cv.LLM_BOT_RISK,
            },
            "llm_settings": {
                "LLM_MAX_TOKENS": av.LLM_MAX_TOKENS,
                "LLM_TEMPERATURE": av.LLM_TEMPERATURE,
                "LLM_FAST_MODE": av.LLM_FAST_MODE,
            },
            "ollama": {
                "OLLAMA_URL": cv.OLLAMA_URL,
                "OLLAMA_MODEL": cv.OLLAMA_MODEL,
            },
        })

    # POST — update system config (Railway env vars hot-reload)
    data = request.get_json()
    env_updates = {}

    for field in ["OPENROUTER_API_KEY"]:
        val = data.get(field, "").strip()
        if val:
            env_updates[field] = val

    models = data.get("llm_models", {})
    for field in ["LLM_RESEARCH", "LLM_RESEARCH_FAST", "LLM_PATTERN", "LLM_PREDICTION", "LLM_SCREENER", "LLM_BOT_SENTIMENT", "LLM_BOT_RISK"]:
        val = models.get(field, "").strip()
        if val:
            env_updates[field] = val

    settings = data.get("llm_settings", {})
    if "LLM_MAX_TOKENS" in settings and settings["LLM_MAX_TOKENS"]:
        env_updates["LLM_MAX_TOKENS"] = str(int(settings["LLM_MAX_TOKENS"]))
    if "LLM_TEMPERATURE" in settings and settings["LLM_TEMPERATURE"] is not None:
        env_updates["LLM_TEMPERATURE"] = str(float(settings["LLM_TEMPERATURE"]))
    if "LLM_FAST_MODE" in settings:
        env_updates["LLM_FAST_MODE"] = "1" if settings["LLM_FAST_MODE"] else "0"

    ollama = data.get("ollama", {})
    for field in ["OLLAMA_URL", "OLLAMA_MODEL"]:
        val = ollama.get(field, "").strip()
        if val:
            env_updates[field] = val

    if env_updates:
        # Update os.environ and hot-reload module globals
        for k, v in env_updates.items():
            os.environ[k] = v
        _reload_module_globals(env_updates)

    return jsonify({"ok": True, "updated": list(env_updates.keys())})


@app.route("/api/admin/invite", methods=["GET", "POST"])
@admin_required
def api_admin_invite():
    if request.method == "GET":
        rows = query("SELECT * FROM invites ORDER BY invited_at DESC")
        return jsonify([dict(r) for r in rows])

    data = request.get_json()
    email = (data.get("email") or "").strip().lower()
    role = data.get("role", "trader")
    if not email:
        return jsonify({"error": "Email is required"}), 400
    if role not in ("trader", "admin", "user"):
        return jsonify({"error": "Invalid role"}), 400

    uid = _uid()
    if IS_POSTGRES:
        execute(
            f"INSERT INTO invites (email, role, invited_by) VALUES ({P}, {P}, {P}) "
            f"ON CONFLICT(email) DO UPDATE SET role = {P}",
            (email, role, uid, role),
        )
    else:
        execute(
            f"INSERT OR REPLACE INTO invites (email, role, invited_by) VALUES ({P}, {P}, {P})",
            (email, role, uid),
        )
    return jsonify({"ok": True, "email": email, "role": role})


@app.route("/api/admin/invite/<email>", methods=["DELETE"])
@admin_required
def api_admin_invite_delete(email):
    execute(f"DELETE FROM invites WHERE email = {P}", (email.lower(),))
    return jsonify({"ok": True})


@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    rows = query("SELECT * FROM users ORDER BY created_at DESC")
    users = []
    for u in rows:
        roles = query(f"SELECT role FROM user_roles WHERE user_id = {P}", (u["id"],))
        users.append({
            "id": u["id"], "email": u["email"], "name": u["name"],
            "picture_url": u["picture_url"],
            "created_at": str(u["created_at"]),
            "last_login": str(u["last_login"]) if u["last_login"] else None,
            "roles": [r["role"] for r in roles],
        })
    return jsonify(users)


@app.route("/api/admin/users/<int:user_id>/role", methods=["POST"])
@admin_required
def api_admin_user_role(user_id):
    data = request.get_json()
    role = data.get("role")
    action = data.get("action", "add")

    if role not in ("admin", "trader", "user"):
        return jsonify({"error": "Invalid role"}), 400

    uid = _uid()
    if action == "remove":
        execute(f"DELETE FROM user_roles WHERE user_id = {P} AND role = {P}", (user_id, role))
    else:
        if IS_POSTGRES:
            execute(
                f"INSERT INTO user_roles (user_id, role, granted_by) VALUES ({P}, {P}, {P}) ON CONFLICT DO NOTHING",
                (user_id, role, uid),
            )
        else:
            execute(
                f"INSERT OR IGNORE INTO user_roles (user_id, role, granted_by) VALUES ({P}, {P}, {P})",
                (user_id, role, uid),
            )
    return jsonify({"ok": True})


# ─── Helpers ────────────────────────────────────────────────────────────

def _upsert_bot_config(user_id, key, value):
    if IS_POSTGRES:
        execute(
            f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, {P}, {P}) "
            f"ON CONFLICT(user_id, key) DO UPDATE SET value = {P}",
            (user_id, key, value, value),
        )
    else:
        execute(
            f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, {P}, {P})",
            (user_id, key, value),
        )


def _upsert_api_key(user_id, provider, key_name, encrypted_value):
    if IS_POSTGRES:
        execute(
            f"INSERT INTO user_api_keys (user_id, provider, key_name, encrypted_value, updated_at) "
            f"VALUES ({P}, {P}, {P}, {P}, NOW()) "
            f"ON CONFLICT(user_id, provider, key_name) DO UPDATE SET encrypted_value = {P}, updated_at = NOW()",
            (user_id, provider, key_name, encrypted_value, encrypted_value),
        )
    else:
        execute(
            f"INSERT OR REPLACE INTO user_api_keys (user_id, provider, key_name, encrypted_value, updated_at) "
            f"VALUES ({P}, {P}, {P}, {P}, datetime('now'))",
            (user_id, provider, key_name, encrypted_value),
        )


def _reload_module_globals(updated_keys: dict):
    """Hot-reload module-level globals after config update."""
    import ai_validator as av
    import crypto_bot.crypto_validator as cv
    import screener as scr

    for key, val in updated_keys.items():
        if key == "OPENROUTER_API_KEY":
            av.OPENROUTER_API_KEY = val
            av.HEADERS["Authorization"] = f"Bearer {val}"
            cv.OPENROUTER_API_KEY = val
            cv.HEADERS["Authorization"] = f"Bearer {val}"
        elif key == "LLM_RESEARCH":
            av.LLM_RESEARCH = val
        elif key == "LLM_RESEARCH_FAST":
            av.LLM_RESEARCH_FAST = val
        elif key == "LLM_PATTERN":
            av.LLM_PATTERN = val
        elif key == "LLM_PREDICTION":
            av.LLM_PREDICTION = val
        elif key == "LLM_SCREENER":
            av.LLM_SCREENER = val
            scr.LLM_SCREENER = val
        elif key == "LLM_BOT_SENTIMENT":
            cv.LLM_BOT_SENTIMENT = val
        elif key == "LLM_BOT_RISK":
            cv.LLM_BOT_RISK = val
        elif key == "LLM_MAX_TOKENS":
            av.LLM_MAX_TOKENS = int(val)
        elif key == "LLM_TEMPERATURE":
            av.LLM_TEMPERATURE = float(val)
        elif key == "LLM_FAST_MODE":
            av.LLM_FAST_MODE = val == "1"
        elif key == "OLLAMA_URL":
            cv.OLLAMA_URL = val
        elif key == "OLLAMA_MODEL":
            cv.OLLAMA_MODEL = val


if __name__ == "__main__":
    init_db()
    start_background_checker()
    app.run(debug=True, port=5001)
