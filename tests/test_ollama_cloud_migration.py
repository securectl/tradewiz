"""Tests for the Ollama Cloud migration + runtime-config resolver.

Spec:
- All Ollama traffic targets the configured URL with Bearer auth.
- crypto/stock validator gate-1 reads URL/key/model via shared.runtime_config
  (DB > env > default) so admin UI edits take effect without restart.
- claude_gating and watchdog_gating route to Ollama when the resolved role
  model is prefixed `ollama/`.
- Missing OLLAMA_API_KEY does NOT crash — falls back gracefully.

Run: docker compose exec app python -m pytest tests/test_ollama_cloud_migration.py -v
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ph():
    from db import IS_POSTGRES
    return "%s" if IS_POSTGRES else "?"


def _clear_ollama_db():
    """Wipe any DB overrides for Ollama keys so env tests are deterministic."""
    from db import execute
    ph = _ph()
    for k in ("ollama_url", "ollama_api_key", "ollama_model"):
        try:
            execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {ph}", (k,))
        except Exception:
            pass


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class TestOllamaCloudClient(unittest.TestCase):
    """crypto_bot.crypto_validator._call_ollama hits Ollama Cloud with Bearer."""

    def setUp(self):
        _clear_ollama_db()

    def tearDown(self):
        _clear_ollama_db()

    def test_sends_bearer_when_key_set(self):
        with patch.dict(os.environ, {"OLLAMA_API_KEY": "test-key-123",
                                     "OLLAMA_URL": "https://ollama.com"}, clear=False):
            from crypto_bot import crypto_validator
            fake = _FakeResponse(200, {"response": '{"execute": true, "confidence": 0.8}'})
            with patch.object(crypto_validator.requests, "post", return_value=fake) as mock_post:
                result = crypto_validator._call_ollama("test prompt")
            self.assertEqual(result.get("execute"), True)
            mock_post.assert_called_once()
            url = mock_post.call_args[0][0]
            self.assertIn("ollama.com", url)
            self.assertIn("/api/generate", url)
            self.assertEqual(mock_post.call_args[1]["headers"]["Authorization"],
                             "Bearer test-key-123")

    def test_missing_key_returns_unconfigured(self):
        with patch.dict(os.environ, {"OLLAMA_API_KEY": ""}, clear=False):
            from crypto_bot import crypto_validator
            result = crypto_validator._call_ollama("test prompt")
            self.assertEqual(result.get("error"), "ollama_cloud_unconfigured")
            self.assertIsNone(result.get("execute"))

    def test_strips_ollama_prefix_from_model_arg(self):
        with patch.dict(os.environ, {"OLLAMA_API_KEY": "k"}, clear=False):
            from crypto_bot import crypto_validator
            fake = _FakeResponse(200, {"response": '{"execute": false}'})
            with patch.object(crypto_validator.requests, "post", return_value=fake) as mock_post:
                crypto_validator._call_ollama("p", model="ollama/gpt-oss:120b")
            payload = mock_post.call_args[1]["json"]
            self.assertEqual(payload["model"], "gpt-oss:120b")

    def test_db_override_beats_env_for_api_key(self):
        """When runtime_config has a DB row for ollama_api_key, it wins over env."""
        from shared.runtime_config import set_setting
        set_setting("ollama_api_key", "db-key-from-ui")
        try:
            with patch.dict(os.environ, {"OLLAMA_API_KEY": "env-key"}, clear=False):
                from crypto_bot import crypto_validator
                fake = _FakeResponse(200, {"response": '{"execute": true}'})
                with patch.object(crypto_validator.requests, "post",
                                  return_value=fake) as mock_post:
                    crypto_validator._call_ollama("p")
                self.assertEqual(mock_post.call_args[1]["headers"]["Authorization"],
                                 "Bearer db-key-from-ui")
        finally:
            _clear_ollama_db()


class TestClaudeBotGating(unittest.TestCase):
    def setUp(self):
        _clear_ollama_db()

    def tearDown(self):
        _clear_ollama_db()

    def _eval(self):
        return {"signal": "BUY", "rsi": 40, "price": 100, "sma20": 99, "sma50": 95}

    def _screener(self):
        return {"category": "oversold", "verdict": "BUY", "screener_conf": 80,
                "days_tracked": 1, "summary": "test"}

    def test_routes_to_ollama_when_prefix_set(self):
        with patch.dict(os.environ, {"OLLAMA_API_KEY": "ck",
                                     "OLLAMA_URL": "https://ollama.com"}, clear=False):
            from claude_bot import bot_engine
            fake = _FakeResponse(200, {
                "message": {"content": '{"approve": true, "confidence": 80, "reasoning": "x"}'}
            })
            with patch("shared.llm_config.get_model", return_value="ollama/gpt-oss:20b"), \
                 patch("requests.post", return_value=fake) as mock_post:
                approved = bot_engine._llm_validate("AAPL", self._eval(), self._screener(), "NEUTRAL")
            self.assertTrue(approved)
            url = mock_post.call_args[0][0]
            self.assertIn("ollama.com", url)
            self.assertIn("/api/chat", url)
            self.assertEqual(mock_post.call_args[1]["headers"]["Authorization"], "Bearer ck")

    def test_falls_through_to_openrouter_when_no_ollama_prefix(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            from claude_bot import bot_engine
            fake = _FakeResponse(200, {
                "choices": [{"message": {"content": '{"approve": true, "confidence": 70}'}}]
            })
            with patch("shared.llm_config.get_model", return_value="deepseek/deepseek-chat"), \
                 patch("requests.post", return_value=fake) as mock_post:
                bot_engine._llm_validate("AAPL", self._eval(), self._screener(), "NEUTRAL")
            url = mock_post.call_args[0][0]
            self.assertIn("openrouter.ai", url)

    def test_no_ollama_key_returns_rule_fallback(self):
        with patch.dict(os.environ, {"OLLAMA_API_KEY": ""}, clear=False):
            from claude_bot import bot_engine
            with patch("shared.llm_config.get_model", return_value="ollama/gpt-oss:20b"), \
                 patch("requests.post") as mock_post:
                result = bot_engine._llm_validate("AAPL", self._eval(), self._screener(), "NEUTRAL")
            mock_post.assert_not_called()
            self.assertTrue(result)


class TestWatchdogGating(unittest.TestCase):
    def setUp(self):
        _clear_ollama_db()

    def tearDown(self):
        _clear_ollama_db()

    def _setup(self):
        return {"signal": "BUY", "strategy": "swing", "confidence": 70,
                "indicators": {"rsi": 40, "price": 100, "sma50": 95, "rel_volume": 1.5}}

    def _screener_info(self):
        return {"screener_verdict": "BUY", "screener_confidence": 80,
                "screener_summary": "test", "category": "oversold", "days_tracked": 2}

    def _regime(self):
        return {"regime": "BULL", "composite_score": 70}

    def test_routes_to_ollama_when_prefix_set(self):
        with patch.dict(os.environ, {"OLLAMA_API_KEY": "wk",
                                     "OLLAMA_URL": "https://ollama.com"}, clear=False):
            from features.watchdog import engine
            fake = _FakeResponse(200, {
                "message": {"content": '{"approve": true, "confidence": 75}'}
            })
            with patch("shared.llm_config.get_model", return_value="ollama/gpt-oss:20b"), \
                 patch("requests.post", return_value=fake) as mock_post:
                approved = engine._llm_vet_watchdog_candidate(
                    "AAPL", self._setup(), self._screener_info(), self._regime())
            self.assertTrue(approved)
            url = mock_post.call_args[0][0]
            self.assertIn("ollama.com", url)
            self.assertIn("/api/chat", url)

    def test_routes_to_openrouter_when_no_prefix(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or"}, clear=False):
            from features.watchdog import engine
            fake = _FakeResponse(200, {
                "choices": [{"message": {"content": '{"approve": true, "confidence": 75}'}}]
            })
            with patch("shared.llm_config.get_model", return_value="google/gemini-2.0-flash-001"), \
                 patch("requests.post", return_value=fake) as mock_post:
                engine._llm_vet_watchdog_candidate(
                    "AAPL", self._setup(), self._screener_info(), self._regime())
            url = mock_post.call_args[0][0]
            self.assertIn("openrouter.ai", url)


class TestNewDefaults(unittest.TestCase):
    def test_claude_gating_default(self):
        from shared.llm_config import DEFAULTS
        self.assertEqual(DEFAULTS["claude_gating"], "ollama/gpt-oss:20b")

    def test_watchdog_gating_default(self):
        from shared.llm_config import DEFAULTS
        self.assertEqual(DEFAULTS["watchdog_gating"], "ollama/gpt-oss:20b")


class TestStatusChecker(unittest.TestCase):
    def setUp(self):
        _clear_ollama_db()

    def tearDown(self):
        _clear_ollama_db()

    def test_service_registered(self):
        from status_checker import SERVICES
        self.assertIn("ollama_cloud", SERVICES)
        self.assertEqual(SERVICES["ollama_cloud"]["category"], "AI Model")

    def test_check_returns_degraded_when_unconfigured(self):
        from status_checker import check_ollama_cloud
        with patch.dict(os.environ, {"OLLAMA_API_KEY": ""}, clear=False):
            result = check_ollama_cloud()
        self.assertEqual(result["status"], "degraded")

    def test_check_returns_operational_on_200(self):
        from status_checker import check_ollama_cloud
        with patch.dict(os.environ, {"OLLAMA_API_KEY": "k"}, clear=False):
            fake = _FakeResponse(200, {"models": []})
            with patch("status_checker.requests.get", return_value=fake):
                result = check_ollama_cloud()
        self.assertEqual(result["status"], "operational")


if __name__ == "__main__":
    unittest.main()
