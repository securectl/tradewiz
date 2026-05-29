"""Tests for admin endpoints added by the Ollama Cloud UI + usage-report work.

Covers:
- /api/admin/ollama-config GET/POST/test/clear (admin-only)
- /api/admin/users/usage JSON + CSV (admin-only)
- /api/admin/users/<id>/usage drill-down (admin-only)
- /api/admin/platform GET/POST (admin-only)

Run: docker compose exec app python -m pytest tests/test_admin_ollama_and_usage.py -v
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_pg():
    from db import IS_POSTGRES
    return IS_POSTGRES


def _ph():
    return "%s" if _is_pg() else "?"


# Positive IDs because Flask's <int:user_id> converter rejects negative numbers.
# Picked high enough to not clash with real seeded users.
ADMIN_UID = 999901
USER_UID = 999902


def _seed_admin_user():
    from db import execute
    ph = _ph()
    for uid, email in ((ADMIN_UID, "admin-test@local"), (USER_UID, "user-test@local")):
        try:
            if _is_pg():
                execute("INSERT INTO users (id, email, name) VALUES (%s, %s, %s) "
                        "ON CONFLICT (id) DO NOTHING",
                        (uid, email, "test"))
            else:
                execute("INSERT OR IGNORE INTO users (id, email, name) VALUES (?, ?, ?)",
                        (uid, email, "test"))
        except Exception:
            pass
    # Promote ADMIN_UID
    try:
        if _is_pg():
            execute("INSERT INTO user_roles (user_id, role) VALUES (%s, 'admin') "
                    "ON CONFLICT DO NOTHING",
                    (ADMIN_UID,))
        else:
            execute("INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?, 'admin')",
                    (ADMIN_UID,))
    except Exception:
        pass


def _cleanup():
    from db import execute
    ph = _ph()
    for uid in (ADMIN_UID, USER_UID):
        for sql in (
            f"DELETE FROM user_roles WHERE user_id = {ph}",
            f"DELETE FROM llm_usage_log WHERE user_id = {ph}",
            f"DELETE FROM user_subscriptions WHERE user_id = {ph}",
            f"DELETE FROM users WHERE id = {ph}",
        ):
            try:
                execute(sql, (uid,))
            except Exception:
                pass
    for k in ("ollama_url", "ollama_api_key", "ollama_model", "deployment_target"):
        try:
            execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {ph}", (k,))
        except Exception:
            pass


def setUpModule():
    """Seed admin + user once for the whole module.

    Important: tests run inside the same container as the live gunicorn worker.
    Both processes have their own psycopg2 connections to the SAME Postgres.
    On a freshly-rebuilt container, the worker's `_on_startup` thread runs
    `ALTER TABLE … ADD COLUMN IF NOT EXISTS` migrations on first HTTP request
    — that acquires AccessExclusiveLock on `users`, which deadlocks with
    flask-login's `load_user` SELECT on `user_subscriptions` (FK references
    users) when our pytest test_client makes its first authenticated call.

    Fix: trigger the worker's startup migrations OUT OF BAND via a real HTTP
    request (urllib hits the gunicorn socket, not the in-process app), wait
    for them to finish, only then seed.
    """
    import time
    import urllib.request
    # Ping the live gunicorn worker (NOT the in-process test_client) so its
    # post-fork migration thread starts and completes before we touch the DB.
    for _ in range(20):
        try:
            urllib.request.urlopen("http://localhost:5000/healthz", timeout=2).read()
            break
        except Exception:
            time.sleep(0.5)
    # Give the background `_delayed_start` thread time to finish all
    # `ALTER TABLE IF NOT EXISTS` statements and auto-start bots before we
    # hit user_subscriptions. The thread sleeps 5s before doing its work,
    # so wait past that window.
    time.sleep(8.0)
    _cleanup()
    _seed_admin_user()


def tearDownModule():
    _cleanup()


def _retry_on_deadlock(fn, *, attempts=4):
    """Retry a request callable when Postgres reports a transient deadlock.

    Tests run in-process alongside the gunicorn worker — both have separate
    connections to the same DB. On the first request after rebuild we
    sometimes deadlock against the worker's background threads. Retrying
    with a small backoff is the standard fix.
    """
    import time
    last = None
    for i in range(attempts):
        resp = fn()
        body = ""
        try:
            body = resp.get_data(as_text=True) or ""
        except Exception:
            pass
        if resp.status_code != 500 or "DeadlockDetected" not in body:
            return resp
        last = resp
        time.sleep(0.3 * (i + 1))
    return last


class _AdminEndpointBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client = app.test_client()
        cls.app = app

    def _login(self, uid):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True

    def _get(self, path):
        return _retry_on_deadlock(lambda: self.client.get(path))

    def _post(self, path, **kwargs):
        return _retry_on_deadlock(lambda: self.client.post(path, **kwargs))


class TestOllamaConfigEndpoints(_AdminEndpointBase):
    def setUp(self):
        from db import execute
        ph = _ph()
        for k in ("ollama_url", "ollama_api_key", "ollama_model"):
            execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {ph}", (k,))

    def test_non_admin_forbidden(self):
        self._login(USER_UID)
        resp = self.client.get("/api/admin/ollama-config")
        self.assertIn(resp.status_code, (302, 401, 403))  # decorator may redirect or 403

    def test_get_returns_keys_with_source(self):
        self._login(ADMIN_UID)
        resp = self.client.get("/api/admin/ollama-config")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertIn("ollama_url", data)
        self.assertIn("ollama_api_key", data)
        self.assertIn("ollama_model", data)
        self.assertIn("source", data["ollama_url"])
        self.assertIn("value_masked", data["ollama_api_key"])
        self.assertIn("is_set", data["ollama_api_key"])

    def test_set_persists_to_db(self):
        self._login(ADMIN_UID)
        resp = self.client.post(
            "/api/admin/ollama-config/set",
            json={"ollama_url": "https://ollama.example",
                  "ollama_model": "qwen3-coder:480b",
                  "ollama_api_key": "secret-from-ui"},
        )
        self.assertEqual(resp.status_code, 200)
        # Verify via GET
        get_resp = self.client.get("/api/admin/ollama-config")
        d = get_resp.get_json()
        self.assertEqual(d["ollama_url"]["value"], "https://ollama.example")
        self.assertEqual(d["ollama_url"]["source"], "db")
        self.assertEqual(d["ollama_model"]["value"], "qwen3-coder:480b")
        self.assertTrue(d["ollama_api_key"]["is_set"])
        self.assertTrue(d["ollama_api_key"]["value_masked"].endswith("m-ui"))

    def test_test_endpoint_no_key(self):
        self._login(ADMIN_UID)
        resp = self.client.post("/api/admin/ollama-config/test")
        d = resp.get_json()
        self.assertFalse(d["ok"])
        self.assertIn("not set", d["message"].lower())

    def test_test_endpoint_with_mocked_success(self):
        self._login(ADMIN_UID)
        # Save a key, then mock the HTTP call
        self.client.post("/api/admin/ollama-config/set",
                         json={"ollama_api_key": "k", "ollama_url": "https://ollama.com"})

        class FakeResp:
            status_code = 200
            text = ""
            def json(self): return {"models": []}

        with patch("requests.get", return_value=FakeResp()):
            resp = self.client.post("/api/admin/ollama-config/test")
        d = resp.get_json()
        self.assertTrue(d["ok"], d.get("message"))

    def test_clear_drops_db_rows(self):
        self._login(ADMIN_UID)
        self.client.post("/api/admin/ollama-config/set",
                         json={"ollama_url": "https://x", "ollama_api_key": "k"})
        self.client.post("/api/admin/ollama-config/clear")
        resp = self.client.get("/api/admin/ollama-config")
        d = resp.get_json()
        # url should now come from env or default — not "db"
        self.assertNotEqual(d["ollama_url"]["source"], "db")


class TestPlatformEndpoints(_AdminEndpointBase):

    def test_non_admin_forbidden(self):
        self._login(USER_UID)
        resp = self.client.get("/api/admin/platform")
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_get_returns_shape(self):
        self._login(ADMIN_UID)
        resp = self.client.get("/api/admin/platform")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        for k in ("detected", "override", "effective"):
            self.assertIn(k, d)

    def test_set_persists(self):
        self._login(ADMIN_UID)
        resp = self.client.post("/api/admin/platform/set", json={"target": "cloud_run"})
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["platform"]["effective"], "cloud_run")
        # Clear
        self.client.post("/api/admin/platform/set", json={"target": "auto"})
        resp = self.client.get("/api/admin/platform")
        d = resp.get_json()
        self.assertEqual(d["override"], "")

    def test_invalid_target_rejected(self):
        self._login(ADMIN_UID)
        resp = self.client.post("/api/admin/platform/set", json={"target": "garbage"})
        self.assertEqual(resp.status_code, 400)


class TestUsageReportEndpoints(_AdminEndpointBase):
    """Seed synthetic llm_usage_log rows for ADMIN_UID + USER_UID then verify aggregates."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from db import execute
        from datetime import datetime, timedelta
        ph = _ph()
        # Make sure the test users have known LLM-call counts
        now = datetime.utcnow()
        # USER_UID: 5 calls today, 2 calls 10 days ago
        for i in range(5):
            execute(
                f"INSERT INTO llm_usage_log (user_id, call_source, model, called_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph})",
                (USER_UID, "api", "google/gemini-2.5-flash",
                 (now - timedelta(hours=i)).isoformat()),
            )
        for i in range(2):
            execute(
                f"INSERT INTO llm_usage_log (user_id, call_source, model, called_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph})",
                (USER_UID, "screener", "deepseek/deepseek-chat",
                 (now - timedelta(days=10, hours=i)).isoformat()),
            )
        # ADMIN_UID: 1 call today
        execute(
            f"INSERT INTO llm_usage_log (user_id, call_source, model, called_at) "
            f"VALUES ({ph}, {ph}, {ph}, {ph})",
            (ADMIN_UID, "api", "anthropic/claude-sonnet-4-6", now.isoformat()),
        )

    def test_non_admin_forbidden(self):
        self._login(USER_UID)
        resp = self.client.get("/api/admin/users/usage")
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_json_includes_seeded_user(self):
        self._login(ADMIN_UID)
        resp = self.client.get("/api/admin/users/usage?sort=calls_desc")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        rows_by_uid = {r["user_id"]: r for r in d["rows"]}
        self.assertIn(USER_UID, rows_by_uid)
        # USER_UID has 7 total LLM calls (5 today + 2 ten days ago)
        self.assertGreaterEqual(rows_by_uid[USER_UID]["calls_total"], 7)
        # 24h window should pick up the 5 recent ones
        self.assertGreaterEqual(rows_by_uid[USER_UID]["calls_24h"], 5)

    def test_email_filter(self):
        self._login(ADMIN_UID)
        resp = self.client.get("/api/admin/users/usage?q=user-test")
        d = resp.get_json()
        emails = {r["email"] for r in d["rows"]}
        # Only the matching user(s) should be returned
        self.assertTrue(all("user-test" in (e or "") for e in emails),
                        f"unexpected rows: {emails}")

    def test_csv_export_headers(self):
        self._login(ADMIN_UID)
        resp = self.client.get("/api/admin/users/usage?format=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers.get("Content-Type", ""))
        self.assertIn("attachment", resp.headers.get("Content-Disposition", ""))
        body = resp.get_data(as_text=True)
        lines = body.strip().split("\n")
        header = lines[0].split(",")
        self.assertIn("user_id", header)
        self.assertIn("email", header)
        self.assertIn("calls_total", header)

    def test_drilldown(self):
        self._login(ADMIN_UID)
        resp = self.client.get(f"/api/admin/users/{USER_UID}/usage")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["user"]["id"], USER_UID)
        # by_model should include the seeded models
        models = {m["model"] for m in d["by_model"]}
        self.assertTrue(
            any("gemini" in m or "deepseek" in m for m in models),
            f"expected seeded models in by_model, got {models}",
        )

    def test_drilldown_user_not_found(self):
        self._login(ADMIN_UID)
        resp = self.client.get("/api/admin/users/-99999/usage")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
