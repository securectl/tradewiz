"""LLM A/B comparison harness.

Replays representative bot-validator prompts through two models (current vs
candidate) and reports agreement, JSON fidelity, and — when the prompt is from
a closed bot trade — what each model would have predicted vs what actually
happened.

Usage (inside the app container):
    docker compose exec app python tools/llm_ab_compare.py \\
        --role bot_sentiment \\
        --baseline google/gemini-2.5-flash \\
        --candidate deepseek/deepseek-chat-v3.1 \\
        --n 50

Roles supported (from shared.llm_config):
  bot_sentiment, bot_risk, watchdog_gating, claude_gating, screener,
  research, research_fast, pattern, prediction

The harness uses real historical prompts derived from bot_trades + screener_results
so the comparison is on YOUR distribution, not benchmarks.

Output: JSON report at tools/llm_ab_results_<timestamp>.json plus a console summary.

Cost note: each run sends ~N prompts to BOTH models. At N=50 and gemini-flash
+ deepseek that's roughly $0.05-$0.30. Use --dry-run to preview without
calling the API.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import IS_POSTGRES, query, query_one  # noqa: E402

P = "%s" if IS_POSTGRES else "?"


# ─── Prompt builders for each role ─────────────────────────────

def _build_bot_validator_prompt(trade):
    """Replay a bot validator prompt for a closed bot trade."""
    coin = trade.get("coin", "?")
    side = trade.get("side", "buy")
    entry = float(trade.get("entry_price") or 0)
    strategy = trade.get("strategy", "?")
    reason = (trade.get("signal_reason") or "")[:200]
    return [
        {"role": "system", "content": "You are a critical trade analyst. Approve or reject this trade. Respond JSON only."},
        {"role": "user", "content": (
            f"Trade signal:\nCoin/Ticker: {coin}\nSide: {side}\nEntry: ${entry:.2f}\n"
            f"Strategy: {strategy}\nReasoning: {reason}\n\n"
            f'Respond ONLY: {{"execute": true/false, "confidence": 0.0-1.0, "reasoning": "1 sentence"}}'
        )},
    ]


def _build_screener_prompt(row):
    ticker = row.get("ticker", "?")
    cat = row.get("category", "?")
    summary = (row.get("summary") or "")[:200]
    return [
        {"role": "system", "content": "You are an equity research screener. Categorize this candidate. Respond JSON only."},
        {"role": "user", "content": (
            f"Ticker: {ticker}\nCategory: {cat}\nSummary: {summary}\n\n"
            f'Respond ONLY: {{"verdict": "OPPORTUNITY|RISKY|AVOID", "confidence": 0-100, '
            f'"reasoning": "1 sentence"}}'
        )},
    ]


# ─── Sample fetchers ─────────────────────────────────────────

def _fetch_bot_validator_samples(n):
    """Closed bot trades — these have ground truth (pnl)."""
    rows = query(
        f"SELECT coin, side, entry_price, strategy, signal_reason, pnl, pnl_pct "
        f"FROM bot_trades WHERE status = 'closed' AND signal_reason IS NOT NULL "
        f"ORDER BY closed_at DESC LIMIT {int(n)}"
    )
    return [{"prompt": _build_bot_validator_prompt(r), "ground_truth": r} for r in (rows or [])]


def _fetch_screener_samples(n):
    rows = query(
        f"SELECT ticker, category, verdict, summary, scan_date "
        f"FROM screener_results ORDER BY scan_date DESC LIMIT {int(n)}"
    )
    return [{"prompt": _build_screener_prompt(r), "ground_truth": r} for r in (rows or [])]


SAMPLE_FETCHERS = {
    "bot_sentiment": _fetch_bot_validator_samples,
    "bot_risk":      _fetch_bot_validator_samples,
    "claude_gating": _fetch_bot_validator_samples,
    "watchdog_gating": _fetch_bot_validator_samples,
    "screener":      _fetch_screener_samples,
}


# ─── LLM caller ──────────────────────────────────────────────

def _call_model(model, messages, timeout=45):
    """Direct OpenRouter call — bypasses get_model so we can pin both A and B
    independently of the live DB override."""
    import requests
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY missing", "raw": ""}
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages,
                  "max_tokens": 400, "temperature": 0.1},
            timeout=timeout,
        )
        resp.raise_for_status()
        return {"raw": resp.json()["choices"][0]["message"]["content"], "error": None}
    except Exception as e:
        return {"error": str(e), "raw": ""}


def _parse_json(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        lines = [l for l in text.split("\n") if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}") + 1
        if s >= 0 and e > s:
            try:
                return json.loads(text[s:e])
            except json.JSONDecodeError:
                pass
    return None


# ─── Scoring ─────────────────────────────────────────────────

def _score_pair(role, baseline_resp, candidate_resp, ground_truth):
    """Return per-sample dict with agreement + accuracy if ground truth available."""
    bp = _parse_json(baseline_resp.get("raw"))
    cp = _parse_json(candidate_resp.get("raw"))
    json_ok_baseline = bp is not None
    json_ok_candidate = cp is not None

    def _decision(parsed):
        if not parsed:
            return None
        # Bot validators
        if "execute" in parsed:
            return bool(parsed.get("execute"))
        # Screener
        v = (parsed.get("verdict") or "").upper()
        if v in ("OPPORTUNITY", "MOMENTUM BUY", "RECOVERY BUY"):
            return True
        if v in ("AVOID", "FALLING KNIFE", "RISKY"):
            return False
        return None

    b_dec = _decision(bp)
    c_dec = _decision(cp)
    agree = b_dec is not None and c_dec is not None and b_dec == c_dec

    # P&L scoring — only meaningful for bot_validator-type roles (closed trades
    # with realized pnl). For screener samples we don't have a 7-day-forward
    # outcome wired up here, so we skip P&L scoring for screener.
    pnl = None
    pnl_winner = None
    if ground_truth and ground_truth.get("pnl") is not None:
        pnl = float(ground_truth["pnl"])
        actual_was_winner = pnl > 0
        # Who would have made money? The one whose decision matched the actual
        # outcome — approve a winner OR reject a loser.
        if b_dec is not None and b_dec == actual_was_winner:
            pnl_winner = "baseline"
        if c_dec is not None and c_dec == actual_was_winner:
            pnl_winner = "candidate" if pnl_winner is None else "both"
        if pnl_winner is None:
            pnl_winner = "neither"

    return {
        "agree": agree,
        "baseline_decision": b_dec,
        "candidate_decision": c_dec,
        "baseline_json_ok": json_ok_baseline,
        "candidate_json_ok": json_ok_candidate,
        "baseline_error": baseline_resp.get("error"),
        "candidate_error": candidate_resp.get("error"),
        "ground_truth_pnl": pnl,
        "pnl_winner": pnl_winner,
    }


# ─── Main ────────────────────────────────────────────────────

def run(role, baseline, candidate, n, dry_run=False):
    fetcher = SAMPLE_FETCHERS.get(role)
    if not fetcher:
        print(f"[error] No sample fetcher for role={role}. Available: {list(SAMPLE_FETCHERS)}")
        return None

    samples = fetcher(n)
    if not samples:
        print(f"[error] No historical samples found for role={role}. "
              f"Need closed bot_trades / screener_results.")
        return None

    print(f"[info] role={role} samples={len(samples)} baseline={baseline} candidate={candidate}")
    if dry_run:
        print("[dry-run] Skipping API calls. Sample prompt:")
        print(json.dumps(samples[0]["prompt"], indent=2)[:500])
        return None

    results = []
    for i, s in enumerate(samples, 1):
        print(f"  [{i}/{len(samples)}] ", end="", flush=True)
        b = _call_model(baseline, s["prompt"])
        c = _call_model(candidate, s["prompt"])
        score = _score_pair(role, b, c, s["ground_truth"])
        results.append({
            "ground_truth": {k: str(v)[:100] for k, v in (s["ground_truth"] or {}).items()},
            "score": score,
            "baseline_raw": (b.get("raw") or "")[:300],
            "candidate_raw": (c.get("raw") or "")[:300],
        })
        marker = "✓" if score["agree"] else "✗"
        print(f"{marker} b={score['baseline_decision']} c={score['candidate_decision']}")

    # ── Summary ──
    n = len(results)
    agree = sum(1 for r in results if r["score"]["agree"])
    b_json = sum(1 for r in results if r["score"]["baseline_json_ok"])
    c_json = sum(1 for r in results if r["score"]["candidate_json_ok"])
    pnl_baseline_wins = sum(1 for r in results if r["score"]["pnl_winner"] in ("baseline", "both"))
    pnl_candidate_wins = sum(1 for r in results if r["score"]["pnl_winner"] in ("candidate", "both"))
    pnl_samples = sum(1 for r in results if r["score"]["ground_truth_pnl"] is not None)

    summary = {
        "role": role,
        "baseline": baseline,
        "candidate": candidate,
        "samples": n,
        "agreement_rate": round(agree / n * 100, 1) if n else 0,
        "baseline_json_fidelity": round(b_json / n * 100, 1) if n else 0,
        "candidate_json_fidelity": round(c_json / n * 100, 1) if n else 0,
        "pnl_evaluable_samples": pnl_samples,
        "pnl_baseline_correct_pct": round(pnl_baseline_wins / pnl_samples * 100, 1) if pnl_samples else None,
        "pnl_candidate_correct_pct": round(pnl_candidate_wins / pnl_samples * 100, 1) if pnl_samples else None,
        "timestamp": datetime.utcnow().isoformat(),
    }

    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    out_path = f"tools/llm_ab_results_{role}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), out_path)
    with open(full_path, "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2)
    print(f"\nFull report: {out_path}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", required=True, choices=list(SAMPLE_FETCHERS),
                    help="Which role to test")
    ap.add_argument("--baseline", required=True, help="Current model (e.g. google/gemini-2.5-flash)")
    ap.add_argument("--candidate", required=True, help="Cheaper/free candidate model")
    ap.add_argument("--n", type=int, default=30, help="Number of historical samples (default 30)")
    ap.add_argument("--dry-run", action="store_true", help="Print sample prompt and exit (no API calls)")
    args = ap.parse_args()
    run(args.role, args.baseline, args.candidate, args.n, dry_run=args.dry_run)
