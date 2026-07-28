"""Tests for the analyzer-recommendation forward-return validator.

Covers the pure aggregation/verdict math, the regime→stance mapping, an
integration run with a synthetic monotonically-rising series (monkeypatching
the network fetch + the recommendation reconstruction), an explicit no-look-
ahead guard, and route registration.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import shared.signal_validator as sv


class TestAggregation(unittest.TestCase):
    def test_agg_basic(self):
        recs = [
            {"score": 10, "fwd": {5: 0.02}},
            {"score": 20, "fwd": {5: -0.01}},
            {"score": 30, "fwd": {5: 0.04}},
            {"score": 40, "fwd": {5: None}},   # missing horizon ignored
        ]
        out = sv._agg(recs, [5])
        self.assertEqual(out["n"], 4)
        self.assertEqual(out[5]["n"], 3)
        # mean of 2, -1, 4 = 1.6667%
        self.assertAlmostEqual(out[5]["avg_pct"], 1.667, places=2)
        self.assertAlmostEqual(out[5]["median_pct"], 2.0, places=2)
        # 2 of 3 positive
        self.assertAlmostEqual(out[5]["hit_rate_pct"], 66.7, places=1)

    def test_agg_empty_horizon(self):
        out = sv._agg([{"score": 1, "fwd": {5: None}}], [5])
        self.assertIsNone(out[5]["avg_pct"])
        self.assertEqual(out[5]["n"], 0)

    def test_pearson(self):
        self.assertAlmostEqual(sv._pearson([1, 2, 3, 4], [1, 2, 3, 4]), 1.0, places=3)
        self.assertAlmostEqual(sv._pearson([1, 2, 3, 4], [4, 3, 2, 1]), -1.0, places=3)
        self.assertIsNone(sv._pearson([1, 2], [1, 2]))       # too few
        self.assertIsNone(sv._pearson([1, 1, 1], [1, 2, 3]))  # zero variance

    def test_market_from_regime(self):
        self.assertEqual(sv._market_from_regime("bull")["stance"], "BUY")
        self.assertEqual(sv._market_from_regime("down")["stance"], "SELL")
        self.assertEqual(sv._market_from_regime("chop")["stance"], "HOLD")
        self.assertIsNone(sv._market_from_regime("unknown"))


class TestVerdict(unittest.TestCase):
    def _stats(self, buy, sell, base):
        action_stats = {
            "BUY": {10: {"avg_pct": buy, "n": 100}},
            "SELL": {10: {"avg_pct": sell, "n": 100}},
        }
        baseline = {10: {"avg_pct": base, "n": 400}}
        return action_stats, baseline

    def test_edge(self):
        a, b = self._stats(buy=3.0, sell=0.0, base=0.5)
        v = sv._build_verdict(a, b, {10: 0.1}, [5, 10, 20])
        self.assertEqual(v["assessment"], "edge")
        self.assertAlmostEqual(v["buy_edge_vs_baseline_pct"], 2.5, places=2)
        self.assertAlmostEqual(v["buy_minus_sell_pct"], 3.0, places=2)

    def test_none_when_flat(self):
        a, b = self._stats(buy=0.5, sell=0.5, base=0.5)
        v = sv._build_verdict(a, b, {10: 0.0}, [5, 10, 20])
        self.assertEqual(v["assessment"], "none")

    def test_weak(self):
        a, b = self._stats(buy=1.0, sell=0.2, base=0.6)
        v = sv._build_verdict(a, b, {10: 0.02}, [5, 10, 20])
        self.assertEqual(v["assessment"], "weak")


class TestIntegration(unittest.TestCase):
    """End-to-end with a synthetic rising series; no network, no heavy engine."""

    def setUp(self):
        self._orig_dl = sv._download_universe
        self._orig_rec = sv._recommendation_at
        n = 260
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        price = [100 * (1.001 ** k) for k in range(n)]   # +0.1%/day, monotonic up
        self.df = pd.DataFrame({
            "Open": price, "High": [p * 1.01 for p in price],
            "Low": [p * 0.99 for p in price], "Close": price,
            "Volume": [1_000_000] * n,
        }, index=idx)
        self.n = n
        sv._download_universe = lambda tickers, s, e: {"AAA": self.df}

    def tearDown(self):
        sv._download_universe = self._orig_dl
        sv._recommendation_at = self._orig_rec

    def test_forward_returns_positive_and_buy_only(self):
        sv._recommendation_at = lambda df_slice, market: ("BUY", 50)
        rep = sv.validate_recommendation_signal(
            ["AAA"], "2024-06-01", "2024-12-31", horizons=(5, 10),
            use_regime=False, step=1)
        self.assertEqual(rep["signal"], "analyzer_recommendation")
        self.assertIn("BUY", rep["by_action"])
        self.assertGreater(rep["sample_size"], 0)
        # Rising price => positive forward return even after friction.
        self.assertGreater(rep["by_action"]["BUY"][10]["avg_pct"], 0)
        self.assertEqual(rep["by_action"]["BUY"][10]["hit_rate_pct"], 100.0)

    def test_no_lookahead(self):
        seen = {"max_idx": -1}

        def spy(df_slice, market):
            seen["max_idx"] = max(seen["max_idx"], len(df_slice) - 1)
            return "HOLD", 0

        sv._recommendation_at = spy
        sv.validate_recommendation_signal(
            ["AAA"], "2024-06-01", "2024-12-31", horizons=(10,),
            use_regime=False, step=1)
        # Last scored bar is n - max_h - 2; the signal must never see the entry
        # bar (i+1) or any forward bar.
        self.assertEqual(seen["max_idx"], self.n - 10 - 2)

    def test_step_reduces_sample(self):
        sv._recommendation_at = lambda df_slice, market: ("BUY", 50)
        r1 = sv.validate_recommendation_signal(
            ["AAA"], "2024-06-01", "2024-12-31", horizons=(5,), use_regime=False, step=1)
        r3 = sv.validate_recommendation_signal(
            ["AAA"], "2024-06-01", "2024-12-31", horizons=(5,), use_regime=False, step=3)
        self.assertGreater(r1["sample_size"], r3["sample_size"])


class TestRoute(unittest.TestCase):
    def test_routes_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/backtest/validate-signal", rules)
        self.assertIn("/api/backtest/validate-signal/<int:run_id>", rules)

    def test_requires_auth(self):
        from app import app
        resp = app.test_client().post("/api/backtest/validate-signal", json={})
        self.assertIn(resp.status_code, (401, 302))


if __name__ == "__main__":
    unittest.main()
