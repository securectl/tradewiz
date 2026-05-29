"""Shared quote-cache migration: the header tiles and both bots' market sensor
now route SPY/QQQ/VIX/BTC/ETH through shared/yf_fetch, so repeated/concurrent
fetches for the same (symbol, period, interval) reuse one Yahoo request."""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import yf_fetch  # noqa: E402


class _CountingTicker:
    """Stub yf.Ticker that counts how many times Yahoo would actually be hit."""
    calls = {}

    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, period=None, interval=None):
        key = (self.symbol, period, interval)
        _CountingTicker.calls[key] = _CountingTicker.calls.get(key, 0) + 1
        # 40 hourly bars — enough for the -7/-8/-24 index math in both callers.
        n = 40
        return pd.DataFrame({
            "Open": [100.0] * n, "High": [101.0] * n,
            "Low": [99.0] * n, "Close": [100.0 + i * 0.1 for i in range(n)],
            "Volume": [1000] * n,
        })


class TestQuoteCache(unittest.TestCase):
    def setUp(self):
        yf_fetch._hist_cache.clear()
        _CountingTicker.calls = {}

    def test_repeated_bot_cycles_reuse_one_fetch(self):
        import market_sensor
        with patch.object(yf_fetch, "ticker", _CountingTicker):
            first = market_sensor._fetch_stock_indicators()
            second = market_sensor._fetch_stock_indicators()
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        # SPY, QQQ (5d/1h) + VIX (5d/1d) = 3 distinct keys, each fetched ONCE
        # despite two scan cycles — the second cycle is served from cache.
        self.assertEqual(_CountingTicker.calls[("SPY", "5d", "1h")], 1)
        self.assertEqual(_CountingTicker.calls[("QQQ", "5d", "1h")], 1)
        self.assertEqual(_CountingTicker.calls[("^VIX", "5d", "1d")], 1)

    def test_tile_and_sensor_share_spy_key(self):
        import market_sensor
        with patch.object(yf_fetch, "ticker", _CountingTicker):
            market_sensor._fetch_stock_indicators()          # bot populates SPY 5d/1h
            # The header tile (app.py market_pulse) reads the same key — no new hit.
            tile = yf_fetch.get_history("SPY", period="5d", interval="1h")
        self.assertFalse(tile.empty)
        self.assertEqual(_CountingTicker.calls[("SPY", "5d", "1h")], 1)

    def test_crypto_sensor_uses_cache(self):
        import market_sensor
        with patch.object(yf_fetch, "ticker", _CountingTicker):
            market_sensor._fetch_crypto_indicators()
            market_sensor._fetch_crypto_indicators()
        self.assertEqual(_CountingTicker.calls[("BTC-USD", "5d", "1h")], 1)
        self.assertEqual(_CountingTicker.calls[("ETH-USD", "5d", "1h")], 1)

    def test_sensor_survives_rate_limit(self):
        import market_sensor

        class _RL:
            def __init__(self, symbol):
                pass

            def history(self, period=None, interval=None):
                raise Exception("Too Many Requests. Rate limited.")

        # Nothing cached, rate-limited, and no FMP fallback → get_history raises
        # RateLimited, which the sensor swallows per-symbol and returns None
        # (the rate limit never propagates up to break the bot scan loop).
        with patch.object(yf_fetch, "ticker", _RL), \
             patch.object(yf_fetch, "_fmp_history", return_value=None), \
             patch.object(yf_fetch.time, "sleep", lambda *_: None):
            self.assertIsNone(market_sensor._fetch_stock_indicators())


if __name__ == "__main__":
    unittest.main()
