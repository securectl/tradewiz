# 02 · Accounts & Billing

## Plans & pricing

| Plan | Price | AI calls / day | Bot access | What you get |
|------|-------|----------------|------------|--------------|
| **Free** | $0 | 5 | — | Analyzer, basic Screener, Tracker |
| **Starter** | $19/mo | 30 | — | Full Screener, Research, IPOs, extended analysis |
| **Pro** | $39/mo | 100 | — | Everything in Starter + Markets (Predictions), Congress Trades, Smart Money, Trump Indicator, priority AI |
| **Admin** | — | Unlimited | ✓ | Role-based; full system access |

> The **AI calls / day** quota is a rolling 24-hour window. It limits how many
> LLM-powered actions (analysis validation, predictions, screener vetting, etc.) you can
> run. When you hit the limit, AI features pause until the window rolls forward.

### Bot access is separate

**Trading-bot access is invite-only and granted per user** — it is *not* bundled into any
paid plan. An admin sets your `bot_access` to some combination of `crypto`, `stock`, and
`watchdog`. Without it, the **ThunderBot / Claude Bot / Crypto Trading / Stock Trading**
tabs stay hidden even on the Pro plan.

---

## Your free trial

Every new account gets a **7-day Pro trial** automatically on sign-up — no card required.

- **During the trial** you have full Pro features and the 100 calls/day quota.
- **3 days before expiry** you get a reminder email with an upgrade link.
- **At expiry:**
  - If you've started a paid subscription, your trial is marked *converted* and your paid
    tier continues.
  - Otherwise you drop to the **Free** plan (5 calls/day) and get a "trial expired" email.

**One trial per person.** TradeWiz fingerprints sign-ups (IP, device, email domain) to
prevent trial abuse; disposable-email domains and repeat sign-ups from the same network
are blocked.

---

## Upgrading & managing your subscription

All billing runs through **Stripe**.

### Upgrade
1. Click any **Upgrade** prompt (or open the pricing modal).
2. Choose **Starter** or **Pro**.
3. You're taken to Stripe Checkout to enter payment.
4. On success you return to TradeWiz with your new tier active.

### Manage / cancel
Open the **Billing Portal** (Stripe Customer Portal) to:
- Upgrade or downgrade,
- Update your payment method,
- Cancel (you keep access until the period ends),
- Download invoices and view billing history.

### Checking your usage
The app shows your current tier, today's usage, and remaining quota (a gauge lives in the
header profile area). Behind the scenes this is the `/billing/status` endpoint, returning
your tier, calls used in the last 24h, your limit, remaining calls, and bot access.

---

## Security & 2FA

- **Passwords** are hashed; accounts can be locked by an admin.
- **Admins must use TOTP 2FA.** On first admin login you scan a QR code with an
  authenticator app and confirm a 6-digit code; the secret is stored encrypted. Every
  later admin login requires a fresh code.
- **Account lock:** A locked account cannot log in through any method until unlocked.

---

## Roles vs. tiers (how access is decided)

TradeWiz gates features with a layered system:

| Gate | Requires | Controls |
|------|----------|----------|
| **Login** | Any signed-in user | Almost everything |
| **Tier** | free / starter / pro / admin | AI quota + Pro-only intelligence tabs |
| **Bot access** | invite-only `crypto` / `stock` / `watchdog` flags | The four bot tabs |
| **Trader role** | admin or any bot access | Advanced trading features |
| **Admin role** | admin | Admin tab + system config |

If you try to use something above your access, the app shows an upgrade prompt or an
"access required" message explaining what you need.
