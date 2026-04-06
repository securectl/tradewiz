# Bot Trading Logic Documentation

## Overview

The trading bots (Crypto & Stock) use a multi-layered approach combining technical analysis, multi-LLM consensus validation, risk management, and market health monitoring. All trading is **paper/demo only** by default.

---

## Architecture

```
Scan Cycle (every 5 min)
  |
  v
Market Sensor Pre-Check (BTC/ETH for crypto, SPY/QQQ/VIX for stocks)
  |-- DANGER  -> Skip entire cycle
  |-- CAUTION -> Require 80%+ LLM confidence
  |-- HEALTHY -> Normal operations
  |
  v
For Each Asset:
  1. Fetch 1-month hourly data (yfinance)
  2. Calculate technical indicators
  3. Run signal strategies (7 crypto / 6 stock)
  4. Apply direction bias filter
  5. Risk management gates
  6. Multi-LLM validation (2-3 validators)
  7. Execute or reject
  |
  v
Exit Monitor:
  - Stop loss / Take profit
  - Trailing stop (>1.5% profit)
  - Time exit (>24h stale positions)
```

---

## Signal Generation Strategies

### Strategy 1: MACD Crossover
**Buy:** MACD histogram crosses above 0 (>0.05% of price), RSI 35-60, price > EMA20 & SMA50
**Sell:** MACD histogram crosses below 0, RSI 40-65, price < EMA20 & SMA50

### Strategy 2: EMA Trend Shift
**Buy:** SMA8 crosses above EMA20, RSI 35-60, MACD histogram > 0, relative volume > 0.8x
**Sell:** SMA8 crosses below EMA20, RSI 40-65, MACD histogram < 0, relative volume > 0.8x

### Strategy 3: RSI Mean Reversion
**Buy:** RSI < 30 (oversold), MACD histogram > 0, price > lower Bollinger Band
**Sell:** RSI > 70 (overbought), MACD histogram < 0, price < SMA8

### Strategy 4: Momentum Breakout
**Buy:** Price > SMA50 & SMA200, volume > 1.8x average, RSI 50-70, MACD > 0
**Sell:** Price < SMA50 & SMA200, volume > 1.8x average, RSI 30-50, MACD < 0

### Strategy 5: Bollinger Band Reversion
**Buy:** Price at/below lower band (BB% <= 0.05), RSI < 35, MACD > 0, BB width > 2%
**Sell:** Price at/above upper band (BB% >= 0.95), RSI > 65, MACD < 0, BB width > 2%

### Strategy 6: Grid Mean Reversion
**Buy:** Price 2+ ATRs below SMA50, RSI < 40, uptrend (SMA50 > SMA200), MACD > 0
**Sell:** Price 2+ ATRs above SMA50, RSI > 60, downtrend (SMA50 < SMA200), MACD < 0

### Strategy 7: Trend Continuation / DCA (Crypto only)
**Buy:** SMA50 > SMA200, price > SMA200, price within 0.8% of EMA20 (pullback), RSI 40-55
**Sell:** SMA50 < SMA200, price < SMA200, price within 0.5% of EMA20 (rally), RSI 55-65

---

## Pre-Signal Filters

- **ATR Volatility:** Skip if ATR < 0.3% of price (too choppy)
- **Low-Price Coins:** Skip if price < $0.50 with ATR < 0.8%, or < $1.00 with ATR < 0.5%
- **Cooldown System:**
  - 3+ consecutive losses: 120 min cooldown
  - 1-2 consecutive losses: 45 min cooldown
  - 0 consecutive losses: 20 min cooldown

---

## Multi-LLM Validation Pipeline

Every trade signal passes through a 2-3 layer LLM validation before execution.

### Gate 1: Local Ollama Validator (Optional)

```
System: "You are a crypto trading analyst for a paper trading bot.
         Evaluate this trade signal."

Response format:
{
    "execute": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation",
    "risk_level": "low/medium/high/extreme"
}

Rules:
- Approve ONLY if >= 3 indicators clearly support direction
- Reject if RSI contradicts (>65 for buy, <35 for sell)
- Reject if indicators mixed or weak agreement
- Reject if coin win rate <30% in history
- Default: execute=false unless strong setup
```

### Gate 2a: OpenRouter Sentiment Analysis

```
System: "You are a critical crypto market analyst. Evaluate trade
         setups rigorously. Your job is to filter out weak trades.
         Respond in JSON only."

Response format:
{
    "execute": true/false,
    "sentiment_score": -1.0 to 1.0,
    "momentum": "bullish/neutral/bearish",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}

Rules:
- Approve ONLY if momentum clearly supports direction
- Reject if historical win rate < 30%
- Reject if trade fights higher timeframe trend
- Reject if momentum neutral or contradicts
```

### Gate 2b: OpenRouter Risk Analysis

```
System: "You are a conservative crypto trading risk analyst. Your
         job is to protect capital by rejecting marginal trades.
         Respond in JSON only."

Response format:
{
    "execute": true/false,
    "risk_score": 0-100,
    "stop_loss_adequate": true/false,
    "false_signal_probability": 0.0-1.0,
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}

Rules:
- Reject if risk_score > 60
- Reject if historical win rate <30%
- Reject if indicators mixed/weak
- Reject if false_signal_probability > 0.5
- Only approve if risk/reward clearly favorable
```

### Consensus Voting

- **All 3 validators available:** Need >= 2 votes (majority)
- **Ollama unavailable:** Need >= 1 of 2 OpenRouter votes
- **Any validator unavailable:** Adjust total voter count
- **No validators available:** Auto-reject (safety default)

### Trade Context Sent to All Validators

```
Crypto Trade Signal for Validation:
- Coin: BTC-USDT
- Side: BUY
- Current Price: $67,450.00
- Signal Reason: MACD bullish crossover with strong volume
- Strategy: macd_crossover

Technical Indicators:
- RSI(14): 45.2
- MACD: 125.3
- MACD Histogram: 42.1
- SMA 50: $65,200
- SMA 200: $58,400
- ATR(14): $1,250
- Volume: 15,234
- Relative Volume: 1.4x

Historical Performance (last 7 days):
- Overall: 12 trades, 58.3% win rate, avg P&L $15.20, total P&L $182.40
- This strategy (macd_crossover): 4 trades, 75% win rate, avg P&L $22.50
- This coin (BTC-USDT): 3 trades, 66.7% win rate, avg P&L $18.30

PAPER TRADING WARNING: Evaluate trade quality critically.
```

---

## Direction Bias System

The bot can filter signals based on market direction:

| Mode | Behavior |
|------|----------|
| `long_only` | Only BUY signals (default for stocks) |
| `short_only` | Only SELL signals |
| `auto` | LLM detects direction per asset, filters accordingly |
| `both` | All signals allowed |

**Auto mode prompt:**
```
System: "You are a crypto technical analyst. Analyze indicators to
         determine market direction. Respond in JSON only."

Response: { "direction": "bullish/bearish/neutral", "confidence": 0-1 }
```
If bearish (confidence > 0.5): skip BUY signals. If bullish: skip SELL signals.

---

## Entry & Position Sizing

### Position Size
- **Crypto:** `size_usd = equity * (max_position_pct / 100)`, then `size_coins = size_usd / price`
- **Stocks:** `qty = int(size_usd / price)` (whole shares only)
- Default: 10% of equity per position

### Stop Loss & Take Profit (ATR-based)

| | Crypto | Stock |
|--|--------|-------|
| **Stop Loss** | 2.0 x ATR | 1.5 x ATR |
| **Take Profit** | 3.0 x ATR | 2.5 x ATR |
| **Min ATR floor** | 1.0% of price | 0.5% of price |

Example: BTC at $67,000 with ATR = $1,250
- Buy SL: $67,000 - $2,500 = $64,500
- Buy TP: $67,000 + $3,750 = $70,750

---

## Exit Conditions

Checked every scan cycle for all open positions:

1. **Hard Stop Loss:** Price hits SL level
2. **Hard Take Profit:** Price hits TP level
3. **Trailing Stop:** When profit >= 1.5%, if price retraces >50% from target and profit > 0.5%
4. **Time Exit:** Position open > 24 hours with < 0.3% movement (stale)

---

## Risk Management

### Kill Switch
- Triggers when daily loss limit breached (default: -$250)
- Immediately stops bot and closes all positions
- Must be manually deactivated

### Position Limits
| Parameter | Default |
|-----------|---------|
| Max position size | 10% of equity |
| Max open positions | 3 |
| Max daily trades | 25 |
| Daily loss limit | -$250 |
| Per-coin daily loss cap | -$30 (crypto only) |

### Cooldown System
| Consecutive Losses | Cooldown |
|-------------------|----------|
| 0 | 20 min |
| 1-2 | 45 min |
| 3+ | 120 min |

### Stock-Specific: PDT Rule
If account equity < $25,000 and day trades >= 3 in 5 trading days, new trades are blocked.

---

## Market Health Sensor

Runs before each scan cycle. Results cached for 30 minutes.

### Crypto Indicators
Fetches BTC-USD and ETH-USD (5-day hourly data):
- Current price, 24h change, 5-day change, RSI, volume ratio

**Rule-based fallback (if LLM unavailable):**
- DANGER: BTC -8%/24h or ETH -10%/24h
- CAUTION: BTC -5%/24h or ETH -7%/24h or BTC -12%/5d
- HEALTHY: Otherwise

### Stock Indicators
Fetches SPY, QQQ, VIX (5-day data):
- Price, 1d change, 5d change, VIX level

**Rule-based fallback:**
- DANGER: SPY -3%/1d or QQQ -4%/1d or VIX > 35
- CAUTION: SPY -1.5%/1d or QQQ -2%/1d or VIX > 25
- HEALTHY: Otherwise

### Impact on Trading
| Status | Action |
|--------|--------|
| DANGER | Skip entire scan cycle |
| CAUTION | Only execute trades with >= 80% LLM confidence |
| HEALTHY | Normal operation |

---

## Key Thresholds: Crypto vs Stock

| Metric | Crypto | Stock |
|--------|--------|-------|
| Scan interval | 300s (5 min) | 300s (5 min) |
| Max position size | 10% of equity | 10% of equity |
| Max open positions | 3 | 3 |
| Stop loss | 2.0x ATR | 1.5x ATR |
| Take profit | 3.0x ATR | 2.5x ATR |
| Min ATR floor | 1.0% | 0.5% |
| Trading hours | 24/7 | 9:30 AM - 4:00 PM ET |
| Market sensor | BTC/ETH | SPY/QQQ/VIX |
| PDT enforcement | N/A | Yes (< $25K) |
| Strategies | 7 | 6 |

---

## LLM Models Used

Configurable via admin panel. Default model assignments:

| Purpose | Config Key | Used For |
|---------|-----------|----------|
| Bot Sentiment | `LLM_BOT_SENTIMENT` | Sentiment gate, direction detection |
| Bot Risk | `LLM_BOT_RISK` | Risk analysis gate |
| Supervisor | `LLM_SUPERVISOR` | Optional veto layer |
| Market Sensor | `LLM_RESEARCH` | Market health assessment |

All LLM calls go through OpenRouter API (or local Ollama for Gate 1).

---

## File Reference

| File | Purpose |
|------|---------|
| `crypto_bot/bot_engine.py` | Crypto scan loop, signal strategies, entry/exit |
| `crypto_bot/crypto_validator.py` | LLM validation prompts & consensus voting |
| `crypto_bot/risk_manager.py` | Kill switch, daily limits, cooldowns |
| `crypto_bot/blofin_client.py` | BloFin exchange API wrapper |
| `stock_bot/stock_engine.py` | Stock scan loop, signal strategies, entry/exit |
| `stock_bot/stock_validator.py` | Stock LLM validation prompts |
| `stock_bot/stock_risk_manager.py` | Stock risk + PDT rules |
| `stock_bot/broker_client.py` | Alpaca & Webull API wrappers |
| `market_sensor.py` | Market health check (HEALTHY/CAUTION/DANGER) |
| `rate_limiter.py` | LLM call quotas per tier |
