"""
Signal validator — historical forward-return backtest for the app's *display*
signals (starting with the analyzer's BUY/HOLD/SELL recommendation).

The strategy backtester (shared/backtest_engine.py) measures tradable detector
functions. This module answers a different, blunter question: **when the
analyzer says BUY (or HOLD, or SELL), what does the price actually do next —
and is that better than entering on a random day?**

Method (no look-ahead):
  • At each historical bar i (with >= WARMUP bars of history), reconstruct the
    recommendation using ONLY data through bar i — indicators, breakout, and a
    regime-derived market stance.
  • "Enter" at the NEXT bar's open (i+1) and measure the forward return to the
    close at i+1+h for each horizon h, net of round-trip friction.
  • Aggregate by action and by market regime, and compare every action's
    average forward return against the baseline (the average forward return
    across *all* bars — i.e. what a coin-flip entry earns over the same window).

A signal has edge only if BUY clearly beats the baseline, SELL clearly trails
it, and average forward return rises monotonically from SELL → BUY. If not,
the signal is noise and should not be trusted with money — that verdict is the
whole point of this tool.
"""

import logging

import numpy as np
import pandas as pd

from shared.backtest_engine import FrictionModel, build_regime_series, _download_universe

logger = logging.getLogger(__name__)

# Bars of history required before a signal is considered valid. The 200-day SMA
# is the longest lookback generate_recommendation() uses.
WARMUP = 200

# Ordering used for the monotonicity check — most bearish → most bullish.
ACTION_ORDER = ["SELL", "REDUCE", "HOLD", "ACCUMULATE", "BUY"]
BULLISH = {"BUY", "ACCUMULATE"}
BEARISH = {"SELL", "REDUCE"}


def _norm(df):
    """Flatten a possibly MultiIndex-columned yfinance frame to plain OHLCV
    columns. yf.download returns 2-level columns (ticker, field) for a
    single-ticker batch; keep the field level."""
    if df is None:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(-1)
    return df


def _market_from_regime(regime):
    """Map a SPY regime label to the market-gauge stance/score that
    generate_recommendation() consumes, so the historical reconstruction keeps
    the macro overlay the live analyzer applies. (Historical Fear&Greed isn't
    available; SPY-structure regime is the honest stand-in.)"""
    if regime == "bull":
        return {"available": True, "stance": "BUY", "score": 30}
    if regime == "down":
        return {"available": True, "stance": "SELL", "score": -30}
    if regime in ("chop",):
        return {"available": True, "stance": "HOLD", "score": 0}
    return None  # unknown → no overlay


def _recommendation_at(df_slice, market):
    """Reconstruct the analyzer verdict from data through the last bar of the
    slice. Returns (action, score). Never raises — returns (None, None) on
    failure so one bad bar can't sink a run."""
    from analysis_engine import (
        calculate_indicators, detect_triangle_pattern,
        detect_breakout_status, generate_recommendation,
    )
    try:
        ind = calculate_indicators(df_slice)
        pattern = detect_triangle_pattern(df_slice)
        bs = detect_breakout_status(df_slice, pattern)
        price = float(df_slice["Close"].iloc[-1])
        rec = generate_recommendation(ind, bs, price, market)
        return rec.get("action"), rec.get("score")
    except Exception:
        return None, None


def _pct(values):
    return round(float(np.mean(values)) * 100, 3) if len(values) else None


def _agg(records, horizons):
    """Aggregate a list of records into per-horizon stats.
    Each record is {"score":.., "fwd": {h: ret_fraction}}."""
    out = {"n": len(records)}
    for h in horizons:
        rets = [r["fwd"][h] for r in records if r["fwd"].get(h) is not None]
        if not rets:
            out[h] = {"avg_pct": None, "median_pct": None, "hit_rate_pct": None, "n": 0}
            continue
        arr = np.array(rets, dtype=float)
        out[h] = {
            "avg_pct": round(float(arr.mean()) * 100, 3),
            "median_pct": round(float(np.median(arr)) * 100, 3),
            "hit_rate_pct": round(float((arr > 0).mean()) * 100, 1),
            "n": len(rets),
        }
    return out


def _pearson(xs, ys):
    if len(xs) < 3:
        return None
    x = np.array(xs, dtype=float)
    y = np.array(ys, dtype=float)
    if x.std() < 1e-12 or y.std() < 1e-12:
        return None
    return round(float(np.corrcoef(x, y)[0, 1]), 4)


def validate_recommendation_signal(
    universe,
    start_date,
    end_date,
    horizons=(5, 10, 20),
    friction=None,
    step=1,
    use_regime=True,
    progress_cb=None,
):
    """Backtest the analyzer recommendation across a universe.

    Args:
        universe: list of tickers.
        start_date / end_date: 'YYYY-MM-DD'. Signals are only scored on bars at
            or after start_date; extra history before it is fetched for warmup.
        horizons: forward-return windows in trading days.
        friction: FrictionModel (default retail equities). Applied round-trip.
        step: evaluate every Nth bar (1 = every bar). Higher = faster, coarser.
        use_regime: fold the SPY-regime market stance into the reconstruction.
        progress_cb: optional callable(done, total).

    Returns a report dict (see module docstring / build-out below)."""
    horizons = list(horizons)
    max_h = max(horizons)
    friction = friction or FrictionModel()

    # Fetch with ~400 calendar days of warmup so the 200d SMA exists at start.
    fetch_start = (pd.Timestamp(start_date) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    data = {t: _norm(v) for t, v in _download_universe(list(universe), fetch_start, end_date).items()}

    # SPY regime series (aligned by date) for the macro overlay + attribution.
    regime_series = None
    if use_regime:
        spy = _norm(_download_universe(["SPY"], fetch_start, end_date).get("SPY"))
        if spy is not None and len(spy) > 200:
            regime_series = build_regime_series(spy)

    start_ts = pd.Timestamp(start_date)
    records = []              # every scored bar
    by_action = {a: [] for a in ACTION_ORDER}
    by_regime = {}           # regime -> list of records
    skipped = 0

    tickers = [t for t in universe if t in data]
    total = len(tickers)
    for ti, ticker in enumerate(tickers):
        df = data[ticker]
        if progress_cb:
            progress_cb(ti, total)
        n = len(df)
        # Need WARMUP history behind i and max_h bars ahead of the entry (i+1).
        for i in range(WARMUP, n - max_h - 1, step):
            bar_date = df.index[i]
            if bar_date < start_ts:
                continue
            regime = None
            if regime_series is not None:
                try:
                    regime = regime_series.asof(bar_date)
                except Exception:
                    regime = None
            market = _market_from_regime(regime) if use_regime else None

            action, score = _recommendation_at(df.iloc[: i + 1], market)
            if action is None:
                skipped += 1
                continue

            entry_raw = float(df["Open"].iloc[i + 1])
            entry = friction.fill_buy(entry_raw)
            fwd = {}
            for h in horizons:
                j = i + 1 + h
                if j >= n:
                    fwd[h] = None
                    continue
                exit_raw = float(df["Close"].iloc[j])
                exit_px = friction.fill_sell(exit_raw)
                fwd[h] = (exit_px - entry) / entry if entry else None

            rec = {"ticker": ticker, "date": str(bar_date.date()),
                   "action": action, "score": score, "regime": regime, "fwd": fwd}
            records.append(rec)
            by_action.setdefault(action, []).append(rec)
            if regime:
                by_regime.setdefault(regime, []).append(rec)

    if progress_cb:
        progress_cb(total, total)

    # ── Aggregate ─────────────────────────────────────────────────
    baseline = _agg(records, horizons)
    action_stats = {a: _agg(recs, horizons) for a, recs in by_action.items() if recs}
    regime_stats = {}
    for rg, recs in by_regime.items():
        regime_stats[rg] = {
            "baseline": _agg(recs, horizons),
            "by_action": {a: _agg([r for r in recs if r["action"] == a], horizons)
                          for a in ACTION_ORDER if any(r["action"] == a for r in recs)},
        }

    # Score↔forward-return correlation per horizon (single-number predictiveness).
    score_corr = {}
    for h in horizons:
        xs = [r["score"] for r in records if r["fwd"].get(h) is not None]
        ys = [r["fwd"][h] for r in records if r["fwd"].get(h) is not None]
        score_corr[h] = _pearson(xs, ys)

    # ── Verdict (honest, heuristic) ───────────────────────────────
    verdict = _build_verdict(action_stats, baseline, score_corr, horizons)

    report = {
        "signal": "analyzer_recommendation",
        "universe": tickers,
        "universe_size": len(tickers),
        "start_date": start_date,
        "end_date": end_date,
        "horizons": horizons,
        "friction_roundtrip_bps": round(friction.per_side_bps * 2, 1),
        "sample_size": len(records),
        "skipped_bars": skipped,
        "step": step,
        "baseline": baseline,
        "by_action": action_stats,
        "by_regime": regime_stats,
        "score_corr": score_corr,
        "verdict": verdict,
    }
    report["summary_lines"] = _summary_lines(report)
    return report


def _build_verdict(action_stats, baseline, score_corr, horizons):
    """Turn the aggregates into a blunt edge/weak/none call at the mid horizon."""
    h = horizons[len(horizons) // 2]
    base = (baseline.get(h) or {}).get("avg_pct")
    buy = ((action_stats.get("BUY") or {}).get(h) or {}).get("avg_pct")
    sell = ((action_stats.get("SELL") or {}).get(h) or {}).get("avg_pct")

    buy_edge = round(buy - base, 3) if (buy is not None and base is not None) else None
    sell_edge = round(base - sell, 3) if (sell is not None and base is not None) else None
    spread = round(buy - sell, 3) if (buy is not None and sell is not None) else None

    # Monotonic: does avg forward return rise SELL→REDUCE→HOLD→ACCUMULATE→BUY?
    ladder = []
    for a in ACTION_ORDER:
        v = ((action_stats.get(a) or {}).get(h) or {}).get("avg_pct")
        if v is not None:
            ladder.append(v)
    monotonic = len(ladder) >= 3 and all(ladder[k] <= ladder[k + 1] + 0.25 for k in range(len(ladder) - 1))

    corr = score_corr.get(h)
    assessment, reason = "none", "BUY does not beat a random-day entry — treat as noise."
    if spread is not None and spread > 1.5 and (corr or 0) > 0.05 and (buy_edge or 0) > 0.3:
        assessment, reason = "edge", "BUY beats baseline and outperforms SELL with a positive score correlation."
    elif spread is not None and spread > 0.5 and (buy_edge or 0) > 0:
        assessment, reason = "weak", "Slight directional tilt, but thin vs costs — not reliable on its own."

    return {
        "horizon": h,
        "assessment": assessment,
        "reason": reason,
        "buy_edge_vs_baseline_pct": buy_edge,
        "sell_edge_vs_baseline_pct": sell_edge,
        "buy_minus_sell_pct": spread,
        "monotonic": monotonic,
        "score_corr": corr,
    }


def _summary_lines(report):
    h = report["verdict"]["horizon"]
    lines = [
        f"Signal: analyzer recommendation | {report['universe_size']} tickers | "
        f"{report['start_date']}→{report['end_date']}",
        f"Sample: {report['sample_size']} scored bars | "
        f"friction {report['friction_roundtrip_bps']:.0f} bps round-trip | horizon {h}d",
    ]
    base = (report["baseline"].get(h) or {}).get("avg_pct")
    lines.append(f"Baseline (random-day entry) avg {h}d fwd return: {base:+.2f}%"
                 if base is not None else "Baseline: n/a")
    for a in ACTION_ORDER:
        st = report["by_action"].get(a)
        if not st:
            continue
        s = st.get(h) or {}
        if s.get("avg_pct") is None:
            continue
        lines.append(f"  {a:<11} n={s['n']:<5} avg {s['avg_pct']:+.2f}%  "
                     f"hit {s['hit_rate_pct']:.0f}%")
    v = report["verdict"]
    corr_txt = f"{v['score_corr']:+.3f}" if v.get("score_corr") is not None else "n/a"
    lines.append(f"Score↔return corr: {corr_txt} | BUY−SELL spread: "
                 f"{v['buy_minus_sell_pct']:+.2f}%" if v.get("buy_minus_sell_pct") is not None
                 else "Score↔return corr: n/a")
    lines.append(f"VERDICT: {v['assessment'].upper()} — {v['reason']}")
    return lines
