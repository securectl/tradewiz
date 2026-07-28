"""Lightweight in-process TTL cache for whole LLM verdicts.

The expensive part of every analysis is the LLM fan-out (3-7 calls per ticker
for setup/12-month, one call per candidate for the screener). Re-running that
fan-out for the same ticker within a short window wastes tokens. This module
memoizes the *finished verdict* so a repeat request inside the TTL skips the
LLM calls entirely.

Mirrors the existing inline ``_cache`` dicts in ``market_sensor.py`` and
``screener._options_cache`` but centralized + unit-testable. Process-local —
production runs single-worker gunicorn (see memory: project_sector_radar), so
one cache per container is the correct scope. Errors are never cached so a
transient LLM failure does not get pinned for the whole TTL.
"""

import time
import threading

_lock = threading.Lock()
_store = {}  # key -> (expires_at_epoch, value)


def get(key):
    """Return the cached value for ``key`` or None if missing/expired."""
    with _lock:
        entry = _store.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if expires_at < time.time():
            _store.pop(key, None)
            return None
        return value


def put(key, value, ttl):
    """Cache ``value`` under ``key`` for ``ttl`` seconds."""
    with _lock:
        _store[key] = (time.time() + ttl, value)


def clear():
    """Drop all cached entries (used by tests)."""
    with _lock:
        _store.clear()


def stats():
    """Return (live_entries, total_entries) — for diagnostics."""
    now = time.time()
    with _lock:
        total = len(_store)
        live = sum(1 for exp, _ in _store.values() if exp >= now)
    return live, total


def _is_error(value):
    """A verdict we should NOT cache (transient failure / unconfigured)."""
    if not isinstance(value, dict):
        return False
    if value.get("error"):
        return True
    # Setup/12-month return configured=False when the key is missing.
    if value.get("configured") is False:
        return True
    return False


def cached_call(key, ttl, producer, force=False):
    """Return a cached verdict for ``key`` or run ``producer()`` and cache it.

    ``producer`` is a zero-arg callable that returns the verdict dict. When
    ``force`` is True the cache is bypassed and refreshed. Error/unconfigured
    verdicts are returned but never cached.
    """
    if not force:
        hit = get(key)
        if hit is not None:
            return hit
    value = producer()
    if not _is_error(value):
        put(key, value, ttl)
    return value
