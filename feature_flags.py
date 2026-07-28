"""Cohort feature flags — the canary-rollout mechanism.

Lets a feature be released progressively instead of all-at-once:

    off     → nobody
    admin   → admins only            (first canary ring)
    beta    → admins + users with the 'beta' role
    percent → admins + a deterministic rollout_pct% of users (stable per user)
    on      → everybody

Admins are always in the vanguard for any state except ``off`` (a flag turned
``off`` is off for everyone, so a broken feature can be killed instantly).

The percentage bucket is a stable hash of (flag, user_id): a given user keeps
the same bucket as you ramp the percentage, so nobody flips in and out.

Flags live in the ``feature_flags`` table and are cached briefly in-process.
``is_enabled`` never raises — on any error it fails closed (feature hidden).
"""

import hashlib
import logging
import threading
import time

from db import query, execute, IS_POSTGRES

logger = logging.getLogger(__name__)

P = "%s" if IS_POSTGRES else "?"
STATES = ("off", "admin", "beta", "percent", "on")

_cache = {}
_cache_ts = 0.0
_TTL = 30
_lock = threading.Lock()


def _load_all():
    rows = query("SELECT flag, state, rollout_pct FROM feature_flags") or []
    return {r["flag"]: {"state": r["state"] or "off",
                        "rollout_pct": int(r["rollout_pct"] or 0)} for r in rows}


def all_flags(force=False):
    """All flags as {flag: {state, rollout_pct}} (cached ~30s)."""
    global _cache, _cache_ts
    now = time.time()
    with _lock:
        if force or (now - _cache_ts) > _TTL or not _cache_ts:
            try:
                _cache = _load_all()
                _cache_ts = now
            except Exception as e:
                logger.debug("feature_flags load failed: %s", e)
        return dict(_cache)


def get_flag(flag):
    return all_flags().get(flag, {"state": "off", "rollout_pct": 0})


def set_flag(flag, state, rollout_pct=0):
    """Create/update a flag. state in STATES; rollout_pct clamped to 0..100."""
    if state not in STATES:
        raise ValueError(f"invalid state {state!r}; must be one of {STATES}")
    pct = max(0, min(100, int(rollout_pct or 0)))
    execute(
        f"INSERT INTO feature_flags (flag, state, rollout_pct, updated_at) "
        f"VALUES ({P}, {P}, {P}, {P}) "
        f"ON CONFLICT (flag) DO UPDATE SET state=excluded.state, "
        f"rollout_pct=excluded.rollout_pct, updated_at=excluded.updated_at",
        (flag, state, pct, _now_iso()),
    )
    all_flags(force=True)
    return {"flag": flag, "state": state, "rollout_pct": pct}


def _now_iso():
    from datetime import datetime
    return datetime.utcnow().isoformat()


def _bucket(flag, user_id):
    """Stable 0..99 bucket for (flag, user). Deterministic — no RNG — so a user
    keeps their bucket as rollout_pct ramps up."""
    h = hashlib.md5(f"{flag}:{user_id}".encode()).hexdigest()
    return int(h[:8], 16) % 100


def is_enabled(flag, user_id=None, roles=None, is_admin=None):
    """Whether ``flag`` is on for this user. Fails closed on any error."""
    try:
        cfg = get_flag(flag)
        state = cfg["state"]
        if state == "on":
            return True
        if state == "off":
            return False
        roles = roles or []
        admin = is_admin if is_admin is not None else ("admin" in roles)
        if admin:
            return True                      # admins lead every canary ring
        if state == "admin":
            return False
        if state == "beta":
            return "beta" in roles
        if state == "percent":
            if user_id is None:
                return False
            return _bucket(flag, user_id) < int(cfg["rollout_pct"])
        return False
    except Exception as e:
        logger.debug("is_enabled(%s) failed: %s", flag, e)
        return False


def enabled_map(user_id=None, roles=None, is_admin=None):
    """{flag: bool} for every known flag, resolved for this user (for the frontend)."""
    return {f: is_enabled(f, user_id=user_id, roles=roles, is_admin=is_admin)
            for f in all_flags()}


def feature_required(flag):
    """Route decorator: 404 the endpoint unless the flag is on for current_user."""
    from functools import wraps
    from flask import abort
    from flask_login import current_user

    def deco(fn):
        @wraps(fn)
        def wrapped(*a, **kw):
            uid = getattr(current_user, "id", None)
            roles = getattr(current_user, "roles", []) or []
            if not is_enabled(flag, user_id=uid, roles=roles):
                abort(404)
            return fn(*a, **kw)
        return wrapped
    return deco
