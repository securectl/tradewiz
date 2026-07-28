# 04 · Analyzer

The **Analyzer** is TradeWiz's deep-dive on a single stock or crypto ticker: an interactive
chart plus a stack of collapsible analysis panels.

**How to open:** type a ticker in the header search, choose a **Period** (YTD, 1M, 3M,
6M default, 12M, 2Y) and **Interval** (Daily / Weekly), and click **Analyze**.
*(Route: `POST /api/analyze` with `ticker`, `period`, `interval`.)*

---

## The chart (left panel)

- **Header:** ticker, company name, current price, and change %.
- **Toolbar — moving averages & volume toggles:** 9 EMA (on), 20 SMA (on), 50 SMA,
  100 SMA, 200 SMA, and Volume (on).
- **Chart:** TradingView Lightweight Charts candlesticks with a volume sub-pane.

---

## Analysis panels (right side, collapsible)

### 1. Breakout Status
Where the price sits relative to a detected pattern: in/out of pattern, the active breakout
level, and confirmation state. Statuses include **breakout_up**, **breakdown**,
**pending**, or **no_pattern**. Confirmation uses a 3-candle close rule plus a volume
surge (≈1.5× average).

### 2. Pattern Detected
The chart pattern TradeWiz found, if any:
- Symmetrical / Ascending / Descending **Triangle**, **Pennant**, **Wedge**
- Momentum setups: **HTF** (High Tight Flag), **VCP** (Volatility Contraction), **EP**
  (Episodic Pivot)

For each pattern you get a **pattern score (0–100)**, breakout & support levels, a price
target, a stop, the risk:reward ratio, and trendline quality (R²).

> **Reading the pattern score:** 80–100 = textbook setup · 60–79 = solid · 40–59 =
> marginal (wait for confirmation) · <40 = questionable.

### 3. Trade Plan ($25K account example)
An AI-generated, ready-to-use plan: direction (bullish/bearish), entry zone, stop loss,
profit target(s), risk:reward, suggested position size / shares, expected hold duration,
and a conviction rating.

### 4. Technical Indicators
The full readout, color-coded green (bullish) / red (bearish) / neutral:

| Group | Indicators |
|-------|-----------|
| Moving averages | SMA 8/20/50/200, EMA 9/20 |
| Momentum | RSI(14), MACD line/signal/histogram |
| Volatility | ATR(14), ADR %, Bollinger Bands (width %, position %) |
| Volume | current volume, 10-day MA, relative volume |
| Crossovers | SMA8/EMA20 cross, MACD bullish/bearish cross |

*RSI: <30 oversold, >70 overbought. Bollinger position: <5% oversold, >95% overbought.*

### 5. Liquidity & Cash Health
A fast balance-sheet check: total cash, total debt, net cash, cash-to-debt and current
ratios, operating & free cash flow, FCF yield, and (for unprofitable names) a cash
**runway** in months. Summarized as a **health score (0–100)** and a rating —
**STRONG / OK / WEAK / RISK** — with a matching color.
*(Route: `GET /api/analyze/<ticker>/liquidity`.)*

### 6. Recent News & Social (24h)
Latest headlines and social chatter for the ticker, plus active catalysts (earnings dates,
FDA decisions, insider activity). A **Refresh** button pulls fresh news on demand.
*(Route: `GET /api/analyze/<ticker>/news?hours=24&force=1` — `hours` 1–72.)*

### 7. Financials (stocks)
Plain-language fundamentals: valuation (P/E trailing & forward, PEG, P/B, P/S, EV/EBITDA),
profitability (gross/operating/net margins, ROE, ROA), balance sheet (cash, debt,
debt/equity, current & quick ratios), cash flow, growth, beta, short interest, 52-week
range, and analyst targets/recommendations.

### 8. AI Multi-Model Validation
Click **Validate with AI** to run the multi-LLM consensus engine (Claude, Gemini,
DeepSeek) for a **BUY / STRONG BUY / WAIT / AVOID** verdict with confidence. Requires an
OpenRouter key configured and counts against your daily AI quota. See
[AI Validation](13-ai-validation.md).

### 9. 12-Month Investment Prediction
Click **Run 12-Month Prediction** for a forward-looking **INVEST / HOLD / PASS** thesis
with price targets (min/fair/max) and a survival-probability estimate. See
[AI Validation](13-ai-validation.md).

### 10. Earnings Analysis
On demand: the next earnings date, the market-implied expected move, and historical
surprise behavior.

---

## Money flow (within analysis)

The Analyzer also surfaces **equity money-flow** signals — Chaikin Money Flow (CMF) and
the Money Flow Index (MFI) — labeled *accumulation*, *distribution*, or *neutral*, with an
options-positioning overlay when options data is available.

---

## Tips

- **Period vs. interval:** use Daily/6M for swing setups; Weekly/2Y for big-picture trend.
- **Confluence beats any single signal** — line up pattern score, indicators, money flow,
  and AI validation before acting.
- Your analyses are saved to **History** (header) for 3 days so you can re-open them
  instantly.
