"""
LLM Rate Limiting — Rolling 24-hour usage tracking per subscription tier.
"""

import threading
import logging
from datetime import datetime, timedelta

from db import IS_POSTGRES, query_one, execute

logger = logging.getLogger(__name__)

P = "%s" if IS_POSTGRES else "?"

# ─── Tier Definitions ────────────────────────────────────────────────

TIER_LIMITS = {
    "free":    {"limit": 5,   "bot_access": False, "label": "Free"},
    "starter": {"limit": 30,  "bot_access": False, "label": "Starter"},
    "basic":   {"limit": 30,  "bot_access": False, "label": "Starter"},  # alias
    "pro":     {"limit": 100, "bot_access": True,  "label": "Pro"},
    "admin":   {"limit": None, "bot_access": True,  "label": "Admin"},
}

# ─── Thread-local LLM user context ──────────────────────────────────

_llm_context = threading.local()


def set_llm_user(user_id, source="api"):
    """Set the current LLM-calling user for this thread (propagates into _call_openrouter)."""
    _llm_context.user_id = user_id
    _llm_context.source = source


def get_llm_user():
    """Get (user_id, source) for the current thread, or (None, None)."""
    uid = getattr(_llm_context, "user_id", None)
    source = getattr(_llm_context, "source", None)
    return uid, source


# ─── Core Functions ──────────────────────────────────────────────────

def get_user_tier(user_id):
    """Query user's subscription tier. Admins short-circuit to 'admin'."""
    # Check admin role first
    role_row = query_one(
        f"SELECT role FROM user_roles WHERE user_id = {P} AND role = 'admin'",
        (user_id,),
    )
    if role_row:
        return "admin"

    row = query_one(
        f"SELECT tier FROM user_subscriptions WHERE user_id = {P} AND status = 'active'",
        (user_id,),
    )
    return row["tier"] if row else "free"


def get_rolling_count(user_id):
    """Count LLM calls in the last 24 hours."""
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    row = query_one(
        f"SELECT COUNT(*) as cnt FROM llm_usage_log WHERE user_id = {P} AND called_at >= {P}",
        (user_id, cutoff),
    )
    return row["cnt"] if row else 0


def check_rate_limit(user_id):
    """Check if user can make more LLM calls.

    Returns dict: {allowed, tier, used, limit, remaining}
    """
    tier = get_user_tier(user_id)
    tier_info = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    limit = tier_info["limit"]

    # Unlimited for admin
    if limit is None:
        return {"allowed": True, "tier": tier, "used": 0, "limit": None, "remaining": None}

    used = get_rolling_count(user_id)
    remaining = max(0, limit - used)

    return {
        "allowed": used < limit,
        "tier": tier,
        "used": used,
        "limit": limit,
        "remaining": remaining,
    }


def check_headroom(user_id, call_count=1):
    """Check if user has enough remaining calls for call_count.

    Returns dict: {allowed, tier, used, limit, remaining}
    """
    tier = get_user_tier(user_id)
    tier_info = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    limit = tier_info["limit"]

    if limit is None:
        return {"allowed": True, "tier": tier, "used": 0, "limit": None, "remaining": None}

    used = get_rolling_count(user_id)
    remaining = max(0, limit - used)

    return {
        "allowed": remaining >= call_count,
        "tier": tier,
        "used": used,
        "limit": limit,
        "remaining": remaining,
    }


def record_llm_call(user_id, call_source="api", model="unknown",
                    prompt_tokens=0, completion_tokens=0, total_tokens=0,
                    cost_usd=None):
    """Record an LLM call in the usage log.

    Token/cost args are optional and default to 0 so existing call sites keep
    working (rate limiting only counts rows). When token counts are supplied
    and ``cost_usd`` is None, the cost is estimated from ``shared.llm_pricing``.
    Powers the admin AI-usage dashboard.
    """
    try:
        now = datetime.utcnow().isoformat()
        pt = int(prompt_tokens or 0)
        ct = int(completion_tokens or 0)
        tt = int(total_tokens or 0) or (pt + ct)
        if cost_usd is None:
            try:
                from shared.llm_pricing import estimate_cost
                cost_usd = estimate_cost(model, pt, ct)
            except Exception:
                cost_usd = 0.0
        execute(
            f"INSERT INTO llm_usage_log (user_id, call_source, model, "
            f"prompt_tokens, completion_tokens, total_tokens, cost_usd, called_at) "
            f"VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P})",
            (user_id, call_source, model, pt, ct, tt, float(cost_usd or 0.0), now),
        )
    except Exception as e:
        logger.warning(f"Failed to record LLM call: {e}")


def has_bot_access(user_id, bot_type=None):
    """Check if user has bot access — invite-only, not tied to any subscription plan.

    Bot access is granted per-user via the bot_access field in user_subscriptions
    or via admin role. It is NOT part of any public subscription tier.

    Args:
        user_id: The user ID to check.
        bot_type: Optional "crypto" or "stock" for granular check.
                  If None, returns True if user has access to any bot.
    """
    tier = get_user_tier(user_id)

    # Admin always has full access
    if tier == "admin":
        return True

    # Invite-only: check per-user bot_access field (not tied to tier)
    row = query_one(
        f"SELECT bot_access FROM user_subscriptions WHERE user_id = {P} AND status = 'active'",
        (user_id,),
    )
    bot_access_str = (row["bot_access"] if row else "none").lower()
    if bot_access_str in ("none", ""):
        return False

    if bot_type is None:
        return bot_access_str != "none"

    # Granular check: "crypto", "stock", or "crypto,stock"
    allowed = [b.strip() for b in bot_access_str.split(",")]
    return bot_type in allowed


def purge_old_usage_logs(days=7):
    """Delete usage log entries older than N days."""
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        execute(f"DELETE FROM llm_usage_log WHERE called_at < {P}", (cutoff,))
        logger.info(f"Purged LLM usage logs older than {days} days")
    except Exception as e:
        logger.warning(f"Failed to purge usage logs: {e}")
