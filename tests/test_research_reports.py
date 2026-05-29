"""
Tests for the derived Market Pressure / imbalance-style report.
Run: python -m pytest tests/test_research_reports.py -v
  or: python tests/test_research_reports.py
"""
import sys
import os
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _row(ticker, verdict, confidence, sector, market_cap,
         price=10.0, scan_date="2026-05-29"):
    """A screener_results-shaped row (volume/industry are fetched live, not stored)."""
    return {
        "ticker": ticker, "price": price, "verdict": verdict,
        "confidence": confidence, "sector": sector, "name": ticker,
        "market_cap": market_cap, "scan_date": scan_date,
    }


# Live-quote / industry stand-ins so the test never touches the network.
FAKE_QUOTES = {
    "AAPL": {"price": 200.0, "volume": 50_000_000},   # $10.0B
    "NVDA": {"price": 100.0, "volume": 40_000_000},   # $4.0B
    "TSLA": {"price": 250.0, "volume": 30_000_000},   # $7.5B
    "MSFT": {"price": 400.0, "volume": 20_000_000},   # $8.0B
    "INTC": {"price": 30.0,  "volume": 60_000_000},   # $1.8B
    "DUP":  {"price": 50.0,  "volume": 10_000_000},   # $0.5B
    # KO (neutral) is filtered before quoting; ZZZZ has no quote → dropped.
}
# INTC intentionally omitted to exercise the industry→sector fallback path.
FAKE_INDUSTRY = {"AAPL": "Hardware", "NVDA": "Semiconductors", "TSLA": "Autos",
                 "MSFT": "Systems Software"}


def _quote_fetcher(tickers):
    return {t: FAKE_QUOTES[t] for t in tickers if t in FAKE_QUOTES}


def _industry_fetcher(tickers):
    return {t: FAKE_INDUSTRY[t] for t in tickers if t in FAKE_INDUSTRY}


class TestVerdictTier(unittest.TestCase):
    def test_tier_mapping(self):
        from features.research.routes import _verdict_tier
        self.assertEqual(_verdict_tier("OPPORTUNITY"), "strong")
        self.assertEqual(_verdict_tier("STRONG BUY"), "strong")
        self.assertEqual(_verdict_tier("BULLISH"), "strong")
        self.assertEqual(_verdict_tier("MOMENTUM BUY"), "momentum")
        self.assertEqual(_verdict_tier("RECOVERY BUY"), "momentum")
        self.assertEqual(_verdict_tier("AVOID"), "avoid")
        self.assertEqual(_verdict_tier("FALLING KNIFE"), "avoid")
        self.assertEqual(_verdict_tier("RISKY"), "cautious")
        self.assertEqual(_verdict_tier("NEUTRAL"), "watch")
        self.assertEqual(_verdict_tier(None), "watch")


class TestDedupe(unittest.TestCase):
    def test_prefers_actionable_over_stale_watch(self):
        from features.research.routes import _dedupe_best, _verdict_tier
        rows = [
            # most recent appearance is a stale WATCH (e.g. oversold scan)...
            _row("DUP", "WATCH", 40, "Tech", 1e11, scan_date="2026-05-29"),
            # ...but an earlier scan flagged it OPPORTUNITY — that should win.
            _row("DUP", "OPPORTUNITY", 85, "Tech", 1e11, scan_date="2026-05-27"),
        ]
        best = _dedupe_best(rows)
        self.assertEqual(_verdict_tier(best["DUP"]["verdict"]), "strong")


class TestBuildReport(unittest.TestCase):
    FIXTURE = [
        _row("AAPL", "OPPORTUNITY", 90, "Information Technology", 3.0e12),
        _row("NVDA", "STRONG BUY", 88, "Information Technology", 2.5e12),
        _row("TSLA", "MOMENTUM BUY", 70, "Consumer Discretionary", 8.0e11),
        _row("MSFT", "AVOID", 80, "Information Technology", 2.8e12),
        _row("INTC", "RISKY", 60, "Information Technology", 1.5e11),
        _row("KO", "NEUTRAL", 50, "Consumer Staples", 2.6e11),          # excluded (watch)
        _row("ZZZZ", "OPPORTUNITY", 95, "Misc", 1.0e9),                  # excluded (no quote)
    ]

    def _build(self, **kw):
        from features.research import routes
        with mock.patch("db.query", return_value=self.FIXTURE):
            return routes.build_imbalance_report(
                quote_fetcher=_quote_fetcher, industry_fetcher=_industry_fetcher, **kw)

    def test_split_buy_sell(self):
        rep = self._build(limit=10)
        self.assertEqual({b["symbol"] for b in rep["buys"]}, {"AAPL", "NVDA", "TSLA"})
        self.assertEqual({s["symbol"] for s in rep["sells"]}, {"MSFT", "INTC"})

    def test_neutral_and_unquoted_excluded(self):
        rep = self._build(limit=10)
        syms = [x["symbol"] for x in rep["buys"] + rep["sells"]]
        self.assertNotIn("KO", syms)     # NEUTRAL → watch tier
        self.assertNotIn("ZZZZ", syms)   # OPPORTUNITY but no live quote → dropped

    def test_ranked_by_dollar_volume_desc(self):
        rep = self._build(limit=10)
        dv = [b["dollar_volume"] for b in rep["buys"]]
        self.assertEqual(dv, sorted(dv, reverse=True))
        # AAPL $10.0B > TSLA $7.5B > NVDA $4.0B
        self.assertEqual([b["symbol"] for b in rep["buys"]], ["AAPL", "TSLA", "NVDA"])
        self.assertEqual([s["symbol"] for s in rep["sells"]], ["MSFT", "INTC"])

    def test_dollar_volume_and_enrichment(self):
        rep = self._build(limit=10)
        aapl = next(b for b in rep["buys"] if b["symbol"] == "AAPL")
        self.assertEqual(aapl["dollar_volume"], 200.0 * 50_000_000)
        self.assertEqual(aapl["volume"], 50_000_000)
        self.assertEqual(aapl["price"], 200.0)
        self.assertEqual(aapl["industry"], "Hardware")
        self.assertEqual(aapl["sector"], "Information Technology")
        # internal-only fields are stripped from the payload
        self.assertNotIn("market_cap", aapl)
        self.assertNotIn("tier", aapl)

    def test_industry_falls_back_to_sector(self):
        # INTC is not in FAKE_INDUSTRY → industry should fall back to its sector.
        rep = self._build(limit=10)
        intc = next(s for s in rep["sells"] if s["symbol"] == "INTC")
        self.assertEqual(intc["industry"], "Information Technology")

    def test_limit_caps_each_side(self):
        rep = self._build(limit=2)
        self.assertEqual(len(rep["buys"]), 2)
        self.assertLessEqual(len(rep["sells"]), 2)

    def test_report_metadata(self):
        rep = self._build(limit=10)
        self.assertIn("source", rep)
        self.assertIn("generated_at", rep)
        self.assertEqual(rep["scan_date"], "2026-05-29")
        self.assertEqual(rep["total_universe"], len(self.FIXTURE))


class TestRouteRegistered(unittest.TestCase):
    def test_blueprint_and_route(self):
        from features.research.routes import bp
        self.assertEqual(bp.name, "research")
        from app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        self.assertIn("/api/research/imbalances", rules)


if __name__ == "__main__":
    unittest.main()
