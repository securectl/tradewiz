"""
Admin API routes — system config, invites, user management, usage, export.
Extracted from app.py.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from io import StringIO

from flask import Blueprint, jsonify, request
from shared.helpers import _uid, P
from decorators import admin_required
from db import query, query_one, execute, IS_POSTGRES
from rate_limiter import TIER_LIMITS

logger = logging.getLogger(__name__)
bp = Blueprint("admin", __name__)


# ─── Admin API ─────────────────────────────────────────────────────────

@bp.route("/api/admin/config", methods=["GET", "POST"])
@admin_required
def api_admin_config():
    """Admin-only: manage system LLM/OpenRouter config."""
    if request.method == "GET":
        import ai_validator as av
        import crypto_bot.crypto_validator as cv
        import market_sensor as ms
        import skills.llm_adapter as skl

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
                "LLM_SUPERVISOR": av.LLM_SUPERVISOR,
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
            "bot_sensor_enabled": ms.BOT_SENSOR_ENABLED,
            "skill_models": {
                "LLM_SKILL": skl.LLM_SKILL,
                "LLM_SKILL_EARNINGS": skl.LLM_SKILL_EARNINGS,
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
    for field in ["LLM_RESEARCH", "LLM_RESEARCH_FAST", "LLM_PATTERN", "LLM_PREDICTION", "LLM_SCREENER", "LLM_SUPERVISOR", "LLM_BOT_SENTIMENT", "LLM_BOT_RISK"]:
        val = models.get(field, "").strip()
        if val:
            env_updates[field] = val
        elif field == "LLM_SUPERVISOR" and field in models:
            # Allow clearing supervisor (empty string disables it)
            env_updates[field] = ""

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

    skill_models = data.get("skill_models", {})
    for field in ["LLM_SKILL", "LLM_SKILL_EARNINGS"]:
        val = skill_models.get(field, "").strip()
        if val:
            env_updates[field] = val

    if "bot_sensor_enabled" in data:
        env_updates["BOT_SENSOR_ENABLED"] = "1" if data["bot_sensor_enabled"] else "0"

    if env_updates:
        # Update os.environ and hot-reload module globals
        for k, v in env_updates.items():
            os.environ[k] = v
        _reload_module_globals(env_updates)

    return jsonify({"ok": True, "updated": list(env_updates.keys())})


@bp.route("/api/admin/invite", methods=["GET", "POST"])
@admin_required
def api_admin_invite():
    if request.method == "GET":
        rows = query("SELECT * FROM invites ORDER BY invited_at DESC")
        invites = []
        for r in rows:
            inv = dict(r)
            # Ensure datetime fields are serializable
            for k in ("invited_at", "accepted_at"):
                if inv.get(k) and hasattr(inv[k], "isoformat"):
                    inv[k] = inv[k].isoformat()
            invites.append(inv)
        return jsonify({"invites": invites})

    data = request.get_json()
    email = (data.get("email") or "").strip().lower()
    role = data.get("role", "trader")
    tier = data.get("tier", "free")
    bot_access_list = data.get("bot_access", [])  # e.g. ["crypto", "stock"]
    if not email:
        return jsonify({"error": "Email is required"}), 400
    if role not in ("trader", "admin", "user"):
        return jsonify({"error": "Invalid role"}), 400
    if tier not in ("free", "basic", "pro"):
        return jsonify({"error": "Invalid tier"}), 400

    # Only Pro tier can have bot access
    if tier != "pro":
        bot_access_list = []
    # Validate bot_access values
    valid_bots = {"crypto", "stock", "watchdog"}
    bot_access_list = [b for b in bot_access_list if b in valid_bots]
    bot_access_str = ",".join(bot_access_list) if bot_access_list else "none"

    # Auto-set role based on tier: pro -> trader, else -> user
    if tier == "pro":
        role = "trader"
    elif role == "trader" and tier != "pro":
        role = "user"

    uid = _uid()
    if IS_POSTGRES:
        execute(
            f"INSERT INTO invites (email, role, tier, bot_access, invited_by) VALUES ({P}, {P}, {P}, {P}, {P}) "
            f"ON CONFLICT(email) DO UPDATE SET role = {P}, tier = {P}, bot_access = {P}",
            (email, role, tier, bot_access_str, uid, role, tier, bot_access_str),
        )
    else:
        execute(
            f"INSERT OR REPLACE INTO invites (email, role, tier, bot_access, invited_by) VALUES ({P}, {P}, {P}, {P}, {P})",
            (email, role, tier, bot_access_str, uid),
        )
    return jsonify({"ok": True, "email": email, "role": role, "tier": tier, "bot_access": bot_access_str})


@bp.route("/api/admin/invite/<email>", methods=["PUT"])
@admin_required
def api_admin_invite_update(email):
    """Update an existing invite's tier and bot_access."""
    data = request.get_json()
    tier = data.get("tier", "free")
    bot_access_list = data.get("bot_access", [])
    if tier not in ("free", "basic", "pro"):
        return jsonify({"error": "Invalid tier"}), 400
    if tier != "pro":
        bot_access_list = []
    valid_bots = {"crypto", "stock", "watchdog"}
    bot_access_list = [b for b in bot_access_list if b in valid_bots]
    bot_access_str = ",".join(bot_access_list) if bot_access_list else "none"
    role = "trader" if tier == "pro" else "user"

    if IS_POSTGRES:
        execute(
            f"UPDATE invites SET tier = {P}, bot_access = {P}, role = {P} WHERE email = {P}",
            (tier, bot_access_str, role, email.lower()),
        )
    else:
        execute(
            f"UPDATE invites SET tier = {P}, bot_access = {P}, role = {P} WHERE email = {P}",
            (tier, bot_access_str, role, email.lower()),
        )
    return jsonify({"ok": True, "tier": tier, "bot_access": bot_access_str})


@bp.route("/api/admin/invite/<email>", methods=["DELETE"])
@admin_required
def api_admin_invite_delete(email):
    execute(f"DELETE FROM invites WHERE email = {P}", (email.lower(),))
    return jsonify({"ok": True})


@bp.route("/api/admin/support-tickets")
@admin_required
def api_admin_support_tickets():
    """List all support requests."""
    rows = query("SELECT * FROM support_requests ORDER BY created_at DESC LIMIT 100")
    return jsonify([{
        "id": r["id"], "ticket_id": r["ticket_id"],
        "name": r["name"], "email": r["email"],
        "issue_type": r["issue_type"], "message": r["message"],
        "status": r["status"], "admin_notes": r.get("admin_notes"),
        "created_at": str(r["created_at"]),
        "resolved_at": str(r["resolved_at"]) if r.get("resolved_at") else None,
    } for r in rows])


@bp.route("/api/admin/support-tickets/<int:ticket_id>/resolve", methods=["POST"])
@admin_required
def api_admin_resolve_ticket(ticket_id):
    """Mark a support ticket as resolved."""
    data = request.get_json()
    notes = data.get("notes", "")
    from datetime import datetime
    execute(
        f"UPDATE support_requests SET status = 'resolved', admin_notes = {P}, resolved_at = {P} WHERE id = {P}",
        (notes, datetime.now().isoformat(), ticket_id),
    )
    return jsonify({"ok": True})


@bp.route("/api/admin/users")
@admin_required
def api_admin_users():
    # id=0 is the system sentinel for global bot_config rows — exclude it.
    rows = query("SELECT * FROM users WHERE id != 0 ORDER BY created_at DESC")
    users = []
    for u in rows:
        roles = query(f"SELECT role FROM user_roles WHERE user_id = {P}", (u["id"],))
        sub_row = query_one(f"SELECT tier, bot_access FROM user_subscriptions WHERE user_id = {P}", (u["id"],))
        users.append({
            "id": u["id"], "email": u["email"], "name": u["name"],
            "picture_url": u["picture_url"],
            "created_at": str(u["created_at"]),
            "last_login": str(u["last_login"]) if u.get("last_login") else None,
            "is_locked": bool(u.get("is_locked")),
            "roles": [r["role"] for r in roles],
            "tier": sub_row["tier"] if sub_row else "free",
            "bot_access": sub_row["bot_access"] if sub_row and sub_row.get("bot_access") else "none",
        })
    return jsonify(users)


@bp.route("/api/admin/users/<int:user_id>/role", methods=["POST"])
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


@bp.route("/api/admin/users/<int:user_id>/lock", methods=["POST"])
@admin_required
def api_admin_user_lock(user_id):
    """Lock or unlock a user account."""
    data = request.get_json()
    lock = data.get("lock", True)
    lock_val = True if IS_POSTGRES else 1
    unlock_val = False if IS_POSTGRES else 0
    execute(
        f"UPDATE users SET is_locked = {P} WHERE id = {P}",
        (lock_val if lock else unlock_val, user_id),
    )
    action = "locked" if lock else "unlocked"
    logger.info(f"Admin {_uid()} {action} user {user_id}")
    return jsonify({"ok": True, "locked": lock, "message": f"Account {action}"})


@bp.route("/api/admin/users/<int:user_id>/tier", methods=["POST"])
@admin_required
def api_admin_user_tier(user_id):
    """Admin-only: set a user's subscription tier and bot access."""
    data = request.get_json()
    tier = data.get("tier")
    bot_access_list = data.get("bot_access")  # e.g. ["crypto", "stock"] or None

    if tier and tier not in ("free", "basic", "pro"):
        return jsonify({"error": "Invalid tier. Choose free, basic, or pro."}), 400

    # Build bot_access string
    if bot_access_list is not None:
        valid_bots = {"crypto", "stock", "watchdog"}
        bot_access_list = [b for b in bot_access_list if b in valid_bots]
        bot_access_str = ",".join(bot_access_list) if bot_access_list else "none"
    else:
        bot_access_str = None

    # Update role based on tier
    if tier == "pro":
        # Grant trader role
        if IS_POSTGRES:
            execute(
                f"INSERT INTO user_roles (user_id, role, granted_by) VALUES ({P}, 'trader', {P}) ON CONFLICT DO NOTHING",
                (user_id, _uid()),
            )
        else:
            execute(
                f"INSERT OR IGNORE INTO user_roles (user_id, role, granted_by) VALUES ({P}, 'trader', {P})",
                (user_id, _uid()),
            )
    elif tier in ("free", "basic"):
        # Remove trader role (keep admin if present)
        execute(f"DELETE FROM user_roles WHERE user_id = {P} AND role = 'trader'", (user_id,))

    # Non-pro tiers can't have bot access
    if tier and tier != "pro":
        bot_access_str = "none"

    if IS_POSTGRES:
        if tier and bot_access_str is not None:
            execute(
                f"INSERT INTO user_subscriptions (user_id, tier, bot_access, status) VALUES ({P}, {P}, {P}, 'active') "
                f"ON CONFLICT (user_id) DO UPDATE SET tier = {P}, bot_access = {P}, updated_at = NOW()",
                (user_id, tier, bot_access_str, tier, bot_access_str),
            )
        elif tier:
            execute(
                f"INSERT INTO user_subscriptions (user_id, tier, status) VALUES ({P}, {P}, 'active') "
                f"ON CONFLICT (user_id) DO UPDATE SET tier = {P}, updated_at = NOW()",
                (user_id, tier, tier),
            )
        elif bot_access_str is not None:
            execute(
                f"UPDATE user_subscriptions SET bot_access = {P}, updated_at = NOW() WHERE user_id = {P}",
                (bot_access_str, user_id),
            )
    else:
        existing = query_one(f"SELECT id FROM user_subscriptions WHERE user_id = {P}", (user_id,))
        if existing:
            parts = []
            params = []
            if tier:
                parts.append(f"tier = {P}")
                params.append(tier)
            if bot_access_str is not None:
                parts.append(f"bot_access = {P}")
                params.append(bot_access_str)
            parts.append("updated_at = datetime('now')")
            params.append(user_id)
            execute(f"UPDATE user_subscriptions SET {', '.join(parts)} WHERE user_id = {P}", params)
        else:
            execute(
                f"INSERT INTO user_subscriptions (user_id, tier, bot_access, status) VALUES ({P}, {P}, {P}, 'active')",
                (user_id, tier or "free", bot_access_str or "none"),
            )

    return jsonify({"ok": True, "tier": tier, "bot_access": bot_access_str})


@bp.route("/api/admin/usage")
@admin_required
def api_admin_usage():
    """Admin-only: view LLM usage across all users."""
    rows = query(
        f"SELECT u.id, u.email, u.name, s.tier, "
        f"(SELECT COUNT(*) FROM llm_usage_log l WHERE l.user_id = u.id AND l.called_at >= {P}) as calls_24h "
        f"FROM users u LEFT JOIN user_subscriptions s ON u.id = s.user_id "
        f"ORDER BY calls_24h DESC",
        ((datetime.utcnow() - timedelta(hours=24)).isoformat(),),
    )
    return jsonify([{
        "id": r["id"], "email": r["email"], "name": r["name"],
        "tier": r["tier"] or "free", "calls_24h": r["calls_24h"],
        "limit": TIER_LIMITS.get(r["tier"] or "free", TIER_LIMITS["free"])["limit"],
    } for r in rows])


@bp.route("/api/admin/export")
@admin_required
def api_admin_export():
    """Admin-only: export data as grouped JSON with metadata."""
    from flask import current_app

    dataset = request.args.get("dataset", "trades")

    def _parse_json_field(val):
        if val and isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                return val
        return val

    records = []

    if dataset == "trades":
        rows = query(
            "SELECT bt.*, u.email as user_email FROM bot_trades bt "
            "JOIN users u ON u.id = bt.user_id "
            "ORDER BY bt.opened_at DESC"
        )
        # Group by asset_type (crypto vs stock)
        groups = {}
        for r in rows:
            asset_type = r.get("asset_type", "crypto")
            groups.setdefault(asset_type, [])
            record = {
                "id": r["id"], "user_email": r["user_email"],
                "coin": r["coin"], "asset_type": asset_type,
                "side": r["side"], "size": r["size"],
                "entry_price": r["entry_price"], "exit_price": r["exit_price"],
                "pnl": r["pnl"], "pnl_pct": r["pnl_pct"],
                "status": r["status"], "strategy": r.get("strategy"),
                "signal_reason": r.get("signal_reason"),
                "validation_result": _parse_json_field(r.get("validation_result")),
                "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
                "direction_bias": r.get("direction_bias"),
                "opened_at": r["opened_at"], "closed_at": r["closed_at"],
            }
            groups[asset_type].append(record)

        export_data = {
            "dataset": "trades",
            "exported_at": datetime.now().isoformat(),
            "total_count": len(rows),
            "groups": {k: {"count": len(v), "records": v} for k, v in groups.items()},
        }

    elif dataset == "analyses":
        rows = query(
            "SELECT s.*, u.email as user_email FROM searches s "
            "JOIN users u ON u.id = s.user_id "
            "ORDER BY s.created_at DESC"
        )
        # Group by ticker
        groups = {}
        for r in rows:
            ticker = r["ticker"]
            groups.setdefault(ticker, [])
            groups[ticker].append({
                "id": r["id"], "user_email": r["user_email"],
                "ticker": ticker, "period": r.get("period"),
                "interval": r.get("interval_val"),
                "result": _parse_json_field(r.get("result_json")),
                "created_at": r["created_at"],
            })

        export_data = {
            "dataset": "analyses",
            "exported_at": datetime.now().isoformat(),
            "total_count": len(rows),
            "tickers_count": len(groups),
            "groups": {k: {"count": len(v), "records": v} for k, v in groups.items()},
        }

    elif dataset == "bot_logs":
        rows = query(
            "SELECT bl.*, u.email as user_email FROM bot_log bl "
            "JOIN users u ON u.id = bl.user_id "
            "WHERE bl.details IS NOT NULL "
            "ORDER BY bl.created_at DESC LIMIT 50000"
        )
        # Group by source (crypto vs stock) and level
        groups = {}
        for r in rows:
            source = r.get("source", "unknown")
            level = r["level"]
            group_key = f"{source}_{level}"
            groups.setdefault(group_key, [])
            groups[group_key].append({
                "id": r["id"], "user_email": r["user_email"],
                "level": level, "message": r["message"],
                "details": _parse_json_field(r.get("details")),
                "source": source,
                "created_at": r["created_at"],
            })

        export_data = {
            "dataset": "bot_logs",
            "exported_at": datetime.now().isoformat(),
            "total_count": len(rows),
            "groups": {k: {"count": len(v), "records": v} for k, v in groups.items()},
        }

    elif dataset == "llm_usage":
        rows = query(
            "SELECT l.*, u.email as user_email FROM llm_usage_log l "
            "JOIN users u ON u.id = l.user_id "
            "ORDER BY l.called_at DESC"
        )
        # Group by model
        groups = {}
        for r in rows:
            model = r["model"] or "unknown"
            groups.setdefault(model, [])
            groups[model].append({
                "id": r["id"], "user_email": r["user_email"],
                "call_source": r["call_source"], "model": model,
                "called_at": r["called_at"],
            })

        export_data = {
            "dataset": "llm_usage",
            "exported_at": datetime.now().isoformat(),
            "total_count": len(rows),
            "models_count": len(groups),
            "groups": {k: {"count": len(v), "records": v} for k, v in groups.items()},
        }

    elif dataset == "journal":
        rows = query(
            "SELECT j.*, u.email as user_email FROM journal_entries j "
            "JOIN users u ON u.id = j.user_id "
            "ORDER BY j.created_at DESC"
        )
        # Group by ticker
        groups = {}
        for r in rows:
            ticker = r["ticker"] or "notes"
            groups.setdefault(ticker, [])
            groups[ticker].append({
                "id": r["id"], "user_email": r["user_email"],
                "ticker": r["ticker"], "action": r.get("action"),
                "notes": r.get("notes"),
                "entry_price": r.get("entry_price"), "exit_price": r.get("exit_price"),
                "shares": r.get("shares"), "pnl": r.get("pnl"),
                "created_at": r["created_at"],
            })

        export_data = {
            "dataset": "journal",
            "exported_at": datetime.now().isoformat(),
            "total_count": len(rows),
            "tickers_count": len(groups),
            "groups": {k: {"count": len(v), "records": v} for k, v in groups.items()},
        }

    elif dataset == "daily_pnl":
        rows = query(
            "SELECT p.*, u.email as user_email FROM bot_daily_pnl p "
            "JOIN users u ON u.id = p.user_id "
            "ORDER BY p.date DESC"
        )
        # Group by user
        groups = {}
        for r in rows:
            user = r["user_email"]
            groups.setdefault(user, [])
            groups[user].append({
                "id": r["id"], "user_email": user,
                "date": r["date"], "total_pnl": r["total_pnl"],
                "trade_count": r["trade_count"],
                "win_count": r["win_count"], "loss_count": r["loss_count"],
            })

        export_data = {
            "dataset": "daily_pnl",
            "exported_at": datetime.now().isoformat(),
            "total_count": len(rows),
            "users_count": len(groups),
            "groups": {k: {"count": len(v), "records": v} for k, v in groups.items()},
        }

    elif dataset == "all":
        # Combined grouped export
        trade_rows = query(
            "SELECT bt.*, u.email as user_email FROM bot_trades bt "
            "JOIN users u ON u.id = bt.user_id "
            "WHERE bt.status = 'closed' AND bt.validation_result IS NOT NULL "
            "ORDER BY bt.opened_at DESC"
        )
        trades_by_outcome = {"wins": [], "losses": []}
        for r in trade_rows:
            record = {
                "coin": r["coin"], "asset_type": r.get("asset_type", "crypto"),
                "side": r["side"], "strategy": r.get("strategy"),
                "signal_reason": r.get("signal_reason"),
                "validation": _parse_json_field(r.get("validation_result")),
                "entry_price": r["entry_price"], "exit_price": r["exit_price"],
                "pnl": r["pnl"], "pnl_pct": r["pnl_pct"],
                "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
                "opened_at": r["opened_at"], "closed_at": r["closed_at"],
            }
            if (r["pnl"] or 0) > 0:
                trades_by_outcome["wins"].append(record)
            else:
                trades_by_outcome["losses"].append(record)

        analysis_rows = query(
            "SELECT s.ticker, s.result_json, s.created_at FROM searches s "
            "WHERE s.result_json IS NOT NULL "
            "ORDER BY s.created_at DESC"
        )
        analyses_by_ticker = {}
        for r in analysis_rows:
            ticker = r["ticker"]
            analyses_by_ticker.setdefault(ticker, [])
            result = _parse_json_field(r.get("result_json"))
            if result:
                analyses_by_ticker[ticker].append({
                    "ticker": ticker,
                    "result": result,
                    "created_at": r["created_at"],
                })

        export_data = {
            "dataset": "all",
            "exported_at": datetime.now().isoformat(),
            "trades": {
                "total": len(trade_rows),
                "wins": {"count": len(trades_by_outcome["wins"]), "records": trades_by_outcome["wins"]},
                "losses": {"count": len(trades_by_outcome["losses"]), "records": trades_by_outcome["losses"]},
            },
            "analyses": {
                "total": len(analysis_rows),
                "tickers_count": len(analyses_by_ticker),
                "by_ticker": {k: {"count": len(v), "records": v} for k, v in analyses_by_ticker.items()},
            },
        }
    else:
        return jsonify({"error": f"Unknown dataset: {dataset}. Use: trades, analyses, bot_logs, llm_usage, journal, daily_pnl, all"}), 400

    content = json.dumps(export_data, default=str, indent=2)
    filename = f"export_{dataset}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    return current_app.response_class(
        response=content,
        status=200,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─── Helpers ────────────────────────────────────────────────────────────

def _reload_module_globals(updated_keys: dict):
    """Hot-reload module-level globals after config update."""
    import ai_validator as av
    import crypto_bot.crypto_validator as cv
    import screener as scr
    import market_sensor as ms
    import skills.llm_adapter as skl

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
        elif key == "LLM_SUPERVISOR":
            av.LLM_SUPERVISOR = val
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
        elif key == "LLM_SKILL":
            skl.LLM_SKILL = val
        elif key == "LLM_SKILL_EARNINGS":
            skl.LLM_SKILL_EARNINGS = val
        elif key == "BOT_SENSOR_ENABLED":
            ms.BOT_SENSOR_ENABLED = val == "1"


# ─── Global bot defaults (admin-set, user_id=0 sentinel) ─────

@bp.route("/api/admin/bot-defaults", methods=["GET"])
@admin_required
def api_admin_bot_defaults_get():
    """List all global bot defaults — rows in bot_config with user_id=0.

    Per-user rows override these at read time via the helpers in each bot
    engine (see _cfg / _get_config / _wd_config — all use
    `WHERE user_id IN (X, 0) ORDER BY CASE WHEN user_id = 0 THEN 1 ELSE 0 END LIMIT 1`,
    sign-agnostic so a user-scoped row always wins over the global row).
    """
    rows = query("SELECT key, value FROM bot_config WHERE user_id = 0 ORDER BY key")
    return jsonify({"defaults": [{"key": r["key"], "value": r["value"]} for r in rows or []]})


def _ensure_global_user():
    """Bootstrap a sentinel user with id=0 so bot_config rows with user_id=0
    satisfy the bot_config_user_id_fkey FK. Idempotent. Sentinel is excluded
    from /api/admin/users so it never appears in the admin UI."""
    try:
        if IS_POSTGRES:
            execute(
                "INSERT INTO users (id, email, name) VALUES (0, 'system@global.local', 'Global Bot Defaults') "
                "ON CONFLICT (id) DO NOTHING"
            )
        else:
            execute(
                "INSERT OR IGNORE INTO users (id, email, name) VALUES (0, 'system@global.local', 'Global Bot Defaults')"
            )
    except Exception as e:
        logger.warning(f"_ensure_global_user failed: {e}")


@bp.route("/api/admin/bot-defaults", methods=["POST"])
@admin_required
def api_admin_bot_defaults_set():
    """Upsert a global default. Body: {"key": "cb_broker", "value": "alpaca"}."""
    data = request.get_json() or {}
    key = (data.get("key") or "").strip()
    value = data.get("value")
    if not key:
        return jsonify({"error": "key is required"}), 400
    if value is None:
        return jsonify({"error": "value is required"}), 400
    value = str(value)
    _ensure_global_user()
    if IS_POSTGRES:
        execute(
            f"INSERT INTO bot_config (user_id, key, value) VALUES (0, {P}, {P}) "
            f"ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )
    else:
        execute(
            f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES (0, {P}, {P})",
            (key, value),
        )
    logger.info(f"Admin {_uid()} set global default {key}={value}")
    return jsonify({"ok": True, "key": key, "value": value})


@bp.route("/api/admin/bot-defaults/<key>", methods=["DELETE"])
@admin_required
def api_admin_bot_defaults_delete(key):
    """Clear a global default. Per-user values still apply unchanged."""
    execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {P}", (key,))
    logger.info(f"Admin {_uid()} cleared global default {key}")
    return jsonify({"ok": True, "key": key})


# ─── Delete user (admin-only, with self-delete guard) ────────

@bp.route("/api/admin/users/<int:user_id>/delete", methods=["DELETE"])
@admin_required
def api_admin_user_delete(user_id):
    """Hard-delete a user. Cascades to bot_config, bot_trades, journal_entries,
    user_roles, user_subscriptions, etc. via ON DELETE CASCADE FKs in
    migrations.py (line ~68 onward).

    Refuses to delete the requesting admin themselves to prevent lockout.
    Refuses user_id=0 (the sentinel for global bot defaults).
    """
    actor = _uid()
    if user_id == actor:
        return jsonify({"error": "Cannot delete your own account from the admin panel"}), 400
    if user_id == 0:
        return jsonify({"error": "user_id 0 is reserved for global bot defaults"}), 400

    target = query_one(f"SELECT id, email FROM users WHERE id = {P}", (user_id,))
    if not target:
        return jsonify({"error": "User not found"}), 404

    try:
        execute(f"DELETE FROM users WHERE id = {P}", (user_id,))
    except Exception as e:
        logger.exception(f"Failed to delete user {user_id}")
        return jsonify({"error": f"Delete failed: {e}"}), 500

    logger.warning(f"Admin {actor} hard-deleted user {user_id} ({target.get('email')})")
    return jsonify({"ok": True, "deleted_user_id": user_id, "email": target.get("email")})


# ─── LLM model overrides + snapshot/revert ─────────────────────────────
#
# The admin UI uses these to swap in cheaper/free models, save snapshots
# before risky changes, and one-click revert if results regress. Resolution
# precedence at runtime: DB override → env var → hardcoded default
# (see shared/llm_config.py).

@bp.route("/api/admin/llm-models", methods=["GET"])
@admin_required
def api_admin_llm_models():
    from shared.llm_config import get_all_models, list_snapshots
    return jsonify({
        "models": get_all_models(),
        "snapshots": list_snapshots(limit=50),
    })


@bp.route("/api/admin/llm-models/set", methods=["POST"])
@admin_required
def api_admin_llm_models_set():
    """Body: {"role": "research", "model": "deepseek/deepseek-chat-v3.1"}.
    Empty model clears the override (falls back to env/default)."""
    from shared.llm_config import set_override, KNOWN_ROLES
    data = request.get_json(silent=True) or {}
    role = data.get("role", "")
    model = data.get("model", "")
    if role not in KNOWN_ROLES:
        return jsonify({"error": f"Unknown role. Valid: {sorted(KNOWN_ROLES)}"}), 400
    try:
        set_override(role, model)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    logger.info(f"Admin {_uid()} set LLM override role={role} model={model or '(cleared)'}")
    return jsonify({"ok": True, "role": role, "model": model})


@bp.route("/api/admin/llm-models/snapshot", methods=["POST"])
@admin_required
def api_admin_llm_models_snapshot():
    """Save current resolved models as a named snapshot. Body: {"label": "..."}"""
    from shared.llm_config import save_snapshot
    data = request.get_json(silent=True) or {}
    label = data.get("label", "")
    snap_id = save_snapshot(label)
    logger.info(f"Admin {_uid()} saved LLM snapshot id={snap_id} label={label!r}")
    return jsonify({"ok": True, "id": snap_id})


@bp.route("/api/admin/llm-models/revert", methods=["POST"])
@admin_required
def api_admin_llm_models_revert():
    """Revert all overrides to a saved snapshot. Body: {"snapshot_id": 42}"""
    from shared.llm_config import revert_to_snapshot
    data = request.get_json(silent=True) or {}
    snap_id = data.get("snapshot_id")
    if not snap_id:
        return jsonify({"error": "snapshot_id required"}), 400
    try:
        revert_to_snapshot(snap_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    logger.warning(f"Admin {_uid()} reverted LLM models to snapshot {snap_id}")
    return jsonify({"ok": True, "snapshot_id": snap_id})


@bp.route("/api/admin/llm-models/clear", methods=["POST"])
@admin_required
def api_admin_llm_models_clear():
    """Wipe ALL DB overrides — every role falls back to env/default."""
    from shared.llm_config import clear_all_overrides
    clear_all_overrides()
    logger.warning(f"Admin {_uid()} cleared all LLM overrides — back to env/defaults")
    return jsonify({"ok": True})


@bp.route("/api/admin/llm-models/snapshot/<int:snap_id>", methods=["DELETE"])
@admin_required
def api_admin_llm_models_snapshot_delete(snap_id):
    from shared.llm_config import delete_snapshot
    delete_snapshot(snap_id)
    return jsonify({"ok": True, "deleted_id": snap_id})


# ─── Ollama Cloud config — SaaS-friendly, DB-backed ────────────────────
#
# These endpoints let an admin edit the Ollama Cloud URL / API key / default
# model from the UI without touching .env. Settings persist in bot_config
# (user_id=0) and are read by the gate-1 / claude_gating / watchdog_gating
# call sites via shared.runtime_config.

_OLLAMA_KEYS = ("ollama_url", "ollama_api_key", "ollama_model")
_OLLAMA_ENV_ALIASES = {
    "ollama_url":     ("OLLAMA_URL",),
    "ollama_api_key": ("OLLAMA_API_KEY",),
    "ollama_model":   ("OLLAMA_MODEL",),
}
_OLLAMA_DEFAULTS = {
    "ollama_url":     "https://ollama.com",
    "ollama_api_key": "",
    "ollama_model":   "gpt-oss:20b",
}


@bp.route("/api/admin/ollama-config", methods=["GET"])
@admin_required
def api_admin_ollama_config_get():
    from shared.runtime_config import get_setting, get_source, mask_secret
    out = {}
    for k in _OLLAMA_KEYS:
        val = get_setting(k, _OLLAMA_DEFAULTS[k], env_aliases=_OLLAMA_ENV_ALIASES[k])
        src = get_source(k, env_aliases=_OLLAMA_ENV_ALIASES[k])
        if k == "ollama_api_key":
            out[k] = {"value_masked": mask_secret(val), "is_set": bool(val), "source": src}
        else:
            out[k] = {"value": val, "source": src}
    return jsonify(out)


@bp.route("/api/admin/ollama-config/set", methods=["POST"])
@admin_required
def api_admin_ollama_config_set():
    """Body: any subset of {ollama_url, ollama_api_key, ollama_model}.
    Empty string clears the DB override (falls back to env / default)."""
    from shared.runtime_config import set_setting
    data = request.get_json(silent=True) or {}
    accepted = {k: data[k] for k in _OLLAMA_KEYS if k in data}
    if not accepted:
        return jsonify({"error": "no valid keys in payload"}), 400
    for k, v in accepted.items():
        set_setting(k, v)
    masked_keys = [k if k != "ollama_api_key" else "ollama_api_key=<set>"
                   for k in accepted.keys()]
    logger.info(f"Admin {_uid()} updated Ollama config: {masked_keys}")
    return jsonify({"ok": True, "updated": list(accepted.keys())})


@bp.route("/api/admin/ollama-config/test", methods=["POST"])
@admin_required
def api_admin_ollama_config_test():
    """Live probe of the currently-effective Ollama Cloud config."""
    import time
    import requests as _requests
    from shared.runtime_config import get_setting

    url = get_setting("ollama_url", _OLLAMA_DEFAULTS["ollama_url"],
                      env_aliases=_OLLAMA_ENV_ALIASES["ollama_url"])
    api_key = get_setting("ollama_api_key", "",
                          env_aliases=_OLLAMA_ENV_ALIASES["ollama_api_key"])
    if not api_key:
        return jsonify({"ok": False, "message": "API key not set", "latency_ms": 0}), 200
    start = time.time()
    try:
        resp = _requests.get(
            f"{url}/api/tags",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        latency = int((time.time() - start) * 1000)
        if resp.status_code == 200:
            return jsonify({"ok": True, "message": "Connected", "latency_ms": latency})
        return jsonify({"ok": False,
                        "message": f"HTTP {resp.status_code}: {resp.text[:160]}",
                        "latency_ms": latency}), 200
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return jsonify({"ok": False, "message": str(e)[:200], "latency_ms": latency}), 200


@bp.route("/api/admin/ollama-config/clear", methods=["POST"])
@admin_required
def api_admin_ollama_config_clear():
    """Clear all DB overrides for Ollama settings — falls back to env / default."""
    from shared.runtime_config import clear_setting
    for k in _OLLAMA_KEYS:
        clear_setting(k)
    logger.warning(f"Admin {_uid()} cleared all Ollama config overrides")
    return jsonify({"ok": True})


# ─── Per-user usage report — admin reporting ────────────────────────────
#
# Powered by llm_usage_log (one row per LLM call). Exports as JSON or CSV.

def _parse_date_window():
    """Read from/to query params (ISO YYYY-MM-DD or datetime). Defaults to last 30d.

    Returns (from_iso, to_iso, days) where days is the integer window size used
    for the 24h/7d/30d sub-aggregates.
    """
    now = datetime.utcnow()
    to_arg = request.args.get("to", "").strip()
    from_arg = request.args.get("from", "").strip()
    try:
        to_dt = datetime.fromisoformat(to_arg) if to_arg else now
    except ValueError:
        to_dt = now
    try:
        from_dt = datetime.fromisoformat(from_arg) if from_arg else (now - timedelta(days=30))
    except ValueError:
        from_dt = now - timedelta(days=30)
    if from_dt > to_dt:
        from_dt, to_dt = to_dt, from_dt
    return from_dt.isoformat(), to_dt.isoformat(), (to_dt - from_dt).days


def _users_usage_rows(from_iso, to_iso, tier_filter, email_q, sort, include_bot):
    """Build the aggregated per-user usage rows."""
    now = datetime.utcnow()
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    cutoff_7d  = (now - timedelta(days=7)).isoformat()
    cutoff_30d = (now - timedelta(days=30)).isoformat()

    where_clauses = []
    params = []
    if tier_filter:
        where_clauses.append(f"COALESCE(s.tier, 'free') = {P}")
        params.append(tier_filter)
    if email_q:
        where_clauses.append(f"LOWER(u.email) LIKE {P}")
        params.append(f"%{email_q.lower()}%")
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = f"""
        SELECT u.id, u.email, u.name, u.created_at as signup_date,
               COALESCE(s.tier, 'free') as tier,
               COALESCE(s.bot_access, 'none') as bot_access,
               (SELECT COUNT(*) FROM llm_usage_log l WHERE l.user_id = u.id) as calls_total,
               (SELECT COUNT(*) FROM llm_usage_log l WHERE l.user_id = u.id AND l.called_at >= {P}) as calls_24h,
               (SELECT COUNT(*) FROM llm_usage_log l WHERE l.user_id = u.id AND l.called_at >= {P}) as calls_7d,
               (SELECT COUNT(*) FROM llm_usage_log l WHERE l.user_id = u.id AND l.called_at >= {P}) as calls_30d,
               (SELECT COUNT(*) FROM llm_usage_log l
                  WHERE l.user_id = u.id AND l.called_at >= {P} AND l.called_at <= {P}) as calls_window,
               (SELECT MIN(l.called_at) FROM llm_usage_log l WHERE l.user_id = u.id) as first_seen,
               (SELECT MAX(l.called_at) FROM llm_usage_log l WHERE l.user_id = u.id) as last_seen,
               (SELECT l.model FROM llm_usage_log l
                  WHERE l.user_id = u.id AND l.called_at >= {P} AND l.called_at <= {P}
                  GROUP BY l.model ORDER BY COUNT(*) DESC LIMIT 1) as top_model,
               (SELECT l.call_source FROM llm_usage_log l
                  WHERE l.user_id = u.id AND l.called_at >= {P} AND l.called_at <= {P}
                  GROUP BY l.call_source ORDER BY COUNT(*) DESC LIMIT 1) as top_source
        FROM users u
        LEFT JOIN user_subscriptions s ON s.user_id = u.id AND s.status = 'active'
        {where_sql}
    """
    sub_params = [cutoff_24h, cutoff_7d, cutoff_30d,
                  from_iso, to_iso,
                  from_iso, to_iso,
                  from_iso, to_iso]
    rows = query(sql, tuple(sub_params + params))

    # Optional bot trade rollup (separate query to keep main aggregation cheap)
    bot_metrics = {}
    if include_bot:
        try:
            bt_rows = query(
                f"SELECT user_id, COUNT(*) as trades, COALESCE(SUM(pnl), 0) as pnl_total "
                f"FROM bot_trades WHERE opened_at >= {P} AND opened_at <= {P} GROUP BY user_id",
                (from_iso, to_iso),
            )
            bot_metrics = {r["user_id"]: r for r in bt_rows}
        except Exception as e:
            logger.warning(f"bot_trades rollup failed (table may not exist): {e}")

    # Roles (admin flag) — 1 query
    role_rows = query("SELECT user_id, role FROM user_roles WHERE role = 'admin'")
    admin_ids = {r["user_id"] for r in role_rows}

    out = []
    for r in rows:
        bt = bot_metrics.get(r["id"])
        item = {
            "user_id":      r["id"],
            "email":        r["email"],
            "name":         r["name"],
            "tier":         r["tier"],
            "bot_access":   r["bot_access"],
            "is_admin":     r["id"] in admin_ids,
            "calls_total":  r["calls_total"] or 0,
            "calls_24h":    r["calls_24h"] or 0,
            "calls_7d":     r["calls_7d"] or 0,
            "calls_30d":    r["calls_30d"] or 0,
            "calls_window": r["calls_window"] or 0,
            "top_model":    r["top_model"],
            "top_source":   r["top_source"],
            "first_seen":   r["first_seen"],
            "last_seen":    r["last_seen"],
            "signup_date":  r["signup_date"],
        }
        if include_bot:
            item["bot_trades_count"] = (bt and bt["trades"]) or 0
            item["bot_pnl_total"]    = float((bt and bt["pnl_total"]) or 0)
        out.append(item)

    # Sort
    key_map = {
        "calls_desc":     lambda x: -x["calls_window"],
        "calls_30d_desc": lambda x: -x["calls_30d"],
        "last_seen_desc": lambda x: x["last_seen"] or "",
        "email_asc":      lambda x: (x["email"] or "").lower(),
        "tier_desc":      lambda x: {"admin": 4, "pro": 3, "basic": 2, "starter": 2, "free": 1}.get(x["tier"], 0) * -1,
    }
    sort_key = key_map.get(sort, key_map["calls_desc"])
    out.sort(key=sort_key, reverse=(sort == "last_seen_desc"))
    return out


@bp.route("/api/admin/users/usage")
@admin_required
def api_admin_users_usage():
    """Per-user usage report. Query params:
      from=YYYY-MM-DD, to=YYYY-MM-DD (default last 30d, UTC)
      tier=free|basic|pro|...
      q=email-substring
      sort=calls_desc|calls_30d_desc|last_seen_desc|email_asc|tier_desc
      include_bot=1 (adds bot_trades_count + bot_pnl_total)
      format=json (default) | csv
    """
    from_iso, to_iso, _days = _parse_date_window()
    tier_filter = (request.args.get("tier") or "").strip().lower() or None
    email_q = (request.args.get("q") or "").strip()
    sort = (request.args.get("sort") or "calls_desc").strip()
    include_bot = request.args.get("include_bot") in ("1", "true", "yes")
    fmt = (request.args.get("format") or "json").lower()

    rows = _users_usage_rows(from_iso, to_iso, tier_filter, email_q, sort, include_bot)

    if fmt == "csv":
        from flask import Response
        buf = StringIO()
        cols = ["user_id", "email", "name", "tier", "bot_access", "is_admin",
                "calls_total", "calls_window", "calls_24h", "calls_7d", "calls_30d",
                "top_model", "top_source", "first_seen", "last_seen", "signup_date"]
        if include_bot:
            cols += ["bot_trades_count", "bot_pnl_total"]
        buf.write(",".join(cols) + "\n")
        for r in rows:
            vals = []
            for c in cols:
                v = r.get(c, "")
                if v is None:
                    v = ""
                v = str(v).replace('"', '""')
                if "," in v or "\n" in v or '"' in v:
                    v = f'"{v}"'
                vals.append(v)
            buf.write(",".join(vals) + "\n")
        fname = f"usage_{from_iso[:10]}_{to_iso[:10]}.csv"
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    return jsonify({
        "from": from_iso,
        "to": to_iso,
        "include_bot": include_bot,
        "count": len(rows),
        "rows": rows,
    })


@bp.route("/api/admin/users/<int:user_id>/usage")
@admin_required
def api_admin_user_usage_detail(user_id):
    """Drill-down: daily call buckets + recent raw events for one user."""
    from_iso, to_iso, _ = _parse_date_window()

    user = query_one(
        f"SELECT u.id, u.email, u.name, COALESCE(s.tier, 'free') as tier "
        f"FROM users u LEFT JOIN user_subscriptions s ON s.user_id = u.id AND s.status = 'active' "
        f"WHERE u.id = {P}",
        (user_id,),
    )
    if not user:
        return jsonify({"error": "user not found"}), 404

    if IS_POSTGRES:
        daily_sql = (
            f"SELECT to_char(called_at::timestamp, 'YYYY-MM-DD') as day, COUNT(*) as cnt "
            f"FROM llm_usage_log WHERE user_id = {P} AND called_at >= {P} AND called_at <= {P} "
            f"GROUP BY day ORDER BY day"
        )
    else:
        daily_sql = (
            f"SELECT substr(called_at, 1, 10) as day, COUNT(*) as cnt "
            f"FROM llm_usage_log WHERE user_id = {P} AND called_at >= {P} AND called_at <= {P} "
            f"GROUP BY day ORDER BY day"
        )
    daily = query(daily_sql, (user_id, from_iso, to_iso))

    by_model = query(
        f"SELECT model, COUNT(*) as cnt FROM llm_usage_log "
        f"WHERE user_id = {P} AND called_at >= {P} AND called_at <= {P} "
        f"GROUP BY model ORDER BY cnt DESC LIMIT 20",
        (user_id, from_iso, to_iso),
    )
    by_source = query(
        f"SELECT call_source, COUNT(*) as cnt FROM llm_usage_log "
        f"WHERE user_id = {P} AND called_at >= {P} AND called_at <= {P} "
        f"GROUP BY call_source ORDER BY cnt DESC LIMIT 20",
        (user_id, from_iso, to_iso),
    )
    recent = query(
        f"SELECT called_at, call_source, model FROM llm_usage_log "
        f"WHERE user_id = {P} ORDER BY called_at DESC LIMIT 100",
        (user_id,),
    )

    return jsonify({
        "user": dict(user) if hasattr(user, "keys") else user,
        "from": from_iso,
        "to": to_iso,
        "daily":     [{"day": d["day"], "cnt": d["cnt"]} for d in daily],
        "by_model":  [{"model": m["model"], "cnt": m["cnt"]} for m in by_model],
        "by_source": [{"source": s["call_source"], "cnt": s["cnt"]} for s in by_source],
        "recent":    [{"called_at": r["called_at"], "source": r["call_source"],
                       "model": r["model"]} for r in recent],
    })


# ─── Platform (Docker vs Cloud Run) ─────────────────────────────────────

@bp.route("/api/admin/platform", methods=["GET"])
@admin_required
def api_admin_platform_get():
    from shared.platform_config import get_platform
    return jsonify(get_platform())


@bp.route("/api/admin/platform/set", methods=["POST"])
@admin_required
def api_admin_platform_set():
    """Body: {target: 'docker' | 'cloud_run' | 'auto'}.
    'auto' (or empty) clears the override and falls back to env-based detection."""
    from shared.runtime_config import set_setting, clear_setting
    from shared.platform_config import VALID_TARGETS, get_platform
    data = request.get_json(silent=True) or {}
    target = (data.get("target") or "").strip().lower()
    if target not in VALID_TARGETS and target != "":
        return jsonify({"error": f"target must be one of {VALID_TARGETS}"}), 400
    if target in ("", "auto"):
        clear_setting("deployment_target")
    else:
        set_setting("deployment_target", target)
    logger.warning(f"Admin {_uid()} set deployment_target={target or 'auto'}")
    return jsonify({"ok": True, "platform": get_platform()})

