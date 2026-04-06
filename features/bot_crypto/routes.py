"""
Crypto Bot API routes — extracted from app.py.
All routes starting with /api/bot/
"""

import json
import logging
from flask import Blueprint, jsonify, request

from shared.helpers import NumpyEncoder, _uid, P, _upsert_bot_config, _upsert_api_key, _pnl_summary
from decorators import login_required, trader_required
from db import query, query_one, execute, IS_POSTGRES
from crypto_bot.bot_engine import get_bot
from crypto_bot.blofin_client import COIN_MAP, DEFAULT_COINS
from crypto_bot.crypto_validator import get_trending_crypto

logger = logging.getLogger(__name__)
bp = Blueprint("bot_crypto", __name__)


@bp.route("/api/bot/status")
@trader_required
def api_bot_status():
    bot = get_bot(_uid())
    return jsonify(bot.get_status())


@bp.route("/api/bot/start", methods=["POST"])
@trader_required
def api_bot_start():
    uid = _uid()
    # Require API keys — check DB first, then fall back to env vars
    has_keys = query_one(
        f"SELECT 1 FROM user_api_keys WHERE user_id = {P} AND provider = 'blofin' LIMIT 1",
        (uid,),
    )
    if not has_keys:
        import os
        env_key = os.getenv("BLOFIN_API_KEY", "")
        env_secret = os.getenv("BLOFIN_API_SECRET", "")
        env_pass = os.getenv("BLOFIN_PASSPHRASE", "")
        if env_key and env_secret and env_pass and env_key != "your_blofin_api_key":
            # Migrate env vars to DB for this user
            from crypto_utils import encrypt
            for kn, val in [("api_key", env_key), ("api_secret", env_secret), ("passphrase", env_pass)]:
                _upsert_api_key(uid, "blofin", kn, encrypt(val))
        else:
            return jsonify({"ok": False, "error": "Please configure your BloFin API keys in Platform Settings before starting the bot."}), 400
    bot = get_bot(uid)
    if bot.is_running:
        return jsonify({"ok": False, "error": "Bot is already running"}), 400
    bot.start()
    return jsonify({"ok": True, "message": "Bot started"})


@bp.route("/api/bot/stop", methods=["POST"])
@trader_required
def api_bot_stop():
    bot = get_bot(_uid())
    bot.stop()
    return jsonify({"ok": True, "message": "Bot stopped"})


@bp.route("/api/bot/kill", methods=["POST"])
@trader_required
def api_bot_kill():
    bot = get_bot(_uid())
    bot.kill()
    return jsonify({"ok": True, "message": "Kill switch activated — bot stopped, positions closed"})


@bp.route("/api/bot/trades")
@trader_required
def api_bot_trades():
    uid = _uid()
    status = request.args.get("status")
    asset_type = request.args.get("asset_type")
    limit = int(request.args.get("limit", 50))

    clauses = [f"user_id = {P}"]
    params = [uid]
    if status:
        clauses.append(f"status = {P}")
        params.append(status)
    if asset_type:
        clauses.append(f"asset_type = {P}")
        params.append(asset_type)

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


@bp.route("/api/bot/trades/<int:trade_id>")
@trader_required
def api_bot_trade_detail(trade_id):
    uid = _uid()
    r = query_one(f"SELECT * FROM bot_trades WHERE id = {P} AND user_id = {P}", (trade_id, uid))
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
        "blofin_order_id": r["blofin_order_id"],
    })


@bp.route("/api/bot/trades/<int:trade_id>/close", methods=["POST"])
@trader_required
def api_bot_close_trade(trade_id):
    uid = _uid()
    r = query_one(f"SELECT * FROM bot_trades WHERE id = {P} AND user_id = {P} AND status = 'open'", (trade_id, uid))
    if not r:
        return jsonify({"ok": False, "error": "Open trade not found"}), 404
    bot = get_bot(_uid())
    coin_info = COIN_MAP.get(r["coin"])
    current_price = bot.client.get_ticker_price(coin_info["blofin"]) if coin_info else 0
    if current_price <= 0:
        return jsonify({"ok": False, "error": "Could not get current price"}), 500
    bot._close_trade(r, current_price, "Manual close")
    return jsonify({"ok": True, "message": f"Trade {trade_id} closed at ${current_price:,.2f}"})


@bp.route("/api/bot/dashboard")
@trader_required
def api_bot_dashboard():
    """Comprehensive dashboard: P&L, win rate, strategies, top assets, streaks, daily goal.
    Pass ?asset=crypto or ?asset=stock to filter by asset type. Default: all."""
    uid = _uid()
    from datetime import datetime, timedelta

    asset_filter = request.args.get("asset", "all")

    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
    ytd_start = f"{now.year}-01-01 00:00:00"

    # Asset type filter clause
    if asset_filter in ("crypto", "stock"):
        asset_clause = f" AND asset_type={P}"
        asset_param = (asset_filter,)
    else:
        asset_clause = ""
        asset_param = ()

    # ── Period summaries (with fees) ──
    base = (
        f"SELECT COALESCE(SUM(pnl),0) as total, COUNT(*) as trades, "
        f"SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins, "
        f"COALESCE(SUM(fee),0) as fees "
        f"FROM bot_trades WHERE status='closed' AND user_id={P}{asset_clause}"
    )

    def _q(extra, params):
        row = query_one(f"{base} {extra}", (uid,) + asset_param + params)
        return {
            "pnl": round(float(row["total"]), 2),
            "trades": int(row["trades"]),
            "wins": int(row["wins"] or 0),
            "fees": round(float(row["fees"] or 0), 2),
        }

    summary = {
        "all": _q("", ()),
        "day": _q(f"AND DATE(closed_at)={P}", (today,)),
        "week": _q(f"AND closed_at>={P}", (week_ago,)),
        "month": _q(f"AND closed_at>={P}", (month_ago,)),
        "year": _q(f"AND closed_at>={P}", (year_ago,)),
        "ytd": _q(f"AND closed_at>={P}", (ytd_start,)),
    }

    total_trades = summary["all"]["trades"]
    total_wins = summary["all"]["wins"]
    win_rate = round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0
    avg_pnl = round(summary["all"]["pnl"] / total_trades, 2) if total_trades > 0 else 0
    total_fees = summary["all"]["fees"]
    net_pnl = round(summary["all"]["pnl"] - total_fees, 2)

    # ── Streak ──
    streak_rows = query(
        f"SELECT pnl FROM bot_trades WHERE status='closed' AND user_id={P}{asset_clause} ORDER BY closed_at DESC LIMIT 50",
        (uid,) + asset_param,
    )
    streak = {"type": "none", "count": 0}
    if streak_rows:
        first_pnl = float(streak_rows[0]["pnl"] or 0)
        streak_type = "win" if first_pnl > 0 else "loss"
        count = 0
        for r in streak_rows:
            p = float(r["pnl"] or 0)
            if (streak_type == "win" and p > 0) or (streak_type == "loss" and p <= 0):
                count += 1
            else:
                break
        streak = {"type": streak_type, "count": count}

    # ── Daily goal ──
    goal_key = "stock_daily_goal" if asset_filter == "stock" else "daily_goal"
    goal_row = query_one(f"SELECT value FROM bot_config WHERE user_id={P} AND key={P}", (uid, goal_key))
    daily_target = float(goal_row["value"]) if goal_row else 500
    daily_actual = summary["day"]["pnl"]
    daily_pct = round(daily_actual / daily_target * 100, 1) if daily_target > 0 else 0

    # ── Strategy breakdown ──
    strat_rows = query(
        f"SELECT strategy, COUNT(*) as trades, "
        f"SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins, "
        f"COALESCE(SUM(pnl),0) as total_pnl, "
        f"COALESCE(SUM(fee),0) as total_fees, "
        f"COALESCE(AVG(pnl),0) as avg_pnl "
        f"FROM bot_trades WHERE status='closed' AND user_id={P}{asset_clause} "
        f"GROUP BY strategy ORDER BY total_pnl DESC",
        (uid,) + asset_param,
    )
    strategies = [{
        "name": r["strategy"] or "unknown",
        "trades": int(r["trades"]),
        "win_rate": round(int(r["wins"] or 0) / int(r["trades"]) * 100, 1) if int(r["trades"]) > 0 else 0,
        "total_pnl": round(float(r["total_pnl"]), 2),
        "total_fees": round(float(r["total_fees"] or 0), 2),
        "avg_pnl": round(float(r["avg_pnl"]), 2),
    } for r in strat_rows]

    # ── Top assets ──
    asset_rows = query(
        f"SELECT coin, asset_type, COUNT(*) as trades, "
        f"SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins, "
        f"COALESCE(SUM(pnl),0) as total_pnl, "
        f"COALESCE(SUM(fee),0) as total_fees "
        f"FROM bot_trades WHERE status='closed' AND user_id={P}{asset_clause} "
        f"GROUP BY coin, asset_type ORDER BY trades DESC LIMIT 10",
        (uid,) + asset_param,
    )
    top_assets = [{
        "coin": r["coin"],
        "asset_type": r["asset_type"] or "crypto",
        "trades": int(r["trades"]),
        "win_rate": round(int(r["wins"] or 0) / int(r["trades"]) * 100, 1) if int(r["trades"]) > 0 else 0,
        "total_pnl": round(float(r["total_pnl"]), 2),
        "total_fees": round(float(r["total_fees"] or 0), 2),
    } for r in asset_rows]

    # ── By asset type (crypto vs stock) ──
    by_asset = {}
    for at in ["crypto", "stock"]:
        at_row = query_one(
            f"SELECT COUNT(*) as trades, "
            f"SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins, "
            f"COALESCE(SUM(pnl),0) as total_pnl, "
            f"COALESCE(SUM(fee),0) as total_fees "
            f"FROM bot_trades WHERE status='closed' AND user_id={P} AND asset_type={P}",
            (uid, at),
        )
        if at_row and int(at_row["trades"]) > 0:
            by_asset[at] = {
                "trades": int(at_row["trades"]),
                "win_rate": round(int(at_row["wins"] or 0) / int(at_row["trades"]) * 100, 1),
                "total_pnl": round(float(at_row["total_pnl"]), 2),
                "total_fees": round(float(at_row["total_fees"] or 0), 2),
            }

    # ── Best / Worst trade ──
    best = query_one(
        f"SELECT coin, side, pnl, strategy FROM bot_trades WHERE status='closed' AND user_id={P}{asset_clause} ORDER BY pnl DESC LIMIT 1",
        (uid,) + asset_param,
    )
    worst = query_one(
        f"SELECT coin, side, pnl, strategy FROM bot_trades WHERE status='closed' AND user_id={P}{asset_clause} ORDER BY pnl ASC LIMIT 1",
        (uid,) + asset_param,
    )

    def _trade_dict(r):
        if not r:
            return None
        return {"coin": r["coin"], "side": r["side"], "pnl": round(float(r["pnl"] or 0), 2), "strategy": r["strategy"]}

    return jsonify({
        "win_rate": win_rate,
        "total_trades": total_trades,
        "avg_pnl": avg_pnl,
        "total_fees": total_fees,
        "net_pnl": net_pnl,
        "streak": streak,
        "daily_goal": {"actual": daily_actual, "target": daily_target, "pct": daily_pct},
        "summary": summary,
        "strategies": strategies,
        "top_assets": top_assets,
        "by_asset": by_asset,
        "best_trade": _trade_dict(best),
        "worst_trade": _trade_dict(worst),
    })


@bp.route("/api/bot/pnl")
@trader_required
def api_bot_pnl():
    uid = _uid()
    days = int(request.args.get("days", 30))
    rows = query(f"SELECT * FROM bot_daily_pnl WHERE user_id = {P} ORDER BY date DESC LIMIT {P}", (uid, days))
    pnl_data = [{
        "date": r["date"], "total_pnl": r["total_pnl"],
        "trade_count": r["trade_count"], "win_count": r["win_count"], "loss_count": r["loss_count"],
    } for r in rows]
    pnl_data.reverse()
    return jsonify(pnl_data)


@bp.route("/api/bot/pnl-summary")
@trader_required
def api_bot_pnl_summary():
    return jsonify(_pnl_summary("crypto"))


@bp.route("/api/bot/tax-estimate")
@trader_required
def api_bot_tax_estimate():
    """Estimated short-term capital gains tax liability from bot trades (both crypto + stock).
    All bot trades are short-term (held < 1 year). Uses 2025 US federal brackets + 3.8% NIIT."""
    uid = _uid()
    from datetime import datetime

    # Get all closed trades this calendar year with realized P&L
    year_start = f"{datetime.now().year}-01-01"
    rows = query(
        f"SELECT coin, asset_type, side, pnl, fee, entry_price, exit_price, size, strategy, opened_at, closed_at "
        f"FROM bot_trades WHERE user_id = {P} AND status = 'closed' AND closed_at >= {P} "
        f"ORDER BY closed_at",
        (uid, year_start),
    )

    crypto_gains = 0.0
    crypto_losses = 0.0
    crypto_fees = 0.0
    crypto_trades = 0
    stock_gains = 0.0
    stock_losses = 0.0
    stock_fees = 0.0
    stock_trades = 0

    for r in rows:
        pnl = float(r["pnl"] or 0)
        fee = float(r["fee"] or 0)
        asset = r.get("asset_type", "crypto")
        if asset == "stock":
            stock_trades += 1
            stock_fees += fee
            if pnl > 0:
                stock_gains += pnl
            else:
                stock_losses += abs(pnl)
        else:
            crypto_trades += 1
            crypto_fees += fee
            if pnl > 0:
                crypto_gains += pnl
            else:
                crypto_losses += abs(pnl)

    # Net realized P&L (gains offset by losses)
    crypto_net = crypto_gains - crypto_losses
    stock_net = stock_gains - stock_losses
    total_net = crypto_net + stock_net
    total_fees = crypto_fees + stock_fees

    # US short-term capital gains tax estimate (taxed as ordinary income)
    # Using simplified 2025 brackets for single filer
    # Note: this is ON TOP of other income — we estimate at marginal rates
    # Conservative: assume user is in 22-32% bracket range
    # Plus 3.8% Net Investment Income Tax (NIIT) if AGI > $200K
    if total_net <= 0:
        tax_rate = 0.0
        estimated_tax = 0.0
        bracket = "No tax (net loss)"
        niit = 0.0
    else:
        # Estimate based on typical trader income levels
        # $0-$11,925: 10%, $11,926-$48,475: 12%, $48,476-$103,350: 22%
        # $103,351-$197,300: 24%, $197,301-$250,525: 32%, $250,526-$626,350: 35%, $626,351+: 37%
        # Most active traders fall in 22-32% range — use 24% as default estimate
        tax_rate = 0.24
        bracket = "24% (estimated marginal rate)"
        if total_net > 100000:
            tax_rate = 0.32
            bracket = "32% (high gains)"
        elif total_net > 50000:
            tax_rate = 0.24
            bracket = "24%"
        elif total_net > 10000:
            tax_rate = 0.22
            bracket = "22%"
        elif total_net > 1000:
            tax_rate = 0.12
            bracket = "12%"
        else:
            tax_rate = 0.10
            bracket = "10%"

        # NIIT: 3.8% on net investment income if gains are significant
        niit_rate = 0.038 if total_net > 10000 else 0.0
        niit = round(total_net * niit_rate, 2)
        estimated_tax = round(total_net * tax_rate + niit, 2)

    # Capital loss deduction: up to $3,000/year against ordinary income
    loss_carryover = 0.0
    loss_deduction = 0.0
    if total_net < 0:
        loss_deduction = min(abs(total_net), 3000)
        loss_carryover = max(abs(total_net) - 3000, 0)

    return jsonify({
        "year": datetime.now().year,
        "crypto": {
            "trades": crypto_trades,
            "gains": round(crypto_gains, 2),
            "losses": round(crypto_losses, 2),
            "net": round(crypto_net, 2),
            "fees": round(crypto_fees, 2),
        },
        "stock": {
            "trades": stock_trades,
            "gains": round(stock_gains, 2),
            "losses": round(stock_losses, 2),
            "net": round(stock_net, 2),
            "fees": round(stock_fees, 2),
        },
        "total": {
            "trades": crypto_trades + stock_trades,
            "net_gains": round(total_net, 2),
            "total_fees": round(total_fees, 2),
        },
        "tax": {
            "bracket": bracket,
            "rate": tax_rate,
            "niit": niit,
            "estimated_tax": estimated_tax,
            "loss_deduction": loss_deduction,
            "loss_carryover": loss_carryover,
            "note": "Estimate only — short-term capital gains taxed as ordinary income. Consult a tax professional.",
        },
    })


@bp.route("/api/bot/platform", methods=["GET", "POST"])
@trader_required
def api_bot_platform():
    uid = _uid()
    if request.method == "GET":
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'platform'", (uid,))
        platform = row["value"] if row else "blofin"
        # Check user's encrypted keys
        from crypto_utils import decrypt
        keys_status = {}
        for kn in ["api_key", "api_secret", "passphrase"]:
            kr = query_one(
                f"SELECT encrypted_value FROM user_api_keys WHERE user_id = {P} AND provider = 'blofin' AND key_name = {P}",
                (uid, kn),
            )
            keys_status[f"has_{kn}"] = kr is not None
        return jsonify({
            "platform": platform,
            **keys_status,
            "configured": all(keys_status.values()),
        })

    # POST — save platform + keys
    data = request.get_json()
    platform = data.get("platform", "blofin")
    _upsert_bot_config(uid, "platform", platform)

    api_key = data.get("api_key", "").strip()
    api_secret = data.get("api_secret", "").strip()
    passphrase = data.get("passphrase", "").strip()

    from crypto_utils import encrypt
    for kn, val in [("api_key", api_key), ("api_secret", api_secret), ("passphrase", passphrase)]:
        if val:
            _upsert_api_key(uid, "blofin", kn, encrypt(val))

    return jsonify({"ok": True, "platform": platform})


@bp.route("/api/bot/test-connection", methods=["POST"])
@trader_required
def api_bot_test_connection():
    try:
        bot = get_bot(_uid())
        bot.client._initialized = False
        bot.client._client = None
        balance = bot.client.get_balance()
        if "error" in balance:
            return jsonify({"ok": False, "error": balance["error"]})
        return jsonify({
            "ok": True,
            "balance": balance.get("total_equity", 0),
            "message": f"Connected! Balance: ${balance.get('total_equity', 0):,.2f}",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@bp.route("/api/bot/check-permissions", methods=["POST"])
@trader_required
def api_bot_check_permissions():
    try:
        bot = get_bot(_uid())
        result = bot.client.check_trade_permission()
        return jsonify(result)
    except Exception as e:
        return jsonify({"has_trade_permission": False, "message": str(e)})


@bp.route("/api/bot/test-trade", methods=["POST"])
@trader_required
def api_bot_test_trade():
    try:
        bot = get_bot(_uid())
        data = request.get_json() or {}
        coin = data.get("coin", "BTC-USDT")
        side = data.get("side", "buy")
        coin_info = COIN_MAP.get(coin)
        if not coin_info:
            return jsonify({"ok": False, "error": f"Unknown coin: {coin}"})
        inst_id = coin_info["blofin"]
        info = bot.client.get_instrument_info(inst_id)
        min_contracts = info["min_size"]
        result = bot.client.place_order(inst_id=inst_id, side=side, size=min_contracts, size_in_contracts=True)
        if result.get("success"):
            return jsonify({
                "ok": True,
                "message": f"Test trade placed: {side} {min_contracts} contracts {inst_id}",
                "order_id": result.get("order_id", ""),
                "raw": str(result.get("raw", "")),
            })
        else:
            return jsonify({"ok": False, "error": result.get("error", "Unknown error"), "code": result.get("code", "")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@bp.route("/api/bot/coins", methods=["GET", "POST"])
@trader_required
def api_bot_coins():
    uid = _uid()
    if request.method == "GET":
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'selected_coins'", (uid,))
        selected = json.loads(row["value"]) if row else list(DEFAULT_COINS)
        coins = [{"key": k, "name": v["name"], "blofin": v["blofin"], "selected": k in selected} for k, v in COIN_MAP.items()]
        return jsonify({"coins": coins, "selected": selected})

    data = request.get_json()
    selected = data.get("selected", [])
    valid = [c for c in selected if c in COIN_MAP]
    if not valid:
        return jsonify({"error": "At least one valid coin must be selected"}), 400
    _upsert_bot_config(uid, "selected_coins", json.dumps(valid))
    return jsonify({"ok": True, "selected": valid})


@bp.route("/api/bot/config", methods=["GET", "POST"])
@trader_required
def api_bot_config():
    uid = _uid()
    if request.method == "GET":
        rows = query(f"SELECT key, value FROM bot_config WHERE user_id = {P}", (uid,))
        return jsonify({r["key"]: r["value"] for r in rows})

    data = request.get_json()
    allowed_keys = {"max_position_pct", "daily_loss_limit", "max_open_positions", "scan_interval_sec", "daily_goal", "trading_mode", "direction_bias", "trade_mode", "quick_trade_mode"}
    for k, v in data.items():
        if k in allowed_keys:
            _upsert_bot_config(uid, k, str(v))
    return jsonify({"ok": True})


@bp.route("/api/bot/log")
@trader_required
def api_bot_log():
    uid = _uid()
    level = request.args.get("level")
    limit = int(request.args.get("limit", 100))

    if level:
        rows = query(
            f"SELECT * FROM bot_log WHERE user_id = {P} AND level = {P} ORDER BY created_at DESC LIMIT {P}",
            (uid, level, limit),
        )
    else:
        rows = query(
            f"SELECT * FROM bot_log WHERE user_id = {P} ORDER BY created_at DESC LIMIT {P}",
            (uid, limit),
        )
    return jsonify([{
        "id": r["id"], "level": r["level"], "message": r["message"],
        "details": r["details"], "created_at": r["created_at"],
    } for r in rows])


@bp.route("/api/bot/top-volume")
@trader_required
def api_bot_top_volume():
    """Top 10 crypto coins by 24h volume."""
    uid = _uid()
    try:
        import yfinance as yf

        # Scan a broad set of crypto tickers
        scan_list = [
            ("BTC", "BTC-USD", "Bitcoin"), ("ETH", "ETH-USD", "Ethereum"),
            ("SOL", "SOL-USD", "Solana"), ("BNB", "BNB-USD", "BNB"),
            ("XRP", "XRP-USD", "XRP"), ("DOGE", "DOGE-USD", "Dogecoin"),
            ("ADA", "ADA-USD", "Cardano"), ("AVAX", "AVAX-USD", "Avalanche"),
            ("DOT", "DOT-USD", "Polkadot"), ("LINK", "LINK-USD", "Chainlink"),
            ("MATIC", "MATIC-USD", "Polygon"), ("ATOM", "ATOM-USD", "Cosmos"),
            ("UNI", "UNI7083-USD", "Uniswap"), ("LTC", "LTC-USD", "Litecoin"),
            ("NEAR", "NEAR-USD", "NEAR"), ("SUI", "SUI20947-USD", "SUI"),
            ("PEPE", "PEPE24478-USD", "PEPE"), ("SHIB", "SHIB-USD", "Shiba Inu"),
            ("ARB", "ARB11841-USD", "Arbitrum"), ("OP", "OP-USD", "Optimism"),
        ]

        # Get user's selected coins for "Added" badge
        row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'selected_coins'", (uid,))
        selected = json.loads(row["value"]) if row else []
        selected_set = set(s.split("-")[0].upper() for s in selected)

        coins = []
        for symbol, yf_ticker, name in scan_list:
            try:
                t = yf.Ticker(yf_ticker)
                hist = t.history(period="2d")
                if hist.empty or len(hist) < 2:
                    continue
                price = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
                vol = float(hist["Volume"].iloc[-1]) * price  # USD volume
                chg = round(((price - prev) / prev) * 100, 2) if prev > 0 else 0
                coins.append({
                    "coin": name, "symbol": symbol, "ticker": f"{symbol}-USDT",
                    "price": round(price, 4), "chg_24h": chg, "vol_24h": round(vol, 0),
                    "selected": symbol.upper() in selected_set,
                })
            except Exception:
                continue

        # Sort by volume descending, take top 10
        coins.sort(key=lambda x: x["vol_24h"], reverse=True)
        return jsonify({"coins": coins[:10]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/bot/trending")
@trader_required
def api_bot_trending():
    uid = _uid()
    try:
        result = get_trending_crypto()
        added_coins = []
        try:
            market_data = result.get("market_data", [])
            high_vol_tickers = [d["ticker"] for d in market_data if d.get("vol_surge", 0) >= 1.5 and d["ticker"] in COIN_MAP]
            if high_vol_tickers:
                row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = 'selected_coins'", (uid,))
                current_selected = json.loads(row["value"]) if row else list(DEFAULT_COINS)
                for ticker in high_vol_tickers:
                    if ticker not in current_selected:
                        current_selected.append(ticker)
                        added_coins.append(ticker)
                if added_coins:
                    _upsert_bot_config(uid, "selected_coins", json.dumps(current_selected))
        except Exception as e:
            print(f"[WARNING] Failed to auto-add trending coins: {e}")

        result["auto_added_coins"] = added_coins
        result_json = json.dumps(result, cls=NumpyEncoder, default=str)
        from flask import current_app
        return current_app.response_class(response=result_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": f"Trending analysis failed: {str(e)}"}), 500
