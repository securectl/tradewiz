"""Tests for the Alpaca daily-bars money-flow source and its screener wiring."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import alpaca_data  # noqa: E402


class TestAlpacaData(unittest.TestCase):
    def setUp(self):
        alpaca_data._bars_cache.clear()

    def test_symbol_filter_skips_crypto_index_fx(self):
        self.assertTrue(alpaca_data._is_alpaca_symbol("AAPL"))
        self.assertFalse(alpaca_data._is_alpaca_symbol("BTC-USD"))
        self.assertFalse(alpaca_data._is_alpaca_symbol("^VIX"))
        self.assertFalse(alpaca_data._is_alpaca_symbol("EURUSD=X"))
        self.assertFalse(alpaca_data._is_alpaca_symbol(""))

    def test_unconfigured_is_false(self):
        with mock.patch.object(alpaca_data, "_creds", return_value=(None, None)):
            self.assertFalse(alpaca_data.is_configured())

    def test_configured_when_both_keys_present(self):
        with mock.patch.object(alpaca_data, "_creds", return_value=("k", "s")):
            self.assertTrue(alpaca_data.is_configured())

    def test_get_daily_bars_empty_when_unconfigured(self):
        with mock.patch.object(alpaca_data, "_get_client", return_value=None):
            self.assertEqual(alpaca_data.get_daily_bars(["AAPL", "MSFT"]), {})

    def test_get_daily_bars_skips_non_equity_without_client(self):
        # All-crypto input never even needs a client.
        with mock.patch.object(alpaca_data, "_get_client") as gc:
            self.assertEqual(alpaca_data.get_daily_bars(["BTC-USD", "^VIX"]), {})
            gc.assert_not_called()

    def test_money_flow_map_empty_when_unconfigured(self):
        with mock.patch.object(alpaca_data, "get_daily_bars", return_value={}):
            self.assertEqual(alpaca_data.money_flow_map(["AAPL"]), {})


class TestScreenerAlpacaPass(unittest.TestCase):
    def test_noop_when_unconfigured(self):
        import screener
        cands = [{"ticker": "AAPL", "mf_signal": None}]
        with mock.patch("shared.alpaca_data.is_configured", return_value=False):
            screener._apply_alpaca_money_flow(cands)
        self.assertIsNone(cands[0]["mf_signal"])

    def test_overrides_candidate_from_alpaca(self):
        import screener
        cands = [{"ticker": "AAPL", "cmf": None, "mfi": None, "mf_signal": None},
                 {"ticker": "MSFT", "cmf": 0.01, "mfi": 50, "mf_signal": "NEUTRAL"}]
        alpaca_mf = {"AAPL": {"cmf": -0.2, "mfi": 30.0, "mf_signal": "OUT", "mf_label": "Money Out"}}
        with mock.patch("shared.alpaca_data.is_configured", return_value=True), \
             mock.patch("shared.alpaca_data.money_flow_map", return_value=alpaca_mf):
            screener._apply_alpaca_money_flow(cands)
        # AAPL filled from Alpaca; MSFT (absent from Alpaca result) keeps its value.
        self.assertEqual(cands[0]["mf_signal"], "OUT")
        self.assertEqual(cands[0]["cmf"], -0.2)
        self.assertEqual(cands[1]["mf_signal"], "NEUTRAL")

    def test_never_raises_on_error(self):
        import screener
        cands = [{"ticker": "AAPL"}]
        with mock.patch("shared.alpaca_data.is_configured", side_effect=RuntimeError("boom")):
            screener._apply_alpaca_money_flow(cands)  # must swallow


if __name__ == "__main__":
    unittest.main()
