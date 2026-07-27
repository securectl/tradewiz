# 03 · Dashboard

The **Dashboard** is your home hub — a single scrollable view that pulls together market
conditions, the next hot sector, your bots, and fresh opportunities.

---

## Market Regime card (top)

A live read on overall market health, fed from the same market gauge as the pulse strip.

- **Regime badge:** RISK-ON · RISK-OFF · DANGER · NEUTRAL (with an LED indicator).
- **Key metrics:** composite score, VIX, SPY 5-day %, Smart Money tilt, put/call ratio.

Use this to decide whether the environment favors taking new positions.

---

## Hero grid

### Sector Radar card (left)
The AI research analyst's pick for the **next hot sector** over a 6–12 month rotation.

- A **NEXT HOT SECTOR** badge with the sector name + its ETF.
- An **investment thesis** paragraph.
- A **"Why Now"** bullet list (top three drivers).
- **Leader tickers** as clickable buttons (jump straight to the Analyzer).
- A **conviction donut** (0–100%) and the **runner-up** sector.

If no research has run yet, the card invites you to open **Research → Run now**. Full
details in [Research & Fin Skills](09-research-finskills.md).

### KPIs grid (right)
Four tiles summarizing your trading:
1. **Total P&L** — net dollars + win/loss record.
2. **Win Rate** — % winners, trade count, average P&L.
3. **Daily Goal** — actual vs. your $ target with a progress bar.
4. **Active Bots** — how many of the four bots are running.

---

## Second row

### Sector Leaderboard (left)
A ranked table of sectors by composite momentum:
**Rank · Sector · Score (bar) · RS 3M % · Trend (MA-stack / above-50d / below-50d) ·
Volume surge (×).** The sector picked by Sector Radar is highlighted.

### Bot Performance (right)
A compact view of all four bots (**Crypto · Stock · Watchdog · Claude**):
status dot (green = running), 30-day trade count, a sparkline, P&L, and win rate, with a
combined 30-day total at the bottom.

---

## Sector Options Flow card
Where options money is moving by sector — two columns, **In-flow** (green) and
**Out-flow** (red), highlighting sectors with the strongest call vs. put activity. This is
the same data surfaced in **Smart Money → Sector Options Flow**
([Market Intelligence](08-market-intelligence.md)).

---

## Opportunities card
Fresh tickers surfaced from recent **Screener** runs — *oversold · breakout · momentum* —
each tagged with a buy/sell/neutral flow color.

## Oversold Stocks card
The most-watched oversold names with price, change %, and a sparkline — candidates that may
be completing a base and ready to bounce.

## Your Best Sectors card
Your **own realized P&L broken down by sector**, as horizontal bars (green = net wins,
red = net losses) — a quick read on where your trading actually makes money.

---

## Behind the dashboard (for reference)

The dashboard is assembled from a few endpoints you may see referenced in support:

| Panel | Endpoint |
|-------|----------|
| High-level metrics | `GET /api/dashboard/summary` |
| Tracked sectors | `GET /api/dashboard/my-sectors` |
| Recent screener wins | `GET /api/dashboard/opportunities` |
| Oversold names | `GET /api/dashboard/oversold` |
| Bot performance | `GET /api/bot/dashboard?asset=all` |
| Market regime tile | `GET /api/market/gauge` |
