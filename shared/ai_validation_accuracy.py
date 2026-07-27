"""Accuracy measurement + confidence gate for the AI Multi-Model Validation panel.

Replays `ai_validator.validate_setup` over history: at sampled past dates it
reconstructs the analysis the panel would have seen (using ONLY data up to that
bar), captures the verdict + composite_score, then scores whether price actually
moved that way over the next `horizon` trading days (default 10 = ~2 weeks).

The result is a calibration table {composite_score bucket → directional hit-rate,
n}. The live panel is then GATED against it: a bullish verdict is only marked
"actionable" when its score bucket has historically been ≥ target% correct on a
large enough sample; otherwise it reads "low confidence — no clear edge". The
measured hit-rate is shown next to the verdict so it's a number, not a vibe.

Honest by construction: no look-ahead (signal from data[:i], entry next open),
and it grades DIRECTION, so a bull-market base rate can't masquerade as skill —
you see the real per-bucket number.
"""

import json
import logging

import numpy as np

from shared.runtime_config import get_setting, set_setting

logger = logging.getLogger(__name__)

WARMUP = 200
_CALIB_KEY = "ai_validation_calibration"

# Bucketing of composite_score → directional reliability.
_BUCKETS = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
BULLISH = {"STRONG BUY", "BUY"}
BEARISH = {"AVOID"}
# WAIT / NEUTRAL make no directional claim → excluded from the accuracy stat.


def _reconstruct_analysis(ticker, df_slice, info):
    """Rebuild the minimal `analysis` dict validate_setup consumes, from data
    through the last bar of df_slice. News/catalysts can't be reconstructed
    historically, so they're empty (the panel degrades gracefully)."""
    from analysis_engine import (
        calculate_indicators, detect_triangle_pattern,
        detect_breakout_status, generate_trade_plan,
    )
    indicators = calculate_indicators(df_slice)
    pattern = detect_triangle_pattern(df_slice)
    breakout = detect_breakout_status(df_slice, pattern)
    trade_plan = generate_trade_plan(df_slice, pattern, indicators, info)
    price = float(df_slice["Close"].iloc[-1])
    prev = float(df_slice["Close"].iloc[-2]) if len(df_slice) > 1 else price
    change = round(price - prev, 2)
    return {
        "ticker": ticker,
        "info": info,
        "current_price": round(price, 2),
        "change": change,
        "change_pct": round((change / prev) * 100, 2) if prev else 0,
        "pattern": pattern,
        "indicators": indicators,
        "breakout_status": breakout,
        "trade_plan": trade_plan,
        "recent_news": [],
        "active_catalysts": [],
    }


def _bucket(score):
    for lo, hi in _BUCKETS:
        if lo <= score < hi:
            return (lo, hi)
    return None


def run_calibration(tickers, start_date, end_date, horizon=10, step=15,
                    sample_per_ticker=12, fast_mode=True, progress_cb=None):
    """Replay the AI validation panel across a sample and score `horizon`-day
    direction. Returns (and persists) a calibration table. Uses whatever LLM
    provider validate_setup is configured for — set the role overrides to
    `ollama/...` first to run it on Ollama Cloud (≈0 OpenRouter credits)."""
    from analysis_engine import fetch_stock_data, get_stock_info
    from ai_validator import validate_setup

    records = []          # {"score":, "verdict":, "fwd":, "correct":}
    done = 0
    total = len(tickers)
    for ti, ticker in enumerate(tickers):
        if progress_cb:
            progress_cb(ti, total)
        try:
            df = fetch_stock_data(ticker, "2y", "1d")
            info = get_stock_info(ticker)
        except Exception:
            continue
        if df is None or len(df) < WARMUP + horizon + 2:
            continue
        # Evenly sample scoreable bars for this ticker.
        lo, hi = WARMUP, len(df) - horizon - 2
        idxs = list(range(lo, hi, step))
        if sample_per_ticker and len(idxs) > sample_per_ticker:
            pick = np.linspace(0, len(idxs) - 1, sample_per_ticker).astype(int)
            idxs = [idxs[k] for k in sorted(set(pick))]

        for i in idxs:
            try:
                analysis = _reconstruct_analysis(ticker, df.iloc[: i + 1], info)
                res = validate_setup(analysis, fast_mode=fast_mode, force_refresh=True)
                verdict = (res or {}).get("verdict") or {}
                fv = verdict.get("final_verdict")
                score = verdict.get("composite_score")
                if fv not in BULLISH and fv not in BEARISH:
                    continue  # WAIT/NEUTRAL — no directional claim
                if score is None:
                    continue
                entry = float(df["Open"].iloc[i + 1])
                exit_px = float(df["Close"].iloc[i + 1 + horizon])
                fwd = (exit_px - entry) / entry if entry else 0.0
                up = fwd > 0
                correct = up if fv in BULLISH else (not up)
                records.append({"ticker": ticker, "date": str(df.index[i].date()),
                                "verdict": fv, "score": float(score),
                                "fwd_pct": round(fwd * 100, 2), "correct": bool(correct)})
                done += 1
            except Exception as e:
                logger.debug(f"calibration point {ticker}@{i} failed: {e}")
                continue

    table = _aggregate(records, horizon)
    save_calibration(table)
    return table


def _aggregate(records, horizon):
    buckets = []
    for lo, hi in _BUCKETS:
        b = [r for r in records if lo <= r["score"] < hi]
        if b:
            hr = round(sum(r["correct"] for r in b) / len(b) * 100, 1)
            buckets.append({"lo": lo, "hi": hi, "hit_rate": hr, "n": len(b)})
        else:
            buckets.append({"lo": lo, "hi": hi, "hit_rate": None, "n": 0})

    bull = [r for r in records if r["verdict"] in BULLISH]
    bear = [r for r in records if r["verdict"] in BEARISH]
    overall = {
        "bullish": {"hit_rate": round(sum(r["correct"] for r in bull) / len(bull) * 100, 1) if bull else None,
                    "n": len(bull)},
        "bearish": {"hit_rate": round(sum(r["correct"] for r in bear) / len(bear) * 100, 1) if bear else None,
                    "n": len(bear)},
    }
    return {
        "horizon": horizon, "target": 75, "min_sample": 20,
        "sample_size": len(records), "buckets": buckets, "overall": overall,
    }


def save_calibration(table):
    try:
        set_setting(_CALIB_KEY, json.dumps(table))
    except Exception as e:
        logger.warning(f"could not persist AI-validation calibration: {e}")


def load_calibration():
    raw = get_setting(_CALIB_KEY, "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def annotate_verdict(verdict):
    """Given a live verdict dict, attach the measured accuracy + a gate.

    Returns a dict merged into the verdict as `accuracy`:
      measured: was a calibration table available for this bucket?
      hit_rate / n: the historical directional accuracy of this score bucket
      actionable: bullish verdict whose bucket cleared target% on enough samples
      target / horizon: the gate's parameters
    """
    table = load_calibration()
    fv = (verdict or {}).get("final_verdict")
    score = (verdict or {}).get("composite_score")
    if not table:
        return {"measured": False, "actionable": None,
                "note": "Accuracy not yet measured — run the calibration."}

    target = table.get("target", 75)
    horizon = table.get("horizon", 10)
    min_sample = table.get("min_sample", 20)
    directional = fv in BULLISH or fv in BEARISH

    hit_rate, n = None, 0
    if score is not None:
        b = _bucket(float(score))
        if b:
            for bk in table.get("buckets", []):
                if bk["lo"] == b[0] and bk["hi"] == b[1]:
                    hit_rate, n = bk.get("hit_rate"), bk.get("n", 0)
                    break

    actionable = bool(
        directional and hit_rate is not None and n >= min_sample and hit_rate >= target
    )
    return {
        "measured": True,
        "hit_rate": hit_rate,
        "n": n,
        "actionable": actionable,
        "directional": directional,
        "target": target,
        "horizon": horizon,
        "overall": table.get("overall"),
        "sample_size": table.get("sample_size"),
    }
