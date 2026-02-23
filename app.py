"""
AI Stock Analyst — Web Application
Flask server with search history (3-day expiry) and analysis API.
"""

import json
import sqlite3
import os
import numpy as np
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
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
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "searches.db")


def get_db():
    """Get SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            period TEXT NOT NULL,
            interval_val TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ticker ON searches(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON searches(expires_at)")

    # Tracker tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            notes TEXT DEFAULT '',
            action TEXT,
            entry_price REAL,
            exit_price REAL,
            shares REAL,
            pnl REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            target_amount REAL NOT NULL,
            actual_amount REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(week_start)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Seed defaults if not present
    conn.execute("INSERT OR IGNORE INTO account_config (key, value) VALUES ('starting_balance', '25000')")
    conn.execute("INSERT OR IGNORE INTO account_config (key, value) VALUES ('current_balance', '25000')")
    conn.execute("INSERT OR IGNORE INTO account_config (key, value) VALUES ('weekly_target', '500')")

    # Status service tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT NOT NULL,
            status TEXT NOT NULL,
            response_time_ms INTEGER DEFAULT 0,
            error_message TEXT,
            checked_at TIMESTAMP NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sc_service ON service_checks(service_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sc_checked ON service_checks(checked_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT NOT NULL,
            incident_type TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            resolved_at TIMESTAMP,
            duration_seconds INTEGER,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ─── Bot tables ──────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Seed bot defaults
    for k, v in [
        ("bot_enabled", "0"), ("kill_switch", "0"),
        ("max_position_pct", "10"), ("daily_loss_limit", "250"),
        ("max_open_positions", "3"), ("scan_interval_sec", "60"),
        ("selected_coins", json.dumps(DEFAULT_COINS)),
        ("daily_goal", "50"), ("platform", "blofin"), ("trading_mode", "futures"),
    ]:
        conn.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)", (k, v))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            pnl_pct REAL,
            status TEXT NOT NULL DEFAULT 'open',
            signal_reason TEXT,
            validation_result TEXT,
            stop_loss REAL,
            take_profit REAL,
            opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP,
            blofin_order_id TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_daily_pnl (
            date TEXT PRIMARY KEY,
            total_pnl REAL DEFAULT 0,
            trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Idempotent schema migration — add new columns to bot_trades
    for col_sql in [
        "ALTER TABLE bot_trades ADD COLUMN strategy TEXT",
        "ALTER TABLE bot_trades ADD COLUMN asset_type TEXT DEFAULT 'crypto'",
        "ALTER TABLE bot_trades ADD COLUMN direction_bias TEXT",
    ]:
        try:
            conn.execute(col_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.commit()
    conn.close()

    # Purge old status checks on startup
    purge_old_checks(90)


def cleanup_expired():
    """Remove searches older than 3 days."""
    conn = get_db()
    conn.execute("DELETE FROM searches WHERE expires_at < ?", (datetime.now().isoformat(),))
    conn.commit()
    conn.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
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

    # For intraday intervals, limit period
    if interval in ["1h", "4h"] and period not in ["1mo", "3mo"]:
        period = "3mo"

    # Map 4h to valid yfinance interval
    yf_interval = "60m" if interval == "1h" else interval
    if interval == "4h":
        yf_interval = "1d"  # yfinance doesn't support 4h; we'll resample

    try:
        result = analyze_ticker(ticker, period, yf_interval)

        # Serialize with numpy handling
        result_json = json.dumps(result, cls=NumpyEncoder, default=str)

        # Save to history
        conn = get_db()
        expires = datetime.now() + timedelta(days=3)
        conn.execute(
            "INSERT INTO searches (ticker, period, interval_val, result_json, expires_at) VALUES (?, ?, ?, ?, ?)",
            (ticker, period, interval, result_json, expires.isoformat()),
        )
        conn.commit()
        conn.close()

        return app.response_class(
            response=result_json,
            status=200,
            mimetype='application/json'
        )

    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500


@app.route("/api/validate", methods=["POST"])
def api_validate():
    """Run AI validation on a completed analysis."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Analysis data is required"}), 400

    if not is_configured():
        return jsonify({
            "configured": False,
            "error": "OpenRouter API key not configured. Add your key to the .env file.",
        }), 200

    try:
        fast_mode = data.pop("fast_mode", None)
        result = validate_setup(data, fast_mode=fast_mode)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"AI validation failed: {str(e)}"}), 500


@app.route("/api/predict", methods=["POST"])
def api_predict():
    """Run 12-month investment prediction for a ticker."""
    data = request.get_json()
    ticker = data.get("ticker", "").strip().upper()

    if not ticker:
        return jsonify({"error": "Ticker symbol is required"}), 400

    if not is_configured():
        return jsonify({
            "configured": False,
            "error": "OpenRouter API key not configured. Add your key to the .env file.",
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
        return app.response_class(
            response=result_json,
            status=200,
            mimetype='application/json'
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Prediction failed: {str(e)}"}), 500


@app.route("/api/ai-status")
def api_ai_status():
    """Check if AI validation is available."""
    return jsonify({"configured": is_configured()})


@app.route("/api/history")
def api_history():
    """Get search history (non-expired)."""
    cleanup_expired()
    conn = get_db()
    rows = conn.execute(
        "SELECT id, ticker, period, interval_val, created_at, expires_at FROM searches ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    conn.close()

    history = []
    for row in rows:
        history.append({
            "id": row["id"],
            "ticker": row["ticker"],
            "period": row["period"],
            "interval": row["interval_val"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        })
    return jsonify(history)


@app.route("/api/history/<int:search_id>")
def api_history_detail(search_id):
    """Get a specific search result."""
    cleanup_expired()
    conn = get_db()
    row = conn.execute("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Search not found or expired"}), 404

    return jsonify(json.loads(row["result_json"]))


@app.route("/api/history/<int:search_id>", methods=["DELETE"])
def api_history_delete(search_id):
    """Delete a search result."""
    conn = get_db()
    conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─── Tracker / Journal API ─────────────────────────────────────

def get_week_start(dt=None):
    """Return ISO date string of Monday for the given date's week."""
    if dt is None:
        dt = datetime.now()
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


@app.route("/api/journal", methods=["GET"])
def api_journal_list():
    """List journal entries, optionally filtered by ticker."""
    ticker = request.args.get("ticker", "").strip().upper()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    offset = (page - 1) * per_page

    conn = get_db()
    if ticker:
        rows = conn.execute(
            "SELECT * FROM journal_entries WHERE ticker = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (ticker, per_page, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM journal_entries ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        ).fetchall()
    conn.close()

    entries = []
    for r in rows:
        entries.append({
            "id": r["id"],
            "ticker": r["ticker"],
            "notes": r["notes"],
            "action": r["action"],
            "entry_price": r["entry_price"],
            "exit_price": r["exit_price"],
            "shares": r["shares"],
            "pnl": r["pnl"],
            "created_at": r["created_at"],
        })
    return jsonify(entries)


@app.route("/api/journal", methods=["POST"])
def api_journal_add():
    """Add a journal entry."""
    data = request.get_json()
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "Ticker is required"}), 400

    notes = data.get("notes", "")
    action = data.get("action")  # BUY or SELL or None
    entry_price = data.get("entry_price")
    exit_price = data.get("exit_price")
    shares = data.get("shares")

    # Auto-calculate P&L for SELL trades
    pnl = None
    if action == "SELL" and entry_price is not None and exit_price is not None and shares is not None:
        pnl = round((exit_price - entry_price) * shares, 2)

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO journal_entries (ticker, notes, action, entry_price, exit_price, shares, pnl) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker, notes, action, entry_price, exit_price, shares, pnl),
    )
    entry_id = cur.lastrowid

    # Update weekly actual if there's a P&L
    if pnl is not None:
        week = get_week_start()
        conn.execute(
            "INSERT INTO weekly_goals (week_start, target_amount, actual_amount) VALUES (?, 0, ?) "
            "ON CONFLICT(week_start) DO UPDATE SET actual_amount = actual_amount + ?",
            (week, pnl, pnl),
        )

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "id": entry_id, "pnl": pnl})


@app.route("/api/journal/<int:entry_id>", methods=["DELETE"])
def api_journal_delete(entry_id):
    """Delete a journal entry and adjust weekly actual if it had P&L."""
    conn = get_db()
    row = conn.execute("SELECT pnl, created_at FROM journal_entries WHERE id = ?", (entry_id,)).fetchone()
    if row and row["pnl"] is not None:
        entry_dt = datetime.fromisoformat(row["created_at"])
        week = get_week_start(entry_dt)
        conn.execute(
            "UPDATE weekly_goals SET actual_amount = actual_amount - ? WHERE week_start = ?",
            (row["pnl"], week),
        )
    conn.execute("DELETE FROM journal_entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/goals", methods=["GET"])
def api_goals_get():
    """Get current week goal, monthly rollup, milestones, and account config."""
    conn = get_db()

    # Account config
    config_rows = conn.execute("SELECT key, value FROM account_config").fetchall()
    config = {r["key"]: r["value"] for r in config_rows}
    starting = float(config.get("starting_balance", 25000))
    current = float(config.get("current_balance", 25000))
    weekly_target = float(config.get("weekly_target", 500))

    # Current week
    week = get_week_start()
    week_row = conn.execute("SELECT * FROM weekly_goals WHERE week_start = ?", (week,)).fetchone()
    weekly_actual = week_row["actual_amount"] if week_row else 0

    # Monthly rollup (last 4 weeks)
    now = datetime.now()
    month_start = (now - timedelta(days=now.weekday() + 21)).strftime("%Y-%m-%d")
    month_rows = conn.execute(
        "SELECT SUM(actual_amount) as total FROM weekly_goals WHERE week_start >= ?",
        (month_start,),
    ).fetchone()
    monthly_actual = month_rows["total"] if month_rows and month_rows["total"] else 0
    monthly_target = round(weekly_target * 4.33, 2)

    # Milestones
    milestones_def = [50000, 100000, 250000, 500000, 750000, 1000000, 1250000, 1500000]
    milestones = []
    for m in milestones_def:
        milestones.append({
            "amount": m,
            "reached": current >= m,
            "label": f"${m:,.0f}",
        })

    # Overall progress toward $1.5M
    progress_pct = min(100, round((current - starting) / (1500000 - starting) * 100, 2)) if current > starting else 0

    conn.close()
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
def api_goals_update():
    """Set/update weekly target."""
    data = request.get_json()
    weekly_target = data.get("weekly_target")
    if weekly_target is None:
        return jsonify({"error": "weekly_target is required"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO account_config (key, value) VALUES ('weekly_target', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (str(weekly_target), str(weekly_target)),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/goals/balance", methods=["POST"])
def api_goals_balance():
    """Update current balance."""
    data = request.get_json()
    balance = data.get("balance")
    if balance is None:
        return jsonify({"error": "balance is required"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO account_config (key, value) VALUES ('current_balance', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (str(balance), str(balance)),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─── Status Service API ────────────────────────────────────────

@app.route("/api/status")
def api_status():
    """Get overall status + latest check per service + 90-day uptime %."""
    conn = get_db()
    services_out = []
    overall = "operational"

    for svc_key, svc_info in SERVICES.items():
        # Latest check
        latest = conn.execute("""
            SELECT status, response_time_ms, error_message, checked_at
            FROM service_checks WHERE service_name = ?
            ORDER BY checked_at DESC LIMIT 1
        """, (svc_key,)).fetchone()

        # 90-day uptime %
        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = ? AND checked_at >= ?",
            (svc_key, cutoff)
        ).fetchone()["cnt"]
        ok = conn.execute(
            "SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = ? AND checked_at >= ? AND status = 'operational'",
            (svc_key, cutoff)
        ).fetchone()["cnt"]
        uptime_pct = round(ok / total * 100, 2) if total > 0 else 100.0

        status = latest["status"] if latest else "unknown"
        if status == "outage":
            overall = "outage"
        elif status == "degraded" and overall != "outage":
            overall = "degraded"

        services_out.append({
            "key": svc_key,
            "name": svc_info["name"],
            "category": svc_info["category"],
            "status": status,
            "response_time_ms": latest["response_time_ms"] if latest else None,
            "error_message": latest["error_message"] if latest else None,
            "checked_at": latest["checked_at"] if latest else None,
            "uptime_pct": uptime_pct,
        })

    conn.close()
    return jsonify({"overall": overall, "services": services_out})


@app.route("/api/status/uptime/<service>")
def api_status_uptime(service):
    """Get 90 daily uptime objects for a service."""
    conn = get_db()
    days = []
    for i in range(89, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        day_start = f"{day} 00:00:00"
        day_end = f"{day} 23:59:59"
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = ? AND checked_at >= ? AND checked_at <= ?",
            (service, day_start, day_end)
        ).fetchone()["cnt"]
        failed = conn.execute(
            "SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = ? AND checked_at >= ? AND checked_at <= ? AND status != 'operational'",
            (service, day_start, day_end)
        ).fetchone()["cnt"]
        uptime_pct = round((total - failed) / total * 100, 1) if total > 0 else 100.0
        days.append({"date": day, "uptime_pct": uptime_pct, "total": total, "failed": failed})
    conn.close()
    return jsonify(days)


@app.route("/api/status/incidents")
def api_status_incidents():
    """Get last 50 incidents."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM service_incidents ORDER BY created_at DESC LIMIT 50
    """).fetchall()
    conn.close()
    incidents = []
    for r in rows:
        incidents.append({
            "id": r["id"],
            "service_name": r["service_name"],
            "service_display": SERVICES.get(r["service_name"], {}).get("name", r["service_name"]),
            "incident_type": r["incident_type"],
            "started_at": r["started_at"],
            "resolved_at": r["resolved_at"],
            "duration_seconds": r["duration_seconds"],
            "error_message": r["error_message"],
        })
    return jsonify(incidents)


@app.route("/api/status/check", methods=["POST"])
def api_status_force_check():
    """Force re-check all services."""
    run_check_cycle()
    return jsonify({"ok": True, "message": "Health check completed"})


@app.route("/api/screener", methods=["POST"])
def api_screener():
    """Run stock screener with AI vetting. Supports lowcap/midcap/largecap/etf categories."""
    data = request.get_json() or {}
    category = data.get("category", "lowcap")
    min_price = float(data.get("min_price", 2.0))
    max_price = float(data.get("max_price", 15.0))
    limit = int(data.get("limit", 20))

    # Clamp values
    min_price = max(0.5, min(min_price, 50))
    max_price = max(min_price + 0.5, min(max_price, 100))
    limit = max(5, min(limit, 50))

    if not is_configured():
        return jsonify({
            "error": "OpenRouter API key not configured.",
            "candidates_scanned": 0,
            "opportunities": [],
            "risky": [],
            "avoided": 0,
        }), 200

    sectors = data.get("sectors", [])

    try:
        result = run_screener(min_price, max_price, limit, category=category, sectors=sectors)
        result_json = json.dumps(result, cls=NumpyEncoder, default=str)
        return app.response_class(
            response=result_json,
            status=200,
            mimetype='application/json'
        )
    except Exception as e:
        return jsonify({"error": f"Screener failed: {str(e)}"}), 500


@app.route("/api/qullamaggie", methods=["POST"])
def api_qullamaggie():
    """Run Qullamaggie breakout scanner."""
    data = request.get_json() or {}
    category = data.get("category", "all")

    # Build ticker list based on category
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
        return app.response_class(
            response=result_json,
            status=200,
            mimetype='application/json'
        )
    except Exception as e:
        return jsonify({"error": f"Qullamaggie scan failed: {str(e)}", "results": [], "scanned": 0}), 500


@app.route("/api/screener/hot-sectors")
def api_hot_sectors():
    """Get AI-identified trending sectors for a time period."""
    period = request.args.get("period", "1mo")
    valid = ["1w", "2w", "1mo", "3mo", "6mo", "1y"]
    if period not in valid:
        period = "1mo"
    result = get_hot_sectors(period)
    return jsonify(result)


# ─── Auto Trading Bot API ─────────────────────────────────────

@app.route("/api/bot/status")
def api_bot_status():
    """Get bot running state, balance, open positions, daily P&L."""
    bot = get_bot()
    return jsonify(bot.get_status())


@app.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    """Start the trading bot."""
    bot = get_bot()
    if bot.is_running:
        return jsonify({"ok": False, "error": "Bot is already running"}), 400
    bot.start()
    return jsonify({"ok": True, "message": "Bot started"})


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    """Stop the trading bot gracefully."""
    bot = get_bot()
    bot.stop()
    return jsonify({"ok": True, "message": "Bot stopped"})


@app.route("/api/bot/kill", methods=["POST"])
def api_bot_kill():
    """Kill switch — stop bot + close all positions."""
    bot = get_bot()
    bot.kill()
    return jsonify({"ok": True, "message": "Kill switch activated — bot stopped, positions closed"})


@app.route("/api/bot/trades")
def api_bot_trades():
    """Get trade history. Query params: status (open|closed), asset_type (crypto|stock), limit (default 50)."""
    status = request.args.get("status")
    asset_type = request.args.get("asset_type")
    limit = int(request.args.get("limit", 50))
    conn = get_db()

    clauses = []
    params = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if asset_type:
        clauses.append("asset_type = ?")
        params.append(asset_type)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM bot_trades{where} ORDER BY opened_at DESC LIMIT ?", params
    ).fetchall()
    conn.close()
    trades = []
    for r in rows:
        trades.append({
            "id": r["id"], "coin": r["coin"], "side": r["side"],
            "size": r["size"], "entry_price": r["entry_price"],
            "exit_price": r["exit_price"], "pnl": r["pnl"],
            "pnl_pct": r["pnl_pct"], "status": r["status"],
            "signal_reason": r["signal_reason"],
            "strategy": r["strategy"],
            "asset_type": r["asset_type"],
            "direction_bias": r["direction_bias"],
            "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
            "opened_at": r["opened_at"], "closed_at": r["closed_at"],
            "blofin_order_id": r["blofin_order_id"],
        })
    return jsonify(trades)


@app.route("/api/bot/trades/<int:trade_id>")
def api_bot_trade_detail(trade_id):
    """Get single trade with LLM validation details."""
    conn = get_db()
    r = conn.execute("SELECT * FROM bot_trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    if not r:
        return jsonify({"error": "Trade not found"}), 404
    trade = {
        "id": r["id"], "coin": r["coin"], "side": r["side"],
        "size": r["size"], "entry_price": r["entry_price"],
        "exit_price": r["exit_price"], "pnl": r["pnl"],
        "pnl_pct": r["pnl_pct"], "status": r["status"],
        "signal_reason": r["signal_reason"],
        "strategy": r["strategy"],
        "asset_type": r["asset_type"],
        "direction_bias": r["direction_bias"],
        "validation_result": json.loads(r["validation_result"]) if r["validation_result"] else None,
        "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
        "opened_at": r["opened_at"], "closed_at": r["closed_at"],
        "blofin_order_id": r["blofin_order_id"],
    }
    return jsonify(trade)


@app.route("/api/bot/pnl")
def api_bot_pnl():
    """Get daily P&L for chart. Query param: days (default 30)."""
    days = int(request.args.get("days", 30))
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM bot_daily_pnl ORDER BY date DESC LIMIT ?", (days,)
    ).fetchall()
    conn.close()
    pnl_data = []
    for r in rows:
        pnl_data.append({
            "date": r["date"], "total_pnl": r["total_pnl"],
            "trade_count": r["trade_count"],
            "win_count": r["win_count"], "loss_count": r["loss_count"],
        })
    pnl_data.reverse()  # Chronological order
    return jsonify(pnl_data)


@app.route("/api/bot/platform", methods=["GET", "POST"])
def api_bot_platform():
    """GET: current platform + masked key status. POST: save platform keys."""
    conn = get_db()
    if request.method == "GET":
        platform = conn.execute("SELECT value FROM bot_config WHERE key = 'platform'").fetchone()
        platform = platform["value"] if platform else "blofin"
        conn.close()
        # Check if keys are set (don't expose actual values)
        has_key = bool(os.getenv("BLOFIN_API_KEY", ""))
        has_secret = bool(os.getenv("BLOFIN_API_SECRET", ""))
        has_pass = bool(os.getenv("BLOFIN_PASSPHRASE", ""))
        return jsonify({
            "platform": platform,
            "has_key": has_key,
            "has_secret": has_secret,
            "has_passphrase": has_pass,
            "configured": has_key and has_secret and has_pass,
        })

    # POST — save platform + keys
    data = request.get_json()
    platform = data.get("platform", "blofin")
    conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('platform', ?)", (platform,))
    conn.commit()
    conn.close()

    # Save API keys to .env file
    api_key = data.get("api_key", "").strip()
    api_secret = data.get("api_secret", "").strip()
    passphrase = data.get("passphrase", "").strip()

    if api_key or api_secret or passphrase:
        from env_writer import update_env
        env_updates = {}
        if api_key:
            env_updates["BLOFIN_API_KEY"] = api_key
        if api_secret:
            env_updates["BLOFIN_API_SECRET"] = api_secret
        if passphrase:
            env_updates["BLOFIN_PASSPHRASE"] = passphrase
        try:
            update_env(env_updates)
            _reload_module_globals(env_updates)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to save keys: {e}"}), 500

    return jsonify({"ok": True, "platform": platform})


@app.route("/api/bot/test-connection", methods=["POST"])
def api_bot_test_connection():
    """Test the platform API connection."""
    try:
        bot = get_bot()
        # Force re-init client with current env
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
def api_bot_check_permissions():
    """Check if the API key has trade permissions."""
    try:
        bot = get_bot()
        result = bot.client.check_trade_permission()
        return jsonify(result)
    except Exception as e:
        return jsonify({"has_trade_permission": False, "message": str(e)})


@app.route("/api/bot/test-trade", methods=["POST"])
def api_bot_test_trade():
    """Place a minimal test trade on BloFin demo."""
    try:
        bot = get_bot()
        data = request.get_json() or {}
        coin = data.get("coin", "BTC-USDT")
        side = data.get("side", "buy")

        coin_info = COIN_MAP.get(coin)
        if not coin_info:
            return jsonify({"ok": False, "error": f"Unknown coin: {coin}"})

        inst_id = coin_info["blofin"]

        # Get instrument info for min size
        info = bot.client.get_instrument_info(inst_id)
        min_contracts = info["min_size"]

        result = bot.client.place_order(
            inst_id=inst_id,
            side=side,
            size=min_contracts,
            size_in_contracts=True,
        )

        if result.get("success"):
            return jsonify({
                "ok": True,
                "message": f"Test trade placed: {side} {min_contracts} contracts {inst_id}",
                "order_id": result.get("order_id", ""),
                "raw": str(result.get("raw", "")),
            })
        else:
            return jsonify({
                "ok": False,
                "error": result.get("error", "Unknown error"),
                "code": result.get("code", ""),
            })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/bot/coins", methods=["GET", "POST"])
def api_bot_coins():
    """GET: list all available coins + which are selected. POST: update selection."""
    conn = get_db()
    if request.method == "GET":
        raw = conn.execute("SELECT value FROM bot_config WHERE key = 'selected_coins'").fetchone()
        selected = json.loads(raw["value"]) if raw else list(DEFAULT_COINS)
        conn.close()
        coins = []
        for key, info in COIN_MAP.items():
            coins.append({
                "key": key,
                "name": info["name"],
                "blofin": info["blofin"],
                "selected": key in selected,
            })
        return jsonify({"coins": coins, "selected": selected})

    # POST — update selected coins
    data = request.get_json()
    selected = data.get("selected", [])
    # Validate all coins exist
    valid = [c for c in selected if c in COIN_MAP]
    if not valid:
        conn.close()
        return jsonify({"error": "At least one valid coin must be selected"}), 400
    conn.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES ('selected_coins', ?)",
        (json.dumps(valid),)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "selected": valid})


@app.route("/api/bot/config", methods=["GET", "POST"])
def api_bot_config():
    """Read or update bot risk settings."""
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute("SELECT key, value FROM bot_config").fetchall()
        conn.close()
        return jsonify({r["key"]: r["value"] for r in rows})

    # POST — update config
    data = request.get_json()
    allowed_keys = {"max_position_pct", "daily_loss_limit", "max_open_positions", "scan_interval_sec", "daily_goal", "trading_mode", "direction_bias"}
    for k, v in data.items():
        if k in allowed_keys:
            conn.execute(
                "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
                (k, str(v))
            )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/bot/log")
def api_bot_log():
    """Get activity log. Query params: level (info|warn|error), limit (default 100)."""
    level = request.args.get("level")
    limit = int(request.args.get("limit", 100))
    conn = get_db()
    if level:
        rows = conn.execute(
            "SELECT * FROM bot_log WHERE level = ? ORDER BY created_at DESC LIMIT ?",
            (level, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bot_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    logs = []
    for r in rows:
        logs.append({
            "id": r["id"], "level": r["level"],
            "message": r["message"], "details": r["details"],
            "created_at": r["created_at"],
        })
    return jsonify(logs)


@app.route("/api/bot/trending")
def api_bot_trending():
    """Get AI-analyzed trending crypto based on live market data."""
    try:
        result = get_trending_crypto()
        result_json = json.dumps(result, cls=NumpyEncoder, default=str)
        return app.response_class(response=result_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": f"Trending analysis failed: {str(e)}"}), 500


def _reload_module_globals(updated_keys: dict):
    """Hot-reload module-level globals after .env update."""
    import ai_validator as av
    import crypto_bot.crypto_validator as cv
    import crypto_bot.blofin_client as bc
    import screener as scr

    for key, val in updated_keys.items():
        if key == "OPENROUTER_API_KEY":
            av.OPENROUTER_API_KEY = val
            av.HEADERS["Authorization"] = f"Bearer {val}"
            cv.OPENROUTER_API_KEY = val
            cv.HEADERS["Authorization"] = f"Bearer {val}"
        elif key == "BLOFIN_API_KEY":
            bc.BLOFIN_API_KEY = val
        elif key == "BLOFIN_API_SECRET":
            bc.BLOFIN_API_SECRET = val
        elif key == "BLOFIN_PASSPHRASE":
            bc.BLOFIN_PASSPHRASE = val
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


@app.route("/api/settings")
def api_settings_get():
    """Return all settings, masking sensitive values."""
    import ai_validator as av
    import crypto_bot.crypto_validator as cv
    import crypto_bot.blofin_client as bc

    def mask(val):
        if not val or val == "your_openrouter_api_key_here":
            return ""
        if len(val) <= 8:
            return "****"
        return val[:4] + "****" + val[-4:]

    return jsonify({
        "api_keys": {
            "openrouter": {
                "configured": av.is_configured(),
                "masked_value": mask(av.OPENROUTER_API_KEY),
            },
            "blofin_api_key": {
                "configured": bool(bc.BLOFIN_API_KEY),
                "masked_value": mask(bc.BLOFIN_API_KEY),
            },
            "blofin_api_secret": {
                "configured": bool(bc.BLOFIN_API_SECRET),
                "masked_value": mask(bc.BLOFIN_API_SECRET),
            },
            "blofin_passphrase": {
                "configured": bool(bc.BLOFIN_PASSPHRASE),
                "masked_value": mask(bc.BLOFIN_PASSPHRASE),
            },
        },
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


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    """Save settings to .env and hot-reload module globals."""
    from env_writer import update_env

    data = request.get_json()
    env_updates = {}

    # API Keys (only update if user provided a non-empty value)
    api_keys = data.get("api_keys", {})
    for field in ["OPENROUTER_API_KEY", "BLOFIN_API_KEY", "BLOFIN_API_SECRET", "BLOFIN_PASSPHRASE"]:
        val = api_keys.get(field, "").strip()
        if val:
            env_updates[field] = val

    # LLM Models
    models = data.get("llm_models", {})
    for field in ["LLM_RESEARCH", "LLM_RESEARCH_FAST", "LLM_PATTERN",
                  "LLM_PREDICTION", "LLM_SCREENER", "LLM_BOT_SENTIMENT", "LLM_BOT_RISK"]:
        val = models.get(field, "").strip()
        if val:
            env_updates[field] = val

    # LLM Settings
    settings = data.get("llm_settings", {})
    if "LLM_MAX_TOKENS" in settings and settings["LLM_MAX_TOKENS"]:
        env_updates["LLM_MAX_TOKENS"] = str(int(settings["LLM_MAX_TOKENS"]))
    if "LLM_TEMPERATURE" in settings and settings["LLM_TEMPERATURE"] is not None:
        env_updates["LLM_TEMPERATURE"] = str(float(settings["LLM_TEMPERATURE"]))
    if "LLM_FAST_MODE" in settings:
        env_updates["LLM_FAST_MODE"] = "1" if settings["LLM_FAST_MODE"] else "0"

    # Ollama
    ollama = data.get("ollama", {})
    for field in ["OLLAMA_URL", "OLLAMA_MODEL"]:
        val = ollama.get(field, "").strip()
        if val:
            env_updates[field] = val

    if not env_updates:
        return jsonify({"ok": True, "message": "No changes"})

    try:
        update_env(env_updates)
        _reload_module_globals(env_updates)
        return jsonify({"ok": True, "updated": list(env_updates.keys())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    init_db()
    start_background_checker()
    app.run(debug=True, port=5001)
