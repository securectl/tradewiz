"""Tests for the cohort/canary feature-flag mechanism."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _flag(state, pct=0):
    return mock.patch("feature_flags.get_flag", return_value={"state": state, "rollout_pct": pct})


class TestIsEnabled(unittest.TestCase):
    def test_off_hides_from_everyone_including_admin(self):
        import feature_flags as ff
        with _flag("off"):
            self.assertFalse(ff.is_enabled("x", user_id=1, roles=["admin"]))
            self.assertFalse(ff.is_enabled("x", user_id=2, roles=[]))

    def test_on_shows_everyone(self):
        import feature_flags as ff
        with _flag("on"):
            self.assertTrue(ff.is_enabled("x", user_id=2, roles=[]))

    def test_admin_state(self):
        import feature_flags as ff
        with _flag("admin"):
            self.assertTrue(ff.is_enabled("x", user_id=1, roles=["admin"]))
            self.assertFalse(ff.is_enabled("x", user_id=2, roles=["trader"]))

    def test_beta_state(self):
        import feature_flags as ff
        with _flag("beta"):
            self.assertTrue(ff.is_enabled("x", user_id=3, roles=["beta"]))
            self.assertTrue(ff.is_enabled("x", user_id=1, roles=["admin"]))  # admin leads
            self.assertFalse(ff.is_enabled("x", user_id=4, roles=["trader"]))

    def test_percent_extremes(self):
        import feature_flags as ff
        with _flag("percent", 0):
            self.assertFalse(ff.is_enabled("x", user_id=99, roles=[]))
        with _flag("percent", 100):
            self.assertTrue(ff.is_enabled("x", user_id=99, roles=[]))
        with _flag("percent", 50):
            self.assertTrue(ff.is_enabled("x", user_id=1, roles=["admin"]))  # admin always

    def test_percent_is_stable_per_user(self):
        import feature_flags as ff
        with _flag("percent", 50):
            a = ff.is_enabled("x", user_id=1234, roles=[])
            b = ff.is_enabled("x", user_id=1234, roles=[])
            self.assertEqual(a, b)

    def test_fails_closed_on_error(self):
        import feature_flags as ff
        with mock.patch("feature_flags.get_flag", side_effect=RuntimeError("db down")):
            self.assertFalse(ff.is_enabled("x", user_id=1, roles=["admin"]))


class TestBucket(unittest.TestCase):
    def test_deterministic_and_in_range(self):
        import feature_flags as ff
        vals = [ff._bucket("flagA", uid) for uid in range(200)]
        self.assertTrue(all(0 <= v < 100 for v in vals))
        self.assertEqual(ff._bucket("flagA", 42), ff._bucket("flagA", 42))
        # different flag → generally different bucket for same user
        self.assertNotEqual(("flagA", 42), ("flagB", 42))

    def test_rollout_is_monotonic(self):
        # As pct increases, a user never flips from enabled back to disabled.
        import feature_flags as ff
        uid = 777
        b = ff._bucket("ramp", uid)
        with mock.patch("feature_flags.get_flag",
                        side_effect=lambda f: {"state": "percent", "rollout_pct": b + 1}):
            self.assertTrue(ff.is_enabled("ramp", user_id=uid, roles=[]))
        with mock.patch("feature_flags.get_flag",
                        side_effect=lambda f: {"state": "percent", "rollout_pct": b}):
            self.assertFalse(ff.is_enabled("ramp", user_id=uid, roles=[]))


class TestSetFlagValidation(unittest.TestCase):
    def test_invalid_state_rejected(self):
        import feature_flags as ff
        with self.assertRaises(ValueError):
            ff.set_flag("x", "bogus", 10)


class TestRoutes(unittest.TestCase):
    def test_my_flags_requires_auth(self):
        from app import app
        self.assertIn(app.test_client().get("/api/feature-flags").status_code, (401, 302))

    def test_admin_flags_requires_admin(self):
        from app import app
        self.assertIn(app.test_client().get("/api/admin/feature-flags").status_code, (401, 403, 302))

    def test_routes_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/feature-flags", rules)
        self.assertIn("/api/admin/feature-flags", rules)


if __name__ == "__main__":
    unittest.main()
