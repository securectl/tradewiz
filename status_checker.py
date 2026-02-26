"""
Status Checker — Health Check Engine for Backend Services
Probes 6 services every 60s, stores results, detects incidents.
"""

import threading
import time
import os
import requests
from datetime import datetime, timedelta

from db import IS_POSTGRES, query, query_one, execute

P = "%s" if IS_POSTGRES else "?"

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
    "database": {
        "name": "Database",
        "category": "Database",
    },
}


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


def check_database() -> dict:
    """Check database connectivity."""
    start = time.time()
    try:
        row = query_one("SELECT 1 as ok")
        elapsed = int((time.time() - start) * 1000)
        if row:
            return {"status": "operational", "response_time_ms": elapsed}
        return {"status": "degraded", "response_time_ms": elapsed,
                "error_message": "Query returned no result"}
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
    results["database"] = check_database()

    return results


def store_check_results(results: dict):
    """Store check results in DB and detect incidents."""
    now = datetime.now().isoformat()

    for service_name, result in results.items():
        # Store the check
        execute(
            f"INSERT INTO service_checks (service_name, status, response_time_ms, error_message, checked_at) "
            f"VALUES ({P}, {P}, {P}, {P}, {P})",
            (service_name, result["status"], result.get("response_time_ms", 0),
             result.get("error_message"), now),
        )

        # Incident detection: compare with previous check
        prev = query_one(
            f"SELECT status FROM service_checks "
            f"WHERE service_name = {P} AND checked_at < {P} "
            f"ORDER BY checked_at DESC LIMIT 1",
            (service_name, now),
        )

        prev_status = prev["status"] if prev else "operational"
        curr_status = result["status"]

        # Transition to degraded/outage → open incident
        if prev_status == "operational" and curr_status in ("degraded", "outage"):
            execute(
                f"INSERT INTO service_incidents (service_name, incident_type, started_at, error_message, created_at) "
                f"VALUES ({P}, {P}, {P}, {P}, {P})",
                (service_name, curr_status, now, result.get("error_message"), now),
            )

        # Recovery → close open incident
        elif curr_status == "operational" and prev_status in ("degraded", "outage"):
            open_incident = query_one(
                f"SELECT id, started_at FROM service_incidents "
                f"WHERE service_name = {P} AND resolved_at IS NULL "
                f"ORDER BY started_at DESC LIMIT 1",
                (service_name,),
            )

            if open_incident:
                started = datetime.fromisoformat(str(open_incident["started_at"]))
                duration = int((datetime.fromisoformat(now) - started).total_seconds())
                execute(
                    f"UPDATE service_incidents SET resolved_at = {P}, duration_seconds = {P} "
                    f"WHERE id = {P}",
                    (now, duration, open_incident["id"]),
                )


def purge_old_checks(days: int = 90):
    """Remove checks older than N days."""
    try:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        execute(f"DELETE FROM service_checks WHERE checked_at < {P}", (cutoff,))
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
