"""Tests for Claude Bot kill-switch daily auto-reset (self-heal).

Background: a daily-loss kill switch used to stay ON forever — one bad day
left the bot showing RUNNING but idle for weeks. _maybe_autoreset_kill clears
an AUTO (daily-loss) kill on a new trading day, but never a manual kill.

Run: docker compose exec app python -m pytest tests/test_claude_bot_killswitch.py -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_bot import bot_engine as cb  # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _run(auto_date, daily_pnl, limit="1000"):
    """Call _maybe_autoreset_kill with mocked config + pnl. Returns
    (result, upserts) where upserts is the list of (key,value) writes."""
    cfg = {"cb_kill_auto_date": auto_date, "cb_daily_loss_limit": limit}
    upserts = []
    def fake_cfg(uid, key, default=None):
        return cfg.get(key, default if default is not None else "")
    def fake_upsert(uid, key, value):
        upserts.append((key, value))
        cfg[key] = value
    with patch.object(cb, "_cfg", side_effect=fake_cfg), \
         patch.object(cb, "_daily_pnl", return_value=daily_pnl), \
         patch("shared.helpers._upsert_bot_config", side_effect=fake_upsert):
        result = cb._maybe_autoreset_kill(1)
    return result, upserts


class TestKillSwitchAutoReset(unittest.TestCase):
    def test_manual_kill_never_resets(self):
        # No auto-date marker => manual kill => never auto-cleared.
        result, upserts = _run(auto_date="", daily_pnl=0.0)
        self.assertFalse(result)
        self.assertEqual(upserts, [])

    def test_same_day_kill_stays(self):
        # Fired today => keep until the next day.
        result, upserts = _run(auto_date=TODAY, daily_pnl=0.0)
        self.assertFalse(result)
        self.assertEqual(upserts, [])

    def test_new_day_within_limit_resets(self):
        # Fired yesterday, today's loss within limit => self-heal.
        result, upserts = _run(auto_date=YESTERDAY, daily_pnl=-10.0, limit="1000")
        self.assertTrue(result)
        self.assertIn(("cb_kill_switch", "0"), upserts)
        self.assertIn(("cb_kill_auto_date", ""), upserts)

    def test_new_day_still_breached_stays(self):
        # Fired yesterday but today already breached again => keep killed.
        result, upserts = _run(auto_date=YESTERDAY, daily_pnl=-2000.0, limit="1000")
        self.assertFalse(result)
        self.assertEqual(upserts, [])


if __name__ == "__main__":
    unittest.main()
