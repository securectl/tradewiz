"""
Status Checker — Health Check Engine for Backend Services
Probes 6 services every 60s, stores results, detects incidents.
"""

import threading
import time
import sqlite3
import os
import requests
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "searches.db")

# Cache for OpenRouter models list (avoid hitting API every 60s)
_models_cache = {"data": None, "expires": 0}

# Service definitions
SERVICES = {
    "openrouter_claude": {
        "name": "OpenRouter (Claude)",
        "category": "AI Model",
        "model_id": "anthropic/claude-sonnet-4",
    },
    "openrouter_gemini": {
        "name": "OpenRouter (Gemini)",
        "category": "AI Model",
        "model_id": "google/gemini-2.5-flash",
    },
    "openrouter_deepseek": {
        "name": "OpenRouter (DeepSeek)",
        "category": "AI Model",
        "model_id": "deepseek/deepseek-chat",
    },
    "yahoo_finance": {
        "name": "Yahoo Finance",
        "category": "Data Feed",
    },
    "flask_server": {
        "name": "Flask Server",
        "category": "Application",
    },
    "sqlite_db": {
        "name": "SQLite Database",
        "category": "Database",
    },
}


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_openrouter_models():
    """Fetch OpenRouter models list (free endpoint). Cache for 60s."""
    now = time.time()
    if _models_cache["data"] and now < _models_cache["expires"]:
        return _models_cache["data"]

    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        model_ids = {m["id"] for m in data.get("data", [])}
        _models_cache["data"] = model_ids
        _models_cache["expires"] = now + 60
        return model_ids
    except Exception:
        return None


def check_openrouter(service_key: str) -> dict:
    """Check if a specific model is available on OpenRouter."""
    model_id = SERVICES[service_key]["model_id"]
    start = time.time()
    try:
        models = _fetch_openrouter_models()
        elapsed = int((time.time() - start) * 1000)
        if models is None:
            return {"status": "outage", "response_time_ms": elapsed,
                    "error_message": "Failed to reach OpenRouter API"}
        if model_id in models:
            return {"status": "operational", "response_time_ms": elapsed}
        else:
            return {"status": "degraded", "response_time_ms": elapsed,
                    "error_message": f"Model {model_id} not found in available models"}
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return {"status": "outage", "response_time_ms": elapsed,
                "error_message": str(e)[:200]}


def check_yahoo_finance() -> dict:
    """Check Yahoo Finance by fetching AAPL price."""
    start = time.time()
    try:
        import yfinance as yf
        t = yf.Ticker("AAPL")
        price = t.fast_info.get("lastPrice")
        elapsed = int((time.time() - start) * 1000)
        if price and price > 0:
            return {"status": "operational", "response_time_ms": elapsed}
        else:
            return {"status": "degraded", "response_time_ms": elapsed,
                    "error_message": "Could not fetch AAPL price"}
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return {"status": "outage", "response_time_ms": elapsed,
                "error_message": str(e)[:200]}


def check_flask_server() -> dict:
    """Self-check — always operational if this code is running."""
    return {"status": "operational", "response_time_ms": 0}


def check_sqlite_db() -> dict:
    """Check SQLite connectivity and table existence."""
    start = time.time()
    try:
        conn = _get_db()
        conn.execute("SELECT 1").fetchone()
        # Verify key tables exist
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        elapsed = int((time.time() - start) * 1000)
        required = {"searches", "journal_entries", "service_checks"}
        missing = required - set(tables)
        if missing:
            return {"status": "degraded", "response_time_ms": elapsed,
                    "error_message": f"Missing tables: {', '.join(missing)}"}
        return {"status": "operational", "response_time_ms": elapsed}
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return {"status": "outage", "response_time_ms": elapsed,
                "error_message": str(e)[:200]}


def run_all_checks() -> dict:
    """Run health checks on all 6 services. Returns dict of results."""
    results = {}

    # OpenRouter checks (all use cached models list)
    for key in ["openrouter_claude", "openrouter_gemini", "openrouter_deepseek"]:
        results[key] = check_openrouter(key)

    results["yahoo_finance"] = check_yahoo_finance()
    results["flask_server"] = check_flask_server()
    results["sqlite_db"] = check_sqlite_db()

    return results


def store_check_results(results: dict):
    """Store check results in DB and detect incidents."""
    conn = _get_db()
    now = datetime.now().isoformat()

    for service_name, result in results.items():
        # Store the check
        conn.execute("""
            INSERT INTO service_checks (service_name, status, response_time_ms, error_message, checked_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            service_name,
            result["status"],
            result.get("response_time_ms", 0),
            result.get("error_message"),
            now,
        ))

        # Incident detection: compare with previous check
        prev = conn.execute("""
            SELECT status FROM service_checks
            WHERE service_name = ? AND checked_at < ?
            ORDER BY checked_at DESC LIMIT 1
        """, (service_name, now)).fetchone()

        prev_status = prev["status"] if prev else "operational"
        curr_status = result["status"]

        # Transition to degraded/outage → open incident
        if prev_status == "operational" and curr_status in ("degraded", "outage"):
            conn.execute("""
                INSERT INTO service_incidents (service_name, incident_type, started_at, error_message, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (service_name, curr_status, now, result.get("error_message"), now))

        # Recovery → close open incident
        elif curr_status == "operational" and prev_status in ("degraded", "outage"):
            open_incident = conn.execute("""
                SELECT id, started_at FROM service_incidents
                WHERE service_name = ? AND resolved_at IS NULL
                ORDER BY started_at DESC LIMIT 1
            """, (service_name,)).fetchone()

            if open_incident:
                started = datetime.fromisoformat(open_incident["started_at"])
                duration = int((datetime.fromisoformat(now) - started).total_seconds())
                conn.execute("""
                    UPDATE service_incidents SET resolved_at = ?, duration_seconds = ?
                    WHERE id = ?
                """, (now, duration, open_incident["id"]))

    conn.commit()
    conn.close()


def purge_old_checks(days: int = 90):
    """Remove checks older than N days."""
    try:
        conn = _get_db()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn.execute("DELETE FROM service_checks WHERE checked_at < ?", (cutoff,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def run_check_cycle():
    """Single check cycle: run all checks and store results."""
    try:
        results = run_all_checks()
        store_check_results(results)
    except Exception as e:
        print(f"[StatusChecker] Error in check cycle: {e}")


def _background_loop():
    """Background timer loop running every 60s."""
    while True:
        run_check_cycle()
        time.sleep(60)


def start_background_checker():
    """Start the background health checker as a daemon thread."""
    thread = threading.Thread(target=_background_loop, daemon=True)
    thread.start()
    print("[StatusChecker] Background health checker started (60s interval)")
