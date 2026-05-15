"""Tests for the 3-state buy-window classifier (May 2026 user request).

User asked: "BUY WINDOW CLOSED when enabled show green otherwise red and when
coming to closing time amber". The status endpoint returns a buy_window_state
field that the frontend maps to colors:
  open          → green
  closing_soon  → amber  (within 15 min of 13:00 ET cutoff)
  closed        → red

Because get_auto_trader_status reads `datetime.now()` directly, we patch
pytz.timezone(...).localize so we can drive each branch deterministically.

Run: docker compose exec app python -m pytest tests/test_buy_window_state.py -v
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeDatetime(datetime):
    """datetime subclass that returns a frozen UTC `now()`."""
    _frozen_utc = None

    @classmethod
    def now(cls, tz=None):  # noqa: D401
        ts = cls._frozen_utc
        if tz is not None:
            return ts.astimezone(tz)
        return ts.replace(tzinfo=None)


def _freeze_et(hour, minute):
    """Build a UTC instant that maps to (hour:minute) ET on a weekday."""
    et = pytz.timezone("US/Eastern")
    et_dt = et.localize(datetime(2026, 5, 4, hour, minute, 0))  # Mon 2026-05-04
    return et_dt.astimezone(pytz.utc)


class TestBuyWindowState(unittest.TestCase):

    def _state(self, hour, minute):
        from features.watchdog import engine
        _FakeDatetime._frozen_utc = _freeze_et(hour, minute)
        with patch.object(engine, "datetime", _FakeDatetime):
            return engine.get_auto_trader_status(user_id=0)

    def test_pre_open_is_closed(self):
        s = self._state(9, 30)  # 9:30 ET — before 9:45 window
        self.assertEqual(s["buy_window_state"], "closed")
        self.assertFalse(s["in_buy_window"])

    def test_just_after_open_is_open(self):
        s = self._state(9, 50)  # 5 min into window
        self.assertEqual(s["buy_window_state"], "open")
        self.assertTrue(s["in_buy_window"])

    def test_mid_window_is_open(self):
        s = self._state(11, 30)  # mid-window, well over 15 min to close
        self.assertEqual(s["buy_window_state"], "open")

    def test_15_min_before_close_is_closing_soon(self):
        s = self._state(12, 45)  # exactly 15 min before 13:00 ET
        self.assertEqual(s["buy_window_state"], "closing_soon")
        self.assertTrue(s["in_buy_window"])
        self.assertLessEqual(s["buy_window_minutes_to_close"], 15)

    def test_5_min_before_close_is_closing_soon(self):
        s = self._state(12, 55)
        self.assertEqual(s["buy_window_state"], "closing_soon")

    def test_after_close_is_closed(self):
        s = self._state(13, 5)  # 5 min after cutoff
        self.assertEqual(s["buy_window_state"], "closed")
        self.assertFalse(s["in_buy_window"])

    def test_afternoon_is_closed(self):
        s = self._state(15, 30)
        self.assertEqual(s["buy_window_state"], "closed")


class TestFrontendMapping(unittest.TestCase):
    """Source-level: lock in the green/amber/red color mapping in the JS."""

    def test_three_state_mapping_present(self):
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        js = (repo / "static" / "js" / "features" / "watchdog" / "watchdog.js").read_text()
        # Must reference all three states with explicit colors
        self.assertIn("buy_window_state", js,
            "Frontend must read buy_window_state from the status payload")
        self.assertIn("'#00c896'", js, "open state should map to green #00c896")
        self.assertIn("'#ff8c42'", js, "closing_soon state should map to amber #ff8c42")
        self.assertIn("'#ff4757'", js, "closed state should map to red #ff4757")
        # Sanity: each state key appears as a map key
        for key in ("open:", "closing_soon:", "closed:"):
            self.assertIn(key, js, f"Frontend buyWindowMap missing {key}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
