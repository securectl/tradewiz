"""Unit tests for watchdog universe expansion + pullback entry gate.

User spec (May 2026):
  1. Watchdog must include daily-rocket names (NBIS, COHR, AAOI, AXTI, WOLF)
     so the screener and signal scan can actually find them.
  2. Entries must never fire on the opening-high spike — wait for a real
     intraday pullback before opening a position.

Run: docker compose exec app python -m pytest tests/test_watchdog_pullback_gate.py -v
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── Universe expansion ──────────────────────────────────────

class TestRocketTickersInUniverse(unittest.TestCase):
    """Each named rocket must appear somewhere in the watchdog's reachable
    universe — either MIDCAP/LOWCAP (default watchlist) or RUSSELL2000_TOP /
    LARGECAP / SP500 (reachable via the gainers screener)."""

    ROCKETS = ["NBIS", "COHR", "AAOI", "AXTI", "WOLF", "ORCL"]

    def test_rockets_in_full_universe(self):
        from screener import _get_full_universe
        universe = set(_get_full_universe())
        missing = [t for t in self.ROCKETS if t not in universe]
        self.assertEqual(missing, [],
            f"Rocket tickers missing from screener universe: {missing}")

    def test_midcap_includes_ai_optical_names(self):
        """The AI/optical/networking rockets the user named must be in
        MIDCAP_TICKERS so the watchdog's default watchlist scans them."""
        from screener import MIDCAP_TICKERS
        for t in ["NBIS", "COHR", "AAOI", "AXTI"]:
            self.assertIn(t, MIDCAP_TICKERS,
                f"{t} must be in MIDCAP_TICKERS (rocket name from May 2026 user feedback)")


class TestLargecapBreakoutThresholdRelaxed(unittest.TestCase):
    """ORCL-type large-caps were getting filtered out by the strict 7+ floor.
    Lowered to 6 in May 2026 to admit more daily-breakout setups."""

    def test_threshold_lowered(self):
        from features.watchdog import engine
        self.assertLessEqual(engine._LARGECAP_BREAKOUT_MIN_SCORE, 6,
            "Qullamaggie threshold must be ≤6 to catch ORCL-type daily breakouts")
        self.assertGreaterEqual(engine._LARGECAP_BREAKOUT_MAX, 8,
            "Per-cycle cap should allow ≥8 large-cap breakouts")


# ─── Pullback gate ───────────────────────────────────────────

def _make_intraday_df(highs, closes):
    """Build a fake 5m yfinance frame from parallel high/close lists."""
    n = len(highs)
    idx = pd.date_range("2026-05-01 09:30", periods=n, freq="5min", tz="US/Eastern")
    return pd.DataFrame({
        "Open":   closes,
        "High":   highs,
        "Low":    [c - 0.10 for c in closes],
        "Close":  closes,
        "Volume": [100000] * n,
    }, index=idx)


class TestPullbackGate(unittest.TestCase):
    """Verify _check_pullback_entry blocks opening-high entries and admits
    real pullbacks. now_et is injected to bypass the wall-clock 20m wait."""

    # 11:00 AM ET — well past the 20m warmup window
    LATE_MORNING = pytz.timezone("US/Eastern").localize(
        datetime(2026, 5, 1, 11, 0, 0))
    EARLY_OPEN = pytz.timezone("US/Eastern").localize(
        datetime(2026, 5, 1, 9, 35, 0))

    def test_blocks_too_early(self):
        """Pre-9:50 ET: block — need at least 20m of session to evaluate."""
        from features.watchdog.engine import _check_pullback_entry
        result = _check_pullback_entry("AAOI", now_et=self.EARLY_OPEN)
        self.assertFalse(result["allowed"])
        self.assertIn("Too early", result["reason"])

    def test_blocks_at_intraday_high(self):
        """Highs that ramp into the current bar (high age = 0): BLOCK."""
        # Steady ramp into current bar — high IS the last bar
        highs  = [100, 101, 102, 103, 104, 105, 106]
        closes = [99.5, 100.5, 101.5, 102.5, 103.5, 104.5, 105.5]
        df = _make_intraday_df(highs, closes)
        from features.watchdog import engine
        with patch.object(engine.yf, "download", return_value=df):
            result = engine._check_pullback_entry("NBIS", now_et=self.LATE_MORNING)
        self.assertFalse(result["allowed"])
        self.assertIn("Opening-high block", result["reason"])

    def test_blocks_at_high_with_no_pullback(self):
        """High set 30m ago but price re-tested it (0% off): BLOCK."""
        highs  = [100, 105, 104, 103, 104, 105]  # high at idx=1, 25m ago
        closes = [99,  104.99, 103, 102, 103.5, 105]
        df = _make_intraday_df(highs, closes)
        from features.watchdog import engine
        with patch.object(engine.yf, "download", return_value=df):
            result = engine._check_pullback_entry("COHR", now_et=self.LATE_MORNING)
        self.assertFalse(result["allowed"])
        self.assertTrue(
            "At/near intraday high" in result["reason"] or "above 9-EMA" not in result["reason"],
            f"Should block near-high entry, got: {result['reason']}")

    def test_allows_clean_pullback(self):
        """High set 25m ago, price 1% off, holding above 9-EMA: ALLOW."""
        # Bars 0-2 ramp up, bars 3-5 pull back ~1.5%
        highs  = [100, 101, 105, 104.5, 104, 103.8]
        closes = [99.5, 100.8, 104.5, 103.8, 103.5, 103.5]
        df = _make_intraday_df(highs, closes)
        from features.watchdog import engine
        with patch.object(engine.yf, "download", return_value=df):
            result = engine._check_pullback_entry("AXTI", now_et=self.LATE_MORNING)
        self.assertTrue(result["allowed"],
            f"Clean pullback should pass, got blocked: {result['reason']}")
        self.assertIn("Pullback OK", result["reason"])

    def test_blocks_failed_breakout(self):
        """Price >4% off intraday high: breakout failed, BLOCK."""
        # High at bar 1, then dump
        highs  = [100, 110, 108, 105, 103, 102]
        closes = [99,  109.5, 107, 104, 102, 100]  # 100 is ~9% off high of 110
        df = _make_intraday_df(highs, closes)
        from features.watchdog import engine
        with patch.object(engine.yf, "download", return_value=df):
            result = engine._check_pullback_entry("WOLF", now_et=self.LATE_MORNING)
        self.assertFalse(result["allowed"])
        self.assertIn("Too far off", result["reason"])

    def test_blocks_below_ema9(self):
        """Price below 5m 9-EMA: trend broken, BLOCK."""
        # Strong opening, then chop down through EMA
        highs  = [100, 105, 104, 103, 102, 101]
        closes = [99.8, 104.8, 103, 101, 100, 99]
        df = _make_intraday_df(highs, closes)
        from features.watchdog import engine
        with patch.object(engine.yf, "download", return_value=df):
            result = engine._check_pullback_entry("ORCL", now_et=self.LATE_MORNING)
        self.assertFalse(result["allowed"])
        # Either pct_off_high or below-EMA — both are correct rejections
        self.assertTrue(
            "Below 5m 9-EMA" in result["reason"] or "Too far off" in result["reason"],
            f"Expected EMA or pullback-depth rejection, got: {result['reason']}")

    def test_allows_when_intraday_data_unavailable(self):
        """yfinance returns empty: allow with warning (don't block engine
        if intraday data is briefly down)."""
        from features.watchdog import engine
        with patch.object(engine.yf, "download", return_value=pd.DataFrame()):
            result = engine._check_pullback_entry("NBIS", now_et=self.LATE_MORNING)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["intraday"].get("warning"), "no_data")

    def test_handles_yfinance_exception(self):
        """yfinance raises: allow with warning, log the error."""
        from features.watchdog import engine
        with patch.object(engine.yf, "download", side_effect=RuntimeError("yf down")):
            result = engine._check_pullback_entry("NBIS", now_et=self.LATE_MORNING)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["intraday"].get("warning"), "error")


class TestPullbackGateWiring(unittest.TestCase):
    """Source-level: ensure the gate is actually called from the trade path."""

    def test_gate_called_from_execute_trade(self):
        import inspect
        from features.watchdog import engine
        src = inspect.getsource(engine._wd_execute_trade)
        self.assertIn("_check_pullback_entry", src,
            "_wd_execute_trade must call _check_pullback_entry before opening")
        self.assertIn("PULLBACK BLOCK", src,
            "Gate rejection must be logged with PULLBACK BLOCK marker")


if __name__ == "__main__":
    unittest.main(verbosity=2)
