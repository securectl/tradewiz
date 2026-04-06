# Trading Bot Logic Documentation

## Overview

Two autonomous trading bots (crypto + stock) run as background daemon threads. Each scans a watchlist on a configurable interval (default 300s), generates signals using 9 technical strategies, validates via multi-LLM consensus, and executes on paper trading accounts. Both bots are self-learning (adaptive sizing, strategy blacklisting) and self-healing (auto-recover from kill switch).

**Paper trading only** — BloFin demo mode (crypto) and Alpaca paper mode (stocks) are enforced.

---

## Scan Cycle Flow

Every scan cycle follows this exact sequence:

```
1. Kill switch check → self-heal if 30 min cooldown elapsed
2. Refresh adaptive parameters (every 30 min)
3. Check daily goal progress
4. Market sensor pre-check (LLM-based: HEALTHY / CAUTION / DANGER)
   - DANGER → skip entire cycle
   - CAUTION → only high-confidence trades allowed
5. Loop through selected coins/stocks:
   a. Fetch 1-month hourly data via yfinance
   b. Calculate technical indicators
   c. Run 9 strategies to generate signal
   d. Pre-filters: ATR too low? Cooldown active? Strategy blacklisted?
   e. Direction bias filter (long_only / short_only / both / auto)
   f. Position sizing (% of equity, scaled by adaptive params)
   g. Risk gate check (kill switch → enabled → daily loss → trade count → position size → max positions → duplicates)
   h. LLM validation (or Quick Trade Mode bypass)
   i. CAUTION mode confidence threshold check
   j. Calculate stop loss / take profit from ATR
   k. Place order on broker
   l. Record trade in database + journal
6. Check exits on all open positions:
   - Stop loss / take profit hit
   - Trailing stop (retraced >50% from target after 1.5%+ P&L)
   - Time exit: stocks only, >24h open with <0.3% P&L
7. Log hourly performance summary
```

---

## 9 Trading Strategies

All strategies use 1-hour candle data with a 1-month lookback.

### 1. MACD Crossover (`macd_cross`)
- **BUY**: MACD bullish crossover + histogram magnitude >= 0.02% of price + RSI between 28-68 + price above EMA20
- **SELL**: MACD bearish crossover + histogram magnitude >= 0.02% + RSI between 32-72 + price below EMA20

### 2. EMA Trend (`ema_trend`)
- **BUY**: SMA8 crosses above EMA20 (fresh cross) + RSI 28-68 + MACD histogram positive (crypto) or RSI 25-68 (stocks)
- **SELL**: SMA8 crosses below EMA20 + RSI 32-72 + MACD histogram negative (crypto) or RSI 32-75 (stocks)

### 3. RSI Mean Reversion (`rsi_reversion`)
- **BUY**: RSI < 35 (crypto) / < 40 (stocks) + MACD histogram positive
- **SELL**: RSI > 65 (crypto) / > 62 (stocks) + MACD histogram negative + price below SMA8

### 4. Momentum Breakout (`momentum`)
- **BUY**: Price above SMA50 + relative volume > 1.2x (crypto) / > 1.0x (stocks) + RSI 45-75/78 + MACD positive
- **SELL**: Price below SMA50 + volume surge + RSI 25-55 + MACD negative

### 5. Bollinger Band Reversion (`bb_reversion`)
- **BUY**: Price at bottom 10% (crypto) / 15% (stocks) of BB range + RSI < 40/42 + MACD turning positive
- **SELL**: Price at top 90% (crypto) / 85% (stocks) of BB range + RSI > 60 + MACD turning negative

### 6. Grid Mean Reversion (`grid_reversion`)
- **BUY**: Price 1.5+ ATR below SMA50 in uptrend (SMA50 > SMA200) + RSI < 45
- **SELL**: Price 1.5+ ATR above SMA50 in downtrend (SMA50 < SMA200) + RSI > 55/58

### 7. Trend Continuation / DCA (`trend_dca`)
- **BUY**: Uptrend (SMA50 > SMA200) + price pulls back within 1.2% (crypto) / 0.8% (stocks) of EMA20 + RSI 35-60 + MACD positive
- **SELL**: Downtrend (SMA50 < SMA200) + price rallies to within 1.0% / 0.8% of EMA20 + RSI 45-68 + MACD negative

### 8. Doji Reversal (`doji_reversal`)
- **BUY**: Doji candle (body < 10% of range) + RSI < 40 + price below EMA20 + prior candle was bearish
- **SELL**: Doji candle + RSI > 60 + price above EMA20 + prior candle was bullish

### 9. Pump/Dump on Close (`pump_on_close`)
- **BUY**: Relative volume >= 1.5x (crypto) / 1.4x (stocks) + large body (> 0.4 ATR) + close in upper 25% of range + RSI < 70 + price above SMA50
- **SELL**: Volume surge + large body + close in lower 25% + RSI > 30 + price below SMA50

---

## Pre-Filters (Before Signal Generation)

| Filter | Crypto | Stocks |
|--------|--------|--------|
| ATR too low (dead market) | < 0.15% of price | < 0.08% of price |
| Low-price thin spread | < $0.50 with ATR < 0.4%, or < $1.00 with ATR < 0.25% | N/A |
| Cooldown after loss | 10 min (0 losses), 15 min (1+), 30 min (3+), 60 min (5+) | 15 min after loss, 8 min after win |

---

## Risk Gates (Sequential — All Must Pass)

Checked in this order for every trade:

| Gate | Crypto Default | Stock Default |
|------|---------------|---------------|
| 1. Kill switch | Must be OFF | Must be OFF |
| 2. Bot enabled | Must be ON | Must be ON |
| 3. Paper mode enforced | `BLOFIN_DEMO=1` required | Alpaca paper mode |
| 4. Daily loss limit | -$500 → activates kill switch | -$500 → activates kill switch |
| 5. Max daily trades | 25 | 25 |
| 6. Max position size | 10% of equity | 10% of equity |
| 7. Max open positions | 6 | 8 |
| 8. No duplicate positions | 1 per coin | 1 per symbol |
| 9. Consecutive loss cooldown | 5+ losses → 1-4h pause per coin | N/A (handled in pre-filter) |
| 10. Per-coin daily loss cap | -$75 per coin | N/A |
| 11. PDT rule | N/A | If equity < $25K and 3+ day trades in 5 days → blocked |
| 12. Market hours | N/A (24/7) | Must be open (configurable extended hours) |

---

## LLM Validation Pipeline

Every signal must pass multi-LLM validation before execution (unless Quick Trade Mode bypasses it).

### Gate 1: Ollama (Local, Fast)
- Model: `llama3.1:8b` (configurable)
- First filter — fast rejection of bad signals
- If Ollama is unreachable, falls back to OpenRouter-only

### Gate 2: OpenRouter (2 Models in Parallel)
- **Sentiment model**: `google/gemini-2.5-flash` (configurable via `LLM_BOT_SENTIMENT`)
- **Risk model**: `deepseek/deepseek-chat-v3-0324` (configurable via `LLM_BOT_RISK`)
- Both must agree (approve) for the trade to execute
- LLM validators are **biased toward approving trades** (paper trading mode)

### Quick Trade Mode (Bypass LLM)
When enabled (`quick_trade_mode=1`), LLM validation is skipped for high-conviction signals:
- Volume surge >= 1.5x AND RSI between 30-60
- OR momentum strategy signals
- Confidence is set to 0.7 for bypassed trades

### CAUTION Mode Confidence Threshold
When market sensor returns CAUTION, trades require minimum LLM confidence:
- Default threshold: 0.45 (crypto) / 0.40 (stocks)
- Adaptive: lowers to 0.35/0.30 when win rate >= 55%, raises to 0.55/0.50 when win rate < 35%

---

## Market Sensor

Pre-scan LLM check of broad market health. Called once per scan cycle, cached for 30 minutes.

- **Crypto**: Checks BTC, ETH prices and indicators
- **Stocks**: Checks SPY, QQQ, VIX
- **LLM Model**: `google/gemini-2.5-flash` (configurable via `LLM_BOT_SENTIMENT`)

| Status | Action |
|--------|--------|
| HEALTHY | Normal trading — all signals processed |
| CAUTION | Only high-confidence trades (above adaptive threshold) |
| DANGER | Skip entire scan cycle — no trades |

---

## Self-Learning (Adaptive Parameters)

Refreshed every 30 minutes based on rolling 7-day trade history.

### Adaptive Position Sizing
| 7-Day Win Rate | Position Scale |
|----------------|----------------|
| >= 60% | 1.3x (scale UP — only on full base, not compounded down) |
| >= 50% | 1.1x |
| 45-50% | 1.0x (default) |
| < 45% | 0.85x |
| < 35% | 0.7x (scale DOWN) |

**Important**: The bot currently only scales DOWN (0.7x-0.85x) for position sizing when losing. When winning (>= 50%), it uses full base size (1.0x). The 1.1x and 1.3x scale factors are calculated but capped at 1.0x via `min(position_scale, 1.0)`.

### Strategy Blacklisting
- Strategies with **< 20% win rate** and **>= 5 trades** in 7 days are blacklisted
- Blacklisted strategies' signals are skipped entirely
- Hot strategies (>= 65% win rate, >= 3 trades) are tracked but not treated differently

### Adaptive Confidence Threshold
Adjusts the minimum LLM confidence required during CAUTION market conditions:
- Win rate >= 55%: lower threshold (0.35 crypto / 0.30 stocks) — trust signals more
- Win rate < 35%: raise threshold (0.55 crypto / 0.50 stocks) — require higher conviction

---

## Self-Healing

When the kill switch activates (due to daily loss limit breach), the bot doesn't stay dead:

1. Kill switch activates → bot stops, all positions closed
2. Bot loop continues running, checking kill switch each cycle
3. After **30 minutes** cooldown, kill switch auto-deactivates
4. Bot resumes normal scanning

---

## Position Sizing

```
base_size = equity * (max_position_pct / 100)    # default 10%
adaptive_scale = min(position_scale, 1.0)         # from self-learning
size_usd = base_size * adaptive_scale             # only scales DOWN

# Crypto: size_coins = size_usd / current_price
# Stocks: qty = floor(size_usd / current_price)   # whole shares only
```

---

## Stop Loss / Take Profit

Calculated using ATR (Average True Range, 14-period):

| | Crypto | Stocks |
|---|--------|--------|
| Minimum ATR | 1% of price | 0.5% of price |
| Stop Loss | 2.0x ATR from entry | 1.5x ATR from entry |
| Take Profit | 3.0x ATR from entry | 2.5x ATR from entry |
| Risk:Reward | 1:1.5 | 1:1.67 |

---

## Exit Logic

Checked every scan cycle for all open positions:

1. **Stop loss hit** — price crosses SL level
2. **Take profit hit** — price crosses TP level
3. **Trailing stop** — if P&L >= 1.5% AND price has retraced > 50% from the target while still profitable (> 0.5%), close to lock in gains
4. **Time exit** (stocks only) — position open > 24 hours with < 0.3% P&L (stale trade)

---

## Direction Bias

Configurable per bot — filters which side of signals to accept:

| Setting | Behavior |
|---------|----------|
| `both` (crypto default) | Accept both BUY and SELL signals |
| `long_only` (stock default) | Only BUY signals, skip all SELL |
| `short_only` | Only SELL signals, skip all BUY |
| `auto` (crypto only) | LLM detects market direction; skips signals that contradict detected trend (if confidence > 50%) |

---

## Daily Goal

Both bots track a configurable daily P&L goal (default $500 each).

- Progress is checked at the start of each scan cycle
- When goal is reached (>= 100%), the bot logs "reducing aggression" but **does not stop trading**
- Dashboard shows daily goal progress as a percentage

---

## Fee Estimation

Fees are estimated (not fetched from broker) and stored per trade:

| | Crypto (BloFin) | Stocks (Alpaca) |
|---|---|---|
| Fee model | 0.08% per side (avg of 0.06% maker / 0.1% taker) | $0.01 per share (SEC + FINRA/TAF regulatory fees) |
| Calculation | `(entry_price * size * 0.0008) + (exit_price * size * 0.0008)` | `shares * 0.01` |
| Stored | `fee` column in `bot_trades` table | Same |

**Note**: P&L calculations do NOT deduct fees. Dashboard shows gross P&L, total fees, and net P&L separately.

---

## Configurable Parameters

All stored in `bot_config` table (key-value per user).

### Crypto Bot
| Key | Default | Description |
|-----|---------|-------------|
| `bot_enabled` | 0 | Master on/off |
| `kill_switch` | 0 | Emergency stop |
| `scan_interval_sec` | 300 | Seconds between scan cycles |
| `daily_loss_limit` | 500 | Daily loss cap ($) before kill switch |
| `max_daily_trades` | 25 | Max trades per day |
| `max_position_pct` | 10 | Max position size as % of equity |
| `max_open_positions` | 6 | Max concurrent open positions |
| `direction_bias` | both | long_only / short_only / both / auto |
| `quick_trade_mode` | 0 | Bypass LLM for high-conviction signals |
| `selected_coins` | DEFAULT_COINS | JSON array of coin keys |
| `daily_goal` | 500 | Daily P&L target ($) |

### Stock Bot
| Key | Default | Description |
|-----|---------|-------------|
| `stock_bot_enabled` | 0 | Master on/off |
| `stock_kill_switch` | 0 | Emergency stop |
| `stock_scan_interval_sec` | 300 | Seconds between scan cycles |
| `stock_daily_loss_limit` | 500 | Daily loss cap ($) |
| `stock_max_daily_trades` | 25 | Max trades per day |
| `stock_max_position_pct` | 10 | Max position size % |
| `stock_max_open_positions` | 8 | Max concurrent positions |
| `stock_direction_bias` | long_only | long_only / short_only / both |
| `stock_quick_trade_mode` | 0 | Bypass LLM |
| `stock_selected_stocks` | DEFAULT_STOCKS | JSON array of tickers |
| `stock_daily_goal` | 500 | Daily P&L target ($) |
| `stock_extended_hours` | 0 | Trade in pre/post market |
| `stock_broker` | alpaca | alpaca or webull |

---

## Why the Bot May Be Underperforming

Common reasons the bot stays under 20% of daily goal:

### Signal Generation Issues
1. **ATR pre-filter too aggressive** — dead markets get skipped entirely. If volatility is low across the board, few signals generate.
2. **Cooldown timers** — after losses, cooldowns of 15-60 min per coin prevent re-entry. With multiple losses, the bot can sit idle for long stretches.
3. **Strategy blacklisting** — if multiple strategies hit <20% win rate, fewer strategies are available to generate signals.

### Validation / Gating Issues
4. **Market sensor returning DANGER** — entire scan cycles are skipped. Check if broad market conditions (BTC/ETH or SPY/QQQ/VIX) are volatile.
5. **Market sensor returning CAUTION** — only high-confidence trades pass, and the confidence threshold may be elevated (0.55) if recent win rate is low.
6. **LLM rejecting signals** — the multi-LLM consensus may reject valid setups. Check bot logs for "LLM validation rejected" messages.
7. **Rate limiting** — LLM rate limits may prevent validation calls.

### Risk Gate Issues
8. **Max open positions reached** — with 6 crypto / 8 stock positions open, no new trades can enter even with valid signals.
9. **Daily trade limit** — 25 trades/day limit reached.
10. **Kill switch cycling** — if daily loss keeps hitting -$500, the bot enters a 30-min heal cycle repeatedly.

### Position Sizing Issues
11. **Adaptive scaling down** — if win rate < 45%, position sizes are reduced to 0.7x-0.85x, reducing potential gains.
12. **Small equity base** — 10% of a small account means tiny positions where fees and slippage eat most of the P&L.

### Market Conditions
13. **Low volatility / ranging market** — all strategies rely on trends or mean reversion from extremes. Sideways markets generate few signals.
14. **Stock market closed** — stock bot only trades during market hours (9:30 AM - 4:00 PM ET, unless extended hours enabled).

### Things to Check
- Bot log for "No signal", "Risk gate blocked", "LLM validation rejected", "Market sensor: DANGER/CAUTION"
- Dashboard win rate — below 35% triggers defensive mode (smaller positions, higher confidence threshold)
- Number of blacklisted strategies
- Kill switch activation history
- Whether Quick Trade Mode is enabled (bypasses slow LLM gating)

---

## IPO Scanner

A dedicated section that scans for upcoming high-stake IPOs, filters by popularity and social demand, and rates each IPO using LLM-powered analysis.

### How It Works

1. **Discovery**: LLM identifies top upcoming IPOs (S-1 filed or credible reports of going public in next 1-6 months)
2. **Filter**: Only high-stake IPOs — $1B+ expected valuation, significant market buzz, or major brand recognition
3. **Social Data Analysis**: LLM evaluates social signals across Reddit, Twitter/X, news coverage, retail investor interest
4. **AI Rating**: Each IPO is rated 1-5 stars across multiple dimensions
5. **Qualification Filter**: IPOs with overall_rating < 2 AND social_buzz < 3 are dropped

### Rating Dimensions (1-5 Stars Each)

| Dimension | Weight | What It Measures |
|-----------|--------|------------------|
| Social Buzz | 25% | Reddit mentions, Twitter/X trends, retail interest, news volume |
| Institutional Interest | 25% | Hedge fund demand, anchor investor quality, oversubscription signals |
| Market Fit | 20% | Current market timing, sector momentum, IPO window conditions |
| Moat | 20% | Competitive advantage, business quality, revenue growth |
| Risk (inverse) | 10% | Lower risk = higher contribution to overall rating |

### Overall Star Rating

```
overall_rating = (social_buzz * 0.25) + (institutional_interest * 0.25) +
                 (market_fit * 0.20) + (moat * 0.20) + ((6 - risk_level) * 0.10)
```

Stars are rounded to nearest integer (1-5). LLM generates the final rating with justification.

### Star Rating Guide

| Stars | Meaning |
|-------|---------|
| 5 | Must-watch — viral social buzz, strong fundamentals, perfect timing |
| 4 | High demand — significant interest, solid company, favorable conditions |
| 3 | Moderate — decent company but mixed signals or uncertain timing |
| 2 | Low interest — limited buzz, niche sector, or poor market timing |
| 1 | Skip — minimal demand, high risk, or questionable fundamentals |

### Card Display

Each IPO card shows:
- Company name, expected ticker, sector
- Star rating (visual 1-5 stars)
- Expected date, valuation range, price range
- Risk level (color-coded: green/orange/red)
- Social Buzz / Institutional / Market Fit / Moat bar charts
- Social signals summary (where the buzz is coming from)
- AI rating justification
- Catalysts (positive drivers)
- Key risks (what could go wrong)

### Technical Details
- **LLM Model**: Uses `LLM_SCREENER` (default: `google/gemini-2.5-flash`)
- **Rate Limited**: 1 LLM call per scan, subject to user's tier limit
- **Cache**: 5-minute client-side cache to avoid repeated scans
- **Route**: `GET /api/ipos`
- **Decorators**: `@login_required`, `@llm_rate_limit(call_source="ipo_scanner", call_count=1)`
