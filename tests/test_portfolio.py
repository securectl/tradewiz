"""Tests for the Portfolio Advisor — CSV parsing, rule recommendations, gating."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIDELITY = """Account Number,Account Name,Symbol,Description,Quantity,Last Price,Current Value,Cost Basis Total
Z123,Individual,AAPL,APPLE INC,10,$339.00,"$3,390.00","$2,000.00"
Z123,Individual,NVDA,NVIDIA CORP,5,$198.00,$990.00,$500.00
Z123,Individual,SPAXX**,FIDELITY MONEY MARKET,1500,$1.00,"$1,500.00",
,,,,,,,
"Brokerage services provided by Fidelity Brokerage Services LLC"
"""

SCHWAB = '''"Positions for account ...as of 07/28/2026"
"Symbol","Description","Quantity","Price","Market Value","Cost Basis"
"TSLA","TESLA INC","8","$307.00","$2,456.00","$3,000.00"
"MSFT","MICROSOFT","4","$397.00","$1,588.00","$1,200.00"
"Cash & Cash Investments","--","--","--","$800.00","--"
'''


class TestParser(unittest.TestCase):
    def test_fidelity(self):
        from features.portfolio.parser import parse_positions_csv, detect_source
        h = {x["symbol"]: x for x in parse_positions_csv(FIDELITY)}
        self.assertEqual(set(h), {"AAPL", "NVDA"})          # cash + footer skipped
        self.assertEqual(h["AAPL"]["shares"], 10)
        self.assertEqual(h["AAPL"]["cost_basis"], 2000.0)
        self.assertEqual(detect_source(FIDELITY), "fidelity")

    def test_schwab(self):
        from features.portfolio.parser import parse_positions_csv
        h = {x["symbol"]: x for x in parse_positions_csv(SCHWAB)}
        self.assertEqual(set(h), {"TSLA", "MSFT"})           # cash row skipped
        self.assertEqual(h["TSLA"]["shares"], 8)
        self.assertEqual(h["TSLA"]["cost_basis"], 3000.0)

    def test_empty_and_junk(self):
        from features.portfolio.parser import parse_positions_csv
        self.assertEqual(parse_positions_csv(""), [])
        self.assertEqual(parse_positions_csv("just some text, no header"), [])


class TestRuleReco(unittest.TestCase):
    def _reco(self, **q):
        from features.portfolio.advisor import _rule_reco
        base = {"uptrend": None, "mf_signal": None, "cmf": None, "rsi": None, "pct_from_high": None}
        base.update(q)
        return _rule_reco(base)

    def test_sell_downtrend_distribution(self):
        r = self._reco(uptrend=False, mf_signal="STRONG_OUT")
        self.assertEqual(r["action"], "SELL")

    def test_trim_weakness(self):
        self.assertEqual(self._reco(uptrend=False, mf_signal="IN")["action"], "TRIM")

    def test_add_uptrend_inflow(self):
        r = self._reco(uptrend=True, mf_signal="STRONG_IN", cmf=0.2, rsi=58)
        self.assertEqual(r["action"], "ADD")

    def test_trim_overbought_at_highs(self):
        r = self._reco(uptrend=True, mf_signal="IN", rsi=80, pct_from_high=-1)
        self.assertEqual(r["action"], "TRIM")

    def test_hold_mixed(self):
        self.assertEqual(self._reco(uptrend=None, mf_signal="NEUTRAL")["action"], "HOLD")


class TestGatingRoutes(unittest.TestCase):
    def test_routes_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        for r in ("/api/portfolio", "/api/portfolio/import", "/api/portfolio/analyze",
                  "/api/portfolio/access", "/api/admin/portfolio-access"):
            self.assertIn(r, rules)

    def test_requires_auth(self):
        from app import app
        c = app.test_client()
        self.assertIn(c.get("/api/portfolio").status_code, (401, 302))
        self.assertIn(c.get("/api/admin/portfolio-access").status_code, (401, 403, 302))


class TestAnalyzeSummary(unittest.TestCase):
    def test_summary_concentration_and_lists(self):
        from features.portfolio.advisor import _portfolio_summary
        rows = [
            {"symbol": "AAPL", "value": 8000, "action": "ADD"},
            {"symbol": "NVDA", "value": 1000, "action": "SELL"},
            {"symbol": "T", "value": 1000, "action": "TRIM"},
        ]
        s = _portfolio_summary(rows)
        self.assertEqual(s["add"], ["AAPL"])
        self.assertEqual(set(s["cut"]), {"NVDA", "T"})
        self.assertEqual(s["top_position"], "AAPL")
        self.assertTrue(s["concentration_flag"])   # AAPL 80% > 25%


if __name__ == "__main__":
    unittest.main()
