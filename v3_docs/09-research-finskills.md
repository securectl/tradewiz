# 09 · Research & Fin Skills

TradeWiz includes a research workspace and a library of professional-grade financial
analysis "skills."

---

## Research tab

A sub-nav with three views.

### Sector Radar (default)
The **auto research analyst** — it identifies the next hot sector for a 6–12 month rotation
play and writes a thesis to back it.

- A **Run now** button triggers a fresh analysis (runs in the background, one at a time).
- The latest report shows the **top sector** + ETF, an **investment thesis**, **"why now"**
  drivers, **leader tickers**, a **conviction** score, and the **runner-up**.
- **History** keeps prior reports so you can see how the call evolves.

**Schedule & depth:** a daily run uses a fast model; a deeper weekly synthesis uses a more
powerful model for multi-week trends. *(Routes: `GET /api/sector-radar/latest`,
`/history`, `/sector/<key>`, `POST /api/sector-radar/run`.)*

### Reports — Market Pressure
**Top buy & sell pressure**, derived from the latest screener verdicts plus volume. Choose
**Top 10 / 15 / 25** and refresh to see where net pressure is concentrated.

### Research Skills
A launcher for the financial-analysis skill catalog (below). Pick a skill on the left,
fill in its inputs on the right, and **Launch Analysis**; running and completed jobs appear
in a jobs list with downloadable results.

---

## Fin Skills tab — Financial Skills Hub

Professional, AI-powered analysis tools grouped by domain. Search by name or browse by
category. Each skill card shows a description, an estimated run time, and any tier
requirement.

Representative skills available:

| Domain | Skills |
|--------|--------|
| **Financial Analysis** | 3-statement model, DCF valuation, comps analysis, competitive analysis, model debug/audit, data cleanup |
| **Investment Banking** | CIM, teaser, buyer list, merger (accretion/dilution) model, process letter, deal tracker, one-pager / strip profile |
| **Equity Research** | Initiate coverage, earnings & earnings-preview, model update, morning note, sector overview, thesis tracker, catalyst calendar, screen/idea generation |
| **Private Equity** | IC memo, DD checklist & meeting prep, returns (IRR/MOIC), unit economics, portfolio monitoring, value-creation plan, deal screening/sourcing, AI-readiness |
| **Wealth Management** | Client report & review, financial plan, investment proposal, rebalancing, tax-loss harvesting |

> These produce institutional-grade outputs (Excel models, DOCX reports, decks). They use
> AI and count against your daily quota; some are gated by plan.

---

## When to use which

- **What's the next theme to rotate into?** → Sector Radar.
- **Where is buy/sell pressure right now?** → Reports / Market Pressure.
- **I need a real model or memo** (DCF, comps, IC memo, earnings note) → Fin Skills.
- **Quick single-name read** → the [Analyzer](04-analyzer.md) instead.
