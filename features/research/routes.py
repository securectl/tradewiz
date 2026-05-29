"""
Research Reports API.

Derived "Market Pressure" report — modeled on the Market-on-Close (MOC)
imbalance report layout (Top Buy / Top Sell tables) but built from the data
this app actually has: the latest persisted screener verdicts (for the
bullish/bearish split) plus live yfinance volume (for the magnitude). This is
NOT a live exchange MOC imbalance feed.

    $Total  = dollar volume = last close x latest volume   (pressure magnitude)
    #Total  = latest daily volume (shares)
    Buy side  = bullish verdicts  (strong / momentum tiers)
    Sell side = bearish verdicts  (avoid / cautious tiers)

Volume/industry are NOT persisted in screener_results (only the LLM vetting
fields are), so they are fetched live. Fetchers are injected for testability.
"""

import json
import logging
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from decorators import login_required

bp = Blueprint("research", __name__)
log = logging.getLogger(__name__)


# Mirror of the frontend _screenerVerdictTier so server + client agree on the
# bullish/bearish split that drives the buy vs sell classification.
def _verdict_tier(verdict):
    v = (verdict or "").upper()
    if "FALLING" in v or v == "AVOID":
        return "avoid"
    if v == "RISKY" or "CAUTIOUS" in v:
        return "cautious"
    if "OPPORTUNITY" in v or "STRONG" in v or v == "BULLISH":
        return "strong"
    if "BOTTOM" in v or "MOMENTUM" in v or "RECOVERY" in v or "ACCELERAT" in v:
        return "momentum"
    return "watch"


_BUY_TIERS = {"strong", "momentum"}
_SELL_TIERS = {"avoid", "cautious"}
_ACTIONABLE = _BUY_TIERS | _SELL_TIERS


def _fetch_live_quotes(tickers):
    """Batch last-close + latest-volume for tickers in a single yf.download call.

    Returns {ticker: {"price": float, "volume": float}}. Best-effort: tickers
    that fail to resolve are simply omitted.
    """
    out = {}
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return out
    try:
        import yfinance as yf
        data = yf.download(" ".join(tickers), period="5d", progress=False, threads=True)
        if data is None or data.empty:
            return out
        close, vol = data["Close"], data["Volume"]
        single = len(tickers) == 1
        for t in tickers:
            try:
                c_series = close if single else close[t]
                v_series = vol if single else vol[t]
                c = float(c_series.dropna().iloc[-1])
                v = float(v_series.dropna().iloc[-1])
                if c > 0 and v > 0:
                    out[t] = {"price": c, "volume": v}
            except Exception:
                continue
    except Exception as e:
        log.warning("[imbalances] live quote fetch failed: %s", e)
    return out


def _fetch_industries(tickers):
    """Best-effort industry lookup via yfinance .info, bounded thread pool.

    Only called for the final ranked rows (≤ 2*limit), so the .info fan-out
    stays small. Returns {ticker: industry}; missing tickers fall back upstream.
    """
    out = {}
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return out
    try:
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _one(t):
            try:
                info = yf.Ticker(t).info or {}
                return t, (info.get("industry") or "")
            except Exception:
                return t, ""

        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(_one, t) for t in tickers]
            try:
                for f in as_completed(futs, timeout=25):
                    t, ind = f.result()
                    if ind:
                        out[t] = ind
            except Exception:
                pass  # timeout — keep whatever resolved in time
    except Exception as e:
        log.warning("[imbalances] industry fetch failed: %s", e)
    return out


def _dedupe_best(rows):
    """One record per ticker, preferring an actionable verdict over a stale
    WATCH/oversold row, then most recent, then highest confidence.
    """
    by_ticker = {}
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        by_ticker.setdefault(t, []).append(r)

    best = {}
    for t, group in by_ticker.items():
        def _key(r):
            return (
                1 if _verdict_tier(r.get("verdict")) in _ACTIONABLE else 0,
                r.get("scan_date") or "",
                float(r.get("confidence") or 0),
            )
        best[t] = max(group, key=_key)
    return best


def build_imbalance_report(limit=10, days=14, include_crypto=False,
                           quote_fetcher=None, industry_fetcher=None):
    """Assemble the derived buy/sell pressure report.

    Steps: pull recent scans → one record per ticker → split buy/sell by verdict
    → pre-rank each side by market cap (cheap proxy) and keep a fetch pool →
    pull live volume for the pool → rank by dollar volume → enrich final rows
    with industry. Fetchers are injectable for testing.
    """
    from db import query, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    quote_fetcher = quote_fetcher or _fetch_live_quotes
    industry_fetcher = industry_fetcher or _fetch_industries

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    if include_crypto:
        rows = query(
            f"SELECT ticker, price, verdict, confidence, sector, name, market_cap, scan_date "
            f"FROM screener_results WHERE scan_date >= {P} "
            f"ORDER BY scan_date DESC, confidence DESC",
            (cutoff,),
        )
    else:
        rows = query(
            f"SELECT ticker, price, verdict, confidence, sector, name, market_cap, scan_date "
            f"FROM screener_results WHERE scan_date >= {P} AND category != {P} "
            f"ORDER BY scan_date DESC, confidence DESC",
            (cutoff, "crypto"),
        )

    best = _dedupe_best(rows)

    def _mk(r):
        try:
            mcap = float(r.get("market_cap") or 0)
        except (TypeError, ValueError):
            mcap = 0.0
        return {
            "symbol": r["ticker"],
            "sector": r.get("sector") or "—",
            "industry": "—",
            "verdict": r.get("verdict") or "—",
            "confidence": float(r.get("confidence") or 0),
            "price": float(r.get("price") or 0),
            "market_cap": mcap,
            "tier": _verdict_tier(r.get("verdict")),
            "scan_date": r.get("scan_date"),
        }

    items = [_mk(r) for r in best.values()]
    buy_pool = sorted([x for x in items if x["tier"] in _BUY_TIERS],
                      key=lambda x: (x["market_cap"], x["confidence"]), reverse=True)
    sell_pool = sorted([x for x in items if x["tier"] in _SELL_TIERS],
                       key=lambda x: (x["market_cap"], x["confidence"]), reverse=True)

    # Bound the live-volume fan-out: a generous pool, then re-rank by $ volume.
    pool_cap = min(max(limit * 3, limit + 5), 30)
    buy_pool, sell_pool = buy_pool[:pool_cap], sell_pool[:pool_cap]

    quotes = quote_fetcher([x["symbol"] for x in buy_pool + sell_pool])

    def _apply_quotes(pool):
        ranked = []
        for x in pool:
            q = quotes.get(x["symbol"])
            if not q:
                continue  # no live volume → no dollar-volume magnitude
            x["price"] = round(q["price"], 2)
            x["volume"] = int(q["volume"])
            x["dollar_volume"] = round(q["price"] * q["volume"], 2)
            ranked.append(x)
        ranked.sort(key=lambda x: x["dollar_volume"], reverse=True)
        return ranked[:limit]

    buys, sells = _apply_quotes(buy_pool), _apply_quotes(sell_pool)

    industries = industry_fetcher([x["symbol"] for x in buys + sells])
    for x in buys + sells:
        x["industry"] = industries.get(x["symbol"], x["sector"]) or "—"
        x.pop("market_cap", None)
        x.pop("tier", None)

    latest_scan = max((x["scan_date"] for x in items if x.get("scan_date")), default=None)

    return {
        "buys": buys,
        "sells": sells,
        "scan_date": latest_scan,
        "generated_at": datetime.now().isoformat(),
        "total_universe": len(items),
        "actionable_count": len(buy_pool) + len(sell_pool),
        "lookback_days": days,
        "source": ("Buy/sell split from screener verdicts; $ Vol & # Vol are live "
                   "yfinance dollar volume — not a live exchange Market-on-Close "
                   "imbalance feed."),
    }


@bp.route("/api/research/imbalances")
@login_required
def api_research_imbalances():
    """Top Buy / Top Sell pressure report (derived).

    Query params:
        limit          per-side row cap (5–25, default 10)
        days           lookback window for screener scans (1–30, default 5)
        include_crypto 1|0 — fold crypto category in (default 0, equities only)
    """
    try:
        limit = max(5, min(int(request.args.get("limit", 10)), 25))
    except (TypeError, ValueError):
        limit = 10
    try:
        days = max(1, min(int(request.args.get("days", 14)), 30))
    except (TypeError, ValueError):
        days = 14
    include_crypto = (request.args.get("include_crypto", "0") or "0").lower() in ("1", "true", "yes")

    try:
        report = build_imbalance_report(limit=limit, days=days, include_crypto=include_crypto)
        # Recent scans can be all-WATCH (e.g. only the oversold screen ran lately);
        # widen the window once so the report still surfaces the latest buy/sell signals.
        if not report["buys"] and not report["sells"] and days < 30:
            report = build_imbalance_report(limit=limit, days=30, include_crypto=include_crypto)
    except Exception as e:
        log.exception("[imbalances] build failed")
        return jsonify({"error": f"Failed to build report: {e}", "buys": [], "sells": []}), 500

    return jsonify(report)
