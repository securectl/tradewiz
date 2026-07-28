# 13 · AI Validation

TradeWiz runs a **multi-LLM consensus pipeline** so no single model's opinion decides a
verdict. You'll meet it in two places in the [Analyzer](04-analyzer.md): **Validate with
AI** (setup validation) and **Run 12-Month Prediction** (investment thesis).

> Both consume your daily AI quota and require an OpenRouter key to be configured. They use
> several models in parallel and reconcile their views.

---

## Setup validation (intraday)

**What you get:** a quick risk-screened verdict for a trade setup —
**STRONG BUY · BUY · WAIT · AVOID** — with confidence.

**How it decides (3-model consensus, cached ~15 min):**
- A **research/fact** model assesses facts and risk.
- A **pattern** model validates the chart setup.
- A **prediction** model estimates price targets and risk.

**Verdict logic (simplified):**
- **STRONG BUY** — strong average score and multiple models bullish.
- **BUY** — solid score, at least one bullish, no warnings.
- **AVOID** — a risk gate trips (extreme risk score, high false-breakout probability,
  invalid pattern, or several red flags), or models agree it's weak.
- **WAIT** — the neutral default.

An optional **supervisor** model can act as a final veto when configured.

---

## 12-month investment prediction

**What you get:** a longer-horizon thesis — **INVEST · HOLD · PASS** — with **min / fair /
max** price targets and a **survival-probability** estimate.

**How it decides (4-model, 3-of-4 quorum, cached ~1 hour):**
1. A **fact gatherer** collects fundamentals + technicals.
2. A **company-health** model judges moat, dilution risk, and survival odds.
3. A **price-action** model judges valuation and upside/downside.
4. A **supervisor** breaks ties only when the first three disagree (skipped when they
   already agree, to save cost).

**Verdict logic (simplified):**
- **INVEST** — quorum of models invest, healthy average score, survival probability high.
- **HOLD** — partial agreement or consensus not reached.
- **PASS** — models lean pass, weak score, low survival odds, or high dilution risk.

---

## Models used

Models are configurable (admins can swap them at runtime — see
[Admin Guide](16-admin-guide.md)). Typical assignments:

| Role | Typical model |
|------|---------------|
| Research / fact | Claude Sonnet (or Gemini Flash in **Fast Mode**) |
| Pattern | Gemini Pro |
| Prediction / price action | DeepSeek |
| Supervisor (optional) | Claude Opus |
| Screener vetting | Gemini Flash |
| Bot sentiment / risk | Gemini Flash / DeepSeek |

> **Fast Mode** (header toggle) swaps in faster, cheaper models for research and health
> checks — handy when you're conserving quota.

---

## Reliability principles

- **Graceful fallback:** every AI path has a non-AI fallback, so a model outage degrades
  features rather than breaking them.
- **Caching:** identical requests reuse recent results (15 min for setups, 1 hour for
  12-month) to keep responses fast and quota-friendly. Force a refresh where the UI offers
  it.
- **Quota-aware:** if you've hit your daily AI limit, validation pauses until the rolling
  window advances. Check your usage in the header gauge or via `/billing/status`.
