"""Verifies that /api/bot/log and /api/stock-bot/log merge synthetic
'TRADE CLOSED' entries derived from bot_trades, so closed trades stay visible
in the activity feed even after the original bot_log row is pruned.

Regression context (May 2026): bot_log retention is 30 days. Trades closed
before that window had their close-event log lines deleted on container
restart, so the activity feed appeared to "hide" closed trades. The merge
synthesizes those entries from bot_trades on each request.

Run: docker compose exec app python -m pytest tests/test_bot_log_close_events.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ph():
    from db import IS_POSTGRES
    return "%s" if IS_POSTGRES else "?"


def _is_pg():
    from db import IS_POSTGRES
    return IS_POSTGRES


class _BotLogTestBase(unittest.TestCase):
    UID = -311

    @classmethod
    def setUpClass(cls):
        from db import execute
        ph = _ph()
        # Test user must satisfy @trader_required — grant 'admin' role
        # (auth.User.is_trader returns True for admins regardless of bot_access).
        if _is_pg():
            execute(
                "INSERT INTO users (id, email, name) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (cls.UID, f"botlog{cls.UID}@test.local", "botlog test"),
            )
            execute(
                "INSERT INTO user_roles (user_id, role) VALUES (%s, 'admin') "
                "ON CONFLICT (user_id, role) DO NOTHING",
                (cls.UID,),
            )
        else:
            execute(
                "INSERT OR IGNORE INTO users (id, email, name) VALUES (?, ?, ?)",
                (cls.UID, f"botlog{cls.UID}@test.local", "botlog test"),
            )
            execute(
                "INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?, 'admin')",
                (cls.UID,),
            )

        # Seed: 1 closed crypto, 1 closed stock, 1 open crypto (open should NOT
        # appear in the synthetic close events).
        seeds = [
            ("crypto", "BTC-USDT", "buy", 1.0, 100.0, 150.0, 50.0, 50.0,
             "macd_cross", "closed"),
            ("stock",  "AAPL",     "buy", 5.0, 200.0, 198.0, -10.0, -1.0,
             "momentum",  "closed"),
            ("crypto", "ETH-USDT", "buy", 2.0, 2000.0, None, None, None,
             "ema_trend", "open"),
        ]
        for at, coin, side, size, entry, exit_p, pnl, pct, strat, status in seeds:
            sql = (
                f"INSERT INTO bot_trades (user_id, coin, side, size, entry_price, "
                f"exit_price, pnl, pnl_pct, strategy, status, asset_type, "
                f"opened_at, closed_at) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, "
                f"{ph}, {ph}, {ph}, {ph}, {ph}, {ph}, "
            )
            if status == "closed":
                sql += (
                    "NOW() - INTERVAL '2 days', NOW() - INTERVAL '1 day')"
                    if _is_pg() else
                    "datetime('now', '-2 days'), datetime('now', '-1 day'))"
                )
            else:
                sql += "NOW(), NULL)" if _is_pg() else "datetime('now'), NULL)"
            execute(sql, (cls.UID, coin, side, size, entry, exit_p, pnl, pct,
                          strat, status, at))

        # Seed one bot_log row in each source so the merge has a real row to
        # interleave with.
        for source in ("crypto", "stock"):
            execute(
                f"INSERT INTO bot_log (user_id, level, message, source) "
                f"VALUES ({ph}, 'info', 'Scan cycle complete', {ph})",
                (cls.UID, source),
            )

    @classmethod
    def tearDownClass(cls):
        from db import execute
        ph = _ph()
        for sql in (
            f"DELETE FROM bot_trades WHERE user_id = {ph}",
            f"DELETE FROM bot_log WHERE user_id = {ph}",
            f"DELETE FROM user_roles WHERE user_id = {ph}",
            f"DELETE FROM users WHERE id = {ph}",
        ):
            try:
                execute(sql, (cls.UID,))
            except Exception:
                pass


class TestCryptoBotLogCloseEvents(_BotLogTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app import app
        cls.client = app.test_client()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.UID)
            sess["_fresh"] = True

    def test_close_event_present_for_closed_crypto_trade(self):
        self._login()
        resp = self.client.get("/api/bot/log?limit=50")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        msgs = [r["message"] for r in resp.get_json()]
        self.assertTrue(
            any("TRADE CLOSED" in m and "BTC-USDT" in m for m in msgs),
            f"expected synthetic 'TRADE CLOSED' for BTC-USDT, got: {msgs}",
        )

    def test_open_trade_does_not_appear_as_closed_event(self):
        self._login()
        resp = self.client.get("/api/bot/log?limit=50")
        msgs = [r["message"] for r in resp.get_json()]
        self.assertFalse(
            any("TRADE CLOSED" in m and "ETH-USDT" in m for m in msgs),
            "open ETH-USDT must not appear as a TRADE CLOSED entry",
        )

    def test_stock_close_does_not_leak_into_crypto_log(self):
        self._login()
        resp = self.client.get("/api/bot/log?limit=50")
        msgs = [r["message"] for r in resp.get_json()]
        self.assertFalse(
            any("AAPL" in m for m in msgs),
            "stock AAPL close must not leak into crypto log",
        )

    def test_real_bot_log_row_still_returned(self):
        self._login()
        resp = self.client.get("/api/bot/log?limit=50")
        msgs = [r["message"] for r in resp.get_json()]
        self.assertIn("Scan cycle complete", msgs)

    def test_warn_level_filter_skips_synthetic_closes(self):
        # Synthetic closes are 'info'. Filtering on 'warn' should not include them.
        self._login()
        resp = self.client.get("/api/bot/log?level=warn&limit=50")
        msgs = [r["message"] for r in resp.get_json()]
        self.assertFalse(any("TRADE CLOSED" in m for m in msgs))

    def test_synthetic_id_is_negative(self):
        # Negative ids are how we keep synthetic rows from colliding with real
        # bot_log primary keys; the JS doesn't depend on this but downstream
        # consumers might.
        self._login()
        resp = self.client.get("/api/bot/log?limit=50")
        rows = resp.get_json()
        synth = [r for r in rows if r.get("message", "").startswith("TRADE CLOSED")]
        self.assertTrue(synth)
        for r in synth:
            self.assertLess(r["id"], 0)


class TestStockBotLogCloseEvents(_BotLogTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app import app
        cls.client = app.test_client()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.UID)
            sess["_fresh"] = True

    def test_close_event_present_for_closed_stock_trade(self):
        self._login()
        resp = self.client.get("/api/stock-bot/log?limit=50")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        msgs = [r["message"] for r in resp.get_json()]
        self.assertTrue(
            any("TRADE CLOSED" in m and "AAPL" in m for m in msgs),
            f"expected synthetic 'TRADE CLOSED' for AAPL, got: {msgs}",
        )

    def test_crypto_close_does_not_leak_into_stock_log(self):
        self._login()
        resp = self.client.get("/api/stock-bot/log?limit=50")
        msgs = [r["message"] for r in resp.get_json()]
        self.assertFalse(
            any("BTC-USDT" in m for m in msgs),
            "crypto BTC-USDT close must not leak into stock log",
        )


if __name__ == "__main__":
    unittest.main()
