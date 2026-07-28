"""Tests for the tabbed admin panel layout (Usage · Users · Models · System)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class TestAdminTemplate(unittest.TestCase):
    def setUp(self):
        self.html = _read("templates/index.html")

    def test_four_subtab_buttons(self):
        for tab in ("usage", "users", "models", "system"):
            self.assertIn('data-atab="%s"' % tab, self.html,
                          "missing sub-tab button for %s" % tab)

    def test_four_panels(self):
        for tab in ("usage", "users", "models", "system"):
            self.assertIn('data-apanel="%s"' % tab, self.html,
                          "missing panel for %s" % tab)
        self.assertEqual(self.html.count("admin-tabpanel"), 4)

    def test_non_usage_panels_hidden_by_default(self):
        # usage panel is visible; the other three start display:none
        self.assertIn('data-apanel="users" style="display:none;"', self.html)
        self.assertIn('data-apanel="models" style="display:none;"', self.html)
        self.assertIn('data-apanel="system" style="display:none;"', self.html)

    def test_cards_not_duplicated(self):
        # Each card must appear exactly once after the reorder.
        for marker in ("admin-usage-report-card", "admin-bot-defaults-card",
                       "admin-ai-usage-card", "admin-config-card",
                       "admin-platform-card", "admin-ollama-config-card"):
            self.assertEqual(self.html.count('id="%s"' % marker), 1,
                             "%s should appear exactly once" % marker)

    def test_usage_tab_holds_usage_cards(self):
        # The Usage panel opens, then both usage cards appear before the Users panel.
        usage_start = self.html.index('data-apanel="usage"')
        users_start = self.html.index('data-apanel="users"')
        segment = self.html[usage_start:users_start]
        self.assertIn('id="admin-ai-usage-card"', segment)
        self.assertIn('id="admin-usage-report-card"', segment)

    def test_token_zero_state_banner_present(self):
        self.assertIn('id="ai-usage-banner"', self.html)


class TestAdminJs(unittest.TestCase):
    def setUp(self):
        self.js = _read("static/js/features/admin/admin.js")

    def test_switch_tab_function_exists(self):
        self.assertIn("function switchAdminTab(", self.js)

    def test_zero_token_banner_logic(self):
        # The loader explains a calls>0 / tokens==0 window instead of silently showing 0.
        self.assertIn("ai-usage-banner", self.js)
        self.assertIn("total_tokens", self.js)


if __name__ == "__main__":
    unittest.main()
