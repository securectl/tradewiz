# 01 · Getting Started

## Creating an account

TradeWiz is **invite-only**. To register you need a pending email invitation (an admin
sends these). Once invited, you can sign up two ways:

### Email + password
1. Go to **/auth/signup**.
2. Enter your **email, name, and password** (minimum 8 characters).
3. The system checks your email against the invite list. If it matches, your account is
   created with the role and tier from your invite, and you're logged in automatically.
4. A **7-day Pro trial** activates immediately — no payment required.

### Sign in with Google
1. Click **Sign in with Google** on the login page.
2. Approve the Google consent screen.
3. If your Google email was invited (or is the configured admin email), your account is
   created and the 7-day Pro trial starts. Otherwise sign-up is blocked.

> **Welcome email:** If email is configured, you'll receive a welcome message confirming
> your trial and its expiry date.

---

## Logging in

- **Regular users:** `/auth/login` with email + password, or **Sign in with Google**.
- **Admins:** must use `/auth/admin-login` followed by a **TOTP 2FA** code (Google
  Authenticator or any TOTP app). See [Admin Guide](16-admin-guide.md) for setup.
- **Security:** 5 failed attempts from one IP in 5 minutes triggers a 15-minute lockout.

Forgot something or locked out? Use the in-app **Support** form (see below) or contact your
administrator.

---

## The interface tour

TradeWiz is a single-page app. Everything is reached from three persistent zones:

### 1. Header (always visible)
- **Logo** — *TradeWiz — Smart Market Intelligence*.
- **Search bar** — ticker input + **Period** (YTD, 1M, 3M, **6M default**, 12M, 2Y) +
  **Interval** (Daily default, Weekly) + **Analyze** button.
- **Right-side controls:**
  - **Theme** — Aurora, Terminal, Daylight.
  - **Font size** — Small / Medium / Large.
  - **Fast Mode** — use faster/cheaper AI models for research & health checks.
  - **Guide** — opens the in-app help.
  - **History** — your recent analyses (re-open with one click).
  - **What's New** — product release notes (badge appears when unread).
  - **Feedback** — short satisfaction survey.
  - **Settings (⚙)** — API keys & preferences (see [Settings](15-settings-api-keys.md)).
  - **Profile** — your tier badge, a 24-hour LLM-usage gauge, and logout.

### 2. Market Pulse strip
A horizontal row of seven live tiles, each with a color meter
(**red → orange → yellow → green**; VIX is inverted so green = calm):

| Tile | Shows |
|------|-------|
| **Signal** | Overall market risk (BUY / HOLD / SELL) blended from SPY, Nasdaq, VIX, Fear & Greed, volume |
| **SPY** | Price, change %, 52-week range |
| **VIX** | Volatility (inverted meter — low is good) |
| **Fear & Greed** | Index score + label |
| **Market Regime** | RISK-ON / RISK-OFF / DANGER / NEUTRAL |
| **Poly/Kalshi** | Prediction-market mood — click to open **Markets** |
| **Trump Mood** | Rhetoric sentiment (Pro) — click to open **Trump** |

### 3. Tab bar
The main navigation. Tabs you can see depend on your plan:

- **All users:** Dashboard · Analyzer · Breakout Scanner · Tracker · Screener · Option Calls · Research · Fin Skills · IPOs
- **Pro:** Markets · Congress · Smart Money · Trump
- **Bot access (invite-only):** ThunderBot · Claude Bot · Crypto Trading · Stock Trading
- **Admin:** Admin

---

## Your first five minutes

1. **Analyze a ticker.** Type `AAPL` in the header search and click **Analyze**. Explore
   the chart, pattern, trade plan, indicators, and AI validation in the
   [Analyzer](04-analyzer.md).
2. **Check the market.** Glance at the **Signal** and **Market Regime** tiles in the pulse
   strip to gauge whether conditions favor new entries.
3. **Run a screen.** Open **Screener**, pick a category (e.g. *Low-Cap*), and click
   **Scan & Vet with AI**. See [Screener](05-screener-scanner.md).
4. **Open the Dashboard.** Review the [Dashboard](03-dashboard.md) for your sectors, the
   next hot sector, and bot performance at a glance.
5. **Log a trade.** In the [Tracker](07-tracker-journal.md), add a note or a full trade
   entry and set a profit goal.

---

## Getting help

- **Guide button** — quick in-app help.
- **Support form** — submit a ticket (name, email, category, message). You receive a
  ticket ID; admins can view and resolve it.
- **Status** — service health (AI models, data feeds, database). Admins see the full
  incident log.

> **Tip:** If you need to run a command yourself during a support session, your
> administrator can guide you — most user-facing actions are just buttons in the app.
