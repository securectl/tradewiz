"""Tests for the Buffett 'copy & paste' tracker (Smart Money tab)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTickerMap(unittest.TestCase):
    def test_maps_mangled_issuer_names(self):
        from features.smart_money.buffett import _ticker_for
        self.assertEqual(_ticker_for("AMERICAN EXPRESS CO", "AMERICAN"), "AXP")
        self.assertEqual(_ticker_for("COCA COLA CO", "COCA"), "KO")
        self.assertEqual(_ticker_for("OCCIDENTAL PETE CORP", "OCCIDENT"), "OXY")

    def test_falls_back_to_raw_ticker(self):
        from features.smart_money.buffett import _ticker_for
        self.assertEqual(_ticker_for("SOME BRAND NEW CO", "xyz"), "XYZ")


class TestAggregation(unittest.TestCase):
    def setUp(self):
        from features.smart_money import buffett
        buffett._cache.clear()

    def _run(self, rows, prices):
        from features.smart_money import buffett
        with mock.patch.object(buffett, "query_one",
                               side_effect=[{"id": 1}, {"d": "2026-05-15"}]), \
             mock.patch.object(buffett, "query", return_value=rows), \
             mock.patch.object(buffett, "_fetch_prices", return_value=prices):
            return buffett.get_buffett_holdings(force_refresh=True)

    def test_dedups_sums_weight_and_ranks(self):
        rows = [
            {"company_name": "APPLE INC", "ticker": "AAPL", "pct_of_portfolio": 5.0, "action": "INCREASED"},
            {"company_name": "APPLE INC", "ticker": "AAPL", "pct_of_portfolio": 17.0, "action": "HELD"},
            {"company_name": "COCA COLA CO", "ticker": "COCA", "pct_of_portfolio": 11.0, "action": "HELD"},
        ]
        d = self._run(rows, {"AAPL": 200.0, "KO": 60.0})
        h = d["holdings"]
        self.assertEqual(d["holdings_count"], 2)
        self.assertEqual(h[0]["ticker"], "AAPL")
        self.assertEqual(h[0]["weight"], 22.0)        # 5 + 17 aggregated
        self.assertEqual(h[0]["action"], "HELD")      # action of the heaviest sub-line
        self.assertEqual(h[0]["rank"], 1)
        self.assertEqual(h[0]["price"], 200.0)
        self.assertEqual(h[1]["ticker"], "KO")        # mangled 'COCA' remapped
        self.assertEqual(h[1]["rank"], 2)

    def test_action_labels_mapped(self):
        rows = [{"company_name": "MACYS INC", "ticker": "MACYS", "pct_of_portfolio": 1.0, "action": "NEW"}]
        d = self._run(rows, {"M": 15.0})
        self.assertEqual(d["holdings"][0]["action"], "NEW")
        self.assertEqual(d["holdings"][0]["ticker"], "M")

    def test_error_when_entity_missing(self):
        from features.smart_money import buffett
        with mock.patch.object(buffett, "query_one", return_value=None):
            d = buffett.get_buffett_holdings(force_refresh=True)
        self.assertIn("error", d)


class TestRealDb(unittest.TestCase):
    """Runs the real SQL (only the price fetch is mocked to avoid network)."""
    def setUp(self):
        from features.smart_money import buffett
        buffett._cache.clear()

    def test_runs_and_returns_sane_shape(self):
        from features.smart_money import buffett
        with mock.patch.object(buffett, "_fetch_prices", return_value={}):
            d = buffett.get_buffett_holdings(force_refresh=True)
        if "error" in d:
            self.skipTest("no Berkshire 13F data in this DB")
        self.assertGreater(d["holdings_count"], 0)
        self.assertTrue(all("ticker" in h and "weight" in h for h in d["holdings"]))
        # ranks are 1..N in order
        self.assertEqual([h["rank"] for h in d["holdings"]],
                         list(range(1, len(d["holdings"]) + 1)))


class TestRoute(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        self.assertIn("/api/smart-money/buffett",
                      [r.rule for r in app.url_map.iter_rules()])

    def test_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/smart-money/buffett")
        self.assertIn(resp.status_code, (401, 403, 302))


if __name__ == "__main__":
    unittest.main()
