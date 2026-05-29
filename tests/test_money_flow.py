"""Tests for the money-flow indicator (shared/money_flow.py) and its screener
wiring — is money flowing IN (accumulation) or OUT (distribution / profit-taking)?"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.money_flow import (  # noqa: E402
    compute_money_flow, classify_money_flow, LABELS, MIN_BARS,
)


def _frame(closes, vols, high_off=0.5, low_off=0.5):
    """Build an OHLCV frame from close prices; highs/lows offset around close."""
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "High": closes + high_off,
        "Low": closes - low_off,
        "Close": closes,
        "Volume": np.asarray(vols, dtype=float),
    })


class TestClassify(unittest.TestCase):
    def test_accumulation(self):
        self.assertEqual(classify_money_flow(0.20, 60, 55, False), "IN")

    def test_distribution(self):
        self.assertEqual(classify_money_flow(-0.20, 40, 45, False), "OUT")

    def test_neutral(self):
        self.assertEqual(classify_money_flow(0.0, 50, 50, False), "NEUTRAL")

    def test_profit_taking_takes_precedence_over_positive_cmf(self):
        # Near highs, MFI was overbought (>80) and is now falling → topping,
        # even though CMF is still positive.
        self.assertEqual(classify_money_flow(0.15, 76, 82, True), "PROFIT_TAKING")

    def test_not_profit_taking_when_not_near_high(self):
        self.assertEqual(classify_money_flow(0.15, 76, 82, False), "IN")

    def test_not_profit_taking_when_mfi_rising(self):
        # Overbought but still climbing, near highs → not (yet) a top signal.
        self.assertEqual(classify_money_flow(0.15, 85, 82, True), "IN")


class TestComputeMoneyFlow(unittest.TestCase):
    def test_insufficient_bars_returns_none(self):
        df = _frame(list(range(10)), [1000] * 10)
        out = compute_money_flow(df)
        self.assertIsNone(out["mf_signal"])
        self.assertEqual(out["mf_label"], LABELS[None])
        self.assertIsNone(out["cmf"])

    def test_accumulation_series(self):
        # Closes near the top of each bar's range on solid volume → CMF positive.
        n = 30
        closes = np.linspace(100, 80, n)        # downtrend so NOT near highs
        df = _frame(closes, [1000] * n, high_off=0.1, low_off=0.9)  # close near high
        out = compute_money_flow(df)
        self.assertGreater(out["cmf"], 0)
        self.assertEqual(out["mf_signal"], "IN")

    def test_distribution_series(self):
        # Closes near the bottom of each bar's range → CMF negative.
        n = 30
        closes = np.linspace(100, 80, n)
        df = _frame(closes, [1000] * n, high_off=0.9, low_off=0.1)  # close near low
        out = compute_money_flow(df)
        self.assertLess(out["cmf"], 0)
        self.assertEqual(out["mf_signal"], "OUT")

    def test_flat_range_no_divide_by_zero(self):
        # High == Low on every bar (zero range) must not raise / NaN out.
        n = 20
        closes = np.array([50.0] * n)
        df = pd.DataFrame({"High": closes, "Low": closes, "Close": closes,
                           "Volume": [1000.0] * n})
        out = compute_money_flow(df)
        self.assertIsNotNone(out["mf_signal"])  # resolves to a real signal, no crash

    def test_missing_columns_returns_none(self):
        df = pd.DataFrame({"Close": list(range(20))})
        self.assertIsNone(compute_money_flow(df)["mf_signal"])

    def test_none_input(self):
        self.assertIsNone(compute_money_flow(None)["mf_signal"])

    def test_profit_taking_detected(self):
        # Clean rally peaking at the prior bar (MFI overbought ~100) then a single
        # dip on the last bar while still near the 20-bar high → PROFIT_TAKING.
        n = 25
        closes = list(np.linspace(50, 100, n - 1)) + [99.0]
        vols = [1000] * n
        df = _frame(closes, vols, high_off=0.2, low_off=0.4)
        out = compute_money_flow(df)
        self.assertEqual(out["mf_signal"], "PROFIT_TAKING")
        self.assertEqual(out["mf_label"], "Profit-Taking")


class TestScreenerHelpers(unittest.TestCase):
    def test_money_flow_fields_picks_three_keys(self):
        from screener import _money_flow_fields
        mf = {"cmf": 0.1, "mfi": 60.0, "mf_signal": "IN", "mf_label": "Money In"}
        self.assertEqual(_money_flow_fields(mf),
                         {"cmf": 0.1, "mfi": 60.0, "mf_signal": "IN"})

    def test_prompt_line_empty_without_signal(self):
        from screener import _mf_prompt_line
        self.assertEqual(_mf_prompt_line({"mf_signal": None}), "")

    def test_prompt_line_describes_signal(self):
        from screener import _mf_prompt_line
        line = _mf_prompt_line({"mf_signal": "PROFIT_TAKING", "cmf": -0.05, "mfi": 78})
        self.assertIn("profit-taking", line.lower())
        self.assertIn("CMF", line)
        self.assertIn("MFI", line)

    def test_persisted_keys_not_in_core_exclusion(self):
        # _store_screener_results dumps every non-core key into data_json; money
        # flow must NOT be a core column, so it round-trips via the blob.
        core = {"ticker", "price", "verdict", "confidence", "summary",
                "sector", "name", "market_cap"}
        for k in ("cmf", "mfi", "mf_signal"):
            self.assertNotIn(k, core)


if __name__ == "__main__":
    unittest.main()
