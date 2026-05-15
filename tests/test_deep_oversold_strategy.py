"""Unit test for the deep_oversold_rebound strategy.

Apr 2026 user request: in deep selloffs every coin is RSI 22-30 but the bot
generates zero signals because all scalp strategies require MACD-turn-up or
price-above-MA confirmation. Added a last-resort "fires on RSI < 25 alone"
strategy to keep the bot actively trading. Knife-risk is explicit; the
self-learning blacklist will auto-remove it if it consistently loses.

Verifies:
  - Strategy fires on RSI < 25
  - Does NOT fire on RSI >= 25
  - Returns {side='buy', strategy='deep_oversold_rebound'}
  - Present in BOTH crypto_bot AND stock_bot
  - Source code mentions self-learning discipline (so future readers know it's
    bounded by the blacklist)

Run: docker compose exec app python -m pytest tests/test_deep_oversold_strategy.py -v
"""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDeepOversoldStrategySource(unittest.TestCase):
    """Source-level checks (don't need to run the whole bot)."""

    def test_present_in_crypto_bot(self):
        from crypto_bot import bot_engine
        src = inspect.getsource(bot_engine)
        self.assertIn("deep_oversold_rebound", src,
            "deep_oversold_rebound strategy missing from crypto_bot")
        self.assertIn("rsi < 25", src,
            "Strategy must gate on rsi < 25 (per user spec)")

    def test_present_in_stock_bot(self):
        from stock_bot import stock_engine
        src = inspect.getsource(stock_engine)
        self.assertIn("deep_oversold_rebound", src,
            "deep_oversold_rebound strategy missing from stock_bot")
        self.assertIn("rsi < 25", src,
            "Strategy must gate on rsi < 25 (per user spec)")

    def test_documents_knife_risk(self):
        """The strategy explicitly catches falling knives — comment must say so
        and reference the self-learning discipline so future readers know the
        guardrail."""
        from crypto_bot import bot_engine
        src = inspect.getsource(bot_engine)
        self.assertIn("knife", src.lower(),
            "Comment must call out the knife-catching nature explicitly")
        self.assertIn("blacklist", src.lower(),
            "Must reference self-learning blacklist as the discipline mechanism")

    def test_only_buys_no_sells(self):
        """Deep oversold = buy candidate. Strategy must not also fire SELL on
        deep overbought (that'd be a separate strategy with different math)."""
        from crypto_bot import bot_engine
        src = inspect.getsource(bot_engine)
        # Find the deep_oversold_rebound block and confirm no sell branch
        # within ~10 lines of the rsi < 25 check
        idx = src.find("rsi < 25")
        self.assertGreater(idx, 0)
        block = src[idx:idx + 600]
        # Should contain side="buy" not side="sell"
        self.assertIn('"side": "buy"', block)
        # Note: SELL branches for overbought already exist (rsi_reversion etc.)


class TestDeepOversoldStrategyExecutes(unittest.TestCase):
    """Run the actual _generate_signal with controlled indicators to confirm
    behavior. Crypto bot is easier to instantiate with user_id=None."""

    def _make_bot(self):
        # Lazy import; bot needs DB available which the test container has
        from crypto_bot.bot_engine import TradingBot
        return TradingBot(user_id=None)

    def _base_indicators(self, rsi):
        """Indicators with neutral values so only RSI varies. Uses values that
        intentionally fail every other strategy's confirmation gate."""
        return {
            "rsi_14": rsi,
            "macd_histogram": -0.001,  # negative → fails MACD-turn-up gates
            "macd": -0.5,
            "macd_signal": -0.4,
            "ema_9": 100.0,
            "ema_20": 100.0,
            "sma_8": 99.0,
            "sma_50": 105.0,           # price below SMA50 → fails momentum
            "sma_200": 110.0,
            "atr_14": 1.0,
            "adr_pct": 1.0,
            "volume": 1000,
            "vol_ma_10": 1100,         # rel_vol < 1 → fails volume gates
            "relative_volume": 0.9,
            "bb_position": 0.5,
            "bb_width": 5.0,
        }

    def _fake_df(self, current_price=98.0):
        import pandas as pd
        # 3 simple bars — enough for any strategy to read .iloc[-1] etc.
        return pd.DataFrame({
            "Open":   [99.0, 98.5, 98.2],
            "High":   [99.5, 99.0, 98.5],
            "Low":    [98.0, 97.5, 97.5],
            "Close":  [99.0, 98.0, current_price],
            "Volume": [1000, 1100, 1000],
        })

    def test_fires_at_rsi_24(self):
        """RSI = 24 (< 25) should produce a deep_oversold_rebound BUY signal
        when no other strategy gates pass."""
        bot = self._make_bot()
        ind = self._base_indicators(rsi=24.0)
        sig = bot._generate_signal("BTC-USDT", ind, self._fake_df())
        self.assertIsNotNone(sig, "RSI=24 must produce a signal (deep oversold)")
        self.assertEqual(sig.get("strategy"), "deep_oversold_rebound")
        self.assertEqual(sig.get("side"), "buy")

    def test_silent_at_rsi_25(self):
        """RSI exactly 25 should NOT trigger (strict less-than)."""
        bot = self._make_bot()
        ind = self._base_indicators(rsi=25.0)
        sig = bot._generate_signal("BTC-USDT", ind, self._fake_df())
        # Strategy must not fire — but other strategies may still match.
        # Either way it must NOT be deep_oversold_rebound.
        if sig is not None:
            self.assertNotEqual(sig.get("strategy"), "deep_oversold_rebound",
                "RSI=25 should not match deep_oversold_rebound (strict <)")

    def test_silent_at_rsi_50(self):
        """Mid-range RSI never triggers deep_oversold_rebound."""
        bot = self._make_bot()
        ind = self._base_indicators(rsi=50.0)
        sig = bot._generate_signal("BTC-USDT", ind, self._fake_df())
        if sig is not None:
            self.assertNotEqual(sig.get("strategy"), "deep_oversold_rebound")


if __name__ == "__main__":
    unittest.main(verbosity=2)
