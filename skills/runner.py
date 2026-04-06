"""
Skill runner — executes skill phases step-by-step with progress callbacks.
"""

import logging
import os
import time

from skills.models import STATUS_CANCELLED
from skills import data_providers, llm_adapter

logger = logging.getLogger(__name__)


def run_skill(job):
    """Execute a skill job phase by phase.
    Called from executor thread pool worker.
    """
    from skills.registry import get_skill

    skill = get_skill(job.skill_id)
    if not skill:
        raise ValueError(f"Unknown skill: {job.skill_id}")

    phases = skill.phases
    total_phases = len(phases)
    context = {"inputs": job.inputs, "data": {}, "results": {}}

    for i, phase in enumerate(phases):
        if job.status == STATUS_CANCELLED:
            return

        phase_name = phase.get("name", f"Phase {i + 1}")
        job.current_phase = phase_name
        job.message = phase.get("description", phase_name)
        job.progress = i / total_phases
        job.notify("progress")

        actions = phase.get("actions", [])
        for action in actions:
            if job.status == STATUS_CANCELLED:
                return
            _execute_action(action, context, job)

    # Build final result
    job.result = context.get("results", {})
    job.result["_meta"] = {
        "skill_id": job.skill_id,
        "ticker": job.inputs.get("ticker", ""),
        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Generate output files
    _generate_outputs(skill, job, context)


def _execute_action(action, context, job):
    """Execute a single action within a phase."""
    action_type = action.get("type", "")
    config = action.get("config", {})
    ticker = context["inputs"].get("ticker", "")
    user_id = job.user_id

    try:
        if action_type == "fetch_fundamentals":
            job.message = f"Fetching fundamentals for {ticker}..."
            job.notify("progress")
            context["data"]["fundamentals"] = data_providers.fetch_fundamentals(ticker)

        elif action_type == "fetch_company_info":
            job.message = f"Fetching company info for {ticker}..."
            job.notify("progress")
            context["data"]["company_info"] = data_providers.fetch_company_info(ticker)

        elif action_type == "fetch_prices":
            period = config.get("period", "1y")
            job.message = f"Fetching price data ({period})..."
            job.notify("progress")
            context["data"]["prices"] = data_providers.fetch_price_data(ticker, period=period)

        elif action_type == "fetch_indicators":
            job.message = "Calculating technical indicators..."
            job.notify("progress")
            context["data"]["indicators"] = data_providers.fetch_indicators(ticker)

        elif action_type == "fetch_income_statements":
            quarterly = config.get("quarterly", False)
            label = "quarterly" if quarterly else "annual"
            job.message = f"Fetching {label} income statements..."
            job.notify("progress")
            key = "income_statements_quarterly" if quarterly else "income_statements"
            context["data"][key] = data_providers.fetch_income_statements(ticker, quarterly=quarterly)

        elif action_type == "fetch_balance_sheet":
            quarterly = config.get("quarterly", False)
            job.message = "Fetching balance sheet..."
            job.notify("progress")
            context["data"]["balance_sheet"] = data_providers.fetch_balance_sheet(ticker, quarterly=quarterly)

        elif action_type == "fetch_cash_flow":
            quarterly = config.get("quarterly", False)
            job.message = "Fetching cash flow data..."
            job.notify("progress")
            context["data"]["cash_flow"] = data_providers.fetch_cash_flow(ticker, quarterly=quarterly)

        elif action_type == "fetch_peers":
            peers = context["inputs"].get("peer_tickers", "")
            if isinstance(peers, str):
                peer_list = [p.strip() for p in peers.split(",") if p.strip()]
            else:
                peer_list = peers or []
            if peer_list:
                job.message = f"Fetching peer data ({len(peer_list)} peers)..."
                job.notify("progress")
                context["data"]["peers"] = data_providers.fetch_peer_data(peer_list)

        elif action_type == "llm_financial_health":
            job.message = "AI analyzing financial health..."
            job.notify("progress")
            raw = llm_adapter.analyze_financial_health(context["data"], user_id=user_id)
            context["results"]["financial_health"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_dcf":
            job.message = "AI building DCF model..."
            job.notify("progress")
            raw = llm_adapter.run_dcf_analysis(context["data"], user_id=user_id)
            context["results"]["dcf"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_comps":
            job.message = "AI running comparable analysis..."
            job.notify("progress")
            raw = llm_adapter.run_comparable_analysis(context["data"], user_id=user_id)
            context["results"]["comps"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_earnings":
            job.message = "AI analyzing earnings..."
            job.notify("progress")
            raw = llm_adapter.analyze_earnings(context["data"], user_id=user_id)
            context["results"]["earnings"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_sector":
            job.message = "AI analyzing sector positioning..."
            job.notify("progress")
            raw = llm_adapter.analyze_sector(context["data"], user_id=user_id)
            context["results"]["sector"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_qc":
            job.message = "Quality check review..."
            job.notify("progress")
            raw = llm_adapter.quality_check(context["results"], job.skill_id, user_id=user_id)
            qc = llm_adapter.parse_llm_json(raw)
            context["results"]["quality_check"] = qc

        elif action_type == "llm_investment_proposal":
            job.message = "AI generating investment proposal..."
            job.notify("progress")
            raw = llm_adapter.generate_investment_proposal(context["data"], context["inputs"], user_id=user_id)
            context["results"]["investment_proposal"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_rebalance":
            job.message = "AI analyzing portfolio drift..."
            job.notify("progress")
            raw = llm_adapter.analyze_rebalance(context["data"], context["inputs"], user_id=user_id)
            context["results"]["rebalance"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_financial_plan":
            job.message = "AI building financial plan..."
            job.notify("progress")
            raw = llm_adapter.generate_financial_plan(context["data"], context["inputs"], user_id=user_id)
            context["results"]["financial_plan"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_tax_loss_harvest":
            job.message = "AI identifying TLH opportunities..."
            job.notify("progress")
            raw = llm_adapter.analyze_tax_loss_harvest(context["data"], context["inputs"], user_id=user_id)
            context["results"]["tax_loss_harvest"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_client_report":
            job.message = "AI generating client report..."
            job.notify("progress")
            raw = llm_adapter.generate_client_report(context["data"], context["inputs"], user_id=user_id)
            context["results"]["client_report"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_client_review":
            job.message = "AI preparing review materials..."
            job.notify("progress")
            raw = llm_adapter.generate_client_review(context["data"], context["inputs"], user_id=user_id)
            context["results"]["client_review"] = llm_adapter.parse_llm_json(raw)

        elif action_type == "llm_value_creation":
            job.message = "AI building value creation plan..."
            job.notify("progress")
            from skills.llm_adapter import generate_value_creation_plan
            context["results"]["value_creation_plan"] = generate_value_creation_plan(context["data"], context.get("inputs"), user_id=job.user_id)

        elif action_type == "llm_deal_screening":
            job.message = "AI screening deal opportunity..."
            job.notify("progress")
            from skills.llm_adapter import screen_deal
            context["results"]["deal_screening"] = screen_deal(context["data"], context.get("inputs"), user_id=job.user_id)

        elif action_type == "llm_earnings_preview":
            job.message = "AI building earnings preview scenarios..."
            job.notify("progress")
            from skills.llm_adapter import generate_earnings_preview
            context["results"]["earnings_preview"] = generate_earnings_preview(context["data"], user_id=job.user_id)

        elif action_type == "llm_morning_note":
            job.message = "AI drafting morning note..."
            job.notify("progress")
            from skills.llm_adapter import generate_morning_note
            context["results"]["morning_note"] = generate_morning_note(context["data"], context.get("inputs"), user_id=job.user_id)

        elif action_type == "llm_model_update":
            job.message = "AI updating financial model..."
            job.notify("progress")
            from skills.llm_adapter import update_financial_model
            context["results"]["model_update"] = update_financial_model(context["data"], context.get("inputs"), user_id=job.user_id)

        elif action_type == "llm_catalyst_calendar":
            job.message = "AI building catalyst calendar..."
            job.notify("progress")
            from skills.llm_adapter import build_catalyst_calendar
            context["results"]["catalyst_calendar"] = build_catalyst_calendar(context["data"], context.get("inputs"), user_id=job.user_id)

        elif action_type == "compute_ratios":
            job.message = "Computing financial ratios..."
            job.notify("progress")
            context["results"]["computed_ratios"] = _compute_ratios(context["data"])

        elif action_type == "generate_charts":
            job.message = "Generating charts..."
            job.notify("progress")
            from skills.chart_generator import generate_earnings_charts
            chart_dir = os.path.join("data", "skill_outputs", job.id, "charts")
            charts = generate_earnings_charts(context, chart_dir)
            context["charts"] = charts
            context["results"]["_charts"] = [
                {"filename": c["filename"], "caption": c["caption"]} for c in charts
            ]

        else:
            logger.warning("Unknown action type: %s", action_type)

    except Exception as e:
        logger.error("Action %s failed: %s", action_type, e)
        context["results"][f"{action_type}_error"] = str(e)


def _compute_ratios(data):
    """Compute derived financial ratios from raw data."""
    fundamentals = data.get("fundamentals", {})
    if not fundamentals:
        return {}

    valuation = fundamentals.get("valuation", {})
    profitability = fundamentals.get("profitability", {})
    balance = fundamentals.get("balance_sheet", {})
    cash_flow_data = fundamentals.get("cash_flow", {})

    return {
        "pe_ratio": valuation.get("pe_trailing"),
        "forward_pe": valuation.get("pe_forward"),
        "peg_ratio": valuation.get("peg_ratio"),
        "price_to_book": valuation.get("price_to_book"),
        "gross_margin": profitability.get("gross_margin"),
        "operating_margin": profitability.get("operating_margin"),
        "net_margin": profitability.get("net_margin"),
        "roe": profitability.get("roe"),
        "roa": profitability.get("roa"),
        "debt_to_equity": balance.get("debt_to_equity"),
        "current_ratio": balance.get("current_ratio"),
        "fcf": cash_flow_data.get("free_cf"),
        "operating_cf": cash_flow_data.get("operating_cf"),
    }


def _generate_outputs(skill, job, context):
    """Generate output files (DOCX, XLSX) for completed skill."""
    from skills.outputs import generate_docx, generate_xlsx
    import os

    output_dir = os.path.join("data", "skill_outputs", job.id)
    os.makedirs(output_dir, exist_ok=True)

    ticker = job.inputs.get("ticker", "report")
    base_name = f"{skill.id}_{ticker}"

    try:
        if "docx" in skill.output_types:
            job.message = "Generating DOCX report..."
            job.notify("progress")
            docx_path = os.path.join(output_dir, f"{base_name}.docx")
            generate_docx(skill, job, context, docx_path)
            job.output_files.append({"format": "docx", "path": docx_path, "name": f"{base_name}.docx"})

        if "xlsx" in skill.output_types:
            job.message = "Generating XLSX report..."
            job.notify("progress")
            xlsx_path = os.path.join(output_dir, f"{base_name}.xlsx")
            generate_xlsx(skill, job, context, xlsx_path)
            job.output_files.append({"format": "xlsx", "path": xlsx_path, "name": f"{base_name}.xlsx"})

    except Exception as e:
        logger.error("Output generation failed for job %s: %s", job.id, e)
        # Don't fail the whole job if output generation fails
        job.result["output_error"] = str(e)
