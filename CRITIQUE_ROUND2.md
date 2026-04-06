# TradeWiz — Round 2 Critique
## Security, Infrastructure, AI Architecture Review
### By: AI & Security Architect | Infinity Ventures Group LLC

---

## 1. SECURITY REVIEW

### CRITICAL (Fix Before Any Public Launch)

| # | Finding | File | Impact | Fix |
|---|---------|------|--------|-----|
| S1 | **.env with real API keys in git** | .env | All accounts compromised | Revoke ALL keys now. Remove .env from git history. Use Secret Manager. |
| S2 | **TOTP encryption uses weak key derivation** | auth.py:104-108 | Admin TOTP bypass if SECRET_KEY leaks | Switch to `crypto_utils.encrypt()` which uses proper Fernet |
| S3 | **SECRET_KEY fallback to "dev-secret-key-change-me"** | app.py:26 | Session hijack in production | Make SECRET_KEY mandatory (no fallback) |

### HIGH (Fix Before Beta Users)

| # | Finding | File | Impact | Fix |
|---|---------|------|--------|-----|
| S4 | **No CSRF on API POST endpoints** | All feature routes | Cross-site request forgery on bot start/stop/kill | Add `SameSite=Strict` cookies OR CSRF token header validation |
| S5 | **No rate limit on login endpoints** | auth.py | Brute force password attacks | Add IP-based rate limit: 5 attempts/min, lockout after 10 |
| S6 | **User can override daily loss limit** | bot config routes | User disables safety limits | Enforce max cap: user can lower but never exceed admin-set maximum |
| S7 | **No account lockout after failed logins** | auth.py | Credential stuffing | Track failed attempts per email, lock after 5 failures for 15 min |

### MEDIUM (Fix Before Scale)

| # | Finding | Fix |
|---|---------|-----|
| S8 | Kill switch can be self-healed (30 min timer) | Admin-only deactivation after manual review |
| S9 | innerHTML with API data (XSS surface) | Use textContent for plaintext, DOMPurify for HTML |
| S10 | Session cookie SameSite=Lax (not Strict) | Change to Strict for API-only flows |
| S11 | No Content-Security-Policy header | Add CSP header via nginx |
| S12 | No request body size limit in Flask | Add `MAX_CONTENT_LENGTH = 10 * 1024 * 1024` |

### Comparison: TradeWiz vs Industry Standards

| Feature | TradeWiz | TradingView | Robinhood | Bloomberg |
|---------|----------|-------------|-----------|-----------|
| Parameterized SQL | Yes | Yes | Yes | Yes |
| API key encryption at rest | Yes (Fernet) | Yes | Yes | Yes |
| Paper-only enforcement | Yes (hardcoded) | N/A | N/A | N/A |
| CSRF protection | Partial (forms only) | Full | Full | Full |
| Rate limiting (auth) | Missing | Yes | Yes | Yes |
| MFA | Admin only (TOTP) | Optional | Required | Required |
| Webhook signature verification | Yes (Stripe) | Yes | Yes | Yes |

**Verdict:** Security is 6/10. Fundamentals are solid (SQL injection, encryption, paper-only). Missing standard web security headers and auth rate limiting.

---

## 2. INFRASTRUCTURE REVIEW

### Current Architecture Problems

| # | Problem | Impact | Fix |
|---|---------|--------|-----|
| I1 | **Single server, single process** | One crash takes down everything | Separate web + bot workers |
| I2 | **In-memory caches (Python dicts)** | Lost on restart, no sharing between workers | Redis or Memorystore |
| I3 | **Bot threads compete with web requests for CPU** | Slow API responses during scan cycles | Separate bot worker process |
| I4 | **No CDN for static assets** | Every JS/CSS request hits Flask | Cloud CDN or Cloudflare |
| I5 | **Certbot SSL renewal** | Can fail silently, manual recovery | Managed SSL (Cloud Run or Cloudflare) |
| I6 | **No health check endpoint for orchestrator** | Docker restart on OOM, no graceful degradation | Add `/healthz` endpoint |
| I7 | **No database connection pooling limits** | 10 max connections, bot threads can exhaust pool | PgBouncer or Cloud SQL Proxy |
| I8 | **No log aggregation** | Logs in Docker stdout, no search or alerting | Cloud Logging or Datadog |
| I9 | **No backup automation** | Manual pg_dump only | Automated daily backups with retention |
| I10 | **Gunicorn 2 workers + 8 threads** | Can handle ~160 concurrent requests max | Auto-scale with Cloud Run |

### Recommended Architecture Tiers

**Tier 1: Quick Wins (Do Now, $0 cost)**
- Add `/healthz` health check endpoint
- Set `MAX_CONTENT_LENGTH` in Flask
- Add nginx `proxy_buffering on` for SSE fix
- Add Content-Security-Policy header in nginx
- Set `gunicorn --preload` for faster worker spawns

**Tier 2: Before 100 Users ($20/mo extra)**
- Cloudflare free tier (CDN + DDoS + managed SSL)
- Separate bot worker container in docker-compose
- Automated pg_dump backup cron job
- Log rotation with `logrotate`

**Tier 3: Before 1000 Users ($100/mo extra)**
- Cloud Run or Railway (auto-scaling)
- Managed PostgreSQL (Cloud SQL or Supabase)
- Redis cache for market data + LLM responses
- Sentry.io for error tracking (free tier: 5K events/mo)

### Comparison: Current vs Competitors

| Feature | TradeWiz | Trade Ideas | TrendSpider | QuantConnect |
|---------|----------|-------------|-------------|-------------|
| Auto-scaling | No | Yes | Yes | Yes |
| CDN | No | Yes | Yes | Yes |
| Health monitoring | Basic | Full | Full | Full |
| Multi-region | No | Yes | Yes | Yes |
| Uptime SLA | None | 99.9% | 99.5% | 99.9% |
| Disaster recovery | Manual | Automated | Automated | Automated |

**Verdict:** Infrastructure is 4/10 for production. Fine for solo development, not ready for paying customers. Cloudflare + separated worker is the minimum viable fix.

---

## 3. AI ARCHITECTURE REVIEW

### What's Strong

| Component | Rating | Why |
|-----------|--------|-----|
| Multi-LLM consensus (3-4 models) | 9/10 | Genuinely novel. No retail competitor does this. |
| Model diversity (Gemini + Claude + DeepSeek) | 8/10 | Different model families reduce correlated errors |
| Supervisor veto layer | 8/10 | Catches overconfident consensus — institutional pattern |
| Risk gates before scoring | 7/10 | Pre-filtering before LLM reduces hallucination impact |
| Prediction market integration | 8/10 | Unique data source, genuine alpha signal |

### What Needs Work

| # | Gap | Current | Recommended | Impact |
|---|-----|---------|-------------|--------|
| A1 | **No backtesting** | Can't validate strategies historically | Add backtesting framework using historical yfinance data | Cannot prove edge exists |
| A2 | **No performance attribution** | Don't know which LLM model adds most value | Track per-model accuracy: did model X's vote correlate with outcome? | Wasting money on models that don't help |
| A3 | **No prompt versioning** | Prompts hardcoded in Python | Version prompts in DB or YAML, A/B test them | Can't iterate on prompts systematically |
| A4 | **No fine-tuning feedback loop** | LLM validates but never learns from results | After trade closes, feed outcome back as training signal | Models don't improve over time |
| A5 | **Same prompts for all market conditions** | Bull/bear/crash all use same prompt | Condition-specific prompts (e.g., "current VIX is 35, market is in fear") | Models lack context |
| A6 | **No confidence calibration** | Model says 70% confidence — is that accurate? | Track calibration: when models say 70%, do 70% of trades win? | Don't know if confidence is meaningful |
| A7 | **LLM hallucination not measured** | No tracking of factual errors in LLM outputs | Sample and manually verify 5% of LLM responses weekly | Could be acting on false information |
| A8 | **Ollama local model underutilized** | Optional, often unreachable | Deploy as sidecar container, always available | Free local inference reduces costs |

### AI Strategy Comparison

| Feature | TradeWiz | Kensho (S&P) | Numerai | QuantConnect |
|---------|----------|-------------|---------|-------------|
| Multi-model consensus | Yes (3-4) | Yes (ensemble) | Yes (crowd) | No |
| Prediction markets | Yes | No | No | No |
| Backtesting | **No** | Yes | Yes | Yes |
| Performance attribution | **No** | Yes | Yes | Yes |
| Fine-tuning loop | **No** | Yes | Yes | No |
| Prompt versioning | **No** | Yes | N/A | N/A |
| Real-time adaptation | Partial (VIX regime) | Yes | Slow | No |

### Recommended AI Enhancements (Priority Order)

**P0: Backtesting Framework**
```
Why: Cannot prove the swing strategies work without historical validation.
What: Run _generate_swing_signal() on 2 years of historical daily data.
      Track: entries, SL/TP hits, hold duration, net P&L after fees.
      Compare: swing_vcp vs swing_htf vs swing_breakout vs swing_trend.
Metric: Sharpe ratio, max drawdown, win rate, profit factor.
Cost: 0 (uses existing yfinance data + local compute).
```

**P1: Model Performance Tracking**
```
Why: You're paying for 3-4 LLM calls per trade but don't know which adds value.
What: When trade closes, check which model voted correctly.
      Store: model_name, vote (approve/reject), trade_outcome (win/loss).
      Dashboard: model accuracy over time, by strategy, by asset.
Metric: Per-model accuracy, false positive rate, value-add over random.
Cost: 0 (just DB logging + dashboard query).
```

**P2: Dynamic Prompt Injection**
```
Why: Current prompts don't mention market conditions.
What: Inject real-time context into every prompt:
      "Current VIX: 23.87 (elevated). Market regime: LEAN BEARISH.
       Poly/Kalshi recession probability: 35%. SPY down -0.5% today.
       This user's 7-day win rate: 48%. Strategy 'swing_vcp' win rate: 62%."
Cost: 0 (already have all this data, just add to prompt).
```

**P3: Confidence Calibration Table**
```
Why: "70% confidence" means nothing without calibration.
What: Track over 100+ trades:
      - When LLM says 60-70% confidence, what % actually win?
      - When LLM says 80%+, what % actually win?
      Show calibration curve on dashboard.
Cost: 0 (just analytics).
```

---

## 4. FEATURE GAP ANALYSIS vs COMPETITORS

### vs TradingView ($15-60/mo, 50M users)
| Feature | TradingView | TradeWiz | Gap |
|---------|-------------|----------|-----|
| Charting | 100+ indicators | 15 indicators | Add more indicators (RSI divergence, VWAP, Fib retracement) |
| Social | 30M ideas shared | None | Add trade idea sharing (later) |
| Alerts | Price/indicator alerts | None | **Add price alerts (email/Discord webhook)** |
| Screener | Multi-param screener | Category-based | Close enough |
| Backtesting | Pine Script | **Missing** | **P0 priority** |
| Paper trading | Built-in | Built-in | Matched |
| Multi-broker | No | Yes (Alpaca + Webull + BloFin) | TradeWiz wins |

### vs Trade Ideas ($118-228/mo, 100K users)
| Feature | Trade Ideas | TradeWiz | Gap |
|---------|-------------|----------|-----|
| AI scanning | Holly AI (proprietary) | Multi-LLM consensus | TradeWiz more transparent |
| Auto-trading | Brokerage+ plan | Both bots | Matched |
| Backtesting | Full historical | **Missing** | Critical gap |
| Alert system | Real-time | **Missing** | Add alerts |
| Entry/exit optimization | AI-optimized | ATR-based | Could improve |
| Price | $228/mo for auto | $99/mo | TradeWiz 57% cheaper |

### vs QuantConnect (Free-$20/mo, developer-focused)
| Feature | QuantConnect | TradeWiz | Gap |
|---------|-------------|----------|-----|
| Custom strategies | Full Python/C# | 9+5 built-in | TradeWiz is no-code |
| Backtesting | Institutional-grade | **Missing** | Critical gap |
| Live trading | Yes | Yes | Matched |
| Data | 50+ sources | yfinance + prediction markets | TradeWiz has unique data |
| Learning curve | High (code required) | Low (UI-driven) | TradeWiz wins |

### Key Competitive Advantages (Keep)
1. **Multi-LLM consensus** — no competitor has this
2. **Prediction market integration** — unique data source
3. **No-code bot** — vs QuantConnect's code-required approach
4. **Lower price** — vs Trade Ideas at $228/mo
5. **17 research skills** — mini Bloomberg at $49/mo

### Critical Gaps to Close
1. **Backtesting** — every serious competitor has this
2. **Price alerts** — basic feature users expect
3. **More indicators** — VWAP, RSI divergence, Fibonacci levels
4. **Trade sharing/community** — social proof drives adoption

---

## 5. OPTIMIZATION RECOMMENDATIONS

### Performance Optimizations

| # | Optimization | Current | Proposed | Impact |
|---|-------------|---------|----------|--------|
| O1 | Cache yfinance data | Fetched every scan (5 min) | Redis cache with 2-min TTL | 60% fewer yfinance calls |
| O2 | Batch LLM calls | Sequential per-coin | Batch 3-5 coins in one prompt | 3-5x fewer LLM calls |
| O3 | Pre-compute indicators | Calculated per request | Background job, cache results | 200ms faster API responses |
| O4 | Static asset hashing | No cache busting | Add `?v=hash` to CSS/JS includes | Aggressive CDN caching |
| O5 | Database indexes | Partial | Add compound indexes on bot_trades(user_id, status, asset_type) | 10x faster dashboard queries |
| O6 | SSE connection pooling | One per tab | Shared SSE multiplexer | Fewer server connections |

### Cost Optimizations

| # | Optimization | Current Cost | Proposed | Savings |
|---|-------------|-------------|----------|---------|
| C1 | Use Gemini Flash for screener (not Pro) | ~$15/mo | ~$3/mo | 80% |
| C2 | Cache LLM responses for same ticker+timeframe | Duplicated calls | 1-hour cache | 40% fewer calls |
| C3 | Skip LLM for repeated scans (no new signal) | Validates every cycle | Only validate new signals | 60% fewer calls |
| C4 | Ollama sidecar for bot validation | $0 but unreliable | Always-on container | $0, more reliable |
| C5 | Vertex AI vs OpenRouter | 30-50% markup | Direct pricing | 30-40% savings |

---

## 6. FINAL SCORECARD

| Category | Round 1 | Round 2 | Target |
|----------|---------|---------|--------|
| **Security** | Not reviewed | 6/10 | 9/10 |
| **Infrastructure** | 5/10 | 4/10 (honest assessment) | 8/10 |
| **AI/ML Pipeline** | 8/10 | 7/10 (missing backtest + attribution) | 9/10 |
| **Trading Engine** | 3/10 | 6/10 (swing mode added) | 8/10 |
| **UX/Dashboard** | 6/10 | 8/10 (narrative + Kalshi style) | 9/10 |
| **Feature Completeness** | 7/10 | 8/10 (17 skills, prediction markets) | 9/10 |
| **Revenue Readiness** | 4/10 | 7/10 (Stripe, tiers, invite flow) | 9/10 |
| **Overall** | 5.3/10 | **6.6/10** | 8.7/10 |

### Top 5 Actions to Reach 8.7/10

1. **Revoke exposed secrets + fix auth security** (S1-S7) — 1 day, prevents breach
2. **Add backtesting framework** (A1) — 1 week, proves strategy edge
3. **Separate bot worker + add Cloudflare** (I1, I4, I5) — 2 days, production-ready infra
4. **Add model performance tracking** (A2) — 2 days, data-driven LLM optimization
5. **Add price alerts (email/Discord)** — 3 days, most-requested feature by traders

---

*Infinity Ventures Group LLC — Confidential*
*Security Classification: Internal Only*
