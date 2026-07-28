"""Tests for the analyzer per-ticker backtest: the article-aligned top-5
strategy detectors + the /api/backtest/ticker route.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import shared.backtest_strategies as S


def _flat(n=70, price=100.0):
    close = [price] * n
    high = [price] * n
    low = [price] * n
    vol = [1_000_000] * n
    return close, high, low, vol


def _valid_setup(d):
    """A detector result is well-formed: stop < entry < take_profit."""
    if d is None:
        return True
    for k in ("entry_price", "stop_loss", "take_profit"):
        if k not in d:
            return False
    return d["stop_loss"] < d["entry_price"] < d["take_profit"]


class TestRegistry(unittest.TestCase):
    def test_top5_registered(self):
        keys = [s["key"] for s in S.TOP_STRATEGIES]
        self.assertEqual(len(keys), 5)
        for k in keys:
            self.assertIn(k, S.STRATEGIES)
            self.assertTrue(callable(S.get_strategy(k)))
        # Article concepts present
        self.assertIn("smc_liquidity_sweep", keys)
        self.assertIn("fibonacci_retracement", keys)
        self.assertIn("breakout_retest", keys)

    def test_unknown_strategy_raises(self):
        with self.assertRaises(ValueError):
            S.get_strategy("does_not_exist")


class TestDetectorsFlatReturnNone(unittest.TestCase):
    """On flat, featureless data no strategy should fire."""

    def test_all_none_on_flat(self):
        c, h, l, v = _flat()
        n = len(c)
        for key, fn in S.STRATEGIES.items():
            self.assertIsNone(fn(c, h, l, v, n), f"{key} fired on flat data")

    def test_all_none_on_short_history(self):
        c, h, l, v = _flat(n=30)
        for key, fn in S.STRATEGIES.items():
            self.assertIsNone(fn(c, h, l, v, len(c)), f"{key} fired on 30 bars")


class TestDetectorTriggers(unittest.TestCase):
    """Craft the exact conditions each of the mechanically-deterministic
    strategies needs, and assert a well-formed setup comes back."""

    def test_smc_liquidity_sweep(self):
        n = 70
        close = [100.0] * n
        high = [101.0] * n
        low = [100.0] * n
        for i in range(50, 69):
            low[i] = 98.0          # prior swing low = 98
        low[69] = 97.0             # today sweeps below 98…
        close[69] = 99.0           # …and reclaims above it
        d = S.smc_liquidity_sweep(close, high, low, [1e6] * n, n)
        self.assertIsNotNone(d)
        self.assertTrue(_valid_setup(d))
        self.assertLess(d["stop_loss"], 97.0)  # below the sweep wick

    def test_price_action_key_level(self):
        n = 50
        close = [100.0] * n
        high = [100.0] * n
        low = [100.0] * n
        for i in range(30, 49):
            low[i] = 98.0          # 20-day support = 98
        low[49] = 98.0
        high[49] = 101.0
        close[49] = 100.0          # rejection: close in upper 40% of range
        d = S.price_action_key_level(close, high, low, [1e6] * n, n)
        self.assertIsNotNone(d)
        self.assertTrue(_valid_setup(d))

    def test_breakout_retest(self):
        n = 70
        close = [100.0] * n
        high = [100.0] * n         # prior range high = 100
        low = [100.0] * n
        close[69] = 101.0          # broke out and holding above
        high[69] = 101.5
        low[69] = 100.5            # pulled back to retest the level
        d = S.breakout_retest(close, high, low, [1e6] * n, n)
        self.assertIsNotNone(d)
        self.assertTrue(_valid_setup(d))
        self.assertLess(d["stop_loss"], 100.0)  # below the breakout level


class TestDetectorsNeverRaise(unittest.TestCase):
    """Fib + momentum are trend-dependent; here we only require they run
    cleanly on a rising series and return a well-formed setup or None."""

    def _rising(self, n=230):
        close = [80.0 + i * 0.2 for i in range(n)]
        high = [c + 0.5 for c in close]
        low = [c - 0.5 for c in close]
        return close, high, low, [1e6] * n

    def test_fibonacci_and_momentum_shape(self):
        c, h, l, v = self._rising()
        for fn in (S.fibonacci_retracement, S.momentum_rsi_stoch,
                   S.smc_liquidity_sweep, S.price_action_key_level, S.breakout_retest):
            self.assertTrue(_valid_setup(fn(c, h, l, v, len(c))))

    def test_no_raise_on_noisy_data(self):
        rng = np.random.default_rng(7)
        n = 260
        close = list(100 + np.cumsum(rng.normal(0, 1, n)))
        high = [c + abs(rng.normal(0, 1)) for c in close]
        low = [c - abs(rng.normal(0, 1)) for c in close]
        vol = [1e6] * n
        for key, fn in S.STRATEGIES.items():
            try:
                self.assertTrue(_valid_setup(fn(close, high, low, vol, n)), key)
            except Exception as e:  # noqa
                self.fail(f"{key} raised on noisy data: {e}")


class TestRoute(unittest.TestCase):
    def test_routes_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/backtest/ticker", rules)
        self.assertIn("/api/backtest/ticker/<int:run_id>", rules)

    def test_requires_auth(self):
        from app import app
        resp = app.test_client().post("/api/backtest/ticker", json={"ticker": "AAPL"})
        self.assertIn(resp.status_code, (401, 302))


if __name__ == "__main__":
    unittest.main()
