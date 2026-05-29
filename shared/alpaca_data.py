"""Alpaca market-data source for daily OHLCV bars.

Yahoo (yfinance) rate-limits this host hard under bot load, which leaves the
screener's money-flow read blank. Alpaca's market-data API is keyed and not
IP-throttled, so it's the preferred source for the money-flow indicator; the
caller falls back to yfinance when Alpaca is not configured or lacks a symbol.

Credentials resolve via shared.runtime_config (DB bot_config user_id=0, then
env ALPACA_API_KEY / ALPACA_SECRET_KEY) — set them globally in the admin
settings or the environment. Free-tier Alpaca serves IEX daily bars, which is
plenty for CMF(20) / MFI(14).
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_client = None
_client_creds = None          # (key, secret) the cached client was built with
_lock = threading.Lock()

_bars_cache = {}              # symbol -> (DataFrame, ts)
_DEFAULT_TTL = 900            # 15 min — daily bars don't change intraday


def _creds():
    """(api_key, secret_key) or (None, None) if not configured."""
    try:
        from shared.runtime_config import get_setting
        key = get_setting("alpaca_api_key", env_aliases=("ALPACA_API_KEY",))
        secret = get_setting("alpaca_secret_key", env_aliases=("ALPACA_SECRET_KEY",))
        if key and secret:
            return key, secret
    except Exception as e:
        logger.debug(f"alpaca creds lookup failed: {e}")
    return None, None


def is_configured():
    key, secret = _creds()
    return bool(key and secret)


def _is_alpaca_symbol(symbol):
    """Alpaca's stock data covers US equities/ETFs only — skip crypto (-USD) and
    index proxies (^...) so the caller falls back to yfinance for those."""
    s = (symbol or "").upper()
    return bool(s) and not s.endswith("-USD") and not s.startswith("^") and "=" not in s


def _get_client():
    """Cached StockHistoricalDataClient, rebuilt if creds change. None if
    unconfigured or the SDK/data module is unavailable."""
    global _client, _client_creds
    key, secret = _creds()
    if not (key and secret):
        return None
    with _lock:
        if _client is not None and _client_creds == (key, secret):
            return _client
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            _client = StockHistoricalDataClient(key, secret)
            _client_creds = (key, secret)
            return _client
        except Exception as e:
            logger.warning(f"Alpaca data client init failed: {e}")
            return None


def get_daily_bars(symbols, days=60, ttl=_DEFAULT_TTL):
    """Return {SYMBOL: DataFrame[High,Low,Close,Volume]} of daily bars from
    Alpaca. Serves cached bars within `ttl`. Symbols Alpaca can't serve (crypto,
    indices) or that error out are simply omitted so the caller can fall back.
    Returns {} when Alpaca is not configured. Never raises."""
    out = {}
    wanted = [s for s in {(s or "").upper() for s in symbols} if _is_alpaca_symbol(s)]
    if not wanted:
        return out

    now = time.time()
    fetch = []
    with _lock:
        for s in wanted:
            c = _bars_cache.get(s)
            if c and now - c[1] < ttl:
                out[s] = c[0].copy()
            else:
                fetch.append(s)
    if not fetch:
        return out

    client = _get_client()
    if client is None:
        return out

    try:
        import pandas as pd
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from datetime import datetime, timedelta, timezone

        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.6) + 10)
        req = StockBarsRequest(symbol_or_symbols=fetch, timeframe=TimeFrame.Day, start=start)
        bars = client.get_stock_bars(req)
        df_all = bars.df  # MultiIndex (symbol, timestamp) with open/high/low/close/volume
        if df_all is None or df_all.empty:
            return out

        for s in fetch:
            try:
                if s not in df_all.index.get_level_values(0):
                    continue
                sub = df_all.xs(s, level=0)
                frame = pd.DataFrame({
                    "High": sub["high"].astype(float),
                    "Low": sub["low"].astype(float),
                    "Close": sub["close"].astype(float),
                    "Volume": sub["volume"].astype(float),
                }).dropna().tail(days)
                if frame.empty:
                    continue
                with _lock:
                    _bars_cache[s] = (frame, time.time())
                out[s] = frame.copy()
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Alpaca get_daily_bars failed: {e}")
    return out


def money_flow_map(symbols, days=60):
    """{SYMBOL: compute_money_flow(df)} for the symbols Alpaca can serve.
    Empty dict when unconfigured — caller keeps its yfinance-derived values."""
    from shared.money_flow import compute_money_flow
    bars = get_daily_bars(symbols, days=days)
    return {sym: compute_money_flow(df) for sym, df in bars.items()}
