"""
BloFin Demo API Client — Paper Trading Only
Uses the blofin SDK (pip install blofin) with demo endpoint patched in.
Hardcoded safety: refuses to connect to production endpoint.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Module-level defaults (used when no per-user keys are provided)
BLOFIN_API_KEY = os.getenv("BLOFIN_API_KEY", "")
BLOFIN_API_SECRET = os.getenv("BLOFIN_API_SECRET", "")
BLOFIN_PASSPHRASE = os.getenv("BLOFIN_PASSPHRASE", "")
BLOFIN_DEMO = os.getenv("BLOFIN_DEMO", "1")

# Coin mapping: yfinance ticker → BloFin instrument
COIN_MAP = {
    "BTC-USDT": {"yf": "BTC-USD", "blofin": "BTC-USDT", "name": "Bitcoin"},
    "ETH-USDT": {"yf": "ETH-USD", "blofin": "ETH-USDT", "name": "Ethereum"},
    "SOL-USDT": {"yf": "SOL-USD", "blofin": "SOL-USDT", "name": "Solana"},
    "BNB-USDT": {"yf": "BNB-USD", "blofin": "BNB-USDT", "name": "BNB"},
    "XRP-USDT": {"yf": "XRP-USD", "blofin": "XRP-USDT", "name": "XRP"},
    "ADA-USDT": {"yf": "ADA-USD", "blofin": "ADA-USDT", "name": "Cardano"},
    "DOGE-USDT": {"yf": "DOGE-USD", "blofin": "DOGE-USDT", "name": "Dogecoin"},
    "AVAX-USDT": {"yf": "AVAX-USD", "blofin": "AVAX-USDT", "name": "Avalanche"},
    "DOT-USDT": {"yf": "DOT-USD", "blofin": "DOT-USDT", "name": "Polkadot"},
    "LINK-USDT": {"yf": "LINK-USD", "blofin": "LINK-USDT", "name": "Chainlink"},
    "MATIC-USDT": {"yf": "MATIC-USD", "blofin": "MATIC-USDT", "name": "Polygon"},
    "ATOM-USDT": {"yf": "ATOM-USD", "blofin": "ATOM-USDT", "name": "Cosmos"},
}

# Default coins enabled on first run
DEFAULT_COINS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT"]


# yfinance tickers that differ from the standard {BASE}-USD format
YF_TICKER_MAP = {
    "SUI": "SUI20947-USD",
    "PEPE": "PEPE24478-USD",
    "ARB": "ARB11841-USD",
    "UNI": "UNI7083-USD",
    "RENDER": "RENDER-USD",
    "RNDR": "RENDER-USD",
    "OP": "OP-USD",
    "WIF": "WIF-USD",
    "NEAR": "NEAR-USD",
    "FET": "FET-USD",
    "INJ": "INJ-USD",
    "LTC": "LTC-USD",
    "SHIB": "SHIB-USD",
}


def _get_yf_ticker(base: str) -> str:
    """Get the correct yfinance ticker for a coin symbol.

    Some coins on yfinance have numeric suffixes (e.g., SUI20947-USD).
    This function checks the known map first, then tries the standard format.
    """
    base_upper = base.upper()
    if base_upper in YF_TICKER_MAP:
        return YF_TICKER_MAP[base_upper]
    return f"{base_upper}-USD"


def validate_yf_ticker(coin_key: str) -> bool:
    """Verify that a coin's yfinance ticker actually returns data."""
    try:
        import yfinance as yf
        info = COIN_MAP.get(coin_key)
        if not info:
            return False
        t = yf.Ticker(info["yf"])
        df = t.history(period="1d", interval="1h")
        return len(df) > 0
    except Exception:
        return False


def resolve_coin(ticker: str) -> dict:
    """Resolve any coin ticker to a COIN_MAP-compatible entry.

    Accepts formats: BTC-USDT, BTC, BTCUSDT, BTC-USD
    Returns dict with yf, blofin, name keys, or None if invalid.
    """
    ticker = ticker.strip().upper()

    # Already in COIN_MAP
    if ticker in COIN_MAP:
        return COIN_MAP[ticker]

    # Normalize to XXX-USDT format
    base = ticker.replace("-USDT", "").replace("-USD", "").replace("USDT", "").replace("USD", "")
    if not base:
        return None

    key = f"{base}-USDT"
    if key in COIN_MAP:
        return COIN_MAP[key]

    # Dynamic resolution for coins not in static map
    return {
        "yf": _get_yf_ticker(base),
        "blofin": f"{base}-USDT",
        "name": base,
    }


def ensure_coin_in_map(ticker: str) -> str:
    """Ensure a coin ticker is in COIN_MAP (add dynamically if needed).

    Returns the normalized COIN_MAP key (e.g., 'SUI-USDT') or None.
    """
    ticker = ticker.strip().upper()
    base = ticker.replace("-USDT", "").replace("-USD", "").replace("USDT", "").replace("USD", "")
    if not base:
        return None

    key = f"{base}-USDT"
    if key not in COIN_MAP:
        COIN_MAP[key] = {
            "yf": _get_yf_ticker(base),
            "blofin": f"{base}-USDT",
            "name": base,
        }
    return key

DEMO_URL = "https://demo-trading-openapi.blofin.com"


class BloFinClient:
    """Wrapper around BloFin SDK. Paper trading only (demo endpoint)."""

    def __init__(self, api_key=None, api_secret=None, passphrase=None, use_env_fallback=False):
        """Initialize with per-user keys. Only falls back to env vars when use_env_fallback=True."""
        if use_env_fallback:
            self._api_key = api_key or BLOFIN_API_KEY
            self._api_secret = api_secret or BLOFIN_API_SECRET
            self._passphrase = passphrase or BLOFIN_PASSPHRASE
        else:
            self._api_key = api_key or ""
            self._api_secret = api_secret or ""
            self._passphrase = passphrase or ""
        if BLOFIN_DEMO != "1":
            raise RuntimeError("SAFETY: BLOFIN_DEMO must be '1'. Refusing to use production endpoint.")
        self._client = None
        self._initialized = False
        self._position_mode = "net_mode"  # net_mode or hedge_mode
        self._instrument_cache = {}  # inst_id -> {contractValue, minSize, lotSize}

    def _ensure_client(self):
        """Lazy-init the BloFin client with demo endpoint patched in."""
        if self._initialized:
            return

        if not all([self._api_key, self._api_secret, self._passphrase]):
            raise RuntimeError("BloFin API credentials not configured. Set BLOFIN_API_KEY, BLOFIN_API_SECRET, BLOFIN_PASSPHRASE in settings.")

        if BLOFIN_DEMO != "1":
            raise RuntimeError("SAFETY: BLOFIN_DEMO must be '1'.")

        try:
            # Patch SDK to use demo endpoint
            import blofin.utils as _utils
            import blofin.constants as _consts
            _utils.REST_API_URL = DEMO_URL
            _consts.REST_API_URL = DEMO_URL

            from blofin import BloFinClient as _SDK
            self._client = _SDK(
                api_key=self._api_key,
                api_secret=self._api_secret,
                passphrase=self._passphrase,
            )
            self._initialized = True
            logger.info("BloFin client initialized (demo: %s)", DEMO_URL)

            # Detect position mode
            try:
                pm = self._client.trading.get_position_mode()
                if pm.get("data", {}).get("positionMode"):
                    self._position_mode = pm["data"]["positionMode"]
                    logger.info("Position mode: %s", self._position_mode)
            except Exception:
                pass

        except ImportError:
            raise RuntimeError("blofin package not installed. Run: pip install blofin")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize BloFin client: {e}")

    def is_configured(self) -> bool:
        """Check if BloFin credentials are set."""
        return bool(self._api_key and self._api_secret and self._passphrase and BLOFIN_DEMO == "1")

    def check_trade_permission(self) -> dict:
        """Check if the API key has trade permissions."""
        self._ensure_client()
        try:
            from blofin.utils import send_request
            result = send_request("GET", "/api/v1/user/query-apikey",
                                  self._client.auth, authenticate=True)
            if result.get("code") == "0" and result.get("data"):
                data = result["data"]
                read_only = data.get("readOnly", 1)
                return {
                    "has_trade_permission": read_only == 0,
                    "read_only": read_only == 1,
                    "api_name": data.get("apiName", ""),
                    "uid": data.get("uid", ""),
                    "message": "API key is READ-ONLY. Create a new key with Trade permission on BloFin." if read_only == 1 else "Trade permission OK",
                }
            return {"has_trade_permission": False, "read_only": True, "message": f"Failed to query API key: {result.get('msg', 'unknown')}"}
        except Exception as e:
            return {"has_trade_permission": False, "read_only": True, "message": str(e)}

    def get_instrument_info(self, inst_id: str) -> dict:
        """Get instrument specs (contract value, min size, lot size)."""
        if inst_id in self._instrument_cache:
            return self._instrument_cache[inst_id]
        self._ensure_client()
        try:
            from blofin.utils import send_request
            result = send_request("GET", "/api/v1/market/instruments",
                                  self._client.auth,
                                  params={"instType": "futures", "instId": inst_id})
            if result.get("data"):
                info = result["data"][0] if isinstance(result["data"], list) else result["data"]
                parsed = {
                    "contract_value": float(info.get("contractValue", "1")),
                    "min_size": float(info.get("minSize", "1")),
                    "lot_size": float(info.get("lotSize", "1")),
                    "tick_size": float(info.get("tickSize", "0.1")),
                    "max_leverage": int(info.get("maxLeverage", "10")),
                }
                self._instrument_cache[inst_id] = parsed
                return parsed
        except Exception as e:
            logger.error(f"Failed to get instrument info for {inst_id}: {e}")
        return {"contract_value": 0.001, "min_size": 1, "lot_size": 1, "tick_size": 0.1, "max_leverage": 10}

    def coins_to_contracts(self, inst_id: str, coin_amount: float) -> float:
        """Convert a coin amount to number of contracts, respecting lot size."""
        info = self.get_instrument_info(inst_id)
        cv = info["contract_value"]
        lot = info["lot_size"]
        min_sz = info["min_size"]
        raw_contracts = coin_amount / cv if cv > 0 else 0
        # Round down to nearest lot size
        contracts = int(raw_contracts / lot) * lot
        contracts = round(contracts, 4)  # avoid float precision issues
        if contracts < min_sz:
            contracts = min_sz
        return contracts

    def get_balance(self) -> dict:
        """Get futures account balance."""
        self._ensure_client()
        try:
            result = self._client.trading.get_futures_account_balance()
            if result and "data" in result and result["data"]:
                data = result["data"][0] if isinstance(result["data"], list) else result["data"]
                return {
                    "total_equity": float(data.get("totalEquity", 0) or 0),
                    "available": float(data.get("availableBalance", 0) or data.get("available", 0) or 0),
                    "used_margin": float(data.get("usedMargin", 0) or data.get("frozenBalance", 0) or 0),
                    "unrealized_pnl": float(data.get("unrealizedPnl", 0) or data.get("upl", 0) or 0),
                    "raw": data,
                }
            # Fallback: try account.get_balance with futures type
            result = self._client.account.get_balance(account_type="futures")
            if result and "data" in result and result["data"]:
                data = result["data"][0] if isinstance(result["data"], list) else result["data"]
                return {
                    "total_equity": float(data.get("totalEquity", 0) or data.get("balance", 0) or 0),
                    "available": float(data.get("availableBalance", 0) or data.get("available", 0) or 0),
                    "used_margin": float(data.get("usedMargin", 0) or 0),
                    "unrealized_pnl": float(data.get("unrealizedPnl", 0) or 0),
                    "raw": data,
                }
            return {"total_equity": 0, "available": 0, "used_margin": 0, "unrealized_pnl": 0, "raw": result}
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return {"total_equity": 0, "available": 0, "used_margin": 0, "unrealized_pnl": 0, "error": str(e)}

    def get_positions(self) -> list:
        """Get all open positions."""
        self._ensure_client()
        try:
            result = self._client.trading.get_positions()
            positions = []
            if result and "data" in result:
                for pos in result["data"]:
                    size = float(pos.get("positionSize", 0) or pos.get("positions", 0) or pos.get("pos", 0) or 0)
                    if size != 0:
                        positions.append({
                            "coin": pos.get("instId", ""),
                            "side": pos.get("posSide", pos.get("positionSide", "")),
                            "size": size,
                            "entry_price": float(pos.get("averagePrice", 0) or pos.get("avgPx", 0) or 0),
                            "mark_price": float(pos.get("markPrice", 0) or pos.get("markPx", 0) or 0),
                            "unrealized_pnl": float(pos.get("unrealizedPnl", 0) or pos.get("upl", 0) or 0),
                            "margin": float(pos.get("margin", 0) or 0),
                            "raw": pos,
                        })
            return positions
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def get_ticker_price(self, inst_id: str) -> float:
        """Get current price for an instrument."""
        self._ensure_client()
        try:
            result = self._client.public.get_tickers(inst_id=inst_id)
            if result and "data" in result and result["data"]:
                data = result["data"][0] if isinstance(result["data"], list) else result["data"]
                return float(data.get("lastPrice", 0) or data.get("last", 0) or 0)
            return 0.0
        except Exception as e:
            logger.error(f"Failed to get ticker price for {inst_id}: {e}")
            return 0.0

    def place_order(self, inst_id: str, side: str, size: float,
                    order_type: str = "market", price: float = None,
                    stop_loss: float = None, take_profit: float = None,
                    size_in_contracts: bool = False) -> dict:
        """Place an order on BloFin demo.

        Args:
            size: Amount in coins (will be converted to contracts) unless size_in_contracts=True
            size_in_contracts: If True, size is already in contracts
        """
        self._ensure_client()

        if BLOFIN_DEMO != "1":
            raise RuntimeError("SAFETY: Cannot place orders outside demo mode")

        try:
            # Convert coin amount to contracts if needed
            if size_in_contracts:
                contract_size = size
            else:
                contract_size = self.coins_to_contracts(inst_id, size)

            # Use correct position side based on account mode
            if self._position_mode == "net_mode":
                pos_side = "net"
            else:
                pos_side = "long" if side.lower() == "buy" else "short"

            # For market orders, price should be 0 (SDK requires the param)
            order_price = price if price and order_type == "limit" else 0

            logger.info(f"Placing order: {side} {contract_size} contracts {inst_id} (mode={self._position_mode}, pos_side={pos_side})")

            result = self._client.trading.place_order(
                inst_id=inst_id,
                margin_mode="cross",
                position_side=pos_side,
                side=side.lower(),
                order_type=order_type,
                price=order_price,
                size=contract_size,
            )
            logger.info(f"Order result: {result}")

            # Check for API error
            code = result.get("code", "")
            if code and code != "0":
                msg = result.get("msg", "Unknown error")
                if code == "152404":
                    msg = "Operation not supported — API key may be READ-ONLY. Create a new key with Trade permission."
                return {"success": False, "error": msg, "code": code, "raw": result}

            order_id = ""
            if result and "data" in result and result["data"]:
                data = result["data"][0] if isinstance(result["data"], list) else result["data"]
                order_id = data.get("orderId", "") or data.get("orderID", "")

            # Place TP/SL via tpsl order if provided
            close_pos_side = pos_side  # same pos side for closing in net mode
            if self._position_mode != "net_mode":
                close_pos_side = "long" if side.lower() == "buy" else "short"

            if (stop_loss or take_profit) and order_id:
                try:
                    kwargs = {
                        "inst_id": inst_id,
                        "margin_mode": "cross",
                        "position_side": close_pos_side,
                        "size": contract_size,
                    }
                    if stop_loss:
                        kwargs["stop_loss_trigger_price"] = stop_loss
                        kwargs["stop_loss_order_price"] = -1  # market
                    if take_profit:
                        kwargs["take_profit_trigger_price"] = take_profit
                        kwargs["take_profit_order_price"] = -1  # market
                    self._client.trading.place_tpsl_order(**kwargs)
                except Exception as tpsl_err:
                    logger.warning(f"Failed to place TP/SL: {tpsl_err}")

            return {"success": True, "order_id": order_id, "contracts": contract_size, "raw": result}
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return {"success": False, "error": str(e)}

    def close_position(self, inst_id: str) -> dict:
        """Close a specific position."""
        self._ensure_client()
        try:
            results = []
            if self._position_mode == "net_mode":
                sides = ["net"]
            else:
                sides = ["long", "short"]
            for pos_side in sides:
                try:
                    r = self._client.trading.close_positions(
                        inst_id=inst_id,
                        margin_mode="cross",
                        position_side=pos_side,
                    )
                    results.append(r)
                except Exception:
                    pass
            logger.info(f"Position closed: {inst_id}")
            return {"success": True, "raw": results}
        except Exception as e:
            logger.error(f"Failed to close position {inst_id}: {e}")
            return {"success": False, "error": str(e)}

    def close_all(self) -> list:
        """Close all open positions."""
        positions = self.get_positions()
        results = []
        for pos in positions:
            r = self.close_position(pos["coin"])
            results.append({"coin": pos["coin"], **r})
        return results

    def get_order_history(self, inst_id: str = None, limit: int = 20) -> list:
        """Get order history."""
        self._ensure_client()
        try:
            kwargs = {}
            if inst_id:
                kwargs["inst_id"] = inst_id
            if limit:
                kwargs["limit"] = limit
            result = self._client.trading.get_order_history(**kwargs)
            if result and "data" in result:
                return result["data"]
            return []
        except Exception as e:
            logger.error(f"Failed to get order history: {e}")
            return []

    def cancel_order(self, inst_id: str, order_id: str) -> dict:
        """Cancel a pending order."""
        self._ensure_client()
        try:
            result = self._client.trading.cancel_order(inst_id=inst_id, order_id=order_id)
            return {"success": True, "raw": result}
        except Exception as e:
            logger.error(f"Failed to cancel order: {e}")
            return {"success": False, "error": str(e)}
