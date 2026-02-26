"""
Stock Trading Bot Engine — Background daemon thread with scan cycle.
Fetches data via yfinance, generates signals, validates via multi-LLM, executes on Alpaca paper.
"""

import os
import json
import logging
import threading
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db import IS_POSTGRES, query, query_one, execute
from stock_bot.broker_client import (
    AlpacaClient, WebullClient, get_broker_client,
    STOCK_MAP, DEFAULT_STOCKS, is_market_open, get_stock_info,
)
from stock_bot.stock_risk_manager import StockRiskManager
from stock_bot.stock_validator import validate_stock_trade

P = "%s" if IS_POSTGRES else "?"


def _get_config(user_id, key, default=None):
    row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = {P}", (user_id, key))
    return row["value"] if row else default


def _log(user_id, level: str, message: str, details: str = None):
    try:
        execute(
            f"INSERT INTO bot_log (user_id, level, message, details, source) VALUES ({P}, {P}, {P}, {P}, 'stock')",
            (user_id, level, message, details),
        )
    except Exception:
        pass
    log_fn = getattr(logger, level, logger.info)
    log_fn(f"[STOCK-BOT] {message}")


def _journal_log(user_id, ticker: str, action: str, entry_price: float = None,
                  exit_price: float = None, shares: float = None,
                  pnl: float = None, notes: str = ""):
    try:
        execute(
            f"INSERT INTO journal_entries (user_id, ticker, notes, action, entry_price, exit_price, shares, pnl) "
            f"VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P})",
            (user_id, ticker, notes, action, entry_price, exit_price, shares, pnl),
        )
        if pnl is not None:
            from datetime import timedelta
            now = datetime.now()
            monday = now - timedelta(days=now.weekday())
            week = monday.strftime("%Y-%m-%d")
            if IS_POSTGRES:
                execute(
                    f"INSERT INTO weekly_goals (user_id, week_start, target_amount, actual_amount) VALUES ({P}, {P}, 0, {P}) "
                    f"ON CONFLICT(user_id, week_start) DO UPDATE SET actual_amount = weekly_goals.actual_amount + {P}",
                    (user_id, week, pnl, pnl),
                )
            else:
                execute(
                    f"INSERT INTO weekly_goals (user_id, week_start, target_amount, actual_amount) VALUES ({P}, {P}, 0, {P}) "
                    f"ON CONFLICT(user_id, week_start) DO UPDATE SET actual_amount = actual_amount + {P}",
                    (user_id, week, pnl, pnl),
                )
    except Exception as e:
        logger.warning(f"Failed to log to journal: {e}")


def _get_trade_performance(user_id) -> dict:
    try:
        cutoff = (datetime.now() - __import__("datetime").timedelta(days=7)).isoformat()
        rows = query(
            f"SELECT coin, strategy, pnl FROM bot_trades WHERE user_id = {P} AND status = 'closed' AND asset_type = 'stock' AND closed_at >= {P}",
            (user_id, cutoff),
        )
        if not rows:
            return {}

        total = len(rows)
        wins = sum(1 for r in rows if (r["pnl"] or 0) > 0)
        pnls = [r["pnl"] or 0 for r in rows]
        overall = {
            "total_trades": total,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "avg_pnl": round(sum(pnls) / total, 2) if total else 0,
            "total_pnl": round(sum(pnls), 2),
        }

        by_strategy = {}
        for r in rows:
            s = r["strategy"] or "unknown"
            by_strategy.setdefault(s, []).append(r["pnl"] or 0)
        strategy_stats = {}
        for s, p_list in by_strategy.items():
            sw = sum(1 for p in p_list if p > 0)
            strategy_stats[s] = {"trades": len(p_list), "win_rate": round(sw / len(p_list) * 100, 1), "avg_pnl": round(sum(p_list) / len(p_list), 2)}

        by_coin = {}
        for r in rows:
            c = r["coin"]
            by_coin.setdefault(c, []).append(r["pnl"] or 0)
        coin_stats = {}
        for c, p_list in by_coin.items():
            cw = sum(1 for p in p_list if p > 0)
            coin_stats[c] = {"trades": len(p_list), "win_rate": round(cw / len(p_list) * 100, 1), "avg_pnl": round(sum(p_list) / len(p_list), 2)}

        return {"overall": overall, "by_strategy": strategy_stats, "by_coin": coin_stats}
    except Exception as e:
        logger.warning(f"Failed to get stock trade performance: {e}")
        return {}


class StockTradingBot:
    """Autonomous stock trading bot — paper trading only (Alpaca/Webull)."""

    def __init__(self, user_id=None):
        self.user_id = user_id
        broker = _get_config(user_id, "stock_broker", "alpaca")
        self.client = get_broker_client(broker)
        self.broker_name = broker
        self.risk_manager = StockRiskManager(user_id=user_id)
        self._thread = None
        self._stop_event = threading.Event()
        self._running = False
        self._last_summary_hour = -1

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            _log(self.user_id, "warn", "Stock bot is already running")
            return

        self.risk_manager.deactivate_kill_switch()

        if IS_POSTGRES:
            execute(f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, 'stock_bot_enabled', '1') ON CONFLICT(user_id, key) DO UPDATE SET value = '1'", (self.user_id,))
        else:
            execute(f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, 'stock_bot_enabled', '1')", (self.user_id,))

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="stock-bot")
        self._thread.start()
        _log(self.user_id, "info", "Stock trading bot started")

    def stop(self):
        self._running = False
        self._stop_event.set()

        if IS_POSTGRES:
            execute(f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, 'stock_bot_enabled', '0') ON CONFLICT(user_id, key) DO UPDATE SET value = '0'", (self.user_id,))
        else:
            execute(f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, 'stock_bot_enabled', '0')", (self.user_id,))

        _log(self.user_id, "info", "Stock trading bot stopped")

    def kill(self):
        _log(self.user_id, "warn", "STOCK KILL SWITCH activated — stopping bot and closing all positions")
        self.stop()
        self.risk_manager.activate_kill_switch()

        try:
            results = self.client.close_all()
            for r in results:
                _log(self.user_id, "warn", f"Closed position: {r.get('coin', '?')} — success: {r.get('success')}")

            execute(
                f"UPDATE bot_trades SET status = 'killed', closed_at = {P} WHERE user_id = {P} AND status = 'open' AND asset_type = 'stock'",
                (datetime.now().isoformat(), self.user_id),
            )
        except Exception as e:
            _log(self.user_id, "error", f"Error during kill switch: {e}")

    def get_status(self) -> dict:
        balance = {"total_equity": 0, "available": 0, "unrealized_pnl": 0}
        positions = []

        try:
            if self.client.is_configured():
                balance = self.client.get_balance()
                positions = self.client.get_positions()
        except Exception:
            pass

        daily_pnl = self.risk_manager.get_daily_pnl()
        config = self.risk_manager.refresh_config()
        extended = config.get("stock_extended_hours", "0") == "1"
        market = is_market_open(extended_hours=extended)

        return {
            "running": self.is_running,
            "kill_switch": self.risk_manager.is_kill_switch_active(),
            "balance": balance, "positions": positions,
            "daily_pnl": daily_pnl,
            "open_trades": self.risk_manager.get_open_positions_count(),
            "config": config, "broker": self.broker_name,
            "broker_configured": self.client.is_configured(),
            "market": market,
        }

    def switch_broker(self, broker: str):
        if self.is_running:
            raise RuntimeError("Stop the bot before switching brokers")
        self.client = get_broker_client(broker)
        self.broker_name = broker
        _log(self.user_id, "info", f"Broker switched to {broker}")

    def _run_loop(self):
        _log(self.user_id, "info", f"Stock bot loop started (broker={self.broker_name})")

        while not self._stop_event.is_set():
            scan_interval = 300
            try:
                scan_interval = int(_get_config(self.user_id, "stock_scan_interval_sec", "300"))
                extended = _get_config(self.user_id, "stock_extended_hours", "0") == "1"
                market = is_market_open(extended_hours=extended)
                if not market["is_open"]:
                    _log(self.user_id, "info", f"Market closed ({market['status']}): {market['reason']} — skipping scan")
                else:
                    self._scan_cycle()
                self._log_hourly_summary()
            except Exception as e:
                _log(self.user_id, "error", f"Scan cycle error: {e}", str(e))

            self._stop_event.wait(timeout=scan_interval)

        self._log_hourly_summary(force=True)
        _log(self.user_id, "info", "Stock bot loop exited")

    def _log_hourly_summary(self, force=False):
        try:
            current_hour = datetime.now().hour
            if not force and current_hour == self._last_summary_hour:
                return
            self._last_summary_hour = current_hour

            perf = _get_trade_performance(self.user_id)
            if not perf or not perf.get("overall"):
                return

            o = perf["overall"]
            daily_pnl = self.risk_manager.get_daily_pnl()
            open_count = self.risk_manager.get_open_positions_count()
            market = is_market_open()

            summary = (
                f"[STOCK PERFORMANCE] "
                f"7d: {o['total_trades']} trades, {o['win_rate']}% win, "
                f"${o['total_pnl']:+.2f} total | "
                f"Today: ${daily_pnl:+.2f} | Open: {open_count} | "
                f"Market: {market['status']} | Broker: {self.broker_name}"
            )
            _log(self.user_id, "info", summary)
        except Exception:
            pass

    def _get_selected_stocks(self) -> list:
        raw = _get_config(self.user_id, "stock_selected_stocks", None)
        if raw:
            try:
                selected = json.loads(raw)
                return [s.upper().strip() for s in selected if s.strip()]
            except Exception:
                pass
        return list(DEFAULT_STOCKS)

    def _scan_cycle(self):
        if self.risk_manager.is_kill_switch_active():
            _log(self.user_id, "warn", "Kill switch active — skipping scan")
            return

        selected = self._get_selected_stocks()
        _log(self.user_id, "info", f"Starting stock scan cycle ({len(selected)} stocks)...")

        for stock_key in selected:
            stock_info = get_stock_info(stock_key)
            try:
                self._process_stock(stock_key, stock_info)
            except Exception as e:
                _log(self.user_id, "error", f"Error processing {stock_key}: {e}")

        self._check_exits()
        _log(self.user_id, "info", "Stock scan cycle complete")

    def _process_stock(self, stock_key: str, stock_info: dict):
        yf_ticker = stock_info["yf"]
        _log(self.user_id, "info", f"Scanning {stock_key}...")

        try:
            import yfinance as yf
            from analysis_engine import calculate_indicators
            ticker_data = yf.Ticker(yf_ticker)
            df = ticker_data.history(period="1mo", interval="1h")
            if df.empty or len(df) < 50:
                _log(self.user_id, "warn", f"{stock_key}: Insufficient data ({len(df)} bars)")
                return
            indicators = calculate_indicators(df)
        except Exception as e:
            _log(self.user_id, "error", f"Failed to fetch data for {stock_key}: {e}")
            return

        signal = self._generate_signal(stock_key, indicators, df)
        if not signal:
            rsi = indicators.get('rsi_14', '?')
            macd_h = indicators.get('macd_histogram', '?')
            price = float(df["Close"].iloc[-1])
            _log(self.user_id, "info", f"{stock_key}: No signal (RSI={rsi}, MACD_H={macd_h}, Price=${price:,.2f})")
            return

        _log(self.user_id, "info", f"{stock_key} signal: {signal['side']} — {signal['reason']}")

        direction_bias = _get_config(self.user_id, "stock_direction_bias", "long_only")
        if direction_bias == "long_only" and signal["side"] == "sell":
            _log(self.user_id, "info", f"{stock_key}: Signal is SELL but direction bias is Long Only — skipping")
            return
        if direction_bias == "short_only" and signal["side"] == "buy":
            _log(self.user_id, "info", f"{stock_key}: Signal is BUY but direction bias is Short Only — skipping")
            return

        current_price = float(df["Close"].iloc[-1])

        balance = self.client.get_balance()
        equity = balance.get("total_equity", 0)
        if equity <= 0:
            _log(self.user_id, "warn", f"No balance available (equity: {equity})")
            return

        max_pct = float(_get_config(self.user_id, "stock_max_position_pct", "10"))
        size_usd = equity * (max_pct / 100)
        qty = int(size_usd / current_price) if current_price > 0 else 0
        if qty < 1:
            _log(self.user_id, "warn", f"{stock_key}: Position too small (${size_usd:.2f} / ${current_price:.2f} = {qty} shares)")
            return

        actual_size_usd = qty * current_price

        risk_check = self.risk_manager.can_open_position(stock_key, actual_size_usd, equity, broker_client=self.client)
        if not risk_check["allowed"]:
            _log(self.user_id, "info", f"{stock_key}: Risk gate blocked — {risk_check['reason']}")
            return

        _log(self.user_id, "info", f"{stock_key}: Running LLM validation...")
        perf_history = _get_trade_performance(self.user_id)
        strategy_name = signal.get("strategy", "unknown")
        validation = validate_stock_trade(
            symbol=stock_key, side=signal["side"], price=current_price,
            indicators=indicators, signal_reason=signal["reason"],
            performance_history=perf_history, strategy_name=strategy_name,
        )

        if not validation["approved"]:
            _log(self.user_id, "info", f"{stock_key}: LLM validation rejected — {validation['summary']}")
            self._record_rejected_signal(stock_key, signal, current_price, validation)
            return

        _log(self.user_id, "info", f"{stock_key}: LLM approved — executing {signal['side']}")

        atr = indicators.get("atr_14", 0)
        min_atr = current_price * 0.005
        effective_atr = max(atr, min_atr)
        if signal["side"] == "buy":
            stop_loss = current_price - (1.5 * effective_atr)
            take_profit = current_price + (2.5 * effective_atr)
        else:
            stop_loss = current_price + (1.5 * effective_atr)
            take_profit = current_price - (2.5 * effective_atr)

        order_result = self.client.place_order(
            symbol=stock_key, side=signal["side"], qty=qty,
            stop_loss=round(stop_loss, 2), take_profit=round(take_profit, 2),
        )

        if order_result.get("success"):
            execute(
                f"INSERT INTO bot_trades (user_id, coin, side, size, entry_price, status, signal_reason, "
                f"validation_result, stop_loss, take_profit, blofin_order_id, strategy, asset_type, direction_bias) "
                f"VALUES ({P}, {P}, {P}, {P}, {P}, 'open', {P}, {P}, {P}, {P}, {P}, {P}, 'stock', {P})",
                (self.user_id, stock_key, signal["side"], qty, current_price,
                 signal["reason"], json.dumps(validation),
                 round(stop_loss, 2), round(take_profit, 2),
                 order_result.get("order_id", ""),
                 signal.get("strategy", "unknown"), direction_bias),
            )
            _log(self.user_id, "info", f"STOCK TRADE OPENED: {signal['side']} {qty} shares {stock_key} @ ${current_price:,.2f} | SL: ${stop_loss:,.2f} | TP: ${take_profit:,.2f}")
            _journal_log(self.user_id,
                ticker=stock_key, action="BUY" if signal["side"] == "buy" else "SELL",
                entry_price=current_price, shares=qty,
                notes=f"[Stock Bot] {signal['side'].upper()} — {signal['reason']}",
            )
        else:
            error_msg = order_result.get("error", "unknown")
            _log(self.user_id, "error", f"Order failed for {stock_key}: {error_msg}")

    def _generate_signal(self, stock_key: str, indicators: dict, df) -> dict:
        rsi = indicators.get("rsi_14", 50)
        macd_hist = indicators.get("macd_histogram", 0)
        macd_bull_cross = indicators.get("macd_bullish_cross", False)
        macd_bear_cross = indicators.get("macd_bearish_cross", False)
        sma8_above_ema20 = indicators.get("sma8_above_ema20", False)
        sma8_ema20_cross = indicators.get("sma8_ema20_cross", False)
        ema_20 = indicators.get("ema_20", 0)
        sma_50 = indicators.get("sma_50", 0)
        sma_200 = indicators.get("sma_200", 0)
        rel_vol = indicators.get("relative_volume", 0)
        atr = indicators.get("atr_14", 0)
        bb_upper = indicators.get("bb_upper", 0)
        bb_lower = indicators.get("bb_lower", 0)
        bb_width = indicators.get("bb_width", 0)
        sma_8 = indicators.get("sma_8", 0)
        current_price = float(df["Close"].iloc[-1])

        atr_pct = (atr / current_price * 100) if current_price > 0 else 0
        if atr_pct < 0.3:
            _log(self.user_id, "info", f"{stock_key}: ATR too low ({atr_pct:.2f}%) — ranging, skipping")
            return None

        macd_pct = abs(macd_hist) / current_price * 100 if current_price > 0 else 0

        try:
            last_trade = query_one(
                f"SELECT opened_at, side, pnl FROM bot_trades WHERE user_id = {P} AND coin = {P} AND asset_type = 'stock' ORDER BY id DESC LIMIT 1",
                (self.user_id, stock_key),
            )
            if last_trade:
                last_time = datetime.strptime(str(last_trade["opened_at"]), "%Y-%m-%d %H:%M:%S")
                minutes_since = (datetime.now() - last_time).total_seconds() / 60
                cooldown = 60 if (last_trade["pnl"] or 0) < 0 else 30
                if 0 < minutes_since < cooldown:
                    _log(self.user_id, "info", f"{stock_key}: Cooldown active ({minutes_since:.0f}/{cooldown} min)")
                    return None
        except Exception:
            pass

        if macd_bull_cross and macd_pct >= 0.05 and rsi < 60 and rsi > 30 and current_price > ema_20:
            return {"side": "buy", "reason": f"MACD bullish crossover (hist={macd_hist:.6f}, {macd_pct:.3f}%) + RSI={rsi:.1f} + price above EMA20", "strategy": "macd_cross"}
        if macd_bear_cross and macd_pct >= 0.05 and rsi > 40 and rsi < 70 and current_price < ema_20:
            return {"side": "sell", "reason": f"MACD bearish crossover (hist={macd_hist:.6f}, {macd_pct:.3f}%) + RSI={rsi:.1f} + price below EMA20", "strategy": "macd_cross"}

        if sma8_ema20_cross:
            if sma8_above_ema20 and rsi < 60 and rsi > 30:
                return {"side": "buy", "reason": f"SMA8 crossed above EMA20 (trend shift bullish) + RSI={rsi:.1f}", "strategy": "ema_trend"}
            if not sma8_above_ema20 and rsi > 40 and rsi < 70:
                return {"side": "sell", "reason": f"SMA8 crossed below EMA20 (trend shift bearish) + RSI={rsi:.1f}", "strategy": "ema_trend"}

        if rsi < 35 and macd_hist > 0 and macd_pct >= 0.03:
            return {"side": "buy", "reason": f"RSI oversold bounce ({rsi:.1f}) + MACD momentum positive ({macd_hist:.6f})", "strategy": "rsi_reversion"}
        if rsi > 70 and macd_hist < 0 and macd_pct >= 0.03 and current_price < sma_8:
            return {"side": "sell", "reason": f"RSI overbought fade ({rsi:.1f}) + MACD momentum negative ({macd_hist:.6f}) + price below SMA8", "strategy": "rsi_reversion"}

        if current_price > sma_50 and rel_vol > 1.5 and 55 < rsi < 70 and macd_hist > 0 and macd_pct >= 0.05:
            return {"side": "buy", "reason": f"Momentum breakout: price above SMA50 + volume surge ({rel_vol:.1f}x) + RSI={rsi:.1f}", "strategy": "momentum"}
        if current_price < sma_50 and rel_vol > 1.5 and 30 < rsi < 45 and macd_hist < 0 and macd_pct >= 0.05:
            return {"side": "sell", "reason": f"Bearish momentum: price below SMA50 + volume surge ({rel_vol:.1f}x) + RSI={rsi:.1f}", "strategy": "momentum"}

        if bb_lower > 0 and bb_upper > 0:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_position = (current_price - bb_lower) / bb_range
                if bb_position <= 0.10 and rsi < 40 and macd_hist > 0:
                    return {"side": "buy", "reason": f"BB lower band bounce (BB%={bb_position:.0%}, width={bb_width:.1f}%) + RSI={rsi:.1f}", "strategy": "bb_reversion"}
                if bb_position >= 0.90 and rsi > 65 and macd_hist < 0:
                    return {"side": "sell", "reason": f"BB upper band rejection (BB%={bb_position:.0%}, width={bb_width:.1f}%) + RSI={rsi:.1f}", "strategy": "bb_reversion"}

        if atr > 0 and sma_50 > 0:
            atr_dist = (current_price - sma_50) / atr
            if atr_dist <= -2.0 and rsi < 40 and sma_50 > sma_200:
                return {"side": "buy", "reason": f"Grid support: price {abs(atr_dist):.1f}x ATR below SMA50 (uptrend pullback) + RSI={rsi:.1f}", "strategy": "grid_reversion"}
            if atr_dist >= 2.0 and rsi > 65 and sma_50 < sma_200:
                return {"side": "sell", "reason": f"Grid resistance: price {atr_dist:.1f}x ATR above SMA50 (downtrend rally) + RSI={rsi:.1f}", "strategy": "grid_reversion"}

        if sma_50 > sma_200 and current_price > sma_200:
            ema20_dist_pct = abs(current_price - ema_20) / ema_20 * 100 if ema_20 > 0 else 999
            if ema20_dist_pct < 0.5 and rsi > 40 and rsi < 55 and macd_hist > 0:
                return {"side": "buy", "reason": f"Trend continuation: pullback to EMA20 (dist={ema20_dist_pct:.2f}%) in uptrend + RSI={rsi:.1f}", "strategy": "trend_dca"}
        if sma_50 < sma_200 and current_price < sma_200:
            ema20_dist_pct = abs(current_price - ema_20) / ema_20 * 100 if ema_20 > 0 else 999
            if ema20_dist_pct < 0.5 and rsi > 50 and rsi < 60 and macd_hist < 0:
                return {"side": "sell", "reason": f"Trend continuation: rally to EMA20 (dist={ema20_dist_pct:.2f}%) in downtrend + RSI={rsi:.1f}", "strategy": "trend_dca"}

        return None

    def _check_exits(self):
        open_trades = query(
            f"SELECT * FROM bot_trades WHERE user_id = {P} AND status = 'open' AND asset_type = 'stock'",
            (self.user_id,),
        )
        for trade in open_trades:
            try:
                current_price = self.client.get_ticker_price(trade["coin"])
                if current_price <= 0:
                    continue

                should_exit = False
                exit_reason = ""
                entry_price = trade["entry_price"]
                stop_loss = trade["stop_loss"]
                take_profit = trade["take_profit"]

                if trade["side"] == "buy":
                    pnl_pct = (current_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - current_price) / entry_price * 100

                if trade["side"] == "buy":
                    if current_price <= stop_loss:
                        should_exit, exit_reason = True, f"Stop loss hit (${stop_loss:,.2f})"
                    elif current_price >= take_profit:
                        should_exit, exit_reason = True, f"Take profit hit (${take_profit:,.2f})"
                else:
                    if current_price >= stop_loss:
                        should_exit, exit_reason = True, f"Stop loss hit (${stop_loss:,.2f})"
                    elif current_price <= take_profit:
                        should_exit, exit_reason = True, f"Take profit hit (${take_profit:,.2f})"

                if not should_exit and pnl_pct >= 1.5:
                    if trade["side"] == "buy":
                        retracement = (take_profit - current_price) / (take_profit - entry_price) if take_profit != entry_price else 0
                    else:
                        retracement = (current_price - take_profit) / (entry_price - take_profit) if entry_price != take_profit else 0
                    if retracement > 0.50 and pnl_pct > 0.5:
                        should_exit, exit_reason = True, f"Trailing stop: P&L {pnl_pct:.1f}%, retraced {retracement:.0%} from target"

                if not should_exit:
                    try:
                        opened_str = str(trade["opened_at"])
                        try:
                            opened = datetime.strptime(opened_str, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            opened = datetime.fromisoformat(opened_str)
                        hours_open = (datetime.now() - opened).total_seconds() / 3600
                        if hours_open >= 24 and abs(pnl_pct) < 0.3:
                            should_exit, exit_reason = True, f"Time exit: open {hours_open:.0f}h with only {pnl_pct:+.2f}% P&L (stale)"
                    except Exception:
                        pass

                if should_exit:
                    self._close_trade(trade, current_price, exit_reason)

            except Exception as e:
                _log(self.user_id, "error", f"Error checking exit for stock trade {trade['id']}: {e}")

    def _close_trade(self, trade, exit_price: float, reason: str):
        self.client.close_position(trade["coin"])

        entry_price = trade["entry_price"]
        size = trade["size"]
        if trade["side"] == "buy":
            pnl = (exit_price - entry_price) * size
        else:
            pnl = (entry_price - exit_price) * size

        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        if trade["side"] == "sell":
            pnl_pct = -pnl_pct

        execute(
            f"UPDATE bot_trades SET exit_price = {P}, pnl = {P}, pnl_pct = {P}, status = 'closed', closed_at = {P} WHERE id = {P}",
            (exit_price, round(pnl, 2), round(pnl_pct, 2), datetime.now().isoformat(), trade["id"]),
        )

        self.risk_manager.record_trade_pnl(round(pnl, 2))

        close_action = "SELL" if trade["side"] == "buy" else "BUY"
        _journal_log(self.user_id,
            ticker=trade["coin"], action=close_action,
            entry_price=entry_price, exit_price=exit_price,
            shares=size, pnl=round(pnl, 2),
            notes=f"[Stock Bot] Closed — {reason} | P&L: ${pnl:,.2f} ({pnl_pct:+.1f}%)",
        )

        _log(self.user_id, "info", f"STOCK TRADE CLOSED: {trade['coin']} {trade['side']} | Entry: ${entry_price:,.2f} -> Exit: ${exit_price:,.2f} | P&L: ${pnl:,.2f} ({pnl_pct:+.1f}%) | {reason}")

    def _record_rejected_signal(self, stock_key: str, signal: dict, price: float, validation: dict):
        _log(self.user_id, "info", f"Signal rejected: {signal['side']} {stock_key} @ ${price:,.2f} — {validation['summary']}",
             json.dumps({"signal": signal, "validation": validation}))


# Global bot instance
_stock_bot_instance = None
_stock_bot_lock = threading.Lock()


def get_stock_bot() -> StockTradingBot:
    global _stock_bot_instance
    with _stock_bot_lock:
        if _stock_bot_instance is None:
            _stock_bot_instance = StockTradingBot()
        return _stock_bot_instance
