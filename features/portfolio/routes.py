"""Portfolio Advisor API — import holdings, analyze (cut/add), admin gating.

Admin-gated per user: a user only sees the feature when an admin grants it
(bot_config key ``portfolio_advisor='1'``) or the user is an admin. All holdings
and analyses are persisted.
"""

import json
import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user

from decorators import login_required, admin_required
from shared.helpers import _uid, _upsert_bot_config
from db import query, query_one, execute, IS_POSTGRES

logger = logging.getLogger(__name__)
bp = Blueprint("portfolio", __name__)
P = "%s" if IS_POSTGRES else "?"


def _is_admin():
    return any(r == "admin" for r in (getattr(current_user, "roles", []) or []))


def _enabled(uid):
    if _is_admin():
        return True
    row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = {P}",
                    (uid, "portfolio_advisor"))
    return bool(row and row.get("value") == "1")


@bp.route("/api/portfolio/access")
@login_required
def portfolio_access():
    """Whether the Portfolio Advisor is enabled for the current user (tab gating)."""
    return jsonify({"enabled": _enabled(_uid())})


@bp.route("/api/portfolio", methods=["GET"])
@login_required
def get_portfolio():
    uid = _uid()
    if not _enabled(uid):
        return jsonify({"error": "Portfolio Advisor is not enabled for your account."}), 403
    rows = query(
        f"SELECT symbol, shares, cost_basis, source, imported_at FROM portfolio_holdings "
        f"WHERE user_id = {P} ORDER BY symbol", (uid,)) or []
    holdings = [{"symbol": r["symbol"], "shares": r["shares"],
                 "cost_basis": r["cost_basis"], "source": r.get("source")} for r in rows]
    last = query_one(
        f"SELECT result, created_at FROM portfolio_analyses WHERE user_id = {P} "
        f"ORDER BY created_at DESC LIMIT 1", (uid,))
    analysis = None
    if last and last.get("result"):
        try:
            analysis = json.loads(last["result"])
            analysis["created_at"] = str(last.get("created_at"))[:19]
        except Exception:
            pass
    return jsonify({"holdings": holdings, "analysis": analysis})


@bp.route("/api/portfolio/import", methods=["POST"])
@login_required
def import_portfolio():
    uid = _uid()
    if not _enabled(uid):
        return jsonify({"error": "Portfolio Advisor is not enabled for your account."}), 403
    data = request.get_json(silent=True) or {}
    csv_text = data.get("csv") or ""
    from features.portfolio.parser import parse_positions_csv, detect_source
    holdings = parse_positions_csv(csv_text)
    if not holdings:
        return jsonify({"ok": False,
                        "error": "Couldn't find any positions. Paste the CSV export from "
                                 "Fidelity or Schwab (must include Symbol + Quantity)."}), 200
    source = data.get("source") or detect_source(csv_text)
    now = datetime.utcnow().isoformat()
    # Replace the user's holdings with the freshly imported set.
    execute(f"DELETE FROM portfolio_holdings WHERE user_id = {P}", (uid,))
    for h in holdings:
        execute(
            f"INSERT INTO portfolio_holdings (user_id, symbol, shares, cost_basis, source, imported_at) "
            f"VALUES ({P},{P},{P},{P},{P},{P}) "
            f"ON CONFLICT(user_id, symbol) DO UPDATE SET shares=excluded.shares, "
            f"cost_basis=excluded.cost_basis, source=excluded.source, imported_at=excluded.imported_at",
            (uid, h["symbol"], h["shares"], h.get("cost_basis"), source, now),
        )
    return jsonify({"ok": True, "imported": len(holdings), "source": source,
                    "symbols": [h["symbol"] for h in holdings]})


@bp.route("/api/portfolio/analyze", methods=["POST"])
@login_required
def analyze():
    uid = _uid()
    if not _enabled(uid):
        return jsonify({"error": "Portfolio Advisor is not enabled for your account."}), 403
    rows = query(f"SELECT symbol, shares, cost_basis FROM portfolio_holdings WHERE user_id = {P}",
                 (uid,)) or []
    if not rows:
        return jsonify({"ok": False, "error": "Import your holdings first."}), 200
    holdings = [{"symbol": r["symbol"], "shares": r["shares"], "cost_basis": r["cost_basis"]}
                for r in rows]
    from features.portfolio.advisor import analyze_portfolio
    result = analyze_portfolio(holdings)
    result["generated_at"] = datetime.utcnow().isoformat()
    execute(
        f"INSERT INTO portfolio_analyses (user_id, holdings_count, result, created_at) "
        f"VALUES ({P},{P},{P},{P})",
        (uid, len(holdings), json.dumps(result), datetime.utcnow().isoformat()),
    )
    return jsonify({"ok": True, "analysis": result})


@bp.route("/api/portfolio", methods=["DELETE"])
@login_required
def clear_portfolio():
    uid = _uid()
    if not _enabled(uid):
        return jsonify({"error": "not enabled"}), 403
    execute(f"DELETE FROM portfolio_holdings WHERE user_id = {P}", (uid,))
    return jsonify({"ok": True})


# ── Admin: grant/revoke the feature per user ──────────────────────────────
@bp.route("/api/admin/portfolio-access", methods=["GET", "POST"])
@admin_required
def admin_portfolio_access():
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        target = d.get("user_id")
        enabled = bool(d.get("enabled"))
        if not target:
            return jsonify({"ok": False, "error": "user_id required"}), 400
        _upsert_bot_config(int(target), "portfolio_advisor", "1" if enabled else "0")
        return jsonify({"ok": True, "user_id": target, "enabled": enabled})
    # GET → list users currently granted
    rows = query(
        f"SELECT user_id FROM bot_config WHERE key = {P} AND value = '1'",
        ("portfolio_advisor",)) or []
    return jsonify({"granted_user_ids": [r["user_id"] for r in rows]})
