# AI Stock Analyst - Claude Code Instructions

## Project Overview

AI-powered stock & crypto trading analysis platform. Flask backend + vanilla JS frontend with TradingView charts, dual trading bots (crypto + stock), multi-LLM validation pipeline, and Stripe subscription billing.

**Stack:** Python 3.11, Flask, PostgreSQL (prod) / SQLite (dev), yfinance, pandas, numpy, OpenRouter LLM API, Docker + nginx

## Architecture

```
Frontend (Flask templates + vanilla JS) → Flask API (40+ routes) → Analysis Engine / Trading Bots / Screeners → Multi-LLM Validation → Broker APIs (BloFin, Alpaca)
```

### Core Modules

| Module | Purpose |
|--------|---------|
| `app.py` | Main Flask app, all API routes |
| `analysis_engine.py` | Technical & fundamental analysis, pattern detection |
| `ai_validator.py` | Multi-LLM consensus validation pipeline |
| `screener.py` | Multi-category stock scanner with AI vetting |
| `market_sensor.py` | Pre-trade market health check |
| `auth.py` | Auth (email + Google OAuth2, TOTP 2FA) |
| `db.py` | Database abstraction (PostgreSQL/SQLite) |
| `migrations.py` | Schema creation & migrations |
| `rate_limiter.py` | LLM quotas + bot access control (invite-only) |
| `decorators.py` | Route decorators (login, admin, bot_access, rate_limit) |
| `subscriptions.py` | Stripe billing integration |
| `crypto_bot/` | Crypto trading bot (engine, validator, risk, BloFin client) |
| `stock_bot/` | Stock trading bot (engine, validator, risk, Alpaca/Webull client) |

## Key Conventions

### Database
- Dual-dialect SQL: `P = "%s" if IS_POSTGRES else "?"` — always use parameterized queries with `P` placeholder
- Get current user: `_uid()` returns user ID or None
- Connection helper: `get_db()` returns a connection from the pool

### Code Style
- No type annotations on existing code — don't add them unless writing new modules
- Logging: use `logging` module + write to `bot_log` DB table for bot events
- Error handling: graceful degradation — if LLM fails, fall back to rule-based logic
- Route decorators: `@login_required`, `@admin_required`, `@pro_required`, `@bot_access_required`, `@llm_rate_limit`

### LLM Integration
- All LLM calls go through OpenRouter API (`ai_validator.py`, `crypto_validator.py`, `stock_validator.py`, `screener.py`)
- Models are configurable via env vars (e.g., `OPENROUTER_MODEL_RESEARCH`, `OPENROUTER_MODEL_PATTERN`)
- Response format: always request JSON from LLMs, parse with fallback error handling
- Rate limiting per subscription tier (free/starter/pro)
- LLM validators for bots are biased toward approving trades (paper trading mode)

### Trading Bots
- **Paper trading only** — BloFin demo mode and Alpaca paper mode are enforced
- **Invite-only access** — Bot access is NOT part of any subscription plan; granted per-user via `bot_access` field in `user_subscriptions` table
- 9 strategies each (MACD, EMA Trend, RSI Reversion, Momentum, BB Reversion, Grid, Trend DCA, Doji Reversal, Pump/Dump on Close)
- Risk gates: kill switch → bot enabled → daily loss ($500 limit) → trade count → position size → max positions (6-8) → duplicates → PDT (stocks) → market hours (stocks)
- **Self-learning**: Adaptive position sizing (scale 0.7x-1.3x based on 7-day win rate), strategy blacklisting (<20% win rate), adaptive CAUTION thresholds
- **Self-healing**: Auto-recover from kill switch after 30 min cooldown
- **Daily goal**: $500 target tracked per bot, configurable via `daily_goal` / `stock_daily_goal` config keys
- Scan cycle: 300s default (configurable `scan_interval_sec`)
- **Dynamic coin support**: Users can add any crypto coin — `ensure_coin_in_map()` in `blofin_client.py` auto-registers with correct yfinance ticker
- **YF_TICKER_MAP**: Maps coins with non-standard yfinance tickers (SUI→SUI20947-USD, PEPE→PEPE24478-USD, etc.)

### Reporting Dashboard
- `GET /api/bot/dashboard` — single endpoint returning comprehensive stats (P&L by period, win rate, strategies, top assets, streaks, daily goal)
- Frontend tiles: Total P&L, Win Rate, Avg P&L, Today/Week/Month, Streak, Daily Goal, Strategy table, Top Assets table, Crypto vs Stock comparison

### Frontend
- Single-page app in `static/js/app.js` (~250 KB)
- TradingView Lightweight Charts for candlestick rendering
- No build system — vanilla JS, no bundler/transpiler

## Important Rules

1. **Live trading is opt-in per user** — paper mode remains the default for every bot. Live orders only route when the user has explicitly set `cb_mode='live'` (or equivalent for other bots). The Alpaca client receives `paper=False` from `_get_broker(user_id)` only in this case. When a bot starts in live mode, log a `LIVE TRADING ENABLED` warning. Risk gates (kill switch, daily loss limit, max positions, hard stop) apply identically to live and paper. Webull stays sandbox-only.
2. **Always use parameterized queries** — never string-interpolate SQL
3. **Keep LLM fallbacks** — every LLM call path must have a non-LLM fallback
4. **Don't break the scan loop** — bot engines run continuously; exceptions must be caught within the loop
5. **Respect rate limits** — check `rate_limiter.py` quotas before adding new LLM call paths
6. **Test with SQLite first** — dev uses SQLite, ensure SQL is dialect-compatible
7. **Bot access is invite-only** — never expose bot features in public subscription plans
8. **Bot should actively trade** — LLM validators biased toward approving for paper trading, cooldowns kept short
9. **Always rebuild after changes** — `docker compose up -d --build` after every code change
10. **Auto-restart all bots on container restart** — every bot that was enabled before a restart/rebuild must come back up automatically. The bootstrap lives in `_auto_start_bots()` in `app.py`, invoked from `_on_startup()` post-fork. It iterates `bot_config` enable flags (`bot_enabled` / `stock_bot_enabled` / `wd_enabled` / `cb_enabled`) per user and calls each engine's start function, then unconditionally starts the global options-flow scanner. Whenever a new bot is added, register its enable-flag + start path in `_auto_start_bots()`.
11. **Test every new feature** — for any new route, helper, or behavior change, add at minimum:
    - **Unit test** in `tests/` covering the happy path + 1–2 edge cases (use `unittest.TestCase`, see `tests/test_routes.py` for the convention).
    - **Smoke check** that exercises the change end-to-end against the running container (e.g. `docker compose exec app python -c "..."` confirming the new module imports, the new route resolves, the data path round-trips). Smoke checks that already exist as one-off `docker compose exec` invocations should be promoted to `tests/test_<feature>.py` after they pass.
    - Run the full suite (`docker compose exec app python -m pytest tests/ -v`) before declaring a feature done. Do not mark a task `completed` if tests are red.
12. **Net-new features ship on a branch + PR** — never commit a new feature directly to `main`. Cut a feature branch, commit there, push, and open a pull request against `main` for review. Trivial fixes (typos, one-line config) may go direct, but anything that adds a route, module, or behavior gets its own branch and PR.

## Running Locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Fill in API keys
python migrations.py
python app.py  # http://localhost:5000
```

## Production Deployment

```bash
docker-compose up -d --build  # PostgreSQL + Flask/Gunicorn + nginx + certbot
```

Also deployable on Railway via `railway.toml`.

## File Structure

```
├── app.py                    # Flask app + API routes (incl /api/bot/dashboard)
├── analysis_engine.py        # Technical/fundamental analysis
├── ai_validator.py           # Multi-LLM validation
├── screener.py               # Stock screener
├── market_sensor.py          # Market health check
├── auth.py                   # Authentication
├── db.py                     # Database layer
├── migrations.py             # Schema
├── rate_limiter.py           # LLM quotas + invite-only bot access
├── decorators.py             # Route decorators
├── subscriptions.py          # Stripe billing
├── crypto_bot/               # Crypto bot package
│   ├── bot_engine.py         # Self-learning, self-healing, daily goal
│   ├── crypto_validator.py   # LLM trade validation (paper-trade biased)
│   ├── risk_manager.py       # Risk gates ($500 daily loss, 6 max positions)
│   └── blofin_client.py      # BloFin API + dynamic COIN_MAP + YF_TICKER_MAP
├── stock_bot/                # Stock bot package
│   ├── stock_engine.py       # Self-learning, self-healing, daily goal
│   ├── stock_validator.py    # LLM trade validation (paper-trade biased)
│   ├── stock_risk_manager.py # Risk gates ($500 daily loss, 8 max positions)
│   └── broker_client.py      # Alpaca/Webull paper trading
├── static/js/app.js          # Frontend SPA
├── static/css/style.css      # Styles (incl dashboard tiles)
├── templates/                # HTML templates
├── docker-compose.yml        # Production stack
├── nginx/nginx.conf          # Reverse proxy
├── requirements.txt          # Python deps
└── .env.example              # Env var template
```
