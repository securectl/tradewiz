"""Tests for shared.runtime_config — DB-backed settings resolver.

Run: docker compose exec app python -m pytest tests/test_runtime_config.py -v
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ph():
    from db import IS_POSTGRES
    return "%s" if IS_POSTGRES else "?"


TEST_KEY = "rt_cfg_unit_test_key"
TEST_KEY_2 = "rt_cfg_unit_test_key2"


class TestResolverPrecedence(unittest.TestCase):
    """DB > env > default."""

    def setUp(self):
        from db import execute
        ph = _ph()
        execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {ph}", (TEST_KEY,))
        execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {ph}", (TEST_KEY_2,))

    def tearDown(self):
        from db import execute
        ph = _ph()
        execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {ph}", (TEST_KEY,))
        execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {ph}", (TEST_KEY_2,))

    def test_falls_back_to_default(self):
        from shared.runtime_config import get_setting
        env_name = TEST_KEY.upper()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(env_name, None)
            self.assertEqual(get_setting(TEST_KEY, "fallback"), "fallback")

    def test_env_beats_default(self):
        from shared.runtime_config import get_setting
        env_name = TEST_KEY.upper()
        with patch.dict(os.environ, {env_name: "from-env"}, clear=False):
            self.assertEqual(get_setting(TEST_KEY, "fallback"), "from-env")

    def test_db_beats_env(self):
        from shared.runtime_config import get_setting, set_setting
        env_name = TEST_KEY.upper()
        set_setting(TEST_KEY, "from-db")
        with patch.dict(os.environ, {env_name: "from-env"}, clear=False):
            self.assertEqual(get_setting(TEST_KEY, "fallback"), "from-db")

    def test_env_aliases(self):
        from shared.runtime_config import get_setting
        with patch.dict(os.environ, {"CUSTOM_ALIAS": "via-alias"}, clear=False):
            self.assertEqual(
                get_setting(TEST_KEY, "fallback", env_aliases=("CUSTOM_ALIAS",)),
                "via-alias",
            )

    def test_set_then_clear(self):
        from shared.runtime_config import get_setting, set_setting, clear_setting
        set_setting(TEST_KEY, "x")
        self.assertEqual(get_setting(TEST_KEY, "d"), "x")
        clear_setting(TEST_KEY)
        # After clear, env-less → default
        env_name = TEST_KEY.upper()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(env_name, None)
            self.assertEqual(get_setting(TEST_KEY, "d"), "d")

    def test_set_empty_string_clears(self):
        from shared.runtime_config import get_setting, set_setting
        set_setting(TEST_KEY, "x")
        set_setting(TEST_KEY, "")  # empty clears
        env_name = TEST_KEY.upper()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(env_name, None)
            self.assertEqual(get_setting(TEST_KEY, "d"), "d")

    def test_get_source(self):
        from shared.runtime_config import set_setting, get_source
        env_name = TEST_KEY.upper()
        # default
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(env_name, None)
            self.assertEqual(get_source(TEST_KEY), "default")
        # env
        with patch.dict(os.environ, {env_name: "x"}, clear=False):
            self.assertEqual(get_source(TEST_KEY), "env")
        # db (clear env to make sure DB-only wins)
        set_setting(TEST_KEY, "y")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(env_name, None)
            self.assertEqual(get_source(TEST_KEY), "db")

    def test_get_all_prefix(self):
        from shared.runtime_config import set_setting, get_all
        set_setting(TEST_KEY, "a")
        set_setting(TEST_KEY_2, "b")
        out = get_all(TEST_KEY)
        self.assertEqual(out.get(TEST_KEY), "a")
        self.assertEqual(out.get(TEST_KEY_2), "b")


class TestMaskSecret(unittest.TestCase):

    def test_mask_short(self):
        from shared.runtime_config import mask_secret
        self.assertEqual(mask_secret("abc"), "***")

    def test_mask_shows_last_n(self):
        from shared.runtime_config import mask_secret
        self.assertEqual(mask_secret("abcdef12", show_last=4), "****ef12")

    def test_mask_empty(self):
        from shared.runtime_config import mask_secret
        self.assertEqual(mask_secret(""), "")
        self.assertEqual(mask_secret(None), "")


if __name__ == "__main__":
    unittest.main()
