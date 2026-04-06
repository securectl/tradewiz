"""
Data providers for skills — wraps analysis_engine + yfinance for structured data fetching.
"""

import logging
import yfinance as yf

logger = logging.getLogger(__name__)


def fetch_fundamentals(ticker):
    """Fetch fundamental data using analysis_engine."""
    from analysis_engine import fetch_fundamentals as _fetch
    try:
        return _fetch(ticker) or {}
    except Exception as e:
        logger.error("Failed to fetch fundamentals for %s: %s", ticker, e)
        return {}


def fetch_price_data(ticker, period="1y", interval="1d"):
    """Fetch OHLCV price data as a list of dicts."""
    from analysis_engine import fetch_stock_data
    try:
        df = fetch_stock_data(ticker, period=period, interval=interval)
        if df is None or df.empty:
            return []
        records = []
        for idx, row in df.iterrows():
            records.append({
                "date": str(idx.date()) if hasattr(idx, "date") else str(idx),
                "open": round(float(row.get("Open", 0)), 2),
                "high": round(float(row.get("High", 0)), 2),
                "low": round(float(row.get("Low", 0)), 2),
                "close": round(float(row.get("Close", 0)), 2),
                "volume": int(row.get("Volume", 0)),
            })
        return records
    except Exception as e:
        logger.error("Failed to fetch prices for %s: %s", ticker, e)
        return []


def fetch_indicators(ticker, period="1y"):
    """Fetch technical indicators."""
    from analysis_engine import fetch_stock_data, calculate_indicators
    try:
        df = fetch_stock_data(ticker, period=period)
        if df is None or df.empty:
            return {}
        return calculate_indicators(df) or {}
    except Exception as e:
        logger.error("Failed to fetch indicators for %s: %s", ticker, e)
        return {}


def fetch_peer_data(tickers):
    """Fetch basic fundamentals for a list of peer tickers."""
    peers = {}
    for t in tickers[:10]:  # cap at 10 peers
        try:
            data = fetch_fundamentals(t)
            if data:
                peers[t] = data
        except Exception:
            continue
    return peers


def fetch_company_info(ticker):
    """Fetch basic company info via yfinance."""
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        return {
            "name": info.get("longName", info.get("shortName", ticker)),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "market_cap": info.get("marketCap", 0),
            "employees": info.get("fullTimeEmployees", 0),
            "description": info.get("longBusinessSummary", ""),
            "website": info.get("website", ""),
            "country": info.get("country", ""),
            "exchange": info.get("exchange", ""),
        }
    except Exception as e:
        logger.error("Failed to fetch company info for %s: %s", ticker, e)
        return {"name": ticker, "sector": "N/A", "industry": "N/A"}


def fetch_income_statements(ticker, quarterly=False):
    """Fetch income statement data via yfinance."""
    try:
        tk = yf.Ticker(ticker)
        df = tk.quarterly_financials if quarterly else tk.financials
        if df is None or df.empty:
            return {}
        result = {}
        for col in df.columns:
            period_key = str(col.date()) if hasattr(col, "date") else str(col)
            result[period_key] = {}
            for idx in df.index:
                val = df.loc[idx, col]
                result[period_key][idx] = float(val) if val == val else None
        return result
    except Exception as e:
        logger.error("Failed to fetch income statements for %s: %s", ticker, e)
        return {}


def fetch_balance_sheet(ticker, quarterly=False):
    """Fetch balance sheet data via yfinance."""
    try:
        tk = yf.Ticker(ticker)
        df = tk.quarterly_balance_sheet if quarterly else tk.balance_sheet
        if df is None or df.empty:
            return {}
        result = {}
        for col in df.columns:
            period_key = str(col.date()) if hasattr(col, "date") else str(col)
            result[period_key] = {}
            for idx in df.index:
                val = df.loc[idx, col]
                result[period_key][idx] = float(val) if val == val else None
        return result
    except Exception as e:
        logger.error("Failed to fetch balance sheet for %s: %s", ticker, e)
        return {}


def fetch_cash_flow(ticker, quarterly=False):
    """Fetch cash flow statement via yfinance."""
    try:
        tk = yf.Ticker(ticker)
        df = tk.quarterly_cashflow if quarterly else tk.cashflow
        if df is None or df.empty:
            return {}
        result = {}
        for col in df.columns:
            period_key = str(col.date()) if hasattr(col, "date") else str(col)
            result[period_key] = {}
            for idx in df.index:
                val = df.loc[idx, col]
                result[period_key][idx] = float(val) if val == val else None
        return result
    except Exception as e:
        logger.error("Failed to fetch cash flow for %s: %s", ticker, e)
        return {}
