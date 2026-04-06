"""
Route protection decorators for role-based access control and rate limiting.
"""

from functools import wraps
from flask import jsonify
from flask_login import current_user, login_required as _login_required


def login_required(f):
    """Require any authenticated user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required"}), 401
        if not current_user.is_admin:
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


def trader_required(f):
    """Require admin or trader role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required"}), 401
        if not current_user.is_trader:
            return jsonify({"error": "Trader access required"}), 403
        return f(*args, **kwargs)
    return decorated


def pro_required(f):
    """Require bot access (invite-only, not tied to any subscription plan)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required"}), 401
        from rate_limiter import has_bot_access
        if not has_bot_access(current_user.id):
            return jsonify({
                "error": "Bot access is invite-only. Contact admin for access.",
            }), 403
        return f(*args, **kwargs)
    return decorated


def bot_access_required(bot_type):
    """Require specific bot access (invite-only, not part of any plan)."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({"error": "Authentication required"}), 401
            from rate_limiter import has_bot_access
            if not has_bot_access(current_user.id, bot_type=bot_type):
                label = "Crypto Bot" if bot_type == "crypto" else "Stock Bot"
                return jsonify({
                    "error": f"{label} is invite-only. Contact admin for access.",
                }), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def subscription_required(min_tier="pro"):
    """Require a minimum subscription tier (pro, starter). Admins always pass."""
    TIER_ORDER = {"free": 0, "basic": 0, "starter": 1, "pro": 2, "admin": 99}
    min_level = TIER_ORDER.get(min_tier, 2)

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({"error": "Authentication required"}), 401
            from rate_limiter import get_user_tier
            tier = get_user_tier(current_user.id)
            level = TIER_ORDER.get(tier, 0)
            if level < min_level:
                return jsonify({
                    "error": f"This feature requires a {min_tier.title()} subscription.",
                    "tier": tier,
                    "required_tier": min_tier,
                    "upgrade_url": "/billing/upgrade",
                }), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def llm_rate_limit(call_source="api", call_count=1):
    """Decorator factory — checks rate limit headroom before allowing LLM calls.

    Args:
        call_source: Label for usage tracking (e.g., 'validate', 'predict', 'screener')
        call_count: Number of LLM calls this endpoint will make
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({"error": "Authentication required"}), 401
            from rate_limiter import check_headroom
            result = check_headroom(current_user.id, call_count)
            if not result["allowed"]:
                return jsonify({
                    "error": "Rate limit exceeded",
                    "tier": result["tier"],
                    "used": result["used"],
                    "limit": result["limit"],
                    "remaining": result["remaining"],
                    "upgrade_url": "/billing/upgrade",
                }), 429
            return f(*args, **kwargs)
        return decorated
    return decorator
