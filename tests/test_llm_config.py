"""Tests for the runtime LLM model resolver + snapshot/revert system.

User spec (May 2026): need a way to swap to cheaper/free models from the
admin UI and one-click revert if results regress. This requires:
  1. A resolver where DB override > env > default
  2. Snapshots of the current set, restorable atomically
  3. Admin endpoints behind admin_required
  4. Call sites that actually consult the resolver

Run: docker compose exec app python -m pytest tests/test_llm_config.py -v
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ph():
    from db import IS_POSTGRES
    return "%s" if IS_POSTGRES else "?"


class TestResolverPrecedence(unittest.TestCase):
    """DB override beats env, env beats default."""

    TEST_ROLE = "research"  # arbitrary KNOWN_ROLES entry

    def setUp(self):
        from db import execute
        ph = _ph()
        # Clean any pre-existing override for this role
        execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {ph}",
                (f"llm_{self.TEST_ROLE}",))

    def tearDown(self):
        from db import execute
        ph = _ph()
        execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {ph}",
                (f"llm_{self.TEST_ROLE}",))

    def test_falls_back_to_default_when_no_override_no_env(self):
        from shared.llm_config import get_model
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_RESEARCH", None)
            self.assertEqual(get_model(self.TEST_ROLE, "fallback/model"), "fallback/model")

    def test_env_var_beats_default(self):
        from shared.llm_config import get_model
        with patch.dict(os.environ, {"LLM_RESEARCH": "env/model"}):
            self.assertEqual(get_model(self.TEST_ROLE, "fallback/model"), "env/model")

    def test_db_override_beats_env(self):
        from shared.llm_config import get_model, set_override
        set_override(self.TEST_ROLE, "db/override-model")
        try:
            with patch.dict(os.environ, {"LLM_RESEARCH": "env/model"}):
                self.assertEqual(get_model(self.TEST_ROLE, "fallback/model"),
                                 "db/override-model")
        finally:
            set_override(self.TEST_ROLE, "")  # clear

    def test_clearing_override_falls_back(self):
        from shared.llm_config import get_model, set_override
        set_override(self.TEST_ROLE, "x/y")
        set_override(self.TEST_ROLE, "")  # clear via empty
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_RESEARCH", None)
            self.assertEqual(get_model(self.TEST_ROLE, "default"), "default")


class TestSnapshotRoundTrip(unittest.TestCase):
    """Save current → mutate → revert → matches saved."""

    def setUp(self):
        from db import execute
        ph = _ph()
        # Clean test data
        execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key LIKE 'llm_%%'")
        execute(f"DELETE FROM llm_snapshots WHERE label LIKE {ph}", ("ZZTEST_%",))

    def tearDown(self):
        from db import execute
        ph = _ph()
        execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key LIKE 'llm_%%'")
        execute(f"DELETE FROM llm_snapshots WHERE label LIKE {ph}", ("ZZTEST_%",))

    def test_save_then_revert_round_trip(self):
        from shared.llm_config import set_override, save_snapshot, revert_to_snapshot, get_model
        # Establish baseline state: research=A, pattern=B
        set_override("research", "model-A")
        set_override("pattern", "model-B")
        snap_id = save_snapshot("ZZTEST_baseline")
        self.assertIsNotNone(snap_id)

        # Mutate: switch to candidate models
        set_override("research", "candidate-X")
        set_override("pattern", "candidate-Y")
        self.assertEqual(get_model("research", "default"), "candidate-X")
        self.assertEqual(get_model("pattern", "default"), "candidate-Y")

        # Revert
        revert_to_snapshot(snap_id)
        self.assertEqual(get_model("research", "default"), "model-A")
        self.assertEqual(get_model("pattern", "default"), "model-B")

    def test_revert_clears_overrides_not_in_snapshot(self):
        """If a role wasn't overridden when the snapshot was taken, revert
        should leave it at env/default — not preserve a later override."""
        from shared.llm_config import set_override, save_snapshot, revert_to_snapshot, get_model, KNOWN_ROLES
        # Snapshot with no overrides set
        snap_id = save_snapshot("ZZTEST_clean")
        # Now add an override
        set_override("research", "later-addition")
        self.assertEqual(get_model("research", "default"), "later-addition")
        # Revert — research should drop back to default
        revert_to_snapshot(snap_id)
        # Whatever the snapshot captured for research is what we get; if it
        # captured the env/default, and that's still the env/default, the
        # override should be cleared.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_RESEARCH", None)
            # The snapshot saved current=DEFAULTS["research"], so revert sets
            # no override (because matching env/default), so we should get default.
            self.assertNotEqual(get_model("research", "test-default"), "later-addition")

    def test_clear_all_wipes_overrides(self):
        from shared.llm_config import set_override, clear_all_overrides, get_all_models
        set_override("research", "x")
        set_override("pattern", "y")
        clear_all_overrides()
        models = get_all_models()
        self.assertFalse(models["research"]["override_set"])
        self.assertFalse(models["pattern"]["override_set"])


class TestCallSitesRoutedThroughResolver(unittest.TestCase):
    """Source-level: ensure the actual gating call sites pass role= so DB
    overrides take effect. If these break, someone refactored the wrappers."""

    def test_ai_validator_research_passes_role(self):
        import inspect
        from ai_validator import _validate_research, _gather_facts
        for fn in (_validate_research, _gather_facts):
            src = inspect.getsource(fn)
            self.assertIn("role=", src,
                f"{fn.__name__} must pass role= to _call_openrouter")

    def test_crypto_validator_passes_role(self):
        import inspect
        from crypto_bot import crypto_validator
        for name in ("_validate_sentiment", "_validate_risk"):
            fn = getattr(crypto_validator, name, None)
            if fn is None:
                continue
            src = inspect.getsource(fn)
            self.assertIn("role=", src, f"{name} must pass role= to _call_openrouter")

    def test_watchdog_uses_resolver(self):
        import inspect
        from features.watchdog import engine
        src = inspect.getsource(engine._llm_vet_watchdog_candidate)
        self.assertIn('get_model("watchdog_gating"', src,
            "Watchdog gating must consult the resolver")

    def test_claude_bot_uses_resolver(self):
        import inspect
        from claude_bot import bot_engine
        src = inspect.getsource(bot_engine._llm_validate)
        self.assertIn('get_model("claude_gating"', src,
            "Claude bot gating must consult the resolver")


class TestAdminEndpointsRequireAdmin(unittest.TestCase):
    """The new endpoints must be behind @admin_required. Source-level check."""

    def test_endpoints_decorated(self):
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        src = (repo / "features" / "admin" / "routes.py").read_text()
        for path in ["/api/admin/llm-models",
                     "/api/admin/llm-models/set",
                     "/api/admin/llm-models/snapshot",
                     "/api/admin/llm-models/revert",
                     "/api/admin/llm-models/clear"]:
            # Find the route definition and verify @admin_required appears in
            # the same block. Simple text sanity check, not a real ACL test.
            idx = src.find(path)
            self.assertGreater(idx, -1, f"Route {path} not declared")
            window = src[max(0, idx - 200): idx + 200]
            self.assertIn("@admin_required", window,
                f"Route {path} must be admin-only")


class TestKnownRoles(unittest.TestCase):
    """All roles the resolver knows about must have a default; known role list
    must include the ones actually used in the codebase."""

    def test_all_known_roles_have_defaults(self):
        from shared.llm_config import KNOWN_ROLES, DEFAULTS
        for role in KNOWN_ROLES:
            self.assertIn(role, DEFAULTS,
                f"Role {role} declared in KNOWN_ROLES but missing from DEFAULTS")

    def test_critical_roles_present(self):
        from shared.llm_config import KNOWN_ROLES
        for role in ("bot_sentiment", "bot_risk", "watchdog_gating",
                     "claude_gating", "screener", "research"):
            self.assertIn(role, KNOWN_ROLES,
                f"Critical role {role} missing from KNOWN_ROLES")


if __name__ == "__main__":
    unittest.main(verbosity=2)
