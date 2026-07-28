"""Alpaca options market-data adapter (seam) for cross-validation.

Alpaca's option chain snapshot (alpaca-py ``OptionHistoricalDataClient``)
reliably carries **open interest** per contract and, on newer SDK/plan tiers,
day volume. We use it as a *second opinion* to cross-check the yfinance call
chain — not as the primary feed. Like ``shared.webull_options`` this is a seam:
it returns ``None`` whenever Alpaca isn't configured, the options SDK module is
absent, or the account lacks the options-data entitlement, so the caller keeps
working on the sources it does have.

Reuses the credential resolver from ``shared.alpaca_data`` (DB bot_config
user_id=0 → env ALPACA_API_KEY / ALPACA_SECRET_KEY).

Return contract matches the other adapters::
    {"source": "alpaca", "price": <float>, "calls": [
        {"strike": float, "expiry": "YYYY-MM-DD", "volume": int,
         "open_interest": int, "last": float}, ...]}
"""

import logging

logger = logging.getLogger(__name__)

_client = None
_client_creds = None


def is_configured():
    try:
        from shared.alpaca_data import is_configured as _ac
        return _ac()
    except Exception:
        return False


def _get_client():
    """Cached OptionHistoricalDataClient, or None if unavailable. Never raises."""
    global _client, _client_creds
    try:
        from shared.alpaca_data import _creds
        key, secret = _creds()
    except Exception:
        key, secret = None, None
    if not (key and secret):
        return None
    if _client is not None and _client_creds == (key, secret):
        return _client
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        _client = OptionHistoricalDataClient(key, secret)
        _client_creds = (key, secret)
        return _client
    except Exception as e:
        logger.debug(f"Alpaca options client unavailable: {e}")
        return None


def _num(v, cast=float, default=0):
    try:
        return cast(v)
    except Exception:
        return default


def fetch_call_chain(symbol):
    """Best-effort Alpaca call chain for ``symbol``. Returns the adapter dict
    or ``None`` (caller cross-validates against whatever sources responded)."""
    client = _get_client()
    if client is None:
        return None
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    try:
        from alpaca.data.requests import OptionChainRequest
        # type="call" filters to calls when the SDK/plan supports it; we also
        # guard on the contract symbol below in case the filter is ignored.
        try:
            req = OptionChainRequest(underlying_symbol=sym, type="call")
        except TypeError:
            req = OptionChainRequest(underlying_symbol=sym)
        chain = client.get_option_chain(req)
    except Exception as e:
        logger.debug(f"Alpaca option chain fetch failed for {sym}: {e}")
        return None

    calls = []
    try:
        items = chain.items() if hasattr(chain, "items") else (chain or {}).items()
        for contract_symbol, snap in items:
            strike, expiry, is_call = _parse_occ(contract_symbol)
            if strike is None or not is_call:
                continue
            oi = getattr(snap, "open_interest", None)
            # Day volume isn't always present on the snapshot; pull it if the
            # SDK exposes a daily bar, else 0 (OI still cross-validates).
            vol = getattr(snap, "volume", None)
            if vol is None:
                bar = getattr(snap, "daily_bar", None) or getattr(snap, "latest_bar", None)
                vol = getattr(bar, "volume", None) if bar is not None else None
            lt = getattr(snap, "latest_trade", None)
            last = getattr(lt, "price", None) if lt is not None else None
            calls.append({
                "strike": round(_num(strike), 2),
                "expiry": expiry,
                "volume": int(_num(vol, int, 0) or 0),
                "open_interest": int(_num(oi, int, 0) or 0),
                "last": round(_num(last, float, 0) or 0, 2),
            })
    except Exception as e:
        logger.debug(f"Alpaca option chain map failed for {sym}: {e}")
        return None

    if not calls:
        return None
    return {"source": "alpaca", "price": 0.0, "calls": calls}


def _parse_occ(occ):
    """Parse an OCC option symbol (e.g. AAPL250117C00150000) into
    (strike, 'YYYY-MM-DD', is_call). Returns (None, None, False) on any error."""
    try:
        s = str(occ)
        # Find the 6-digit yymmdd + C/P + 8-digit strike tail.
        # Root is variable length; the date starts 15 chars from the end.
        tail = s[-15:]
        yymmdd = tail[:6]
        cp = tail[6]
        strike_raw = tail[7:]
        expiry = f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
        strike = int(strike_raw) / 1000.0
        return strike, expiry, (cp.upper() == "C")
    except Exception:
        return None, None, False
