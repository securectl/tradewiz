"""Tests for the analyzer Buy/Hold/Sell recommendation signal."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis_engine import generate_recommendation as gr  # noqa: E402


class TestRecommendation(unittest.TestCase):
    def test_bullish_confluence_buys(self):
        rec = gr({"sma_20": 100, "sma_50": 98, "sma_200": 90, "rsi_14": 60,
                  "macd_histogram": 0.5, "macd_bullish_cross": True,
                  "relative_volume": 1.8, "bb_upper": 110, "bb_lower": 85},
                 {"status": "breakout_up"}, 105)
        self.assertIn(rec["action"], ("BUY", "ACCUMULATE"))
        self.assertGreater(rec["score"], 0)
        self.assertTrue(rec["reasons"])

    def test_bearish_confluence_sells(self):
        rec = gr({"sma_20": 100, "sma_50": 102, "sma_200": 110, "rsi_14": 38,
                  "macd_histogram": -0.5, "macd_bearish_cross": True,
                  "relative_volume": 1.0, "bb_upper": 115, "bb_lower": 95},
                 {"status": "breakdown"}, 96)
        self.assertIn(rec["action"], ("SELL", "REDUCE"))
        self.assertLess(rec["score"], 0)

    def test_neutral_holds(self):
        # Mixed: longer-term uptrend but price has slipped below short MAs, flat
        # momentum → roughly balanced → HOLD.
        rec = gr({"sma_20": 100, "sma_50": 100, "sma_200": 90, "rsi_14": 50,
                  "macd_histogram": 0, "relative_volume": 1.0,
                  "bb_upper": 110, "bb_lower": 90}, {"status": ""}, 99.5)
        self.assertEqual(rec["action"], "HOLD")

    def test_overbought_caution(self):
        rec = gr({"sma_20": 100, "sma_50": 98, "sma_200": 90, "rsi_14": 82,
                  "macd_histogram": 0.1, "relative_volume": 1.0,
                  "bb_upper": 104, "bb_lower": 88}, {"status": ""}, 108)
        # Overbought + above upper band should temper the score
        self.assertTrue(any("overbought" in r.lower() or "overextended" in r.lower()
                            for r in rec["reasons"]))

    def test_shape(self):
        rec = gr({}, {}, 100)
        for k in ("action", "label", "score", "confidence", "summary", "reasons"):
            self.assertIn(k, rec)


if __name__ == "__main__":
    unittest.main()
