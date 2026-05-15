"""
Risk Manager — Safety gates for the crypto trading bot.
Enforces position limits, daily loss caps, kill switch, and paper-only mode.
"""

import os
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db import IS_POSTGRES, query, query_one, execute

P = "%s" if IS_POSTGRES else "?"


def _get_config_val(user_id, key, default=None):
    # User-specific row wins; falls back to admin-set global row (user_id=0).
    row = query_one(
        f"SELECT value FROM bot_config WHERE user_id IN ({P}, 0) AND key = {P} "
        f"ORDER BY CASE WHEN user_id = 0 THEN 1 ELSE 0 END LIMIT 1",
        (user_id, key),
    )
    return row["value"] if row else default


class RiskManager:
    """Validates every trade against safety rules before execution."""

    def __init__(self, user_id=None):
        self.user_id = user_id
        self.kill_switch_active = False

    def refresh_config(self) -> dict:
        rows = query(f"SELECT key, value FROM bot_config WHERE user_id = {P}", (self.user_id,))
        return {r["key"]: r["value"] for r in rows}

    def is_kill_switch_active(self) -> bool:
        if self.kill_switch_active:
            return True
        val = _get_config_val(self.user_id, "kill_switch", "0")
        return val == "1"

    def activate_kill_switch(self):
        self.kill_switch_active = True
        if IS_POSTGRES:
            execute(f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, 'kill_switch', '1') ON CONFLICT(user_id, key) DO UPDATE SET value = '1'", (self.user_id,))
            execute(f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, 'bot_enabled', '0') ON CONFLICT(user_id, key) DO UPDATE SET value = '0'", (self.user_id,))
        else:
            execute(f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, 'kill_switch', '1')", (self.user_id,))
            execute(f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, 'bot_enabled', '0')", (self.user_id,))
        logger.warning("KILL SWITCH ACTIVATED — bot stopped, all positions should be closed")

    def deactivate_kill_switch(self):
        self.kill_switch_active = False
        if IS_POSTGRES:
            execute(f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, 'kill_switch', '0') ON CONFLICT(user_id, key) DO UPDATE SET value = '0'", (self.user_id,))
        else:
            execute(f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, 'kill_switch', '0')", (self.user_id,))
        logger.info("Kill switch deactivated")

    def get_daily_pnl(self) -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        row = query_one(f"SELECT total_pnl FROM bot_daily_pnl WHERE user_id = {P} AND date = {P}", (self.user_id, today))
        return float(row["total_pnl"]) if row else 0.0

    def get_daily_trade_count(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        row = query_one(f"SELECT trade_count FROM bot_daily_pnl WHERE user_id = {P} AND date = {P}", (self.user_id, today))
        return int(row["trade_count"]) if row else 0

    def get_open_positions_count(self) -> int:
        row = query_one(
            f"SELECT COUNT(*) as cnt FROM bot_trades WHERE user_id = {P} AND status = 'open' AND asset_type = 'crypto'",
            (self.user_id,),
        )
        return row["cnt"] if row else 0

    def get_open_positions_value(self) -> float:
        """Sum of notional value (size * entry_price) of crypto open positions.
        Used by the total-exposure gate to cap aggregate funds deployed.
        Must filter by asset_type='crypto' — without it, the gate counts
        watchdog/claude stock positions and blocks every crypto signal once
        any stock bot has positions open (incident May 2026)."""
        row = query_one(
            f"SELECT COALESCE(SUM(size * entry_price), 0) as val FROM bot_trades "
            f"WHERE user_id = {P} AND status = 'open' AND asset_type = 'crypto'",
            (self.user_id,),
        )
        return float(row["val"]) if row else 0.0

    def has_open_position(self, coin: str) -> bool:
        row = query_one(
            f"SELECT COUNT(*) as cnt FROM bot_trades WHERE user_id = {P} AND coin = {P} AND status = 'open' AND asset_type = 'crypto'",
            (self.user_id, coin),
        )
        return row["cnt"] > 0 if row else False

    def get_coin_consecutive_losses(self, coin: str) -> int:
        rows = query(f"SELECT pnl FROM bot_trades WHERE user_id = {P} AND coin = {P} AND status = 'closed' ORDER BY id DESC LIMIT 20", (self.user_id, coin))
        streak = 0
        for r in rows:
            if (r["pnl"] or 0) < 0:
                streak += 1
            else:
                break
        return streak

    def get_coin_daily_pnl(self, coin: str) -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        row = query_one(
            f"SELECT COALESCE(SUM(pnl), 0) as total FROM bot_trades WHERE user_id = {P} AND coin = {P} AND status = 'closed' AND closed_at >= {P}",
            (self.user_id, coin, today),
        )
        return float(row["total"]) if row else 0.0

    def get_coin_recent_trade_time(self, coin: str):
        row = query_one(
            f"SELECT closed_at, opened_at FROM bot_trades WHERE user_id = {P} AND coin = {P} ORDER BY id DESC LIMIT 1",
            (self.user_id, coin),
        )
        if not row:
            return None
        time_str = str(row["closed_at"] or row["opened_at"] or "")
        if not time_str:
            return None
        try:
            return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%f")
        except ValueError:
            try:
                return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    return datetime.fromisoformat(time_str)
                except Exception:
                    return None

    def can_open_position(self, coin: str, size_usd: float, balance: float, is_swing: bool = False) -> dict:
        """Check all risk gates before opening a position.
        is_swing: If True, uses swing trade limits (higher sizing, fewer positions, higher daily loss cap)."""
        if self.is_kill_switch_active():
            return {"allowed": False, "reason": "Kill switch is active"}

        bot_enabled = _get_config_val(self.user_id, "bot_enabled", "0")
        if bot_enabled != "1":
            return {"allowed": False, "reason": "Bot is not enabled"}

        if os.getenv("BLOFIN_DEMO", "1") != "1":
            return {"allowed": False, "reason": "SAFETY: Only paper trading (demo mode) is allowed"}

        # Swing trades get higher daily loss limit (10% of equity vs fixed $500)
        daily_limit = float(_get_config_val(self.user_id, "daily_loss_limit", "2000" if is_swing else "500"))
        daily_pnl = self.get_daily_pnl()
        if daily_pnl <= -daily_limit:
            self.activate_kill_switch()
            return {"allowed": False, "reason": f"Daily loss limit breached (${daily_pnl:.2f} / -${daily_limit:.2f})"}

        max_daily = int(_get_config_val(self.user_id, "max_daily_trades", "10" if is_swing else "25"))
        daily_trades = self.get_daily_trade_count()
        if daily_trades >= max_daily:
            return {"allowed": False, "reason": f"Daily trade limit reached ({daily_trades}/{max_daily})"}

        # Swing: 25% position, 3 max open | Scalp: 10% position, 6 max open
        max_pct = float(_get_config_val(self.user_id, "max_position_pct", "25" if is_swing else "10"))
        max_size = balance * (max_pct / 100)
        if size_usd > max_size:
            return {"allowed": False, "reason": f"Position size ${size_usd:.2f} exceeds {max_pct}% limit (${max_size:.2f})"}

        # Total exposure gate — cap sum of open positions as % of equity. Stops
        # the bot from running fully loaded across every signal it sees. Default
        # 60% leaves a 40% cushion for new entries + drawdown breathing room.
        max_exp_pct = float(_get_config_val(self.user_id, "max_total_exposure_pct", "60"))
        if balance > 0 and max_exp_pct > 0:
            deployed = self.get_open_positions_value()
            projected = deployed + size_usd
            cap = balance * (max_exp_pct / 100)
            if projected > cap:
                return {"allowed": False, "reason": f"Total exposure ${projected:.0f} would exceed {max_exp_pct}% cap (${cap:.0f}) — already deployed ${deployed:.0f}"}

        max_open = int(_get_config_val(self.user_id, "max_open_positions", "3" if is_swing else "6"))
        current_open = self.get_open_positions_count()
        if current_open >= max_open:
            return {"allowed": False, "reason": f"Max open positions reached ({current_open}/{max_open})"}

        if self.has_open_position(coin):
            return {"allowed": False, "reason": f"Already have an open position for {coin}"}

        # Swing trades have longer cooldowns
        consec_losses = self.get_coin_consecutive_losses(coin)
        cooldown_threshold = 3 if is_swing else 5
        if consec_losses >= cooldown_threshold:
            last_trade_time = self.get_coin_recent_trade_time(coin)
            cooldown_hours = min(4 * (consec_losses // cooldown_threshold), 24) if is_swing else min(1 * (consec_losses // 5), 4)
            if last_trade_time:
                hours_since = (datetime.now() - last_trade_time).total_seconds() / 3600
                if hours_since < cooldown_hours:
                    return {"allowed": False, "reason": f"{coin}: {consec_losses} consecutive losses — paused for {cooldown_hours}h ({hours_since:.1f}h elapsed)"}

        # Per-coin daily loss cap (higher for swing)
        coin_daily_pnl = self.get_coin_daily_pnl(coin)
        coin_daily_limit = 200.0 if is_swing else 75.0
        if coin_daily_pnl <= -coin_daily_limit:
            return {"allowed": False, "reason": f"{coin}: Daily coin loss cap hit (${coin_daily_pnl:.2f} / -${coin_daily_limit:.2f})"}

        return {"allowed": True, "reason": "All risk gates passed"}

    def record_trade_pnl(self, pnl: float):
        today = datetime.now().strftime("%Y-%m-%d")
        is_win = 1 if pnl > 0 else 0
        is_loss = 1 if pnl < 0 else 0

        if IS_POSTGRES:
            execute(
                f"INSERT INTO bot_daily_pnl (user_id, date, total_pnl, trade_count, win_count, loss_count) "
                f"VALUES ({P}, {P}, {P}, 1, {P}, {P}) "
                f"ON CONFLICT(user_id, date) DO UPDATE SET total_pnl = bot_daily_pnl.total_pnl + {P}, trade_count = bot_daily_pnl.trade_count + 1, win_count = bot_daily_pnl.win_count + {P}, loss_count = bot_daily_pnl.loss_count + {P}",
                (self.user_id, today, pnl, is_win, is_loss, pnl, is_win, is_loss),
            )
        else:
            execute(
                f"INSERT INTO bot_daily_pnl (user_id, date, total_pnl, trade_count, win_count, loss_count) "
                f"VALUES ({P}, {P}, {P}, 1, {P}, {P}) "
                f"ON CONFLICT(user_id, date) DO UPDATE SET total_pnl = total_pnl + {P}, trade_count = trade_count + 1, win_count = win_count + {P}, loss_count = loss_count + {P}",
                (self.user_id, today, pnl, is_win, is_loss, pnl, is_win, is_loss),
            )

        daily_pnl = self.get_daily_pnl()
        daily_limit = float(_get_config_val(self.user_id, "daily_loss_limit", "500"))
        if daily_pnl <= -daily_limit:
            logger.warning(f"Daily loss limit breached: ${daily_pnl:.2f} / -${daily_limit:.2f}")
            self.activate_kill_switch()
