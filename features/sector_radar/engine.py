"""
Sector Radar — Auto Research Analyst.

Ranks market sectors for a 6-12 month rotation play. Each daily run:
  1. Computes a quantitative score per sector ETF — relative strength vs SPY
     (1m/3m/6m), trend/MA-stack, breakout proximity, and volume surge.
  2. Gathers free-tier context — macro regime (watchdog), geopolitical/policy
     signals (Trump mood sector map), market-wide options flow (smart-money
     tilt), and news catalysts (GDELT/yfinance/Reddit) for the leaders.
  3. Feeds the structured evidence to a PhD-analyst LLM (Sonnet daily, Opus on
     the weekly deep-dive) that synthesizes the "next hot sector" thesis.

All data is free-tier — there is no paid news/fund-flow feed, so "smart money"
is proxied by ETF volume surges + market-wide put/call flow + relative
strength. Outputs are conviction-weighted theses backed by evidence, never
guarantees. A pure-quant fallback runs when the LLM is unavailable so the
feature never hard-fails.
"""

import json
import logging
import threading
import time
from datetime import datetime

import numpy as np
import yfinance as yf

from db import query, query_one, execute, IS_POSTGRES

logger = logging.getLogger(__name__)
_P = "%s" if IS_POSTGRES else "?"

# ─── Sector / theme universe ─────────────────────────────────
# Each entry: representative ETF (the RS/volume/breakout proxy) + a handful of
# leader tickers (what the user would actually trade). Curated to cover the
# 11 GICS sectors plus the high-velocity thematics where rotations actually
# show up first (semis, AI, defense, energy, miners, uranium, etc.).
SECTOR_UNIVERSE = [
    {"key": "technology",    "label": "Technology",            "etf": "XLK", "leaders": ["MSFT", "AAPL", "NVDA", "AVGO"]},
    {"key": "semis",         "label": "Semiconductors",        "etf": "SMH", "leaders": ["NVDA", "AMD", "AVGO", "MU", "TSM"]},
    {"key": "ai",            "label": "Artificial Intelligence","etf": "AIQ", "leaders": ["NVDA", "PLTR", "SMCI", "ARM", "MRVL"]},
    {"key": "software",      "label": "Software / Cloud",      "etf": "IGV", "leaders": ["MSFT", "CRM", "NOW", "SNOW", "ORCL"]},
    {"key": "cybersecurity", "label": "Cybersecurity",         "etf": "CIBR","leaders": ["PANW", "CRWD", "ZS", "FTNT"]},
    {"key": "comm",          "label": "Communication Services","etf": "XLC", "leaders": ["GOOGL", "META", "NFLX", "DIS"]},
    {"key": "financials",    "label": "Financials",            "etf": "XLF", "leaders": ["JPM", "BAC", "GS", "MS"]},
    {"key": "regional_banks","label": "Regional Banks",        "etf": "KRE", "leaders": ["WFC", "PNC", "USB", "TFC"]},
    {"key": "energy",        "label": "Energy",                "etf": "XLE", "leaders": ["XOM", "CVX", "COP", "EOG"]},
    {"key": "oil_ep",        "label": "Oil & Gas E&P",         "etf": "XOP", "leaders": ["OXY", "DVN", "FANG", "MRO"]},
    {"key": "energy_svcs",   "label": "Energy Services",       "etf": "OIH", "leaders": ["SLB", "HAL", "BKR"]},
    {"key": "healthcare",    "label": "Healthcare",            "etf": "XLV", "leaders": ["LLY", "UNH", "JNJ", "ABBV"]},
    {"key": "biotech",       "label": "Biotech",               "etf": "XBI", "leaders": ["VRTX", "REGN", "MRNA", "ALNY"]},
    {"key": "industrials",   "label": "Industrials",           "etf": "XLI", "leaders": ["GE", "CAT", "HON", "UBER"]},
    {"key": "defense",       "label": "Defense & Aerospace",   "etf": "ITA", "leaders": ["LMT", "RTX", "NOC", "GD"]},
    {"key": "discretionary", "label": "Consumer Discretionary","etf": "XLY", "leaders": ["AMZN", "TSLA", "HD", "MCD"]},
    {"key": "staples",       "label": "Consumer Staples",      "etf": "XLP", "leaders": ["PG", "KO", "COST", "WMT"]},
    {"key": "utilities",     "label": "Utilities",             "etf": "XLU", "leaders": ["NEE", "DUK", "SO", "CEG"]},
    {"key": "materials",     "label": "Materials",             "etf": "XLB", "leaders": ["LIN", "FCX", "NUE", "SHW"]},
    {"key": "gold_miners",   "label": "Gold & Silver Miners",  "etf": "GDX", "leaders": ["NEM", "GOLD", "AEM", "WPM"]},
    {"key": "uranium",       "label": "Uranium / Nuclear",     "etf": "URA", "leaders": ["CCJ", "UEC", "CEG", "NXE"]},
    {"key": "clean_energy",  "label": "Clean Energy",          "etf": "TAN", "leaders": ["FSLR", "ENPH", "RUN"]},
    {"key": "ev_battery",    "label": "EV / Battery",          "etf": "LIT", "leaders": ["TSLA", "ALB", "LI", "RIVN"]},
    {"key": "homebuilders",  "label": "Homebuilders",          "etf": "XHB", "leaders": ["DHI", "LEN", "PHM", "TOL"]},
    {"key": "real_estate",   "label": "Real Estate",           "etf": "XLRE","leaders": ["AMT", "PLD", "EQIX", "O"]},
    {"key": "china_em",      "label": "China / Emerging Mkts", "etf": "KWEB","leaders": ["BABA", "PDD", "JD", "BIDU"]},
    {"key": "crypto_equity", "label": "Crypto Equities",       "etf": "BITQ","leaders": ["COIN", "MSTR", "MARA", "RIOT"]},
]

_BENCHMARK = "SPY"

# Quant scoring weights (sum to 100)
_W_RS = 45        # relative strength vs SPY (the dominant rotation signal)
_W_TREND = 20     # MA stack / above key MAs
_W_BREAKOUT = 20  # proximity to 60-day high
_W_VOLUME = 15    # volume surge (smart-money inflow proxy)

# Bound news latency: only pull catalysts for the top-scoring sectors the
# analyst will actually focus on.
_NEWS_TOP_N = 6

_cache = {}  # in-process signal cache
_SIGNAL_TTL = 1800  # 30 min
_run_state = {"running": False, "started_at": 0}


# ─── Quant signal computation ────────────────────────────────

def _ret_pct(close, window):
    """Percent return over `window` trading days, or None if not enough data."""
    if close is None or len(close) <= window:
        return None
    past = close[-1 - window]
    if past <= 0:
        return None
    return (close[-1] / past - 1.0) * 100.0


def _sma(close, n):
    return float(np.mean(close[-n:])) if len(close) >= n else float(np.mean(close))


def compute_sector_signals(force=False):
    """Return a list of per-sector signal dicts sorted by composite score desc.

    Each dict: key, label, etf, leaders, price, rs_1m/3m/6m (excess vs SPY),
    ret_3m, trend flags, breakout proximity, volume surge, component scores,
    and the composite `score` (0-100). Cached 30 min unless force=True.
    """
    if not force:
        cached = _cache.get("signals")
        if cached and time.time() - cached[1] < _SIGNAL_TTL:
            return cached[0]

    etfs = [s["etf"] for s in SECTOR_UNIVERSE]
    symbols = list(dict.fromkeys([_BENCHMARK] + etfs))
    frames = {}
    try:
        data = yf.download(" ".join(symbols), period="1y", interval="1d",
                           progress=False, threads=True, group_by="ticker", timeout=40)
        if not data.empty:
            for t in symbols:
                try:
                    sub = data[t].dropna(how="all") if t in data else None
                    if sub is not None and not sub.empty:
                        frames[t] = sub
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"Sector Radar download failed: {e}")

    # Benchmark returns (excess RS is measured against these)
    spy = frames.get(_BENCHMARK)
    spy_close = spy["Close"].values.flatten().astype(float) if spy is not None and not spy.empty else None
    spy_1m = _ret_pct(spy_close, 21) or 0.0
    spy_3m = _ret_pct(spy_close, 63) or 0.0
    spy_6m = _ret_pct(spy_close, 126) or 0.0

    out = []
    for s in SECTOR_UNIVERSE:
        df = frames.get(s["etf"])
        if df is None or df.empty or len(df) < 60:
            continue
        try:
            close = df["Close"].values.flatten().astype(float)
            vol = df["Volume"].values.flatten().astype(float)
            price = float(close[-1])

            r1 = _ret_pct(close, 21) or 0.0
            r3 = _ret_pct(close, 63) or 0.0
            r6 = _ret_pct(close, 126) or 0.0
            rs_1m = r1 - spy_1m
            rs_3m = r3 - spy_3m
            rs_6m = r6 - spy_6m
            # Blend favors the 1-3m window (where rotations turn) over 6m
            rs_blend = 0.5 * rs_3m + 0.3 * rs_1m + 0.2 * rs_6m

            sma50 = _sma(close, 50)
            sma200 = _sma(close, 200)
            above_50 = price > sma50
            ma_stack = price > sma50 > sma200

            high_60 = float(np.max(close[-60:]))
            pct_from_high = (high_60 - price) / high_60 * 100 if high_60 > 0 else 99.0

            last5 = float(np.mean(vol[-5:])) if len(vol) >= 5 else float(np.mean(vol))
            prior20 = float(np.mean(vol[-25:-5])) if len(vol) >= 25 else float(np.mean(vol))
            vol_surge = last5 / prior20 if prior20 > 0 else 1.0

            # ── Component scores ──
            rs_score = max(0.0, min(_W_RS, 22.5 + rs_blend * 1.5))
            trend_score = (10 if above_50 else 0) + (10 if sma50 > sma200 else 0)
            breakout_score = max(0.0, min(_W_BREAKOUT, _W_BREAKOUT - pct_from_high * 2))
            volume_score = max(0.0, min(_W_VOLUME, (vol_surge - 1.0) * 18 + 5))
            score = int(round(rs_score + trend_score + breakout_score + volume_score))

            out.append({
                "key": s["key"], "label": s["label"], "etf": s["etf"], "leaders": s["leaders"],
                "price": round(price, 2),
                "ret_1m": round(r1, 1), "ret_3m": round(r3, 1), "ret_6m": round(r6, 1),
                "rs_1m": round(rs_1m, 1), "rs_3m": round(rs_3m, 1), "rs_6m": round(rs_6m, 1),
                "rs_blend": round(rs_blend, 1),
                "above_50d": above_50, "ma_stack": ma_stack,
                "pct_from_60d_high": round(pct_from_high, 1),
                "vol_surge": round(vol_surge, 2),
                "components": {
                    "rs": round(rs_score, 1), "trend": trend_score,
                    "breakout": round(breakout_score, 1), "volume": round(volume_score, 1),
                },
                "score": score,
                "catalysts": [],  # filled for top sectors during run_analysis
            })
        except Exception as e:
            logger.debug(f"Sector signal failed for {s['etf']}: {e}")
            continue

    out.sort(key=lambda x: -x["score"])
    _cache["signals"] = (out, time.time())
    return out


# ─── Context: macro, policy, smart-money, news ───────────────

def _macro_context():
    """Market regime + macro indicators from the watchdog engine."""
    try:
        from features.watchdog.engine import get_regime
        r = get_regime()
        return {
            "regime": r.get("regime", "UNKNOWN"),
            "composite_score": r.get("composite_score"),
            "axes": r.get("axes", {}),
            "vix": (r.get("market") or {}).get("vix"),
            "spy_5d": (r.get("market") or {}).get("spy_5d"),
        }
    except Exception as e:
        logger.warning(f"Sector Radar macro context failed: {e}")
        return {"regime": "UNKNOWN"}


def _policy_context():
    """Geopolitical / policy sector signals from Trump mood."""
    try:
        from trump_mood import get_trump_mood
        t = get_trump_mood()
        ts = t.get("trade_signals", {}) or {}
        return {
            "mood": t.get("mood", 0),
            "label": t.get("label", "NEUTRAL"),
            "buy_sectors": [x.get("sector") for x in ts.get("buy", []) if x.get("sector")][:6],
            "avoid_sectors": [x.get("sector") for x in ts.get("avoid", []) if x.get("sector")][:6],
            "signals": (ts.get("buy", []) or [])[:6],
        }
    except Exception as e:
        logger.warning(f"Sector Radar policy context failed: {e}")
        return {}


def _smart_money_context():
    """Market-wide options-flow tilt (put/call) as a smart-money proxy."""
    try:
        from features.watchdog.options_flow import get_current_flow
        flow = get_current_flow() or {}
        pc = []
        for sym, d in flow.items():
            r = d.get("pc_ratio") or d.get("put_call_ratio")
            if r:
                pc.append(float(r))
        if not pc:
            return {}
        avg_pc = sum(pc) / len(pc)
        tilt = "bullish" if avg_pc < 0.9 else ("bearish" if avg_pc > 1.1 else "neutral")
        return {"avg_put_call": round(avg_pc, 2), "tilt": tilt, "symbols": list(flow.keys())}
    except Exception as e:
        logger.debug(f"Sector Radar smart-money context failed: {e}")
        return {}


def _sector_catalysts(sector, limit=4):
    """Recent bullish news catalysts for a sector — ETF first, then top leader."""
    out = []
    try:
        from shared.news_fetcher import active_catalysts, headlines_for_llm
        for sym in [sector["etf"]] + sector["leaders"][:2]:
            try:
                cats = active_catalysts(sym, hours=72, limit=limit)
                out.extend(f"{sym}: {c}" for c in cats)
            except Exception:
                continue
            if len(out) >= limit:
                break
        if not out:  # fall back to plain headlines on the ETF
            try:
                hl = headlines_for_llm(sector["etf"], hours=72, limit=3)
                out.extend(f"{sector['etf']}: {h}" for h in hl)
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Sector catalysts failed for {sector['etf']}: {e}")
    return out[:limit]


# ─── LLM synthesis ───────────────────────────────────────────

def _llm_synthesize(top_signals, context, deep=False):
    """Ask the analyst LLM to synthesize a 6-12mo rotation thesis. Returns the
    parsed dict, or None on any failure (caller falls back to pure quant)."""
    try:
        from ai_validator import _call_openrouter, is_configured
        if not is_configured():
            return None
        role = "sector_research_deep" if deep else "sector_research"
        try:
            from shared.llm_config import get_model
            model = get_model(role, "anthropic/claude-sonnet-4-6")
        except Exception:
            model = "anthropic/claude-sonnet-4-6"

        board_lines = []
        for s in top_signals:
            board_lines.append(
                f"{s['label']} ({s['etf']}): score {s['score']}/100 | "
                f"RS vs SPY 1m {s['rs_1m']:+.1f}% 3m {s['rs_3m']:+.1f}% 6m {s['rs_6m']:+.1f}% | "
                f"{'MA-stacked' if s['ma_stack'] else ('above 50d' if s['above_50d'] else 'below 50d')} | "
                f"{s['pct_from_60d_high']:.1f}% from 60d high | vol surge {s['vol_surge']:.2f}x | "
                f"leaders {', '.join(s['leaders'])}"
                + (f" | catalysts: {'; '.join(s['catalysts'])}" if s.get("catalysts") else "")
            )
        board = "\n".join(board_lines)

        macro = context.get("macro", {})
        policy = context.get("policy", {})
        sm = context.get("smart_money", {})

        prompt = f"""You are a PhD-level macro & equity strategist running a sector-rotation desk. Your job: identify the ONE sector most likely to lead the market over the NEXT 6-12 MONTHS, using relative strength, breakout structure, volume (smart-money) inflow, the macro regime, geopolitics/policy, and news catalysts. Think like the analyst who called Oil during the Iran-US conflict and Semis (NVDA/AMD/MU) on the AI capex breakout — connect the dots between the news narrative and where capital is actually rotating.

MARKET REGIME: {macro.get('regime', 'UNKNOWN')} (composite {macro.get('composite_score', '?')}, VIX {macro.get('vix', '?')}, SPY 5d {macro.get('spy_5d', '?')})
OPTIONS-FLOW TILT (broad market, smart-money proxy): {sm.get('tilt', 'n/a')} (avg put/call {sm.get('avg_put_call', 'n/a')})
POLICY / GEOPOLITICS (Trump mood {policy.get('label', 'n/a')}, score {policy.get('mood', 'n/a')}):
  Favored sectors: {', '.join(policy.get('buy_sectors', [])) or 'none flagged'}
  Pressured sectors: {', '.join(policy.get('avoid_sectors', [])) or 'none flagged'}

QUANT SECTOR LEADERBOARD (ranked by composite momentum score):
{board}

Weigh durable 6-12mo drivers (capex cycles, policy, supply/demand regimes) over short-term noise. A sector already extended for 6 months may be late; a sector just turning up with a fresh catalyst and accelerating volume may be early. Be decisive but honest about risk.

Respond in STRICT JSON only:
{{
  "top_sector": "sector label",
  "etf": "representative ETF",
  "conviction": 0-100,
  "horizon": "e.g. 6-12 months",
  "thesis": "2-4 sentence 6-12 month thesis connecting the catalyst, the relative strength, and the smart-money flow",
  "why_now": ["catalyst 1", "catalyst 2", "catalyst 3"],
  "leaders": ["TICKER1", "TICKER2", "TICKER3"],
  "runner_up": "second-best sector label",
  "rotate_out_of": ["sector to underweight"],
  "key_risks": ["risk 1", "risk 2"]
}}"""

        raw = _call_openrouter(
            model,
            [
                {"role": "system", "content": "You are a rigorous sell-side sector strategist. You only respond in valid JSON. You reason from evidence and never fabricate data not given to you."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3, max_tokens=1100, timeout=60, role=role,
        )
        import re
        m = re.search(r'\{[\s\S]*\}', raw or "")
        if not m:
            return None
        result = json.loads(m.group())
        # Guard against error payloads / malformed JSON that parse but lack a
        # usable call (e.g. an OpenRouter {"error": ...} body). Treat as a miss
        # so the caller uses the pure-quant fallback instead of a blank thesis.
        if not result.get("top_sector"):
            logger.warning("Sector Radar LLM returned no top_sector — using quant fallback")
            return None
        try:
            result["conviction"] = max(0, min(100, int(float(result.get("conviction", 60)))))
        except (TypeError, ValueError):
            result["conviction"] = 60
        result["model"] = model
        return result
    except Exception as e:
        logger.warning(f"Sector Radar LLM synthesis failed: {e}")
        return None


def _quant_fallback(signals):
    """Pure-quant analyst output when the LLM is unavailable."""
    if not signals:
        return {
            "top_sector": "n/a", "etf": "", "conviction": 0, "horizon": "6-12 months",
            "thesis": "No sector data available this run.", "why_now": [],
            "leaders": [], "runner_up": "", "rotate_out_of": [], "key_risks": [],
            "fallback": True,
        }
    top = signals[0]
    runner = signals[1]["label"] if len(signals) > 1 else ""
    worst = signals[-1]["label"] if len(signals) > 1 else ""
    drivers = []
    if top["rs_3m"] > 0:
        drivers.append(f"+{top['rs_3m']:.0f}% relative strength vs SPY over 3 months")
    if top["ma_stack"]:
        drivers.append("price stacked above the 50d and 200d moving averages")
    if top["vol_surge"] >= 1.2:
        drivers.append(f"{top['vol_surge']:.1f}x volume surge (inflow)")
    if top["pct_from_60d_high"] <= 3:
        drivers.append("trading at/near 60-day highs (breakout)")
    return {
        "top_sector": top["label"], "etf": top["etf"],
        "conviction": min(95, max(40, top["score"])),
        "horizon": "6-12 months",
        "thesis": (f"{top['label']} screens as the strongest sector on momentum: "
                   + ", ".join(drivers) + "." if drivers
                   else f"{top['label']} ranks highest on the composite momentum score."),
        "why_now": drivers,
        "leaders": top["leaders"][:4],
        "runner_up": runner,
        "rotate_out_of": [worst] if worst else [],
        "key_risks": ["Quant-only ranking — LLM synthesis unavailable; no qualitative catalyst check.",
                      "Momentum can mean a sector is late, not early."],
        "fallback": True,
    }


# ─── Orchestration + persistence ─────────────────────────────

def run_analysis(deep=False, trigger="manual", force=True):
    """Run a full sector analysis, persist it, and return the report dict."""
    signals = compute_sector_signals(force=force)

    # Attach news catalysts to the top sectors only (bounded latency)
    for s in signals[:_NEWS_TOP_N]:
        try:
            s["catalysts"] = _sector_catalysts(s)
        except Exception:
            s["catalysts"] = []

    context = {
        "macro": _macro_context(),
        "policy": _policy_context(),
        "smart_money": _smart_money_context(),
    }

    analyst = _llm_synthesize(signals[:_NEWS_TOP_N], context, deep=deep)
    fallback = analyst is None
    if fallback:
        analyst = _quant_fallback(signals)

    report = {
        "generated_at": datetime.now().isoformat(),
        "mode": "deep" if deep else "daily",
        "trigger": trigger,
        "regime": context["macro"].get("regime", "UNKNOWN"),
        "context": context,
        "board": signals,
        "analyst": analyst,
        "fallback": fallback,
    }
    try:
        _save_report(report)
    except Exception as e:
        logger.error(f"Sector Radar save failed: {e}")
    return report


def _save_report(report):
    analyst = report.get("analyst", {})
    execute(
        f"""INSERT INTO sector_radar_reports
            (run_date, mode, regime, top_sector, conviction, report_json)
            VALUES ({_P},{_P},{_P},{_P},{_P},{_P})""",
        (
            report["generated_at"], report["mode"], report["regime"],
            analyst.get("top_sector", ""), float(analyst.get("conviction", 0) or 0),
            json.dumps(report),
        ),
    )


def get_latest():
    """Most recent report dict, or None."""
    try:
        row = query_one(
            "SELECT report_json FROM sector_radar_reports ORDER BY id DESC LIMIT 1"
        )
        if row and row.get("report_json"):
            return json.loads(row["report_json"])
    except Exception as e:
        logger.warning(f"Sector Radar get_latest failed: {e}")
    return None


def get_history(limit=20):
    """Compact list of prior calls for the trend view (no full board)."""
    try:
        rows = query(
            f"SELECT run_date, mode, regime, top_sector, conviction "
            f"FROM sector_radar_reports ORDER BY id DESC LIMIT {_P}",
            (int(limit),),
        )
        return [dict(r) for r in (rows or [])]
    except Exception as e:
        logger.warning(f"Sector Radar get_history failed: {e}")
        return []


def get_sector_detail(key):
    """Per-sector detail pulled from the latest report's board."""
    latest = get_latest()
    if not latest:
        return None
    for s in latest.get("board", []):
        if s.get("key") == key:
            return s
    return None


def run_async(deep=False, trigger="manual"):
    """Kick off a run in a background thread. Idempotent — one run at a time."""
    if _run_state.get("running"):
        return {"ok": False, "status": "already_running"}
    _run_state["running"] = True
    _run_state["started_at"] = time.time()

    def _bg():
        try:
            run_analysis(deep=deep, trigger=trigger)
        except Exception as e:
            logger.error(f"Sector Radar async run failed: {e}")
        finally:
            _run_state["running"] = False

    threading.Thread(target=_bg, daemon=True, name="sector-radar").start()
    return {"ok": True, "status": "started"}


def is_running():
    return bool(_run_state.get("running"))
