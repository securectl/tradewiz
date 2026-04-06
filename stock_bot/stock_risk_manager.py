"""
Stock Risk Manager — Safety gates for the stock trading bot.
Mirrors crypto risk_manager with stock-specific gates (PDT, market hours).
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
    row = query_one(f"SELECT value FROM bot_config WHERE user_id = {P} AND key = {P}", (user_id, key))
    return row["value"] if row else default


class StockRiskManager:
    """Validates every stock trade against safety rules before execution."""

    def __init__(self, user_id=None):
        self.user_id = user_id
        self.kill_switch_active = False

    def refresh_config(self) -> dict:
        rows = query(f"SELECT key, value FROM bot_config WHERE user_id = {P} AND key LIKE 'stock_%%'", (self.user_id,))
        return {r["key"]: r["value"] for r in rows}

    def is_kill_switch_active(self) -> bool:
        if self.kill_switch_active:
            return True
        val = _get_config_val(self.user_id, "stock_kill_switch", "0")
        return val == "1"

    def activate_kill_switch(self):
        self.kill_switch_active = True
        if IS_POSTGRES:
            execute(f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, 'stock_kill_switch', '1') ON CONFLICT(user_id, key) DO UPDATE SET value = '1'", (self.user_id,))
            execute(f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, 'stock_bot_enabled', '0') ON CONFLICT(user_id, key) DO UPDATE SET value = '0'", (self.user_id,))
        else:
            execute(f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, 'stock_kill_switch', '1')", (self.user_id,))
            execute(f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, 'stock_bot_enabled', '0')", (self.user_id,))
        logger.warning("STOCK KILL SWITCH ACTIVATED — bot stopped, all positions should be closed")

    def deactivate_kill_switch(self):
        self.kill_switch_active = False
        if IS_POSTGRES:
            execute(f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, 'stock_kill_switch', '0') ON CONFLICT(user_id, key) DO UPDATE SET value = '0'", (self.user_id,))
        else:
            execute(f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, 'stock_kill_switch', '0')", (self.user_id,))
        logger.info("Stock kill switch deactivated")

    def get_daily_pnl(self) -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            row = query_one(f"SELECT total_pnl FROM stock_daily_pnl WHERE user_id = {P} AND date = {P}", (self.user_id, today))
            if row:
                return float(row["total_pnl"])
        except Exception:
            pass
        row2 = query_one(
            f"SELECT COALESCE(SUM(pnl), 0) as total FROM bot_trades WHERE user_id = {P} AND asset_type = 'stock' AND status = 'closed' AND DATE(closed_at) = {P}",
            (self.user_id, today),
        )
        return float(row2["total"]) if row2 else 0.0

    def get_daily_trade_count(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            row = query_one(f"SELECT trade_count FROM stock_daily_pnl WHERE user_id = {P} AND date = {P}", (self.user_id, today))
            if row:
                return int(row["trade_count"])
        except Exception:
            pass
        row2 = query_one(
            f"SELECT COUNT(*) as cnt FROM bot_trades WHERE user_id = {P} AND asset_type = 'stock' AND DATE(opened_at) = {P}",
            (self.user_id, today),
        )
        return int(row2["cnt"]) if row2 else 0

    def get_open_positions_count(self) -> int:
        row = query_one(
            f"SELECT COUNT(*) as cnt FROM bot_trades WHERE user_id = {P} AND status = 'open' AND asset_type = 'stock'",
            (self.user_id,),
        )
        return row["cnt"] if row else 0

    def has_open_position(self, symbol: str) -> bool:
        row = query_one(
            f"SELECT COUNT(*) as cnt FROM bot_trades WHERE user_id = {P} AND coin = {P} AND status = 'open' AND asset_type = 'stock'",
            (self.user_id, symbol),
        )
        return row["cnt"] > 0 if row else False

    def can_open_position(self, symbol: str, size_usd: float, balance: float, broker_client=None, is_swing: bool = False) -> dict:
        """Check all risk gates. is_swing=True uses concentrated swing trade limits."""
        from stock_bot.broker_client import is_market_open

        if self.is_kill_switch_active():
            return {"allowed": False, "reason": "Kill switch is active"}

        bot_enabled = _get_config_val(self.user_id, "stock_bot_enabled", "0")
        if bot_enabled != "1":
            return {"allowed": False, "reason": "Stock bot is not enabled"}

        # Swing: higher daily loss tolerance (10% of equity)
        daily_limit = float(_get_config_val(self.user_id, "stock_daily_loss_limit", "2000" if is_swing else "500"))
        daily_pnl = self.get_daily_pnl()
        if daily_pnl <= -daily_limit:
            self.activate_kill_switch()
            return {"allowed": False, "reason": f"Daily loss limit breached (${daily_pnl:.2f} / -${daily_limit:.2f})"}

        max_daily = int(_get_config_val(self.user_id, "stock_max_daily_trades", "5" if is_swing else "25"))
        daily_trades = self.get_daily_trade_count()
        if daily_trades >= max_daily:
            return {"allowed": False, "reason": f"Daily trade limit reached ({daily_trades}/{max_daily})"}

        # Swing: 25% max position, 3 max open | Scalp: 10%, 8
        max_pct = float(_get_config_val(self.user_id, "stock_max_position_pct", "25" if is_swing else "10"))
        max_size = balance * (max_pct / 100)
        if size_usd > max_size:
            return {"allowed": False, "reason": f"Position size ${size_usd:.2f} exceeds {max_pct}% limit (${max_size:.2f})"}

        max_open = int(_get_config_val(self.user_id, "stock_max_open_positions", "3" if is_swing else "8"))
        current_open = self.get_open_positions_count()
        if current_open >= max_open:
            return {"allowed": False, "reason": f"Max open positions reached ({current_open}/{max_open})"}

        if self.has_open_position(symbol):
            return {"allowed": False, "reason": f"Already have an open position for {symbol}"}

        extended_hours = _get_config_val(self.user_id, "stock_extended_hours", "0") == "1"

        if broker_client:
            try:
                equity = broker_client.get_account_equity()
                day_trades = broker_client.get_day_trade_count()
                if equity < 25000 and day_trades >= 3:
                    return {"allowed": False, "reason": f"PDT rule: equity ${equity:,.2f} < $25K and {day_trades} day trades in 5 days"}
            except Exception as e:
                logger.warning(f"PDT check failed: {e}")

        market = is_market_open(extended_hours=extended_hours)
        if not market["is_open"]:
            return {"allowed": False, "reason": f"Market closed: {market['reason']}"}

        return {"allowed": True, "reason": "All risk gates passed"}

    def record_trade_pnl(self, pnl: float):
        today = datetime.now().strftime("%Y-%m-%d")
        is_win = 1 if pnl > 0 else 0
        is_loss = 1 if pnl < 0 else 0

        if IS_POSTGRES:
            execute(
                f"INSERT INTO stock_daily_pnl (user_id, date, total_pnl, trade_count, win_count, loss_count) "
                f"VALUES ({P}, {P}, {P}, 1, {P}, {P}) "
                f"ON CONFLICT(user_id, date) DO UPDATE SET total_pnl = stock_daily_pnl.total_pnl + {P}, trade_count = stock_daily_pnl.trade_count + 1, win_count = stock_daily_pnl.win_count + {P}, loss_count = stock_daily_pnl.loss_count + {P}",
                (self.user_id, today, pnl, is_win, is_loss, pnl, is_win, is_loss),
            )
        else:
            execute(
                f"INSERT INTO stock_daily_pnl (user_id, date, total_pnl, trade_count, win_count, loss_count) "
                f"VALUES ({P}, {P}, {P}, 1, {P}, {P}) "
                f"ON CONFLICT(user_id, date) DO UPDATE SET total_pnl = total_pnl + {P}, trade_count = trade_count + 1, win_count = win_count + {P}, loss_count = loss_count + {P}",
                (self.user_id, today, pnl, is_win, is_loss, pnl, is_win, is_loss),
            )

        daily_pnl = self.get_daily_pnl()
        daily_limit = float(_get_config_val(self.user_id, "stock_daily_loss_limit", "500"))
        if daily_pnl <= -daily_limit:
            logger.warning(f"Stock daily loss limit breached: ${daily_pnl:.2f} / -${daily_limit:.2f}")
            self.activate_kill_switch()
