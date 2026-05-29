"""
Tests for the email mailer + daily alert digest.
Run: python -m pytest tests/test_alerts.py -v
"""
import sys
import os
import unittest
from unittest import mock
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMailer(unittest.TestCase):
    def test_no_provider_noops(self):
        import shared.mailer as m
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(m.is_configured())
            self.assertFalse(m.send_email("a@b.com", "s", "<p>h</p>"))

    def test_resend_path_posts(self):
        import shared.mailer as m
        fake = mock.Mock(status_code=200, text="{}")
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "re_123", "EMAIL_FROM": "X <x@y.com>"}, clear=True):
            with mock.patch("requests.post", return_value=fake) as post:
                ok = m.send_email("to@x.com", "Subj", "<p>hi</p>")
        self.assertTrue(ok)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.resend.com/emails")
        self.assertIn("Bearer re_123", kwargs["headers"]["Authorization"])
        self.assertEqual(kwargs["json"]["to"], ["to@x.com"])
        self.assertEqual(kwargs["json"]["from"], "X <x@y.com>")

    def test_resend_failure_returns_false(self):
        import shared.mailer as m
        fake = mock.Mock(status_code=422, text="bad")
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "re_123"}, clear=True):
            with mock.patch("requests.post", return_value=fake):
                self.assertFalse(m.send_email("to@x.com", "s", "h"))


class TestVolumeSpikes(unittest.TestCase):
    def test_flags_only_above_ratio(self):
        import alerts
        # Build a fake yfinance-style multiindex frame via a stub object.
        import pandas as pd
        idx = pd.RangeIndex(10)
        # SPIKE: last vol 10x the prior mean; CALM: flat volume.
        vol = pd.DataFrame({"SPIKE": [100]*9 + [1000], "CALM": [100]*10}, index=idx)
        close = pd.DataFrame({"SPIKE": [5.0]*10, "CALM": [5.0]*10}, index=idx)
        data = pd.concat({"Volume": vol, "Close": close}, axis=1)
        res = alerts.find_volume_spikes(universe=["SPIKE", "CALM"], downloader=lambda tk: data)
        tickers = [r["ticker"] for r in res]
        self.assertIn("SPIKE", tickers)
        self.assertNotIn("CALM", tickers)
        self.assertGreaterEqual(res[0]["ratio"], 2.0)


class TestPersistentOversold(unittest.TestCase):
    def test_uses_latest_date_and_min_days(self):
        import alerts
        calls = {}
        def fake_query(sql, params=()):
            if "MAX(scan_date)" in sql:
                return [{"d": "2026-05-29"}]
            calls["params"] = params
            return [{"ticker": "ABC", "name": "Abc", "sector": "Tech", "price": 10.0,
                     "rsi_14": 28.0, "days_tracked": 6, "bottom_signal_strength": "STRONG",
                     "ai_verdict": "BOTTOM FORMING"}]
        res = alerts.find_persistent_oversold(min_days=5, limit=3, querier=fake_query)
        self.assertEqual(res[0]["ticker"], "ABC")
        self.assertEqual(calls["params"], ("2026-05-29", 5, 3))

    def test_empty_when_no_data(self):
        import alerts
        res = alerts.find_persistent_oversold(querier=lambda sql, params=(): [{"d": None}])
        self.assertEqual(res, [])


class TestUpcomingEarnings(unittest.TestCase):
    def test_only_within_window(self):
        import alerts
        today = date(2026, 5, 29)
        soon = today + timedelta(days=3)
        far = today + timedelta(days=30)
        fetch = lambda t: {"SOON": soon, "FAR": far, "NONE": None}.get(t)
        res = alerts.find_upcoming_earnings(days=7, universe=["SOON", "FAR", "NONE"],
                                            fetcher=fetch, today=today)
        tickers = [r["ticker"] for r in res]
        self.assertEqual(tickers, ["SOON"])
        self.assertEqual(res[0]["days_away"], 3)


class TestDigest(unittest.TestCase):
    def test_builds_subject_and_sections(self):
        import alerts
        subject, html = alerts.build_daily_digest(
            [{"ticker": "NVDA", "price": 214.5, "ratio": 3.1, "volume": 5e7, "avg_volume": 1.6e7}],
            [{"ticker": "INTC", "price": 30.1, "rsi_14": 27.0, "days_tracked": 6, "sector": "Tech"}],
            [{"ticker": "AAPL", "earnings_date": "2026-06-01", "days_away": 3}],
            date_str="May 29, 2026",
        )
        self.assertIn("3 signals", subject)
        self.assertIn("Volume Spikes", html)
        self.assertIn("Earnings Coming Up", html)
        self.assertIn("NVDA", html)
        self.assertIn("AAPL", html)

    def test_empty_sections_render_placeholder(self):
        import alerts
        subject, html = alerts.build_daily_digest([], [], [], date_str="May 29, 2026")
        self.assertIn("0 signals", subject)
        self.assertIn("Nothing flagged today.", html)


class TestRecipients(unittest.TestCase):
    def test_env_override_takes_priority(self):
        import alerts
        with mock.patch.dict(os.environ, {"ALERT_RECIPIENTS": "a@x.com, b@y.com"}, clear=True):
            self.assertEqual(alerts.recipient_emails(), ["a@x.com", "b@y.com"])


class TestSendDailyAlerts(unittest.TestCase):
    def test_skips_without_provider(self):
        import alerts
        with mock.patch("shared.mailer.is_configured", return_value=False):
            res = alerts.send_daily_alerts()
        self.assertEqual(res["skipped"], "no_provider")

    def test_sends_to_each_recipient(self):
        import alerts
        with mock.patch("shared.mailer.is_configured", return_value=True), \
             mock.patch("shared.mailer.send_email", return_value=True) as send, \
             mock.patch.object(alerts, "collect_alerts",
                               return_value={"volume_spikes": [], "oversold": [], "earnings": []}):
            res = alerts.send_daily_alerts(recipients=["a@x.com", "b@y.com"])
        self.assertEqual(res["sent"], 2)
        self.assertEqual(send.call_count, 2)


class TestRoutesRegistered(unittest.TestCase):
    def test_alert_routes(self):
        from features.alerts.routes import bp
        self.assertEqual(bp.name, "alerts")
        from app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        for path in ("/api/alerts/status", "/api/alerts/optin",
                     "/api/alerts/preview", "/api/alerts/send-test"):
            self.assertIn(path, rules)


if __name__ == "__main__":
    unittest.main()
