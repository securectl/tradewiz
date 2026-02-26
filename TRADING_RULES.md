# Trading Rules & Matrix

Complete reference for all measurements, thresholds, strategies, and risk gates used by the crypto and stock trading bots.

---

## 1. Technical Indicators

### Moving Averages

| Indicator | Period | Type | Formula | Purpose |
|-----------|--------|------|---------|---------|
| SMA 8 | 8 bars | Simple | `close.rolling(8).mean()` | Short-term trend (Trend Breaker) |
| EMA 9 | 9 bars | Exponential | `close.ewm(span=9).mean()` | Alternative short-term momentum |
| EMA 20 | 20 bars | Exponential | `close.ewm(span=20).mean()` | Medium-term trend / BB midline |
| SMA 20 | 20 bars | Simple | `close.rolling(20).mean()` | Bollinger Band base |
| SMA 50 | 50 bars | Simple | `close.rolling(50).mean()` | Intermediate support/resistance |
| SMA 200 | 200 bars | Simple | `close.rolling(200).mean()` | Long-term trend direction |

### RSI (Relative Strength Index)

| Component | Formula | Period |
|-----------|---------|--------|
| Delta | `close.diff()` | 1 bar |
| Avg Gain | `delta.where(delta > 0, 0).rolling(14).mean()` | 14 bars |
| Avg Loss | `(-delta.where(delta < 0, 0)).rolling(14).mean()` | 14 bars |
| RS | `avg_gain / avg_loss` | — |
| **RSI** | **`100 - (100 / (1 + RS))`** | **14 bars** |

### MACD (Moving Average Convergence Divergence)

| Component | Formula | Period |
|-----------|---------|--------|
| EMA Fast | `close.ewm(span=12).mean()` | 12 bars |
| EMA Slow | `close.ewm(span=26).mean()` | 26 bars |
| MACD Line | `ema_12 - ema_26` | — |
| Signal Line | `macd_line.ewm(span=9).mean()` | 9 bars |
| **Histogram** | **`macd_line - signal_line`** | — |

**Crossover detection:**
- Bullish cross: `prev_macd <= prev_signal AND curr_macd > curr_signal`
- Bearish cross: `prev_macd >= prev_signal AND curr_macd < curr_signal`

### ATR (Average True Range)

| Component | Formula | Period |
|-----------|---------|--------|
| True Range | `max(H - L, abs(H - prev_close), abs(L - prev_close))` | Per bar |
| **ATR(14)** | **`SMA(TR, 14)`** | **14 bars** |
| TR(4) | `SMA(TR, 4)` | 4 bars |
| ADR% | `SMA((H - L) / close * 100, 14)` | 14 bars |

### Bollinger Bands (20, 2)

| Band | Formula |
|------|---------|
| Middle | `SMA(close, 20)` |
| Upper | `SMA(close, 20) + 2 * STD(close, 20)` |
| Lower | `SMA(close, 20) - 2 * STD(close, 20)` |
| Width % | `(4 * STD) / SMA_20 * 100` |
| Position % | `(price - lower) / (upper - lower)` — 0% = at lower, 100% = at upper |

### Volume

| Indicator | Formula | Purpose |
|-----------|---------|---------|
| Volume MA(10) | `volume.rolling(10).mean()` | Baseline average volume |
| Relative Volume | `current_vol / vol_ma_10` | Surge detection (>1.5x = surge) |

### SMA8/EMA20 Cross Detection

| State | Check |
|-------|-------|
| SMA8 above EMA20 | `sma_8 > ema_20` |
| Bullish cross | Previous bar: `sma8 <= ema20`, Current: `sma8 > ema20` |
| Bearish cross | Previous bar: `sma8 >= ema20`, Current: `sma8 < ema20` |

---

## 2. Data Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Historical lookback | 1 month | Amount of price data fetched |
| Bar interval | 1 hour | Candle period |
| Minimum bars required | 50 | Skip coin if data too thin |
| Performance history | 7 days | Lookback for trade stats fed to LLM |
| Scan interval | 300 seconds (5 min) | Time between scan cycles |

---

## 3. Pre-Filters (Applied Before All Strategies)

Every coin must pass these filters before any strategy is evaluated.

### Filter A: ATR Minimum (Ranging Market Detection)

| Measurement | Threshold | Action |
|-------------|-----------|--------|
| `ATR / price * 100` | **< 0.3%** | **SKIP** — market is ranging/choppy, no edge |

### Filter B: MACD Histogram Minimum Strength

| Measurement | Formula | Used by |
|-------------|---------|---------|
| MACD % of price | `abs(macd_histogram) / price * 100` | Strategy 1, 4: require >= 0.05% |
| | | Strategy 3: require >= 0.03% |

### Filter C: Per-Coin Cooldown

| Condition | Cooldown | Action |
|-----------|----------|--------|
| Last trade on same coin was profitable | **30 minutes** | SKIP if less time elapsed |
| Last trade on same coin was a loss | **60 minutes** | SKIP if less time elapsed |

---

## 4. Signal Generation Strategies

Seven strategies are evaluated in order. The first match wins.

### Strategy 1: MACD Crossover

**BUY conditions (all must be true):**
| Condition | Threshold |
|-----------|-----------|
| MACD bullish cross detected | `macd_bull_cross = true` |
| MACD histogram strength | `macd_pct >= 0.05%` of price |
| RSI range | `30 < RSI < 60` |
| Price vs EMA20 | `price > EMA20` |

**SELL conditions (all must be true):**
| Condition | Threshold |
|-----------|-----------|
| MACD bearish cross detected | `macd_bear_cross = true` |
| MACD histogram strength | `macd_pct >= 0.05%` of price |
| RSI range | `40 < RSI < 70` |
| Price vs EMA20 | `price < EMA20` |

### Strategy 2: EMA Trend (SMA8/EMA20 Cross)

**BUY:** SMA8 just crossed above EMA20 + `30 < RSI < 60`
**SELL:** SMA8 just crossed below EMA20 + `40 < RSI < 70`

### Strategy 3: RSI Mean Reversion

**BUY conditions:**
| Condition | Threshold |
|-----------|-----------|
| RSI oversold | `RSI < 35` |
| MACD turning positive | `macd_histogram > 0` |
| MACD strength | `macd_pct >= 0.03%` of price |

**SELL conditions:**
| Condition | Threshold |
|-----------|-----------|
| RSI overbought | `RSI > 70` |
| MACD turning negative | `macd_histogram < 0` |
| MACD strength | `macd_pct >= 0.03%` of price |
| Price confirmation | `price < SMA8` |

### Strategy 4: Momentum Breakout

**BUY conditions:**
| Condition | Threshold |
|-----------|-----------|
| Trend | `price > SMA50` |
| Volume surge | `relative_volume > 1.5x` |
| RSI zone | `55 < RSI < 70` |
| MACD positive | `macd_histogram > 0` |
| MACD strength | `macd_pct >= 0.05%` of price |

**SELL conditions:**
| Condition | Threshold |
|-----------|-----------|
| Trend | `price < SMA50` |
| Volume surge | `relative_volume > 1.5x` |
| RSI zone | `30 < RSI < 45` |
| MACD negative | `macd_histogram < 0` |
| MACD strength | `macd_pct >= 0.05%` of price |

### Strategy 5: Bollinger Band Reversion

**BUY:** Price in bottom 10% of BB (`bb_position <= 0.10`) + `RSI < 40` + `macd_histogram > 0`
**SELL:** Price in top 10% of BB (`bb_position >= 0.90`) + `RSI > 65` + `macd_histogram < 0`

### Strategy 6: Grid Mean Reversion (ATR-Based)

Inspired by BloFin Futures Grid Bot. Uses ATR-measured distance from SMA50.

**BUY conditions:**
| Condition | Threshold |
|-----------|-----------|
| Price distance | **2+ ATR below SMA50** (`atr_dist <= -2.0`) |
| RSI oversold | `RSI < 40` |
| Uptrend required | `SMA50 > SMA200` (deep pullback in uptrend) |

**SELL conditions:**
| Condition | Threshold |
|-----------|-----------|
| Price distance | **2+ ATR above SMA50** (`atr_dist >= 2.0`) |
| RSI overbought | `RSI > 65` |
| Downtrend required | `SMA50 < SMA200` (extended rally in downtrend) |

### Strategy 7: Trend Continuation / DCA-Style Re-Entry

Inspired by BloFin Futures DCA Bot. Buys pullbacks to EMA20 in confirmed trends.

**BUY conditions:**
| Condition | Threshold |
|-----------|-----------|
| Confirmed uptrend | `SMA50 > SMA200` and `price > SMA200` |
| Pullback to EMA20 | `abs(price - EMA20) / EMA20 < 0.5%` |
| RSI range | `40 < RSI < 55` |
| MACD momentum | `macd_histogram > 0` |

**SELL conditions:**
| Condition | Threshold |
|-----------|-----------|
| Confirmed downtrend | `SMA50 < SMA200` and `price < SMA200` |
| Rally to EMA20 | `abs(price - EMA20) / EMA20 < 0.5%` |
| RSI range | `50 < RSI < 60` |
| MACD momentum | `macd_histogram < 0` |

---

## 5. Direction Bias Filter

Applied after signal generation, before risk checks.

| Bias Mode | Rule |
|-----------|------|
| `long_only` | Reject all SELL signals |
| `short_only` | Reject all BUY signals |
| `both` | Allow both directions |
| `auto` | Use LLM to detect market direction. Reject BUY if LLM says bearish with >50% confidence. Reject SELL if LLM says bullish with >50% confidence. |

---

## 6. Risk Management Gates

Sequential gates — **any single gate blocking prevents the trade**.

| Gate | Rule | Default Threshold | Action on Block |
|------|------|-------------------|-----------------|
| 1 | Kill Switch | Active = true | BLOCK all trades |
| 2 | Bot Enabled | Enabled = false | BLOCK all trades |
| 3 | Paper Only | `BLOFIN_DEMO != "1"` | BLOCK — safety net |
| 4 | Daily Loss Limit | Daily P&L <= **-$250** | BLOCK + auto-activate kill switch |
| 5 | Max Position Size | Trade > **10%** of equity | BLOCK this trade |
| 6 | Max Open Positions | Open positions >= **3** | BLOCK new entries |
| 7 | No Duplicates | Same coin already open | BLOCK duplicate entry |

### Stock Bot Additional Gates

| Gate | Rule | Threshold |
|------|------|-----------|
| 8 | PDT Rule | Equity < $25K and 3+ day trades in rolling 5 days |
| 9 | Market Hours | Market closed (outside 9:30 AM - 4:00 PM ET weekdays) |

---

## 7. Position Sizing

| Parameter | Formula | Default |
|-----------|---------|---------|
| Max position % | Configurable | **10%** of equity |
| Size in USD | `equity * (max_position_pct / 100)` | — |
| Size in coins | `size_usd / current_price` | — |
| Size in shares (stocks) | `int(size_usd / current_price)` | Whole shares only |

### BloFin Contract Conversion (Crypto)

| Step | Formula |
|------|---------|
| Raw contracts | `coin_amount / contract_value` |
| Lot rounding | `int(raw_contracts / lot_size) * lot_size` |
| Minimum enforce | `max(contracts, min_size)` |

---

## 8. Stop Loss & Take Profit

### ATR-Based SL/TP

| Parameter | Formula | Risk:Reward |
|-----------|---------|-------------|
| ATR floor | `effective_atr = max(atr_14, price * 0.005)` | Ensures SL/TP never collapse to 0 |
| **Stop Loss** | **1.5x ATR** from entry | — |
| **Take Profit** | **2.5x ATR** from entry | **1:1.67 R:R** |

**BUY trade:**
```
stop_loss  = entry_price - (1.5 * effective_atr)
take_profit = entry_price + (2.5 * effective_atr)
```

**SELL trade:**
```
stop_loss  = entry_price + (1.5 * effective_atr)
take_profit = entry_price - (2.5 * effective_atr)
```

---

## 9. Exit Rules

Four exit rules checked on every scan cycle for open trades.

### Rule 1 & 2: Hard Stop Loss / Take Profit

| Side | Stop Loss Trigger | Take Profit Trigger |
|------|-------------------|---------------------|
| BUY | `current_price <= stop_loss` | `current_price >= take_profit` |
| SELL | `current_price >= stop_loss` | `current_price <= take_profit` |

### Rule 3: Trailing Stop (Profit Lock)

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Activation | Trade is **1.5%+** in profit | Start trailing |
| Retracement | Price retraced **50%+** from TP target back toward entry | EXIT — lock remaining profit |
| Min profit | P&L must still be **> 0.5%** | Safety check before exit |

### Rule 4: Time-Based Exit (Stale Trade)

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Time open | **>= 24 hours** | Evaluate for exit |
| P&L range | P&L between **-0.3%** and **+0.3%** | EXIT — trade is stale, free capital |

---

## 10. LLM Validation Pipeline

Every signal that passes risk gates goes through multi-model LLM validation.

### Architecture

```
Signal → [Gate 1: Ollama (local)] → [Gate 2a: OpenRouter Sentiment] → [Gate 2b: OpenRouter Risk]
                                          ↓
                                    Majority Vote → APPROVE / REJECT
```

### Vote Rules

| Scenario | Requirement | Threshold |
|----------|-------------|-----------|
| All 3 validators available | Majority vote | **2 of 3** must approve |
| Ollama unavailable | Both OpenRouter agree | **2 of 2** must approve |
| One OpenRouter fails | Remaining 2 vote | **2 of 2** must approve |
| No validators available | Auto-approve | Paper trading safety net |

### What Each Validator Checks

**Ollama (Local, Fast — Gate 1):**
- RSI direction alignment (overbought for sells, oversold for buys)
- MACD histogram strength and direction
- Historical win rate (flags if < 40%)
- Confluence count (needs 2-3+ indicators aligned)
- Outputs: `approve/reject`, `confidence` (0-1), `reasoning`

**OpenRouter Sentiment (Gate 2a):**
- Momentum direction and strength
- Multi-indicator confluence assessment
- Historical performance context
- Market narrative and sector rotation
- Outputs: `approve/reject`, `confidence` (0-1), `reasoning`

**OpenRouter Risk (Gate 2b):**
- Risk score (0-100, reject if **> 60**)
- False signal probability (0-1, reject if **> 0.5**)
- Risk/reward ratio assessment
- Historical win rate impact (< 40% raises rejection bar)
- Outputs: `approve/reject`, `risk_score`, `false_signal_prob`, `reasoning`

### Performance History Fed to LLMs

The last 7 days of closed trades are summarized and included in every validation prompt:

| Metric | Calculation |
|--------|-------------|
| Total trades | Count of closed trades |
| Win rate | `wins / total * 100` |
| Average P&L | `sum(pnl) / total` |
| Total P&L | `sum(pnl)` |
| By strategy | Win rate and avg P&L per strategy name |
| By coin | Win rate and avg P&L per coin |

---

## 11. Trending Auto-Add (Volume Surge Detection)

When the trending panel is refreshed, high-volume coins are auto-added to the bot's coin list.

| Parameter | Threshold | Action |
|-----------|-----------|--------|
| Volume surge | **>= 1.5x** average volume | Add to selected coins |
| Must be in COIN_MAP | Coin must be tradeable on BloFin | Skip if not listed |
| Existing coins | **Never removed** | Only append, never drop |
| Duplicate check | Skip if already selected | No duplicates |

---

## 12. Configurable Parameters (Database)

### Crypto Bot Config

| Key | Default | Type | Description |
|-----|---------|------|-------------|
| `bot_enabled` | 0 | Boolean | Master on/off switch |
| `kill_switch` | 0 | Boolean | Emergency stop — closes all |
| `max_position_pct` | 10 | % of equity | Max size per trade |
| `daily_loss_limit` | 250 | USD | Daily max loss before kill switch |
| `max_open_positions` | 3 | Count | Max concurrent trades |
| `scan_interval_sec` | 300 | Seconds | Time between scan cycles |
| `selected_coins` | BTC, ETH, SOL, BNB | JSON array | Coins to scan |
| `daily_goal` | 50 | USD | Daily profit target (display only) |
| `direction_bias` | both | Enum | long_only / short_only / both / auto |

### Stock Bot Config

| Key | Default | Type | Description |
|-----|---------|------|-------------|
| `stock_bot_enabled` | 0 | Boolean | Master on/off |
| `stock_kill_switch` | 0 | Boolean | Emergency stop |
| `stock_max_position_pct` | 10 | % of equity | Max size per trade |
| `stock_daily_loss_limit` | 250 | USD | Daily max loss |
| `stock_max_open_positions` | 3 | Count | Max concurrent |
| `stock_scan_interval_sec` | 300 | Seconds | Scan frequency |
| `stock_selected_stocks` | AAPL, TSLA, NVDA, MSFT, AMD | JSON array | Stocks to scan |
| `stock_daily_goal` | 50 | USD | Daily target |
| `stock_direction_bias` | long_only | Enum | Direction filter |
| `stock_broker` | alpaca | String | Broker selection |

---

## 13. Available Coins & Stocks

### Crypto (BloFin)

| Symbol | Name | Default |
|--------|------|---------|
| BTC-USDT | Bitcoin | Yes |
| ETH-USDT | Ethereum | Yes |
| SOL-USDT | Solana | Yes |
| BNB-USDT | BNB | Yes |
| XRP-USDT | XRP | No |
| ADA-USDT | Cardano | No |
| DOGE-USDT | Dogecoin | No |
| AVAX-USDT | Avalanche | No |
| DOT-USDT | Polkadot | No |
| LINK-USDT | Chainlink | No |
| MATIC-USDT | Polygon | No |
| ATOM-USDT | Cosmos | No |

### Stocks (Alpaca Paper)

| Symbol | Name | Default |
|--------|------|---------|
| AAPL | Apple | Yes |
| TSLA | Tesla | Yes |
| NVDA | NVIDIA | Yes |
| MSFT | Microsoft | Yes |
| AMD | AMD | Yes |
| AMZN | Amazon | No |
| GOOGL | Alphabet | No |
| META | Meta | No |
| PLTR | Palantir | No |
| COIN | Coinbase | No |
| SOFI | SoFi | No |
| SPY | S&P 500 ETF | No |
| QQQ | Nasdaq ETF | No |
| MARA | Marathon Digital | No |
| RIOT | Riot Platforms | No |

---

## 14. Safety Rules

| Rule | Enforcement | Purpose |
|------|-------------|---------|
| Paper trading only (crypto) | `BLOFIN_DEMO == "1"` checked at client init | Prevent real money trading |
| Paper trading only (stocks) | `paper=True` hardcoded in Alpaca client | Prevent real money trading |
| Kill switch auto-trigger | Activated when daily loss >= $250 | Circuit breaker |
| Read-only API fallback | Detects `READ-ONLY` error, records as paper trade | Graceful degradation |
| No duplicate positions | Risk gate 7 blocks same coin | Prevent over-concentration |
| Max 3 concurrent positions | Risk gate 6 enforced | Capital preservation |
| ATR floor for SL/TP | `max(atr, price * 0.5%)` | Prevent SL=TP on low-priced coins |
| 30/60 min cooldown | Per-coin, longer after loss | Prevent over-trading |
| 24h stale trade exit | Close flat trades after 24h | Free trapped capital |
