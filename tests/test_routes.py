"""
Comprehensive route and static file tests for the modular restructure.
Run: python -m pytest tests/test_routes.py -v
  or: python tests/test_routes.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest


class TestAppImports(unittest.TestCase):
    """Verify all modules import without errors."""

    def test_app_imports(self):
        from app import app
        self.assertIsNotNone(app)

    def test_shared_helpers(self):
        from shared.helpers import NumpyEncoder, _uid, P, _upsert_bot_config, _upsert_api_key, get_week_start, _pnl_summary
        self.assertIsNotNone(NumpyEncoder)
        self.assertIsNotNone(_uid)

    def test_shared_prompts(self):
        from shared.prompts.ipo import build_ipo_discovery_prompt, build_ipo_retry_prompt
        from shared.prompts.analysis import RESEARCH_SYSTEM
        from shared.prompts.screener import LOWCAP_SYSTEM
        self.assertTrue(len(build_ipo_discovery_prompt("ctx", "plats")) > 100)
        self.assertTrue(len(RESEARCH_SYSTEM) > 50)
        self.assertTrue(len(LOWCAP_SYSTEM) > 50)

    def test_feature_blueprints_import(self):
        from features.ipo.routes import bp as ipo_bp
        from features.status.routes import bp as status_bp
        from features.tracker.routes import bp as tracker_bp
        from features.admin.routes import bp as admin_bp
        from features.user.routes import bp as user_bp
        from features.analysis.routes import bp as analysis_bp
        from features.screener.routes import bp as screener_bp
        from features.bot_crypto.routes import bp as bot_crypto_bp
        from features.bot_stock.routes import bp as bot_stock_bp
        self.assertEqual(ipo_bp.name, "ipo")
        self.assertEqual(status_bp.name, "status")
        self.assertEqual(tracker_bp.name, "tracker")
        self.assertEqual(admin_bp.name, "admin")
        self.assertEqual(user_bp.name, "user")
        self.assertEqual(analysis_bp.name, "analysis")
        self.assertEqual(screener_bp.name, "screener")
        self.assertEqual(bot_crypto_bp.name, "bot_crypto")
        self.assertEqual(bot_stock_bp.name, "bot_stock")

    def test_existing_packages(self):
        from crypto_bot.bot_engine import get_bot
        from stock_bot.stock_engine import get_stock_bot
        from skills.skill_bp import skill_bp
        self.assertIsNotNone(get_bot)
        self.assertIsNotNone(get_stock_bot)
        self.assertIsNotNone(skill_bp)


class TestRouteRegistration(unittest.TestCase):
    """Verify all routes are registered on the Flask app."""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app
        cls.client = app.test_client()
        cls.rules = {str(r): list(r.methods) for r in app.url_map.iter_rules()}

    def _assert_route_exists(self, path, method="GET"):
        matching = [r for r in self.rules if r == path or r.replace("<", "").replace(">", "") in path]
        self.assertTrue(len(matching) > 0, f"Route {method} {path} not registered")

    # Analysis routes
    def test_analyze_route(self):
        self._assert_route_exists("/api/analyze", "POST")

    def test_validate_route(self):
        self._assert_route_exists("/api/validate", "POST")

    def test_predict_route(self):
        self._assert_route_exists("/api/predict", "POST")

    def test_ai_status_route(self):
        self._assert_route_exists("/api/ai-status")

    # Screener routes
    def test_screener_route(self):
        self._assert_route_exists("/api/screener", "POST")

    def test_hot_sectors_route(self):
        self._assert_route_exists("/api/screener/hot-sectors")

    # IPO routes
    def test_ipos_route(self):
        self._assert_route_exists("/api/ipos")

    def test_ipo_platforms_route(self):
        self._assert_route_exists("/api/ipo-platforms")

    # Tracker routes
    def test_history_route(self):
        self._assert_route_exists("/api/history")

    def test_journal_route(self):
        self._assert_route_exists("/api/journal")

    def test_goals_route(self):
        self._assert_route_exists("/api/goals")

    # Bot crypto routes
    def test_bot_status_route(self):
        self._assert_route_exists("/api/bot/status")

    def test_bot_start_route(self):
        self._assert_route_exists("/api/bot/start", "POST")

    def test_bot_dashboard_route(self):
        self._assert_route_exists("/api/bot/dashboard")

    def test_bot_trades_route(self):
        self._assert_route_exists("/api/bot/trades")

    def test_bot_config_route(self):
        self._assert_route_exists("/api/bot/config")

    # Bot stock routes
    def test_stock_bot_status_route(self):
        self._assert_route_exists("/api/stock-bot/status")

    def test_stock_bot_start_route(self):
        self._assert_route_exists("/api/stock-bot/start", "POST")

    def test_stock_bot_trades_route(self):
        self._assert_route_exists("/api/stock-bot/trades")

    # Admin routes
    def test_admin_config_route(self):
        self._assert_route_exists("/api/admin/config")

    def test_admin_users_route(self):
        self._assert_route_exists("/api/admin/users")

    def test_admin_invite_route(self):
        self._assert_route_exists("/api/admin/invite")

    def test_admin_usage_route(self):
        self._assert_route_exists("/api/admin/usage")

    # User routes
    def test_me_route(self):
        self._assert_route_exists("/api/me")

    def test_settings_route(self):
        self._assert_route_exists("/api/settings")

    # Status routes
    def test_status_route(self):
        self._assert_route_exists("/api/status")

    def test_status_incidents_route(self):
        self._assert_route_exists("/api/status/incidents")


class TestPublicEndpoints(unittest.TestCase):
    """Test endpoints that don't require authentication."""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client = app.test_client()

    def test_ai_status(self):
        r = self.client.get("/api/ai-status")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("configured", data)

    def test_root_redirects(self):
        r = self.client.get("/")
        self.assertIn(r.status_code, [302, 301])

    def test_login_page(self):
        r = self.client.get("/auth/login")
        self.assertEqual(r.status_code, 200)


class TestAuthProtection(unittest.TestCase):
    """Verify auth-protected routes return 401/302, not 500."""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client = app.test_client()

    def _check_protected(self, method, path):
        if method == "GET":
            r = self.client.get(path)
        else:
            r = self.client.post(path, json={})
        self.assertIn(r.status_code, [401, 302, 403],
                      f"{method} {path} returned {r.status_code} (expected 401/302/403)")
        self.assertNotEqual(r.status_code, 500,
                            f"{method} {path} returned 500 SERVER ERROR")

    def test_me(self):
        self._check_protected("GET", "/api/me")

    def test_analyze(self):
        self._check_protected("POST", "/api/analyze")

    def test_validate(self):
        self._check_protected("POST", "/api/validate")

    def test_predict(self):
        self._check_protected("POST", "/api/predict")

    def test_screener(self):
        self._check_protected("POST", "/api/screener")

    def test_ipos(self):
        self._check_protected("GET", "/api/ipos")

    def test_history(self):
        self._check_protected("GET", "/api/history")

    def test_journal(self):
        self._check_protected("GET", "/api/journal")

    def test_goals(self):
        self._check_protected("GET", "/api/goals")

    def test_settings(self):
        self._check_protected("GET", "/api/settings")

    def test_bot_status(self):
        self._check_protected("GET", "/api/bot/status")

    def test_bot_trades(self):
        self._check_protected("GET", "/api/bot/trades")

    def test_stock_bot_status(self):
        self._check_protected("GET", "/api/stock-bot/status")

    def test_admin_config(self):
        self._check_protected("GET", "/api/admin/config")

    def test_admin_users(self):
        self._check_protected("GET", "/api/admin/users")

    def test_status(self):
        self._check_protected("GET", "/api/status")


class TestStaticFiles(unittest.TestCase):
    """Verify all JS and CSS files are accessible."""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client = app.test_client()

    JS_FILES = [
        "/static/js/core.js",
        "/static/js/init.js",
        "/static/js/features/analysis/analysis.js",
        "/static/js/features/screener/screener.js",
        "/static/js/features/ipo/ipo.js",
        "/static/js/features/tracker/tracker.js",
        "/static/js/features/bot_crypto/bot_crypto.js",
        "/static/js/features/bot_stock/bot_stock.js",
        "/static/js/features/status/status.js",
        "/static/js/features/research/research.js",
        "/static/js/features/user/settings.js",
        "/static/js/features/admin/admin.js",
    ]

    CSS_FILES = [
        "/static/css/core.css",
        "/static/css/docs.css",
        "/static/css/features/analysis/analysis.css",
        "/static/css/features/screener/screener.css",
        "/static/css/features/ipo/ipo.css",
        "/static/css/features/tracker/tracker.css",
        "/static/css/features/bot_crypto/bot_crypto.css",
        "/static/css/features/bot_stock/bot_stock.css",
        "/static/css/features/status/status.css",
        "/static/css/features/research/research.css",
        "/static/css/features/user/settings.css",
        "/static/css/features/admin/admin.css",
    ]

    def test_js_files_accessible(self):
        for path in self.JS_FILES:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, f"JS file {path} returned {r.status_code}")
            self.assertGreater(len(r.data), 50, f"JS file {path} is suspiciously small ({len(r.data)} bytes)")

    def test_css_files_accessible(self):
        for path in self.CSS_FILES:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, f"CSS file {path} returned {r.status_code}")

    def test_old_files_gone(self):
        """Ensure the old monolithic files no longer exist."""
        r1 = self.client.get("/static/js/app.js")
        r2 = self.client.get("/static/css/style.css")
        self.assertEqual(r1.status_code, 404, "Old app.js still exists!")
        self.assertEqual(r2.status_code, 404, "Old style.css still exists!")


class TestIndexHtmlIntegrity(unittest.TestCase):
    """Verify index.html references all required scripts and stylesheets."""

    @classmethod
    def setUpClass(cls):
        with open("templates/index.html") as f:
            cls.html = f.read()

    def test_core_css_referenced(self):
        self.assertIn("core.css", self.html)

    def test_core_js_referenced(self):
        self.assertIn("core.js", self.html)

    def test_init_js_referenced(self):
        self.assertIn("init.js", self.html)

    def test_no_old_app_js(self):
        self.assertNotIn("app.js", self.html)

    def test_no_old_style_css(self):
        self.assertNotIn("style.css", self.html)

    def test_all_feature_js_referenced(self):
        for feature in ["analysis", "screener", "ipo", "tracker", "bot_crypto", "bot_stock", "status", "research", "settings", "admin"]:
            self.assertIn(f"features/{feature}" if feature != "settings" else "features/user/settings",
                         self.html, f"Feature JS {feature} not in index.html")

    def test_all_feature_css_referenced(self):
        for feature in ["analysis", "screener", "ipo", "tracker", "bot_crypto", "bot_stock", "status", "research", "admin"]:
            self.assertIn(f"features/{feature}", self.html, f"Feature CSS {feature} not in index.html")


class TestBlueprintCount(unittest.TestCase):
    """Verify expected number of blueprints are registered."""

    def test_blueprint_count(self):
        from app import app
        # Expected: auth, docs, billing, skills, ipo, status, tracker, admin, user, analysis, screener, bot_crypto, bot_stock
        bp_names = [bp.name for bp in app.blueprints.values()]
        expected = {"auth", "docs", "billing", "skills", "ipo", "status", "tracker",
                    "admin", "user", "analysis", "screener", "bot_crypto", "bot_stock"}
        for name in expected:
            self.assertIn(name, bp_names, f"Blueprint '{name}' not registered")

    def test_no_routes_in_app_py(self):
        """App.py should have minimal routes (just root + feature_static)."""
        from app import app
        app_routes = [r for r in app.url_map.iter_rules()
                      if r.endpoint not in ('static',) and '.' not in r.endpoint]
        # Should only have root '/' and '/features/<path:filename>'
        self.assertLessEqual(len(app_routes), 3,
                            f"app.py has too many direct routes: {[str(r) for r in app_routes]}")


class TestBotWatchdog(unittest.TestCase):
    """Verify bot crash recovery code exists."""

    def test_crypto_bot_has_watchdog(self):
        with open("crypto_bot/bot_engine.py") as f:
            code = f.read()
        self.assertIn("_start_watchdog", code, "Crypto bot missing watchdog")
        self.assertIn("WATCHDOG", code, "Crypto bot missing WATCHDOG log message")

    def test_stock_bot_has_watchdog(self):
        with open("stock_bot/stock_engine.py") as f:
            code = f.read()
        self.assertIn("_start_watchdog", code, "Stock bot missing watchdog")

    def test_crypto_validator_has_timeout(self):
        with open("crypto_bot/crypto_validator.py") as f:
            code = f.read()
        self.assertIn("timeout=90", code, "Crypto validator missing futures timeout")

    def test_stock_validator_has_timeout(self):
        with open("stock_bot/stock_validator.py") as f:
            code = f.read()
        self.assertIn("timeout=90", code, "Stock validator missing futures timeout")


if __name__ == "__main__":
    unittest.main(verbosity=2)
