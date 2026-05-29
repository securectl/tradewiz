"""
Screener API routes — extracted from app.py.
/api/screener, /api/qullamaggie, and /api/screener/hot-sectors
"""

import csv
import io
import json
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, Response

from shared.helpers import NumpyEncoder
from decorators import login_required
from ai_validator import is_configured
from analysis_engine import qullamaggie_scan
from screener import run_screener, get_hot_sectors

bp = Blueprint("screener", __name__)


@bp.route("/api/screener", methods=["POST"])
@login_required
def api_screener():
    data = request.get_json() or {}
    category = data.get("category", "lowcap")
    min_price = float(data.get("min_price", 2.0))
    max_price = float(data.get("max_price", 15.0))
    limit = int(data.get("limit", 20))
    min_price = max(0.5, min(min_price, 50))
    max_price = max(min_price + 0.5, min(max_price, 100))
    limit = max(5, min(limit, 50))

    if not is_configured():
        return jsonify({"error": "OpenRouter API key not configured.", "candidates_scanned": 0, "opportunities": [], "risky": [], "avoided": 0}), 200

    sectors = data.get("sectors", [])
    try:
        result = run_screener(min_price, max_price, limit, category=category, sectors=sectors)
        result_json = json.dumps(result, cls=NumpyEncoder, default=str)
        from flask import current_app
        return current_app.response_class(response=result_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": f"Screener failed: {str(e)}"}), 500


@bp.route("/api/qullamaggie", methods=["POST"])
@login_required
def api_qullamaggie():
    data = request.get_json() or {}
    category = data.get("category", "all")
    from screener import LOWCAP_TICKERS, MIDCAP_TICKERS, LARGECAP_TICKERS
    if category == "lowcap":
        tickers = LOWCAP_TICKERS
    elif category == "midcap":
        tickers = MIDCAP_TICKERS
    elif category == "largecap":
        tickers = LARGECAP_TICKERS
    else:
        tickers = list(set(LOWCAP_TICKERS + MIDCAP_TICKERS + LARGECAP_TICKERS))
    try:
        results = qullamaggie_scan(tickers)
        result_json = json.dumps({"results": results, "scanned": len(tickers)}, cls=NumpyEncoder, default=str)
        from flask import current_app
        return current_app.response_class(response=result_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": f"Qullamaggie scan failed: {str(e)}", "results": [], "scanned": 0}), 500


@bp.route("/api/screener/hot-sectors")
@login_required
def api_hot_sectors():
    period = request.args.get("period", "1mo")
    valid = ["1w", "2w", "1mo", "3mo", "6mo", "1y"]
    if period not in valid:
        period = "1mo"
    return jsonify(get_hot_sectors(period))


@bp.route("/api/screener/history")
@login_required
def api_screener_history():
    """Get historical screener results for trend analysis.
    ?category=lowcap&days=30  — all results for a category over N days
    ?ticker=AAPL&days=30      — all appearances of a ticker across categories
    ?category=lowcap&ticker=AAPL&days=30 — specific combo
    """
    from db import query, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"

    category = request.args.get("category")
    ticker = request.args.get("ticker")
    days = min(int(request.args.get("days", 30)), 90)

    conditions = [f"scan_date >= {P}"]
    params = []

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    params.append(cutoff)

    if category:
        conditions.append(f"category = {P}")
        params.append(category)
    if ticker:
        conditions.append(f"ticker = {P}")
        params.append(ticker.upper())

    where = " AND ".join(conditions)
    rows = query(
        f"SELECT category, scan_date, ticker, price, verdict, confidence, summary, sector, name, market_cap "
        f"FROM screener_results WHERE {where} ORDER BY scan_date DESC, confidence DESC LIMIT 500",
        tuple(params),
    )

    results = [{
        "category": r["category"], "scan_date": r["scan_date"], "ticker": r["ticker"],
        "price": r["price"], "verdict": r["verdict"], "confidence": r["confidence"],
        "summary": r.get("summary", ""), "sector": r.get("sector"), "name": r.get("name"),
        "market_cap": r.get("market_cap"),
    } for r in rows]

    return jsonify({"results": results, "count": len(results), "days": days})


@bp.route("/api/screener/trending")
@login_required
def api_screener_trending():
    """Find stocks appearing in multiple scans over recent days — persistent signals.
    Returns tickers sorted by number of appearances (most persistent first).
    """
    from db import query, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"

    days = min(int(request.args.get("days", 7)), 30)
    category = request.args.get("category")

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    if category:
        rows = query(
            f"SELECT ticker, name, sector, COUNT(DISTINCT scan_date) as appearances, "
            f"MAX(scan_date) as last_seen, MIN(scan_date) as first_seen, "
            f"ROUND(AVG(confidence)::numeric, 1) as avg_confidence, "
            f"MAX(verdict) as latest_verdict, ROUND(AVG(price)::numeric, 2) as avg_price "
            f"FROM screener_results WHERE category = {P} AND scan_date >= {P} "
            f"GROUP BY ticker, name, sector HAVING COUNT(DISTINCT scan_date) >= 2 "
            f"ORDER BY appearances DESC, avg_confidence DESC LIMIT 50"
            if IS_POSTGRES else
            f"SELECT ticker, name, sector, COUNT(DISTINCT scan_date) as appearances, "
            f"MAX(scan_date) as last_seen, MIN(scan_date) as first_seen, "
            f"ROUND(AVG(confidence), 1) as avg_confidence, "
            f"MAX(verdict) as latest_verdict, ROUND(AVG(price), 2) as avg_price "
            f"FROM screener_results WHERE category = {P} AND scan_date >= {P} "
            f"GROUP BY ticker, name, sector HAVING COUNT(DISTINCT scan_date) >= 2 "
            f"ORDER BY appearances DESC, avg_confidence DESC LIMIT 50",
            (category, cutoff),
        )
    else:
        rows = query(
            f"SELECT ticker, name, sector, COUNT(DISTINCT scan_date) as appearances, "
            f"COUNT(DISTINCT category) as categories, "
            f"MAX(scan_date) as last_seen, MIN(scan_date) as first_seen, "
            f"ROUND(AVG(confidence)::numeric, 1) as avg_confidence, "
            f"MAX(verdict) as latest_verdict, ROUND(AVG(price)::numeric, 2) as avg_price "
            f"FROM screener_results WHERE scan_date >= {P} "
            f"GROUP BY ticker, name, sector HAVING COUNT(DISTINCT scan_date) >= 2 "
            f"ORDER BY appearances DESC, categories DESC, avg_confidence DESC LIMIT 50"
            if IS_POSTGRES else
            f"SELECT ticker, name, sector, COUNT(DISTINCT scan_date) as appearances, "
            f"COUNT(DISTINCT category) as categories, "
            f"MAX(scan_date) as last_seen, MIN(scan_date) as first_seen, "
            f"ROUND(AVG(confidence), 1) as avg_confidence, "
            f"MAX(verdict) as latest_verdict, ROUND(AVG(price), 2) as avg_price "
            f"FROM screener_results WHERE scan_date >= {P} "
            f"GROUP BY ticker, name, sector HAVING COUNT(DISTINCT scan_date) >= 2 "
            f"ORDER BY appearances DESC, categories DESC, avg_confidence DESC LIMIT 50",
            (cutoff,),
        )

    # PostgreSQL ROUND(numeric, n) returns Decimal — Flask's JSON encoder
    # serializes Decimal as a string, which breaks `.toFixed()` on the client.
    # Coerce to float so the client sees a JS Number.
    def _f(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    results = [{
        "ticker": r["ticker"], "name": r.get("name"), "sector": r.get("sector"),
        "appearances": r["appearances"], "categories": r.get("categories", 1),
        "last_seen": r["last_seen"], "first_seen": r["first_seen"],
        "avg_confidence": _f(r.get("avg_confidence")), "latest_verdict": r.get("latest_verdict"),
        "avg_price": _f(r.get("avg_price")),
    } for r in rows]

    return jsonify({"trending": results, "count": len(results), "days": days})


# ─── Oversold Export (CSV / TXT / PDF) ──────────────────────
def _parse_iso_date(s, default):
    if not s:
        return default
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return default


def _fetch_oversold_rows(start_date, end_date):
    """Return raw oversold_daily rows between start_date and end_date inclusive."""
    from db import query, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    rows = query(
        f"SELECT ticker, scan_date, price, rsi_14, pct_change_1mo, market_cap, sector, name, "
        f"ai_verdict, ai_confidence, ai_summary, bottom_signal_strength, decline_reason, "
        f"status, days_tracked, first_seen, price_trend "
        f"FROM oversold_daily WHERE scan_date >= {P} AND scan_date <= {P} "
        f"ORDER BY ticker ASC, scan_date ASC",
        (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
    )
    return [dict(r) for r in rows]


def _dedupe_oversold(rows):
    """Collapse multiple scan rows per ticker into one enriched record.

    Each output row reflects what the user actually wants to know once:
      - days_seen: distinct scan dates the ticker appeared in window
      - first_seen / last_seen: bookends within window
      - latest_*: most recent scan's price/RSI/verdict/etc.
      - price_change_pct: drift from first observed price to latest
      - trend_path: condensed status timeline (e.g. "falling → stabilizing → bouncing")
    """
    by_ticker = {}
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        by_ticker.setdefault(t, []).append(r)

    out = []
    for ticker, scans in by_ticker.items():
        scans.sort(key=lambda x: x.get("scan_date") or "")
        first = scans[0]
        last = scans[-1]
        dates = sorted({s.get("scan_date") for s in scans if s.get("scan_date")})
        first_price = first.get("price") or 0
        last_price = last.get("price") or 0
        try:
            price_change_pct = ((float(last_price) - float(first_price)) / float(first_price)) * 100 if first_price else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            price_change_pct = 0.0
        path = []
        for s in scans:
            tr = s.get("price_trend") or s.get("status")
            if tr and (not path or path[-1] != tr):
                path.append(tr)
        out.append({
            "ticker": ticker,
            "name": last.get("name") or "",
            "sector": last.get("sector") or "",
            "days_seen": len(dates),
            "first_seen": dates[0] if dates else None,
            "last_seen": dates[-1] if dates else None,
            "first_price": float(first_price) if first_price else None,
            "latest_price": float(last_price) if last_price else None,
            "price_change_pct": round(price_change_pct, 2),
            "latest_rsi": last.get("rsi_14"),
            "latest_pct_change_1mo": last.get("pct_change_1mo"),
            "latest_verdict": last.get("ai_verdict") or "",
            "latest_confidence": last.get("ai_confidence"),
            "latest_status": last.get("status") or "",
            "latest_price_trend": last.get("price_trend") or "",
            "bottom_signal_strength": last.get("bottom_signal_strength") or "",
            "decline_reason": last.get("decline_reason") or "",
            "trend_path": " → ".join(path) if path else "",
            "summary": last.get("ai_summary") or "",
        })
    # Most insightful first: by days_seen desc, then confidence desc
    out.sort(key=lambda r: (r["days_seen"], r.get("latest_confidence") or 0), reverse=True)
    return out


_CSV_COLUMNS = [
    ("ticker", "Ticker"),
    ("name", "Name"),
    ("sector", "Sector"),
    ("days_seen", "Days Seen"),
    ("first_seen", "First Seen"),
    ("last_seen", "Last Seen"),
    ("first_price", "First Price"),
    ("latest_price", "Latest Price"),
    ("price_change_pct", "Price Δ %"),
    ("latest_rsi", "Latest RSI"),
    ("latest_pct_change_1mo", "1M Change %"),
    ("latest_verdict", "Latest Verdict"),
    ("latest_confidence", "Confidence"),
    ("latest_status", "Status"),
    ("latest_price_trend", "Trend"),
    ("trend_path", "Trend Path"),
    ("bottom_signal_strength", "Bottom Signal"),
    ("decline_reason", "Decline Reason"),
    ("summary", "AI Summary"),
]


def _build_csv(records):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([h for _, h in _CSV_COLUMNS])
    for r in records:
        writer.writerow([r.get(k, "") for k, _ in _CSV_COLUMNS])
    return buf.getvalue()


def _build_txt(records, start_date, end_date, deduped):
    lines = []
    lines.append("OVERSOLD SCREENER EXPORT")
    lines.append(f"Date Range: {start_date} → {end_date}")
    lines.append(f"Mode: {'Deduped (one row per ticker)' if deduped else 'Raw (every scan row)'}")
    lines.append(f"Total Records: {len(records)}")
    lines.append("=" * 78)
    for r in records:
        lines.append("")
        lines.append(f"{r.get('ticker','?')}  —  {r.get('name','') or 'Unknown'}  ({r.get('sector','') or 'n/a'})")
        seen = r.get("days_seen")
        if seen:
            lines.append(f"  Seen {seen} day(s) | first: {r.get('first_seen')} | last: {r.get('last_seen')}")
        fp = r.get("first_price")
        lp = r.get("latest_price")
        chg = r.get("price_change_pct")
        if fp is not None and lp is not None:
            lines.append(f"  Price: ${fp:.2f} → ${lp:.2f} ({chg:+.2f}%)")
        rsi = r.get("latest_rsi")
        if rsi is not None:
            try:
                lines.append(f"  RSI-14: {float(rsi):.1f} | 1M change: {float(r.get('latest_pct_change_1mo') or 0):+.1f}%")
            except (TypeError, ValueError):
                pass
        v = r.get("latest_verdict")
        c = r.get("latest_confidence")
        st = r.get("latest_status")
        tp = r.get("latest_price_trend")
        if v or c or st:
            lines.append(f"  Verdict: {v or '—'} | Confidence: {c or 0}% | Status: {st or '—'} | Trend: {tp or '—'}")
        path = r.get("trend_path")
        if path:
            lines.append(f"  Trajectory: {path}")
        bs = r.get("bottom_signal_strength")
        if bs:
            lines.append(f"  Bottom Signal: {bs}")
        dr = r.get("decline_reason")
        if dr:
            lines.append(f"  Decline Reason: {dr}")
        s = r.get("summary")
        if s:
            lines.append(f"  Summary: {s}")
    lines.append("")
    return "\n".join(lines)


def _build_pdf(records, start_date, end_date, deduped):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter),
                            leftMargin=0.4 * inch, rightMargin=0.4 * inch,
                            topMargin=0.4 * inch, bottomMargin=0.4 * inch)
    styles = getSampleStyleSheet()
    title = Paragraph("<b>Oversold Screener Export</b>", styles["Title"])
    subtitle = Paragraph(
        f"Date range: <b>{start_date} → {end_date}</b> &nbsp;|&nbsp; "
        f"Mode: <b>{'Deduped — one row per ticker' if deduped else 'Raw scan rows'}</b> &nbsp;|&nbsp; "
        f"Records: <b>{len(records)}</b>",
        styles["Normal"],
    )

    header = ["Ticker", "Name / Sector", "Seen", "First → Last", "Price Δ", "RSI", "Verdict", "Conf", "Trend Path"]
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7, leading=9)
    rows = [header]
    for r in records:
        try:
            rsi = f"{float(r.get('latest_rsi') or 0):.1f}"
        except (TypeError, ValueError):
            rsi = "—"
        try:
            chg = f"{float(r.get('price_change_pct') or 0):+.1f}%"
        except (TypeError, ValueError):
            chg = "—"
        first_p = r.get("first_price")
        last_p = r.get("latest_price")
        price_cell = f"${first_p:.2f}→${last_p:.2f}<br/>({chg})" if first_p and last_p else "—"
        rows.append([
            Paragraph(f"<b>{r.get('ticker','')}</b>", cell_style),
            Paragraph(f"{r.get('name','') or '—'}<br/><font color='#888888'>{r.get('sector','') or ''}</font>", cell_style),
            str(r.get("days_seen", "")),
            Paragraph(f"{r.get('first_seen','')}<br/>{r.get('last_seen','')}", cell_style),
            Paragraph(price_cell, cell_style),
            rsi,
            Paragraph(r.get("latest_verdict", "") or "—", cell_style),
            f"{int(r.get('latest_confidence') or 0)}%",
            Paragraph(r.get("trend_path", "") or "—", cell_style),
        ])

    table = Table(rows, repeatRows=1, colWidths=[
        0.65 * inch, 1.8 * inch, 0.45 * inch, 0.95 * inch,
        1.05 * inch, 0.5 * inch, 1.2 * inch, 0.5 * inch, 2.1 * inch,
    ])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2a2e39")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTSIZE", (0, 1), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7fa")]),
    ]))

    doc.build([title, Spacer(1, 6), subtitle, Spacer(1, 10), table])
    return buf.getvalue()


VALID_EXPORT_CATEGORIES = {
    "oversold", "lowcap", "midcap", "largecap", "etf",
    "metals_mining", "crypto", "ai", "gainers", "losers",
}


def _fetch_screener_rows(category, start_date, end_date):
    """Return raw screener_results rows for any non-oversold category."""
    from db import query, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    rows = query(
        f"SELECT category, scan_date, ticker, price, verdict, confidence, summary, "
        f"sector, name, market_cap "
        f"FROM screener_results WHERE category = {P} "
        f"AND scan_date >= {P} AND scan_date <= {P} "
        f"ORDER BY ticker ASC, scan_date ASC",
        (category, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
    )
    return [dict(r) for r in rows]


def _dedupe_screener(rows):
    """Collapse repeat appearances per ticker into a single insight-dense record.

    Per ticker we surface:
      - days_seen / first_seen / last_seen — recurrence within the window
      - price drift across the window
      - verdict_path — condensed verdict timeline (e.g. "WATCH → OPPORTUNITY")
      - best_confidence / latest_confidence
    """
    by_ticker = {}
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        by_ticker.setdefault(t, []).append(r)

    out = []
    for ticker, scans in by_ticker.items():
        scans.sort(key=lambda x: x.get("scan_date") or "")
        first = scans[0]
        last = scans[-1]
        dates = sorted({s.get("scan_date") for s in scans if s.get("scan_date")})
        first_price = first.get("price") or 0
        last_price = last.get("price") or 0
        try:
            price_change_pct = ((float(last_price) - float(first_price)) / float(first_price)) * 100 if first_price else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            price_change_pct = 0.0
        path = []
        for s in scans:
            v = (s.get("verdict") or "").strip()
            if v and (not path or path[-1] != v):
                path.append(v)
        confidences = [s.get("confidence") for s in scans if s.get("confidence") is not None]
        try:
            best_confidence = max(float(c) for c in confidences) if confidences else None
        except (TypeError, ValueError):
            best_confidence = None
        out.append({
            "ticker": ticker,
            "name": last.get("name") or "",
            "sector": last.get("sector") or "",
            "market_cap": last.get("market_cap"),
            "days_seen": len(dates),
            "first_seen": dates[0] if dates else None,
            "last_seen": dates[-1] if dates else None,
            "first_price": float(first_price) if first_price else None,
            "latest_price": float(last_price) if last_price else None,
            "price_change_pct": round(price_change_pct, 2),
            "latest_verdict": last.get("verdict") or "",
            "latest_confidence": last.get("confidence"),
            "best_confidence": best_confidence,
            "verdict_path": " → ".join(path) if path else "",
            "summary": last.get("summary") or "",
        })
    out.sort(key=lambda r: (r["days_seen"], r.get("best_confidence") or 0), reverse=True)
    return out


_SCREENER_CSV_COLUMNS = [
    ("ticker", "Ticker"),
    ("name", "Name"),
    ("sector", "Sector"),
    ("market_cap", "Market Cap"),
    ("days_seen", "Days Seen"),
    ("first_seen", "First Seen"),
    ("last_seen", "Last Seen"),
    ("first_price", "First Price"),
    ("latest_price", "Latest Price"),
    ("price_change_pct", "Price Δ %"),
    ("latest_verdict", "Latest Verdict"),
    ("latest_confidence", "Latest Confidence"),
    ("best_confidence", "Best Confidence"),
    ("verdict_path", "Verdict Path"),
    ("summary", "AI Summary"),
]


def _build_screener_csv(records):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([h for _, h in _SCREENER_CSV_COLUMNS])
    for r in records:
        writer.writerow([r.get(k, "") for k, _ in _SCREENER_CSV_COLUMNS])
    return buf.getvalue()


def _build_raw_screener_csv(rows):
    cols = [
        ("ticker", "Ticker"), ("scan_date", "Scan Date"), ("name", "Name"), ("sector", "Sector"),
        ("market_cap", "Market Cap"), ("price", "Price"),
        ("verdict", "Verdict"), ("confidence", "Confidence"), ("summary", "AI Summary"),
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([h for _, h in cols])
    for r in rows:
        writer.writerow([r.get(k, "") for k, _ in cols])
    return buf.getvalue()


def _build_screener_txt(category, records, start_date, end_date, deduped):
    lines = []
    lines.append(f"{category.upper().replace('_',' ')} SCREENER EXPORT")
    lines.append(f"Date Range: {start_date} → {end_date}")
    lines.append(f"Mode: {'Deduped (one row per ticker)' if deduped else 'Raw (every scan row)'}")
    lines.append(f"Total Records: {len(records)}")
    lines.append("=" * 78)
    for r in records:
        lines.append("")
        lines.append(f"{r.get('ticker','?')}  —  {r.get('name','') or 'Unknown'}  ({r.get('sector','') or 'n/a'})")
        seen = r.get("days_seen")
        if seen:
            lines.append(f"  Seen {seen} day(s) | first: {r.get('first_seen')} | last: {r.get('last_seen')}")
        fp = r.get("first_price")
        lp = r.get("latest_price")
        chg = r.get("price_change_pct")
        if fp is not None and lp is not None:
            lines.append(f"  Price: ${fp:.2f} → ${lp:.2f} ({chg:+.2f}%)")
        v = r.get("latest_verdict")
        lc = r.get("latest_confidence")
        bc = r.get("best_confidence")
        if v or lc or bc:
            lines.append(f"  Verdict: {v or '—'} | Latest Conf: {lc or 0}% | Best Conf: {bc or 0}%")
        path = r.get("verdict_path")
        if path:
            lines.append(f"  Verdict Path: {path}")
        s = r.get("summary")
        if s:
            lines.append(f"  Summary: {s}")
    lines.append("")
    return "\n".join(lines)


def _build_screener_pdf(category, records, start_date, end_date, deduped):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter),
                            leftMargin=0.4 * inch, rightMargin=0.4 * inch,
                            topMargin=0.4 * inch, bottomMargin=0.4 * inch)
    styles = getSampleStyleSheet()
    pretty = category.replace("_", " ").title()
    title = Paragraph(f"<b>{pretty} Screener Export</b>", styles["Title"])
    subtitle = Paragraph(
        f"Date range: <b>{start_date} → {end_date}</b> &nbsp;|&nbsp; "
        f"Mode: <b>{'Deduped — one row per ticker' if deduped else 'Raw scan rows'}</b> &nbsp;|&nbsp; "
        f"Records: <b>{len(records)}</b>",
        styles["Normal"],
    )

    header = ["Ticker", "Name / Sector", "Seen", "First → Last", "Price Δ", "Verdict", "Conf (L/B)", "Verdict Path"]
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7, leading=9)
    rows_out = [header]
    for r in records:
        try:
            chg = f"{float(r.get('price_change_pct') or 0):+.1f}%"
        except (TypeError, ValueError):
            chg = "—"
        first_p = r.get("first_price")
        last_p = r.get("latest_price")
        price_cell = f"${first_p:.2f}→${last_p:.2f}<br/>({chg})" if first_p and last_p else "—"
        lc = int(r.get("latest_confidence") or 0)
        bc = int(r.get("best_confidence") or 0)
        rows_out.append([
            Paragraph(f"<b>{r.get('ticker','')}</b>", cell_style),
            Paragraph(f"{r.get('name','') or '—'}<br/><font color='#888888'>{r.get('sector','') or ''}</font>", cell_style),
            str(r.get("days_seen", "")),
            Paragraph(f"{r.get('first_seen','')}<br/>{r.get('last_seen','')}", cell_style),
            Paragraph(price_cell, cell_style),
            Paragraph(r.get("latest_verdict", "") or "—", cell_style),
            f"{lc}% / {bc}%",
            Paragraph(r.get("verdict_path", "") or "—", cell_style),
        ])

    table = Table(rows_out, repeatRows=1, colWidths=[
        0.7 * inch, 1.9 * inch, 0.45 * inch, 0.95 * inch,
        1.1 * inch, 1.3 * inch, 0.85 * inch, 2.2 * inch,
    ])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2a2e39")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTSIZE", (0, 1), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7fa")]),
    ]))

    doc.build([title, Spacer(1, 6), subtitle, Spacer(1, 10), table])
    return buf.getvalue()


def _resolve_export_args():
    """Parse format/start/end/dedupe from the request — shared by both export routes."""
    fmt = (request.args.get("format", "csv") or "csv").lower()
    if fmt not in ("csv", "txt", "pdf"):
        return None, ("format must be csv, txt, or pdf", 400)
    today = datetime.now().date()
    default_start = today - timedelta(days=30)
    start_date = _parse_iso_date(request.args.get("start"), default_start)
    end_date = _parse_iso_date(request.args.get("end"), today)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    dedupe = (request.args.get("dedupe", "1") or "1").lower() not in ("0", "false", "no")
    return (fmt, start_date, end_date, dedupe), None


@bp.route("/api/screener/export")
@login_required
def api_screener_export():
    """Export any screener category as CSV / TXT / PDF.

    Query params:
        category=lowcap|midcap|...|oversold  (required)
        format=csv|txt|pdf                   (default csv)
        start=YYYY-MM-DD                     (default: 30 days ago)
        end=YYYY-MM-DD                       (default: today)
        dedupe=1|0                           (default 1; collapse per-ticker repeats)
    """
    category = (request.args.get("category", "") or "").strip().lower()
    if category not in VALID_EXPORT_CATEGORIES:
        return jsonify({"error": f"category must be one of {sorted(VALID_EXPORT_CATEGORIES)}"}), 400

    parsed, err = _resolve_export_args()
    if err:
        msg, code = err
        return jsonify({"error": msg}), code
    fmt, start_date, end_date, dedupe = parsed

    if category == "oversold":
        return _serve_oversold_export(fmt, start_date, end_date, dedupe)
    return _serve_category_export(category, fmt, start_date, end_date, dedupe)


def _serve_category_export(category, fmt, start_date, end_date, dedupe):
    try:
        rows = _fetch_screener_rows(category, start_date, end_date)
    except Exception as e:
        return jsonify({"error": f"Failed to fetch {category} data: {e}"}), 500

    records = _dedupe_screener(rows) if dedupe else rows
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"screener_{category}_{start_date}_{end_date}_{stamp}"

    if fmt == "csv":
        body = _build_screener_csv(records) if dedupe else _build_raw_screener_csv(rows)
        return Response(
            body, mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.csv"'},
        )
    if fmt == "txt":
        body = _build_screener_txt(category, records, start_date, end_date, dedupe)
        return Response(
            body, mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.txt"'},
        )
    try:
        body = _build_screener_pdf(category, records, start_date, end_date, dedupe)
    except ImportError:
        return jsonify({"error": "PDF export requires reportlab (pip install reportlab)"}), 500
    return Response(
        body, mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{base_name}.pdf"'},
    )


def _serve_oversold_export(fmt, start_date, end_date, dedupe):
    try:
        rows = _fetch_oversold_rows(start_date, end_date)
    except Exception as e:
        return jsonify({"error": f"Failed to fetch oversold data: {e}"}), 500
    records = _dedupe_oversold(rows) if dedupe else rows
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"oversold_{start_date}_{end_date}_{stamp}"
    if fmt == "csv":
        body = _build_csv(records) if dedupe else _build_raw_csv(rows)
        return Response(
            body, mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.csv"'},
        )
    if fmt == "txt":
        body = _build_txt(records, start_date, end_date, dedupe)
        return Response(
            body, mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.txt"'},
        )
    try:
        body = _build_pdf(records, start_date, end_date, dedupe)
    except ImportError:
        return jsonify({"error": "PDF export requires reportlab (pip install reportlab)"}), 500
    return Response(
        body, mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{base_name}.pdf"'},
    )


@bp.route("/api/screener/oversold/export")
@login_required
def api_oversold_export():
    """Backward-compat alias for /api/screener/export?category=oversold."""
    parsed, err = _resolve_export_args()
    if err:
        msg, code = err
        return jsonify({"error": msg}), code
    fmt, start_date, end_date, dedupe = parsed
    return _serve_oversold_export(fmt, start_date, end_date, dedupe)


def _build_raw_csv(rows):
    """Raw (non-deduped) CSV — one row per scan_date observation."""
    cols = [
        ("ticker", "Ticker"), ("scan_date", "Scan Date"), ("name", "Name"), ("sector", "Sector"),
        ("price", "Price"), ("rsi_14", "RSI-14"), ("pct_change_1mo", "1M Change %"),
        ("ai_verdict", "Verdict"), ("ai_confidence", "Confidence"),
        ("status", "Status"), ("price_trend", "Trend"), ("days_tracked", "Days Tracked"),
        ("first_seen", "First Seen"), ("bottom_signal_strength", "Bottom Signal"),
        ("decline_reason", "Decline Reason"), ("ai_summary", "AI Summary"),
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([h for _, h in cols])
    for r in rows:
        writer.writerow([r.get(k, "") for k, _ in cols])
    return buf.getvalue()
