"""Unit tests for per-user broker keys + global bot-config resolver.

Covers the work shipped in the Apr 2026 sessions:
  - shared.helpers.get_user_api_keys / mask_api_key / delete_user_api_key
  - claude_bot.bot_engine._cfg with global (user_id=0) fallback
  - claude_bot.bot_engine._get_broker per-user key injection
  - features.admin.routes._ensure_global_user idempotency

Run: docker compose exec app python -m pytest tests/test_user_broker_keys.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMaskApiKey(unittest.TestCase):
    """mask_api_key should hide the middle but keep last-4 for UX recognition."""

    def test_long_key_shows_first_2_last_4(self):
        from shared.helpers import mask_api_key
        self.assertEqual(mask_api_key("PKABCDEFGHIJ1234"), "PK•••1234")

    def test_short_key_fully_hidden(self):
        from shared.helpers import mask_api_key
        self.assertEqual(mask_api_key("abc123"), "••••••")

    def test_empty_returns_empty(self):
        from shared.helpers import mask_api_key
        self.assertEqual(mask_api_key(""), "")
        self.assertEqual(mask_api_key(None), "")


class TestUserApiKeysRoundTrip(unittest.TestCase):
    """Encrypt → store → decrypt → retrieve, end-to-end through the helper."""

    TEST_UID = -42  # negative so we never collide with a real user

    @classmethod
    def setUpClass(cls):
        # Sentinel user to satisfy bot_config_user_id_fkey is also negative-id-safe
        # because user_api_keys FK is to users(id) ON DELETE CASCADE — but for a
        # purely-in-memory test we'll skip the FK by going around it. The cleanest
        # path is to insert a throwaway user and delete on tearDown.
        from db import execute, IS_POSTGRES
        if IS_POSTGRES:
            execute(
                "INSERT INTO users (id, email, name) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (cls.TEST_UID, f"test{cls.TEST_UID}@example.local", "test"),
            )
        else:
            execute(
                "INSERT OR IGNORE INTO users (id, email, name) VALUES (?, ?, ?)",
                (cls.TEST_UID, f"test{cls.TEST_UID}@example.local", "test"),
            )

    @classmethod
    def tearDownClass(cls):
        # Best-effort cleanup. Active bot threads can hold row locks on the
        # users table, causing transient deadlocks; the test rows are harmless
        # if left behind (negative IDs never collide with real users).
        from db import execute
        ph = "%s" if _is_pg() else "?"
        for sql in (
            f"DELETE FROM user_api_keys WHERE user_id = {ph}",
            f"DELETE FROM bot_config WHERE user_id = {ph}",
            f"DELETE FROM users WHERE id = {ph}",
        ):
            try:
                execute(sql, (cls.TEST_UID,))
            except Exception:
                pass

    def test_round_trip_alpaca(self):
        from shared.helpers import _upsert_api_key, get_user_api_keys
        from crypto_utils import encrypt
        _upsert_api_key(self.TEST_UID, "alpaca", "api_key", encrypt("PKLIVE12345ABC"))
        _upsert_api_key(self.TEST_UID, "alpaca", "secret_key", encrypt("topsecret"))
        keys = get_user_api_keys(self.TEST_UID, "alpaca")
        self.assertEqual(keys.get("api_key"), "PKLIVE12345ABC")
        self.assertEqual(keys.get("secret_key"), "topsecret")

    def test_unknown_user_returns_empty(self):
        from shared.helpers import get_user_api_keys
        self.assertEqual(get_user_api_keys(-99999, "alpaca"), {})

    def test_no_user_id_returns_empty(self):
        from shared.helpers import get_user_api_keys
        self.assertEqual(get_user_api_keys(None, "alpaca"), {})
        self.assertEqual(get_user_api_keys(0, "alpaca"), {})  # 0 = sentinel, not a real user

    def test_delete_clears_single_key(self):
        from shared.helpers import _upsert_api_key, get_user_api_keys, delete_user_api_key
        from crypto_utils import encrypt
        _upsert_api_key(self.TEST_UID, "webull", "app_key", encrypt("aaa"))
        _upsert_api_key(self.TEST_UID, "webull", "app_secret", encrypt("bbb"))
        delete_user_api_key(self.TEST_UID, "webull", "app_key")
        keys = get_user_api_keys(self.TEST_UID, "webull")
        self.assertNotIn("app_key", keys)
        self.assertEqual(keys.get("app_secret"), "bbb")


class TestGlobalConfigResolver(unittest.TestCase):
    """User-specific bot_config rows must override global (user_id=0) rows.

    Validates the `WHERE user_id IN (X, 0) ORDER BY CASE WHEN user_id = 0
    THEN 1 ELSE 0 END LIMIT 1` pattern across all 6 bot config getters via
    claude_bot._cfg as proxy. Sign-agnostic so user always wins over global.
    """

    TEST_UID = -43
    TEST_KEY = "cb_test_resolver_key"

    @classmethod
    def setUpClass(cls):
        from db import execute, IS_POSTGRES
        # Sentinel user (id=0) and our test user
        if IS_POSTGRES:
            execute(
                "INSERT INTO users (id, email, name) VALUES (0, 'system@global.local', 'global') ON CONFLICT (id) DO NOTHING"
            )
            execute(
                "INSERT INTO users (id, email, name) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (cls.TEST_UID, f"test{cls.TEST_UID}@example.local", "test"),
            )
        else:
            execute(
                "INSERT OR IGNORE INTO users (id, email, name) VALUES (0, 'system@global.local', 'global')"
            )
            execute(
                "INSERT OR IGNORE INTO users (id, email, name) VALUES (?, ?, ?)",
                (cls.TEST_UID, f"test{cls.TEST_UID}@example.local", "test"),
            )

    @classmethod
    def tearDownClass(cls):
        from db import execute
        # Don't cascade delete the sentinel — other tests/runtime might need it
        ph = "%s" if _is_pg() else "?"
        execute(f"DELETE FROM bot_config WHERE user_id IN (0, {ph}) AND key = {ph}",
                (cls.TEST_UID, cls.TEST_KEY))
        execute(f"DELETE FROM users WHERE id = {ph}", (cls.TEST_UID,))

    def test_falls_back_to_global_when_user_unset(self):
        from shared.helpers import _upsert_bot_config
        from claude_bot.bot_engine import _cfg
        _upsert_bot_config(0, self.TEST_KEY, "global-value")
        # Make sure no user-specific row exists
        from db import execute
        ph = "%s" if _is_pg() else "?"
        execute(f"DELETE FROM bot_config WHERE user_id = {ph} AND key = {ph}",
                (self.TEST_UID, self.TEST_KEY))
        self.assertEqual(_cfg(self.TEST_UID, self.TEST_KEY), "global-value")

    def test_user_value_overrides_global(self):
        from shared.helpers import _upsert_bot_config
        from claude_bot.bot_engine import _cfg
        _upsert_bot_config(0, self.TEST_KEY, "global-value")
        _upsert_bot_config(self.TEST_UID, self.TEST_KEY, "user-value")
        self.assertEqual(_cfg(self.TEST_UID, self.TEST_KEY), "user-value")

    def test_unset_falls_through_to_hardcoded_default(self):
        from claude_bot.bot_engine import _cfg
        # Unknown key not in CB_DEFAULTS → empty-string default
        self.assertEqual(_cfg(self.TEST_UID, "no_such_key_anywhere", default=None), "")
        # Explicit default arg respected
        self.assertEqual(_cfg(self.TEST_UID, "no_such_key_anywhere", default="x"), "x")


class TestGetBrokerInjectsUserKeys(unittest.TestCase):
    """_get_broker(user_id) should pass per-user keys into the broker factory."""

    TEST_UID = -44

    @classmethod
    def setUpClass(cls):
        from db import execute, IS_POSTGRES
        if IS_POSTGRES:
            execute(
                "INSERT INTO users (id, email, name) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (cls.TEST_UID, f"test{cls.TEST_UID}@example.local", "test"),
            )
        else:
            execute(
                "INSERT OR IGNORE INTO users (id, email, name) VALUES (?, ?, ?)",
                (cls.TEST_UID, f"test{cls.TEST_UID}@example.local", "test"),
            )

    @classmethod
    def tearDownClass(cls):
        from db import execute
        ph = "%s" if _is_pg() else "?"
        execute(f"DELETE FROM users WHERE id = {ph}", (cls.TEST_UID,))

    def test_alpaca_keys_injected_into_client(self):
        from shared.helpers import _upsert_api_key
        from crypto_utils import encrypt
        from claude_bot.bot_engine import _get_broker
        TEST_KEY = "PKINJECTED12345"
        TEST_SEC = "secret-injected"
        _upsert_api_key(self.TEST_UID, "alpaca", "api_key", encrypt(TEST_KEY))
        _upsert_api_key(self.TEST_UID, "alpaca", "secret_key", encrypt(TEST_SEC))
        b = _get_broker(self.TEST_UID)
        self.assertIsNotNone(b)
        self.assertEqual(type(b).__name__, "AlpacaClient")
        self.assertEqual(b._api_key, TEST_KEY)
        self.assertEqual(b._secret_key, TEST_SEC)

    def test_default_mode_routes_paper(self):
        """No cb_mode set → paper. Verifies CLAUDE.md rule #1's "paper by default"."""
        from db import execute
        from shared.helpers import _upsert_bot_config
        from claude_bot.bot_engine import _get_broker
        ph = "%s" if _is_pg() else "?"
        # Make sure cb_mode is unset
        execute(f"DELETE FROM bot_config WHERE user_id = {ph} AND key = 'cb_mode'", (self.TEST_UID,))
        b = _get_broker(self.TEST_UID)
        self.assertIsNotNone(b)
        self.assertTrue(b._paper_mode, "paper must be the default when cb_mode is unset")

    def test_live_mode_routes_live(self):
        """cb_mode='live' → AlpacaClient(paper=False). Live trading opt-in."""
        from shared.helpers import _upsert_bot_config
        from claude_bot.bot_engine import _get_broker
        _upsert_bot_config(self.TEST_UID, "cb_mode", "live")
        b = _get_broker(self.TEST_UID)
        self.assertIsNotNone(b)
        self.assertFalse(b._paper_mode, "cb_mode='live' must yield paper=False on the client")
        # Cleanup so next test isn't affected
        from db import execute
        ph = "%s" if _is_pg() else "?"
        execute(f"DELETE FROM bot_config WHERE user_id = {ph} AND key = 'cb_mode'", (self.TEST_UID,))


class TestEnsureGlobalUserIdempotent(unittest.TestCase):
    """Running _ensure_global_user multiple times must not error or duplicate."""

    def test_idempotent(self):
        from features.admin.routes import _ensure_global_user
        from db import query_one
        _ensure_global_user()
        _ensure_global_user()
        _ensure_global_user()
        u = query_one("SELECT id, email FROM users WHERE id = 0")
        self.assertIsNotNone(u)
        self.assertEqual(u["email"], "system@global.local")


def _is_pg():
    from db import IS_POSTGRES
    return IS_POSTGRES


if __name__ == "__main__":
    unittest.main(verbosity=2)
