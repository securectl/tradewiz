"""Regression test for the fabricated 200-day SMA bug.

On a window shorter than 200 bars (e.g. the analyzer's 6-month default ≈ 124
bars) the code used to fall back to the *current close* for sma_200, which
flipped strong uptrends into a fake "downtrend" (50-MA below a 200-MA that was
really just today's price). It must instead fall back to a real average of the
available bars, so the trend read stays honest.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from analysis_engine import calculate_indicators, generate_recommendation


def _rising_df(n):
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = [100 + i * 0.5 for i in range(n)]          # steady uptrend
    return pd.DataFrame({
        "Open": close, "High": [c + 1 for c in close],
        "Low": [c - 1 for c in close], "Close": close,
        "Volume": [1_000_000] * n,
    }, index=idx)


class TestSma200NotFabricated(unittest.TestCase):
    def test_short_window_sma200_is_not_current_price(self):
        df = _rising_df(124)                            # ~6 months of daily bars
        ind = calculate_indicators(df)
        last_close = float(df["Close"].iloc[-1])
        # The old bug set sma_200 == last close. It must be a genuine average.
        self.assertNotAlmostEqual(ind["sma_200"], last_close, places=2)
        # On a rising series the average of prior bars is BELOW the latest price.
        self.assertLess(ind["sma_200"], last_close)
        # And the mean of a linear ramp sits near the midpoint, above the start.
        self.assertGreater(ind["sma_200"], float(df["Close"].iloc[0]))

    def test_uptrend_reads_as_uptrend_on_short_window(self):
        df = _rising_df(124)
        ind = calculate_indicators(df)
        # 50-MA (recent) must sit above the fallback 200-MA on a clean uptrend.
        self.assertGreater(ind["sma_50"], ind["sma_200"])
        rec = generate_recommendation(ind, {}, float(df["Close"].iloc[-1]), None)
        self.assertNotEqual(rec["action"], "SELL")
        self.assertTrue(any("Uptrend" in r for r in rec["reasons"]),
                        f"expected an uptrend reason, got {rec['reasons']}")

    def test_full_window_sma200_is_true_mean(self):
        df = _rising_df(260)
        ind = calculate_indicators(df)
        expected = float(df["Close"].rolling(200).mean().iloc[-1])
        self.assertAlmostEqual(ind["sma_200"], round(expected, 2), places=1)

    def test_sma200_stays_numeric(self):
        # Consumers (stock bot, ai_validator) do arithmetic on sma_200 — it must
        # never be None regardless of window length.
        for n in (30, 60, 124, 260):
            ind = calculate_indicators(_rising_df(n))
            self.assertIsInstance(ind["sma_200"], (int, float))


if __name__ == "__main__":
    unittest.main()
