"""Tests for the ThunderBot UI (formerly TraderBot, May 2026 user request).

User asked for three things:
  1. Options Flow section collapsible behind a toggle button (was always-on,
     pushing trading content below the fold).
  2. Trading settings/config visible on the page (user reported "I don't see
     trading settings and config" — the FUNDS-only strip wasn't enough).
  3. Rename "Watchdog" → "ThunderBot" in user-facing labels (DB keys, routes,
     and asset_type='watchdog' stay unchanged for data continuity).

Run: docker compose exec app python -m pytest tests/test_traderbot_ui.py -v
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


REPO = Path(__file__).resolve().parent.parent
INDEX_HTML = (REPO / "templates" / "index.html").read_text()
WATCHDOG_JS = (REPO / "static" / "js" / "features" / "watchdog" / "watchdog.js").read_text()
ADMIN_JS = (REPO / "static" / "js" / "features" / "admin" / "admin.js").read_text()
CORE_JS = (REPO / "static" / "js" / "core.js").read_text()


class TestRenameToThunderBot(unittest.TestCase):
    """User-facing labels say 'ThunderBot'; routes/keys still say 'watchdog'."""

    def test_nav_tab_label_is_thunderbot(self):
        # The nav tab still routes via data-tab="watchdog" but the label is ThunderBot
        self.assertIn('data-tab="watchdog"', INDEX_HTML)
        self.assertIn(">ThunderBot<", INDEX_HTML)

    def test_loading_text_is_thunderbot(self):
        self.assertIn("Loading ThunderBot", INDEX_HTML)

    def test_admin_button_label_is_thunderbot(self):
        self.assertIn(">ThunderBot<", ADMIN_JS)

    def test_invite_modal_label_is_thunderbot(self):
        # The invite checkbox value stays 'watchdog' (server-side contract);
        # only the visible label changes.
        self.assertIn('value="watchdog"> ThunderBot', INDEX_HTML)

    def test_user_guide_title_is_thunderbot(self):
        self.assertIn("ThunderBot — User Guide", CORE_JS)

    def test_routes_unchanged(self):
        """DB keys + API routes must NOT be renamed — would break existing data."""
        # If these break, someone tried to rename the wd_ prefix or /api/watchdog/
        # path. Don't.
        self.assertIn("/api/watchdog/auto/status", WATCHDOG_JS)
        self.assertIn("/api/watchdog/auto/config", WATCHDOG_JS)

    def test_db_keys_unchanged(self):
        """The wd_* config keys and asset_type='watchdog' stay put."""
        from features.watchdog.engine import WD_DEFAULTS
        # Spot-check a few keys
        self.assertIn("wd_max_position_pct", WD_DEFAULTS)
        self.assertIn("wd_max_total_exposure_pct", WD_DEFAULTS)
        self.assertIn("wd_kill_switch", WD_DEFAULTS)


class TestOptionsFlowCollapsible(unittest.TestCase):
    """The options flow section is hidden by default and revealed by a toggle."""

    def test_toggle_button_present(self):
        self.assertIn("wd-options-flow-toggle", WATCHDOG_JS)
        self.assertIn("wdToggleOptionsFlow", WATCHDOG_JS)

    def test_panel_hidden_by_default(self):
        # Default state flag — initial render uses _wdOptionsFlowVisible=false
        self.assertIn("_wdOptionsFlowVisible = false", WATCHDOG_JS)

    def test_toggle_function_flips_visibility(self):
        # Sanity: toggle should actually toggle the panel display
        self.assertIn("wd-options-flow-panel", WATCHDOG_JS)
        self.assertIn("_wdOptionsFlowVisible = !_wdOptionsFlowVisible", WATCHDOG_JS)


class TestSettingsPanelVisible(unittest.TestCase):
    """All trading knobs render in a visible Settings panel — not hidden in a modal."""

    def test_settings_label_present(self):
        self.assertIn("Trading Settings", WATCHDOG_JS,
            "User must see a clearly labeled Trading Settings section")

    def test_all_knobs_have_inputs(self):
        # Each of the seven trading knobs must have an input element id
        for input_id in [
            "wd-cfg-max-pct",          # per-position %
            "wd-cfg-max-exposure",     # max total exposure %
            "wd-cfg-max-positions",    # max open positions
            "wd-cfg-loss-limit",       # daily loss limit
            "wd-cfg-min-conf",         # min signal confidence
            "wd-cfg-scan-interval",    # scan interval
            "wd-mode-select",          # paper/live
        ]:
            self.assertIn(input_id, WATCHDOG_JS,
                f"Settings panel missing input #{input_id}")

    def test_save_handler_posts_all_keys(self):
        """wdSaveSettings must send every config key the server allowlist accepts."""
        self.assertIn("wdSaveSettings", WATCHDOG_JS)
        for key in [
            "wd_max_position_pct",
            "wd_max_total_exposure_pct",
            "wd_max_positions",
            "wd_daily_loss_limit",
            "wd_min_confidence",
            "wd_scan_interval",
        ]:
            self.assertIn(key, WATCHDOG_JS,
                f"Save handler must include {key} in payload")


if __name__ == "__main__":
    unittest.main(verbosity=2)
