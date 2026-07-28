"""
Billing Blueprint — Stripe subscription management endpoints.
"""

import logging
from flask import Blueprint, jsonify, request, redirect
from flask_login import current_user

from rate_limiter import check_rate_limit, has_bot_access, TIER_LIMITS
from subscriptions import (
    is_configured as stripe_configured,
    create_checkout_session,
    create_portal_session,
    handle_webhook,
    STRIPE_PRICE_STARTER,
    STRIPE_PRICE_PRO,
)
from decorators import login_required

logger = logging.getLogger(__name__)

billing_bp = Blueprint("billing", __name__, url_prefix="/billing")


@billing_bp.route("/status")
@login_required
def billing_status():
    """Return current user's tier, usage, and billing info."""
    uid = current_user.id
    usage = check_rate_limit(uid)
    bot = has_bot_access(uid)

    # Subscription detail for the account panel (status, renewal, cancel state).
    subscription = None
    try:
        from db import query_one, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        row = query_one(
            f"SELECT status, current_period_end, cancel_at_period_end, "
            f"stripe_subscription_id FROM user_subscriptions WHERE user_id = {P}",
            (uid,),
        )
        if row:
            subscription = {
                "status": row.get("status"),
                "current_period_end": (str(row.get("current_period_end"))
                                       if row.get("current_period_end") else None),
                "cancel_at_period_end": bool(row.get("cancel_at_period_end")),
                "active": bool(row.get("stripe_subscription_id")),
            }
    except Exception:
        pass

    return jsonify({
        "tier": usage["tier"],
        "used": usage["used"],
        "limit": usage["limit"],
        "remaining": usage["remaining"],
        "bot_access": bot,
        "subscription": subscription,
        "stripe_configured": stripe_configured(),
        "prices": {
            "starter": {"price_id": STRIPE_PRICE_STARTER, "amount": _plan_amount("starter", 19), "name": "Starter"},
            "pro": {"price_id": STRIPE_PRICE_PRO, "amount": _plan_amount("pro", 39), "name": "Pro"},
        },
        "tiers": {
            k: {"limit": v["limit"], "bot_access": v["bot_access"]}
            for k, v in TIER_LIMITS.items() if k != "admin"
        },
    })


@billing_bp.route("/checkout/<tier>", methods=["POST"])
@login_required
def billing_checkout(tier):
    """Create a Stripe Checkout session for the given tier. Only invited users can upgrade."""
    if tier not in ("starter", "pro"):
        return jsonify({"error": "Invalid tier. Choose 'starter' or 'pro'."}), 400

    if not stripe_configured():
        return jsonify({"error": "Stripe is not configured. Contact administrator."}), 503

    # Verify user was invited (has an accepted invite or is admin)
    from db import query_one, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    is_admin = any(r == "admin" for r in (current_user.roles or []))
    if not is_admin:
        invite = query_one(
            f"SELECT email FROM invites WHERE email = {P} AND accepted_at IS NOT NULL",
            (current_user.email.lower(),),
        )
        if not invite:
            return jsonify({"error": "Upgrade is available to invited users only. Contact an administrator."}), 403

    try:
        base_url = request.host_url.rstrip("/")
        url = create_checkout_session(
            user_id=current_user.id,
            email=current_user.email,
            name=current_user.name,
            tier=tier,
            success_url=f"{base_url}/?billing=success",
            cancel_url=f"{base_url}/?billing=canceled",
        )
        return jsonify({"url": url})
    except Exception as e:
        logger.error(f"Checkout failed: {e}")
        return jsonify({"error": str(e)}), 500


@billing_bp.route("/portal", methods=["POST"])
@login_required
def billing_portal():
    """Create a Stripe Billing Portal session."""
    if not stripe_configured():
        return jsonify({"error": "Stripe is not configured"}), 503

    try:
        base_url = request.host_url.rstrip("/")
        url = create_portal_session(
            user_id=current_user.id,
            return_url=f"{base_url}/",
        )
        return jsonify({"url": url})
    except Exception as e:
        logger.error(f"Portal session failed: {e}")
        return jsonify({"error": str(e)}), 500


@billing_bp.route("/webhook", methods=["POST"])
def billing_webhook():
    """Stripe webhook endpoint — no auth, signature verified."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        result = handle_webhook(payload, sig_header)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 400


def _plan_amount(tier, default):
    """Admin-set displayed monthly amount (app_settings) or the default."""
    try:
        from app_settings import get_setting
        v = get_setting(f"price_{tier}_amount")
        return int(float(v)) if v not in (None, "") else default
    except Exception:
        return default


@billing_bp.route("/admin/pricing", methods=["GET", "POST"])
@login_required
def admin_pricing():
    """Admin-only: view/set the displayed amount + Stripe price id per plan."""
    from flask_login import current_user
    if not any(r == "admin" for r in (getattr(current_user, "roles", []) or [])):
        return jsonify({"error": "admin only"}), 403
    from app_settings import get_setting, set_setting
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        for tier in ("starter", "pro", "trader"):
            if f"{tier}_amount" in d and str(d[f"{tier}_amount"]).strip() != "":
                set_setting(f"price_{tier}_amount", int(float(d[f"{tier}_amount"])))
            if f"{tier}_price_id" in d:
                set_setting(f"price_{tier}_stripe_id", (d[f"{tier}_price_id"] or "").strip())
        return jsonify({"ok": True})
    out = {}
    for tier, default in (("starter", 19), ("pro", 39), ("trader", 79)):
        out[tier] = {
            "amount": _plan_amount(tier, default),
            "stripe_price_id": get_setting(f"price_{tier}_stripe_id", ""),
        }
    return jsonify({"pricing": out})


@billing_bp.route("/upgrade")
def billing_upgrade():
    """Redirect to main page with pricing modal trigger."""
    return redirect("/?show=pricing")
