"""Tests for the money-flow + uptrend gate on screener recommendations."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestClassifyTrend(unittest.TestCase):
    def _series(self, vals):
        import pandas as pd
        return pd.Series(vals, index=pd.date_range("2026-01-01", periods=len(vals), freq="D"))

    def test_uptrend(self):
        import screener
        up = self._series([100 + i * 0.6 for i in range(220)])  # steadily rising
        self.assertTrue(screener._classify_trend(up)["uptrend"])

    def test_downtrend(self):
        import screener
        down = self._series([200 - i * 0.5 for i in range(220)])  # steadily falling
        self.assertFalse(screener._classify_trend(down)["uptrend"])

    def test_short_history_unknown(self):
        import screener
        self.assertIsNone(screener._classify_trend(self._series([1, 2, 3]))["uptrend"])


class TestFlowTrendGate(unittest.TestCase):
    def _run(self, opps, trends):
        import screener
        result = {"opportunities": list(opps), "risky": []}
        with mock.patch.object(screener, "_batch_trend", return_value=trends):
            return screener._flow_trend_gate(result)

    def test_downtrend_demoted(self):
        r = self._run(
            [{"ticker": "TAN", "mf_signal": "IN", "confidence": 80}],
            {"TAN": {"uptrend": False, "trend_label": "Downtrend"}},
        )
        self.assertEqual(len(r["opportunities"]), 0)
        self.assertEqual(len(r["risky"]), 1)
        self.assertIn("downtrend", r["risky"][0]["flow_trend_demoted"])

    def test_distribution_demoted(self):
        r = self._run(
            [{"ticker": "NLR", "mf_signal": "STRONG_OUT", "confidence": 75}],
            {"NLR": {"uptrend": True, "trend_label": "Uptrend"}},
        )
        self.assertEqual(len(r["opportunities"]), 0)
        self.assertIn("money flowing out", r["risky"][0]["flow_trend_demoted"])

    def test_negative_cmf_demoted(self):
        r = self._run(
            [{"ticker": "XYZ", "mf_signal": "NEUTRAL", "cmf": -0.2, "confidence": 60}],
            {"XYZ": {"uptrend": None, "trend_label": "Sideways"}},
        )
        self.assertEqual(len(r["opportunities"]), 0)

    def test_money_in_uptrend_kept(self):
        r = self._run(
            [{"ticker": "NVDA", "mf_signal": "STRONG_IN", "cmf": 0.15, "confidence": 90}],
            {"NVDA": {"uptrend": True, "trend_label": "Uptrend"}},
        )
        self.assertEqual(len(r["opportunities"]), 1)
        self.assertEqual(r["opportunities"][0]["trend_label"], "Uptrend")
        self.assertEqual(len(r["risky"]), 0)

    def test_missing_data_not_demoted(self):
        # No trend info + no distribution evidence → stays an opportunity.
        r = self._run(
            [{"ticker": "AAA", "confidence": 70}],
            {"AAA": {"uptrend": None, "trend_label": "—"}},
        )
        self.assertEqual(len(r["opportunities"]), 1)


if __name__ == "__main__":
    unittest.main()
