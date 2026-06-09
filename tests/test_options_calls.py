"""Tests for the Option Calls feature (rising vs falling call volume)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRouteRegistered(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/options/calls", rules)

    def test_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/options/calls?symbol=AAPL")
        self.assertIn(resp.status_code, (401, 302))

    def test_missing_symbol_400(self):
        # 400 should win over auth only if route runs; login_required guards
        # first, so we just assert it's not a 500.
        from app import app
        resp = app.test_client().get("/api/options/calls")
        self.assertIn(resp.status_code, (400, 401, 302))


class TestClassify(unittest.TestCase):
    """The pure vol/OI classifier — no network."""

    def _calls(self):
        return [
            {"strike": 100, "expiry": "2026-06-19", "volume": 1000, "open_interest": 500, "last": 5.0},   # ratio 2.0 -> up
            {"strike": 95,  "expiry": "2026-06-19", "volume": 60,   "open_interest": 100,  "last": 8.0},  # ratio 0.6 -> up
            {"strike": 110, "expiry": "2026-06-19", "volume": 10,   "open_interest": 1000, "last": 1.0},  # ratio 0.01, OI>=50 -> down
            {"strike": 105, "expiry": "2026-06-19", "volume": 0,    "open_interest": 300,  "last": 2.0},  # no volume -> skipped
        ]

    def test_buckets_split_by_ratio(self):
        from features.options_calls.engine import _classify
        out = _classify("XYZ", 105.0, self._calls())
        inc_strikes = {c["strike"] for c in out["increasing"]}
        dec_strikes = {c["strike"] for c in out["decreasing"]}
        self.assertEqual(inc_strikes, {100.0, 95.0})
        self.assertEqual(dec_strikes, {110.0})

    def test_totals_skip_zero_volume(self):
        from features.options_calls.engine import _classify
        out = _classify("XYZ", 105.0, self._calls())
        # zero-volume contract excluded from totals/contracts
        self.assertEqual(out["totals"]["call_volume"], 1070)
        self.assertEqual(out["totals"]["contracts"], 3)

    def test_read_accumulating(self):
        from features.options_calls.engine import _classify
        out = _classify("XYZ", 105.0, self._calls())
        # agg ratio 1070/1600 = 0.67 -> ACCUMULATING
        self.assertEqual(out["read"], "ACCUMULATING")

    def test_moneyness(self):
        from features.options_calls.engine import _classify
        out = _classify("XYZ", 105.0, self._calls())
        by_strike = {c["strike"]: c["moneyness"] for c in out["increasing"]}
        self.assertEqual(by_strike[100.0], "ITM")  # strike below spot
        # 110 is OTM (in decreasing bucket)
        dec = {c["strike"]: c["moneyness"] for c in out["decreasing"]}
        self.assertEqual(dec[110.0], "OTM")


class TestGetCallActivity(unittest.TestCase):
    def setUp(self):
        from features.options_calls import engine
        engine._CACHE.clear()

    def test_falls_back_to_yfinance_when_webull_empty(self):
        from features.options_calls import engine
        sample = [{"strike": 50, "expiry": "2026-07-17", "volume": 200, "open_interest": 100, "last": 1.0}]
        with mock.patch("shared.webull_options.fetch_call_chain", return_value=None), \
             mock.patch.object(engine, "_fetch_calls_yf", return_value=(50.0, sample)):
            out = engine.get_call_activity("ABC")
        self.assertEqual(out["source"], "yfinance")
        self.assertEqual(out["symbol"], "ABC")
        self.assertEqual(out["totals"]["call_volume"], 200)

    def test_uses_webull_when_available(self):
        from features.options_calls import engine
        wb = {"source": "webull", "price": 50.0,
              "calls": [{"strike": 50, "expiry": "2026-07-17", "volume": 300, "open_interest": 100, "last": 1.0}]}
        with mock.patch("shared.webull_options.fetch_call_chain", return_value=wb):
            out = engine.get_call_activity("ABC")
        self.assertEqual(out["source"], "webull")
        self.assertEqual(out["totals"]["call_volume"], 300)

    def test_error_when_no_chain(self):
        from features.options_calls import engine
        with mock.patch("shared.webull_options.fetch_call_chain", return_value=None), \
             mock.patch.object(engine, "_fetch_calls_yf", return_value=(0.0, [])):
            out = engine.get_call_activity("NOPE")
        self.assertIn("error", out)

    def test_blank_symbol(self):
        from features.options_calls import engine
        self.assertIn("error", engine.get_call_activity("  "))


class TestWebullSeam(unittest.TestCase):
    def test_unavailable_without_creds(self):
        from shared import webull_options
        with mock.patch.dict(os.environ, {"WEBULL_APP_KEY": "", "WEBULL_APP_SECRET": ""}, clear=False):
            self.assertFalse(webull_options.is_webull_options_available())
            self.assertIsNone(webull_options.fetch_call_chain("AAPL"))


if __name__ == "__main__":
    unittest.main()
