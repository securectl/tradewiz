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
    valid_bots = {"crypto", "stock"}
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
    valid_bots = {"crypto", "stock"}
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
    rows = query("SELECT * FROM users ORDER BY created_at DESC")
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
        valid_bots = {"crypto", "stock"}
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
