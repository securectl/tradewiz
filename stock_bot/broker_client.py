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
WEBULL_SANDBOX = os.getenv("WEBULL_SANDBOX", "1")  # "1" = sandbox/UAT, "0" = production
WEBULL_SANDBOX_ENDPOINT = "us-openapi-alb.uat.webullbroker.com"

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
    """Wrapper around Alpaca SDK. Paper trading only."""

    def __init__(self, api_key=None, secret_key=None):
        """Initialize with optional per-user keys. Falls back to env vars."""
        self._api_key = api_key or ALPACA_API_KEY
        self._secret_key = secret_key or ALPACA_SECRET_KEY
        self._client = None
        self._initialized = False

    def _ensure_client(self):
        """Lazy-init the Alpaca trading client — paper only."""
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
                paper=True,  # SAFETY: Always paper trading
            )
            self._initialized = True
            logger.info("Alpaca client initialized (paper trading)")
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
    """Direct HTTP client for Webull OpenAPI (no SDK dependency).
    Implements HMAC-SHA1 signing per Webull spec.
    Defaults to sandbox/UAT for paper trading."""

    _instrument_cache = {}

    def __init__(self, app_key=None, app_secret=None, account_id=None):
        """Initialize with optional per-user keys. Falls back to env vars."""
        self._app_key = app_key or WEBULL_APP_KEY
        self._app_secret = app_secret or WEBULL_APP_SECRET
        self._account_id = account_id or WEBULL_ACCOUNT_ID
        self._initialized = False

    def _ensure_client(self):
        if self._initialized:
            return
        if not all([self._app_key, self._app_secret]):
            raise RuntimeError("Webull credentials not configured. Set WEBULL_APP_KEY and WEBULL_APP_SECRET in settings.")
        if not self._account_id:
            raise RuntimeError("Webull Account ID not configured. Set WEBULL_ACCOUNT_ID in settings.")
        self._initialized = True
        env = "SANDBOX" if WEBULL_SANDBOX == "1" else "PRODUCTION"
        logger.info(f"Webull client initialized ({env})")

    def _get_host(self):
        if WEBULL_SANDBOX == "1":
            return WEBULL_SANDBOX_ENDPOINT
        return "api.webull.com"

    def _get_base_url(self):
        return f"https://{self._get_host()}"

    def _sign_and_build_headers(self, uri: str, query_params: dict = None, body_params: dict = None) -> dict:
        """Build HMAC-SHA1 signed headers matching Webull SDK exactly."""
        import hmac
        import hashlib
        import base64
        import socket
        import json as _json
        from urllib.parse import quote

        host = self._get_host()

        # Generate sign headers (same as SDK's _refresh_sign_headers)
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

        # Build string to sign: uri + "&" + sorted_kv + ["&" + body_md5]
        sorted_items = sorted(sign_params.items(), key=lambda x: x[0])
        sorted_kv = "&".join(f"{k}={v}" for k, v in sorted_items)
        string_to_sign = uri + "&" + sorted_kv
        if body_string:
            string_to_sign += "&" + body_string

        # URL-encode the entire string (safe='')
        string_to_sign = quote(string_to_sign, safe='')

        # HMAC-SHA1 with (app_secret + "&") as key
        key = (self._app_secret + "&").encode()
        signature = base64.b64encode(
            hmac.new(key, string_to_sign.encode(), hashlib.sha1).digest()
        ).decode().strip()

        # Return headers for the actual request
        return {
            "Content-Type": "application/json",
            "x-app-key": self._app_key,
            "x-timestamp": ts,
            "x-signature": signature,
            "x-signature-algorithm": "HMAC-SHA1",
            "x-signature-version": "1.0",
            "x-signature-nonce": nonce,
        }

    def _request(self, method: str, path: str, params: dict = None, body: dict = None) -> dict:
        """Make a signed HTTP request to Webull API."""
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
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text}", "status": resp.status_code}

    def is_configured(self) -> bool:
        return bool(self._app_key and self._app_secret and self._account_id)

    def get_balance(self) -> dict:
        self._ensure_client()
        try:
            result = self._request("GET", f"/account/balance",
                                   params={"account_id": self._account_id, "currency": "USD"})
            if result["ok"]:
                data = result["data"]
                return {
                    "total_equity": float(data.get("total_asset", 0)),
                    "available": float(data.get("total_cash_balance", 0)),
                    "unrealized_pnl": 0.0,
                }
            return {"total_equity": 0, "available": 0, "unrealized_pnl": 0, "error": result.get("error", "")}
        except Exception as e:
            logger.error(f"Webull get_balance failed: {e}")
            return {"total_equity": 0, "available": 0, "unrealized_pnl": 0, "error": str(e)}

    def get_positions(self) -> list:
        self._ensure_client()
        try:
            result = self._request("GET", f"/account/positions",
                                   params={"account_id": self._account_id, "page_size": "100"})
            if not result["ok"]:
                logger.error(f"Webull get_positions: {result.get('error')}")
                return []
            data = result["data"]
            positions = []
            for h in data.get("holdings", []):
                qty = float(h.get("qty", 0))
                if qty == 0:
                    continue
                positions.append({
                    "coin": h.get("symbol", ""),
                    "side": "buy",
                    "size": qty,
                    "entry_price": float(h.get("unit_cost", 0)),
                    "unrealized_pnl": float(h.get("unrealized_profit_loss", 0)),
                    "market_value": float(h.get("market_value", 0)),
                    "instrument_id": h.get("instrument_id", ""),
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
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]
        # Try positions first
        try:
            for pos in self.get_positions():
                if pos["coin"] == symbol and pos.get("instrument_id"):
                    self._instrument_cache[symbol] = pos["instrument_id"]
                    return pos["instrument_id"]
        except Exception:
            pass
        # Search instruments
        try:
            result = self._request("GET", "/instrument/search", params={"symbol": symbol})
            if result["ok"]:
                data = result["data"]
                items = data if isinstance(data, list) else data.get("instruments", [data])
                for item in items:
                    iid = str(item.get("instrument_id", ""))
                    if iid:
                        self._instrument_cache[symbol] = iid
                        return iid
        except Exception as e:
            logger.warning(f"Webull instrument lookup for {symbol}: {e}")
        raise RuntimeError(f"Cannot resolve instrument_id for {symbol}")

    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = "market",
                    stop_loss: float = None,
                    take_profit: float = None) -> dict:
        self._ensure_client()
        try:
            instrument_id = self._get_instrument_id(symbol)
            order_side = "BUY" if side.lower() == "buy" else "SELL"
            client_order_id = uuid.uuid4().hex[:32]

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

            result = self._request("POST", "/trade/order/place", body=order_body)
            if result["ok"]:
                logger.info(f"Webull order placed: {side} {qty} {symbol}")

                # Place separate SL if provided
                if stop_loss and order_side == "BUY":
                    sl_body = {
                        "account_id": self._account_id,
                        "stock_order": {
                            "client_order_id": uuid.uuid4().hex[:32],
                            "side": "SELL",
                            "tif": "GTC",
                            "extended_hours_trading": False,
                            "instrument_id": instrument_id,
                            "order_type": "STOP_LOSS",
                            "qty": str(int(qty)),
                            "stop_price": str(round(stop_loss, 2)),
                        },
                    }
                    self._request("POST", "/trade/order/place", body=sl_body)

                return {"success": True, "order_id": client_order_id, "qty": float(qty)}
            else:
                logger.error(f"Webull order failed: {result.get('error')}")
                return {"success": False, "error": result.get("error", "Unknown")}
        except Exception as e:
            logger.error(f"Webull place_order failed: {e}")
            return {"success": False, "error": str(e)}

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
        return 0

    def get_account_equity(self) -> float:
        balance = self.get_balance()
        return balance.get("total_equity", 0.0)


def get_broker_client(broker: str = "alpaca", **keys):
    """Factory: return the correct broker client based on config.

    Args:
        broker: "alpaca" or "webull"
        **keys: Per-user API keys. For Alpaca: api_key, secret_key.
                For Webull: app_key, app_secret, account_id.
    """
    if broker == "webull":
        return WebullClient(
            app_key=keys.get("app_key"),
            app_secret=keys.get("app_secret"),
            account_id=keys.get("account_id"),
        )
    return AlpacaClient(
        api_key=keys.get("api_key"),
        secret_key=keys.get("secret_key"),
    )
