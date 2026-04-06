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
            tier TEXT NOT NULL DEFAULT 'free',
            bot_access TEXT NOT NULL DEFAULT 'none',
            invited_by INTEGER REFERENCES users(id),
            invited_at TIMESTAMP DEFAULT NOW(),
            accepted_at TIMESTAMP
        )
    """)
    # Add tier/bot_access columns to existing invites tables
    cur.execute("ALTER TABLE invites ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'free'")
    cur.execute("ALTER TABLE invites ADD COLUMN IF NOT EXISTS bot_access TEXT NOT NULL DEFAULT 'none'")

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
            direction_bias TEXT,
            fee REAL DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bt_user ON bot_trades(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bt_status ON bot_trades(status)")
    # Compound indexes for dashboard queries (critique R2 recommendation)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bt_user_status_asset ON bot_trades(user_id, status, asset_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bt_user_closed ON bot_trades(user_id, closed_at) WHERE status = 'closed'")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bt_user_open ON bot_trades(user_id, coin) WHERE status = 'open'")
    cur.execute("ALTER TABLE bot_trades ADD COLUMN IF NOT EXISTS fee REAL DEFAULT 0")

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

    # ── Subscriptions & Usage ─────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            tier TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_price_id TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            current_period_start TIMESTAMP,
            current_period_end TIMESTAMP,
            cancel_at_period_end BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            call_source TEXT,
            model TEXT,
            called_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_user_time ON llm_usage_log(user_id, called_at)")

    # Add bot_access column to user_subscriptions
    cur.execute("ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS bot_access TEXT NOT NULL DEFAULT 'none'")

    # Rename 'starter' tier to 'basic'
    cur.execute("UPDATE user_subscriptions SET tier = 'basic' WHERE tier = 'starter'")

    # Seed free tier for existing users without a subscription row
    cur.execute("""
        INSERT INTO user_subscriptions (user_id, tier, status)
        SELECT id, 'free', 'active' FROM users
        WHERE id NOT IN (SELECT user_id FROM user_subscriptions)
    """)

    # ── Skill Jobs ─────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS skill_jobs (
            id TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            skill_id TEXT NOT NULL,
            inputs_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            progress REAL DEFAULT 0,
            current_phase TEXT DEFAULT '',
            message TEXT DEFAULT '',
            result_json TEXT,
            error TEXT DEFAULT '',
            output_files_json TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_skill_jobs_user ON skill_jobs(user_id)")

    # ── Auth columns for email/password + TOTP ──────────────────
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret TEXT")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_locked BOOLEAN DEFAULT FALSE")
    # Relax google_id NOT NULL so email-registered users can have NULL
    try:
        cur.execute("ALTER TABLE users ALTER COLUMN google_id DROP NOT NULL")
    except Exception:
        pass  # Already nullable or constraint doesn't exist

    # Support requests table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS support_requests (
            id SERIAL PRIMARY KEY,
            ticket_id TEXT UNIQUE,
            name TEXT,
            email TEXT,
            issue_type TEXT,
            message TEXT,
            status TEXT DEFAULT 'open',
            admin_notes TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            resolved_at TIMESTAMP
        )
    """)

    # ── Trump Mood History ──────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trump_mood_history (
            id SERIAL PRIMARY KEY,
            mood REAL NOT NULL,
            label TEXT NOT NULL,
            description TEXT,
            pattern_trend TEXT,
            pattern_acceleration REAL,
            day_scores TEXT,
            top_signals TEXT,
            notable_posts TEXT,
            posts_analyzed INTEGER DEFAULT 0,
            src_truth INTEGER DEFAULT 0,
            src_news INTEGER DEFAULT 0,
            src_whitehouse INTEGER DEFAULT 0,
            ai_prediction TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_trump_mood_created
        ON trump_mood_history (created_at DESC)
    """)

    cur.close()
    logger.info("PostgreSQL tables created.")


def _sqlite_recreate_if_pk_changed(conn, table, new_ddl):
    """Recreate a SQLite table if its primary key doesn't include user_id.
    Preserves data by copying rows into the new schema."""
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if "user_id" in cols:
            return  # Already migrated
        # Backup, drop, recreate, copy
        old_cols = ", ".join(cols)
        conn.execute(f"ALTER TABLE {table} RENAME TO _{table}_old")
        conn.execute(new_ddl)
        # Copy old data (user_id will be NULL)
        conn.execute(f"INSERT INTO {table} ({old_cols}) SELECT {old_cols} FROM _{table}_old")
        conn.execute(f"DROP TABLE _{table}_old")
        logger.info(f"Recreated table {table} with new primary key")
    except Exception as e:
        logger.debug(f"Table recreate skipped ({table}): {e}")


def _sqlite_add_column(conn, table, column, col_type):
    """Add a column to a SQLite table if it doesn't exist."""
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            logger.info(f"Added column {column} to {table}")
    except Exception as e:
        logger.debug(f"Column migration skipped ({table}.{column}): {e}")


def _run_sqlite(conn):
    """SQLite schema — mirrors the original init_db() plus new auth tables."""
    from crypto_bot.blofin_client import DEFAULT_COINS
    from stock_bot.broker_client import DEFAULT_STOCKS

    # ── Users & Auth (lite versions, no FK enforcement) ──────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id TEXT UNIQUE,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            picture_url TEXT,
            password_hash TEXT,
            totp_secret TEXT,
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
            tier TEXT NOT NULL DEFAULT 'free',
            bot_access TEXT NOT NULL DEFAULT 'none',
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
            direction_bias TEXT,
            fee REAL DEFAULT 0
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

    # ── Subscriptions & Usage ────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            tier TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_price_id TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            current_period_start TIMESTAMP,
            current_period_end TIMESTAMP,
            cancel_at_period_end INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            call_source TEXT,
            model TEXT,
            called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_user_time ON llm_usage_log(user_id, called_at)")

    # Seed free tier for existing users without a subscription row
    conn.execute("""
        INSERT OR IGNORE INTO user_subscriptions (user_id, tier, status)
        SELECT id, 'free', 'active' FROM users
        WHERE id NOT IN (SELECT user_id FROM user_subscriptions)
    """)

    # ── Migrate old tables: add user_id if missing ──────────────
    _sqlite_add_column(conn, "bot_trades", "user_id", "INTEGER")
    _sqlite_add_column(conn, "bot_log", "user_id", "INTEGER")
    _sqlite_add_column(conn, "bot_log", "source", "TEXT DEFAULT 'crypto'")
    _sqlite_add_column(conn, "bot_trades", "fee", "REAL DEFAULT 0")
    _sqlite_add_column(conn, "searches", "user_id", "INTEGER")
    _sqlite_add_column(conn, "journal_entries", "user_id", "INTEGER")
    _sqlite_add_column(conn, "weekly_goals", "user_id", "INTEGER")

    # Tables whose PRIMARY KEY changed (key → user_id,key) must be recreated
    _sqlite_recreate_if_pk_changed(conn, "bot_config", """
        CREATE TABLE bot_config (
            user_id INTEGER, key TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY (user_id, key))""")
    _sqlite_recreate_if_pk_changed(conn, "bot_daily_pnl", """
        CREATE TABLE bot_daily_pnl (
            user_id INTEGER, date TEXT NOT NULL,
            total_pnl REAL DEFAULT 0, trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0, loss_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date))""")
    _sqlite_recreate_if_pk_changed(conn, "stock_daily_pnl", """
        CREATE TABLE stock_daily_pnl (
            user_id INTEGER, date TEXT NOT NULL,
            total_pnl REAL DEFAULT 0, trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0, loss_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date))""")
    _sqlite_recreate_if_pk_changed(conn, "account_config", """
        CREATE TABLE account_config (
            user_id INTEGER, key TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY (user_id, key))""")

    # ── Invite tier/bot_access columns ──
    _sqlite_add_column(conn, "invites", "tier", "TEXT DEFAULT 'free'")
    _sqlite_add_column(conn, "invites", "bot_access", "TEXT DEFAULT 'none'")
    _sqlite_add_column(conn, "user_subscriptions", "bot_access", "TEXT DEFAULT 'none'")

    # Rename 'starter' tier to 'basic'
    try:
        conn.execute("UPDATE user_subscriptions SET tier = 'basic' WHERE tier = 'starter'")
    except Exception:
        pass

    # ── Skill Jobs ────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_jobs (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            skill_id TEXT NOT NULL,
            inputs_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            progress REAL DEFAULT 0,
            current_phase TEXT DEFAULT '',
            message TEXT DEFAULT '',
            result_json TEXT,
            error TEXT DEFAULT '',
            output_files_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_jobs_user ON skill_jobs(user_id)")

    # ── Auth columns for email/password + TOTP (existing DBs) ──
    _sqlite_add_column(conn, "users", "password_hash", "TEXT")
    _sqlite_add_column(conn, "users", "totp_secret", "TEXT")
    _sqlite_add_column(conn, "users", "is_locked", "INTEGER DEFAULT 0")

    # Support requests table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS support_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT UNIQUE,
            name TEXT,
            email TEXT,
            issue_type TEXT,
            message TEXT,
            status TEXT DEFAULT 'open',
            admin_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        )
    """)

    # ── Trump Mood History ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trump_mood_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mood REAL NOT NULL,
            label TEXT NOT NULL,
            description TEXT,
            pattern_trend TEXT,
            pattern_acceleration REAL,
            day_scores TEXT,
            top_signals TEXT,
            notable_posts TEXT,
            posts_analyzed INTEGER DEFAULT 0,
            src_truth INTEGER DEFAULT 0,
            src_news INTEGER DEFAULT 0,
            src_whitehouse INTEGER DEFAULT 0,
            ai_prediction TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
