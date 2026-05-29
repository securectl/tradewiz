"""
Sector Radar API routes — the Auto Research Analyst in the Research tab.

  GET  /api/sector-radar/latest        latest report (board + analyst thesis)
  GET  /api/sector-radar/history       compact list of prior calls
  GET  /api/sector-radar/sector/<key>  per-sector detail from latest board
  POST /api/sector-radar/run           trigger a fresh run (threaded)
"""

import logging

from flask import Blueprint, jsonify, request

from decorators import login_required
from shared.helpers import _uid
from features.sector_radar import engine

logger = logging.getLogger(__name__)
bp = Blueprint("sector_radar", __name__)


@bp.route("/api/sector-radar/latest")
@login_required
def api_sector_radar_latest():
    report = engine.get_latest()
    return jsonify({
        "report": report,
        "running": engine.is_running(),
        "has_data": report is not None,
    })


@bp.route("/api/sector-radar/history")
@login_required
def api_sector_radar_history():
    try:
        limit = min(60, max(1, int(request.args.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20
    return jsonify({"history": engine.get_history(limit=limit)})


@bp.route("/api/sector-radar/sector/<key>")
@login_required
def api_sector_radar_sector(key):
    detail = engine.get_sector_detail(key)
    if detail is None:
        return jsonify({"error": "Sector not found in latest report"}), 404
    return jsonify({"sector": detail})


@bp.route("/api/sector-radar/run", methods=["POST"])
@login_required
def api_sector_radar_run():
    """Trigger a fresh analysis in the background. One run at a time globally."""
    if engine.is_running():
        return jsonify({"ok": False, "status": "already_running",
                        "message": "A research run is already in progress."}), 409
    body = request.get_json(silent=True) or {}
    deep = bool(body.get("deep", False))
    result = engine.run_async(deep=deep, trigger=f"manual:user{_uid()}")
    return jsonify(result)
