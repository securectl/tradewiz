"""
Database schema migrations.
Run directly: python migrations.py
Creates all tables in PostgreSQL or SQLite depending on DATABASE_URL.
"""

import os
import sys
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

from db import get_db, put_db, IS_POSTGRES


def _exec(conn, sql):
    """Execute a statement, ignoring errors (idempotent)."""
    try:
        if IS_POSTGRES:
            cur = conn.cursor()
            cur.execute(sql)
            cur.close()
        else:
            conn.execute(sql)
    except Exception as e:
        logger.debug(f"Skipped (already exists?): {e}")


def run_migrations():
    conn = get_db()
    try:
        if IS_POSTGRES:
            _run_postgres(conn)
        else:
            _run_sqlite(conn)
        conn.commit()
        logger.info("Migrations complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        put_db(conn)


def _run_postgres(conn):
    cur = conn.cursor()

    # ── Users & Auth ─────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            google_id TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            picture_url TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            last_login TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            granted_by INTEGER REFERENCES users(id),
            granted_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (user_id, role)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_api_keys (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            key_name TEXT NOT NULL,
            encrypted_value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, provider, key_name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invites (
            email TEXT PRIMARY KEY,
            role TEXT NOT NULL DEFAULT 'trader',
            invited_by INTEGER REFERENCES users(id),
            invited_at TIMESTAMP DEFAULT NOW(),
            accepted_at TIMESTAMP
        )
    """)

    # ── Search History ───────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS searches (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            ticker TEXT NOT NULL,
            period TEXT NOT NULL,
            interval_val TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ticker ON searches(ticker)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_expires ON searches(expires_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_searches_user ON searches(user_id)")

    # ── Tracker ──────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            ticker TEXT NOT NULL,
            notes TEXT DEFAULT '',
            action TEXT,
            entry_price REAL,
            exit_price REAL,
            shares REAL,
            pnl REAL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_user ON journal_entries(user_id)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS weekly_goals (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            week_start TEXT NOT NULL,
            target_amount REAL NOT NULL,
            actual_amount REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, week_start)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS account_config (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )
    """)

    # ── Status Service ───────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS service_checks (
            id SERIAL PRIMARY KEY,
            service_name TEXT NOT NULL,
            status TEXT NOT NULL,
            response_time_ms INTEGER DEFAULT 0,
            error_message TEXT,
            checked_at TIMESTAMP NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sc_service ON service_checks(service_name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sc_checked ON service_checks(checked_at)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS service_incidents (
            id SERIAL PRIMARY KEY,
            service_name TEXT NOT NULL,
            incident_type TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            resolved_at TIMESTAMP,
            duration_seconds INTEGER,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # ── Bot Config & Trades ──────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_trades (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            coin TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            pnl_pct REAL,
            status TEXT NOT NULL DEFAULT 'open',
            signal_reason TEXT,
            validation_result TEXT,
            stop_loss REAL,
            take_profit REAL,
            opened_at TIMESTAMP DEFAULT NOW(),
            closed_at TIMESTAMP,
            blofin_order_id TEXT,
            strategy TEXT,
            asset_type TEXT DEFAULT 'crypto',
            direction_bias TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bt_user ON bot_trades(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bt_status ON bot_trades(status)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_daily_pnl (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            total_pnl REAL DEFAULT 0,
            trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            level TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            details TEXT,
            source TEXT DEFAULT 'crypto',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bl_user ON bot_log(user_id)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily_pnl (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            total_pnl REAL DEFAULT 0,
            trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date)
        )
    """)

    cur.close()
    logger.info("PostgreSQL tables created.")


def _run_sqlite(conn):
    """SQLite schema — mirrors the original init_db() plus new auth tables."""
    from crypto_bot.blofin_client import DEFAULT_COINS
    from stock_bot.broker_client import DEFAULT_STOCKS

    # ── Users & Auth (lite versions, no FK enforcement) ──────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            picture_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER,
            role TEXT NOT NULL,
            granted_by INTEGER,
            granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, role)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            provider TEXT NOT NULL,
            key_name TEXT NOT NULL,
            encrypted_value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, provider, key_name)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS invites (
            email TEXT PRIMARY KEY,
            role TEXT NOT NULL DEFAULT 'trader',
            invited_by INTEGER,
            invited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            accepted_at TIMESTAMP
        )
    """)

    # ── Original tables (with user_id added) ─────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ticker TEXT NOT NULL,
            period TEXT NOT NULL,
            interval_val TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ticker ON searches(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON searches(expires_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ticker TEXT NOT NULL,
            notes TEXT DEFAULT '',
            action TEXT,
            entry_price REAL,
            exit_price REAL,
            shares REAL,
            pnl REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            week_start TEXT NOT NULL,
            target_amount REAL NOT NULL,
            actual_amount REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, week_start)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_config (
            user_id INTEGER,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )
    """)

    # ── Status tables (global) ───────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT NOT NULL,
            status TEXT NOT NULL,
            response_time_ms INTEGER DEFAULT 0,
            error_message TEXT,
            checked_at TIMESTAMP NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sc_service ON service_checks(service_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sc_checked ON service_checks(checked_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT NOT NULL,
            incident_type TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            resolved_at TIMESTAMP,
            duration_seconds INTEGER,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Bot tables (with user_id) ────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            user_id INTEGER,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            coin TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            pnl_pct REAL,
            status TEXT NOT NULL DEFAULT 'open',
            signal_reason TEXT,
            validation_result TEXT,
            stop_loss REAL,
            take_profit REAL,
            opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP,
            blofin_order_id TEXT,
            strategy TEXT,
            asset_type TEXT DEFAULT 'crypto',
            direction_bias TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_daily_pnl (
            user_id INTEGER,
            date TEXT NOT NULL,
            total_pnl REAL DEFAULT 0,
            trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            level TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            details TEXT,
            source TEXT DEFAULT 'crypto',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily_pnl (
            user_id INTEGER,
            date TEXT NOT NULL,
            total_pnl REAL DEFAULT 0,
            trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date)
        )
    """)

    logger.info("SQLite tables created.")


def seed_defaults(user_id):
    """Seed default config values for a new user."""
    from db import execute
    from crypto_bot.blofin_client import DEFAULT_COINS
    from stock_bot.broker_client import DEFAULT_STOCKS

    placeholder = "%s" if IS_POSTGRES else "?"

    defaults = [
        # Account
        ("starting_balance", "25000"),
        ("current_balance", "25000"),
        ("weekly_target", "500"),
        # Crypto bot
        ("bot_enabled", "0"), ("kill_switch", "0"),
        ("max_position_pct", "10"), ("daily_loss_limit", "250"),
        ("max_open_positions", "3"), ("scan_interval_sec", "60"),
        ("selected_coins", json.dumps(DEFAULT_COINS)),
        ("daily_goal", "50"), ("platform", "blofin"), ("trading_mode", "futures"),
        # Stock bot
        ("stock_bot_enabled", "0"), ("stock_kill_switch", "0"),
        ("stock_max_position_pct", "10"), ("stock_daily_loss_limit", "250"),
        ("stock_max_open_positions", "3"), ("stock_scan_interval_sec", "300"),
        ("stock_selected_stocks", json.dumps(DEFAULT_STOCKS)),
        ("stock_daily_goal", "50"), ("stock_broker", "alpaca"),
        ("stock_direction_bias", "long_only"), ("stock_extended_hours", "0"),
    ]

    conn = get_db()
    try:
        if IS_POSTGRES:
            cur = conn.cursor()
            for k, v in defaults:
                cur.execute(
                    f"INSERT INTO bot_config (user_id, key, value) VALUES ({placeholder}, {placeholder}, {placeholder}) "
                    f"ON CONFLICT (user_id, key) DO NOTHING",
                    (user_id, k, v),
                )
            for k, v in [("starting_balance", "25000"), ("current_balance", "25000"), ("weekly_target", "500")]:
                cur.execute(
                    f"INSERT INTO account_config (user_id, key, value) VALUES ({placeholder}, {placeholder}, {placeholder}) "
                    f"ON CONFLICT (user_id, key) DO NOTHING",
                    (user_id, k, v),
                )
            cur.close()
        else:
            for k, v in defaults:
                conn.execute(
                    f"INSERT OR IGNORE INTO bot_config (user_id, key, value) VALUES ({placeholder}, {placeholder}, {placeholder})",
                    (user_id, k, v),
                )
            for k, v in [("starting_balance", "25000"), ("current_balance", "25000"), ("weekly_target", "500")]:
                conn.execute(
                    f"INSERT OR IGNORE INTO account_config (user_id, key, value) VALUES ({placeholder}, {placeholder}, {placeholder})",
                    (user_id, k, v),
                )
        conn.commit()
    finally:
        put_db(conn)


if __name__ == "__main__":
    run_migrations()
    print("All migrations applied successfully.")
