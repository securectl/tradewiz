"""Tests for admin-editable plan pricing (app_settings + resolution)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSettingsRoundTrip(unittest.TestCase):
    def test_set_get(self):
        import migrations, app_settings
        migrations.run_migrations()
        app_settings.set_setting("price_starter_amount", 29)
        self.assertEqual(app_settings.get_setting("price_starter_amount"), "29")
        self.assertEqual(app_settings.get_setting("nope", "fallback"), "fallback")


class TestPriceResolution(unittest.TestCase):
    def test_resolve_price_id_setting_wins(self):
        import subscriptions as s
        with mock.patch("app_settings.get_setting", return_value="price_ADMIN"):
            self.assertEqual(s.resolve_price_id("starter"), "price_ADMIN")

    def test_resolve_price_id_falls_back_to_env(self):
        import subscriptions as s
        with mock.patch("app_settings.get_setting", return_value=None), \
             mock.patch.dict(s.TIER_PRICES, {"pro": "price_ENV"}):
            self.assertEqual(s.resolve_price_id("pro"), "price_ENV")

    def test_plan_amount_default_and_override(self):
        import billing_bp
        with mock.patch("app_settings.get_setting", return_value=None):
            self.assertEqual(billing_bp._plan_amount("starter", 19), 19)
        with mock.patch("app_settings.get_setting", return_value="49"):
            self.assertEqual(billing_bp._plan_amount("starter", 19), 49)


class TestPricingRoute(unittest.TestCase):
    def test_requires_auth(self):
        from app import app
        self.assertIn(app.test_client().get("/billing/admin/pricing").status_code, (401, 403, 302))

    def test_route_registered(self):
        from app import app
        self.assertIn("/billing/admin/pricing", [r.rule for r in app.url_map.iter_rules()])


class TestLandingUsesPrice(unittest.TestCase):
    def test_landing_shows_configured_price(self):
        import migrations, app_settings
        migrations.run_migrations()
        app_settings.set_setting("price_starter_amount", 27)
        from app import app
        html = app.test_client().get("/").data.decode()
        self.assertIn("$27", html)


if __name__ == "__main__":
    unittest.main()
