"""Tests for the Earnings Calendar feature (weekly most-anticipated board)."""

import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRouteRegistered(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/earnings/calendar", rules)

    def test_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/earnings/calendar")
        self.assertIn(resp.status_code, (401, 302))


class TestWeekWindow(unittest.TestCase):
    def test_monday_to_friday(self):
        from features.earnings_calendar.engine import _week_window
        # 2026-07-29 is a Wednesday -> window is Mon 27 .. Fri 31.
        mon, fri = _week_window(0, today=date(2026, 7, 29))
        self.assertEqual(mon, date(2026, 7, 27))
        self.assertEqual(fri, date(2026, 7, 31))
        self.assertEqual(mon.weekday(), 0)
        self.assertEqual(fri.weekday(), 4)

    def test_offset_shifts_by_weeks(self):
        from features.earnings_calendar.engine import _week_window
        mon, _ = _week_window(1, today=date(2026, 7, 27))
        self.assertEqual(mon, date(2026, 8, 3))


class TestScaleScores(unittest.TestCase):
    def test_min_max_scaled_0_100(self):
        from features.earnings_calendar.engine import _scale_scores
        rows = [{"_raw": -0.2}, {"_raw": 0.0}, {"_raw": 0.3}]
        _scale_scores(rows)
        self.assertEqual(rows[0]["score"], 0)
        self.assertEqual(rows[2]["score"], 100)
        self.assertTrue(0 < rows[1]["score"] < 100)
        self.assertNotIn("_raw", rows[0])

    def test_all_none_defaults_50(self):
        from features.earnings_calendar.engine import _scale_scores
        rows = [{"_raw": None}, {"_raw": None}]
        _scale_scores(rows)
        self.assertTrue(all(r["score"] == 50 for r in rows))


class TestRRGQuadrant(unittest.TestCase):
    def _series(self, values):
        import pandas as pd
        idx = pd.date_range("2026-01-01", periods=len(values), freq="D")
        return pd.Series(values, index=idx)

    def test_leading_when_outperforming_and_improving(self):
        from features.earnings_calendar.engine import _rrg_quadrant
        # Stock accelerates upward; benchmark flat -> ratio rising, above its
        # mean and short-mean above long-mean => Leading.
        stock = self._series([100 + i * 1.5 for i in range(80)])
        bench = self._series([100.0 for _ in range(80)])
        q, rs_ratio, rs_mom = _rrg_quadrant(stock, bench)
        self.assertEqual(q, "LE")
        self.assertGreater(rs_ratio, 100)
        self.assertGreater(rs_mom, 100)

    def test_lagging_when_underperforming_and_falling(self):
        from features.earnings_calendar.engine import _rrg_quadrant
        stock = self._series([100 - i * 1.2 for i in range(80)])
        bench = self._series([100.0 for _ in range(80)])
        q, _, _ = _rrg_quadrant(stock, bench)
        self.assertEqual(q, "LAG")

    def test_short_history_is_safe(self):
        from features.earnings_calendar.engine import _rrg_quadrant
        stock = self._series([100, 101, 102])
        bench = self._series([100, 100, 100])
        q, _, _ = _rrg_quadrant(stock, bench)
        self.assertIn(q, ("LE", "WE", "IM", "LAG"))


class TestBuildWeek(unittest.TestCase):
    """Assembly grouping — earnings + price legs stubbed (no network)."""

    def _run(self, closes=None):
        eng = "features.earnings_calendar.engine"
        earnings = {
            "MSFT": (date(2026, 7, 27), "bmo", "Microsoft Corp"),   # Mon before open
            "AAPL": (date(2026, 7, 29), "amc", "Apple Inc."),       # Wed after close
            "XOM": (date(2026, 7, 31), "tbd", "Exxon Mobil"),       # Fri TBD
            "OLD": (date(2026, 7, 20), "bmo", "Last week co"),      # outside window
        }
        with mock.patch(f"{eng}._universe", return_value=list(earnings.keys())), \
             mock.patch(f"{eng}._gather_earnings",
                        return_value={k: v for k, v in earnings.items()
                                      if date(2026, 7, 27) <= v[0] <= date(2026, 7, 31)}), \
             mock.patch(f"{eng}._download_prices", return_value=closes or {}):
            from features.earnings_calendar.engine import _build_week
            return _build_week(week_offset=0, today=date(2026, 7, 27))

    def test_week_metadata(self):
        payload = self._run()
        self.assertEqual(payload["week_start"], "2026-07-27")
        self.assertEqual(payload["week_end"], "2026-07-31")
        self.assertEqual(len(payload["days"]), 5)
        self.assertEqual(payload["counts"]["total"], 3)   # OLD excluded

    def test_rows_grouped_by_day_and_session(self):
        payload = self._run()
        mon, wed, fri = payload["days"][0], payload["days"][2], payload["days"][4]
        self.assertEqual(mon["dow"], "MON")
        self.assertEqual([r["symbol"] for r in mon["sessions"]["bmo"]["rows"]], ["MSFT"])
        self.assertEqual([r["symbol"] for r in wed["sessions"]["amc"]["rows"]], ["AAPL"])
        self.assertEqual([r["symbol"] for r in fri["sessions"]["tbd"]["rows"]], ["XOM"])

    def test_scores_present_and_sorted(self):
        import pandas as pd
        idx = pd.date_range("2026-01-01", periods=80, freq="D")
        closes = {
            "MSFT": pd.Series([100 + i for i in range(80)], index=idx),
            "AAPL": pd.Series([100 - i * 0.5 for i in range(80)], index=idx),
            "XOM": pd.Series([100 + (i % 5) for i in range(80)], index=idx),
            "SPY": pd.Series([100.0 for _ in range(80)], index=idx),
        }
        payload = self._run(closes=closes)
        for day in payload["days"]:
            for key in ("bmo", "amc", "tbd"):
                rows = day["sessions"][key]["rows"]
                for r in rows:
                    self.assertIsInstance(r["score"], int)
                    self.assertGreaterEqual(r["score"], 0)
                    self.assertLessEqual(r["score"], 100)


if __name__ == "__main__":
    unittest.main()
