"""Tests for the SMS alert scaffold (shared/sms.py + alerts dispatch wiring)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _env(**kw):
    return mock.patch.dict(os.environ, kw, clear=True)

_TWILIO = dict(TWILIO_ACCOUNT_SID="AC1", TWILIO_AUTH_TOKEN="tok", TWILIO_FROM_NUMBER="+15550001111")


class TestNormalizeE164(unittest.TestCase):
    def test_us_10_digit_gets_country_code(self):
        from shared.sms import normalize_e164
        self.assertEqual(normalize_e164("555-123-4567"), "+15551234567")

    def test_keeps_plus_prefixed(self):
        from shared.sms import normalize_e164
        self.assertEqual(normalize_e164("+44 20 7946 0958"), "+442079460958")

    def test_rejects_garbage(self):
        from shared.sms import normalize_e164
        self.assertIsNone(normalize_e164("abc"))
        self.assertIsNone(normalize_e164(""))
        self.assertIsNone(normalize_e164("123"))  # too short


class TestIsConfigured(unittest.TestCase):
    def test_dormant_without_env(self):
        from shared.sms import is_configured
        with _env():
            self.assertFalse(is_configured())

    def test_configured_with_from_number(self):
        from shared.sms import is_configured
        with _env(**_TWILIO):
            self.assertTrue(is_configured())

    def test_configured_with_messaging_service(self):
        from shared.sms import is_configured
        with _env(TWILIO_ACCOUNT_SID="AC1", TWILIO_AUTH_TOKEN="t", TWILIO_MESSAGING_SERVICE_SID="MG1"):
            self.assertTrue(is_configured())


class TestSendSms(unittest.TestCase):
    def test_noop_when_unconfigured(self):
        from shared import sms
        with _env():
            with mock.patch("requests.post") as post:
                self.assertFalse(sms.send_sms("+15551234567", "hi"))
                post.assert_not_called()

    def test_invalid_number_noop(self):
        from shared import sms
        with _env(**_TWILIO):
            self.assertFalse(sms.send_sms("nope", "hi"))

    def test_success_path(self):
        from shared import sms
        with _env(**_TWILIO):
            resp = mock.Mock(status_code=201, text="ok")
            with mock.patch("requests.post", return_value=resp) as post:
                self.assertTrue(sms.send_sms("5551234567", "hello"))
                # posted to Twilio with normalized destination
                _, kwargs = post.call_args
                self.assertEqual(kwargs["data"]["To"], "+15551234567")
                self.assertEqual(kwargs["data"]["From"], "+15550001111")


class TestAlertsSmsDispatch(unittest.TestCase):
    def test_digest_sms_is_short_and_has_optout(self):
        import alerts
        body = alerts.build_daily_digest_sms(
            [{"ticker": "AAPL"}, {"ticker": "MSFT"}], [{"x": 1}], [{"y": 1}])
        self.assertLessEqual(len(body), 320)
        self.assertIn("AAPL", body)
        self.assertIn("STOP", body)

    def test_digest_sms_empty(self):
        import alerts
        body = alerts.build_daily_digest_sms([], [], [])
        self.assertIn("no notable signals", body)

    def test_send_daily_alerts_sms_channel(self):
        import alerts
        sent = []
        with mock.patch.object(alerts, "collect_alerts",
                               return_value={"volume_spikes": [{"ticker": "AAPL"}],
                                             "oversold": [], "earnings": []}), \
             mock.patch.object(alerts, "recipient_phones", return_value=["+15551234567"]), \
             mock.patch("shared.mailer.is_configured", return_value=False), \
             mock.patch("shared.sms.is_configured", return_value=True), \
             mock.patch("shared.sms.send_sms", side_effect=lambda to, body: sent.append(to) or True):
            res = alerts.send_daily_alerts()
        self.assertEqual(res["sms_sent"], 1)
        self.assertEqual(res["email_sent"], 0)
        self.assertEqual(sent, ["+15551234567"])

    def test_send_daily_alerts_no_provider(self):
        import alerts
        with mock.patch("shared.mailer.is_configured", return_value=False), \
             mock.patch("shared.sms.is_configured", return_value=False):
            res = alerts.send_daily_alerts()
        self.assertEqual(res.get("skipped"), "no_provider")


if __name__ == "__main__":
    unittest.main()
