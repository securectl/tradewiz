"""Global (not per-user) key/value settings — e.g. admin-editable plan pricing.

Small cached KV over the ``app_settings`` table. Reads fall back to a default so
callers work before anything is configured.
"""

import logging
import threading
import time

from db import query, query_one, execute, IS_POSTGRES

logger = logging.getLogger(__name__)
P = "%s" if IS_POSTGRES else "?"

_cache = {}
_ts = 0.0
_TTL = 30
_lock = threading.Lock()


def _load():
    rows = query("SELECT key, value FROM app_settings") or []
    return {r["key"]: r["value"] for r in rows}


def all_settings(force=False):
    global _cache, _ts
    now = time.time()
    with _lock:
        if force or (now - _ts) > _TTL or not _ts:
            try:
                _cache = _load()
                _ts = now
            except Exception as e:
                logger.debug("app_settings load failed: %s", e)
        return dict(_cache)


def get_setting(key, default=None):
    val = all_settings().get(key)
    return val if val is not None else default


def set_setting(key, value):
    execute(
        f"INSERT INTO app_settings (key, value) VALUES ({P}, {P}) "
        f"ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    all_settings(force=True)
