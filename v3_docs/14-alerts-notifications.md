# 14 · Alerts & Notifications

TradeWiz can email you a **daily market digest** plus account-related notices.

---

## Daily alerts digest

Sent before market open to opted-in users, the digest has up to three sections:

1. **Volume spikes** — stocks trading well above their average volume (default ≥2× the
   20-day average).
2. **Persistent oversold** — tickers flagged oversold by the screener for several
   consecutive days (default ≥5), with RSI, sector, price, and bottom-signal strength.
3. **Upcoming earnings** — large-cap names reporting within the next several days
   (default 7).

If a section can't be built, it's simply omitted — the rest of the email still goes out.

### Opting in
Daily alerts are **opt-in**. Enable them in your alert settings (stored as the
`alert_emails` preference). Admins can also set global recipients.

### Tuning (admin / deployment)
Thresholds are environment-configurable: `ALERT_VOL_SPIKE_RATIO` (2.0), 
`ALERT_OVERSOLD_MIN_DAYS` (5), `ALERT_EARNINGS_DAYS` (7), `ALERT_TOP_N` (3), and an
optional `ALERT_RECIPIENTS` override.

---

## Account & lifecycle emails

| Email | When |
|-------|------|
| **Welcome / trial started** | immediately on sign-up |
| **Trial expiring** | ~3 days before your 7-day trial ends |
| **Trial expired** | after the trial lapses (with an upgrade link) |
| **Support request received** | confirmation when you submit a ticket |

---

## Email delivery (for deployers)

TradeWiz sends mail through **Resend** (preferred) or **SMTP** (Gmail, SES, SendGrid,
Mailgun, etc.). If neither is configured, email is skipped (the app logs a warning and
keeps working). Set `RESEND_API_KEY` *or* the `SMTP_*` variables plus `EMAIL_FROM`,
`APP_NAME`, `APP_URL`, and `SUPPORT_EMAIL`. See `Email_Setup.md` in the repo for a full
walkthrough.

---

## In-app notifications

- **What's New** — release notes in the header (a badge appears when there's something new).
- **Status** — live service health; admins get a full incident log.
- **Bot activity** — surfaced live in the bot tabs and the [Tracker](07-tracker-journal.md)
  rather than by email.
