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

    # Exclude bot-auto-logged entries — historically crypto/stock bots wrote
    # rows here with notes prefixed by "[Crypto Bot]"/"[Stock Bot]", which
    # polluted the user's manual journal with crypto-pair entries. The
    # auto-logging was removed Apr 2026; this filter hides legacy rows so
    # the journal panel only shows user-created notes.
    bot_filter = f"AND (notes IS NULL OR notes NOT LIKE {P})"
    bot_param = ("[%Bot]%",)

    if ticker:
        rows = query(
            f"SELECT * FROM journal_entries WHERE user_id = {P} AND ticker = {P} {bot_filter} "
            f"ORDER BY created_at DESC LIMIT {P} OFFSET {P}",
            (uid, ticker) + bot_param + (per_page, offset),
        )
    else:
        rows = query(
            f"SELECT * FROM journal_entries WHERE user_id = {P} {bot_filter} "
            f"ORDER BY created_at DESC LIMIT {P} OFFSET {P}",
            (uid,) + bot_param + (per_page, offset),
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


# ─── Bot trades (per-user, all four sources) ─────────────────

VALID_BOT_SOURCES = {"crypto", "stock", "claude", "watchdog"}


@bp.route("/api/tracker/bot-trades")
@login_required
def api_tracker_bot_trades():
    """All bot trades for the current user, with optional source filter.

    Query params:
        source = all | crypto | stock | claude | watchdog (default all)
        limit  = max trades returned (default 100, capped 500)
        status = open | closed | all (default all)

    Returns per-source summary + filtered trade list. Per-user isolation
    is enforced by `user_id = _uid()`; non-logged-in users get 401 from
    @login_required and never see another user's trades.
    """
    uid = _uid()
    source = (request.args.get("source") or "all").lower()
    status = (request.args.get("status") or "all").lower()
    try:
        limit = max(1, min(500, int(request.args.get("limit", 100))))
    except ValueError:
        limit = 100

    # Per-source summary (always all 4 sources, regardless of filter, so the
    # UI can show counts on the filter pills without a second request).
    by_source = {}
    for src in sorted(VALID_BOT_SOURCES):
        row = query_one(
            f"SELECT COUNT(*) AS trades, "
            f"SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_count, "
            f"SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed_count, "
            f"COALESCE(SUM(CASE WHEN status='closed' THEN pnl ELSE 0 END), 0) AS total_pnl, "
            f"SUM(CASE WHEN status='closed' AND pnl > 0 THEN 1 ELSE 0 END) AS wins "
            f"FROM bot_trades WHERE user_id = {P} AND asset_type = {P}",
            (uid, src),
        )
        if row and int(row["trades"] or 0) > 0:
            closed = int(row["closed_count"] or 0)
            wins = int(row["wins"] or 0)
            by_source[src] = {
                "trades": int(row["trades"]),
                "open": int(row["open_count"] or 0),
                "closed": closed,
                "wins": wins,
                "win_rate": round(wins / closed * 100, 1) if closed > 0 else 0.0,
                "total_pnl": round(float(row["total_pnl"] or 0), 2),
            }

    # `overall` reflects the ACTIVE filter so the headline P&L line on the
    # tracker UI moves when the user clicks a source pill (audit Apr 2026:
    # "when filter is used on tracker it does not show correct data").
    # When source='all', sum across all sources. When a single source is
    # picked, scope to that bucket only.
    if source in VALID_BOT_SOURCES:
        scoped = by_source.get(source, {"trades": 0, "open": 0, "closed": 0,
                                         "wins": 0, "total_pnl": 0.0, "win_rate": 0.0})
        overall_pnl = round(scoped["total_pnl"], 2)
        overall_trades = scoped["trades"]
        overall_wins = scoped["wins"]
        overall_closed = scoped["closed"]
        overall_win_rate = scoped["win_rate"]
    else:
        overall_pnl = round(sum(s["total_pnl"] for s in by_source.values()), 2)
        overall_trades = sum(s["trades"] for s in by_source.values())
        overall_wins = sum(s["wins"] for s in by_source.values())
        overall_closed = sum(s["closed"] for s in by_source.values())
        overall_win_rate = round(overall_wins / overall_closed * 100, 1) if overall_closed > 0 else 0.0

    # Filtered trade list
    clauses = [f"user_id = {P}"]
    params = [uid]
    if source in VALID_BOT_SOURCES:
        clauses.append(f"asset_type = {P}")
        params.append(source)
    elif source != "all":
        return jsonify({"error": f"invalid source — use one of all/{'/'.join(sorted(VALID_BOT_SOURCES))}"}), 400
    if status in ("open", "closed"):
        clauses.append(f"status = {P}")
        params.append(status)

    where = " AND ".join(clauses)
    params.append(limit)
    rows = query(
        f"SELECT id, coin, side, size, entry_price, exit_price, pnl, pnl_pct, status, "
        f"asset_type, strategy, opened_at, closed_at "
        f"FROM bot_trades WHERE {where} ORDER BY opened_at DESC LIMIT {P}",
        params,
    )
    trades = [{
        "id": r["id"], "coin": r["coin"], "side": r["side"], "size": r["size"],
        "entry_price": r["entry_price"], "exit_price": r["exit_price"],
        "pnl": r["pnl"], "pnl_pct": r["pnl_pct"], "status": r["status"],
        "source": r["asset_type"], "strategy": r["strategy"],
        "opened_at": str(r["opened_at"]) if r["opened_at"] else None,
        "closed_at": str(r["closed_at"]) if r["closed_at"] else None,
    } for r in (rows or [])]

    return jsonify({
        "trades": trades,
        "by_source": by_source,
        "overall": {
            "pnl": overall_pnl,
            "trades": overall_trades,
            "wins": overall_wins,
            "closed": overall_closed,
            "win_rate": overall_win_rate,
        },
        "filter": {"source": source, "status": status, "limit": limit},
    })
