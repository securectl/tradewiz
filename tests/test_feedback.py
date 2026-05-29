"""Tests for the user feedback feature."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFeedback(unittest.TestCase):
    def test_routes_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/feedback", rules)
        self.assertIn("/api/admin/feedback", rules)

    def test_clamp_int(self):
        from features.feedback.routes import _clamp_int
        self.assertEqual(_clamp_int(7, 0, 10), 7)
        self.assertEqual(_clamp_int(15, 0, 10), 10)   # clamp high
        self.assertEqual(_clamp_int(-3, 0, 10), 0)    # clamp low
        self.assertIsNone(_clamp_int("x", 0, 10))
        self.assertIsNone(_clamp_int(None, 1, 5))

    def test_submit_requires_auth(self):
        from app import app
        resp = app.test_client().post("/api/feedback", json={"nps": 9})
        self.assertIn(resp.status_code, (401, 302))

    def test_admin_feedback_requires_admin(self):
        from app import app
        resp = app.test_client().get("/api/admin/feedback")
        self.assertIn(resp.status_code, (401, 302, 403))

    def test_recipients_prefers_feedback_email(self):
        from features.feedback import routes
        with mock.patch.dict(os.environ,
                             {"FEEDBACK_EMAIL": "fb@x.com, ops@x.com", "ADMIN_EMAIL": "admin@x.com"}):
            self.assertEqual(routes._feedback_recipients(), ["fb@x.com", "ops@x.com"])

    def test_recipients_falls_back_to_admin(self):
        from features.feedback import routes
        env = {k: v for k, v in os.environ.items() if k not in ("FEEDBACK_EMAIL", "ADMIN_EMAIL")}
        env["ADMIN_EMAIL"] = "admin@x.com"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(routes._feedback_recipients(), ["admin@x.com"])

    def test_email_feedback_sends_to_recipients(self):
        from features.feedback import routes
        with mock.patch("shared.mailer.send_email", return_value=True) as real_send, \
             mock.patch("shared.mailer.is_configured", return_value=True), \
             mock.patch.object(routes, "_feedback_recipients", return_value=["admin@x.com"]), \
             mock.patch.object(routes, "_user_email", return_value="user@x.com"):
            routes._email_feedback(1, 9, 5, 4, "charts", "more coins", "")
            real_send.assert_called_once()
            to, subject, html = real_send.call_args[0]
            self.assertEqual(to, "admin@x.com")
            self.assertIn("user@x.com", subject)
            self.assertIn("more coins", html)

    def test_email_feedback_noop_when_unconfigured(self):
        from features.feedback import routes
        with mock.patch("shared.mailer.is_configured", return_value=False), \
             mock.patch("shared.mailer.send_email") as real_send:
            routes._email_feedback(1, 9, None, None, "", "", "")
            real_send.assert_not_called()

    def test_email_feedback_never_raises(self):
        from features.feedback import routes
        with mock.patch("shared.mailer.is_configured", side_effect=RuntimeError("boom")):
            # Must swallow any error so the feedback save is never affected.
            routes._email_feedback(1, 9, 5, 4, "v", "i", "")


if __name__ == "__main__":
    unittest.main()
