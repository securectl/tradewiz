# 05 · Screener & Scanner

The **Screener** scans curated universes of stocks (and crypto) by category and uses AI to
**vet each candidate**, returning a verdict, a confidence score, and a one-line rationale.

**How to run:** open **Screener**, pick a **category**, adjust filters, and click
**Scan & Vet with AI**. *(Route: `POST /api/screener`.)*

---

## Categories

| Category | Universe | What the AI looks for |
|----------|----------|------------------------|
| **Low-Cap** | ~$50M–$2B | Momentum + risk/reward, recent earnings, insider activity |
| **Mid-Cap** | ~$2B–$20B | Growth potential, execution risk, technical setup |
| **Large-Cap** | $20B+ | Sector positioning, relative value, growth runway |
| **ETFs** | Broad indices | Sector rotation, theme exposure, beta alignment |
| **Metals & Mining** | All caps | Commodity cycle, geopolitics, production |
| **Crypto** | All caps | Market cycle, L1/L2/DeFi positioning, regulation |
| **AI** | All caps | AI moat, revenue exposure, valuation vs. peers |
| **Top Gainers** | All caps | 1-day / 1-week / 3-month momentum |
| **Top Losers** | All caps | Recovery / oversold-bounce setups |
| **Oversold** | All caps | RSI <30, basing, volume profile, reversion |

---

## Filters & controls

- **Min / Max price** sliders (defaults 2 and 15 for low-cap; ranges adjustable).
- **Scan limit** — how many candidates to vet (5–50, default 20).
- **Sector pills** — narrow to specific sectors (populated per category).
- **Timeframe** — for Gainers/Losers: 1 Day, 1 Week, 3 Months.
- A **risk banner** reminds you small-caps ($2–$15) carry elevated risk — never allocate
  more than ~2% per position.

---

## What each result shows

For every vetted candidate:

- **Ticker, name, sector, industry**
- **Price, market cap, 52-week range**
- **Verdict** — category-specific label, e.g. **OPPORTUNITY / RISKY / AVOID** (low/mid),
  **STRONG GROWTH / STEADY** (large-cap), **STRONG BUY / ACCUMULATE** (ETF),
  **BULLISH / NEUTRAL / BEARISH** (crypto), **MOMENTUM BUY / WATCH** (gainers),
  **RECOVERY BUY / WATCH** (losers), **RECOVERY READY / CONSOLIDATING / STILL FALLING**
  (oversold).
- **Confidence (0–100)** — 0–40 low · 41–70 medium · 71–100 high.
- **Summary** — 1–2 sentence AI rationale.
- **Technical snapshot** — price vs. 50d/200d SMA, RSI, volume ratio, ADR%.
- **Fundamental context** and **money-flow** indicators where available.

> **Caching:** results are cached per category per day to keep things fast and control AI
> cost. Re-running a category the same day usually returns the cached set.

---

## Hot Sectors

At the top of the Screener, **Hot Sectors** uses AI to name the trending themes over a
chosen window — **1W · 2W · 1M (default) · 3M · 6M · 1Y** — with a short narrative on why
each is hot. *(Route: `GET /api/screener/hot-sectors?period=1mo`.)*

---

## History & Trending

TradeWiz remembers what the screener surfaces, so you can spot **persistent** signals.

- **Past Scans** — browse prior results by category, ticker, or date (up to 90 days).
  *(Route: `GET /api/screener/history`.)*
- **Trending Signals** — tickers that appear repeatedly across days, with appearance count,
  average confidence, latest verdict, and first/last-seen dates.
  *(Route: `GET /api/screener/trending`.)*

A name that keeps reappearing with rising confidence is often a stronger setup than a
one-day flash.

---

## Exporting

The export bar lets you set a date range, optionally **deduplicate** (one row per ticker,
showing "seen N days"), and export to **CSV, TXT, or PDF**.

---

## Suggested workflow

1. Pick a category that fits your thesis (e.g. **Oversold** for bounce plays).
2. Set price/limit filters and **Scan & Vet with AI**.
3. Sort by confidence; open high-confidence names in the [Analyzer](04-analyzer.md).
4. Check **Trending Signals** — favor names showing multi-day persistence.
5. Log anything you act on in the [Tracker](07-tracker-journal.md).
