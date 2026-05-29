"""Central outbound email — Resend HTTP API first, SMTP fallback.

Activated by either set of env vars:
  RESEND_API_KEY (+ optional EMAIL_FROM)         — preferred (just an API key)
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS  — legacy fallback

If neither is configured, send_email() logs a warning and no-ops (returns
False) so callers never raise. All app email (trial notices, daily alerts)
routes through here so there is a single place to swap providers.
"""
import logging
import os

log = logging.getLogger(__name__)

DEFAULT_FROM = "TradeWiz <onboarding@resend.dev>"


def from_address():
    return os.getenv("EMAIL_FROM") or os.getenv("ALERT_FROM_EMAIL") or DEFAULT_FROM


def is_configured():
    """True if any provider is wired up."""
    return bool(os.getenv("RESEND_API_KEY") or (os.getenv("SMTP_HOST") and os.getenv("SMTP_USER")))


def send_email(to_email, subject, html_body):
    """Send one HTML email. Returns True on success, False otherwise (never raises)."""
    if not to_email:
        return False
    api_key = os.getenv("RESEND_API_KEY")
    if api_key:
        return _send_resend(api_key, to_email, subject, html_body)
    if os.getenv("SMTP_HOST"):
        return _send_smtp(to_email, subject, html_body)
    log.warning("No email provider configured (RESEND_API_KEY or SMTP_*) — '%s' not sent to %s", subject, to_email)
    return False


def _send_resend(api_key, to_email, subject, html_body):
    import requests
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": from_address(), "to": [to_email], "subject": subject, "html": html_body},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            log.info("Resend email sent to %s: %s", to_email, subject)
            return True
        log.error("Resend send failed (%s): %s", resp.status_code, resp.text[:300])
        return False
    except Exception as e:
        log.error("Resend send error to %s: %s", to_email, e)
        return False


def _send_smtp(to_email, subject, html_body):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    pw = os.getenv("SMTP_PASS", "")
    if not all([host, user, pw]):
        log.warning("SMTP not fully configured — email not sent to %s", to_email)
        return False
    frm = os.getenv("EMAIL_FROM") or user
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = frm
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))
    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, pw)
            server.sendmail(frm, to_email, msg.as_string())
        log.info("SMTP email sent to %s: %s", to_email, subject)
        return True
    except Exception as e:
        log.error("SMTP send failed to %s: %s", to_email, e)
        return False
