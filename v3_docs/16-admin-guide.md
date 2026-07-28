# 16 · Admin Guide

Admins get an **Admin** tab and elevated routes for running TradeWiz. All admin actions
require the **admin** role, and **admin login requires TOTP 2FA**.

---

## First-time admin setup

1. Set `ADMIN_EMAIL` in the environment.
2. Start the app and go to **/auth/admin-setup**.
3. Set a password (8+ chars). The admin user is created with **admin + trader** roles and a
   default Pro subscription.
4. You're redirected to **TOTP setup** — scan the QR with an authenticator app and confirm
   the 6-digit code (the secret is stored encrypted).
5. Thereafter, log in at **/auth/admin-login** (email + password) → **TOTP code**.

You can re-run TOTP setup anytime while logged in to rotate your 2FA secret.

---

## Admin tab at a glance

| Card | Purpose |
|------|---------|
| **AI Usage** | LLM call stats by role, plus usage by user |
| **Invite Users** | Create invites with tier + bot access |
| **Users** | Manage roles, tiers, locks, deletions |
| **Global Bot Defaults** | Set bot config applied to all users (unless overridden) |
| **System Config (LLM Models)** | Assign models per role |
| **LLM Overrides / Snapshots** | Swap models at runtime, snapshot & revert |
| **Ollama Config** | Set & test the Ollama endpoint |
| **Platform Status** | Service health, deployment target |
| **Users Usage Analytics** | Per-user quota & usage reports |

---

## Invitations

TradeWiz is invite-only. Create or update invites with an email, **role** (user / trader /
admin), **tier** (free / starter / pro), and — for Pro — **bot access** (`crypto`,
`stock`, `watchdog`). *(Routes: `GET/POST /api/admin/invite`,
`PUT/DELETE /api/admin/invite/<email>`.)*

> Setting **tier = pro** auto-grants the trader role; bot access is only meaningful on Pro
> and is what makes the four bot tabs appear for that user.

---

## User management

- **List users** — roles, tier, bot access, created/last-login, lock status
  (`GET /api/admin/users`).
- **Roles** — add/remove admin / trader / user (`POST /api/admin/users/<id>/role`).
- **Tier & bot access** — set per user (`POST /api/admin/users/<id>/tier`).
- **Lock / unlock** — block sign-in (`POST /api/admin/users/<id>/lock`).
- **Delete** — hard-delete a user and all their data (you can't delete yourself)
  (`DELETE /api/admin/users/<id>/delete`).

---

## LLM configuration

TradeWiz resolves each model role as **DB override → environment variable → built-in
default**, so you can change models without redeploying.

- **System Config** — set models for roles: research, research (fast), pattern, prediction,
  screener, supervisor, bot sentiment, bot risk, and skills
  (`GET/POST /api/admin/config`). Also `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`, `LLM_FAST_MODE`.
- **Runtime overrides & snapshots** — swap a single role's model live, **snapshot** the
  current config, and **revert** if results regress (`/api/admin/llm-models`,
  `/set`, `/snapshot`, `/revert`, `/clear`).

> **Don't assert model quality without measuring.** Snapshot before a swap, compare
> outcomes, and revert if the change underperforms.

### Ollama
View/set/test/clear the global Ollama URL, key, and model
(`/api/admin/ollama-config[/set|/test|/clear]`). All Ollama is **cloud** (`ollama.com`)
with an API key — there is no local daemon.

---

## Bot defaults

Set **global** bot configuration with `POST /api/admin/bot-defaults` (stored against the
user_id=0 sentinel). Per-user settings override globals at read time, so globals act as the
baseline for everyone. Clear one with `DELETE /api/admin/bot-defaults/<key>`.

> **Auto-restart:** every bot that was enabled comes back up automatically after a server
> restart, and the global options-flow scanner always starts. When adding a new bot, wire
> its enable flag and start path into the startup bootstrap.

---

## Usage analytics & exports

- **AI usage** — token + cost aggregation by model, source, day, and top users
  (`GET /api/admin/ai-usage`); quick 24h list (`/api/admin/usage`); per-user drill-down
  (`/api/admin/users/<id>/usage`) and filterable reports (`/api/admin/users/usage`,
  JSON or CSV).
- **Data export** — export trades, analyses, bot logs, LLM usage, journal, or daily P&L as
  grouped JSON (`GET /api/admin/export?dataset=...`).

---

## Support tickets

View all submitted tickets and mark them resolved with admin notes
(`GET /api/admin/support-tickets`, `POST /api/admin/support-tickets/<id>/resolve`).

---

## Service status & incidents

A background checker probes the AI providers (OpenRouter models, Ollama), Yahoo Finance,
the Flask app, and the database roughly every minute, recording **operational / degraded /
outage** states and opening/closing incidents automatically. Surfaced on the **Status**
view and the admin platform card.

---

## Operational notes

- **Deployment:** Docker Compose (PostgreSQL + Gunicorn + nginx + certbot) in production;
  also runnable on Railway / Cloud Run. Detect/override the platform via
  `/api/admin/platform`.
- **Rebuild after code changes:** `docker compose up -d --build`.
- **Database:** dual-dialect (PostgreSQL in prod, SQLite in dev) — always parameterized
  queries.
