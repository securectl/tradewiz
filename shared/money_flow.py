"""Money-flow read for a price series — is money flowing IN (accumulation) or
OUT (distribution / profit-taking)?

Combines two volume-weighted indicators into one glanceable signal:
  - Chaikin Money Flow (CMF, 20) — sign tells accumulation vs distribution.
  - Money Flow Index (MFI, 14)   — overbought (>80) + rolling over near highs
                                   flags profit-taking / a likely top.

`compute_money_flow(df)` is the entry point (takes an OHLCV DataFrame, returns a
small dict). `classify_money_flow(...)` is the pure decision rule, split out so
the branch logic is unit-testable without building DataFrames. Both are pure
pandas/numpy with no app dependencies so they're reusable by the screener and
the deep analyzer alike.
"""

import numpy as np
import pandas as pd

# Minimum bars before the indicators are meaningful (MFI needs 14 + a prior bar,
# CMF averages 20). Below this we report no signal rather than a noisy one.
MIN_BARS = 15

CMF_PERIOD = 20
MFI_PERIOD = 14
CMF_IN_THRESHOLD = 0.05      # CMF >= this → accumulation
CMF_OUT_THRESHOLD = -0.05    # CMF <= this → distribution
MFI_OVERBOUGHT = 80          # MFI above this recently → profit-taking risk
NEAR_HIGH_FRAC = 0.97        # close within 3% of the 20-bar high → "near highs"

# Signal codes → human labels. None → "—" (insufficient data).
LABELS = {
    "IN": "Money In",
    "OUT": "Money Out",
    "PROFIT_TAKING": "Profit-Taking",
    "NEUTRAL": "Neutral",
    None: "—",
}


def _chaikin_money_flow(high, low, close, volume, period=CMF_PERIOD):
    """CMF over the last `period` bars. Returns a float (0.0 if undefined)."""
    rng = (high - low).replace(0, np.nan)            # guard flat bars (H == L)
    mfm = ((close - low) - (high - close)) / rng     # money-flow multiplier
    mfm = mfm.fillna(0.0)
    mfv = mfm * volume                               # money-flow volume
    vol_sum = volume.iloc[-period:].sum()
    if vol_sum <= 0:
        return 0.0
    return float(mfv.iloc[-period:].sum() / vol_sum)


def _money_flow_index(high, low, close, volume, period=MFI_PERIOD):
    """Return (mfi, mfi_prev) — current MFI and MFI one bar earlier, so callers
    can tell whether it is rolling over. Values in [0, 100]."""
    tp = (high + low + close) / 3.0                  # typical price
    raw_flow = tp * volume
    tp_diff = tp.diff()
    pos_flow = raw_flow.where(tp_diff > 0, 0.0)
    neg_flow = raw_flow.where(tp_diff < 0, 0.0)

    def _mfi_at(end):
        # MFI over the `period` bars ending at iloc position `end` (inclusive).
        lo = end - period + 1
        if lo < 1:                                   # need a prior bar for diff
            return None
        pos = pos_flow.iloc[lo:end + 1].sum()
        neg = neg_flow.iloc[lo:end + 1].sum()
        if neg == 0:
            return 100.0 if pos > 0 else 50.0
        ratio = pos / neg
        return float(100.0 - (100.0 / (1.0 + ratio)))

    last = len(close) - 1
    return _mfi_at(last), _mfi_at(last - 1)


def classify_money_flow(cmf, mfi, mfi_prev, near_high):
    """Combine CMF + MFI into one signal code. PROFIT_TAKING is checked first so
    a topping stock near its highs isn't masked by a still-positive CMF."""
    if (near_high and mfi is not None and mfi_prev is not None
            and mfi_prev >= MFI_OVERBOUGHT and mfi < mfi_prev):
        return "PROFIT_TAKING"
    if cmf >= CMF_IN_THRESHOLD:
        return "IN"
    if cmf <= CMF_OUT_THRESHOLD:
        return "OUT"
    return "NEUTRAL"


def compute_money_flow(df):
    """Compute money flow for an OHLCV DataFrame (needs High/Low/Close/Volume).

    Returns {"cmf", "mfi", "mf_signal", "mf_label"}. With fewer than MIN_BARS
    rows (or on any error) mf_signal is None and mf_label is "—" so callers can
    render a neutral placeholder — it never raises.
    """
    none_result = {"cmf": None, "mfi": None, "mf_signal": None, "mf_label": LABELS[None]}
    try:
        if df is None or len(df) < MIN_BARS:
            return none_result
        for col in ("High", "Low", "Close", "Volume"):
            if col not in df.columns:
                return none_result
        high = df["High"].astype(float)
        low = df["Low"].astype(float)
        close = df["Close"].astype(float)
        volume = df["Volume"].astype(float)
        if close.isna().all() or volume.fillna(0).sum() <= 0:
            return none_result

        cmf = _chaikin_money_flow(high, low, close, volume)
        mfi, mfi_prev = _money_flow_index(high, low, close, volume)

        recent_high = high.iloc[-CMF_PERIOD:].max()
        near_high = bool(recent_high > 0 and close.iloc[-1] >= NEAR_HIGH_FRAC * recent_high)

        signal = classify_money_flow(cmf, mfi, mfi_prev, near_high)
        return {
            "cmf": round(cmf, 3),
            "mfi": round(mfi, 1) if mfi is not None else None,
            "mf_signal": signal,
            "mf_label": LABELS.get(signal, LABELS[None]),
        }
    except Exception:
        return none_result
