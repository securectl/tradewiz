"""Tests for the analyzer's Short vs Bull Interest read (compute_short_vs_bull).

Covers the pure computation — no network — with a synthetic OHLCV frame and a
fundamentals dict. Verifies the short side (reported short interest + trend),
the bull side (options flow with money-flow fallback), and the 14-day daily
pressure series.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from analysis_engine import compute_short_vs_bull


def _df(days=20):
    """Synthetic daily OHLCV. Closes pinned near the high => strong buying."""
    idx = pd.date_range("2026-06-01", periods=days, freq="D")
    rows = []
    for i in range(days):
        base = 100 + i
        rows.append({
            "Open": base,
            "High": base + 2,
            "Low": base - 2,
            "Close": base + 1.9,   # close near the high => bull share high
            "Volume": 1_000_000 + i * 1000,
        })
    return pd.DataFrame(rows, index=idx)


def _fund(**over):
    risk = {
        "short_ratio": 3.1,
        "shares_short": 12_400_000,
        "shares_short_prior": 10_000_000,
        "short_percent_float": 0.124,
        "short_percent_outstanding": 0.10,
        "short_interest_date": 1_752_192_000,
        "short_interest_prior_date": 1_750_896_000,
    }
    risk.update(over)
    return {"risk": risk}


class TestShortSide(unittest.TestCase):
    def test_short_metrics_and_rising_trend(self):
        r = compute_short_vs_bull("XYZ", _df(), fundamentals=_fund(), money_flow={})
        self.assertTrue(r["available"])
        s = r["short"]
        self.assertEqual(s["percent_float"], 12.4)
        self.assertEqual(s["days_to_cover"], 3.1)
        self.assertEqual(s["shares_short"], 12_400_000)
        # 12.4M vs 10M prior => +24% rising
        self.assertEqual(s["change_pct"], 24.0)
        self.assertEqual(s["trend"], "rising")
        # 12.4% of float against a 20% full-scale ceiling => 62% fill
        self.assertEqual(s["fill"], 62)

    def test_falling_trend(self):
        r = compute_short_vs_bull(
            "XYZ", _df(),
            fundamentals=_fund(shares_short=9_000_000, shares_short_prior=12_000_000),
            money_flow={})
        self.assertEqual(r["short"]["trend"], "falling")
        self.assertLess(r["short"]["change_pct"], 0)

    def test_fill_capped_at_100(self):
        r = compute_short_vs_bull(
            "XYZ", _df(), fundamentals=_fund(short_percent_float=0.55), money_flow={})
        self.assertEqual(r["short"]["fill"], 100)

    def test_no_short_data(self):
        r = compute_short_vs_bull("XYZ", _df(), fundamentals={"risk": {}}, money_flow={})
        self.assertIsNone(r["short"])
        # bull + series still make it available
        self.assertTrue(r["available"])


class TestBullSide(unittest.TestCase):
    def test_options_source(self):
        mf = {"options": {
            "sentiment": "BULLISH", "call_value": 6_000_000, "put_value": 4_000_000,
            "net_premium": 2_000_000, "pc_ratio": 0.61,
        }}
        r = compute_short_vs_bull("XYZ", _df(), fundamentals=_fund(), money_flow=mf)
        b = r["bull"]
        self.assertEqual(b["source"], "options")
        self.assertEqual(b["fill"], 60)   # 6M / 10M
        self.assertEqual(b["pc_ratio"], 0.61)

    def test_money_flow_fallback(self):
        mf = {"equity": {"cmf": 0.5, "mfi": 62, "mf_label": "Accumulation"}}
        r = compute_short_vs_bull("XYZ", _df(), fundamentals=_fund(), money_flow=mf)
        b = r["bull"]
        self.assertEqual(b["source"], "money_flow")
        # cmf 0.5 -> (0.5+1)/2*100 = 75
        self.assertEqual(b["fill"], 75)

    def test_money_flow_fallback_when_options_empty(self):
        mf = {"options": {"call_value": 0, "put_value": 0}, "equity": {"cmf": -1.0}}
        r = compute_short_vs_bull("XYZ", _df(), fundamentals=_fund(), money_flow=mf)
        self.assertEqual(r["bull"]["source"], "money_flow")
        self.assertEqual(r["bull"]["fill"], 0)


class TestSeries(unittest.TestCase):
    def test_14_day_series_shape(self):
        r = compute_short_vs_bull("XYZ", _df(20), fundamentals=_fund(), money_flow={})
        s = r["series_14d"]
        self.assertEqual(len(s), 14)   # tail(14)
        for pt in s:
            self.assertIn("date", pt)
            self.assertEqual(pt["bull"] + pt["bear"], 100)
            self.assertGreaterEqual(pt["bull"], 0)
            self.assertLessEqual(pt["bull"], 100)
        # close near high => strongly bullish daily reads
        self.assertGreater(s[-1]["bull"], 80)

    def test_zero_range_day_is_neutral(self):
        idx = pd.date_range("2026-06-01", periods=2, freq="D")
        df = pd.DataFrame(
            [{"Open": 100, "High": 100, "Low": 100, "Close": 100, "Volume": 1000}] * 2, index=idx)
        r = compute_short_vs_bull("XYZ", df, fundamentals=_fund(), money_flow={})
        self.assertEqual(r["series_14d"][0]["bull"], 50)

    def test_never_raises_on_bad_input(self):
        r = compute_short_vs_bull("XYZ", None, fundamentals=None, money_flow=None)
        self.assertFalse(r["available"])
        self.assertEqual(r["series_14d"], [])


if __name__ == "__main__":
    unittest.main()
