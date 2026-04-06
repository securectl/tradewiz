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
from market_sensor import check_market_health

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
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        execute(
            f"INSERT INTO journal_entries (user_id, ticker, notes, action, entry_price, exit_price, shares, pnl, created_at) "
            f"VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}, {P})",
            (user_id, ticker, notes, action, entry_price, exit_price, shares, pnl, now),
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


def _load_user_keys(user_id, provider):
    """Load decrypted API keys for a user from user_api_keys table."""
    try:
        from crypto_utils import decrypt
        rows = query(
            f"SELECT key_name, encrypted_value FROM user_api_keys WHERE user_id = {P} AND provider = {P}",
            (user_id, provider),
        )
        return {r["key_name"]: decrypt(r["encrypted_value"]) for r in rows}
    except Exception as e:
        logger.warning(f"Failed to load user keys for provider {provider}: {e}")
        return {}


def _get_daily_goal_progress(user_id) -> dict:
    """Check progress toward $500 daily goal."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        row = query_one(
            f"SELECT COALESCE(SUM(pnl), 0) as total FROM bot_trades WHERE user_id = {P} AND status = 'closed' AND asset_type = 'stock' AND DATE(closed_at) = {P}",
            (user_id, today),
        )
        realized = float(row["total"]) if row else 0.0
        daily_goal = float(_get_config(user_id, "stock_daily_goal", "500"))
        return {"realized": realized, "goal": daily_goal, "remaining": daily_goal - realized, "pct": round(realized / daily_goal * 100, 1) if daily_goal > 0 else 0}
    except Exception:
        return {"realized": 0, "goal": 500, "remaining": 500, "pct": 0}


def _get_adaptive_params(user_id) -> dict:
    """Self-learning: adjust trading parameters based on recent performance."""
    perf = _get_trade_performance(user_id)
    params = {
        "position_scale": 1.0,
        "blacklisted_strategies": [],
        "hot_strategies": [],
        "confidence_threshold": 0.4,
    }
    if not perf or not perf.get("overall"):
        return params

    overall = perf["overall"]
    win_rate = overall.get("win_rate", 50)

    if win_rate >= 60:
        params["position_scale"] = 1.3
    elif win_rate >= 50:
        params["position_scale"] = 1.1
    elif win_rate < 35:
        params["position_scale"] = 0.7
    elif win_rate < 45:
        params["position_scale"] = 0.85

    for strat, stats in perf.get("by_strategy", {}).items():
        if stats["trades"] >= 5 and stats["win_rate"] < 20:
            params["blacklisted_strategies"].append(strat)
        elif stats["trades"] >= 3 and stats["win_rate"] >= 65:
            params["hot_strategies"].append(strat)

    if win_rate >= 55:
        params["confidence_threshold"] = 0.3
    elif win_rate < 35:
        params["confidence_threshold"] = 0.5

    return params


class StockTradingBot:
    """Autonomous stock trading bot — paper trading only (Alpaca/Webull)."""

    def __init__(self, user_id=None):
        self.user_id = user_id
        broker = _get_config(user_id, "stock_broker", "alpaca")
        # Load per-user keys for the selected broker
        if user_id:
            keys = _load_user_keys(user_id, broker)
            if broker == "alpaca" and keys:
                self.client = AlpacaClient(
                    api_key=keys.get("api_key"),
                    secret_key=keys.get("secret_key"),
                )
            elif broker == "webull" and keys:
                self.client = WebullClient(
                    app_key=keys.get("app_key"),
                    app_secret=keys.get("app_secret"),
                    account_id=keys.get("account_id"),
                )
            else:
                self.client = get_broker_client(broker)
        else:
            self.client = get_broker_client(broker)
        self.broker_name = broker
        self.risk_manager = StockRiskManager(user_id=user_id)
        self._thread = None
        self._stop_event = threading.Event()
        self._running = False
        self._last_summary_hour = -1
        self._last_sensor = None
        self._adaptive_params = {}
        self._last_adaptive_refresh = None
        self._kill_switch_time = None
        self._qullamaggie_hits = []
        self._poly_sentiment = None

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
        self._start_watchdog()

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

        goal = _get_daily_goal_progress(self.user_id)

        return {
            "running": self.is_running,
            "kill_switch": self.risk_manager.is_kill_switch_active(),
            "balance": balance, "positions": positions,
            "daily_pnl": daily_pnl,
            "open_trades": self.risk_manager.get_open_positions_count(),
            "config": config, "broker": self.broker_name,
            "broker_configured": self.client.is_configured(),
            "market": market,
            "market_sensor": self._last_sensor,
            "daily_goal": goal,
            "adaptive": self._adaptive_params,
        }

    def switch_broker(self, broker: str):
        if self.is_running:
            raise RuntimeError("Stop the bot before switching brokers")
        # Load per-user keys for the new broker
        if self.user_id:
            keys = _load_user_keys(self.user_id, broker)
            if broker == "alpaca" and keys:
                self.client = AlpacaClient(
                    api_key=keys.get("api_key"),
                    secret_key=keys.get("secret_key"),
                )
            elif broker == "webull" and keys:
                self.client = WebullClient(
                    app_key=keys.get("app_key"),
                    app_secret=keys.get("app_secret"),
                    account_id=keys.get("account_id"),
                )
            else:
                self.client = get_broker_client(broker)
        else:
            self.client = get_broker_client(broker)
        self.broker_name = broker
        _log(self.user_id, "info", f"Broker switched to {broker}")

    def _run_loop(self):
        try:
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
        except Exception as e:
            _log(self.user_id, "error", f"FATAL: Stock bot loop crashed — {e}", str(e))
            logger.exception("FATAL: Stock bot loop crashed")
        finally:
            self._running = False

    def _start_watchdog(self):
        """Daemon thread that restarts the bot loop if it dies unexpectedly."""
        import time
        def _watchdog():
            while True:
                time.sleep(60)
                if not self._running:
                    return  # Bot was intentionally stopped
                if self._thread is None or not self._thread.is_alive():
                    _log(self.user_id, "warn", "WATCHDOG: Stock bot thread died — restarting")
                    logger.warning("WATCHDOG: Stock bot thread died, restarting")
                    self._thread = threading.Thread(target=self._run_loop, daemon=True, name="stock-bot")
                    self._running = True
                    self._thread.start()
        t = threading.Thread(target=_watchdog, daemon=True, name="stock-bot-watchdog")
        t.start()

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

    def _refresh_adaptive(self):
        """Refresh adaptive parameters every 30 minutes."""
        now = datetime.now()
        if self._last_adaptive_refresh and (now - self._last_adaptive_refresh).total_seconds() < 1800:
            return
        self._adaptive_params = _get_adaptive_params(self.user_id)
        self._last_adaptive_refresh = now
        if self._adaptive_params.get("blacklisted_strategies"):
            _log(self.user_id, "info", f"Self-learning: blacklisted strategies: {self._adaptive_params['blacklisted_strategies']}")
        if self._adaptive_params.get("hot_strategies"):
            _log(self.user_id, "info", f"Self-learning: hot strategies: {self._adaptive_params['hot_strategies']}")
        _log(self.user_id, "info", f"Self-learning: position scale={self._adaptive_params['position_scale']:.2f}")

    def _self_heal(self):
        """Auto-recover from kill switch after 30 min cooldown."""
        if not self.risk_manager.is_kill_switch_active():
            return False
        if self._kill_switch_time is None:
            self._kill_switch_time = datetime.now()
            return True
        elapsed = (datetime.now() - self._kill_switch_time).total_seconds() / 60
        if elapsed >= 30:
            _log(self.user_id, "info", f"Self-healing: auto-recovering from kill switch after {elapsed:.0f} min cooldown")
            self.risk_manager.deactivate_kill_switch()
            self._kill_switch_time = None
            return False
        return True

    def _get_vix_level(self, sensor: dict) -> float:
        """Get current VIX level from sensor data or fetch directly."""
        # Try to get from sensor's key_indicators first (cached)
        try:
            indicators = sensor.get("key_indicators", {})
            if isinstance(indicators, dict):
                vix = indicators.get("vix")
                if vix and float(vix) > 0:
                    return float(vix)
            # Parse from reasoning string if structured
            reasoning = sensor.get("reasoning", "")
            if "VIX" in reasoning:
                import re
                m = re.search(r'VIX\s*[=:]?\s*(\d+\.?\d*)', reasoning)
                if m:
                    return float(m.group(1))
        except Exception:
            pass
        # Direct fetch as fallback
        try:
            import yfinance as yf
            vix_data = yf.Ticker("^VIX").history(period="1d")
            if not vix_data.empty:
                return float(vix_data["Close"].iloc[-1])
        except Exception as e:
            _log(self.user_id, "warn", f"Could not fetch VIX: {e}")
        return 20.0  # Default neutral

    def _scan_cycle(self):
        if self.risk_manager.is_kill_switch_active():
            if self._self_heal():
                _log(self.user_id, "warn", "Kill switch active — waiting for self-heal cooldown")
                return

        self._refresh_adaptive()

        # ── Daily goal & loss protection ──────────────────────────
        goal = _get_daily_goal_progress(self.user_id)
        daily_pnl = self.risk_manager.get_daily_pnl()

        # HARD STOP: If daily goal reached, stop opening new trades (protect profits)
        if goal["pct"] >= 100:
            _log(self.user_id, "info", f"DAILY GOAL REACHED: ${goal['realized']:.2f} / ${goal['goal']:.2f} ({goal['pct']}%) — no new trades, protecting profits")
            self._check_exits()
            return

        # HARD STOP: If daily loss exceeds 50% of goal, pause trading (capital protection)
        loss_threshold = -(goal["goal"] * 0.5)
        if daily_pnl < loss_threshold:
            _log(self.user_id, "warn", f"LOSS PROTECTION: Daily P&L ${daily_pnl:.2f} exceeds 50% of goal (${loss_threshold:.2f}) — pausing new trades")
            self._check_exits()
            return

        # SCALE DOWN: If losing today, reduce position sizes progressively
        if daily_pnl < 0:
            loss_ratio = abs(daily_pnl) / goal["goal"] if goal["goal"] > 0 else 0
            if loss_ratio > 0.25:
                _log(self.user_id, "info", f"Loss caution: daily P&L ${daily_pnl:.2f} ({loss_ratio:.0%} of goal) — reducing position sizes")

        # Market sensor pre-check
        sensor = check_market_health("stock")
        self._last_sensor = sensor

        # PANIC: market crash — auto-liquidate ALL positions to preserve capital
        if sensor["status"] == "PANIC":
            _log(self.user_id, "error", f"PANIC KILL SWITCH: {sensor['reasoning']}")
            _log(self.user_id, "error", "Auto-liquidating all stock positions to preserve capital!")
            try:
                results = self.client.close_all()
                for r in results:
                    _log(self.user_id, "warn", f"Panic close: {r.get('coin', '?')} — success: {r.get('success')}")
                execute(
                    f"UPDATE bot_trades SET status = 'panic_closed', closed_at = {P} WHERE user_id = {P} AND status = 'open' AND asset_type = 'stock'",
                    (datetime.now().isoformat(), self.user_id),
                )
                self.risk_manager.activate_kill_switch()
            except Exception as e:
                _log(self.user_id, "error", f"Panic liquidation failed: {e}")
            return

        if sensor["status"] == "DANGER":
            _log(self.user_id, "warn", f"Market sensor: DANGER — {sensor['reasoning']} — skipping entire scan cycle")
            return

        if sensor["status"] == "CAUTION":
            _log(self.user_id, "info", f"Market sensor: CAUTION — {sensor['reasoning']} — only high-confidence trades allowed")

        # ── VIX Regime Detection ────────────────────────────────────
        # VIX < 23 → cautious bull (long bias, moderate sizing)
        # VIX >= 23 → bear regime (shorts allowed, tighter stops, smaller size, stricter LLM gating)
        vix_level = self._get_vix_level(sensor)
        if vix_level < 23:
            vix_regime = "bull"
            _log(self.user_id, "info", f"VIX Regime: CAUTIOUS BULL (VIX={vix_level:.1f} < 23) — favoring long positions, moderate sizing")
        else:
            vix_regime = "bear"
            _log(self.user_id, "info", f"VIX Regime: BEAR (VIX={vix_level:.1f} >= 23) — defensive mode, tighter stops, stricter gating")

        # ── Prediction Market Sentiment ─────────────────────────────
        try:
            from prediction_markets import get_poly_sentiment_for_pulse
            self._poly_sentiment = get_poly_sentiment_for_pulse()
            poly_mood = self._poly_sentiment.get("mood", "—")
            _log(self.user_id, "info", f"Poly sentiment: {poly_mood} (score={self._poly_sentiment.get('score', 0)}) | VIX regime: {vix_regime}")
        except Exception as e:
            self._poly_sentiment = None
            _log(self.user_id, "warn", f"Could not fetch poly sentiment: {e}")

        _log(self.user_id, "info", f"Market sensor: {sensor['status']} (cached={sensor['cached']})")

        selected = self._get_selected_stocks()

        # ── Qullamaggie Scan (swing/hybrid mode) ─────────────────
        trade_mode = _get_config(self.user_id, "stock_trade_mode", "hybrid")
        if trade_mode in ("swing", "hybrid"):
            try:
                from analysis_engine import qullamaggie_scan
                qulla_results = qullamaggie_scan(selected)
                self._qullamaggie_hits = [r.get("ticker", r.get("symbol", "")) for r in qulla_results if r]
                if self._qullamaggie_hits:
                    _log(self.user_id, "info", f"Qullamaggie scan hits: {', '.join(self._qullamaggie_hits)}")
            except Exception as e:
                self._qullamaggie_hits = []
                _log(self.user_id, "warn", f"Qullamaggie scan failed: {e}")

        _log(self.user_id, "info", f"Starting stock scan cycle ({len(selected)} stocks, regime={vix_regime}, mode={trade_mode})...")

        for stock_key in selected:
            stock_info = get_stock_info(stock_key)
            try:
                self._process_stock(stock_key, stock_info, sensor=sensor, vix_regime=vix_regime, vix_level=vix_level)
            except Exception as e:
                _log(self.user_id, "error", f"Error processing {stock_key}: {e}")

        self._check_exits()
        _log(self.user_id, "info", "Stock scan cycle complete")

    def _process_stock(self, stock_key: str, stock_info: dict, sensor: dict = None, vix_regime: str = "bull", vix_level: float = 20.0):
        yf_ticker = stock_info["yf"]
        _log(self.user_id, "info", f"Scanning {stock_key}...")

        trade_mode = _get_config(self.user_id, "stock_trade_mode", "hybrid")
        signal = None
        is_swing = False

        try:
            import yfinance as yf
            from analysis_engine import calculate_indicators
            ticker_data = yf.Ticker(yf_ticker)

            # ── Swing signal (daily candles) ──────────────────────
            if trade_mode in ("swing", "hybrid"):
                try:
                    df_daily = ticker_data.history(period="3mo", interval="1d")
                    if df_daily is not None and len(df_daily) >= 50:
                        daily_indicators = calculate_indicators(df_daily)
                        swing_signal = self._generate_swing_signal(stock_key, daily_indicators, df_daily)
                        if swing_signal:
                            # Boost confidence if stock appeared in Qullamaggie scan
                            if stock_key in self._qullamaggie_hits:
                                swing_signal["reason"] += f" [Qullamaggie scan confirmed]"
                                _log(self.user_id, "info", f"{stock_key}: Swing signal boosted by Qullamaggie scan hit")

                            # Poly sentiment gate for swing entries
                            poly_mood = (self._poly_sentiment or {}).get("mood", "—")
                            if poly_mood.upper() == "BEARISH":
                                _log(self.user_id, "info", f"{stock_key}: Poly sentiment BEARISH — skipping swing entry (too risky for concentrated positions)")
                                swing_signal = None
                            elif poly_mood.upper() == "BULLISH":
                                _log(self.user_id, "info", f"{stock_key}: Poly sentiment BULLISH — full sizing on swing trade")

                            if swing_signal:
                                signal = swing_signal
                                is_swing = True
                except Exception as e:
                    _log(self.user_id, "warn", f"{stock_key}: Swing data fetch failed: {e}")

            # ── Scalp signal (hourly candles) ─────────────────────
            if signal is None and trade_mode in ("scalp", "hybrid"):
                df = ticker_data.history(period="1mo", interval="1h")
                if df.empty or len(df) < 50:
                    _log(self.user_id, "warn", f"{stock_key}: Insufficient hourly data ({len(df) if not df.empty else 0} bars)")
                    return
                indicators = calculate_indicators(df)
                signal = self._generate_signal(stock_key, indicators, df)

        except Exception as e:
            _log(self.user_id, "error", f"Failed to fetch data for {stock_key}: {e}")
            return

        if not signal:
            try:
                _indicators = daily_indicators if is_swing else (indicators if 'indicators' in dir() else {})
            except NameError:
                _indicators = {}
            rsi = _indicators.get('rsi_14', '?') if _indicators else '?'
            macd_h = _indicators.get('macd_histogram', '?') if _indicators else '?'
            _log(self.user_id, "info", f"{stock_key}: No signal (RSI={rsi}, MACD_H={macd_h})")
            return

        # For swing signals, set indicators/df from daily data for downstream use
        if is_swing:
            try:
                indicators = daily_indicators
                df = df_daily
            except NameError:
                pass

        # Self-learning: skip blacklisted strategies
        strategy_name = signal.get("strategy", "unknown")
        if strategy_name in self._adaptive_params.get("blacklisted_strategies", []):
            _log(self.user_id, "info", f"{stock_key}: Strategy '{strategy_name}' blacklisted by self-learning — skipping")
            return

        _log(self.user_id, "info", f"{stock_key} signal: {signal['side']} — {signal['reason']}")

        # ── VIX Regime Gating ───────────────────────────────────────
        # BULL regime (VIX < 23): cautious longs only, block shorts
        # BEAR regime (VIX >= 23): allow shorts, block weak buy signals, tighter RSI gates
        rsi = indicators.get("rsi_14", 50)
        if vix_regime == "bull":
            # Cautious bull: only long, reject buys if RSI already overbought
            if signal["side"] == "sell":
                _log(self.user_id, "info", f"{stock_key}: VIX BULL regime (VIX={vix_level:.1f}) — blocking SELL signal, bull market favors longs")
                return
            if rsi > 70:
                _log(self.user_id, "info", f"{stock_key}: VIX BULL regime — RSI too high ({rsi:.1f} > 70) for cautious entry, skipping")
                return
        elif vix_regime == "bear":
            # Bear regime: block buys unless strong momentum, tighten criteria
            if signal["side"] == "buy":
                # Only allow buys with very strong confluence in bear regime
                strategy = signal.get("strategy", "")
                if strategy not in ("momentum", "bb_reversion", "grid_reversion"):
                    _log(self.user_id, "info", f"{stock_key}: VIX BEAR regime (VIX={vix_level:.1f}) — blocking BUY '{strategy}' signal, only momentum/reversion buys allowed in bear market")
                    return
                if rsi > 55:
                    _log(self.user_id, "info", f"{stock_key}: VIX BEAR regime — RSI too high for buy ({rsi:.1f} > 55), skipping")
                    return
                rel_vol = indicators.get("relative_volume", 0)
                if rel_vol < 1.2:
                    _log(self.user_id, "info", f"{stock_key}: VIX BEAR regime — volume too low for buy ({rel_vol:.1f}x < 1.2x), skipping")
                    return
                _log(self.user_id, "info", f"{stock_key}: VIX BEAR regime — allowing strong BUY ({strategy}, RSI={rsi:.1f}, vol={rel_vol:.1f}x)")

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
        base_size = equity * (max_pct / 100)

        # Swing trades: 25% position size, max 3 swing positions
        if is_swing:
            base_size = equity * 0.25
            swing_open = query_one(
                f"SELECT COUNT(*) as cnt FROM bot_trades WHERE user_id = {P} AND status = 'open' AND asset_type = 'stock' AND strategy LIKE 'swing_%'",
                (self.user_id,),
            )
            swing_count = int(swing_open["cnt"]) if swing_open else 0
            if swing_count >= 3:
                _log(self.user_id, "info", f"{stock_key}: Max swing positions reached ({swing_count}/3) — skipping swing entry")
                return
            _log(self.user_id, "info", f"{stock_key}: Swing trade sizing — 25% equity (${base_size:.2f}), {swing_count}/3 swing positions open")

        # Self-learning: adaptive position sizing (scale down on cold streak, full size on hot)
        pos_scale = min(self._adaptive_params.get("position_scale", 1.0), 1.0)
        # VIX bear regime: reduce position size by 30% for capital protection
        if vix_regime == "bear":
            pos_scale *= 0.7
            _log(self.user_id, "info", f"{stock_key}: VIX BEAR regime — reducing position size to {pos_scale:.0%}")
        # Daily loss scaling: if losing today, reduce sizes to protect remaining capital
        daily_pnl = self.risk_manager.get_daily_pnl()
        goal_val = float(_get_config(self.user_id, "stock_daily_goal", "500"))
        if daily_pnl < 0 and goal_val > 0:
            loss_ratio = abs(daily_pnl) / goal_val
            if loss_ratio > 0.25:
                loss_scale = max(0.3, 1.0 - loss_ratio)  # scale down to 30% min
                pos_scale *= loss_scale
                _log(self.user_id, "info", f"{stock_key}: Loss protection — daily P&L ${daily_pnl:.2f}, scaling to {pos_scale:.0%}")
        size_usd = base_size * pos_scale if pos_scale < 1.0 else base_size
        qty = int(size_usd / current_price) if current_price > 0 else 0
        if qty < 1:
            _log(self.user_id, "warn", f"{stock_key}: Position too small (${size_usd:.2f} / ${current_price:.2f} = {qty} shares)")
            return

        actual_size_usd = qty * current_price

        risk_check = self.risk_manager.can_open_position(stock_key, actual_size_usd, equity, broker_client=self.client)
        if not risk_check["allowed"]:
            _log(self.user_id, "info", f"{stock_key}: Risk gate blocked — {risk_check['reason']}")
            return

        # Quick Trade Mode: bypass LLM gating for high-conviction signals
        quick_mode = _get_config(self.user_id, "stock_quick_trade_mode", "0") == "1"
        bypass_llm = False
        if quick_mode:
            rsi = indicators.get("rsi_14", 50)
            rel_vol = indicators.get("relative_volume", 0)
            strategy = signal.get("strategy", "")
            if rel_vol >= 1.5 and 30 <= rsi <= 60:
                bypass_llm = True
                _log(self.user_id, "info", f"{stock_key}: Quick Trade Mode — bypassing LLM (vol={rel_vol:.1f}x, RSI={rsi:.1f})")
            elif strategy == "momentum":
                bypass_llm = True
                _log(self.user_id, "info", f"{stock_key}: Quick Trade Mode — bypassing LLM (momentum strategy)")

        if bypass_llm:
            validation = {"approved": True, "summary": "Quick Trade Mode — LLM bypassed", "confidence": 0.7}
        else:
            # Check rate limit before LLM validation
            try:
                from rate_limiter import check_rate_limit as _check_rl, set_llm_user
                rl = _check_rl(self.user_id)
                if not rl["allowed"]:
                    _log(self.user_id, "info", f"{stock_key}: Rate limit reached ({rl['used']}/{rl['limit']}) — skipping LLM validation")
                    return
                set_llm_user(self.user_id, "bot_trade")
            except Exception:
                pass

            _log(self.user_id, "info", f"{stock_key}: Running LLM validation...")
            perf_history = _get_trade_performance(self.user_id)
            strategy_name = signal.get("strategy", "unknown")

            # Enrich signal reason with VIX context for LLM awareness
            enriched_reason = signal["reason"]
            if vix_regime == "bear":
                enriched_reason += f" [VIX BEAR REGIME: VIX={vix_level:.1f} — elevated fear, require extra confluence, prefer defensive plays]"
            else:
                enriched_reason += f" [VIX BULL REGIME: VIX={vix_level:.1f} — low fear, cautious longs favored]"

            validation = validate_stock_trade(
                symbol=stock_key, side=signal["side"], price=current_price,
                indicators=indicators, signal_reason=enriched_reason,
                performance_history=perf_history, strategy_name=strategy_name,
            )

            if not validation["approved"]:
                _log(self.user_id, "info", f"{stock_key}: LLM validation rejected — {validation['summary']}")
                self._record_rejected_signal(stock_key, signal, current_price, validation)
                return

            # VIX-aware confidence thresholds
            confidence = validation.get("confidence", 0)
            if vix_regime == "bear":
                # Bear regime: require higher confidence (experienced analyst gate)
                bear_threshold = 0.55
                if confidence < bear_threshold:
                    _log(self.user_id, "info", f"{stock_key}: VIX BEAR regime — LLM confidence {confidence:.0%} < {bear_threshold:.0%} bear threshold — too risky, skipping")
                    return

            # CAUTION mode: adaptive confidence threshold (stacks with VIX gate)
            if sensor and sensor.get("status") == "CAUTION":
                threshold = self._adaptive_params.get("confidence_threshold", 0.4)
                if confidence < threshold:
                    _log(self.user_id, "info", f"{stock_key}: Market CAUTION — LLM confidence {confidence:.0%} < {threshold:.0%} threshold — skipping")
                    return

        _log(self.user_id, "info", f"{stock_key}: LLM approved — executing {signal['side']}")

        atr = indicators.get("atr_14", 0)
        min_atr = current_price * 0.005
        effective_atr = max(atr, min_atr)

        # Swing trades: 8% stop loss, 25% take profit target (wider than scalp)
        if is_swing:
            if signal["side"] == "buy":
                stop_loss = current_price * 0.92   # 8% stop loss
                take_profit = current_price * 1.25  # 25% take profit
            else:
                stop_loss = current_price * 1.08
                take_profit = current_price * 0.75
        else:
            # VIX-aware SL/TP: tighter stops in bear regime, wider targets in bull
            if vix_regime == "bear":
                sl_mult = 1.2   # tighter stop — protect capital
                tp_mult = 2.0   # modest target — take profits quick
            else:
                sl_mult = 1.5   # standard stop
                tp_mult = 2.5   # standard target

            if signal["side"] == "buy":
                stop_loss = current_price - (sl_mult * effective_atr)
                take_profit = current_price + (tp_mult * effective_atr)
            else:
                stop_loss = current_price + (sl_mult * effective_atr)
                take_profit = current_price - (tp_mult * effective_atr)

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
        if atr_pct < 0.08:
            _log(self.user_id, "info", f"{stock_key}: ATR too low ({atr_pct:.2f}%) — ranging, skipping")
            return None

        macd_pct = abs(macd_hist) / current_price * 100 if current_price > 0 else 0

        try:
            last_trade = query_one(
                f"SELECT opened_at, side, pnl FROM bot_trades WHERE user_id = {P} AND coin = {P} AND asset_type = 'stock' ORDER BY id DESC LIMIT 1",
                (self.user_id, stock_key),
            )
            if last_trade:
                try:
                    last_time = datetime.strptime(str(last_trade["opened_at"]), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    last_time = datetime.fromisoformat(str(last_trade["opened_at"]))
                minutes_since = (datetime.now() - last_time).total_seconds() / 60
                cooldown = 15 if (last_trade["pnl"] or 0) < 0 else 8
                if 0 < minutes_since < cooldown:
                    _log(self.user_id, "info", f"{stock_key}: Cooldown active ({minutes_since:.0f}/{cooldown} min)")
                    return None
        except Exception:
            pass

        if macd_bull_cross and macd_pct >= 0.02 and rsi < 65 and rsi > 25 and current_price > ema_20:
            return {"side": "buy", "reason": f"MACD bullish crossover (hist={macd_hist:.6f}, {macd_pct:.3f}%) + RSI={rsi:.1f} + price above EMA20", "strategy": "macd_cross"}
        if macd_bear_cross and macd_pct >= 0.02 and rsi > 35 and rsi < 75 and current_price < ema_20:
            return {"side": "sell", "reason": f"MACD bearish crossover (hist={macd_hist:.6f}, {macd_pct:.3f}%) + RSI={rsi:.1f} + price below EMA20", "strategy": "macd_cross"}

        if sma8_ema20_cross:
            if sma8_above_ema20 and rsi < 68 and rsi > 25:
                return {"side": "buy", "reason": f"SMA8 crossed above EMA20 (trend shift bullish) + RSI={rsi:.1f}", "strategy": "ema_trend"}
            if not sma8_above_ema20 and rsi > 32 and rsi < 75:
                return {"side": "sell", "reason": f"SMA8 crossed below EMA20 (trend shift bearish) + RSI={rsi:.1f}", "strategy": "ema_trend"}

        if rsi < 40 and macd_hist > 0:
            return {"side": "buy", "reason": f"RSI oversold bounce ({rsi:.1f}) + MACD momentum positive ({macd_hist:.6f})", "strategy": "rsi_reversion"}
        if rsi > 62 and macd_hist < 0 and current_price < sma_8:
            return {"side": "sell", "reason": f"RSI overbought fade ({rsi:.1f}) + MACD momentum negative ({macd_hist:.6f}) + price below SMA8", "strategy": "rsi_reversion"}

        if current_price > sma_50 and rel_vol > 1.0 and 45 < rsi < 78 and macd_hist > 0:
            return {"side": "buy", "reason": f"Momentum breakout: price above SMA50 + volume surge ({rel_vol:.1f}x) + RSI={rsi:.1f}", "strategy": "momentum"}
        if current_price < sma_50 and rel_vol > 1.0 and 22 < rsi < 55 and macd_hist < 0:
            return {"side": "sell", "reason": f"Bearish momentum: price below SMA50 + volume surge ({rel_vol:.1f}x) + RSI={rsi:.1f}", "strategy": "momentum"}

        if bb_lower > 0 and bb_upper > 0:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_position = (current_price - bb_lower) / bb_range
                if bb_position <= 0.15 and rsi < 42 and macd_hist > 0:
                    return {"side": "buy", "reason": f"BB lower band bounce (BB%={bb_position:.0%}, width={bb_width:.1f}%) + RSI={rsi:.1f}", "strategy": "bb_reversion"}
                if bb_position >= 0.85 and rsi > 60 and macd_hist < 0:
                    return {"side": "sell", "reason": f"BB upper band rejection (BB%={bb_position:.0%}, width={bb_width:.1f}%) + RSI={rsi:.1f}", "strategy": "bb_reversion"}

        if atr > 0 and sma_50 > 0:
            atr_dist = (current_price - sma_50) / atr
            if atr_dist <= -1.5 and rsi < 45 and sma_50 > sma_200:
                return {"side": "buy", "reason": f"Grid support: price {abs(atr_dist):.1f}x ATR below SMA50 (uptrend pullback) + RSI={rsi:.1f}", "strategy": "grid_reversion"}
            if atr_dist >= 1.5 and rsi > 58 and sma_50 < sma_200:
                return {"side": "sell", "reason": f"Grid resistance: price {atr_dist:.1f}x ATR above SMA50 (downtrend rally) + RSI={rsi:.1f}", "strategy": "grid_reversion"}

        if sma_50 > sma_200 and current_price > sma_200:
            ema20_dist_pct = abs(current_price - ema_20) / ema_20 * 100 if ema_20 > 0 else 999
            if ema20_dist_pct < 0.8 and rsi > 35 and rsi < 60 and macd_hist > 0:
                return {"side": "buy", "reason": f"Trend continuation: pullback to EMA20 (dist={ema20_dist_pct:.2f}%) in uptrend + RSI={rsi:.1f}", "strategy": "trend_dca"}
        if sma_50 < sma_200 and current_price < sma_200:
            ema20_dist_pct = abs(current_price - ema_20) / ema_20 * 100 if ema_20 > 0 else 999
            if ema20_dist_pct < 0.8 and rsi > 45 and rsi < 65 and macd_hist < 0:
                return {"side": "sell", "reason": f"Trend continuation: rally to EMA20 (dist={ema20_dist_pct:.2f}%) in downtrend + RSI={rsi:.1f}", "strategy": "trend_dca"}

        # --- Strategy 8: Doji Reversal ---
        if len(df) >= 2:
            c_open = float(df["Open"].iloc[-1])
            c_high = float(df["High"].iloc[-1])
            c_low = float(df["Low"].iloc[-1])
            c_range = c_high - c_low
            c_body = abs(current_price - c_open)
            if c_range > 0 and c_body < c_range * 0.10:
                prev_open = float(df["Open"].iloc[-2])
                prev_close = float(df["Close"].iloc[-2])
                if rsi < 40 and current_price < ema_20 and prev_close < prev_open:
                    return {"side": "buy", "reason": f"Doji reversal: doji candle + RSI={rsi:.1f} + price below EMA20 + prior bearish candle", "strategy": "doji_reversal"}
                if rsi > 60 and current_price > ema_20 and prev_close > prev_open:
                    return {"side": "sell", "reason": f"Doji reversal: doji candle + RSI={rsi:.1f} + price above EMA20 + prior bullish candle", "strategy": "doji_reversal"}

        # --- Strategy 9: Pump on Close ---
        if len(df) >= 2 and atr > 0:
            c_open = float(df["Open"].iloc[-1])
            c_high = float(df["High"].iloc[-1])
            c_low = float(df["Low"].iloc[-1])
            c_range = c_high - c_low
            c_body = abs(current_price - c_open)
            if rel_vol >= 1.4 and c_body > atr * 0.4 and c_range > 0:
                close_pos = (current_price - c_low) / c_range
                if close_pos >= 0.75 and rsi < 70 and current_price > sma_50:
                    return {"side": "buy", "reason": f"Pump on close: vol surge ({rel_vol:.1f}x) + large body + close in upper 25% + RSI={rsi:.1f} + above SMA50", "strategy": "pump_on_close"}
                if close_pos <= 0.25 and rsi > 30 and current_price < sma_50:
                    return {"side": "sell", "reason": f"Dump on close: vol surge ({rel_vol:.1f}x) + large body + close in lower 25% + RSI={rsi:.1f} + below SMA50", "strategy": "pump_on_close"}

        return None

    def _generate_swing_signal(self, stock_key, indicators, df):
        """Generate swing trade signals from daily candles (VCP, HTF, Breakout, Earnings Gap, Trend Pullback)."""
        if df is None or len(df) < 50:
            return None

        try:
            import numpy as np
            current_price = float(df["Close"].iloc[-1])
            close = df["Close"].values
            high = df["High"].values
            low = df["Low"].values
            volume = df["Volume"].values
            n = len(df)

            # Compute daily indicators
            sma_10 = float(np.mean(close[-10:])) if n >= 10 else 0
            sma_20 = float(np.mean(close[-20:])) if n >= 20 else 0
            sma_50 = float(np.mean(close[-50:])) if n >= 50 else 0
            sma_200 = float(np.mean(close[-200:])) if n >= 200 else float(np.mean(close))
            # EMA 20
            ema_20 = float(df["Close"].ewm(span=20).mean().iloc[-1]) if n >= 20 else 0
            avg_vol = float(np.mean(volume[-20:])) if n >= 20 else float(np.mean(volume))
            current_vol = float(volume[-1])
            rel_vol = current_vol / avg_vol if avg_vol > 0 else 0

            # RSI 14 on daily
            deltas = np.diff(close[-15:]) if n >= 15 else np.diff(close)
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            avg_gain = np.mean(gains) if len(gains) > 0 else 0
            avg_loss = np.mean(losses) if len(losses) > 0 else 0.001
            rs = avg_gain / avg_loss if avg_loss > 0 else 100
            rsi = 100 - (100 / (1 + rs))

            # 20-day high
            high_20 = float(np.max(high[-20:])) if n >= 20 else float(np.max(high))

            # --- 1. VCP (Volatility Contraction Pattern) ---
            # Price within 3% of 10-day SMA, range contracting, volume declining
            if sma_10 > 0:
                dist_to_sma10 = abs(current_price - sma_10) / sma_10
                if dist_to_sma10 < 0.03 and n >= 20:
                    # Range contracting: last 5-day range < prior 10-day range
                    recent_range = float(np.max(high[-5:]) - np.min(low[-5:]))
                    prior_range = float(np.max(high[-15:-5]) - np.min(low[-15:-5]))
                    # Volume declining: last 5-day avg vol < prior 10-day avg vol
                    recent_vol = float(np.mean(volume[-5:]))
                    prior_vol = float(np.mean(volume[-15:-5]))
                    if prior_range > 0 and recent_range < prior_range * 0.75 and recent_vol < prior_vol * 0.85:
                        return {
                            "side": "buy",
                            "reason": f"VCP: price within {dist_to_sma10:.1%} of SMA10, range contracting ({recent_range:.2f} vs {prior_range:.2f}), volume declining ({recent_vol:.0f} vs {prior_vol:.0f})",
                            "strategy": "swing_vcp",
                            "timeframe": "daily",
                        }

            # --- 2. HTF (High Tight Flag) ---
            # 30%+ run from recent low, pullback < 15%
            if n >= 30:
                low_30 = float(np.min(low[-30:]))
                high_since_low = float(np.max(high[-30:]))
                run_pct = (high_since_low - low_30) / low_30 * 100 if low_30 > 0 else 0
                pullback_pct = (high_since_low - current_price) / high_since_low * 100 if high_since_low > 0 else 0
                if run_pct >= 30 and pullback_pct < 15 and pullback_pct > 1:
                    return {
                        "side": "buy",
                        "reason": f"HTF: {run_pct:.0f}% run from 30d low, pullback only {pullback_pct:.1f}% from high (tight consolidation)",
                        "strategy": "swing_htf",
                        "timeframe": "daily",
                    }

            # --- 3. Breakout ---
            # Price above 20-day high with 1.5x avg volume
            if current_price > high_20 * 0.998 and rel_vol >= 1.5:
                return {
                    "side": "buy",
                    "reason": f"Breakout: price ${current_price:.2f} above 20d high ${high_20:.2f} with {rel_vol:.1f}x avg volume",
                    "strategy": "swing_breakout",
                    "timeframe": "daily",
                }

            # --- 4. Earnings Gap ---
            # Stock gapped up 5%+ recently (within last 5 days) with volume surge
            for i in range(1, min(6, n)):
                prev_close = float(close[-(i+1)])
                day_open = float(df["Open"].iloc[-i])
                day_vol = float(volume[-i])
                if prev_close > 0:
                    gap_pct = (day_open - prev_close) / prev_close * 100
                    if gap_pct >= 5 and day_vol > avg_vol * 2.0:
                        # Post-earnings momentum: price holding above gap level
                        gap_level = day_open
                        if current_price >= gap_level * 0.97:
                            return {
                                "side": "buy",
                                "reason": f"Earnings Gap: {gap_pct:.1f}% gap up {i}d ago with {day_vol/avg_vol:.1f}x volume, price holding above gap level",
                                "strategy": "swing_earnings",
                                "timeframe": "daily",
                            }

            # --- 5. Trend Pullback ---
            # Pullback to rising 20 EMA, SMA50 > SMA200, RSI 40-60
            if ema_20 > 0 and sma_50 > sma_200:
                dist_to_ema20 = abs(current_price - ema_20) / ema_20
                # Check EMA is rising (current > 5 bars ago)
                ema_20_prev = float(df["Close"].ewm(span=20).mean().iloc[-6]) if n >= 25 else ema_20
                ema_rising = ema_20 > ema_20_prev
                if dist_to_ema20 < 0.02 and ema_rising and 40 <= rsi <= 60:
                    return {
                        "side": "buy",
                        "reason": f"Trend Pullback: price within {dist_to_ema20:.1%} of rising EMA20, SMA50 > SMA200, RSI={rsi:.1f}",
                        "strategy": "swing_trend",
                        "timeframe": "daily",
                    }

        except Exception as e:
            _log(self.user_id, "warn", f"{stock_key}: Swing signal error: {e}")

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

                strategy = trade.get("strategy", "") or ""
                is_swing_trade = strategy.startswith("swing_")

                # Swing trades: trailing stop locks 50% of max profit
                if not should_exit and is_swing_trade and pnl_pct > 0:
                    # For swing trades, use a trailing stop that locks in 50% of max profit
                    # Max profit approximated: the best pnl_pct so far is at least current pnl_pct
                    # We trail at 50% of current profit — if price retraces more than half, exit
                    trail_floor_pct = pnl_pct * 0.50
                    if pnl_pct >= 3.0:
                        # Once 3%+ profit on swing, lock 50% via trailing
                        if trade["side"] == "buy":
                            trailing_stop_price = entry_price * (1 + trail_floor_pct / 100)
                            if current_price <= trailing_stop_price:
                                should_exit, exit_reason = True, f"Swing trailing stop: locking {trail_floor_pct:.1f}% of {pnl_pct:.1f}% max profit"
                        else:
                            trailing_stop_price = entry_price * (1 - trail_floor_pct / 100)
                            if current_price >= trailing_stop_price:
                                should_exit, exit_reason = True, f"Swing trailing stop: locking {trail_floor_pct:.1f}% of {pnl_pct:.1f}% max profit"

                # Scalp trailing stop (original logic)
                if not should_exit and not is_swing_trade and pnl_pct >= 1.5:
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
                        days_open = hours_open / 24

                        # Swing trades: time exit at 14 days
                        if is_swing_trade:
                            if days_open >= 14:
                                should_exit, exit_reason = True, f"Swing time exit: open {days_open:.0f} days (max 14d), P&L {pnl_pct:+.1f}%"
                        else:
                            # Scalp: original 24h stale exit
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
            gross_pnl = (exit_price - entry_price) * size
        else:
            gross_pnl = (entry_price - exit_price) * size

        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        if trade["side"] == "sell":
            pnl_pct = -pnl_pct

        # Trading fees: Alpaca $0 commission but SEC/FINRA/TAF fees apply (~$0.01/share)
        # Webull: similar fee structure
        fee = round(size * 0.01, 4)

        # NET P&L = gross minus fees
        pnl = round(gross_pnl - fee, 2)

        execute(
            f"UPDATE bot_trades SET exit_price = {P}, pnl = {P}, pnl_pct = {P}, fee = {P}, status = 'closed', closed_at = {P} WHERE id = {P}",
            (exit_price, pnl, round(pnl_pct, 2), fee, datetime.now().isoformat(), trade["id"]),
        )

        self.risk_manager.record_trade_pnl(pnl)

        _log(self.user_id, "info",
             f"STOCK TRADE CLOSED: {trade['coin']} {trade['side']} | Entry: ${entry_price:,.2f} → Exit: ${exit_price:,.2f} | "
             f"Gross: ${gross_pnl:,.2f} | Fee: ${fee:,.2f} | Net P&L: ${pnl:,.2f} ({pnl_pct:+.1f}%) | {reason}")

        close_action = "SELL" if trade["side"] == "buy" else "BUY"
        _journal_log(self.user_id,
            ticker=trade["coin"], action=close_action,
            entry_price=entry_price, exit_price=exit_price,
            shares=size, pnl=pnl,
            notes=f"[Stock Bot] Closed — {reason} | Gross: ${gross_pnl:,.2f} | Fee: ${fee:,.2f} | Net: ${pnl:,.2f} ({pnl_pct:+.1f}%)",
        )

        _log(self.user_id, "info", f"STOCK TRADE CLOSED: {trade['coin']} {trade['side']} | Entry: ${entry_price:,.2f} -> Exit: ${exit_price:,.2f} | P&L: ${pnl:,.2f} ({pnl_pct:+.1f}%) | Fee: ${fee:,.2f} | {reason}")

    def _record_rejected_signal(self, stock_key: str, signal: dict, price: float, validation: dict):
        _log(self.user_id, "info", f"Signal rejected: {signal['side']} {stock_key} @ ${price:,.2f} — {validation['summary']}",
             json.dumps({"signal": signal, "validation": validation}))


# Per-user bot instances keyed by user_id
_stock_bot_instances = {}
_stock_bot_lock = threading.Lock()


def get_stock_bot(user_id=None) -> StockTradingBot:
    with _stock_bot_lock:
        if user_id in _stock_bot_instances:
            existing = _stock_bot_instances[user_id]
            # Check if broker config changed since bot was created
            current_broker = _get_config(user_id, "stock_broker", "alpaca")
            if existing.broker_name != current_broker and not existing.is_running:
                logger.info(f"Broker changed from {existing.broker_name} to {current_broker}, rebuilding bot")
                _stock_bot_instances[user_id] = StockTradingBot(user_id=user_id)
        if user_id not in _stock_bot_instances:
            _stock_bot_instances[user_id] = StockTradingBot(user_id=user_id)
        return _stock_bot_instances[user_id]
