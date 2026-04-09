"""
Core Trading Bot Engine — Background daemon thread with 5-min scan cycle.
Fetches data via yfinance, generates signals, validates via multi-LLM, executes on BloFin demo.
"""

import os
import json
import logging
import threading
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db import get_db, put_db, IS_POSTGRES, query, query_one, execute
from crypto_bot.blofin_client import BloFinClient, COIN_MAP, DEFAULT_COINS, ensure_coin_in_map
from crypto_bot.risk_manager import RiskManager
from crypto_bot.crypto_validator import validate_trade, detect_direction
from market_sensor import check_market_health

P = "%s" if IS_POSTGRES else "?"


def _get_config(user_id, key, default=None):
    row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = {P}", (user_id, key))
    return row["value"] if row else default


def _log(user_id, level: str, message: str, details: str = None):
    """Write to bot_log table."""
    try:
        execute(
            f"INSERT INTO bot_log (user_id, level, message, details) VALUES ({P}, {P}, {P}, {P})",
            (user_id, level, message, details),
        )
    except Exception:
        pass
    log_fn = getattr(logger, level, logger.info)
    log_fn(f"[BOT] {message}")


def _journal_log(user_id, ticker: str, action: str, entry_price: float = None,
                  exit_price: float = None, shares: float = None,
                  pnl: float = None, notes: str = ""):
    """Log a trade to the Tracker journal for visibility."""
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
    """Query last 7 days of closed trades and return performance stats."""
    try:
        cutoff = (datetime.now() - __import__("datetime").timedelta(days=7)).isoformat()
        rows = query(
            f"SELECT coin, strategy, pnl FROM bot_trades WHERE user_id = {P} AND status = 'closed' AND closed_at >= {P}",
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
        logger.warning(f"Failed to get trade performance: {e}")
        return {}


def _is_user_admin(user_id):
    """Check if user has admin role."""
    try:
        row = query_one(f"SELECT role FROM user_roles WHERE user_id = {P} AND role = 'admin'", (user_id,))
        return row is not None
    except Exception:
        return False


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
            f"SELECT COALESCE(SUM(pnl), 0) as total FROM bot_trades WHERE user_id = {P} AND status = 'closed' AND asset_type = 'crypto' AND DATE(closed_at) = {P}",
            (user_id, today),
        )
        realized = float(row["total"]) if row else 0.0
        daily_goal = float(_get_config(user_id, "daily_goal", "500"))
        return {"realized": realized, "goal": daily_goal, "remaining": daily_goal - realized, "pct": round(realized / daily_goal * 100, 1) if daily_goal > 0 else 0}
    except Exception:
        return {"realized": 0, "goal": 500, "remaining": 500, "pct": 0}


def _get_adaptive_params(user_id) -> dict:
    """Self-learning: adjust trading parameters based on recent performance.

    Uses statistical significance (not just raw win rate) to avoid overfit:
    - Blacklist requires min 6 trades AND negative net P&L AND <35% win rate
    - Hot strategy requires min 8 trades AND positive net P&L AND >55% win rate
    - Position sizing bounded to [0.7, 1.0] — never scale UP (overfit trap)
    - Uses 14-day rolling window (not all-time) to respond to regime changes
    """
    params = {
        "position_scale": 1.0,
        "blacklisted_strategies": [],
        "hot_strategies": [],
        "cautious_coins": [],     # Coins with consistently negative P&L — reduce size
        "confidence_threshold": 0.45,
    }

    try:
        # Use 14-day rolling window — responsive to regime changes, avoids stale data
        rows = query(
            f"SELECT strategy, coin, pnl, fee FROM bot_trades "
            f"WHERE user_id = {P} AND status = 'closed' AND asset_type = 'crypto' "
            f"AND closed_at >= NOW() - INTERVAL '14 days' ORDER BY closed_at",
            (user_id,),
        )
        if not rows or len(rows) < 5:
            return params

        all_pnls = [float(r["pnl"] or 0) for r in rows]
        total_trades = len(all_pnls)
        total_wins = sum(1 for p in all_pnls if p > 0)
        win_rate = total_wins / total_trades * 100

        # Position sizing: bounded [0.7, 1.0] — never scale UP to prevent overfit amplification
        # Scale down on losing streaks to protect capital
        if win_rate < 35:
            params["position_scale"] = 0.7
        elif win_rate < 45:
            params["position_scale"] = 0.85
        else:
            params["position_scale"] = 1.0  # No scale-up; 1.0 is max

        # Strategy-level learning with statistical significance
        swing_strategies = {"swing_vcp", "swing_htf", "swing_breakout", "swing_trend"}
        by_strat = {}
        for r in rows:
            s = r["strategy"] or "unknown"
            by_strat.setdefault(s, []).append(float(r["pnl"] or 0))

        for strat, pnls in by_strat.items():
            if strat in swing_strategies:
                continue
            n = len(pnls)
            net = sum(pnls)
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / n * 100 if n > 0 else 0

            # Blacklist: min 6 trades + WR < 35% + net negative
            # This is stricter than raw WR alone — requires both frequency AND consistent loss
            if n >= 6 and wr < 35 and net < 0:
                params["blacklisted_strategies"].append(strat)
            # Hot: min 8 trades + WR > 55% + net positive (high bar to avoid false positives)
            elif n >= 8 and wr > 55 and net > 0:
                params["hot_strategies"].append(strat)

        # Coin-level learning: flag coins with consistent losses (min 5 trades + WR<35% + net negative)
        by_coin = {}
        for r in rows:
            c = r["coin"]
            by_coin.setdefault(c, []).append(float(r["pnl"] or 0))

        for coin, pnls in by_coin.items():
            n = len(pnls)
            net = sum(pnls)
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / n * 100 if n > 0 else 0
            if n >= 5 and wr < 35 and net < 0:
                params["cautious_coins"].append(coin)

        # Confidence threshold: raise when cold, but bounded to [0.40, 0.55]
        if win_rate >= 55:
            params["confidence_threshold"] = 0.40
        elif win_rate < 35:
            params["confidence_threshold"] = 0.55
        else:
            params["confidence_threshold"] = 0.45

    except Exception as e:
        logger.warning(f"Failed to compute adaptive params: {e}")

    return params


class TradingBot:
    """Autonomous crypto trading bot — paper trading only."""

    def __init__(self, user_id=None):
        self.user_id = user_id
        # Load per-user BloFin keys — only admin falls back to env vars
        keys = _load_user_keys(user_id, "blofin") if user_id else {}
        is_admin = _is_user_admin(user_id) if user_id else False
        self.client = BloFinClient(
            api_key=keys.get("api_key"),
            api_secret=keys.get("api_secret"),
            passphrase=keys.get("passphrase"),
            use_env_fallback=is_admin,
        )
        self.risk_manager = RiskManager(user_id=user_id)
        self._thread = None
        self._stop_event = threading.Event()
        self._running = False
        self._last_summary_hour = -1
        self._last_sensor = None
        self._adaptive_params = {}
        self._last_adaptive_refresh = None
        self._kill_switch_time = None

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            _log(self.user_id, "warn", "Bot is already running")
            return

        self.risk_manager.deactivate_kill_switch()

        if IS_POSTGRES:
            execute(
                f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, 'bot_enabled', '1') "
                f"ON CONFLICT(user_id, key) DO UPDATE SET value = '1'",
                (self.user_id,),
            )
        else:
            execute(
                f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, 'bot_enabled', '1')",
                (self.user_id,),
            )

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="crypto-bot")
        self._thread.start()
        _log(self.user_id, "info", "Trading bot started")
        self._start_watchdog()

    def stop(self):
        self._running = False
        self._stop_event.set()

        if IS_POSTGRES:
            execute(
                f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, 'bot_enabled', '0') "
                f"ON CONFLICT(user_id, key) DO UPDATE SET value = '0'",
                (self.user_id,),
            )
        else:
            execute(
                f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, 'bot_enabled', '0')",
                (self.user_id,),
            )
        _log(self.user_id, "info", "Trading bot stopped")

    def kill(self):
        _log(self.user_id, "warn", "KILL SWITCH activated — stopping bot and closing all positions")
        self.stop()
        self.risk_manager.activate_kill_switch()

        try:
            results = self.client.close_all()
            for r in results:
                _log(self.user_id, "warn", f"Closed position: {r.get('coin', '?')} — success: {r.get('success')}")

            execute(
                f"UPDATE bot_trades SET status = 'killed', closed_at = {P} WHERE user_id = {P} AND status = 'open'",
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

        goal = _get_daily_goal_progress(self.user_id)

        return {
            "running": self.is_running,
            "kill_switch": self.risk_manager.is_kill_switch_active(),
            "balance": balance,
            "positions": positions,
            "daily_pnl": daily_pnl,
            "open_trades": self.risk_manager.get_open_positions_count(),
            "config": config,
            "blofin_configured": self.client.is_configured(),
            "market_sensor": self._last_sensor,
            "daily_goal": goal,
            "adaptive": self._adaptive_params,
        }

    def _run_loop(self):
        try:
            _log(self.user_id, "info", "Bot loop started — scanning every cycle")
            while not self._stop_event.is_set():
                scan_interval = 300
                try:
                    scan_interval = int(_get_config(self.user_id, "scan_interval_sec", "300"))
                    self._scan_cycle()
                    self._log_hourly_summary()
                except Exception as e:
                    _log(self.user_id, "error", f"Scan cycle error: {e}", str(e))
                self._stop_event.wait(timeout=scan_interval)
            self._log_hourly_summary(force=True)
            _log(self.user_id, "info", "Bot loop exited")
        except Exception as e:
            _log(self.user_id, "error", f"FATAL: Bot loop crashed — {e}", str(e))
            logger.exception("FATAL: Crypto bot loop crashed")
        finally:
            self._running = False

    def _start_watchdog(self):
        """Daemon thread that restarts the bot loop if it dies unexpectedly."""
        def _watchdog():
            while True:
                time.sleep(60)
                if not self._running:
                    return  # Bot was intentionally stopped
                if self._thread is None or not self._thread.is_alive():
                    _log(self.user_id, "warn", "WATCHDOG: Bot thread died — restarting")
                    logger.warning("WATCHDOG: Crypto bot thread died, restarting")
                    self._thread = threading.Thread(target=self._run_loop, daemon=True, name="crypto-bot")
                    self._running = True
                    self._thread.start()
        t = threading.Thread(target=_watchdog, daemon=True, name="crypto-bot-watchdog")
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

            summary = (
                f"[CRYPTO PERFORMANCE] "
                f"7d: {o['total_trades']} trades, {o['win_rate']}% win, "
                f"${o['total_pnl']:+.2f} total | "
                f"Today: ${daily_pnl:+.2f} | Open: {open_count}"
            )
            _log(self.user_id, "info", summary)
        except Exception:
            pass

    def _get_selected_coins(self) -> list:
        raw = _get_config(self.user_id, "selected_coins", None)
        if raw:
            try:
                selected = json.loads(raw)
                # Ensure all custom coins are registered in COIN_MAP
                valid = []
                for c in selected:
                    key = ensure_coin_in_map(c)
                    if key:
                        valid.append(key)
                return valid if valid else list(DEFAULT_COINS)
            except Exception:
                pass
        return list(DEFAULT_COINS)

    def _refresh_adaptive(self):
        """Refresh adaptive parameters every 30 minutes."""
        now = datetime.now()
        if self._last_adaptive_refresh and (now - self._last_adaptive_refresh).total_seconds() < 1800:
            return
        self._adaptive_params = _get_adaptive_params(self.user_id)
        self._last_adaptive_refresh = now
        if self._adaptive_params.get("blacklisted_strategies"):
            _log(self.user_id, "info", f"Self-learning: blacklisted strategies (low win rate): {self._adaptive_params['blacklisted_strategies']}")
        if self._adaptive_params.get("hot_strategies"):
            _log(self.user_id, "info", f"Self-learning: hot strategies: {self._adaptive_params['hot_strategies']}")
        if self._adaptive_params.get("cautious_coins"):
            _log(self.user_id, "info", f"Self-learning: cautious coins (reduced size): {self._adaptive_params['cautious_coins']}")
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

    def _scan_cycle(self):
        if self.risk_manager.is_kill_switch_active():
            if self._self_heal():
                _log(self.user_id, "warn", "Kill switch active — waiting for self-heal cooldown")
                return

        self._refresh_adaptive()

        # Daily goal progress check
        goal = _get_daily_goal_progress(self.user_id)
        if goal["pct"] >= 100:
            _log(self.user_id, "info", f"Daily goal reached! ${goal['realized']:.2f} / ${goal['goal']:.2f} ({goal['pct']}%) — reducing aggression")

        # Market sensor pre-check
        sensor = check_market_health("crypto")
        self._last_sensor = sensor

        # PANIC: market crash detected — auto-liquidate ALL positions to preserve capital
        if sensor["status"] == "PANIC":
            _log(self.user_id, "error", f"PANIC KILL SWITCH: {sensor['reasoning']}")
            _log(self.user_id, "error", "Auto-liquidating all crypto positions to preserve capital!")
            try:
                results = self.client.close_all()
                for r in results:
                    _log(self.user_id, "warn", f"Panic close: {r.get('coin', '?')} — success: {r.get('success')}")
                # Mark all open trades as panic-closed
                execute(
                    f"UPDATE bot_trades SET status = 'panic_closed', closed_at = {P} WHERE user_id = {P} AND status = 'open' AND asset_type = 'crypto'",
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

        _log(self.user_id, "info", f"Market sensor: {sensor['status']} (cached={sensor['cached']})")

        selected = self._get_selected_coins()
        _log(self.user_id, "info", f"Starting scan cycle ({len(selected)} coins)...")

        for coin_key in selected:
            coin_info = COIN_MAP.get(coin_key)
            if not coin_info:
                continue
            try:
                self._process_coin(coin_key, coin_info, sensor=sensor)
            except Exception as e:
                _log(self.user_id, "error", f"Error processing {coin_key}: {e}")

        self._check_exits()
        _log(self.user_id, "info", "Scan cycle complete")

    def _process_coin(self, coin_key: str, coin_info: dict, sensor: dict = None):
        yf_ticker = coin_info["yf"]
        blofin_id = coin_info["blofin"]

        _log(self.user_id, "info", f"Scanning {coin_key}...")

        trade_mode = _get_config(self.user_id, "trade_mode", "scalp")  # "scalp", "swing", "hybrid"

        signal = None
        indicators = {}
        df = None

        # --- Swing / Hybrid: fetch daily data and generate swing signals ---
        if trade_mode in ("swing", "hybrid"):
            try:
                import yfinance as yf
                from analysis_engine import calculate_indicators

                ticker_data = yf.Ticker(yf_ticker)
                df_daily = ticker_data.history(period="3mo", interval="1d")

                if df_daily is not None and len(df_daily) >= 50:
                    daily_indicators = calculate_indicators(df_daily)
                    swing_signal = self._generate_swing_signal(coin_key, daily_indicators, df_daily)
                    if swing_signal:
                        # Check max 3 open swing positions
                        swing_open = query_one(
                            f"SELECT COUNT(*) as cnt FROM bot_trades WHERE user_id = {P} AND status = 'open' AND asset_type = 'crypto' AND strategy LIKE 'swing_%'",
                            (self.user_id,),
                        )
                        swing_count = int(swing_open["cnt"]) if swing_open else 0
                        if swing_count >= 3:
                            _log(self.user_id, "info", f"{coin_key}: Swing signal found but max swing positions (3) reached — skipping")
                        else:
                            signal = swing_signal
                            indicators = daily_indicators
                            df = df_daily
                            _log(self.user_id, "info", f"{coin_key}: Swing signal on daily TF — {swing_signal['strategy']}")
                else:
                    _log(self.user_id, "warn", f"{coin_key}: Insufficient daily data for swing analysis ({len(df_daily) if df_daily is not None else 0} bars)")
            except Exception as e:
                _log(self.user_id, "error", f"Failed to fetch daily data for {coin_key}: {e}")

        # --- Scalp / Hybrid: fetch hourly data and generate scalp signals ---
        if signal is None and trade_mode in ("scalp", "hybrid"):
            try:
                import yfinance as yf
                from analysis_engine import calculate_indicators

                ticker_data = yf.Ticker(yf_ticker)
                df = ticker_data.history(period="1mo", interval="1h")

                if df.empty or len(df) < 50:
                    _log(self.user_id, "warn", f"{coin_key}: Insufficient data ({len(df)} bars)")
                    return

                indicators = calculate_indicators(df)
            except Exception as e:
                _log(self.user_id, "error", f"Failed to fetch data for {coin_key}: {e}")
                return

            signal = self._generate_signal(coin_key, indicators, df)

        # If swing-only mode and no swing signal, also need hourly data for logging
        if signal is None and trade_mode == "swing":
            try:
                import yfinance as yf
                from analysis_engine import calculate_indicators

                ticker_data = yf.Ticker(yf_ticker)
                df = ticker_data.history(period="1mo", interval="1h")
                if df is not None and len(df) >= 50:
                    indicators = calculate_indicators(df)
            except Exception:
                pass

        if not signal:
            try:
                rsi = indicators.get('rsi_14', '?')
                macd_h = indicators.get('macd_histogram', '?')
                sma50 = indicators.get('sma_50', '?')
                price = float(df["Close"].iloc[-1])
                _log(self.user_id, "info", f"{coin_key}: No signal (RSI={rsi}, MACD_H={macd_h}, SMA50={sma50}, Price=${price:,.2f})")
            except Exception:
                _log(self.user_id, "info", f"{coin_key}: No signal (no indicator data available)")
            return

        # Self-learning: skip blacklisted strategies
        strategy_name = signal.get("strategy", "unknown")
        if strategy_name in self._adaptive_params.get("blacklisted_strategies", []):
            _log(self.user_id, "info", f"{coin_key}: Strategy '{strategy_name}' blacklisted by self-learning (low win rate) — skipping")
            return

        _log(self.user_id, "info", f"{coin_key} signal: {signal['side']} — {signal['reason']}")

        direction_bias = _get_config(self.user_id, "direction_bias", "both")

        if direction_bias == "long_only" and signal["side"] == "sell":
            _log(self.user_id, "info", f"{coin_key}: Signal is SELL but direction bias is Long Only — skipping")
            return
        if direction_bias == "short_only" and signal["side"] == "buy":
            _log(self.user_id, "info", f"{coin_key}: Signal is BUY but direction bias is Short Only — skipping")
            return
        if direction_bias == "auto":
            # Data-driven rule-based filter (replaces LLM direction detection which had 8% WR).
            # Derived from 127 trade analysis: sell+RSI30-40 = 100% WR, buy+RSI<50 = net negative.
            rsi_val = indicators.get("rsi_14", 50)
            if signal["side"] == "buy" and rsi_val < 50:
                _log(self.user_id, "info", f"{coin_key}: Auto filter — skipping BUY at RSI={rsi_val:.1f} < 50 (data: buy+RSI<50 is net negative)")
                return
            if signal["side"] == "buy" and rsi_val > 70:
                _log(self.user_id, "info", f"{coin_key}: Auto filter — skipping BUY at RSI={rsi_val:.1f} > 70 (overbought, data: -$63 at RSI 70+)")
                return
            if signal["side"] == "sell" and rsi_val < 25:
                _log(self.user_id, "info", f"{coin_key}: Auto filter — skipping SELL at RSI={rsi_val:.1f} < 25 (deeply oversold, bounce risk)")
                return

        current_price = float(df["Close"].iloc[-1])

        balance = self.client.get_balance()
        equity = balance.get("total_equity", 0)
        if equity <= 0:
            _log(self.user_id, "warn", f"No balance available (equity: {equity})")
            return

        is_swing = signal.get("timeframe") == "daily"
        if is_swing:
            base_size = equity * 0.25  # 25% position for swing trades
        else:
            max_pct = float(_get_config(self.user_id, "max_position_pct", "10"))
            base_size = equity * (max_pct / 100)
        # Self-learning: adaptive position sizing (scale down on cold streak, full size on hot)
        pos_scale = min(self._adaptive_params.get("position_scale", 1.0), 1.0)
        size_usd = base_size * pos_scale if pos_scale < 1.0 else base_size
        # Cautious coins: halve position size for coins with consistent recent losses
        if coin_key in self._adaptive_params.get("cautious_coins", []):
            size_usd *= 0.5
            _log(self.user_id, "info", f"{coin_key}: Cautious coin — reducing position to 50% (${size_usd:.2f})")
        size_coins = size_usd / current_price if current_price > 0 else 0

        risk_check = self.risk_manager.can_open_position(coin_key, size_usd, equity)
        if not risk_check["allowed"]:
            _log(self.user_id, "info", f"{coin_key}: Risk gate blocked — {risk_check['reason']}")
            return

        # Quick Trade Mode: bypass LLM gating for high-conviction signals
        # GUARDED: only bypass for SELL signals (data: sell+both = +$371, buy+both = -$128)
        # Never bypass for buy signals — they need LLM validation to filter false breakouts
        quick_mode = _get_config(self.user_id, "quick_trade_mode", "0") == "1"
        bypass_llm = False
        if quick_mode and signal["side"] == "sell":
            rsi_qm = indicators.get("rsi_14", 50)
            rel_vol_qm = indicators.get("relative_volume", 0)
            strategy = signal.get("strategy", "")
            # Bypass conditions: Volume surge + RSI confirmation for sells only
            if rel_vol_qm >= 1.5 and 30 <= rsi_qm <= 60:
                bypass_llm = True
                _log(self.user_id, "info", f"{coin_key}: Quick Trade Mode — bypassing LLM for SELL (vol={rel_vol_qm:.1f}x, RSI={rsi_qm:.1f})")
            elif strategy == "momentum" and rsi_qm < 45:
                bypass_llm = True
                _log(self.user_id, "info", f"{coin_key}: Quick Trade Mode — bypassing LLM for bearish momentum (RSI={rsi_qm:.1f})")

        if bypass_llm:
            validation = {"approved": True, "summary": "Quick Trade Mode — LLM bypassed", "confidence": 0.7}
        else:
            # Check rate limit before LLM validation
            try:
                from rate_limiter import check_rate_limit as _check_rl, set_llm_user
                rl = _check_rl(self.user_id)
                if not rl["allowed"]:
                    _log(self.user_id, "info", f"{coin_key}: Rate limit reached ({rl['used']}/{rl['limit']}) — skipping LLM validation")
                    return
                set_llm_user(self.user_id, "bot_trade")
            except Exception:
                pass

            _log(self.user_id, "info", f"{coin_key}: Running LLM validation...")
            perf_history = _get_trade_performance(self.user_id)
            strategy_name = signal.get("strategy", "unknown")
            validation = validate_trade(
                coin=coin_key, side=signal["side"], price=current_price,
                indicators=indicators, signal_reason=signal["reason"],
                performance_history=perf_history, strategy_name=strategy_name,
            )

            if not validation["approved"]:
                _log(self.user_id, "info", f"{coin_key}: LLM validation rejected — {validation['summary']}")
                self._record_rejected_signal(coin_key, signal, current_price, validation)
                return

            # CAUTION mode: adaptive confidence threshold
            if sensor and sensor.get("status") == "CAUTION":
                confidence = validation.get("confidence", 0)
                threshold = self._adaptive_params.get("confidence_threshold", 0.45)
                if confidence < threshold:
                    _log(self.user_id, "info", f"{coin_key}: Market CAUTION — LLM confidence {confidence:.0%} < {threshold:.0%} threshold — skipping")
                    return

        _log(self.user_id, "info", f"{coin_key}: LLM approved — executing {signal['side']}")

        atr = indicators.get("atr_14", 0)
        min_atr = current_price * 0.01
        effective_atr = max(atr, min_atr)

        if is_swing:
            # Swing trades: wider SL (8% below entry) and wider TP (25% above entry)
            if signal["side"] == "buy":
                stop_loss = current_price * 0.92   # 8% below
                take_profit = current_price * 1.25  # 25% above
            else:
                stop_loss = current_price * 1.08   # 8% above
                take_profit = current_price * 0.75  # 25% below
        else:
            # Data-driven SL/TP (derived from 127-trade analysis):
            # - Losers avg 1.8% move, winners avg 2.3% — R:R was 1.27, too tight.
            # - Tighter SL (1.2x ATR) cuts losers ~25% faster.
            # - Wider TP (4.0x ATR) lets winners run to capture full momentum.
            # - Target R:R = 3.3:1, profitable at 30%+ win rate after ~0.16% fee drag.
            # - Hot strategies get slightly wider SL (1.5x) to avoid premature stops.
            strategy_name = signal.get("strategy", "")
            is_hot = strategy_name in self._adaptive_params.get("hot_strategies", [])
            sl_mult = 1.5 if is_hot else 1.2
            tp_mult = 4.0

            if signal["side"] == "buy":
                stop_loss = current_price - (sl_mult * effective_atr)
                take_profit = current_price + (tp_mult * effective_atr)
            else:
                stop_loss = current_price + (sl_mult * effective_atr)
                take_profit = current_price - (tp_mult * effective_atr)

        order_result = self.client.place_order(
            inst_id=blofin_id, side=signal["side"], size=size_coins,
            stop_loss=round(stop_loss, 2), take_profit=round(take_profit, 2),
        )

        if order_result.get("success"):
            contracts = order_result.get("contracts", size_coins)
            execute(
                f"INSERT INTO bot_trades (user_id, coin, side, size, entry_price, status, signal_reason, "
                f"validation_result, stop_loss, take_profit, blofin_order_id, strategy, asset_type, direction_bias) "
                f"VALUES ({P}, {P}, {P}, {P}, {P}, 'open', {P}, {P}, {P}, {P}, {P}, {P}, 'crypto', {P})",
                (self.user_id, coin_key, signal["side"], round(size_coins, 6), current_price,
                 signal["reason"], json.dumps(validation),
                 round(stop_loss, 2), round(take_profit, 2),
                 order_result.get("order_id", ""),
                 signal.get("strategy", "unknown"), direction_bias),
            )
            _log(self.user_id, "info", f"TRADE OPENED: {signal['side']} {size_coins:.6f} {coin_key} ({contracts} contracts) @ ${current_price:,.2f} | SL: ${stop_loss:,.2f} | TP: ${take_profit:,.2f}")
            _journal_log(self.user_id,
                ticker=coin_key, action="BUY" if signal["side"] == "buy" else "SELL",
                entry_price=current_price, shares=round(size_coins, 6),
                notes=f"[Crypto Bot] {signal['side'].upper()} — {signal['reason']}",
            )
        else:
            error_msg = order_result.get("error", "unknown")
            _log(self.user_id, "error", f"Order failed for {coin_key}: {error_msg}")
            if "READ-ONLY" in error_msg or "not supported" in error_msg.lower():
                execute(
                    f"INSERT INTO bot_trades (user_id, coin, side, size, entry_price, status, signal_reason, "
                    f"validation_result, stop_loss, take_profit, blofin_order_id, strategy, asset_type, direction_bias) "
                    f"VALUES ({P}, {P}, {P}, {P}, {P}, 'open', {P}, {P}, {P}, {P}, 'PAPER-LOCAL', {P}, 'crypto', {P})",
                    (self.user_id, coin_key, signal["side"], round(size_coins, 6), current_price,
                     signal["reason"], json.dumps(validation),
                     round(stop_loss, 2), round(take_profit, 2),
                     signal.get("strategy", "unknown"), direction_bias),
                )
                _log(self.user_id, "warn", f"PAPER TRADE (local): {signal['side']} {size_coins:.6f} {coin_key} @ ${current_price:,.2f} — BloFin API key is read-only")
                _journal_log(self.user_id,
                    ticker=coin_key, action="BUY" if signal["side"] == "buy" else "SELL",
                    entry_price=current_price, shares=round(size_coins, 6),
                    notes=f"[Crypto Bot] PAPER {signal['side'].upper()} — {signal['reason']}",
                )

    def _generate_signal(self, coin_key: str, indicators: dict, df) -> dict:
        """Generate BUY/SELL signal using multiple strategies."""
        rsi = indicators.get("rsi_14", 50)
        macd_hist = indicators.get("macd_histogram", 0)
        macd_bull_cross = indicators.get("macd_bullish_cross", False)
        macd_bear_cross = indicators.get("macd_bearish_cross", False)
        sma8_above_ema20 = indicators.get("sma8_above_ema20", False)
        sma8_ema20_cross = indicators.get("sma8_ema20_cross", False)
        ema_20 = indicators.get("ema_20", 0)
        sma_50 = indicators.get("sma_50", 0)
        sma_8 = indicators.get("sma_8", 0)
        sma_200 = indicators.get("sma_200", 0)
        rel_vol = indicators.get("relative_volume", 0)
        atr = indicators.get("atr_14", 0)
        bb_upper = indicators.get("bb_upper", 0)
        bb_lower = indicators.get("bb_lower", 0)
        bb_mid = indicators.get("bb_mid", 0)
        bb_width = indicators.get("bb_width", 0)
        current_price = float(df["Close"].iloc[-1])

        # === PRE-FILTERS ===
        atr_pct = (atr / current_price * 100) if current_price > 0 else 0
        if atr_pct < 0.15:
            _log(self.user_id, "info", f"{coin_key}: ATR too low ({atr_pct:.2f}% of price) — market dead, skipping")
            return None

        if current_price < 0.50 and atr_pct < 0.4:
            _log(self.user_id, "info", f"{coin_key}: Low-price coin (${current_price:.4f}) with thin ATR ({atr_pct:.2f}%) — spread will eat profit, skipping")
            return None
        if current_price < 1.0 and atr_pct < 0.25:
            _log(self.user_id, "info", f"{coin_key}: Sub-$1 coin (${current_price:.4f}) with low ATR ({atr_pct:.2f}%) — spread risk, skipping")
            return None

        # === DATA-DRIVEN RSI GUARD (derived from 127-trade statistical analysis) ===
        # Prevents the most statistically significant losing patterns:
        #   buy + RSI<50 = -$167 across 28 trades (net negative in ALL buckets 0-50)
        #   sell + RSI 30-40 = +$255 across 14 trades (100% WR — golden zone)
        #   buy + RSI>70 = -$63 across 8 trades (overbought reversal risk)
        # These filters are applied BEFORE strategy-specific logic to save compute.
        # Guard: flag for post-strategy RSI validation
        _rsi_buy_ok = 50 <= rsi <= 70   # Buy only in RSI 50-70 sweet spot
        _rsi_sell_ok = rsi < 70          # Sell OK below 70 (best at 30-40)

        macd_pct = abs(macd_hist) / current_price * 100 if current_price > 0 else 0

        try:
            last_trade_time = self.risk_manager.get_coin_recent_trade_time(coin_key)
            if last_trade_time:
                minutes_since = (datetime.now() - last_trade_time).total_seconds() / 60
                consec_losses = self.risk_manager.get_coin_consecutive_losses(coin_key)
                if consec_losses >= 5:
                    cooldown = 60
                elif consec_losses >= 3:
                    cooldown = 30
                elif consec_losses >= 1:
                    cooldown = 15
                else:
                    cooldown = 10
                if 0 < minutes_since < cooldown:
                    _log(self.user_id, "info", f"{coin_key}: Cooldown active ({minutes_since:.0f}/{cooldown} min, {consec_losses} consec losses)")
                    return None
        except Exception:
            pass

        # --- Strategy 1: MACD Crossover ---
        if macd_bull_cross and macd_pct >= 0.02 and rsi < 68 and rsi > 28 and current_price > ema_20 and _rsi_buy_ok:
            return {"side": "buy", "reason": f"MACD bullish crossover (hist={macd_hist:.6f}, {macd_pct:.3f}%) + RSI={rsi:.1f} + price above EMA20", "strategy": "macd_cross"}
        if macd_bear_cross and macd_pct >= 0.02 and rsi > 32 and rsi < 72 and current_price < ema_20:
            return {"side": "sell", "reason": f"MACD bearish crossover (hist={macd_hist:.6f}, {macd_pct:.3f}%) + RSI={rsi:.1f} + price below EMA20", "strategy": "macd_cross"}

        # --- Strategy 2: EMA Trend ---
        if sma8_ema20_cross:
            if sma8_above_ema20 and rsi < 68 and rsi > 28 and macd_hist > 0 and _rsi_buy_ok:
                return {"side": "buy", "reason": f"SMA8 crossed above EMA20 (trend shift bullish) + RSI={rsi:.1f} + MACD positive", "strategy": "ema_trend"}
            if not sma8_above_ema20 and rsi > 32 and rsi < 72 and macd_hist < 0:
                return {"side": "sell", "reason": f"SMA8 crossed below EMA20 (trend shift bearish) + RSI={rsi:.1f} + MACD negative", "strategy": "ema_trend"}

        # --- Strategy 3: RSI Mean Reversion ---
        # NOTE: buy side disabled by RSI guard — data shows RSI<35 buys are net -$65 (20% WR).
        # Sell side (overbought fade) is kept — statistically profitable.
        if rsi > 65 and macd_hist < 0 and current_price < sma_8:
            return {"side": "sell", "reason": f"RSI overbought fade ({rsi:.1f}) + MACD momentum negative ({macd_hist:.6f}) + below SMA8", "strategy": "rsi_reversion"}

        # --- Strategy 4: Momentum Breakout ---
        if current_price > sma_50 and rel_vol > 1.2 and 50 < rsi < 70 and macd_hist > 0 and _rsi_buy_ok:
            return {"side": "buy", "reason": f"Momentum breakout: price above SMA50 + volume surge ({rel_vol:.1f}x) + RSI={rsi:.1f}", "strategy": "momentum"}
        if current_price < sma_50 and rel_vol > 1.2 and 25 < rsi < 55 and macd_hist < 0:
            return {"side": "sell", "reason": f"Bearish momentum: price below SMA50 + volume surge ({rel_vol:.1f}x) + RSI={rsi:.1f}", "strategy": "momentum"}

        # --- Strategy 5: Bollinger Band Reversion ---
        # BB buy is kept even below RSI 50 — it's the strongest reversion signal (+$62, 67% WR)
        # but requires stricter RSI floor (>35 instead of <40)
        if bb_lower > 0 and bb_upper > 0:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_position = (current_price - bb_lower) / bb_range
                if bb_position <= 0.10 and rsi > 35 and rsi < 45 and macd_hist > 0:
                    return {"side": "buy", "reason": f"BB lower band touch (BB%={bb_position:.0%}, width={bb_width:.1f}%) + RSI={rsi:.1f} + MACD turning up", "strategy": "bb_reversion"}
                if bb_position >= 0.90 and rsi > 60 and macd_hist < 0:
                    return {"side": "sell", "reason": f"BB upper band touch (BB%={bb_position:.0%}, width={bb_width:.1f}%) + RSI={rsi:.1f} + MACD turning down", "strategy": "bb_reversion"}

        # --- Strategy 6: Grid Mean Reversion ---
        if atr > 0 and sma_50 > 0:
            atr_dist = (current_price - sma_50) / atr
            # Grid buy: apply RSI guard — data shows grid buy net -$121 across 5 trades
            if atr_dist <= -1.5 and rsi < 45 and sma_50 > sma_200 and _rsi_buy_ok:
                return {"side": "buy", "reason": f"Grid support: price {abs(atr_dist):.1f}x ATR below SMA50 (uptrend pullback) + RSI={rsi:.1f}", "strategy": "grid_reversion"}
            if atr_dist >= 1.5 and rsi > 55 and sma_50 < sma_200:
                return {"side": "sell", "reason": f"Grid resistance: price {atr_dist:.1f}x ATR above SMA50 (downtrend rally) + RSI={rsi:.1f}", "strategy": "grid_reversion"}

        # --- Strategy 7: Trend Continuation ---
        if sma_50 > sma_200 and current_price > sma_200:
            ema20_dist_pct = abs(current_price - ema_20) / ema_20 * 100 if ema_20 > 0 else 999
            if ema20_dist_pct < 1.2 and rsi > 50 and rsi < 60 and macd_hist > 0 and _rsi_buy_ok:
                return {"side": "buy", "reason": f"Trend continuation: pullback to EMA20 (dist={ema20_dist_pct:.2f}%) in uptrend (SMA50>200) + RSI={rsi:.1f}", "strategy": "trend_dca"}
        if sma_50 < sma_200 and current_price < sma_200:
            ema20_dist_pct = abs(current_price - ema_20) / ema_20 * 100 if ema_20 > 0 else 999
            if ema20_dist_pct < 1.0 and rsi > 45 and rsi < 68 and macd_hist < 0:
                return {"side": "sell", "reason": f"Trend continuation: rally to EMA20 (dist={ema20_dist_pct:.2f}%) in downtrend (SMA50<200) + RSI={rsi:.1f}", "strategy": "trend_dca"}

        # --- Strategy 8: Doji Reversal ---
        # Doji buy disabled — data: 4 trades, 25% WR, -$44 total, bootstrap P(profitable)=5%
        # Doji sell kept — needs more data but theoretically sound
        if len(df) >= 2:
            c_open = float(df["Open"].iloc[-1])
            c_high = float(df["High"].iloc[-1])
            c_low = float(df["Low"].iloc[-1])
            c_range = c_high - c_low
            c_body = abs(current_price - c_open)
            if c_range > 0 and c_body < c_range * 0.10:
                prev_open = float(df["Open"].iloc[-2])
                prev_close = float(df["Close"].iloc[-2])
                if rsi > 60 and current_price > ema_20 and prev_close > prev_open:
                    return {"side": "sell", "reason": f"Doji reversal: doji candle + RSI={rsi:.1f} + price above EMA20 + prior bullish candle", "strategy": "doji_reversal"}

        # --- Strategy 9: Pump on Close ---
        if len(df) >= 2 and atr > 0:
            c_open = float(df["Open"].iloc[-1])
            c_high = float(df["High"].iloc[-1])
            c_low = float(df["Low"].iloc[-1])
            c_range = c_high - c_low
            c_body = abs(current_price - c_open)
            if rel_vol >= 1.5 and c_body > atr * 0.4 and c_range > 0:
                close_pos = (current_price - c_low) / c_range
                if close_pos >= 0.75 and rsi < 70 and current_price > sma_50:
                    signal = {"side": "buy", "reason": f"Pump on close: vol surge ({rel_vol:.1f}x) + large body + close in upper 25% + RSI={rsi:.1f} + above SMA50", "strategy": "pump_on_close"}
                    if _rsi_buy_ok:
                        return signal
                    else:
                        _log(self.user_id, "info", f"{coin_key}: Pump buy signal blocked by RSI guard (RSI={rsi:.1f} outside 50-70)")
                        return None
                if close_pos <= 0.25 and rsi > 30 and current_price < sma_50:
                    return {"side": "sell", "reason": f"Dump on close: vol surge ({rel_vol:.1f}x) + large body + close in lower 25% + RSI={rsi:.1f} + below SMA50", "strategy": "pump_on_close"}

        return None

    def _generate_swing_signal(self, coin_key: str, indicators: dict, df) -> dict:
        """Generate swing trade signals on DAILY timeframes using 50+ daily candles."""
        if df is None or df.empty or len(df) < 50:
            return None

        current_price = float(df["Close"].iloc[-1])
        sma_50 = indicators.get("sma_50", 0)
        sma_200 = indicators.get("sma_200", 0)
        ema_20 = indicators.get("ema_20", 0)
        rsi = indicators.get("rsi_14", 50)
        rel_vol = indicators.get("relative_volume", 0)

        # --- Swing Strategy 1: VCP (Volatility Contraction Pattern) ---
        # Price within 3% of 10-day SMA, successive range contractions, volume declining
        try:
            sma_10 = df["Close"].rolling(10).mean().iloc[-1]
            dist_to_sma10 = abs(current_price - sma_10) / sma_10 * 100 if sma_10 > 0 else 999

            if dist_to_sma10 <= 3.0 and len(df) >= 30:
                # Check for successive range contractions (3 windows of 5 bars)
                ranges = []
                for i in range(3):
                    start = -(15 - i * 5)
                    end = -(10 - i * 5) if (10 - i * 5) > 0 else None
                    window = df.iloc[start:end] if end else df.iloc[start:]
                    if len(window) > 0:
                        r = (window["High"].max() - window["Low"].min()) / current_price * 100
                        ranges.append(r)

                # Volume declining over last 3 weeks
                vol_windows = []
                for i in range(3):
                    start = -(15 - i * 5)
                    end = -(10 - i * 5) if (10 - i * 5) > 0 else None
                    window = df.iloc[start:end] if end else df.iloc[start:]
                    if len(window) > 0:
                        vol_windows.append(window["Volume"].mean())

                range_contracting = len(ranges) == 3 and ranges[0] > ranges[1] > ranges[2]
                vol_declining = len(vol_windows) == 3 and vol_windows[0] > vol_windows[1] > vol_windows[2]

                if range_contracting and vol_declining:
                    return {
                        "side": "buy",
                        "reason": f"VCP: price within {dist_to_sma10:.1f}% of SMA10, ranges contracting ({ranges[0]:.1f}%→{ranges[1]:.1f}%→{ranges[2]:.1f}%), volume declining",
                        "strategy": "swing_vcp",
                        "timeframe": "daily",
                    }
        except Exception:
            pass

        # --- Swing Strategy 2: HTF (High Tight Flag) ---
        # 30%+ run-up from recent low, pullback <15%, tight range
        try:
            recent_low = df["Low"].iloc[-50:].min()
            recent_high = df["High"].iloc[-20:].max()
            runup_pct = (recent_high - recent_low) / recent_low * 100 if recent_low > 0 else 0
            pullback_pct = (recent_high - current_price) / recent_high * 100 if recent_high > 0 else 999

            if runup_pct >= 30 and pullback_pct < 15:
                # Check tight range in last 5 days
                last5_range = (df["High"].iloc[-5:].max() - df["Low"].iloc[-5:].min()) / current_price * 100
                if last5_range < 8:
                    return {
                        "side": "buy",
                        "reason": f"HTF: {runup_pct:.0f}% run-up, pullback only {pullback_pct:.1f}%, tight 5d range ({last5_range:.1f}%)",
                        "strategy": "swing_htf",
                        "timeframe": "daily",
                    }
        except Exception:
            pass

        # --- Swing Strategy 3: Breakout ---
        # Price breaks above 20-day high with 1.5x volume
        try:
            high_20d = df["High"].iloc[-21:-1].max()  # 20-day high excluding today
            if current_price > high_20d and rel_vol >= 1.5:
                breakout_pct = (current_price - high_20d) / high_20d * 100
                return {
                    "side": "buy",
                    "reason": f"Breakout: price ${current_price:,.2f} broke above 20d high ${high_20d:,.2f} (+{breakout_pct:.1f}%) on {rel_vol:.1f}x volume",
                    "strategy": "swing_breakout",
                    "timeframe": "daily",
                }
        except Exception:
            pass

        # --- Swing Strategy 4: Trend Continuation ---
        # Price pulling back to rising 20 EMA in uptrend (SMA50 > SMA200), RSI 40-60
        try:
            if sma_50 > sma_200 and sma_50 > 0 and ema_20 > 0:
                ema20_dist_pct = abs(current_price - ema_20) / ema_20 * 100
                if ema20_dist_pct < 2.0 and 40 <= rsi <= 60:
                    return {
                        "side": "buy",
                        "reason": f"Swing trend: pullback to EMA20 (dist={ema20_dist_pct:.1f}%) in uptrend (SMA50>200), RSI={rsi:.1f}",
                        "strategy": "swing_trend",
                        "timeframe": "daily",
                    }
        except Exception:
            pass

        return None

    def _check_exits(self):
        rows = query(
            f"SELECT * FROM bot_trades WHERE user_id = {P} AND status = 'open' AND asset_type = 'crypto'",
            (self.user_id,),
        )
        for trade in rows:
            try:
                coin_info = COIN_MAP.get(trade["coin"])
                if not coin_info:
                    continue
                current_price = self.client.get_ticker_price(coin_info["blofin"])
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

                is_swing_trade = str(trade.get("strategy", "")).startswith("swing_")

                # Trailing stop logic
                if not should_exit and is_swing_trade:
                    # Swing trades: lock in 50% of max unrealized profit
                    if pnl_pct >= 5.0:
                        # Calculate max possible profit (distance to TP)
                        if trade["side"] == "buy":
                            max_profit_pct = (take_profit - entry_price) / entry_price * 100
                        else:
                            max_profit_pct = (entry_price - take_profit) / entry_price * 100
                        # If price has retraced and given back more than 50% of peak unrealized
                        locked_floor = pnl_pct * 0.50  # lock 50% of current unrealized
                        # Check if we've retraced significantly from what we had
                        if trade["side"] == "buy":
                            peak_price_approx = entry_price * (1 + max_profit_pct / 100 * (pnl_pct / max_profit_pct if max_profit_pct > 0 else 0))
                            retracement_from_peak = (peak_price_approx - current_price) / (peak_price_approx - entry_price) if peak_price_approx != entry_price else 0
                        else:
                            retracement_from_peak = 0.0  # simplified
                        if retracement_from_peak > 0.50 and pnl_pct > 2.0:
                            should_exit, exit_reason = True, f"Swing trailing stop: locking profit at {pnl_pct:.1f}% (retraced {retracement_from_peak:.0%} from peak)"
                elif not should_exit and pnl_pct >= 2.0:
                    # Scalp trailing stop: only if profit is meaningful (above fee drag)
                    if trade["side"] == "buy":
                        retracement = (take_profit - current_price) / (take_profit - entry_price) if take_profit != entry_price else 0
                    else:
                        retracement = (current_price - take_profit) / (entry_price - take_profit) if entry_price != take_profit else 0
                    if retracement > 0.50 and pnl_pct > 0.8:
                        should_exit, exit_reason = True, f"Trailing stop: P&L was {pnl_pct:.1f}%, retraced {retracement:.0%} from target"

                if not should_exit:
                    try:
                        opened_str = str(trade["opened_at"])
                        try:
                            opened = datetime.strptime(opened_str, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            opened = datetime.fromisoformat(opened_str)
                        hours_open = (datetime.now() - opened).total_seconds() / 3600

                        if is_swing_trade:
                            # Swing time exit: 14 days
                            if hours_open >= 336 and abs(pnl_pct) < 2.0:
                                should_exit, exit_reason = True, f"Swing time exit: open {hours_open / 24:.0f}d with only {pnl_pct:+.2f}% P&L (stale swing trade)"
                        else:
                            # Data-driven time exits (from hold-time analysis):
                            # - Hold 0-6h: profitable (+$130), let it run
                            # - Hold 6-12h: worst bucket (-$93), exit if underwater
                            # - Hold 12-24h: marginal (-$12), exit if stale
                            # Cut losers at 8h if they're underwater, stale exit at 16h
                            if hours_open >= 8 and pnl_pct < -0.3:
                                should_exit, exit_reason = True, f"Time exit: open {hours_open:.0f}h underwater ({pnl_pct:+.2f}%) — data shows 6-12h hold is worst bucket"
                            elif hours_open >= 16 and abs(pnl_pct) < 0.5:
                                should_exit, exit_reason = True, f"Time exit: open {hours_open:.0f}h with only {pnl_pct:+.2f}% P&L (stale, below fee threshold)"
                    except Exception:
                        pass

                if should_exit:
                    self._close_trade(trade, current_price, exit_reason)

            except Exception as e:
                _log(self.user_id, "error", f"Error checking exit for trade {trade['id']}: {e}")

    def _close_trade(self, trade, exit_price: float, reason: str):
        coin_info = COIN_MAP.get(trade["coin"])
        if coin_info:
            self.client.close_position(coin_info["blofin"])

        entry_price = trade["entry_price"]
        size = trade["size"]
        if trade["side"] == "buy":
            gross_pnl = (exit_price - entry_price) * size
        else:
            gross_pnl = (entry_price - exit_price) * size

        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        if trade["side"] == "sell":
            pnl_pct = -pnl_pct

        # Trading fees: entry + exit (0.06% maker / 0.1% taker — use 0.08% avg per side)
        fee_rate = 0.0008
        fee = round((entry_price * size * fee_rate) + (exit_price * size * fee_rate), 4)

        # NET P&L = gross minus fees — this is the REAL number
        pnl = round(gross_pnl - fee, 2)
        fee_pct = (fee / (entry_price * size) * 100) if entry_price * size > 0 else 0
        net_pnl_pct = round(pnl_pct - fee_pct, 2)

        execute(
            f"UPDATE bot_trades SET exit_price = {P}, pnl = {P}, pnl_pct = {P}, fee = {P}, status = 'closed', closed_at = {P} WHERE id = {P}",
            (exit_price, pnl, net_pnl_pct, fee, datetime.now().isoformat(), trade["id"]),
        )

        self.risk_manager.record_trade_pnl(pnl)

        _log(self.user_id, "info",
             f"TRADE CLOSED: {trade['coin']} {trade['side']} | Entry: ${entry_price:,.2f} → Exit: ${exit_price:,.2f} | "
             f"Gross: ${gross_pnl:,.2f} | Fee: ${fee:,.2f} ({fee_pct:.2f}%) | Net P&L: ${pnl:,.2f} ({net_pnl_pct:+.1f}%) | {reason}")

        close_action = "SELL" if trade["side"] == "buy" else "BUY"
        _journal_log(self.user_id,
            ticker=trade["coin"], action=close_action,
            entry_price=entry_price, exit_price=exit_price,
            shares=size, pnl=pnl,
            notes=f"[Crypto Bot] Closed — {reason} | Gross: ${gross_pnl:,.2f} | Fee: ${fee:,.2f} | Net: ${pnl:,.2f} ({net_pnl_pct:+.1f}%)",
        )

    def _record_rejected_signal(self, coin_key: str, signal: dict, price: float, validation: dict):
        _log(self.user_id, "info", f"Signal rejected: {signal['side']} {coin_key} @ ${price:,.2f} — {validation['summary']}",
             json.dumps({"signal": signal, "validation": validation}))


# Per-user bot instances keyed by user_id
_bot_instances = {}
_bot_lock = threading.Lock()


def get_bot(user_id=None) -> TradingBot:
    with _bot_lock:
        if user_id not in _bot_instances:
            _bot_instances[user_id] = TradingBot(user_id=user_id)
        return _bot_instances[user_id]
