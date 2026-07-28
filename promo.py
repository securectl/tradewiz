"""Promo / access codes — admin-generated codes that grant free access.

An admin generates random codes (e.g. to share for traffic/launch); a user
plugs a code in and gets the code's tier free for N days. Grants ride on the
existing trial fields in ``user_subscriptions`` so the daily trial-expiry job
reverts them automatically when they lapse.

Redemption is idempotent per user (one code per user) and bounded by ``max_uses``.
"""

import logging
import secrets
import string
from datetime import datetime, timedelta

from db import query, query_one, execute, IS_POSTGRES

logger = logging.getLogger(__name__)
P = "%s" if IS_POSTGRES else "?"

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I
VALID_TIERS = ("free", "starter", "pro")


def generate_code(length=8, prefix=""):
    """A random, human-friendly code like 'TW-7KQ4M2XP'."""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return (prefix + body) if prefix else body


def create_codes(tier="starter", days=30, max_uses=1, expires_days=90,
                 quantity=1, created_by=None, prefix="TW-"):
    """Generate ``quantity`` unique codes. Returns the list of code strings."""
    tier = tier if tier in VALID_TIERS else "starter"
    days = max(1, int(days))
    max_uses = max(1, int(max_uses))
    now = datetime.utcnow()
    code_expiry = (now + timedelta(days=int(expires_days))).isoformat() if expires_days else None
    made = []
    for _ in range(max(1, min(200, int(quantity)))):
        for _attempt in range(6):                      # retry on the rare collision
            code = generate_code(prefix=prefix)
            try:
                execute(
                    f"INSERT INTO promo_codes (code, tier, days, max_uses, used_count, "
                    f"active, expires_at, created_by, created_at) "
                    f"VALUES ({P},{P},{P},{P},0,{P},{P},{P},{P})",
                    (code, tier, days, max_uses, True if IS_POSTGRES else 1,
                     code_expiry, created_by, now.isoformat()),
                )
                made.append(code)
                break
            except Exception:
                continue
    return made


def list_codes(limit=200):
    """All codes (newest first) with usage, for the admin table."""
    rows = query(
        f"SELECT code, tier, days, max_uses, used_count, active, expires_at, created_at "
        f"FROM promo_codes ORDER BY created_at DESC LIMIT {P}", (limit,)
    ) or []
    out = []
    for r in rows:
        out.append({
            "code": r["code"], "tier": r["tier"], "days": int(r["days"] or 0),
            "max_uses": int(r["max_uses"] or 0), "used_count": int(r["used_count"] or 0),
            "active": bool(r["active"]),
            "expires_at": str(r["expires_at"])[:10] if r.get("expires_at") else None,
            "created_at": str(r["created_at"])[:10] if r.get("created_at") else None,
        })
    return out


def set_active(code, active):
    execute(f"UPDATE promo_codes SET active = {P} WHERE code = {P}",
            (bool(active) if IS_POSTGRES else (1 if active else 0), code.strip().upper()))


def code_is_valid(code):
    """True if ``code`` is currently redeemable by *someone* (active, unexpired,
    uses remaining). Per-user redemption limits aren't checked here — used to gate
    signup before the account exists."""
    code = (code or "").strip().upper()
    if not code:
        return False
    try:
        row = query_one(
            f"SELECT active, expires_at, used_count, max_uses FROM promo_codes WHERE code = {P}",
            (code,),
        )
        if not row or not bool(row["active"]):
            return False
        if row.get("expires_at") and str(row["expires_at"]) < datetime.utcnow().isoformat():
            return False
        if int(row["used_count"] or 0) >= int(row["max_uses"] or 0):
            return False
        return True
    except Exception:
        return False


def redeem_code(user_id, code):
    """Redeem ``code`` for ``user_id``. Returns (ok, message, info)."""
    code = (code or "").strip().upper()
    if not code:
        return False, "Enter a code.", None
    row = query_one(
        f"SELECT code, tier, days, max_uses, used_count, active, expires_at "
        f"FROM promo_codes WHERE code = {P}", (code,)
    )
    if not row:
        return False, "That code isn't valid.", None
    if not bool(row["active"]):
        return False, "That code is no longer active.", None
    if row.get("expires_at") and str(row["expires_at"]) < datetime.utcnow().isoformat():
        return False, "That code has expired.", None
    if int(row["used_count"] or 0) >= int(row["max_uses"] or 0):
        return False, "That code has already been fully redeemed.", None
    if query_one(f"SELECT 1 FROM promo_redemptions WHERE code = {P} AND user_id = {P}",
                 (code, user_id)):
        return False, "You've already redeemed this code.", None

    tier = row["tier"] if row["tier"] in VALID_TIERS else "starter"
    days = max(1, int(row["days"] or 30))
    now = datetime.utcnow()
    ends = (now + timedelta(days=days)).isoformat()

    # Grant via the trial fields so the existing expiry job reverts it later.
    _grant_access(user_id, tier, now.isoformat(), ends)
    execute(f"INSERT INTO promo_redemptions (code, user_id, redeemed_at) VALUES ({P},{P},{P})",
            (code, user_id, now.isoformat()))
    execute(f"UPDATE promo_codes SET used_count = used_count + 1 WHERE code = {P}", (code,))
    logger.info("promo %s redeemed by user %s → %s for %sd", code, user_id, tier, days)
    return True, f"Code applied — {tier.title()} unlocked for {days} days!", {"tier": tier, "days": days}


def _grant_access(user_id, tier, started_iso, ends_iso):
    """Upsert the user's subscription to a promo grant (trial-style, auto-expiring)."""
    if IS_POSTGRES:
        execute(
            "INSERT INTO user_subscriptions (user_id, tier, status, trial_status, "
            "trial_started_at, trial_ends_at, updated_at) "
            f"VALUES ({P},{P},'active','active',{P},{P},{P}) "
            "ON CONFLICT (user_id) DO UPDATE SET tier=excluded.tier, status='active', "
            "trial_status='active', trial_started_at=excluded.trial_started_at, "
            "trial_ends_at=excluded.trial_ends_at, updated_at=excluded.updated_at",
            (user_id, tier, started_iso, ends_iso, started_iso),
        )
    else:
        exists = query_one(f"SELECT 1 FROM user_subscriptions WHERE user_id = {P}", (user_id,))
        if exists:
            execute(
                "UPDATE user_subscriptions SET tier=?, status='active', trial_status='active', "
                "trial_started_at=?, trial_ends_at=?, updated_at=? WHERE user_id=?",
                (tier, started_iso, ends_iso, started_iso, user_id),
            )
        else:
            execute(
                "INSERT INTO user_subscriptions (user_id, tier, status, trial_status, "
                "trial_started_at, trial_ends_at, updated_at) "
                "VALUES (?,?,'active','active',?,?,?)",
                (user_id, tier, started_iso, ends_iso, started_iso),
            )
