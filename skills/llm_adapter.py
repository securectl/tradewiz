"""
LLM adapter for skills — wraps ai_validator._call_openrouter() with skill-specific prompts.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

LLM_SKILL = os.getenv("LLM_SKILL", "nvidia/nemotron-3-super-120b-a12b")
LLM_SKILL_EARNINGS = os.getenv("LLM_SKILL_EARNINGS", "anthropic/claude-opus-4-6")


def _call_llm(prompt, system_prompt=None, user_id=None, source="skill",
              model=None, max_tokens=4096, temperature=0.2):
    """Make an LLM call through OpenRouter, reusing the existing infrastructure."""
    from ai_validator import _call_openrouter
    from rate_limiter import set_llm_user

    if user_id:
        set_llm_user(user_id, source)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    result = _call_openrouter(
        model=model or LLM_SKILL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        _user_id=user_id,
        _source=source,
    )
    return result


def analyze_financial_health(data, user_id=None):
    """LLM analysis of company financial health."""
    system = (
        "You are a senior financial analyst. Analyze the provided financial data and produce "
        "a comprehensive financial health assessment. Return valid JSON with keys: "
        "overall_score (1-10), overall_rating (Strong/Good/Fair/Weak/Critical), "
        "liquidity (dict with current_ratio_assessment, quick_ratio_note, score), "
        "solvency (dict with debt_equity_assessment, interest_coverage_note, score), "
        "profitability (dict with margin_assessment, roe_assessment, score), "
        "efficiency (dict with asset_turnover_note, score), "
        "growth (dict with revenue_trend, earnings_trend, score), "
        "red_flags (list of strings), strengths (list of strings), "
        "executive_snapshot (dict with: headline (str, 1 bold sentence — the key financial health conclusion), "
        "bullets (list of 3-5 strings, each ~50 words covering the most important findings), "
        "score (str, e.g. '7/10 — Good')), "
        "summary (2-3 sentence overall assessment)."
    )
    prompt = f"Analyze the financial health of this company:\n\n{json.dumps(data, indent=2, default=str)}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_health")


def run_dcf_analysis(data, user_id=None):
    """LLM-assisted DCF valuation analysis."""
    system = (
        "You are an expert equity analyst specializing in DCF valuation. Given the financial data, "
        "build a discounted cash flow model. Return valid JSON with keys: "
        "wacc_estimate (float), terminal_growth_rate (float), "
        "projected_fcf (list of {year, fcf, growth_rate} for 5 years), "
        "terminal_value (float), enterprise_value (float), equity_value (float), "
        "implied_share_price (float), current_price (float), "
        "upside_downside_pct (float), valuation_verdict (Undervalued/Fair/Overvalued), "
        "sensitivity_table (list of {wacc, growth, price} scenarios), "
        "key_assumptions (list of strings), risks (list of strings), "
        "executive_snapshot (dict with: headline (str, 1 bold sentence — fair value verdict + upside/downside), "
        "bullets (list of 3-5 strings, each ~50 words covering the most important valuation findings), "
        "verdict (str, e.g. 'Undervalued by 15% — Buy')), "
        "summary (2-3 sentence valuation conclusion)."
    )
    prompt = f"Perform a DCF valuation analysis:\n\n{json.dumps(data, indent=2, default=str)}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_dcf", max_tokens=6000)


def run_comparable_analysis(data, user_id=None):
    """LLM-assisted comparable company analysis."""
    system = (
        "You are an investment banking analyst. Perform a comparable company analysis. "
        "Return valid JSON with keys: "
        "target_ticker (str), peer_tickers (list), "
        "multiples_comparison (list of {ticker, pe, ev_ebitda, ps, pb, ev_revenue}), "
        "median_multiples (dict with pe, ev_ebitda, ps, pb), "
        "implied_values (dict with based_on_pe, based_on_ev_ebitda, based_on_ps), "
        "average_implied_price (float), current_price (float), "
        "premium_discount_pct (float), "
        "relative_verdict (Premium/Discount/In-line), "
        "quality_adjustments (list of strings explaining why premium/discount is justified), "
        "executive_snapshot (dict with: headline (str, 1 bold sentence — valuation relative to peers), "
        "bullets (list of 3-5 strings, each ~50 words covering key relative valuation findings), "
        "verdict (str, e.g. 'Trading at 10% discount to peers — Undervalued')), "
        "summary (2-3 sentences)."
    )
    prompt = f"Perform comparable company analysis:\n\n{json.dumps(data, indent=2, default=str)}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_comps", max_tokens=6000)


def analyze_earnings(data, user_id=None):
    """LLM analysis of earnings — institutional-quality earnings update report."""
    system = (
        "You are a senior sell-side equity research analyst writing an institutional-quality "
        "quarterly earnings update report. Use the financial data provided to write a comprehensive "
        "analysis. Be specific with numbers — cite actual revenue figures, EPS, margins, growth rates. "
        "Compare actuals vs consensus estimates where possible. Write in professional equity research style.\n\n"
        "Return valid JSON with these keys:\n\n"
        "header: dict with:\n"
        "  company_name (str), ticker (str), rating (Buy/Hold/Sell/Strong Buy/Strong Sell),\n"
        "  current_price (float), price_target (float), upside_pct (float),\n"
        "  sector (str), industry (str), market_cap (str formatted like '$75.0B'),\n"
        "  report_date (str)\n\n"
        "earnings_summary: dict with:\n"
        "  headline (str, e.g. 'Revenue & EPS BEAT'),\n"
        "  table (list of dicts, each with: metric, actual, consensus, variance, result),\n"
        "  key_takeaways (list of 3-5 strings, each a substantive paragraph with bold lead sentence)\n\n"
        "updated_estimates: dict with:\n"
        "  table (list of dicts, each with: metric, fy_actual, fy_actual_label, fy1_estimate, fy1_label, fy2_estimate, fy2_label),\n"
        "  source_note (str)\n\n"
        "revenue_analysis: dict with:\n"
        "  narrative (str, 2-3 detailed paragraphs with specific numbers),\n"
        "  quarterly_revenue (list of dicts with quarter, revenue, yoy_growth),\n"
        "  organic_growth (float or null)\n\n"
        "segment_analysis: dict with:\n"
        "  narrative (str, 1-2 paragraphs analyzing segments or geographic regions),\n"
        "  segments (list of dicts with name, revenue, growth, key_drivers)\n\n"
        "profitability_analysis: dict with:\n"
        "  narrative (str, 2-3 paragraphs on gross margin, operating margin, EPS trends),\n"
        "  gross_margin_current (float), gross_margin_prior (float),\n"
        "  operating_margin_current (float), operating_margin_prior (float),\n"
        "  eps_current (float), eps_prior (float), eps_growth (float)\n\n"
        "operating_highlights: dict with:\n"
        "  table (list of dicts with metric, q_value, fy_value, prior_fy_value, yoy_change),\n"
        "  free_cash_flow_narrative (str),\n"
        "  capital_allocation (str, on dividends, buybacks, debt)\n\n"
        "guidance: dict with:\n"
        "  narrative (str paragraph),\n"
        "  table (list of dicts with metric, guidance_range, notes)\n\n"
        "investment_thesis: dict with:\n"
        "  pillars (list of dicts with name, status (UNCHANGED/STRENGTHENED/WEAKENED), detail),\n"
        "  key_risks (list of strings, each a substantive 1-2 sentence risk)\n\n"
        "valuation: dict with:\n"
        "  narrative (str, 1-2 paragraphs on relative valuation),\n"
        "  peer_table (list of dicts with company, market_cap, forward_pe, ev_ebitda, growth, gross_margin),\n"
        "  price_target_methodology (list of dicts with methodology, implied_value, weight, contribution),\n"
        "  blended_target (float)\n\n"
        "appendix: dict with:\n"
        "  earnings_call_highlights (list of strings, key management quotes/commentary),\n"
        "  notable_items (str or null, one-time charges, impairments, etc.),\n"
        "  analyst_activity (str, consensus rating and recent PT changes)\n\n"
        "executive_snapshot: dict with:\n"
        "  headline (str, 1 bold sentence — the key earnings verdict, e.g. 'Strong Beat Reinforces Buy Thesis'),\n"
        "  bullets (list of 3-5 strings, each ~50 words covering: beat/miss, key metric changes, guidance, thesis impact),\n"
        "  rating_action (str, e.g. 'Maintain Buy, PT $250 (+12% upside)')\n\n"
        "overall_score (int 1-10), summary (str, 2-3 sentences)"
    )
    prompt = f"Write an institutional-quality earnings update report:\n\n{json.dumps(data, indent=2, default=str)}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_earnings",
                     model=LLM_SKILL_EARNINGS, max_tokens=12000)


def analyze_sector(data, user_id=None):
    """LLM analysis of sector positioning and trends."""
    system = (
        "You are a sector research analyst. Analyze the company's sector positioning. "
        "Return valid JSON with keys: "
        "sector (str), industry (str), "
        "sector_outlook (dict with trend, growth_drivers, headwinds, rating), "
        "competitive_position (dict with market_share_note, moat_assessment, key_competitors), "
        "relative_valuation (dict with vs_sector_pe, vs_sector_growth, premium_discount), "
        "macro_sensitivity (dict with interest_rate, inflation, gdp, regulatory), "
        "esg_considerations (dict with environmental, social, governance, score), "
        "catalysts (list of strings), risks (list of strings), "
        "overall_sector_score (1-10), "
        "executive_snapshot (dict with: headline (str, 1 bold sentence — sector outlook conclusion), "
        "bullets (list of 3-5 strings, each ~50 words covering competitive position and key drivers), "
        "outlook (str, e.g. 'Sector Outperform — strong secular tailwinds')), "
        "summary (2-3 sentences)."
    )
    prompt = f"Analyze sector positioning:\n\n{json.dumps(data, indent=2, default=str)}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_sector")


def quality_check(report_data, skill_name, user_id=None):
    """QC review pass — check report for consistency and accuracy."""
    system = (
        "You are a quality control reviewer for AI-generated financial analysis reports. "
        "Your job is to ensure the report is coherent and useful, NOT to verify exact numbers "
        "(since the data comes from real-time market feeds and LLM estimates). "
        "Focus on: logical coherence between sections, reasonable assumptions, "
        "completeness of required fields, and whether the conclusions follow from the data. "
        "Minor rounding differences or estimation variations are acceptable — do NOT fail for those. "
        "Only flag genuine logical errors, missing critical sections, or contradictory conclusions. "
        "Return valid JSON with keys: "
        "passed (bool — should be true if report is coherent and usable), "
        "issues (list of strings — only real problems, not nitpicks), "
        "suggestions (list of strings — optional improvements), "
        "confidence_score (1-10, where 7+ means usable report)."
    )
    prompt = (
        f"Review this {skill_name} report for quality and coherence:\n\n"
        f"{json.dumps(report_data, indent=2, default=str)}"
    )
    return _call_llm(prompt, system, user_id=user_id, source="skill_qc", max_tokens=2048)


def _format_csv_context(inputs):
    """Format uploaded CSV data for inclusion in LLM prompts."""
    csv_data = inputs.get("portfolio_csv")
    if not csv_data or not isinstance(csv_data, dict):
        return ""
    rows = csv_data.get("rows", [])
    if not rows:
        return ""
    headers = csv_data.get("headers", [])
    lines = [", ".join(headers)]
    for row in rows[:100]:  # Cap at 100 rows
        lines.append(", ".join(str(row.get(h, "")) for h in headers))
    return f"\n\nClient portfolio data (from uploaded CSV):\n{chr(10).join(lines)}\n"


def generate_investment_proposal(data, inputs, user_id=None):
    """LLM generates a client investment proposal."""
    risk = inputs.get("risk_tolerance", "moderate")
    horizon = inputs.get("investment_horizon", "5-10 years")
    system = (
        "You are a wealth management advisor creating a client investment proposal. "
        f"Client risk tolerance: {risk}. Investment horizon: {horizon}. "
        "Return valid JSON with keys: "
        "executive_summary (str), recommended_allocation (list of {ticker, weight_pct, rationale}), "
        "expected_return_annual (float), expected_volatility (float), "
        "risk_assessment (dict with score_1_10, max_drawdown_estimate, diversification_note), "
        "income_projection (dict with dividend_yield, annual_income_per_100k), "
        "fees_disclosure (str), implementation_steps (list of strings), "
        "monitoring_plan (str), "
        "executive_snapshot (dict with: headline (str, 1 bold sentence — the investment recommendation), "
        "bullets (list of 3-5 strings, each ~50 words covering allocation, expected return, risk), "
        "recommendation (str, e.g. 'Moderate Growth — 8.5% expected annual return')), "
        "summary (2-3 sentences)."
    )
    csv_ctx = _format_csv_context(inputs)
    prompt = f"Create an investment proposal based on this portfolio data:\n\n{json.dumps(data, indent=2, default=str)}{csv_ctx}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_wm_proposal", max_tokens=6000)


def analyze_rebalance(data, inputs, user_id=None):
    """LLM analyzes portfolio drift and generates rebalancing trades."""
    target = inputs.get("target_allocation", "")
    system = (
        "You are a portfolio manager analyzing drift and generating rebalancing trades. "
        + (f"Target allocation percentages: {target}. " if target else
           "Determine an appropriate target allocation based on the holdings. ")
        + "Return valid JSON with keys: "
        "current_allocation (list of {ticker, current_weight_pct, target_weight_pct, drift_pct}), "
        "rebalancing_trades (list of {ticker, action (buy/sell), shares_estimate, rationale}), "
        "tax_considerations (list of strings), "
        "transaction_cost_estimate (str), "
        "post_rebalance_metrics (dict with expected_return, volatility, sharpe_estimate), "
        "executive_snapshot (dict with: headline (str, 1 bold sentence — drift summary and action needed), "
        "bullets (list of 3-5 strings, each ~50 words covering drift, key trades, tax impact), "
        "action_summary (str, e.g. '5 trades needed — reduce tech overweight, add fixed income')), "
        "summary (2-3 sentences)."
    )
    csv_ctx = _format_csv_context(inputs)
    prompt = f"Analyze portfolio drift and generate rebalancing plan:\n\n{json.dumps(data, indent=2, default=str)}{csv_ctx}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_wm_rebalance", max_tokens=6000)


def generate_financial_plan(data, inputs, user_id=None):
    """LLM builds a comprehensive financial plan."""
    risk = inputs.get("risk_tolerance", "moderate")
    horizon = inputs.get("investment_horizon", "10+ years")
    system = (
        "You are a certified financial planner building a comprehensive financial plan. "
        f"Risk tolerance: {risk}. Planning horizon: {horizon}. "
        "Return valid JSON with keys: "
        "plan_summary (str), "
        "current_portfolio_assessment (dict with total_value_note, asset_mix, strengths, weaknesses), "
        "recommended_allocation (dict with equities_pct, fixed_income_pct, alternatives_pct, cash_pct), "
        "retirement_projection (dict with assumptions, projected_value_10yr, projected_value_20yr, "
        "monthly_income_estimate), "
        "savings_targets (list of {milestone, target_amount, timeline}), "
        "risk_management (dict with insurance_notes, emergency_fund, diversification_score), "
        "tax_strategy (list of strings), "
        "action_items (list of strings with priority), "
        "executive_snapshot (dict with: headline (str, 1 bold sentence — financial plan conclusion), "
        "bullets (list of 3-5 strings, each ~50 words covering retirement readiness, key actions, allocation), "
        "priorities (str, e.g. 'Priority 1: Max retirement contributions. Priority 2: Build 6-month emergency fund')), "
        "summary (2-3 sentences)."
    )
    csv_ctx = _format_csv_context(inputs)
    prompt = f"Build a financial plan based on this portfolio data:\n\n{json.dumps(data, indent=2, default=str)}{csv_ctx}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_wm_plan", max_tokens=6000)


def analyze_tax_loss_harvest(data, inputs=None, user_id=None):
    """LLM identifies tax-loss harvesting opportunities."""
    system = (
        "You are a tax-aware portfolio manager identifying tax-loss harvesting opportunities. "
        "Analyze the portfolio for positions with unrealized losses. "
        "Return valid JSON with keys: "
        "opportunities (list of {ticker, estimated_loss_pct, substitute_ticker, "
        "substitute_rationale, wash_sale_warning}), "
        "total_estimated_tax_savings_per_100k (float), "
        "positions_to_hold (list of {ticker, reason}), "
        "wash_sale_rules_reminder (str), "
        "implementation_timeline (str), "
        "executive_snapshot (dict with: headline (str, 1 bold sentence — harvesting opportunity summary), "
        "bullets (list of 3-5 strings, each ~50 words covering top opportunities and savings), "
        "estimated_savings (str, e.g. 'Est. $3,200 tax savings on $100K portfolio')), "
        "summary (2-3 sentences)."
    )
    csv_ctx = _format_csv_context(inputs or {})
    prompt = f"Identify tax-loss harvesting opportunities:\n\n{json.dumps(data, indent=2, default=str)}{csv_ctx}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_wm_tlh", max_tokens=4096)


def generate_client_report(data, inputs, user_id=None):
    """LLM generates a client performance report."""
    benchmark = inputs.get("benchmark", "SPY")
    system = (
        f"You are a wealth advisor generating a client performance report. Benchmark: {benchmark}. "
        "Return valid JSON with keys: "
        "report_period (str), "
        "portfolio_performance (dict with total_return_pct, annualized_return, volatility, sharpe_ratio), "
        "benchmark_comparison (dict with benchmark_ticker, benchmark_return_pct, alpha, tracking_error), "
        "top_contributors (list of {ticker, contribution_pct}), "
        "top_detractors (list of {ticker, contribution_pct}), "
        "asset_allocation_summary (list of {category, weight_pct}), "
        "market_commentary (str, 2-3 paragraphs), "
        "outlook_and_recommendations (list of strings), "
        "executive_snapshot (dict with: headline (str, 1 bold sentence — performance vs benchmark verdict), "
        "bullets (list of 3-5 strings, each ~50 words covering returns, alpha, top movers), "
        "performance (str, e.g. '+12.3% YTD — outperforming SPY by 280bps')), "
        "summary (2-3 sentences)."
    )
    prompt = f"Generate a client performance report:\n\n{json.dumps(data, indent=2, default=str)}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_wm_report", max_tokens=6000)


def generate_client_review(data, inputs=None, user_id=None):
    """LLM prepares client review meeting materials."""
    system = (
        "You are a wealth advisor preparing for a client review meeting. "
        "Return valid JSON with keys: "
        "meeting_agenda (list of strings), "
        "portfolio_snapshot (dict with performance_summary, key_changes, rebalancing_needed), "
        "discussion_points (list of {topic, talking_point, data_reference}), "
        "market_update (dict with headline, key_themes, impact_on_portfolio), "
        "recommended_actions (list of {action, priority, rationale}), "
        "questions_to_ask_client (list of strings), "
        "follow_up_items (list of strings), "
        "executive_snapshot (dict with: headline (str, 1 bold sentence — meeting focus and key action), "
        "bullets (list of 3-5 strings, each ~50 words covering agenda highlights and recommendations), "
        "key_actions (str, e.g. 'Rebalance portfolio + discuss tax-loss harvesting before year-end')), "
        "summary (2-3 sentences)."
    )
    csv_ctx = _format_csv_context(inputs or {})
    prompt = f"Prepare client review meeting materials:\n\n{json.dumps(data, indent=2, default=str)}{csv_ctx}"
    return _call_llm(prompt, system, user_id=user_id, source="skill_wm_review", max_tokens=6000)


def generate_value_creation_plan(data, inputs=None, user_id=None):
    """Generate a PE-style value creation plan for a portfolio company."""
    system = (
        "You are a senior private equity operating partner with 20 years of experience. "
        "Build a comprehensive post-acquisition value creation plan. Return valid JSON with keys: "
        "company_overview (str), investment_thesis (str), "
        "revenue_levers (array of {lever, description, impact_estimate, timeline, confidence}), "
        "margin_expansion (array of {initiative, current, target, savings_estimate}), "
        "operational_improvements (array of {area, action, expected_outcome}), "
        "addon_targets (array of {type, rationale, criteria}), "
        "management_kpis (array of {kpi, target, frequency}), "
        "hundred_day_plan (array of {priority, action, owner, milestone}), "
        "three_year_roadmap (array of {year, focus, revenue_target, ebitda_target}), "
        "key_risks (array of {risk, mitigation, severity}), "
        "exit_considerations (str)"
    )
    context_str = inputs.get("context", "") if inputs else ""
    prompt = f"Company data:\n{json.dumps(data, default=str, indent=2)}\n\nDeal context: {context_str}\n\nBuild the value creation plan."
    raw = _call_llm(prompt, system, user_id=user_id, max_tokens=6000)
    return parse_llm_json(raw)


def screen_deal(data, inputs=None, user_id=None):
    """Screen an inbound PE deal opportunity."""
    system = (
        "You are a senior private equity investor screening inbound deals. "
        "Evaluate this opportunity and return valid JSON with keys: "
        "verdict (GO / DILIGENCE / PASS), confidence (0-100), "
        "business_quality_score (1-10), market_position (str), "
        "financial_profile ({revenue, growth_rate, margins, debt_level, fcf_yield}), "
        "strengths (array of str), red_flags (array of str), "
        "key_questions_for_diligence (array of str), "
        "preliminary_valuation ({low, mid, high, methodology, comparable_multiples}), "
        "fit_assessment (str), recommendation_summary (str)"
    )
    criteria = inputs.get("criteria", "") if inputs else ""
    deal_info = inputs.get("deal_info", "") if inputs else ""
    prompt = f"Company data:\n{json.dumps(data, default=str, indent=2)}\n\nDeal info: {deal_info}\nInvestment criteria: {criteria}\n\nScreen this deal."
    raw = _call_llm(prompt, system, user_id=user_id, max_tokens=5000)
    return parse_llm_json(raw)


def generate_earnings_preview(data, user_id=None):
    """Generate a pre-earnings scenario analysis."""
    system = (
        "You are a senior equity research analyst with 20 years of experience preparing for earnings. "
        "Build a pre-earnings preview with scenarios. Return valid JSON with keys: "
        "company (str), quarter (str), earnings_date (str or 'TBD'), "
        "consensus ({revenue, eps, guidance}), "
        "historical_pattern ({avg_beat_pct, avg_miss_pct, beat_rate, last_4_quarters: array}), "
        "bull_case ({revenue, eps, key_drivers: array, probability, price_target}), "
        "base_case ({revenue, eps, key_drivers: array, probability, price_target}), "
        "bear_case ({revenue, eps, key_drivers: array, probability, price_target}), "
        "key_metrics_to_watch (array of {metric, why_it_matters, consensus_expectation}), "
        "options_implied_move (str), "
        "positioning_recommendation (str), "
        "risks (array of str)"
    )
    prompt = f"Financial data:\n{json.dumps(data, default=str, indent=2)}\n\nBuild the earnings preview."
    raw = _call_llm(prompt, system, user_id=user_id, model=LLM_SKILL, max_tokens=5000)
    return parse_llm_json(raw)


def generate_morning_note(data, inputs=None, user_id=None):
    """Generate a morning meeting note for the trading desk."""
    system = (
        "You are a senior equity strategist preparing the morning meeting note. "
        "Write a concise, actionable 2-minute read. Return valid JSON with keys: "
        "date (str), market_summary (str, 2-3 sentences on overnight/pre-market), "
        "key_levels ({spy: {support, resistance}, qqq: {support, resistance}, vix: current}), "
        "overnight_movers (array of {ticker, move_pct, catalyst}), "
        "economic_calendar (array of {time, event, consensus, prior, importance}), "
        "earnings_today (array of {ticker, time, consensus_eps, consensus_rev}), "
        "sector_rotation (str, which sectors are leading/lagging), "
        "trade_ideas (array of {ticker, direction, thesis, entry, stop, target}), "
        "risk_radar (array of str, top 3 risks to watch today), "
        "bottom_line (str, 1-sentence key takeaway)"
    )
    tickers = inputs.get("tickers", "") if inputs else ""
    sectors = inputs.get("sectors", "") if inputs else ""
    prompt = f"Market data:\n{json.dumps(data, default=str, indent=2)}\n\nFocus tickers: {tickers}\nFocus sectors: {sectors}\n\nWrite the morning note."
    raw = _call_llm(prompt, system, user_id=user_id, max_tokens=4000)
    return parse_llm_json(raw)


def update_financial_model(data, inputs=None, user_id=None):
    """Update a financial model with latest quarterly data and revised estimates."""
    system = (
        "You are a senior equity research analyst updating your financial model after new data. "
        "Compare actuals vs prior estimates, revise forward projections. Return valid JSON with keys: "
        "company (str), last_quarter (str), "
        "actuals_vs_estimates ({revenue: {actual, estimate, beat_miss_pct}, eps: {actual, estimate, beat_miss_pct}, "
        "key_metrics: array of {metric, actual, estimate, beat_miss_pct}}), "
        "estimate_revisions ({this_year: {revenue_old, revenue_new, change_pct, eps_old, eps_new, change_pct}, "
        "next_year: {revenue_old, revenue_new, change_pct, eps_old, eps_new, change_pct}}), "
        "valuation_update ({pe_forward, ev_ebitda, dcf_value, peer_avg_pe, premium_discount}), "
        "rating_change ({old_rating, new_rating, old_target, new_target, rationale}), "
        "key_model_changes (array of {item, old_assumption, new_assumption, impact}), "
        "risks_to_estimates (array of str)"
    )
    notes = inputs.get("notes", "") if inputs else ""
    prompt = f"Financial data:\n{json.dumps(data, default=str, indent=2)}\n\nUpdate notes: {notes}\n\nUpdate the model."
    raw = _call_llm(prompt, system, user_id=user_id, max_tokens=5000)
    return parse_llm_json(raw)


def build_catalyst_calendar(data, inputs=None, user_id=None):
    """Build a forward-looking catalyst calendar with impact rankings."""
    system = (
        "You are an equity research analyst building a catalyst calendar. "
        "Identify all upcoming events that could move the stock price. Return valid JSON with keys: "
        "company (str), timeframe (str), "
        "catalysts (array of {date, event, category (earnings/regulatory/product/macro/technical/conference), "
        "description, expected_impact (high/medium/low), direction (bullish/bearish/uncertain), "
        "probability (0-100), estimated_move_pct}), "
        "technical_levels ({support: array of float, resistance: array of float, "
        "key_moving_averages: {sma50, sma200}}), "
        "upcoming_earnings ({date, consensus_eps, consensus_rev}), "
        "sector_events (array of {date, event, relevance}), "
        "macro_events (array of {date, event, potential_impact}), "
        "summary (str, 2-3 sentence overview of catalyst landscape)"
    )
    timeframe = inputs.get("timeframe", "90d") if inputs else "90d"
    prompt = f"Company data:\n{json.dumps(data, default=str, indent=2)}\n\nTimeframe: {timeframe}\n\nBuild the catalyst calendar."
    raw = _call_llm(prompt, system, user_id=user_id, max_tokens=4000)
    return parse_llm_json(raw)


def parse_llm_json(raw_text):
    """Parse LLM response as JSON, with fallback extraction."""
    if not raw_text:
        return {}
    # Try direct parse
    try:
        return json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        pass
    # Try extracting JSON from markdown code block
    if "```" in raw_text:
        parts = raw_text.split("```")
        for part in parts[1::2]:  # odd-indexed parts are inside code blocks
            text = part.strip()
            if text.startswith("json"):
                text = text[4:].strip()
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue
    # Try finding first { to last }
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw_text[start:end + 1])
        except (json.JSONDecodeError, TypeError):
            pass
    return {"raw_response": raw_text}
