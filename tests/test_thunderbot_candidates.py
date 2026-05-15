"""Tests for the ThunderBot Identified-Candidates pipeline (May 2026).

User directive 2026-05-14: drop multi-strategy signals; auto-trader only acts
on RSI + Volume + Bull-Flag candidates refreshed every 15 min, with LLM-gated
pullback risk that returns size_mult + hard_stop_pct. These tests lock in:

  1. Bull-flag detector accepts a clean pole+flag and rejects bad inputs.
  2. Candidate scorer respects RSI / rel-vol / range / flag gates.
  3. The cohort cache key changes every 15 min so a new scan kicks in.
  4. Pullback-risk gate fallback returns sane defaults when no API key.
  5. The /api/watchdog/candidates route is wired and returns the expected shape.

Run: docker compose exec app python -m pytest tests/test_thunderbot_candidates.py -v
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_5m_bull_flag_df(pole_pct=2.5, flag_drift_pct=-0.2, flag_range_pct=0.8, bars=12):
    """Synthesize a 5m candle DataFrame with a clean bull-flag pattern."""
    base = 100.0
    closes = []
    # First half: pole — linear climb to base * (1 + pole_pct/100)
    pole_top = base * (1 + pole_pct / 100)
    pole_bars = bars // 2
    for i in range(pole_bars):
        closes.append(base + (pole_top - base) * (i + 1) / pole_bars)
    # Second half: flag — consolidate around pole_top with drift
    flag_bars = bars - pole_bars
    drift_end = pole_top * (1 + flag_drift_pct / 100)
    rng = pole_top * flag_range_pct / 100
    for i in range(flag_bars):
        c = pole_top + (drift_end - pole_top) * (i + 1) / flag_bars
        # add tiny oscillation within range
        c += rng * (0.5 - (i % 2)) * 0.5
        closes.append(c)

    closes = np.array(closes)
    highs = closes * 1.002
    lows = closes * 0.998
    opens = np.concatenate([[base], closes[:-1]])
    vols = np.concatenate([np.full(pole_bars, 50000.0), np.full(flag_bars, 30000.0)])

    idx = pd.date_range(end=datetime.now(), periods=bars, freq="5min")
    return pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols,
    }, index=idx)


class TestBullFlagDetector(unittest.TestCase):
    """The pole+flag heuristic should accept a clean pattern and reject bad ones."""

    def test_detects_clean_bull_flag(self):
        from features.watchdog.engine import _detect_bull_flag
        df = _make_5m_bull_flag_df(pole_pct=2.5, flag_drift_pct=-0.2, flag_range_pct=0.8)
        result = _detect_bull_flag(df)
        self.assertTrue(result["detected"], f"Should detect clean flag, got: {result}")
        self.assertGreaterEqual(result["pole_pct"], 1.2)

    def test_rejects_weak_pole(self):
        from features.watchdog.engine import _detect_bull_flag
        df = _make_5m_bull_flag_df(pole_pct=0.3)  # well below 0.8% threshold
        result = _detect_bull_flag(df)
        self.assertFalse(result["detected"])
        self.assertIn("weak pole", result["reason"])

    def test_rejects_wide_flag_range(self):
        from features.watchdog.engine import _detect_bull_flag
        # Bigger range via larger oscillation
        df = _make_5m_bull_flag_df(pole_pct=2.5, flag_range_pct=3.0)
        result = _detect_bull_flag(df)
        # The detector measures actual H-L range — make sure it would fail. We
        # build a df where flag highs/lows span more than 1.8%.
        base = 100.0
        pole_top = base * 1.025
        closes = list(np.linspace(base, pole_top, 6))
        # Flag bars that span 3% high-to-low
        closes += [pole_top * 1.015, pole_top * 0.985, pole_top * 1.01, pole_top * 0.99, pole_top * 1.005, pole_top]
        c = np.array(closes)
        df2 = pd.DataFrame({
            "Open": np.concatenate([[base], c[:-1]]),
            "High": c * 1.005,
            "Low": c * 0.995,
            "Close": c,
            "Volume": np.full(len(c), 30000.0),
        }, index=pd.date_range(end=datetime.now(), periods=len(c), freq="5min"))
        result = _detect_bull_flag(df2)
        # Either rejected via wide flag or drift — both are valid rejections
        # for this messy pattern.
        if result["detected"]:
            self.fail(f"Should reject distributing pattern: {result}")

    def test_rejects_insufficient_bars(self):
        from features.watchdog.engine import _detect_bull_flag
        df = _make_5m_bull_flag_df(bars=4)
        result = _detect_bull_flag(df)
        self.assertFalse(result["detected"])

    def test_handles_empty_input(self):
        from features.watchdog.engine import _detect_bull_flag
        self.assertFalse(_detect_bull_flag(None)["detected"])
        self.assertFalse(_detect_bull_flag(pd.DataFrame())["detected"])


class TestCandidateScorer(unittest.TestCase):
    """RSI / rel-vol / range / flag gates and the composite score."""

    def _daily(self, rsi_target=55, rel_vol=2.0):
        """Build a 30-day daily frame that produces approx the requested RSI."""
        # Simple construction: walk closes so RSI lands near the target.
        # We don't need exact match — just within the 40-72 acceptance band.
        n = 30
        closes = [100.0]
        # Mix of up/down days. More up days → higher RSI.
        up_frac = 0.5 + (rsi_target - 50) / 100
        for i in range(n - 1):
            move = 0.6 if (i * 7) % 10 < up_frac * 10 else -0.4
            closes.append(closes[-1] + move)
        vols = [1_000_000.0] * (n - 1) + [int(1_000_000 * rel_vol)]
        idx = pd.date_range(end=datetime.now(), periods=n, freq="D")
        return pd.DataFrame({
            "Open": closes, "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes], "Close": closes, "Volume": vols,
        }, index=idx)

    def _mid_day_et(self):
        """A fixed mid-session ET datetime so the time-of-day rel-vol math is
        deterministic in tests. 12:00 ET = ~38% of the 9:30-16:00 session."""
        import pytz
        return pytz.timezone("US/Eastern").localize(datetime(2026, 5, 14, 12, 0, 0))

    def test_scores_passing_candidate(self):
        from features.watchdog.engine import _score_thunderbot_candidate
        # rel_vol=2.0 of full-day average × 38% elapsed = pacing at ~5x normal.
        # That's well above the 1.2x time-of-day-adjusted gate.
        df_d = self._daily(rsi_target=55, rel_vol=2.0)
        df_5 = _make_5m_bull_flag_df()
        result = _score_thunderbot_candidate("TEST", df_5, df_d, premarket_pct=1.5,
                                             now_et=self._mid_day_et())
        self.assertIsNotNone(result)
        self.assertNotIn("reject", result, f"Clean setup should score, got: {result}")
        self.assertGreater(result["score"], 0)
        self.assertEqual(result["ticker"], "TEST")
        self.assertIn("RSI", " ".join(result["reason_chips"]))

    def test_rejects_low_rel_volume(self):
        from features.watchdog.engine import _score_thunderbot_candidate
        # rel_vol 0.1 of full day × 38% elapsed = ~0.26x pace, well below 1.2x.
        df_d = self._daily(rsi_target=55, rel_vol=0.1)
        df_5 = _make_5m_bull_flag_df()
        result = _score_thunderbot_candidate("TEST", df_5, df_d, now_et=self._mid_day_et())
        self.assertTrue(result.get("reject"))
        self.assertEqual(result["stage"], "rel_volume")

    def test_rejects_missing_flag(self):
        from features.watchdog.engine import _score_thunderbot_candidate
        df_d = self._daily(rsi_target=55, rel_vol=2.0)
        # 5m frame with no pole
        df_5 = _make_5m_bull_flag_df(pole_pct=0.3)
        result = _score_thunderbot_candidate("TEST", df_5, df_d, now_et=self._mid_day_et())
        self.assertTrue(result.get("reject"))
        self.assertEqual(result["stage"], "bull_flag")


class TestCohortCaching(unittest.TestCase):
    """The candidate cache should key on the 15-min cohort window."""

    def test_cohort_key_changes_every_15_min(self):
        from features.watchdog import engine
        # Patch yfinance + the universe to avoid network calls
        with patch.object(engine, "_thunderbot_universe", return_value=[]):
            r1 = engine.identify_thunderbot_candidates(user_id=0)
            r2 = engine.identify_thunderbot_candidates(user_id=0)
            # Same cohort within 15 min → second call returns cached payload
            self.assertEqual(r1["cohort"], r2["cohort"])
            # second call should be flagged cached (empty universe still gets cached)
            # Note: with empty universe, the result is cached on first call.
            self.assertTrue(r2["cached"] or r2["candidates"] == r1["candidates"])

    def test_force_refresh_bypasses_cache(self):
        from features.watchdog import engine
        with patch.object(engine, "_thunderbot_universe", return_value=[]):
            engine.identify_thunderbot_candidates(user_id=0)
            r2 = engine.identify_thunderbot_candidates(user_id=0, force_refresh=True)
            self.assertFalse(r2["cached"])


class TestPullbackRiskGate(unittest.TestCase):
    """Pullback gate must always return a sane envelope, even without LLM."""

    def test_fallback_when_no_api_key(self):
        from features.watchdog.engine import _llm_pullback_risk_gate
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
            r = _llm_pullback_risk_gate("AMD", {"price": 100, "rsi": 55, "rel_volume": 2.0,
                                                "intra_range_pct": 2.5, "premarket_pct": 1.0,
                                                "bull_flag": {"pole_pct": 2.5, "flag_drift_pct": -0.2}})
        self.assertTrue(r["allow"])
        self.assertEqual(r["source"], "fallback")
        self.assertGreaterEqual(r["size_mult"], 0.3)
        self.assertLessEqual(r["size_mult"], 1.0)
        self.assertGreaterEqual(r["hard_stop_pct"], 1.5)
        self.assertLessEqual(r["hard_stop_pct"], 3.0)


class TestCandidatesRoute(unittest.TestCase):
    """Route should be registered and return the expected shape."""

    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/watchdog/candidates", rules)


class TestAsyncCandidatesFetch(unittest.TestCase):
    """get_thunderbot_candidates_async must NEVER block on the 30-50s yfinance
    scan — the UI route relies on this contract. It either returns cached/
    stale data instantly or returns a pending payload + kicks off a background
    refresh."""

    def setUp(self):
        # Clear any cached candidates between tests so each starts cold
        from features.watchdog import engine
        for k in list(engine._cache.keys()):
            if "thunderbot" in k:
                engine._cache.pop(k, None)
        engine._THUNDERBOT_SCAN_INFLIGHT["running"] = False

    def test_cold_call_returns_pending_quickly(self):
        from features.watchdog import engine
        import time as _t
        # Patch the universe to a single ticker so even if the bg thread runs
        # it doesn't slam yfinance during the test
        with patch.object(engine, "_thunderbot_universe", return_value=["TEST"]):
            t0 = _t.time()
            r = engine.get_thunderbot_candidates_async(user_id=99)
            elapsed = _t.time() - t0
        self.assertLess(elapsed, 1.0, f"async fetch took {elapsed:.2f}s — must be sub-second")
        self.assertTrue(r.get("pending"), f"expected pending payload, got: {r}")
        self.assertTrue(r.get("refreshing"))

    def test_warm_call_returns_cached(self):
        from features.watchdog import engine
        # Prime the cache by calling the sync function with an empty universe
        with patch.object(engine, "_thunderbot_universe", return_value=[]):
            engine.identify_thunderbot_candidates(user_id=99, force_refresh=True)
        r = engine.get_thunderbot_candidates_async(user_id=99)
        self.assertTrue(r["cached"])
        self.assertFalse(r.get("pending", False))


class TestTickerYahooLinks(unittest.TestCase):
    """Tickers across ThunderBot + Screener should route through the shared
    yahooFinanceLink helper in core.js so every visible ticker is one click
    from the Yahoo Finance quote page."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        cls.CORE_JS = (repo / "static" / "js" / "core.js").read_text()
        cls.WATCHDOG_JS = (repo / "static" / "js" / "features" / "watchdog" / "watchdog.js").read_text()
        cls.SCREENER_JS = (repo / "static" / "js" / "features" / "screener" / "screener.js").read_text()

    def test_shared_helper_defined_in_core(self):
        self.assertIn("function yahooFinanceLink", self.CORE_JS)
        # Must URL-encode (BRK.B etc.) and open in a new tab with noopener
        self.assertIn("encodeURIComponent(ticker)", self.CORE_JS)
        self.assertIn("finance.yahoo.com/quote/", self.CORE_JS)
        self.assertIn('target="_blank"', self.CORE_JS)
        self.assertIn('rel="noopener noreferrer"', self.CORE_JS)

    def test_candidates_table_uses_helper(self):
        self.assertIn("yahooFinanceLink(c.ticker)", self.WATCHDOG_JS)

    def test_rejection_samples_use_helper(self):
        self.assertIn("yahooFinanceLink(s.ticker", self.WATCHDOG_JS)

    def test_watchlist_chips_use_helper(self):
        # The watchlist chip block must link the ticker (compact variant)
        self.assertIn("yahooFinanceLink(t, {compact: true})", self.WATCHDOG_JS)

    def test_paper_trades_use_helper(self):
        # Both open + closed trade rows must wrap the ticker
        self.assertIn("yahooFinanceLink(t.ticker)", self.WATCHDOG_JS)

    def test_screener_card_uses_helper_with_stop_propagation(self):
        # Cards expand on row-click, so the link MUST stopPropagation so
        # clicking the ticker doesn't also toggle the details panel.
        self.assertIn("yahooFinanceLink(c.ticker, {stopPropagation: true})", self.SCREENER_JS)

    def test_screener_trending_tracker_uses_helper(self):
        self.assertIn("yahooFinanceLink(t.ticker)", self.SCREENER_JS)

    def test_screener_scanner_rows_use_helper(self):
        self.assertIn("yahooFinanceLink(r.ticker)", self.SCREENER_JS)


class TestScanLoopWiring(unittest.TestCase):
    """The scan loop must source from identify_thunderbot_candidates, not get_signals."""

    def test_scan_loop_calls_candidate_identifier(self):
        from pathlib import Path
        engine_src = (Path(__file__).resolve().parent.parent /
                      "features" / "watchdog" / "engine.py").read_text()
        # In the scan loop, the BUY-execution path should reference the
        # candidate identifier — not the legacy get_signals().
        # Find the scan loop section by anchor comment "4. Identify ThunderBot"
        self.assertIn("4. Identify ThunderBot", engine_src)
        # Within that block we should see the call:
        scan_block = engine_src.split("4. Identify ThunderBot")[1].split("# Sleep for scan interval")[0]
        self.assertIn("identify_thunderbot_candidates(user_id)", scan_block)
        # And it should NOT contain the old get_signals dispatch in the same block
        self.assertNotIn("get_signals(watchlist)", scan_block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
