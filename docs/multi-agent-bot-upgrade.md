# Design Spec — Multi-Agent Trading Bots

**Status:** Proposal · **Branch:** `spec-multi-agent-bots` · **Author:** engineering
**Scope:** crypto + stock bots (`crypto_bot/`, `stock_bot/`)

---

## 1. Problem

Since inception the bots have not made money. This is not (only) a tuning
problem — it is **structural**:

- **Signal generation is 100% rule-based TA.** `_generate_signal()` in
  `crypto_bot/bot_engine.py` is a prioritized `if`-ladder of ~10 strategies
  (MACD, RSI reversal, momentum, Bollinger, doji, pump-on-close, …). No learning,
  no adaptation of the *entry logic* itself.
- **The "AI" only rubber-stamps.** `crypto_bot/crypto_validator.py::validate_trade`
  is 3–4 **independent, stateless single-shot LLM votes** (Ollama `gpt-oss:20b`,
  Gemini Flash "sentiment", DeepSeek "risk", optional supervisor) on an
  already-formed trade. They do not talk to each other, hold no memory, use no
  tools, and **auto-approve when unreachable** (paper-trading bias). It is a
  consensus filter, not an agent system.
- **Nothing optimizes for realized P&L.** Risk gates are rule-based
  (`risk_manager.py`). "Self-learning" (`_get_adaptive_params()`) is win-rate
  position scaling + strategy blacklisting — useful guardrails, but it tunes
  *sizing*, not the *decision*. The objective the pipeline actually maximizes is
  "did the vote pass," which is uncorrelated with money made.

Independent confirmation already exists in-repo: the forward-return validator
(`shared/signal_validator.py`) measured a **negative edge** on the analyzer's
megacap-tech recommendations. We should assume the current entry logic has **no
edge** until proven otherwise, and design so that edge is *measured*, not
*assumed*.

---

## 2. Goal

Replace the "generate → rubber-stamp → trade" flow with a **4-agent decision
system whose components are individually measurable against realized P&L**, plus
an **outcome-feedback loop** that adjusts each agent's influence based on whether
its calls actually made money.

Non-goals: changing brokers, leaving paper mode by default, or removing the
existing rule-based strategies (they become *inputs*, not the whole decision).

---

## 3. The four agents

Each agent is a small module exposing a uniform interface so the orchestrator can
run them in parallel and score them independently:

```python
# agents/base.py
class AgentVerdict(TypedDict):
    score: float        # -1.0 (strong avoid) .. +1.0 (strong go)
    confidence: float   # 0..1
    reason: str         # human-readable, logged
    features: dict      # raw numbers behind the score (for the feedback loop)

def analyze(symbol, market_ctx, position_ctx) -> AgentVerdict: ...
```

| # | Agent | Job | Promote from | Kind |
|---|-------|-----|--------------|------|
| 1 | **Volume-Sensor** | Is there real participation behind this move? Relative volume regime, volume trend, dry-up vs surge. | `relative_volume` checks scattered in `_generate_signal` (crypto:1030, 1088) | rule + optional LLM |
| 2 | **P&L / Risk-Budget** | Given recent realized results for this symbol+strategy, should we press or pull back? Sizing multiplier + veto in cold streaks. | `_get_trade_performance` (crypto:91), `_get_adaptive_params` (crypto:174) | statistics |
| 3 | **Gatekeeper (orchestrator)** | Fuse agents 1/2/4 + rule signal + risk gates into the final trade / no-trade / size decision. The only agent that can say "yes." | generalize optional `_validate_supervisor` veto (crypto_validator:594) | LLM + rules |
| 4 | **Spike-Monitor (RSI/Volume)** | Detect exhaustion vs ignition: RSI extremes, RSI divergence, volume-spike-with/without-follow-through. | `_rsi_buy_ok/_rsi_sell_ok` (crypto:972), `rel_vol >= 1.5` (crypto:1088) | rule |

Agents 1, 2, 4 are **cheap and deterministic first** (pure indicator math on data
we already fetch). LLM reasoning is an *optional enrichment* layer on top, gated
by the existing per-role model config so it can be turned off per agent without
code changes. This keeps the hot path fast and the fallback (CLAUDE.md rule #3)
free.

---

## 4. Orchestration

The Gatekeeper is a **weighted fusion + veto**, not a chat loop:

```
signal (rule-based, existing) ─┐
volume_sensor.analyze() ───────┤
spike_monitor.analyze() ───────┼─► Gatekeeper.decide()
pnl_agent.analyze() ───────────┤        │
risk_manager gates ────────────┘        ├─ hard vetoes: any risk gate fails      → NO-TRADE
                                        ├─ hard veto:  pnl_agent cold-streak stop → NO-TRADE
                                        ├─ weighted score = Σ wᵢ·scoreᵢ·confᵢ
                                        └─ TRADE if weighted score ≥ θ, size = base·pnl_mult·f(score)
```

- **Two-tier, mirroring today's validator:** cheap deterministic tier runs every
  cycle; the LLM tier (Gatekeeper reasoning + optional per-agent LLM) runs only
  when the deterministic score is near the threshold θ — saves tokens, respects
  `rate_limiter.py`.
- **Weights `wᵢ` and threshold `θ` are the learned parameters** (see §5), stored
  in `bot_config`, admin-overridable — reusing the existing runtime-override
  resolver.
- **Sequential option:** for the highest-conviction path we can run the richer
  sequential pattern from `ai_validator.py` (`_gather_facts → health →
  price_action → supervisor`, where each stage consumes the prior's JSON) instead
  of pure parallel fusion. Start with parallel fusion; graduate specific setups to
  sequential if measurement justifies the latency.

Existing plumbing we reuse (no new infra): parallel exec + thread-local user
context (`crypto_validator._run_with_context`), per-role model selection
(`shared/llm_config.get_model`), usage metering (`rate_limiter`), JSON parsing +
Ollama/OpenRouter fallback (`skills/llm_adapter._call_llm`).

---

## 5. The part that actually addresses "never made money": the feedback loop

Every decision is logged with **each agent's score/confidence/features** at entry
time. When the trade closes, we join the realized P&L back to those features:

```
agent_decisions(trade_id, agent, score, confidence, features_json, entry_ts)
   ⨝ bot_trades(pnl, closed_at)
```

Then, on a schedule (reuse the 30-min `_refresh_adaptive` tick):

1. **Per-agent hit-rate & edge:** for each agent, correlation between its score
   and realized P&L. An agent whose positive scores lose money gets its weight
   `wᵢ` decayed toward 0. An agent that reliably predicts winners gets weight
   raised (bounded, like the existing 0.7–1.0 sizing clamp to avoid overfit).
2. **Threshold calibration:** move θ to the score cutoff that maximized realized
   expectancy over the trailing window.
3. **Kill/keep:** an agent that stays net-negative for N decisions is auto-muted
   (weight 0) and surfaced in the admin panel — same pattern as strategy
   blacklisting today.

This is the closed loop the current bots lack: influence flows to whatever is
**actually making money**, measured, not assumed.

**Validation before trust:** wire the loop through the existing forward-return
validator (`shared/signal_validator.py`) and run in **shadow mode** (decisions
logged, no orders) for a defined period. Promote to live-paper only when measured
expectancy is positive out-of-sample. Consistent with the repo's "measure before
trusting" stance.

---

## 6. Integration points (where code changes land)

```
crypto_bot/agents/               # NEW package (mirror in stock_bot/ via shared base)
  base.py                        # AgentVerdict, analyze() contract
  volume_sensor.py               # agent 1
  pnl_agent.py                   # agent 2  (wraps _get_trade_performance/_get_adaptive_params)
  spike_monitor.py               # agent 4
  gatekeeper.py                  # agent 3 orchestrator (weighted fusion + veto + optional LLM)
crypto_bot/bot_engine.py         # _process_coin: replace direct validate_trade() call with
                                 #   gatekeeper.decide(); log agent_decisions rows
crypto_bot/feedback.py           # NEW: join agent_decisions ⨝ bot_trades, update weights/θ
migrations.py                    # NEW table agent_decisions (both dialects)
shared/llm_config.py             # register new per-agent roles (already the pattern)
features/admin/…                 # surface weights/θ + muted agents (reuse LLM-override UI)
tests/test_agents_*.py           # unit per agent + orchestrator fusion + feedback math
```

`stock_bot/` reuses the same `agents/` via a shared base (the stock validator is
already a thin wrapper over the crypto one — same approach).

---

## 7. Rollout (phased, each independently shippable)

1. **Phase 0 — instrumentation.** Add `agent_decisions` table + logging of the
   *existing* decision's features. No behavior change. Start collecting ground
   truth immediately.
2. **Phase 1 — deterministic agents.** Ship agents 1/2/4 as pure math; Gatekeeper
   runs in **shadow** (logs a decision alongside the live rule path, places no
   orders). Compare shadow vs live P&L.
3. **Phase 2 — feedback loop.** Turn on weight/θ learning against Phase-1 data.
   Still shadow.
4. **Phase 3 — take the wheel (paper).** Gatekeeper becomes the decision-maker on
   BloFin demo / Alpaca paper. Rule signal + risk gates remain as hard inputs/vetoes.
5. **Phase 4 — LLM enrichment.** Add the optional per-agent + Gatekeeper LLM tier,
   gated by threshold proximity and rate limits.

Gate each phase on measured expectancy, not vibes.

## 8. Risks & mitigations

- **Overfitting the weights** → bounded adjustments (0.x clamps like today),
  trailing-window only, out-of-sample validation gate before promotion.
- **Latency / token blowup** → deterministic tier first, LLM only near θ, per-role
  model config + metering already enforce budgets.
- **Breaking the scan loop** (CLAUDE.md rule #4) → agents wrapped in try/except;
  any agent error ⇒ that agent abstains (score 0, confidence 0), loop continues.
- **Silent degradation** → every muted agent and every θ change logged to
  `bot_log` and shown in admin.

## 9. Estimate

~1–2 engineer-weeks for Phases 0–3 (deterministic system + feedback loop; the
infra already exists). Phase 4 (LLM enrichment) is incremental on top. The
expensive part is not code — it is the **shadow-mode measurement window** before
we trust it with paper capital.

---

*Companion to the Earnings Calendar work on `feat-earnings-calendar`. See the
bot-engine analysis in that branch's discussion for the file:line references
cited above.*
