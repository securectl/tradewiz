"""Tests for the Dashboard landing endpoint + theme wiring."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDashboardRoute(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/dashboard/summary", rules)

    def test_bots_config_well_formed(self):
        from features.dashboard.routes import _BOTS
        assets = {b[0] for b in _BOTS}
        self.assertEqual(assets, {"watchdog", "crypto", "stock", "claude"})
        for asset, name, flag, goal in _BOTS:
            self.assertTrue(name and flag and goal)

    def test_enabled_helper(self):
        from features.dashboard import routes as r
        with patch.object(r, "query_one", return_value={"value": "1"}):
            self.assertTrue(r._enabled(1, "cb_enabled"))
        with patch.object(r, "query_one", return_value={"value": "0"}):
            self.assertFalse(r._enabled(1, "cb_enabled"))
        with patch.object(r, "query_one", return_value=None):
            self.assertFalse(r._enabled(1, "cb_enabled"))


class TestMySectors(unittest.TestCase):
    def test_norm_ticker(self):
        from features.dashboard.routes import _norm_ticker
        self.assertEqual(_norm_ticker("ATOM-USDT"), "ATOM-USD")
        self.assertEqual(_norm_ticker("AAPL"), "AAPL")

    def test_aggregate_sectors(self):
        from features.dashboard.routes import _aggregate_sectors
        trades = [{"coin": "AAPL", "pnl": 100}, {"coin": "MSFT", "pnl": -50},
                  {"coin": "XOM", "pnl": 200}, {"coin": "NVDA", "pnl": 50}]
        secmap = {"AAPL": "Technology", "MSFT": "Technology", "XOM": "Energy", "NVDA": "Technology"}
        out = _aggregate_sectors(trades, secmap)
        self.assertEqual(out[0]["sector"], "Energy")            # +200 best, ranked first
        tech = next(s for s in out if s["sector"] == "Technology")
        self.assertEqual(tech["pnl"], 100.0)                    # 100 - 50 + 50
        self.assertEqual(tech["trades"], 3)
        self.assertEqual(tech["wins"], 2)
        self.assertAlmostEqual(tech["win_rate"], 66.7, places=1)

    def test_resolve_sectors_no_network(self):
        from features.dashboard import routes as r
        with patch.object(r, "query", return_value=[{"ticker": "AAPL", "sector": "Technology"}]):
            out = r._resolve_sectors(["AAPL", "SPY", "BTC-USDT"])
        self.assertEqual(out["AAPL"], "Technology")   # screener_results
        self.assertEqual(out["SPY"], "Broad Market")  # ETF map
        self.assertEqual(out["BTC-USDT"], "Crypto")   # crypto bucket

    def test_route_registered(self):
        from app import app
        rules = [x.rule for x in app.url_map.iter_rules()]
        self.assertIn("/api/dashboard/my-sectors", rules)


class TestPermissionBoundary(unittest.TestCase):
    """Unauthenticated requests must be rejected — each user only sees own data."""
    def test_summary_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/dashboard/summary")
        self.assertIn(resp.status_code, (401, 302))

    def test_my_sectors_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/dashboard/my-sectors")
        self.assertIn(resp.status_code, (401, 302))


if __name__ == "__main__":
    unittest.main()
