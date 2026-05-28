"""Tests for ThunderBot exit tuning (May 2026 trade review).

The review of 96 closed trades found:
  - An RSI>60/+0.5% rule force-closed 48% of trades at +0.97% avg while the
    4-7% target was hit only twice. Winners were being strangled at ~1%.
  - One uncapped -12.4% blowup erased 27% of net profit.

Fixes locked in here (all in the pure _wd_decide_exit helper):
  1. Trailing stop arms at +2.5%, ratchets up, tightens past +4%.
  2. Catastrophic floor exits at <= -5% regardless of the stop level.
  3. Overnight carry / EOD force-close.
  4. RSI safety only fires when truly overbought (>75) AND already a real
     winner (>= +3%) — it must NOT cut a +1% trade anymore.

Run: docker compose exec app python -m pytest tests/test_thunderbot_exits.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from features.watchdog.engine import _wd_decide_exit, WD_DEFAULTS  # noqa: E402
from features.watchdog import engine as wd  # noqa: E402


def _cfg(**overrides):
    """Build a decision cfg from WD_DEFAULTS, with eod/overnight off by default."""
    c = {
        "hard_floor_pct": float(WD_DEFAULTS["wd_hard_floor_pct"]),
        "trail_arm_pct": float(WD_DEFAULTS["wd_trail_arm_pct"]),
        "trail_giveback_pct": float(WD_DEFAULTS["wd_trail_giveback_pct"]),
        "trail_tight_pct": float(WD_DEFAULTS["wd_trail_tight_pct"]),
        "trail_tight_giveback_pct": float(WD_DEFAULTS["wd_trail_tight_giveback_pct"]),
        "rsi_exit_min": float(WD_DEFAULTS["wd_rsi_exit_min"]),
        "rsi_exit_min_pnl": float(WD_DEFAULTS["wd_rsi_exit_min_pnl"]),
        "eod": False,
        "overnight": False,
    }
    c.update(overrides)
    return c


def _trade(entry=100.0, stop=98.0, tp=106.0, side="long", size=10):
    return {"entry_price": entry, "stop_loss": stop, "take_profit": tp,
            "side": side, "size": size, "coin": "TEST", "id": 1}


class TestTrailingStop(unittest.TestCase):
    def test_does_not_arm_below_threshold(self):
        # +1% gain — below the 2.5% arm. No new stop, no exit. This is the
        # exact scenario the old RSI>60/+0.5% rule used to bail on.
        reason, new_stop = _wd_decide_exit(_trade(), current_price=101.0,
                                           rsi=None, cfg=_cfg())
        self.assertIsNone(reason)
        self.assertIsNone(new_stop)

    def test_arms_and_sets_trailing_stop(self):
        # +3% gain arms the trail (1.5% giveback) → stop ratchets up to ~101.46
        reason, new_stop = _wd_decide_exit(_trade(stop=98.0), current_price=103.0,
                                           rsi=None, cfg=_cfg())
        self.assertIsNone(reason)
        self.assertIsNotNone(new_stop)
        self.assertAlmostEqual(new_stop, round(103.0 * 0.985, 2), places=2)
        self.assertGreater(new_stop, 98.0)

    def test_never_lowers_stop(self):
        # Stored stop already above the would-be trail → keep it, no update.
        reason, new_stop = _wd_decide_exit(_trade(stop=102.0), current_price=103.0,
                                           rsi=None, cfg=_cfg())
        self.assertIsNone(reason)
        self.assertIsNone(new_stop)

    def test_tightens_past_four_percent(self):
        # +5% gain → tighter 1.0% giveback → stop ~103.95
        reason, new_stop = _wd_decide_exit(_trade(stop=98.0), current_price=105.0,
                                           rsi=None, cfg=_cfg())
        self.assertIsNone(reason)
        self.assertAlmostEqual(new_stop, round(105.0 * 0.99, 2), places=2)

    def test_trailed_stop_locks_in_profit(self):
        # A previously-trailed stop at 101.46 is hit when price slips to 101.00,
        # locking in ~+1% instead of round-tripping to break-even.
        reason, new_stop = _wd_decide_exit(_trade(stop=101.46), current_price=101.0,
                                           rsi=None, cfg=_cfg())
        self.assertIsNotNone(reason)
        self.assertIn("101.46", reason)


class TestCatastrophicFloor(unittest.TestCase):
    def test_floor_fires_before_wider_stop(self):
        # -6% with the ATR stop still sitting at 92 (not yet hit at 94). The
        # hard floor must catch it — this is the missing guard that let VCX
        # run to -12.4%.
        reason, _ = _wd_decide_exit(_trade(stop=92.0), current_price=94.0,
                                    rsi=None, cfg=_cfg())
        self.assertIsNotNone(reason)
        self.assertIn("Catastrophic", reason)

    def test_floor_disabled_when_zero(self):
        reason, _ = _wd_decide_exit(_trade(stop=80.0), current_price=94.0,
                                    rsi=None, cfg=_cfg(hard_floor_pct=0))
        self.assertIsNone(reason)


class TestOvernightAndEod(unittest.TestCase):
    def test_eod_closes_everything(self):
        reason, _ = _wd_decide_exit(_trade(), current_price=99.0,
                                    rsi=None, cfg=_cfg(eod=True))
        self.assertIn("EOD close", reason)

    def test_overnight_carry_exits(self):
        reason, _ = _wd_decide_exit(_trade(), current_price=100.5,
                                    rsi=None, cfg=_cfg(overnight=True))
        self.assertIn("Overnight carry", reason)


class TestRsiSafety(unittest.TestCase):
    def test_does_not_fire_on_small_winner(self):
        # The old bug: RSI 80 at +1% would exit. It must NOT now (pnl < 3%).
        reason, _ = _wd_decide_exit(_trade(stop=98.0), current_price=101.0,
                                    rsi=80.0, cfg=_cfg())
        self.assertIsNone(reason)

    def test_does_not_fire_when_not_overbought(self):
        # RSI 65 (a normal working breakout) at +5% must not trigger.
        reason, _ = _wd_decide_exit(_trade(stop=98.0), current_price=105.0,
                                    rsi=65.0, cfg=_cfg())
        self.assertIsNone(reason)

    def test_fires_when_overbought_and_real_winner(self):
        reason, _ = _wd_decide_exit(_trade(stop=98.0, tp=120.0), current_price=104.0,
                                    rsi=80.0, cfg=_cfg())
        self.assertIsNotNone(reason)
        self.assertIn("RSI overbought", reason)


class TestProfitCaps(unittest.TestCase):
    def test_seven_percent_hard_cap(self):
        reason, _ = _wd_decide_exit(_trade(stop=98.0, tp=120.0), current_price=108.0,
                                    rsi=None, cfg=_cfg())
        self.assertIn("7% cap", reason)

    def test_take_profit_level(self):
        reason, _ = _wd_decide_exit(_trade(stop=98.0, tp=106.0), current_price=106.5,
                                    rsi=None, cfg=_cfg())
        self.assertIn("Take-profit", reason)


class TestConfigAndScorerWiring(unittest.TestCase):
    def test_new_defaults_present(self):
        for k in ("wd_hard_floor_pct", "wd_trail_arm_pct", "wd_trail_giveback_pct",
                  "wd_trail_tight_pct", "wd_trail_tight_giveback_pct",
                  "wd_rsi_exit_min", "wd_rsi_exit_min_pnl", "wd_min_candidate_score"):
            self.assertIn(k, WD_DEFAULTS)

    def test_tightened_entry_gates(self):
        self.assertEqual(wd._TB_MIN_REL_VOL, 1.35)
        self.assertEqual(wd._TB_MIN_INTRA_RANGE, 1.0)


if __name__ == "__main__":
    unittest.main()
