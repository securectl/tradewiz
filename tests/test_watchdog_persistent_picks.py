"""Unit tests for the watchdog's persistent low/mid-cap picker.

Per the user's spec (Apr 2026): low/mid-cap tickers that appear in 2+ daily
screener scans should be treated as opportunities and routed to the watchdog,
regardless of today's snapshot verdict.

Run: docker compose exec app python -m pytest tests/test_watchdog_persistent_picks.py -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_pg():
    from db import IS_POSTGRES
    return IS_POSTGRES


def _ph():
    return "%s" if _is_pg() else "?"


class TestPersistentLowMidCapPicks(unittest.TestCase):
    """Seed screener_results with synthetic data and verify the picker."""

    SEED_PREFIX = "ZZTEST"  # Tickers prefixed with this so we don't collide

    @classmethod
    def setUpClass(cls):
        from db import execute
        ph = _ph()
        today = datetime.now().date()
        # Three tickers with varying persistence:
        #   ZZTEST_PERSIST_3D — 3 distinct scan_dates → should qualify
        #   ZZTEST_PERSIST_1D — 1 scan only → should NOT qualify (needs ≥2)
        #   ZZTEST_KNIFE_3D   — 3 days BUT verdict='FALLING KNIFE' → excluded
        seeds = []
        for delta in range(3):
            d = (today - timedelta(days=delta)).strftime("%Y-%m-%d")
            seeds.append((d, "lowcap", "ZZTEST_PERSIST_3D", "OPPORTUNITY", 70))
            seeds.append((d, "lowcap", "ZZTEST_KNIFE_3D",   "FALLING KNIFE", 55))
        # Single-day pick
        seeds.append((today.strftime("%Y-%m-%d"), "midcap", "ZZTEST_PERSIST_1D", "RISKY", 65))
        for scan_date, cat, ticker, verdict, conf in seeds:
            execute(
                f"INSERT INTO screener_results (category, scan_date, ticker, verdict, confidence) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
                (cat, scan_date, ticker, verdict, conf),
            )

    @classmethod
    def tearDownClass(cls):
        from db import execute
        ph = _ph()
        try:
            execute(f"DELETE FROM screener_results WHERE ticker LIKE {ph}", (f"{cls.SEED_PREFIX}%",))
        except Exception:
            pass

    def test_picker_returns_persistent_ticker(self):
        from features.watchdog.engine import _get_persistent_lowmidcap_picks
        picks = _get_persistent_lowmidcap_picks(min_days=2)
        tickers = {p["ticker"] for p in picks}
        self.assertIn("ZZTEST_PERSIST_3D", tickers,
            "3-day-persistent low-cap must qualify")

    def test_picker_excludes_single_day(self):
        from features.watchdog.engine import _get_persistent_lowmidcap_picks
        picks = _get_persistent_lowmidcap_picks(min_days=2)
        tickers = {p["ticker"] for p in picks}
        self.assertNotIn("ZZTEST_PERSIST_1D", tickers,
            "1-day pick must not qualify with min_days=2")

    def test_picker_excludes_falling_knife(self):
        from features.watchdog.engine import _get_persistent_lowmidcap_picks
        picks = _get_persistent_lowmidcap_picks(min_days=2)
        tickers = {p["ticker"] for p in picks}
        self.assertNotIn("ZZTEST_KNIFE_3D", tickers,
            "Falling-knife verdict overrides persistence — must be excluded")

    def test_picker_returns_correct_shape(self):
        """Returned dicts must match what _get_screener_candidates merges in."""
        from features.watchdog.engine import _get_persistent_lowmidcap_picks
        picks = _get_persistent_lowmidcap_picks(min_days=2)
        relevant = [p for p in picks if p["ticker"] == "ZZTEST_PERSIST_3D"]
        self.assertEqual(len(relevant), 1)
        p = relevant[0]
        for key in ("ticker", "screener_confidence", "screener_verdict",
                    "category", "entry_style", "days_tracked"):
            self.assertIn(key, p)
        self.assertEqual(p["category"], "lowcap")
        self.assertEqual(p["days_tracked"], 3)
        # Confidence boost: avg 70 + (3-1)*5 = +10 → 80
        self.assertGreaterEqual(p["screener_confidence"], 75)
        self.assertEqual(p["screener_verdict"], "PERSISTENT-3D")


class TestCategoryStylesContract(unittest.TestCase):
    """Lock in the user's "low/mid-cap focus, large-cap only on breakout" spec.

    Watchdog must:
      - Include lowcap and midcap in _get_screener_candidates
      - NOT include largecap (those route via _get_largecap_breakouts overlay)
    """

    def test_largecap_not_in_screener_categories(self):
        import inspect
        from features.watchdog import engine
        src = inspect.getsource(engine._get_screener_candidates)
        # Permissive largecap inclusion was the bug behind AAPL/MSFT trades
        # opening today. Scan source for the smoking-gun tuple.
        self.assertNotIn('("largecap", "momentum")', src,
            "largecap must NOT be in category_styles — use _get_largecap_breakouts overlay")

    def test_lowcap_is_in_screener_categories(self):
        import inspect
        from features.watchdog import engine
        src = inspect.getsource(engine._get_screener_candidates)
        self.assertIn('("lowcap", "momentum")', src,
            "lowcap must be in category_styles per Apr 2026 user spec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
