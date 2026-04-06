# TradeWiz Codebase Documentation

> AI-powered stock & crypto trading analysis platform with dual trading bots, multi-LLM validation, and Stripe subscription billing.

**Stack:** Python 3.11, Flask, PostgreSQL (prod) / SQLite (dev), yfinance, pandas, numpy, OpenRouter LLM API, TradingView Lightweight Charts, Docker + nginx

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Application Entry Point (app.py)](#application-entry-point)
3. [Analysis Engine](#analysis-engine)
4. [AI Validator](#ai-validator)
5. [Screener](#screener)
6. [Market Sensor](#market-sensor)
7. [Crypto Trading Bot](#crypto-trading-bot)
8. [Stock Trading Bot](#stock-trading-bot)
9. [Authentication & Authorization](#authentication--authorization)
10. [Database Layer](#database-layer)
11. [Billing & Subscriptions](#billing--subscriptions)
12. [Rate Limiting & Decorators](#rate-limiting--decorators)
13. [Skills System](#skills-system)
14. [Frontend SPA](#frontend-spa)
15. [Feature Blueprints](#feature-blueprints)
16. [Infrastructure](#infrastructure)
17. [Module Dependency Map](#module-dependency-map)
18. [API Route Reference](#api-route-reference)

---

## Architecture Overview

```
Browser (SPA)
  |
  ├── TradingView Charts
  ├── Tab-based UI (10+ tabs)
  └── SSE streams for real-time updates
  |
  v
nginx (SSL/TLS, rate limiting, gzip)
  |
  v
Flask/Gunicorn (app.py + 13 blueprints)
  |
  ├── Core Blueprints: auth, docs, billing, skills
  ├── Feature Blueprints: analysis, screener, ipo, tracker,
  │   bot_crypto, bot_stock, admin, user, status
  |
  ├── Analysis Pipeline:
  │   analysis_engine.py → ai_validator.py → screener.py
  │   market_sensor.py (pre-trade health gate)
  |
  ├── Trading Bots (daemon threads, 5-min scan cycles):
  │   crypto_bot/ → BloFin Demo API (paper only)
  │   stock_bot/  → Alpaca Paper / Webull Sandbox
  |
  ├── LLM Integration (OpenRouter + optional Ollama):
  │   3-model setup validation, 4-model 12-month prediction
  │   2-layer bot trade validation (Ollama + 2x OpenRouter)
  |
  └── Data: PostgreSQL (prod) / SQLite (dev)
       yfinance (market data), Stripe (billing)
```

---

## Application Entry Point

**File:** `app.py` (~260 lines)

### What It Does
- Creates Flask app with security hardening (SECRET_KEY guard, session cookies, ProxyFix)
- Registers 13 blueprints (4 core + 9 feature)
- On first HTTP request: runs migrations, starts background checker, auto-starts user bots

### Blueprint Registration Order
| Blueprint | Source | URL Prefix |
|-----------|--------|------------|
| auth_bp | auth.py | /auth |
| docs_bp | docs_blueprint.py | /docs |
| billing_bp | billing_bp.py | /billing |
| skill_bp | skills/skill_bp.py | /api/skills |
| ipo_bp | features/ipo/routes.py | /api |
| status_bp | features/status/routes.py | /api |
| tracker_bp | features/tracker/routes.py | /api |
| bot_crypto_bp | features/bot_crypto/routes.py | /api |
| bot_stock_bp | features/bot_stock/routes.py | /api |
| screener_bp | features/screener/routes.py | /api |
| admin_bp_feat | features/admin/routes.py | /api |
| user_bp | features/user/routes.py | /api |
| analysis_bp | features/analysis/routes.py | /api |

### Routes Defined in app.py
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Redirect to login or render SPA |
| GET | `/api/market-pulse` | SPY/VIX/Fear & Greed (5-min cache) |
| GET | `/features/<path>` | Serve feature static assets |

### Startup Sequence
1. Load `.env` → create Flask app → validate SECRET_KEY
2. Configure session security → attach ProxyFix middleware
3. Register all blueprints
4. On first request (file-locked for multi-worker safety):
   - Run `migrations.run_migrations()` + purge old checks
   - Start background status checker thread
   - After 5s delay: auto-start bots for users with `bot_enabled=1`

---

## Analysis Engine

**File:** `analysis_engine.py` (~2,058 lines)

### What It Does
Multi-strategy technical and fundamental analysis: pattern detection, indicator calculation, trade planning, breakout confirmation, and Qullamaggie scanning.

### Key Functions

| Function | Purpose |
|----------|---------|
| `analyze_ticker(ticker, period, interval)` | **Master orchestrator** — runs all analyses, returns full analysis dict |
| `fetch_stock_data(ticker, period, interval)` | OHLCV data via yfinance |
| `fetch_fundamentals(ticker)` | 12+ categories: valuation, profitability, balance sheet, cash flow, analyst consensus |
| `fetch_fundamentals_crypto(ticker)` | Crypto-specific (market cap, supply, volume) |
| `detect_triangle_pattern(df)` | Detects triangles, wedges, pennants, channels using swing points + trendline fitting |
| `calculate_indicators(df)` | ATR, RSI, MACD, Bollinger Bands, SMA/EMA (8/9/20/50/200), volume analysis |
| `detect_breakout_status(df, pattern)` | 3-candle confirmation + 1.5x volume surge required |
| `generate_trade_plan(df, pattern, indicators, info)` | Entry/SL/TP with Fibonacci extensions, position sizing on $25K |
| `grade_setup(pattern, indicators, rr)` | A+ to F grade based on pattern quality, R:R, volume, RSI, MA alignment |
| `detect_candlestick_patterns(df)` | Inside Day, Outside Day, Doji, Pump/Dump on Close |
| `detect_trendline_tests(df, pattern)` | Candles testing support/resistance with volume/strength scoring |
| `qullamaggie_scan(tickers)` | Scans for High Tight Flags, VCP, Episodic Pivots |

### Key Algorithms
- **Swing point detection:** `scipy.signal.argrelextrema` (order 2-13)
- **Trendline fitting:** `scipy.stats.linregress` with validation (no candle crosses between anchors)
- **Pattern scoring:** Weighted multi-factor (0-100): line validity, touches, recency, compactness, volume decline, apex distance
- **Breakout:** 3 consecutive closes above/below level + 1.5x volume surge
- **Trade targets:** Fibonacci extensions (TP1=0.618x, TP2=1.0x, TP3=1.618x pattern height)

### Called By
- `features/analysis/routes.py` (POST /api/analyze)
- `ai_validator.py` (data summary building)
- Trading bot engines (indicator calculation)

---

## AI Validator

**File:** `ai_validator.py` (~851 lines)

### What It Does
Multi-LLM consensus validation with two pipelines:
1. **Setup Validation** — 3 models in parallel for short-term trade signals
2. **12-Month Prediction** — 4 models (sequential + parallel) with 3/4 quorum

### LLM Models (via OpenRouter)
| Role | Default Model | Used In |
|------|--------------|---------|
| LLM_RESEARCH | claude-sonnet-4-6 | Setup validation (fundamentals), 12-month fact gathering |
| LLM_PATTERN | gemini-2.5-pro-preview | Setup validation (chart analysis) |
| LLM_PREDICTION | deepseek-chat-v3-0324 | Setup validation (price targets), 12-month price action |
| LLM_SUPERVISOR | (optional) | Veto layer for both pipelines |

### Setup Validation Pipeline (`validate_setup`)
```
[Research Model]  ──┐
[Pattern Model]   ──┼── parallel → _build_final_verdict() → [Supervisor veto]
[Prediction Model]──┘
```
- **Risk gates (pre-scoring):** Risk score >= 75, HIGH false breakout, pattern invalid, 3+ red flags
- **Verdicts:** STRONG BUY (avg>=70, 2+ bullish), BUY (avg>=55, 1+ bullish), WAIT, NEUTRAL, AVOID

### 12-Month Prediction Pipeline (`predict_12month`)
```
Phase 1: [Fact Gatherer] → sequential
Phase 2: [Company Health] + [Price Action] → parallel
Phase 3: [Supervisor Review] → sequential
→ _build_investment_verdict() with 3/4 quorum
```
- **INVEST:** 3+ votes + avg_score >= 55
- **PASS:** 2+ pass votes OR avg_score < 35 OR survival < 75%
- **HOLD:** fallback

### Called By
- `features/analysis/routes.py` (POST /api/validate, POST /api/predict)
- `screener.py` (supervisor post-filter)

---

## Screener

**File:** `screener.py` (~1,217 lines)

### What It Does
Multi-category stock scanner with AI vetting. Scans ~2,000 tickers across 10 categories with category-specific LLM evaluation.

### Categories & Ticker Counts
| Category | Tickers | Price Range | Key Filter |
|----------|---------|-------------|------------|
| lowcap | ~65 | $2-$15 | Market cap < $2B |
| midcap | ~60 | $15-$100 | Market cap $2B-$20B |
| largecap | ~70 | $50+ | Market cap > $20B |
| etf | ~40 | — | Thematic growth ETFs |
| metals_mining | ~40 | — | Gold, silver, copper, lithium, uranium |
| crypto | ~15 | — | BTC, ETH, SOL, etc. |
| ai | ~30 | — | Pure-play AI, chips, cloud, robotics |
| gainers | ~2,000 | — | Top % gainers (1d/1w/3mo) |
| losers | ~2,000 | — | Top % losers (recovery plays) |

### Pipeline
```
scan_*_candidates() → _parallel_vet(batch_size=5) → _categorize_results()
                           │                              │
                      vet_*_candidate()              opportunities / risky / avoided
                      (LLM_SCREENER model)           (sorted by confidence)
                           │
                      [Optional supervisor post-filter]
```

### Key Functions
| Function | Purpose |
|----------|---------|
| `run_screener(category, limit, sectors)` | Full pipeline: scan → vet → categorize |
| `get_hot_sectors(period)` | LLM identifies trending sectors/themes |
| `scan_*_candidates()` | Category-specific yfinance data fetch |
| `vet_*_candidate()` | Category-specific LLM vetting with tailored prompts |

---

## Market Sensor

**File:** `market_sensor.py` (~305 lines)

### What It Does
Pre-trade market health check for trading bots. Fetches broad indicators, classifies as HEALTHY / CAUTION / DANGER. Results cached 30 minutes.

### Indicators
| Market | Indicators |
|--------|-----------|
| Crypto | BTC & ETH: price, 24h/5d change%, RSI(14), volume ratio |
| Stock | SPY & QQQ: price, 1d/5d change%; VIX level |

### Assessment
- **LLM-based** (if API key configured): Uses LLM_BOT_SENTIMENT model, temperature 0.1
- **Rule-based fallback:** Crypto DANGER if BTC -8% (24h); Stock DANGER if SPY -3% (1d) or VIX > 35

### Called By
- `crypto_bot/bot_engine.py` (every scan cycle)
- `stock_bot/stock_engine.py` (every scan cycle)

---

## Crypto Trading Bot

**Directory:** `crypto_bot/` (4 files, ~2,100 lines total)

### bot_engine.py (~894 lines) — Core Trading Daemon

**Scan Cycle (every 300s):**
1. Kill switch check + self-healing (30-min auto-recovery)
2. Adaptive learning refresh (every 30 min)
3. Daily goal check ($500 target)
4. Market sensor pre-check (skip on DANGER, tighten on CAUTION)
5. Per-coin processing (configurable coin list)
6. Exit management for open positions

**9 Trading Strategies:**
| Strategy | Signal Logic |
|----------|-------------|
| macd_cross | Bullish/bearish MACD cross + RSI 28-68 + price vs EMA20 |
| ema_trend | SMA8 crosses EMA20 + MACD confirmation |
| rsi_reversion | RSI < 35 (buy) or > 65 (sell) + MACD direction |
| momentum | Price > SMA50 + volume surge > 1.2x + RSI 45-75 |
| bb_reversion | BB position <= 10% or >= 90% + RSI + MACD |
| grid_reversion | Price > 1.5x ATR from SMA50 + trend direction |
| trend_dca | Pullback to EMA20 in established trend |
| doji_reversal | Doji candle + RSI extreme + prior candle direction |
| pump_on_close | Volume surge > 1.5x + large body + close at extreme |

**Self-Learning (every 30 min):**
- Position scaling: 0.7x (win rate < 35%) to 1.3x (win rate >= 60%) based on 7-day history
- Strategy blacklisting: < 20% win rate on 5+ trades
- Confidence threshold adaptation: 0.35-0.55 in CAUTION mode

**Exit Logic:**
- Stop Loss: price - 2x ATR (buy) / price + 2x ATR (sell)
- Take Profit: price + 3x ATR (buy) / price - 3x ATR (sell)
- Trailing Stop: If P&L >= 1.5% but retraced > 50%
- Time Exit: Open > 24h with < 0.3% P&L

### crypto_validator.py (~542 lines) — Multi-LLM Trade Validation

**2-Layer Consensus:**
```
Gate 1: Ollama (local, 30s) ──────────────────┐
Gate 2a: OpenRouter Sentiment (parallel, 90s) ─┼── Majority vote (2/3 or 1/2 if Ollama down)
Gate 2b: OpenRouter Risk (parallel, 90s) ──────┘    + optional supervisor veto
```
- Timeout fallback: auto-approve with 0.5 confidence (paper trading)
- Also provides: `detect_direction()` (bullish/bearish/neutral) and `get_trending_crypto()`

### risk_manager.py (~196 lines) — Safety Gates

**10 Sequential Gates in `can_open_position()`:**
1. Kill switch active
2. Bot not enabled
3. Paper-only enforcement (BLOFIN_DEMO must = "1")
4. Daily loss limit (-$500 → activates kill switch)
5. Daily trade count (max 25)
6. Position size (max 10% of equity)
7. Max open positions (max 6)
8. Duplicate position for same coin
9. Consecutive loss cooldown (5+ losses → up to 4h pause)
10. Per-coin daily loss cap (-$75)

### blofin_client.py (~469 lines) — BloFin Demo API

- **COIN_MAP:** 12 pre-configured coins (BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, DOT, LINK, MATIC, ATOM)
- **YF_TICKER_MAP:** Special yfinance mappings (SUI→SUI20947-USD, PEPE→PEPE24478-USD, etc.)
- **`ensure_coin_in_map()`:** Dynamic coin addition at runtime
- **Safety:** Raises RuntimeError if BLOFIN_DEMO != "1"
- **Operations:** place_order, close_position, get_balance, get_positions, get_ticker_price
- **Contract sizing:** Converts coin amounts to contracts respecting lot_size/min_size specs

---

## Stock Trading Bot

**Directory:** `stock_bot/` (4 files, ~2,000 lines total)

### stock_engine.py — Core Trading Daemon

Nearly identical architecture to crypto bot with stock-specific adjustments:
- **Default stocks:** AAPL, TSLA, NVDA, MSFT, AMD
- **Market hours enforcement:** 9:30 AM - 4:00 PM ET (optional extended hours)
- **ATR thresholds:** >= 0.08% of price (vs 0.15% for crypto)
- **SL/TP:** 1.5x ATR stop loss, 2.5x ATR take profit (tighter than crypto)
- **Fee estimate:** $0.01/share (SEC + FINRA)
- Same 9 strategies with slightly adjusted RSI/MACD thresholds

### stock_validator.py — LLM Validation

Same 2-layer consensus as crypto but with stock-specific context:
- Earnings risk awareness, PDT rules, market hours, sector rotation considerations

### stock_risk_manager.py — Safety Gates

Same 10-gate structure plus:
- **PDT Rule:** If equity < $25K, limit day trades to 3 per 5-day rolling window
- **Market hours gate:** Rejects trades when market closed
- **Max open positions:** 8 (vs 6 for crypto)
- **Direction bias default:** long_only (vs both for crypto)

### broker_client.py — Alpaca & Webull Wrappers

**AlpacaClient:**
- `paper=True` hardcoded (no live trading)
- Bracket orders with SL/TP
- Day trade count tracking for PDT

**WebullClient:**
- HMAC-SHA1 signed requests per Webull OpenAPI spec
- Sandbox mode by default (WEBULL_SANDBOX="1")
- Separate SL order after main order

**STOCK_MAP:** 15 pre-configured stocks + DEFAULT_STOCKS list
**`is_market_open()`:** Timezone-aware US market hours check with extended hours support

---

## Authentication & Authorization

**File:** `auth.py` (~945 lines)

### Auth Methods
| Method | Flow |
|--------|------|
| Email/Password | Registration (invite-only) → bcrypt hash → login |
| Google OAuth2 | authlib redirect → callback → upsert user |
| Dev Login | POST /auth/dev-login (development only, grants admin+trader) |
| TOTP 2FA | Admin-only: QR code setup → pyotp verification |

### Key Routes
| Route | Purpose |
|-------|---------|
| GET/POST /auth/login | Login page |
| POST /auth/email-login | Email + password auth |
| GET /auth/google | Google OAuth redirect |
| GET /auth/google/callback | OAuth callback handler |
| GET/POST /auth/totp-setup | TOTP QR generation + confirmation |
| GET/POST /auth/totp-verify | TOTP code verification |
| GET/POST /auth/admin-setup | First-time admin password setup |
| GET /auth/logout | Session cleanup |

### User Model
`User(UserMixin)` with: id, google_id, email, name, picture_url, roles, tier, password_hash, totp_secret

### TOTP Encryption
- Fernet symmetric encryption using SHA256-derived key from SECRET_KEY
- `encrypt_totp_secret()` / `decrypt_totp_secret()`

---

## Database Layer

**File:** `db.py` (~175 lines)

### Dual Backend
- **PostgreSQL** (when DATABASE_URL set): ThreadedConnectionPool (2-10 connections), RealDictCursor
- **SQLite** (fallback): Direct connections with Row factory

### Helper Functions
| Function | Purpose |
|----------|---------|
| `get_db()` | Get connection from pool |
| `put_db(conn)` | Return connection to pool |
| `query(sql, params)` | SELECT → list of dicts |
| `query_one(sql, params)` | SELECT → single dict or None |
| `execute(sql, params)` | INSERT/UPDATE/DELETE with commit |
| `execute_many(statements)` | Multiple statements in transaction |

### Placeholder Convention
```python
P = "%s" if IS_POSTGRES else "?"
```
All SQL uses `P` for parameterized queries. Never string-interpolate.

---

**File:** `migrations.py` (~742 lines)

### Tables
| Table | Purpose |
|-------|---------|
| users | User accounts (email, google_id, password_hash, totp_secret) |
| user_roles | RBAC roles (admin, trader) |
| user_api_keys | Encrypted broker API keys |
| invites | Pre-registration invites with tier + bot access |
| searches | Cached analysis results (3-day TTL) |
| journal_entries | Trade journal entries |
| weekly_goals | Weekly P&L accumulation |
| account_config | User account settings |
| service_checks | Health check results |
| service_incidents | Service incident log |
| bot_config | Bot settings (key-value per user) |
| bot_trades | Open/closed trades with P&L |
| bot_daily_pnl | Daily P&L aggregates (crypto) |
| stock_daily_pnl | Daily P&L aggregates (stock) |
| bot_log | Audit log for bot events |
| user_subscriptions | Stripe billing (tier, status, bot_access) |
| llm_usage_log | Per-user LLM call tracking |
| skill_jobs | Skill execution jobs |

---

## Billing & Subscriptions

**Files:** `subscriptions.py` (~290 lines), `billing_bp.py` (~114 lines)

### Subscription Tiers
| Tier | LLM Calls/Day | Bot Access | Price |
|------|---------------|------------|-------|
| free | 5 | No | $0 |
| starter | 25 | No | STRIPE_PRICE_STARTER |
| pro | 100 | Invite-only | STRIPE_PRICE_PRO |
| admin | Unlimited | Yes | — |

### Stripe Integration
- `create_checkout_session()` → Stripe Checkout
- `create_portal_session()` → Stripe Billing Portal
- `handle_webhook()` → Processes: checkout.completed, subscription.updated/deleted, payment.failed

### Routes
| Route | Purpose |
|-------|---------|
| GET /billing/status | Tier, usage, bot_access |
| POST /billing/checkout/<tier> | Create Stripe checkout |
| POST /billing/portal | Create billing portal |
| POST /billing/webhook | Stripe webhook endpoint |

---

## Rate Limiting & Decorators

**Files:** `rate_limiter.py` (~174 lines), `decorators.py` (~103 lines)

### Rate Limiter
- Rolling 24-hour window from `llm_usage_log` table
- Thread-local context for LLM user tracking
- `check_rate_limit(user_id)` → {allowed, tier, used, limit, remaining}
- `has_bot_access(user_id, bot_type)` → checks invite-only bot_access field

### Route Decorators
| Decorator | Requires |
|-----------|----------|
| `@login_required` | Any authenticated user |
| `@admin_required` | Admin role |
| `@trader_required` | Admin or trader role |
| `@pro_required` | Bot access (invite-only) |
| `@bot_access_required(type)` | Granular bot access ("crypto"/"stock") |
| `@llm_rate_limit(source, count)` | LLM quota headroom |

---

## Skills System

**Directory:** `skills/` (~8 files)

### Architecture
```
skill_bp.py (Flask routes) → registry.py (YAML catalog) → executor.py (job mgmt) → runner.py (execution)
                                                                                       ↓
                                                                              llm_adapter.py (OpenRouter)
                                                                              outputs.py (DOCX/XLSX/JSON)
                                                                              chart_generator.py (matplotlib)
```

### Skill Catalog (YAML definitions in `skills/catalog/`)
| Domain | Skills |
|--------|--------|
| financial-analysis | DCF Valuation, Comparable Analysis, Financial Health |
| equity-research | Earnings Analysis, Sector Analysis |
| wealth-management | Client Report, Client Review, Financial Plan, Investment Proposal, Portfolio Rebalance, Tax-Loss Harvest |

### Routes
| Route | Purpose |
|-------|---------|
| GET /api/skills/catalog | List skills by tier |
| GET /api/skills/catalog/<id> | Skill detail |
| POST /api/skills/launch | Launch job (returns job_id) |
| GET /api/skills/jobs/<id>/status | Job status |
| GET /api/skills/jobs/<id>/stream | SSE progress stream |
| GET /api/skills/jobs/<id>/output | Download output file |

### Tier Gating
free=0, basic=1, pro=2, admin=3. Concurrent job limits: free=1, basic=2, pro=3, admin=999.

---

## Frontend SPA

### File Structure (~6,600 lines JS, ~12 CSS files)
```
static/js/
├── core.js          (1,100 lines) — App shell, auth, billing, market pulse, tab mgmt
├── init.js          (43 lines)    — Event binding, runs last
└── features/
    ├── analysis/    (2,235 lines) — TradingView charts, pattern rendering, AI validation
    ├── bot_crypto/  (1,052 lines) — Crypto bot dashboard, P&L charts, coin manager
    ├── research/    (727 lines)   — Skill catalog, job execution, SSE streaming
    ├── screener/    (676 lines)   — Multi-category screener UI, sector filtering
    ├── bot_stock/   (548 lines)   — Stock bot dashboard
    ├── ipo/         (451 lines)   — IPO/pre-IPO/VC scanner
    ├── admin/       (392 lines)   — User management, invites
    ├── tracker/     (224 lines)   — Trade journal, goals
    ├── status/      (162 lines)   — Service health dashboard
    └── user/        (124 lines)   — Settings modal (API keys)
```

### SPA Architecture
- **Single HTML entry:** `templates/index.html` (1,246 lines)
- **Tab-based navigation:** 10+ tabs via `switchTab(tab)` in core.js
- **No build system:** Vanilla JS, no bundler/transpiler
- **Charts:** TradingView Lightweight Charts (candlestick + volume + MA overlays + trendlines)
- **Real-time:** SSE (EventSource) for skill jobs and earnings analysis
- **Polling:** Market pulse (5 min), bot status (2-5s when visible)

### Tab Layout
| Tab | Content | Access |
|-----|---------|--------|
| Analyzer | Chart + right panel (pattern, trade plan, indicators, AI) | All users |
| Breakout Scanner | Qullamaggie scan results | All users |
| Tracker | Trade journal + weekly goals | All users |
| Screener | Multi-category stock screener | All users |
| Research | Skill catalog + job runner | Tier-gated |
| Fin Skills | Financial analysis hub | Tier-gated |
| IPOs | IPO/pre-IPO/VC deals | All users |
| Crypto Trading | Crypto bot dashboard | Invite-only |
| Stock Trading | Stock bot dashboard | Invite-only |
| Status | Service health | Admin only |
| Admin | User management | Admin only |

### Theme
Dark theme with teal/blue accents: `--bg-primary: #0b0e14`, `--accent-green: #00c896`, `--accent-blue: #4f8aff`

---

## Feature Blueprints

**Directory:** `features/` — Each feature is a package with `routes.py`

| Feature | Key Routes | Purpose |
|---------|-----------|---------|
| analysis | /api/analyze, /api/validate, /api/predict, /api/ai-status | Core ticker analysis + AI |
| screener | /api/screener, /api/screener/hot-sectors | Stock screening |
| ipo | /api/ipos, /api/ipo-platforms | IPO discovery |
| tracker | /api/journal, /api/goals | Trade journal + goals |
| bot_crypto | /api/bot/start, /api/bot/stop, /api/bot/kill, /api/bot/status | Crypto bot control |
| bot_stock | /api/stock-bot/start, /api/stock-bot/stop, /api/stock-bot/status | Stock bot control |
| admin | /api/admin/users, /api/admin/invite | User management |
| user | /api/settings, /api/me | User profile + API keys |
| status | /api/status, /api/status/incidents, /api/status/check | Service health |

---

## Infrastructure

### Docker Compose Services
| Service | Image | Purpose |
|---------|-------|---------|
| db | postgres:16-alpine | Database (pgdata volume) |
| app | Custom Dockerfile | Flask/Gunicorn (2 workers, 4 threads, port 5000) |
| nginx | nginx:alpine | Reverse proxy (80/443), SSL/TLS, rate limiting |
| certbot | certbot | Let's Encrypt auto-renewal (every 12h) |

### Nginx Highlights
- TLS 1.2/1.3, HSTS (2 years), OCSP stapling
- Rate limiting: 10 req/s general, tighter on /api/ipos, /api/predict
- SSE routes: buffering off, 600s timeouts
- Static assets: 7-day cache

### Key Environment Variables
| Category | Variables |
|----------|----------|
| LLM Models | LLM_RESEARCH, LLM_PATTERN, LLM_PREDICTION, LLM_SCREENER, LLM_SUPERVISOR |
| Bot Models | LLM_BOT_SENTIMENT, LLM_BOT_RISK, OLLAMA_URL, OLLAMA_MODEL |
| Crypto Bot | BLOFIN_API_KEY/SECRET/PASSPHRASE, BLOFIN_DEMO=1 |
| Stock Bot | ALPACA_API_KEY/SECRET_KEY, WEBULL_* |
| Billing | STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_STARTER/PRO |
| Auth | SECRET_KEY, GOOGLE_CLIENT_ID/SECRET, ADMIN_EMAIL |
| Database | DATABASE_URL, POSTGRES_PASSWORD |
| Security | ENCRYPTION_KEY (Fernet, for API key storage) |

### Health Check (`healthcheck.sh`)
Checks: disk/memory/load, Docker containers, PostgreSQL, nginx (config + SSL), Flask endpoints, external API reachability, error logs. Supports `--fix` for auto-remediation.

---

## Module Dependency Map

```
app.py
├── auth.py ← db.py, migrations.py, crypto_utils.py
├── docs_blueprint.py
├── billing_bp.py ← subscriptions.py ← db.py
│                 ← rate_limiter.py ← db.py
│                 ← decorators.py ← rate_limiter.py
├── skills/skill_bp.py ← registry.py, executor.py, runner.py, llm_adapter.py, outputs.py
├── features/analysis/routes.py ← analysis_engine.py ← yfinance, scipy, pandas, numpy
│                                ← ai_validator.py ← OpenRouter API, rate_limiter.py
│                                                   ← shared/prompts/analysis.py
├── features/screener/routes.py ← screener.py ← yfinance, ai_validator._call_openrouter
│                                              ← shared/prompts/screener.py
├── features/bot_crypto/routes.py ← crypto_bot/bot_engine.py ← crypto_validator.py (Ollama + OpenRouter)
│                                                             ← risk_manager.py ← db.py
│                                                             ← blofin_client.py ← blofin SDK
│                                                             ← market_sensor.py ← yfinance, OpenRouter
├── features/bot_stock/routes.py ← stock_bot/stock_engine.py ← stock_validator.py (Ollama + OpenRouter)
│                                                             ← stock_risk_manager.py ← db.py
│                                                             ← broker_client.py ← alpaca-py, webull SDK
│                                                             ← market_sensor.py
└── shared/helpers.py ← db.py (utility functions used across features)
```

---

## API Route Reference

### Analysis
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | /api/analyze | login | Analyze ticker (returns chart data + indicators + pattern) |
| POST | /api/validate | login + llm_rate | Multi-LLM setup validation |
| POST | /api/predict | login + llm_rate | 12-month investment prediction |
| GET | /api/ai-status | login | LLM configuration status |
| GET | /api/history | login | Search history |
| GET | /api/market-pulse | login | SPY/VIX/Fear & Greed |

### Screener
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | /api/screener | login + llm_rate | Run multi-category screener |
| GET | /api/screener/hot-sectors | login | Trending sectors |

### Trading Bots
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /api/bot/status | bot_access | Crypto bot status |
| POST | /api/bot/start | bot_access | Start crypto bot |
| POST | /api/bot/stop | bot_access | Stop crypto bot |
| POST | /api/bot/kill | bot_access | Emergency kill (close all) |
| GET | /api/bot/trades | bot_access | Trade history |
| POST | /api/bot/trades/<id>/close | bot_access | Close specific trade |
| GET | /api/bot/config | bot_access | Bot configuration |
| POST | /api/bot/config | bot_access | Update bot config |
| GET | /api/stock-bot/status | bot_access | Stock bot status |
| POST | /api/stock-bot/start | bot_access | Start stock bot |
| POST | /api/stock-bot/stop | bot_access | Stop stock bot |
| GET | /api/stock-bot/trades | bot_access | Stock trade history |

### Skills
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /api/skills/catalog | login | List available skills |
| POST | /api/skills/launch | login + llm_rate | Launch skill job |
| GET | /api/skills/jobs/<id>/stream | login | SSE progress stream |
| GET | /api/skills/jobs/<id>/output | login | Download output |

### IPO
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | /api/ipos | login + llm_rate | Scan IPO/pre-IPO/VC deals |
| GET | /api/ipo-platforms | login | Investment platform directory |

### User & Admin
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /api/me | login | Current user profile |
| GET | /api/settings | login | Load settings |
| POST | /api/settings | login | Save API keys + preferences |
| GET | /api/admin/users | admin | List all users |
| POST | /api/admin/users/<id>/tier | admin | Set user tier + bot access |
| GET | /api/admin/invite | admin | Pending invites |
| POST | /api/admin/invite | admin | Create invite |

### Billing
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /billing/status | login | Tier, usage, bot_access |
| POST | /billing/checkout/<tier> | login | Stripe checkout |
| POST | /billing/portal | login | Billing portal |
| POST | /billing/webhook | none | Stripe webhook |

### Other
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /api/journal | login | Trade journal entries |
| POST | /api/journal | login | Add journal entry |
| DELETE | /api/journal/<id> | login | Delete entry |
| GET | /api/goals | login | Weekly goals |
| GET | /api/status | login | Service health |
| POST | /api/status/check | admin | Force health check |
| GET | /docs/ | none | Documentation index |
| GET | /docs/<slug> | none | Documentation page |
