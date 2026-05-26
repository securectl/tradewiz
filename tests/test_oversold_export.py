"""
Tests for /api/screener/oversold/export and its helpers.
Run: python -m pytest tests/test_oversold_export.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestExportRouteRegistration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app
        cls.client = app.test_client()
        cls.rules = {str(r) for r in app.url_map.iter_rules()}

    def test_route_registered(self):
        self.assertIn("/api/screener/oversold/export", self.rules)

    def test_route_requires_auth(self):
        r = self.client.get("/api/screener/oversold/export?format=csv")
        # Login-required decorator returns 401 or redirect (302), never 500.
        self.assertIn(r.status_code, [401, 302, 403])
        self.assertNotEqual(r.status_code, 500)

    def test_invalid_format_rejected_before_auth_for_authed_user(self):
        # Even with bad format, unauth users still get redirected/401 first.
        r = self.client.get("/api/screener/oversold/export?format=xml")
        self.assertIn(r.status_code, [400, 401, 302, 403])


class TestDedupeLogic(unittest.TestCase):
    """The dedupe path is the core insight: collapse N scan rows for one ticker
    into a single record showing days_seen, trajectory, and latest metrics."""

    def test_single_ticker_multiple_dates_collapses_to_one(self):
        from features.screener.routes import _dedupe_oversold
        rows = [
            {"ticker": "ABC", "scan_date": "2026-05-20", "price": 10.0, "rsi_14": 28.0,
             "ai_verdict": "WATCH", "ai_confidence": 50, "status": "tracking",
             "price_trend": "falling", "name": "ABC Corp", "sector": "Tech"},
            {"ticker": "ABC", "scan_date": "2026-05-22", "price": 9.5, "rsi_14": 29.0,
             "ai_verdict": "WATCH", "ai_confidence": 55, "status": "tracking",
             "price_trend": "falling", "name": "ABC Corp", "sector": "Tech"},
            {"ticker": "ABC", "scan_date": "2026-05-25", "price": 9.8, "rsi_14": 32.0,
             "ai_verdict": "BOTTOM FORMING", "ai_confidence": 70, "status": "consolidating",
             "price_trend": "bouncing", "name": "ABC Corp", "sector": "Tech"},
        ]
        out = _dedupe_oversold(rows)
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual(r["ticker"], "ABC")
        self.assertEqual(r["days_seen"], 3)
        self.assertEqual(r["first_seen"], "2026-05-20")
        self.assertEqual(r["last_seen"], "2026-05-25")
        self.assertEqual(r["latest_verdict"], "BOTTOM FORMING")
        self.assertEqual(r["latest_confidence"], 70)
        # Trend path should consolidate consecutive duplicates (falling, falling, bouncing → falling → bouncing)
        self.assertEqual(r["trend_path"], "falling → bouncing")
        # Price change: 10 → 9.8 = -2%
        self.assertAlmostEqual(r["price_change_pct"], -2.0, places=1)

    def test_multiple_tickers_each_kept_separate(self):
        from features.screener.routes import _dedupe_oversold
        rows = [
            {"ticker": "AAA", "scan_date": "2026-05-20", "price": 10.0, "ai_confidence": 60},
            {"ticker": "BBB", "scan_date": "2026-05-20", "price": 20.0, "ai_confidence": 50},
            {"ticker": "AAA", "scan_date": "2026-05-21", "price": 10.5, "ai_confidence": 65},
        ]
        out = _dedupe_oversold(rows)
        self.assertEqual(len(out), 2)
        tickers = {r["ticker"] for r in out}
        self.assertEqual(tickers, {"AAA", "BBB"})
        aaa = next(r for r in out if r["ticker"] == "AAA")
        self.assertEqual(aaa["days_seen"], 2)

    def test_empty_input_returns_empty(self):
        from features.screener.routes import _dedupe_oversold
        self.assertEqual(_dedupe_oversold([]), [])

    def test_sort_by_days_seen_desc(self):
        from features.screener.routes import _dedupe_oversold
        rows = [
            {"ticker": "ONE", "scan_date": "2026-05-25", "price": 5.0, "ai_confidence": 90},
            {"ticker": "MANY", "scan_date": "2026-05-20", "price": 5.0, "ai_confidence": 50},
            {"ticker": "MANY", "scan_date": "2026-05-21", "price": 5.0, "ai_confidence": 55},
            {"ticker": "MANY", "scan_date": "2026-05-22", "price": 5.0, "ai_confidence": 60},
        ]
        out = _dedupe_oversold(rows)
        # MANY (seen 3 days) should rank above ONE (seen 1 day), despite ONE having higher confidence.
        self.assertEqual(out[0]["ticker"], "MANY")
        self.assertEqual(out[1]["ticker"], "ONE")


class TestBuilders(unittest.TestCase):
    """Smoke tests for CSV/TXT/PDF builder functions."""

    def _sample(self):
        return [{
            "ticker": "TEST",
            "name": "Test Co",
            "sector": "Tech",
            "days_seen": 2,
            "first_seen": "2026-05-20",
            "last_seen": "2026-05-22",
            "first_price": 10.0,
            "latest_price": 9.5,
            "price_change_pct": -5.0,
            "latest_rsi": 28.5,
            "latest_pct_change_1mo": -12.3,
            "latest_verdict": "WATCH",
            "latest_confidence": 65,
            "latest_status": "tracking",
            "latest_price_trend": "stabilizing",
            "bottom_signal_strength": "MODERATE",
            "decline_reason": "Sector rotation",
            "trend_path": "falling → stabilizing",
            "summary": "Looks oversold but waiting for confirmation.",
        }]

    def test_csv_has_header_and_row(self):
        from features.screener.routes import _build_csv
        body = _build_csv(self._sample())
        self.assertIn("Ticker", body)
        self.assertIn("Days Seen", body)
        self.assertIn("TEST", body)
        self.assertIn("falling → stabilizing", body)

    def test_txt_has_summary_block(self):
        from features.screener.routes import _build_txt
        from datetime import date
        body = _build_txt(self._sample(), date(2026, 5, 1), date(2026, 5, 26), True)
        self.assertIn("OVERSOLD SCREENER EXPORT", body)
        self.assertIn("TEST", body)
        self.assertIn("Seen 2 day(s)", body)
        self.assertIn("Trajectory:", body)

    def test_pdf_produces_bytes(self):
        from features.screener.routes import _build_pdf
        from datetime import date
        body = _build_pdf(self._sample(), date(2026, 5, 1), date(2026, 5, 26), True)
        self.assertIsInstance(body, (bytes, bytearray))
        # PDF magic header
        self.assertTrue(body.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
