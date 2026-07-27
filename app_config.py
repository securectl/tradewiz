"""Central environment/production detection.

One source of truth for "are we in production" so security-sensitive defaults
(SECRET_KEY guard, Secure cookies, dev-login bypass, debug) key off an explicit
signal rather than an unrelated integration being configured.

Precedence: explicit ``APP_ENV`` / ``FLASK_ENV`` wins; otherwise we fall back to
"a DATABASE_URL is set" (the historical proxy for prod). Set ``APP_ENV=production``
in real deployments — especially any that run on SQLite without a DATABASE_URL.
"""

import os

_PROD_TOKENS = ("production", "prod")
_DEV_TOKENS = ("development", "dev", "local", "test", "testing")


def is_production():
    """True when running in production."""
    env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    if env in _PROD_TOKENS:
        return True
    if env in _DEV_TOKENS:
        return False
    # Back-compat fallback: a configured Postgres DATABASE_URL implies prod.
    return bool(os.getenv("DATABASE_URL"))


def dev_login_enabled():
    """The /auth/dev-login admin bypass may only run when NOT production.

    Never keyed on Google OAuth config (that left it live on any prod deploy
    without GOOGLE_CLIENT_ID). Enabled locally when there is no DATABASE_URL
    (SQLite dev / the test harness) or when DEV_LOGIN is explicitly set.
    """
    if is_production():
        return False
    if (os.getenv("DEV_LOGIN") or "").strip().lower() in ("1", "true", "yes"):
        return True
    return not os.getenv("DATABASE_URL")


def debug_enabled():
    """Werkzeug debugger only when explicitly opted in and not production."""
    if is_production():
        return False
    return (os.getenv("FLASK_DEBUG") or "").strip() in ("1", "true", "yes")
