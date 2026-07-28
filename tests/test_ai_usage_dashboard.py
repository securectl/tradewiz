"""Tests for the admin Token & AI usage dashboard + token recording."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRouteRegistered(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/admin/ai-usage", rules)

    def test_requires_admin(self):
        from app import app
        resp = app.test_client().get("/api/admin/ai-usage")
        # admin_required -> 401 (unauth) or 302 redirect; never 200 anonymously
        self.assertIn(resp.status_code, (401, 403, 302))


class TestPricing(unittest.TestCase):
    def test_known_model_cost(self):
        from shared.llm_pricing import estimate_cost
        # gpt-4o: 2.50 prompt + 10.00 completion per 1M
        cost = estimate_cost("openai/gpt-4o", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 12.50, places=4)

    def test_unknown_model_uses_default(self):
        from shared.llm_pricing import estimate_cost, DEFAULT_PRICE
        cost = estimate_cost("some/unknown-model", 1_000_000, 0)
        self.assertAlmostEqual(cost, DEFAULT_PRICE["prompt"], places=4)

    def test_ollama_prefix_stripped(self):
        from shared.llm_pricing import estimate_cost
        a = estimate_cost("ollama/gpt-oss:20b", 1_000_000, 0)
        b = estimate_cost("gpt-oss:20b", 1_000_000, 0)
        self.assertEqual(a, b)

    def test_zero_tokens_zero_cost(self):
        from shared.llm_pricing import estimate_cost
        self.assertEqual(estimate_cost("openai/gpt-4o", 0, 0), 0.0)


class TestRecordLlmCall(unittest.TestCase):
    def test_records_tokens_and_estimates_cost(self):
        import rate_limiter
        captured = {}

        def fake_execute(sql, params):
            captured["sql"] = sql
            captured["params"] = params

        with mock.patch.object(rate_limiter, "execute", side_effect=fake_execute):
            rate_limiter.record_llm_call(
                7, "screener", "openai/gpt-4o-mini",
                prompt_tokens=1000, completion_tokens=500,
            )
        # New columns are present in the INSERT
        self.assertIn("prompt_tokens", captured["sql"])
        self.assertIn("cost_usd", captured["sql"])
        params = captured["params"]
        # (user_id, source, model, pt, ct, tt, cost, now)
        self.assertEqual(params[0], 7)
        self.assertEqual(params[3], 1000)   # prompt_tokens
        self.assertEqual(params[4], 500)    # completion_tokens
        self.assertEqual(params[5], 1500)   # total derived from pt+ct
        self.assertGreater(params[6], 0)    # estimated cost > 0

    def test_backward_compatible_zero_tokens(self):
        import rate_limiter
        captured = {}
        with mock.patch.object(rate_limiter, "execute",
                               side_effect=lambda s, p: captured.update(sql=s, params=p)):
            rate_limiter.record_llm_call(7, "api", "x")  # old-style call
        self.assertEqual(captured["params"][3], 0)
        self.assertEqual(captured["params"][5], 0)
        self.assertEqual(captured["params"][6], 0.0)


class TestUsageAggregation(unittest.TestCase):
    """The route should aggregate the mocked rows into the response shape."""

    def test_aggregation_shape(self):
        from features.admin import routes as r

        totals = {"calls": 3, "prompt_tokens": 30, "completion_tokens": 15,
                  "total_tokens": 45, "cost_usd": 0.5}
        by_model = [{"model": "openai/gpt-4o", "calls": 2, "total_tokens": 40, "cost_usd": 0.45}]
        by_source = [{"call_source": "screener", "calls": 3, "total_tokens": 45, "cost_usd": 0.5}]
        daily = [{"d": "2026-06-09", "calls": 3, "total_tokens": 45, "cost_usd": 0.5}]
        top_users = [{"email": "a@b.com", "name": "A", "calls": 3, "total_tokens": 45, "cost_usd": 0.5}]

        from app import app
        with app.test_request_context("/api/admin/ai-usage?from=2026-05-01&to=2026-06-09"), \
             mock.patch.object(r, "_parse_date_window",
                               return_value=("2026-05-01T00:00:00", "2026-06-09T00:00:00", 39)), \
             mock.patch.object(r, "query_one", return_value=totals), \
             mock.patch.object(r, "query", side_effect=[by_model, by_source, daily, top_users]):
            # call the view function directly, bypassing the auth decorator wrapper
            resp = r.api_admin_ai_usage.__wrapped__()
        data = resp.get_json()
        self.assertEqual(data["totals"]["calls"], 3)
        self.assertEqual(data["totals"]["cost_usd"], 0.5)
        self.assertEqual(data["by_model"][0]["model"], "openai/gpt-4o")
        self.assertEqual(data["by_source"][0]["source"], "screener")
        self.assertEqual(data["daily"][0]["day"], "2026-06-09")
        self.assertEqual(data["top_users"][0]["email"], "a@b.com")


class TestUsageRouteRealDb(unittest.TestCase):
    """Exercise the REAL SQL end-to-end.

    TestUsageAggregation mocks query()/query_one(), so it cannot catch dialect
    errors in the SQL strings — e.g. the Postgres reserved-word `day` alias that
    made the whole route 500 (`syntax error at or near "day"`) and left the
    dashboard blank. This runs the view against the live DB.
    """

    def test_route_runs_against_real_db(self):
        from app import app
        from features.admin import routes as r
        with app.test_request_context("/api/admin/ai-usage?from=2026-01-01&to=2026-12-31"):
            resp = r.api_admin_ai_usage.__wrapped__()
        data = resp.get_json()
        for key in ("totals", "daily", "by_model", "by_source", "top_users"):
            self.assertIn(key, data)
        self.assertIn("calls", data["totals"])
        # Each daily row must carry the 'day' key the frontend expects.
        for row in data["daily"]:
            self.assertIn("day", row)
            self.assertIn("calls", row)


if __name__ == "__main__":
    unittest.main()
