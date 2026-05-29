"""Tests for the combined money-flow read (equity CMF/MFI fused with options flow)
in analysis_engine.compute_money_flow_read / _combine_flow."""
import os
import sys
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis_engine as ae  # noqa: E402


def _opt(sentiment, pc=1.0, net=0):
    return {"sentiment": sentiment, "pc_ratio": pc, "net_premium": net,
            "call_value": 0, "put_value": 0}


class TestCombineFlow(unittest.TestCase):
    def test_no_options_passthrough(self):
        sig, label, note = ae._combine_flow("IN", None)
        self.assertEqual(sig, "IN")
        self.assertIn("no options", note.lower())

    def test_accumulation_confirmed(self):
        sig, label, _ = ae._combine_flow("IN", _opt("BULLISH"))
        self.assertEqual(sig, "STRONG_IN")
        self.assertIn("Strong Money In", label)

    def test_distribution_confirmed(self):
        sig, label, _ = ae._combine_flow("OUT", _opt("BEARISH"))
        self.assertEqual(sig, "STRONG_OUT")

    def test_profit_taking_plus_bearish_options_is_top(self):
        sig, label, _ = ae._combine_flow("PROFIT_TAKING", _opt("BEARISH"))
        self.assertEqual(sig, "TOP")
        self.assertIn("Topping", label)

    def test_divergence_equity_in_options_out(self):
        sig, label, _ = ae._combine_flow("IN", _opt("BEARISH"))
        self.assertEqual(sig, "DIVERGENCE")

    def test_neutral_equity_options_led(self):
        self.assertEqual(ae._combine_flow("NEUTRAL", _opt("BULLISH"))[0], "IN")
        self.assertEqual(ae._combine_flow(None, _opt("BEARISH"))[0], "OUT")


class TestComputeMoneyFlowRead(unittest.TestCase):
    def _df(self, n=30):
        closes = np.linspace(50, 80, n)
        return pd.DataFrame({"High": closes + 0.1, "Low": closes - 0.9,
                             "Close": closes, "Volume": [1000] * n})

    def test_crypto_skips_options(self):
        # is_crypto=True must not attempt an options fetch.
        with mock.patch("features.watchdog.options_flow._fetch_options_flow") as fof:
            res = ae.compute_money_flow_read("BTC-USD", self._df(), is_crypto=True)
            fof.assert_not_called()
        self.assertIsNone(res["options"])
        self.assertIn("equity", res)

    def test_equity_plus_options_fused(self):
        with mock.patch("features.watchdog.options_flow._fetch_options_flow",
                        return_value=_opt("BULLISH", pc=0.5, net=1_000_000)):
            res = ae.compute_money_flow_read("AAPL", self._df(), is_crypto=False)
        self.assertIsNotNone(res["options"])
        self.assertEqual(res["options"]["sentiment"], "BULLISH")
        self.assertIn(res["signal"], ("STRONG_IN", "IN", "DIVERGENCE", "NEUTRAL", "OUT"))

    def test_options_failure_degrades_to_equity(self):
        with mock.patch("features.watchdog.options_flow._fetch_options_flow",
                        side_effect=RuntimeError("chain throttled")):
            res = ae.compute_money_flow_read("AAPL", self._df(), is_crypto=False)
        self.assertIsNone(res["options"])
        self.assertTrue(res["label"])  # still produces a read


if __name__ == "__main__":
    unittest.main()
