"""
AI Stock Analyst — Web Application
Flask server with multi-user auth, PostgreSQL/SQLite, and trading bots.
"""

import os
from flask import Flask, render_template, redirect, url_for, request
from flask_login import current_user
from dotenv import load_dotenv

load_dotenv()

from flask import jsonify
from db import IS_POSTGRES
from auth import auth_bp, init_auth
from docs_blueprint import docs_bp
from billing_bp import billing_bp
from skills.skill_bp import skill_bp
from status_checker import start_background_checker, purge_old_checks
from decorators import login_required, subscription_required

app = Flask(__name__)
app.json.sort_keys = False

# ─── Secret key with production guard ────────────────────────────────
_secret = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
if _secret in ("dev-secret-key-change-me", "change-me-to-a-random-string") and os.getenv("DATABASE_URL"):
    raise RuntimeError(
        "FATAL: SECRET_KEY is still the default. "
        "Set a strong random SECRET_KEY in your environment for production."
    )
app.config["SECRET_KEY"] = _secret

# ─── Session cookie hardening ────────────────────────────────────────
app.config["SESSION_COOKIE_SECURE"] = bool(os.getenv("DATABASE_URL"))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # No browser caching for static files

# Trust reverse proxy headers (nginx) so url_for generates https:// URLs
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Initialize auth
init_auth(app)
app.register_blueprint(auth_bp)

# Register existing blueprints
app.register_blueprint(docs_bp)
app.register_blueprint(billing_bp)
app.register_blueprint(skill_bp)

# Register feature blueprints
from features.ipo.routes import bp as ipo_bp
from features.status.routes import bp as status_bp
from features.tracker.routes import bp as tracker_bp
from features.bot_crypto.routes import bp as bot_crypto_bp
from features.bot_stock.routes import bp as bot_stock_bp
from features.screener.routes import bp as screener_bp
from features.admin.routes import bp as admin_bp_feat
from features.user.routes import bp as user_bp
from features.analysis.routes import bp as analysis_bp
from features.predictions.routes import bp as predictions_bp
from features.congress.routes import bp as congress_bp
from features.smart_money.routes import bp as smart_money_bp
from features.watchdog.routes import bp as watchdog_bp
from features.backtest.routes import bp as backtest_bp
from features.sector_radar.routes import bp as sector_radar_bp
from features.release_notes.routes import bp as release_notes_bp
from features.dashboard.routes import bp as dashboard_bp
from features.feedback.routes import bp as feedback_bp
from features.research.routes import bp as research_bp
from features.alerts.routes import bp as alerts_bp
from features.options_calls.routes import bp as options_calls_bp
from features.earnings_calendar.routes import bp as earnings_calendar_bp
from features.news_agent.routes import bp as news_agent_bp
from claude_bot.routes import bp as claude_bot_bp

app.register_blueprint(ipo_bp)
app.register_blueprint(status_bp)
app.register_blueprint(tracker_bp)
app.register_blueprint(bot_crypto_bp)
app.register_blueprint(bot_stock_bp)
app.register_blueprint(screener_bp)
app.register_blueprint(admin_bp_feat)
app.register_blueprint(user_bp)
app.register_blueprint(analysis_bp)
app.register_blueprint(predictions_bp)
app.register_blueprint(congress_bp)
app.register_blueprint(smart_money_bp)
app.register_blueprint(watchdog_bp)
app.register_blueprint(backtest_bp)
app.register_blueprint(sector_radar_bp)
app.register_blueprint(release_notes_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(feedback_bp)
app.register_blueprint(research_bp)
app.register_blueprint(alerts_bp)
app.register_blueprint(options_calls_bp)
app.register_blueprint(earnings_calendar_bp)
app.register_blueprint(news_agent_bp)
app.register_blueprint(claude_bot_bp)

# ─── Startup: log rotation + cleanup ────────────────────────────
def _startup_cleanup():
    """Trim old bot_log and service_checks entries to prevent DB bloat."""
    import os
    # Clean stale scheduler lock from previous container run
    try:
        os.remove("/tmp/scheduler.lock")
    except OSError:
        pass
    try:
        from db import execute, IS_POSTGRES
        if IS_POSTGRES:
            execute("DELETE FROM bot_log WHERE created_at < NOW() - INTERVAL '30 days'")
            execute("DELETE FROM service_checks WHERE checked_at < NOW() - INTERVAL '7 days'")
            execute("DELETE FROM llm_usage_log WHERE created_at < NOW() - INTERVAL '30 days'")
            import logging
            logging.getLogger(__name__).info("Startup cleanup: trimmed old log/check entries")
    except Exception:
        pass

# Note: do NOT call _startup_cleanup() here. With gunicorn --preload it runs in
# the master before fork, opens psycopg2 pool connections, and the workers
# inherit broken TCP sockets — the worker boot then hangs on futex/connection.
# It's invoked from _on_startup() (post-fork in the worker) instead.


# ─── Scheduled Jobs (daily screener scans @ 9 AM CST) ───────
def _run_scheduled_oversold_scan():
    """Run oversold scan at 9 AM CST daily. Stores results in DB for all users."""
    import logging
    log = logging.getLogger("scheduler")
    log.info("[SCHEDULER] Starting daily oversold scan (9 AM CST)...")
    try:
        from screener import _oversold_background_scan
        _oversold_background_scan(limit=40)  # store top-40 so more oversold names accrue "days"
        log.info("[SCHEDULER] Oversold scan complete")
    except Exception as e:
        log.error(f"[SCHEDULER] Oversold scan failed: {e}")


def _run_scheduled_screener_scans():
    """Run ALL category scans to pre-populate cache for the day.
    Global pull — results shared across all users. Staggered to limit AI load."""
    import logging
    import time as _time
    log = logging.getLogger("scheduler")
    log.info("[SCHEDULER] Starting daily screener pre-cache (ALL categories)...")
    try:
        from screener import run_screener
        categories = ["lowcap", "midcap", "largecap", "etf", "metals_mining", "crypto", "ai", "gainers", "losers"]
        for category in categories:
            try:
                result = run_screener(category=category, limit=15)
                opps = len(result.get("opportunities", []))
                log.info(f"[SCHEDULER] {category} scan: {result.get('candidates_scanned', 0)} scanned, {opps} opportunities")
                _time.sleep(10)  # stagger between categories to limit AI API load
            except Exception as e:
                log.warning(f"[SCHEDULER] {category} scan failed: {e}")
        log.info("[SCHEDULER] All category scans complete")
    except Exception as e:
        log.error(f"[SCHEDULER] Screener pre-cache failed: {e}")


def _run_scheduled_sector_radar():
    """Run the Sector Radar auto research analyst daily at 9:30 AM CST.
    Mondays get the deeper (Opus) weekly synthesis; other days use Sonnet."""
    import logging
    from datetime import datetime as _dt
    log = logging.getLogger("scheduler")
    deep = _dt.utcnow().weekday() == 0  # Monday
    log.info(f"[SCHEDULER] Starting Sector Radar analysis (deep={deep})...")
    try:
        from features.sector_radar.engine import run_analysis
        report = run_analysis(deep=deep, trigger="scheduled")
        top = (report.get("analyst") or {}).get("top_sector", "?")
        log.info(f"[SCHEDULER] Sector Radar complete — top sector: {top}")
    except Exception as e:
        log.error(f"[SCHEDULER] Sector Radar failed: {e}")


def _init_scheduler():
    """Initialize APScheduler for daily jobs. Only runs in one gunicorn worker."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        import logging

        scheduler = BackgroundScheduler(daemon=True)

        # Oversold scan at 9:00 AM CST (14:00 UTC)
        scheduler.add_job(
            _run_scheduled_oversold_scan,
            CronTrigger(hour=14, minute=0, timezone="UTC"),  # 9 AM CST = 14:00 UTC
            id="daily_oversold_scan",
            replace_existing=True,
        )

        # Pre-cache ALL screener categories at 9:10 AM CST (staggered to avoid overload)
        scheduler.add_job(
            _run_scheduled_screener_scans,
            CronTrigger(hour=14, minute=10, timezone="UTC"),  # 9:10 AM CST
            id="daily_screener_precache",
            replace_existing=True,
        )

        # Trial expiry check + warning emails at 8:00 AM CST (13:00 UTC)
        def _run_trial_checks():
            log = logging.getLogger("scheduler")
            try:
                from trial_manager import check_and_expire_trials, check_trial_expiry_warnings
                expired = check_and_expire_trials()
                warned = check_trial_expiry_warnings()
                log.info(f"[SCHEDULER] Trial check: {expired} expired, {warned} warnings sent")
            except Exception as e:
                log.error(f"[SCHEDULER] Trial check failed: {e}")

        scheduler.add_job(
            _run_trial_checks,
            CronTrigger(hour=13, minute=0, timezone="UTC"),  # 8 AM CST
            id="daily_trial_check",
            replace_existing=True,
        )

        # Daily alert digest (volume spikes / persistent oversold / upcoming
        # earnings) at 8:30 AM CST (13:30 UTC), before the open.
        def _run_daily_alerts():
            log = logging.getLogger("scheduler")
            try:
                from alerts import send_daily_alerts
                result = send_daily_alerts()
                log.info(f"[SCHEDULER] Daily alerts: {result.get('sent', 0)} sent "
                         f"(skipped={result.get('skipped')})")
            except Exception as e:
                log.error(f"[SCHEDULER] Daily alerts failed: {e}")

        scheduler.add_job(
            _run_daily_alerts,
            CronTrigger(hour=13, minute=30, timezone="UTC"),  # 8:30 AM CST
            id="daily_alert_digest",
            replace_existing=True,
        )

        # Sector Radar auto research analyst at 9:30 AM CST (14:30 UTC), after
        # the screener pre-cache. Mondays run the deeper Opus weekly synthesis.
        scheduler.add_job(
            _run_scheduled_sector_radar,
            CronTrigger(hour=14, minute=30, timezone="UTC"),  # 9:30 AM CST
            id="daily_sector_radar",
            replace_existing=True,
        )

        scheduler.start()
        logging.getLogger("scheduler").info("[SCHEDULER] Started — trials 8AM, alerts 8:30AM, oversold 9AM, screener 9:10AM, sector radar 9:30AM CST")
    except Exception as e:
        import logging
        logging.getLogger("scheduler").warning(f"[SCHEDULER] Failed to start: {e}")


# Start scheduler once via before_first_request equivalent
_scheduler_started = False

@app.before_request
def _maybe_start_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    import os
    _lock_path = "/tmp/scheduler.lock"
    try:
        _lock_fd = os.open(_lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(_lock_fd, str(os.getpid()).encode())
        os.close(_lock_fd)
        _init_scheduler()
    except (FileExistsError, OSError):
        pass


# Serve feature static files (JS/CSS)
@app.route('/features/<path:filename>')
def feature_static(filename):
    from flask import send_from_directory
    return send_from_directory('features', filename)


# ─── Market Pulse (header tiles) ─────────────────────────────────────

@app.route("/api/market-pulse")
@login_required
def market_pulse():
    """Return VIX, SPY range, and Fear & Greed for header tiles."""
    import requests as _req
    import time as _time
    # Shared cache (shared/yf_fetch): same (symbol, period, interval) keys as the
    # bots' market sensor, so the tiles reuse the bots' fetch and vice versa.
    from shared.yf_fetch import get_history

    # Check cache (5 min TTL)
    now = _time.time()
    if hasattr(market_pulse, '_cache') and (now - market_pulse._cache.get('_t', 0)) < 300:
        return jsonify(market_pulse._cache['data'])

    data = {}

    # SPY
    try:
        spy_df = get_history("SPY", period="5d", interval="1h")
        if spy_df is not None and not spy_df.empty:
            price = float(spy_df["Close"].iloc[-1])
            day_high = float(spy_df["High"].iloc[-7:].max()) if len(spy_df) >= 7 else float(spy_df["High"].max())
            day_low = float(spy_df["Low"].iloc[-7:].min()) if len(spy_df) >= 7 else float(spy_df["Low"].min())
            prev_close = float(spy_df["Close"].iloc[-8]) if len(spy_df) >= 8 else float(spy_df["Close"].iloc[0])
            change_pct = ((price - prev_close) / prev_close) * 100
            data["spy"] = {
                "price": round(price, 2),
                "change_pct": round(change_pct, 2),
                "day_high": round(day_high, 2),
                "day_low": round(day_low, 2),
            }
    except Exception:
        pass

    # VIX
    try:
        vix_df = get_history("^VIX", period="5d", interval="1d")
        if vix_df is not None and not vix_df.empty:
            vix_val = float(vix_df["Close"].iloc[-1])
            vix_prev = float(vix_df["Close"].iloc[-2]) if len(vix_df) >= 2 else vix_val
            vix_change = ((vix_val - vix_prev) / vix_prev) * 100
            data["vix"] = {
                "value": round(vix_val, 2),
                "change_pct": round(vix_change, 2),
                # 5-day low/high so the header meter can place the current VIX.
                "day_high": round(float(vix_df["High"].max()), 2),
                "day_low": round(float(vix_df["Low"].min()), 2),
            }
    except Exception:
        pass

    # Fear & Greed Index — try CNN first, fall back to Alternative.me (crypto F&G)
    fg_found = False
    try:
        fg_resp = _req.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", timeout=3,
                           headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        if fg_resp.status_code == 200:
            fg_data = fg_resp.json()
            score = fg_data.get("fear_and_greed", {}).get("score")
            rating = fg_data.get("fear_and_greed", {}).get("rating")
            if score is not None:
                data["fear_greed"] = {
                    "score": round(float(score)),
                    "rating": rating or "",
                    "source": "cnn",
                }
                fg_found = True
    except Exception:
        pass

    # Fallback: Alternative.me Crypto Fear & Greed (reliable, always works)
    if not fg_found:
        try:
            alt_resp = _req.get("https://api.alternative.me/fng/?limit=1", timeout=5)
            if alt_resp.status_code == 200:
                alt_data = alt_resp.json()
                items = alt_data.get("data", [])
                if items:
                    score = int(items[0].get("value", 50))
                    rating = items[0].get("value_classification", "")
                    # Compute a blended score using VIX as stock proxy
                    # VIX < 17 = greedy (+15), 17-23 = neutral, 23-30 = fearful (-15), 30+ = extreme fear (-25)
                    vix_adj = 0
                    if data.get("vix"):
                        v = data["vix"]["value"]
                        if v < 17: vix_adj = 15
                        elif v < 20: vix_adj = 5
                        elif v < 23: vix_adj = 0
                        elif v < 30: vix_adj = -15
                        else: vix_adj = -25
                    blended = max(0, min(100, score + vix_adj))
                    if blended >= 75: blended_rating = "Extreme Greed"
                    elif blended >= 55: blended_rating = "Greed"
                    elif blended >= 45: blended_rating = "Neutral"
                    elif blended >= 25: blended_rating = "Fear"
                    else: blended_rating = "Extreme Fear"
                    data["fear_greed"] = {
                        "score": blended,
                        "rating": blended_rating,
                        "source": "blended",
                        "crypto_raw": score,
                        "vix_adjustment": vix_adj,
                    }
        except Exception:
            pass

    # Prediction market sentiment (Polymarket + Kalshi)
    try:
        from prediction_markets import get_poly_sentiment_for_pulse
        poly = get_poly_sentiment_for_pulse()
        data["poly_sentiment"] = poly
    except Exception:
        pass

    # Trump Mood gauge (Truth Social + GDELT + White House)
    try:
        from trump_mood import get_trump_mood
        trump = get_trump_mood()
        data["trump_mood"] = {
            "mood": trump["mood"],
            "label": trump["label"],
            "color": trump["color"],
            "description": trump["description"],
            "pattern": trump["pattern"],
            "posts_analyzed": trump["posts_analyzed"],
        }
    except Exception:
        pass

    # Cache it
    if not hasattr(market_pulse, '_cache'):
        market_pulse._cache = {}
    market_pulse._cache = {'data': data, '_t': now}

    return jsonify(data)


# ─── Trump Mood Detail ────────────────────────────────────────────────

@app.route("/api/trump-mood")
@login_required
@subscription_required("pro")
def api_trump_mood():
    """Full Trump Mood analysis with posts, signals, and 3-day pattern."""
    from trump_mood import get_trump_mood, _cache
    if request.args.get("force"):
        _cache.pop("trump_mood", None)
    return jsonify(get_trump_mood())


@app.route("/api/trump/history")
@login_required
@subscription_required("pro")
def api_trump_history():
    """Historical trump mood data for charting."""
    from trump_mood import get_mood_history
    days = request.args.get("days", 30, type=int)
    return jsonify(get_mood_history(min(days, 90)))


@app.route("/api/trump/predict")
@login_required
@subscription_required("pro")
def api_trump_predict():
    """AI prediction of Trump's next market-moving actions.
    Shared globally — cached for 12h. Only makes LLM call when force=1."""
    from trump_mood import get_ai_prediction, get_trump_mood
    force = bool(request.args.get("force"))
    current = get_trump_mood()
    return jsonify(get_ai_prediction(current, force=force))


@app.route("/api/trump/backtracks")
@login_required
@subscription_required("pro")
def api_trump_backtracks():
    """Detected policy reversals with stats — rule-based detection."""
    from trump_mood import get_backtracks
    days = request.args.get("days", 90, type=int)
    force = bool(request.args.get("force"))
    return jsonify(get_backtracks(min(days, 180), force=force))


@app.route("/api/trump/backtracks/predict")
@login_required
@subscription_required("pro")
def api_trump_backtracks_predict():
    """AI prediction of next policy reversal — LLM-powered, cached 12h.
    Only makes LLM call when force=1."""
    from trump_mood import get_backtrack_prediction
    force = bool(request.args.get("force"))
    return jsonify(get_backtrack_prediction(force=force))


# ─── Trump → Market ML Forecaster ────────────────────────────────────

@app.route("/api/trump/forecast")
@login_required
@subscription_required("pro")
def api_trump_forecast():
    """Forward-looking ML forecast: predicts SPY/QQQ/BTC/ETH returns (1D/5D/21D)
    from Trump mood using a self-correcting ensemble (Ridge + KNN + EWMA + Opus LLM)."""
    from trump_forecaster import generate_forecast
    force = bool(request.args.get("force"))
    return jsonify(generate_forecast(force=force))


@app.route("/api/trump/correlation")
@login_required
@subscription_required("pro")
def api_trump_correlation():
    """Mood ↔ market correlation timeline with lead/lag Pearson at 0-7 day lags."""
    from trump_forecaster import get_correlation_timeline
    days = request.args.get("days", 60, type=int)
    return jsonify(get_correlation_timeline(days=min(days, 180)))


@app.route("/api/trump/forecast/accuracy")
@login_required
@subscription_required("pro")
def api_trump_forecast_accuracy():
    """Per-asset, per-horizon accuracy metrics from self-learning pipeline."""
    from trump_forecaster import get_forecast_accuracy, evaluate_past_predictions
    if request.args.get("evaluate"):
        evaluate_past_predictions()
    return jsonify(get_forecast_accuracy())


# ─── Health Check (for orchestrators, load balancers, monitoring) ────

@app.route("/healthz")
def healthz():
    """Health check endpoint — returns 200 if app is running and DB is reachable."""
    try:
        from db import query_one
        query_one("SELECT 1 AS ok", ())
        platform = "unknown"
        try:
            from shared.platform_config import get_platform
            platform = get_platform()["effective"]
        except Exception:
            pass
        return jsonify({"status": "healthy", "db": "ok", "platform": platform}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "db": str(e)}), 503


# ─── Security Headers ────────────────────────────────────────────────

@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if os.getenv("DATABASE_URL"):  # Production only
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


# ─── Request Size Limit ──────────────────────────────────────────────

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB max upload


# ─── Root route ──────────────────────────────────────────────────────

@app.route("/")
def index():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    return render_template("index.html")


# ─── Init DB ─────────────────────────────────────────────────────────

def init_db():
    """Run migrations to set up tables."""
    from migrations import run_migrations
    run_migrations()
    purge_old_checks(90)


# ─── Auto-start bots ─────────────────────────────────────────────────

def _auto_start_bots():
    """Auto-restart all bots that were enabled before the container went down.

    Reads `bot_config` enable flags per bot and restarts the matching engine for
    each user. Also starts the global options-flow scanner unconditionally.
    Idempotent: each bot's start path internally guards against double-spawn.

    Bots covered:
      - Crypto bot (enable flag: bot_enabled)
      - Stock bot  (enable flag: stock_bot_enabled)
      - Watchdog   (enable flag: wd_enabled)
      - Claude Bot (enable flag: cb_enabled)
      - Options-flow scanner (global, no per-user flag)
    """
    import logging
    _log = logging.getLogger(__name__)

    def _enabled_uids(flag: str) -> list:
        try:
            from db import query
            rows = query(
                "SELECT DISTINCT user_id FROM bot_config "
                "WHERE key = %s AND value = '1'" if _is_postgres() else
                "SELECT DISTINCT user_id FROM bot_config WHERE key = ? AND value = '1'",
                (flag,),
            )
            return [r["user_id"] for r in (rows or [])]
        except Exception as e:
            _log.warning(f"[AUTO-START] enable-flag query for {flag} failed: {e}")
            return []

    # Crypto bot
    for uid in _enabled_uids("bot_enabled"):
        try:
            from crypto_bot.bot_engine import get_bot
            bot = get_bot(uid)
            if not bot.is_running:
                bot.start()
                _log.info(f"[AUTO-START] Crypto bot started for user {uid}")
        except Exception as e:
            _log.warning(f"[AUTO-START] Crypto bot failed for user {uid}: {e}")

    # Stock bot
    for uid in _enabled_uids("stock_bot_enabled"):
        try:
            from stock_bot.stock_engine import get_stock_bot
            sbot = get_stock_bot(uid)
            if not sbot.is_running:
                sbot.start()
                _log.info(f"[AUTO-START] Stock bot started for user {uid}")
        except Exception as e:
            _log.warning(f"[AUTO-START] Stock bot failed for user {uid}: {e}")

    # Watchdog auto-trader
    for uid in _enabled_uids("wd_enabled"):
        try:
            from features.watchdog.engine import start_auto_trader
            start_auto_trader(uid)
            _log.info(f"[AUTO-START] Watchdog auto-trader started for user {uid}")
        except Exception as e:
            _log.warning(f"[AUTO-START] Watchdog failed for user {uid}: {e}")

    # Claude Bot
    for uid in _enabled_uids("cb_enabled"):
        try:
            from claude_bot.bot_engine import start as cb_start
            cb_start(uid)
            _log.info(f"[AUTO-START] Claude Bot started for user {uid}")
        except Exception as e:
            _log.warning(f"[AUTO-START] Claude Bot failed for user {uid}: {e}")

    # Options-flow scanner (global, no per-user flag)
    try:
        from features.watchdog.options_flow import start_scanner, is_scanner_running
        if not is_scanner_running():
            start_scanner()
            _log.info("[AUTO-START] Options-flow scanner started")
    except Exception as e:
        _log.warning(f"[AUTO-START] Options-flow scanner failed: {e}")

    # News agent scanner (global, no per-user flag) — RSS/Reddit ingestion
    try:
        from features.news_agent.agent import start_scanner as news_start, is_scanner_running as news_running
        if not news_running():
            news_start()
            _log.info("[AUTO-START] News agent scanner started")
    except Exception as e:
        _log.warning(f"[AUTO-START] News agent scanner failed: {e}")


def _is_postgres():
    try:
        from db import IS_POSTGRES
        return IS_POSTGRES
    except Exception:
        return False


def _on_startup():
    """Initialize background tasks and auto-start on app startup."""
    import logging
    import threading
    _log = logging.getLogger(__name__)
    try:
        init_db()
        _log.info("Migrations complete.")
    except Exception as e:
        _log.error(f"Migration error: {e}")

    # Run cleanup AFTER fork — opening DB pool in master before fork breaks
    # workers under gunicorn --preload (they inherit broken pg sockets).
    try:
        _startup_cleanup()
    except Exception as e:
        _log.warning(f"Startup cleanup failed: {e}")

    start_background_checker()

    # Auto-start bots after short delay
    def _delayed_start():
        import time
        time.sleep(5)
        _auto_start_bots()
        # Start Smart Money daily refresh
        try:
            from features.smart_money.engine import start_daily_refresh
            start_daily_refresh()
            _log.info("[STARTUP] Smart Money daily refresh started")
        except Exception as e:
            _log.warning(f"[STARTUP] Smart Money start failed: {e}")
        # NOTE: Sector Radar is intentionally NOT seeded at startup — a full
        # scan (yfinance + news + LLM) would compete with bot auto-start on the
        # single gunicorn worker during the fragile boot window. The daily 9:30
        # CST cron and the manual "Run now" button populate it instead.
        _log.info("[STARTUP] Background checker and bot auto-start complete")

    threading.Thread(target=_delayed_start, daemon=True).start()


# ─── Gunicorn startup ────────────────────────────────────────────────

_startup_done = False

@app.before_request
def _ensure_startup():
    global _startup_done
    if not _startup_done:
        _startup_done = True
        import threading
        # Use file lock to ensure only one worker runs startup
        lock_path = "/tmp/tradewiz_startup.lock"
        try:
            import fcntl
            lock_file = open(lock_path, "w")
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            threading.Thread(target=_on_startup, daemon=True).start()
        except (IOError, OSError):
            # Another worker already running startup
            pass


if __name__ == "__main__":
    init_db()
    start_background_checker()
    app.run(debug=True, port=5001)
