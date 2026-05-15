"""Tests for the per-bot total-exposure cap + funds-deployed metrics.

User spec (May 2026): every bot must (1) expose a config knob to cap aggregate
funds deployed across all open positions as % of equity, (2) block new entries
that would exceed it, and (3) report deployed-funds % in the dashboard.

Run: docker compose exec app python -m pytest tests/test_funds_settings.py -v
"""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ph():
    from db import IS_POSTGRES
    return "%s" if IS_POSTGRES else "?"


class TestConfigDefaultsExist(unittest.TestCase):
    """Each bot must declare a default for the new exposure cap."""

    def test_watchdog_default(self):
        from features.watchdog.engine import WD_DEFAULTS
        self.assertIn("wd_max_total_exposure_pct", WD_DEFAULTS)
        # Watchdog is the day trader → conservative default
        self.assertLessEqual(float(WD_DEFAULTS["wd_max_total_exposure_pct"]), 75)

    def test_claude_bot_default(self):
        from claude_bot.bot_engine import CB_DEFAULTS
        self.assertIn("cb_max_total_exposure_pct", CB_DEFAULTS)


class TestRouteAllowlists(unittest.TestCase):
    """Config-save endpoints must accept the new key — otherwise the UI silently
    drops user changes."""

    def test_watchdog_allows_exposure_key(self):
        import inspect
        from features.watchdog import routes
        # Pull the route function source and look for the allowed key set
        src = inspect.getsource(routes)
        self.assertIn('"wd_max_total_exposure_pct"', src,
            "Watchdog route allowlist must accept wd_max_total_exposure_pct")

    def test_crypto_bot_allows_exposure_key(self):
        import inspect
        from features.bot_crypto import routes
        src = inspect.getsource(routes)
        self.assertIn('"max_total_exposure_pct"', src,
            "Crypto bot route allowlist must accept max_total_exposure_pct")

    def test_stock_bot_allows_exposure_key(self):
        import inspect
        from features.bot_stock import routes
        src = inspect.getsource(routes)
        self.assertIn('"stock_max_total_exposure_pct"', src,
            "Stock bot route allowlist must accept stock_max_total_exposure_pct")

    def test_claude_bot_allows_exposure_key(self):
        import inspect
        from claude_bot import routes
        src = inspect.getsource(routes)
        self.assertIn('"cb_max_total_exposure_pct"', src,
            "Claude bot route allowlist must accept cb_max_total_exposure_pct")


class TestRiskGateBlocksOverexposure(unittest.TestCase):
    """End-to-end: seed open positions, set a tight cap, verify the next entry
    is blocked. Uses a synthetic user_id so we don't pollute real data."""

    USER_ID = 999991  # high enough to avoid real users
    ASSET_TYPE = "watchdog"

    @classmethod
    def setUpClass(cls):
        from db import execute, query_one
        ph = _ph()
        # Seed user row first — bot_trades has a FK to users(id)
        existing = query_one(f"SELECT id FROM users WHERE id = {ph}", (cls.USER_ID,))
        if not existing:
            execute(
                f"INSERT INTO users (id, email) VALUES ({ph}, {ph})",
                (cls.USER_ID, f"zztest{cls.USER_ID}@test.local"),
            )
        # Seed 2 open watchdog positions worth $5000 each = $10000 deployed
        for ticker, size, price in [("ZZTEST_A", 100, 50.0), ("ZZTEST_B", 50, 100.0)]:
            execute(
                f"INSERT INTO bot_trades (user_id, coin, side, size, entry_price, status, "
                f"asset_type, strategy) VALUES ({ph},{ph},'buy',{ph},{ph},'open',{ph},'test')",
                (cls.USER_ID, ticker, size, price, cls.ASSET_TYPE),
            )
        # Set tight 50% exposure cap
        execute(
            f"INSERT INTO bot_config (user_id, key, value) VALUES ({ph},{ph},{ph})",
            (cls.USER_ID, "wd_max_total_exposure_pct", "50"),
        )
        execute(
            f"INSERT INTO bot_config (user_id, key, value) VALUES ({ph},{ph},{ph})",
            (cls.USER_ID, "wd_max_position_pct", "30"),
        )
        execute(
            f"INSERT INTO bot_config (user_id, key, value) VALUES ({ph},{ph},{ph})",
            (cls.USER_ID, "wd_enabled", "1"),
        )

    @classmethod
    def tearDownClass(cls):
        from db import execute
        ph = _ph()
        try:
            execute(f"DELETE FROM bot_trades WHERE user_id = {ph}", (cls.USER_ID,))
            execute(f"DELETE FROM bot_config WHERE user_id = {ph}", (cls.USER_ID,))
            execute(f"DELETE FROM users WHERE id = {ph}", (cls.USER_ID,))
        except Exception:
            pass

    def test_open_value_sums_correctly(self):
        from features.watchdog.engine import _wd_open_value
        # 100*50 + 50*100 = 5000 + 5000 = 10000
        self.assertAlmostEqual(_wd_open_value(self.USER_ID), 10000.0, places=2)

    def test_risk_check_blocks_when_cap_exceeded(self):
        """With $10k already deployed, balance=$30k, cap=50% → $15k cap.
        New position of $6k pushes to $16k → BLOCKED."""
        from features.watchdog.engine import _wd_risk_check
        result = _wd_risk_check(self.USER_ID, "ZZTEST_NEW", size_usd=6000, balance=30000)
        self.assertFalse(result["allowed"])
        self.assertIn("Total exposure", result["reason"])
        self.assertTrue("50%" in result["reason"] or "50.0%" in result["reason"],
            f"Expected 50% cap mention in: {result['reason']}")

    def test_risk_check_allows_when_under_cap(self):
        """$10k deployed + $4k new = $14k vs $15k cap → ALLOWED (other gates pass)."""
        from features.watchdog.engine import _wd_risk_check
        # Use a small balance-relative size so per-position pct also passes
        result = _wd_risk_check(self.USER_ID, "ZZTEST_NEW", size_usd=4000, balance=30000)
        # Expect either allowed=True OR a non-exposure rejection (e.g. position pct)
        if not result["allowed"]:
            self.assertNotIn("Total exposure", result["reason"],
                f"Should not be blocked by exposure gate, got: {result['reason']}")


class TestBalanceExposesFundsMetrics(unittest.TestCase):
    """Each bot's balance/status response must surface deployed-pct fields so
    the UI can render a funds gauge."""

    def test_watchdog_balance_has_deployed_fields(self):
        from features.watchdog.engine import get_watchdog_balance
        result = get_watchdog_balance(user_id=999992)
        for key in ("funds_deployed_usd", "funds_deployed_pct",
                    "funds_available_pct", "max_total_exposure_pct",
                    "max_position_pct"):
            self.assertIn(key, result, f"watchdog balance missing {key}")

    def test_claude_bot_balance_has_deployed_fields(self):
        from claude_bot.bot_engine import get_balance
        result = get_balance(user_id=999993)
        for key in ("funds_deployed_usd", "funds_deployed_pct",
                    "funds_available_pct", "max_total_exposure_pct",
                    "max_position_pct"):
            self.assertIn(key, result, f"claude_bot balance missing {key}")


class TestRiskManagerHelpers(unittest.TestCase):
    """The crypto + stock RiskManager classes need a get_open_positions_value
    helper for the new exposure gate."""

    def test_crypto_risk_manager_has_value_helper(self):
        from crypto_bot.risk_manager import RiskManager
        rm = RiskManager(user_id=0)
        self.assertTrue(hasattr(rm, "get_open_positions_value"))
        # Should return 0 for a fresh user
        self.assertIsInstance(rm.get_open_positions_value(), float)

    def test_stock_risk_manager_has_value_helper(self):
        from stock_bot.stock_risk_manager import StockRiskManager
        rm = StockRiskManager(user_id=0)
        self.assertTrue(hasattr(rm, "get_open_positions_value"))
        self.assertIsInstance(rm.get_open_positions_value(), float)


if __name__ == "__main__":
    unittest.main(verbosity=2)
