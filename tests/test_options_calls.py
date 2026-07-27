"""Tests for the Option Calls feature (rising vs falling call volume)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRouteRegistered(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/options/calls", rules)

    def test_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/options/calls?symbol=AAPL")
        self.assertIn(resp.status_code, (401, 302))

    def test_missing_symbol_400(self):
        # 400 should win over auth only if route runs; login_required guards
        # first, so we just assert it's not a 500.
        from app import app
        resp = app.test_client().get("/api/options/calls")
        self.assertIn(resp.status_code, (400, 401, 302))


class TestClassify(unittest.TestCase):
    """The pure vol/OI classifier — no network."""

    def _calls(self):
        return [
            {"strike": 100, "expiry": "2026-06-19", "volume": 1000, "open_interest": 500, "last": 5.0},   # ratio 2.0 -> up
            {"strike": 95,  "expiry": "2026-06-19", "volume": 60,   "open_interest": 100,  "last": 8.0},  # ratio 0.6 -> up
            {"strike": 110, "expiry": "2026-06-19", "volume": 10,   "open_interest": 1000, "last": 1.0},  # ratio 0.01, OI>=50 -> down
            {"strike": 105, "expiry": "2026-06-19", "volume": 0,    "open_interest": 300,  "last": 2.0},  # no volume -> skipped
        ]

    def test_buckets_split_by_ratio(self):
        from features.options_calls.engine import _classify
        out = _classify("XYZ", 105.0, self._calls())
        inc_strikes = {c["strike"] for c in out["increasing"]}
        dec_strikes = {c["strike"] for c in out["decreasing"]}
        self.assertEqual(inc_strikes, {100.0, 95.0})
        self.assertEqual(dec_strikes, {110.0})

    def test_totals_skip_zero_volume(self):
        from features.options_calls.engine import _classify
        out = _classify("XYZ", 105.0, self._calls())
        # zero-volume contract excluded from totals/contracts
        self.assertEqual(out["totals"]["call_volume"], 1070)
        self.assertEqual(out["totals"]["contracts"], 3)

    def test_read_accumulating(self):
        from features.options_calls.engine import _classify
        out = _classify("XYZ", 105.0, self._calls())
        # agg ratio 1070/1600 = 0.67 -> ACCUMULATING
        self.assertEqual(out["read"], "ACCUMULATING")

    def test_moneyness(self):
        from features.options_calls.engine import _classify
        out = _classify("XYZ", 105.0, self._calls())
        by_strike = {c["strike"]: c["moneyness"] for c in out["increasing"]}
        self.assertEqual(by_strike[100.0], "ITM")  # strike below spot
        # 110 is OTM (in decreasing bucket)
        dec = {c["strike"]: c["moneyness"] for c in out["decreasing"]}
        self.assertEqual(dec[110.0], "OTM")

    def test_top_contract_is_highest_volume(self):
        from features.options_calls.engine import _classify
        out = _classify("XYZ", 105.0, self._calls())
        # strike 100 has the highest volume (1000)
        self.assertIsNotNone(out["top_contract"])
        self.assertEqual(out["top_contract"]["strike"], 100.0)
        self.assertEqual(out["top_contract"]["volume"], 1000)
        # and it's flagged in the increasing bucket so the row highlights
        flagged = [c for c in out["increasing"] if c.get("top")]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["strike"], 100.0)

    def test_no_top_contract_when_empty(self):
        from features.options_calls.engine import _classify
        out = _classify("XYZ", 105.0, [])
        self.assertIsNone(out["top_contract"])


class TestGetCallActivity(unittest.TestCase):
    def setUp(self):
        from features.options_calls import engine
        engine._CACHE.clear()

    def test_falls_back_to_yfinance_when_webull_empty(self):
        from features.options_calls import engine
        sample = [{"strike": 50, "expiry": "2026-07-17", "volume": 200, "open_interest": 100, "last": 1.0}]
        with mock.patch("shared.webull_options.fetch_call_chain", return_value=None), \
             mock.patch("shared.alpaca_options.fetch_call_chain", return_value=None), \
             mock.patch.object(engine, "_fetch_calls_yf", return_value=(50.0, sample)), \
             mock.patch.object(engine, "_record_snapshot"), \
             mock.patch.object(engine, "get_call_history", return_value=[]):
            out = engine.get_call_activity("ABC")
        self.assertEqual(out["source"], "yfinance")
        self.assertEqual(out["symbol"], "ABC")
        self.assertEqual(out["totals"]["call_volume"], 200)
        self.assertFalse(out["validation"]["multi_source"])

    def test_uses_webull_when_available(self):
        from features.options_calls import engine
        wb = {"source": "webull", "price": 50.0,
              "calls": [{"strike": 50, "expiry": "2026-07-17", "volume": 300, "open_interest": 100, "last": 1.0}]}
        with mock.patch("shared.webull_options.fetch_call_chain", return_value=wb), \
             mock.patch("shared.alpaca_options.fetch_call_chain", return_value=None), \
             mock.patch.object(engine, "_fetch_calls_yf", return_value=(0.0, [])), \
             mock.patch.object(engine, "_record_snapshot"), \
             mock.patch.object(engine, "get_call_history", return_value=[]):
            out = engine.get_call_activity("ABC")
        self.assertEqual(out["source"], "webull")
        self.assertEqual(out["totals"]["call_volume"], 300)

    def test_cross_validates_webull_and_yahoo(self):
        from features.options_calls import engine
        wb = {"source": "webull", "price": 50.0,
              "calls": [{"strike": 50, "expiry": "2026-07-17", "volume": 300, "open_interest": 100, "last": 1.0}]}
        yf = [{"strike": 50, "expiry": "2026-07-17", "volume": 310, "open_interest": 100, "last": 1.0}]
        with mock.patch("shared.webull_options.fetch_call_chain", return_value=wb), \
             mock.patch("shared.alpaca_options.fetch_call_chain", return_value=None), \
             mock.patch.object(engine, "_fetch_calls_yf", return_value=(50.0, yf)), \
             mock.patch.object(engine, "_record_snapshot"), \
             mock.patch.object(engine, "get_call_history", return_value=[]):
            out = engine.get_call_activity("ABC")
        v = out["validation"]
        self.assertTrue(v["multi_source"])
        self.assertEqual(set(v["sources"]), {"webull", "yfinance"})
        self.assertEqual(v["primary"], "webull")
        # 300 vs 310 agree (within 25% / >5 abs but <3.3%)
        self.assertEqual(v["volume_agreement_pct"], 100.0)

    def test_records_snapshot_and_attaches_history(self):
        from features.options_calls import engine
        sample = [{"strike": 50, "expiry": "2026-07-17", "volume": 200, "open_interest": 100, "last": 1.0}]
        hist = [{"date": "2026-06-08", "call_volume": 150, "vol_oi_ratio": 1.5, "read": "ACCUMULATING"}]
        with mock.patch("shared.webull_options.fetch_call_chain", return_value=None), \
             mock.patch("shared.alpaca_options.fetch_call_chain", return_value=None), \
             mock.patch.object(engine, "_fetch_calls_yf", return_value=(50.0, sample)), \
             mock.patch.object(engine, "_record_snapshot") as rec, \
             mock.patch.object(engine, "get_call_history", return_value=hist):
            out = engine.get_call_activity("ABC")
        rec.assert_called_once()
        self.assertEqual(out["history"], hist)

    def test_drops_alpaca_when_all_zero_volume_and_oi(self):
        """Regression for APLD: Alpaca's free feed returns the full chain with
        volume=0/OI=0 on every contract. It must not win source-priority and
        blank the view — yfinance (real data) should become the primary."""
        from features.options_calls import engine
        alpaca_empty = {"source": "alpaca", "price": 41.0,
                        "calls": [{"strike": 75, "expiry": "2026-09-18",
                                   "volume": 0, "open_interest": 0, "last": 2.35}] * 5}
        yf = [{"strike": 40, "expiry": "2026-06-12", "volume": 500, "open_interest": 100, "last": 1.0}]
        with mock.patch("shared.webull_options.fetch_call_chain", return_value=None), \
             mock.patch("shared.alpaca_options.fetch_call_chain", return_value=alpaca_empty), \
             mock.patch.object(engine, "_fetch_calls_yf", return_value=(41.0, yf)), \
             mock.patch.object(engine, "_record_snapshot"), \
             mock.patch.object(engine, "get_call_history", return_value=[]):
            out = engine.get_call_activity("APLD")
        self.assertNotIn("error", out)
        self.assertEqual(out["source"], "yfinance")
        self.assertEqual(out["totals"]["call_volume"], 500)
        self.assertEqual(out["totals"]["contracts"], 1)
        # Alpaca contributed nothing, so this is effectively single-source.
        self.assertNotIn("alpaca", out["validation"]["sources"])

    def test_has_signal_helper(self):
        from features.options_calls import engine
        self.assertFalse(engine._has_signal([{"volume": 0, "open_interest": 0}]))
        self.assertFalse(engine._has_signal([]))
        self.assertTrue(engine._has_signal([{"volume": 0, "open_interest": 50}]))
        self.assertTrue(engine._has_signal([{"volume": 3, "open_interest": 0}]))

    def test_error_when_no_chain(self):
        from features.options_calls import engine
        with mock.patch("shared.webull_options.fetch_call_chain", return_value=None), \
             mock.patch("shared.alpaca_options.fetch_call_chain", return_value=None), \
             mock.patch.object(engine, "_fetch_calls_yf", return_value=(0.0, [])):
            out = engine.get_call_activity("NOPE")
        self.assertIn("error", out)

    def test_blank_symbol(self):
        from features.options_calls import engine
        self.assertIn("error", engine.get_call_activity("  "))


class TestDirection(unittest.TestCase):
    def test_buy_near_ask(self):
        from features.options_calls.engine import _direction
        side, conf = _direction(1.2, 1.0, 1.2)   # printed at the ask
        self.assertEqual(side, "buy")
        self.assertGreater(conf, 0.5)

    def test_sell_near_bid(self):
        from features.options_calls.engine import _direction
        side, conf = _direction(0.5, 0.5, 0.7)   # printed at the bid
        self.assertEqual(side, "sell")
        self.assertGreater(conf, 0.5)

    def test_mid_is_neutral(self):
        from features.options_calls.engine import _direction
        self.assertEqual(_direction(0.6, 0.5, 0.7)[0], "neutral")

    def test_missing_or_crossed_quote_neutral(self):
        from features.options_calls.engine import _direction
        self.assertEqual(_direction(1.0, 0, 0)[0], "neutral")
        self.assertEqual(_direction(1.0, 1.5, 1.0)[0], "neutral")  # bid > ask


class TestClassifyFlow(unittest.TestCase):
    def _payload(self):
        from features.options_calls.engine import classify_flow
        calls = [
            {"strike": 50, "expiry": "2026-07-17", "volume": 1000, "open_interest": 10, "last": 1.2, "bid": 1.0, "ask": 1.2},  # bought -> long call
            {"strike": 55, "expiry": "2026-07-17", "volume": 800, "open_interest": 10, "last": 0.5, "bid": 0.5, "ask": 0.7},   # sold -> naked call
        ]
        puts = [
            {"strike": 45, "expiry": "2026-07-17", "volume": 500, "open_interest": 10, "last": 1.0, "bid": 0.8, "ask": 1.0},   # bought -> long put
            {"strike": 48, "expiry": "2026-07-17", "volume": 900, "open_interest": 10, "last": 0.6, "bid": 0.6, "ask": 0.8},   # sold -> naked put
        ]
        return classify_flow("ABC", 50.0, calls, puts)

    def test_buckets_routed_correctly(self):
        d = self._payload()
        b = d["buckets"]
        self.assertEqual(b["long_calls"]["rows"][0]["strike"], 50)
        self.assertEqual(b["naked_calls"]["rows"][0]["strike"], 55)
        self.assertEqual(b["long_puts"]["rows"][0]["strike"], 45)
        self.assertEqual(b["naked_puts"]["rows"][0]["strike"], 48)

    def test_ranks_assigned(self):
        d = self._payload()
        self.assertEqual(d["buckets"]["long_calls"]["rows"][0]["rank"], 1)

    def test_top_n_cap(self):
        from features.options_calls.engine import classify_flow, FLOW_TOP
        calls = [{"strike": 50 + i, "expiry": "2026-07-17", "volume": 100 + i,
                  "open_interest": 10, "last": 1.2, "bid": 1.0, "ask": 1.2} for i in range(10)]
        d = classify_flow("ABC", 50.0, calls, [])
        self.assertEqual(len(d["buckets"]["long_calls"]["rows"]), FLOW_TOP)
        # ranked by volume desc -> the largest-volume contract is #1
        self.assertEqual(d["buckets"]["long_calls"]["rows"][0]["rank"], 1)
        self.assertEqual(d["buckets"]["long_calls"]["rows"][0]["volume"], 109)

    def test_lean_bullish(self):
        self.assertEqual(self._payload()["summary"]["lean"], "BULLISH")

    def test_neutral_contracts_excluded(self):
        from features.options_calls.engine import classify_flow
        calls = [{"strike": 50, "expiry": "2026-07-17", "volume": 1000,
                  "open_interest": 10, "last": 0.6, "bid": 0.5, "ask": 0.7}]  # mid -> neutral
        d = classify_flow("ABC", 50.0, calls, [])
        self.assertEqual(d["buckets"]["long_calls"]["rows"], [])
        self.assertEqual(d["buckets"]["naked_calls"]["rows"], [])


class TestGetOptionsFlow(unittest.TestCase):
    def setUp(self):
        from features.options_calls import engine
        engine._FLOW_CACHE.clear()

    def test_returns_buckets(self):
        from features.options_calls import engine
        calls = [{"strike": 50, "expiry": "2026-07-17", "volume": 1000,
                  "open_interest": 10, "last": 1.2, "bid": 1.0, "ask": 1.2}]
        with mock.patch.object(engine, "_fetch_chain_yf", return_value=(50.0, calls, [])):
            out = engine.get_options_flow("ABC")
        self.assertNotIn("error", out)
        self.assertIn("long_calls", out["buckets"])
        self.assertEqual(out["buckets"]["long_calls"]["rows"][0]["rank"], 1)

    def test_error_when_no_chain(self):
        from features.options_calls import engine
        with mock.patch.object(engine, "_fetch_chain_yf", return_value=(0.0, [], [])):
            out = engine.get_options_flow("NOPE")
        self.assertIn("error", out)


class TestFlowRoute(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/options/flow", rules)

    def test_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/options/flow?symbol=AAPL")
        self.assertIn(resp.status_code, (401, 403, 302))

    def test_requires_symbol(self):
        from app import app
        # Even unauthenticated, missing-symbol path returns 400 only after auth;
        # so just assert the route never 200s without a symbol via a direct call.
        from features.options_calls import routes as r
        with app.test_request_context("/api/options/flow"):
            resp, code = r.api_options_flow.__wrapped__()
        self.assertEqual(code, 400)


class TestSnapshotPersistence(unittest.TestCase):
    def _result(self):
        return {
            "symbol": "ABC", "price": 50.0, "read": "ACCUMULATING",
            "totals": {"call_volume": 200, "call_oi": 100, "vol_oi_ratio": 2.0,
                       "contracts": 1, "increasing_count": 1, "decreasing_count": 0},
            "top_contract": {"strike": 50.0, "expiry": "2026-07-17", "volume": 200},
        }

    def test_record_upserts_and_purges(self):
        from features.options_calls import engine
        calls = []
        with mock.patch("db.execute", side_effect=lambda sql, params: calls.append((sql, params))), \
             mock.patch("db.IS_POSTGRES", False):
            engine._record_snapshot(self._result(), "yfinance")
        # one upsert INSERT + one purge DELETE
        self.assertEqual(len(calls), 2)
        self.assertIn("INSERT INTO options_call_snapshots", calls[0][0])
        self.assertIn("ON CONFLICT", calls[0][0])
        self.assertIn("DELETE FROM options_call_snapshots", calls[1][0])
        # top_contract strike/volume carried into the row
        self.assertIn(50.0, calls[0][1])
        self.assertIn(200, calls[0][1])

    def test_record_never_raises(self):
        from features.options_calls import engine
        with mock.patch("db.execute", side_effect=RuntimeError("db down")):
            engine._record_snapshot(self._result(), "yfinance")  # must not raise

    def test_history_shape(self):
        from features.options_calls import engine
        rows = [{"snap_date": "2026-06-08", "price": 49.0, "call_volume": 150, "call_oi": 90,
                 "vol_oi_ratio": 1.67, "read": "ACCUMULATING", "top_strike": 50.0,
                 "top_expiry": "2026-07-17", "top_volume": 120}]
        with mock.patch("db.query", return_value=rows), mock.patch("db.IS_POSTGRES", False):
            out = engine.get_call_history("ABC")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["date"], "2026-06-08")
        self.assertEqual(out[0]["call_volume"], 150)
        self.assertEqual(out[0]["top_strike"], 50.0)


class TestSnapshotTable(unittest.TestCase):
    def test_table_created_in_migrations(self):
        # both dialect branches reference the table
        import inspect, migrations
        src = inspect.getsource(migrations)
        self.assertIn("options_call_snapshots", src)


class TestCrossValidation(unittest.TestCase):
    def test_agree_tolerance(self):
        from features.options_calls.engine import _agree
        self.assertTrue(_agree({"volume": 100}, {"volume": 110}, "volume"))   # 10% rel
        self.assertFalse(_agree({"volume": 100}, {"volume": 200}, "volume"))  # 100% rel
        self.assertTrue(_agree({"volume": 2}, {"volume": 4}, "volume"))       # <=5 abs
        self.assertIsNone(_agree({"volume": 0}, {"volume": 0}, "volume"))     # nothing to compare

    def test_cross_validate_summary_and_divergence(self):
        from features.options_calls.engine import _cross_validate
        sources = {
            "webull": {"price": 50, "calls": [
                {"strike": 50, "expiry": "2026-07-17", "volume": 300, "open_interest": 100},
                {"strike": 55, "expiry": "2026-07-17", "volume": 100, "open_interest": 50}]},
            "yfinance": {"price": 50, "calls": [
                {"strike": 50, "expiry": "2026-07-17", "volume": 305, "open_interest": 100},   # agrees
                {"strike": 55, "expiry": "2026-07-17", "volume": 900, "open_interest": 50}]},  # volume diverges
        }
        summary, per = _cross_validate("webull", sources)
        self.assertTrue(summary["multi_source"])
        self.assertEqual(summary["divergent_contracts"], 1)
        self.assertEqual(per[(55.0, "2026-07-17")]["divergent"], True)
        self.assertEqual(per[(50.0, "2026-07-17")]["divergent"], False)
        self.assertIn("webull", per[(50.0, "2026-07-17")]["sources"])
        self.assertIn("yfinance", per[(50.0, "2026-07-17")]["sources"])

    def test_single_source_note(self):
        from features.options_calls.engine import _cross_validate
        sources = {"yfinance": {"price": 50, "calls": [
            {"strike": 50, "expiry": "2026-07-17", "volume": 300, "open_interest": 100}]}}
        summary, _ = _cross_validate("yfinance", sources)
        self.assertFalse(summary["multi_source"])
        self.assertIn("unavailable", summary["note"])


class TestAlpacaOptionsSeam(unittest.TestCase):
    def test_occ_parse(self):
        from shared.alpaca_options import _parse_occ
        strike, expiry, is_call = _parse_occ("AAPL250117C00150000")
        self.assertEqual(strike, 150.0)
        self.assertEqual(expiry, "2025-01-17")
        self.assertTrue(is_call)
        # a put
        _, _, is_call_p = _parse_occ("AAPL250117P00150000")
        self.assertFalse(is_call_p)

    def test_unconfigured_returns_none(self):
        from shared import alpaca_options
        with mock.patch.object(alpaca_options, "_get_client", return_value=None):
            self.assertIsNone(alpaca_options.fetch_call_chain("AAPL"))


class TestWebullSeam(unittest.TestCase):
    def test_unavailable_without_creds(self):
        from shared import webull_options
        with mock.patch.dict(os.environ, {"WEBULL_APP_KEY": "", "WEBULL_APP_SECRET": ""}, clear=False):
            self.assertFalse(webull_options.is_webull_options_available())
            self.assertIsNone(webull_options.fetch_call_chain("AAPL"))


if __name__ == "__main__":
    unittest.main()
