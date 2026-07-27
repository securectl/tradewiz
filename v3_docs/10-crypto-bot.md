# 10 · Crypto Trading Bot

The **Crypto Trading** bot scans your selected coins on a cycle, detects technical signals,
runs them past AI validators, and executes **paper trades** with full risk management.

> 🔒 **Paper trading by default.** The bot runs against BloFin **demo mode**. Live trading
> is opt-in per user and gated; if it is ever enabled, the bot logs a
> `LIVE TRADING ENABLED` warning and the same risk gates apply.
>
> 🎟️ **Bot access is invite-only** — the tab only appears if an admin granted you `crypto`
> bot access (it is not part of any paid plan).

**Where:** the **Crypto Trading** tab. **Setup:** add BloFin API keys in
[Settings](15-settings-api-keys.md) first.

---

## Controls (top bar)

**Status** (Running/Stopped) · sensor status · **balance** · **today's P&L** ·
**daily-goal progress**, plus buttons: **Start · Stop · ⚙ Config · KILL** (emergency
close-all). *(Routes: `POST /api/bot/start`, `/stop`, `/kill`; `GET /api/bot/status`.)*

---

## Strategies (9)

The bot evaluates these in order; the first match fires:

1. **MACD Crossover** — bullish/bearish MACD cross with RSI & EMA20 alignment
2. **EMA Trend** — SMA8 crossing EMA20 with RSI/MACD agreement
3. **RSI Mean Reversion** — oversold/overbought with MACD turning
4. **Momentum Breakout** — price > SMA50/200 + volume surge + RSI band
5. **Bollinger Band Reversion** — price at band extremes + RSI extreme
6. **Grid Mean Reversion** — price ≥1.5 ATR from SMA50 in the trend direction
7. **Trend Continuation / DCA** — pullback to EMA20 in a confirmed trend
8. **Doji Reversal** — indecision candle reversing at an extreme
9. **Pump/Dump on Close** — volume + large body + close near the high/low

**Pre-filters** skip dead or too-thin markets (very low ATR, sub-$1 coins with tight
ranges) and enforce a per-coin **cooldown** after losses (longer after consecutive losses).

---

## Coins

Defaults include **BTC, ETH, SOL, BNB**; you can add others (XRP, ADA, DOGE, AVAX, DOT,
LINK, MATIC, ATOM, …). TradeWiz auto-maps coins to the right data ticker, so you can add
most pairs. Edit the list in the bot's **Config**.

---

## AI validation pipeline

Each candidate must pass the validators before a trade opens:

1. **Gate 1 — Ollama (optional, fast):** checks indicator confluence, RSI alignment, and
   your recent win rate. If Ollama is unavailable, it's skipped.
2. **Gate 2 — OpenRouter (two models):** a **sentiment** model scores momentum/direction; a
   **risk** model scores risk and false-signal probability (and can reject outright).

**Voting:** with all three available, ≥2 must approve; if Ollama is down, both OpenRouter
models must agree; if none are reachable, the trade is rejected (safe default). Validators
are tuned to be **trade-friendly in paper mode** so the bot stays active.

---

## Market sensor (pre-cycle macro gate)

Before each cycle the bot checks BTC/ETH health (cached ~30 min):

| Status | Roughly | Action |
|--------|---------|--------|
| **HEALTHY** | normal | trade normally |
| **CAUTION** | BTC −5…−8% / ETH −7…−10% in 24h | require high-confidence (≈80%) trades only |
| **DANGER** | BTC −8% or ETH −10% in 24h | skip the entire cycle |

---

## Risk gates (defaults)

Trades must clear every gate, in order:

| Gate | Default |
|------|---------|
| Kill switch | must be OFF |
| Bot enabled | must be ON |
| Paper mode | enforced |
| **Daily loss limit** | **−$500** (scalp) / −$2,000 (swing) → breach auto-fires the kill switch |
| **Max daily trades** | **25** (scalp) / 10 (swing) |
| **Max position size** | **10%** of equity (scalp) / 25% (swing) |
| **Max total exposure** | **60%** of equity across all open positions |
| **Max open positions** | **6** (scalp) / 3 (swing) |
| No duplicate positions | one open position per coin |

> These are **defaults**; an admin or your per-user config can adjust them
> (`daily_loss_limit`, `max_position_pct`, `max_total_exposure_pct`, `max_open_positions`,
> `max_daily_trades`, `direction_bias`, `scan_interval_sec`, `daily_goal`, `selected_coins`).

---

## Entries & exits

- **Stops/targets** are ATR-based (with a price-percent floor so they never collapse on
  cheap coins) — roughly a 1:1.5 reward:risk.
- **Trailing stop** activates once a trade is in profit and exits on a meaningful retrace.
- **Time exit** closes stale trades that sit flat for ~24h.

---

## Self-learning & self-healing

- **Adaptive position sizing** — scales size based on your trailing 7-day win rate (scales
  *down* on weak performance; never inflates risk in paper mode).
- **Strategy blacklisting** — a strategy with a poor win rate over enough trades is paused.
- **Self-healing** — after the kill switch fires, the bot can auto-recover on a new trading
  day once the loss condition clears.

---

## Dashboard & logs

The **Crypto Trading** tab shows a performance dashboard (win rate, total trades, avg P&L),
a **daily P&L chart** (day/week/month/year/all-time), **open positions**, a **completed
trades** log, and a **trade journal**. The unified bot dashboard also breaks down P&L by
**strategy** and **top coins**, current **streak**, **daily-goal** progress, best/worst
trades, and fees (gross vs. net P&L). *(Route: `GET /api/bot/dashboard?asset=crypto`;
trades via `GET /api/bot/trades`; manual close via `POST /api/bot/trades/<id>/close`.)*

---

## Quick start

1. **Settings → API Keys:** add BloFin key, secret, and passphrase.
2. **Crypto Trading → ⚙ Config:** pick coins, scan interval, daily goal, and risk caps.
3. **Start.** Watch the first cycle in the logs.
4. Monitor open positions and the daily-goal progress; use **KILL** to flatten everything
   instantly if needed.

> See [Settings & API Keys](15-settings-api-keys.md) for credential setup and
> [Tracker](07-tracker-journal.md) for the consolidated trade feed.
