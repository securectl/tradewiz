"""Tests for the marketing landing page + public-signup gating."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLandingRoute(unittest.TestCase):
    def test_root_serves_landing_not_login(self):
        # Going live: anonymous visitors get the marketing page, not a redirect.
        from app import app
        r = app.test_client().get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"TradeWiz", r.data)
        self.assertIn(b"3-day", r.data)
        self.assertIn(b"/auth/signup", r.data)   # CTA points at signup

    def test_welcome_preview_route(self):
        from app import app
        self.assertEqual(app.test_client().get("/welcome").status_code, 200)


class TestPublicSignupGate(unittest.TestCase):
    def test_signup_uses_public_flag(self):
        import inspect, auth
        src = inspect.getsource(auth.signup)
        self.assertIn("public_signup", src)

    def test_public_signup_defaults_off(self):
        # With no flag row, is_enabled must be False (invite-only stays the default).
        import feature_flags as ff
        from unittest import mock
        with mock.patch("feature_flags.get_flag", return_value={"state": "off", "rollout_pct": 0}):
            self.assertFalse(ff.is_enabled("public_signup"))


class TestTrialLength(unittest.TestCase):
    def test_trial_days_env_configurable(self):
        import importlib, trial_manager
        with unittest.mock.patch.dict(os.environ, {"TRIAL_DAYS": "5"}):
            importlib.reload(trial_manager)
            self.assertEqual(trial_manager.TRIAL_DAYS, 5)
        # restore module to env default
        importlib.reload(trial_manager)


if __name__ == "__main__":
    import unittest.mock  # noqa
    unittest.main()
