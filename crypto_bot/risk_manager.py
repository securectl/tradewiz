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

    def get_coin_consecutive_losses(self, coin: str) -> int:
        """Count consecutive losses for a coin (most recent trades first)."""
        conn = _get_db()
        rows = conn.execute(
            "SELECT pnl FROM bot_trades WHERE coin = ? AND status = 'closed' ORDER BY id DESC LIMIT 20",
            (coin,),
        ).fetchall()
        conn.close()
        streak = 0
        for r in rows:
            if (r["pnl"] or 0) < 0:
                streak += 1
            else:
                break
        return streak

    def get_coin_daily_pnl(self, coin: str) -> float:
        """Get today's P&L for a specific coin."""
        conn = _get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) as total FROM bot_trades "
            "WHERE coin = ? AND status = 'closed' AND closed_at >= ?",
            (coin, today),
        ).fetchone()
        conn.close()
        return float(row["total"]) if row else 0.0

    def get_coin_recent_trade_time(self, coin: str) -> datetime | None:
        """Get the most recent trade close time for a coin."""
        conn = _get_db()
        row = conn.execute(
            "SELECT closed_at, opened_at FROM bot_trades WHERE coin = ? ORDER BY id DESC LIMIT 1",
            (coin,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        # Prefer closed_at (most recent activity), fall back to opened_at
        time_str = row["closed_at"] or row["opened_at"]
        if not time_str:
            return None
        try:
            return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%f")
        except ValueError:
            try:
                return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None

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

        # Gate 8: Per-coin consecutive loss circuit breaker
        # After 3 consecutive losses, pause this coin for 2 hours
        consec_losses = self.get_coin_consecutive_losses(coin)
        if consec_losses >= 3:
            last_trade_time = self.get_coin_recent_trade_time(coin)
            cooldown_hours = min(2 * (consec_losses // 3), 12)  # Escalating: 2h, 4h, 6h... max 12h
            if last_trade_time:
                hours_since = (datetime.now() - last_trade_time).total_seconds() / 3600
                if hours_since < cooldown_hours:
                    return {
                        "allowed": False,
                        "reason": f"{coin}: {consec_losses} consecutive losses — paused for {cooldown_hours}h ({hours_since:.1f}h elapsed)",
                    }

        # Gate 9: Per-coin daily loss cap ($30 per coin)
        coin_daily_pnl = self.get_coin_daily_pnl(coin)
        coin_daily_limit = 30.0
        if coin_daily_pnl <= -coin_daily_limit:
            return {
                "allowed": False,
                "reason": f"{coin}: Daily coin loss cap hit (${coin_daily_pnl:.2f} / -${coin_daily_limit:.2f})",
            }

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
