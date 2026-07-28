# 11 · Stock Trading Bot

The **Stock Trading** bot is the equities counterpart to the crypto bot: it scans your
selected stocks during market hours, detects signals, validates with AI, and executes
**paper trades** through Alpaca (or Webull sandbox).

> 🔒 **Paper trading by default** (Alpaca paper mode / Webull sandbox). Live trading is
> opt-in per user and gated. 🎟️ **Bot access is invite-only** (`stock`).

**Where:** the **Stock Trading** tab. **Setup:** add broker API keys in
[Settings](15-settings-api-keys.md).

---

## Controls

Same layout as the crypto bot: **Start · Stop · ⚙ Config · KILL**, with status, balance,
today's P&L, and daily-goal progress. *(Routes: `POST /api/stock-bot/start`, `/stop`,
`/kill`; `GET /api/stock-bot/status`.)*

---

## Strategies

**Intraday (hourly) strategies** mirror the crypto bot: MACD Crossover, EMA Trend, RSI Mean
Reversion, Momentum Breakout, Bollinger Band Reversion, Grid Mean Reversion, Trend
Continuation/DCA, Doji Reversal, and Pump/Dump on Close.

**Swing (daily) strategies** add multi-day setups: **VCP**, **HTF**, **Breakout**,
**Earnings** (post-report consolidation), and **Trend** (higher highs/lows pullback). The
**trade mode** config (`stock_trade_mode`) selects scalp, swing, or **hybrid**.

---

## Stocks

Defaults: **AAPL, TSLA, NVDA, MSFT, AMD**; you can add others (AMZN, GOOGL, META, PLTR,
COIN, SOFI, SPY, QQQ, MARA, RIOT, …) in the bot's **Config** or via
**Stock Trading → add stock**.

---

## VIX-aware gating

Before scanning, the bot checks SPY/QQQ/VIX (cached ~30 min):

| Status | Roughly | Behavior |
|--------|---------|----------|
| **HEALTHY (bull)** | VIX < 23 | favors cautious longs; blocks shorts |
| **CAUTION** | VIX 23–25 or sharp index drop | high-confidence trades only |
| **DANGER** | VIX > 25 or big index drop | skip the cycle |

In a **bull regime** the bot leans long and skips overbought buys; in a **bear regime** it
trims position sizes and demands higher conviction.

---

## Stock-specific risk gates (defaults)

Everything the crypto bot enforces, plus two equities rules:

| Gate | Default |
|------|---------|
| **Daily loss limit** | **−$500** (scalp) / −$2,000 (swing) |
| **Max daily trades** | **25** (scalp) / 5 (swing) |
| **Max position size** | **10%** of equity (scalp) / 25% (swing) |
| **Max total exposure** | **60%** of equity |
| **Max open positions** | **8** (scalp) / 3 (swing) |
| **PDT rule** | if equity < $25,000 and you've made 3+ day trades in 5 rolling days, new trades are blocked |
| **Market hours** | only trades 9:30 AM – 4:00 PM ET on weekdays (unless extended hours are enabled) |

Config keys are the `stock_`-prefixed equivalents (`stock_daily_loss_limit`,
`stock_max_position_pct`, `stock_max_total_exposure_pct`, `stock_max_open_positions`,
`stock_max_daily_trades`, `stock_direction_bias` — default `long_only`, `stock_broker`,
`stock_selected_stocks`, `stock_trade_mode`, `stock_extended_hours`).

---

## Entries, exits & AI validation

Stops/targets are ATR-based (≈1:1.67 reward:risk) for scalps, and percentage-based for
swings; the same trailing-stop and time-exit logic applies. AI validation uses the
**sentiment + risk** OpenRouter models (Ollama optional), with the same voting rules and
paper-trade-friendly tuning as the crypto bot.

---

## Self-learning, dashboard & logs

Adaptive sizing, strategy blacklisting, and self-healing behave the same as the crypto bot.
The **Stock Trading** tab shows the performance dashboard, daily P&L chart, open positions,
completed trades, and broker connection status. *(Routes: `GET /api/stock-bot/trades`,
`/pnl`, `/log`, `/top-movers`; `GET /api/bot/dashboard?asset=stock`;
`POST /api/stock-bot/config`.)*

---

## Quick start

1. **Settings → API Keys:** add Alpaca (or Webull) credentials.
2. **Stock Trading → ⚙ Config:** choose broker, stocks, trade mode, and risk caps.
3. **Start** during market hours and watch the first scan.
4. Mind the **PDT rule** if your paper equity is under $25K.
