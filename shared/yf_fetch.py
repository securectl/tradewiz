"""Resilient yfinance access — shared browser-impersonating session, an
in-process TTL cache, and retry/backoff on Yahoo 429s.

Yahoo rate-limits by IP under heavy load (many bots + scans on one host). This
layer (1) cuts request volume via caching so repeated/concurrent fetches reuse
data, (2) impersonates a browser (curl_cffi) to reduce fingerprint-based 429s,
and (3) serves slightly-stale cached data instead of failing when Yahoo blocks.
Callers can catch RateLimited to fall back further (e.g. a DB-cached result).
"""

import logging
import threading
import time

import yfinance as yf

logger = logging.getLogger(__name__)


class RateLimited(Exception):
    """Yahoo returned 429 / rate-limited and no cached data was available."""


_session = None
_session_init = False


def _get_session():
    """Shared curl_cffi Chrome-impersonating session (reduces 429s). None if
    curl_cffi is unavailable — yfinance then uses its default session."""
    global _session, _session_init
    if not _session_init:
        _session_init = True
        try:
            from curl_cffi import requests as creq
            _session = creq.Session(impersonate="chrome")
        except Exception as e:
            logger.warning(f"curl_cffi session unavailable: {e}")
            _session = None
    return _session


def ticker(symbol):
    """yf.Ticker bound to the shared session when possible."""
    sess = _get_session()
    try:
        return yf.Ticker(symbol, session=sess) if sess else yf.Ticker(symbol)
    except TypeError:
        # Older yfinance without session kwarg
        return yf.Ticker(symbol)


def is_rate_limit_error(e):
    if isinstance(e, RateLimited):
        return True
    s = str(e).lower().replace("-", " ")
    name = type(e).__name__.lower()
    return ("too many requests" in s or "rate limit" in s or "429" in s
            or "ratelimit" in name)


_hist_cache = {}        # (symbol, period, interval) -> (df, ts)
_lock = threading.Lock()
_DEFAULT_TTL = 300       # 5 min

# How many days of history to keep per requested period (for the fallback trim).
_PERIOD_DAYS = {"1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "2y": 732, "ytd": 400}


def _fmp_history(symbol, period, interval):
    """Fallback OHLCV from Financial Modeling Prep when Yahoo fails.

    Uses the already-configured FMP_API_KEY (Google Finance has no usable
    public historical API, and Stooq now requires a key, so FMP is the
    fallback). Covers daily/weekly stocks, ETFs, and crypto; intraday returns
    None (FMP's EOD endpoint is end-of-day only)."""
    if interval in ("1h", "4h", "60m"):
        return None
    import os
    key = os.getenv("FMP_API_KEY", "")
    try:
        from shared.runtime_config import get_setting
        key = get_setting("fmp_api_key", key, env_aliases=("FMP_API_KEY",)) or key
    except Exception:
        pass
    if not key:
        return None
    sym = symbol.upper()
    if sym.endswith("-USD"):
        sym = sym.replace("-", "")  # BTC-USD -> BTCUSD (FMP crypto format)
    try:
        import requests
        import pandas as pd
        url = (f"https://financialmodelingprep.com/stable/historical-price-eod/full"
               f"?symbol={sym}&apikey={key}")
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list) or not data:
            return None
        df = pd.DataFrame(data)
        if "date" not in df.columns or "close" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).set_index("date").sort_index()
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                "close": "Close", "volume": "Volume"})
        for c in ("Open", "High", "Low", "Close", "Volume"):
            if c not in df.columns:
                df[c] = 0
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        if interval == "1wk":
            df = df.resample("W").agg({"Open": "first", "High": "max", "Low": "min",
                                       "Close": "last", "Volume": "sum"}).dropna()
        if period == "ytd":
            cutoff = pd.Timestamp(pd.Timestamp.now().year, 1, 1)
        else:
            cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=_PERIOD_DAYS.get(period, 186))
        df = df[df.index >= cutoff]
        return df if not df.empty else None
    except Exception as e:
        logger.warning(f"FMP fallback failed for {symbol}: {e}")
        return None


def get_history(symbol, period="6mo", interval="1d", ttl=_DEFAULT_TTL, retries=2):
    """Cached OHLCV history with retry/backoff. Serves stale cache on failure;
    raises RateLimited if rate-limited and nothing is cached, or the original
    error for non-rate-limit failures."""
    key = (symbol.upper(), period, interval)
    now = time.time()
    with _lock:
        c = _hist_cache.get(key)
        if c and now - c[1] < ttl:
            return c[0].copy()

    rl_err = None        # a rate-limit exception, if seen
    last_df = None       # last (possibly empty) successful response
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            df = ticker(symbol).history(period=period, interval=interval)
            last_df = df
            if df is not None and not df.empty:
                with _lock:
                    _hist_cache[key] = (df, time.time())
                return df.copy()
            # Empty WITHOUT an exception → genuinely "no data" (e.g. bad symbol);
            # don't treat as rate-limited. One quick retry covers a transient blip.
        except Exception as e:
            if not is_rate_limit_error(e):
                raise
            rl_err = e
        if attempt < retries:
            time.sleep(delay)
            delay *= 2

    # Exhausted — serve stale cache if we have any (better than failing).
    with _lock:
        c = _hist_cache.get(key)
        if c is not None:
            logger.info(f"yf_fetch: serving STALE cache for {key}")
            return c[0].copy()

    # Fallback source (FMP) — covers daily/weekly stocks, ETFs & crypto when
    # Yahoo is rate-limited or returns nothing.
    fb = _fmp_history(symbol, period, interval)
    if fb is not None and not fb.empty:
        with _lock:
            _hist_cache[key] = (fb, time.time())
        logger.info(f"yf_fetch: served FMP fallback for {key}")
        return fb.copy()

    if rl_err is not None:
        raise RateLimited(f"Yahoo rate-limited for {symbol}")
    # No rate-limit error and no fallback data — return the empty frame so the
    # caller raises a clean "no data / check symbol" (404).
    return last_df
