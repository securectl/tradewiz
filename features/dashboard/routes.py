"""Dashboard landing — aggregated KPIs, per-bot performance, regime, smart-money.

Powers the showcase-style home page. Sector Radar hero + leaderboard are
fetched separately by the frontend from /api/sector-radar/latest.
"""

import logging
from datetime import datetime, timedelta

from flask import Blueprint, jsonify

from decorators import login_required
from shared.helpers import _uid
from db import query, query_one, IS_POSTGRES

logger = logging.getLogger(__name__)
bp = Blueprint("dashboard", __name__)
P = "%s" if IS_POSTGRES else "?"

# asset_type in bot_trades -> (friendly name, enable-flag, daily-goal key)
_BOTS = [
    ("watchdog", "ThunderBot",  "wd_enabled",        "daily_goal"),
    ("crypto",   "Crypto Bot",  "bot_enabled",       "daily_goal"),
    ("stock",    "Stock Bot",   "stock_bot_enabled", "stock_daily_goal"),
    ("claude",   "Claude Bot",  "cb_enabled",        "daily_goal"),
]


def _enabled(uid, flag):
    try:
        row = query_one(
            f"SELECT value FROM bot_config WHERE user_id={P} AND key={P}", (uid, flag)
        )
        return bool(row and row["value"] == "1")
    except Exception:
        return False


# ─── Sector resolution for "best sectors from my trades" ─────
_SECTOR_CACHE = {}  # in-process yfinance fallback cache
_ETF_SECTOR = {
    "SPY": "Broad Market", "DIA": "Broad Market", "QQQ": "Broad Market", "IWM": "Broad Market",
    "VOO": "Broad Market", "VTI": "Broad Market",
    "GLD": "Gold & Metals", "SLV": "Gold & Metals", "GDX": "Gold & Metals",
    "TLT": "Bonds", "IEF": "Bonds", "SHY": "Bonds", "HYG": "Bonds",
    "XLE": "Energy", "XOP": "Energy", "OIH": "Energy", "USO": "Energy",
    "XLK": "Technology", "SMH": "Technology", "SOXX": "Technology",
    "XLF": "Financials", "XLV": "Healthcare", "XLI": "Industrials", "XLU": "Utilities",
    "XLB": "Materials", "XLP": "Consumer Staples", "XLY": "Consumer Discretionary",
    "XLRE": "Real Estate", "XLC": "Communication Services",
}


def _norm_ticker(t):
    """Crypto trades use BloFin's -USDT suffix; screener uses yfinance -USD."""
    return t[:-5] + "-USD" if t.endswith("-USDT") else t


def _resolve_sectors(tickers):
    """Map each traded ticker to a sector. screener_results first (no network),
    then a small ETF map, crypto bucket, then a capped yfinance fallback."""
    out = {}
    norm = {t: _norm_ticker(t) for t in tickers}
    uniq = list({v for v in norm.values()})
    sec_by_norm = {}
    if uniq:
        try:
            ph = ",".join([P] * len(uniq))
            rows = query(
                f"SELECT ticker, sector FROM screener_results "
                f"WHERE sector IS NOT NULL AND sector<>'' AND sector<>'N/A' AND ticker IN ({ph})",
                tuple(uniq),
            )
            for r in rows:
                sec_by_norm.setdefault(r["ticker"], r["sector"])
        except Exception as e:
            logger.warning(f"sector screener lookup failed: {e}")

    misses = []
    for t in tickers:
        n = norm[t]
        if n in sec_by_norm:
            out[t] = sec_by_norm[n]
        elif t in _SECTOR_CACHE:
            out[t] = _SECTOR_CACHE[t]
        elif t.upper() in _ETF_SECTOR:
            out[t] = _ETF_SECTOR[t.upper()]
        elif "-USD" in t or "-USDT" in t:
            out[t] = "Crypto"
        else:
            misses.append(t)

    if misses:
        try:
            import yfinance as yf
            for t in misses[:12]:  # cap network lookups per call
                sec = "Other"
                try:
                    sec = (yf.Ticker(t).info or {}).get("sector") or "Other"
                except Exception:
                    sec = "Other"
                _SECTOR_CACHE[t] = sec
                out[t] = sec
        except Exception:
            pass
        for t in misses[12:]:
            out[t] = "Other"
    return out


def _aggregate_sectors(trades, secmap):
    """Pure aggregation: trades=[{coin,pnl}], secmap={coin:sector} →
    sorted list of {sector, pnl, trades, wins, win_rate} (best P&L first)."""
    agg = {}
    for tr in trades:
        sec = secmap.get(tr["coin"], "Other")
        a = agg.setdefault(sec, {"sector": sec, "pnl": 0.0, "trades": 0, "wins": 0})
        p = float(tr.get("pnl") or 0)
        a["pnl"] += p
        a["trades"] += 1
        if p > 0:
            a["wins"] += 1
    out = []
    for a in agg.values():
        a["pnl"] = round(a["pnl"], 2)
        a["win_rate"] = round(a["wins"] / a["trades"] * 100, 1) if a["trades"] else 0.0
        out.append(a)
    out.sort(key=lambda x: -x["pnl"])
    return out


@bp.route("/api/dashboard/summary")
@login_required
def api_dashboard_summary():
    uid = _uid()
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    # ── Portfolio KPIs (all bots combined) ──
    allrow = query_one(
        f"SELECT COALESCE(SUM(pnl),0) total, COUNT(*) trades, "
        f"SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins "
        f"FROM bot_trades WHERE status='closed' AND user_id={P}", (uid,)
    ) or {}
    trades = int(allrow.get("trades") or 0)
    wins = int(allrow.get("wins") or 0)
    total_pnl = round(float(allrow.get("total") or 0), 2)
    win_rate = round(wins / trades * 100, 1) if trades else 0.0
    avg_pnl = round(total_pnl / trades, 2) if trades else 0.0

    # Today's realized P&L (for the daily-goal tile)
    todayrow = query_one(
        f"SELECT COALESCE(SUM(pnl),0) total FROM bot_trades "
        f"WHERE status='closed' AND user_id={P} AND DATE(closed_at)={P}", (uid, today)
    ) or {}
    daily_actual = round(float(todayrow.get("total") or 0), 2)
    goal_row = query_one(
        f"SELECT value FROM bot_config WHERE user_id={P} AND key='daily_goal'", (uid,)
    )
    daily_target = float(goal_row["value"]) if goal_row and goal_row.get("value") else 1000.0
    daily_pct = round(max(0, daily_actual) / daily_target * 100, 1) if daily_target > 0 else 0.0

    # ── Per-bot 30-day performance + enabled state ──
    bots = []
    active = 0
    for asset, name, flag, _goalkey in _BOTS:
        is_on = _enabled(uid, flag)
        if is_on:
            active += 1
        row = query_one(
            f"SELECT COALESCE(SUM(pnl),0) pnl, COUNT(*) trades, "
            f"SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins "
            f"FROM bot_trades WHERE status='closed' AND user_id={P} "
            f"AND asset_type={P} AND closed_at>={P}", (uid, asset, month_ago)
        ) or {}
        bt = int(row.get("trades") or 0)
        bw = int(row.get("wins") or 0)
        # tiny sparkline: last 12 closed trades' cumulative pnl
        spark_rows = query(
            f"SELECT pnl FROM bot_trades WHERE status='closed' AND user_id={P} "
            f"AND asset_type={P} ORDER BY closed_at DESC LIMIT 12", (uid, asset)
        ) or []
        cum, spark = 0.0, []
        for r in reversed(spark_rows):
            cum += float(r["pnl"] or 0)
            spark.append(round(cum, 2))
        bots.append({
            "asset_type": asset, "name": name, "enabled": is_on,
            "pnl_30d": round(float(row.get("pnl") or 0), 2),
            "trades_30d": bt,
            "win_rate": round(bw / bt * 100, 1) if bt else 0.0,
            "spark": spark,
        })

    # Note: market regime + smart-money are intentionally NOT computed here —
    # they require a cold yfinance call that would make this endpoint slow.
    # The frontend reads them from the cached Sector Radar report instead.
    return jsonify({
        "kpis": {
            "total_pnl": total_pnl, "win_rate": win_rate, "wins": wins,
            "losses": trades - wins, "trades": trades, "avg_pnl": avg_pnl,
            "daily_goal": {"target": daily_target, "actual": daily_actual, "pct": daily_pct},
            "active_bots": active,
        },
        "bots": bots,
    })


@bp.route("/api/dashboard/my-sectors")
@login_required
def api_my_sectors():
    """Best/worst sectors by the CURRENT user's realized P&L (uid-scoped)."""
    uid = _uid()
    rows = query(
        f"SELECT coin, pnl FROM bot_trades WHERE status='closed' AND user_id={P}", (uid,)
    ) or []
    if not rows:
        return jsonify({"sectors": []})
    tickers = list({r["coin"] for r in rows})
    secmap = _resolve_sectors(tickers)
    sectors = _aggregate_sectors(rows, secmap)
    return jsonify({"sectors": sectors})


# ─── Market opportunities (oversold / breakout / momentum) ───
# Verdicts that represent an actionable long opportunity (set by the screener's
# AI vetting). WATCH / NEUTRAL / RISKY / FALLING KNIFE / AVOID are excluded.
_ACTIONABLE = {
    "OPPORTUNITY", "STRONG BUY", "MOMENTUM BUY", "BOTTOM FORMING",
    "STRONG GROWTH", "BULLISH", "RECOVERY BUY",
}
# screener category -> opportunity-type label shown on the dashboard
_CAT_TYPE = {
    "oversold": "Oversold", "gainers_1d": "Breakout", "gainers": "Breakout",
    "largecap": "Growth", "midcap": "Momentum", "lowcap": "Momentum",
    "etf": "ETF", "ai": "AI", "crypto": "Crypto",
    "metals_mining": "Metals", "losers_1d": "Recovery",
}


@bp.route("/api/dashboard/opportunities")
@login_required
def api_opportunities():
    """Current stock opportunities (oversold / breakout / momentum / growth)
    from the latest screener scan per category. Market data — same for all
    users; still auth-gated."""
    try:
        cats = query("SELECT category, MAX(scan_date) md FROM screener_results GROUP BY category") or []
    except Exception as e:
        logger.warning(f"opportunities cats query failed: {e}")
        return jsonify({"opportunities": []})

    opps = {}  # ticker -> best row (highest confidence)
    for c in cats:
        cat, md = c["category"], c["md"]
        if cat not in _CAT_TYPE or not md:
            continue
        try:
            rows = query(
                f"SELECT ticker, verdict, confidence, sector, price, summary "
                f"FROM screener_results WHERE category={P} AND scan_date={P}", (cat, md)
            ) or []
        except Exception:
            continue
        for r in rows:
            if (r.get("verdict") or "").upper() not in _ACTIONABLE:
                continue
            conf = float(r.get("confidence") or 0)
            existing = opps.get(r["ticker"])
            if existing and existing["confidence"] >= conf:
                continue
            opps[r["ticker"]] = {
                "ticker": r["ticker"], "type": _CAT_TYPE[cat],
                "verdict": r.get("verdict"), "confidence": round(conf, 0),
                "sector": r.get("sector") or "—",
                "price": round(float(r["price"]), 2) if r.get("price") else None,
                "summary": (r.get("summary") or "")[:150],
                "scan_date": str(md)[:10],
            }
    out = sorted(opps.values(), key=lambda x: -x["confidence"])[:15]
    return jsonify({"opportunities": out})


@bp.route("/api/dashboard/oversold")
@login_required
def api_oversold():
    """Most-watched oversold stocks — ranked by how many daily scans they've
    appeared in (persistence = potential bottom forming). Falling knives
    (still declining) are excluded so these read as opportunities."""
    cut = "(CURRENT_DATE - INTERVAL '30 days')::text" if IS_POSTGRES else "date('now','-30 days')"
    try:
        rows = query(
            f"SELECT ticker, COUNT(DISTINCT scan_date) days, MAX(scan_date) last_seen, "
            f"MAX(sector) sector, MAX(price) price, MAX(verdict) verdict, "
            f"ROUND(AVG(confidence)) conf "
            f"FROM screener_results WHERE category='oversold' AND scan_date >= {cut} "
            f"AND (verdict IS NULL OR UPPER(verdict) NOT IN ('FALLING KNIFE','AVOID')) "
            f"GROUP BY ticker HAVING COUNT(DISTINCT scan_date) >= 2 "
            f"ORDER BY days DESC, conf DESC LIMIT 12"
        ) or []
    except Exception as e:
        logger.warning(f"oversold query failed: {e}")
        return jsonify({"oversold": []})
    out = [{
        "ticker": r["ticker"], "days": int(r["days"] or 0),
        "sector": r.get("sector") or "—",
        "price": round(float(r["price"]), 2) if r.get("price") else None,
        "verdict": r.get("verdict") or "",
        "conf": int(r["conf"] or 0),
        "last_seen": str(r.get("last_seen"))[:10],
    } for r in rows]
    return jsonify({"oversold": out})
