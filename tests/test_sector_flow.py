"""Tests for sector-level options money flow (Smart Money + dashboard)."""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRoute(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/smart-money/sector-flow", rules)

    def test_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/smart-money/sector-flow")
        self.assertIn(resp.status_code, (401, 302))


class TestClassify(unittest.TestCase):
    def test_thresholds(self):
        from features.smart_money.sector_flow import _classify
        # Pure net-dollar-flow signal — P/C volume must NOT flip it.
        self.assertEqual(_classify(500_000)[0], "MONEY IN")     # net call premium
        self.assertEqual(_classify(-500_000)[0], "SELLING")     # net put premium
        self.assertEqual(_classify(0)[0], "NEUTRAL")
        # A high volume P/C does not override positive dollar flow.
        self.assertEqual(_classify(500_000, 4.4)[0], "MONEY IN")

    def test_divergence_flagged(self):
        from features.smart_money import sector_flow as sf
        with mock.patch("features.watchdog.options_flow._fetch_options_flow",
                        return_value={"net_premium": 2_000_000, "pc_ratio": 4.4,
                                      "call_value": 4e6, "put_value": 2e6,
                                      "call_volume": 1000, "put_volume": 4400, "sentiment": "x"}):
            out = sf._compute()
        # MONEY IN by dollars but P/C 4.4 -> flagged divergent for honesty
        self.assertTrue(all(s["pc_divergent"] for s in out["sectors"]))


class TestCompute(unittest.TestCase):
    def test_compute_classifies_and_ranks(self):
        from features.smart_money import sector_flow as sf

        def fake_flow(etf):
            # XLK bullish, XLE bearish, others neutral-ish
            table = {
                "XLK": {"net_premium": 2_000_000, "pc_ratio": 0.5, "call_value": 3e6, "put_value": 1e6,
                        "call_volume": 5000, "put_volume": 1000, "sentiment": "BULLISH"},
                "XLE": {"net_premium": -1_500_000, "pc_ratio": 1.8, "call_value": 1e6, "put_value": 2.5e6,
                        "call_volume": 800, "put_volume": 3000, "sentiment": "BEARISH"},
            }
            return table.get(etf, {"net_premium": 0, "pc_ratio": 1.0, "call_value": 1e5, "put_value": 1e5,
                                   "call_volume": 100, "put_volume": 100, "sentiment": "NEUTRAL"})

        with mock.patch("features.watchdog.options_flow._fetch_options_flow", side_effect=fake_flow):
            out = sf._compute()
        self.assertEqual(out["count"], len(sf.SECTOR_ETFS))
        # XLK is the top inflow, XLE the top outflow
        self.assertEqual(out["top_inflow"], "Technology")
        self.assertEqual(out["top_outflow"], "Energy")
        self.assertEqual(out["tilt"], "RISK-ON")  # +2M and -1.5M and zeros -> net positive
        in_sectors = {s["sector"] for s in out["money_in"]}
        self.assertIn("Technology", in_sectors)
        sell_sectors = {s["sector"] for s in out["selling"]}
        self.assertIn("Energy", sell_sectors)

    def test_compute_skips_unavailable(self):
        from features.smart_money import sector_flow as sf
        with mock.patch("features.watchdog.options_flow._fetch_options_flow", return_value=None):
            out = sf._compute()
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["sectors"], [])


class TestGetNonBlocking(unittest.TestCase):
    def setUp(self):
        from features.smart_money import sector_flow as sf
        sf._cache["data"] = None
        sf._cache["ts"] = 0.0
        sf._refreshing = False

    def test_cached_when_fresh(self):
        from features.smart_money import sector_flow as sf
        sf._cache["data"] = {"sectors": [{"sector": "X"}], "tilt": "RISK-ON", "count": 1}
        sf._cache["ts"] = time.time()
        out = sf.get_sector_options_flow()
        self.assertTrue(out["cached"])
        self.assertFalse(out["computing"])
        self.assertEqual(out["tilt"], "RISK-ON")

    def test_first_call_computes_in_background(self):
        from features.smart_money import sector_flow as sf
        canned = {"sectors": [{"sector": "Tech"}], "money_in": [], "selling": [],
                  "tilt": "RISK-ON", "total_net_premium": 1, "count": 1, "timestamp": "x"}
        with mock.patch.object(sf, "_compute", return_value=canned):
            first = sf.get_sector_options_flow()
            self.assertTrue(first["computing"])           # returns immediately, empty board
            self.assertEqual(first["sectors"], [])
            # background thread should populate the cache shortly
            for _ in range(100):
                if not sf._refreshing and sf._cache["data"]:
                    break
                time.sleep(0.02)
            second = sf.get_sector_options_flow()
        self.assertFalse(second.get("computing"))
        self.assertEqual(second["tilt"], "RISK-ON")


if __name__ == "__main__":
    unittest.main()
