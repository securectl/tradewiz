"""Smoke tests for the bot config popup modals.

Per CLAUDE.md rule #11 — every new feature ships with at least a smoke test.
The modal markup itself is template-driven and the open/close logic is
client-side, so these tests verify the *contract*:
  - Each bot's index.html ships the expected modal+backdrop pair
  - Each bot's toolbar has a `openBotConfig('<slug>')` trigger
  - core.js exposes the open/close helpers
  - The Claude Bot JS still wraps its config in a modal renderer

Run: docker compose exec app python -m pytest tests/test_bot_config_modals.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INDEX_PATH = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
CORE_JS_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "js", "core.js")
CB_JS_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "js", "features", "claude_bot", "claude_bot.js")


class TestModalContract(unittest.TestCase):
    """Lock the modal IDs and trigger names so accidental edits don't break
    the open/close wiring."""

    @classmethod
    def setUpClass(cls):
        with open(INDEX_PATH) as f:
            cls.index = f.read()
        with open(CORE_JS_PATH) as f:
            cls.core_js = f.read()
        with open(CB_JS_PATH) as f:
            cls.cb_js = f.read()

    def test_open_close_helpers_exist(self):
        self.assertIn("function openBotConfig(", self.core_js,
            "core.js must expose openBotConfig() helper")
        self.assertIn("function closeBotConfig(", self.core_js,
            "core.js must expose closeBotConfig() helper")

    def test_escape_key_closes_modals(self):
        """Pressing Esc should attempt to close every known modal slug."""
        self.assertIn("e.key === 'Escape'", self.core_js)
        # Each known slug must appear in the Escape handler list
        for slug in ("cb", "crypto-bot", "stock-bot"):
            self.assertIn(slug, self.core_js)

    def test_crypto_bot_modal_present(self):
        self.assertIn('id="crypto-bot-config-modal"', self.index)
        self.assertIn('id="crypto-bot-config-backdrop"', self.index)
        self.assertIn("openBotConfig('crypto-bot')", self.index,
            "Crypto Bot toolbar must have a ⚙ Config trigger")

    def test_stock_bot_modal_present(self):
        self.assertIn('id="stock-bot-config-modal"', self.index)
        self.assertIn('id="stock-bot-config-backdrop"', self.index)
        self.assertIn("openBotConfig('stock-bot')", self.index,
            "Stock Bot toolbar must have a ⚙ Config trigger")

    def test_claude_bot_modal_renderer(self):
        self.assertIn("function renderCbConfigModal", self.cb_js,
            "Claude Bot must render its config inside the modal wrapper")
        self.assertIn('id="cb-config-modal"', self.cb_js)
        self.assertIn('id="cb-config-backdrop"', self.cb_js)
        self.assertIn("openBotConfig('cb')", self.cb_js,
            "Claude Bot header must have ⚙ Config button")

    def test_existing_inputs_still_inside_crypto_modal(self):
        """Sanity: the crypto bot's settings IDs (e.g. bot-cfg-take-profit)
        still appear in the file — wrapping them in a modal must not have
        accidentally deleted any field."""
        # Pick a few well-known crypto bot config field IDs from the original layout
        for fid in ("bot-cfg-platform", "bot-cfg-trade-mode", "bot-config-form"):
            self.assertIn(fid, self.index,
                f"crypto bot field {fid!r} must still be in the template after modal wrap")

    def test_existing_inputs_still_inside_stock_modal(self):
        for fid in ("sbot-config-form", "sbot-cfg-daily-goal", "sbot-cfg-max-pct"):
            self.assertIn(fid, self.index,
                f"stock bot field {fid!r} must still be in the template after modal wrap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
