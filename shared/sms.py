"""Central outbound SMS — Twilio REST API.

Mirrors ``shared.mailer``: activated only when Twilio env vars are set, otherwise
``send_sms()`` logs a warning and no-ops (returns False) so callers never raise.

Env:
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_FROM_NUMBER          — an SMS-capable Twilio number in E.164 (+1...)
   (or) TWILIO_MESSAGING_SERVICE_SID — use a Messaging Service instead of a from-number

Compliance note: US application-to-person SMS requires A2P 10DLC brand+campaign
registration before carriers will reliably deliver. Consent must be collected and
logged, and STOP/HELP honored (Twilio auto-handles STOP on its numbers; the app
also clears the opt-in on the inbound webhook). This module only sends — the
gating dependency is the registration, not the code.
"""
import logging
import os
import re

log = logging.getLogger(__name__)

_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def is_configured():
    """True when Twilio credentials + a sender (number or messaging service) are set."""
    return bool(
        os.getenv("TWILIO_ACCOUNT_SID")
        and os.getenv("TWILIO_AUTH_TOKEN")
        and (os.getenv("TWILIO_FROM_NUMBER") or os.getenv("TWILIO_MESSAGING_SERVICE_SID"))
    )


def normalize_e164(number, default_country="1"):
    """Best-effort E.164 normalization. Returns ``+<digits>`` or None if implausible.

    Keeps a leading ``+``; otherwise strips separators and, for a bare 10-digit US
    number, prepends the default country code. Not a validator — Twilio is the
    source of truth — just enough to store/compare consistently.
    """
    if not number:
        return None
    s = str(number).strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if not plus and len(digits) == 10:
        digits = default_country + digits
    if len(digits) < 8 or len(digits) > 15:
        return None
    return "+" + digits


def send_sms(to_number, body):
    """Send one SMS. Returns True on success, False otherwise (never raises)."""
    to = normalize_e164(to_number)
    if not to:
        log.warning("[sms] invalid destination number — not sending")
        return False
    if not is_configured():
        log.warning("[sms] Twilio not configured (TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM) "
                    "— message to %s not sent", to)
        return False

    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    data = {"To": to, "Body": (body or "")[:1600]}  # Twilio splits into segments
    msid = os.getenv("TWILIO_MESSAGING_SERVICE_SID")
    if msid:
        data["MessagingServiceSid"] = msid
    else:
        data["From"] = os.getenv("TWILIO_FROM_NUMBER")

    import requests
    try:
        resp = requests.post(
            _API.format(sid=sid), data=data, auth=(sid, token), timeout=15,
        )
        if resp.status_code in (200, 201):
            log.info("[sms] sent to %s", to)
            return True
        log.error("[sms] send failed (%s): %s", resp.status_code, resp.text[:300])
        return False
    except Exception as e:
        log.error("[sms] send error to %s: %s", to, e)
        return False
