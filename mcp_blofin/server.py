"""
MCP Server — Exposes BloFin demo trading tools.
8 tools: get_account_balance, get_positions, get_ticker_price, place_order,
         close_position, get_order_history, cancel_order, get_market_data

Used by bot engine in-process; can later be exposed over stdio/SSE.
"""

import json
import logging
from crypto_bot.blofin_client import BloFinClient, COIN_MAP

logger = logging.getLogger(__name__)

# Shared client instance
_client = None


def _get_client() -> BloFinClient:
    global _client
    if _client is None:
        _client = BloFinClient()
    return _client


# ─── MCP Tool Definitions ────────────────────────────────────

TOOLS = [
    {
        "name": "get_account_balance",
        "description": "Get demo account balance, equity, available margin, and unrealized P&L",
        "parameters": {},
    },
    {
        "name": "get_positions",
        "description": "Get all open positions on the demo account",
        "parameters": {},
    },
    {
        "name": "get_ticker_price",
        "description": "Get current price for a trading pair",
        "parameters": {
            "inst_id": {"type": "string", "description": "Instrument ID, e.g. BTC-USDT", "required": True},
        },
    },
    {
        "name": "place_order",
        "description": "Place a market or limit order on BloFin demo",
        "parameters": {
            "inst_id": {"type": "string", "description": "Instrument ID, e.g. BTC-USDT", "required": True},
            "side": {"type": "string", "description": "buy or sell", "required": True},
            "size": {"type": "number", "description": "Order size in units", "required": True},
            "order_type": {"type": "string", "description": "market or limit", "default": "market"},
            "price": {"type": "number", "description": "Limit price (required for limit orders)"},
            "stop_loss": {"type": "number", "description": "Stop loss price"},
            "take_profit": {"type": "number", "description": "Take profit price"},
        },
    },
    {
        "name": "close_position",
        "description": "Close an open position for a specific instrument",
        "parameters": {
            "inst_id": {"type": "string", "description": "Instrument ID, e.g. BTC-USDT", "required": True},
        },
    },
    {
        "name": "get_order_history",
        "description": "Get recent order history",
        "parameters": {
            "inst_id": {"type": "string", "description": "Filter by instrument ID"},
            "limit": {"type": "integer", "description": "Number of orders to return", "default": 20},
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancel a pending order",
        "parameters": {
            "inst_id": {"type": "string", "description": "Instrument ID", "required": True},
            "order_id": {"type": "string", "description": "Order ID to cancel", "required": True},
        },
    },
    {
        "name": "get_market_data",
        "description": "Get available trading pairs and coin mappings",
        "parameters": {},
    },
]


def call_tool(name: str, params: dict = None) -> dict:
    """Execute an MCP tool by name. Returns result dict."""
    params = params or {}
    client = _get_client()

    try:
        if name == "get_account_balance":
            return client.get_balance()

        elif name == "get_positions":
            return {"positions": client.get_positions()}

        elif name == "get_ticker_price":
            inst_id = params.get("inst_id", "")
            price = client.get_ticker_price(inst_id)
            return {"inst_id": inst_id, "price": price}

        elif name == "place_order":
            return client.place_order(
                inst_id=params["inst_id"],
                side=params["side"],
                size=params["size"],
                order_type=params.get("order_type", "market"),
                price=params.get("price"),
                stop_loss=params.get("stop_loss"),
                take_profit=params.get("take_profit"),
            )

        elif name == "close_position":
            return client.close_position(params["inst_id"])

        elif name == "get_order_history":
            orders = client.get_order_history(
                inst_id=params.get("inst_id"),
                limit=params.get("limit", 20),
            )
            return {"orders": orders}

        elif name == "cancel_order":
            return client.cancel_order(params["inst_id"], params["order_id"])

        elif name == "get_market_data":
            return {
                "coins": COIN_MAP,
                "supported_pairs": list(COIN_MAP.keys()),
            }

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"MCP tool {name} failed: {e}")
        return {"error": str(e)}


def list_tools() -> list:
    """Return all available MCP tool definitions."""
    return TOOLS
