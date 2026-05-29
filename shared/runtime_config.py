"""Generic runtime settings resolver — DB-backed, env-fallback.

Designed for SaaS: any setting an admin needs to change in production lives
in `bot_config(user_id=0, key, value)` so it can be edited from the admin UI
without redeploying or touching .env. On Cloud Run, .env is not writable, so
this is the only sane place for runtime config.

Resolution precedence:
  1. DB row in bot_config (user_id=0, key=<key>)
  2. Environment variable (with optional alias list)
  3. Default supplied by caller

Companion module to [[shared-llm-config]], which uses the same precedence
pattern but specialized for LLM model role resolution.
"""

import logging
import os

from db import IS_POSTGRES, query, query_one, execute

logger = logging.getLogger(__name__)
P = "%s" if IS_POSTGRES else "?"


def get_setting(key, default=None, env_aliases=None):
    """Resolve the active value for `key`.

    `env_aliases` is a tuple of env-var names to check as a fallback. If not
    provided, the env name `key.upper()` is checked.
    """
    # 1. DB row
    try:
        row = query_one(
            f"SELECT value FROM bot_config WHERE user_id = 0 AND key = {P}",
            (key,),
        )
        if row and row["value"] not in (None, ""):
            return row["value"]
    except Exception as e:
        logger.debug(f"runtime_config DB lookup failed for {key}: {e}")

    # 2. Env vars
    candidates = env_aliases or (key.upper(),)
    for name in candidates:
        val = os.getenv(name)
        if val:
            return val

    # 3. Default
    return default


def set_setting(key, value):
    """Persist `value` for `key` in bot_config (user_id=0). Empty value deletes."""
    value = (value or "").strip() if isinstance(value, str) else value
    if value in (None, ""):
        clear_setting(key)
        return
    if IS_POSTGRES:
        execute(
            f"INSERT INTO bot_config (user_id, key, value) VALUES (0, {P}, {P}) "
            f"ON CONFLICT(user_id, key) DO UPDATE SET value = EXCLUDED.value",
            (key, str(value)),
        )
    else:
        execute(
            f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES (0, {P}, {P})",
            (key, str(value)),
        )


def clear_setting(key):
    """Delete the DB row for `key`. Resolver falls back to env / default."""
    execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {P}", (key,))


def get_all(prefix):
    """Return {key: value} for all DB-stored settings whose key starts with `prefix`."""
    rows = query(
        f"SELECT key, value FROM bot_config WHERE user_id = 0 AND key LIKE {P}",
        (f"{prefix}%",),
    )
    return {r["key"]: r["value"] for r in rows}


def get_source(key, env_aliases=None):
    """Return where the active value comes from: 'db', 'env', or 'default'."""
    try:
        row = query_one(
            f"SELECT value FROM bot_config WHERE user_id = 0 AND key = {P}",
            (key,),
        )
        if row and row["value"] not in (None, ""):
            return "db"
    except Exception:
        pass
    candidates = env_aliases or (key.upper(),)
    for name in candidates:
        if os.getenv(name):
            return "env"
    return "default"


def mask_secret(value, show_last=4):
    """Mask a secret string for display: '*****abcd' showing last `show_last` chars."""
    if not value:
        return ""
    if len(value) <= show_last:
        return "*" * len(value)
    return "*" * (len(value) - show_last) + value[-show_last:]
