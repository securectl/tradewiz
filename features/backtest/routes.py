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
    """List available strategies."""
    from shared.backtest_strategies import STRATEGIES
    return jsonify({"strategies": list(STRATEGIES.keys())})


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
