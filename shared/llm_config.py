"""Runtime-overridable LLM model resolver.

Resolution precedence for any role:
  1. DB override — bot_config(user_id=0, key=f"llm_{role}")
  2. Env var      — os.getenv(f"LLM_{role.upper()}")
  3. Hardcoded default passed by the caller

The DB override exists so the admin UI can swap to cheaper/free models without
a redeploy and revert atomically. Snapshots (saved + restored from
llm_snapshots table) capture the full set of overrides at a point in time so
"revert to previous version" is one click.

Roles in use (May 2026):
  research          — ai_validator deep research / fact gather (default sonnet)
  research_fast     — ai_validator fast mode (default gemini-flash)
  pattern           — ai_validator pattern detection (default gemini-pro)
  prediction        — ai_validator prediction layer (default deepseek)
  screener          — screener.py vetting (default gemini-flash)
  supervisor        — optional supervisor gate (default unset)
  bot_sentiment     — crypto/stock validator gate 2 (default gemini-flash)
  bot_risk          — crypto/stock validator gate 3 (default deepseek)
  watchdog_gating   — _llm_vet_watchdog_candidate (default ollama/gpt-oss:20b)
  claude_gating     — claude_bot._llm_validate (default ollama/gpt-oss:20b)
  forecast_opus     — trump_forecaster (default opus)
"""
import json
import logging
import os
from datetime import datetime

from db import IS_POSTGRES, query, query_one, execute

logger = logging.getLogger(__name__)
P = "%s" if IS_POSTGRES else "?"

# All roles the admin UI knows about. Anything not in this list won't show up
# in the override panel but can still be set via env var.
KNOWN_ROLES = (
    "research",
    "research_fast",
    "pattern",
    "prediction",
    "screener",
    "supervisor",
    "bot_sentiment",
    "bot_risk",
    "watchdog_gating",
    "claude_gating",
    "forecast_opus",
    "sector_research",       # Sector Radar daily thesis — Sonnet 4.6 default
    "sector_research_deep",  # Sector Radar weekly deep-dive — Opus default
)

# Hardcoded fallbacks — must match the constants in their original modules so
# behavior is identical when no override and no env var are set.
#
# Routing convention: a model string prefixed `ollama/` routes the call to
# Ollama Cloud (https://ollama.com) with Bearer auth; anything else uses
# OpenRouter. Gating/limiter tier defaults to Ollama Cloud per the project
# subscription (see memory: project_ollama_cloud.md).
DEFAULTS = {
    "research":        "anthropic/claude-sonnet-4-6",
    "research_fast":   "google/gemini-2.5-flash",
    "pattern":         "google/gemini-2.5-pro-preview",
    "prediction":      "deepseek/deepseek-chat-v3-0324",
    "screener":        "google/gemini-2.5-flash",
    "supervisor":      "",
    "bot_sentiment":   "google/gemini-2.5-flash",
    "bot_risk":        "deepseek/deepseek-chat-v3-0324",
    "watchdog_gating": "ollama/gpt-oss:20b",
    "claude_gating":   "ollama/gpt-oss:20b",
    "forecast_opus":   "anthropic/claude-opus-4-6",
    "sector_research":      "anthropic/claude-sonnet-4-6",
    "sector_research_deep": "anthropic/claude-opus-4-6",
}


def get_model(role, default=None):
    """Resolve the active model for a role. See module docstring for precedence."""
    # 1. DB override
    try:
        row = query_one(
            f"SELECT value FROM bot_config WHERE user_id = 0 AND key = {P}",
            (f"llm_{role}",),
        )
        if row and row["value"]:
            return row["value"]
    except Exception as e:
        # bot_config table missing or schema drift — fall through to env/default
        logger.debug(f"llm_config DB lookup failed for {role}: {e}")

    # 2. Env var
    env_val = os.getenv(f"LLM_{role.upper()}")
    if env_val:
        return env_val

    # 3. Caller-supplied default, then module DEFAULTS
    return default if default is not None else DEFAULTS.get(role, "")


def get_all_models():
    """Return {role: {current, env, default, override_set}} for the admin UI."""
    out = {}
    for role in KNOWN_ROLES:
        env_val = os.getenv(f"LLM_{role.upper()}", "")
        try:
            row = query_one(
                f"SELECT value FROM bot_config WHERE user_id = 0 AND key = {P}",
                (f"llm_{role}",),
            )
            override = row["value"] if row else ""
        except Exception:
            override = ""
        current = override or env_val or DEFAULTS.get(role, "")
        out[role] = {
            "current":      current,
            "override":     override,
            "env":          env_val,
            "default":      DEFAULTS.get(role, ""),
            "override_set": bool(override),
        }
    return out


def set_override(role, model):
    """Persist a DB override for `role`. Empty string clears it."""
    if role not in KNOWN_ROLES:
        raise ValueError(f"Unknown LLM role: {role}")
    key = f"llm_{role}"
    model = (model or "").strip()
    if not model:
        # Clearing the override
        execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {P}", (key,))
        return
    # Upsert
    if IS_POSTGRES:
        execute(
            f"INSERT INTO bot_config (user_id, key, value) VALUES (0, {P}, {P}) "
            f"ON CONFLICT(user_id, key) DO UPDATE SET value = EXCLUDED.value",
            (key, model),
        )
    else:
        execute(
            f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES (0, {P}, {P})",
            (key, model),
        )


def clear_all_overrides():
    """Wipe every llm_* override row. Roles fall back to env / default."""
    keys = tuple(f"llm_{r}" for r in KNOWN_ROLES)
    placeholders = ",".join([P] * len(keys))
    execute(
        f"DELETE FROM bot_config WHERE user_id = 0 AND key IN ({placeholders})",
        keys,
    )


def save_snapshot(label):
    """Persist the current set of overrides as a named snapshot."""
    label = (label or f"snapshot_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}").strip()
    snapshot = {role: get_all_models()[role]["current"] for role in KNOWN_ROLES}
    payload = json.dumps(snapshot)
    if IS_POSTGRES:
        return execute(
            f"INSERT INTO llm_snapshots (label, models_json) VALUES ({P}, {P}) RETURNING id",
            (label, payload),
        )
    return execute(
        f"INSERT INTO llm_snapshots (label, models_json) VALUES ({P}, {P})",
        (label, payload),
    )


def list_snapshots(limit=20):
    rows = query(
        f"SELECT id, label, created_at, models_json FROM llm_snapshots "
        f"ORDER BY id DESC LIMIT {int(limit)}"
    )
    out = []
    for r in rows or []:
        try:
            models = json.loads(r["models_json"]) if r.get("models_json") else {}
        except Exception:
            models = {}
        out.append({
            "id": r["id"],
            "label": r["label"],
            "created_at": str(r.get("created_at") or ""),
            "models": models,
        })
    return out


def revert_to_snapshot(snapshot_id):
    """Apply every model in the snapshot as a DB override. Roles missing from
    the snapshot are cleared (so they fall back to env/default)."""
    row = query_one(
        f"SELECT models_json FROM llm_snapshots WHERE id = {P}",
        (int(snapshot_id),),
    )
    if not row:
        raise ValueError(f"Snapshot {snapshot_id} not found")
    try:
        models = json.loads(row["models_json"]) if row.get("models_json") else {}
    except Exception:
        raise ValueError("Snapshot has invalid JSON")
    # Clear all first, then re-apply only the roles present in the snapshot
    clear_all_overrides()
    for role, model in (models or {}).items():
        if role in KNOWN_ROLES and model:
            # Don't write an override if the snapshot value matches what env/default
            # would already give us — keeps the override list minimal.
            env_val = os.getenv(f"LLM_{role.upper()}", "")
            default = DEFAULTS.get(role, "")
            if model != (env_val or default):
                set_override(role, model)
    return True


def delete_snapshot(snapshot_id):
    execute(f"DELETE FROM llm_snapshots WHERE id = {P}", (int(snapshot_id),))
