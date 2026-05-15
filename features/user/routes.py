"""
User API routes — profile, settings.
Extracted from app.py.
"""

from flask import Blueprint, jsonify, request
from flask_login import current_user
from shared.helpers import _uid, P, _upsert_api_key
from decorators import login_required
from db import query, query_one, execute, IS_POSTGRES

bp = Blueprint("user", __name__)


@bp.route("/api/me")
@login_required
def api_me():
    """Return current user info + roles + bot access + trial status."""
    data = current_user.to_dict()
    # Add bot_access from subscription
    sub_row = query_one(
        f"SELECT bot_access FROM user_subscriptions WHERE user_id = {P} AND status = 'active'",
        (current_user.id,),
    )
    data["bot_access"] = sub_row["bot_access"] if sub_row and sub_row.get("bot_access") else "none"

    # Add trial status
    try:
        from trial_manager import get_trial_status
        data["trial"] = get_trial_status(current_user.id)
    except Exception:
        data["trial"] = {"trial_status": "none", "eligible": False}

    return jsonify(data)


@bp.route("/api/settings")
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

    # Expose under both keys for forward/backward compatibility — historical
    # frontend uses `api_keys`, server-internal name is `user_keys`.
    result = {"user_keys": user_keys, "api_keys": user_keys}

    # System config (LLM models) — visible to all, editable by admin
    import ai_validator as av
    import crypto_bot.crypto_validator as cv

    result["llm_models"] = {
        "LLM_RESEARCH": av.LLM_RESEARCH,
        "LLM_RESEARCH_FAST": av.LLM_RESEARCH_FAST,
        "LLM_PATTERN": av.LLM_PATTERN,
        "LLM_PREDICTION": av.LLM_PREDICTION,
        "LLM_SCREENER": av.LLM_SCREENER,
        "LLM_SUPERVISOR": av.LLM_SUPERVISOR,
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


@bp.route("/api/settings", methods=["POST"])
@login_required
def api_settings_save():
    """Save user API keys (encrypted per user)."""
    uid = _uid()
    data = request.get_json()

    from crypto_utils import encrypt

    # User broker keys — accept either field name (api_keys is the historical
    # frontend convention; user_keys is the server-internal name).
    user_keys = data.get("api_keys") or data.get("user_keys") or {}
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
