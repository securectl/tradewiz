"""Tests for the Sector Radar auto research analyst (May 2026).

Locks in:
  1. Quant helpers (_ret_pct, _sma) and sector scoring on synthetic data.
  2. The pure-quant fallback used when the LLM is unavailable.
  3. Report persistence round-trip (_save_report → get_latest/get_history).
  4. The new sector_research LLM roles are registered.
  5. The /api/sector-radar/* routes are wired into the app.

Run: docker compose exec app python -m pytest tests/test_sector_radar.py -v
"""
import os
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from features.sector_radar import engine as sr  # noqa: E402


def _series_df(start, end, n=140, vol=1_000_000.0, vol_surge_last=1.0):
    """Synthetic OHLCV frame: close ramps linearly start→end over n days."""
    close = np.linspace(start, end, n)
    volume = np.full(n, vol)
    volume[-5:] = vol * vol_surge_last  # recent volume surge
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": volume,
    })


class TestQuantHelpers(unittest.TestCase):
    def test_ret_pct(self):
        close = np.array([100.0] * 100 + [110.0])  # +10% on the last bar vs 1d ago
        self.assertAlmostEqual(sr._ret_pct(close, 1), 10.0, places=3)
        self.assertIsNone(sr._ret_pct(np.array([1.0, 2.0]), 50))

    def test_sma(self):
        close = np.arange(1, 51, dtype=float)  # 1..50
        self.assertAlmostEqual(sr._sma(close, 50), 25.5, places=3)


class TestSectorScoring(unittest.TestCase):
    def test_outperformer_scores_and_ranks(self):
        # SPY flat-ish (+5%), SMH strong (+35%). Only those two have data, so
        # only Semiconductors should be scored — and with positive RS.
        spy = _series_df(100, 105)
        smh = _series_df(100, 135, vol_surge_last=1.6)
        combined = pd.concat({"SPY": spy, "SMH": smh}, axis=1)

        with patch.object(sr.yf, "download", return_value=combined):
            signals = sr.compute_sector_signals(force=True)

        self.assertTrue(signals, "Expected at least one scored sector")
        semis = [s for s in signals if s["key"] == "semis"]
        self.assertEqual(len(semis), 1)
        s = semis[0]
        self.assertGreater(s["rs_3m"], 0)        # outperforming SPY
        self.assertGreater(s["score"], 0)
        self.assertIn("components", s)
        self.assertTrue(s["above_50d"])          # strong uptrend

    def test_empty_download_yields_no_signals(self):
        with patch.object(sr.yf, "download", return_value=pd.DataFrame()):
            self.assertEqual(sr.compute_sector_signals(force=True), [])


class TestQuantFallback(unittest.TestCase):
    def test_fallback_picks_top_and_flags_itself(self):
        signals = [
            {"label": "Energy", "etf": "XLE", "leaders": ["XOM", "CVX"], "score": 88,
             "rs_3m": 12.0, "ma_stack": True, "vol_surge": 1.5, "pct_from_60d_high": 1.0},
            {"label": "Utilities", "etf": "XLU", "leaders": ["NEE"], "score": 40,
             "rs_3m": -3.0, "ma_stack": False, "vol_surge": 0.9, "pct_from_60d_high": 9.0},
        ]
        out = sr._quant_fallback(signals)
        self.assertEqual(out["top_sector"], "Energy")
        self.assertTrue(out["fallback"])
        self.assertEqual(out["runner_up"], "Utilities")
        self.assertIn("XOM", out["leaders"])
        self.assertGreaterEqual(out["conviction"], 40)

    def test_fallback_handles_empty(self):
        out = sr._quant_fallback([])
        self.assertTrue(out["fallback"])
        self.assertEqual(out["conviction"], 0)


class TestPersistence(unittest.TestCase):
    def test_save_and_read_back(self):
        report = {
            "generated_at": "2026-05-28T10:00:00",
            "mode": "daily", "trigger": "unittest", "regime": "RISK-ON",
            "context": {"macro": {"regime": "RISK-ON"}},
            "board": [{"key": "semis", "label": "Semiconductors", "etf": "SMH",
                       "score": 91, "leaders": ["NVDA"]}],
            "analyst": {"top_sector": "Semiconductors", "conviction": 82,
                        "leaders": ["NVDA", "AMD"]},
            "fallback": False,
        }
        sr._save_report(report)
        latest = sr.get_latest()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["analyst"]["top_sector"], "Semiconductors")
        self.assertEqual(latest["board"][0]["key"], "semis")

        hist = sr.get_history(limit=5)
        self.assertTrue(hist)
        self.assertEqual(hist[0]["top_sector"], "Semiconductors")

        detail = sr.get_sector_detail("semis")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["etf"], "SMH")


class TestLlmRolesAndRoutes(unittest.TestCase):
    def test_sector_roles_registered(self):
        from shared.llm_config import KNOWN_ROLES, DEFAULTS
        self.assertIn("sector_research", KNOWN_ROLES)
        self.assertIn("sector_research_deep", KNOWN_ROLES)
        self.assertTrue(DEFAULTS["sector_research"])

    def test_routes_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        for path in ("/api/sector-radar/latest", "/api/sector-radar/history",
                     "/api/sector-radar/run"):
            self.assertIn(path, rules)


if __name__ == "__main__":
    unittest.main()
