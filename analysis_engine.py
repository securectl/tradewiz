"""
Stock Breakout Analysis Engine
Detects symmetrical triangles, ascending/descending triangles, pennants, wedges.
Calculates breakout levels, price targets, stop losses, and confidence scores.
"""

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.signal import argrelextrema
from scipy.stats import linregress
from datetime import datetime, timedelta
import json


SECTOR_BENCHMARKS = {
    "Technology": {"pe": 30, "pb": 8, "ps": 7, "margin": 0.20, "roe": 0.18, "de": 50, "ev_ebitda": 22},
    "Healthcare": {"pe": 22, "pb": 4, "ps": 4, "margin": 0.12, "roe": 0.15, "de": 60, "ev_ebitda": 16},
    "Financial Services": {"pe": 13, "pb": 1.3, "ps": 3, "margin": 0.25, "roe": 0.12, "de": 150, "ev_ebitda": 10},
    "Consumer Cyclical": {"pe": 20, "pb": 4, "ps": 1.5, "margin": 0.08, "roe": 0.20, "de": 80, "ev_ebitda": 14},
    "Consumer Defensive": {"pe": 22, "pb": 5, "ps": 2, "margin": 0.10, "roe": 0.25, "de": 70, "ev_ebitda": 15},
    "Industrials": {"pe": 20, "pb": 4, "ps": 2, "margin": 0.10, "roe": 0.15, "de": 80, "ev_ebitda": 14},
    "Energy": {"pe": 10, "pb": 1.5, "ps": 1.2, "margin": 0.08, "roe": 0.12, "de": 50, "ev_ebitda": 6},
    "Basic Materials": {"pe": 14, "pb": 2, "ps": 1.5, "margin": 0.10, "roe": 0.10, "de": 50, "ev_ebitda": 8},
    "Utilities": {"pe": 18, "pb": 1.8, "ps": 2.5, "margin": 0.12, "roe": 0.10, "de": 120, "ev_ebitda": 12},
    "Real Estate": {"pe": 35, "pb": 2, "ps": 8, "margin": 0.25, "roe": 0.06, "de": 100, "ev_ebitda": 20},
    "Communication Services": {"pe": 18, "pb": 3, "ps": 3, "margin": 0.15, "roe": 0.12, "de": 70, "ev_ebitda": 12},
}


def fetch_fundamentals(ticker: str) -> dict:
    """Fetch fundamental data for 12-month investment analysis."""
    stock = yf.Ticker(ticker)
    info = stock.info

    current_price = info.get("currentPrice", info.get("regularMarketPrice", 0))

    # Valuation
    pe_trailing = info.get("trailingPE")
    pe_forward = info.get("forwardPE")
    peg = info.get("pegRatio")
    pb = info.get("priceToBook")
    ps = info.get("priceToSalesTrailing12Months")
    ev_ebitda = info.get("enterpriseToEbitda")
    ev_revenue = info.get("enterpriseToRevenue")

    # Profitability
    revenue_growth = info.get("revenueGrowth")
    gross_margin = info.get("grossMargins")
    operating_margin = info.get("operatingMargins")
    net_margin = info.get("profitMargins")
    roe = info.get("returnOnEquity")
    roa = info.get("returnOnAssets")

    # Balance Sheet
    total_cash = info.get("totalCash", 0)
    total_debt = info.get("totalDebt", 0)
    de_ratio = info.get("debtToEquity")
    current_ratio = info.get("currentRatio")
    book_value = info.get("bookValue")

    # Cash Flow
    operating_cf = info.get("operatingCashflow", 0)
    free_cf = info.get("freeCashflow", 0)

    # Risk
    beta = info.get("beta")
    short_ratio = info.get("shortRatio")
    # Short interest (reported ~twice a month at exchange settlement dates).
    # yfinance's .info carries the current + prior-month figures; we only ever
    # extracted shortRatio before. Pull the rest so the analyzer can show a
    # short-vs-bull positioning read with a real (bi-monthly) trend.
    shares_short = info.get("sharesShort")
    shares_short_prior = info.get("sharesShortPriorMonth")
    short_pct_float = info.get("shortPercentOfFloat")
    short_pct_out = info.get("sharesPercentSharesOut")
    short_interest_date = info.get("dateShortInterest")           # epoch seconds
    short_interest_prior_date = info.get("sharesShortPreviousMonthDate")  # epoch
    fifty_two_high = info.get("fiftyTwoWeekHigh")
    fifty_two_low = info.get("fiftyTwoWeekLow")

    # Analyst
    target_high = info.get("targetHighPrice")
    target_low = info.get("targetLowPrice")
    target_mean = info.get("targetMeanPrice")
    target_median = info.get("targetMedianPrice")
    recommendation = info.get("recommendationKey")
    num_analysts = info.get("numberOfAnalystOpinions")

    # Derived metrics
    cash_to_debt = (total_cash / total_debt) if total_debt and total_debt > 0 else None
    market_cap = info.get("marketCap", 0)
    fcf_yield = (free_cf / market_cap * 100) if market_cap and market_cap > 0 and free_cf else None

    # Burn rate & cash runway (for unprofitable companies)
    quarterly_cf = operating_cf / 4 if operating_cf else 0
    is_burning = quarterly_cf < 0
    burn_rate_monthly = abs(quarterly_cf / 3) if is_burning else 0
    cash_runway_months = (total_cash / burn_rate_monthly) if burn_rate_monthly > 0 else None

    # Debt coverage
    debt_coverage = (operating_cf / total_debt) if total_debt and total_debt > 0 and operating_cf else None

    # Trend data (annual financials)
    trends = {}
    try:
        inc = stock.income_stmt
        if inc is not None and not inc.empty:
            trends["revenue"] = {str(c.year): _safe_num(inc.loc["Total Revenue", c]) for c in inc.columns if "Total Revenue" in inc.index}
            trends["net_income"] = {str(c.year): _safe_num(inc.loc["Net Income", c]) for c in inc.columns if "Net Income" in inc.index}
            if "Gross Profit" in inc.index:
                trends["gross_profit"] = {str(c.year): _safe_num(inc.loc["Gross Profit", c]) for c in inc.columns}
    except Exception:
        pass

    try:
        bs = stock.balance_sheet
        if bs is not None and not bs.empty:
            if "Total Debt" in bs.index:
                trends["total_debt"] = {str(c.year): _safe_num(bs.loc["Total Debt", c]) for c in bs.columns}
            cash_key = "Cash And Cash Equivalents" if "Cash And Cash Equivalents" in bs.index else "Cash Cash Equivalents And Short Term Investments" if "Cash Cash Equivalents And Short Term Investments" in bs.index else None
            if cash_key:
                trends["cash"] = {str(c.year): _safe_num(bs.loc[cash_key, c]) for c in bs.columns}
    except Exception:
        pass

    try:
        cf = stock.cashflow
        if cf is not None and not cf.empty:
            if "Free Cash Flow" in cf.index:
                trends["free_cash_flow"] = {str(c.year): _safe_num(cf.loc["Free Cash Flow", c]) for c in cf.columns}
            elif "Operating Cash Flow" in cf.index:
                trends["operating_cf"] = {str(c.year): _safe_num(cf.loc["Operating Cash Flow", c]) for c in cf.columns}
    except Exception:
        pass

    return {
        "ticker": ticker.upper(),
        "name": info.get("shortName", info.get("longName", ticker)),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "market_cap": market_cap,
        "current_price": current_price,
        "valuation": {
            "pe_trailing": pe_trailing,
            "pe_forward": pe_forward,
            "peg_ratio": peg,
            "price_to_book": pb,
            "price_to_sales": ps,
            "ev_to_ebitda": ev_ebitda,
            "ev_to_revenue": ev_revenue,
        },
        "profitability": {
            "revenue_growth": revenue_growth,
            "gross_margin": gross_margin,
            "operating_margin": operating_margin,
            "net_margin": net_margin,
            "roe": roe,
            "roa": roa,
        },
        "balance_sheet": {
            "total_cash": total_cash,
            "total_debt": total_debt,
            "debt_to_equity": de_ratio,
            "current_ratio": current_ratio,
            "book_value": book_value,
        },
        "cash_flow": {
            "operating_cf": operating_cf,
            "free_cf": free_cf,
        },
        "risk": {
            "beta": beta,
            "short_ratio": short_ratio,
            "shares_short": shares_short,
            "shares_short_prior": shares_short_prior,
            "short_percent_float": short_pct_float,
            "short_percent_outstanding": short_pct_out,
            "short_interest_date": short_interest_date,
            "short_interest_prior_date": short_interest_prior_date,
            "fifty_two_week_high": fifty_two_high,
            "fifty_two_week_low": fifty_two_low,
        },
        "analyst": {
            "target_high": target_high,
            "target_low": target_low,
            "target_mean": target_mean,
            "target_median": target_median,
            "recommendation": recommendation,
            "num_analysts": num_analysts,
        },
        "derived": {
            "cash_to_debt": round(cash_to_debt, 2) if cash_to_debt is not None else None,
            "fcf_yield_pct": round(fcf_yield, 2) if fcf_yield is not None else None,
            "is_burning_cash": is_burning,
            "burn_rate_monthly": round(burn_rate_monthly, 0) if is_burning else None,
            "cash_runway_months": round(cash_runway_months, 1) if cash_runway_months is not None else None,
            "debt_coverage": round(debt_coverage, 2) if debt_coverage is not None else None,
            "debt_to_burn": round(total_debt / burn_rate_monthly, 1) if burn_rate_monthly > 0 else None,
            "interest_coverage": round(info.get("operatingIncome", 0) / info.get("interestExpense", 1), 2) if info.get("interestExpense") else None,
            "quick_ratio": info.get("quickRatio"),
            "gross_margin": gross_margin,
            "operating_margin": operating_margin,
            "revenue_per_share": info.get("revenuePerShare"),
            "earnings_growth": info.get("earningsGrowth"),
        },
        "comparison": _build_comparison(info.get("sector", "N/A"), pe_trailing, pe_forward, pb, ev_ebitda, net_margin, roe, de_ratio),
        "trends": trends,
    }


def fetch_fundamentals_crypto(ticker: str) -> dict:
    """Fetch fundamental data for crypto tickers (BTC-USD, ETH-USD, etc.).
    yfinance returns market data but no traditional financials for crypto."""
    stock = yf.Ticker(ticker)
    info = stock.info

    current_price = info.get("currentPrice", info.get("regularMarketPrice", 0))
    market_cap = info.get("marketCap", 0)
    volume_24h = info.get("volume24Hr", info.get("averageDailyVolume10Day", 0))
    circulating_supply = info.get("circulatingSupply")
    max_supply = info.get("maxSupply")
    fifty_two_high = info.get("fiftyTwoWeekHigh")
    fifty_two_low = info.get("fiftyTwoWeekLow")

    return {
        "ticker": ticker.upper(),
        "name": info.get("shortName", info.get("longName", ticker)),
        "sector": "Cryptocurrency",
        "industry": "Digital Assets",
        "market_cap": market_cap,
        "current_price": current_price,
        "is_crypto": True,
        "market_data": {
            "market_cap": market_cap,
            "volume_24h": volume_24h,
            "circulating_supply": circulating_supply,
            "max_supply": max_supply,
        },
        "performance": {
            "fifty_two_week_high": fifty_two_high,
            "fifty_two_week_low": fifty_two_low,
        },
        # Null out inapplicable sections to prevent renderFinancials() crash
        "valuation": None,
        "profitability": None,
        "balance_sheet": None,
        "cash_flow": None,
        "risk": {
            "beta": info.get("beta"),
            "short_ratio": None,
            "fifty_two_week_high": fifty_two_high,
            "fifty_two_week_low": fifty_two_low,
        },
        "analyst": None,
        "derived": None,
        "trends": {},
    }


def _safe_num(val):
    """Convert pandas/numpy value to Python number, handling NaN."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if f != f else f
    except (ValueError, TypeError):
        return None


def _build_comparison(sector, pe_trailing, pe_forward, pb, ev_ebitda, net_margin, roe, de_ratio):
    """Build sector comparison data for valuation metrics."""
    benchmarks = SECTOR_BENCHMARKS.get(sector, {})
    comparison = {}
    if not benchmarks:
        return comparison

    def _verdict_lower_better(pct_diff):
        return "cheap" if pct_diff < -15 else "expensive" if pct_diff > 15 else "fair"

    if pe_trailing and benchmarks.get("pe"):
        pct_diff = ((pe_trailing - benchmarks["pe"]) / benchmarks["pe"]) * 100
        comparison["pe"] = {"value": pe_trailing, "industry_avg": benchmarks["pe"], "diff_pct": round(pct_diff, 1), "verdict": _verdict_lower_better(pct_diff)}

    if pe_forward and benchmarks.get("pe"):
        pct_diff = ((pe_forward - benchmarks["pe"]) / benchmarks["pe"]) * 100
        comparison["pe_forward"] = {"value": pe_forward, "industry_avg": benchmarks["pe"], "diff_pct": round(pct_diff, 1), "verdict": _verdict_lower_better(pct_diff)}

    if pb and benchmarks.get("pb"):
        pct_diff = ((pb - benchmarks["pb"]) / benchmarks["pb"]) * 100
        comparison["pb"] = {"value": pb, "industry_avg": benchmarks["pb"], "diff_pct": round(pct_diff, 1), "verdict": _verdict_lower_better(pct_diff)}

    if ev_ebitda and benchmarks.get("ev_ebitda"):
        pct_diff = ((ev_ebitda - benchmarks["ev_ebitda"]) / benchmarks["ev_ebitda"]) * 100
        comparison["ev_ebitda"] = {"value": ev_ebitda, "industry_avg": benchmarks["ev_ebitda"], "diff_pct": round(pct_diff, 1), "verdict": _verdict_lower_better(pct_diff)}

    if net_margin is not None and benchmarks.get("margin"):
        comparison["margin"] = {"value": net_margin, "industry_avg": benchmarks["margin"], "diff_pct": round(((net_margin - benchmarks["margin"]) / abs(benchmarks["margin"])) * 100, 1)}

    if roe is not None and benchmarks.get("roe"):
        comparison["roe"] = {"value": roe, "industry_avg": benchmarks["roe"], "diff_pct": round(((roe - benchmarks["roe"]) / abs(benchmarks["roe"])) * 100, 1)}

    if de_ratio is not None and benchmarks.get("de"):
        pct_diff = ((de_ratio - benchmarks["de"]) / benchmarks["de"]) * 100
        comparison["de"] = {"value": de_ratio, "industry_avg": benchmarks["de"], "diff_pct": round(pct_diff, 1), "verdict": _verdict_lower_better(pct_diff)}

    return comparison


def fetch_stock_data(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV data from Yahoo Finance via the resilient cached/retrying
    fetch layer. Raises ValueError for a bad symbol; RateLimited propagates so
    the route can serve a cached analysis."""
    from shared.yf_fetch import get_history
    df = get_history(ticker, period=period, interval=interval)
    if df is None or df.empty:
        raise ValueError(f"No data found for ticker '{ticker}'. Please check the symbol is correct (e.g. AAPL not APPL).")
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df


def get_stock_info(ticker: str) -> dict:
    """Best-effort basic stock info. Never raises — if Yahoo is rate-limited,
    return minimal defaults so analysis can still proceed on price data."""
    defaults = {"name": ticker, "exchange": "N/A", "currency": "USD",
                "sector": "N/A", "industry": "N/A", "market_cap": 0}
    try:
        from shared.yf_fetch import ticker as _yt
        info = _yt(ticker).info or {}
    except Exception:
        return defaults
    return {
        "name": info.get("shortName", info.get("longName", ticker)),
        "exchange": info.get("exchange", "N/A"),
        "currency": info.get("currency", "USD"),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "market_cap": info.get("marketCap", 0),
    }


def find_swing_points(df: pd.DataFrame, order: int = 5) -> tuple:
    """Find swing highs and swing lows using local extrema."""
    highs_idx = argrelextrema(df["High"].values, np.greater_equal, order=order)[0]
    lows_idx = argrelextrema(df["Low"].values, np.less_equal, order=order)[0]
    return highs_idx, lows_idx


def fit_trendline(indices: np.ndarray, values: np.ndarray) -> tuple:
    """Fit a linear trendline, return (slope, intercept, r_squared)."""
    if len(indices) < 2:
        return None, None, 0
    slope, intercept, r_value, _, _ = linregress(indices, values)
    return slope, intercept, r_value ** 2


def check_line_validity(slope, intercept, start_idx, end_idx, df, line_type):
    """Check that no candle between two anchor points cuts through the trendline.
    For resistance: no candle High should exceed the line.
    For support: no candle Low should go below the line.
    Returns True if line is valid (no cuts)."""
    lo = min(start_idx, end_idx)
    hi = max(start_idx, end_idx)
    for idx in range(lo, hi + 1):
        line_val = slope * idx + intercept
        if line_type == "resistance":
            if df["High"].iloc[idx] > line_val:
                return False
        else:  # support
            if df["Low"].iloc[idx] < line_val:
                return False
    return True


def construct_validated_trendline(swing_indices, df, line_type):
    """Build a validated trendline from the 2 most recent swing points.
    Iterates backward through swing point pairs until a valid pair is found.
    line_type: 'resistance' (uses High) or 'support' (uses Low).
    Returns (slope, intercept, anchor_a_idx, anchor_b_idx) or None."""
    if len(swing_indices) < 2:
        return None

    price_col = "High" if line_type == "resistance" else "Low"
    # Sort most recent first
    sorted_pts = sorted(swing_indices, reverse=True)

    # Try pairs: most recent A with earlier B, then move A back
    for i in range(len(sorted_pts) - 1):
        a_idx = int(sorted_pts[i])
        a_val = float(df[price_col].iloc[a_idx])
        for j in range(i + 1, len(sorted_pts)):
            b_idx = int(sorted_pts[j])
            b_val = float(df[price_col].iloc[b_idx])

            if a_idx == b_idx:
                continue

            slope = (a_val - b_val) / (a_idx - b_idx)
            intercept = a_val - slope * a_idx

            if check_line_validity(slope, intercept, b_idx, a_idx, df, line_type):
                return slope, intercept, a_idx, b_idx

    return None


def detect_horizontal_resistance(df: pd.DataFrame, highs_idx: np.ndarray, tolerance_pct: float = 0.03) -> tuple:
    """
    Detect horizontal resistance: multiple swing highs clustering at the same price.
    Returns (level, touches, r_squared) or (None, 0, 0).
    """
    if len(highs_idx) < 2:
        return None, 0, 0

    high_values = df["High"].values[highs_idx]

    # Find the price level with most touches within tolerance
    best_level = None
    best_touches = 0

    for val in high_values:
        tolerance = val * tolerance_pct
        touches = np.sum(np.abs(high_values - val) <= tolerance)
        if touches > best_touches:
            best_touches = touches
            # Use mean of all touches as the level
            mask = np.abs(high_values - val) <= tolerance
            best_level = float(np.mean(high_values[mask]))

    if best_touches >= 2 and best_level is not None:
        # R² of a flat line = how clustered the touches are
        tolerance = best_level * tolerance_pct
        mask = np.abs(high_values - best_level) <= tolerance
        touching_values = high_values[mask]
        if len(touching_values) >= 2:
            variance = np.var(touching_values)
            mean_val = np.mean(touching_values)
            r2 = 1.0 - (variance / max(mean_val ** 2, 0.01)) if mean_val > 0 else 0
            return best_level, int(best_touches), min(r2, 1.0)

    return None, 0, 0


def detect_horizontal_support(df: pd.DataFrame, lows_idx: np.ndarray, tolerance_pct: float = 0.03) -> tuple:
    """Detect horizontal support: multiple swing lows at same price."""
    if len(lows_idx) < 2:
        return None, 0, 0

    low_values = df["Low"].values[lows_idx]
    best_level = None
    best_touches = 0

    for val in low_values:
        tolerance = val * tolerance_pct
        touches = np.sum(np.abs(low_values - val) <= tolerance)
        if touches > best_touches:
            best_touches = touches
            mask = np.abs(low_values - val) <= tolerance
            best_level = float(np.mean(low_values[mask]))

    if best_touches >= 2 and best_level is not None:
        tolerance = best_level * tolerance_pct
        mask = np.abs(low_values - best_level) <= tolerance
        touching_values = low_values[mask]
        if len(touching_values) >= 2:
            variance = np.var(touching_values)
            mean_val = np.mean(touching_values)
            r2 = 1.0 - (variance / max(mean_val ** 2, 0.01)) if mean_val > 0 else 0
            return best_level, int(best_touches), min(r2, 1.0)

    return None, 0, 0


def detect_triangle_pattern(df: pd.DataFrame, min_points: int = 2) -> dict:
    """
    Detect triangle/wedge patterns using two approaches:
    1. Horizontal resistance/support detection (for ascending/descending triangles)
    2. Sloped trendline fitting (for symmetrical triangles, wedges, pennants)
    Picks the best pattern across all methods.
    """
    n = len(df)
    if n < 20:
        return None

    avg_price = float(df["Close"].mean())
    best_pattern = None
    best_score = 0

    # ── APPROACH 1: Horizontal resistance + sloped support (ascending triangle) ──
    # ── APPROACH 2: Horizontal support + sloped resistance (descending triangle) ──
    # Use wider range of swing point orders for major structure
    for order in [3, 5, 7, 10, 13]:
        highs_idx, lows_idx = find_swing_points(df, order=order)
        if len(highs_idx) < 2 or len(lows_idx) < 2:
            continue

        # -- Ascending triangle: flat top + rising bottom --
        h_resistance, h_touches, h_r2 = detect_horizontal_resistance(df, highs_idx)
        if h_resistance is not None and h_touches >= 2:
            lower_result = construct_validated_trendline(lows_idx, df, "support")
            if lower_result is not None:
                lower_slope, lower_intercept, _, _ = lower_result
                if lower_slope > 0:
                    support_at_end = lower_slope * (n - 1) + lower_intercept
                    if support_at_end < h_resistance:
                        score = _score_horizontal_pattern(
                            h_r2, h_touches, 1.0, lows_idx, n, df, "ascending"
                        )
                        if score > best_score:
                            best_score = score
                            best_pattern = _build_horizontal_pattern(
                                df, n, "ascending_triangle", score,
                                h_resistance, 0, h_r2,  # flat resistance
                                lower_slope, lower_intercept, 1.0,
                                highs_idx, lows_idx, is_upper_flat=True
                            )

        # -- Descending triangle: flat bottom + falling top --
        h_support, s_touches, s_r2 = detect_horizontal_support(df, lows_idx)
        if h_support is not None and s_touches >= 2:
            upper_result = construct_validated_trendline(highs_idx, df, "resistance")
            if upper_result is not None:
                upper_slope, upper_intercept, _, _ = upper_result
                if upper_slope < 0:
                    resistance_at_end = upper_slope * (n - 1) + upper_intercept
                    if resistance_at_end > h_support:
                        score = _score_horizontal_pattern(
                            1.0, s_touches, s_r2, highs_idx, n, df, "descending"
                        )
                        if score > best_score:
                            best_score = score
                            best_pattern = _build_horizontal_pattern(
                                df, n, "descending_triangle", score,
                                upper_slope, upper_intercept, 1.0,
                                h_support, 0, s_r2,  # flat support
                                highs_idx, lows_idx, is_upper_flat=False
                            )

    # ── APPROACH 3: Both lines sloped (symmetrical triangle, wedges, pennants) ──
    for order in [2, 3, 4, 5, 6, 7, 8]:
        highs_idx, lows_idx = find_swing_points(df, order=order)
        if len(highs_idx) < min_points or len(lows_idx) < min_points:
            continue

        for lookback_pct in [0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.0]:
            lookback_start = int(n * lookback_pct)
            recent_highs = highs_idx[highs_idx >= lookback_start]
            recent_lows = lows_idx[lows_idx >= lookback_start]

            if len(recent_highs) < 2 or len(recent_lows) < 2:
                continue

            upper_result = construct_validated_trendline(recent_highs, df, "resistance")
            lower_result = construct_validated_trendline(recent_lows, df, "support")
            if upper_result is None or lower_result is None:
                continue

            upper_slope, upper_intercept, upper_anchor_a, upper_anchor_b = upper_result
            lower_slope, lower_intercept, lower_anchor_a, lower_anchor_b = lower_result

            pattern_type = classify_triangle(upper_slope, lower_slope, avg_price)
            if pattern_type is None:
                continue

            pattern_oldest = min(upper_anchor_b, lower_anchor_b)
            upper_at_start = upper_slope * pattern_oldest + upper_intercept
            lower_at_start = lower_slope * pattern_oldest + lower_intercept
            upper_at_end = upper_slope * (n - 1) + upper_intercept
            lower_at_end = lower_slope * (n - 1) + lower_intercept

            if upper_at_end < lower_at_end:
                continue

            # For converging patterns, check gap is narrowing
            if pattern_type in ("symmetrical_triangle", "pennant"):
                start_gap = upper_at_start - lower_at_start
                end_gap = upper_at_end - lower_at_end
                if start_gap > 0 and end_gap >= start_gap:
                    continue

            # Convergence point
            if abs(upper_slope - lower_slope) > 1e-10:
                apex_idx = (lower_intercept - upper_intercept) / (upper_slope - lower_slope)
            else:
                apex_idx = n + 50

            if pattern_type in ("symmetrical_triangle", "pennant") and apex_idx < n * 0.7:
                continue

            score = calculate_pattern_score(
                recent_highs, recent_lows, n, apex_idx, df,
                upper_slope, upper_intercept, lower_slope, lower_intercept
            )

            if score > best_score:
                best_score = score
                pattern_start = min(recent_highs[0], recent_lows[0])
                pattern_zone = df.iloc[pattern_start:]
                pattern_high = float(pattern_zone["High"].max())
                pattern_low = float(pattern_zone["Low"].min())
                pattern_height = pattern_high - pattern_low

                breakout_level = upper_at_end
                support_level = lower_at_end
                price_target = breakout_level + pattern_height
                stop_loss = support_level - (pattern_height * 0.1)

                upper_line_points = []
                lower_line_points = []
                draw_start = min(upper_anchor_b, lower_anchor_b)
                draw_end = min(int(apex_idx) if apex_idx < n + 50 else n + 20, n + 20)
                for idx in range(int(draw_start), draw_end):
                    upper_line_points.append({
                        "idx": idx,
                        "value": round(upper_slope * idx + upper_intercept, 2)
                    })
                    lower_line_points.append({
                        "idx": idx,
                        "value": round(lower_slope * idx + lower_intercept, 2)
                    })

                best_pattern = {
                    "type": pattern_type,
                    "score": round(score, 2),
                    "breakout_level": round(float(breakout_level), 2),
                    "support_level": round(float(support_level), 2),
                    "price_target": round(float(price_target), 2),
                    "stop_loss": round(float(stop_loss), 2),
                    "pattern_height": round(pattern_height, 2),
                    "risk_reward_ratio": round(
                        (price_target - breakout_level) / max(breakout_level - stop_loss, 0.01), 2
                    ),
                    "apex_index": int(apex_idx),
                    "upper_trendline": {
                        "slope": upper_slope, "intercept": upper_intercept,
                        "anchors": [upper_anchor_b, upper_anchor_a],
                        "points": upper_line_points,
                    },
                    "lower_trendline": {
                        "slope": lower_slope, "intercept": lower_intercept,
                        "anchors": [lower_anchor_b, lower_anchor_a],
                        "points": lower_line_points,
                    },
                    "swing_highs": [int(x) for x in recent_highs],
                    "swing_lows": [int(x) for x in recent_lows],
                    "pattern_start_idx": int(pattern_start),
                }

    return best_pattern


def _score_horizontal_pattern(line_r2, h_touches, other_r2, other_idx, n, df, direction):
    """Score a pattern with one horizontal and one sloped line."""
    score = 0
    # Horizontal level quality (more touches = better)
    score += min(h_touches / 4, 1) * 25
    score += line_r2 * 15
    # Sloped line quality
    score += other_r2 * 20
    # Touch points on sloped line
    score += min(len(other_idx) / 5, 1) * 15
    # Recency
    if len(other_idx) > 0:
        recency = other_idx[-1] / n
        score += recency * 15
    # Volume
    if "Volume" in df.columns:
        vol = df["Volume"].values
        mid = n // 2
        if mid > 0 and n > mid + 1:
            early_vol = np.mean(vol[:mid]) if mid > 0 else 1
            late_vol = np.mean(vol[mid:])
            if early_vol > 0:
                vol_decline = 1 - (late_vol / early_vol)
                score += max(0, min(vol_decline, 1)) * 10
    return min(score, 100)


def _build_horizontal_pattern(df, n, pattern_type, score,
                               upper_val_or_slope, upper_intercept_or_zero, upper_r2,
                               lower_val_or_slope, lower_intercept_or_zero, lower_r2,
                               highs_idx, lows_idx, is_upper_flat):
    """Build pattern dict for horizontal resistance/support patterns."""
    pattern_start = min(
        highs_idx[0] if len(highs_idx) > 0 else 0,
        lows_idx[0] if len(lows_idx) > 0 else 0
    )

    # Use first actual swing point for sloped line drawing start (not pattern_start which can be idx 0)
    first_low_idx = int(lows_idx[0]) if len(lows_idx) > 0 else int(pattern_start)
    first_high_idx = int(highs_idx[0]) if len(highs_idx) > 0 else int(pattern_start)

    if is_upper_flat:
        # Ascending: flat resistance at upper_val_or_slope, sloped support
        resistance_level = float(upper_val_or_slope)
        lower_slope = float(lower_val_or_slope)
        lower_intercept = float(lower_intercept_or_zero)
        support_at_end = lower_slope * (n - 1) + lower_intercept

        # Flat resistance: draw from first high touch to beyond current bar
        upper_line_points = [
            {"idx": int(first_high_idx), "value": round(resistance_level, 2)},
            {"idx": n - 1, "value": round(resistance_level, 2)},
            {"idx": n + 15, "value": round(resistance_level, 2)},
        ]
        # Sloped support: for ascending triangles, start from the swing low closest to
        # where price first reaches ~50% of resistance level (where the triangle begins)
        half_resistance = resistance_level * 0.15
        draw_start_idx = first_low_idx
        for li in range(len(lows_idx)):
            sl_idx = int(lows_idx[li])
            actual_low = float(df["Low"].iloc[sl_idx])
            if actual_low >= half_resistance:
                draw_start_idx = sl_idx
                break
        # Use the actual swing low price as the visual start (not regression value)
        start_val = float(df["Low"].iloc[draw_start_idx])
        lower_line_points = [
            {"idx": int(draw_start_idx), "value": round(start_val, 2)},
            {"idx": n - 1, "value": round(support_at_end, 2)},
            {"idx": n + 15, "value": round(lower_slope * (n + 15) + lower_intercept, 2)},
        ]

        breakout_level = resistance_level
        support_level = support_at_end
        u_slope = 0.0
        u_intercept = resistance_level
        l_slope = lower_slope
        l_intercept = lower_intercept
    else:
        # Descending: sloped resistance, flat support at lower_val_or_slope
        support_level_flat = float(lower_val_or_slope)
        upper_slope = float(upper_val_or_slope)
        upper_intercept = float(upper_intercept_or_zero)
        resistance_at_end = upper_slope * (n - 1) + upper_intercept

        # Sloped resistance: draw from first swing high to end, key points only
        upper_line_points = [
            {"idx": int(first_high_idx), "value": round(upper_slope * first_high_idx + upper_intercept, 2)},
            {"idx": n - 1, "value": round(resistance_at_end, 2)},
            {"idx": n + 5, "value": round(upper_slope * (n + 5) + upper_intercept, 2)},
        ]
        # Flat support: draw from first low touch
        lower_line_points = [
            {"idx": int(first_low_idx), "value": round(support_level_flat, 2)},
            {"idx": n - 1, "value": round(support_level_flat, 2)},
            {"idx": n + 15, "value": round(support_level_flat, 2)},
        ]

        breakout_level = resistance_at_end
        support_level = support_level_flat
        u_slope = upper_slope
        u_intercept = upper_intercept
        l_slope = 0.0
        l_intercept = support_level_flat

    pattern_zone = df.iloc[int(pattern_start):]
    pattern_high = float(pattern_zone["High"].max())
    pattern_low = float(pattern_zone["Low"].min())
    pattern_height = pattern_high - pattern_low
    price_target = breakout_level + pattern_height
    stop_loss = support_level - (pattern_height * 0.1)

    return {
        "type": pattern_type,
        "score": round(score, 2),
        "breakout_level": round(float(breakout_level), 2),
        "support_level": round(float(support_level), 2),
        "price_target": round(float(price_target), 2),
        "stop_loss": round(float(stop_loss), 2),
        "pattern_height": round(pattern_height, 2),
        "risk_reward_ratio": round(
            (price_target - breakout_level) / max(breakout_level - stop_loss, 0.01), 2
        ),
        "apex_index": n + 50,
        "upper_trendline": {
            "slope": u_slope, "intercept": u_intercept,
            "r_squared": round(upper_r2, 4), "points": upper_line_points,
        },
        "lower_trendline": {
            "slope": l_slope, "intercept": l_intercept,
            "r_squared": round(lower_r2, 4), "points": lower_line_points,
        },
        "swing_highs": [int(x) for x in highs_idx],
        "swing_lows": [int(x) for x in lows_idx],
        "pattern_start_idx": int(pattern_start),
    }


def classify_triangle(upper_slope: float, lower_slope: float, avg_price: float = 100) -> str:
    """Classify triangle/wedge/channel type based on strict slope relationship table.
    | Pattern              | Resistance Slope | Support Slope                          |
    |----------------------|-----------------|----------------------------------------|
    | Symmetrical Triangle | Negative        | Positive                               |
    | Ascending Triangle   | Flat (~0)       | Positive                               |
    | Descending Triangle  | Negative        | Flat (~0)                              |
    | Rising Wedge         | Positive        | Positive, support slope > resistance   |
    | Falling Wedge        | Negative        | Negative, support steeper (more neg)   |
    | Channel Up           | Positive        | Positive, slopes roughly equal         |
    | Channel Down         | Negative        | Negative, slopes roughly equal         |
    """
    slope_threshold = avg_price * 0.0003

    upper_pos = upper_slope > slope_threshold
    upper_neg = upper_slope < -slope_threshold
    upper_flat = abs(upper_slope) < slope_threshold
    lower_pos = lower_slope > slope_threshold
    lower_neg = lower_slope < -slope_threshold
    lower_flat = abs(lower_slope) < slope_threshold

    # Symmetrical triangle: resistance falling, support rising
    if upper_neg and lower_pos:
        return "symmetrical_triangle"

    # Ascending triangle: flat resistance, rising support
    if upper_flat and lower_pos:
        return "ascending_triangle"

    # Descending triangle: falling resistance, flat support
    if upper_neg and lower_flat:
        return "descending_triangle"

    # Both positive slopes
    if upper_pos and lower_pos:
        slope_ratio = abs(upper_slope) / max(abs(lower_slope), 1e-10)
        # Channel up: slopes roughly equal (ratio 0.5 to 2.0)
        if 0.5 < slope_ratio < 2.0:
            return "channel_up"
        # Rising wedge: support slope > resistance slope (steeper support)
        if lower_slope > upper_slope:
            return "rising_wedge"
        return "channel_up"

    # Both negative slopes
    if upper_neg and lower_neg:
        slope_ratio = abs(upper_slope) / max(abs(lower_slope), 1e-10)
        # Channel down: slopes roughly equal
        if 0.5 < slope_ratio < 2.0:
            return "channel_down"
        # Falling wedge: support steeper (more negative) than resistance
        if lower_slope < upper_slope:
            return "falling_wedge"
        return "channel_down"

    return None


def calculate_pattern_score(
    highs_idx, lows_idx, n, apex_idx, df,
    upper_slope=None, upper_intercept=None,
    lower_slope=None, lower_intercept=None
) -> float:
    """Calculate a confidence score for the pattern (0-100).
    Uses validation-based scoring instead of R²."""
    score = 0

    # Line validity — both lines pass validation (0-25)
    valid_upper = False
    valid_lower = False
    if upper_slope is not None and upper_intercept is not None and len(highs_idx) >= 2:
        valid_upper = check_line_validity(
            upper_slope, upper_intercept,
            int(highs_idx[-1]), int(highs_idx[0]), df, "resistance"
        )
    if lower_slope is not None and lower_intercept is not None and len(lows_idx) >= 2:
        valid_lower = check_line_validity(
            lower_slope, lower_intercept,
            int(lows_idx[-1]), int(lows_idx[0]), df, "support"
        )
    if valid_upper and valid_lower:
        score += 25
    elif valid_upper or valid_lower:
        score += 12

    # Touch points — additional swing points near the line (0-15)
    near_touches = 0
    if upper_slope is not None and upper_intercept is not None:
        for idx in highs_idx:
            line_val = upper_slope * idx + upper_intercept
            if abs(df["High"].iloc[idx] - line_val) < line_val * 0.015:
                near_touches += 1
    if lower_slope is not None and lower_intercept is not None:
        for idx in lows_idx:
            line_val = lower_slope * idx + lower_intercept
            if abs(df["Low"].iloc[idx] - line_val) < line_val * 0.015:
                near_touches += 1
    score += min(near_touches / 6, 1) * 15

    # Recency — pattern should be near current price (0-15)
    last_high = highs_idx[-1]
    last_low = lows_idx[-1]
    recency = max(last_high, last_low) / n
    score += recency * 15

    # Compactness — tight patterns in recent data score higher (0-20)
    # Measures what fraction of the chart the pattern spans; smaller = better
    pattern_start = min(highs_idx[0], lows_idx[0])
    pattern_span = n - pattern_start
    span_ratio = pattern_span / n  # 1.0 = entire chart, 0.1 = last 10%
    if span_ratio <= 0.5:
        score += 20  # Pattern in last 50% of chart — very compact
    elif span_ratio <= 0.7:
        score += 12
    else:
        score += 4  # Wide patterns get minimal compactness bonus

    # Volume declining in consolidation (0-15)
    if "Volume" in df.columns:
        vol = df["Volume"].values
        if pattern_start < n - 5:
            early_vol = np.mean(vol[pattern_start : pattern_start + 5])
            late_vol = np.mean(vol[-5:])
            if early_vol > 0:
                vol_decline = 1 - (late_vol / early_vol)
                score += max(0, min(vol_decline, 1)) * 15

    # Apex distance — not too close, not too far (0-10)
    apex_distance = (apex_idx - n) / n
    if 0.05 < apex_distance < 0.5:
        score += 10
    elif 0 < apex_distance <= 0.05:
        score += 5

    return min(score, 100)


def detect_trendline_tests(df: pd.DataFrame, pattern: dict) -> list:
    """
    Detect significant candles that test key trendlines.
    A trendline test is a candle that:
    - Touches or pierces the support/resistance trendline
    - Has above-average volume (selling/buying pressure)
    - The candle body or wick reaches within ATR tolerance of the trendline
    Returns list of test events with index, type, and details.
    """
    if pattern is None:
        return []

    n = len(df)
    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values
    open_ = df["Open"].values
    volume = df["Volume"].values

    # Calculate ATR for tolerance
    tr_list = []
    for i in range(1, n):
        tr = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
        tr_list.append(tr)
    atr = np.mean(tr_list[-14:]) if len(tr_list) >= 14 else np.mean(tr_list) if tr_list else 1.0

    # Volume average for comparison
    vol_ma = pd.Series(volume).rolling(20).mean().values

    tests = []
    pattern_start = pattern.get("pattern_start_idx", 0)

    # Get trendline parameters
    upper = pattern.get("upper_trendline", {})
    lower = pattern.get("lower_trendline", {})
    u_slope = upper.get("slope", 0)
    u_intercept = upper.get("intercept", 0)
    l_slope = lower.get("slope", 0)
    l_intercept = lower.get("intercept", 0)

    for i in range(max(pattern_start, 1), n):
        candle_range = high[i] - low[i]
        candle_body = abs(close[i] - open_[i])
        is_bearish = close[i] < open_[i]
        is_bullish = close[i] > open_[i]
        vol_ratio = volume[i] / vol_ma[i] if (not np.isnan(vol_ma[i]) and vol_ma[i] > 0) else 0

        # Support trendline test: candle low approaches support line
        support_at_i = l_slope * i + l_intercept
        dist_to_support = low[i] - support_at_i
        support_tolerance = atr * 0.3

        if -support_tolerance <= dist_to_support <= support_tolerance:
            # Candle touches or pierces support
            # Stronger signal if: bearish candle + high volume + bounces (close above support)
            strength = 0
            reasons = []

            pct_move = abs((close[i] - open_[i]) / open_[i] * 100) if open_[i] > 0 else 0
            if is_bearish and (candle_body > atr * 0.5 or pct_move >= 3.0):
                strength += 2
                reasons.append(f"Large bearish candle ({pct_move:.1f}% drop)")
            elif is_bearish and pct_move >= 1.5:
                strength += 1
                reasons.append(f"Bearish candle ({pct_move:.1f}% drop)")
            if vol_ratio >= 1.3:
                strength += 2
                reasons.append(f"High volume ({vol_ratio:.1f}x avg)")
            elif vol_ratio >= 1.0:
                strength += 1
                reasons.append(f"Above-avg volume ({vol_ratio:.1f}x)")
            if close[i] > support_at_i:
                strength += 1
                reasons.append("Bounced — closed above support")
            if dist_to_support < 0:
                strength += 1
                reasons.append("Pierced support (wick below)")

            # Check if next candle bounced (if available)
            if i + 1 < n and close[i + 1] > close[i]:
                strength += 1
                reasons.append("Next candle recovered")

            if strength >= 3:
                tests.append({
                    "idx": int(i),
                    "type": "support_test",
                    "label": "Support Test",
                    "price": round(float(low[i]), 2),
                    "trendline_value": round(float(support_at_i), 2),
                    "strength": min(strength, 5),
                    "volume_ratio": round(float(vol_ratio), 2),
                    "candle_change_pct": round(float(pct_move), 2),
                    "reasons": reasons,
                    "held": bool(close[i] > support_at_i),
                })

        # Resistance trendline test: candle high approaches resistance line
        resistance_at_i = u_slope * i + u_intercept
        dist_to_resistance = resistance_at_i - high[i]
        resistance_tolerance = atr * 0.3

        if -resistance_tolerance <= dist_to_resistance <= resistance_tolerance:
            strength = 0
            reasons = []

            if is_bullish and candle_body > atr * 0.5:
                strength += 2
                reasons.append("Large bullish candle testing resistance")
            if is_bearish and candle_body > atr * 0.5:
                strength += 2
                reasons.append("Rejection candle at resistance")
            if vol_ratio >= 1.3:
                strength += 2
                reasons.append(f"High volume ({vol_ratio:.1f}x avg)")
            elif vol_ratio >= 1.0:
                strength += 1
                reasons.append(f"Above-avg volume ({vol_ratio:.1f}x)")
            if close[i] < resistance_at_i:
                strength += 1
                reasons.append("Rejected — closed below resistance")
            if dist_to_resistance < 0:
                reasons.append("Pierced resistance (wick above)")

            if strength >= 3:
                tests.append({
                    "idx": int(i),
                    "type": "resistance_test",
                    "label": "Resistance Test",
                    "price": round(float(high[i]), 2),
                    "trendline_value": round(float(resistance_at_i), 2),
                    "strength": min(strength, 5),
                    "volume_ratio": round(float(vol_ratio), 2),
                    "candle_change_pct": round(float(abs((close[i] - open_[i]) / open_[i] * 100)) if open_[i] > 0 else 0, 2),
                    "reasons": reasons,
                    "held": bool(close[i] < resistance_at_i),
                })

    # Deduplicate: keep only the strongest test within a 5-bar window per type
    deduped = []
    for t in tests:
        too_close = False
        for existing in deduped:
            if existing["type"] == t["type"] and abs(existing["idx"] - t["idx"]) < 5:
                # Keep the stronger one
                if t["strength"] > existing["strength"]:
                    deduped.remove(existing)
                else:
                    too_close = True
                break
        if not too_close:
            deduped.append(t)
    tests = deduped

    # Sort by weighted score: strength + recency bonus (recent tests matter more)
    # Tests in last 5 bars get a significant boost — current price action is critical
    for t in tests:
        recency_bonus = t["idx"] / n  # 0.0 (oldest) to 1.0 (newest)
        last_bar_bonus = 3 if t["idx"] >= n - 5 else 0
        t["_sort_score"] = t["strength"] + recency_bonus * 2 + last_bar_bonus
    tests.sort(key=lambda x: x["_sort_score"], reverse=True)
    # Clean up internal sort key
    for t in tests:
        del t["_sort_score"]
    return tests[:5]


def calculate_indicators(df: pd.DataFrame) -> dict:
    """Calculate technical indicators matching the grounding data."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    n = len(df)

    # ATR (14)
    tr_list = []
    for i in range(1, n):
        tr = max(
            high.iloc[i] - low.iloc[i],
            abs(high.iloc[i] - close.iloc[i - 1]),
            abs(low.iloc[i] - close.iloc[i - 1]),
        )
        tr_list.append(tr)
    tr_series = pd.Series(tr_list)
    atr_14 = tr_series.rolling(14).mean().iloc[-1] if len(tr_series) >= 14 else 0

    # TR(4) — 4-period true range
    tr_4 = tr_series.rolling(4).mean().iloc[-1] if len(tr_series) >= 4 else 0

    # ADR% (Average Daily Range as percentage)
    daily_range_pct = ((high - low) / close * 100).rolling(14).mean().iloc[-1]

    # Volume and Volume MA(10)
    current_vol = int(volume.iloc[-1])
    vol_ma_10 = int(volume.rolling(10).mean().iloc[-1])

    # Moving averages (SMA 8 per Trend Breaker strategy, EMA 20 per Trend Breaker)
    sma_8 = close.rolling(8).mean().iloc[-1] if n >= 8 else close.iloc[-1]
    ema_9 = close.ewm(span=9).mean().iloc[-1]
    ema_20 = close.ewm(span=20).mean().iloc[-1]
    sma_20 = close.rolling(20).mean().iloc[-1] if n >= 20 else close.iloc[-1]
    sma_50 = close.rolling(50).mean().iloc[-1] if n >= 50 else close.iloc[-1]
    # When there aren't 200 bars (e.g. a 6-month daily window has ~124), a true
    # 200-day SMA can't be formed. Fall back to the average of the bars we DO
    # have — NOT the current price, which would fabricate a fake trend (price
    # above/below a "200-MA" that is really just today's close). Consumers do
    # arithmetic on this, so it must stay numeric. analyze_ticker also feeds a
    # long daily window here so the trend read is genuine for the analyzer.
    sma_200 = close.rolling(200).mean().iloc[-1] if n >= 200 else close.mean()

    # SMA(8)/EMA(20) crossover detection (Trend Breaker signal)
    sma_8_series = close.rolling(8).mean()
    ema_20_series = close.ewm(span=20).mean()
    sma8_above_ema20 = float(sma_8) > float(ema_20)
    sma8_ema20_cross = False
    if n >= 10:
        prev_sma8 = float(sma_8_series.iloc[-2]) if not pd.isna(sma_8_series.iloc[-2]) else 0
        prev_ema20 = float(ema_20_series.iloc[-2]) if not pd.isna(ema_20_series.iloc[-2]) else 0
        # Bullish crossover: SMA8 crossed above EMA20
        if prev_sma8 <= prev_ema20 and float(sma_8) > float(ema_20):
            sma8_ema20_cross = True
        # Bearish crossover: SMA8 crossed below EMA20
        if prev_sma8 >= prev_ema20 and float(sma_8) < float(ema_20):
            sma8_ema20_cross = True

    # RSI (14)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = (100 - (100 / (1 + rs))).iloc[-1]

    # MACD (12, 26, 9 per Trend Breaker strategy)
    ema_12 = close.ewm(span=12).mean()
    ema_26 = close.ewm(span=26).mean()
    macd_line = ema_12 - ema_26
    signal_line = macd_line.ewm(span=9).mean()
    macd_histogram = macd_line - signal_line

    # MACD crossover detection (Trend Breaker: MACD lines must cross before breakout)
    macd_bullish_cross = False
    macd_bearish_cross = False
    if n >= 3:
        prev_macd = float(macd_line.iloc[-2])
        prev_signal = float(signal_line.iloc[-2])
        curr_macd = float(macd_line.iloc[-1])
        curr_signal = float(signal_line.iloc[-1])
        if prev_macd <= prev_signal and curr_macd > curr_signal:
            macd_bullish_cross = True
        if prev_macd >= prev_signal and curr_macd < curr_signal:
            macd_bearish_cross = True

    # Relative volume
    rel_vol = current_vol / vol_ma_10 if vol_ma_10 > 0 else 0

    return {
        "atr_14": round(atr_14, 6),
        "tr_4": round(tr_4, 6),
        "adr_pct": round(daily_range_pct, 2),
        "volume": current_vol,
        "vol_ma_10": vol_ma_10,
        "relative_volume": round(rel_vol, 2),
        "sma_8": round(sma_8, 2),
        "ema_9": round(ema_9, 2),
        "ema_20": round(ema_20, 2),
        "sma_20": round(sma_20, 2),
        "sma_50": round(sma_50, 2),
        "sma_200": round(sma_200, 2),
        "sma8_above_ema20": sma8_above_ema20,
        "sma8_ema20_cross": sma8_ema20_cross,
        "rsi_14": round(rsi, 2),
        "macd": round(macd_line.iloc[-1], 4),
        "macd_signal": round(signal_line.iloc[-1], 4),
        "macd_histogram": round(macd_histogram.iloc[-1], 4),
        "macd_bullish_cross": macd_bullish_cross,
        "macd_bearish_cross": macd_bearish_cross,
        # Bollinger Bands (20, 2)
        "bb_upper": round(float(sma_20 + 2 * close.rolling(20).std().iloc[-1]) if n >= 20 else 0, 6),
        "bb_lower": round(float(sma_20 - 2 * close.rolling(20).std().iloc[-1]) if n >= 20 else 0, 6),
        "bb_mid": round(float(sma_20), 6),
        "bb_width": round(float((4 * close.rolling(20).std().iloc[-1]) / sma_20 * 100) if n >= 20 and sma_20 > 0 else 0, 2),
    }


def detect_breakout_status(df: pd.DataFrame, pattern: dict) -> dict:
    """Determine if a breakout has occurred or is pending.
    Uses multi-candle confirmation (3 closes above/below) per Trend Breaker strategy
    and requires volume surge (1.5x avg) per Breakout Trading Strategy.
    """
    if pattern is None:
        return {"status": "no_pattern", "message": "No clear pattern detected"}

    n = len(df)
    close = df["Close"].values
    volume = df["Volume"].values
    current_close = float(close[-1])
    current_vol = float(volume[-1])
    avg_vol = float(df["Volume"].rolling(10).mean().iloc[-1])

    breakout_level = pattern["breakout_level"]
    support_level = pattern["support_level"]

    # Count consecutive candle closes above/below levels (Trend Breaker: 3 candle rule)
    candles_above_breakout = 0
    for i in range(n - 1, max(n - 6, -1), -1):
        if float(close[i]) > breakout_level:
            candles_above_breakout += 1
        else:
            break

    candles_below_support = 0
    for i in range(n - 1, max(n - 6, -1), -1):
        if float(close[i]) < support_level:
            candles_below_support += 1
        else:
            break

    # Volume confirmation requires 1.5x average (strong surge)
    vol_confirm = current_vol > avg_vol * 1.5

    # Check for upside breakout
    if current_close > breakout_level:
        confirmed = candles_above_breakout >= 3 and vol_confirm
        partially_confirmed = candles_above_breakout >= 3 or vol_confirm

        if confirmed:
            msg = f"BREAKOUT ABOVE {breakout_level:.2f} — confirmed ({candles_above_breakout} candles + volume surge)"
        elif candles_above_breakout >= 3:
            msg = f"BREAKOUT ABOVE {breakout_level:.2f} — {candles_above_breakout} candles confirmed, awaiting volume"
        elif vol_confirm:
            msg = f"BREAKOUT ABOVE {breakout_level:.2f} — volume confirmed, needs {3 - candles_above_breakout} more candle(s)"
        else:
            msg = f"BREAKOUT ABOVE {breakout_level:.2f} — early, needs confirmation ({candles_above_breakout}/3 candles, no volume surge)"

        return {
            "status": "breakout_up",
            "confirmed": confirmed,
            "partially_confirmed": partially_confirmed,
            "candles_above": candles_above_breakout,
            "volume_confirmed": vol_confirm,
            "message": msg,
            "direction": "bullish",
        }

    # Check for downside breakdown
    if current_close < support_level:
        confirmed = candles_below_support >= 3 and current_vol > avg_vol * 1.5

        return {
            "status": "breakdown",
            "confirmed": confirmed,
            "candles_below": candles_below_support,
            "volume_confirmed": current_vol > avg_vol * 1.5,
            "message": f"BREAKDOWN BELOW support at {support_level:.2f}" + (
                f" — confirmed ({candles_below_support} candles)" if confirmed
                else f" — needs confirmation ({candles_below_support}/3 candles)"
            ),
            "direction": "bearish",
        }

    # Price within the pattern
    distance_to_breakout = (breakout_level - current_close) / current_close * 100
    distance_to_support = (current_close - support_level) / current_close * 100
    compression = distance_to_breakout + distance_to_support

    if compression < 3:
        urgency = "IMMINENT"
    elif compression < 6:
        urgency = "APPROACHING"
    else:
        urgency = "FORMING"

    return {
        "status": "consolidating",
        "urgency": urgency,
        "distance_to_breakout_pct": round(distance_to_breakout, 2),
        "distance_to_support_pct": round(distance_to_support, 2),
        "compression_pct": round(compression, 2),
        "message": f"{urgency} — Price consolidating. {distance_to_breakout:.1f}% below breakout level.",
        "direction": "neutral",
    }


def generate_trade_plan(df: pd.DataFrame, pattern: dict, indicators: dict, info: dict) -> dict:
    """Generate a detailed trade plan with entry, TP1/TP2/TP3, stop loss, and position sizing."""
    if pattern is None:
        return None

    current_price = float(df["Close"].iloc[-1])
    breakout_level = pattern["breakout_level"]
    support_level = pattern["support_level"]
    pattern_height = pattern["pattern_height"]
    atr = indicators.get("atr_14", 0)

    # ── Entry Price ──
    # Recommended entry: just above the breakout level with a small buffer
    entry_buffer = atr * 0.1 if atr > 0 else pattern_height * 0.02
    entry_price = breakout_level + entry_buffer

    # ── Stop Loss ──
    # Use the strongest nearby support from the last 4-5 swing lows.
    # Pick the highest swing low that's still below the breakout level (tightest valid stop).
    # Fall back to support trendline minus ATR buffer if no swing lows available.
    swing_lows = pattern.get("swing_lows", [])
    swing_low_prices = []
    for sl_idx in swing_lows:
        if sl_idx < len(df):
            sl_price = float(df["Low"].iloc[sl_idx])
            if sl_price < breakout_level:
                swing_low_prices.append(sl_price)

    if swing_low_prices:
        # Use last 4-5 swing lows to find the best support cluster
        recent_lows = swing_low_prices[-5:]
        # Pick the highest swing low below breakout (tightest stop = best R:R)
        # but validate it's a real support (at least 2 lows within 2% of each other)
        best_stop_ref = max(recent_lows)
        tolerance = best_stop_ref * 0.02
        cluster = [p for p in recent_lows if abs(p - best_stop_ref) <= tolerance]
        if len(cluster) >= 2:
            # Strong support cluster — use the mean
            best_stop_ref = sum(cluster) / len(cluster)
        stop_buffer = atr * 0.25 if atr > 0 else best_stop_ref * 0.005
        stop_loss = best_stop_ref - stop_buffer
    else:
        stop_buffer = atr * 0.5 if atr > 0 else pattern_height * 0.1
        stop_loss = support_level - stop_buffer

    # Sanity check: stop loss should not be more than 10% below entry
    max_stop_distance = entry_price * 0.10
    if entry_price - stop_loss > max_stop_distance:
        stop_loss = entry_price - max_stop_distance

    # ── Take Profit Levels (TP1 / TP2 / TP3) ──
    # TP1: 0.618 Fibonacci extension of pattern height (conservative, secure partial profits)
    # TP2: 1.0x pattern height from breakout (measured move — classic target)
    # TP3: 1.618 Fibonacci extension (aggressive, let runners ride)
    tp1 = breakout_level + (pattern_height * 0.618)
    tp2 = breakout_level + pattern_height          # full measured move
    tp3 = breakout_level + (pattern_height * 1.618)

    # Risk / Reward calculations
    risk_per_share = entry_price - stop_loss
    reward_tp1 = tp1 - entry_price
    reward_tp2 = tp2 - entry_price
    reward_tp3 = tp3 - entry_price
    rr_tp1 = reward_tp1 / max(risk_per_share, 0.01)
    rr_tp2 = reward_tp2 / max(risk_per_share, 0.01)
    rr_tp3 = reward_tp3 / max(risk_per_share, 0.01)

    # Position sizing for $25,000 account, risking 1-2% per trade
    account_size = 25000
    risk_pct = 0.015  # 1.5% risk per trade
    risk_amount = account_size * risk_pct
    position_size = int(risk_amount / max(risk_per_share, 0.01))
    position_value = position_size * entry_price

    # R:R minimum check (Breakout Strategy: min 1:2, Trend Breaker: 1:3 ideal)
    rr_warning = None
    if rr_tp2 < 1.5:
        rr_warning = "Poor risk/reward — R:R below 1.5:1. Consider skipping this trade."
    elif rr_tp2 < 2.0:
        rr_warning = "Marginal risk/reward — below 2:1 minimum recommended by breakout strategies."

    # Expected value with tiered exits:
    # Sell 1/3 at TP1, 1/3 at TP2, 1/3 at TP3
    win_rate = 0.55  # Realistic win rate for breakout trading (false breakouts are primary risk)
    avg_reward = (reward_tp1 + reward_tp2 + reward_tp3) / 3
    expected_value = (win_rate * avg_reward - (1 - win_rate) * risk_per_share) * position_size

    # Profit at each TP level (full position size for display)
    profit_tp1 = position_size * reward_tp1
    profit_tp2 = position_size * reward_tp2
    profit_tp3 = position_size * reward_tp3

    return {
        "entry_price": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
        "tp1_pct": round((tp1 - entry_price) / entry_price * 100, 1),
        "tp2_pct": round((tp2 - entry_price) / entry_price * 100, 1),
        "tp3_pct": round((tp3 - entry_price) / entry_price * 100, 1),
        "stop_pct": round((entry_price - stop_loss) / entry_price * 100, 1),
        "rr_tp1": round(rr_tp1, 2),
        "rr_tp2": round(rr_tp2, 2),
        "rr_tp3": round(rr_tp3, 2),
        "risk_per_share": round(risk_per_share, 2),
        "risk_reward_ratio": round(rr_tp2, 2),  # headline R:R uses TP2
        "position_size_shares": position_size,
        "position_value": round(position_value, 2),
        "risk_amount": round(risk_amount, 2),
        "profit_tp1": round(profit_tp1, 2),
        "profit_tp2": round(profit_tp2, 2),
        "profit_tp3": round(profit_tp3, 2),
        "potential_loss": round(position_size * risk_per_share, 2),
        "expected_value": round(expected_value, 2),
        "win_rate_assumed": win_rate,
        "rr_warning": rr_warning,
        "macd_bullish_cross": indicators.get("macd_bullish_cross", False),
        "sma8_ema20_cross": indicators.get("sma8_ema20_cross", False),
        "exit_strategy": "Scale out: 1/3 at TP1, 1/3 at TP2, 1/3 at TP3. Move stop to breakeven after TP1 hit.",
        "grade": grade_setup(pattern, indicators, rr_tp2),
    }


def grade_setup(pattern: dict, indicators: dict, risk_reward: float) -> str:
    """Grade the trade setup from A+ to F.
    Incorporates grounding data rules: R:R minimums, MA crossover signals,
    trendline touch count (3+ per Trend Breaker), and MACD confirmation.
    """
    score = 0

    # Pattern quality
    if pattern["score"] >= 70:
        score += 3
    elif pattern["score"] >= 50:
        score += 2
    elif pattern["score"] >= 30:
        score += 1

    # Risk/Reward (Breakout Strategy: min 1:2, Trend Breaker: 1:3 ideal)
    if risk_reward >= 3:
        score += 3
    elif risk_reward >= 2:
        score += 2
    elif risk_reward >= 1.5:
        score += 1
    # Below 1.5 gets 0 — poor R:R per grounding data

    # Volume trend
    if indicators["relative_volume"] >= 1.5:
        score += 2
    elif indicators["relative_volume"] >= 1.0:
        score += 1

    # RSI not overbought
    if 40 <= indicators["rsi_14"] <= 65:
        score += 2
    elif 30 <= indicators["rsi_14"] <= 70:
        score += 1

    # MA alignment: Trend Breaker uses SMA(8) > EMA(20), plus classic EMA(9) > SMA(20) > SMA(50)
    if indicators["ema_9"] > indicators["sma_20"] > indicators["sma_50"]:
        score += 2
    elif indicators["ema_9"] > indicators["sma_20"]:
        score += 1

    # Trendline touch count bonus (Trend Breaker: minimum 3 touch points)
    total_touches = len(pattern.get("swing_highs", [])) + len(pattern.get("swing_lows", []))
    if total_touches >= 6:
        score += 2
    elif total_touches >= 4:
        score += 1
    # < 4 touches: no bonus (weak pattern per grounding data)

    # MACD/MA crossover confirmation (Trend Breaker: indicators must cross before breakout)
    if indicators.get("macd_bullish_cross") or indicators.get("sma8_ema20_cross"):
        score += 1

    grades = {16: "A+", 15: "A+", 14: "A+", 13: "A", 12: "A", 11: "A",
              10: "B+", 9: "B+", 8: "B", 7: "B-",
              6: "C+", 5: "C", 4: "C-", 3: "D+", 2: "D", 1: "D", 0: "F"}
    return grades.get(min(score, 16), "F")


def detect_candlestick_patterns(df: pd.DataFrame) -> list:
    """Detect Inside Day and Outside Day candlestick patterns (Swing Trade Pro).
    Inside Day: current range inside prior day's range — signals momentum breakout (3-5 days).
    Outside Day: current range engulfs prior day's range — signals reversal.
    """
    n = len(df)
    if n < 3:
        return []

    patterns = []
    high = df["High"].values
    low = df["Low"].values
    close = df["Close"].values
    open_ = df["Open"].values
    volume = df["Volume"].values
    vol_ma = pd.Series(volume).rolling(10).mean().values

    for i in range(1, n):
        curr_range = high[i] - low[i]
        prev_range = high[i - 1] - low[i - 1]

        # Inside Day: current high < prior high AND current low > prior low
        if high[i] < high[i - 1] and low[i] > low[i - 1]:
            # Check for N-Bar Narrow Range (3BNR): is this the narrowest of last 10?
            is_narrow = True
            lookback = min(10, i)
            for j in range(i - lookback, i):
                if j >= 0 and (high[j] - low[j]) < curr_range:
                    is_narrow = False
                    break

            patterns.append({
                "idx": i,
                "type": "inside_day",
                "label": "Inside Day" + (" (Narrow Range)" if is_narrow else ""),
                "signal": "breakout_pending",
                "description": "Range contraction — momentum breakout expected within 3-5 days",
                "narrow_range": is_narrow,
            })

        # Outside Day: current high > prior high AND current low < prior low
        elif high[i] > high[i - 1] and low[i] < low[i - 1]:
            # Bullish outside day: close above prior high
            bullish = close[i] > high[i - 1]
            # Bearish outside day: close below prior low
            bearish = close[i] < low[i - 1]
            vol_ratio = volume[i] / vol_ma[i] if (not np.isnan(vol_ma[i]) and vol_ma[i] > 0) else 0

            direction = "bullish" if bullish else "bearish" if bearish else "neutral"
            patterns.append({
                "idx": i,
                "type": "outside_day",
                "label": f"Outside Day ({direction.title()})",
                "signal": "reversal",
                "direction": direction,
                "description": f"Engulfing range — {direction} reversal signal" + (
                    " with high volume" if vol_ratio >= 1.3 else ""
                ),
                "volume_ratio": round(float(vol_ratio), 2),
            })

        # Doji Detection: body < 10% of candle range
        body = abs(close[i] - open_[i])
        if curr_range > 0 and body < curr_range * 0.10:
            upper_wick = high[i] - max(close[i], open_[i])
            lower_wick = min(close[i], open_[i]) - low[i]
            tiny = curr_range * 0.10

            if lower_wick > 2 * body and upper_wick < tiny:
                doji_sub = "Dragonfly"
                direction = "bullish"
            elif upper_wick > 2 * body and lower_wick < tiny:
                doji_sub = "Gravestone"
                direction = "bearish"
            else:
                doji_sub = "Standard"
                direction = "neutral"

            patterns.append({
                "idx": i,
                "type": "doji",
                "label": f"Doji ({doji_sub})",
                "signal": "reversal",
                "direction": direction,
                "description": f"{doji_sub} doji — {'bullish reversal signal' if direction == 'bullish' else 'bearish reversal signal' if direction == 'bearish' else 'indecision'}",
            })

        # Pump/Dump on Close: volume >= 2x average + large body + close at extreme
        vol_ratio = volume[i] / vol_ma[i] if (not np.isnan(vol_ma[i]) and vol_ma[i] > 0) else 0
        atr_10 = np.mean([high[j] - low[j] for j in range(max(0, i - 10), i + 1)]) if i > 0 else curr_range
        if vol_ratio >= 2.0 and body > atr_10 * 0.5 and curr_range > 0:
            close_pos = (close[i] - low[i]) / curr_range
            if close_pos >= 0.75:
                patterns.append({
                    "idx": i,
                    "type": "pump_close",
                    "label": "Pump on Close",
                    "signal": "momentum",
                    "direction": "bullish",
                    "description": f"Volume surge ({vol_ratio:.1f}x avg) with close in upper range — bullish momentum",
                    "volume_ratio": round(float(vol_ratio), 2),
                })
            elif close_pos <= 0.25:
                patterns.append({
                    "idx": i,
                    "type": "pump_close",
                    "label": "Dump on Close",
                    "signal": "momentum",
                    "direction": "bearish",
                    "description": f"Volume surge ({vol_ratio:.1f}x avg) with close in lower range — bearish momentum",
                    "volume_ratio": round(float(vol_ratio), 2),
                })

    # Return only the most recent 10 patterns
    return patterns[-10:]


def calculate_moving_averages_series(df: pd.DataFrame) -> dict:
    """Calculate full MA series for chart overlay.
    Includes SMA(8) per Trend Breaker strategy.
    """
    close = df["Close"]
    return {
        "sma_8": close.rolling(8).mean().round(2).tolist(),
        "ema_9": close.ewm(span=9).mean().round(2).tolist(),
        "ema_20": close.ewm(span=20).mean().round(2).tolist(),
        "sma_20": close.rolling(20).mean().round(2).tolist(),
        "sma_50": close.rolling(50).mean().round(2).tolist(),
        "sma_100": close.rolling(100).mean().round(2).tolist(),
        "sma_200": close.rolling(200).mean().round(2).tolist(),
    }


def generate_recommendation(indicators: dict, breakout_status: dict, current_price: float,
                            market: dict = None) -> dict:
    """Rule-based BUY / ACCUMULATE / HOLD / REDUCE / SELL signal from the
    technical picture — trend (MA structure), RSI, MACD, breakout, Bollinger.
    Returns action + a -100..100 score + confidence + plain-English reasons,
    so the analyzer can say when to buy, hold, or dispose.

    When ``market`` (the market-condition gauge from shared.market_gauge) is
    supplied, the score is risk-adjusted by the broad-market backdrop: a
    defensive (SELL) tape penalizes bullish setups and caps a marginal BUY,
    while a risk-on (BUY) tape gives a small tailwind. This is what makes the
    analyzer macro-aware so users limit risk in bad markets."""
    indicators = indicators or {}
    score = 0
    reasons = []
    rsi = indicators.get("rsi_14") or 50
    sma20 = indicators.get("sma_20") or 0
    sma50 = indicators.get("sma_50") or 0
    sma200 = indicators.get("sma_200") or 0
    macd_h = indicators.get("macd_histogram") or 0
    bb_up = indicators.get("bb_upper") or 0
    bb_low = indicators.get("bb_lower") or 0
    rel_vol = indicators.get("relative_volume") or 1

    # Trend / MA structure (highest weight)
    if sma50 and sma200:
        if sma50 > sma200:
            score += 20; reasons.append("Uptrend — 50-day MA above 200-day MA")
        else:
            score -= 20; reasons.append("Downtrend — 50-day MA below 200-day MA")
    if sma50 and current_price:
        if current_price > sma50:
            score += 12; reasons.append("Price holding above the 50-day MA")
        else:
            score -= 12; reasons.append("Price below the 50-day MA")
    if sma20 and current_price:
        score += 6 if current_price > sma20 else -6

    # RSI momentum
    if rsi >= 70:
        score -= 8; reasons.append(f"RSI {rsi:.0f} overbought — extended, consider trimming")
    elif rsi >= 55:
        score += 12; reasons.append(f"RSI {rsi:.0f} — strong momentum")
    elif rsi >= 45:
        score += 3; reasons.append(f"RSI {rsi:.0f} — neutral")
    elif rsi >= 30:
        score -= 5; reasons.append(f"RSI {rsi:.0f} — weak momentum")
    else:
        score += 2; reasons.append(f"RSI {rsi:.0f} oversold — possible bounce, but elevated risk")

    # MACD
    if indicators.get("macd_bullish_cross"):
        score += 12; reasons.append("MACD bullish crossover")
    elif indicators.get("macd_bearish_cross"):
        score -= 12; reasons.append("MACD bearish crossover")
    elif macd_h > 0:
        score += 6; reasons.append("MACD histogram positive")
    elif macd_h < 0:
        score -= 6; reasons.append("MACD histogram negative")

    # Breakout structure
    bs = (breakout_status or {}).get("status", "") or ""
    if bs in ("breakout_up", "breakout", "confirmed"):
        score += 15
        reasons.append("Confirmed upside breakout" + (" on strong volume" if rel_vol >= 1.5 else ""))
    elif bs in ("breakdown", "breakout_down"):
        score -= 15; reasons.append("Downside breakdown")

    # Bollinger extremes
    if bb_up and current_price and current_price > bb_up:
        score -= 5; reasons.append("Above the upper Bollinger Band — overextended")
    elif bb_low and current_price and current_price < bb_low:
        score += 3; reasons.append("Below the lower Bollinger Band — stretched")

    # ── Market-condition risk adjustment ──
    # Fold the broad-market gauge into the per-stock score so verdicts are
    # macro-aware. A defensive tape limits risk; a risk-on tape adds conviction.
    market_meta = None
    if market and market.get("available"):
        mstance = market.get("stance")
        mscore = market.get("score", 0)
        market_meta = {"stance": mstance, "score": mscore}
        if mstance == "SELL":
            score -= 15
            reasons.insert(0, f"Market gauge defensive (SELL {mscore:+d}) — macro headwind, limiting risk")
        elif mstance == "BUY":
            score += 8
            reasons.insert(0, f"Market gauge risk-on (BUY {mscore:+d}) — macro tailwind")
        else:
            reasons.insert(0, f"Market gauge neutral (HOLD {mscore:+d})")

    score = max(-100, min(100, score))
    if score >= 35:
        action, label, summary = "BUY", "Buy", "Bullish confluence — favorable entry."
    elif score >= 12:
        action, label, summary = "ACCUMULATE", "Accumulate / Hold", "Leaning bullish — hold or add on strength."
    elif score > -12:
        action, label, summary = "HOLD", "Hold / Neutral", "Mixed signals — hold and wait for confirmation."
    elif score > -35:
        action, label, summary = "REDUCE", "Reduce / Trim", "Leaning bearish — consider trimming exposure."
    else:
        action, label, summary = "SELL", "Sell / Dispose", "Bearish confluence — distribute or avoid."

    # In a defensive market, never let a marginal setup read as an outright BUY.
    if market_meta and market_meta["stance"] == "SELL" and action == "BUY":
        action, label = "ACCUMULATE", "Accumulate / Hold"
        summary = "Bullish setup, but market is risk-off — scale in cautiously, limit size."

    return {
        "action": action, "label": label, "score": score,
        "confidence": int(min(95, 50 + abs(score) // 2)),
        "summary": summary, "reasons": reasons[:6],
        "market": market_meta,
    }


def _combine_flow(eq_signal, options):
    """Fuse the equity money-flow signal with options sentiment into one
    money-in/out read. Options flow is a leading tell, so when it agrees it
    strengthens the call and when it conflicts it's flagged as a divergence."""
    eq_label = {
        "IN": "Money In", "OUT": "Money Out", "PROFIT_TAKING": "Profit-Taking",
        "NEUTRAL": "Neutral", None: "—",
    }
    if not options or not options.get("sentiment"):
        return eq_signal, eq_label.get(eq_signal, "—"), "Equity flow only (no options data)."

    sent = (options.get("sentiment") or "").upper()
    opt_bull, opt_bear = sent == "BULLISH", sent == "BEARISH"
    opt_txt = f"options {sent.lower()} (P/C {options.get('pc_ratio')}, net premium {options.get('net_premium')})"

    if eq_signal == "IN" and opt_bull:
        return "STRONG_IN", "Strong Money In", f"Accumulation confirmed by {opt_txt}."
    if eq_signal in ("OUT", "PROFIT_TAKING") and opt_bear:
        if eq_signal == "PROFIT_TAKING":
            return "TOP", "Topping / Profit-Taking", f"Near highs, equity distributing, and {opt_txt} — likely top."
        return "STRONG_OUT", "Strong Money Out", f"Distribution confirmed by {opt_txt}."
    if eq_signal == "IN" and opt_bear:
        return "DIVERGENCE", "Mixed — equity in, options out", f"Equity accumulation but {opt_txt}."
    if eq_signal in ("OUT", "PROFIT_TAKING") and opt_bull:
        return "DIVERGENCE", "Mixed — equity out, options in", f"Equity weak but {opt_txt}."
    if eq_signal in (None, "NEUTRAL") and opt_bull:
        return "IN", "Money In (options-led)", f"Flat equity flow but {opt_txt}."
    if eq_signal in (None, "NEUTRAL") and opt_bear:
        return "OUT", "Money Out (options-led)", f"Flat equity flow but {opt_txt}."
    return eq_signal or "NEUTRAL", eq_label.get(eq_signal, "Neutral"), f"Equity {eq_label.get(eq_signal,'neutral').lower()}, {opt_txt}."


def compute_money_flow_read(ticker, df, is_crypto=False):
    """Combined money-in/out read for a single ticker: equity flow (Chaikin
    Money Flow + Money Flow Index) fused with premium-weighted options flow.
    Options is a key leading indicator of money moving in/out, so it's folded in
    here. Degrades to equity-only when options data is unavailable (crypto,
    non-optionable names, or a throttled chain). Never raises."""
    try:
        from shared.money_flow import compute_money_flow
        equity = compute_money_flow(df)
    except Exception:
        equity = {"cmf": None, "mfi": None, "mf_signal": None, "mf_label": "—"}

    options = None
    if not is_crypto:
        try:
            from features.watchdog.options_flow import _fetch_options_flow
            of = _fetch_options_flow(ticker)
            if of:
                options = {
                    "sentiment": of.get("sentiment"),
                    "net_premium": of.get("net_premium"),
                    "pc_ratio": of.get("pc_ratio"),
                    "call_value": of.get("call_value"),
                    "put_value": of.get("put_value"),
                }
        except Exception:
            options = None

    signal, label, note = _combine_flow(equity.get("mf_signal"), options)
    return {"equity": equity, "options": options, "signal": signal, "label": label, "note": note}


# Short-interest % of float that fills the bear meter completely. ~20% of float
# short is a heavily-shorted name, so we scale the meter against that ceiling.
_SHORT_FLOAT_FULL = 0.20


def compute_short_vs_bull(ticker, df, is_crypto=False, fundamentals=None, money_flow=None):
    """Side-by-side short-interest (bear positioning) vs bull-interest (bullish
    options flow), plus a 14-day daily buying-vs-selling pressure series.

    Short side — reported short interest from yfinance (exchange settlement
    dates, ~twice a month): shares short, % of float, days-to-cover, and the
    change vs the prior report (a real, if coarse, trend).

    Bull side — premium-weighted options flow (call value vs put value), reused
    from the already-computed money_flow read. Falls back to equity money-flow
    (CMF) when a name has no tradable options (crypto, thin small caps).

    14-day series — a genuine *daily* proxy: the money-flow multiplier (close
    location within each day's range) mapped to a 0–100 bull share. True short
    interest is not reported daily, so this line reads day-to-day accumulation/
    distribution pressure; the reported short level is overlaid separately.

    Never raises — degrades to whatever data is available."""
    try:
        risk = ((fundamentals or {}).get("risk") or {})
        mf = money_flow or {}

        # ── Short side (reported, bi-monthly) ─────────────────────────
        shares_short = risk.get("shares_short")
        shares_short_prior = risk.get("shares_short_prior")
        pct_float = risk.get("short_percent_float")          # fraction, e.g. 0.124
        pct_out = risk.get("short_percent_outstanding")      # fraction
        days_to_cover = risk.get("short_ratio")

        chg_pct = None
        if shares_short and shares_short_prior:
            try:
                chg_pct = round((shares_short - shares_short_prior) / shares_short_prior * 100, 1)
            except ZeroDivisionError:
                chg_pct = None
        trend = None
        if chg_pct is not None:
            trend = "rising" if chg_pct > 1 else "falling" if chg_pct < -1 else "flat"

        pct_float_prior = None
        if pct_float is not None and shares_short and shares_short_prior:
            # Prior % of float, assuming float is roughly stable between reports.
            try:
                pct_float_prior = round(pct_float * shares_short_prior / shares_short * 100, 1)
            except ZeroDivisionError:
                pct_float_prior = None

        short = None
        if shares_short is not None or pct_float is not None or days_to_cover is not None:
            short = {
                "shares_short": shares_short,
                "shares_short_prior": shares_short_prior,
                "percent_float": round(pct_float * 100, 1) if pct_float is not None else None,
                "percent_float_prior": pct_float_prior,
                "percent_outstanding": round(pct_out * 100, 1) if pct_out is not None else None,
                "days_to_cover": round(days_to_cover, 1) if days_to_cover is not None else None,
                "change_pct": chg_pct,
                "trend": trend,
                "fill": min(100, round(pct_float / _SHORT_FLOAT_FULL * 100)) if pct_float is not None else None,
                "reported_date": risk.get("short_interest_date"),
                "prior_date": risk.get("short_interest_prior_date"),
            }

        # ── Bull side (options premium; fall back to equity money-flow) ─
        opt = mf.get("options")
        if opt and (opt.get("call_value") or opt.get("put_value")):
            cv = float(opt.get("call_value") or 0)
            pv = float(opt.get("put_value") or 0)
            total = cv + pv
            bull = {
                "source": "options",
                "sentiment": opt.get("sentiment"),
                "call_value": cv,
                "put_value": pv,
                "net_premium": opt.get("net_premium"),
                "pc_ratio": opt.get("pc_ratio"),
                "fill": round(cv / total * 100) if total > 0 else None,
            }
        else:
            eq = mf.get("equity") or {}
            cmf = eq.get("cmf")
            bull = {
                "source": "money_flow",
                "sentiment": eq.get("mf_label"),
                "cmf": cmf,
                "mfi": eq.get("mfi"),
                # CMF is bounded [-1, 1]; map to a 0–100 bull share.
                "fill": round((max(-1.0, min(1.0, cmf)) + 1) / 2 * 100) if cmf is not None else None,
            }

        # ── 14-day daily pressure series (accumulation proxy) ──────────
        series = []
        try:
            recent = df.tail(14)
            for idx, row in recent.iterrows():
                hi = float(row["High"]); lo = float(row["Low"]); cl = float(row["Close"])
                vol = float(row["Volume"]) if row["Volume"] == row["Volume"] else 0.0
                rng = hi - lo
                mult = ((cl - lo) - (hi - cl)) / rng if rng > 0 else 0.0  # -1..1
                bull_share = round((mult + 1) / 2 * 100)
                series.append({
                    "date": str(idx.date()),
                    "bull": bull_share,
                    "bear": 100 - bull_share,
                    "volume": int(vol),
                })
        except Exception:
            series = []

        available = bool(short) or (bull and bull.get("fill") is not None) or bool(series)
        return {"available": available, "short": short, "bull": bull, "series_14d": series}
    except Exception:
        return {"available": False, "short": None, "bull": None, "series_14d": []}


def analyze_ticker(ticker: str, period: str = "6mo", interval: str = "1d") -> dict:
    """Full analysis pipeline for a ticker."""
    # Fetch data
    df = fetch_stock_data(ticker, period, interval)
    info = get_stock_info(ticker)

    # Fetch fundamentals for the Financials panel
    # Detect crypto tickers (e.g., BTC-USD, ETH-USD) — no traditional fundamentals
    is_crypto = ticker.upper().endswith('-USD') and not ticker.upper().startswith('USD')
    try:
        if is_crypto:
            fundamentals = fetch_fundamentals_crypto(ticker)
        else:
            fundamentals = fetch_fundamentals(ticker)
    except Exception:
        fundamentals = None

    # Detect pattern
    pattern = detect_triangle_pattern(df)

    # Calculate indicators. The trend read (50 vs 200-day MA) needs ≥200 daily
    # bars, but the chart window is often shorter (6mo ≈ 124 bars) — which used
    # to fabricate a fake 200-MA and flip strong uptrends into "downtrend".
    # For daily analysis, compute indicators on a guaranteed-long daily series
    # so the 200-MA (and the recommendation) are genuine. The chart still uses
    # the requested `df`; only the indicator snapshot uses the longer history.
    ind_df = df
    if interval in ("1d", "1wk") and len(df) < 220:
        try:
            long_df = fetch_stock_data(ticker, "2y", "1d")
            if long_df is not None and len(long_df) > len(df):
                ind_df = long_df
        except Exception:
            ind_df = df  # graceful — fall back to the chart window
    indicators = calculate_indicators(ind_df)

    # Money-in/out read: equity flow (CMF/MFI) fused with options flow
    money_flow = compute_money_flow_read(ticker, df, is_crypto)

    # Short interest (bear positioning) vs bull interest (bullish options flow),
    # with a 14-day daily buying/selling pressure series. Reuses money_flow's
    # options read — no extra network call.
    short_vs_bull = compute_short_vs_bull(ticker, df, is_crypto, fundamentals, money_flow)

    # Trendline tests (significant candles testing support/resistance)
    trendline_tests = detect_trendline_tests(df, pattern)

    # Candlestick patterns (Inside Day / Outside Day per Swing Trade Pro)
    candle_patterns = detect_candlestick_patterns(df)

    # Breakout status
    breakout_status = detect_breakout_status(df, pattern)

    # Trade plan
    trade_plan = generate_trade_plan(df, pattern, indicators, info)

    # Moving average series for chart
    ma_series = calculate_moving_averages_series(df)

    # Prepare OHLCV data for chart
    ohlcv = []
    dates = []
    volumes = []
    for i, (idx, row) in enumerate(df.iterrows()):
        timestamp = int(idx.timestamp())
        ohlcv.append({
            "time": timestamp,
            "open": round(row["Open"], 2),
            "high": round(row["High"], 2),
            "low": round(row["Low"], 2),
            "close": round(row["Close"], 2),
        })
        volumes.append({
            "time": timestamp,
            "value": int(row["Volume"]),
            "color": "rgba(38, 166, 154, 0.5)" if row["Close"] >= row["Open"]
                     else "rgba(239, 83, 80, 0.5)",
        })
        dates.append(timestamp)

    # Convert trendline points to timestamps
    if pattern:
        for line_key in ["upper_trendline", "lower_trendline"]:
            for pt in pattern[line_key]["points"]:
                idx = pt["idx"]
                if 0 <= idx < len(dates):
                    pt["time"] = dates[idx]
                else:
                    # Extrapolate timestamp
                    if len(dates) >= 2:
                        avg_step = (dates[-1] - dates[0]) / (len(dates) - 1)
                        pt["time"] = int(dates[-1] + avg_step * (idx - len(dates) + 1))
                    else:
                        pt["time"] = dates[-1]

        # Convert swing point indices to timestamps — only show anchor points
        # used by the trendlines to avoid chart clutter
        upper_anchors = set(pattern.get("upper_trendline", {}).get("anchors", []))
        lower_anchors = set(pattern.get("lower_trendline", {}).get("anchors", []))
        pattern["swing_highs_ts"] = [
            {"time": dates[i], "value": round(df["High"].iloc[i], 2)}
            for i in pattern["swing_highs"] if i < len(dates) and i in upper_anchors
        ]
        pattern["swing_lows_ts"] = [
            {"time": dates[i], "value": round(df["Low"].iloc[i], 2)}
            for i in pattern["swing_lows"] if i < len(dates) and i in lower_anchors
        ]

        # Price target and stop loss timestamps (project forward)
        if len(dates) >= 2:
            avg_step = (dates[-1] - dates[0]) / (len(dates) - 1)
            pattern["target_time"] = int(dates[-1] + avg_step * 20)
        else:
            pattern["target_time"] = dates[-1]

    # Convert trendline test indices to timestamps
    trendline_tests_ts = []
    for test in trendline_tests:
        idx = test["idx"]
        if 0 <= idx < len(dates):
            test["time"] = dates[idx]
            # Add OHLC data for the candle (with NaN safety)
            o = df["Open"].iloc[idx]
            h = df["High"].iloc[idx]
            l = df["Low"].iloc[idx]
            c = df["Close"].iloc[idx]
            v = df["Volume"].iloc[idx]
            test["open"] = round(float(o), 2) if not (isinstance(o, float) and np.isnan(o)) else 0
            test["high"] = round(float(h), 2) if not (isinstance(h, float) and np.isnan(h)) else 0
            test["low"] = round(float(l), 2) if not (isinstance(l, float) and np.isnan(l)) else 0
            test["close"] = round(float(c), 2) if not (isinstance(c, float) and np.isnan(c)) else 0
            test["volume"] = int(v) if not (isinstance(v, float) and np.isnan(v)) else 0
            test["date"] = str(df.index[idx].date())
            trendline_tests_ts.append(test)

    # MA series with timestamps
    ma_chart = {}
    for key, values in ma_series.items():
        ma_chart[key] = [
            {"time": dates[i], "value": v}
            for i, v in enumerate(values)
            if not (isinstance(v, float) and np.isnan(v))
        ]

    current_price = round(df["Close"].iloc[-1], 2)
    prev_close = round(df["Close"].iloc[-2], 2) if len(df) > 1 else current_price
    change = round(current_price - prev_close, 2)
    change_pct = round((change / prev_close) * 100, 2) if prev_close else 0

    # Recent news headlines (last 24h) — fed into the LLM context for catalyst awareness.
    # Cached upstream by news_fetcher; safe to call on every analyze.
    recent_news_lines = []
    active_catalysts_lines = []
    if not is_crypto:
        try:
            from shared.news_fetcher import headlines_for_llm, active_catalysts
            recent_news_lines = headlines_for_llm(ticker, hours=24, limit=8)
            active_catalysts_lines = active_catalysts(ticker, hours=24, limit=5)
        except Exception:
            recent_news_lines = []
            active_catalysts_lines = []

    # News agent — the platform's own 30-day RSS/Reddit store, tagged with this
    # ticker. Surfaced for display AND folded into the LLM context so the AI
    # analysis is aware of what the news/Reddit tape is saying about the name.
    news_agent_items = []
    try:
        from features.news_agent.agent import recent as _na_recent
        news_agent_items = _na_recent(ticker=ticker, hours=72, limit=8)
        for it in news_agent_items:
            line = f"[{it.get('source')}] {it.get('title')} ({it.get('sentiment')})"
            if line not in recent_news_lines:
                recent_news_lines.append(line)
    except Exception:
        news_agent_items = []

    # Market-condition gauge — fetched once (cached 15 min) so the per-stock
    # verdict is macro-aware. Stocks only (crypto trades a different tape).
    market_condition = None
    if not is_crypto:
        try:
            from shared.market_gauge import get_market_gauge
            market_condition = get_market_gauge()
        except Exception:
            market_condition = None

    return {
        "ticker": ticker.upper(),
        "info": info,
        "current_price": current_price,
        "change": change,
        "change_pct": change_pct,
        "ohlcv": ohlcv,
        "volumes": volumes,
        "pattern": pattern,
        "indicators": indicators,
        "money_flow": money_flow,
        "short_vs_bull": short_vs_bull,
        "breakout_status": breakout_status,
        "trade_plan": trade_plan,
        "market_condition": market_condition,  # consumed by frontend + ai_validator
        "recommendation": generate_recommendation(indicators, breakout_status, current_price, market_condition),
        "moving_averages": ma_chart,
        "trendline_tests": trendline_tests_ts,
        "candle_patterns": [
            {**cp, "time": dates[cp["idx"]], "date": str(df.index[cp["idx"]].date())}
            for cp in candle_patterns if cp["idx"] < len(dates)
        ],
        "fundamentals": fundamentals,
        "recent_news": recent_news_lines,  # consumed by ai_validator._build_data_summary
        "active_catalysts": active_catalysts_lines,  # high-signal bullish news for LLM
        "news_agent": news_agent_items,  # 30-day RSS/Reddit store tagged with this ticker
        "analysis_time": datetime.now().isoformat(),
        "period": period,
        "interval": interval,
    }


# ─── Qullamaggie Breakout Scanner ─────────────────────────────

def qullamaggie_scan(tickers: list = None) -> list:
    """
    Scan tickers for Qullamaggie breakout setups.
    Detects High Tight Flags (HTF), VCPs, and Episodic Pivots (EP).
    Returns list of qualifying setups sorted by score.
    """
    if tickers is None:
        from screener import LOWCAP_TICKERS, MIDCAP_TICKERS, LARGECAP_TICKERS
        tickers = list(set(LOWCAP_TICKERS + MIDCAP_TICKERS + LARGECAP_TICKERS))

    results = []
    batch_size = 50

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            tickers_str = " ".join(batch)
            data = yf.download(tickers_str, period="6mo", progress=False, threads=True)
            if data.empty:
                continue

            for ticker in batch:
                try:
                    if len(batch) == 1:
                        df = data
                    else:
                        if ticker not in data["Close"].columns:
                            continue
                        df = pd.DataFrame({
                            "Open": data["Open"][ticker],
                            "High": data["High"][ticker],
                            "Low": data["Low"][ticker],
                            "Close": data["Close"][ticker],
                            "Volume": data["Volume"][ticker],
                        })

                    df = df.dropna()
                    if len(df) < 60:
                        continue

                    close = df["Close"].values
                    high = df["High"].values
                    low = df["Low"].values
                    volume = df["Volume"].values
                    n = len(df)

                    current_price = float(close[-1])
                    if current_price < 5:
                        continue

                    # Dollar volume filter (>= $3M)
                    avg_vol_20 = float(np.mean(volume[-20:]))
                    dollar_volume = current_price * avg_vol_20
                    if dollar_volume < 3_000_000:
                        continue

                    # Moving averages
                    sma_50 = float(pd.Series(close).rolling(50).mean().iloc[-1])
                    sma_150 = float(pd.Series(close).rolling(min(150, n)).mean().iloc[-1]) if n >= 50 else current_price
                    sma_200 = float(pd.Series(close).rolling(min(200, n)).mean().iloc[-1]) if n >= 50 else current_price

                    # MA Alignment: close > SMA200, SMA150 > SMA200, SMA50 > SMA150
                    ma_aligned = current_price > sma_200 and sma_150 > sma_200 and sma_50 > sma_150

                    # 52-week high/low (use available data)
                    high_52w = float(np.max(high))
                    low_52w = float(np.min(low))

                    # Relative strength checks
                    above_52w_low_pct = (current_price - low_52w) / max(low_52w, 0.01) * 100
                    below_52w_high_pct = (high_52w - current_price) / max(high_52w, 0.01) * 100

                    # Period gains (computed up front — both branches use them)
                    gain_1m = (current_price / float(close[max(0, n - 21)]) - 1) * 100 if n > 21 else 0
                    gain_3m = (current_price / float(close[max(0, n - 63)]) - 1) * 100 if n > 63 else 0
                    gain_6m = (current_price / float(close[0]) - 1) * 100

                    # ADR% (14-day average daily range as %)
                    daily_range_pct = ((high[-14:] - low[-14:]) / close[-14:]) * 100
                    adr_pct = float(np.mean(daily_range_pct))

                    # Stage 2 first: catches beaten-down recovery names that the
                    # standard >30%-above-52w-low filter would reject (INTC/SNDK/MU pattern).
                    setup = _detect_stage2_breakout(close, high, low, volume, n)

                    if setup is None:
                        # Standard momentum gates: must be above 30% from 52w low and within 25% of high
                        if above_52w_low_pct < 30 or below_52w_high_pct > 25:
                            continue

                        # Relative strength: at least one must pass
                        rs_pass = gain_1m >= 25 or gain_3m >= 50 or gain_6m >= 150
                        if not rs_pass and not ma_aligned:
                            continue

                        # Check for HTF / VCP / EP setups
                        setup = _detect_qullamaggie_setup(df, close, high, low, volume, n, adr_pct)
                        if setup is None:
                            continue

                    # Score the setup (1-10)
                    score = _score_qullamaggie_setup(
                        setup, ma_aligned, gain_1m, gain_3m, gain_6m, adr_pct, dollar_volume
                    )

                    # Position sizing (0.25% risk per trade, $25K account)
                    account_size = 25000
                    risk_per_trade = account_size * 0.0025
                    risk_per_share = max(setup["entry_price"] - setup["stop_loss"], 0.01)
                    shares = min(
                        int(risk_per_trade / risk_per_share),
                        int(account_size * 0.15 / setup["entry_price"])  # 15% position cap
                    )

                    # Get sector info
                    try:
                        info = yf.Ticker(ticker).info or {}
                        sector = info.get("sector", "Unknown")
                        name = info.get("shortName", ticker)
                    except Exception:
                        sector = "Unknown"
                        name = ticker

                    if setup["type"] == "STAGE2":
                        sell_plan = "Trail with 50d SMA, scale 1/3 at +20%, exit on close < 50d"
                    elif setup["type"] == "EP":
                        sell_plan = "Tight stop below gap day low, sell 50% +15%, trail rest"
                    else:
                        sell_plan = "Sell 50% after 3-5 days, trail rest with 10/20 SMA"

                    row = {
                        "ticker": ticker,
                        "name": name,
                        "sector": sector,
                        "setup_type": setup["type"],
                        "score": round(score, 1),
                        "entry_price": round(setup["entry_price"], 2),
                        "stop_loss": round(setup["stop_loss"], 2),
                        "current_price": round(current_price, 2),
                        "adr_pct": round(adr_pct, 1),
                        "rel_strength_1m": round(gain_1m, 1),
                        "rel_strength_3m": round(gain_3m, 1),
                        "rel_strength_6m": round(gain_6m, 1),
                        "volume_ratio": round(float(volume[-1]) / avg_vol_20, 1) if avg_vol_20 > 0 else 0,
                        "dollar_volume": round(dollar_volume / 1e6, 1),
                        "ma_aligned": ma_aligned,
                        "consolidation_depth_pct": round(setup.get("depth_pct", 0), 1),
                        "consolidation_days": setup.get("consolidation_days", 0),
                        "prior_move_pct": round(setup.get("prior_move_pct", 0), 1),
                        "shares": shares,
                        "risk_amount": round(shares * risk_per_share, 2),
                        "position_value": round(shares * setup["entry_price"], 2),
                        "sell_plan": sell_plan,
                    }
                    if setup["type"] == "STAGE2":
                        row.update({
                            "above_52w_low_pct": setup.get("above_52w_low_pct"),
                            "below_52w_high_pct": setup.get("below_52w_high_pct"),
                            "sma_ratio": setup.get("sma_ratio"),
                            "vol_expansion": setup.get("vol_expansion"),
                            "rsi": setup.get("rsi"),
                            "days_since_52w_low": setup.get("days_since_52w_low"),
                            "base_high": setup.get("base_high"),
                            "phase": setup.get("phase"),
                        })
                    results.append(row)

                except Exception:
                    continue
        except Exception:
            continue

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def _detect_stage2_breakout(close, high, low, volume, n):
    """Detect Stage 2 / Recovery Breakout — beaten-down stock at first leg up.

    Catches the INTC / SNDK / MU pattern: stock has been crushed for months,
    formed a base near the lows, SMA50 just turned up, volume expanding,
    breaking above base resistance with RSI recovering from oversold.

    Different gating from HTF/VCP/EP — requires the stock to be in the
    LOWER 60% of its 52-week range (the opposite of momentum scanner).

    Returns setup dict or None.
    """
    if n < 60:
        return None

    current_price = float(close[-1])
    high_52w = float(np.max(high))
    low_52w = float(np.min(low))
    above_52w_low_pct = (current_price - low_52w) / max(low_52w, 0.01) * 100
    below_52w_high_pct = (high_52w - current_price) / max(high_52w, 0.01) * 100

    # Recovery zone gates: lower 75% of 52w range, off the absolute low
    if below_52w_high_pct < 20:
        return None  # already near highs — handled by HTF/VCP/EP
    if above_52w_low_pct > 75:
        return None  # past the inflection
    if above_52w_low_pct < 3:
        return None  # too close to low, no base yet

    # Days since 52w low — base length proxy (need at least 15)
    low_idx = int(np.argmin(low))
    days_since_low = n - 1 - low_idx
    if days_since_low < 15:
        return None

    # No new 52w low in the last 10 days — must have stopped bleeding
    recent_low = float(np.min(low[-10:]))
    if recent_low <= low_52w * 1.005:
        return None

    # SMA structure: price within reach of SMA50 (above, or within 5% below) AND SMA50 not in freefall
    sma_50 = float(pd.Series(close).rolling(50).mean().iloc[-1])
    sma_50_20d_ago = float(pd.Series(close).rolling(50).mean().iloc[-21]) if n > 71 else sma_50
    sma_200 = float(pd.Series(close).rolling(min(200, n)).mean().iloc[-1])

    sma50_not_falling = sma_50 >= sma_50_20d_ago * 0.99  # flat or rising
    price_near_sma50 = current_price >= sma_50 * 0.95  # above or within 5%
    if not (sma50_not_falling and price_near_sma50):
        return None

    # SMA50 not too far below SMA200 (within 15%)
    sma_ratio = sma_50 / sma_200 if sma_200 > 0 else 0
    if sma_ratio < 0.85:
        return None

    # Base detection: 30-day range pre-breakout, 4-35%
    base_start = max(0, n - 45)
    base_end = max(0, n - 5)
    if base_end - base_start < 15:
        return None
    base_high = float(np.max(high[base_start:base_end]))
    base_low = float(np.min(low[base_start:base_end]))
    base_range_pct = (base_high - base_low) / max(base_low, 0.01) * 100
    if not (3 <= base_range_pct <= 40):
        return None

    # Volume not collapsing: last 5d >= 0.7x prior 20d (1.2x+ is a bonus, scored later)
    vol_recent = float(np.mean(volume[-5:]))
    vol_prior = float(np.mean(volume[-25:-5])) if n >= 25 else vol_recent
    vol_expansion = vol_recent / max(vol_prior, 1)
    if vol_expansion < 0.7:
        return None

    # 1-month gain: -10 to +35% (allow basing names with chop, but not still falling hard)
    gain_1m = (current_price / float(close[-21]) - 1) * 100 if n > 21 else 0
    if gain_1m < -10 or gain_1m > 35:
        return None

    # RSI in recovery zone (35-72)
    deltas = np.diff(close[-15:])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains)) if len(gains) else 0
    avg_loss = float(np.mean(losses)) if len(losses) else 0.001
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    rsi = 100 - 100 / (1 + rs)
    if not (35 <= rsi <= 72):
        return None

    # Phase detection: "breakout" if breaking above base, "basing" if still consolidating
    if current_price >= base_high * 0.98:
        phase = "breakout"
    elif current_price >= base_high * 0.92:
        phase = "loaded"  # right at the launch pad
    else:
        phase = "basing"

    return {
        "type": "STAGE2",
        "entry_price": current_price,
        "stop_loss": float(np.min(low[-10:])),
        "depth_pct": base_range_pct,
        "consolidation_days": days_since_low,
        "prior_move_pct": gain_1m,
        # Stage 2 specific context
        "above_52w_low_pct": round(above_52w_low_pct, 1),
        "below_52w_high_pct": round(below_52w_high_pct, 1),
        "sma_ratio": round(sma_ratio, 3),
        "vol_expansion": round(vol_expansion, 2),
        "rsi": round(rsi, 1),
        "days_since_52w_low": days_since_low,
        "base_high": round(base_high, 2),
        "phase": phase,
    }


def _detect_qullamaggie_setup(df, close, high, low, volume, n, adr_pct):
    """Detect HTF, VCP, or EP setup. Returns setup dict or None."""

    # === Episodic Pivot (EP) — check first (most recent day) ===
    if n >= 21:
        gap_pct = (float(close[-1]) / float(close[-2]) - 1) * 100
        avg_vol_20 = float(np.mean(volume[-21:-1]))
        vol_ratio = float(volume[-1]) / max(avg_vol_20, 1)

        if gap_pct >= 10 and vol_ratio >= 2.0:
            return {
                "type": "EP",
                "entry_price": float(high[-1]),  # Above opening range high
                "stop_loss": float(low[-1]),  # Below gap day low
                "depth_pct": 0,
                "consolidation_days": 0,
                "prior_move_pct": gap_pct,
            }

    # === High Tight Flag (HTF) ===
    if n >= 20:
        # Find max in last 60 days
        lookback = min(60, n)
        recent_high = high[-lookback:]
        recent_low = low[-lookback:]
        recent_close = close[-lookback:]

        peak_idx = int(np.argmax(recent_high))
        peak_price = float(recent_high[peak_idx])

        # Find the low before the peak to measure run-up
        pre_peak_low = float(np.min(recent_low[:max(peak_idx, 1)]))
        run_up_pct = (peak_price / max(pre_peak_low, 0.01) - 1) * 100

        if run_up_pct >= 80:
            # Days since peak
            days_since_peak = lookback - peak_idx
            current_price = float(close[-1])
            pullback_pct = (peak_price - current_price) / max(peak_price, 0.01) * 100

            if pullback_pct <= 25 and 10 <= days_since_peak <= 40:
                # Check for tightening range
                last_10 = high[-10:] - low[-10:]
                first_half = float(np.mean(last_10[:5]))
                second_half = float(np.mean(last_10[5:]))
                range_contracting = second_half < first_half * 0.8 if first_half > 0 else False

                # Check volume declining
                vol_last_5 = float(np.mean(volume[-5:]))
                vol_20 = float(np.mean(volume[-20:]))
                vol_declining = vol_last_5 < vol_20 * 0.7

                if range_contracting or vol_declining:
                    return {
                        "type": "HTF",
                        "entry_price": peak_price,  # Break above peak
                        "stop_loss": float(np.min(low[-5:])),  # Below recent swing low
                        "depth_pct": pullback_pct,
                        "consolidation_days": days_since_peak,
                        "prior_move_pct": run_up_pct,
                    }

    # === VCP (Volatility Contraction Pattern) ===
    if n >= 40:
        sma_10 = pd.Series(close).rolling(10).mean()
        sma_21 = pd.Series(close).rolling(21).mean()

        current_price = float(close[-1])
        sma_10_val = float(sma_10.iloc[-1])

        # Price within 3% of 10-day SMA
        price_near_sma10 = abs(current_price - sma_10_val) / max(sma_10_val, 0.01) * 100 < 3

        if price_near_sma10:
            # Look for successive contractions in last 40 days
            lookback_data = 40
            segment = min(lookback_data, n)
            seg_high = high[-segment:]
            seg_low = low[-segment:]

            # Split into thirds and measure range
            third = segment // 3
            if third >= 3:
                r1 = float(np.max(seg_high[:third]) - np.min(seg_low[:third]))
                r2 = float(np.max(seg_high[third:2*third]) - np.min(seg_low[third:2*third]))
                r3 = float(np.max(seg_high[2*third:]) - np.min(seg_low[2*third:]))

                avg_price = float(np.mean(close[-segment:]))
                final_contraction_pct = r3 / max(avg_price, 0.01) * 100

                # Each contraction should be tighter than previous
                if r2 < r1 and r3 < r2 and final_contraction_pct < 10:
                    # Check 10d SMA crossed above 21d SMA recently
                    cross_days = 0
                    for j in range(min(10, n - 21)):
                        idx = -(j + 1)
                        if not np.isnan(sma_10.iloc[idx]) and not np.isnan(sma_21.iloc[idx]):
                            if float(sma_10.iloc[idx]) > float(sma_21.iloc[idx]):
                                cross_days += 1
                            else:
                                break

                    if cross_days <= 10:
                        pivot = float(np.max(seg_high[2*third:]))
                        return {
                            "type": "VCP",
                            "entry_price": pivot,  # Break above pivot
                            "stop_loss": float(np.min(seg_low[2*third:])),
                            "depth_pct": final_contraction_pct,
                            "consolidation_days": segment,
                            "prior_move_pct": (float(np.max(high[-segment:])) / float(np.min(low[-segment:])) - 1) * 100,
                        }

    return None


def _score_qullamaggie_setup(setup, ma_aligned, gain_1m, gain_3m, gain_6m, adr_pct, dollar_volume):
    """Score a Qullamaggie setup from 1-10."""
    score = 5.0  # Base

    # MA alignment bonus
    if ma_aligned:
        score += 1.5

    # Relative strength (how many thresholds passed)
    rs_count = 0
    if gain_1m >= 25: rs_count += 1
    if gain_3m >= 50: rs_count += 1
    if gain_6m >= 150: rs_count += 1
    score += rs_count * 0.5

    # ADR% (prefer >= 5%)
    if adr_pct >= 8:
        score += 1.0
    elif adr_pct >= 5:
        score += 0.5

    # Setup type bonus
    if setup["type"] == "EP":
        score += 0.5  # Gap ups are high conviction
    elif setup["type"] == "HTF":
        if setup.get("prior_move_pct", 0) >= 100:
            score += 1.0
    elif setup["type"] == "VCP":
        if setup.get("depth_pct", 0) < 5:
            score += 0.5  # Very tight VCP
    elif setup["type"] == "STAGE2":
        # Stage 2 scoring: rebuild from scratch — momentum rules don't apply to recovery plays
        score = 4.0
        # Phase: breakout > loaded > basing
        phase = setup.get("phase", "basing")
        if phase == "breakout":
            score += 2.0
        elif phase == "loaded":
            score += 1.2
        else:
            score += 0.4  # still useful as a watchlist
        # Volume expansion
        ve = setup.get("vol_expansion", 0)
        if ve >= 1.8:
            score += 1.5
        elif ve >= 1.4:
            score += 0.8
        elif ve >= 1.1:
            score += 0.4
        # Earlier in the recovery = better
        ab_low = setup.get("above_52w_low_pct", 100)
        if ab_low <= 25:
            score += 1.0
        elif ab_low <= 50:
            score += 0.5
        # SMA structure: past golden cross = better
        sr = setup.get("sma_ratio", 0)
        if sr >= 1.0:
            score += 0.8
        elif sr >= 0.95:
            score += 0.4
        # RSI in the sweet spot
        rsi = setup.get("rsi", 0)
        if 50 <= rsi <= 65:
            score += 0.5
        # Tighter base
        if setup.get("depth_pct", 100) <= 15:
            score += 0.5
        # Liquidity
        if dollar_volume >= 20_000_000:
            score += 0.5

    # Dollar volume (higher = better liquidity)
    if dollar_volume >= 20_000_000:
        score += 0.5

    return min(max(score, 1), 10)
