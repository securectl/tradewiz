"""Claude Trading Bot engine — scalp-and-rotate with re-entry watchlist.

Sources candidates from the existing screener (Top Gainers + Oversold + Bottom
Forming with 3+ days persistence). Applies RSI<30 buy / RSI>60 sell logic with
a tight 4-5% take-profit and -2% scalp stop.

Core capability (the new piece): on every exit, the ticker is added to a
re-entry watchlist. On the next scan cycles, if the price stabilizes (last bar
held flat or bounced), the bot re-enters at half size. Compounds scalp wins.

Stocks only. Paper trading via Alpaca (paper=True enforced).
"""

import json
import logging
import threading
import time
from datetime import datetime, timedelta

import numpy as np
import yfinance as yf

from db import IS_POSTGRES, execute, query, query_one
from market_sensor import check_market_health

logger = logging.getLogger(__name__)
P = "%s" if IS_POSTGRES else "?"

ASSET_TYPE = "claude"

# Default config (writable to bot_config table per user)
CB_DEFAULTS = {
    "cb_enabled": "0",
    "cb_kill_switch": "0",
    "cb_broker": "alpaca",              # alpaca | webull
    "cb_mode": "paper",                 # paper | live (live blocked by CLAUDE.md rule #1)
    "cb_scan_interval": "300",          # 5 min
    "cb_max_positions": "5",
    "cb_max_position_pct": "12",        # % of equity per position
    "cb_max_total_exposure_pct": "60",  # cap on sum of open positions (% of equity)
    "cb_daily_loss_limit": "500",
    "cb_min_confidence": "65",
    "cb_take_profit_pct": "5.0",        # +5% lock
    "cb_scalp_stop_pct": "2.0",         # -2% scalp stop (then re-entry)
    "cb_hard_stop_pct": "8.0",          # -8% hard stop, no re-entry
    "cb_reentry_size_factor": "0.5",    # 50% normal size on re-entry
    "cb_reentry_max_attempts": "2",
    "cb_reentry_window_days": "3",
}

_state = {"running": False, "thread": None, "user_id": None}


# ─── Config / logging helpers ───────────────────────────────────────────

def _cfg(user_id, key, default=None):
    # Resolution order: user-specific row → global row (user_id=0, admin-set
    # via /api/admin/bot-defaults) → hardcoded CB_DEFAULTS. Single query;
    # ORDER BY user_id DESC picks the user row when both exist.
    try:
        row = query_one(
            f"SELECT value FROM bot_config WHERE user_id IN ({P}, 0) AND key = {P} "
            f"ORDER BY CASE WHEN user_id = 0 THEN 1 ELSE 0 END LIMIT 1",
            (user_id, key),
        )
        if row:
            return row["value"]
    except Exception:
        pass
    return default if default is not None else CB_DEFAULTS.get(key, "")


def _log(user_id, level, msg):
    try:
        execute(
            f"INSERT INTO bot_log (user_id, level, message, source) VALUES ({P},{P},{P},'claude_bot')",
            (user_id, level, msg[:500]),
        )
    except Exception:
        pass
    getattr(logger, level, logger.info)(f"[CB:{user_id}] {msg}")


# ─── Position / PnL helpers ─────────────────────────────────────────────

def _open_count(user_id):
    try:
        row = query_one(
            f"SELECT COUNT(*) as cnt FROM bot_trades WHERE user_id = {P} AND asset_type = {P} AND status = 'open'",
            (user_id, ASSET_TYPE),
        )
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0


def _has_position(user_id, ticker):
    try:
        row = query_one(
            f"SELECT id FROM bot_trades WHERE user_id = {P} AND coin = {P} AND asset_type = {P} AND status = 'open'",
            (user_id, ticker, ASSET_TYPE),
        )
        return row is not None
    except Exception:
        return False


def _open_value(user_id):
    """Sum of notional value (size * entry_price) of open claude_bot positions."""
    try:
        row = query_one(
            f"SELECT COALESCE(SUM(size * entry_price), 0) as val FROM bot_trades "
            f"WHERE user_id = {P} AND asset_type = {P} AND status = 'open'",
            (user_id, ASSET_TYPE),
        )
        return float(row["val"]) if row else 0.0
    except Exception:
        return 0.0


def _daily_pnl(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        row = query_one(
            f"SELECT COALESCE(SUM(pnl), 0) as t FROM bot_trades "
            f"WHERE user_id = {P} AND asset_type = {P} AND status = 'closed' AND DATE(closed_at) = {P}",
            (user_id, ASSET_TYPE, today),
        )
        return float(row["t"]) if row else 0.0
    except Exception:
        return 0.0


# ─── Re-entry watchlist (the new core capability) ───────────────────────

def _add_reentry(user_id, ticker, exit_price, exit_pnl_pct, exit_reason):
    """Add a ticker to the re-entry watchlist after exit."""
    try:
        execute(
            f"INSERT INTO bot_reentry_watch "
            f"(user_id, ticker, asset_type, exit_price, exit_pnl_pct, exit_reason, status) "
            f"VALUES ({P},{P},{P},{P},{P},{P},'watching')",
            (user_id, ticker, ASSET_TYPE, float(exit_price),
             float(exit_pnl_pct), exit_reason[:200]),
        )
        _log(user_id, "info", f"Watching {ticker} for re-entry (exit ${exit_price:.2f}, {exit_pnl_pct:+.2f}%)")
    except Exception as e:
        _log(user_id, "warning", f"_add_reentry({ticker}) failed: {e}")


def _expire_reentries(user_id):
    """Mark watchlist entries past the re-entry window as expired."""
    try:
        days = int(_cfg(user_id, "cb_reentry_window_days", "3"))
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        execute(
            f"UPDATE bot_reentry_watch SET status = 'expired', resolved_at = {P} "
            f"WHERE user_id = {P} AND asset_type = {P} AND status = 'watching' AND created_at < {P}",
            (datetime.now().isoformat(), user_id, ASSET_TYPE, cutoff),
        )
    except Exception:
        pass


def _list_reentries(user_id):
    try:
        return query(
            f"SELECT * FROM bot_reentry_watch "
            f"WHERE user_id = {P} AND asset_type = {P} AND status = 'watching' "
            f"ORDER BY created_at DESC",
            (user_id, ASSET_TYPE),
        )
    except Exception:
        return []


def _resolve_reentry(user_id, ticker, status):
    try:
        execute(
            f"UPDATE bot_reentry_watch SET status = {P}, resolved_at = {P} "
            f"WHERE user_id = {P} AND ticker = {P} AND asset_type = {P} AND status = 'watching'",
            (status, datetime.now().isoformat(), user_id, ticker, ASSET_TYPE),
        )
    except Exception:
        pass


# ─── Indicators ─────────────────────────────────────────────────────────

def _compute_rsi(close):
    if len(close) < 15:
        return None
    deltas = np.diff(close[-15:])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains)) if len(gains) else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) else 0.001
    rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
    return 100.0 - (100.0 / (1.0 + rs))


def _fetch_daily(ticker, period="6mo"):
    try:
        df = yf.download(ticker, period=period, interval="1d", progress=False, timeout=15)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


# ─── Candidate sourcing ─────────────────────────────────────────────────

def _get_oversold_from_daily(min_conf):
    """Query oversold_daily directly — the persistent source of truth for day-over-day
    oversold tracking. Bypasses the per-day screener_results cache which can be empty
    if no oversold scan has run today.
    """
    try:
        rows = query(
            f"SELECT ticker, days_tracked, ai_confidence, ai_verdict, ai_summary, "
            f"price, rsi_14, status, price_trend, first_seen, scan_date "
            f"FROM oversold_daily "
            f"WHERE scan_date = (SELECT MAX(scan_date) FROM oversold_daily) "
            f"AND ai_verdict IS NOT NULL AND ai_verdict != 'FALLING KNIFE' "
            f"ORDER BY days_tracked DESC, ai_confidence DESC LIMIT 30"
        )
    except Exception:
        return []

    # Pre-filter by confidence, then enrich with multi-TF RSI to compute composite
    keep = []
    for r in rows:
        days = int(r.get("days_tracked") or 1)
        conf = float(r.get("ai_confidence") or 0)
        threshold = (min_conf - 10) if days >= 3 else min_conf  # persistent → relaxed
        if conf < threshold:
            continue
        keep.append((r, days, conf))

    tickers = [r["ticker"] for r, _, _ in keep]
    mtf = _compute_multi_tf_rsi_batch(tickers) if tickers else {}

    out = []
    for r, days, conf in keep:
        rsi_d = float(r.get("rsi_14")) if r.get("rsi_14") is not None else None
        m = mtf.get(r["ticker"], {})
        rsi_w = m.get("rsi_weekly")
        rsi_m = m.get("rsi_monthly")
        # Composite = simple avg across timeframes that have data; falls back to daily only
        present = [v for v in (rsi_d, rsi_w, rsi_m) if v is not None]
        composite = round(sum(present) / len(present), 1) if present else None
        out.append({
            "ticker": r["ticker"],
            "confidence": conf,
            "verdict": r.get("ai_verdict", ""),
            "summary": (r.get("ai_summary") or "")[:200],
            "days_tracked": days,
            "price_trend": r.get("price_trend"),
            "status": r.get("status"),
            "first_seen": str(r.get("first_seen")) if r.get("first_seen") else None,
            "rsi_daily": rsi_d,
            "rsi_weekly": rsi_w,
            "rsi_monthly": rsi_m,
            "rsi_composite": composite,
        })

    # Sort by composite RSI ascending — lowest = strongest oversold confluence
    out.sort(key=lambda c: (c["rsi_composite"] if c["rsi_composite"] is not None else 999,
                             -c["confidence"]))
    return out


def _get_candidates(user_id, min_conf):
    """Pull candidates from screener: Top Gainers (momentum) + Oversold (reversal).

    Oversold uses oversold_daily directly so the bot picks up day-over-day persistence
    even when the per-day screener cache hasn't been populated by a fresh scan.
    """
    candidates = []

    # Oversold from persistent table — already sorted by composite RSI (lowest first)
    try:
        for opp in _get_oversold_from_daily(min_conf):
            ticker = opp.get("ticker", "")
            if not ticker or any(c["ticker"] == ticker for c in candidates):
                continue
            candidates.append({
                "ticker": ticker,
                "category": "oversold",
                "entry_style": "reversal",
                "screener_conf": opp["confidence"],
                "days_tracked": opp["days_tracked"],
                "verdict": opp.get("verdict", ""),
                "summary": opp.get("summary", ""),
                "rsi_daily": opp.get("rsi_daily"),
                "rsi_weekly": opp.get("rsi_weekly"),
                "rsi_monthly": opp.get("rsi_monthly"),
                "rsi_composite": opp.get("rsi_composite"),
            })
    except Exception as e:
        _log(user_id, "warning", f"oversold_daily lookup failed: {e}")

    # Gainers from screener cache (these change daily; cache is fine)
    try:
        from screener import run_screener
        try:
            r = run_screener(category="gainers", limit=15, timeframe="1d")
            for opp in r.get("opportunities", []):
                ticker = opp.get("ticker", "")
                conf = opp.get("confidence", 0)
                if not ticker or conf < min_conf:
                    continue
                if any(c["ticker"] == ticker for c in candidates):
                    continue
                candidates.append({
                    "ticker": ticker,
                    "category": "gainers",
                    "entry_style": "momentum",
                    "screener_conf": conf,
                    "days_tracked": 1,
                    "verdict": opp.get("verdict", ""),
                    "summary": (opp.get("summary") or "")[:200],
                })
        except Exception as e:
            _log(user_id, "warning", f"Screener gainers failed: {e}")
    except Exception as e:
        _log(user_id, "warning", f"Screener import failed: {e}")

    return candidates


# ─── Signal logic ────────────────────────────────────────────────────────

def _evaluate(ticker, entry_style, composite_rsi=None):
    """Returns dict {signal, rsi, price, sma20, sma50, atr, reason} or None."""
    df = _fetch_daily(ticker)
    if df is None or len(df) < 30:
        return None
    close = df["Close"].values.flatten()
    high = df["High"].values.flatten()
    low = df["Low"].values.flatten()
    price = float(close[-1])
    rsi = _compute_rsi(close)
    if rsi is None:
        return None
    sma20 = float(np.mean(close[-20:]))
    sma50 = float(np.mean(close[-50:])) if len(close) >= 50 else float(np.mean(close))
    # ATR14
    if len(close) >= 15:
        tr = np.maximum(high[-14:] - low[-14:],
                        np.maximum(np.abs(high[-14:] - close[-15:-1]),
                                   np.abs(low[-14:] - close[-15:-1])))
        atr = float(np.mean(tr))
    else:
        atr = float(np.mean(high - low))

    out = {"rsi": round(rsi, 1), "price": price, "sma20": round(sma20, 2),
           "sma50": round(sma50, 2), "atr": round(atr, 4)}

    # Reversal entry (oversold/bottom-forming): multi-TF RSI confluence + structure
    # Use composite RSI when available — single-TF daily RSI can fire on healthy dips
    # in stocks that are still strong on weekly/monthly.
    if entry_style == "reversal":
        # Composite gate: average of D/W/M RSI < 40 (multi-TF oversold confluence)
        # AND daily RSI < 40 (confirms current weakness, not just historical)
        composite_ok = composite_rsi is not None and composite_rsi < 40
        daily_ok = rsi < 40
        # Fall back to daily-only when composite unavailable
        passes = composite_ok if composite_rsi is not None else (rsi < 35)

        if passes and daily_ok and sma20 > 0:
            dist = (price - sma20) / sma20
            if dist > -0.08:
                cmp_str = f", composite={composite_rsi:.1f}" if composite_rsi is not None else ""
                return {**out, "signal": "BUY",
                        "reason": f"RSI_D={rsi:.1f}{cmp_str} oversold, within {abs(dist):.1%} of SMA20"}
            return {**out, "signal": "SKIP", "reason": f"RSI_D={rsi:.1f}, {abs(dist):.1%} below SMA20 — falling knife"}
        if composite_rsi is not None:
            return {**out, "signal": "SKIP",
                    "reason": f"composite RSI={composite_rsi:.1f} (need <40), daily={rsi:.1f}"}
        return {**out, "signal": "SKIP", "reason": f"RSI_D={rsi:.1f} not oversold (need <35)"}

    # Momentum entry: relaxed band — RSI 45-72, price above SMA20 with SMA20>=SMA50
    if entry_style == "momentum":
        if 45 <= rsi <= 72 and price > sma20 and sma20 >= sma50 * 0.99:
            return {**out, "signal": "BUY", "reason": f"RSI={rsi:.1f} momentum, price>SMA20"}
        return {**out, "signal": "SKIP", "reason": f"RSI={rsi:.1f} momentum gates failed (price>{sma20:.2f}? sma20>=sma50?)"}

    return {**out, "signal": "SKIP", "reason": f"RSI={rsi:.1f}, no setup"}


def _llm_validate(ticker, eval_data, screener_info, regime):
    """LLM gating via OpenRouter. Falls back to rule-based approve.

    Model resolution (tiered):
      1. OPENROUTER_MODEL_GATING — claude_bot specific override
      2. OPENROUTER_MODEL_RESEARCH — codebase-wide research model
      3. Hard default: deepseek/deepseek-chat (cheapest reliable option for JSON gates)
    """
    try:
        import os, re, requests
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        # DB override (admin UI) > env vars > hardcoded default
        env_model = (os.getenv("OPENROUTER_MODEL_GATING")
                     or os.getenv("OPENROUTER_MODEL_RESEARCH")
                     or "deepseek/deepseek-chat")
        try:
            from shared.llm_config import get_model
            model = get_model("claude_gating", env_model)
        except Exception:
            model = env_model
        if not api_key:
            return eval_data.get("signal") == "BUY"
        prompt = (
            f"You are a swing trade analyst. Approve or reject this trade for a 1-3 day scalp.\n"
            f"TICKER: {ticker}\n"
            f"SCREENER: {screener_info.get('category')} verdict={screener_info.get('verdict')} "
            f"conf={screener_info.get('screener_conf')}% days={screener_info.get('days_tracked')}\n"
            f"NOTES: {screener_info.get('summary')}\n"
            f"TECHNICAL: signal={eval_data['signal']} rsi={eval_data['rsi']} "
            f"price={eval_data['price']} sma20={eval_data['sma20']} sma50={eval_data['sma50']}\n"
            f"REGIME: {regime}\n"
            f'Respond JSON only: {{"approve": true/false, "confidence": 0-100, "reasoning": "one sentence"}}'
        )
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1, "max_tokens": 200},
            timeout=30,
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            m = re.search(r'\{[^}]+\}', content)
            if m:
                r = json.loads(m.group())
                approved = bool(r.get("approve")) and r.get("confidence", 0) >= 60
                logger.info(f"[CB] LLM({model}) {ticker} → approve={approved} conf={r.get('confidence')}")
                return approved
            logger.warning(f"[CB] LLM({model}) {ticker}: no JSON in response: {content[:120]}")
        else:
            logger.warning(f"[CB] LLM({model}) {ticker}: HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"LLM validate failed for {ticker}: {e}")
    return eval_data.get("signal") == "BUY"


# ─── Trade execution ────────────────────────────────────────────────────

def _get_broker(user_id=None):
    """Resolve broker client per user config + user-private API keys.

    Reads cb_broker (alpaca|webull) and cb_mode (paper|live). When cb_mode='live'
    the broker is initialized against the user's LIVE Alpaca account — real
    orders, real money. CLAUDE.md rule #1 was relaxed Apr 2026 to permit this
    on explicit per-user opt-in; paper remains the default for any user who
    hasn't set cb_mode.

    Key resolution: per-user `user_api_keys` rows (encrypted) take precedence;
    if a user hasn't set their own keys, the broker factory falls back to the
    container's env vars (ALPACA_API_KEY / WEBULL_APP_KEY / etc).
    """
    broker_name = "alpaca"
    requested_mode = "paper"
    if user_id is not None:
        broker_name = (_cfg(user_id, "cb_broker", "alpaca") or "alpaca").lower()
        requested_mode = (_cfg(user_id, "cb_mode", "paper") or "paper").lower()
    is_live = requested_mode == "live"
    if is_live:
        logger.warning(
            f"[CB:{user_id}] LIVE TRADING ENABLED — cb_mode='live'. "
            f"Real orders will route to broker={broker_name!r}. Risk gates "
            f"(kill_switch, daily_loss_limit, hard_stop) still apply."
        )

    user_keys = {}
    if user_id is not None:
        try:
            from shared.helpers import get_user_api_keys
            user_keys = get_user_api_keys(user_id, broker_name)
        except Exception as e:
            logger.debug(f"[CB:{user_id}] user_api_keys lookup failed: {e}")

    try:
        from stock_bot.broker_client import get_broker_client
        # paper=True for paper-mode users; paper=False routes to live broker.
        # Webull client ignores paper kwarg (sandbox-only in this app).
        return get_broker_client(broker=broker_name, paper=(not is_live), **user_keys)
    except Exception as e:
        logger.warning(f"[CB:{user_id}] Broker {broker_name!r} init failed: {e}")
        return None


def _open_trade(user_id, ticker, eval_data, reason, size_factor=1.0):
    price = eval_data["price"]
    if price <= 0:
        return False

    broker = _get_broker(user_id)
    equity = 100000.0
    if broker is not None:
        try:
            bal = broker.get_balance()
            equity = float(bal.get("total_equity", 0)) or equity
        except Exception:
            pass

    # Risk gates
    if _cfg(user_id, "cb_kill_switch") == "1":
        _log(user_id, "info", f"BLOCKED {ticker}: kill switch active")
        return False
    daily_limit = float(_cfg(user_id, "cb_daily_loss_limit", "500"))
    if _daily_pnl(user_id) <= -daily_limit:
        from shared.helpers import _upsert_bot_config
        _upsert_bot_config(user_id, "cb_kill_switch", "1")
        _log(user_id, "error", f"BLOCKED {ticker}: daily loss limit breached, kill switch ON")
        return False
    max_pos = int(_cfg(user_id, "cb_max_positions", "5"))
    if _open_count(user_id) >= max_pos:
        _log(user_id, "info", f"BLOCKED {ticker}: max {max_pos} positions reached")
        return False
    if _has_position(user_id, ticker):
        _log(user_id, "info", f"BLOCKED {ticker}: already holding")
        return False

    max_pct = float(_cfg(user_id, "cb_max_position_pct", "12"))
    target_size_usd = equity * (max_pct / 100.0) * size_factor
    # Removed prior hardcoded $5000 cap so cb_max_position_pct actually rules
    # the position size for users with bigger paper accounts. Re-introduce per
    # broker if you need a hard ceiling separate from the pct.
    shares = max(1, int(target_size_usd / price))

    # Total exposure cap — sum of open positions can't exceed this % of equity.
    max_exp_pct = float(_cfg(user_id, "cb_max_total_exposure_pct", "60"))
    if equity > 0 and max_exp_pct > 0:
        deployed = _open_value(user_id)
        projected = deployed + (shares * price)
        cap = equity * (max_exp_pct / 100.0)
        if projected > cap:
            _log(user_id, "info",
                 f"BLOCKED {ticker}: total exposure ${projected:.0f} would exceed {max_exp_pct}% cap "
                 f"(${cap:.0f}) — already deployed ${deployed:.0f}")
            return False

    tp_pct = float(_cfg(user_id, "cb_take_profit_pct", "5.0"))
    scalp_stop_pct = float(_cfg(user_id, "cb_scalp_stop_pct", "2.0"))
    take_profit = round(price * (1.0 + tp_pct / 100.0), 2)
    stop_loss = round(price * (1.0 - scalp_stop_pct / 100.0), 2)

    order_id = f"paper_{int(time.time())}"
    if broker is not None and broker.is_configured():
        try:
            order = broker.place_order(
                symbol=ticker, side="buy", qty=shares,
                stop_loss=stop_loss, take_profit=take_profit,
            )
            if order.get("success"):
                order_id = order.get("order_id", order_id)
            else:
                _log(user_id, "warning", f"Alpaca order returned !success for {ticker}: {order.get('error')}")
        except Exception as e:
            _log(user_id, "warning", f"Alpaca order failed for {ticker} (logging paper): {e}")

    try:
        execute(
            f"INSERT INTO bot_trades "
            f"(user_id, coin, side, size, entry_price, stop_loss, take_profit, status, "
            f" strategy, asset_type, signal_reason, blofin_order_id, direction_bias) "
            f"VALUES ({P},{P},'buy',{P},{P},{P},{P},'open',{P},{P},{P},{P},'long')",
            (user_id, ticker, shares, price, stop_loss, take_profit,
             "scalp_rotate", ASSET_TYPE, reason[:500], order_id),
        )
        _log(user_id, "info",
             f"OPEN {ticker} x{shares} @ ${price:.2f} | TP ${take_profit} SL ${stop_loss} | {reason[:80]}")
        return True
    except Exception as e:
        _log(user_id, "error", f"DB insert failed for {ticker}: {e}")
        return False


def _close_trade(user_id, trade, current_price, exit_reason, eligible_reentry):
    try:
        entry = float(trade["entry_price"])
        size = float(trade["size"])

        # Sanity guardrail: yfinance occasionally returns garbage prints (single
        # bad tick, post-acquisition stale data, split mishandling). A move of
        # more than ±100% intraday for an equity is a data bug, not reality.
        # Skip the close so the bot re-evaluates next cycle with fresh data.
        # Audit Apr 2026: PRTA closed at $433.47 vs $11.51 entry → +$183K phantom P&L.
        if entry > 0 and abs(current_price / entry - 1.0) > 1.0:
            _log(user_id, "error",
                 f"REJECTED close for {trade['coin']}: implausible price ${current_price:.2f} "
                 f"vs entry ${entry:.2f} (move {((current_price/entry - 1)*100):+.0f}%) — likely yfinance bad data")
            return

        broker = _get_broker(user_id)
        if broker is not None and broker.is_configured():
            try:
                broker.close_position(trade["coin"])
            except Exception as e:
                _log(user_id, "warning", f"Broker close failed for {trade['coin']}: {e}")

        pnl = (current_price - entry) * size
        pnl_pct = (current_price - entry) / entry * 100.0 if entry > 0 else 0.0
        fee = round(size * 0.01, 4)

        execute(
            f"UPDATE bot_trades SET exit_price = {P}, pnl = {P}, pnl_pct = {P}, fee = {P}, "
            f"status = 'closed', closed_at = {'NOW()' if IS_POSTGRES else 'CURRENT_TIMESTAMP'} "
            f"WHERE id = {P}",
            (current_price, round(pnl - fee, 2), round(pnl_pct, 2), fee, trade["id"]),
        )
        _log(user_id, "info",
             f"CLOSE {trade['coin']} @ ${current_price:.2f} | PnL ${pnl - fee:+.2f} ({pnl_pct:+.2f}%) | {exit_reason}")

        if eligible_reentry:
            _add_reentry(user_id, trade["coin"], current_price, pnl_pct, exit_reason)
    except Exception as e:
        _log(user_id, "error", f"Close failed for {trade.get('coin')}: {e}")


# ─── Exit checks ────────────────────────────────────────────────────────

def _check_exits(user_id):
    """Apply scalp-rotate exit rules. -2% scalp + re-entry, +5% TP + re-entry,
    -8% hard stop NO re-entry, RSI>60 lock + re-entry."""
    tp_pct = float(_cfg(user_id, "cb_take_profit_pct", "5.0"))
    scalp_pct = float(_cfg(user_id, "cb_scalp_stop_pct", "2.0"))
    hard_pct = float(_cfg(user_id, "cb_hard_stop_pct", "8.0"))

    try:
        rows = query(
            f"SELECT * FROM bot_trades WHERE user_id = {P} AND asset_type = {P} AND status = 'open'",
            (user_id, ASSET_TYPE),
        )
    except Exception:
        return

    for trade in rows:
        try:
            ticker = trade["coin"]
            entry = float(trade["entry_price"])
            df = _fetch_daily(ticker, period="3mo")
            if df is None or df.empty:
                continue
            close = df["Close"].values.flatten()
            current_price = float(close[-1])
            pnl_pct = (current_price - entry) / entry * 100.0 if entry > 0 else 0.0
            rsi = _compute_rsi(close)

            # Hard hold cap: 48h force-close to prevent stale positions hanging
            # on indefinitely (audit Apr 2026 found user holdings unmoved for 3+ days).
            hours_open = None
            try:
                opened_str = str(trade["opened_at"])
                try:
                    opened = datetime.strptime(opened_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    opened = datetime.fromisoformat(opened_str)
                hours_open = (datetime.now() - opened).total_seconds() / 3600
            except Exception:
                pass

            exit_reason = None
            eligible_reentry = False

            if hours_open is not None and hours_open >= 48:
                exit_reason = f"Hard 48h cap: open {hours_open / 24:.1f}d, P&L {pnl_pct:+.2f}% (force-close)"
                eligible_reentry = False
            elif pnl_pct <= -hard_pct:
                exit_reason = f"Hard stop {pnl_pct:.2f}% (no re-entry)"
                eligible_reentry = False
            elif pnl_pct >= tp_pct:
                exit_reason = f"Take-profit +{pnl_pct:.2f}%"
                eligible_reentry = True
            elif pnl_pct <= -scalp_pct:
                exit_reason = f"Scalp stop {pnl_pct:.2f}%"
                eligible_reentry = True
            elif rsi is not None and rsi > 60 and pnl_pct > 0.5:
                exit_reason = f"RSI overbought ({rsi:.1f}>60), locking +{pnl_pct:.2f}%"
                eligible_reentry = True

            if exit_reason:
                _close_trade(user_id, trade, current_price, exit_reason, eligible_reentry)
        except Exception as e:
            _log(user_id, "warning", f"Exit check error {trade.get('coin')}: {e}")


# ─── Re-entry checks ────────────────────────────────────────────────────

def _check_reentries(user_id, regime):
    """Look for stabilized re-entry candidates and re-open at half size.

    Stabilization rule: last close >= exit price * 0.99 AND last bar bullish
    (close > previous close). RSI must not be overbought (>60).
    """
    _expire_reentries(user_id)
    if regime == "DANGER":
        return

    max_attempts = int(_cfg(user_id, "cb_reentry_max_attempts", "2"))
    size_factor = float(_cfg(user_id, "cb_reentry_size_factor", "0.5"))
    watching = _list_reentries(user_id)
    for w in watching:
        try:
            ticker = w["ticker"]
            if w.get("attempts", 0) >= max_attempts:
                _resolve_reentry(user_id, ticker, "max_attempts")
                continue
            if _has_position(user_id, ticker):
                continue

            df = _fetch_daily(ticker, period="3mo")
            if df is None or len(df) < 5:
                continue
            close = df["Close"].values.flatten()
            current_price = float(close[-1])
            prev_close = float(close[-2])
            exit_price = float(w["exit_price"])
            rsi = _compute_rsi(close)
            if rsi is None:
                continue

            stabilized = (current_price >= exit_price * 0.99 and current_price > prev_close)
            if not stabilized or rsi > 60:
                continue

            sma20 = float(np.mean(close[-20:]))
            eval_data = {
                "signal": "BUY",
                "rsi": round(rsi, 1),
                "price": current_price,
                "sma20": round(sma20, 2),
                "sma50": float(np.mean(close[-50:])) if len(close) >= 50 else sma20,
                "atr": 0.0,
            }
            reason = (f"Re-entry: stabilized {current_price/exit_price-1:+.1%} vs exit ${exit_price:.2f}, "
                      f"RSI={rsi:.1f}, last bar bullish")
            ok = _open_trade(user_id, ticker, eval_data, reason, size_factor=size_factor)
            try:
                execute(
                    f"UPDATE bot_reentry_watch SET attempts = attempts + 1 "
                    f"WHERE user_id = {P} AND ticker = {P} AND asset_type = {P} AND status = 'watching'",
                    (user_id, ticker, ASSET_TYPE),
                )
            except Exception:
                pass
            if ok:
                _resolve_reentry(user_id, ticker, "reentered")
        except Exception as e:
            _log(user_id, "warning", f"Re-entry check error {w.get('ticker')}: {e}")


# ─── Scan loop ──────────────────────────────────────────────────────────

def _scan_cycle(user_id):
    if _cfg(user_id, "cb_enabled") != "1":
        return False

    if _cfg(user_id, "cb_kill_switch") == "1":
        _log(user_id, "warning", "Kill switch active — skipping cycle")
        return True

    # Always check exits first (preserve gains, scalp losers)
    _check_exits(user_id)

    # Market regime gate
    regime = "UNKNOWN"
    try:
        from features.watchdog.engine import get_regime
        regime = get_regime().get("regime", "UNKNOWN")
    except Exception:
        try:
            health = check_market_health("stock")
            regime = "DANGER" if health.get("status") in ("DANGER", "PANIC") else "NEUTRAL"
        except Exception:
            pass

    if regime == "DANGER":
        _log(user_id, "warning", f"Regime {regime} — exits only, no new entries")
        return True

    # Re-entry candidates first (they're already vetted by prior trade)
    _check_reentries(user_id, regime)

    # Fresh candidates from screener
    min_conf = int(_cfg(user_id, "cb_min_confidence", "65"))
    candidates = _get_candidates(user_id, min_conf)
    if candidates:
        def _label(c):
            tag = f"{c['ticker']}({c['category']},{c['screener_conf']}%"
            if c['category'] == 'oversold':
                tag += f",{c.get('days_tracked', 1)}d"
                if c.get('rsi_composite') is not None:
                    tag += f",rsi_avg={c['rsi_composite']}"
            return tag + ")"
        cand_summary = ", ".join(_label(c) for c in candidates[:8])
        persistent = sum(1 for c in candidates if c['category'] == 'oversold' and c.get('days_tracked', 1) >= 3)
        _log(user_id, "info",
             f"Screener returned {len(candidates)} candidates [{cand_summary}] persistent_oversold={persistent} regime={regime}")
    else:
        _log(user_id, "info", f"Screener returned 0 candidates (regime={regime}) — screener cache may be empty or all below conf={min_conf}")

    new_trades = 0
    for cand in candidates:
        if new_trades >= 2:
            break
        try:
            eval_data = _evaluate(cand["ticker"], cand["entry_style"], composite_rsi=cand.get("rsi_composite"))
            if not eval_data:
                _log(user_id, "info", f"{cand['ticker']}: no data (yfinance fetch failed)")
                continue
            if eval_data.get("signal") != "BUY":
                _log(user_id, "info", f"{cand['ticker']}: SKIP — {eval_data.get('reason', '?')} (RSI={eval_data.get('rsi')}, price=${eval_data.get('price'):.2f})")
                continue
            _log(user_id, "info", f"{cand['ticker']}: BUY signal — {eval_data['reason']} (validating with LLM)")
            if not _llm_validate(cand["ticker"], eval_data, cand, regime):
                _log(user_id, "info", f"{cand['ticker']}: LLM rejected")
                continue
            if _open_trade(user_id, cand["ticker"], eval_data,
                           f"[{cand['category']}] {eval_data['reason']}"):
                new_trades += 1
        except Exception as e:
            _log(user_id, "warning", f"Candidate {cand.get('ticker')} error: {e}")

    _log(user_id, "info",
         f"Cycle done: opened={new_trades}, open={_open_count(user_id)}, daily_pnl=${_daily_pnl(user_id):.2f}")
    return True


def _scan_loop(user_id):
    _log(user_id, "info", "Claude bot loop started")
    while _state.get("running"):
        try:
            keep_going = _scan_cycle(user_id)
            if not keep_going:
                _log(user_id, "info", "Disabled via config — stopping")
                break
        except Exception as e:
            _log(user_id, "error", f"Scan cycle error: {e}")
        interval = int(_cfg(user_id, "cb_scan_interval", "300"))
        for _ in range(interval):
            if not _state.get("running"):
                break
            time.sleep(1)
    _log(user_id, "info", "Claude bot loop stopped")
    _state["running"] = False


# ─── Public API ─────────────────────────────────────────────────────────

def start(user_id):
    if _state.get("running"):
        return {"ok": False, "error": "Already running"}
    _state["user_id"] = user_id
    _state["running"] = True
    t = threading.Thread(target=_scan_loop, args=(user_id,), daemon=True, name="claude-bot")
    _state["thread"] = t
    t.start()
    return {"ok": True, "message": "Claude bot started (paper mode)"}


def stop():
    if not _state.get("running"):
        return {"ok": False, "error": "Not running"}
    _state["running"] = False
    return {"ok": True, "message": "Claude bot stopping..."}


def status(user_id):
    return {
        "running": _state.get("running", False),
        "user_id": _state.get("user_id"),
        "enabled": _cfg(user_id, "cb_enabled") == "1",
        "kill_switch": _cfg(user_id, "cb_kill_switch") == "1",
        "open_positions": _open_count(user_id),
        "daily_pnl": _daily_pnl(user_id),
        "watchlist_count": len(_list_reentries(user_id)),
        "config": {
            "broker": _cfg(user_id, "cb_broker", "alpaca"),
            "mode": _cfg(user_id, "cb_mode", "paper"),
            "scan_interval": int(_cfg(user_id, "cb_scan_interval", "300")),
            "max_positions": int(_cfg(user_id, "cb_max_positions", "5")),
            "max_position_pct": float(_cfg(user_id, "cb_max_position_pct", "12")),
            "max_total_exposure_pct": float(_cfg(user_id, "cb_max_total_exposure_pct", "60")),
            "daily_loss_limit": float(_cfg(user_id, "cb_daily_loss_limit", "500")),
            "min_confidence": int(_cfg(user_id, "cb_min_confidence", "65")),
            "take_profit_pct": float(_cfg(user_id, "cb_take_profit_pct", "5.0")),
            "scalp_stop_pct": float(_cfg(user_id, "cb_scalp_stop_pct", "2.0")),
            "hard_stop_pct": float(_cfg(user_id, "cb_hard_stop_pct", "8.0")),
            "reentry_size_factor": float(_cfg(user_id, "cb_reentry_size_factor", "0.5")),
            "reentry_max_attempts": int(_cfg(user_id, "cb_reentry_max_attempts", "2")),
            "reentry_window_days": int(_cfg(user_id, "cb_reentry_window_days", "3")),
        },
    }


def reentry_watchlist(user_id):
    return [
        {
            "ticker": w["ticker"],
            "exit_price": w.get("exit_price"),
            "exit_pnl_pct": w.get("exit_pnl_pct"),
            "exit_reason": w.get("exit_reason"),
            "attempts": w.get("attempts", 0),
            "created_at": str(w.get("created_at")),
        }
        for w in _list_reentries(user_id)
    ]


_mtf_rsi_cache = {}  # ticker -> (data_dict, ts)
_MTF_RSI_TTL = 3600  # 1 hour


def _rsi_from_closes(closes, period=14):
    """Simple-mean RSI on a 1-D array of closes. Matches the screener's RSI-14 formula."""
    if closes is None or len(closes) < period + 1:
        return None
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains)) if len(gains) else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) else 0.001
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_multi_tf_rsi_batch(tickers):
    """Compute weekly + monthly RSI-14 for a list of tickers using a single batched
    yfinance download. Per-ticker results are cached for 1 hour.

    Returns: dict[ticker] -> {rsi_weekly, rsi_weekly_prev, rsi_weekly_change, rsi_monthly}
    """
    out = {}
    needed = []
    now = time.time()
    for t in tickers:
        cached = _mtf_rsi_cache.get(t)
        if cached and now - cached[1] < _MTF_RSI_TTL:
            out[t] = cached[0]
        else:
            needed.append(t)

    if not needed:
        return out

    try:
        import pandas as pd
        df = yf.download(" ".join(needed), period="2y", interval="1d",
                         progress=False, threads=True, group_by="ticker")
        if df is None or df.empty:
            return out

        # Single-ticker case: yfinance returns flat columns
        single = (len(needed) == 1)

        for t in needed:
            try:
                if single:
                    closes = df["Close"].dropna()
                else:
                    if t not in df.columns.get_level_values(0):
                        continue
                    closes = df[t]["Close"].dropna()

                if len(closes) < 30:
                    continue

                # Weekly: resample to Friday close
                weekly = closes.resample("W-FRI").last().dropna()
                rsi_w = _rsi_from_closes(weekly.values, period=14)
                rsi_w_prev = _rsi_from_closes(weekly.values[:-1], period=14) if len(weekly) >= 16 else None

                # Monthly: resample to month-end close
                monthly = closes.resample("ME").last().dropna()
                rsi_m = _rsi_from_closes(monthly.values, period=14)

                rec = {
                    "rsi_weekly": round(rsi_w, 1) if rsi_w is not None else None,
                    "rsi_weekly_prev": round(rsi_w_prev, 1) if rsi_w_prev is not None else None,
                    "rsi_weekly_change": (
                        round(rsi_w - rsi_w_prev, 1)
                        if (rsi_w is not None and rsi_w_prev is not None) else None
                    ),
                    "rsi_monthly": round(rsi_m, 1) if rsi_m is not None else None,
                }
                _mtf_rsi_cache[t] = (rec, now)
                out[t] = rec
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"_compute_multi_tf_rsi_batch failed: {e}")

    return out


def get_oversold_tracker(limit=30):
    """Return latest oversold_daily entries grouped by ticker, ordered by days_tracked.

    Surfaces the day-over-day persistence the bot uses when sourcing candidates.
    Shows what the screener's seen each day, regardless of whether claude_bot
    has acted on it yet.
    """
    try:
        rows = query(
            f"SELECT ticker, MAX(days_tracked) AS days, MAX(scan_date) AS last_scan, "
            f"MAX(ai_confidence) AS conf, MAX(price) AS price, MAX(rsi_14) AS rsi, "
            f"MAX(status) AS status, MAX(price_trend) AS price_trend, "
            f"MAX(ai_verdict) AS verdict, MAX(first_seen) AS first_seen "
            f"FROM oversold_daily "
            f"WHERE scan_date >= (SELECT MAX(scan_date) FROM oversold_daily) "
            f"GROUP BY ticker "
            f"ORDER BY days DESC, conf DESC LIMIT {P}",
            (int(limit),),
        )
        tickers = [r["ticker"] for r in rows]
        mtf = _compute_multi_tf_rsi_batch(tickers) if tickers else {}

        result = []
        for r in rows:
            mt = mtf.get(r["ticker"], {})
            rsi_d = float(r["rsi"]) if r["rsi"] is not None else None
            rsi_w = mt.get("rsi_weekly")
            rsi_m = mt.get("rsi_monthly")
            present = [v for v in (rsi_d, rsi_w, rsi_m) if v is not None]
            composite = round(sum(present) / len(present), 1) if present else None
            result.append({
                "ticker": r["ticker"],
                "days_tracked": int(r["days"] or 1),
                "last_scan": str(r["last_scan"]),
                "first_seen": str(r["first_seen"]) if r.get("first_seen") else None,
                "confidence": float(r["conf"]) if r["conf"] is not None else None,
                "price": float(r["price"]) if r["price"] is not None else None,
                # Daily RSI-14 from screener (simple-mean on daily closes)
                "rsi": rsi_d,
                "rsi_weekly": rsi_w,
                "rsi_weekly_prev": mt.get("rsi_weekly_prev"),
                "rsi_weekly_change": mt.get("rsi_weekly_change"),
                "rsi_monthly": rsi_m,
                # Composite (avg of available timeframes) — bot's primary opportunity ranker
                "rsi_composite": composite,
                "status": r.get("status"),
                "price_trend": r.get("price_trend"),
                "verdict": r.get("verdict"),
                # >=3 days = the threshold claude_bot uses for persistent-oversold acceptance
                "qualifies": int(r["days"] or 1) >= 3 and r.get("verdict") != "FALLING KNIFE",
                # Strong opportunity: composite < 35 + qualifies + not falling knife
                "is_opportunity": (composite is not None and composite < 35
                                   and int(r["days"] or 1) >= 3
                                   and r.get("verdict") != "FALLING KNIFE"),
            })
        # Sort by composite RSI ascending (best multi-TF oversold first)
        result.sort(key=lambda x: (x["rsi_composite"] if x["rsi_composite"] is not None else 999,
                                    -(x["confidence"] or 0)))
        return result
    except Exception:
        return []


def get_logs(user_id, limit=100):
    """Recent bot_log entries for the claude bot."""
    try:
        rows = query(
            f"SELECT level, message, created_at FROM bot_log "
            f"WHERE user_id = {P} AND source = 'claude_bot' "
            f"ORDER BY created_at DESC LIMIT {P}",
            (user_id, int(limit)),
        )
        return [{"level": r["level"], "message": r["message"], "created_at": str(r["created_at"])} for r in rows]
    except Exception:
        return []


def get_balance(user_id):
    """Paper P&L summary for the dashboard. Starts from $100k paper equity."""
    paper_start = 100000.0
    today = datetime.now().strftime("%Y-%m-%d")

    realized = 0.0
    fees = 0.0
    total_trades = wins = losses = 0
    try:
        row = query_one(
            f"SELECT COALESCE(SUM(pnl),0) as pnl, COALESCE(SUM(fee),0) as fee, "
            f"COUNT(*) as cnt, "
            f"SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, "
            f"SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses "
            f"FROM bot_trades WHERE user_id = {P} AND asset_type = {P} AND status = 'closed'",
            (user_id, ASSET_TYPE),
        )
        if row:
            realized = float(row["pnl"] or 0)
            fees = float(row["fee"] or 0)
            total_trades = int(row["cnt"] or 0)
            wins = int(row["wins"] or 0)
            losses = int(row["losses"] or 0)
    except Exception:
        pass

    # Today's P&L
    today_pnl = _daily_pnl(user_id)

    # Week / month rollups
    week_pnl = month_pnl = 0.0
    try:
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
        row = query_one(
            f"SELECT COALESCE(SUM(pnl),0) as t FROM bot_trades "
            f"WHERE user_id = {P} AND asset_type = {P} AND status = 'closed' AND DATE(closed_at) >= {P}",
            (user_id, ASSET_TYPE, week_start),
        )
        week_pnl = float(row["t"]) if row else 0.0
    except Exception:
        pass
    try:
        month_start = datetime.now().strftime("%Y-%m-01")
        row = query_one(
            f"SELECT COALESCE(SUM(pnl),0) as t FROM bot_trades "
            f"WHERE user_id = {P} AND asset_type = {P} AND status = 'closed' AND DATE(closed_at) >= {P}",
            (user_id, ASSET_TYPE, month_start),
        )
        month_pnl = float(row["t"]) if row else 0.0
    except Exception:
        pass

    # Unrealized from open positions
    unrealized = 0.0
    open_count = 0
    try:
        opens = query(
            f"SELECT coin, side, size, entry_price FROM bot_trades "
            f"WHERE user_id = {P} AND asset_type = {P} AND status = 'open'",
            (user_id, ASSET_TYPE),
        )
        open_count = len(opens)
        for t in opens:
            try:
                cp = yf.Ticker(t["coin"]).info.get("regularMarketPrice")
                if cp and t.get("entry_price"):
                    mult = 1 if str(t.get("side", "buy")) in ("long", "buy") else -1
                    unrealized += (float(cp) - float(t["entry_price"])) * float(t.get("size", 0)) * mult
            except Exception:
                pass
    except Exception:
        pass

    paper_balance = paper_start + realized
    total_equity_val = paper_balance + unrealized
    deployed = _open_value(user_id)
    deployed_pct = round(deployed / total_equity_val * 100, 1) if total_equity_val > 0 else 0
    return {
        "paper_start": paper_start,
        "paper_balance": round(paper_balance, 2),
        "total_equity": round(total_equity_val, 2),
        "total_realized_pnl": round(realized, 2),
        "total_unrealized_pnl": round(unrealized, 2),
        "funds_deployed_usd": round(deployed, 2),
        "funds_deployed_pct": deployed_pct,
        "funds_available_pct": round(max(0, 100 - deployed_pct), 1),
        "max_total_exposure_pct": float(_cfg(user_id, "cb_max_total_exposure_pct", "60")),
        "max_position_pct": float(_cfg(user_id, "cb_max_position_pct", "12")),
        "today_pnl": round(today_pnl, 2),
        "week_pnl": round(week_pnl, 2),
        "month_pnl": round(month_pnl, 2),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / total_trades * 100) if total_trades > 0 else 0, 1),
        "open_positions": open_count,
        "total_fees": round(fees, 2),
        "asset_type": ASSET_TYPE,
        "timestamp": datetime.now().isoformat(),
    }
