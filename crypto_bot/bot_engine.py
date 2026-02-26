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


def _journal_log(ticker: str, action: str, entry_price: float = None,
                  exit_price: float = None, shares: float = None,
                  pnl: float = None, notes: str = ""):
    """Log a trade to the Tracker journal for visibility."""
    try:
        conn = _get_db()
        conn.execute(
            "INSERT INTO journal_entries (ticker, notes, action, entry_price, exit_price, shares, pnl) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticker, notes, action, entry_price, exit_price, shares, pnl),
        )
        # Update weekly actuals if there's a P&L
        if pnl is not None:
            from datetime import timedelta
            now = datetime.now()
            monday = now - timedelta(days=now.weekday())
            week = monday.strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO weekly_goals (week_start, target_amount, actual_amount) VALUES (?, 0, ?) "
                "ON CONFLICT(week_start) DO UPDATE SET actual_amount = actual_amount + ?",
                (week, pnl, pnl),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to log to journal: {e}")


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
        self._last_summary_hour = -1

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
                self._log_hourly_summary()
            except Exception as e:
                _log("error", f"Scan cycle error: {e}", str(e))

            # Wait for the interval or stop event
            self._stop_event.wait(timeout=scan_interval)

        # Final summary on exit
        self._log_hourly_summary(force=True)
        _log("info", "Bot loop exited")

    def _log_hourly_summary(self, force=False):
        """Log a performance summary once per hour (or on force)."""
        try:
            current_hour = datetime.now().hour
            if not force and current_hour == self._last_summary_hour:
                return
            self._last_summary_hour = current_hour

            perf = _get_trade_performance()
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
            _log("info", summary)
        except Exception:
            pass

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
        # Enforce minimum ATR of 1% of price (wider buffer for low-priced coins)
        min_atr = current_price * 0.01
        effective_atr = max(atr, min_atr)
        if signal["side"] == "buy":
            stop_loss = current_price - (2.0 * effective_atr)
            take_profit = current_price + (3.0 * effective_atr)
        else:
            stop_loss = current_price + (2.0 * effective_atr)
            take_profit = current_price - (3.0 * effective_atr)

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
            _journal_log(
                ticker=coin_key, action="BUY" if signal["side"] == "buy" else "SELL",
                entry_price=current_price, shares=round(size_coins, 6),
                notes=f"[Crypto Bot] {signal['side'].upper()} — {signal['reason']}",
            )
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
                _journal_log(
                    ticker=coin_key, action="BUY" if signal["side"] == "buy" else "SELL",
                    entry_price=current_price, shares=round(size_coins, 6),
                    notes=f"[Crypto Bot] PAPER {signal['side'].upper()} — {signal['reason']}",
                )

    def _generate_signal(self, coin_key: str, indicators: dict, df) -> dict:
        """Generate BUY/SELL signal using multiple strategies.

        Strategies (any one can trigger):
        1. MACD Crossover — bullish/bearish cross with trend confirmation
        2. EMA Trend — SMA8/EMA20 crossover with momentum
        3. RSI Mean Reversion — oversold bounce / overbought rejection
        4. Momentum Breakout — strong move with volume confirmation
        5. Bollinger Band Reversion — price touches lower/upper band with RSI confirm
        6. Grid Mean Reversion — price at ATR-based support/resistance levels
        7. Trend Continuation (DCA-style) — add to winning trend on pullback

        Filters applied before any strategy:
        - MACD histogram must exceed minimum strength (0.05% of price)
        - RSI must NOT be in dead zone (45-60) for MACD/EMA strategies
        - ATR must be > 0.3% of price (skip ranging/choppy markets)
        - Per-coin cooldown: no re-entry within 30 min of last trade on same coin
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
        sma_200 = indicators.get("sma_200", 0)
        rel_vol = indicators.get("relative_volume", 0)
        atr = indicators.get("atr_14", 0)
        bb_upper = indicators.get("bb_upper", 0)
        bb_lower = indicators.get("bb_lower", 0)
        bb_mid = indicators.get("bb_mid", 0)
        bb_width = indicators.get("bb_width", 0)
        current_price = float(df["Close"].iloc[-1])

        # === PRE-FILTERS ===

        # Filter A: ATR must be meaningful (> 0.3% of price) — skip choppy/flat markets
        atr_pct = (atr / current_price * 100) if current_price > 0 else 0
        if atr_pct < 0.3:
            _log("info", f"{coin_key}: ATR too low ({atr_pct:.2f}% of price) — market choppy, skipping")
            return None

        # Filter A2: Spread/slippage guard for low-priced coins
        # On low-priced coins (< $1), the minimum tick spread eats a huge % of the trade.
        # Require ATR to be at least 0.5% for coins under $1, 0.8% for coins under $0.50
        if current_price < 0.50 and atr_pct < 0.8:
            _log("info", f"{coin_key}: Low-price coin (${current_price:.4f}) with thin ATR ({atr_pct:.2f}%) — spread will eat profit, skipping")
            return None
        if current_price < 1.0 and atr_pct < 0.5:
            _log("info", f"{coin_key}: Sub-$1 coin (${current_price:.4f}) with low ATR ({atr_pct:.2f}%) — spread risk, skipping")
            return None

        # Filter B: MACD histogram relative strength
        macd_pct = abs(macd_hist) / current_price * 100 if current_price > 0 else 0

        # Filter C: Per-coin cooldown — use closed_at for proper timing
        # After a loss: 45 min cooldown. After a win: 20 min. After consecutive losses: escalating.
        try:
            last_trade_time = self.risk_manager.get_coin_recent_trade_time(coin_key)
            if last_trade_time:
                minutes_since = (datetime.now() - last_trade_time).total_seconds() / 60
                consec_losses = self.risk_manager.get_coin_consecutive_losses(coin_key)
                if consec_losses >= 3:
                    cooldown = 120  # 2 hours after 3+ consecutive losses
                elif consec_losses >= 1:
                    cooldown = 45   # 45 min after a loss
                else:
                    cooldown = 20   # 20 min after a win
                if 0 < minutes_since < cooldown:
                    _log("info", f"{coin_key}: Cooldown active ({minutes_since:.0f}/{cooldown} min, {consec_losses} consec losses)")
                    return None
        except Exception:
            pass

        # --- Strategy 1: MACD Crossover ---
        # Tightened: require stronger MACD signal (0.05%), narrower RSI window, and trend alignment
        if macd_bull_cross and macd_pct >= 0.05 and rsi < 60 and rsi > 35 and current_price > ema_20 and current_price > sma_50:
            return {
                "side": "buy",
                "reason": f"MACD bullish crossover (hist={macd_hist:.6f}, {macd_pct:.3f}%) + RSI={rsi:.1f} + price above EMA20 & SMA50",
                "strategy": "macd_cross",
            }
        if macd_bear_cross and macd_pct >= 0.05 and rsi > 40 and rsi < 65 and current_price < ema_20 and current_price < sma_50:
            return {
                "side": "sell",
                "reason": f"MACD bearish crossover (hist={macd_hist:.6f}, {macd_pct:.3f}%) + RSI={rsi:.1f} + price below EMA20 & SMA50",
                "strategy": "macd_cross",
            }

        # --- Strategy 2: EMA Trend (SMA8/EMA20 cross) ---
        # Tightened: require MACD confirmation + volume above average
        if sma8_ema20_cross:
            if sma8_above_ema20 and rsi < 60 and rsi > 35 and macd_hist > 0 and rel_vol > 0.8:
                return {
                    "side": "buy",
                    "reason": f"SMA8 crossed above EMA20 (trend shift bullish) + RSI={rsi:.1f} + MACD positive",
                    "strategy": "ema_trend",
                }
            if not sma8_above_ema20 and rsi > 40 and rsi < 65 and macd_hist < 0 and rel_vol > 0.8:
                return {
                    "side": "sell",
                    "reason": f"SMA8 crossed below EMA20 (trend shift bearish) + RSI={rsi:.1f} + MACD negative",
                    "strategy": "ema_trend",
                }

        # --- Strategy 3: RSI Mean Reversion ---
        # Tightened: deeper oversold/overbought thresholds + price must confirm
        if rsi < 30 and macd_hist > 0 and current_price > bb_lower:
            return {
                "side": "buy",
                "reason": f"RSI oversold bounce ({rsi:.1f}) + MACD momentum positive ({macd_hist:.6f}) + above BB lower",
                "strategy": "rsi_reversion",
            }
        if rsi > 70 and macd_hist < 0 and current_price < sma_8 and current_price < bb_upper:
            return {
                "side": "sell",
                "reason": f"RSI overbought fade ({rsi:.1f}) + MACD momentum negative ({macd_hist:.6f}) + below SMA8 & BB upper",
                "strategy": "rsi_reversion",
            }

        # --- Strategy 4: Momentum Breakout with volume ---
        # Tightened: require stronger volume surge (1.8x) and SMA alignment
        if current_price > sma_50 and current_price > sma_200 and rel_vol > 1.8 and 50 < rsi < 70 and macd_hist > 0:
            return {
                "side": "buy",
                "reason": f"Momentum breakout: price above SMA50/200 + volume surge ({rel_vol:.1f}x) + RSI={rsi:.1f}",
                "strategy": "momentum",
            }
        if current_price < sma_50 and current_price < sma_200 and rel_vol > 1.8 and 30 < rsi < 50 and macd_hist < 0:
            return {
                "side": "sell",
                "reason": f"Bearish momentum: price below SMA50/200 + volume surge ({rel_vol:.1f}x) + RSI={rsi:.1f}",
                "strategy": "momentum",
            }

        # --- Strategy 5: Bollinger Band Reversion ---
        if bb_lower > 0 and bb_upper > 0:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_position = (current_price - bb_lower) / bb_range

                # Tightened: require band touch (<=0.05 / >=0.95), wider BB, and stronger RSI
                if bb_position <= 0.05 and rsi < 35 and macd_hist > 0 and bb_width > 2.0:
                    return {
                        "side": "buy",
                        "reason": f"BB lower band touch (BB%={bb_position:.0%}, width={bb_width:.1f}%) + RSI={rsi:.1f} + MACD turning up",
                        "strategy": "bb_reversion",
                    }
                if bb_position >= 0.95 and rsi > 65 and macd_hist < 0 and bb_width > 2.0:
                    return {
                        "side": "sell",
                        "reason": f"BB upper band touch (BB%={bb_position:.0%}, width={bb_width:.1f}%) + RSI={rsi:.1f} + MACD turning down",
                        "strategy": "bb_reversion",
                    }

        # --- Strategy 6: Grid Mean Reversion (ATR-based) ---
        if atr > 0 and sma_50 > 0:
            atr_dist = (current_price - sma_50) / atr

            # Tightened: require deeper deviation (2x ATR) and stronger RSI confirmation
            if atr_dist <= -2.0 and rsi < 40 and sma_50 > sma_200 and macd_hist > 0:
                return {
                    "side": "buy",
                    "reason": f"Grid support: price {abs(atr_dist):.1f}x ATR below SMA50 (uptrend pullback) + RSI={rsi:.1f}",
                    "strategy": "grid_reversion",
                }
            if atr_dist >= 2.0 and rsi > 60 and sma_50 < sma_200 and macd_hist < 0:
                return {
                    "side": "sell",
                    "reason": f"Grid resistance: price {atr_dist:.1f}x ATR above SMA50 (downtrend rally) + RSI={rsi:.1f}",
                    "strategy": "grid_reversion",
                }

        # --- Strategy 7: Trend Continuation / DCA-style re-entry ---
        # If overall trend is strong (SMA50 > SMA200) and price pulls back to EMA20, buy the dip
        # Inspired by BloFin DCA bot — add to position at better price levels
        # Tightened: require volume confirmation and narrower RSI ranges
        if sma_50 > sma_200 and current_price > sma_200:
            ema20_dist_pct = abs(current_price - ema_20) / ema_20 * 100 if ema_20 > 0 else 999
            if ema20_dist_pct < 0.8 and rsi > 40 and rsi < 55 and macd_hist > 0 and rel_vol > 0.8:
                return {
                    "side": "buy",
                    "reason": f"Trend continuation: pullback to EMA20 (dist={ema20_dist_pct:.2f}%) in uptrend (SMA50>200) + RSI={rsi:.1f}",
                    "strategy": "trend_dca",
                }
        if sma_50 < sma_200 and current_price < sma_200:
            ema20_dist_pct = abs(current_price - ema_20) / ema_20 * 100 if ema_20 > 0 else 999
            if ema20_dist_pct < 0.5 and rsi > 55 and rsi < 65 and macd_hist < 0 and rel_vol > 0.8:
                return {
                    "side": "sell",
                    "reason": f"Trend continuation: rally to EMA20 (dist={ema20_dist_pct:.2f}%) in downtrend (SMA50<200) + RSI={rsi:.1f}",
                    "strategy": "trend_dca",
                }

        return None

    def _check_exits(self):
        """Check open trades for exit conditions.

        Exit rules (inspired by BloFin bot patterns):
        1. Hard stop loss (ATR-based)
        2. Take profit target
        3. Trailing stop: once trade is 1.5% in profit, trail at 50% of max gain
        4. Time-based exit: close after 24h if P&L is between -0.3% and +0.3% (stale trade)
        """
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

                # Calculate current P&L percentage
                if trade["side"] == "buy":
                    pnl_pct = (current_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - current_price) / entry_price * 100

                # Rule 1 & 2: Hard SL/TP
                if trade["side"] == "buy":
                    if current_price <= stop_loss:
                        should_exit = True
                        exit_reason = f"Stop loss hit (${stop_loss:,.2f})"
                    elif current_price >= take_profit:
                        should_exit = True
                        exit_reason = f"Take profit hit (${take_profit:,.2f})"
                else:
                    if current_price >= stop_loss:
                        should_exit = True
                        exit_reason = f"Stop loss hit (${stop_loss:,.2f})"
                    elif current_price <= take_profit:
                        should_exit = True
                        exit_reason = f"Take profit hit (${take_profit:,.2f})"

                # Rule 3: Trailing stop — if in profit > 1.5%, trail at 50% of peak
                if not should_exit and pnl_pct >= 1.5:
                    # Calculate what max P&L could have been based on TP distance
                    tp_pct = abs(take_profit - entry_price) / entry_price * 100 if entry_price > 0 else 3
                    # If price has retraced more than 50% from the TP target, lock profit
                    if trade["side"] == "buy":
                        max_possible = take_profit
                        retracement = (max_possible - current_price) / (max_possible - entry_price) if max_possible != entry_price else 0
                    else:
                        max_possible = take_profit
                        retracement = (current_price - max_possible) / (entry_price - max_possible) if entry_price != max_possible else 0

                    if retracement > 0.50 and pnl_pct > 0.5:
                        should_exit = True
                        exit_reason = f"Trailing stop: P&L was {pnl_pct:.1f}%, retraced {retracement:.0%} from target"

                # Rule 4: Time-based exit — close stale trades after 24h
                if not should_exit:
                    try:
                        opened = datetime.strptime(trade["opened_at"], "%Y-%m-%d %H:%M:%S")
                        hours_open = (datetime.now() - opened).total_seconds() / 3600
                        if hours_open >= 24 and abs(pnl_pct) < 0.3:
                            should_exit = True
                            exit_reason = f"Time exit: open {hours_open:.0f}h with only {pnl_pct:+.2f}% P&L (stale)"
                    except Exception:
                        pass

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

        # Log close to Tracker journal
        close_action = "SELL" if trade["side"] == "buy" else "BUY"
        _journal_log(
            ticker=trade["coin"], action=close_action,
            entry_price=entry_price, exit_price=exit_price,
            shares=size, pnl=round(pnl, 2),
            notes=f"[Crypto Bot] Closed — {reason} | P&L: ${pnl:,.2f} ({pnl_pct:+.1f}%)",
        )

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
