"""
Daily-alert API routes.

  GET  /api/alerts/status     — is email configured + is the current user opted in
  POST /api/alerts/optin      — toggle the current user into/out of the daily digest
  GET  /api/alerts/preview    — build today's digest (signals + rendered HTML)
  POST /api/alerts/send-test  — email today's digest to the current user only
"""
import logging

from flask import Blueprint, jsonify, request

from decorators import login_required
from shared.helpers import _uid, _upsert_bot_config
import alerts as alerts_mod

bp = Blueprint("alerts", __name__)
log = logging.getLogger(__name__)


@bp.route("/api/alerts/status")
@login_required
def alerts_status():
    from shared.mailer import is_configured
    from db import query_one, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    optin = False
    try:
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = {P}",
                        (_uid(), "alert_emails"))
        optin = bool(row and row.get("value") == "1")
    except Exception:
        pass
    return jsonify({"email_configured": is_configured(), "optin": optin})


@bp.route("/api/alerts/optin", methods=["POST"])
@login_required
def alerts_optin():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    _upsert_bot_config(_uid(), "alert_emails", "1" if enabled else "0")
    return jsonify({"ok": True, "optin": enabled})


@bp.route("/api/alerts/preview")
@login_required
def alerts_preview():
    data = alerts_mod.collect_alerts()
    subject, html = alerts_mod.build_daily_digest(
        data["volume_spikes"], data["oversold"], data["earnings"])
    return jsonify({"subject": subject, "html": html, "signals": data})


@bp.route("/api/alerts/send-test", methods=["POST"])
@login_required
def alerts_send_test():
    from shared.mailer import send_email, is_configured
    from db import query_one, IS_POSTGRES
    if not is_configured():
        return jsonify({"ok": False,
                        "error": "No email provider configured. Set RESEND_API_KEY and EMAIL_FROM."}), 200
    P = "%s" if IS_POSTGRES else "?"
    row = query_one(f"SELECT email FROM users WHERE id = {P}", (_uid(),))
    email = row.get("email") if row else None
    if not email:
        return jsonify({"ok": False, "error": "No email on file for your account."}), 200
    data = alerts_mod.collect_alerts()
    subject, html = alerts_mod.build_daily_digest(
        data["volume_spikes"], data["oversold"], data["earnings"])
    ok = send_email(email, "[TEST] " + subject, html)
    return jsonify({"ok": ok, "to": email, "signals": data})
