"""
Trump → Market Forecaster

Forward-looking ML forecaster that predicts stock (SPY, QQQ) and crypto (BTC, ETH)
moves based on Trump Mood Index (from trump_mood.py).

Architecture (ensemble, self-learning):

  Feature vector (built from trump_mood_history + yfinance prices):
    - mood_today, mood_yesterday, mood_avg_3d
    - mood_trend (+1 improving / 0 stable / -1 deteriorating)
    - mood_acceleration, posts_analyzed, src_truth, src_news
    - asset_return_1d (autoregressive), asset_return_5d, asset_vol_20d

  Models (all pure-numpy, no sklearn/torch dependency):
    1. Ridge Regression     — linear baseline with L2 regularization
    2. K-Nearest Neighbors  — captures non-linearity via historical analogs
    3. Exponential Smoothing — Holt trend baseline (anchors to drift)
    4. LLM (Claude Opus)    — qualitative reasoning on rhetoric → sector impact

  Ensemble:
    Weighted average with weights stored in DB. Self-correcting:
    every run, evaluate predictions whose horizons have elapsed, compute
    MAE per model, re-weight inversely (w_i ∝ 1 / (MAE_i + ε)).

  Horizons: 1D (tomorrow), 5D (week), 21D (month).

  Correlation timeline: Pearson(mood_t-k, return_t) for k ∈ [0..7] — shows
  lead/lag relationship between Trump mood and each asset.

  Erratic-behavior model (two-scenario probabilistic forecast):
    Trump's mood is non-stationary and reverses frequently. Each horizon's
    forecast is a probability-weighted blend of two scenarios:
      - PERSIST: current mood carries forward (baseline ensemble output)
      - FLIP:    mood reverses sign — re-run models with inverted mood features
    The reversal probability P(flip) is driven by:
      (a) mood volatility  — stdev of daily mood over last 14d
      (b) backtrack rate   — recent policy reversals per day (from trump_backtracks)
      (c) mean-reversion pressure — how far current mood is from its long-run mean
      (d) horizon length   — longer horizons have higher cumulative flip risk
    P(flip) also reduces confidence (wider probability band).

All data is cached; LLM calls are rate-limited to once per 6h globally.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta

import numpy as np

logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────
ASSETS = {
    "SPY": {"label": "S&P 500", "class": "stock", "yf": "SPY"},
    "QQQ": {"label": "Nasdaq 100", "class": "stock", "yf": "QQQ"},
    "BTC": {"label": "Bitcoin", "class": "crypto", "yf": "BTC-USD"},
    "ETH": {"label": "Ethereum", "class": "crypto", "yf": "ETH-USD"},
}

HORIZONS = {
    "1D": {"label": "Tomorrow", "days": 1},
    "5D": {"label": "This Week", "days": 5},
    "21D": {"label": "This Month", "days": 21},
}

MODELS = ["ridge", "knn", "ewma", "llm"]
DEFAULT_WEIGHTS = {"ridge": 0.30, "knn": 0.30, "ewma": 0.15, "llm": 0.25}

FORECAST_TTL = 6 * 3600       # 6 hours — forecast cache
LLM_FORECAST_TTL = 6 * 3600   # 6 hours — LLM reasoning cache
CORR_TTL = 3600               # 1 hour — correlation cache
MIN_TRAIN_ROWS = 8            # fallback to heuristic below this

_cache = {}


def _get_cached(key, ttl):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < ttl:
            return data
    return None


def _set_cached(key, data):
    _cache[key] = (data, time.time())


# ─── Data Assembly ───────────────────────────────────────────

def _load_mood_daily(days=90):
    """Load Trump mood history, aggregated to daily averages.

    Returns list of dicts sorted oldest→newest:
      [{date, mood, mood_trend, mood_accel, posts, src_truth, src_news}]
    """
    try:
        from trump_mood import get_mood_history
        rows = get_mood_history(days)
    except Exception as e:
        logger.warning(f"Failed to load mood history: {e}")
        return []

    if not rows:
        return []

    # Group by date
    by_date = {}
    for r in rows:
        ca = r.get("created_at")
        if not ca:
            continue
        try:
            dt = datetime.fromisoformat(str(ca).replace("Z", "").split(".")[0])
        except Exception:
            continue
        d = dt.date().isoformat()
        bucket = by_date.setdefault(d, {"mood": [], "trend": [], "accel": [],
                                        "posts": 0, "src_truth": 0, "src_news": 0})
        try:
            bucket["mood"].append(float(r.get("mood", 0) or 0))
        except Exception:
            pass
        t = r.get("pattern_trend") or "stable"
        bucket["trend"].append({"improving": 1, "deteriorating": -1}.get(t, 0))
        try:
            bucket["accel"].append(float(r.get("pattern_acceleration", 0) or 0))
        except Exception:
            pass
        bucket["posts"] += int(r.get("posts_analyzed", 0) or 0)
        bucket["src_truth"] += int(r.get("src_truth", 0) or 0)
        bucket["src_news"] += int(r.get("src_news", 0) or 0)

    out = []
    for d, b in sorted(by_date.items()):
        if not b["mood"]:
            continue
        out.append({
            "date": d,
            "mood": float(np.mean(b["mood"])),
            "mood_trend": float(np.mean(b["trend"])) if b["trend"] else 0.0,
            "mood_accel": float(np.mean(b["accel"])) if b["accel"] else 0.0,
            "posts": b["posts"],
            "src_truth": b["src_truth"],
            "src_news": b["src_news"],
        })
    return out


def _load_prices(ticker, days=120):
    """Load daily OHLC from yfinance as list of (date_str, close)."""
    try:
        import yfinance as yf
        period = f"{max(days + 30, 90)}d"
        df = yf.download(ticker, period=period, interval="1d", progress=False, timeout=15)
        if df is None or len(df) == 0:
            return []
        closes = df["Close"].values.flatten()
        dates = [d.date().isoformat() for d in df.index.to_pydatetime()]
        return list(zip(dates, [float(c) for c in closes]))
    except Exception as e:
        logger.warning(f"yfinance failed for {ticker}: {e}")
        return []


def _build_dataset(asset_key, mood_daily, prices):
    """Align mood + prices by date and build feature/target matrix.

    For each trading day t with mood data, build features from mood at t and
    past prices, and targets = forward returns (1D, 5D, 21D).

    Returns dict:
      X (n × 9) features, y_1d, y_5d, y_21d arrays, dates (n,).
    """
    if not mood_daily or not prices:
        return None

    price_map = dict(prices)
    price_dates = [p[0] for p in prices]
    date_to_idx = {d: i for i, d in enumerate(price_dates)}
    close_arr = np.array([p[1] for p in prices], dtype=float)

    feats, y1, y5, y21, used_dates = [], [], [], [], []

    for i, m in enumerate(mood_daily):
        d = m["date"]
        # Find price idx on/before this date
        idx = date_to_idx.get(d)
        if idx is None:
            # Find most recent trading day ≤ d
            candidates = [j for j, pd in enumerate(price_dates) if pd <= d]
            if not candidates:
                continue
            idx = candidates[-1]

        if idx < 20 or idx >= len(close_arr) - 1:
            continue

        # Mood context (today, yesterday, 3d avg)
        mood_today = m["mood"]
        mood_yest = mood_daily[i - 1]["mood"] if i > 0 else mood_today
        mood_3d = float(np.mean([x["mood"] for x in mood_daily[max(0, i - 2):i + 1]]))

        # Asset context at t (past 20d)
        ret_1d_lag = float((close_arr[idx] / close_arr[idx - 1]) - 1) * 100
        ret_5d_lag = float((close_arr[idx] / close_arr[idx - 5]) - 1) * 100
        vol_20d = float(np.std(np.diff(close_arr[idx - 19:idx + 1]) / close_arr[idx - 19:idx])) * 100

        # Forward targets
        def fwd(horizon):
            j = idx + horizon
            if j >= len(close_arr):
                return None
            return float((close_arr[j] / close_arr[idx]) - 1) * 100

        r1 = fwd(1); r5 = fwd(5); r21 = fwd(21)
        if r1 is None:
            continue

        feats.append([
            mood_today,
            mood_yest,
            mood_3d,
            m["mood_trend"],
            m["mood_accel"],
            min(m["posts"], 100) / 100.0,  # normalized
            ret_1d_lag,
            ret_5d_lag,
            vol_20d,
        ])
        y1.append(r1)
        y5.append(r5 if r5 is not None else r1 * 5)  # rough proxy if incomplete
        y21.append(r21 if r21 is not None else r5 * 4 if r5 is not None else r1 * 21)
        used_dates.append(d)

    if not feats:
        return None

    return {
        "X": np.array(feats, dtype=float),
        "y1": np.array(y1, dtype=float),
        "y5": np.array(y5, dtype=float),
        "y21": np.array(y21, dtype=float),
        "dates": used_dates,
        "last_close": float(close_arr[-1]),
    }


# ─── Erratic Behavior Modeling ───────────────────────────────

def _compute_erratic_metrics(mood_daily):
    """Quantify Trump's mood erraticism — drives reversal probability.

    Returns:
      {
        mood_volatility:  stdev of mood over last 14 days (0-100 scale)
        mood_vol_30d:     stdev over last 30d
        swing_count_14d:  # of sign flips in last 14 days
        backtrack_rate:   reversals per day over last 30d (from trump_backtracks)
        mean_reversion_pressure: |current - 30d_avg| / stdev (z-score)
        persistence_score: 0-1 (1=very persistent, 0=highly erratic)
        current_mood:     latest daily mood
        baseline_mood:    30d rolling mean (what it reverts toward)
      }
    """
    metrics = {
        "mood_volatility": 25.0,       # prior: moderate
        "mood_vol_30d": 25.0,
        "swing_count_14d": 0,
        "backtrack_rate": 0.0,
        "mean_reversion_pressure": 0.0,
        "persistence_score": 0.5,
        "current_mood": 0.0,
        "baseline_mood": 0.0,
    }

    if mood_daily:
        moods = np.array([m["mood"] for m in mood_daily], dtype=float)
        last14 = moods[-14:] if len(moods) >= 14 else moods
        last30 = moods[-30:] if len(moods) >= 30 else moods

        metrics["mood_volatility"] = float(np.std(last14)) if len(last14) > 1 else 25.0
        metrics["mood_vol_30d"] = float(np.std(last30)) if len(last30) > 1 else 25.0
        metrics["current_mood"] = float(moods[-1])
        metrics["baseline_mood"] = float(np.mean(last30))

        # Count sign flips in last 14 days
        if len(last14) >= 2:
            signs = np.sign(last14)
            flips = int(np.sum(np.abs(np.diff(signs)) > 1))  # flip when sign changes by ≥1
            metrics["swing_count_14d"] = flips

        # Mean-reversion pressure: how far is current mood from baseline?
        sd = max(metrics["mood_vol_30d"], 5.0)
        metrics["mean_reversion_pressure"] = abs(metrics["current_mood"] - metrics["baseline_mood"]) / sd

    # Recent reversal rate from backtracks table
    try:
        from db import get_db, put_db, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        conn = get_db()
        try:
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) FROM trump_backtracks WHERE backtrack_date >= NOW() - INTERVAL '30 days'"
                )
                n = cur.fetchone()[0]
                cur.close()
            else:
                r = conn.execute(
                    "SELECT COUNT(*) FROM trump_backtracks WHERE backtrack_date >= datetime('now', '-30 days')"
                ).fetchone()
                n = r[0] if r else 0
            metrics["backtrack_rate"] = float(n) / 30.0
        finally:
            put_db(conn)
    except Exception as e:
        logger.debug(f"Backtrack rate load failed: {e}")

    # Persistence score: 1=stable, 0=chaotic. Volatility >40 and flip>3 in 14d = very erratic.
    # Typical mood volatility range 10-50; more than 35 is "erratic".
    vol_component = max(0.0, 1.0 - metrics["mood_volatility"] / 50.0)
    flip_component = max(0.0, 1.0 - metrics["swing_count_14d"] / 5.0)
    bt_component = max(0.0, 1.0 - metrics["backtrack_rate"] * 10.0)  # 0.1/day = fully erratic
    metrics["persistence_score"] = round(
        0.45 * vol_component + 0.35 * flip_component + 0.20 * bt_component, 3
    )

    return metrics


def _reversal_probability(metrics, horizon_days):
    """P(mood flips sign before horizon ends) ∈ [0.05, 0.75].

    Combines:
      - horizon-cumulative hazard (longer window → more chances to flip)
      - volatility multiplier (high stdev → easier to cross zero)
      - mean-reversion pressure (extreme mood → stronger pull back)
      - recent flip/backtrack rate (empirical prior)
    """
    # Daily hazard from observed flip rate (flips per day in last 14d)
    daily_flip_hazard = metrics["swing_count_14d"] / 14.0  # 0 to ~0.3
    # Augment with backtrack evidence
    daily_flip_hazard += 0.5 * metrics["backtrack_rate"]   # 0 to ~0.1

    # Volatility boost — mood near zero with high stdev flips easily
    current = metrics["current_mood"]
    vol = metrics["mood_volatility"]
    zero_proximity = max(0.0, 1.0 - abs(current) / 50.0)  # 1 if at 0, 0 if ±50
    vol_boost = (vol / 50.0) * zero_proximity * 0.05       # up to +5%/day

    # Mean-reversion pressure: extreme mood (z>1.5) adds hazard
    mr_boost = max(0.0, metrics["mean_reversion_pressure"] - 1.0) * 0.02  # up to +few%/day

    daily_hazard = daily_flip_hazard + vol_boost + mr_boost

    # Cumulative flip probability over horizon: 1 - (1 - daily_hazard)^horizon
    daily_hazard = max(0.005, min(0.25, daily_hazard))
    p_flip = 1.0 - (1.0 - daily_hazard) ** horizon_days

    return round(max(0.05, min(0.75, p_flip)), 3)


# ─── Models (pure numpy) ─────────────────────────────────────

def _standardize(X):
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd < 1e-8, 1.0, sd)
    return (X - mu) / sd, mu, sd


def _ridge_fit_predict(X_train, y_train, x_new, lam=1.0):
    """Ridge regression via closed form: w = (XᵀX + λI)⁻¹ Xᵀy."""
    Xs, mu, sd = _standardize(X_train)
    # Add intercept
    Xi = np.hstack([np.ones((Xs.shape[0], 1)), Xs])
    n_feat = Xi.shape[1]
    reg = lam * np.eye(n_feat)
    reg[0, 0] = 0  # no regularization on intercept
    try:
        w = np.linalg.solve(Xi.T @ Xi + reg, Xi.T @ y_train)
    except np.linalg.LinAlgError:
        return float(np.mean(y_train)), 0.3
    xn_s = (x_new - mu) / sd
    xn_i = np.concatenate([[1.0], xn_s])
    pred = float(xn_i @ w)
    # Confidence from training R²
    y_hat = Xi @ w
    ss_res = float(np.sum((y_train - y_hat) ** 2))
    ss_tot = float(np.sum((y_train - y_train.mean()) ** 2)) or 1.0
    r2 = max(0.0, 1.0 - ss_res / ss_tot)
    return pred, min(0.95, 0.35 + 0.6 * r2)


def _knn_predict(X_train, y_train, x_new, k=5):
    """K-nearest neighbors regression on standardized features."""
    Xs, mu, sd = _standardize(X_train)
    xn_s = (x_new - mu) / sd
    # Weight mood features more (first 5 features) — they are the signal
    weights = np.array([2.0, 1.5, 2.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5])
    dists = np.sqrt(np.sum(((Xs - xn_s) * weights) ** 2, axis=1))
    k = min(k, len(y_train))
    idx = np.argsort(dists)[:k]
    nn_weights = 1.0 / (dists[idx] + 1e-3)
    pred = float(np.sum(y_train[idx] * nn_weights) / np.sum(nn_weights))
    # Confidence inversely scales with dispersion of neighbors
    disp = float(np.std(y_train[idx]))
    conf = max(0.3, min(0.9, 0.85 - disp / 10.0))
    return pred, conf


def _ewma_predict(y_train, alpha=0.25):
    """Exponentially weighted moving average — anchors to recent drift."""
    if len(y_train) < 3:
        return float(np.mean(y_train)) if len(y_train) else 0.0, 0.3
    ewma = y_train[0]
    for val in y_train[1:]:
        ewma = alpha * val + (1 - alpha) * ewma
    return float(ewma), 0.45


def _llm_forecast(asset_key, mood_context, horizon_label):
    """Use Claude Opus to reason qualitatively about expected market response."""
    cache_key = f"llm_forecast_{asset_key}_{horizon_label}"
    cached = _get_cached(cache_key, LLM_FORECAST_TTL)
    if cached is not None:
        return cached

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key or api_key == "your_openrouter_api_key_here":
        return {"pred": 0.0, "conf": 0.3, "reasoning": "LLM unavailable (no API key)."}

    try:
        import requests as _req
        model = os.getenv("LLM_FORECAST_OPUS", "anthropic/claude-opus-4-6")
        asset_info = ASSETS.get(asset_key, {})
        erratic = mood_context.get("erratic", {}) or {}
        prompt = f"""You are an econometrics + political-risk analyst. Trump's rhetoric is known to be
non-stationary — he frequently pivots from threats to deals and back within days. You MUST
factor this reversal risk into your forecast (a positive mood today has meaningful probability
of flipping negative within the horizon, and vice versa).

ASSET: {asset_info.get('label', asset_key)} ({asset_key}, {asset_info.get('class')})
HORIZON: {horizon_label}

CURRENT TRUMP MOOD SNAPSHOT:
- Mood Index: {mood_context['mood']:.1f} / 100 ({mood_context['label']})
- 3-Day Pattern: {mood_context['trend']} (acceleration: {mood_context['accel']:.2f})
- Top signals: {mood_context['top_signals']}
- Notable posts: {mood_context['notable_posts']}

ERRATIC-BEHAVIOR METRICS (quantified reversal risk):
- 14-day mood volatility (stdev): {erratic.get('mood_volatility', 0):.1f}
- Sign flips in last 14 days: {erratic.get('swing_count_14d', 0)}
- Recent backtrack rate: {erratic.get('backtrack_rate', 0):.3f} reversals/day
- Mean-reversion pressure (z-score from 30d baseline): {erratic.get('mean_reversion_pressure', 0):.2f}
- Persistence score: {erratic.get('persistence_score', 0.5):.2f} (1=stable, 0=chaotic)
- 30-day baseline mood: {erratic.get('baseline_mood', 0):.1f}

Your forecast for {asset_key} over {horizon_label} must REFLECT the reversal risk — if mood
is extreme (|z|>1.5) or volatility is high, do NOT assume the current stance persists. Consider
a probability-weighted average of (a) mood persists and (b) mood flips sign mid-horizon.
Longer horizons = more flip risk.

Respond with JSON only:
{{
  "predicted_return_pct": <float, e.g. -1.2 for -1.2%>,
  "confidence": <0.0-1.0 — LOWER when erratic metrics are high>,
  "reasoning": "<1-2 sentences — MUST mention whether you expect persistence or flip and why>",
  "reversal_risk_assessment": "<1 short phrase: LOW/MEDIUM/HIGH + brief reason>",
  "dominant_driver": "<1 short phrase>"
}}"""

        resp = _req.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5001",
                "X-Title": "AI Stock Analyst - Trump Forecaster",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a political-risk market forecaster. Respond with valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 400,
                "temperature": 0.2,
            },
            timeout=40,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = json.loads(content)
        result = {
            "pred": float(parsed.get("predicted_return_pct", 0.0)),
            "conf": float(parsed.get("confidence", 0.5)),
            "reasoning": str(parsed.get("reasoning", "")),
            "driver": str(parsed.get("dominant_driver", "")),
            "reversal_risk_assessment": str(parsed.get("reversal_risk_assessment", "")),
        }
        _set_cached(cache_key, result)
        return result
    except Exception as e:
        logger.warning(f"LLM forecast failed for {asset_key}/{horizon_label}: {e}")
        # Fallback: simple mood-proportional heuristic
        mood = mood_context.get("mood", 0)
        # Tariff/bearish mood hurts crypto more than stocks; bullish helps both
        scale = 0.04 if ASSETS.get(asset_key, {}).get("class") == "crypto" else 0.02
        pred = mood * scale
        return {"pred": pred, "conf": 0.35, "reasoning": "LLM unavailable; heuristic fallback.", "driver": "mood-proxy"}


# ─── Weights (self-learning) ─────────────────────────────────

def _load_weights(asset_key, horizon):
    """Load ensemble weights from DB (falls back to defaults)."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        conn = get_db()
        try:
            if IS_POSTGRES:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    f"SELECT weights_json FROM trump_forecast_weights WHERE asset={P} AND horizon={P}",
                    (asset_key, horizon),
                )
                row = cur.fetchone()
                cur.close()
            else:
                row = conn.execute(
                    f"SELECT weights_json FROM trump_forecast_weights WHERE asset={P} AND horizon={P}",
                    (asset_key, horizon),
                ).fetchone()
                row = dict(row) if row else None
            if row and row.get("weights_json"):
                return json.loads(row["weights_json"])
        finally:
            put_db(conn)
    except Exception as e:
        logger.debug(f"Weights load failed: {e}")
    return dict(DEFAULT_WEIGHTS)


def _save_weights(asset_key, horizon, weights, mae_by_model, n_samples):
    """Persist updated ensemble weights to DB."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        conn = get_db()
        try:
            wj = json.dumps(weights)
            mj = json.dumps(mae_by_model)
            now = datetime.now().isoformat()
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"""INSERT INTO trump_forecast_weights (asset, horizon, weights_json, mae_json, n_samples, updated_at)
                        VALUES ({P},{P},{P},{P},{P},{P})
                        ON CONFLICT (asset, horizon) DO UPDATE SET
                            weights_json=EXCLUDED.weights_json,
                            mae_json=EXCLUDED.mae_json,
                            n_samples=EXCLUDED.n_samples,
                            updated_at=EXCLUDED.updated_at""",
                    (asset_key, horizon, wj, mj, n_samples, now),
                )
                cur.close()
            else:
                conn.execute(
                    f"""INSERT OR REPLACE INTO trump_forecast_weights
                        (asset, horizon, weights_json, mae_json, n_samples, updated_at)
                        VALUES ({P},{P},{P},{P},{P},{P})""",
                    (asset_key, horizon, wj, mj, n_samples, now),
                )
            conn.commit()
        finally:
            put_db(conn)
    except Exception as e:
        logger.warning(f"Weights save failed: {e}")


# ─── Forecast Generation ─────────────────────────────────────

def _ensemble_combine(preds, confs, weights):
    """Weighted ensemble of model predictions.

    preds: {model: pred_float}, confs: {model: conf_float}, weights: {model: w}
    Returns (combined_pred, combined_conf, effective_weights).
    """
    active = {m: preds[m] for m in MODELS if m in preds}
    if not active:
        return 0.0, 0.3, weights

    # Effective weights = base_weight × confidence, renormalized
    eff = {m: max(0.01, weights.get(m, 0.25)) * max(0.1, confs.get(m, 0.5)) for m in active}
    total = sum(eff.values()) or 1.0
    eff = {m: v / total for m, v in eff.items()}
    combined = sum(active[m] * eff[m] for m in active)

    # Confidence: weighted avg penalized by disagreement
    mean_conf = sum(confs.get(m, 0.5) * eff[m] for m in active)
    if len(active) > 1:
        vals = list(active.values())
        disagreement = float(np.std(vals))
        # 2% std across models is "normal" — more reduces confidence
        penalty = min(0.3, disagreement / 10.0)
        mean_conf = max(0.2, mean_conf - penalty)
    return float(combined), float(mean_conf), eff


def _direction(pred, threshold=0.3):
    if pred > threshold:
        return "up"
    if pred < -threshold:
        return "down"
    return "flat"


def _severity(pred_pct, asset_class):
    """Characterize predicted move magnitude."""
    ap = abs(pred_pct)
    if asset_class == "crypto":
        if ap >= 8: return "extreme"
        if ap >= 4: return "high"
        if ap >= 1.5: return "medium"
        return "low"
    else:
        if ap >= 3: return "extreme"
        if ap >= 1.5: return "high"
        if ap >= 0.5: return "medium"
        return "low"


def generate_forecast(force=False):
    """Generate forward-looking forecast for all assets × horizons.

    Returns:
      {
        generated_at, cached, mood_snapshot,
        forecasts: { SPY: { "1D": {..}, "5D": {..}, "21D": {..} }, ... },
        accuracy: { SPY: { "1D": {mae, n, ...}, ... }, ... }
      }
    """
    cached = _get_cached("forecast", FORECAST_TTL)
    if cached and not force:
        return {**cached, "cached": True}

    # Opportunistically grade past predictions before new run (self-correcting)
    try:
        evaluate_past_predictions()
    except Exception as e:
        logger.warning(f"Past prediction evaluation failed: {e}")

    from trump_mood import get_trump_mood
    trump = get_trump_mood()
    mood_daily = _load_mood_daily(days=90)

    # Quantify erratic behavior (drives two-scenario reversal blend)
    erratic = _compute_erratic_metrics(mood_daily)

    mood_context = {
        "mood": trump.get("mood", 0),
        "label": trump.get("label", "NEUTRAL"),
        "trend": trump.get("pattern", {}).get("trend", "stable"),
        "accel": trump.get("pattern", {}).get("acceleration", 0),
        "top_signals": "; ".join(s.get("text", "") for s in trump.get("top_signals", [])[:5]),
        "notable_posts": "; ".join((p.get("text", "") or "")[:120] for p in trump.get("notable_posts", [])[:3]),
        "erratic": erratic,
    }

    forecasts = {}
    accuracy_summary = {}

    for asset_key, meta in ASSETS.items():
        asset_forecast = {}
        prices = _load_prices(meta["yf"], days=150)
        ds = _build_dataset(asset_key, mood_daily, prices) if prices else None

        accuracy_summary[asset_key] = _load_accuracy_stats(asset_key)

        for h_key, h_info in HORIZONS.items():
            horizon_days = h_info["days"]
            weights = _load_weights(asset_key, h_key)

            preds = {}
            confs = {}
            ml_ok = False

            # Two-scenario forecast: (A) persist, (B) flip. Blended by P(flip).
            p_flip = _reversal_probability(erratic, horizon_days)
            preds_flip = {}
            confs_flip = {}

            if ds is not None and len(ds["y1"]) >= MIN_TRAIN_ROWS:
                X = ds["X"]
                y = ds[f"y{horizon_days}"] if horizon_days in (1, 5, 21) else ds["y1"]

                # Current feature vector: build from latest mood + most recent price data
                m_latest = mood_daily[-1]
                m_prev = mood_daily[-2] if len(mood_daily) >= 2 else m_latest
                m_3d = float(np.mean([x["mood"] for x in mood_daily[-3:]]))
                close_arr = np.array([p[1] for p in prices], dtype=float)
                ret_1d = float((close_arr[-1] / close_arr[-2]) - 1) * 100 if len(close_arr) >= 2 else 0
                ret_5d = float((close_arr[-1] / close_arr[-6]) - 1) * 100 if len(close_arr) >= 6 else 0
                vol_20 = float(np.std(np.diff(close_arr[-21:]) / close_arr[-21:-1])) * 100 if len(close_arr) >= 21 else 1.0

                # PERSIST scenario: current mood carries forward
                x_persist = np.array([
                    m_latest["mood"],
                    m_prev["mood"],
                    m_3d,
                    m_latest["mood_trend"],
                    m_latest["mood_accel"],
                    min(m_latest["posts"], 100) / 100.0,
                    ret_1d,
                    ret_5d,
                    vol_20,
                ])

                # FLIP scenario: mood mean-reverts past baseline toward opposite sign.
                # Target flip magnitude = symmetric across 30d baseline, capped at ±60.
                baseline = erratic["baseline_mood"]
                flipped_mood = float(max(-60.0, min(60.0, 2 * baseline - m_latest["mood"])))
                # If baseline is near current mood, force an opposite-sign flip anchored to -sign*30
                if abs(flipped_mood - m_latest["mood"]) < 20:
                    flipped_mood = -np.sign(m_latest["mood"] or 1) * 30.0
                x_flip = np.array([
                    flipped_mood,
                    m_latest["mood"],  # the flip just happened; previous = today's mood
                    (flipped_mood + m_latest["mood"] + m_prev["mood"]) / 3.0,
                    -m_latest["mood_trend"],   # reversed trend
                    -m_latest["mood_accel"],
                    min(m_latest["posts"], 100) / 100.0,
                    ret_1d,
                    ret_5d,
                    vol_20,
                ])

                for scen_name, x_vec, sink_p, sink_c in [
                    ("persist", x_persist, preds, confs),
                    ("flip",    x_flip,    preds_flip, confs_flip),
                ]:
                    try:
                        p, c = _ridge_fit_predict(X, y, x_vec, lam=2.0)
                        sink_p["ridge"] = p; sink_c["ridge"] = c
                    except Exception as e:
                        logger.debug(f"ridge {scen_name} fail {asset_key}/{h_key}: {e}")
                    try:
                        p, c = _knn_predict(X, y, x_vec, k=min(5, len(y)))
                        sink_p["knn"] = p; sink_c["knn"] = c
                    except Exception as e:
                        logger.debug(f"knn {scen_name} fail {asset_key}/{h_key}: {e}")

                # EWMA is mood-agnostic (uses target history only) — same in both scenarios
                try:
                    p, c = _ewma_predict(y, alpha=0.3)
                    preds["ewma"] = p; confs["ewma"] = c
                    preds_flip["ewma"] = p; confs_flip["ewma"] = c
                except Exception as e:
                    logger.debug(f"ewma fail {asset_key}/{h_key}: {e}")

                ml_ok = True
            else:
                # Not enough training data → simple heuristic fallback (both scenarios)
                mood_scale = 0.04 if meta["class"] == "crypto" else 0.02
                horizon_scale = {"1D": 1.0, "5D": 2.2, "21D": 4.5}[h_key]
                preds["ridge"] = mood_context["mood"] * mood_scale * horizon_scale * 0.6
                confs["ridge"] = 0.3
                flipped_heur = -np.sign(mood_context["mood"] or 1) * 30.0
                preds_flip["ridge"] = float(flipped_heur * mood_scale * horizon_scale * 0.6)
                confs_flip["ridge"] = 0.3

            # LLM — already factors erratic metrics via prompt, shared for both scenarios
            llm_out = _llm_forecast(asset_key, mood_context, h_info["label"])
            preds["llm"] = llm_out["pred"]
            confs["llm"] = llm_out["conf"]
            # In flip scenario, assume LLM directional prediction inverts (dampened 0.7x)
            preds_flip["llm"] = -0.7 * llm_out["pred"]
            confs_flip["llm"] = max(0.25, llm_out["conf"] - 0.1)

            persist_pred, persist_conf, eff_weights = _ensemble_combine(preds, confs, weights)
            flip_pred, flip_conf, _ = _ensemble_combine(preds_flip, confs_flip, weights)

            # Probability-weighted blend
            combined = (1 - p_flip) * persist_pred + p_flip * flip_pred
            # Confidence is reduced by scenario disagreement and flip probability
            scenario_disagreement = abs(persist_pred - flip_pred)
            base_conf = (1 - p_flip) * persist_conf + p_flip * flip_conf
            erratic_penalty = min(0.35, (1 - erratic["persistence_score"]) * 0.4 + p_flip * 0.25)
            conf = max(0.15, base_conf * (1 - erratic_penalty) - min(0.15, scenario_disagreement / 30.0))

            # Clamp predictions to sensible ranges by asset class × horizon
            max_abs = {
                "stock": {"1D": 4.0, "5D": 8.0, "21D": 15.0},
                "crypto": {"1D": 10.0, "5D": 20.0, "21D": 40.0},
            }[meta["class"]][h_key]
            combined = float(max(-max_abs, min(max_abs, combined)))

            direction = _direction(combined, threshold=0.15 if meta["class"] == "stock" else 0.4)
            severity = _severity(combined, meta["class"])

            asset_forecast[h_key] = {
                "horizon": h_key,
                "horizon_label": h_info["label"],
                "predicted_return_pct": round(combined, 2),
                "direction": direction,
                "severity": severity,
                "confidence": round(conf, 2),
                "model_predictions": {k: round(v, 2) for k, v in preds.items()},
                "model_confidences": {k: round(v, 2) for k, v in confs.items()},
                "effective_weights": {k: round(v, 3) for k, v in eff_weights.items()},
                "ml_trained": ml_ok,
                "reasoning": llm_out.get("reasoning", ""),
                "driver": llm_out.get("driver", ""),
                # Two-scenario breakdown
                "scenarios": {
                    "persist": {
                        "predicted_return_pct": round(persist_pred, 2),
                        "probability": round(1 - p_flip, 3),
                        "confidence": round(persist_conf, 2),
                    },
                    "flip": {
                        "predicted_return_pct": round(flip_pred, 2),
                        "probability": round(p_flip, 3),
                        "confidence": round(flip_conf, 2),
                    },
                },
                "reversal_risk": {
                    "probability": p_flip,
                    "assessment": (
                        "HIGH" if p_flip >= 0.40 else
                        "MEDIUM" if p_flip >= 0.20 else
                        "LOW"
                    ),
                    "llm_assessment": llm_out.get("reversal_risk_assessment", ""),
                },
            }

            # Persist this prediction for future evaluation
            _save_prediction(asset_key, h_key, combined, conf, preds, mood_context)

        forecasts[asset_key] = {
            "label": meta["label"],
            "class": meta["class"],
            "horizons": asset_forecast,
        }

    result = {
        "generated_at": datetime.now().isoformat(),
        "cached": False,
        "mood_snapshot": {
            "mood": mood_context["mood"],
            "label": mood_context["label"],
            "trend": mood_context["trend"],
            "acceleration": mood_context["accel"],
        },
        "erratic_metrics": {
            "mood_volatility_14d": round(erratic["mood_volatility"], 1),
            "mood_volatility_30d": round(erratic["mood_vol_30d"], 1),
            "swing_count_14d": erratic["swing_count_14d"],
            "backtrack_rate_per_day": round(erratic["backtrack_rate"], 3),
            "mean_reversion_pressure": round(erratic["mean_reversion_pressure"], 2),
            "persistence_score": erratic["persistence_score"],
            "baseline_mood_30d": round(erratic["baseline_mood"], 1),
            "current_vs_baseline": round(erratic["current_mood"] - erratic["baseline_mood"], 1),
        },
        "forecasts": forecasts,
        "accuracy": accuracy_summary,
        "training_rows": len(mood_daily),
        "models": MODELS,
    }
    _set_cached("forecast", result)
    return result


# ─── Correlation Timeline ────────────────────────────────────

def get_correlation_timeline(days=60):
    """Pearson correlation of mood_t-k vs asset_return_t for lags 0..7.

    Returns {asset: {lag: corr_coeff}, best_lag: {asset: int}, series: [...]}.
    The 'series' is a daily time-aligned dataset for charting mood + returns.
    """
    cached = _get_cached(f"corr_{days}", CORR_TTL)
    if cached:
        return cached

    mood_daily = _load_mood_daily(days=days)
    if len(mood_daily) < 7:
        return {"assets": {}, "best_lag": {}, "series": [], "note": "Insufficient mood history — need at least 7 days."}

    mood_dates = [m["date"] for m in mood_daily]
    mood_vals = np.array([m["mood"] for m in mood_daily], dtype=float)

    assets_corr = {}
    best_lag = {}
    series = [{"date": m["date"], "mood": round(m["mood"], 1)} for m in mood_daily]

    for asset_key, meta in ASSETS.items():
        prices = _load_prices(meta["yf"], days=days + 30)
        if not prices:
            continue
        price_map = {d: c for d, c in prices}
        dates_sorted = sorted(price_map.keys())

        # Build daily returns aligned with mood dates
        aligned_returns = []
        for d in mood_dates:
            idx_candidates = [i for i, pd in enumerate(dates_sorted) if pd <= d]
            if len(idx_candidates) < 2:
                aligned_returns.append(np.nan)
                continue
            i = idx_candidates[-1]
            if i == 0:
                aligned_returns.append(np.nan)
            else:
                r = (price_map[dates_sorted[i]] / price_map[dates_sorted[i - 1]]) - 1
                aligned_returns.append(r * 100)
        returns = np.array(aligned_returns, dtype=float)

        # Add to series
        for i, s in enumerate(series):
            s[asset_key] = round(returns[i], 3) if not np.isnan(returns[i]) else None

        # Compute corr at lags 0..7 (mood leads returns by k days)
        lag_corrs = {}
        for lag in range(0, 8):
            if lag >= len(mood_vals):
                break
            m_slice = mood_vals[:-lag] if lag > 0 else mood_vals
            r_slice = returns[lag:]
            mask = ~np.isnan(r_slice)
            if mask.sum() < 5:
                continue
            try:
                c = float(np.corrcoef(m_slice[mask], r_slice[mask])[0, 1])
                if np.isnan(c):
                    continue
                lag_corrs[lag] = round(c, 3)
            except Exception:
                pass

        if lag_corrs:
            assets_corr[asset_key] = {
                "label": meta["label"],
                "class": meta["class"],
                "lags": lag_corrs,
            }
            bl = max(lag_corrs.items(), key=lambda x: abs(x[1]))
            best_lag[asset_key] = {"lag_days": bl[0], "corr": bl[1]}

    result = {
        "assets": assets_corr,
        "best_lag": best_lag,
        "series": series,
        "generated_at": datetime.now().isoformat(),
    }
    _set_cached(f"corr_{days}", result)
    return result


# ─── Predictions Persistence & Self-Correction ───────────────

def _save_prediction(asset, horizon, combined_pred, confidence, model_preds, mood_context):
    """Persist a prediction row for later evaluation."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        conn = get_db()
        try:
            now = datetime.now()
            horizon_days = HORIZONS[horizon]["days"]
            target_date = (now + timedelta(days=horizon_days)).isoformat()
            mp_json = json.dumps({k: float(v) for k, v in model_preds.items()})
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"""INSERT INTO trump_market_predictions
                        (asset, horizon, predicted_return_pct, confidence, model_predictions,
                         mood_at_prediction, mood_label, target_date, created_at)
                        VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P})""",
                    (asset, horizon, float(combined_pred), float(confidence), mp_json,
                     float(mood_context["mood"]), mood_context["label"], target_date, now.isoformat()),
                )
                cur.close()
            else:
                conn.execute(
                    f"""INSERT INTO trump_market_predictions
                        (asset, horizon, predicted_return_pct, confidence, model_predictions,
                         mood_at_prediction, mood_label, target_date, created_at)
                        VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P})""",
                    (asset, horizon, float(combined_pred), float(confidence), mp_json,
                     float(mood_context["mood"]), mood_context["label"], target_date, now.isoformat()),
                )
            conn.commit()
        finally:
            put_db(conn)
    except Exception as e:
        logger.debug(f"Prediction save failed: {e}")


def evaluate_past_predictions():
    """Grade predictions whose target date has passed, compute per-model MAE,
    and update ensemble weights via inverse-MAE reweighting (self-correction)."""
    try:
        from db import get_db, put_db, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        conn = get_db()
    except Exception:
        return

    try:
        now = datetime.now().isoformat()
        if IS_POSTGRES:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"""SELECT * FROM trump_market_predictions
                    WHERE actual_return_pct IS NULL AND target_date <= {P}
                    ORDER BY created_at ASC LIMIT 200""",
                (now,),
            )
            rows = cur.fetchall()
            cur.close()
        else:
            rows = conn.execute(
                f"""SELECT * FROM trump_market_predictions
                    WHERE actual_return_pct IS NULL AND target_date <= {P}
                    ORDER BY created_at ASC LIMIT 200""",
                (now,),
            ).fetchall()
            rows = [dict(r) for r in rows]

        if not rows:
            return

        # Group by asset; fetch prices per asset
        asset_rows = {}
        for r in rows:
            asset_rows.setdefault(r["asset"], []).append(r)

        graded = 0
        for asset, asset_records in asset_rows.items():
            meta = ASSETS.get(asset)
            if not meta:
                continue
            prices = _load_prices(meta["yf"], days=60)
            if not prices:
                continue
            price_map = dict(prices)
            dates_sorted = sorted(price_map.keys())

            def _closest_on_or_before(date_str):
                cands = [d for d in dates_sorted if d <= date_str]
                return cands[-1] if cands else None

            for rec in asset_records:
                try:
                    created = str(rec["created_at"])[:10]
                    target = str(rec["target_date"])[:10]
                    d0 = _closest_on_or_before(created)
                    d1 = _closest_on_or_before(target)
                    if not d0 or not d1 or d0 == d1:
                        continue
                    p0 = price_map[d0]; p1 = price_map[d1]
                    actual = ((p1 / p0) - 1) * 100
                    error = actual - float(rec["predicted_return_pct"])
                    if IS_POSTGRES:
                        cur = conn.cursor()
                        cur.execute(
                            f"""UPDATE trump_market_predictions
                                SET actual_return_pct={P}, error_pct={P}, evaluated_at={P}
                                WHERE id={P}""",
                            (float(actual), float(error), datetime.now().isoformat(), rec["id"]),
                        )
                        cur.close()
                    else:
                        conn.execute(
                            f"""UPDATE trump_market_predictions
                                SET actual_return_pct={P}, error_pct={P}, evaluated_at={P}
                                WHERE id={P}""",
                            (float(actual), float(error), datetime.now().isoformat(), rec["id"]),
                        )
                    graded += 1
                except Exception as e:
                    logger.debug(f"Grading failed for pred {rec.get('id')}: {e}")
        conn.commit()

        if graded == 0:
            return

        # Re-compute per-model MAE and update weights per (asset, horizon)
        for asset in ASSETS.keys():
            for horizon in HORIZONS.keys():
                if IS_POSTGRES:
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                    cur.execute(
                        f"""SELECT predicted_return_pct, model_predictions, actual_return_pct
                            FROM trump_market_predictions
                            WHERE asset={P} AND horizon={P} AND actual_return_pct IS NOT NULL
                            ORDER BY evaluated_at DESC LIMIT 40""",
                        (asset, horizon),
                    )
                    eval_rows = cur.fetchall()
                    cur.close()
                else:
                    eval_rows = conn.execute(
                        f"""SELECT predicted_return_pct, model_predictions, actual_return_pct
                            FROM trump_market_predictions
                            WHERE asset={P} AND horizon={P} AND actual_return_pct IS NOT NULL
                            ORDER BY evaluated_at DESC LIMIT 40""",
                        (asset, horizon),
                    ).fetchall()
                    eval_rows = [dict(r) for r in eval_rows]

                if len(eval_rows) < 3:
                    continue

                mae_by_model = {m: [] for m in MODELS}
                for er in eval_rows:
                    actual = float(er["actual_return_pct"])
                    mp = er.get("model_predictions")
                    if isinstance(mp, str):
                        try:
                            mp = json.loads(mp)
                        except Exception:
                            mp = {}
                    if not isinstance(mp, dict):
                        continue
                    for m in MODELS:
                        if m in mp:
                            mae_by_model[m].append(abs(actual - float(mp[m])))
                mae = {}
                for m, errs in mae_by_model.items():
                    if errs:
                        mae[m] = float(np.mean(errs))

                if len(mae) < 2:
                    continue
                # Inverse-MAE weights (add epsilon to avoid div by zero)
                eps = 0.5
                inv = {m: 1.0 / (v + eps) for m, v in mae.items()}
                total = sum(inv.values()) or 1.0
                new_w = {m: inv.get(m, 0.0) / total for m in MODELS}
                # Smooth with prior (exponential moving toward new weights)
                prior = _load_weights(asset, horizon)
                blended = {m: round(0.7 * prior.get(m, DEFAULT_WEIGHTS[m]) + 0.3 * new_w.get(m, DEFAULT_WEIGHTS[m]), 4)
                           for m in MODELS}
                _save_weights(asset, horizon, blended, mae, len(eval_rows))
    finally:
        put_db(conn)


def _load_accuracy_stats(asset):
    """Aggregate per-horizon accuracy metrics for display."""
    stats = {}
    try:
        from db import get_db, put_db, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        conn = get_db()
        try:
            for horizon in HORIZONS.keys():
                if IS_POSTGRES:
                    import psycopg2.extras
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                    cur.execute(
                        f"""SELECT predicted_return_pct, actual_return_pct, error_pct
                            FROM trump_market_predictions
                            WHERE asset={P} AND horizon={P} AND actual_return_pct IS NOT NULL
                            ORDER BY evaluated_at DESC LIMIT 30""",
                        (asset, horizon),
                    )
                    rows = cur.fetchall()
                    cur.close()
                else:
                    rows = conn.execute(
                        f"""SELECT predicted_return_pct, actual_return_pct, error_pct
                            FROM trump_market_predictions
                            WHERE asset={P} AND horizon={P} AND actual_return_pct IS NOT NULL
                            ORDER BY evaluated_at DESC LIMIT 30""",
                        (asset, horizon),
                    ).fetchall()
                    rows = [dict(r) for r in rows]
                if not rows:
                    stats[horizon] = {"n": 0, "mae": None, "direction_accuracy": None}
                    continue
                errs = [abs(float(r["error_pct"])) for r in rows if r.get("error_pct") is not None]
                correct_dir = sum(
                    1 for r in rows
                    if (float(r["predicted_return_pct"]) >= 0) == (float(r["actual_return_pct"]) >= 0)
                )
                stats[horizon] = {
                    "n": len(rows),
                    "mae": round(float(np.mean(errs)), 2) if errs else None,
                    "direction_accuracy": round(correct_dir / len(rows), 2),
                }
        finally:
            put_db(conn)
    except Exception as e:
        logger.debug(f"Accuracy stats failed for {asset}: {e}")
    return stats


def get_forecast_accuracy():
    """Public accessor — returns accuracy stats for all assets."""
    return {a: _load_accuracy_stats(a) for a in ASSETS.keys()}
