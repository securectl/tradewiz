"""Tests for production-flag hardening (dev-login, debug, secret/cookie guards)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _env(**kw):
    """Isolated environment with only the given vars set."""
    return mock.patch.dict(os.environ, kw, clear=True)


class TestIsProduction(unittest.TestCase):
    def test_explicit_app_env_prod(self):
        from app_config import is_production
        with _env(APP_ENV="production"):
            self.assertTrue(is_production())
        with _env(APP_ENV="prod"):
            self.assertTrue(is_production())

    def test_explicit_dev_wins_over_database_url(self):
        from app_config import is_production
        with _env(APP_ENV="development", DATABASE_URL="postgres://x"):
            self.assertFalse(is_production())

    def test_flask_env_prod(self):
        from app_config import is_production
        with _env(FLASK_ENV="production"):
            self.assertTrue(is_production())

    def test_fallback_database_url_implies_prod(self):
        from app_config import is_production
        with _env(DATABASE_URL="postgres://x"):
            self.assertTrue(is_production())

    def test_no_signal_is_not_prod(self):
        from app_config import is_production
        with _env():
            self.assertFalse(is_production())


class TestDevLoginEnabled(unittest.TestCase):
    def test_disabled_in_production_even_with_flag(self):
        from app_config import dev_login_enabled
        with _env(APP_ENV="production", DEV_LOGIN="1"):
            self.assertFalse(dev_login_enabled())
        # DATABASE_URL-inferred prod also disables it (the H1 fix).
        with _env(DATABASE_URL="postgres://x"):
            self.assertFalse(dev_login_enabled())

    def test_enabled_locally_without_database_url(self):
        from app_config import dev_login_enabled
        with _env():
            self.assertTrue(dev_login_enabled())

    def test_explicit_flag_enables_in_nonprod_with_db(self):
        from app_config import dev_login_enabled
        with _env(APP_ENV="development", DATABASE_URL="postgres://x", DEV_LOGIN="1"):
            self.assertTrue(dev_login_enabled())
        with _env(APP_ENV="development", DATABASE_URL="postgres://x"):
            self.assertFalse(dev_login_enabled())

    def test_not_keyed_on_google_client_id(self):
        # The old bug: presence/absence of GOOGLE_CLIENT_ID must NOT decide this.
        from app_config import dev_login_enabled
        with _env(DATABASE_URL="postgres://x", GOOGLE_CLIENT_ID="abc"):
            self.assertFalse(dev_login_enabled())
        with _env(DATABASE_URL="postgres://x"):  # no GOOGLE_CLIENT_ID
            self.assertFalse(dev_login_enabled())  # still disabled in prod


class TestDebugEnabled(unittest.TestCase):
    def test_off_in_production(self):
        from app_config import debug_enabled
        with _env(APP_ENV="production", FLASK_DEBUG="1"):
            self.assertFalse(debug_enabled())

    def test_opt_in_locally(self):
        from app_config import debug_enabled
        with _env(FLASK_DEBUG="1"):
            self.assertTrue(debug_enabled())
        with _env():
            self.assertFalse(debug_enabled())


class TestDevLoginRouteGated(unittest.TestCase):
    def test_route_403_when_disabled(self):
        from app import app
        with mock.patch("app_config.dev_login_enabled", return_value=False):
            resp = app.test_client().post("/auth/dev-login", data={"email": "x@y.z"})
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
