"""Backtest API — kick off runs, poll status, fetch reports."""

import json
import logging
import threading
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from db import IS_POSTGRES, execute, query, query_one
from decorators import login_required
from shared.helpers import _uid

logger = logging.getLogger(__name__)
bp = Blueprint("backtest", __name__)
P = "%s" if IS_POSTGRES else "?"

_running_runs = {}  # run_id -> {progress: int, total: int, started_at: str}


def _resolve_universe(name):
    """Map a universe name to a ticker list."""
    from screener import LARGECAP_TICKERS, MIDCAP_TICKERS, LOWCAP_TICKERS
    name = (name or "midcap").lower()
    if name == "largecap":
        return LARGECAP_TICKERS
    if name == "lowcap":
        return LOWCAP_TICKERS
    if name == "all":
        return list(set(LARGECAP_TICKERS + MIDCAP_TICKERS + LOWCAP_TICKERS))
    return MIDCAP_TICKERS  # default


@bp.route("/api/backtest/strategies")
@login_required
def api_strategies():
    """List available strategies + the curated top set (key/label/desc)."""
    from shared.backtest_strategies import STRATEGIES, TOP_STRATEGIES
    return jsonify({"strategies": list(STRATEGIES.keys()), "top": TOP_STRATEGIES})


@bp.route("/api/backtest/run", methods=["POST"])
@login_required
def api_run():
    """Kick off a backtest run in a background thread.

    Body JSON:
        strategy: name from STRATEGIES (default 'stage2')
        universe: 'largecap' | 'midcap' | 'lowcap' | 'all' (default 'midcap')
        start_date: 'YYYY-MM-DD' (default 2 years ago)
        end_date: 'YYYY-MM-DD' (default today)
        risk_pct, max_positions, max_hold_days, trail_after_pct: optional overrides
    """
    from shared.backtest_engine import run_backtest, FrictionModel
    from shared.backtest_strategies import get_strategy

    data = request.get_json(silent=True) or {}
    strategy = data.get("strategy", "stage2")
    universe_name = data.get("universe", "midcap")
    end = data.get("end_date") or datetime.now().strftime("%Y-%m-%d")
    start = data.get("start_date") or (datetime.now() - timedelta(days=2 * 365)).strftime("%Y-%m-%d")

    risk_pct = float(data.get("risk_pct", 1.0))
    max_positions = int(data.get("max_positions", 5))
    max_hold_days = int(data.get("max_hold_days", 60))
    trail_after_pct = data.get("trail_after_pct", 20.0)
    if trail_after_pct in ("", None):
        trail_after_pct = None
    else:
        trail_after_pct = float(trail_after_pct)

    try:
        detector = get_strategy(strategy)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    universe = _resolve_universe(universe_name)
    uid = _uid()

    # Pre-create the DB row so the user gets an ID immediately
    if IS_POSTGRES:
        row = query_one(
            f"INSERT INTO backtest_runs "
            f"(user_id, strategy, start_date, end_date, universe_size, status) "
            f"VALUES ({P},{P},{P},{P},{P},'running') RETURNING id",
            (uid, strategy, start, end, len(universe)),
        )
        run_id = int(row["id"])
    else:
        execute(
            f"INSERT INTO backtest_runs "
            f"(user_id, strategy, start_date, end_date, universe_size, status) "
            f"VALUES ({P},{P},{P},{P},{P},'running')",
            (uid, strategy, start, end, len(universe)),
        )
        row = query_one("SELECT last_insert_rowid() as id")
        run_id = int(row["id"]) if row else 0

    _running_runs[run_id] = {"progress": 0, "total": 0, "started_at": datetime.now().isoformat()}

    def _progress(i, total):
        st = _running_runs.get(run_id)
        if st:
            st["progress"] = i
            st["total"] = total

    def _worker():
        try:
            report = run_backtest(
                detector,
                universe,
                strategy_name=strategy,
                start_date=start,
                end_date=end,
                risk_pct=risk_pct,
                max_positions=max_positions,
                max_hold_days=max_hold_days,
                trail_after_pct=trail_after_pct,
                friction=FrictionModel(),
                progress_cb=_progress,
            )
            execute(
                f"UPDATE backtest_runs SET trade_count={P}, total_return_pct={P}, cagr_pct={P}, "
                f"sharpe={P}, max_drawdown_pct={P}, win_rate={P}, profit_factor={P}, "
                f"expectancy_pct={P}, report_json={P}, status='completed' WHERE id={P}",
                (
                    report.trade_count, report.total_return_pct, report.cagr_pct,
                    report.sharpe, report.max_drawdown_pct, report.win_rate,
                    report.profit_factor, report.expectancy_pct,
                    json.dumps(report.to_dict(), default=str), run_id,
                ),
            )
        except Exception as e:
            logger.exception(f"Backtest run {run_id} failed")
            execute(
                f"UPDATE backtest_runs SET status='failed', report_json={P} WHERE id={P}",
                (json.dumps({"error": str(e)}), run_id),
            )
        finally:
            _running_runs.pop(run_id, None)

    threading.Thread(target=_worker, daemon=True, name=f"backtest-{run_id}").start()

    return jsonify({
        "ok": True,
        "run_id": run_id,
        "strategy": strategy,
        "universe": universe_name,
        "universe_size": len(universe),
        "start_date": start,
        "end_date": end,
    })


@bp.route("/api/backtest/status/<int:run_id>")
@login_required
def api_status(run_id):
    row = query_one(
        f"SELECT id, strategy, status, trade_count, total_return_pct, cagr_pct, sharpe, "
        f"max_drawdown_pct, win_rate, profit_factor, expectancy_pct, created_at "
        f"FROM backtest_runs WHERE id = {P} AND user_id = {P}",
        (run_id, _uid()),
    )
    if not row:
        return jsonify({"error": "not found"}), 404
    out = dict(row)
    out["created_at"] = str(out["created_at"])
    if run_id in _running_runs:
        out["progress"] = _running_runs[run_id]
    return jsonify(out)


@bp.route("/api/backtest/<int:run_id>/report")
@login_required
def api_report(run_id):
    row = query_one(
        f"SELECT report_json FROM backtest_runs WHERE id = {P} AND user_id = {P}",
        (run_id, _uid()),
    )
    if not row or not row.get("report_json"):
        return jsonify({"error": "not found or still running"}), 404
    try:
        return jsonify(json.loads(row["report_json"]))
    except Exception:
        return jsonify({"error": "report_json corrupt"}), 500


# ── Per-ticker strategy backtest (analyzer "Backtest" button) ──
# Runs the curated top strategies against a single ticker and returns per-
# strategy metrics. In-memory results (single-worker gunicorn).
_ticker_runs = {}
_ticker_seq = [0]


@bp.route("/api/backtest/ticker", methods=["POST"])
@login_required
def api_backtest_ticker():
    """Backtest the top strategies on ONE ticker (from the analyzer).

    Body JSON:
        ticker: symbol (required)
        strategies: optional list of strategy keys (default = curated top set)
        start_date/end_date: 'YYYY-MM-DD' (default last 3 years)
    """
    from shared.backtest_strategies import TOP_STRATEGIES

    data = request.get_json(silent=True) or {}
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    end = data.get("end_date") or datetime.now().strftime("%Y-%m-%d")
    start = data.get("start_date") or (datetime.now() - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
    keys = data.get("strategies") or [s["key"] for s in TOP_STRATEGIES]
    keys = [str(k) for k in keys][:5]

    _ticker_seq[0] += 1
    run_id = _ticker_seq[0]
    _ticker_runs[run_id] = {
        "status": "running", "ticker": ticker, "progress": 0, "total": len(keys),
        "started_at": datetime.now().isoformat(), "results": [],
        "params": {"start_date": start, "end_date": end},
    }

    def _worker():
        from shared.backtest_engine import run_backtest, FrictionModel
        from shared.backtest_strategies import get_strategy, TOP_STRATEGIES as TS
        meta = {s["key"]: s for s in TS}
        for i, k in enumerate(keys):
            try:
                detector = get_strategy(k)
                rep = run_backtest(
                    detector, [ticker], strategy_name=k, start_date=start, end_date=end,
                    max_positions=1, friction=FrictionModel())
                pf = rep.profit_factor
                _ticker_runs[run_id]["results"].append({
                    "key": k, "label": meta.get(k, {}).get("label", k),
                    "desc": meta.get(k, {}).get("desc", ""),
                    "trades": rep.trade_count, "win_rate": rep.win_rate,
                    "total_return_pct": rep.total_return_pct, "cagr_pct": rep.cagr_pct,
                    "profit_factor": (None if pf in (float("inf"), float("-inf")) else round(pf, 2)),
                    "expectancy_pct": rep.expectancy_pct, "max_drawdown_pct": rep.max_drawdown_pct,
                    "sharpe": rep.sharpe,
                })
            except Exception as e:
                logger.exception(f"ticker backtest {ticker}/{k} failed")
                _ticker_runs[run_id]["results"].append({
                    "key": k, "label": meta.get(k, {}).get("label", k), "error": str(e)})
            _ticker_runs[run_id]["progress"] = i + 1
        _ticker_runs[run_id]["status"] = "completed"

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"ok": True, "run_id": run_id, "ticker": ticker,
                    "strategies": keys, "start_date": start, "end_date": end})


@bp.route("/api/backtest/ticker/<int:run_id>")
@login_required
def api_backtest_ticker_status(run_id):
    st = _ticker_runs.get(run_id)
    if not st:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "run_id": run_id, "status": st["status"], "ticker": st["ticker"],
        "progress": st["progress"], "total": st["total"],
        "results": st["results"], "params": st["params"],
    })


# ── Signal validation (forward-return backtest of the analyzer recommendation) ──
# Results are held in-memory (single-worker gunicorn) rather than a DB table —
# runs are cheap to reproduce and the report schema differs from backtest_runs.
_signal_runs = {}   # run_id -> {status, progress, total, started_at, report, error, params}
_signal_seq = [0]


@bp.route("/api/backtest/validate-signal", methods=["POST"])
@login_required
def api_validate_signal():
    """Backtest the analyzer's BUY/HOLD/SELL recommendation against forward
    returns — does the verdict beat a random-day entry, net of costs?

    Body JSON:
        universe: 'largecap'|'midcap'|'lowcap'|'all' (default 'midcap')
        tickers:  optional explicit list (overrides universe)
        max_tickers: cap universe size (default 40)
        start_date/end_date: 'YYYY-MM-DD' (default last 2 years)
        step: evaluate every Nth bar (default 1)
        horizons: list of forward-return windows in trading days (default [5,10,20])
    """
    from shared.signal_validator import validate_recommendation_signal

    data = request.get_json(silent=True) or {}
    universe_name = data.get("universe", "midcap")
    universe = _resolve_universe(universe_name)
    tickers = data.get("tickers")
    if tickers and isinstance(tickers, list):
        universe = [str(t).strip().upper() for t in tickers if str(t).strip()]
        universe_name = "custom"
    try:
        max_tickers = max(1, min(200, int(data.get("max_tickers", 40))))
    except (TypeError, ValueError):
        max_tickers = 40
    universe = list(universe)[:max_tickers]

    end = data.get("end_date") or datetime.now().strftime("%Y-%m-%d")
    start = data.get("start_date") or (datetime.now() - timedelta(days=2 * 365)).strftime("%Y-%m-%d")
    try:
        step = max(1, int(data.get("step", 1)))
    except (TypeError, ValueError):
        step = 1
    horizons = data.get("horizons") or [5, 10, 20]
    try:
        horizons = [int(h) for h in horizons if int(h) > 0][:5] or [5, 10, 20]
    except (TypeError, ValueError):
        horizons = [5, 10, 20]

    _signal_seq[0] += 1
    run_id = _signal_seq[0]
    _signal_runs[run_id] = {
        "status": "running", "progress": 0, "total": len(universe),
        "started_at": datetime.now().isoformat(), "report": None, "error": None,
        "params": {"universe": universe_name, "start_date": start, "end_date": end,
                   "step": step, "horizons": horizons},
    }

    def _progress(done, total):
        st = _signal_runs.get(run_id)
        if st:
            st["progress"], st["total"] = done, total

    def _worker():
        try:
            report = validate_recommendation_signal(
                universe, start, end, horizons=tuple(horizons), step=step, progress_cb=_progress)
            _signal_runs[run_id]["report"] = report
            _signal_runs[run_id]["status"] = "completed"
        except Exception as e:
            logger.exception(f"signal validation run {run_id} failed")
            _signal_runs[run_id]["status"] = "failed"
            _signal_runs[run_id]["error"] = str(e)

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({
        "ok": True, "run_id": run_id, "universe": universe_name,
        "universe_size": len(universe), "start_date": start, "end_date": end,
    })


@bp.route("/api/backtest/validate-signal/<int:run_id>")
@login_required
def api_validate_signal_status(run_id):
    st = _signal_runs.get(run_id)
    if not st:
        return jsonify({"error": "not found"}), 404
    out = {
        "run_id": run_id, "status": st["status"], "progress": st["progress"],
        "total": st["total"], "started_at": st["started_at"], "params": st["params"],
    }
    if st["status"] == "completed":
        out["report"] = st["report"]
    if st["error"]:
        out["error"] = st["error"]
    return jsonify(out)


@bp.route("/api/backtest/list")
@login_required
def api_list():
    rows = query(
        f"SELECT id, strategy, start_date, end_date, universe_size, trade_count, "
        f"total_return_pct, cagr_pct, sharpe, max_drawdown_pct, win_rate, "
        f"profit_factor, expectancy_pct, status, created_at "
        f"FROM backtest_runs WHERE user_id = {P} ORDER BY created_at DESC LIMIT 50",
        (_uid(),),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = str(d["created_at"])
        out.append(d)
    return jsonify({"runs": out})
