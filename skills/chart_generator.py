"""
Chart generator for earnings reports — creates matplotlib charts matching sell-side style.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Style constants
NAVY = "#17365D"
LIGHT_NAVY = "#2C5F8A"
BLUE = "#5B9BD5"
GRAY = "#B0B3B8"
GREEN = "#26A69A"
RED = "#EF5350"
BG_COLOR = "#FFFFFF"
GRID_COLOR = "#D1D4DC"
FONT_FAMILY = "sans-serif"


def generate_earnings_charts(context, output_dir):
    """Generate all earnings charts, return list of chart metadata dicts.

    Each dict: {"filename": str, "title": str, "caption": str, "path": str}
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.error("matplotlib not installed, skipping chart generation")
        return []

    os.makedirs(output_dir, exist_ok=True)

    earnings = context.get("results", {}).get("earnings", {})
    header = earnings.get("header", {})
    company_name = header.get("company_name", context.get("inputs", {}).get("ticker", "Company"))
    ticker = header.get("ticker", context.get("inputs", {}).get("ticker", ""))

    charts = []

    # Common style setup
    plt.rcParams.update({
        "font.family": FONT_FAMILY,
        "font.size": 11,
        "axes.titlesize": 14,
        "axes.titleweight": "bold",
        "axes.labelsize": 11,
        "axes.facecolor": BG_COLOR,
        "figure.facecolor": BG_COLOR,
        "axes.grid": True,
        "grid.color": GRID_COLOR,
        "grid.linestyle": "--",
        "grid.alpha": 0.7,
    })

    # Chart 1: Quarterly Revenue
    c = _chart_quarterly_revenue(earnings, company_name, ticker, output_dir, plt)
    if c:
        charts.append(c)

    # Chart 2: EPS Trend
    c = _chart_eps_trend(earnings, company_name, ticker, output_dir, plt)
    if c:
        charts.append(c)

    # Chart 3: Segment/Geographic Breakdown (horizontal bar)
    c = _chart_segment_breakdown(earnings, company_name, ticker, output_dir, plt)
    if c:
        charts.append(c)

    # Chart 4: Gross Margin Trend
    c = _chart_gross_margin(earnings, context, company_name, ticker, output_dir, plt)
    if c:
        charts.append(c)

    # Chart 5: Operating Highlights / FCF
    c = _chart_fcf(earnings, company_name, ticker, output_dir, plt)
    if c:
        charts.append(c)

    # Chart 6: Peer Forward P/E Comparison
    c = _chart_peer_valuation(earnings, company_name, ticker, output_dir, plt)
    if c:
        charts.append(c)

    # Chart 7: Updated Estimates (grouped bar)
    c = _chart_estimates(earnings, company_name, ticker, output_dir, plt)
    if c:
        charts.append(c)

    # Chart 8: Price Target Methodology (horizontal bar)
    c = _chart_price_target(earnings, company_name, ticker, output_dir, plt)
    if c:
        charts.append(c)

    plt.close("all")
    return charts


def _safe_float(val, default=0.0):
    """Extract float from various formats like '$5,230M', '+2.2%', etc."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace("$", "").replace(",", "").replace("%", "").replace("+", "").strip()
    # Handle suffixes
    multiplier = 1
    if s.upper().endswith("T"):
        multiplier = 1e12
        s = s[:-1]
    elif s.upper().endswith("B"):
        multiplier = 1e9
        s = s[:-1]
    elif s.upper().endswith("M"):
        multiplier = 1e6
        s = s[:-1]
    elif s.upper().endswith("K"):
        multiplier = 1e3
        s = s[:-1]
    try:
        return float(s) * multiplier
    except (ValueError, TypeError):
        return default


def _add_source(fig, text, fontsize=8):
    """Add a source citation at the bottom of the figure."""
    fig.text(0.5, 0.01, text, ha="center", fontsize=fontsize, color="#999999", style="italic")


def _save_chart(fig, path, dpi=150):
    """Save chart with tight layout."""
    fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.3)
    import matplotlib.pyplot as plt
    plt.close(fig)


def _chart_quarterly_revenue(earnings, company_name, ticker, output_dir, plt):
    """Chart 1: Quarterly Net Sales bar chart."""
    import matplotlib.ticker as mticker

    ra = earnings.get("revenue_analysis", {})
    qrev = ra.get("quarterly_revenue", [])
    if not qrev or len(qrev) < 2:
        return None

    quarters = [r.get("quarter", "") for r in qrev]
    revenues = [_safe_float(r.get("revenue", 0)) for r in qrev]

    # Normalize to millions if needed
    if all(r > 1e8 for r in revenues if r > 0):
        revenues = [r / 1e6 for r in revenues]
        unit = "$M"
    elif all(r > 1e5 for r in revenues if r > 0):
        revenues = [r / 1e3 for r in revenues]
        unit = "$K"
    else:
        unit = "$M"
        # Assume already in millions if small numbers with "M" in original
        raw_vals = [r.get("revenue", "") for r in qrev]
        if any("B" in str(v) for v in raw_vals):
            revenues = [_safe_float(r.get("revenue", 0)) / 1e6 for r in qrev]

    n = len(quarters)
    colors = [GRAY] * (n - 2) + [BLUE, NAVY] if n >= 3 else [BLUE, NAVY][:n]
    if len(colors) < n:
        colors = [GRAY] * (n - len(colors)) + colors

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(range(n), revenues, color=colors, width=0.6, edgecolor="none")

    # Data labels
    for bar, val in zip(bars, revenues):
        if val > 0:
            label = f"${val:,.0f}M" if unit == "$M" else f"${val:,.0f}{unit[1:]}"
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(revenues) * 0.02,
                    label, ha="center", va="bottom", fontsize=11, fontweight="bold")

    ax.set_xticks(range(n))
    ax.set_xticklabels(quarters, fontsize=10)
    ax.set_ylabel(f"Net Sales ({unit})", fontsize=11)
    ax.set_title(f"{company_name} — Quarterly Net Sales", fontsize=14, fontweight="bold", pad=15)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}M"))
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_source(fig, f"Source: {company_name} Quarterly Earnings Releases")
    path = os.path.join(output_dir, "chart1_revenue.png")
    _save_chart(fig, path)
    return {
        "filename": "chart1_revenue.png",
        "title": f"Quarterly Net Sales",
        "caption": f"Figure 1: Quarterly Net Sales — Revenue Trend",
        "path": path,
    }


def _chart_eps_trend(earnings, company_name, ticker, output_dir, plt):
    """Chart 2: EPS trend with consensus line."""
    pa = earnings.get("profitability_analysis", {})
    es = earnings.get("earnings_summary", {})
    est = es.get("table", [])

    # Try to get EPS data from quarterly revenue or profitability
    ra = earnings.get("revenue_analysis", {})
    qrev = ra.get("quarterly_revenue", [])

    eps_current = pa.get("eps_current")
    eps_prior = pa.get("eps_prior")

    if eps_current is None:
        return None

    # Try to find consensus EPS from earnings summary table
    consensus_eps = None
    for row in est:
        metric = str(row.get("metric", "")).upper()
        if "EPS" in metric and "GAAP" not in metric:
            consensus_eps = _safe_float(row.get("consensus"))
            break
    if consensus_eps is None:
        for row in est:
            metric = str(row.get("metric", "")).upper()
            if "EPS" in metric:
                consensus_eps = _safe_float(row.get("consensus"))
                break

    # Build quarters - use available data
    quarters = []
    eps_vals = []
    if qrev and len(qrev) >= 2:
        # We have quarterly data
        for q in qrev:
            quarters.append(q.get("quarter", ""))
            eps_vals.append(None)  # We don't have per-quarter EPS typically
        # Use current and prior for last two quarters
        if eps_prior is not None and len(quarters) >= 2:
            eps_vals[-2] = float(eps_prior)
        eps_vals[-1] = float(eps_current)
    else:
        if eps_prior is not None:
            quarters = ["Prior Quarter", "Latest Quarter"]
            eps_vals = [float(eps_prior), float(eps_current)]
        else:
            quarters = ["Latest Quarter"]
            eps_vals = [float(eps_current)]

    # Filter out None values
    filtered = [(q, e) for q, e in zip(quarters, eps_vals) if e is not None]
    if not filtered:
        return None

    quarters, eps_vals = zip(*filtered)
    quarters = list(quarters)
    eps_vals = list(eps_vals)
    n = len(quarters)

    colors = [GRAY] * (n - 2) + [BLUE, NAVY] if n >= 3 else [BLUE, NAVY][:n]
    if len(colors) < n:
        colors = [GRAY] * (n - len(colors)) + colors

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(range(n), eps_vals, color=colors, width=0.5, edgecolor="none")

    # Consensus line
    if consensus_eps and consensus_eps > 0:
        ax.axhline(y=consensus_eps, color=RED, linestyle="--", linewidth=1.5, alpha=0.7,
                    label=f"Consensus: ${consensus_eps:.2f}")
        ax.legend(loc="lower right", framealpha=0.8, fontsize=9)

    # Data labels
    for bar, val in zip(bars, eps_vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(eps_vals) * 0.02,
                f"${val:.2f}", ha="center", va="bottom", fontsize=11, fontweight="bold")

    ax.set_xticks(range(n))
    ax.set_xticklabels(quarters, fontsize=10)
    ax.set_ylabel("EPS ($)", fontsize=11)
    ax.set_title(f"EPS Trend — {'Beat' if eps_current > (consensus_eps or 0) else 'Steady Growth'}",
                 fontsize=14, fontweight="bold", pad=15)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_source(fig, f"Source: {company_name} Earnings Releases")
    path = os.path.join(output_dir, "chart2_eps.png")
    _save_chart(fig, path)
    return {
        "filename": "chart2_eps.png",
        "title": "EPS Trend",
        "caption": f"Figure 2: EPS — Steady Growth Trend",
        "path": path,
    }


def _chart_segment_breakdown(earnings, company_name, ticker, output_dir, plt):
    """Chart 3: Segment/geographic growth horizontal bar chart."""
    sa = earnings.get("segment_analysis", {})
    if not isinstance(sa, dict):
        return None
    segments = sa.get("segments", [])
    if not segments or len(segments) < 2:
        return None

    names = [s.get("name", "") for s in segments]
    growths = [_safe_float(s.get("growth", 0)) for s in segments]

    # Sort by growth
    paired = sorted(zip(names, growths), key=lambda x: x[1])
    names, growths = zip(*paired)
    names = list(names)
    growths = list(growths)

    colors = [RED if g < 0 else GREEN for g in growths]

    fig, ax = plt.subplots(figsize=(10, max(6, len(names) * 0.8)))
    bars = ax.barh(range(len(names)), growths, color=colors, height=0.6, edgecolor="none")

    # Data labels
    for bar, val, name in zip(bars, growths, names):
        offset = max(abs(v) for v in growths) * 0.05
        x_pos = val + offset if val >= 0 else val - offset
        ha = "left" if val >= 0 else "right"
        ax.text(x_pos, bar.get_y() + bar.get_height() / 2,
                f"{'+' if val > 0 else ''}{val:.1f}%",
                ha=ha, va="center", fontsize=11, fontweight="bold")

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=10)
    ax.axvline(x=0, color="black", linewidth=0.8)
    ax.set_xlabel("Growth (%)", fontsize=11)
    ax.set_title(f"Segment / Geographic Growth Breakdown", fontsize=14, fontweight="bold", pad=15)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_source(fig, f"Source: {company_name} Earnings Release")
    path = os.path.join(output_dir, "chart3_segments.png")
    _save_chart(fig, path)
    return {
        "filename": "chart3_segments.png",
        "title": "Segment Breakdown",
        "caption": f"Figure 3: Segment / Geographic Growth — Key Drivers",
        "path": path,
    }


def _chart_gross_margin(earnings, context, company_name, ticker, output_dir, plt):
    """Chart 4: Gross margin trend line chart."""
    import matplotlib.ticker as mticker

    pa = earnings.get("profitability_analysis", {})
    gm_current = pa.get("gross_margin_current")
    gm_prior = pa.get("gross_margin_prior")

    if gm_current is None:
        return None

    # Try to build a quarterly gross margin series from income statements
    inc_q = context.get("data", {}).get("income_statements_quarterly", {})
    quarters = []
    margins = []

    if inc_q:
        sorted_periods = sorted(inc_q.keys())
        for period in sorted_periods:
            data = inc_q[period]
            revenue = data.get("Total Revenue") or data.get("Revenue")
            cogs = data.get("Cost Of Revenue") or data.get("Cost Of Goods Sold")
            gross = data.get("Gross Profit")
            if revenue and revenue > 0:
                if gross:
                    gm = (gross / revenue) * 100
                elif cogs:
                    gm = ((revenue - cogs) / revenue) * 100
                else:
                    continue
                # Format period label
                parts = period.split("-")
                if len(parts) >= 2:
                    month = int(parts[1])
                    year = parts[0]
                    q_num = (month - 1) // 3 + 1
                    label = f"Q{q_num} {year}"
                else:
                    label = period
                quarters.append(label)
                margins.append(round(gm, 1))

    # Fallback: just use current and prior
    if len(margins) < 2:
        if gm_prior is not None:
            quarters = ["Prior Period", "Current Period"]
            margins = [float(gm_prior), float(gm_current)]
        else:
            return None

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(range(len(quarters)), margins, color=NAVY, linewidth=2.5, marker="o",
            markersize=8, markerfacecolor=NAVY, markeredgecolor="white", markeredgewidth=1.5)

    # Data labels
    for i, (q, m) in enumerate(zip(quarters, margins)):
        ax.annotate(f"{m:.1f}%", (i, m), textcoords="offset points",
                    xytext=(0, 12), ha="center", fontsize=10, fontweight="bold")

    ax.set_xticks(range(len(quarters)))
    ax.set_xticklabels(quarters, fontsize=9, rotation=30 if len(quarters) > 6 else 0)
    ax.set_ylabel("Gross Margin (%)", fontsize=11)
    ax.set_title(f"Gross Margin Trend", fontsize=14, fontweight="bold", pad=15)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.1f}%"))
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_source(fig, f"Source: {company_name} Quarterly Earnings Releases")
    path = os.path.join(output_dir, "chart4_margin.png")
    _save_chart(fig, path)
    return {
        "filename": "chart4_margin.png",
        "title": "Gross Margin Trend",
        "caption": f"Figure 4: Gross Margin Trend — Profitability Trajectory",
        "path": path,
    }


def _chart_fcf(earnings, company_name, ticker, output_dir, plt):
    """Chart 5: Free Cash Flow bar chart."""
    oh = earnings.get("operating_highlights", {})
    oht = oh.get("table", [])

    # Extract FCF or operating cash flow data
    fcf_rows = [r for r in oht if "cash" in str(r.get("metric", "")).lower() or "fcf" in str(r.get("metric", "")).lower()]
    if not fcf_rows:
        return None

    metrics = []
    for r in oht:
        metric = r.get("metric", "")
        fy_val = _safe_float(r.get("fy_value"))
        prior_val = _safe_float(r.get("prior_fy_value"))
        if fy_val > 0 or prior_val > 0:
            metrics.append({"metric": metric, "current": fy_val, "prior": prior_val})

    if not metrics:
        return None

    # Focus on key cash flow metrics
    names = [m["metric"] for m in metrics[:6]]
    current = [m["current"] for m in metrics[:6]]
    prior = [m["prior"] for m in metrics[:6]]

    # Normalize to billions/millions
    max_val = max(max(current), max(prior)) if current and prior else 1
    if max_val > 1e9:
        current = [v / 1e9 for v in current]
        prior = [v / 1e9 for v in prior]
        unit = "$B"
    elif max_val > 1e6:
        current = [v / 1e6 for v in current]
        prior = [v / 1e6 for v in prior]
        unit = "$M"
    else:
        unit = "$"

    n = len(names)
    x = range(n)
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 6))
    bars1 = ax.bar([i - width / 2 for i in x], prior, width, label="Prior FY", color=GRAY, edgecolor="none")
    bars2 = ax.bar([i + width / 2 for i in x], current, width, label="Current FY", color=NAVY, edgecolor="none")

    # Data labels on current
    for bar, val in zip(bars2, current):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(current) * 0.02,
                    f"{unit[0]}{val:,.1f}{unit[1:]}" if len(unit) > 1 else f"${val:,.1f}",
                    ha="center", va="bottom", fontsize=9, fontweight="bold")

    ax.set_xticks(range(n))
    ax.set_xticklabels(names, fontsize=9, rotation=20 if n > 4 else 0)
    ax.set_ylabel(unit, fontsize=11)
    ax.set_title(f"Operating Highlights — Year-over-Year", fontsize=14, fontweight="bold", pad=15)
    ax.legend(loc="upper right", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_source(fig, f"Source: {company_name} Earnings Release")
    path = os.path.join(output_dir, "chart5_fcf.png")
    _save_chart(fig, path)
    return {
        "filename": "chart5_fcf.png",
        "title": "Operating Highlights",
        "caption": f"Figure 5: Key Operating Metrics — Year-over-Year Comparison",
        "path": path,
    }


def _chart_peer_valuation(earnings, company_name, ticker, output_dir, plt):
    """Chart 6: Peer forward P/E comparison horizontal bar."""
    val = earnings.get("valuation", {})
    peers = val.get("peer_table", [])
    if not peers or len(peers) < 2:
        return None

    names = []
    fwd_pes = []
    for p in peers:
        pe = _safe_float(p.get("forward_pe"))
        if pe > 0:
            company = str(p.get("company", ""))
            # Remove ticker in parens if present
            if "(" in company:
                company = company[:company.index("(")].strip()
            names.append(company)
            fwd_pes.append(pe)

    if len(names) < 2:
        return None

    # Sort by P/E
    paired = sorted(zip(names, fwd_pes), key=lambda x: x[1])
    names, fwd_pes = zip(*paired)
    names = list(names)
    fwd_pes = list(fwd_pes)

    # Color the target company differently
    colors = []
    for name in names:
        if ticker.upper() in name.upper() or company_name.upper() in name.upper():
            colors.append(NAVY)
        else:
            colors.append(BLUE)

    fig, ax = plt.subplots(figsize=(10, max(5, len(names) * 0.7)))
    bars = ax.barh(range(len(names)), fwd_pes, color=colors, height=0.5, edgecolor="none")

    for bar, val_pe in zip(bars, fwd_pes):
        ax.text(val_pe + max(fwd_pes) * 0.02, bar.get_y() + bar.get_height() / 2,
                f"{val_pe:.1f}x", ha="left", va="center", fontsize=11, fontweight="bold")

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=10)
    ax.set_xlabel("Forward P/E", fontsize=11)
    ax.set_title(f"Forward P/E — Peer Comparison", fontsize=14, fontweight="bold", pad=15)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_source(fig, "Source: Yahoo Finance, StockAnalysis.com")
    path = os.path.join(output_dir, "chart6_peer_pe.png")
    _save_chart(fig, path)
    return {
        "filename": "chart6_peer_pe.png",
        "title": "Peer Forward P/E",
        "caption": f"Figure 6: Forward P/E — Peer Valuation Comparison",
        "path": path,
    }


def _chart_estimates(earnings, company_name, ticker, output_dir, plt):
    """Chart 7: Updated estimates grouped bar chart (Revenue and EPS projections)."""
    import matplotlib.ticker as mticker

    ue = earnings.get("updated_estimates", {})
    uet = ue.get("table", [])
    if not uet:
        return None

    # Find revenue row
    rev_row = None
    eps_row = None
    for row in uet:
        metric = str(row.get("metric", "")).lower()
        if "revenue" in metric and "growth" not in metric:
            rev_row = row
        elif "eps" in metric and "growth" not in metric:
            eps_row = row

    if not rev_row and not eps_row:
        return None

    # Use revenue for the chart
    target = rev_row or eps_row
    labels = []
    values = []

    if target.get("fy_actual_label") and target.get("fy_actual"):
        labels.append(target["fy_actual_label"])
        values.append(_safe_float(target["fy_actual"]))
    if target.get("fy1_label") and target.get("fy1_estimate"):
        labels.append(target["fy1_label"])
        values.append(_safe_float(target["fy1_estimate"]))
    if target.get("fy2_label") and target.get("fy2_estimate"):
        labels.append(target["fy2_label"])
        values.append(_safe_float(target["fy2_estimate"]))

    if len(labels) < 2:
        return None

    # Normalize
    max_val = max(values) if values else 1
    if max_val > 1e9:
        values = [v / 1e9 for v in values]
        unit = "$B"
    elif max_val > 1e6:
        values = [v / 1e6 for v in values]
        unit = "$M"
    else:
        unit = "$"

    colors = [GRAY if "A" in l else BLUE for l in labels]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(range(len(labels)), values, color=colors, width=0.5, edgecolor="none")

    for bar, val in zip(bars, values):
        label_text = f"{unit[0]}{val:,.1f}{unit[1:]}" if len(unit) > 1 else f"${val:,.1f}"
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values) * 0.02,
                label_text, ha="center", va="bottom", fontsize=11, fontweight="bold")

    metric_name = target.get("metric", "Revenue")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel(unit, fontsize=11)
    ax.set_title(f"{metric_name} — Actual vs. Estimates", fontsize=14, fontweight="bold", pad=15)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_source(fig, f"Source: {company_name} filings, analyst consensus estimates")
    path = os.path.join(output_dir, "chart7_estimates.png")
    _save_chart(fig, path)
    return {
        "filename": "chart7_estimates.png",
        "title": f"{metric_name} Estimates",
        "caption": f"Figure 7: {metric_name} — Forward Estimates",
        "path": path,
    }


def _chart_price_target(earnings, company_name, ticker, output_dir, plt):
    """Chart 8: Price target methodology waterfall/bar."""
    val = earnings.get("valuation", {})
    ptm = val.get("price_target_methodology", [])
    blended = val.get("blended_target")

    if not ptm or len(ptm) < 2:
        return None

    methods = [m.get("methodology", "")[:30] for m in ptm]
    implied = [_safe_float(m.get("implied_value")) for m in ptm]
    weights = [_safe_float(m.get("weight")) for m in ptm]

    if not any(v > 0 for v in implied):
        return None

    colors = [LIGHT_NAVY, BLUE, GREEN, "#FF9800", "#AB47BC"][:len(methods)]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(range(len(methods)), implied, color=colors, width=0.5, edgecolor="none")

    for bar, val_v, w in zip(bars, implied, weights):
        if val_v > 0:
            w_text = f"{w:.0f}%" if w > 1 else f"{w*100:.0f}%"
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(implied) * 0.02,
                    f"${val_v:.0f}\n({w_text})", ha="center", va="bottom", fontsize=10, fontweight="bold")

    # Blended target line
    if blended and blended > 0:
        ax.axhline(y=float(blended), color=RED, linestyle="--", linewidth=1.5,
                    label=f"Blended Target: ${blended:.0f}")
        ax.legend(loc="upper right", fontsize=9)

    ax.set_xticks(range(len(methods)))
    ax.set_xticklabels(methods, fontsize=8, rotation=15)
    ax.set_ylabel("Implied Value ($)", fontsize=11)
    ax.set_title(f"Price Target Methodology — {ticker}", fontsize=14, fontweight="bold", pad=15)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_source(fig, f"Source: Analyst estimates and valuation models")
    path = os.path.join(output_dir, "chart8_pt.png")
    _save_chart(fig, path)
    return {
        "filename": "chart8_pt.png",
        "title": "Price Target",
        "caption": f"Figure 8: Price Target Methodology — Blended Approach",
        "path": path,
    }
