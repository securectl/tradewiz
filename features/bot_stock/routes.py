"""
Stock Bot API routes — extracted from app.py.
All routes starting with /api/stock-bot/
"""

import json
import logging
from flask import Blueprint, jsonify, request

from shared.helpers import _uid, P, _upsert_bot_config, _upsert_api_key, _pnl_summary
from decorators import login_required, trader_required
from db import query, query_one, execute, IS_POSTGRES
from stock_bot.stock_engine import get_stock_bot
from stock_bot.broker_client import STOCK_MAP, DEFAULT_STOCKS, is_market_open, get_stock_info, validate_ticker

logger = logging.getLogger(__name__)
bp = Blueprint("bot_stock", __name__)


@bp.route("/api/stock-bot/status")
@trader_required
def api_stock_bot_status():
    bot = get_stock_bot(_uid())
    return jsonify(bot.get_status())


@bp.route("/api/stock-bot/start", methods=["POST"])
@trader_required
def api_stock_bot_start():
    uid = _uid()
    # Determine which broker this user selected
    row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'stock_broker'", (uid,))
    broker = row["value"] if row else "alpaca"
    # Require per-user API keys before starting
    has_keys = query_one(
        f"SELECT 1 FROM user_api_keys WHERE user_id = {P} AND provider = {P} LIMIT 1",
        (uid, broker),
    )
    if not has_keys:
        label = broker.title()
        return jsonify({"ok": False, "error": f"Please configure your {label} API keys in Broker Settings before starting the bot."}), 400
    bot = get_stock_bot(uid)
    if bot.is_running:
        return jsonify({"ok": False, "error": "Stock bot is already running"}), 400
    bot.start()
    return jsonify({"ok": True, "message": "Stock bot started"})


@bp.route("/api/stock-bot/stop", methods=["POST"])
@trader_required
def api_stock_bot_stop():
    bot = get_stock_bot(_uid())
    bot.stop()
    return jsonify({"ok": True, "message": "Stock bot stopped"})


@bp.route("/api/stock-bot/kill", methods=["POST"])
@trader_required
def api_stock_bot_kill():
    bot = get_stock_bot(_uid())
    bot.kill()
    return jsonify({"ok": True, "message": "Kill switch activated — stock bot stopped, positions closed"})


@bp.route("/api/stock-bot/trades")
@trader_required
def api_stock_bot_trades():
    uid = _uid()
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))

    clauses = [f"user_id = {P}", "asset_type = 'stock'"]
    params = [uid]
    if status:
        clauses.append(f"status = {P}")
        params.append(status)

    where = " WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = query(f"SELECT * FROM bot_trades{where} ORDER BY opened_at DESC LIMIT {P}", params)

    return jsonify([{
        "id": r["id"], "coin": r["coin"], "side": r["side"],
        "size": r["size"], "entry_price": r["entry_price"],
        "exit_price": r["exit_price"], "pnl": r["pnl"],
        "pnl_pct": r["pnl_pct"], "status": r["status"],
        "signal_reason": r["signal_reason"], "strategy": r["strategy"],
        "asset_type": r["asset_type"], "direction_bias": r["direction_bias"],
        "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
        "opened_at": r["opened_at"], "closed_at": r["closed_at"],
        "blofin_order_id": r["blofin_order_id"],
    } for r in rows])


@bp.route("/api/stock-bot/trades/<int:trade_id>")
@trader_required
def api_stock_bot_trade_detail(trade_id):
    uid = _uid()
    r = query_one(f"SELECT * FROM bot_trades WHERE id = {P} AND user_id = {P} AND asset_type = 'stock'", (trade_id, uid))
    if not r:
        return jsonify({"error": "Trade not found"}), 404
    return jsonify({
        "id": r["id"], "coin": r["coin"], "side": r["side"],
        "size": r["size"], "entry_price": r["entry_price"],
        "exit_price": r["exit_price"], "pnl": r["pnl"],
        "pnl_pct": r["pnl_pct"], "status": r["status"],
        "signal_reason": r["signal_reason"], "strategy": r["strategy"],
        "asset_type": r["asset_type"], "direction_bias": r["direction_bias"],
        "validation_result": json.loads(r["validation_result"]) if r["validation_result"] else None,
        "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
        "opened_at": r["opened_at"], "closed_at": r["closed_at"],
    })


@bp.route("/api/stock-bot/trades/<int:trade_id>/close", methods=["POST"])
@trader_required
def api_stock_bot_close_trade(trade_id):
    uid = _uid()
    r = query_one(
        f"SELECT * FROM bot_trades WHERE id = {P} AND user_id = {P} AND asset_type = 'stock' AND status = 'open'",
        (trade_id, uid),
    )
    if not r:
        return jsonify({"ok": False, "error": "Open stock trade not found"}), 404
    bot = get_stock_bot(_uid())
    current_price = bot.client.get_ticker_price(r["coin"])
    if current_price <= 0:
        return jsonify({"ok": False, "error": "Could not get current price"}), 500
    bot._close_trade(r, current_price, "Manual close")
    return jsonify({"ok": True, "message": f"Trade {trade_id} closed at ${current_price:,.2f}"})


@bp.route("/api/stock-bot/pnl")
@trader_required
def api_stock_bot_pnl():
    uid = _uid()
    days = int(request.args.get("days", 30))
    rows = query(f"SELECT * FROM stock_daily_pnl WHERE user_id = {P} ORDER BY date DESC LIMIT {P}", (uid, days))
    pnl_data = [{
        "date": r["date"], "total_pnl": r["total_pnl"],
        "trade_count": r["trade_count"], "win_count": r["win_count"], "loss_count": r["loss_count"],
    } for r in rows]
    pnl_data.reverse()
    return jsonify(pnl_data)


@bp.route("/api/stock-bot/pnl-summary")
@trader_required
def api_stock_bot_pnl_summary():
    return jsonify(_pnl_summary("stock"))


@bp.route("/api/stock-bot/config", methods=["GET", "POST"])
@trader_required
def api_stock_bot_config():
    uid = _uid()
    if request.method == "GET":
        rows = query(f"SELECT key, value FROM bot_config WHERE user_id = {P} AND key LIKE 'stock_%%'", (uid,))
        return jsonify({r["key"]: r["value"] for r in rows})

    data = request.get_json()
    allowed_keys = {
        "stock_max_position_pct", "stock_daily_loss_limit", "stock_max_open_positions",
        "stock_scan_interval_sec", "stock_daily_goal", "stock_direction_bias",
        "stock_extended_hours", "stock_trade_mode", "stock_quick_trade_mode",
    }
    for k, v in data.items():
        if k in allowed_keys:
            _upsert_bot_config(uid, k, str(v))
    return jsonify({"ok": True})


@bp.route("/api/stock-bot/stocks", methods=["GET", "POST"])
@trader_required
def api_stock_bot_stocks():
    uid = _uid()
    if request.method == "GET":
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'stock_selected_stocks'", (uid,))
        selected = json.loads(row["value"]) if row else list(DEFAULT_STOCKS)
        custom_row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'stock_custom_tickers'", (uid,))
        custom_tickers = json.loads(custom_row["value"]) if custom_row else {}
        stocks = []
        seen = set()
        for key, info in STOCK_MAP.items():
            stocks.append({"key": key, "name": info["name"], "selected": key in selected})
            seen.add(key)
        for sym, name in custom_tickers.items():
            if sym not in seen:
                stocks.append({"key": sym, "name": name, "selected": sym in selected, "custom": True})
                seen.add(sym)
        for sym in selected:
            if sym not in seen:
                stocks.append({"key": sym, "name": sym, "selected": True, "custom": True})
        return jsonify({"stocks": stocks, "selected": selected})

    data = request.get_json()
    selected = data.get("selected", [])
    valid = [s.upper().strip() for s in selected if s.strip()]
    if not valid:
        return jsonify({"error": "At least one stock must be selected"}), 400
    _upsert_bot_config(uid, "stock_selected_stocks", json.dumps(valid))
    return jsonify({"ok": True, "selected": valid})


@bp.route("/api/stock-bot/stocks/add", methods=["POST"])
@trader_required
def api_stock_bot_stocks_add():
    uid = _uid()
    data = request.get_json()
    symbol = (data.get("symbol") or "").upper().strip()
    if not symbol:
        return jsonify({"error": "Symbol is required"}), 400
    if len(symbol) > 10:
        return jsonify({"error": "Invalid symbol"}), 400
    if symbol in STOCK_MAP:
        return jsonify({"valid": True, "symbol": symbol, "name": STOCK_MAP[symbol]["name"], "already_known": True})
    result = validate_ticker(symbol)
    if not result["valid"]:
        return jsonify(result), 400

    row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'stock_custom_tickers'", (uid,))
    custom = json.loads(row["value"]) if row else {}
    custom[symbol] = result["name"]
    _upsert_bot_config(uid, "stock_custom_tickers", json.dumps(custom))
    return jsonify({"valid": True, "symbol": symbol, "name": result["name"], "price": result.get("price", 0)})


@bp.route("/api/stock-bot/log")
@trader_required
def api_stock_bot_log():
    uid = _uid()
    level = request.args.get("level")
    limit = int(request.args.get("limit", 100))

    clauses = [f"user_id = {P}", "source = 'stock'"]
    params = [uid]
    if level:
        clauses.append(f"level = {P}")
        params.append(level)

    where = " WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = query(f"SELECT * FROM bot_log{where} ORDER BY created_at DESC LIMIT {P}", params)

    return jsonify([{
        "id": r["id"], "level": r["level"], "message": r["message"],
        "details": r["details"], "created_at": r["created_at"],
    } for r in rows])


@bp.route("/api/stock-bot/top-movers")
@trader_required
def api_stock_bot_top_movers():
    """Top stock movers by volume and price change."""
    uid = _uid()
    try:
        import yfinance as yf

        # Broad scan list — popular + bot defaults
        scan_list = [
            "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "AMD",
            "PLTR", "COIN", "SOFI", "MARA", "RIOT", "SPY", "QQQ",
            "SMCI", "ARM", "MU", "AVGO", "CRM", "NFLX", "ORCL", "UBER",
            "SNAP", "SQ", "SHOP", "RBLX", "DKNG", "RIVN", "LCID",
        ]

        # User's selected stocks
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'stock_selected_stocks'", (uid,))
        selected = set(json.loads(row["value"])) if row else set(DEFAULT_STOCKS)

        stocks = []
        for symbol in scan_list:
            try:
                t = yf.Ticker(symbol)
                hist = t.history(period="2d")
                if hist.empty or len(hist) < 2:
                    continue
                price = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                vol = float(hist["Volume"].iloc[-1])
                chg = round(((price - prev) / prev) * 100, 2) if prev > 0 else 0
                stocks.append({
                    "symbol": symbol,
                    "name": get_stock_info(symbol).get("name", symbol),
                    "price": round(price, 2),
                    "chg_pct": chg,
                    "volume": round(vol, 0),
                    "vol_usd": round(vol * price, 0),
                    "selected": symbol in selected,
                })
            except Exception:
                continue

        # Sort by absolute change (biggest movers first)
        stocks.sort(key=lambda x: abs(x["chg_pct"]), reverse=True)
        return jsonify({"stocks": stocks[:15]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/stock-bot/broker", methods=["GET", "POST"])
@trader_required
def api_stock_bot_broker():
    uid = _uid()
    if request.method == "GET":
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'stock_broker'", (uid,))
        broker = row["value"] if row else "alpaca"

        keys_status = {}
        if broker == "webull":
            # app_key and app_secret are required; account_id is optional (auto-discovered)
            for kn in ["app_key", "app_secret"]:
                kr = query_one(
                    f"SELECT encrypted_value FROM user_api_keys WHERE user_id = {P} AND provider = 'webull' AND key_name = {P}",
                    (uid, kn),
                )
                keys_status[f"has_{kn}"] = kr is not None
            # Check if account_id is saved (optional — will be auto-discovered if missing)
            kr = query_one(
                f"SELECT encrypted_value FROM user_api_keys WHERE user_id = {P} AND provider = 'webull' AND key_name = 'account_id'",
                (uid,),
            )
            keys_status["has_account_id"] = kr is not None
        else:
            for kn in ["api_key", "secret_key"]:
                kr = query_one(
                    f"SELECT encrypted_value FROM user_api_keys WHERE user_id = {P} AND provider = 'alpaca' AND key_name = {P}",
                    (uid, kn),
                )
                keys_status[f"has_{kn}"] = kr is not None

        # For Webull, configured = has app_key + app_secret (account_id is auto-discovered)
        if broker == "webull":
            configured = keys_status.get("has_app_key", False) and keys_status.get("has_app_secret", False)
        else:
            configured = all(keys_status.values())
        return jsonify({"broker": broker, **keys_status, "configured": configured})

    data = request.get_json()
    broker = data.get("broker", "alpaca")
    _upsert_bot_config(uid, "stock_broker", broker)

    from crypto_utils import encrypt
    api_key = data.get("api_key", "").strip()
    api_secret = data.get("api_secret", "").strip()
    account_id = data.get("account_id", "").strip()

    if broker == "webull":
        if api_key:
            _upsert_api_key(uid, "webull", "app_key", encrypt(api_key))
        if api_secret:
            _upsert_api_key(uid, "webull", "app_secret", encrypt(api_secret))
        if account_id:
            _upsert_api_key(uid, "webull", "account_id", encrypt(account_id))
    else:
        if api_key:
            _upsert_api_key(uid, "alpaca", "api_key", encrypt(api_key))
        if api_secret:
            _upsert_api_key(uid, "alpaca", "secret_key", encrypt(api_secret))

    # Force the cached bot instance to pick up the new broker + keys
    try:
        bot = get_stock_bot(uid)
        if bot.is_running:
            return jsonify({"ok": True, "broker": broker, "warning": "Bot is running. Stop it first to apply broker change."})
        bot.switch_broker(broker)
    except Exception as e:
        logger.warning(f"Broker switch for user {uid}: {e}")
        # Force-reset the cached instance so next access rebuilds with new keys
        from stock_bot.stock_engine import _stock_bot_instances, _stock_bot_lock
        with _stock_bot_lock:
            _stock_bot_instances.pop(uid, None)

    return jsonify({"ok": True, "broker": broker})


@bp.route("/api/stock-bot/test-connection", methods=["POST"])
@trader_required
def api_stock_bot_test_connection():
    uid = _uid()
    try:
        # Force rebuild so test always uses freshest keys
        from stock_bot.stock_engine import _stock_bot_instances, _stock_bot_lock
        with _stock_bot_lock:
            _stock_bot_instances.pop(uid, None)
        bot = get_stock_bot(uid)
        balance = bot.client.get_balance()
        if "error" in balance:
            return jsonify({"ok": False, "error": balance["error"]})
        broker_label = bot.broker_name.capitalize()

        # If Webull and account was auto-discovered, save it back to DB and return it
        extra = {}
        if bot.broker_name == "webull" and hasattr(bot.client, '_account_id') and bot.client._account_id:
            extra["account_id"] = bot.client._account_id
            # Persist auto-discovered account_id so we don't re-discover every time
            try:
                from crypto_utils import encrypt
                _upsert_api_key(uid, "webull", "account_id", encrypt(bot.client._account_id))
            except Exception:
                pass

        return jsonify({
            "ok": True,
            "balance": balance.get("total_equity", 0),
            "message": f"Connected to {broker_label}! Balance: ${balance.get('total_equity', 0):,.2f}",
            **extra,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@bp.route("/api/stock-bot/webull-accounts", methods=["POST"])
@trader_required
def api_stock_bot_webull_accounts():
    """Fetch all Webull accounts linked to the user's API app."""
    uid = _uid()
    try:
        from stock_bot.stock_engine import _load_user_keys
        from stock_bot.broker_client import WebullClient
        keys = _load_user_keys(uid, "webull")
        if not keys.get("app_key") or not keys.get("app_secret"):
            return jsonify({"ok": False, "error": "Save your Webull App Key and App Secret first, then fetch accounts."}), 400

        client = WebullClient(
            app_key=keys.get("app_key"),
            app_secret=keys.get("app_secret"),
        )
        # Force init without account_id requirement
        client._app_key = keys.get("app_key", "")
        client._app_secret = keys.get("app_secret", "")

        # Fetch accounts via /app/subscriptions/list
        result = client._request_raw("GET", "/app/subscriptions/list")
        if not result["ok"]:
            return jsonify({"ok": False, "error": f"Failed to fetch accounts: {result.get('error', 'Unknown')}"}), 400

        data = result["data"]
        items = data if isinstance(data, list) else [data]

        accounts = []
        for item in items:
            account_id = str(item.get("account_id", ""))
            if not account_id:
                continue

            # Try to get account profile for type info
            profile = {}
            try:
                profile_result = client._request_raw(
                    "GET", "/account/profile",
                    params={"account_id": account_id},
                )
                if profile_result["ok"]:
                    profile = profile_result["data"]
            except Exception:
                pass

            account_type = profile.get("account_type", item.get("account_type", "unknown"))
            account_number = profile.get("account_number", item.get("account_number", ""))
            account_status = profile.get("account_status", "")

            # Determine if paper/demo
            is_paper = "paper" in str(account_type).lower() or "demo" in str(account_type).lower() or "simulation" in str(account_type).lower()

            accounts.append({
                "account_id": account_id,
                "account_number": account_number,
                "account_type": account_type,
                "account_status": account_status,
                "is_paper": is_paper,
                "label": f"{account_type} — {account_number}" if account_number else f"{account_type} ({account_id[:8]}...)",
            })

        if not accounts:
            return jsonify({"ok": False, "error": "No accounts found. Make sure your Webull account is linked at developer.webull.com"}), 400

        return jsonify({"ok": True, "accounts": accounts})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/api/stock-bot/test-trade", methods=["POST"])
@trader_required
def api_stock_bot_test_trade():
    """Manual test trade — buy 1 share of a stock to verify broker connection works end-to-end."""
    uid = _uid()
    data = request.get_json()
    symbol = (data.get("symbol") or "AAPL").upper().strip()
    side = data.get("side", "buy").lower()
    qty = int(data.get("qty", 1))
    extended = data.get("extended_hours", False)

    if qty < 1 or qty > 10:
        return jsonify({"ok": False, "error": "Test trade qty must be 1-10 shares"}), 400
    if side not in ("buy", "sell"):
        return jsonify({"ok": False, "error": "Side must be 'buy' or 'sell'"}), 400

    try:
        bot = get_stock_bot(uid)
        if not bot.client.is_configured():
            return jsonify({"ok": False, "error": "Broker not configured. Save API keys first."}), 400

        # Check market hours (unless extended hours enabled)
        from stock_bot.broker_client import is_market_open
        market = is_market_open(extended_hours=extended)
        if not market["is_open"]:
            return jsonify({"ok": False, "error": f"Market is {market['status']}: {market['reason']}. Enable extended hours to trade outside regular hours."}), 400

        # Get current price
        price = bot.client.get_ticker_price(symbol)
        if price <= 0:
            return jsonify({"ok": False, "error": f"Could not get price for {symbol}"}), 400

        # Place order
        result = bot.client.place_order(symbol=symbol, side=side, qty=qty)
        if result.get("success"):
            # Record in bot_trades for tracking
            from datetime import datetime
            execute(
                f"INSERT INTO bot_trades (user_id, coin, side, size, entry_price, status, signal_reason, "
                f"strategy, asset_type, direction_bias) "
                f"VALUES ({P}, {P}, {P}, {P}, {P}, 'open', {P}, 'manual_test', 'stock', 'manual')",
                (uid, symbol, side, qty, price, f"Manual test trade: {side} {qty}x {symbol} @ ${price:,.2f}"),
            )
            return jsonify({
                "ok": True,
                "message": f"Test trade placed: {side.upper()} {qty}x {symbol} @ ${price:,.2f}",
                "order_id": result.get("order_id"),
                "price": price,
            })
        else:
            return jsonify({"ok": False, "error": result.get("error", "Order failed")}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/api/stock-bot/market-hours")
@trader_required
def api_stock_bot_market_hours():
    return jsonify(is_market_open())
