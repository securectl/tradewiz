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

    return jsonify({
        "tier": usage["tier"],
        "used": usage["used"],
        "limit": usage["limit"],
        "remaining": usage["remaining"],
        "bot_access": bot,
        "stripe_configured": stripe_configured(),
        "prices": {
            "starter": {"price_id": STRIPE_PRICE_STARTER, "amount": 19, "name": "Starter"},
            "pro": {"price_id": STRIPE_PRICE_PRO, "amount": 39, "name": "Pro"},
        },
        "tiers": {
            k: {"limit": v["limit"], "bot_access": v["bot_access"]}
            for k, v in TIER_LIMITS.items() if k != "admin"
        },
    })


@billing_bp.route("/checkout/<tier>", methods=["POST"])
@login_required
def billing_checkout(tier):
    """Create a Stripe Checkout session for the given tier. Open to any logged-in
    user (public self-serve). Bot access remains invite-only and is never granted
    by a paid plan."""
    if tier not in ("starter", "pro"):
        return jsonify({"error": "Invalid tier. Choose 'starter' or 'pro'."}), 400

    if not stripe_configured():
        return jsonify({"error": "Stripe is not configured. Contact administrator."}), 503

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


@billing_bp.route("/upgrade")
def billing_upgrade():
    """Redirect to main page with pricing modal trigger."""
    return redirect("/?show=pricing")
