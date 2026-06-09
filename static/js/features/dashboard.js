/* Dashboard landing — showcase-style home wired to live data. */

function _dEsc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function _dPct(v) {
    if (v == null || isNaN(v)) return '—';
    const n = Number(v);
    return (n >= 0 ? '+' : '') + n.toFixed(1) + '%';
}
function _dMoney(v) {
    const n = Number(v) || 0;
    return (n >= 0 ? '+$' : '-$') + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
}
function _dRegimeColor(r) {
    return r === 'RISK-ON' ? 'var(--accent-green)'
        : r === 'DANGER' ? 'var(--accent-red)'
        : r === 'RISK-OFF' ? 'var(--accent-orange)' : 'var(--accent-yellow)';
}
function _dMiniBars(spark) {
    if (!spark || !spark.length) return '';
    const max = Math.max(1, ...spark.map(Math.abs));
    return '<div class="dash-minibars">' + spark.map(v => {
        const h = Math.max(8, Math.round(Math.abs(v) / max * 100));
        const cls = v >= 0 ? 'pos' : 'neg';
        return `<i class="${cls}" style="height:${h}%"></i>`;
    }).join('') + '</div>';
}

async function loadDashboard() {
    try {
        const [sumR, srR, gaugeR] = await Promise.all([
            fetch('/api/dashboard/summary'),
            fetch('/api/sector-radar/latest').catch(() => null),
            fetch('/api/market/gauge').catch(() => null),
        ]);
        const summary = await sumR.json();
        let report = null;
        if (srR) { const sr = await srR.json().catch(() => ({})); report = (sr && sr.report) || null; }
        let gauge = null;
        if (gaugeR) { gauge = await gaugeR.json().catch(() => null); }
        renderDashRegime(report, gauge);
        renderDashHero(report);
        renderDashKpis(summary.kpis || {});
        renderDashBoard(report);
        renderDashBots(summary.bots || []);
        loadDashSectorFlow(); // sector options money flow — non-blocking
        loadOpportunities();  // market opportunities (screener) — non-blocking
        loadOversold();       // most-watched oversold — non-blocking
        loadMySectors();      // sector resolution may hit network — non-blocking
    } catch (err) {
        console.error('dashboard load failed', err);
        const el = document.getElementById('dash-kpis');
        if (el) el.innerHTML = '<div class="dash-empty">Failed to load dashboard.</div>';
    }
}

function renderDashRegime(report, gauge) {
    const el = document.getElementById('dash-regime'); if (!el) return;
    const ctx = (report && report.context) || {};
    const r = ctx.macro || {}, sm = ctx.smart_money || {};

    // Prefer the live market gauge (always available, cached) for the macro
    // read; fall back to the sector-radar report's macro context.
    let regime = r.regime || 'UNKNOWN';
    let composite = r.composite_score;
    let vix = r.vix;
    let spy5d = r.spy_5d;
    if (gauge && gauge.available) {
        regime = gauge.stance === 'BUY' ? 'RISK-ON' : gauge.stance === 'SELL' ? 'RISK-OFF' : 'NEUTRAL';
        composite = gauge.score;
        const c = gauge.components || {};
        if (c.vix && c.vix.value != null) vix = c.vix.value;
        if (c.spy && c.spy.change_5d != null) spy5d = c.spy.change_5d;
    }
    el.innerHTML = `
        <span class="dash-pill"><span class="dash-led" style="background:${_dRegimeColor(regime)}"></span>${_dEsc(regime)}</span>
        <span class="dash-kv">Composite <b>${composite != null ? composite : '—'}</b></span>
        <span class="dash-kv">VIX <b>${vix != null ? vix : '—'}</b></span>
        <span class="dash-kv">SPY 5d <b>${_dPct(spy5d)}</b></span>
        <span class="dash-kv">Smart-money <b>${_dEsc(sm.tilt || 'n/a')}</b>${sm.avg_put_call != null ? ' · P/C ' + sm.avg_put_call : ''}</span>
        <span class="dash-kv dash-kv-right">Live market gauge</span>`;
}

function renderDashHero(report) {
    const el = document.getElementById('dash-sr'); if (!el) return;
    if (!report || !report.analyst) {
        el.innerHTML = `<h3 class="dash-h3">Sector Radar</h3>
            <div class="dash-empty">No sector research yet.
            <div style="margin-top:10px;"><button class="btn-analyze" onclick="switchTab('research')">Open Research → Run now</button></div></div>`;
        return;
    }
    const a = report.analyst;
    const conv = Math.round(Number(a.conviction) || 0);
    const why = (a.why_now || []).slice(0, 3).map(w => `<li>${_dEsc(w)}</li>`).join('');
    const leaders = (a.leaders || []).slice(0, 5).map(t =>
        `<button class="dash-ticker" onclick="sectorRadarAnalyze && sectorRadarAnalyze('${_dEsc(t)}')">${_dEsc(t)}</button>`).join('');
    el.innerHTML = `
        <h3 class="dash-h3">Sector Radar <span class="dash-sub">next hot sector · ${_dEsc(a.horizon || '6-12 months')}</span></h3>
        <div class="dash-sr-body">
          <div class="dash-sr-main">
            <div class="dash-sr-tag">NEXT HOT SECTOR</div>
            <div class="dash-sr-name">${_dEsc(a.top_sector || '—')}${a.etf ? `<span class="dash-sr-etf">${_dEsc(a.etf)}</span>` : ''}</div>
            <p class="dash-sr-thesis">${_dEsc(a.thesis || '')}</p>
            ${why ? `<ul class="dash-why">${why}</ul>` : ''}
            ${leaders ? `<div class="dash-ticker-row">${leaders}</div>` : ''}
          </div>
          <div class="dash-sr-side">
            <div class="dash-donut" style="--v:${conv}"><div class="dash-donut-in"><div class="dash-donut-num">${conv}</div><div class="dash-donut-lbl">conviction</div></div></div>
            ${a.runner_up ? `<div class="dash-side-row"><span>Runner-up</span><b>${_dEsc(a.runner_up)}</b></div>` : ''}
          </div>
        </div>
        ${report.fallback ? '<div class="dash-note">Quant-only ranking — LLM synthesis unavailable.</div>' : ''}`;
}

function renderDashKpis(k) {
    const el = document.getElementById('dash-kpis'); if (!el) return;
    const goal = k.daily_goal || { target: 0, actual: 0, pct: 0 };
    const pnlCls = (k.total_pnl || 0) >= 0 ? 'up' : 'down';
    el.innerHTML = `
        <div class="dash-kpi"><div class="dash-kpi-k">Total P&amp;L</div>
            <div class="dash-kpi-v ${pnlCls}">${_dMoney(k.total_pnl)}</div>
            <div class="dash-kpi-d">${k.wins || 0}W / ${k.losses || 0}L</div></div>
        <div class="dash-kpi"><div class="dash-kpi-k">Win Rate</div>
            <div class="dash-kpi-v">${(k.win_rate != null ? k.win_rate : 0)}%</div>
            <div class="dash-kpi-d">${k.trades || 0} trades · avg ${_dMoney(k.avg_pnl)}</div></div>
        <div class="dash-kpi"><div class="dash-kpi-k">Daily Goal</div>
            <div class="dash-kpi-v">$${Math.round(goal.actual)} <span class="dash-kpi-of">/ $${Math.round(goal.target)}</span></div>
            <div class="dash-bar"><i style="width:${Math.max(0, Math.min(100, goal.pct))}%"></i><span>${Math.round(goal.pct)}%</span></div></div>
        <div class="dash-kpi"><div class="dash-kpi-k">Active Bots</div>
            <div class="dash-kpi-v">${k.active_bots || 0}</div>
            <div class="dash-kpi-d">of 4 bots running</div></div>`;
}

function renderDashBoard(report) {
    const el = document.getElementById('dash-board'); if (!el) return;
    if (!report || !report.board || !report.board.length) {
        el.innerHTML = `<h3 class="dash-h3">Sector Leaderboard</h3><div class="dash-empty">No sector data yet.</div>`;
        return;
    }
    const top = (report.analyst && report.analyst.top_sector) || '';
    const rows = report.board.slice(0, 8).map((sct, i) => {
        const isTop = sct.label === top;
        const trend = sct.ma_stack ? '<span class="dash-badge on">MA-stack</span>'
            : (sct.above_50d ? '<span class="dash-badge">&gt;50d</span>' : '<span class="dash-badge off">&lt;50d</span>');
        return `<tr class="${isTop ? 'top' : ''}">
            <td class="dash-rank">${i + 1}</td>
            <td><b>${_dEsc(sct.label)}</b> <span class="dash-etf">${_dEsc(sct.etf)}</span></td>
            <td><div class="dash-scorebar"><i style="width:${Math.max(0, Math.min(100, sct.score))}%"></i><span>${sct.score}</span></div></td>
            <td class="${(sct.rs_3m || 0) >= 0 ? 'up' : 'down'}">${_dPct(sct.rs_3m)}</td>
            <td>${trend}</td>
            <td>${sct.vol_surge != null ? sct.vol_surge.toFixed(2) + 'x' : '—'}</td>
        </tr>`;
    }).join('');
    el.innerHTML = `<h3 class="dash-h3">Sector Leaderboard <span class="dash-sub">composite momentum</span></h3>
        <div class="dash-table-wrap"><table class="dash-table">
        <thead><tr><th>#</th><th>Sector</th><th>Score</th><th>RS 3m</th><th>Trend</th><th>Vol</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
}

function renderDashBots(bots) {
    const el = document.getElementById('dash-bots'); if (!el) return;
    if (!bots.length) { el.innerHTML = `<h3 class="dash-h3">Bot Performance</h3><div class="dash-empty">No bots.</div>`; return; }
    let total = 0;
    const rows = bots.map(b => {
        total += b.pnl_30d || 0;
        const cls = (b.pnl_30d || 0) >= 0 ? 'up' : 'down';
        const dot = b.enabled ? '<span class="dash-dot on"></span>' : '<span class="dash-dot"></span>';
        return `<div class="dash-botrow">
            <div class="dash-bot-name">${dot}${_dEsc(b.name)}<small>${b.enabled ? 'running' : 'stopped'} · ${b.trades_30d || 0} trades</small></div>
            ${_dMiniBars(b.spark)}
            <div class="dash-bot-stat"><div class="${cls}">${_dMoney(b.pnl_30d)}</div><div class="dash-bot-sub">${b.win_rate || 0}% win</div></div>
        </div>`;
    }).join('');
    const tcls = total >= 0 ? 'up' : 'down';
    el.innerHTML = `<h3 class="dash-h3">Bot Performance <span class="dash-sub">last 30 days</span></h3>${rows}
        <div class="dash-bot-total"><span>Combined 30d</span><b class="${tcls}">${_dMoney(total)}</b></div>`;
}

/* Best sectors from the current user's own trades (uid-scoped on the server). */
async function loadMySectors() {
    const el = document.getElementById('dash-mysectors');
    if (!el) return;
    el.innerHTML = '<h3 class="dash-h3">Your Best Sectors <span class="dash-sub">by realized P&L across your trades</span></h3><div class="dash-empty">Analyzing your trades…</div>';
    try {
        const resp = await fetch('/api/dashboard/my-sectors');
        const data = await resp.json();
        const secs = data.sectors || [];
        if (!secs.length) {
            el.innerHTML = '<h3 class="dash-h3">Your Best Sectors</h3><div class="dash-empty">No closed trades yet — your sector performance will appear here.</div>';
            return;
        }
        const max = Math.max(1, ...secs.map(s => Math.abs(s.pnl)));
        const rows = secs.map(s => {
            const cls = s.pnl >= 0 ? 'pos' : 'neg';
            const w = Math.round(Math.abs(s.pnl) / max * 100);
            return `<div class="dash-sec-row">
                <div class="dash-sec-name">${_dEsc(s.sector)}<small>${s.trades} trade${s.trades === 1 ? '' : 's'} · ${s.win_rate}% win</small></div>
                <div class="dash-sec-bar"><i class="${cls}" style="width:${w}%"></i></div>
                <div class="dash-sec-pnl ${s.pnl >= 0 ? 'up' : 'down'}">${_dMoney(s.pnl)}</div>
            </div>`;
        }).join('');
        el.innerHTML = `<h3 class="dash-h3">Your Best Sectors <span class="dash-sub">by realized P&L across your trades</span></h3>${rows}`;
    } catch (err) {
        console.error('my-sectors load failed', err);
        el.innerHTML = '<h3 class="dash-h3">Your Best Sectors</h3><div class="dash-empty">Failed to load.</div>';
    }
}

/* Market opportunities — oversold / breakout / momentum from the screener. */
function _dOppColor(t) {
    return { Oversold: 'var(--accent-blue)', Breakout: 'var(--accent-green)', Momentum: 'var(--accent-purple)',
             Growth: 'var(--accent-green)', Recovery: 'var(--accent-yellow)', AI: 'var(--accent-purple)',
             ETF: 'var(--accent-blue)', Crypto: 'var(--accent-yellow)', Metals: 'var(--accent-orange)' }[t] || 'var(--accent-blue)';
}
/* Generic client-side sort comparator: numeric fields vs text. */
function _dSortRows(rows, field, dir, numeric) {
    const m = dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
        let va = a[field], vb = b[field];
        if (numeric.has(field)) { va = Number(va) || 0; vb = Number(vb) || 0; }
        else { va = (va || '').toString().toUpperCase(); vb = (vb || '').toString().toUpperCase(); }
        return va < vb ? -1 * m : va > vb ? 1 * m : 0;
    });
}
function _dArrow(state, f) { return state.field === f ? (state.dir === 'asc' ? ' ▲' : ' ▼') : ''; }

/* ── Opportunities (sortable) ── */
let _oppsData = [];
let _oppsSort = { field: 'confidence', dir: 'desc' };
const _OPPS_NUM = new Set(['confidence', 'price']);

/* Money-flow chip: are buyers accumulating or are sellers taking profit?
   Shared visual contract with the Screener (same colors/labels). */
const _MF_META = {
    STRONG_IN: { label: 'Strong In', color: '#26a69a' },
    IN: { label: 'Money In', color: '#26a69a' },
    NEUTRAL: { label: 'Neutral', color: '#787b86' },
    DIVERGENCE: { label: 'Divergence', color: '#ff9800' },
    PROFIT_TAKING: { label: 'Profit-Taking', color: '#ff9800' },
    OUT: { label: 'Money Out', color: '#ef5350' },
    TOP: { label: 'Topping', color: '#ef5350' },
    STRONG_OUT: { label: 'Strong Out', color: '#ef5350' },
};
function _mfChip(signal, cmf, mfi) {
    const m = _MF_META[signal];
    if (!m) return '<span class="dash-opp-flow" style="color:#555;">—</span>';
    const tip = [cmf != null ? 'CMF ' + cmf : null, mfi != null ? 'MFI ' + Math.round(mfi) : null]
        .filter(Boolean).join(' · ');
    return `<span class="dash-opp-flow" title="${tip}" style="color:${m.color};border-color:${m.color}44;">${m.label}</span>`;
}

async function loadOpportunities() {
    const el = document.getElementById('dash-opps'); if (!el) return;
    el.innerHTML = '<h3 class="dash-h3">Opportunities <span class="dash-sub">oversold · breakout · momentum from the screener</span></h3><div class="dash-empty">Loading…</div>';
    try {
        const data = await (await fetch('/api/dashboard/opportunities')).json();
        _oppsData = data.opportunities || [];
        renderOpps();
    } catch (err) {
        console.error('opportunities load failed', err);
        el.innerHTML = '<h3 class="dash-h3">Opportunities</h3><div class="dash-empty">Failed to load.</div>';
    }
}
function sortOpps(f) {
    if (_oppsSort.field === f) _oppsSort.dir = _oppsSort.dir === 'asc' ? 'desc' : 'asc';
    else _oppsSort = { field: f, dir: _OPPS_NUM.has(f) ? 'desc' : 'asc' };
    renderOpps();
}
function renderOpps() {
    const el = document.getElementById('dash-opps'); if (!el) return;
    if (!_oppsData.length) {
        el.innerHTML = '<h3 class="dash-h3">Opportunities</h3><div class="dash-empty">No current opportunities — run the Screener to populate.</div>';
        return;
    }
    const sorted = _dSortRows(_oppsData, _oppsSort.field, _oppsSort.dir, _OPPS_NUM);
    const rows = sorted.map(o => `
        <div class="dash-opp" onclick="sectorRadarAnalyze && sectorRadarAnalyze('${_dEsc(o.ticker)}')">
            <span class="dash-opp-tk">${_dEsc(o.ticker)}</span>
            <span class="dash-opp-type" style="color:${_dOppColor(o.type)};border-color:${_dOppColor(o.type)}">${_dEsc(o.type)}</span>
            <span class="dash-opp-sec">${_dEsc(o.sector)}</span>
            <span class="dash-opp-verdict">${_dEsc(o.verdict || '')}</span>
            ${_mfChip(o.mf_signal, o.cmf, o.mfi)}
            <span class="dash-opp-price">${o.price != null ? '$' + o.price : ''}</span>
            <span class="dash-opp-conf">${Math.round(o.confidence)}%</span>
        </div>`).join('');
    const asOf = _oppsData[0] && _oppsData[0].scan_date ? ` <span class="dash-sub">· scanned ${_dEsc(_oppsData[0].scan_date)}</span>` : '';
    const h = (f, label) => `<span class="dash-sortable" onclick="sortOpps('${f}')">${label}${_dArrow(_oppsSort, f)}</span>`;
    el.innerHTML = `<h3 class="dash-h3">Opportunities <span class="dash-sub">oversold · breakout · momentum</span>${asOf}</h3>
        <div class="dash-opp-head">${h('ticker', 'Ticker')}${h('type', 'Type')}${h('sector', 'Sector')}${h('verdict', 'Verdict')}${h('mf_signal', 'Flow')}${h('price', 'Price')}${h('confidence', 'Conf')}</div>
        ${rows}`;
}

/* ── Most-watched oversold (sortable) ── */
let _ovData = [];
let _ovSort = { field: 'days', dir: 'desc' };
const _OV_NUM = new Set(['days', 'price']);

async function loadOversold() {
    const el = document.getElementById('dash-oversold'); if (!el) return;
    el.innerHTML = '<h3 class="dash-h3">Most-Watched Oversold <span class="dash-sub">persistent oversold = potential bottom forming</span></h3><div class="dash-empty">Loading…</div>';
    try {
        const data = await (await fetch('/api/dashboard/oversold')).json();
        _ovData = data.oversold || [];
        renderOversold();
    } catch (err) {
        console.error('oversold load failed', err);
        el.innerHTML = '<h3 class="dash-h3">Most-Watched Oversold</h3><div class="dash-empty">Failed to load.</div>';
    }
}
function sortOversold(f) {
    if (_ovSort.field === f) _ovSort.dir = _ovSort.dir === 'asc' ? 'desc' : 'asc';
    else _ovSort = { field: f, dir: _OV_NUM.has(f) ? 'desc' : 'asc' };
    renderOversold();
}
function renderOversold() {
    const el = document.getElementById('dash-oversold'); if (!el) return;
    if (!_ovData.length) {
        el.innerHTML = '<h3 class="dash-h3">Most-Watched Oversold</h3><div class="dash-empty">No persistent oversold names right now.</div>';
        return;
    }
    const maxd = Math.max(..._ovData.map(r => r.days));
    const sorted = _dSortRows(_ovData, _ovSort.field, _ovSort.dir, _OV_NUM);
    const body = sorted.map(r => `
        <div class="dash-opp" onclick="sectorRadarAnalyze && sectorRadarAnalyze('${_dEsc(r.ticker)}')">
            <span class="dash-opp-tk">${_dEsc(r.ticker)}</span>
            <span class="dash-ov-days"><span class="dash-ov-bar"><i style="width:${Math.round(r.days / maxd * 100)}%"></i></span>${r.days}d</span>
            <span class="dash-opp-sec">${_dEsc(r.sector)}</span>
            <span class="dash-opp-verdict">${_dEsc(r.verdict)}</span>
            <span class="dash-opp-price">${r.price != null ? '$' + r.price : ''}</span>
        </div>`).join('');
    const h = (f, label) => `<span class="dash-sortable" onclick="sortOversold('${f}')">${label}${_dArrow(_ovSort, f)}</span>`;
    el.innerHTML = `<h3 class="dash-h3">Most-Watched Oversold <span class="dash-sub">days in the daily oversold scan — longer = stronger bottom signal</span></h3>
        <div class="dash-ov-head">${h('ticker', 'Ticker')}${h('days', 'Days oversold')}${h('sector', 'Sector')}${h('verdict', 'Status')}${h('price', 'Price')}</div>
        ${body}`;
}

// ─── Sector options money flow (dashboard card) ─────────────
let _dashSfPoll = null;
function _sfMoneyDash(v) {
    const n = Math.abs(Number(v) || 0);
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return '$' + (n / 1e3).toFixed(0) + 'K';
    return '$' + n.toFixed(0);
}
async function loadDashSectorFlow() {
    const el = document.getElementById('dash-sectorflow'); if (!el) return;
    el.innerHTML = '<h3 class="dash-h3">Sector Options Flow</h3><div class="dash-empty">Loading…</div>';
    try {
        const d = await (await fetch('/api/smart-money/sector-flow')).json();
        if (d.computing && (!d.sectors || !d.sectors.length)) {
            el.innerHTML = '<h3 class="dash-h3">Sector Options Flow</h3><div class="dash-empty">Computing sector flow…</div>';
            clearTimeout(_dashSfPoll);
            _dashSfPoll = setTimeout(loadDashSectorFlow, 6000);
            return;
        }
        if (!d.sectors || !d.sectors.length) {
            el.innerHTML = '<h3 class="dash-h3">Sector Options Flow</h3><div class="dash-empty">No sector options data.</div>';
            return;
        }
        const tiltColor = d.tilt === 'RISK-ON' ? 'var(--accent-green)' : d.tilt === 'RISK-OFF' ? 'var(--accent-red)' : 'var(--accent-yellow)';
        const row = (s) => `<div class="dash-sf-row">
            <span class="dash-sf-name">${_dEsc(s.sector)} <span class="dash-sf-etf">${_dEsc(s.etf)}</span></span>
            <span class="dash-sf-net" style="color:${s.color}">${s.net_premium >= 0 ? '+' : ''}${_sfMoneyDash(s.net_premium)}</span>
            <span class="dash-sf-sig" style="color:${s.color}">${_dEsc(s.flow_signal)}</span></div>`;
        const ins = (d.money_in || []).slice(0, 4).map(row).join('') || '<div class="dash-empty">None</div>';
        const outs = (d.selling || []).slice(0, 4).map(row).join('') || '<div class="dash-empty">None</div>';
        el.innerHTML = `<h3 class="dash-h3">Sector Options Flow
                <span class="dash-sub">tilt <b style="color:${tiltColor}">${_dEsc(d.tilt)}</b> · calls vs puts</span></h3>
            <div class="dash-sf-cols">
              <div><div class="dash-sf-h dash-sf-in">▲ Money flowing in</div>${ins}</div>
              <div><div class="dash-sf-h dash-sf-out">▼ Being sold</div>${outs}</div>
            </div>`;
    } catch (e) {
        el.innerHTML = '<h3 class="dash-h3">Sector Options Flow</h3><div class="dash-empty">Failed to load.</div>';
    }
}
