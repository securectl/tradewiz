# Breakout Strategy — Phased Roadmap

Status: **Phase A complete (Stage 2 detector live in Breakout Scanner).** Phases B and C are deferred until Stage 2 signals have been observed for a few days.

## Phase A — Done

- New `_detect_stage2_breakout` in `analysis_engine.py` (catches INTC/SNDK/MU-style recovery setups).
- Wired into `qullamaggie_scan` as a parallel branch ahead of the strict momentum gate.
- UI splits results into 🚀 Rocket Setups (Stage 2) and ⚡ Momentum Breakouts (HTF/VCP/EP).
- Stage 2 cards show phase (`breakout` / `loaded` / `basing`) plus context metrics (`above_52w_low_pct`, `vol_expansion`, `sma_ratio`, etc.).

## Phase B — Stock bot → Breakout Strategy

Replace `stock_bot/stock_engine.py`'s 9 day-trade strategies (MACD, EMA Trend, RSI Reversion, Momentum, BB Reversion, Grid, Trend DCA, Doji, Pump on Close) with the 4 breakout setups (HTF, VCP, EP, Stage 2). Position-trade scale, not scalp.

### Scope of changes

- **Signal generation**: `_generate_signal` calls into `_detect_qullamaggie_setup` and `_detect_stage2_breakout` instead of the 9 inline strategies.
- **Hold horizon**: weeks, not hours. Drop the 5-min scan loop; daily scan only (or 30-min during market hours).
- **Exit rules**:
  - Stop: 8–10% below entry, or break of SMA50, whichever is closer.
  - Profit-take: trail at 20-day low after +20%; full exit at +50% or stage transition (Stage 2 → Stage 3 distribution).
  - Time exit: drop the current short-window time exits.
- **Position sizing**: larger per name, fewer concurrent (max 4). Risk 0.5–1% of equity per trade.
- **LLM validation**: keep, tuned for breakout context (`is the chart actually setting up vs. fake breakout?`).
- **Risk gates**: keep daily loss limit, kill switch, regime check; relax PDT/trade-count gates since fewer trades.

### Migration risks

- Existing open day-trade positions need a graceful handoff (close out before swap, or let them complete on old rules with a feature flag).
- Win-rate metrics will reset; backtest the breakout-only flow against ~6 months of data first.

## Phase C — Unified entry feed across bots

Make the stock bot subscribe to two upstream signal sources instead of generating its own:

1. **Oversold day-over-day** (already in `oversold_daily`, consumed by `claude_bot`).
2. **Stage 2 / momentum breakouts** (new from Phase A, surfaced by `qullamaggie_scan`).

Stock bot becomes a **router**: pull candidates from both feeds, dedupe, apply per-source position-sizing rules, route through LLM validation, execute. Less strategy code in `stock_engine`, more shared infrastructure.

### Benefits

- One source of truth for screening (no parallel logic in 3 bots).
- Stage 2 picks naturally feed both `claude_bot` (scalp-rotate) and the stock bot (position trade) with different exit rules.
- Easier to reason about: bots differ on **execution style**, not on **what they look at**.

### Implementation sketch

- Extract candidate-sourcing into a shared `signals/feed.py`:
  - `iter_oversold_persistent(min_days=3)`
  - `iter_stage2_breakouts(min_score=6)`
  - `iter_htf_vcp_ep(min_score=7)`
- `stock_engine` consumes via these feeds, drops its inline strategy code.
- `claude_bot._get_candidates` switches to the same feed (it already pulls from `oversold_daily` directly — minimal refactor).

## Decision Gate

Before starting Phase B:

- Watch the Breakout Scanner Rocket Setups for 1–2 weeks.
- Confirm Stage 2 picks actually move (track outcomes, not just signals).
- If win rate < 40% or average move < 8%, revisit detector gates before bot integration.
