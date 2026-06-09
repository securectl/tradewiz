"""Tests for the market-condition risk gauge and its analyzer wiring."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd


def _df(closes, volumes):
    return pd.DataFrame({"Close": closes, "Volume": volumes})


class TestRoute(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/market/gauge", rules)

    def test_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/market/gauge")
        self.assertIn(resp.status_code, (401, 302))


class TestIndexComponent(unittest.TestCase):
    def setUp(self):
        from shared import market_gauge
        self.mg = market_gauge

    def test_uptrend_high_volume_is_positive(self):
        closes = list(range(100, 125))            # steadily rising
        vols = [1_000_000] * 24 + [2_000_000]     # last bar heavy on an up day
        with mock.patch("shared.yf_fetch.get_history", return_value=_df(closes, vols)):
            score, meta = self.mg._index_component("SPY", 35)
        self.assertGreater(score, 0)
        self.assertEqual(meta["vol_direction"], "STRONG BUYING")
        self.assertTrue(meta["above_sma20"])

    def test_downtrend_high_volume_is_negative(self):
        closes = list(range(125, 100, -1))        # steadily falling
        vols = [1_000_000] * 24 + [2_000_000]     # heavy on a down day
        with mock.patch("shared.yf_fetch.get_history", return_value=_df(closes, vols)):
            score, meta = self.mg._index_component("SPY", 35)
        self.assertLess(score, 0)
        self.assertEqual(meta["vol_direction"], "STRONG SELLING")

    def test_unavailable_returns_none(self):
        with mock.patch("shared.yf_fetch.get_history", return_value=None):
            score, meta = self.mg._index_component("SPY", 35)
        self.assertIsNone(score)
        self.assertIn("error", meta)


class TestVixAndFg(unittest.TestCase):
    def setUp(self):
        from shared import market_gauge
        self.mg = market_gauge

    def test_low_vix_positive_high_vix_negative(self):
        with mock.patch("shared.yf_fetch.get_history", return_value=_df([12.0], [0])):
            s_low, _ = self.mg._vix_component(25)
        with mock.patch("shared.yf_fetch.get_history", return_value=_df([40.0], [0])):
            s_high, m = self.mg._vix_component(25)
        self.assertGreater(s_low, 0)
        self.assertEqual(s_high, -25.0)
        self.assertEqual(m["level"], "extreme")

    def test_fg_component_extremes(self):
        s_greed, _ = self.mg._fg_component({"score": 85, "rating": "Extreme Greed"}, 15)
        s_fear, _ = self.mg._fg_component({"score": 10, "rating": "Extreme Fear"}, 15)
        self.assertEqual(s_greed, 15)
        self.assertEqual(s_fear, -15.0)

    def test_fg_unavailable(self):
        s, m = self.mg._fg_component(None, 15)
        self.assertIsNone(s)


class TestGetMarketGauge(unittest.TestCase):
    def setUp(self):
        from shared import market_gauge
        market_gauge._CACHE.clear()
        self.mg = market_gauge

    def test_bullish_components_yield_buy(self):
        with mock.patch.object(self.mg, "_index_component", side_effect=[(30.0, {"change_5d": 2.0, "vol_direction": "STRONG BUYING"}), (20.0, {"change_5d": 2.5, "vol_direction": "NORMAL"})]), \
             mock.patch.object(self.mg, "_vix_component", return_value=(20.0, {"value": 14, "level": "very low"})), \
             mock.patch.object(self.mg, "_fear_greed", return_value={"score": 80, "rating": "Extreme Greed"}), \
             mock.patch.object(self.mg, "_fg_component", return_value=(15.0, {"score": 80, "rating": "Extreme Greed"})):
            g = self.mg.get_market_gauge(force_refresh=True)
        self.assertEqual(g["stance"], "BUY")
        self.assertTrue(g["available"])
        self.assertGreaterEqual(g["score"], 25)

    def test_bearish_components_yield_sell(self):
        with mock.patch.object(self.mg, "_index_component", side_effect=[(-30.0, {"change_5d": -3.0, "vol_direction": "STRONG SELLING"}), (-20.0, {"change_5d": -4.0, "vol_direction": "STRONG SELLING"})]), \
             mock.patch.object(self.mg, "_vix_component", return_value=(-25.0, {"value": 38, "level": "extreme"})), \
             mock.patch.object(self.mg, "_fear_greed", return_value={"score": 12, "rating": "Extreme Fear"}), \
             mock.patch.object(self.mg, "_fg_component", return_value=(-15.0, {"score": 12, "rating": "Extreme Fear"})):
            g = self.mg.get_market_gauge(force_refresh=True)
        self.assertEqual(g["stance"], "SELL")
        self.assertLessEqual(g["score"], -25)

    def test_all_unavailable_defaults_hold(self):
        with mock.patch.object(self.mg, "_index_component", return_value=(None, {"error": "x"})), \
             mock.patch.object(self.mg, "_vix_component", return_value=(None, {"error": "x"})), \
             mock.patch.object(self.mg, "_fear_greed", return_value=None), \
             mock.patch.object(self.mg, "_fg_component", return_value=(None, {"error": "x"})):
            g = self.mg.get_market_gauge(force_refresh=True)
        self.assertEqual(g["stance"], "HOLD")
        self.assertFalse(g["available"])

    def test_summarize_for_llm(self):
        g = {"available": True, "stance": "SELL", "score": -40, "label": "Risk-Off / Defensive",
             "components": {"spy": {"change_5d": -3.0, "vol_direction": "STRONG SELLING"},
                            "vix": {"value": 38, "level": "extreme"}}}
        block = self.mg.summarize_for_llm(g)
        self.assertIn("MARKET CONDITION: SELL", block)
        self.assertIn("VIX", block)
        self.assertEqual(self.mg.summarize_for_llm({"available": False}), "")


class TestAnalyzerAdjustment(unittest.TestCase):
    """generate_recommendation should risk-adjust by the market gauge."""

    def _bullish_indicators(self):
        return {
            "rsi_14": 60, "sma_20": 95, "sma_50": 100, "sma_200": 90,
            "macd_histogram": 1.0, "macd_bullish_cross": True,
            "bb_upper": 120, "bb_lower": 80, "relative_volume": 1.6,
        }

    def test_sell_market_downgrades_buy(self):
        from analysis_engine import generate_recommendation
        ind = self._bullish_indicators()
        base = generate_recommendation(ind, {"status": "breakout_up"}, 110)
        self.assertEqual(base["action"], "BUY")
        adj = generate_recommendation(ind, {"status": "breakout_up"}, 110,
                                      market={"available": True, "stance": "SELL", "score": -40})
        self.assertNotEqual(adj["action"], "BUY")           # downgraded
        self.assertLess(adj["score"], base["score"])         # penalized
        self.assertEqual(adj["market"]["stance"], "SELL")

    def test_buy_market_adds_tailwind(self):
        from analysis_engine import generate_recommendation
        ind = self._bullish_indicators()
        base = generate_recommendation(ind, {"status": "breakout_up"}, 110)
        adj = generate_recommendation(ind, {"status": "breakout_up"}, 110,
                                      market={"available": True, "stance": "BUY", "score": 40})
        self.assertGreaterEqual(adj["score"], base["score"])

    def test_no_market_unchanged(self):
        from analysis_engine import generate_recommendation
        ind = self._bullish_indicators()
        rec = generate_recommendation(ind, {"status": "breakout_up"}, 110)
        self.assertIsNone(rec["market"])


if __name__ == "__main__":
    unittest.main()
