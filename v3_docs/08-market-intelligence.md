# 08 · Market Intelligence

TradeWiz bundles several alternative-data feeds that read the *crowd*, *institutions*, and
*politics* rather than just the chart. Most live under their own tabs; several are
**Pro-plan** features.

| Feature | Tab | Plan |
|---------|-----|------|
| Prediction Markets | **Markets** | Pro |
| Congress Trades | **Congress** | Pro |
| Smart Money (13F + options flow) | **Smart Money** | Pro (options flow open to all) |
| Trump Market Indicator | **Trump** | Pro |
| Sector Radar | **Research** | all |
| Option Calls | **Option Calls** | all |

---

## Prediction Markets (Markets tab) — Pro

Live odds from **Polymarket + Kalshi** distilled into a single market-mood read.

- **Betting Market Mood** banner — BULLISH / LEAN BULL / NEUTRAL / LEAN BEAR / BEARISH,
  derived from rate-cut/hike odds, recession risk, index & BTC targets, geopolitical and
  tariff markets.
- **Major Movers** — events with the biggest 24h odds swings.
- **All Markets** — every tracked market with current odds and change, filterable by
  category (election, crypto, macro, tech, …).

Use it as a **macro backdrop check** before position trades. The pulse-strip **Poly/Kalshi**
tile is a shortcut to this tab. *(Routes: `GET /api/predictions`, `/api/predictions/sentiment`.)*

---

## Congress Trades (Congress tab) — Pro

Stock disclosures filed by members of Congress under the STOCK Act.

- **Filters:** chamber (House / Senate / all), politician name, state, ticker, and lookback
  (7 / 14 / 30 / 90 days, 6 months, 1 year).
- **Stats bar:** total trades, most active members, top states, buys vs. sells.
- **Sections:** Top Traders, related News, and a detailed filings table.

Data comes from official House Clerk PTR filings plus Senate sources (QuiverQuant, news,
and curated feeds). *(Route: `GET /api/congress/trades`.)*

---

## Smart Money (Smart Money tab)

Two views via a sub-nav:

### Institutions — Pro
Tracks the top hedge funds and institutional investors from **SEC 13F filings**:
fund holdings, recent adds/exits, and **convergence signals** — tickers where multiple
funds are building the same position. *(Routes: `/api/smart-money/summary`,
`/holdings/<id>`, `/ticker/<ticker>`, `/signals`, `/refresh`.)*

### Sector Options Flow — open to all
Options money flow across the **11 SPDR sector ETFs** (XLK, XLV, XLY, XLF, XLE, …):
call vs. put volume, open interest, net premium, and a **Bullish / Bearish / Neutral**
read per sector with money-flow direction (inflow vs. outflow). This is the same data on
the Dashboard's **Sector Options Flow** card. *(Route: `/api/smart-money/sector-flow`.)*

---

## Trump Market Indicator (Trump tab) — Pro

An AI read on presidential rhetoric and its likely market impact.

- **Actionable Signal** — a single "what to do now" call: **BUY (add risk) / TRIM /
  SIDELINE / WAIT (unusual quiet)**.
- **Current Mood** — a –100…+100 index (BULLISH … BEARISH) from market-moving keywords,
  amplified by ALL-CAPS, exclamations, and topics like China/Iran/tariffs.
- **AI Prediction** — a forward-looking forecast of the next likely action and its sector
  impact (generate on demand).
- **Trade Signals** — sectors/assets the rhetoric favors vs. those to avoid (e.g. China
  tariffs → avoid semis, favor domestic manufacturing; Iran conflict → favor defense &
  oil).
- **Mood timeline**, a **Mood→Market forecaster** (self-learning ensemble), a
  **lead/lag correlation** matrix, a **policy-reversal** tracker, detected key signals, and
  notable posts.

Data sources include Truth Social, GDELT/Google News, and White House feeds; snapshots are
stored for history. *(Routes: `GET /api/trump/mood`, `/mood/history`,
`POST /api/trump/mood/predict`.)* The pulse-strip **Trump Mood** tile links here.

---

## Sector Radar (Research tab)

The daily AI research analyst that names the **next hot sector** for a 6–12 month rotation.
Covered in detail in [Research & Fin Skills](09-research-finskills.md); also surfaced on the
[Dashboard](03-dashboard.md).

---

## Option Calls (Option Calls tab)

Tracks which **call contracts** (by strike/expiry) are gaining or losing volume — a quick
read on where call buyers are leaning.

- Enter a ticker and click **Show Calls**.
- See call buckets with **rising** (green) vs. **falling** (red) volume, plus implied
  volatility, open interest, last price, and volume/OI ratio.

Data comes from Webull with a yfinance fallback. *(Route: `GET /api/options/calls?symbol=AAPL`.)*

---

## ThunderBot Market Regime (Watchdog)

The **Watchdog** engine also exposes a composite **Market Regime** read — trend (SPY vs.
SMA50/200), volatility (VIX), momentum, and breadth — rolled into a 0–100 score and a
trade gate (**ALLOWED / WAIT_REGIME**). It powers the pulse-strip **Market Regime** tile and
feeds the ThunderBot auto-trader ([Claude Bot & ThunderBot](12-claude-thunderbot.md)).
*(Routes: `/api/watchdog/summary`, `/signals`, `/candidates`, `/sentiment`, `/health`.)*
