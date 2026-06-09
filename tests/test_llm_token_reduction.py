"""
Tests for the LLM token-reduction changes:
  - shared/llm_cache.py TTL verdict cache
  - Anthropic cache_control breakpoint injection (_with_prompt_cache)
  - validate_setup / predict_12month verdict memoization
  - 12-month supervisor fan-out gating on base-model consensus
  - bot-validator / global max_tokens trim

Run: python -m pytest tests/test_llm_token_reduction.py -v
  or: python tests/test_llm_token_reduction.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import unittest

from shared import llm_cache


class TestLlmCache(unittest.TestCase):
    def setUp(self):
        llm_cache.clear()

    def test_put_get_roundtrip(self):
        llm_cache.put("k", {"v": 1}, ttl=10)
        self.assertEqual(llm_cache.get("k"), {"v": 1})

    def test_miss_returns_none(self):
        self.assertIsNone(llm_cache.get("nope"))

    def test_expiry(self):
        llm_cache.put("k", {"v": 1}, ttl=0.05)
        self.assertIsNotNone(llm_cache.get("k"))
        time.sleep(0.08)
        self.assertIsNone(llm_cache.get("k"))

    def test_cached_call_memoizes(self):
        calls = {"n": 0}

        def producer():
            calls["n"] += 1
            return {"verdict": "BULLISH"}

        first = llm_cache.cached_call("key", 10, producer)
        second = llm_cache.cached_call("key", 10, producer)
        self.assertEqual(first, second)
        self.assertEqual(calls["n"], 1)  # producer ran once

    def test_cached_call_force_bypasses(self):
        calls = {"n": 0}

        def producer():
            calls["n"] += 1
            return {"verdict": "BULLISH"}

        llm_cache.cached_call("key", 10, producer)
        llm_cache.cached_call("key", 10, producer, force=True)
        self.assertEqual(calls["n"], 2)

    def test_errors_not_cached(self):
        calls = {"n": 0}

        def producer():
            calls["n"] += 1
            return {"error": "boom"}

        llm_cache.cached_call("key", 10, producer)
        llm_cache.cached_call("key", 10, producer)
        self.assertEqual(calls["n"], 2)  # ran both times — error never pinned

    def test_unconfigured_not_cached(self):
        calls = {"n": 0}

        def producer():
            calls["n"] += 1
            return {"configured": False}

        llm_cache.cached_call("key", 10, producer)
        llm_cache.cached_call("key", 10, producer)
        self.assertEqual(calls["n"], 2)


class TestPromptCacheBreakpoint(unittest.TestCase):
    def test_anthropic_gets_breakpoint(self):
        from ai_validator import _with_prompt_cache
        msgs = [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ]
        out = _with_prompt_cache("anthropic/claude-sonnet-4-6", msgs)
        sys_content = out[0]["content"]
        self.assertIsInstance(sys_content, list)
        self.assertEqual(sys_content[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(sys_content[0]["text"], "be terse")
        # user message untouched
        self.assertEqual(out[1], {"role": "user", "content": "hi"})

    def test_non_anthropic_is_noop(self):
        from ai_validator import _with_prompt_cache
        msgs = [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ]
        out = _with_prompt_cache("google/gemini-2.5-flash", msgs)
        self.assertEqual(out, msgs)  # unchanged

    def test_only_first_system_marked(self):
        from ai_validator import _with_prompt_cache
        msgs = [
            {"role": "system", "content": "a"},
            {"role": "system", "content": "b"},
        ]
        out = _with_prompt_cache("anthropic/claude-opus-4-8", msgs)
        self.assertIsInstance(out[0]["content"], list)
        self.assertIsInstance(out[1]["content"], str)  # second left alone


class TestBaseModelConsensus(unittest.TestCase):
    def test_unanimous_agree(self):
        from ai_validator import _base_models_agree
        a = {"verdict": "INVEST"}
        self.assertTrue(_base_models_agree(a, dict(a), dict(a)))

    def test_disagreement(self):
        from ai_validator import _base_models_agree
        self.assertFalse(_base_models_agree(
            {"verdict": "INVEST"}, {"verdict": "PASS"}, {"verdict": "INVEST"}))

    def test_error_forces_supervisor(self):
        from ai_validator import _base_models_agree
        self.assertFalse(_base_models_agree(
            {"verdict": "INVEST"}, {"error": "x"}, {"verdict": "INVEST"}))


class TestNumTrim(unittest.TestCase):
    def test_rounds_floats(self):
        from ai_validator import _num
        self.assertEqual(_num(123.456789012), 123.46)

    def test_leaves_strings(self):
        from ai_validator import _num
        self.assertEqual(_num("N/A"), "N/A")
        self.assertIsNone(_num(None))

    def test_leaves_bool(self):
        from ai_validator import _num
        self.assertIs(_num(True), True)


class TestMaxTokensTrim(unittest.TestCase):
    def test_global_default_lowered(self):
        import ai_validator
        # default trimmed from 2048 -> 1024 (env may override in prod)
        self.assertLessEqual(ai_validator.LLM_MAX_TOKENS, 1024)


class TestSetupCaching(unittest.TestCase):
    """validate_setup memoizes the 3-model fan-out per ticker."""

    def setUp(self):
        llm_cache.clear()
        import ai_validator
        self.av = ai_validator
        self._orig = (ai_validator.is_configured,
                      ai_validator._validate_research,
                      ai_validator._validate_pattern,
                      ai_validator._validate_prediction)
        self.calls = {"n": 0}

        ai_validator.is_configured = lambda: True

        def _research(summary, ticker, fast_mode=False):
            self.calls["n"] += 1
            return {"verdict": "BULLISH", "confidence": 70}

        ai_validator._validate_research = _research
        ai_validator._validate_pattern = lambda s, t: {"pattern_valid": True, "pattern_confidence": 70}
        ai_validator._validate_prediction = lambda s, t: {"trade_verdict": "TAKE", "risk_score": 30, "overall_probability": 70}

    def tearDown(self):
        (self.av.is_configured, self.av._validate_research,
         self.av._validate_pattern, self.av._validate_prediction) = self._orig
        llm_cache.clear()

    def _analysis(self):
        return {"ticker": "TEST", "current_price": 10.0, "change": 0.1,
                "change_pct": 1.0, "info": {}, "indicators": {},
                "breakout_status": {}}

    def test_second_call_hits_cache(self):
        r1 = self.av.validate_setup(self._analysis())
        r2 = self.av.validate_setup(self._analysis())
        self.assertEqual(self.calls["n"], 1)  # fan-out ran once
        self.assertEqual(r1["verdict"]["final_verdict"], r2["verdict"]["final_verdict"])

    def test_force_refresh_bypasses(self):
        self.av.validate_setup(self._analysis())
        self.av.validate_setup(self._analysis(), force_refresh=True)
        self.assertEqual(self.calls["n"], 2)


class TestSupervisorGating(unittest.TestCase):
    """predict_12month skips the supervisor LLM when base models agree."""

    def setUp(self):
        llm_cache.clear()
        import ai_validator
        self.av = ai_validator
        self._orig = (ai_validator.is_configured, ai_validator._gather_facts,
                      ai_validator._evaluate_company_health,
                      ai_validator._evaluate_price_action,
                      ai_validator._supervisor_review)
        self.sup_calls = {"n": 0}
        ai_validator.is_configured = lambda: True
        ai_validator._build_fundamentals_summary = lambda f: "funds"
        ai_validator._build_price_action_summary = lambda i, d: "price"

        def _sup(facts, health, price, ticker, fast_mode=False):
            self.sup_calls["n"] += 1
            return {"verdict": "INVEST", "confidence": 70}

        ai_validator._supervisor_review = _sup

    def tearDown(self):
        (self.av.is_configured, self.av._gather_facts,
         self.av._evaluate_company_health, self.av._evaluate_price_action,
         self.av._supervisor_review) = self._orig
        llm_cache.clear()

    def _set_base(self, fv, hv, pv):
        self.av._gather_facts = lambda f, p, t, fast=False: {"verdict": fv, "confidence": 70}
        self.av._evaluate_company_health = lambda f, fo, t, fast=False: {"verdict": hv, "confidence": 70, "survival_probability": 90}
        self.av._evaluate_price_action = lambda p, f, fo, t: {"verdict": pv, "confidence": 70, "price_targets": {}}

    def test_skips_supervisor_on_consensus(self):
        self._set_base("INVEST", "INVEST", "INVEST")
        res = self.av.predict_12month({"ticker": "TEST"})
        self.assertEqual(self.sup_calls["n"], 0)
        self.assertTrue(res["supervisor"].get("skipped"))

    def test_runs_supervisor_on_disagreement(self):
        self._set_base("INVEST", "PASS", "INVEST")
        self.av.predict_12month({"ticker": "TEST2"}, force_refresh=True)
        self.assertEqual(self.sup_calls["n"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
