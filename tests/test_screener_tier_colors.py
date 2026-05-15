"""Tests for the Screener verdict-tier color mapping (May 2026 user request).

User asked: "use different colors for Screener". The screener previously
collapsed every verdict into green (opportunity) or orange (everything else).
This test locks in:
  1. JS exports a _screenerVerdictTier helper + _SCREENER_TIER_COLOR map.
  2. Cards get a tier-* class so CSS can color the left border distinctly.
  3. CSS defines all five tier classes with their accent colors.

We can't easily execute the JS here, so we assert on source content — a
contract check that the function/class names and the five tiers all exist.

Run: docker compose exec app python -m pytest tests/test_screener_tier_colors.py -v
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = Path(__file__).resolve().parent.parent
SCREENER_JS = (REPO / "static" / "js" / "features" / "screener" / "screener.js").read_text()
SCREENER_CSS = (REPO / "static" / "css" / "features" / "screener" / "screener.css").read_text()


class TestVerdictTierHelper(unittest.TestCase):
    """JS exports the tier helper + color map."""

    def test_tier_helper_defined(self):
        self.assertIn("function _screenerVerdictTier", SCREENER_JS)

    def test_tier_color_map_defined(self):
        self.assertIn("_SCREENER_TIER_COLOR", SCREENER_JS)

    def test_all_five_tiers_in_color_map(self):
        for tier in ("strong", "momentum", "watch", "cautious", "avoid"):
            # Each key must appear in the color map
            self.assertIn(f"{tier}:", SCREENER_JS,
                f"Missing tier key '{tier}' in _SCREENER_TIER_COLOR")

    def test_card_renders_with_tier_class(self):
        # buildScreenerCard adds `tier-${tier}` to the card's class list
        self.assertIn('class="screener-card ${type} tier-${tier}"', SCREENER_JS)

    def test_trending_table_uses_tier_helper(self):
        # The trending tracker table should use the tier helper, not the old
        # multi-includes() branch
        self.assertIn("_screenerVerdictTier(t.latest_verdict)", SCREENER_JS)

    def test_scanner_rows_use_tier_helper(self):
        self.assertIn("_screenerVerdictTier(r.verdict)", SCREENER_JS)


class TestVerdictTierCSS(unittest.TestCase):
    """CSS defines all five tier classes with the right accent variables."""

    def test_all_five_tier_classes(self):
        for tier in ("tier-strong", "tier-momentum", "tier-watch",
                     "tier-cautious", "tier-avoid"):
            self.assertIn(f".screener-card.{tier}", SCREENER_CSS,
                f"CSS missing .screener-card.{tier}")

    def test_tier_strong_uses_green(self):
        # The strong tier must use --accent-green (high conviction long)
        idx = SCREENER_CSS.index(".screener-card.tier-strong")
        block = SCREENER_CSS[idx: idx + 200]
        self.assertIn("--accent-green", block)

    def test_tier_avoid_uses_red(self):
        idx = SCREENER_CSS.index(".screener-card.tier-avoid")
        block = SCREENER_CSS[idx: idx + 200]
        self.assertIn("--accent-red", block)

    def test_tier_momentum_uses_purple(self):
        # Momentum/Recovery setups use purple so they're visually distinct
        # from both strong (green) and watch (blue)
        idx = SCREENER_CSS.index(".screener-card.tier-momentum")
        block = SCREENER_CSS[idx: idx + 200]
        self.assertIn("--accent-purple", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
