"""
Stock Broker API Wrappers — Alpaca (Paper) & Webull.
Hardcoded safety: paper=True always for Alpaca. No live trading.
"""

import os
import logging
import uuid
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Module-level defaults (used when no per-user keys are provided)
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
WEBULL_APP_KEY = os.getenv("WEBULL_APP_KEY", "")
WEBULL_APP_SECRET = os.getenv("WEBULL_APP_SECRET", "")
WEBULL_ACCOUNT_ID = os.getenv("WEBULL_ACCOUNT_ID", "")
# Webull OpenAPI production endpoint (no sandbox exists — use paper trading account)
WEBULL_API_HOST = "api.webull.com"

# Stock mapping: symbol -> metadata
STOCK_MAP = {
    "AAPL":  {"yf": "AAPL",  "name": "Apple"},
    "TSLA":  {"yf": "TSLA",  "name": "Tesla"},
    "NVDA":  {"yf": "NVDA",  "name": "NVIDIA"},
    "MSFT":  {"yf": "MSFT",  "name": "Microsoft"},
    "AMZN":  {"yf": "AMZN",  "name": "Amazon"},
    "GOOGL": {"yf": "GOOGL", "name": "Alphabet"},
    "META":  {"yf": "META",  "name": "Meta Platforms"},
    "AMD":   {"yf": "AMD",   "name": "AMD"},
    "PLTR":  {"yf": "PLTR",  "name": "Palantir"},
    "COIN":  {"yf": "COIN",  "name": "Coinbase"},
    "SOFI":  {"yf": "SOFI",  "name": "SoFi Technologies"},
    "SPY":   {"yf": "SPY",   "name": "S&P 500 ETF"},
    "QQQ":   {"yf": "QQQ",   "name": "Nasdaq 100 ETF"},
    "MARA":  {"yf": "MARA",  "name": "Marathon Digital"},
    "RIOT":  {"yf": "RIOT",  "name": "Riot Platforms"},
}

DEFAULT_STOCKS = ["AAPL", "TSLA", "NVDA", "MSFT", "AMD"]


def get_stock_info(symbol: str) -> dict:
    """Get stock info for any ticker. Uses STOCK_MAP for known stocks,
    falls back to yfinance lookup for custom tickers."""
    symbol = symbol.upper().strip()
    if symbol in STOCK_MAP:
        return STOCK_MAP[symbol]
    # For any ticker, yf symbol = ticker itself
    return {"yf": symbol, "name": symbol}


def validate_ticker(symbol: str) -> dict:
    """Validate a ticker exists via yfinance. Returns {valid, name, price} or {valid: False, error}."""
    import yfinance as yf
    symbol = symbol.upper().strip()
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        name = info.get("shortName") or info.get("longName") or ""
        price = info.get("regularMarketPrice") or info.get("currentPrice") or 0
        if not name and not price:
            # Try fetching recent history as fallback
            hist = ticker.history(period="5d")
            if hist.empty:
                return {"valid": False, "error": f"Ticker '{symbol}' not found"}
            price = float(hist["Close"].iloc[-1])
            name = symbol
        return {"valid": True, "symbol": symbol, "name": name, "price": price}
    except Exception as e:
        return {"valid": False, "error": str(e)}


def is_market_open(extended_hours: bool = False) -> dict:
    """Check if US stock market is currently open using Eastern time.

    Args:
        extended_hours: If True, treat pre-market (4:00-9:30 AM) and
                       after-hours (4:00-8:00 PM) as open for trading.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    now = datetime.now(eastern)
    weekday = now.weekday()  # 0=Monday, 6=Sunday

    hour = now.hour
    minute = now.minute
    time_minutes = hour * 60 + minute

    # Weekend
    if weekday >= 5:
        return {"is_open": False, "status": "closed", "reason": "Weekend"}

    market_open = 9 * 60 + 30   # 9:30 AM ET
    market_close = 16 * 60       # 4:00 PM ET
    pre_market_start = 4 * 60    # 4:00 AM ET
    after_hours_end = 20 * 60    # 8:00 PM ET

    if market_open <= time_minutes < market_close:
        return {"is_open": True, "status": "open", "reason": "Regular market hours"}
    elif pre_market_start <= time_minutes < market_open:
        return {"is_open": extended_hours, "status": "pre_market", "reason": "Pre-market (4:00-9:30 AM ET)"}
    elif market_close <= time_minutes < after_hours_end:
        return {"is_open": extended_hours, "status": "after_hours", "reason": "After hours (4:00-8:00 PM ET)"}
    else:
        return {"is_open": False, "status": "closed", "reason": "Market closed"}


class AlpacaClient:
    """Wrapper around Alpaca SDK. Supports paper and live trading modes."""

    def __init__(self, api_key=None, secret_key=None, paper=True, use_env_fallback=False):
        """Initialize with per-user keys. Only falls back to env vars when use_env_fallback=True.
        paper=True (default) for paper trading, paper=False for live trading."""
        if use_env_fallback:
            self._api_key = api_key or ALPACA_API_KEY
            self._secret_key = secret_key or ALPACA_SECRET_KEY
        else:
            self._api_key = api_key or ""
            self._secret_key = secret_key or ""
        self._paper_mode = paper
        self._client = None
        self._initialized = False

    def _ensure_client(self):
        """Lazy-init the Alpaca trading client."""
        if self._initialized:
            return

        if not all([self._api_key, self._secret_key]):
            raise RuntimeError(
                "Alpaca API credentials not configured. "
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in settings."
            )

        try:
            from alpaca.trading.client import TradingClient
            self._client = TradingClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
                paper=self._paper_mode,
            )
            self._initialized = True
            mode = "paper" if self._paper_mode else "LIVE"
            logger.info(f"Alpaca client initialized ({mode} trading)")
        except ImportError:
            raise RuntimeError("alpaca-py package not installed. Run: pip install alpaca-py")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Alpaca client: {e}")

    def is_configured(self) -> bool:
        """Check if Alpaca credentials are set."""
        return bool(self._api_key and self._secret_key)

    def get_balance(self) -> dict:
        """Get account balance."""
        self._ensure_client()
        try:
            account = self._client.get_account()
            return {
                "total_equity": float(account.equity or 0),
                "available": float(account.buying_power or 0),
                "unrealized_pnl": float(account.unrealized_pl or 0),
            }
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return {"total_equity": 0, "available": 0, "unrealized_pnl": 0, "error": str(e)}

    def get_positions(self) -> list:
        """Get all open positions."""
        self._ensure_client()
        try:
            positions = self._client.get_all_positions()
            result = []
            for pos in positions:
                result.append({
                    "coin": pos.symbol,
                    "side": "buy" if pos.side.value == "long" else "sell",
                    "size": float(pos.qty or 0),
                    "entry_price": float(pos.avg_entry_price or 0),
                    "unrealized_pnl": float(pos.unrealized_pl or 0),
                    "market_value": float(pos.market_value or 0),
                })
            return result
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def get_ticker_price(self, symbol: str) -> float:
        """Get current price via yfinance (free, no data subscription needed)."""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1d", interval="1m")
            if not data.empty:
                return float(data["Close"].iloc[-1])
            # Fallback to daily data
            data = ticker.history(period="5d")
            if not data.empty:
                return float(data["Close"].iloc[-1])
            return 0.0
        except Exception as e:
            logger.error(f"Failed to get ticker price for {symbol}: {e}")
            return 0.0

    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = "market",
                    stop_loss: float = None,
                    take_profit: float = None) -> dict:
        """Place a stock order on Alpaca paper.

        Args:
            symbol: Stock ticker (e.g. "AAPL")
            side: "buy" or "sell"
            qty: Number of shares
            order_type: "market" (only market supported for now)
            stop_loss: Stop loss price
            take_profit: Take profit price
        """
        self._ensure_client()

        try:
            from alpaca.trading.requests import MarketOrderRequest, OrderSide, TimeInForce
            from alpaca.trading.requests import TakeProfitRequest, StopLossRequest

            order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

            # Build order request
            order_kwargs = {
                "symbol": symbol,
                "qty": qty,
                "side": order_side,
                "time_in_force": TimeInForce.DAY,
            }

            # Use bracket order if SL/TP provided
            if stop_loss and take_profit:
                order_kwargs["order_class"] = "bracket"
                order_kwargs["take_profit"] = TakeProfitRequest(limit_price=round(take_profit, 2))
                order_kwargs["stop_loss"] = StopLossRequest(stop_price=round(stop_loss, 2))

            order_request = MarketOrderRequest(**order_kwargs)
            order = self._client.submit_order(order_request)

            logger.info(f"Order placed: {side} {qty} {symbol} — id: {order.id}")
            return {
                "success": True,
                "order_id": str(order.id),
                "qty": float(qty),
            }
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return {"success": False, "error": str(e)}

    def close_position(self, symbol: str) -> dict:
        """Close a specific position."""
        self._ensure_client()
        try:
            self._client.close_position(symbol)
            logger.info(f"Position closed: {symbol}")
            return {"success": True}
        except Exception as e:
            logger.error(f"Failed to close position {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def close_all(self) -> list:
        """Close all open positions."""
        self._ensure_client()
        results = []
        try:
            self._client.close_all_positions(cancel_orders=True)
            results.append({"coin": "ALL", "success": True})
        except Exception as e:
            logger.error(f"Failed to close all positions: {e}")
            results.append({"coin": "ALL", "success": False, "error": str(e)})
        return results

    def get_day_trade_count(self) -> int:
        """Get the number of day trades in the rolling 5-day window."""
        self._ensure_client()
        try:
            account = self._client.get_account()
            return int(account.daytrade_count or 0)
        except Exception:
            return 0

    def get_account_equity(self) -> float:
        """Get account equity for PDT check."""
        self._ensure_client()
        try:
            account = self._client.get_account()
            return float(account.equity or 0)
        except Exception:
            return 0.0


class WebullClient:
    """Webull OpenAPI client with HMAC-SHA1 signing.
    Uses official Webull OpenAPI endpoints (https://developer.webull.com/api-doc/).
    Paper trading is done via a Webull paper trading account — there is no sandbox URL."""

    _instrument_cache = {}

    def __init__(self, app_key=None, app_secret=None, account_id=None):
        """Initialize with per-user keys. Each user must configure their own."""
        self._app_key = app_key or ""
        self._app_secret = app_secret or ""
        self._account_id = account_id or ""
        self._initialized = False
        self._sdk_available = False
        self._api = None  # SDK trade API object

    def _ensure_client(self):
        if self._initialized:
            return
        if not all([self._app_key, self._app_secret]):
            raise RuntimeError(
                "Webull credentials not configured. "
                "Set your App Key and App Secret in Broker Settings. "
                "Get them from https://developer.webull.com"
            )

        # Try official SDK first, fall back to raw HTTP
        try:
            from webullsdkcore.client import ApiClient
            from webullsdktrade.api import API
            api_client = ApiClient(
                app_key=self._app_key,
                app_secret=self._app_secret,
                region_id="us",
            )
            self._api = API(api_client)
            self._sdk_available = True
            logger.info("Webull client initialized via official SDK")
        except ImportError:
            self._sdk_available = False
            logger.info("Webull SDK not installed, using direct HTTP signing")
        except Exception as e:
            self._sdk_available = False
            logger.warning(f"Webull SDK init failed, falling back to HTTP: {e}")

        # Auto-discover account_id if not provided
        if not self._account_id:
            self._account_id = self._discover_account_id()
            if not self._account_id:
                raise RuntimeError(
                    "Webull Account ID not found. Either:\n"
                    "1. Enter it manually in Broker Settings, OR\n"
                    "2. Make sure your Webull account is linked to your API app at developer.webull.com"
                )
            logger.info(f"Webull account auto-discovered: {self._account_id}")

        self._initialized = True
        logger.info(f"Webull client ready (account={self._account_id})")

    def _discover_account_id(self) -> str:
        """Auto-discover account_id via /app/subscriptions/list endpoint."""
        try:
            if self._sdk_available and self._api:
                resp = self._api.account.get_app_subscriptions()
                data = resp.json() if hasattr(resp, 'json') else resp
                if isinstance(data, list) and data:
                    account_id = str(data[0].get("account_id", ""))
                    if account_id:
                        return account_id
            else:
                result = self._request_raw("GET", "/app/subscriptions/list")
                if result["ok"]:
                    data = result["data"]
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        account_id = str(item.get("account_id", ""))
                        if account_id:
                            return account_id
        except Exception as e:
            logger.warning(f"Webull account discovery failed: {e}")
        return ""

    def _get_host(self):
        return WEBULL_API_HOST

    def _get_base_url(self):
        return f"https://{self._get_host()}"

    def _sign_and_build_headers(self, uri: str, query_params: dict = None, body_params: dict = None) -> dict:
        """Build HMAC-SHA1 signed headers per Webull OpenAPI spec."""
        import hmac
        import hashlib
        import base64
        import socket
        import json as _json
        from urllib.parse import quote

        host = self._get_host()

        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        nonce_name = socket.gethostname() + str(uuid.uuid1())
        nonce = str(uuid.uuid5(uuid.NAMESPACE_URL, nonce_name))

        sign_headers = {
            "x-app-key": self._app_key,
            "x-timestamp": ts,
            "x-signature-version": "1.0",
            "x-signature-algorithm": "HMAC-SHA1",
            "x-signature-nonce": nonce,
            "Host": host,
        }

        # Merge sign headers + query params into sign_params (lowercase keys)
        sign_params = {k.lower(): v for k, v in sign_headers.items()}
        if query_params:
            for k, v in query_params.items():
                sign_params[k] = str(v)

        # Body MD5 (compact JSON, uppercase hex)
        body_string = None
        if body_params is not None:
            raw = _json.dumps(body_params, ensure_ascii=False, separators=(',', ':'))
            body_string = hashlib.md5(raw.encode()).hexdigest().upper()

        # Build string to sign: uri & sorted_kv [& body_md5]
        sorted_items = sorted(sign_params.items(), key=lambda x: x[0])
        sorted_kv = "&".join(f"{k}={v}" for k, v in sorted_items)
        string_to_sign = uri + "&" + sorted_kv
        if body_string:
            string_to_sign += "&" + body_string

        # URL-encode the entire string (no safe chars)
        string_to_sign = quote(string_to_sign, safe='')

        # HMAC-SHA1 with (app_secret + "&") as key
        key = (self._app_secret + "&").encode()
        signature = base64.b64encode(
            hmac.new(key, string_to_sign.encode(), hashlib.sha1).digest()
        ).decode().strip()

        return {
            "Content-Type": "application/json",
            "x-app-key": self._app_key,
            "x-timestamp": ts,
            "x-signature": signature,
            "x-signature-algorithm": "HMAC-SHA1",
            "x-signature-version": "1.0",
            "x-signature-nonce": nonce,
        }

    def _request_raw(self, method: str, path: str, params: dict = None, body: dict = None) -> dict:
        """Make a signed HTTP request to Webull API (direct, no SDK)."""
        import requests as req
        import json as _json

        base = self._get_base_url()
        headers = self._sign_and_build_headers(path, query_params=params, body_params=body)

        url = f"{base}{path}"
        if params:
            sorted_params = sorted(params.items())
            param_str = "&".join(f"{k}={v}" for k, v in sorted_params)
            url += "?" + param_str

        body_str = None
        if body is not None:
            body_str = _json.dumps(body, ensure_ascii=False, separators=(',', ':'))

        try:
            if method.upper() == "GET":
                resp = req.get(url, headers=headers, timeout=15)
            else:
                resp = req.post(url, headers=headers, data=body_str, timeout=15)

            if resp.status_code == 200:
                try:
                    return {"ok": True, "data": resp.json(), "status": 200}
                except Exception:
                    return {"ok": True, "data": {}, "status": 200}
            else:
                error_msg = f"HTTP {resp.status_code}"
                try:
                    err_data = resp.json()
                    error_msg = err_data.get("message", err_data.get("error_code", resp.text))
                except Exception:
                    error_msg = resp.text[:200]
                return {"ok": False, "error": error_msg, "status": resp.status_code}
        except req.exceptions.Timeout:
            return {"ok": False, "error": "Request timed out", "status": 0}
        except req.exceptions.ConnectionError as e:
            return {"ok": False, "error": f"Connection failed: {e}", "status": 0}

    def is_configured(self) -> bool:
        return bool(self._app_key and self._app_secret)

    def get_balance(self) -> dict:
        self._ensure_client()
        try:
            if self._sdk_available and self._api:
                resp = self._api.account.get_account_balance(
                    account_id=self._account_id,
                    total_asset_currency="USD",
                )
                data = resp.json() if hasattr(resp, 'json') else resp
            else:
                result = self._request_raw(
                    "GET", "/account/balance",
                    params={"account_id": self._account_id, "total_asset_currency": "USD"},
                )
                if not result["ok"]:
                    return {"total_equity": 0, "available": 0, "unrealized_pnl": 0, "error": result["error"]}
                data = result["data"]

            # Parse balance — handle nested account_currency_assets
            total_equity = float(data.get("total_asset", 0))
            cash_balance = float(data.get("total_cash_balance", 0))
            unrealized = 0.0

            # Try to get more detailed info from currency assets
            currency_assets = data.get("account_currency_assets", [])
            for ca in currency_assets:
                if ca.get("currency", "").upper() == "USD":
                    total_equity = float(ca.get("net_liquidation_value", total_equity))
                    cash_balance = float(ca.get("cash_balance", cash_balance))
                    break

            return {
                "total_equity": total_equity,
                "available": cash_balance,
                "unrealized_pnl": unrealized,
            }
        except Exception as e:
            logger.error(f"Webull get_balance failed: {e}")
            return {"total_equity": 0, "available": 0, "unrealized_pnl": 0, "error": str(e)}

    def get_positions(self) -> list:
        self._ensure_client()
        try:
            if self._sdk_available and self._api:
                resp = self._api.account.get_account_position(
                    account_id=self._account_id,
                    page_size=100,
                )
                data = resp.json() if hasattr(resp, 'json') else resp
            else:
                result = self._request_raw(
                    "GET", "/account/positions",
                    params={"account_id": self._account_id, "page_size": "100"},
                )
                if not result["ok"]:
                    logger.error(f"Webull get_positions: {result.get('error')}")
                    return []
                data = result["data"]

            positions = []
            for h in data.get("holdings", []):
                qty = float(h.get("qty", 0))
                if qty == 0:
                    continue
                instrument_id = str(h.get("instrument_id", ""))
                symbol = h.get("symbol", "")
                if instrument_id and symbol:
                    self._instrument_cache[symbol] = instrument_id
                positions.append({
                    "coin": symbol,
                    "side": "buy",
                    "size": qty,
                    "entry_price": float(h.get("unit_cost", 0)),
                    "unrealized_pnl": float(h.get("unrealized_profit_loss", 0)),
                    "market_value": float(h.get("market_value", 0)),
                    "instrument_id": instrument_id,
                })
            return positions
        except Exception as e:
            logger.error(f"Webull get_positions failed: {e}")
            return []

    def get_ticker_price(self, symbol: str) -> float:
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1d", interval="1m")
            if not data.empty:
                return float(data["Close"].iloc[-1])
            data = ticker.history(period="5d")
            if not data.empty:
                return float(data["Close"].iloc[-1])
            return 0.0
        except Exception as e:
            logger.error(f"Failed to get ticker price for {symbol}: {e}")
            return 0.0

    def _get_instrument_id(self, symbol: str) -> str:
        """Resolve symbol to Webull instrument_id via /instrument/list endpoint."""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        # Check positions cache first
        try:
            for pos in self.get_positions():
                if pos["coin"] == symbol and pos.get("instrument_id"):
                    self._instrument_cache[symbol] = pos["instrument_id"]
                    return pos["instrument_id"]
        except Exception:
            pass

        # Use the official /instrument/list endpoint (not /instrument/search)
        try:
            if self._sdk_available and self._api:
                resp = self._api.instrument.get_instrument(symbols=symbol, category="US_STOCK")
                data = resp.json() if hasattr(resp, 'json') else resp
                items = data if isinstance(data, list) else [data]
            else:
                result = self._request_raw(
                    "GET", "/instrument/list",
                    params={"symbols": symbol, "category": "US_STOCK"},
                )
                if not result["ok"]:
                    # Try as ETF
                    result = self._request_raw(
                        "GET", "/instrument/list",
                        params={"symbols": symbol, "category": "US_ETF"},
                    )
                if result["ok"]:
                    data = result["data"]
                    items = data if isinstance(data, list) else [data]
                else:
                    items = []

            for item in items:
                iid = str(item.get("instrument_id", ""))
                sym = item.get("symbol", "")
                if iid:
                    self._instrument_cache[sym or symbol] = iid
                    if sym == symbol or not sym:
                        return iid
        except Exception as e:
            logger.warning(f"Webull instrument lookup for {symbol}: {e}")

        raise RuntimeError(f"Cannot resolve instrument_id for {symbol}. Verify the ticker is valid on Webull.")

    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = "market",
                    stop_loss: float = None,
                    take_profit: float = None) -> dict:
        self._ensure_client()
        try:
            instrument_id = self._get_instrument_id(symbol)
            order_side = "BUY" if side.lower() == "buy" else "SELL"
            client_order_id = uuid.uuid4().hex[:32]

            if self._sdk_available and self._api:
                resp = self._api.order.place_order(
                    account_id=self._account_id,
                    qty=str(int(qty)),
                    instrument_id=instrument_id,
                    side=order_side,
                    client_order_id=client_order_id,
                    order_type="MARKET",
                    extended_hours_trading=False,
                    tif="DAY",
                )
                data = resp.json() if hasattr(resp, 'json') else resp
                logger.info(f"Webull order placed via SDK: {side} {qty} {symbol}")
            else:
                order_body = {
                    "account_id": self._account_id,
                    "stock_order": {
                        "client_order_id": client_order_id,
                        "side": order_side,
                        "tif": "DAY",
                        "extended_hours_trading": False,
                        "instrument_id": instrument_id,
                        "order_type": "MARKET",
                        "qty": str(int(qty)),
                    },
                }
                result = self._request_raw("POST", "/trade/order/place", body=order_body)
                if not result["ok"]:
                    error_msg = result.get("error", "Unknown")
                    # Handle known Webull errors with user-friendly messages
                    if "FIXGW_NOT_READY_MARKET" in str(error_msg):
                        error_msg = "Market is closed — Webull FIX gateway not available outside trading hours"
                    elif "NOT_TRADING" in str(error_msg):
                        error_msg = f"Trading not available: {error_msg}"
                    logger.error(f"Webull order failed: {error_msg}")
                    return {"success": False, "error": error_msg}
                logger.info(f"Webull order placed: {side} {qty} {symbol}")

            # Place separate SL order if provided
            if stop_loss:
                sl_side = "SELL" if order_side == "BUY" else "BUY"
                sl_id = uuid.uuid4().hex[:32]
                if self._sdk_available and self._api:
                    try:
                        self._api.order.place_order(
                            account_id=self._account_id,
                            qty=str(int(qty)),
                            instrument_id=instrument_id,
                            side=sl_side,
                            client_order_id=sl_id,
                            order_type="STOP_LOSS",
                            stop_price=str(round(stop_loss, 2)),
                            extended_hours_trading=False,
                            tif="GTC",
                        )
                    except Exception as e:
                        logger.warning(f"Webull SL order failed: {e}")
                else:
                    sl_body = {
                        "account_id": self._account_id,
                        "stock_order": {
                            "client_order_id": sl_id,
                            "side": sl_side,
                            "tif": "GTC",
                            "extended_hours_trading": False,
                            "instrument_id": instrument_id,
                            "order_type": "STOP_LOSS",
                            "qty": str(int(qty)),
                            "stop_price": str(round(stop_loss, 2)),
                        },
                    }
                    self._request_raw("POST", "/trade/order/place", body=sl_body)

            # Place separate TP order if provided
            if take_profit:
                tp_side = "SELL" if order_side == "BUY" else "BUY"
                tp_id = uuid.uuid4().hex[:32]
                if self._sdk_available and self._api:
                    try:
                        self._api.order.place_order(
                            account_id=self._account_id,
                            qty=str(int(qty)),
                            instrument_id=instrument_id,
                            side=tp_side,
                            client_order_id=tp_id,
                            order_type="LIMIT",
                            limit_price=str(round(take_profit, 2)),
                            extended_hours_trading=False,
                            tif="GTC",
                        )
                    except Exception as e:
                        logger.warning(f"Webull TP order failed: {e}")
                else:
                    tp_body = {
                        "account_id": self._account_id,
                        "stock_order": {
                            "client_order_id": tp_id,
                            "side": tp_side,
                            "tif": "GTC",
                            "extended_hours_trading": False,
                            "instrument_id": instrument_id,
                            "order_type": "LIMIT",
                            "qty": str(int(qty)),
                            "limit_price": str(round(take_profit, 2)),
                        },
                    }
                    self._request_raw("POST", "/trade/order/place", body=tp_body)

            return {"success": True, "order_id": client_order_id, "qty": float(qty)}
        except Exception as e:
            error_msg = str(e)
            if "FIXGW_NOT_READY_MARKET" in error_msg or "CAN_NOT_TRADING" in error_msg:
                error_msg = "Market is closed — Webull FIX gateway not available outside trading hours"
            logger.error(f"Webull place_order failed: {error_msg}")
            return {"success": False, "error": error_msg}

    def close_position(self, symbol: str) -> dict:
        self._ensure_client()
        try:
            for pos in self.get_positions():
                if pos["coin"] == symbol and pos["size"] > 0:
                    return self.place_order(symbol, "sell", pos["size"])
            return {"success": False, "error": f"No open position for {symbol}"}
        except Exception as e:
            logger.error(f"Webull close_position failed: {e}")
            return {"success": False, "error": str(e)}

    def close_all(self) -> list:
        self._ensure_client()
        results = []
        try:
            for pos in self.get_positions():
                if pos["size"] > 0:
                    r = self.place_order(pos["coin"], "sell", pos["size"])
                    r["coin"] = pos["coin"]
                    results.append(r)
            if not results:
                results.append({"coin": "ALL", "success": True})
        except Exception as e:
            logger.error(f"Webull close_all failed: {e}")
            results.append({"coin": "ALL", "success": False, "error": str(e)})
        return results

    def get_day_trade_count(self) -> int:
        """Webull API doesn't expose a PDT counter directly."""
        return 0

    def get_account_equity(self) -> float:
        balance = self.get_balance()
        return balance.get("total_equity", 0.0)


def get_broker_client(broker: str = "alpaca", paper: bool = True, **keys):
    """Factory: return the correct broker client based on config.

    Args:
        broker: "alpaca" or "webull"
        paper: True → paper trading; False → live trading. Caller must
            explicitly pass paper=False to enable live (default is safe).
        **keys: Per-user API keys. For Alpaca: api_key, secret_key.
                For Webull: app_key, app_secret, account_id.
    """
    if broker == "webull":
        # Webull SDK is sandbox-only in this app — paper kwarg is ignored.
        return WebullClient(
            app_key=keys.get("app_key"),
            app_secret=keys.get("app_secret"),
            account_id=keys.get("account_id"),
        )
    return AlpacaClient(
        api_key=keys.get("api_key"),
        secret_key=keys.get("secret_key"),
        paper=paper,
    )
