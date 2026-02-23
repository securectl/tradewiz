"""
Risk Manager — Safety gates for the crypto trading bot.
Enforces position limits, daily loss caps, kill switch, and paper-only mode.
"""

import os
import sqlite3
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "searches.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_config(conn, key, default=None):
    row = conn.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


class RiskManager:
    """Validates every trade against safety rules before execution."""

    def __init__(self):
        self.kill_switch_active = False

    def refresh_config(self) -> dict:
        """Load current risk config from DB."""
        conn = _get_db()
        config = {}
        rows = conn.execute("SELECT key, value FROM bot_config").fetchall()
        for r in rows:
            config[r["key"]] = r["value"]
        conn.close()
        return config

    def is_kill_switch_active(self) -> bool:
        """Check if kill switch is engaged."""
        if self.kill_switch_active:
            return True
        conn = _get_db()
        val = _get_config(conn, "kill_switch", "0")
        conn.close()
        return val == "1"

    def activate_kill_switch(self):
        """Engage the kill switch."""
        self.kill_switch_active = True
        conn = _get_db()
        conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('kill_switch', '1')")
        conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('bot_enabled', '0')")
        conn.commit()
        conn.close()
        logger.warning("KILL SWITCH ACTIVATED — bot stopped, all positions should be closed")

    def deactivate_kill_switch(self):
        """Disengage the kill switch."""
        self.kill_switch_active = False
        conn = _get_db()
        conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('kill_switch', '0')")
        conn.commit()
        conn.close()
        logger.info("Kill switch deactivated")

    def get_daily_pnl(self) -> float:
        """Get today's total P&L."""
        conn = _get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        row = conn.execute("SELECT total_pnl FROM bot_daily_pnl WHERE date = ?", (today,)).fetchone()
        conn.close()
        return float(row["total_pnl"]) if row else 0.0

    def get_open_positions_count(self) -> int:
        """Count open trades in the DB."""
        conn = _get_db()
        row = conn.execute("SELECT COUNT(*) as cnt FROM bot_trades WHERE status = 'open'").fetchone()
        conn.close()
        return row["cnt"] if row else 0

    def has_open_position(self, coin: str) -> bool:
        """Check if we already have an open position for this coin."""
        conn = _get_db()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM bot_trades WHERE coin = ? AND status = 'open'",
            (coin,)
        ).fetchone()
        conn.close()
        return row["cnt"] > 0 if row else False

    def can_open_position(self, coin: str, size_usd: float, balance: float) -> dict:
        """Check all risk gates before opening a position.

        Returns:
            dict with 'allowed' (bool), 'reason' (str if blocked)
        """
        # Gate 1: Kill switch
        if self.is_kill_switch_active():
            return {"allowed": False, "reason": "Kill switch is active"}

        # Gate 2: Bot enabled
        conn = _get_db()
        bot_enabled = _get_config(conn, "bot_enabled", "0")
        if bot_enabled != "1":
            conn.close()
            return {"allowed": False, "reason": "Bot is not enabled"}

        # Gate 3: Paper only
        if os.getenv("BLOFIN_DEMO", "1") != "1":
            conn.close()
            return {"allowed": False, "reason": "SAFETY: Only paper trading (demo mode) is allowed"}

        # Gate 4: Daily loss limit
        daily_limit = float(_get_config(conn, "daily_loss_limit", "250"))
        daily_pnl = self.get_daily_pnl()
        if daily_pnl <= -daily_limit:
            conn.close()
            self.activate_kill_switch()
            return {"allowed": False, "reason": f"Daily loss limit breached (${daily_pnl:.2f} / -${daily_limit:.2f})"}

        # Gate 5: Max position size
        max_pct = float(_get_config(conn, "max_position_pct", "10"))
        max_size = balance * (max_pct / 100)
        if size_usd > max_size:
            conn.close()
            return {"allowed": False, "reason": f"Position size ${size_usd:.2f} exceeds {max_pct}% limit (${max_size:.2f})"}

        # Gate 6: Max open positions
        max_open = int(_get_config(conn, "max_open_positions", "3"))
        current_open = self.get_open_positions_count()
        if current_open >= max_open:
            conn.close()
            return {"allowed": False, "reason": f"Max open positions reached ({current_open}/{max_open})"}

        # Gate 7: No duplicate positions
        if self.has_open_position(coin):
            conn.close()
            return {"allowed": False, "reason": f"Already have an open position for {coin}"}

        conn.close()
        return {"allowed": True, "reason": "All risk gates passed"}

    def record_trade_pnl(self, pnl: float):
        """Update daily P&L tracking."""
        conn = _get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        is_win = 1 if pnl > 0 else 0
        is_loss = 1 if pnl < 0 else 0

        conn.execute("""
            INSERT INTO bot_daily_pnl (date, total_pnl, trade_count, win_count, loss_count)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_pnl = total_pnl + ?,
                trade_count = trade_count + 1,
                win_count = win_count + ?,
                loss_count = loss_count + ?
        """, (today, pnl, is_win, is_loss, pnl, is_win, is_loss))
        conn.commit()
        conn.close()

        # Check if daily loss limit breached after this trade
        daily_pnl = self.get_daily_pnl()
        config = self.refresh_config()
        daily_limit = float(config.get("daily_loss_limit", "250"))
        if daily_pnl <= -daily_limit:
            logger.warning(f"Daily loss limit breached: ${daily_pnl:.2f} / -${daily_limit:.2f}")
            self.activate_kill_switch()
