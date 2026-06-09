"""Webull options market-data adapter (seam).

The app's Webull integration (``stock_bot/broker_client.py``) is built on the
official ``webullsdkcore`` / ``webullsdktrade`` packages, which expose option
*order placement* but **not** option *market data* (chains / per-contract
volume). Options quote data in the Webull OpenAPI lives in the separate
``webull-python-sdk-mdata`` package, which is not installed today and is
gated behind a paid market-data subscription.

So this module is a *seam*: it tries to fetch a call chain from Webull and
returns ``None`` when Webull can't supply it. Callers (see
``features/options_calls/engine.py``) fall back to yfinance — which already
powers ``features/watchdog/options_flow.py`` — so the feature works today and
flips to Webull automatically the moment the mdata SDK + credentials are
wired in. This honors CLAUDE.md rule #3 (every external-data path keeps a
non-vendor fallback).

Return contract — ``fetch_call_chain(symbol)`` returns either ``None`` (not
available, caller should fall back) or a dict::

    {
        "source": "webull",
        "price": <float spot price or 0>,
        "calls": [
            {"strike": float, "expiry": "YYYY-MM-DD", "volume": int,
             "open_interest": int, "last": float},
            ...
        ],
    }

Only call contracts are returned — this feature is about call activity.
"""

import logging
import os

logger = logging.getLogger(__name__)


def is_webull_options_available():
    """True only when the Webull market-data SDK is importable *and* a Webull
    app key is configured. Today the SDK is absent, so this is False and every
    caller falls back to yfinance. Cheap to call (no network)."""
    if not (os.getenv("WEBULL_APP_KEY") or os.getenv("WEBULL_APP_SECRET")):
        return False
    try:
        import webullsdkmdata  # noqa: F401
    except Exception:
        return False
    return True


def fetch_call_chain(symbol, app_key=None, app_secret=None):
    """Fetch the near-dated call chain for ``symbol`` from Webull market data.

    Returns the dict described in the module docstring, or ``None`` when Webull
    options data is unavailable (the common case today) so the caller falls
    back to yfinance. Never raises — failures degrade to ``None``.
    """
    if not is_webull_options_available():
        return None

    key = app_key or os.getenv("WEBULL_APP_KEY", "")
    secret = app_secret or os.getenv("WEBULL_APP_SECRET", "")
    if not (key and secret):
        return None

    try:
        # NOTE: wiring point. When webull-python-sdk-mdata is installed and the
        # account carries an options market-data subscription, build the mdata
        # client here and map its option-chain response into the return
        # contract. Kept behind the availability gate above so importing this
        # module never hard-depends on the mdata SDK.
        from webullsdkcore.client import ApiClient
        from webullsdkmdata.quotes.option import Option  # type: ignore

        api_client = ApiClient(app_key=key, app_secret=secret, region_id="us")
        option_api = Option(api_client)
        raw = option_api.get_option_chain(symbol)  # shape is SDK-version specific
        calls = _map_webull_calls(raw)
        if not calls:
            return None
        return {"source": "webull", "price": _extract_price(raw), "calls": calls}
    except Exception as e:
        logger.debug(f"Webull options fetch unavailable for {symbol}: {e}")
        return None


def _map_webull_calls(raw):
    """Best-effort map of a Webull option-chain payload into our call list.

    Defensive: the OpenAPI mdata option-chain schema is version-specific and
    not yet pinned, so we tolerate missing keys and skip non-call legs. Returns
    [] when nothing usable is present (caller then falls back)."""
    calls = []
    try:
        legs = raw.get("data") if isinstance(raw, dict) else raw
        for leg in (legs or []):
            if str(leg.get("direction", leg.get("optionType", ""))).lower() not in ("call", "c"):
                continue
            calls.append({
                "strike": float(leg.get("strikePrice", leg.get("strike", 0)) or 0),
                "expiry": str(leg.get("expireDate", leg.get("expiry", "")) or ""),
                "volume": int(float(leg.get("volume", 0) or 0)),
                "open_interest": int(float(leg.get("openInterest", leg.get("openIntrest", 0)) or 0)),
                "last": float(leg.get("close", leg.get("lastPrice", 0)) or 0),
            })
    except Exception:
        return []
    return calls


def _extract_price(raw):
    try:
        if isinstance(raw, dict):
            return float(raw.get("close", raw.get("price", 0)) or 0)
    except Exception:
        pass
    return 0.0
