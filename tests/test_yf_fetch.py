"""Tests for the resilient yfinance fetch layer (cache + retry + RateLimited)."""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import yf_fetch  # noqa: E402
from shared.yf_fetch import RateLimited, is_rate_limit_error  # noqa: E402


class _Hist:
    def __init__(self, df=None, exc=None):
        self._df, self._exc = df, exc
    def history(self, period=None, interval=None):
        if self._exc:
            raise self._exc
        return self._df


class TestYfFetch(unittest.TestCase):
    def setUp(self):
        yf_fetch._hist_cache.clear()

    def test_is_rate_limit_error(self):
        self.assertTrue(is_rate_limit_error(Exception("Too Many Requests. Rate limited.")))
        self.assertTrue(is_rate_limit_error(Exception("HTTP 429")))
        self.assertFalse(is_rate_limit_error(Exception("connection reset")))

    def test_caches_and_serves_stale(self):
        df = pd.DataFrame({"Close": [1, 2, 3]})
        with patch.object(yf_fetch, "ticker", return_value=_Hist(df=df)):
            out = yf_fetch.get_history("AAPL")
        self.assertEqual(len(out), 3)
        # Even if Yahoo now rate-limits, a within-TTL request serves cache.
        with patch.object(yf_fetch, "ticker", return_value=_Hist(exc=Exception("Too Many Requests"))):
            out2 = yf_fetch.get_history("AAPL")
        self.assertEqual(len(out2), 3)

    def test_rate_limited_without_cache_raises(self):
        # Patch the FMP fallback to None so the test stays offline.
        with patch.object(yf_fetch, "_fmp_history", return_value=None), \
             patch.object(yf_fetch, "ticker", return_value=_Hist(exc=Exception("Too Many Requests"))):
            with self.assertRaises(RateLimited):
                yf_fetch.get_history("ZZZZ", retries=0)

    def test_non_ratelimit_error_propagates(self):
        with patch.object(yf_fetch, "ticker", return_value=_Hist(exc=KeyError("boom"))):
            with self.assertRaises(KeyError):
                yf_fetch.get_history("AAPL", retries=0)

    def test_empty_result_is_not_ratelimit(self):
        # Empty WITHOUT an exception = bad symbol → return empty (caller 404s),
        # NOT RateLimited (fallback patched off).
        with patch.object(yf_fetch, "_fmp_history", return_value=None), \
             patch.object(yf_fetch, "ticker", return_value=_Hist(df=pd.DataFrame())):
            out = yf_fetch.get_history("ZZZZ", retries=0)
        self.assertTrue(out is None or out.empty)

    def test_fmp_fallback_served_on_rate_limit(self):
        # When Yahoo rate-limits and the FMP fallback returns data, serve it.
        fb = pd.DataFrame({"Open": [1], "High": [1], "Low": [1], "Close": [9], "Volume": [100]})
        with patch.object(yf_fetch, "_fmp_history", return_value=fb), \
             patch.object(yf_fetch, "ticker", return_value=_Hist(exc=Exception("Too Many Requests"))):
            out = yf_fetch.get_history("AAPL", retries=0)
        self.assertEqual(len(out), 1)
        self.assertEqual(out["Close"].iloc[-1], 9)


if __name__ == "__main__":
    unittest.main()
