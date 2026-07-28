# 06 · Breakout Scanner (Qullamaggie)

The **Breakout Scanner** hunts for high-conviction momentum setups using the Qullamaggie
methodology. Open the **Breakout Scanner** tab, choose a universe, and click
**Scan for Setups**. *(Route: `POST /api/qullamaggie` with `category`.)*

**Universe:** All Stocks · Low-Cap · Mid-Cap · Large-Cap.

---

## The four setup types

| Setup | Entry trigger | Stop | Typical hold |
|-------|---------------|------|--------------|
| **HTF — High Tight Flag** | Break above 60-day peak | Below recent swing low | 3–5 days |
| **VCP — Volatility Contraction** | Break above the pivot (tightest range) | Below recent support | 5–10 days |
| **EP — Episodic Pivot** | Above the gap-day opening range | Below the gap-day low | 1–3 days |
| **Stage 2 — Recovery breakout** | At current price (already loaded) | Below 10-day low | 2–4 weeks |

---

## The setup score (1–10)

Every setup starts at **5.0** and earns or loses points:

- **MA alignment** (+1.5) — price > SMA200, SMA150 > SMA200, SMA50 > SMA150.
- **Relative strength** (+0.5 each gate) — ≥25% over 1m, ≥50% over 3m, ≥150% over 6m.
- **ADR%** — +1.0 if ≥8%, +0.5 if ≥5% (volatility you can trade).
- **Setup-type bonus** — EP +0.5 (gap conviction); HTF +1.0 if the prior move ≥100%;
  VCP +0.5 if depth <5% (very tight); Stage 2 weighted by phase
  (breakout > loaded > basing).

**Higher score = higher probability.** Treat 8+ as your A-setups.

---

## What each result shows

- **Ticker, name, sector**
- **Setup type** (HTF / VCP / EP / Stage 2) and **score (1–10)**
- **Entry price, stop loss, and suggested position size / shares** (sized to ~0.25% account
  risk per trade on a $25K example)
- **Current price** (vs. entry)
- **ADR %** — average daily range as a volatility proxy
- **Relative strength** — 1-month / 3-month / 6-month gains
- **Volume ratio** (vs. 20-day average) and **dollar volume** (>$3M/day preferred for
  liquidity)
- **MA alignment** flag and **consolidation context** (depth %, days formed)
- **Prior move** context (gap % for EP, run-up % for HTF, etc.)
- An AI-generated **sell plan**

---

## How to use it

1. Scan a universe and sort by **score**.
2. Confirm the name has enough **dollar volume** to trade cleanly.
3. Open it in the [Analyzer](04-analyzer.md) to see the pattern and trade plan in context.
4. Respect the **stop** — these are momentum setups; cut losers fast.
5. Log entries and exits in the [Tracker](07-tracker-journal.md).

> The same setup types (HTF, VCP, EP) appear inside the Analyzer's **Pattern Detected**
> panel and drive the **Stock Bot's** swing strategies ([Stock Bot](11-stock-bot.md)).
