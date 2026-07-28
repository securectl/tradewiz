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
    sms_optin = False
    phone_masked = None
    phone_verified = False
    try:
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = {P}",
                        (_uid(), "alert_emails"))
        optin = bool(row and row.get("value") == "1")
        srow = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = {P}",
                         (_uid(), "alert_sms"))
        sms_optin = bool(srow and srow.get("value") == "1")
        urow = query_one(f"SELECT phone, phone_verified FROM users WHERE id = {P}", (_uid(),))
        if urow and urow.get("phone"):
            p = urow["phone"]
            phone_masked = ("*" * max(0, len(p) - 4)) + p[-4:]
            phone_verified = urow.get("phone_verified") in (True, 1)
    except Exception:
        pass
    try:
        from shared.sms import is_configured as sms_configured
        sms_on = sms_configured()
    except Exception:
        sms_on = False
    return jsonify({"email_configured": is_configured(), "optin": optin,
                    "sms_configured": sms_on, "sms_optin": sms_optin,
                    "phone": phone_masked, "phone_verified": phone_verified})


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


# ── SMS alerts ────────────────────────────────────────────────────────────
import secrets as _secrets
from datetime import datetime, timedelta

_CODE_TTL_MIN = 10
_MAX_ATTEMPTS = 5


def _mask_phone(p):
    if not p:
        return None
    return ("*" * max(0, len(p) - 4)) + p[-4:]


@bp.route("/api/alerts/phone", methods=["POST"])
@login_required
def alerts_set_phone():
    """Store a phone number and text it a 6-digit verification code."""
    from shared.sms import send_sms, is_configured, normalize_e164
    from db import execute, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    data = request.get_json(silent=True) or {}
    phone = normalize_e164(data.get("phone"))
    if not phone:
        return jsonify({"ok": False, "error": "Enter a valid phone number (e.g. +15551234567)."}), 200
    if not is_configured():
        return jsonify({"ok": False, "error": "SMS is not configured yet. Set the Twilio env vars."}), 200

    code = f"{_secrets.randbelow(1000000):06d}"
    expires = (datetime.utcnow() + timedelta(minutes=_CODE_TTL_MIN)).isoformat()
    # Store phone (unverified) + the pending code.
    execute(f"UPDATE users SET phone = {P}, phone_verified = {P} WHERE id = {P}",
            (phone, False if IS_POSTGRES else 0, _uid()))
    execute(
        f"INSERT INTO phone_verifications (user_id, phone, code, attempts, expires_at) "
        f"VALUES ({P},{P},{P},0,{P}) "
        f"ON CONFLICT(user_id) DO UPDATE SET phone=excluded.phone, code=excluded.code, "
        f"attempts=0, expires_at=excluded.expires_at",
        (_uid(), phone, code, expires),
    )
    sent = send_sms(phone, f"Your TradeWiz verification code is {code}. Expires in {_CODE_TTL_MIN} min.")
    return jsonify({"ok": bool(sent), "phone": _mask_phone(phone),
                    "error": None if sent else "Could not send the code. Check the number and try again."})


@bp.route("/api/alerts/phone/verify", methods=["POST"])
@login_required
def alerts_verify_phone():
    """Verify the 6-digit code and mark the phone verified."""
    from db import query_one, execute, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    row = query_one(
        f"SELECT phone, code, attempts, expires_at FROM phone_verifications WHERE user_id = {P}",
        (_uid(),),
    )
    if not row:
        return jsonify({"ok": False, "error": "Request a code first."}), 200
    if int(row.get("attempts") or 0) >= _MAX_ATTEMPTS:
        return jsonify({"ok": False, "error": "Too many attempts. Request a new code."}), 200
    if str(row.get("expires_at") or "") < datetime.utcnow().isoformat():
        return jsonify({"ok": False, "error": "Code expired. Request a new one."}), 200
    if not code or not _secrets.compare_digest(str(code), str(row.get("code"))):
        execute(f"UPDATE phone_verifications SET attempts = attempts + 1 WHERE user_id = {P}", (_uid(),))
        return jsonify({"ok": False, "error": "Incorrect code."}), 200

    execute(f"UPDATE users SET phone_verified = {P} WHERE id = {P}",
            (True if IS_POSTGRES else 1, _uid()))
    execute(f"DELETE FROM phone_verifications WHERE user_id = {P}", (_uid(),))
    # Verifying implies consent to receive alerts — opt in.
    _upsert_bot_config(_uid(), "alert_sms", "1")
    return jsonify({"ok": True, "verified": True, "sms_optin": True})


@bp.route("/api/alerts/sms-optin", methods=["POST"])
@login_required
def alerts_sms_optin():
    """Toggle SMS alert opt-in (requires a verified phone to enable)."""
    from db import query_one, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
    if enabled:
        row = query_one(f"SELECT phone_verified FROM users WHERE id = {P}", (_uid(),))
        if not (row and (row.get("phone_verified") in (True, 1))):
            return jsonify({"ok": False, "error": "Verify a phone number first."}), 200
    _upsert_bot_config(_uid(), "alert_sms", "1" if enabled else "0")
    return jsonify({"ok": True, "sms_optin": enabled})


@bp.route("/api/sms/inbound", methods=["POST"])
def sms_inbound():
    """Twilio inbound webhook — honor STOP/UNSUBSCRIBE by clearing the opt-in.

    No auth (Twilio posts here). Twilio also auto-handles STOP on its numbers;
    this keeps our own opt-in state in sync so the digest loop stops targeting."""
    from db import query_one, execute, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    body = (request.form.get("Body") or "").strip().upper()
    from_num = (request.form.get("From") or "").strip()
    if body in ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT") and from_num:
        try:
            row = query_one(f"SELECT id FROM users WHERE phone = {P}", (from_num,))
            if row:
                _upsert_bot_config(row["id"], "alert_sms", "0")
        except Exception:
            pass
    # Empty TwiML — acknowledge without auto-replying (Twilio sends its own STOP ack).
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response></Response>", 200,
            {"Content-Type": "text/xml"})
