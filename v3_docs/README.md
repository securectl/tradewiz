# TradeWiz — User Manual (v3)

**Smart Market Intelligence for stocks & crypto.**
TradeWiz combines AI-powered technical & fundamental analysis, multi-LLM validation,
breakout scanning, market-intelligence feeds, and three automated paper-trading bots
into a single web app at **[tradewiz.market](https://tradewiz.market)**.

> ⚠️ **Not financial advice.** TradeWiz is a research and education tool. All trading
> bots run in **paper (simulated) mode** by default. You are responsible for your own
> trading decisions.

---

## How this manual is organized

| # | Chapter | What's inside |
|---|---------|---------------|
| 01 | [Getting Started](01-getting-started.md) | Sign up, log in, the interface tour, tiers at a glance |
| 02 | [Accounts & Billing](02-accounts-billing.md) | Plans, free trial, upgrading, the billing portal, 2FA |
| 03 | [Dashboard](03-dashboard.md) | Your home hub — regime, sectors, bots, opportunities |
| 04 | [Analyzer](04-analyzer.md) | Deep-dive technicals, patterns, trade plans, fundamentals, news |
| 05 | [Screener & Scanner](05-screener-scanner.md) | Multi-category AI-vetted stock screening, hot sectors, history |
| 06 | [Breakout Scanner](06-breakout-scanner.md) | Qullamaggie momentum setups (HTF / VCP / EP / Stage 2) |
| 07 | [Tracker & Journal](07-tracker-journal.md) | Log trades, set goals, review bot activity |
| 08 | [Market Intelligence](08-market-intelligence.md) | Predictions, Congress, Smart Money, Trump Mood, Sector Radar, Option Calls |
| 09 | [Research & Fin Skills](09-research-finskills.md) | Sector Radar analyst, market pressure, pro finance skills |
| 10 | [Crypto Trading Bot](10-crypto-bot.md) | Strategies, risk gates, config, dashboard, BloFin |
| 11 | [Stock Trading Bot](11-stock-bot.md) | Strategies, PDT/market-hours gates, Alpaca/Webull |
| 12 | [Claude Bot & ThunderBot](12-claude-thunderbot.md) | Oversold re-entry bot + watchdog auto-trader |
| 13 | [AI Validation](13-ai-validation.md) | Multi-LLM consensus & 12-month investment prediction |
| 14 | [Alerts & Notifications](14-alerts-notifications.md) | Email digests, what gets sent, opting in |
| 15 | [Settings & API Keys](15-settings-api-keys.md) | OpenRouter, BloFin, Alpaca, Webull, Ollama, preferences |
| 16 | [Admin Guide](16-admin-guide.md) | Users, invites, LLM config, usage analytics, exports |
| 99 | [TradeWiz Guide — System Prompt](99-tradewiz-guide-system-prompt.md) | Drop-in system prompt for an in-app AI assistant |

---

## The 30-second tour

TradeWiz is a single-page app organized into **tabs**. Some tabs are gated by plan:

- **Everyone:** Dashboard · Analyzer · Breakout Scanner · Tracker · Screener · Option Calls · Research · Fin Skills · IPOs
- **Pro plan:** Markets (Predictions) · Congress · Smart Money · Trump
- **Bot access (invite-only):** ThunderBot · Claude Bot · Crypto Trading · Stock Trading
- **Admins:** Admin

A **Market Pulse strip** runs across the top of every view with seven live, color-metered
tiles: Signal, SPY, VIX, Fear & Greed, Market Regime, Poly/Kalshi, and Trump Mood.

---

## Plans at a glance

| Plan | Price | AI calls / day | Bot access | Highlights |
|------|-------|----------------|------------|------------|
| **Free** | $0 | 5 | — | Analyzer, basic Screener, Tracker |
| **Starter** | $19/mo | 30 | — | Full Screener, Research, IPOs |
| **Pro** | $39/mo | 100 | — | + Markets, Congress, Smart Money, Trump |
| **Admin** | — | Unlimited | ✓ | System configuration |

> **Bot access is invite-only** and granted per-user — it is *not* part of any paid plan.
> New users automatically receive a **7-day Pro trial**.

See [Accounts & Billing](02-accounts-billing.md) for details.

---

## Conventions used in this manual

- **Tab → Section → Control** paths describe where to click, e.g. *Screener → Category → Scan & Vet with AI*.
- Code-styled names like `daily_loss_limit` are configuration keys you may see in settings or the admin panel.
- Routes like `GET /api/market/gauge` are the backend endpoints behind each feature (useful for power users and support).
