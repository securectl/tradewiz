"""Portfolio Advisor — vet each holding and recommend where to CUT and where to ADD.

Grounded in real data first (trend, money flow, RSI, distance from highs, P&L vs
cost basis) so the AI can't hand-wave: every holding gets a deterministic
rule-based recommendation, then a "25-year stock advisor" LLM synthesizes the
narrative + portfolio-level guidance on top of those facts. If the LLM is
unavailable the rule-based recommendations stand (CLAUDE.md rule #3).
"""

import json
import logging

logger = logging.getLogger(__name__)

ACTIONS = ("ADD", "HOLD", "TRIM", "SELL")


def _holding_quant(symbol):
    """Per-holding facts: price, trend, money-flow, RSI, % off 52w high."""
    q = {"symbol": symbol, "price": None, "trend": "—", "uptrend": None,
         "mf_signal": None, "mf_label": "—", "cmf": None, "rsi": None,
         "pct_from_high": None}
    try:
        import yfinance as yf
        df = yf.Ticker(symbol).history(period="10mo")
        if df is None or len(df) < 30:
            return q
        close = df["Close"].dropna()
        q["price"] = round(float(close.iloc[-1]), 2)
        # Trend (reuse the screener's classifier)
        try:
            from screener import _classify_trend
            tr = _classify_trend(close)
            q["trend"], q["uptrend"] = tr.get("trend_label", "—"), tr.get("uptrend")
        except Exception:
            pass
        # Money flow (Chaikin + MFI, fused with options)
        try:
            from analysis_engine import compute_money_flow_read
            mf = compute_money_flow_read(symbol, df)
            q["mf_signal"], q["mf_label"] = mf.get("signal"), mf.get("label")
            q["cmf"] = (mf.get("equity") or {}).get("cmf")
        except Exception:
            pass
        # RSI(14)
        try:
            delta = close.diff()
            up = delta.clip(lower=0).rolling(14).mean()
            dn = (-delta.clip(upper=0)).rolling(14).mean()
            rs = up / dn.replace(0, 1e-9)
            q["rsi"] = round(float(100 - 100 / (1 + rs.iloc[-1])), 1)
        except Exception:
            pass
        # Distance from 52-week high
        try:
            hi = float(close.tail(252).max())
            if hi > 0:
                q["pct_from_high"] = round((q["price"] - hi) / hi * 100, 1)
        except Exception:
            pass
    except Exception as e:
        logger.debug("quant failed for %s: %s", symbol, e)
    return q


_DISTRIB = {"OUT", "STRONG_OUT", "TOP", "PROFIT_TAKING", "DISTRIBUTION"}
_INFLOW = {"IN", "STRONG_IN"}


def _rule_reco(q):
    """Deterministic ADD / HOLD / TRIM / SELL from the facts."""
    up = q.get("uptrend")
    mf = (q.get("mf_signal") or "").upper()
    cmf = q.get("cmf")
    rsi = q.get("rsi")
    distributing = mf in _DISTRIB or (cmf is not None and cmf < -0.08)
    inflow = mf in _INFLOW or (cmf is not None and cmf > 0.08)
    overbought = rsi is not None and rsi >= 75
    oversold = rsi is not None and rsi <= 32

    if up is False and distributing:
        return {"action": "SELL", "conviction": "high",
                "reason": "Downtrend with money flowing out — cut the position."}
    if up is False or distributing:
        return {"action": "TRIM", "conviction": "medium",
                "reason": "Weak trend or distribution — reduce and raise the stop."}
    if overbought and (q.get("pct_from_high") or -99) > -3:
        return {"action": "TRIM", "conviction": "low",
                "reason": "Extended and overbought at the highs — take some profit."}
    if up is True and inflow and not overbought:
        return {"action": "ADD", "conviction": "high",
                "reason": "Uptrend with money flowing in — add on strength."}
    if oversold and up is not False:
        return {"action": "HOLD", "conviction": "low",
                "reason": "Oversold but trend intact — hold; wait for stabilization."}
    return {"action": "HOLD", "conviction": "medium",
            "reason": "Mixed signals — hold and monitor."}


def _portfolio_summary(rows):
    total = sum((r.get("value") or 0) for r in rows) or 0
    cut = [r["symbol"] for r in rows if r["action"] in ("SELL", "TRIM")]
    add = [r["symbol"] for r in rows if r["action"] == "ADD"]
    # Concentration: largest single position as % of portfolio value.
    conc = 0
    top = None
    if total:
        for r in rows:
            pct = (r.get("value") or 0) / total * 100
            if pct > conc:
                conc, top = pct, r["symbol"]
    return {
        "holdings": len(rows),
        "total_value": round(total, 2) if total else None,
        "cut": cut, "add": add,
        "top_position": top, "top_position_pct": round(conc, 1) if conc else None,
        "concentration_flag": conc >= 25,
    }


def analyze_portfolio(holdings, use_llm=True):
    """Analyze a list of {symbol, shares, cost_basis} → per-holding + summary."""
    rows = []
    for h in holdings[:60]:
        sym = h.get("symbol")
        if not sym:
            continue
        q = _holding_quant(sym)
        reco = _rule_reco(q)
        shares = h.get("shares") or 0
        cost = h.get("cost_basis")
        value = round((q.get("price") or 0) * shares, 2) if q.get("price") else None
        pnl_pct = None
        if cost and value:
            pnl_pct = round((value - cost) / cost * 100, 1)
        rows.append({
            "symbol": sym, "shares": shares, "price": q.get("price"),
            "value": value, "cost_basis": cost, "pnl_pct": pnl_pct,
            "trend": q.get("trend"), "mf_label": q.get("mf_label"),
            "rsi": q.get("rsi"), "pct_from_high": q.get("pct_from_high"),
            "action": reco["action"], "conviction": reco["conviction"],
            "reason": reco["reason"],
        })

    summary = _portfolio_summary(rows)
    advisor_note = None
    if use_llm and rows:
        advisor_note = _advisor_llm(rows, summary)
    return {"holdings": rows, "summary": summary, "advisor_note": advisor_note}


_ADVISOR_SYSTEM = (
    "You are a portfolio advisor with 25+ years of experience managing equity "
    "portfolios through multiple market cycles. You are disciplined, risk-aware "
    "and blunt. You are given a client's holdings with objective data already "
    "computed (trend, money flow, RSI, distance from highs, P&L, and a preliminary "
    "rule-based action for each). Do NOT contradict the objective data; use it. "
    "Return STRICT JSON only: {\"overview\": str, \"where_to_cut\": str, "
    "\"where_to_add\": str, \"risk\": str}. Be specific and reference tickers. "
    "This is educational analysis, not personalized investment advice."
)


def _advisor_llm(rows, summary):
    """25-year-advisor narrative on top of the facts. Returns a dict or None."""
    try:
        import os
        import requests
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            return None
        model = os.getenv("OPENROUTER_MODEL_RESEARCH", "anthropic/claude-3.5-sonnet")
        compact = [{k: r.get(k) for k in ("symbol", "value", "pnl_pct", "trend",
                                          "mf_label", "rsi", "action", "reason")} for r in rows]
        user = ("Client holdings + computed facts:\n" + json.dumps(compact) +
                "\n\nPortfolio summary: " + json.dumps(summary) +
                "\n\nGive your professional read.")
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "temperature": 0.3, "max_tokens": 700,
                  "messages": [{"role": "system", "content": _ADVISOR_SYSTEM},
                               {"role": "user", "content": user}]},
            timeout=40,
        )
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        m = content[content.find("{"):content.rfind("}") + 1]
        return json.loads(m) if m else None
    except Exception as e:
        logger.debug("advisor LLM failed: %s", e)
        return None
