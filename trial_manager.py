"""
Trial Manager — 7-day free trial with fraud prevention.

Handles:
- Trial activation on signup (auto-grant Pro tier for 7 days)
- IP/geo fingerprinting to detect duplicate trials
- Trial expiry enforcement (revert to free tier)
- Email notifications (welcome, 3-day warning, expiry)
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta

from db import query, query_one, execute, IS_POSTGRES

logger = logging.getLogger(__name__)
P = "%s" if IS_POSTGRES else "?"

TRIAL_DAYS = 7
TRIAL_TIER = "pro"  # What tier users get during trial


# ─── Trial Activation ────────────────────────────────────────

def activate_trial(user_id, request_obj=None):
    """Activate a 7-day free trial for a new user.

    Steps:
    1. Check if user already had a trial (no second chances)
    2. Fingerprint the request (IP, geo, device) for fraud detection
    3. Check fingerprints against known trial abusers
    4. If clean, set tier=pro + trial_status=active + trial_ends_at
    5. Send welcome email

    Returns: {"ok": bool, "message": str, "trial_ends_at": str}
    """
    # 1. Check existing trial
    sub = query_one(
        f"SELECT trial_status, trial_ends_at FROM user_subscriptions WHERE user_id = {P}",
        (user_id,),
    )
    if sub and sub.get("trial_status") in ("active", "expired", "converted"):
        return {"ok": False, "message": "Trial already used on this account."}

    # 2. Fingerprint request
    fingerprint = _capture_fingerprint(user_id, request_obj)

    # 3. Fraud check
    fraud = _check_trial_fraud(user_id, fingerprint)
    if not fraud["allowed"]:
        logger.warning(f"Trial blocked for user {user_id}: {fraud['reason']}")
        return {"ok": False, "message": f"Trial unavailable: {fraud['reason']}"}

    # 4. Activate trial
    now = datetime.now()
    trial_end = now + timedelta(days=TRIAL_DAYS)

    if IS_POSTGRES:
        execute(
            f"""UPDATE user_subscriptions
                SET tier = {P}, trial_status = 'active',
                    trial_started_at = {P}, trial_ends_at = {P},
                    updated_at = NOW()
                WHERE user_id = {P}""",
            (TRIAL_TIER, now.isoformat(), trial_end.isoformat(), user_id),
        )
    else:
        execute(
            f"""UPDATE user_subscriptions
                SET tier = {P}, trial_status = 'active',
                    trial_started_at = {P}, trial_ends_at = {P},
                    updated_at = datetime('now')
                WHERE user_id = {P}""",
            (TRIAL_TIER, now.isoformat(), trial_end.isoformat(), user_id),
        )

    # 5. Send welcome email
    try:
        _send_trial_welcome(user_id, trial_end)
    except Exception as e:
        logger.warning(f"Welcome email failed for user {user_id}: {e}")

    logger.info(f"Trial activated for user {user_id}, expires {trial_end.strftime('%Y-%m-%d')}")
    return {
        "ok": True,
        "message": f"7-day Pro trial activated! Expires {trial_end.strftime('%B %d, %Y')}",
        "trial_ends_at": trial_end.isoformat(),
    }


# ─── Trial Expiry Enforcement ────────────────────────────────

def check_and_expire_trials():
    """Check all active trials and expire any past their end date.
    Called by scheduler daily or on user login."""
    try:
        if IS_POSTGRES:
            expired = query(
                "SELECT user_id, trial_ends_at FROM user_subscriptions "
                "WHERE trial_status = 'active' AND trial_ends_at < NOW()"
            )
        else:
            expired = query(
                "SELECT user_id, trial_ends_at FROM user_subscriptions "
                "WHERE trial_status = 'active' AND trial_ends_at < datetime('now')"
            )

        for row in expired:
            uid = row["user_id"]
            # Only revert if no active Stripe subscription
            has_stripe = query_one(
                f"SELECT stripe_subscription_id FROM user_subscriptions "
                f"WHERE user_id = {P} AND stripe_subscription_id IS NOT NULL AND stripe_subscription_id != ''",
                (uid,),
            )
            if has_stripe:
                # User converted — mark trial as converted, keep tier
                execute(
                    f"UPDATE user_subscriptions SET trial_status = 'converted' WHERE user_id = {P}",
                    (uid,),
                )
                logger.info(f"Trial converted to paid for user {uid}")
            else:
                # Revert to free tier
                execute(
                    f"UPDATE user_subscriptions SET tier = 'free', trial_status = 'expired' WHERE user_id = {P}",
                    (uid,),
                )
                logger.info(f"Trial expired for user {uid} — reverted to free")
                try:
                    _send_trial_expired(uid)
                except Exception:
                    pass

        return len(expired)
    except Exception as e:
        logger.error(f"Trial expiry check failed: {e}")
        return 0


def check_trial_expiry_warnings():
    """Send 3-day warning emails for trials about to expire.
    Called by scheduler daily."""
    try:
        warning_date = (datetime.now() + timedelta(days=3)).isoformat()

        if IS_POSTGRES:
            rows = query(
                f"SELECT user_id, trial_ends_at FROM user_subscriptions "
                f"WHERE trial_status = 'active' AND trial_ends_at <= {P} "
                f"AND trial_ends_at > NOW()",
                (warning_date,),
            )
        else:
            rows = query(
                f"SELECT user_id, trial_ends_at FROM user_subscriptions "
                f"WHERE trial_status = 'active' AND trial_ends_at <= {P} "
                f"AND trial_ends_at > datetime('now')",
                (warning_date,),
            )

        for row in rows:
            try:
                _send_trial_warning(row["user_id"], row["trial_ends_at"])
            except Exception as e:
                logger.warning(f"Trial warning email failed for user {row['user_id']}: {e}")

        return len(rows)
    except Exception as e:
        logger.error(f"Trial warning check failed: {e}")
        return 0


def get_trial_status(user_id):
    """Get trial info for a user."""
    sub = query_one(
        f"SELECT trial_status, trial_started_at, trial_ends_at, tier FROM user_subscriptions WHERE user_id = {P}",
        (user_id,),
    )
    if not sub:
        return {"trial_status": "none", "eligible": True}

    status = sub.get("trial_status", "none")
    ends_at = sub.get("trial_ends_at")
    days_left = 0
    if ends_at and status == "active":
        try:
            end_dt = datetime.fromisoformat(str(ends_at).replace("Z", ""))
            days_left = max(0, (end_dt - datetime.now()).days)
        except Exception:
            pass

    return {
        "trial_status": status,
        "trial_started_at": str(sub.get("trial_started_at", "")) if sub.get("trial_started_at") else None,
        "trial_ends_at": str(ends_at) if ends_at else None,
        "days_left": days_left,
        "tier": sub.get("tier", "free"),
        "eligible": status == "none",
    }


# ─── Fraud Prevention ────────────────────────────────────────

def _capture_fingerprint(user_id, request_obj):
    """Capture IP, geo, and device fingerprint from the request."""
    fp = {
        "ip_address": "",
        "country": "",
        "region": "",
        "city": "",
        "fingerprint_hash": "",
        "email_domain": "",
        "device_info": "",
    }

    if not request_obj:
        return fp

    # IP address (handle proxies)
    ip = request_obj.headers.get("X-Forwarded-For", request_obj.remote_addr or "")
    if "," in ip:
        ip = ip.split(",")[0].strip()
    fp["ip_address"] = ip

    # Device info from User-Agent
    ua = request_obj.headers.get("User-Agent", "")
    fp["device_info"] = ua[:300]

    # Email domain
    try:
        user = query_one(f"SELECT email FROM users WHERE id = {P}", (user_id,))
        if user and user.get("email"):
            fp["email_domain"] = user["email"].split("@")[-1].lower()
    except Exception:
        pass

    # Fingerprint hash: combine IP + UA + email domain for dedup
    raw = f"{ip}|{ua}|{fp['email_domain']}"
    fp["fingerprint_hash"] = hashlib.sha256(raw.encode()).hexdigest()[:32]

    # Geo lookup via free IP API
    if ip and ip not in ("127.0.0.1", "localhost", ""):
        try:
            import requests
            resp = requests.get(f"http://ip-api.com/json/{ip}?fields=country,regionName,city", timeout=5)
            if resp.status_code == 200:
                geo = resp.json()
                fp["country"] = geo.get("country", "")
                fp["region"] = geo.get("regionName", "")
                fp["city"] = geo.get("city", "")
        except Exception:
            pass

    # Save fingerprint
    try:
        execute(
            f"""INSERT INTO trial_fingerprints
                (user_id, ip_address, country, region, city, fingerprint_hash,
                 email_domain, device_info)
                VALUES ({P},{P},{P},{P},{P},{P},{P},{P})""",
            (user_id, fp["ip_address"], fp["country"], fp["region"], fp["city"],
             fp["fingerprint_hash"], fp["email_domain"], fp["device_info"]),
        )
    except Exception as e:
        logger.warning(f"Failed to save fingerprint: {e}")

    return fp


def _check_trial_fraud(user_id, fingerprint):
    """Check for trial abuse indicators.

    Checks:
    1. Same IP already used a trial
    2. Same fingerprint hash already used a trial
    3. Disposable email domain
    4. Too many trials from same IP range (/24 subnet)
    """
    flags = []
    score = 0

    ip = fingerprint.get("ip_address", "")
    fp_hash = fingerprint.get("fingerprint_hash", "")
    email_domain = fingerprint.get("email_domain", "")

    # 1. Same IP already had a trial
    if ip and ip not in ("127.0.0.1", ""):
        existing = query_one(
            f"""SELECT tf.user_id FROM trial_fingerprints tf
                JOIN user_subscriptions us ON tf.user_id = us.user_id
                WHERE tf.ip_address = {P} AND tf.user_id != {P}
                AND us.trial_status IN ('active', 'expired', 'converted')
                LIMIT 1""",
            (ip, user_id),
        )
        if existing:
            flags.append("ip_reuse")
            score += 40

    # 2. Same fingerprint hash
    if fp_hash:
        existing = query_one(
            f"""SELECT tf.user_id FROM trial_fingerprints tf
                JOIN user_subscriptions us ON tf.user_id = us.user_id
                WHERE tf.fingerprint_hash = {P} AND tf.user_id != {P}
                AND us.trial_status IN ('active', 'expired', 'converted')
                LIMIT 1""",
            (fp_hash, user_id),
        )
        if existing:
            flags.append("fingerprint_reuse")
            score += 50

    # 3. Disposable email domains
    disposable_domains = {
        "tempmail.com", "throwaway.email", "guerrillamail.com", "mailinator.com",
        "yopmail.com", "trashmail.com", "sharklasers.com", "guerrillamailblock.com",
        "grr.la", "dispostable.com", "maildrop.cc", "temp-mail.org",
        "fakeinbox.com", "emailondeck.com", "10minutemail.com", "tmpmail.net",
    }
    if email_domain in disposable_domains:
        flags.append("disposable_email")
        score += 30

    # 4. IP subnet abuse (more than 3 trials from same /24)
    if ip and "." in ip:
        subnet = ".".join(ip.split(".")[:3]) + ".%"
        try:
            subnet_count = query_one(
                f"""SELECT COUNT(DISTINCT tf.user_id) as cnt FROM trial_fingerprints tf
                    JOIN user_subscriptions us ON tf.user_id = us.user_id
                    WHERE tf.ip_address LIKE {P} AND tf.user_id != {P}
                    AND us.trial_status IN ('active', 'expired', 'converted')""",
                (subnet, user_id),
            )
            if subnet_count and int(subnet_count["cnt"]) >= 3:
                flags.append("subnet_abuse")
                score += 25
        except Exception:
            pass

    # Save fraud assessment
    if flags:
        try:
            execute(
                f"UPDATE trial_fingerprints SET fraud_score = {P}, fraud_flags = {P} "
                f"WHERE user_id = {P} AND id = (SELECT MAX(id) FROM trial_fingerprints WHERE user_id = {P})",
                (score, json.dumps(flags), user_id, user_id),
            )
        except Exception:
            pass

    # Block if score >= 50
    if score >= 50:
        reason = "Multiple fraud indicators detected"
        if "ip_reuse" in flags:
            reason = "A trial was already used from this network"
        elif "fingerprint_reuse" in flags:
            reason = "A trial was already used from this device"
        return {"allowed": False, "reason": reason, "score": score, "flags": flags}

    return {"allowed": True, "reason": "Clean", "score": score, "flags": flags}


# ─── Email Notifications ─────────────────────────────────────

def _get_user_email(user_id):
    """Get user's email and name."""
    try:
        row = query_one(f"SELECT email, name FROM users WHERE id = {P}", (user_id,))
        return row if row else {"email": None, "name": None}
    except Exception:
        return {"email": None, "name": None}


def _send_email(to_email, subject, html_body):
    """Send an email via the shared mailer (Resend → SMTP fallback)."""
    from shared.mailer import send_email
    return send_email(to_email, subject, html_body)


def _send_trial_welcome(user_id, trial_end):
    """Send welcome email when trial starts."""
    user = _get_user_email(user_id)
    if not user.get("email"):
        return
    name = user.get("name") or "there"
    end_str = trial_end.strftime("%B %d, %Y")
    app_name = os.getenv("APP_NAME", "TradeWiz")
    app_url = os.getenv("APP_URL", "https://tradewiz.market")

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;background:#0d1117;color:#e6e6e6;">
        <h1 style="color:#4f8aff;margin-bottom:8px;">Welcome to {app_name} Pro!</h1>
        <p>Hi {name},</p>
        <p>Your <strong>7-day free Pro trial</strong> is now active. You have full access to:</p>
        <ul style="line-height:2;">
            <li>100 AI analysis calls per day</li>
            <li>Trump Market Indicator & Trade Signals</li>
            <li>Congress Trades Tracker</li>
            <li>Prediction Markets</li>
            <li>Advanced Screener with all categories</li>
            <li>Priority AI models</li>
        </ul>
        <p><strong>Your trial expires on {end_str}.</strong></p>
        <p>To keep Pro access after your trial, upgrade anytime from the app.</p>
        <div style="margin:24px 0;">
            <a href="{app_url}" style="background:#4f8aff;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold;">Open {app_name}</a>
        </div>
        <p style="color:#636b7e;font-size:12px;">If you have questions, reply to this email.</p>
    </div>
    """
    _send_email(user["email"], f"Your {app_name} Pro Trial Has Started!", html)


def _send_trial_warning(user_id, trial_ends_at):
    """Send 3-day warning email."""
    user = _get_user_email(user_id)
    if not user.get("email"):
        return
    name = user.get("name") or "there"
    app_name = os.getenv("APP_NAME", "TradeWiz")
    app_url = os.getenv("APP_URL", "https://tradewiz.market")

    try:
        end_dt = datetime.fromisoformat(str(trial_ends_at).replace("Z", ""))
        end_str = end_dt.strftime("%B %d, %Y")
        days_left = max(0, (end_dt - datetime.now()).days)
    except Exception:
        end_str = "soon"
        days_left = 3

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;background:#0d1117;color:#e6e6e6;">
        <h1 style="color:#ff8c42;margin-bottom:8px;">Your Trial Expires in {days_left} Days</h1>
        <p>Hi {name},</p>
        <p>Your {app_name} Pro trial ends on <strong>{end_str}</strong>.</p>
        <p>After your trial, you'll be moved to the Free plan (5 AI calls/day). To keep full Pro access:</p>
        <div style="margin:24px 0;">
            <a href="{app_url}?show=pricing" style="background:#4f8aff;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold;">Upgrade to Pro — $39/mo</a>
        </div>
        <p style="color:#636b7e;font-size:12px;">No action needed if you're happy with the Free plan.</p>
    </div>
    """
    _send_email(user["email"], f"Your {app_name} Pro Trial Expires in {days_left} Days", html)


def _send_trial_expired(user_id):
    """Send trial expired notification."""
    user = _get_user_email(user_id)
    if not user.get("email"):
        return
    name = user.get("name") or "there"
    app_name = os.getenv("APP_NAME", "TradeWiz")
    app_url = os.getenv("APP_URL", "https://tradewiz.market")

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;background:#0d1117;color:#e6e6e6;">
        <h1 style="color:#ff4757;margin-bottom:8px;">Your Pro Trial Has Ended</h1>
        <p>Hi {name},</p>
        <p>Your {app_name} Pro trial has expired. You've been moved to the Free plan.</p>
        <p>You still have access to:</p>
        <ul style="line-height:2;">
            <li>5 AI analysis calls per day</li>
            <li>Stock Analyzer & Charts</li>
            <li>Basic Screener</li>
            <li>Portfolio Tracker</li>
        </ul>
        <p>Miss Pro features? Upgrade anytime:</p>
        <div style="margin:24px 0;">
            <a href="{app_url}?show=pricing" style="background:#4f8aff;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold;">Upgrade to Pro — $39/mo</a>
        </div>
    </div>
    """
    _send_email(user["email"], f"Your {app_name} Pro Trial Has Ended", html)
