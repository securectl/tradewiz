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
    cur.execute("ALTER TABLE bot_trades ADD COLUMN IF NOT EXISTS regime_at_entry TEXT")

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

    # Re-entry watchlist: when claude_bot exits a position, ticker stays here
    # for up to N days so the bot can scalp-and-rotate back in if it stabilizes.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_reentry_watch (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            ticker TEXT NOT NULL,
            asset_type TEXT DEFAULT 'claude',
            exit_price REAL,
            exit_pnl_pct REAL,
            exit_reason TEXT,
            attempts INTEGER DEFAULT 0,
            status TEXT DEFAULT 'watching',
            created_at TIMESTAMP DEFAULT NOW(),
            resolved_at TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_brw_user_status ON bot_reentry_watch(user_id, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_brw_user_ticker ON bot_reentry_watch(user_id, ticker, status)")

    # Backtest run history — strategy validation before deploying capital
    cur.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            strategy TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            universe_size INTEGER,
            trade_count INTEGER,
            total_return_pct REAL,
            cagr_pct REAL,
            sharpe REAL,
            max_drawdown_pct REAL,
            win_rate REAL,
            profit_factor REAL,
            expectancy_pct REAL,
            report_json TEXT,
            status TEXT DEFAULT 'completed',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_btr_user_created ON backtest_runs(user_id, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_btr_strategy ON backtest_runs(strategy, created_at DESC)")

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
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            called_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # Token/cost columns (added for the admin AI-usage dashboard; backfill 0 on
    # rows logged before this migration).
    cur.execute("ALTER TABLE llm_usage_log ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER DEFAULT 0")
    cur.execute("ALTER TABLE llm_usage_log ADD COLUMN IF NOT EXISTS completion_tokens INTEGER DEFAULT 0")
    cur.execute("ALTER TABLE llm_usage_log ADD COLUMN IF NOT EXISTS total_tokens INTEGER DEFAULT 0")
    cur.execute("ALTER TABLE llm_usage_log ADD COLUMN IF NOT EXISTS cost_usd REAL DEFAULT 0")

    # Feature flags — cohort/canary rollout control (off/admin/beta/percent/on).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS feature_flags (
            flag TEXT PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'off',
            rollout_pct INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # Seed the Earnings tab as an admin-only canary (admins can then ramp it).
    cur.execute("INSERT INTO feature_flags (flag, state, rollout_pct) "
                "VALUES ('earnings_tab', 'admin', 0) ON CONFLICT (flag) DO NOTHING")
    # Public (non-invite) signup — OFF by default; flip to 'on' in Admin to go live.
    cur.execute("INSERT INTO feature_flags (flag, state, rollout_pct) "
                "VALUES ('public_signup', 'off', 0) ON CONFLICT (flag) DO NOTHING")
    # Landing-page visits — first-time-IP detection for the Starter recommendation.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS landing_visits (
            ip TEXT PRIMARY KEY,
            first_seen TIMESTAMP DEFAULT NOW(),
            hits INTEGER DEFAULT 1
        )
    """)
    # Promo / access codes — admin-generated codes that grant free tier access.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            tier TEXT NOT NULL DEFAULT 'starter',
            days INTEGER NOT NULL DEFAULT 30,
            max_uses INTEGER NOT NULL DEFAULT 1,
            used_count INTEGER NOT NULL DEFAULT 0,
            active BOOLEAN DEFAULT TRUE,
            expires_at TIMESTAMP,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_redemptions (
            id SERIAL PRIMARY KEY,
            code TEXT NOT NULL,
            user_id INTEGER,
            redeemed_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(code, user_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_user_time ON llm_usage_log(user_id, called_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_time ON llm_usage_log(called_at)")

    # Daily call-option activity snapshots (one row per symbol per day) — powers
    # the 30-day trend on the Option Calls tab. Rows older than 30 days are
    # purged on write (see features/options_calls/engine._record_snapshot).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS options_call_snapshots (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            snap_date TEXT NOT NULL,
            price REAL DEFAULT 0,
            call_volume INTEGER DEFAULT 0,
            call_oi INTEGER DEFAULT 0,
            vol_oi_ratio REAL DEFAULT 0,
            read TEXT,
            top_strike REAL,
            top_expiry TEXT,
            top_volume INTEGER DEFAULT 0,
            contracts INTEGER DEFAULT 0,
            increasing_count INTEGER DEFAULT 0,
            decreasing_count INTEGER DEFAULT 0,
            source TEXT,
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(symbol, snap_date)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_optsnap_symbol_date ON options_call_snapshots(symbol, snap_date)")

    # Earnings calendar — one JSON board per week (mirror of the computed weekly
    # "most anticipated reports" view, so a restart serves the last board fast).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS earnings_calendar_snapshots (
            id SERIAL PRIMARY KEY,
            week_start TEXT NOT NULL,
            payload TEXT,
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(week_start)
        )
    """)

    # LLM model snapshots — admin-saved point-in-time captures of which models
    # are wired to which roles. Used by the "revert to previous version"
    # button in the admin UI when a cheaper-model swap turns out worse.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS llm_snapshots (
            id SERIAL PRIMARY KEY,
            label TEXT NOT NULL,
            models_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

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

    # ── Trial columns on user_subscriptions ──────────────────
    cur.execute("ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMP")
    cur.execute("ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP")
    cur.execute("ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS trial_status TEXT DEFAULT 'none'")

    # ── Trial Fingerprints (fraud prevention) ─────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trial_fingerprints (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            ip_address TEXT,
            country TEXT,
            region TEXT,
            city TEXT,
            fingerprint_hash TEXT,
            email_domain TEXT,
            payment_method_hash TEXT,
            device_info TEXT,
            fraud_score REAL DEFAULT 0,
            fraud_flags TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tf_ip ON trial_fingerprints(ip_address)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tf_fingerprint ON trial_fingerprints(fingerprint_hash)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tf_email_domain ON trial_fingerprints(email_domain)")

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

    # ── Trump Backtracks (Policy Reversal Tracking) ────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trump_backtracks (
            id SERIAL PRIMARY KEY,
            policy_area TEXT NOT NULL,
            initial_stance TEXT NOT NULL,
            initial_date TIMESTAMP NOT NULL,
            initial_mood REAL,
            backtrack_stance TEXT NOT NULL,
            backtrack_date TIMESTAMP NOT NULL,
            backtrack_mood REAL,
            days_to_reversal INTEGER,
            mood_swing REAL,
            market_impact TEXT,
            detection_method TEXT DEFAULT 'rule',
            confidence REAL DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_backtracks_policy
        ON trump_backtracks (policy_area)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_backtracks_date
        ON trump_backtracks (backtrack_date DESC)
    """)

    # ── Trump → Market ML Forecaster ─────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trump_market_predictions (
            id SERIAL PRIMARY KEY,
            asset TEXT NOT NULL,
            horizon TEXT NOT NULL,
            predicted_return_pct REAL NOT NULL,
            confidence REAL,
            model_predictions TEXT,
            mood_at_prediction REAL,
            mood_label TEXT,
            target_date TIMESTAMP,
            actual_return_pct REAL,
            error_pct REAL,
            evaluated_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tmp_asset_h ON trump_market_predictions(asset, horizon, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tmp_target ON trump_market_predictions(target_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tmp_eval ON trump_market_predictions(evaluated_at)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trump_forecast_weights (
            asset TEXT NOT NULL,
            horizon TEXT NOT NULL,
            weights_json TEXT NOT NULL,
            mae_json TEXT,
            n_samples INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (asset, horizon)
        )
    """)

    # ── Watchdog Trader ────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS watchdog_snapshots (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            regime TEXT NOT NULL,
            composite_score REAL NOT NULL,
            market_score REAL,
            sentiment_score REAL,
            technical_score REAL,
            spy_price REAL,
            vix REAL,
            trump_mood REAL,
            signals_json TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ws_user_time ON watchdog_snapshots(user_id, created_at DESC)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS watchdog_paper_trades (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL DEFAULT 'long',
            shares REAL NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            stop_loss REAL,
            take_profit REAL,
            pnl REAL,
            pnl_pct REAL,
            status TEXT NOT NULL DEFAULT 'open',
            strategy TEXT,
            regime_at_entry TEXT,
            reasoning TEXT,
            notes TEXT,
            opened_at TIMESTAMP DEFAULT NOW(),
            closed_at TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wpt_user_status ON watchdog_paper_trades(user_id, status)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS watchdog_signals (
            id SERIAL PRIMARY KEY,
            ticker TEXT NOT NULL,
            signal TEXT NOT NULL,
            confidence REAL,
            strategy TEXT,
            reasoning TEXT,
            regime TEXT,
            price_at_signal REAL,
            indicators_json TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wsig_ticker_time ON watchdog_signals(ticker, created_at DESC)")

    # ── Oversold Daily Tracker ──────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS oversold_daily (
            id SERIAL PRIMARY KEY,
            ticker TEXT NOT NULL,
            scan_date TEXT NOT NULL,
            price REAL,
            rsi_14 REAL,
            pct_change_1mo REAL,
            market_cap REAL,
            sector TEXT,
            name TEXT,
            ai_verdict TEXT,
            ai_confidence REAL,
            ai_summary TEXT,
            bottom_signal_strength TEXT,
            decline_reason TEXT,
            validated BOOLEAN DEFAULT FALSE,
            status TEXT DEFAULT 'tracking',
            days_tracked INTEGER DEFAULT 1,
            first_seen TEXT,
            price_trend TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_oversold_ticker_date ON oversold_daily (ticker, scan_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_oversold_date ON oversold_daily (scan_date DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_oversold_status ON oversold_daily (status)")

    # ── Screener Results Cache (all categories) ────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS screener_results (
            id SERIAL PRIMARY KEY,
            category TEXT NOT NULL,
            scan_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            price REAL,
            verdict TEXT,
            confidence REAL,
            summary TEXT,
            sector TEXT,
            name TEXT,
            market_cap REAL,
            data_json TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_screener_cat_date_ticker ON screener_results (category, scan_date, ticker)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_screener_cat_date ON screener_results (category, scan_date DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_screener_ticker ON screener_results (ticker, scan_date DESC)")

    # ── Sector Radar (Auto Research Analyst) ───────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sector_radar_reports (
            id SERIAL PRIMARY KEY,
            run_date TEXT NOT NULL,
            mode TEXT,
            regime TEXT,
            top_sector TEXT,
            conviction REAL,
            report_json TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sector_radar_date ON sector_radar_reports (id DESC)")

    # ── User feedback (NPS / CSAT / ease / open text) ──────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_feedback (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            nps INTEGER,
            csat INTEGER,
            ease INTEGER,
            valuable TEXT,
            improve TEXT,
            email TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created ON user_feedback (created_at DESC)")

    # ── Institutional / Whale Tracker ──────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS whale_entities (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            cik TEXT,
            description TEXT,
            aum_billions REAL,
            notable_holdings TEXT,
            data_json TEXT,
            last_updated TIMESTAMP DEFAULT NOW(),
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(name, entity_type)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS whale_holdings (
            id SERIAL PRIMARY KEY,
            entity_id INTEGER REFERENCES whale_entities(id),
            ticker TEXT NOT NULL,
            company_name TEXT,
            shares BIGINT,
            value_usd REAL,
            pct_of_portfolio REAL,
            change_shares BIGINT DEFAULT 0,
            change_pct REAL DEFAULT 0,
            action TEXT,
            filing_date TEXT,
            report_date TEXT,
            source TEXT,
            data_json TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wh_entity_ticker ON whale_holdings (entity_id, ticker)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wh_ticker ON whale_holdings (ticker, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wh_filing ON whale_holdings (filing_date DESC)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS whale_activity (
            id SERIAL PRIMARY KEY,
            entity_id INTEGER REFERENCES whale_entities(id),
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,
            shares BIGINT,
            value_usd REAL,
            price_at_filing REAL,
            filing_date TEXT,
            source TEXT,
            data_json TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wa_ticker ON whale_activity (ticker, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wa_entity ON whale_activity (entity_id, created_at DESC)")

    # ── News agent (RSS/Reddit ingestion; 30-day retention) ──────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS news_articles (
            id SERIAL PRIMARY KEY,
            source TEXT,
            category TEXT,
            title TEXT NOT NULL,
            summary TEXT,
            url TEXT UNIQUE,
            published_at TIMESTAMP,
            tickers TEXT,
            sectors TEXT,
            sentiment TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news_articles (published_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_news_created ON news_articles (created_at DESC)")

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
        CREATE TABLE IF NOT EXISTS bot_reentry_watch (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ticker TEXT NOT NULL,
            asset_type TEXT DEFAULT 'claude',
            exit_price REAL,
            exit_pnl_pct REAL,
            exit_reason TEXT,
            attempts INTEGER DEFAULT 0,
            status TEXT DEFAULT 'watching',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_brw_user_status ON bot_reentry_watch(user_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_brw_user_ticker ON bot_reentry_watch(user_id, ticker, status)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            strategy TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            universe_size INTEGER,
            trade_count INTEGER,
            total_return_pct REAL,
            cagr_pct REAL,
            sharpe REAL,
            max_drawdown_pct REAL,
            win_rate REAL,
            profit_factor REAL,
            expectancy_pct REAL,
            report_json TEXT,
            status TEXT DEFAULT 'completed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_btr_user_created ON backtest_runs(user_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_btr_strategy ON backtest_runs(strategy, created_at DESC)")

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
        CREATE TABLE IF NOT EXISTS feature_flags (
            flag TEXT PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'off',
            rollout_pct INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT OR IGNORE INTO feature_flags (flag, state, rollout_pct) "
                 "VALUES ('earnings_tab', 'admin', 0)")
    conn.execute("INSERT OR IGNORE INTO feature_flags (flag, state, rollout_pct) "
                 "VALUES ('public_signup', 'off', 0)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS landing_visits (
            ip TEXT PRIMARY KEY,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hits INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            tier TEXT NOT NULL DEFAULT 'starter',
            days INTEGER NOT NULL DEFAULT 30,
            max_uses INTEGER NOT NULL DEFAULT 1,
            used_count INTEGER NOT NULL DEFAULT 0,
            active INTEGER DEFAULT 1,
            expires_at TIMESTAMP,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            user_id INTEGER,
            redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(code, user_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            call_source TEXT,
            model TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Token/cost columns for the admin AI-usage dashboard (pre-existing rows -> 0).
    _sqlite_add_column(conn, "llm_usage_log", "prompt_tokens", "INTEGER DEFAULT 0")
    _sqlite_add_column(conn, "llm_usage_log", "completion_tokens", "INTEGER DEFAULT 0")
    _sqlite_add_column(conn, "llm_usage_log", "total_tokens", "INTEGER DEFAULT 0")
    _sqlite_add_column(conn, "llm_usage_log", "cost_usd", "REAL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_user_time ON llm_usage_log(user_id, called_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_time ON llm_usage_log(called_at)")

    # Daily call-option activity snapshots (one row per symbol per day) for the
    # 30-day trend on the Option Calls tab. Old rows purged on write.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS options_call_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            snap_date TEXT NOT NULL,
            price REAL DEFAULT 0,
            call_volume INTEGER DEFAULT 0,
            call_oi INTEGER DEFAULT 0,
            vol_oi_ratio REAL DEFAULT 0,
            read TEXT,
            top_strike REAL,
            top_expiry TEXT,
            top_volume INTEGER DEFAULT 0,
            contracts INTEGER DEFAULT 0,
            increasing_count INTEGER DEFAULT 0,
            decreasing_count INTEGER DEFAULT 0,
            source TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, snap_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_optsnap_symbol_date ON options_call_snapshots(symbol, snap_date)")

    # Earnings calendar — one JSON board per week (see the Postgres section).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_calendar_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            payload TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(week_start)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            models_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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
    _sqlite_add_column(conn, "bot_trades", "regime_at_entry", "TEXT")
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

    # ── Trial columns ────────────────────────────────────────
    _sqlite_add_column(conn, "user_subscriptions", "trial_started_at", "TIMESTAMP")
    _sqlite_add_column(conn, "user_subscriptions", "trial_ends_at", "TIMESTAMP")
    _sqlite_add_column(conn, "user_subscriptions", "trial_status", "TEXT DEFAULT 'none'")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trial_fingerprints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ip_address TEXT,
            country TEXT,
            region TEXT,
            city TEXT,
            fingerprint_hash TEXT,
            email_domain TEXT,
            payment_method_hash TEXT,
            device_info TEXT,
            fraud_score REAL DEFAULT 0,
            fraud_flags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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

    # ── Trump Backtracks (Policy Reversal Tracking) ────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trump_backtracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_area TEXT NOT NULL,
            initial_stance TEXT NOT NULL,
            initial_date TIMESTAMP NOT NULL,
            initial_mood REAL,
            backtrack_stance TEXT NOT NULL,
            backtrack_date TIMESTAMP NOT NULL,
            backtrack_mood REAL,
            days_to_reversal INTEGER,
            mood_swing REAL,
            market_impact TEXT,
            detection_method TEXT DEFAULT 'rule',
            confidence REAL DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Trump → Market ML Forecaster ─────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trump_market_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT NOT NULL,
            horizon TEXT NOT NULL,
            predicted_return_pct REAL NOT NULL,
            confidence REAL,
            model_predictions TEXT,
            mood_at_prediction REAL,
            mood_label TEXT,
            target_date TIMESTAMP,
            actual_return_pct REAL,
            error_pct REAL,
            evaluated_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tmp_asset_h ON trump_market_predictions(asset, horizon, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tmp_target ON trump_market_predictions(target_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tmp_eval ON trump_market_predictions(evaluated_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trump_forecast_weights (
            asset TEXT NOT NULL,
            horizon TEXT NOT NULL,
            weights_json TEXT NOT NULL,
            mae_json TEXT,
            n_samples INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (asset, horizon)
        )
    """)

    # ── Watchdog Trader ────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchdog_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            regime TEXT NOT NULL,
            composite_score REAL NOT NULL,
            market_score REAL,
            sentiment_score REAL,
            technical_score REAL,
            spy_price REAL,
            vix REAL,
            trump_mood REAL,
            signals_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchdog_paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL DEFAULT 'long',
            shares REAL NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            stop_loss REAL,
            take_profit REAL,
            pnl REAL,
            pnl_pct REAL,
            status TEXT NOT NULL DEFAULT 'open',
            strategy TEXT,
            regime_at_entry TEXT,
            reasoning TEXT,
            notes TEXT,
            opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchdog_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            signal TEXT NOT NULL,
            confidence REAL,
            strategy TEXT,
            reasoning TEXT,
            regime TEXT,
            price_at_signal REAL,
            indicators_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Oversold Daily Tracker ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oversold_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            scan_date TEXT NOT NULL,
            price REAL,
            rsi_14 REAL,
            pct_change_1mo REAL,
            market_cap REAL,
            sector TEXT,
            name TEXT,
            ai_verdict TEXT,
            ai_confidence REAL,
            ai_summary TEXT,
            bottom_signal_strength TEXT,
            decline_reason TEXT,
            validated BOOLEAN DEFAULT 0,
            status TEXT DEFAULT 'tracking',
            days_tracked INTEGER DEFAULT 1,
            first_seen TEXT,
            price_trend TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, scan_date)
        )
    """)

    # ── Screener Results Cache (all categories) ────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS screener_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            scan_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            price REAL,
            verdict TEXT,
            confidence REAL,
            summary TEXT,
            sector TEXT,
            name TEXT,
            market_cap REAL,
            data_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(category, scan_date, ticker)
        )
    """)

    # ── Sector Radar (Auto Research Analyst) ───────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_radar_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            mode TEXT,
            regime TEXT,
            top_sector TEXT,
            conviction REAL,
            report_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sector_radar_date ON sector_radar_reports (id DESC)")

    # ── User feedback (NPS / CSAT / ease / open text) ──────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            nps INTEGER,
            csat INTEGER,
            ease INTEGER,
            valuable TEXT,
            improve TEXT,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created ON user_feedback (created_at DESC)")

    # ── Institutional / Whale Tracker ──────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS whale_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            cik TEXT,
            description TEXT,
            aum_billions REAL,
            notable_holdings TEXT,
            data_json TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, entity_type)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS whale_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER REFERENCES whale_entities(id),
            ticker TEXT NOT NULL,
            company_name TEXT,
            shares INTEGER,
            value_usd REAL,
            pct_of_portfolio REAL,
            change_shares INTEGER DEFAULT 0,
            change_pct REAL DEFAULT 0,
            action TEXT,
            filing_date TEXT,
            report_date TEXT,
            source TEXT,
            data_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS whale_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER REFERENCES whale_entities(id),
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,
            shares INTEGER,
            value_usd REAL,
            price_at_filing REAL,
            filing_date TEXT,
            source TEXT,
            data_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── News agent (RSS/Reddit ingestion; 30-day retention) ──────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            category TEXT,
            title TEXT NOT NULL,
            summary TEXT,
            url TEXT UNIQUE,
            published_at TIMESTAMP,
            tickers TEXT,
            sectors TEXT,
            sentiment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news_articles (published_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_created ON news_articles (created_at DESC)")

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
