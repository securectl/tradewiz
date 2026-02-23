"""
Core Trading Bot Engine — Background daemon thread with 5-min scan cycle.
Fetches data via yfinance, generates signals, validates via multi-LLM, executes on BloFin demo.
"""

import os
import json
import sqlite3
import logging
import threading
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "searches.db")

# Import siblings
from crypto_bot.blofin_client import BloFinClient, COIN_MAP, DEFAULT_COINS
from crypto_bot.risk_manager import RiskManager
from crypto_bot.crypto_validator import validate_trade, detect_direction


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_config(key, default=None):
    conn = _get_db()
    row = conn.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def _log(level: str, message: str, details: str = None):
    """Write to bot_log table."""
    try:
        conn = _get_db()
        conn.execute(
            "INSERT INTO bot_log (level, message, details) VALUES (?, ?, ?)",
            (level, message, details),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Don't crash on log failure
    log_fn = getattr(logger, level, logger.info)
    log_fn(f"[BOT] {message}")


def _get_trade_performance() -> dict:
    """Query last 7 days of closed trades and return performance stats."""
    try:
        conn = _get_db()
        cutoff = (datetime.now() - __import__("datetime").timedelta(days=7)).isoformat()
        rows = conn.execute(
            "SELECT coin, strategy, pnl FROM bot_trades WHERE status = 'closed' AND closed_at >= ?",
            (cutoff,),
        ).fetchall()
        conn.close()

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

        # By strategy
        by_strategy = {}
        for r in rows:
            s = r["strategy"] or "unknown"
            by_strategy.setdefault(s, []).append(r["pnl"] or 0)
        strategy_stats = {}
        for s, p_list in by_strategy.items():
            sw = sum(1 for p in p_list if p > 0)
            strategy_stats[s] = {
                "trades": len(p_list),
                "win_rate": round(sw / len(p_list) * 100, 1),
                "avg_pnl": round(sum(p_list) / len(p_list), 2),
            }

        # By coin
        by_coin = {}
        for r in rows:
            c = r["coin"]
            by_coin.setdefault(c, []).append(r["pnl"] or 0)
        coin_stats = {}
        for c, p_list in by_coin.items():
            cw = sum(1 for p in p_list if p > 0)
            coin_stats[c] = {
                "trades": len(p_list),
                "win_rate": round(cw / len(p_list) * 100, 1),
                "avg_pnl": round(sum(p_list) / len(p_list), 2),
            }

        return {"overall": overall, "by_strategy": strategy_stats, "by_coin": coin_stats}
    except Exception as e:
        logger.warning(f"Failed to get trade performance: {e}")
        return {}


class TradingBot:
    """Autonomous crypto trading bot — paper trading only."""

    def __init__(self):
        self.client = BloFinClient()
        self.risk_manager = RiskManager()
        self._thread = None
        self._stop_event = threading.Event()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self):
        """Start the bot in a background daemon thread."""
        if self.is_running:
            _log("warn", "Bot is already running")
            return

        # Reset kill switch if it was active
        self.risk_manager.deactivate_kill_switch()

        # Mark bot as enabled
        conn = _get_db()
        conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('bot_enabled', '1')")
        conn.commit()
        conn.close()

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="crypto-bot")
        self._thread.start()
        _log("info", "Trading bot started")

    def stop(self):
        """Stop the bot gracefully."""
        self._running = False
        self._stop_event.set()

        conn = _get_db()
        conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('bot_enabled', '0')")
        conn.commit()
        conn.close()

        _log("info", "Trading bot stopped")

    def kill(self):
        """Kill switch — stop bot and close all positions."""
        _log("warn", "KILL SWITCH activated — stopping bot and closing all positions")
        self.stop()
        self.risk_manager.activate_kill_switch()

        try:
            results = self.client.close_all()
            for r in results:
                _log("warn", f"Closed position: {r.get('coin', '?')} — success: {r.get('success')}")

            # Mark all open trades as closed in DB
            conn = _get_db()
            conn.execute(
                "UPDATE bot_trades SET status = 'killed', closed_at = ? WHERE status = 'open'",
                (datetime.now().isoformat(),),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            _log("error", f"Error during kill switch: {e}")

    def get_status(self) -> dict:
        """Get bot status summary."""
        balance = {"total_equity": 0, "available": 0, "unrealized_pnl": 0}
        positions = []

        try:
            if self.client.is_configured():
                balance = self.client.get_balance()
                positions = self.client.get_positions()
        except Exception:
            pass  # Don't spam log on every 5s poll

        daily_pnl = self.risk_manager.get_daily_pnl()
        config = self.risk_manager.refresh_config()

        return {
            "running": self.is_running,
            "kill_switch": self.risk_manager.is_kill_switch_active(),
            "balance": balance,
            "positions": positions,
            "daily_pnl": daily_pnl,
            "open_trades": self.risk_manager.get_open_positions_count(),
            "config": config,
            "blofin_configured": self.client.is_configured(),
        }

    def _run_loop(self):
        """Main trading loop — runs every scan_interval seconds."""
        _log("info", "Bot loop started — scanning every cycle")

        while not self._stop_event.is_set():
            scan_interval = 300
            try:
                scan_interval = int(_get_config("scan_interval_sec", "300"))
                self._scan_cycle()
            except Exception as e:
                _log("error", f"Scan cycle error: {e}", str(e))

            # Wait for the interval or stop event
            self._stop_event.wait(timeout=scan_interval)

        _log("info", "Bot loop exited")

    def _get_selected_coins(self) -> list:
        """Get user-selected coins from bot_config, or defaults."""
        raw = _get_config("selected_coins", None)
        if raw:
            import json as _json
            try:
                selected = _json.loads(raw)
                return [c for c in selected if c in COIN_MAP]
            except Exception:
                pass
        return list(DEFAULT_COINS)

    def _scan_cycle(self):
        """One full scan cycle — check selected coins for signals."""
        if self.risk_manager.is_kill_switch_active():
            _log("warn", "Kill switch active — skipping scan")
            return

        selected = self._get_selected_coins()
        _log("info", f"Starting scan cycle ({len(selected)} coins)...")

        for coin_key in selected:
            coin_info = COIN_MAP.get(coin_key)
            if not coin_info:
                continue
            try:
                self._process_coin(coin_key, coin_info)
            except Exception as e:
                _log("error", f"Error processing {coin_key}: {e}")

        # Check exit conditions for open trades
        self._check_exits()

        _log("info", "Scan cycle complete")

    def _process_coin(self, coin_key: str, coin_info: dict):
        """Analyze a single coin for entry signals."""
        yf_ticker = coin_info["yf"]
        blofin_id = coin_info["blofin"]

        _log("info", f"Scanning {coin_key}...")

        # Fetch 1h data via yfinance
        try:
            import yfinance as yf
            import pandas as pd
            from analysis_engine import calculate_indicators

            ticker_data = yf.Ticker(yf_ticker)
            df = ticker_data.history(period="1mo", interval="1h")

            if df.empty or len(df) < 50:
                _log("warn", f"{coin_key}: Insufficient data ({len(df)} bars)")
                return

            indicators = calculate_indicators(df)
        except Exception as e:
            _log("error", f"Failed to fetch data for {coin_key}: {e}")
            return

        # Generate signal
        signal = self._generate_signal(coin_key, indicators, df)
        if not signal:
            rsi = indicators.get('rsi_14', '?')
            macd_h = indicators.get('macd_histogram', '?')
            sma50 = indicators.get('sma_50', '?')
            price = float(df["Close"].iloc[-1])
            _log("info", f"{coin_key}: No signal (RSI={rsi}, MACD_H={macd_h}, SMA50={sma50}, Price=${price:,.2f})")
            return

        _log("info", f"{coin_key} signal: {signal['side']} — {signal['reason']}")

        # Direction bias filtering
        direction_bias = _get_config("direction_bias", "both")

        if direction_bias == "long_only" and signal["side"] == "sell":
            _log("info", f"{coin_key}: Signal is SELL but direction bias is Long Only — skipping")
            return
        if direction_bias == "short_only" and signal["side"] == "buy":
            _log("info", f"{coin_key}: Signal is BUY but direction bias is Short Only — skipping")
            return
        if direction_bias == "auto":
            current_price_for_dir = float(df["Close"].iloc[-1])
            direction_result = detect_direction(coin_key, current_price_for_dir, indicators)
            detected = direction_result.get("direction", "neutral")
            confidence = direction_result.get("confidence", 0)
            _log("info", f"{coin_key}: LLM direction = {detected} (conf={confidence:.0%}) — signal is {signal['side']}")
            if detected == "bearish" and signal["side"] == "buy" and confidence > 0.5:
                _log("info", f"{coin_key}: Skipping BUY — LLM detected bearish market")
                return
            if detected == "bullish" and signal["side"] == "sell" and confidence > 0.5:
                _log("info", f"{coin_key}: Skipping SELL — LLM detected bullish market")
                return

        # Get current price
        current_price = float(df["Close"].iloc[-1])

        # Calculate position size
        balance = self.client.get_balance()
        equity = balance.get("total_equity", 0)
        if equity <= 0:
            _log("warn", f"No balance available (equity: {equity})")
            return

        max_pct = float(_get_config("max_position_pct", "10"))
        size_usd = equity * (max_pct / 100)
        size_coins = size_usd / current_price if current_price > 0 else 0

        # Risk check
        risk_check = self.risk_manager.can_open_position(coin_key, size_usd, equity)
        if not risk_check["allowed"]:
            _log("info", f"{coin_key}: Risk gate blocked — {risk_check['reason']}")
            return

        # LLM Validation
        _log("info", f"{coin_key}: Running LLM validation...")
        perf_history = _get_trade_performance()
        strategy_name = signal.get("strategy", "unknown")
        validation = validate_trade(
            coin=coin_key,
            side=signal["side"],
            price=current_price,
            indicators=indicators,
            signal_reason=signal["reason"],
            performance_history=perf_history,
            strategy_name=strategy_name,
        )

        if not validation["approved"]:
            _log("info", f"{coin_key}: LLM validation rejected — {validation['summary']}")
            # Still log the rejected trade
            self._record_rejected_signal(coin_key, signal, current_price, validation)
            return

        _log("info", f"{coin_key}: LLM approved — executing {signal['side']}")

        # Calculate stop loss and take profit
        atr = indicators.get("atr_14", 0)
        if signal["side"] == "buy":
            stop_loss = current_price - (1.5 * atr)
            take_profit = current_price + (2.5 * atr)
        else:
            stop_loss = current_price + (1.5 * atr)
            take_profit = current_price - (2.5 * atr)

        # Execute trade (size is in coins, place_order converts to contracts)
        order_result = self.client.place_order(
            inst_id=blofin_id,
            side=signal["side"],
            size=size_coins,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
        )

        if order_result.get("success"):
            contracts = order_result.get("contracts", size_coins)
            # Record in DB
            conn = _get_db()
            conn.execute("""
                INSERT INTO bot_trades (coin, side, size, entry_price, status, signal_reason,
                    validation_result, stop_loss, take_profit, blofin_order_id,
                    strategy, asset_type, direction_bias)
                VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                coin_key, signal["side"], round(size_coins, 6), current_price,
                signal["reason"], json.dumps(validation),
                round(stop_loss, 2), round(take_profit, 2),
                order_result.get("order_id", ""),
                signal.get("strategy", "unknown"), "crypto", direction_bias,
            ))
            conn.commit()
            conn.close()
            _log("info", f"TRADE OPENED: {signal['side']} {size_coins:.6f} {coin_key} ({contracts} contracts) @ ${current_price:,.2f} | SL: ${stop_loss:,.2f} | TP: ${take_profit:,.2f}")
        else:
            error_msg = order_result.get("error", "unknown")
            _log("error", f"Order failed for {coin_key}: {error_msg}")
            # Still record as paper trade in DB if it's a permission issue
            if "READ-ONLY" in error_msg or "not supported" in error_msg.lower():
                conn = _get_db()
                conn.execute("""
                    INSERT INTO bot_trades (coin, side, size, entry_price, status, signal_reason,
                        validation_result, stop_loss, take_profit, blofin_order_id,
                        strategy, asset_type, direction_bias)
                    VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    coin_key, signal["side"], round(size_coins, 6), current_price,
                    signal["reason"], json.dumps(validation),
                    round(stop_loss, 2), round(take_profit, 2),
                    "PAPER-LOCAL",
                    signal.get("strategy", "unknown"), "crypto", direction_bias,
                ))
                conn.commit()
                conn.close()
                _log("warn", f"PAPER TRADE (local): {signal['side']} {size_coins:.6f} {coin_key} @ ${current_price:,.2f} — BloFin API key is read-only")

    def _generate_signal(self, coin_key: str, indicators: dict, df) -> dict:
        """Generate BUY/SELL signal using multiple strategies.

        Strategies (any one can trigger):
        1. MACD Crossover — bullish/bearish cross with trend confirmation
        2. EMA Trend — SMA8/EMA20 crossover with momentum
        3. RSI Mean Reversion — oversold bounce / overbought rejection
        4. Momentum Breakout — strong move with volume confirmation
        """
        rsi = indicators.get("rsi_14", 50)
        macd_hist = indicators.get("macd_histogram", 0)
        macd_bull_cross = indicators.get("macd_bullish_cross", False)
        macd_bear_cross = indicators.get("macd_bearish_cross", False)
        sma8_above_ema20 = indicators.get("sma8_above_ema20", False)
        sma8_ema20_cross = indicators.get("sma8_ema20_cross", False)
        ema_20 = indicators.get("ema_20", 0)
        sma_50 = indicators.get("sma_50", 0)
        sma_8 = indicators.get("sma_8", 0)
        rel_vol = indicators.get("relative_volume", 0)
        current_price = float(df["Close"].iloc[-1])

        # --- Strategy 1: MACD Crossover ---
        # Bullish: MACD just crossed above signal + RSI not overbought
        if macd_bull_cross and rsi < 65 and current_price > ema_20:
            return {
                "side": "buy",
                "reason": f"MACD bullish crossover (hist={macd_hist:.4f}) + RSI={rsi:.1f} + price above EMA20",
                "strategy": "macd_cross",
            }
        # Bearish: MACD just crossed below signal + RSI not oversold
        if macd_bear_cross and rsi > 35 and current_price < ema_20:
            return {
                "side": "sell",
                "reason": f"MACD bearish crossover (hist={macd_hist:.4f}) + RSI={rsi:.1f} + price below EMA20",
                "strategy": "macd_cross",
            }

        # --- Strategy 2: EMA Trend (SMA8/EMA20 cross) ---
        if sma8_ema20_cross:
            if sma8_above_ema20 and rsi < 65:
                return {
                    "side": "buy",
                    "reason": f"SMA8 crossed above EMA20 (trend shift bullish) + RSI={rsi:.1f}",
                    "strategy": "ema_trend",
                }
            if not sma8_above_ema20 and rsi > 35:
                return {
                    "side": "sell",
                    "reason": f"SMA8 crossed below EMA20 (trend shift bearish) + RSI={rsi:.1f}",
                    "strategy": "ema_trend",
                }

        # --- Strategy 3: RSI Mean Reversion ---
        # Oversold bounce: RSI < 40 and turning up (MACD hist positive = momentum shifting)
        if rsi < 40 and macd_hist > 0:
            return {
                "side": "buy",
                "reason": f"RSI oversold bounce ({rsi:.1f}) + MACD momentum positive ({macd_hist:.4f})",
                "strategy": "rsi_reversion",
            }
        # Overbought rejection: RSI > 65 and fading (MACD hist negative)
        if rsi > 65 and macd_hist < 0 and current_price < sma_8:
            return {
                "side": "sell",
                "reason": f"RSI overbought fade ({rsi:.1f}) + MACD momentum negative ({macd_hist:.4f}) + price below SMA8",
                "strategy": "rsi_reversion",
            }

        # --- Strategy 4: Momentum Breakout with volume ---
        # Strong bullish momentum: price above SMA50, strong relative volume, RSI 50-70 (trending)
        if current_price > sma_50 and rel_vol > 1.5 and 50 < rsi < 70 and macd_hist > 0:
            return {
                "side": "buy",
                "reason": f"Momentum breakout: price above SMA50 + volume surge ({rel_vol:.1f}x) + RSI={rsi:.1f}",
                "strategy": "momentum",
            }
        # Strong bearish momentum
        if current_price < sma_50 and rel_vol > 1.5 and 30 < rsi < 50 and macd_hist < 0:
            return {
                "side": "sell",
                "reason": f"Bearish momentum: price below SMA50 + volume surge ({rel_vol:.1f}x) + RSI={rsi:.1f}",
                "strategy": "momentum",
            }

        return None

    def _check_exits(self):
        """Check open trades for exit conditions (SL/TP hit)."""
        conn = _get_db()
        open_trades = conn.execute("SELECT * FROM bot_trades WHERE status = 'open'").fetchall()
        conn.close()

        for trade in open_trades:
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
                    if current_price <= stop_loss:
                        should_exit = True
                        exit_reason = f"Stop loss hit (${stop_loss:,.2f})"
                    elif current_price >= take_profit:
                        should_exit = True
                        exit_reason = f"Take profit hit (${take_profit:,.2f})"
                else:  # sell
                    if current_price >= stop_loss:
                        should_exit = True
                        exit_reason = f"Stop loss hit (${stop_loss:,.2f})"
                    elif current_price <= take_profit:
                        should_exit = True
                        exit_reason = f"Take profit hit (${take_profit:,.2f})"

                if should_exit:
                    self._close_trade(trade, current_price, exit_reason)

            except Exception as e:
                _log("error", f"Error checking exit for trade {trade['id']}: {e}")

    def _close_trade(self, trade, exit_price: float, reason: str):
        """Close a trade and record P&L."""
        coin_info = COIN_MAP.get(trade["coin"])
        if coin_info:
            self.client.close_position(coin_info["blofin"])

        # Calculate P&L
        entry_price = trade["entry_price"]
        size = trade["size"]
        if trade["side"] == "buy":
            pnl = (exit_price - entry_price) * size
        else:
            pnl = (entry_price - exit_price) * size

        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        if trade["side"] == "sell":
            pnl_pct = -pnl_pct

        # Update DB
        conn = _get_db()
        conn.execute("""
            UPDATE bot_trades SET
                exit_price = ?, pnl = ?, pnl_pct = ?, status = 'closed', closed_at = ?
            WHERE id = ?
        """, (exit_price, round(pnl, 2), round(pnl_pct, 2), datetime.now().isoformat(), trade["id"]))
        conn.commit()
        conn.close()

        # Record in daily P&L
        self.risk_manager.record_trade_pnl(round(pnl, 2))

        _log("info", f"TRADE CLOSED: {trade['coin']} {trade['side']} | Entry: ${entry_price:,.2f} → Exit: ${exit_price:,.2f} | P&L: ${pnl:,.2f} ({pnl_pct:+.1f}%) | {reason}")

    def _record_rejected_signal(self, coin_key: str, signal: dict, price: float, validation: dict):
        """Log a rejected signal for review."""
        _log("info", f"Signal rejected: {signal['side']} {coin_key} @ ${price:,.2f} — {validation['summary']}",
             json.dumps({"signal": signal, "validation": validation}))


# Global bot instance
_bot_instance = None
_bot_lock = threading.Lock()


def get_bot() -> TradingBot:
    """Get or create the global bot instance."""
    global _bot_instance
    with _bot_lock:
        if _bot_instance is None:
            _bot_instance = TradingBot()
        return _bot_instance
