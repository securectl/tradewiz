"""Tests for the news agent — ticker/sector tagging, trending aggregation,
sentiment scoring, and route registration.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from features.news_agent import agent


class TestTickerExtraction(unittest.TestCase):
    def test_cashtags(self):
        out = agent._extract_tickers("Big move in $AAPL and $TSLA today")
        self.assertIn("AAPL", out)
        self.assertIn("TSLA", out)

    def test_bare_known_tickers(self):
        out = agent._extract_tickers("NVDA and AMD rally on AI demand")
        self.assertIn("NVDA", out)
        self.assertIn("AMD", out)

    def test_stopwords_excluded(self):
        # CEO/USA/GDP/ETF look like tickers but must not match
        out = agent._extract_tickers("The CEO says USA GDP and the ETF are fine")
        for junk in ("CEO", "USA", "GDP", "ETF"):
            self.assertNotIn(junk, out)

    def test_unknown_bareword_ignored(self):
        # A 3-letter uppercase word not in the universe shouldn't match
        out = agent._extract_tickers("XYZ is not a real ticker here")
        self.assertNotIn("XYZ", out)


class TestSectorExtraction(unittest.TestCase):
    def test_sector_from_ticker(self):
        secs = agent._extract_sectors("NVDA earnings beat", ["NVDA"])
        self.assertIn("Technology", secs)

    def test_sector_from_keyword(self):
        secs = agent._extract_sectors("OPEC cuts crude oil output", [])
        self.assertIn("Energy", secs)

    def test_crypto_keyword(self):
        secs = agent._extract_sectors("Bitcoin surges past resistance", [])
        self.assertIn("Crypto", secs)


class TestSentimentScore(unittest.TestCase):
    def test_net_sentiment(self):
        self.assertEqual(agent._sent_score(["bullish", "bullish", "bearish"]), round(1 / 3, 2))
        self.assertEqual(agent._sent_score(["bearish", "bearish"]), -1.0)
        self.assertEqual(agent._sent_score(["neutral", "neutral"]), 0.0)


class TestTrending(unittest.TestCase):
    def test_aggregates_stocks_and_sectors(self):
        rows = [
            {"tickers": "AAPL,NVDA", "sectors": "Technology", "sentiment": "bullish", "category": "market"},
            {"tickers": "AAPL", "sectors": "Technology", "sentiment": "bullish", "category": "reddit"},
            {"tickers": "NVDA", "sectors": "Technology", "sentiment": "bearish", "category": "market"},
            {"tickers": "XOM", "sectors": "Energy", "sentiment": "neutral", "category": "market"},
        ]
        with mock.patch.object(agent, "query", return_value=rows):
            out = agent.trending(hours=24, limit=10)
        self.assertEqual(out["total_articles"], 4)
        top = out["stocks"][0]
        self.assertEqual(top["ticker"], "AAPL")   # 2 mentions, most
        self.assertEqual(top["mentions"], 2)
        self.assertEqual(top["reddit_mentions"], 1)
        self.assertEqual(top["sector"], "Technology")
        tech = next(s for s in out["sectors"] if s["sector"] == "Technology")
        self.assertEqual(tech["mentions"], 3)

    def test_empty(self):
        with mock.patch.object(agent, "query", return_value=[]):
            out = agent.trending()
        self.assertEqual(out["stocks"], [])
        self.assertEqual(out["sectors"], [])


class TestTickerSignal(unittest.TestCase):
    def test_signal_aggregates(self):
        rows = [
            {"sentiment": "bullish", "category": "reddit"},
            {"sentiment": "bullish", "category": "market"},
            {"sentiment": "bearish", "category": "market"},
        ]
        with mock.patch.object(agent, "query", return_value=rows):
            sig = agent.ticker_signal("NVDA", hours=48)
        self.assertEqual(sig["mentions"], 3)
        self.assertEqual(sig["reddit_mentions"], 1)
        self.assertTrue(sig["buzz"])          # >= 3 mentions
        self.assertAlmostEqual(sig["sentiment_score"], round(1 / 3, 2))

    def test_empty_ticker(self):
        sig = agent.ticker_signal("", hours=48)
        self.assertEqual(sig["mentions"], 0)
        self.assertFalse(sig["buzz"])

    def test_low_buzz_not_flagged(self):
        with mock.patch.object(agent, "query", return_value=[{"sentiment": "bullish", "category": "market"}]):
            sig = agent.ticker_signal("AAPL")
        self.assertEqual(sig["mentions"], 1)
        self.assertFalse(sig["buzz"])


class TestScannerLifecycle(unittest.TestCase):
    def test_flag_toggles(self):
        self.assertFalse(agent.is_scanner_running())


class TestRoutes(unittest.TestCase):
    def test_routes_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/news/feed", rules)
        self.assertIn("/api/news/trending", rules)
        self.assertIn("/api/news/refresh", rules)

    def test_feed_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/news/feed")
        self.assertIn(resp.status_code, (401, 302))

    def test_trending_requires_auth(self):
        from app import app
        resp = app.test_client().get("/api/news/trending")
        self.assertIn(resp.status_code, (401, 302))


if __name__ == "__main__":
    unittest.main()
