"""
Daily market-alert digest.

Three signal sections, emailed once a day before the open:
  1. Volume spikes      — stocks trading well above their average daily volume
  2. Persistent oversold — tickers the oversold screen has flagged >= N days
  3. Upcoming earnings   — names reporting within the next few days

Data sources already in the app: yfinance (volume / earnings dates) and the
oversold_daily table. Detectors accept injected fetchers so the digest logic
is unit-testable without network or DB. Delivery goes through shared.mailer
(Resend → SMTP). Everything degrades gracefully: a failed section is simply
omitted rather than blocking the email.
"""
import logging
import os
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

VOLUME_SPIKE_RATIO = float(os.getenv("ALERT_VOL_SPIKE_RATIO", "2.0"))
OVERSOLD_MIN_DAYS = int(os.getenv("ALERT_OVERSOLD_MIN_DAYS", "5"))
EARNINGS_WINDOW_DAYS = int(os.getenv("ALERT_EARNINGS_DAYS", "7"))
TOP_N = int(os.getenv("ALERT_TOP_N", "3"))


# ─── Detectors ───────────────────────────────────────────────
def find_volume_spikes(limit=TOP_N, universe=None, downloader=None):
    """Stocks whose latest volume is >= VOLUME_SPIKE_RATIO x their trailing average."""
    if universe is None:
        from screener import LARGECAP_TICKERS, MIDCAP_TICKERS
        universe = list(dict.fromkeys(LARGECAP_TICKERS + MIDCAP_TICKERS))
    if downloader is None:
        import yfinance as yf
        downloader = lambda tk: yf.download(" ".join(tk), period="1mo", progress=False, threads=True)

    out = []
    try:
        data = downloader(universe)
        if data is None or getattr(data, "empty", True):
            return out
        vol, close = data["Volume"], data["Close"]
        single = len(universe) == 1
        for t in universe:
            try:
                vs = (vol if single else vol[t]).dropna()
                cs = (close if single else close[t]).dropna()
                if len(vs) < 6:
                    continue
                today = float(vs.iloc[-1])
                avg = float(vs.iloc[:-1].mean())
                if avg <= 0 or today <= 0:
                    continue
                ratio = today / avg
                if ratio >= VOLUME_SPIKE_RATIO:
                    out.append({
                        "ticker": t, "volume": int(today), "avg_volume": int(avg),
                        "ratio": round(ratio, 2), "price": round(float(cs.iloc[-1]), 2),
                    })
            except Exception:
                continue
    except Exception as e:
        log.warning("[alerts] volume-spike scan failed: %s", e)
    out.sort(key=lambda x: x["ratio"], reverse=True)
    return out[:limit]


def find_persistent_oversold(min_days=OVERSOLD_MIN_DAYS, limit=TOP_N, querier=None):
    """Tickers in the latest oversold scan with days_tracked >= min_days."""
    if querier is None:
        from db import query as querier
    from db import IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    try:
        latest = querier(f"SELECT MAX(scan_date) AS d FROM oversold_daily", ())
        d = latest[0]["d"] if latest and latest[0].get("d") else None
        if not d:
            return []
        rows = querier(
            f"SELECT ticker, name, sector, price, rsi_14, days_tracked, "
            f"bottom_signal_strength, ai_verdict "
            f"FROM oversold_daily WHERE scan_date = {P} AND days_tracked >= {P} "
            f"ORDER BY days_tracked DESC, rsi_14 ASC LIMIT {P}",
            (d, min_days, limit),
        )
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("[alerts] oversold scan failed: %s", e)
        return []


def _default_earnings_fetcher(ticker):
    """Return next earnings date (datetime.date) for a ticker, or None. Best-effort."""
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        dt = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)) and ed:
                dt = ed[0]
            else:
                dt = ed
        else:  # older yfinance returns a DataFrame
            try:
                dt = cal.loc["Earnings Date"][0]
            except Exception:
                dt = None
        if dt is None:
            return None
        if hasattr(dt, "date"):
            return dt.date()
        return dt
    except Exception:
        return None


def find_upcoming_earnings(days=EARNINGS_WINDOW_DAYS, limit=TOP_N, universe=None,
                           fetcher=None, today=None):
    """Names reporting within `days`. Bounded universe to keep the .info fan-out small."""
    if universe is None:
        from screener import LARGECAP_TICKERS
        universe = list(LARGECAP_TICKERS)[:40]
    fetcher = fetcher or _default_earnings_fetcher
    today = today or datetime.now().date()
    horizon = today + timedelta(days=days)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    found = []
    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(fetcher, t): t for t in universe}
            try:
                for f in as_completed(futs, timeout=30):
                    t = futs[f]
                    d = f.result()
                    if d and today <= d <= horizon:
                        found.append({"ticker": t, "earnings_date": d.isoformat(),
                                      "days_away": (d - today).days})
            except Exception:
                pass  # timeout — keep whatever resolved
    except Exception as e:
        log.warning("[alerts] earnings scan failed: %s", e)
    found.sort(key=lambda x: x["days_away"])
    return found[:limit]


# ─── Digest assembly (pure) ──────────────────────────────────
def _fmt_vol(n):
    n = float(n or 0)
    if n >= 1e9:
        return f"{n/1e9:.1f}B"
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    if n >= 1e3:
        return f"{n/1e3:.0f}K"
    return f"{int(n)}"


def _section(title, color, rows_html):
    body = rows_html or '<tr><td style="padding:8px 10px;color:#888;">Nothing flagged today.</td></tr>'
    return (
        f'<h2 style="font-size:15px;color:{color};margin:22px 0 8px;">{title}</h2>'
        f'<table style="width:100%;border-collapse:collapse;font-size:13px;">{body}</table>'
    )


def build_daily_digest(volume_spikes, oversold, earnings, app_name="TradeWiz", date_str=None):
    """Pure: assemble the three signal lists into (subject, html). No I/O."""
    date_str = date_str or datetime.now().strftime("%b %d, %Y")

    vol_rows = "".join(
        f'<tr style="border-bottom:1px solid #eee;">'
        f'<td style="padding:6px 10px;font-weight:700;">{r["ticker"]}</td>'
        f'<td style="padding:6px 10px;">${r.get("price","—")}</td>'
        f'<td style="padding:6px 10px;color:#1a7f37;font-weight:700;">{r.get("ratio","—")}x avg</td>'
        f'<td style="padding:6px 10px;color:#666;">{_fmt_vol(r.get("volume"))} vs {_fmt_vol(r.get("avg_volume"))}</td>'
        f'</tr>'
        for r in (volume_spikes or [])
    )
    ov_rows = "".join(
        f'<tr style="border-bottom:1px solid #eee;">'
        f'<td style="padding:6px 10px;font-weight:700;">{r.get("ticker")}</td>'
        f'<td style="padding:6px 10px;">${r.get("price","—")}</td>'
        f'<td style="padding:6px 10px;color:#666;">RSI {round(r.get("rsi_14") or 0,1)}</td>'
        f'<td style="padding:6px 10px;color:#b15c00;font-weight:700;">{r.get("days_tracked","—")}x days</td>'
        f'<td style="padding:6px 10px;color:#666;">{r.get("sector","") or ""}</td>'
        f'</tr>'
        for r in (oversold or [])
    )
    earn_rows = "".join(
        f'<tr style="border-bottom:1px solid #eee;">'
        f'<td style="padding:6px 10px;font-weight:700;">{r.get("ticker")}</td>'
        f'<td style="padding:6px 10px;">{r.get("earnings_date")}</td>'
        f'<td style="padding:6px 10px;color:#0a58ca;font-weight:700;">in {r.get("days_away")}d</td>'
        f'</tr>'
        for r in (earnings or [])
    )

    html = (
        f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;color:#222;">'
        f'<h1 style="font-size:18px;margin:0 0 2px;">{app_name} — Daily Alerts</h1>'
        f'<div style="color:#888;font-size:12px;">{date_str}</div>'
        + _section("📈 Volume Spikes", "#1a7f37", vol_rows)
        + _section(f"📉 Oversold {OVERSOLD_MIN_DAYS}x+ (persistent)", "#b15c00", ov_rows)
        + _section("📅 Earnings Coming Up", "#0a58ca", earn_rows)
        + '<p style="color:#aaa;font-size:11px;margin-top:24px;">Automated research digest — not investment advice. '
          'Past performance does not guarantee future results.</p>'
        + '</div>'
    )
    counts = len(volume_spikes or []) + len(oversold or []) + len(earnings or [])
    subject = f"{app_name} Daily Alerts — {date_str} ({counts} signals)"
    return subject, html


# ─── Collection + delivery ───────────────────────────────────
def collect_alerts():
    """Run all three detectors and return the raw lists (best-effort each)."""
    return {
        "volume_spikes": find_volume_spikes(),
        "oversold": find_persistent_oversold(),
        "earnings": find_upcoming_earnings(),
    }


def recipient_emails():
    """Who receives the digest: ALERT_RECIPIENTS env override, else opted-in users."""
    env = os.getenv("ALERT_RECIPIENTS", "").strip()
    if env:
        return [e.strip() for e in env.split(",") if e.strip()]
    try:
        from db import query, IS_POSTGRES
        P = "%s" if IS_POSTGRES else "?"
        rows = query(
            f"SELECT DISTINCT u.email FROM users u "
            f"JOIN bot_config b ON b.user_id = u.id "
            f"WHERE b.key = {P} AND b.value = {P} AND u.email IS NOT NULL",
            ("alert_emails", "1"),
        )
        return [r["email"] for r in rows if r.get("email")]
    except Exception as e:
        log.warning("[alerts] recipient lookup failed: %s", e)
        return []


def send_daily_alerts(recipients=None, app_name="TradeWiz"):
    """Build the digest and email it. Returns a small status dict."""
    from shared.mailer import send_email, is_configured
    if not is_configured():
        log.warning("[alerts] no email provider configured — skipping daily alerts")
        return {"sent": 0, "skipped": "no_provider"}

    data = collect_alerts()
    subject, html = build_daily_digest(data["volume_spikes"], data["oversold"],
                                       data["earnings"], app_name=app_name)
    recips = recipients if recipients is not None else recipient_emails()
    if not recips:
        log.info("[alerts] digest built but no recipients configured")
        return {"sent": 0, "skipped": "no_recipients", "signals": data}

    sent = sum(1 for to in recips if send_email(to, subject, html))
    log.info("[alerts] daily digest sent to %d/%d recipients", sent, len(recips))
    return {"sent": sent, "recipients": len(recips), "signals": data}
