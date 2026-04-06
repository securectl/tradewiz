import json
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from shared.helpers import _uid, P, NumpyEncoder, get_week_start, cleanup_expired
from decorators import login_required
from db import get_db, put_db, query, query_one, execute, IS_POSTGRES

bp = Blueprint("tracker", __name__)


# ─── History API ──────────────────────────────────────────────────────

@bp.route("/api/history")
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


@bp.route("/api/history/<int:search_id>")
@login_required
def api_history_detail(search_id):
    """Get a specific search result."""
    cleanup_expired()
    uid = _uid()
    row = query_one(f"SELECT * FROM searches WHERE id = {P} AND user_id = {P}", (search_id, uid))
    if not row:
        return jsonify({"error": "Search not found or expired"}), 404
    return jsonify(json.loads(row["result_json"]))


@bp.route("/api/history/<int:search_id>", methods=["DELETE"])
@login_required
def api_history_delete(search_id):
    """Delete a search result."""
    uid = _uid()
    execute(f"DELETE FROM searches WHERE id = {P} AND user_id = {P}", (search_id, uid))
    return jsonify({"ok": True})


# ─── Tracker / Journal API ─────────────────────────────────────────────

@bp.route("/api/journal", methods=["GET"])
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


@bp.route("/api/journal", methods=["POST"])
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


@bp.route("/api/journal/<int:entry_id>", methods=["DELETE"])
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


# ─── Goals API ────────────────────────────────────────────────────────

@bp.route("/api/goals", methods=["GET"])
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


@bp.route("/api/goals", methods=["POST"])
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


@bp.route("/api/goals/balance", methods=["POST"])
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
