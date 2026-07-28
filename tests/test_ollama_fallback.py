"""Tests for the OpenRouter→Ollama Cloud fallback.

Covers: the enable/configured gates, try_fallback returning None when off or
unconfigured, a successful fallback returning the answer, an errored Ollama
call yielding None (so the caller keeps the original OpenRouter error), and
that the admin ollama-config surface exposes the new flag.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.ollama_fallback as ofb


class TestGates(unittest.TestCase):
    def test_disabled_by_default(self):
        with mock.patch.object(ofb, "get_setting", return_value="0"):
            self.assertFalse(ofb.fallback_enabled())

    def test_enabled_truthy_values(self):
        for v in ("1", "true", "YES", "on"):
            with mock.patch.object(ofb, "get_setting", return_value=v):
                self.assertTrue(ofb.fallback_enabled(), v)

    def test_is_configured_needs_key(self):
        with mock.patch.object(ofb, "_cfg", return_value=("https://ollama.com", "", "gpt-oss:20b")):
            self.assertFalse(ofb.is_configured())
        with mock.patch.object(ofb, "_cfg", return_value=("https://ollama.com", "sk-x", "gpt-oss:20b")):
            self.assertTrue(ofb.is_configured())


class TestTryFallback(unittest.TestCase):
    def test_none_when_disabled(self):
        with mock.patch.object(ofb, "fallback_enabled", return_value=False), \
             mock.patch.object(ofb, "is_configured", return_value=True):
            self.assertIsNone(ofb.try_fallback([{"role": "user", "content": "hi"}]))

    def test_none_when_unconfigured(self):
        with mock.patch.object(ofb, "fallback_enabled", return_value=True), \
             mock.patch.object(ofb, "is_configured", return_value=False):
            self.assertIsNone(ofb.try_fallback([{"role": "user", "content": "hi"}]))

    def test_returns_answer_on_success(self):
        with mock.patch.object(ofb, "fallback_enabled", return_value=True), \
             mock.patch.object(ofb, "is_configured", return_value=True), \
             mock.patch.object(ofb, "call_ollama_chat", return_value='{"verdict":"BUY"}'):
            out = ofb.try_fallback([{"role": "user", "content": "hi"}])
            self.assertEqual(out, '{"verdict":"BUY"}')

    def test_error_payload_yields_none(self):
        # An Ollama error payload must NOT masquerade as an answer.
        with mock.patch.object(ofb, "fallback_enabled", return_value=True), \
             mock.patch.object(ofb, "is_configured", return_value=True), \
             mock.patch.object(ofb, "call_ollama_chat",
                               return_value=json.dumps({"error": "boom"})):
            self.assertIsNone(ofb.try_fallback([{"role": "user", "content": "hi"}]))

    def test_plain_text_answer_passes_through(self):
        with mock.patch.object(ofb, "fallback_enabled", return_value=True), \
             mock.patch.object(ofb, "is_configured", return_value=True), \
             mock.patch.object(ofb, "call_ollama_chat", return_value="Just text."):
            self.assertEqual(ofb.try_fallback([{"role": "user", "content": "hi"}]), "Just text.")


class TestCallOllamaChat(unittest.TestCase):
    def test_no_key_returns_error(self):
        with mock.patch.object(ofb, "_cfg", return_value=("https://ollama.com", "", "gpt-oss:20b")):
            out = ofb.call_ollama_chat([{"role": "user", "content": "hi"}])
            self.assertIn("error", json.loads(out))

    def test_success_increments_stats(self):
        before = ofb.get_stats()["fallback_calls"]
        fake = mock.Mock()
        fake.raise_for_status = lambda: None
        fake.json = lambda: {"message": {"content": "hello"}}
        with mock.patch.object(ofb, "_cfg", return_value=("https://ollama.com", "sk-x", "gpt-oss:20b")), \
             mock.patch.object(ofb.requests, "post", return_value=fake):
            out = ofb.call_ollama_chat([{"role": "user", "content": "hi"}], reason="test")
        self.assertEqual(out, "hello")
        self.assertEqual(ofb.get_stats()["fallback_calls"], before + 1)

    def test_strips_ollama_prefix(self):
        captured = {}
        fake = mock.Mock()
        fake.raise_for_status = lambda: None
        fake.json = lambda: {"message": {"content": "ok"}}
        def _post(url, **kw):
            captured["model"] = kw["json"]["model"]
            return fake
        with mock.patch.object(ofb, "_cfg", return_value=("https://ollama.com", "sk-x", "ollama/gpt-oss:20b")), \
             mock.patch.object(ofb.requests, "post", side_effect=_post):
            ofb.call_ollama_chat([{"role": "user", "content": "hi"}])
        self.assertEqual(captured["model"], "gpt-oss:20b")


class TestAdminConfigExposesFlag(unittest.TestCase):
    def test_flag_in_ollama_keys(self):
        from features.admin import routes as admin_routes
        self.assertIn("ollama_fallback_enabled", admin_routes._OLLAMA_KEYS)
        self.assertIn("ollama_fallback_enabled", admin_routes._OLLAMA_DEFAULTS)
        self.assertEqual(admin_routes._OLLAMA_DEFAULTS["ollama_fallback_enabled"], "0")


if __name__ == "__main__":
    unittest.main()
