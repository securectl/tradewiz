"""
AI Validation Engine — Multi-LLM Rigorous Trade Setup Vetting
Uses 3 specialized LLMs via OpenRouter to validate every setup from every angle.
Risk management is the #1 priority. Capital preservation above all.

Model 1 (Research):    Fundamentals, sector, red flags, catalysts
Model 2 (Pattern):     Chart pattern validation, breakout probability
Model 3 (Prediction):  Price targets, risk modeling, stop loss, scenarios
"""

import os
import json
import requests
import concurrent.futures
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

LLM_RESEARCH = os.getenv("LLM_RESEARCH", "anthropic/claude-sonnet-4-6")
LLM_RESEARCH_FAST = os.getenv("LLM_RESEARCH_FAST", "google/gemini-2.5-flash")
LLM_PATTERN = os.getenv("LLM_PATTERN", "google/gemini-2.5-pro-preview")
LLM_PREDICTION = os.getenv("LLM_PREDICTION", "deepseek/deepseek-chat-v3-0324")
LLM_SCREENER = os.getenv("LLM_SCREENER", "google/gemini-2.5-flash")
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
                     max_tokens: int = None, timeout: int = None) -> str:
    """Make a single call to OpenRouter with per-call token/timeout control."""
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
        return data["choices"][0]["message"]["content"]
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

    return "\n\n".join(parts).strip()


# ─── Validate Setup (3-Model Short-Term) ─────────────────────────

def _validate_research(data_summary: str, ticker: str, fast_mode: bool = False) -> dict:
    """Model 1: Fundamental research — risk-first, concise."""
    model = LLM_RESEARCH_FAST if fast_mode else LLM_RESEARCH
    messages = [
        {"role": "system", "content": "You are a risk-first stock analyst. Capital preservation is #1. Respond with ONLY valid JSON. No markdown, no code fences."},
        {"role": "user", "content": f"""Analyze {ticker} setup. Find reasons this trade could FAIL. Never risk >5% account on any position.

{data_summary}

Respond with ONLY this JSON (replace values):
{{"verdict":"BULLISH","confidence":75,"catalysts_bullish":["catalyst1"],"catalysts_bearish":["risk1"],"red_flags":["flag1"],"earnings_risk":"assessment","key_risk":"biggest single risk","summary":"1-2 sentences, risk-first"}}"""}
    ]
    raw = _call_openrouter(model, messages, max_tokens=768, timeout=30)
    return _parse_json_response(raw, "research")


def _validate_pattern(data_summary: str, ticker: str) -> dict:
    """Model 2: Pattern validation — always uses Gemini Pro for accuracy."""
    messages = [
        {"role": "system", "content": "You are a technical analyst. False breakouts destroy accounts. Respond with ONLY valid JSON. No markdown, no code fences."},
        {"role": "user", "content": f"""Validate or reject this pattern for {ticker}. Be ruthless — false breakouts destroy accounts.

{data_summary}

Respond with ONLY this JSON (replace values):
{{"pattern_valid":true,"pattern_confidence":75,"detected_pattern":"Symmetrical Triangle","breakout_probability":70,"false_breakout_risk":"MEDIUM","false_breakout_reasons":["reason1"],"support_resistance_key_levels":["$100","$95"],"optimal_entry":"price and condition","invalidation_level":"$X","summary":"1-2 sentences"}}"""}
    ]
    raw = _call_openrouter(LLM_PATTERN, messages, max_tokens=768, timeout=30)
    return _parse_json_response(raw, "pattern")


def _validate_prediction(data_summary: str, ticker: str) -> dict:
    """Model 3: Prediction & risk — pressure points, stop loss, scenarios."""
    messages = [
        {"role": "system", "content": "You are a quantitative risk strategist. Cut losses at stop-loss, no exceptions, no averaging down. Respond with ONLY valid JSON. No markdown, no code fences."},
        {"role": "user", "content": f"""$25K account. Analyze risk/reward for {ticker}. Don't marry the trade.

{data_summary}

PRESSURE POINTS to check: Options/gamma/OI, Earnings proximity, Ex-div, Fed/FOMC, Sector rotation, Liquidity, Gap risk, Volatility regime

Respond with ONLY this JSON (replace values):
{{"trade_verdict":"TAKE","overall_probability":70,"price_targets":{{"conservative":100,"moderate":110,"aggressive":120}},"stop_loss":{{"valid":true,"recommended":90,"reason":"reason"}},"risk_score":40,"pressure_points":[{{"factor":"name","impact":"HIGH","detail":"explanation"}}],"position_size_ok":true,"scenarios":{{"bull_case":"scenario (prob%)","base_case":"scenario (prob%)","bear_case":"scenario (prob%)"}},"expected_value_per_trade":150,"summary":"1-2 sentences, risk-first"}}"""}
    ]
    raw = _call_openrouter(LLM_PREDICTION, messages, max_tokens=1024, timeout=45)
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


def _predict_business_viability(fundamentals_summary: str, ticker: str, fast_mode: bool = False) -> dict:
    """LLM 1: Business viability — moat, sector outlook, growth durability."""
    model = LLM_RESEARCH_FAST if fast_mode else LLM_RESEARCH
    messages = [
        {"role": "system", "content": "You are a risk-first investment analyst. Capital preservation is #1. No hopium, no emotional language. Respond with ONLY valid JSON. No markdown, no code fences."},
        {"role": "user", "content": f"""Evaluate {ticker} BUSINESS VIABILITY for 12-month hold. Protect capital. Focus on survival and downside risk first, then upside.

{fundamentals_summary}

Respond with ONLY this JSON:
{{"verdict":"INVEST","confidence":70,"moat_score":65,"moat_assessment":"1 sentence","sector_outlook":"1 sentence","growth_durability":"1 sentence","competitive_threats":["threat1"],"catalysts_12m":["catalyst1"],"bear_thesis":"why this could fail","bull_thesis":"why this could succeed","revenue_trajectory":"growing/flat/declining","summary":"1-2 sentences, risk-first"}}"""}
    ]
    raw = _call_openrouter(model, messages, max_tokens=1024, timeout=45)
    return _parse_json_response(raw, "business_viability")


def _predict_financial_health(fundamentals_summary: str, ticker: str, fast_mode: bool = False) -> dict:
    """LLM 2: Financial health — burn rate, cash runway, debt, survival probability."""
    model = LLM_RESEARCH_FAST if fast_mode else LLM_PATTERN
    messages = [
        {"role": "system", "content": "You are a forensic financial analyst. Focus on survival probability and dilution risk. No emotional language. Respond with ONLY valid JSON. No markdown, no code fences."},
        {"role": "user", "content": f"""Evaluate {ticker} FINANCIAL HEALTH for 12-month hold. Is this company financially safe? Focus on: cash burn, debt servicing, dilution risk, bankruptcy risk.

{fundamentals_summary}

IMPORTANT: Be realistic about survival probability. Most established profitable companies have >90% survival probability. Only assign <75% for companies actively burning cash with <12 months runway, facing imminent debt defaults, or in active restructuring.

Respond with ONLY this JSON:
{{"verdict":"INVEST","confidence":70,"survival_probability":85,"financial_grade":"A","cash_position":"strong/adequate/weak/critical","burn_assessment":"1 sentence if burning","debt_risk":"LOW/MEDIUM/HIGH","fcf_trajectory":"improving/stable/declining","dilution_risk":"LOW/MEDIUM/HIGH","revenue_quality":"1 sentence","red_flags":["flag1"],"green_flags":["flag1"],"summary":"1-2 sentences, risk-first"}}"""}
    ]
    raw = _call_openrouter(model, messages, max_tokens=1200, timeout=45)
    return _parse_json_response(raw, "financial_health")


def _predict_valuation_price(fundamentals_summary: str, ticker: str) -> dict:
    """LLM 3: Valuation & price target — DCF-lite, peer comparison, 12m targets."""
    messages = [
        {"role": "system", "content": "You are a quantitative valuation analyst. No hopium. Respond with ONLY valid JSON. No markdown, no code fences."},
        {"role": "user", "content": f"""Calculate FAIR VALUE and 12-MONTH PRICE TARGETS for {ticker}. Focus on margin of safety and downside risk.

{fundamentals_summary}

Respond with ONLY this JSON:
{{"verdict":"INVEST","confidence":70,"fair_value":0.00,"current_vs_fair":"undervalued/fairly valued/overvalued","margin_of_safety_pct":15,"price_targets":{{"bear":0.00,"bear_probability":20,"base":0.00,"base_probability":55,"bull":0.00,"bull_probability":25}},"upside_pct":25,"downside_pct":15,"peer_comparison":"1 sentence","valuation_assessment":"1 sentence","dcf_notes":"key assumptions","entry_attractiveness":"attractive/fair/wait","catalysts_for_rerating":["catalyst1"],"summary":"1-2 sentences, risk-first"}}"""}
    ]
    raw = _call_openrouter(LLM_PREDICTION, messages, max_tokens=1024, timeout=45)
    return _parse_json_response(raw, "valuation_price")


def _build_investment_verdict(biz: dict, health: dict, val: dict) -> dict:
    """Synthesize 3 investment model outputs into final INVEST/HOLD/PASS verdict with risk gates."""
    scores = []
    verdicts = []
    risk_flags = []

    # ── Risk Gates ──
    # Default survival to 90 if health model errored (don't penalize for LLM failure)
    survival = health.get("survival_probability") or 90
    if health.get("error"):
        survival = 90  # Don't force PASS on model errors
    dilution_risk = str(health.get("dilution_risk", "")).upper()

    if survival < 75:
        risk_flags.append("Survival probability {}% (below 75% threshold)".format(survival))

    if dilution_risk.startswith("HIGH"):
        risk_flags.append("HIGH dilution risk")

    # Force PASS on critical risk
    force_pass = survival < 75 or dilution_risk.startswith("HIGH")

    # ── Scoring ──
    biz_conf = biz.get("confidence", 0)
    biz_verdict = biz.get("verdict", "HOLD").upper()
    if not biz.get("error"):
        scores.append(biz_conf)
        verdicts.append(biz_verdict)

    health_conf = health.get("confidence", 0)
    health_verdict = health.get("verdict", "HOLD").upper()
    if not health.get("error"):
        scores.append(health_conf)
        verdicts.append(health_verdict)

    val_conf = val.get("confidence", 0)
    val_verdict = val.get("verdict", "HOLD").upper()
    if not val.get("error"):
        scores.append(val_conf)
        verdicts.append(val_verdict)

    avg_score = sum(scores) / len(scores) if scores else 0
    invest_count = sum(1 for v in verdicts if v == "INVEST")
    pass_count = sum(1 for v in verdicts if v == "PASS")

    if force_pass:
        final = "PASS"
        color = "red"
    elif pass_count >= 2 or avg_score < 35:
        final = "PASS"
        color = "red"
    elif invest_count >= 2 and avg_score >= 60:
        final = "INVEST"
        color = "green"
    elif invest_count >= 1 and avg_score >= 50:
        final = "HOLD"
        color = "yellow"
    else:
        final = "HOLD"
        color = "yellow"

    price_targets = val.get("price_targets", {})

    return {
        "final_verdict": final,
        "color": color,
        "composite_score": round(avg_score, 1),
        "business_score": biz_conf,
        "health_score": health_conf,
        "valuation_score": val_conf,
        "survival_probability": survival,
        "models_invest": invest_count,
        "models_pass": pass_count,
        "total_models": len(verdicts),
        "price_targets": price_targets,
        "fair_value": val.get("fair_value"),
        "upside_pct": val.get("upside_pct"),
        "downside_pct": val.get("downside_pct"),
        "risk_flags": risk_flags,
    }


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

    # Build context-specific summaries to reduce tokens
    research_summary = _build_data_summary(analysis, context="research")
    pattern_summary = _build_data_summary(analysis, context="pattern")
    prediction_summary = _build_data_summary(analysis, context="prediction")

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_research = executor.submit(_validate_research, research_summary, ticker, use_fast)
        future_pattern = executor.submit(_validate_pattern, pattern_summary, ticker)
        future_prediction = executor.submit(_validate_prediction, prediction_summary, ticker)

        research = future_research.result()
        pattern = future_pattern.result()
        prediction = future_prediction.result()

    verdict = _build_final_verdict(research, pattern, prediction)

    research_model = LLM_RESEARCH_FAST if use_fast else LLM_RESEARCH
    return {
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


def predict_12month(fundamentals: dict, fast_mode: bool = None) -> dict:
    """
    Run 12-month investment prediction using 3 LLMs in parallel.
    Returns comprehensive investment verdict with INVEST/HOLD/PASS.
    """
    if not is_configured():
        return {
            "configured": False,
            "error": "OpenRouter API key not configured. Add your key to .env file.",
        }

    use_fast = fast_mode if fast_mode is not None else LLM_FAST_MODE
    ticker = fundamentals.get("ticker", "UNKNOWN")
    fundamentals_summary = _build_fundamentals_summary(fundamentals)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_biz = executor.submit(_predict_business_viability, fundamentals_summary, ticker, use_fast)
        future_health = executor.submit(_predict_financial_health, fundamentals_summary, ticker, use_fast)
        future_val = executor.submit(_predict_valuation_price, fundamentals_summary, ticker)

        biz = future_biz.result()
        health = future_health.result()
        val = future_val.result()

    verdict = _build_investment_verdict(biz, health, val)

    biz_model = LLM_RESEARCH_FAST if use_fast else LLM_RESEARCH
    health_model = LLM_RESEARCH_FAST if use_fast else LLM_PATTERN
    return {
        "configured": True,
        "ticker": ticker,
        "timestamp": datetime.now().isoformat(),
        "models": {
            "business_viability": biz_model,
            "financial_health": health_model,
            "valuation_price": LLM_PREDICTION,
        },
        "fundamentals_snapshot": {
            "pe_forward": fundamentals.get("valuation", {}).get("pe_forward"),
            "fcf_yield": fundamentals.get("derived", {}).get("fcf_yield_pct"),
            "de_ratio": fundamentals.get("balance_sheet", {}).get("debt_to_equity"),
            "revenue_growth": fundamentals.get("profitability", {}).get("revenue_growth"),
            "is_burning": fundamentals.get("derived", {}).get("is_burning_cash"),
            "cash_runway": fundamentals.get("derived", {}).get("cash_runway_months"),
        },
        "business_viability": biz,
        "financial_health": health,
        "valuation_price": val,
        "verdict": verdict,
    }
