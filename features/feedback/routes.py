"""User feedback — industry-standard survey (NPS / CSAT / ease + open text).

POST /api/feedback        submit (login_required, stored against the user)
GET  /api/admin/feedback  read responses + aggregates (admin_required)
"""

import logging
import os
from html import escape

from flask import Blueprint, jsonify, request

from decorators import login_required, admin_required
from shared.helpers import _uid
from db import query, execute, IS_POSTGRES

logger = logging.getLogger(__name__)
bp = Blueprint("feedback", __name__)
P = "%s" if IS_POSTGRES else "?"


def _clamp_int(v, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))


def _feedback_recipients():
    """Where feedback notifications go. FEEDBACK_EMAIL overrides ADMIN_EMAIL."""
    raw = os.getenv("FEEDBACK_EMAIL") or os.getenv("ADMIN_EMAIL") or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def _user_email(user_id):
    if not user_id:
        return None
    try:
        rows = query(f"SELECT email FROM users WHERE id = {P}", (user_id,))
        if rows:
            return rows[0]["email"]
    except Exception as e:
        logger.warning(f"feedback user email lookup failed: {e}")
    return None


def _email_feedback(user_id, nps, csat, ease, valuable, improve, email, app_name="TradeWiz"):
    """Best-effort: email a feedback summary to the admin. Never raises."""
    try:
        from shared.mailer import send_email, is_configured
        if not is_configured():
            return
        recips = _feedback_recipients()
        if not recips:
            return

        contact = email or _user_email(user_id) or "unknown"

        def row(label, value):
            if value is None or value == "":
                return ""
            return (f'<tr><td style="padding:4px 12px 4px 0;color:#888;">{escape(label)}</td>'
                    f'<td style="padding:4px 0;">{escape(str(value))}</td></tr>')

        html = (
            f"<h2>New {escape(app_name)} feedback</h2>"
            f'<table style="border-collapse:collapse;font-family:system-ui,sans-serif;">'
            f"{row('NPS (0-10)', nps)}"
            f"{row('CSAT (1-5)', csat)}"
            f"{row('Ease (1-5)', ease)}"
            f"{row('Most valuable', valuable)}"
            f"{row('What to improve', improve)}"
            f"{row('From', contact)}"
            f"{row('User ID', user_id)}"
            f"</table>"
        )
        subject = f"[{app_name}] New feedback from {contact}"
        sent = sum(1 for to in recips if send_email(to, subject, html))
        logger.info("feedback emailed to %d/%d recipients", sent, len(recips))
    except Exception as e:
        logger.warning(f"feedback email failed: {e}")


@bp.route("/api/feedback", methods=["POST"])
@login_required
def api_feedback_submit():
    body = request.get_json(silent=True) or {}
    nps = _clamp_int(body.get("nps"), 0, 10)
    csat = _clamp_int(body.get("csat"), 1, 5)
    ease = _clamp_int(body.get("ease"), 1, 5)
    valuable = (body.get("valuable") or "").strip()[:120]
    improve = (body.get("improve") or "").strip()[:2000]
    email = (body.get("email") or "").strip()[:200]

    # Require at least one signal so we don't store empty submissions.
    if nps is None and csat is None and ease is None and not improve:
        return jsonify({"error": "Please answer at least one question."}), 400

    try:
        execute(
            f"INSERT INTO user_feedback (user_id, nps, csat, ease, valuable, improve, email) "
            f"VALUES ({P},{P},{P},{P},{P},{P},{P})",
            (_uid(), nps, csat, ease, valuable, improve, email),
        )
    except Exception as e:
        logger.error(f"feedback insert failed: {e}")
        return jsonify({"error": "Could not save feedback."}), 500

    # Notify the admin by email — best-effort, never blocks the save.
    _email_feedback(_uid(), nps, csat, ease, valuable, improve, email)

    return jsonify({"ok": True, "message": "Thanks for your feedback!"})


@bp.route("/api/admin/feedback")
@admin_required
def api_feedback_list():
    rows = query(
        "SELECT id, user_id, nps, csat, ease, valuable, improve, email, created_at "
        "FROM user_feedback ORDER BY id DESC LIMIT 200"
    ) or []
    items = [dict(r) for r in rows]

    nps_vals = [r["nps"] for r in items if r.get("nps") is not None]
    csat_vals = [r["csat"] for r in items if r.get("csat") is not None]
    ease_vals = [r["ease"] for r in items if r.get("ease") is not None]
    # NPS = % promoters (9-10) − % detractors (0-6)
    nps_score = None
    if nps_vals:
        promoters = sum(1 for v in nps_vals if v >= 9)
        detractors = sum(1 for v in nps_vals if v <= 6)
        nps_score = round((promoters - detractors) / len(nps_vals) * 100)

    return jsonify({
        "feedback": items,
        "stats": {
            "count": len(items),
            "nps": nps_score,
            "avg_csat": round(sum(csat_vals) / len(csat_vals), 1) if csat_vals else None,
            "avg_ease": round(sum(ease_vals) / len(ease_vals), 1) if ease_vals else None,
        },
    })
