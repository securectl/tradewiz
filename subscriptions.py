"""
Stripe Subscription Management — Checkout, Portal, Webhooks.
"""

import os
import logging
from datetime import datetime

from db import IS_POSTGRES, query_one, execute

logger = logging.getLogger(__name__)

P = "%s" if IS_POSTGRES else "?"

# ─── Config ──────────────────────────────────────────────────────────

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_STARTER = os.getenv("STRIPE_PRICE_STARTER", "")
STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "")

TIER_PRICES = {
    "starter": STRIPE_PRICE_STARTER,
    "pro": STRIPE_PRICE_PRO,
}

PRICE_TO_TIER = {}
if STRIPE_PRICE_STARTER:
    PRICE_TO_TIER[STRIPE_PRICE_STARTER] = "starter"
if STRIPE_PRICE_PRO:
    PRICE_TO_TIER[STRIPE_PRICE_PRO] = "pro"


def _get_stripe():
    """Lazy-import stripe to avoid startup failure if not installed."""
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


def is_configured():
    return bool(STRIPE_SECRET_KEY)


# ─── Customer Management ────────────────────────────────────────────

def get_or_create_stripe_customer(user_id, email, name=None):
    """Get existing Stripe customer ID or create one."""
    row = query_one(
        f"SELECT stripe_customer_id FROM user_subscriptions WHERE user_id = {P}",
        (user_id,),
    )
    if row and row["stripe_customer_id"]:
        return row["stripe_customer_id"]

    stripe = _get_stripe()
    customer = stripe.Customer.create(
        email=email,
        name=name or email,
        metadata={"user_id": str(user_id)},
    )
    customer_id = customer["id"]

    # Upsert subscription row with customer ID
    _upsert_subscription(user_id, tier="free", stripe_customer_id=customer_id)
    return customer_id


# ─── Checkout & Portal ──────────────────────────────────────────────

def create_checkout_session(user_id, email, name, tier, success_url, cancel_url):
    """Create a Stripe Checkout Session for a subscription tier."""
    stripe = _get_stripe()
    price_id = TIER_PRICES.get(tier)
    if not price_id:
        raise ValueError(f"No Stripe price configured for tier: {tier}")

    customer_id = get_or_create_stripe_customer(user_id, email, name)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": str(user_id), "tier": tier},
    )
    return session.url


def create_portal_session(user_id, return_url):
    """Create a Stripe Billing Portal session for managing subscription."""
    stripe = _get_stripe()
    row = query_one(
        f"SELECT stripe_customer_id FROM user_subscriptions WHERE user_id = {P}",
        (user_id,),
    )
    if not row or not row["stripe_customer_id"]:
        raise ValueError("No Stripe customer found for this user")

    session = stripe.billing_portal.Session.create(
        customer=row["stripe_customer_id"],
        return_url=return_url,
    )
    return session.url


# ─── Webhook Handling ────────────────────────────────────────────────

def handle_webhook(payload, sig_header):
    """Process Stripe webhook events."""
    stripe = _get_stripe()

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.warning(f"Webhook signature verification failed: {e}")
        raise

    # Idempotency: Stripe may deliver an event more than once and out of order.
    # Claim it by event id; if already processed, acknowledge and skip so we don't
    # re-run (e.g. an out-of-order `updated` reviving a `deleted` subscription).
    event_id = event.get("id")
    if event_id and _event_already_processed(event_id):
        return {"status": "ok", "duplicate": True}

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data)
    elif event_type == "customer.subscription.updated":
        _sync_subscription(data)
    elif event_type == "customer.subscription.deleted":
        _cancel_subscription(data)
    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(data)

    if event_id:
        _mark_event_processed(event_id, event_type)
    return {"status": "ok"}


def _event_already_processed(event_id):
    """True if this Stripe event id was handled before. Fail-open (treat as new)
    on any DB error so we never silently drop a real event."""
    try:
        return bool(query_one(
            f"SELECT 1 FROM stripe_events WHERE event_id = {P}", (event_id,)
        ))
    except Exception as e:
        logger.debug(f"stripe_events lookup failed ({e}); processing anyway")
        return False


def _mark_event_processed(event_id, event_type):
    """Record a processed event id. Best-effort; never breaks the webhook."""
    try:
        execute(
            f"INSERT INTO stripe_events (event_id, event_type, processed_at) "
            f"VALUES ({P}, {P}, {P}) ON CONFLICT (event_id) DO NOTHING",
            (event_id, event_type, datetime.utcnow().isoformat()),
        )
    except Exception as e:
        logger.debug(f"stripe_events record failed: {e}")


def _handle_checkout_completed(session):
    """Handle successful checkout — activate subscription."""
    stripe = _get_stripe()
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")

    if not subscription_id:
        return

    # Fetch the full subscription to get price info
    sub = stripe.Subscription.retrieve(subscription_id)
    _sync_subscription(sub)


def _sync_subscription(sub):
    """Sync a Stripe subscription object to our DB."""
    customer_id = sub.get("customer")
    if not customer_id:
        return

    # Find user by customer ID
    row = query_one(
        f"SELECT user_id FROM user_subscriptions WHERE stripe_customer_id = {P}",
        (customer_id,),
    )
    if not row:
        logger.warning(f"No user found for Stripe customer {customer_id}")
        return

    user_id = row["user_id"]
    status = sub.get("status", "active")

    # Get price ID from items
    price_id = None
    items = sub.get("items", {}).get("data", [])
    if items:
        price_id = items[0].get("price", {}).get("id")

    tier = PRICE_TO_TIER.get(price_id, "free") if price_id else "free"

    # If subscription is not active, revert to free
    if status not in ("active", "trialing"):
        tier = "free"

    # Period fields: on the current Stripe API (Basil, 2025-06-30) these moved off
    # the Subscription onto its items. Read from the item, fall back to top-level.
    item = items[0] if items else {}
    period_start = sub.get("current_period_start") or item.get("current_period_start")
    period_end = sub.get("current_period_end") or item.get("current_period_end")
    cancel_at_end = sub.get("cancel_at_period_end", False)

    _upsert_subscription(
        user_id,
        tier=tier,
        stripe_customer_id=customer_id,
        stripe_subscription_id=sub.get("id"),
        stripe_price_id=price_id,
        status=status,
        current_period_start=datetime.utcfromtimestamp(period_start).isoformat() if period_start else None,
        current_period_end=datetime.utcfromtimestamp(period_end).isoformat() if period_end else None,
        cancel_at_period_end=cancel_at_end,
    )
    logger.info(f"Synced subscription for user {user_id}: tier={tier}, status={status}")


def _cancel_subscription(sub):
    """Handle subscription deletion — revert to free tier."""
    customer_id = sub.get("customer")
    if not customer_id:
        return

    row = query_one(
        f"SELECT user_id FROM user_subscriptions WHERE stripe_customer_id = {P}",
        (customer_id,),
    )
    if not row:
        return

    _upsert_subscription(
        row["user_id"],
        tier="free",
        stripe_subscription_id=None,
        stripe_price_id=None,
        status="canceled",
        cancel_at_period_end=False,
    )
    logger.info(f"Subscription canceled for user {row['user_id']}, reverted to free tier")


def _handle_payment_failed(invoice):
    """Log payment failure (subscription stays active until Stripe retries exhaust)."""
    customer_id = invoice.get("customer")
    logger.warning(f"Payment failed for customer {customer_id}")


# ─── DB Helpers ──────────────────────────────────────────────────────

def _upsert_subscription(user_id, tier="free", stripe_customer_id=None,
                          stripe_subscription_id=None, stripe_price_id=None,
                          status="active", current_period_start=None,
                          current_period_end=None, cancel_at_period_end=False):
    """Insert or update a user's subscription row."""
    now = datetime.utcnow().isoformat()
    cancel_val = cancel_at_period_end if IS_POSTGRES else (1 if cancel_at_period_end else 0)

    if IS_POSTGRES:
        execute(
            f"INSERT INTO user_subscriptions (user_id, tier, stripe_customer_id, stripe_subscription_id, "
            f"stripe_price_id, status, current_period_start, current_period_end, cancel_at_period_end, updated_at) "
            f"VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}) "
            f"ON CONFLICT (user_id) DO UPDATE SET "
            f"tier = {P}, stripe_customer_id = COALESCE({P}, user_subscriptions.stripe_customer_id), "
            f"stripe_subscription_id = {P}, stripe_price_id = {P}, status = {P}, "
            f"current_period_start = COALESCE({P}, user_subscriptions.current_period_start), "
            f"current_period_end = COALESCE({P}, user_subscriptions.current_period_end), "
            f"cancel_at_period_end = {P}, updated_at = {P}",
            (user_id, tier, stripe_customer_id, stripe_subscription_id, stripe_price_id,
             status, current_period_start, current_period_end, cancel_val, now,
             tier, stripe_customer_id, stripe_subscription_id, stripe_price_id, status,
             current_period_start, current_period_end, cancel_val, now),
        )
    else:
        # Check if row exists
        existing = query_one(f"SELECT id FROM user_subscriptions WHERE user_id = {P}", (user_id,))
        if existing:
            # Build dynamic update
            sets = [f"tier = {P}", f"status = {P}", f"cancel_at_period_end = {P}", f"updated_at = {P}"]
            params = [tier, status, cancel_val, now]
            if stripe_customer_id is not None:
                sets.append(f"stripe_customer_id = {P}")
                params.append(stripe_customer_id)
            if stripe_subscription_id is not None:
                sets.append(f"stripe_subscription_id = {P}")
                params.append(stripe_subscription_id)
            if stripe_price_id is not None:
                sets.append(f"stripe_price_id = {P}")
                params.append(stripe_price_id)
            if current_period_start is not None:
                sets.append(f"current_period_start = {P}")
                params.append(current_period_start)
            if current_period_end is not None:
                sets.append(f"current_period_end = {P}")
                params.append(current_period_end)
            params.append(user_id)
            execute(
                f"UPDATE user_subscriptions SET {', '.join(sets)} WHERE user_id = {P}",
                params,
            )
        else:
            execute(
                f"INSERT INTO user_subscriptions (user_id, tier, stripe_customer_id, stripe_subscription_id, "
                f"stripe_price_id, status, current_period_start, current_period_end, cancel_at_period_end, updated_at) "
                f"VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}, {P})",
                (user_id, tier, stripe_customer_id, stripe_subscription_id, stripe_price_id,
                 status, current_period_start, current_period_end, cancel_val, now),
            )
