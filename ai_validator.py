"""
AI Validation Engine — Multi-LLM Rigorous Trade Setup Vetting
Uses specialized LLMs via OpenRouter to validate every setup from every angle.
Risk management is the #1 priority. Capital preservation above all.

Setup Validation (3-Model):
  Model 1 (Research):    Fundamentals, sector, red flags, catalysts
  Model 2 (Pattern):     Chart pattern validation, breakout probability
  Model 3 (Prediction):  Price targets, risk modeling, stop loss, scenarios

12-Month Prediction (4-Model, 3/4 Quorum):
  LLM 1 (Fact Gatherer):    Gathers all facts (fundamentals + technicals) — runs first
  LLM 2 (Company Health):   Financial health + moat assessment — parallel
  LLM 3 (Price Action):     Valuation + technical analysis — parallel
  LLM 4 (Supervisor):       Reviews all, casts independent vote — runs last
"""

import os
import json
import requests
import concurrent.futures
from datetime import datetime
from dotenv import load_dotenv
from shared.prompts.analysis import (
    RESEARCH_SYSTEM, RESEARCH_USER_TEMPLATE,
    PATTERN_SYSTEM, PATTERN_USER_TEMPLATE,
    PREDICTION_SYSTEM, PREDICTION_USER_TEMPLATE,
    FACT_GATHERER_SYSTEM, FACT_GATHERER_USER_TEMPLATE,
    COMPANY_HEALTH_SYSTEM, COMPANY_HEALTH_USER_TEMPLATE,
    PRICE_ACTION_SYSTEM, PRICE_ACTION_USER_TEMPLATE,
    SUPERVISOR_PROMPTS, SUPERVISOR_USER_TEMPLATE,
)

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

LLM_RESEARCH = os.getenv("LLM_RESEARCH", "anthropic/claude-sonnet-4-6")
LLM_RESEARCH_FAST = os.getenv("LLM_RESEARCH_FAST", "google/gemini-2.5-flash")
LLM_PATTERN = os.getenv("LLM_PATTERN", "google/gemini-2.5-pro-preview")
LLM_PREDICTION = os.getenv("LLM_PREDICTION", "deepseek/deepseek-chat-v3-0324")
LLM_SCREENER = os.getenv("LLM_SCREENER", "google/gemini-2.5-flash")
LLM_SUPERVISOR = os.getenv("LLM_SUPERVISOR", "")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_FAST_MODE = os.getenv("LLM_FAST_MODE", "0") == "1"

HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "http://localhost:5001",
    "X-Title": "AI Stock Analyst",
}


def is_configured() -> bool:
    """Check if OpenRouter API key is set."""
    return bool(OPENROUTER_API_KEY) and OPENROUTER_API_KEY != "your_openrouter_api_key_here"


def _call_openrouter(model: str, messages: list, temperature: float = None,
                     max_tokens: int = None, timeout: int = None,
                     _user_id: int = None, _source: str = None,
                     role: str = None) -> str:
    """Make a single LLM call. Routes to Vertex AI if configured, else OpenRouter.

    If `role` is passed, the model name is resolved through shared.llm_config
    so admin-set DB overrides take precedence over the env-resolved `model`
    argument. This is what makes the admin "swap to cheaper model" UI work
    without a redeploy. Without `role`, behavior is unchanged.
    """
    if role:
        try:
            from shared.llm_config import get_model
            model = get_model(role, model)
        except Exception:
            pass  # llm_config unavailable — fall through with original model

    # ── Vertex AI path (direct Google Cloud, no OpenRouter markup) ──
    try:
        from vertex_adapter import is_available, call_vertex
        if is_available():
            content = call_vertex(
                model, messages,
                max_tokens=max_tokens or LLM_MAX_TOKENS,
                temperature=temperature if temperature is not None else LLM_TEMPERATURE,
                timeout=timeout or 60,
            )
            # Record LLM usage
            try:
                from rate_limiter import get_llm_user, record_llm_call
                uid, source = _user_id, _source
                if not uid:
                    uid, source = get_llm_user()
                if uid:
                    record_llm_call(uid, source or "api", model)
            except Exception:
                pass
            return content
    except ImportError:
        pass  # vertex_adapter not installed, use OpenRouter
    except Exception as e:
        # Vertex failed, fall through to OpenRouter
        import logging
        logging.getLogger(__name__).warning(f"Vertex AI failed, falling back to OpenRouter: {e}")

    # ── OpenRouter path (default) ──
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens or LLM_MAX_TOKENS,
        "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
    }

    req_timeout = timeout or 60

    try:
        resp = requests.post(OPENROUTER_URL, headers=HEADERS, json=payload, timeout=req_timeout)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        # Record LLM usage
        try:
            from rate_limiter import get_llm_user, record_llm_call
            uid, source = _user_id, _source
            if not uid:
                uid, source = get_llm_user()
            if uid:
                record_llm_call(uid, source or "api", model)
        except Exception:
            pass
        return content
    except requests.exceptions.Timeout:
        return json.dumps({"error": "LLM request timed out. Try again."})
    except requests.exceptions.RequestException as e:
        return json.dumps({"error": f"OpenRouter API error: {str(e)}"})
    except (KeyError, IndexError) as e:
        return json.dumps({"error": f"Unexpected API response: {str(e)}"})


# ─── Context-Specific Data Summaries ─────────────────────────────

def _build_data_summary(analysis: dict, context: str = "full") -> str:
    """Build a compact text summary of the analysis data for LLM context.

    context options:
      - "research": Company info, sector, price, breakout (skip OHLCV bars, trendline R², swing counts)
      - "pattern": Price action, indicators, pattern details, trendlines (skip company, sector, market cap)
      - "prediction": Current price, pattern targets, trade plan, indicators (skip individual OHLCV bars)
      - "full": Everything (legacy behavior)
    """
    info = analysis.get("info", {})
    pattern = analysis.get("pattern")
    indicators = analysis.get("indicators", {})
    breakout = analysis.get("breakout_status", {})
    trade_plan = analysis.get("trade_plan")
    recent_news = analysis.get("recent_news") or []  # list[str], 24h headlines
    active_catalysts = analysis.get("active_catalysts") or []  # high-signal bullish news

    parts = []

    # Company info (skip for pattern context)
    if context != "pattern":
        parts.append(f"""STOCK: {analysis['ticker']}
Company: {info.get('name', 'N/A')}
Sector: {info.get('sector', 'N/A')} | Industry: {info.get('industry', 'N/A')}
Exchange: {info.get('exchange', 'N/A')}
Market Cap: ${info.get('market_cap', 0):,.0f}""")
    else:
        parts.append(f"STOCK: {analysis['ticker']}")

    # Price info (always included)
    parts.append(f"""CURRENT PRICE: ${analysis['current_price']}
Change: {analysis['change']} ({analysis['change_pct']}%)""")

    # Indicators (skip for research context)
    if context != "research":
        parts.append(f"""TECHNICAL INDICATORS:
  RSI(14): {indicators.get('rsi_14', 'N/A')}
  MACD: {indicators.get('macd', 'N/A')} | Signal: {indicators.get('macd_signal', 'N/A')} | Histogram: {indicators.get('macd_histogram', 'N/A')}
  EMA 9: {indicators.get('ema_9', 'N/A')} | EMA 21: {indicators.get('ema_21', 'N/A')} | SMA 50: {indicators.get('sma_50', 'N/A')}
  ATR(14): {indicators.get('atr_14', 'N/A')} | ADR%: {indicators.get('adr_pct', 'N/A')}%
  Volume: {indicators.get('volume', 'N/A')} | Vol MA(10): {indicators.get('vol_ma_10', 'N/A')} | Rel Vol: {indicators.get('relative_volume', 'N/A')}x""")

    # Recent price action (only for pattern context)
    if context in ("pattern", "full"):
        ohlcv = analysis.get("ohlcv", [])
        recent_bars = ohlcv[-10:] if len(ohlcv) >= 10 else ohlcv
        price_summary = "\n".join([
            f"  {b['time']}: O={b['open']} H={b['high']} L={b['low']} C={b['close']}"
            for b in recent_bars
        ])
        parts.append(f"RECENT PRICE ACTION (last {len(recent_bars)} bars):\n{price_summary}")

    # Pattern info
    if pattern:
        pattern_text = f"""DETECTED PATTERN: {pattern['type'].replace('_', ' ').title()}
  Confidence Score: {pattern['score']}%
  Breakout Level: ${pattern['breakout_level']}
  Support Level: ${pattern['support_level']}
  Price Target: ${pattern['price_target']}
  Stop Loss: ${pattern['stop_loss']}
  Pattern Height: ${pattern['pattern_height']}
  Risk/Reward: 1:{pattern['risk_reward_ratio']}"""

        # Trendline R² and swing counts only for pattern/full context
        if context in ("pattern", "full"):
            pattern_text += f"""
  Trendline R² (Upper): {pattern['upper_trendline'].get('r_squared', 'N/A')}
  Trendline R² (Lower): {pattern['lower_trendline'].get('r_squared', 'N/A')}
  Swing Highs: {len(pattern.get('swing_highs', []))} points
  Swing Lows: {len(pattern.get('swing_lows', []))} points"""

        parts.append(pattern_text)

    # Breakout status
    parts.append(f"""BREAKOUT STATUS: {breakout.get('status', 'N/A')}
  {breakout.get('message', '')}""")

    # Trade plan (skip for research context)
    if trade_plan and context != "research":
        target = trade_plan.get('price_target') or trade_plan.get('tp2') or trade_plan.get('tp1', 'N/A')
        parts.append(f"""PROPOSED TRADE PLAN:
  Entry: ${trade_plan.get('entry_price', 'N/A')}
  Target: ${target}
  Stop Loss: ${trade_plan.get('stop_loss', 'N/A')}
  Risk/Reward: 1:{trade_plan.get('risk_reward_ratio', 'N/A')}
  Position Size: {trade_plan.get('position_size_shares', 'N/A')} shares (${trade_plan.get('position_value', 0):,.0f})
  Risk Amount: ${trade_plan.get('risk_amount', 'N/A')} (1.5% of $25K)
  Setup Grade: {trade_plan.get('grade', 'N/A')}""")

    # Active catalysts — high-signal bullish news (earnings beats, guidance raises,
    # FDA approvals, M&A, contract wins). Surfaced separately so the model weighs them
    # ahead of generic headlines. Empty when nothing material in the window.
    if active_catalysts and context in ("research", "prediction", "full"):
        cat_block = "\n".join(f"  - {c}" for c in active_catalysts[:5])
        parts.append(f"ACTIVE CATALYSTS (last 24h, high-signal bullish news):\n{cat_block}")

    # Recent news headlines (last 24h, included for research + prediction contexts).
    # Helps the LLM weigh catalysts vs noise — e.g., earnings beat, M&A, FDA, sector
    # rotation. Capped at 8 lines to keep token cost low.
    if recent_news and context in ("research", "prediction", "full"):
        news_block = "\n".join(f"  - {h}" for h in recent_news[:8])
        parts.append(f"RECENT NEWS / SOCIAL (last 24h):\n{news_block}")

    return "\n\n".join(parts).strip()


# ─── Validate Setup (3-Model Short-Term) ─────────────────────────

def _validate_research(data_summary: str, ticker: str, fast_mode: bool = False) -> dict:
    """Model 1: Fundamental research — risk-first, concise."""
    model = LLM_RESEARCH_FAST if fast_mode else LLM_RESEARCH
    role = "research_fast" if fast_mode else "research"
    messages = [
        {"role": "system", "content": RESEARCH_SYSTEM},
        {"role": "user", "content": RESEARCH_USER_TEMPLATE.format(ticker=ticker, data_summary=data_summary)},
    ]
    raw = _call_openrouter(model, messages, max_tokens=768, timeout=30, role=role)
    return _parse_json_response(raw, "research")


def _validate_pattern(data_summary: str, ticker: str) -> dict:
    """Model 2: Pattern validation — always uses Gemini Pro for accuracy."""
    messages = [
        {"role": "system", "content": PATTERN_SYSTEM},
        {"role": "user", "content": PATTERN_USER_TEMPLATE.format(ticker=ticker, data_summary=data_summary)},
    ]
    raw = _call_openrouter(LLM_PATTERN, messages, max_tokens=768, timeout=30, role="pattern")
    return _parse_json_response(raw, "pattern")


def _validate_prediction(data_summary: str, ticker: str) -> dict:
    """Model 3: Prediction & risk — pressure points, stop loss, scenarios."""
    messages = [
        {"role": "system", "content": PREDICTION_SYSTEM},
        {"role": "user", "content": PREDICTION_USER_TEMPLATE.format(ticker=ticker, data_summary=data_summary)},
    ]
    raw = _call_openrouter(LLM_PREDICTION, messages, max_tokens=1024, timeout=45, role="prediction")
    return _parse_json_response(raw, "prediction")


# ─── Risk Gates & Final Verdict ──────────────────────────────────

def _build_final_verdict(research: dict, pattern: dict, prediction: dict) -> dict:
    """Synthesize all 3 analyses into a final verdict with risk gates."""
    scores = []
    verdicts = []
    risk_flags = []

    # ── Risk Gates (checked before scoring) ──
    risk_score = prediction.get("risk_score", 0)
    if risk_score >= 75:
        risk_flags.append("Extreme risk score ({})".format(risk_score))

    false_bo = pattern.get("false_breakout_risk", "")
    if false_bo == "HIGH":
        risk_flags.append("HIGH false breakout risk")

    if not pattern.get("error") and not pattern.get("pattern_valid", True):
        risk_flags.append("Pattern rejected by AI")

    red_flags = research.get("red_flags", [])
    if len(red_flags) >= 3:
        risk_flags.append("{} red flags identified".format(len(red_flags)))

    # Force AVOID if 2+ risk gates triggered
    force_avoid = len(risk_flags) >= 2

    # ── Scoring ──
    # Research score
    r_conf = research.get("confidence", 0)
    r_verdict = research.get("verdict", "NEUTRAL")
    if not research.get("error"):
        scores.append(r_conf)
        verdicts.append(r_verdict)

    # Pattern score
    p_conf = pattern.get("pattern_confidence", 0)
    p_valid = pattern.get("pattern_valid", False)
    if not pattern.get("error"):
        scores.append(p_conf if p_valid else max(0, p_conf - 30))
        verdicts.append("BULLISH" if p_valid and p_conf >= 60 else "AVOID")

    # Prediction score
    pr_prob = prediction.get("overall_probability", 0)
    pr_verdict = prediction.get("trade_verdict", "SKIP")
    if not prediction.get("error"):
        scores.append(pr_prob)
        verdicts.append("BULLISH" if pr_verdict == "TAKE" else "AVOID" if pr_verdict == "SKIP" else "NEUTRAL")

    avg_score = sum(scores) / len(scores) if scores else 0
    bullish_count = sum(1 for v in verdicts if v == "BULLISH")
    avoid_count = sum(1 for v in verdicts if v == "AVOID")

    if force_avoid:
        final = "AVOID"
        color = "red"
    elif avg_score >= 70 and bullish_count >= 2:
        final = "STRONG BUY"
        color = "green"
    elif avg_score >= 55 and bullish_count >= 1 and avoid_count == 0:
        final = "BUY"
        color = "green"
    elif avoid_count >= 2 or avg_score < 35:
        final = "AVOID"
        color = "red"
    elif prediction.get("trade_verdict") == "WAIT":
        final = "WAIT"
        color = "yellow"
    else:
        final = "NEUTRAL"
        color = "yellow"

    return {
        "final_verdict": final,
        "color": color,
        "composite_score": round(avg_score, 1),
        "research_score": r_conf,
        "pattern_score": p_conf,
        "prediction_score": pr_prob,
        "models_agreeing": bullish_count,
        "models_warning": avoid_count,
        "total_models": len(verdicts),
        "risk_flags": risk_flags,
    }


# ─── 12-Month Prediction (3-Model) ──────────────────────────────

def _build_fundamentals_summary(fundamentals: dict) -> str:
    """Build a compact text summary of fundamental data for 12-month LLM analysis."""
    f = fundamentals
    val = f.get("valuation", {})
    prof = f.get("profitability", {})
    bs = f.get("balance_sheet", {})
    cf = f.get("cash_flow", {})
    risk = f.get("risk", {})
    analyst = f.get("analyst", {})
    derived = f.get("derived", {})
    trends = f.get("trends", {})

    def fmt(v, pct=False, prefix="", suffix=""):
        if v is None:
            return "N/A"
        if pct:
            return f"{v*100:.1f}%" if isinstance(v, float) and abs(v) < 10 else f"{v:.1f}%"
        if isinstance(v, (int, float)) and abs(v) >= 1e9:
            return f"{prefix}${v/1e9:.1f}B{suffix}"
        if isinstance(v, (int, float)) and abs(v) >= 1e6:
            return f"{prefix}${v/1e6:.0f}M{suffix}"
        return f"{prefix}{v}{suffix}"

    def fmt_trend(d):
        if not d:
            return "N/A"
        items = sorted(d.items())
        return " -> ".join(f"{y}: {fmt(v)}" for y, v in items if v is not None)

    summary = f"""STOCK: {f.get('ticker', 'N/A')}
Company: {f.get('name', 'N/A')}
Sector: {f.get('sector', 'N/A')} | Industry: {f.get('industry', 'N/A')}
Market Cap: {fmt(f.get('market_cap'))}
Current Price: ${f.get('current_price', 'N/A')}

VALUATION:
  P/E (Trailing): {fmt(val.get('pe_trailing'))} | P/E (Forward): {fmt(val.get('pe_forward'))}
  PEG Ratio: {fmt(val.get('peg_ratio'))} | P/B: {fmt(val.get('price_to_book'))} | P/S: {fmt(val.get('price_to_sales'))}
  EV/EBITDA: {fmt(val.get('ev_to_ebitda'))} | EV/Revenue: {fmt(val.get('ev_to_revenue'))}

PROFITABILITY:
  Revenue Growth: {fmt(prof.get('revenue_growth'), pct=True)}
  Gross Margin: {fmt(prof.get('gross_margin'), pct=True)} | Operating Margin: {fmt(prof.get('operating_margin'), pct=True)} | Net Margin: {fmt(prof.get('net_margin'), pct=True)}
  ROE: {fmt(prof.get('roe'), pct=True)} | ROA: {fmt(prof.get('roa'), pct=True)}

BALANCE SHEET:
  Cash: {fmt(bs.get('total_cash'))} | Debt: {fmt(bs.get('total_debt'))}
  D/E Ratio: {fmt(bs.get('debt_to_equity'))} | Current Ratio: {fmt(bs.get('current_ratio'))}
  Book Value/Share: {fmt(bs.get('book_value'))}

CASH FLOW:
  Operating CF: {fmt(cf.get('operating_cf'))} | Free CF: {fmt(cf.get('free_cf'))}

DERIVED METRICS:
  Cash-to-Debt: {fmt(derived.get('cash_to_debt'))} | FCF Yield: {fmt(derived.get('fcf_yield_pct'), pct=True)}
  Burning Cash: {'YES' if derived.get('is_burning_cash') else 'No'}"""

    if derived.get('is_burning_cash'):
        summary += f"""
  Burn Rate: {fmt(derived.get('burn_rate_monthly'))}/month | Cash Runway: {fmt(derived.get('cash_runway_months'))} months"""

    summary += f"""
  Debt Coverage: {fmt(derived.get('debt_coverage'))}

RISK:
  Beta: {fmt(risk.get('beta'))} | Short Ratio: {fmt(risk.get('short_ratio'))}
  52-Week Range: ${risk.get('fifty_two_week_low', 'N/A')} - ${risk.get('fifty_two_week_high', 'N/A')}

ANALYST CONSENSUS:
  Recommendation: {analyst.get('recommendation', 'N/A')} ({analyst.get('num_analysts', 0)} analysts)
  Target: Low ${analyst.get('target_low', 'N/A')} | Mean ${analyst.get('target_mean', 'N/A')} | High ${analyst.get('target_high', 'N/A')}"""

    if trends:
        summary += "\n\nFINANCIAL TRENDS (Annual):"
        for key, data in trends.items():
            summary += f"\n  {key.replace('_', ' ').title()}: {fmt_trend(data)}"

    return summary


def _build_price_action_summary(indicators: dict, df) -> str:
    """Build compact technical/price action summary for LLM context."""
    if not indicators or df is None or len(df) == 0:
        return "PRICE ACTION: No technical data available."

    close = df["Close"]
    current = float(close.iloc[-1])
    parts = []

    # Moving averages + positions
    sma8 = indicators.get("sma_8", 0)
    ema20 = indicators.get("ema_20", 0)
    sma50 = indicators.get("sma_50", 0)
    sma200 = indicators.get("sma_200", 0)
    ma_stack = []
    for label, val in [("SMA8", sma8), ("EMA20", ema20), ("SMA50", sma50), ("SMA200", sma200)]:
        pct = ((current - val) / val * 100) if val else 0
        ma_stack.append(f"{label}=${val:.2f} ({pct:+.1f}%)")
    parts.append("MOVING AVERAGES:\n  " + " | ".join(ma_stack))
    parts.append(f"  SMA8 {'above' if indicators.get('sma8_above_ema20') else 'below'} EMA20 | Cross: {'YES' if indicators.get('sma8_ema20_cross') else 'No'}")

    # RSI
    rsi = indicators.get("rsi_14", 0)
    rsi_zone = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
    parts.append(f"RSI(14): {rsi:.1f} ({rsi_zone})")

    # MACD
    macd = indicators.get("macd", 0)
    macd_sig = indicators.get("macd_signal", 0)
    macd_hist = indicators.get("macd_histogram", 0)
    macd_cross = "bullish cross" if indicators.get("macd_bullish_cross") else "bearish cross" if indicators.get("macd_bearish_cross") else "no cross"
    parts.append(f"MACD: {macd:.4f} | Signal: {macd_sig:.4f} | Hist: {macd_hist:.4f} | {macd_cross}")

    # Bollinger Bands
    bb_upper = indicators.get("bb_upper", 0)
    bb_lower = indicators.get("bb_lower", 0)
    bb_width = indicators.get("bb_width", 0)
    bb_pos = "above upper" if current > bb_upper else "below lower" if current < bb_lower else "within bands"
    parts.append(f"BOLLINGER BANDS: Upper=${bb_upper:.2f} Lower=${bb_lower:.2f} Width={bb_width:.1f}% | Price {bb_pos}")

    # ATR + volume
    atr = indicators.get("atr_14", 0)
    rel_vol = indicators.get("relative_volume", 0)
    parts.append(f"ATR(14): ${atr:.2f} ({atr/current*100:.1f}% of price) | Relative Volume: {rel_vol:.2f}x")

    # Last 5 candles
    last5 = df.tail(5)
    candles = []
    for _, row in last5.iterrows():
        date_str = str(row.name.date()) if hasattr(row.name, 'date') else str(row.name)
        candles.append(f"  {date_str}: O={row['Open']:.2f} H={row['High']:.2f} L={row['Low']:.2f} C={row['Close']:.2f} V={int(row['Volume']):,}")
    parts.append("LAST 5 CANDLES:\n" + "\n".join(candles))

    # Trend structure
    above_50 = current > sma50
    above_200 = current > sma200
    trend = "strong uptrend" if above_50 and above_200 and sma50 > sma200 else \
            "uptrend" if above_200 else \
            "downtrend" if not above_50 and not above_200 else "mixed"
    parts.append(f"TREND: {trend} (Price {'>' if above_50 else '<'} SMA50 {'>' if above_200 else '<'} SMA200)")

    return "\n".join(parts)


def _gather_facts(fundamentals_summary: str, price_action_summary: str, ticker: str, fast_mode: bool = False) -> dict:
    """LLM 1 (Fact Gatherer): Gather and organize all relevant facts about the company."""
    model = LLM_RESEARCH_FAST if fast_mode else LLM_RESEARCH
    role = "research_fast" if fast_mode else "research"
    messages = [
        {"role": "system", "content": FACT_GATHERER_SYSTEM},
        {"role": "user", "content": FACT_GATHERER_USER_TEMPLATE.format(ticker=ticker, fundamentals_summary=fundamentals_summary, price_action_summary=price_action_summary)},
    ]
    raw = _call_openrouter(model, messages, max_tokens=1200, timeout=45, role=role)
    return _parse_json_response(raw, "fact_gatherer")


def _evaluate_company_health(fundamentals_summary: str, fact_gatherer_output: dict, ticker: str, fast_mode: bool = False) -> dict:
    """LLM 2 (Company Health): Financial health + moat assessment, informed by fact gatherer."""
    model = LLM_RESEARCH_FAST if fast_mode else LLM_PATTERN
    role = "research_fast" if fast_mode else "pattern"
    facts_context = json.dumps({k: v for k, v in fact_gatherer_output.items() if not k.startswith("_")}, default=str)[:1500]
    messages = [
        {"role": "system", "content": COMPANY_HEALTH_SYSTEM},
        {"role": "user", "content": COMPANY_HEALTH_USER_TEMPLATE.format(ticker=ticker, fundamentals_summary=fundamentals_summary, facts_context=facts_context)},
    ]
    raw = _call_openrouter(model, messages, max_tokens=1200, timeout=45, role=role)
    return _parse_json_response(raw, "company_health")


def _evaluate_price_action(price_action_summary: str, fundamentals_summary: str, fact_gatherer_output: dict, ticker: str) -> dict:
    """LLM 3 (Price Action): Valuation + technical analysis, informed by fact gatherer."""
    facts_context = json.dumps({k: v for k, v in fact_gatherer_output.items() if not k.startswith("_")}, default=str)[:1500]
    messages = [
        {"role": "system", "content": PRICE_ACTION_SYSTEM},
        {"role": "user", "content": PRICE_ACTION_USER_TEMPLATE.format(ticker=ticker, fundamentals_summary=fundamentals_summary, price_action_summary=price_action_summary, facts_context=facts_context)},
    ]
    raw = _call_openrouter(LLM_PREDICTION, messages, max_tokens=1200, timeout=45, role="prediction")
    return _parse_json_response(raw, "price_action")


def _build_investment_verdict(facts: dict, health: dict, price: dict, supervisor: dict) -> dict:
    """Synthesize 4 model outputs into final INVEST/HOLD/PASS verdict with 3/4 quorum."""
    scores = []
    verdicts = []
    risk_flags = []

    # ── Risk Gates ──
    survival = health.get("survival_probability") or 90
    if health.get("error"):
        survival = 90
    dilution_risk = str(health.get("dilution_risk", "")).upper()

    if survival < 75:
        risk_flags.append("Survival probability {}% (below 75% threshold)".format(survival))
    if dilution_risk.startswith("HIGH"):
        risk_flags.append("HIGH dilution risk")

    force_pass = survival < 75 or dilution_risk.startswith("HIGH")

    # ── Scoring (4 voters) ──
    model_scores = {}
    for label, model_out in [("fact_gatherer", facts), ("company_health", health),
                              ("price_action", price), ("supervisor", supervisor)]:
        conf = model_out.get("confidence", 0)
        verdict = model_out.get("verdict", "HOLD").upper()
        if not model_out.get("error"):
            scores.append(conf)
            verdicts.append(verdict)
            model_scores[label] = conf

    avg_score = sum(scores) / len(scores) if scores else 0
    invest_count = sum(1 for v in verdicts if v == "INVEST")
    pass_count = sum(1 for v in verdicts if v == "PASS")

    # 3/4 quorum logic
    if force_pass:
        final = "PASS"
        color = "red"
    elif pass_count >= 2 or avg_score < 35:
        final = "PASS"
        color = "red"
    elif invest_count >= 3 and avg_score >= 55:
        final = "INVEST"
        color = "green"
    elif invest_count >= 2 and avg_score >= 45:
        final = "HOLD"
        color = "yellow"
    else:
        final = "HOLD"
        color = "yellow"

    price_targets = price.get("price_targets", {})

    return {
        "final_verdict": final,
        "color": color,
        "composite_score": round(avg_score, 1),
        "fact_gatherer_score": model_scores.get("fact_gatherer", 0),
        "health_score": model_scores.get("company_health", 0),
        "price_action_score": model_scores.get("price_action", 0),
        "supervisor_score": model_scores.get("supervisor", 0),
        "survival_probability": survival,
        "models_invest": invest_count,
        "models_pass": pass_count,
        "total_models": len(verdicts),
        "price_targets": price_targets,
        "fair_value": price.get("fair_value"),
        "upside_pct": price.get("upside_pct"),
        "downside_pct": price.get("downside_pct"),
        "risk_flags": risk_flags,
    }


# ─── Supervisor / Judge LLM ───────────────────────────────────────

def _validate_supervisor(context: str, prior_verdicts: dict, layer: str) -> dict:
    """4th LLM gate — Supervisor/Judge that can veto prior consensus.

    Only called when LLM_SUPERVISOR is configured (non-empty).
    Returns {override: bool, final_verdict, reasoning, confidence, risk_flags}.
    On error, returns {override: False} for graceful degradation.
    """
    if not LLM_SUPERVISOR:
        return {"override": False, "skipped": True}

    system_prompt = SUPERVISOR_PROMPTS.get(layer, SUPERVISOR_PROMPTS["setup"])

    verdicts_summary = json.dumps(prior_verdicts, indent=2, default=str)[:2000]

    user_msg = SUPERVISOR_USER_TEMPLATE.format(
        layer=layer,
        verdicts_summary=verdicts_summary,
        context=context[:2000],
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    try:
        raw = _call_openrouter(LLM_SUPERVISOR, messages, max_tokens=1024, timeout=45, role="supervisor")
        result = _parse_json_response(raw, "supervisor")
        result["model"] = LLM_SUPERVISOR
        return result
    except Exception as e:
        return {"override": False, "error": str(e), "model": LLM_SUPERVISOR}


# ─── JSON Parsing ────────────────────────────────────────────────

def _parse_json_response(raw: str, label: str) -> dict:
    """Parse JSON from LLM response, handling common formatting issues."""
    if not raw:
        return {"error": f"Empty response from {label} model", "_raw": ""}

    cleaned = raw.strip()

    # Strip markdown code fences
    if "```" in cleaned:
        import re
        match = re.search(r'```(?:json)?\s*\n([\s\S]*?)\n\s*```', cleaned)
        if match:
            cleaned = match.group(1).strip()
        else:
            lines = cleaned.split("\n")
            stripped = []
            inside = False
            for line in lines:
                if line.strip().startswith("```"):
                    inside = not inside
                    continue
                if inside or not line.strip().startswith("```"):
                    stripped.append(line)
            cleaned = "\n".join(stripped).strip()

    # Try direct parse
    try:
        parsed = json.loads(cleaned)
        parsed["_model_label"] = label
        return parsed
    except json.JSONDecodeError:
        pass

    # Try to extract JSON object by finding matching braces
    start = cleaned.find("{")
    if start >= 0:
        depth = 0
        end = start
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            try:
                parsed = json.loads(cleaned[start:end])
                parsed["_model_label"] = label
                return parsed
            except json.JSONDecodeError:
                pass

    # Last resort: try fixing common issues
    try:
        import re
        fixed = cleaned[cleaned.find("{"):cleaned.rfind("}") + 1]
        fixed = re.sub(r',\s*}', '}', fixed)
        fixed = re.sub(r',\s*]', ']', fixed)
        parsed = json.loads(fixed)
        parsed["_model_label"] = label
        return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    return {
        "error": f"Could not parse {label} model response as JSON",
        "_raw": raw[:500],
        "_model_label": label,
    }


# ─── Public API ──────────────────────────────────────────────────

def validate_setup(analysis: dict, fast_mode: bool = None) -> dict:
    """
    Run all 3 LLM validations in parallel and synthesize results.
    Returns comprehensive AI verdict with risk gates.
    """
    if not is_configured():
        return {
            "configured": False,
            "error": "OpenRouter API key not configured. Add your key to .env file.",
        }

    use_fast = fast_mode if fast_mode is not None else LLM_FAST_MODE
    ticker = analysis.get("ticker", "UNKNOWN")

    # Capture user context from calling thread to propagate into executor threads
    from rate_limiter import get_llm_user, set_llm_user
    _ctx_uid, _ctx_src = get_llm_user()

    def _run_with_context(fn, *args):
        if _ctx_uid:
            set_llm_user(_ctx_uid, _ctx_src)
        return fn(*args)

    # Build context-specific summaries to reduce tokens
    research_summary = _build_data_summary(analysis, context="research")
    pattern_summary = _build_data_summary(analysis, context="pattern")
    prediction_summary = _build_data_summary(analysis, context="prediction")

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_research = executor.submit(_run_with_context, _validate_research, research_summary, ticker, use_fast)
        future_pattern = executor.submit(_run_with_context, _validate_pattern, pattern_summary, ticker)
        future_prediction = executor.submit(_run_with_context, _validate_prediction, prediction_summary, ticker)

        research = future_research.result()
        pattern = future_pattern.result()
        prediction = future_prediction.result()

    verdict = _build_final_verdict(research, pattern, prediction)

    # Supervisor gate — only if configured
    supervisor = None
    if LLM_SUPERVISOR and verdict["final_verdict"] not in ("AVOID",):
        supervisor = _validate_supervisor(
            research_summary[:1000],
            {"research": research.get("verdict"), "pattern_valid": pattern.get("pattern_valid"),
             "prediction": prediction.get("trade_verdict"), "final": verdict["final_verdict"],
             "composite_score": verdict["composite_score"]},
            layer="setup",
        )
        if supervisor.get("override") is True:
            verdict["final_verdict"] = "AVOID"
            verdict["color"] = "red"
            verdict["risk_flags"].append(f"Supervisor veto: {supervisor.get('reasoning', 'N/A')}")

    research_model = LLM_RESEARCH_FAST if use_fast else LLM_RESEARCH
    result = {
        "configured": True,
        "ticker": ticker,
        "timestamp": datetime.now().isoformat(),
        "models": {
            "research": research_model,
            "pattern": LLM_PATTERN,
            "prediction": LLM_PREDICTION,
        },
        "research": research,
        "pattern": pattern,
        "prediction": prediction,
        "verdict": verdict,
    }
    if supervisor:
        result["supervisor"] = supervisor
    return result


def _supervisor_review(facts: dict, health: dict, price: dict, ticker: str, fast_mode: bool = False) -> dict:
    """LLM 4 (Supervisor): Reviews all prior model outputs and casts own vote."""
    model = LLM_SUPERVISOR if LLM_SUPERVISOR else (LLM_RESEARCH_FAST if fast_mode else LLM_RESEARCH)
    role = "supervisor" if LLM_SUPERVISOR else ("research_fast" if fast_mode else "research")
    prior_summary = json.dumps({
        "fact_gatherer": {k: v for k, v in facts.items() if not k.startswith("_")},
        "company_health": {k: v for k, v in health.items() if not k.startswith("_")},
        "price_action": {k: v for k, v in price.items() if not k.startswith("_")},
    }, default=str)[:3000]

    messages = [
        {"role": "system", "content": "You are a senior investment supervisor. Your job is to review all prior analysis, catch errors or overconfidence, identify missed risks, and cast your own independent vote. Respond with ONLY valid JSON. No markdown, no code fences."},
        {"role": "user", "content": f"""Review the complete analysis of {ticker} from 3 prior models. Cast your own INVEST/HOLD/PASS vote.

PRIOR MODEL OUTPUTS:
{prior_summary}

As the final reviewer, check for:
1. Overconfidence or hopium in any model
2. Missed macro/sector risks
3. Contradictions between models
4. Whether the evidence actually supports the verdicts given

Respond with ONLY this JSON:
{{"verdict":"INVEST","confidence":70,"agrees_with_health":true,"agrees_with_price_action":true,"override_flags":["any critical issue that should force a different verdict"],"reasoning":"2-3 sentences explaining your independent assessment","risk_flags":["risk1"],"summary":"1 sentence final take"}}"""}
    ]
    raw = _call_openrouter(model, messages, max_tokens=1024, timeout=45, role=role)
    return _parse_json_response(raw, "supervisor")


def predict_12month(fundamentals: dict, indicators: dict = None, df=None, fast_mode: bool = None) -> dict:
    """
    Run 12-month investment prediction using 4 LLMs.
    Phase 1: Fact Gatherer (sequential)
    Phase 2: Company Health + Price Action (parallel)
    Phase 3: Supervisor Review (sequential)
    Returns comprehensive investment verdict with INVEST/HOLD/PASS (3/4 quorum).
    """
    if not is_configured():
        return {
            "configured": False,
            "error": "OpenRouter API key not configured. Add your key to .env file.",
        }

    use_fast = fast_mode if fast_mode is not None else LLM_FAST_MODE
    ticker = fundamentals.get("ticker", "UNKNOWN")
    fundamentals_summary = _build_fundamentals_summary(fundamentals)
    price_action_summary = _build_price_action_summary(indicators or {}, df)

    # Capture thread-local LLM user context for propagation into child threads
    from rate_limiter import get_llm_user, set_llm_user
    _ctx_uid, _ctx_src = get_llm_user()

    def _run_with_context(fn, *args):
        if _ctx_uid:
            set_llm_user(_ctx_uid, _ctx_src)
        return fn(*args)

    # Phase 1: Fact Gatherer (sequential — feeds into Phase 2)
    facts = _gather_facts(fundamentals_summary, price_action_summary, ticker, use_fast)

    # Phase 2: Company Health + Price Action (parallel)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_health = executor.submit(_run_with_context, _evaluate_company_health, fundamentals_summary, facts, ticker, use_fast)
        future_price = executor.submit(_run_with_context, _evaluate_price_action, price_action_summary, fundamentals_summary, facts, ticker)

        health = future_health.result()
        price = future_price.result()

    # Phase 3: Supervisor Review (sequential — reviews all prior outputs)
    supervisor = _run_with_context(_supervisor_review, facts, health, price, ticker, use_fast)

    # Build verdict with 3/4 quorum from all 4 models
    verdict = _build_investment_verdict(facts, health, price, supervisor)

    fact_model = LLM_RESEARCH_FAST if use_fast else LLM_RESEARCH
    health_model = LLM_RESEARCH_FAST if use_fast else LLM_PATTERN
    supervisor_model = LLM_SUPERVISOR if LLM_SUPERVISOR else fact_model
    result = {
        "configured": True,
        "ticker": ticker,
        "timestamp": datetime.now().isoformat(),
        "models": {
            "fact_gatherer": fact_model,
            "company_health": health_model,
            "price_action": LLM_PREDICTION,
            "supervisor": supervisor_model,
        },
        "fundamentals_snapshot": {
            "pe_forward": fundamentals.get("valuation", {}).get("pe_forward"),
            "fcf_yield": fundamentals.get("derived", {}).get("fcf_yield_pct"),
            "de_ratio": fundamentals.get("balance_sheet", {}).get("debt_to_equity"),
            "revenue_growth": fundamentals.get("profitability", {}).get("revenue_growth"),
            "is_burning": fundamentals.get("derived", {}).get("is_burning_cash"),
            "cash_runway": fundamentals.get("derived", {}).get("cash_runway_months"),
        },
        "fact_gatherer": facts,
        "company_health": health,
        "price_action": price,
        "supervisor": supervisor,
        "verdict": verdict,
    }
    return result
