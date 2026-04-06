from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from decorators import admin_required
from status_checker import SERVICES, run_check_cycle
from db import get_db, put_db, query, IS_POSTGRES
from shared.helpers import P

bp = Blueprint("status", __name__)


@bp.route("/api/status")
@admin_required
def api_status():
    conn = get_db()
    try:
        services_out = []
        overall = "operational"
        for svc_key, svc_info in SERVICES.items():
            if IS_POSTGRES:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    f"SELECT status, response_time_ms, error_message, checked_at FROM service_checks WHERE service_name = {P} ORDER BY checked_at DESC LIMIT 1",
                    (svc_key,),
                )
                latest = cur.fetchone()
                cutoff = (datetime.now() - timedelta(days=90)).isoformat()
                cur.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P}", (svc_key, cutoff))
                total = cur.fetchone()["cnt"]
                cur.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND status = 'operational'", (svc_key, cutoff))
                ok = cur.fetchone()["cnt"]
                cur.close()
            else:
                latest = conn.execute(
                    f"SELECT status, response_time_ms, error_message, checked_at FROM service_checks WHERE service_name = {P} ORDER BY checked_at DESC LIMIT 1",
                    (svc_key,),
                ).fetchone()
                if latest:
                    latest = dict(latest)
                cutoff = (datetime.now() - timedelta(days=90)).isoformat()
                total = dict(conn.execute(
                    f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P}", (svc_key, cutoff)
                ).fetchone())["cnt"]
                ok = dict(conn.execute(
                    f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND status = 'operational'", (svc_key, cutoff)
                ).fetchone())["cnt"]

            uptime_pct = round(ok / total * 100, 2) if total > 0 else 100.0
            status = latest["status"] if latest else "unknown"
            if status == "outage":
                overall = "outage"
            elif status == "degraded" and overall != "outage":
                overall = "degraded"

            services_out.append({
                "key": svc_key, "name": svc_info["name"],
                "category": svc_info["category"], "status": status,
                "response_time_ms": latest["response_time_ms"] if latest else None,
                "error_message": latest["error_message"] if latest else None,
                "checked_at": latest["checked_at"] if latest else None,
                "uptime_pct": uptime_pct,
            })
    finally:
        put_db(conn)
    return jsonify({"overall": overall, "services": services_out})


@bp.route("/api/status/uptime/<service>")
@admin_required
def api_status_uptime(service):
    conn = get_db()
    try:
        days = []
        for i in range(89, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            day_start = f"{day} 00:00:00"
            day_end = f"{day} 23:59:59"
            if IS_POSTGRES:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND checked_at <= {P}", (service, day_start, day_end))
                total = cur.fetchone()["cnt"]
                cur.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND checked_at <= {P} AND status != 'operational'", (service, day_start, day_end))
                failed = cur.fetchone()["cnt"]
                cur.close()
            else:
                total = dict(conn.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND checked_at <= {P}", (service, day_start, day_end)).fetchone())["cnt"]
                failed = dict(conn.execute(f"SELECT COUNT(*) as cnt FROM service_checks WHERE service_name = {P} AND checked_at >= {P} AND checked_at <= {P} AND status != 'operational'", (service, day_start, day_end)).fetchone())["cnt"]
            uptime_pct = round((total - failed) / total * 100, 1) if total > 0 else 100.0
            days.append({"date": day, "uptime_pct": uptime_pct, "total": total, "failed": failed})
    finally:
        put_db(conn)
    return jsonify(days)


@bp.route("/api/status/incidents")
@admin_required
def api_status_incidents():
    rows = query("SELECT * FROM service_incidents ORDER BY created_at DESC LIMIT 50")
    return jsonify([{
        "id": r["id"], "service_name": r["service_name"],
        "service_display": SERVICES.get(r["service_name"], {}).get("name", r["service_name"]),
        "incident_type": r["incident_type"], "started_at": r["started_at"],
        "resolved_at": r["resolved_at"], "duration_seconds": r["duration_seconds"],
        "error_message": r["error_message"],
    } for r in rows])


@bp.route("/api/status/check", methods=["POST"])
@admin_required
def api_status_force_check():
    run_check_cycle()
    return jsonify({"ok": True, "message": "Health check completed"})
