# 12 · Claude Bot & ThunderBot

Two more automated, invite-only bots round out the trading suite. Both run **paper-only**.

---

## Claude Bot

A scalp + **re-entry** bot for US equities. It sources oversold/momentum candidates from
the screener, takes quick profits, and — uniquely — keeps an eye on names it just exited to
**re-enter** them if they stabilize.

**Where:** the **Claude Bot** tab (requires bot access). Broker: Alpaca/Webull paper.

### How it works
1. **Sources candidates** from the daily **oversold** tracker (filtered by AI confidence)
   plus screener **Top Gainers**, ranked by a multi-timeframe RSI composite.
2. **Enters** on oversold/momentum (e.g. daily RSI < 30 to buy), sized to a configurable
   % of equity, up to a max number of concurrent positions.
3. **Exits** on a take-profit (~4–5%), a tight scalp stop (~2%), or a hard stop (~8%).
4. **Re-entry watchlist** — after a take-profit or scalp-stop exit (not a hard stop), the
   ticker goes on a watchlist; if it stabilizes on the next cycle, the bot re-enters at a
   **reduced size**, for a limited number of attempts within a few-day window.

### Key settings
`cb_enabled`, `cb_broker`, `cb_max_positions`, `cb_max_position_pct`,
`cb_max_total_exposure_pct`, `cb_daily_loss_limit`, `cb_min_confidence`,
`cb_take_profit_pct`, `cb_scalp_stop_pct`, `cb_hard_stop_pct`, `cb_reentry_size_factor`,
`cb_reentry_max_attempts`, `cb_reentry_window_days`. It self-heals (kill switch auto-clears
on a new day) like the other bots.

### What you see
Status (running, open positions, daily P&L, goal progress, watchlist count), the **re-entry
watchlist**, an **oversold tracker**, account balance, and activity logs. *(Routes:
`GET /api/claude_bot/status`, `/watchlist`, `/oversold-tracker`, `/balance`, `/logs`;
`POST /api/claude_bot/start`, `/stop`, `/config`, `/trades/<id>/close`.)*

---

## ThunderBot (Watchdog)

An AI-driven **alerts + auto-trader** built on TradeWiz's market-regime engine. It watches
for setups (RSI + volume + bull-flag style candidates), gates them by overall market
regime, and can place paper trades automatically.

**Where:** the **ThunderBot** tab (requires `watchdog` bot access).

### Trading window & exits
ThunderBot is tuned for a defined intraday window — **buy 9:45 AM–1:00 PM ET** — and takes
profits in a roughly **4–7%** band. (This is the internal "watchdog" bot.)

### Market-regime gate
Every decision passes through the composite **Market Regime** read (trend, volatility,
momentum, breadth) that also powers the pulse-strip tile. When the regime says
**WAIT_REGIME**, new entries are held.

### Controls & config
Start/stop the auto-trader, toggle its kill switch, and set its parameters: `wd_mode`,
`wd_scan_interval`, `wd_max_positions`, `wd_max_position_pct`, `wd_max_total_exposure_pct`,
`wd_daily_loss_limit`, `wd_min_confidence`, and a custom `wd_watchlist`. *(Routes:
`POST /api/watchdog/auto/start`, `/stop`, `/config`, `/kill`;
`GET /api/watchdog/summary`, `/candidates`, `/signals`, `/sentiment`, `/health`;
`GET/POST /api/watchdog/watchlist`.)*

### What you see
Bot status, active candidates/signals, recent triggers, and the regime/sentiment summary.

---

## All four bots, side by side

| | Crypto | Stock | Claude | ThunderBot |
|--|--------|-------|--------|------------|
| Assets | Crypto (BloFin) | US stocks (Alpaca/Webull) | US stocks | US stocks |
| Style | Multi-strategy scalp/swing | Multi-strategy scalp/swing | Oversold scalp + re-entry | Regime-gated intraday |
| Access flag | `crypto` | `stock` | bot access | `watchdog` |
| Daily-goal default | $500 | $500 | $500 | $500 |
| Mode | Paper (live opt-in) | Paper (live opt-in) | Paper | Paper |

> All bots auto-restart after a server restart if they were enabled, and all surface their
> trades in the [Tracker](07-tracker-journal.md) and the unified bot dashboard.
