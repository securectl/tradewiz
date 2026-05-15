"""Unit tests for the tracker /api/tracker/bot-trades endpoint and the
by_asset filter fix in /api/bot/dashboard.

Covers:
  - Per-user isolation (one user's trades never appear in another user's view)
  - Source filter: all/crypto/stock/claude/watchdog
  - by_source summary counts include all 4 sources
  - 401 for non-logged-in users
  - by_asset block in /api/bot/dashboard honors ?asset= filter

Run: docker compose exec app python -m pytest tests/test_tracker_bot_trades.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_pg():
    from db import IS_POSTGRES
    return IS_POSTGRES


def _ph():
    return "%s" if _is_pg() else "?"


class _TrackerTestBase(unittest.TestCase):
    """Shared fixture: two test users with mixed-source trades each."""

    UID_A = -201
    UID_B = -202

    @classmethod
    def setUpClass(cls):
        from db import execute
        ph = _ph()
        # Test users
        for uid in (cls.UID_A, cls.UID_B):
            try:
                if _is_pg():
                    execute(
                        "INSERT INTO users (id, email, name) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                        (uid, f"tracker{uid}@test.local", "tracker test"),
                    )
                else:
                    execute(
                        "INSERT OR IGNORE INTO users (id, email, name) VALUES (?, ?, ?)",
                        (uid, f"tracker{uid}@test.local", "tracker test"),
                    )
            except Exception:
                pass

        # Seed trades. UID_A: 1 closed crypto +50, 1 closed stock -10, 1 open claude.
        # UID_B:  1 closed watchdog +20.
        seeds = [
            # (user_id, asset_type, coin, side, size, entry, exit, pnl, status)
            (cls.UID_A, "crypto",   "BTC-USDT", "buy", 1.0, 100.0, 150.0,  50.0, "closed"),
            (cls.UID_A, "stock",    "AAPL",     "buy", 5.0, 200.0, 198.0, -10.0, "closed"),
            (cls.UID_A, "claude",   "MSFT",     "buy", 2.0, 300.0, None,   None, "open"),
            (cls.UID_B, "watchdog", "SPY",      "buy", 3.0, 400.0, 410.0,  20.0, "closed"),
        ]
        for uid, at, coin, side, size, entry, exit_p, pnl, status in seeds:
            execute(
                f"INSERT INTO bot_trades (user_id, coin, side, size, entry_price, exit_price, pnl, "
                f"status, asset_type, opened_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, "
                + ("NOW())" if _is_pg() else "datetime('now'))"),
                (uid, coin, side, size, entry, exit_p, pnl, status, at),
            )

    @classmethod
    def tearDownClass(cls):
        from db import execute
        ph = _ph()
        for uid in (cls.UID_A, cls.UID_B):
            for sql in (
                f"DELETE FROM bot_trades WHERE user_id = {ph}",
                f"DELETE FROM users WHERE id = {ph}",
            ):
                try:
                    execute(sql, (uid,))
                except Exception:
                    pass


class TestTrackerBotTradesEndpoint(_TrackerTestBase):
    """Hit the route through Flask test_client with a logged-in session."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app import app
        cls.client = app.test_client()
        cls.app = app

    def _login(self, uid):
        # flask-login session keys vary by version — set _user_id directly.
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True

    def test_unauthenticated_returns_401(self):
        # Use a fresh test_client (no session) — flask-login has nothing to read.
        clean = self.app.test_client()
        resp = clean.get("/api/tracker/bot-trades")
        self.assertEqual(resp.status_code, 401)

    def test_user_a_sees_only_own_trades(self):
        self._login(self.UID_A)
        resp = self.client.get("/api/tracker/bot-trades?source=all&limit=50")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        coins = {t["coin"] for t in data["trades"]}
        self.assertIn("BTC-USDT", coins)
        self.assertIn("AAPL", coins)
        self.assertIn("MSFT", coins)
        self.assertNotIn("SPY", coins, "user A must not see user B's watchdog trade")

    def test_user_b_sees_only_own_trades(self):
        self._login(self.UID_B)
        resp = self.client.get("/api/tracker/bot-trades?source=all")
        data = resp.get_json()
        coins = {t["coin"] for t in data["trades"]}
        self.assertEqual(coins, {"SPY"}, "user B should only see their own watchdog trade")

    def test_source_filter_crypto_only(self):
        self._login(self.UID_A)
        resp = self.client.get("/api/tracker/bot-trades?source=crypto")
        data = resp.get_json()
        sources = {t["source"] for t in data["trades"]}
        self.assertEqual(sources, {"crypto"})

    def test_source_filter_stock_excludes_crypto(self):
        """Regression test for the user-visible bug: stock view leaking crypto."""
        self._login(self.UID_A)
        resp = self.client.get("/api/tracker/bot-trades?source=stock")
        data = resp.get_json()
        sources = {t["source"] for t in data["trades"]}
        self.assertEqual(sources, {"stock"})
        self.assertNotIn("crypto", sources)
        self.assertNotIn("claude", sources)

    def test_invalid_source_returns_400(self):
        self._login(self.UID_A)
        resp = self.client.get("/api/tracker/bot-trades?source=bogus")
        self.assertEqual(resp.status_code, 400)

    def test_by_source_summary_includes_all_present_sources(self):
        self._login(self.UID_A)
        resp = self.client.get("/api/tracker/bot-trades")
        data = resp.get_json()
        # User A has crypto + stock + claude; not watchdog
        self.assertIn("crypto", data["by_source"])
        self.assertIn("stock", data["by_source"])
        self.assertIn("claude", data["by_source"])
        self.assertNotIn("watchdog", data["by_source"])

    def test_overall_pnl_excludes_open_trades(self):
        """Open trades have null pnl — must not contribute to overall P&L."""
        self._login(self.UID_A)
        resp = self.client.get("/api/tracker/bot-trades")
        data = resp.get_json()
        # crypto +50, stock -10, claude open=null → overall = +40
        self.assertEqual(data["overall"]["pnl"], 40.0)
        self.assertEqual(data["overall"]["closed"], 2)

    def test_status_filter_open(self):
        self._login(self.UID_A)
        resp = self.client.get("/api/tracker/bot-trades?status=open")
        data = resp.get_json()
        statuses = {t["status"] for t in data["trades"]}
        self.assertEqual(statuses, {"open"})

    def test_overall_scopes_to_active_filter(self):
        """Audit Apr 2026: Overall line stayed at lifetime sum even when a
        source filter was active. With seed data (UID_A): crypto +50, stock -10,
        claude open. Filtering to 'crypto' should show overall pnl=50 trades=1
        — not the all-sources total of +40."""
        self._login(self.UID_A)
        resp_all = self.client.get("/api/tracker/bot-trades?source=all").get_json()
        resp_crypto = self.client.get("/api/tracker/bot-trades?source=crypto").get_json()
        resp_stock = self.client.get("/api/tracker/bot-trades?source=stock").get_json()

        # All sources: crypto +50 + stock -10 + claude(null) = +40
        self.assertEqual(resp_all["overall"]["pnl"], 40.0)
        self.assertEqual(resp_all["overall"]["trades"], 3)

        # Crypto only: +50, 1 trade
        self.assertEqual(resp_crypto["overall"]["pnl"], 50.0)
        self.assertEqual(resp_crypto["overall"]["trades"], 1)
        self.assertEqual(resp_crypto["overall"]["closed"], 1)

        # Stock only: -10, 1 trade
        self.assertEqual(resp_stock["overall"]["pnl"], -10.0)
        self.assertEqual(resp_stock["overall"]["trades"], 1)


class TestByAssetHonorsFilter(_TrackerTestBase):
    """Regression: /api/bot/dashboard?asset=stock previously returned by_asset
    with both crypto and stock. The fix scopes by_asset to the requested
    filter."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app import app
        cls.client = app.test_client()

    def _login(self, uid):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True

    def test_dashboard_stock_filter_excludes_crypto(self):
        self._login(self.UID_A)
        resp = self.client.get("/api/bot/dashboard?asset=stock")
        # 200 if user has trader role; 403 if not. Either way, when 200,
        # by_asset must not contain crypto.
        if resp.status_code == 200:
            data = resp.get_json()
            self.assertNotIn("crypto", data.get("by_asset", {}),
                "by_asset must not include crypto when ?asset=stock")
        # If 403 (no trader role), the assertion is moot — skip
        else:
            self.skipTest(f"user has no trader role ({resp.status_code})")


class TestJournalExcludesBotEntries(_TrackerTestBase):
    """Regression: journal_entries was being polluted by crypto/stock bots
    auto-logging. Auto-logging was removed (Apr 2026) and the journal API
    now filters out any legacy '[%Bot]%'-tagged rows."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app import app
        cls.client = app.test_client()
        cls.app = app

        # Seed journal_entries: 1 manual + 2 bot-tagged (legacy)
        from db import execute
        ph = _ph()
        seeds = [
            (cls.UID_A, "AAPL",     "Manual buy at support", "BUY"),
            (cls.UID_A, "SOL-USDT", "[Crypto Bot] BUY — RSI oversold", "BUY"),
            (cls.UID_A, "MSFT",     "[Stock Bot] Closed — TP hit", "SELL"),
        ]
        for uid, ticker, notes, action in seeds:
            execute(
                f"INSERT INTO journal_entries (user_id, ticker, notes, action) VALUES ({ph}, {ph}, {ph}, {ph})",
                (uid, ticker, notes, action),
            )

    def _login(self, uid):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True

    def test_journal_excludes_bot_logged_entries(self):
        self._login(self.UID_A)
        resp = self.client.get("/api/journal")
        self.assertEqual(resp.status_code, 200)
        entries = resp.get_json()
        notes = [e["notes"] for e in entries]
        self.assertTrue(any("Manual buy" in (n or "") for n in notes),
            "manual entry must be visible")
        self.assertFalse(any("[Crypto Bot]" in (n or "") for n in notes),
            "crypto bot-tagged entries must be hidden from journal")
        self.assertFalse(any("[Stock Bot]" in (n or "") for n in notes),
            "stock bot-tagged entries must be hidden from journal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
