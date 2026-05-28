"""User feedback — industry-standard survey (NPS / CSAT / ease + open text).

POST /api/feedback        submit (login_required, stored against the user)
GET  /api/admin/feedback  read responses + aggregates (admin_required)
"""

import logging

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
