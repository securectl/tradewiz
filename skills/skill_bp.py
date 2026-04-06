"""
Skills Flask Blueprint — all /api/skills/* endpoints.
"""

import json
import os
import time
import queue
import logging

from flask import Blueprint, jsonify, request, Response, send_file
from flask_login import current_user

from decorators import login_required
from rate_limiter import get_user_tier, check_headroom
from skills import registry, executor
from skills.models import SkillJob
from skills.runner import run_skill

logger = logging.getLogger(__name__)

skill_bp = Blueprint("skills", __name__)


@skill_bp.route("/api/skills/catalog")
@login_required
def skill_catalog():
    """List all skills filtered by user tier."""
    tier = get_user_tier(current_user.id)
    catalog = registry.get_catalog(tier)
    return jsonify({"skills": catalog, "tier": tier})


@skill_bp.route("/api/skills/catalog/<skill_id>")
@login_required
def skill_detail(skill_id):
    """Get detailed skill definition."""
    skill = registry.get_skill(skill_id)
    if not skill:
        return jsonify({"error": "Skill not found"}), 404
    tier = get_user_tier(current_user.id)
    available = registry.check_tier_access(skill_id, tier)
    return jsonify({
        "id": skill.id,
        "name": skill.name,
        "domain": skill.domain,
        "description": skill.description,
        "tier_required": skill.tier_required,
        "estimated_minutes": skill.estimated_minutes,
        "llm_calls_required": skill.llm_calls_required,
        "input_schema": skill.input_schema,
        "output_types": skill.output_types,
        "phases": skill.phases,
        "available": available,
    })


@skill_bp.route("/api/skills/launch", methods=["POST"])
@login_required
def skill_launch():
    """Launch a skill job. Returns job_id (HTTP 202)."""
    data = request.get_json() or {}
    skill_id = data.get("skill_id", "")
    inputs = data.get("inputs", {})

    # Validate skill exists
    skill = registry.get_skill(skill_id)
    if not skill:
        return jsonify({"error": "Skill not found"}), 404

    user_id = current_user.id
    tier = get_user_tier(user_id)

    # Tier access check
    if not registry.check_tier_access(skill_id, tier):
        return jsonify({"error": f"Skill requires {skill.tier_required} tier or higher"}), 403

    # Concurrency check
    if not executor.can_launch(user_id, tier):
        max_jobs = registry.MAX_CONCURRENT.get(tier, 1)
        return jsonify({"error": f"Max {max_jobs} concurrent jobs for {tier} tier"}), 429

    # LLM headroom check
    headroom = check_headroom(user_id, skill.llm_calls_required)
    if not headroom.get("allowed"):
        return jsonify({
            "error": "Insufficient LLM calls remaining",
            "used": headroom.get("used", 0),
            "limit": headroom.get("limit", 0),
            "remaining": headroom.get("remaining", 0),
        }), 429

    # Validate required inputs
    for field in skill.input_schema:
        if field.get("required") and not inputs.get(field["name"]):
            return jsonify({"error": f"Missing required input: {field['label']}"}), 400

    # Create and submit job
    job = SkillJob(
        user_id=user_id,
        skill_id=skill_id,
        inputs=inputs,
    )
    executor.submit_job(job, run_skill)

    return jsonify({"job_id": job.id, "status": job.status}), 202


@skill_bp.route("/api/skills/jobs")
@login_required
def skill_jobs():
    """List user's skill jobs."""
    jobs = executor.get_user_jobs(current_user.id)
    return jsonify({"jobs": [j.to_dict() for j in jobs]})


@skill_bp.route("/api/skills/jobs/<job_id>")
@login_required
def skill_job_status(job_id):
    """Get job status and progress."""
    job = executor.get_job(job_id)
    if not job or job.user_id != current_user.id:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job.to_dict())


@skill_bp.route("/api/skills/jobs/<job_id>/stream")
@login_required
def skill_job_stream(job_id):
    """SSE stream for real-time job progress updates."""
    job = executor.get_job(job_id)
    if not job or job.user_id != current_user.id:
        return jsonify({"error": "Job not found"}), 404

    def event_stream():
        q = queue.Queue()

        def on_update(j, event_type):
            q.put((event_type, j.to_dict()))

        job.add_listener(on_update)

        try:
            # Send current state immediately
            yield _sse_format("status", job.to_dict())

            # Drain any events that fired between job submit and listener registration
            while not q.empty():
                try:
                    event_type, data = q.get_nowait()
                    yield _sse_format(event_type, data)
                    if event_type in ("completed", "failed", "cancelled"):
                        return
                except queue.Empty:
                    break

            # Stream updates until job completes
            while job.status in ("pending", "running"):
                try:
                    event_type, data = q.get(timeout=30)
                    yield _sse_format(event_type, data)
                    if event_type in ("completed", "failed", "cancelled"):
                        break
                except queue.Empty:
                    # Send keepalive to keep connection alive
                    yield ": keepalive\n\n"

            # Final state
            yield _sse_format("done", job.to_dict())
        finally:
            job.remove_listener(on_update)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@skill_bp.route("/api/skills/jobs/<job_id>/result")
@login_required
def skill_job_result(job_id):
    """Get completed job result as JSON (for in-browser viewing)."""
    job = executor.get_job(job_id)
    if not job or job.user_id != current_user.id:
        return jsonify({"error": "Job not found"}), 404
    if job.status != "completed":
        return jsonify({"error": "Job not yet completed", "status": job.status}), 400
    return jsonify({"result": job.result, "output_files": job.output_files})


@skill_bp.route("/api/skills/jobs/<job_id>/download/<fmt>")
@login_required
def skill_job_download(job_id, fmt):
    """Download generated report file (docx/xlsx)."""
    job = executor.get_job(job_id)
    if not job or job.user_id != current_user.id:
        return jsonify({"error": "Job not found"}), 404
    if job.status != "completed":
        return jsonify({"error": "Job not yet completed"}), 400

    expected_dir = os.path.abspath(os.path.join("data", "skill_outputs"))
    for f in job.output_files:
        if f["format"] == fmt:
            path = f["path"]
            if not os.path.abspath(path).startswith(expected_dir):
                return jsonify({"error": "Invalid file path"}), 403
            if os.path.exists(path):
                mime = {
                    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                }.get(fmt, "application/octet-stream")
                return send_file(path, mimetype=mime, as_attachment=True, download_name=f["name"])

    return jsonify({"error": f"No {fmt} file available"}), 404


@skill_bp.route("/api/skills/jobs/<job_id>/charts/<filename>")
@login_required
def skill_job_chart(job_id, filename):
    """Serve a generated chart image."""
    job = executor.get_job(job_id)
    if not job or job.user_id != current_user.id:
        return jsonify({"error": "Job not found"}), 404
    # Sanitize filename
    safe_name = os.path.basename(filename)
    chart_path = os.path.join("data", "skill_outputs", job_id, "charts", safe_name)
    if os.path.exists(chart_path):
        return send_file(chart_path, mimetype="image/png")
    return jsonify({"error": "Chart not found"}), 404


@skill_bp.route("/api/skills/jobs/<job_id>", methods=["DELETE"])
@login_required
def skill_job_delete(job_id):
    """Cancel a running job or delete a completed one."""
    job = executor.get_job(job_id)
    if not job or job.user_id != current_user.id:
        return jsonify({"error": "Job not found"}), 404

    if job.status in ("pending", "running"):
        executor.cancel_job(job_id, current_user.id)
        return jsonify({"status": "cancelled"})
    else:
        executor.delete_job(job_id, current_user.id)
        return jsonify({"status": "deleted"})


def _sse_format(event, data):
    """Format data as an SSE event."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
