"""Feature-flag API — per-user resolution (for the frontend to gate UI) and
admin management of the canary rollout.

  GET  /api/feature-flags        — {flag: enabled_bool} for the current user
  GET  /api/admin/feature-flags  — all flags + their state/rollout (admin)
  POST /api/admin/feature-flags  — set a flag's state/rollout_pct (admin)
"""

import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user

from decorators import login_required, admin_required
import feature_flags as ff

logger = logging.getLogger(__name__)
bp = Blueprint("feature_flags", __name__)


@bp.route("/api/feature-flags")
@login_required
def my_flags():
    """Flags resolved for the current user — the frontend uses this to show/hide
    canary features."""
    return jsonify(ff.enabled_map(
        user_id=getattr(current_user, "id", None),
        roles=getattr(current_user, "roles", []) or [],
    ))


@bp.route("/api/admin/feature-flags")
@admin_required
def list_flags():
    return jsonify({"flags": ff.all_flags(force=True), "states": list(ff.STATES)})


@bp.route("/api/admin/feature-flags", methods=["POST"])
@admin_required
def update_flag():
    data = request.get_json(silent=True) or {}
    flag = (data.get("flag") or "").strip()
    state = (data.get("state") or "").strip()
    rollout_pct = data.get("rollout_pct", 0)
    if not flag:
        return jsonify({"ok": False, "error": "flag name required"}), 400
    if state not in ff.STATES:
        return jsonify({"ok": False, "error": f"state must be one of {list(ff.STATES)}"}), 400
    try:
        result = ff.set_flag(flag, state, rollout_pct)
        return jsonify({"ok": True, "flag": result})
    except Exception as e:
        logger.error("set_flag failed: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
