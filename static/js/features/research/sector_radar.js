/* Sector Radar — Auto Research Analyst UI (Research tab) */

let _srLoaded = false;
let _srPollTimer = null;
let _srLastGeneratedAt = null;

/* Called by switchTab('research') — default to the Sector Radar sub-view. */
function initResearchTab() {
    switchResearchSub('radar');
}

/* Toggle between the Sector Radar and Research Skills sub-views. */
function switchResearchSub(sub) {
    document.querySelectorAll('.research-subnav-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.sub === sub);
    });
    const radar = document.getElementById('research-sub-radar');
    const reports = document.getElementById('research-sub-reports');
    const skills = document.getElementById('research-sub-skills');
    if (radar) radar.style.display = (sub === 'radar') ? 'block' : 'none';
    if (reports) reports.style.display = (sub === 'reports') ? 'block' : 'none';
    if (skills) skills.style.display = (sub === 'skills') ? 'block' : 'none';

    if (sub === 'radar') {
        if (!_srLoaded) loadSectorRadar();
    } else if (sub === 'reports') {
        if (typeof loadImbalanceReport === 'function' && !_imbLoaded) loadImbalanceReport();
    } else if (sub === 'skills') {
        if (typeof loadSkillCatalog === 'function') loadSkillCatalog();
        if (typeof loadSkillJobs === 'function') loadSkillJobs();
    }
}

function _srEsc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _srConvictionClass(c) {
    c = Number(c) || 0;
    if (c >= 75) return 'sr-conv-high';
    if (c >= 55) return 'sr-conv-med';
    return 'sr-conv-low';
}

function _srPct(v) {
    if (v == null || isNaN(v)) return '–';
    const n = Number(v);
    return (n >= 0 ? '+' : '') + n.toFixed(1) + '%';
}

function _srPctClass(v) {
    if (v == null || isNaN(v)) return '';
    return Number(v) >= 0 ? 'sr-pos' : 'sr-neg';
}

async function loadSectorRadar() {
    const body = document.getElementById('sr-body');
    if (body && !_srLoaded) body.innerHTML = '<div class="sr-loading">Loading latest research&hellip;</div>';
    try {
        const [latestResp, histResp] = await Promise.all([
            fetch('/api/sector-radar/latest'),
            fetch('/api/sector-radar/history?limit=15'),
        ]);
        const latest = await latestResp.json();
        const hist = await histResp.json().catch(() => ({ history: [] }));
        _srLoaded = true;
        renderSectorRadar(latest, hist.history || []);
    } catch (err) {
        console.error('Sector Radar load failed', err);
        if (body) body.innerHTML = '<div class="sr-error">Failed to load Sector Radar. Try again shortly.</div>';
    }
}

function renderSectorRadar(latest, history) {
    const body = document.getElementById('sr-body');
    if (!body) return;

    const running = latest && latest.running;
    if (!latest || !latest.report) {
        body.innerHTML = `<div class="sr-empty">
            <p>No sector research yet.${running ? ' A run is in progress &mdash; results appear shortly.' : ''}</p>
            ${running ? '<div class="sr-loading">Analyzing sectors&hellip;</div>'
                      : '<p class="sr-sub">Click <b>Run now</b> to generate the first report.</p>'}
        </div>`;
        updateSrMeta(null, running);
        return;
    }

    const r = latest.report;
    _srLastGeneratedAt = r.generated_at;
    const a = r.analyst || {};
    const board = r.board || [];
    const ctx = r.context || {};
    const macro = ctx.macro || {};
    const policy = ctx.policy || {};
    const sm = ctx.smart_money || {};

    // ── Hero call ──
    const whyNow = (a.why_now || []).map(w => `<li>${_srEsc(w)}</li>`).join('');
    const leaders = (a.leaders || []).map(t =>
        `<button class="sr-ticker" onclick="sectorRadarAnalyze('${_srEsc(t)}')">${_srEsc(t)}</button>`
    ).join('');
    const risks = (a.key_risks || []).map(w => `<li>${_srEsc(w)}</li>`).join('');
    const rotateOut = (a.rotate_out_of || []).map(s => `<span class="sr-chip sr-chip-neg">${_srEsc(s)}</span>`).join('');

    const heroHtml = `
      <div class="sr-hero">
        <div class="sr-hero-main">
          <div class="sr-hero-label">NEXT HOT SECTOR &mdash; ${_srEsc(a.horizon || '6-12 months')}</div>
          <div class="sr-hero-sector">${_srEsc(a.top_sector || '—')}
            ${a.etf ? `<span class="sr-hero-etf">${_srEsc(a.etf)}</span>` : ''}</div>
          <p class="sr-hero-thesis">${_srEsc(a.thesis || '')}</p>
          ${whyNow ? `<div class="sr-hero-why"><div class="sr-h4">Why now</div><ul>${whyNow}</ul></div>` : ''}
          ${leaders ? `<div class="sr-hero-leaders"><div class="sr-h4">Leaders</div><div class="sr-ticker-row">${leaders}</div></div>` : ''}
        </div>
        <div class="sr-hero-side">
          <div class="sr-conv ${_srConvictionClass(a.conviction)}">
            <div class="sr-conv-num">${Math.round(Number(a.conviction) || 0)}</div>
            <div class="sr-conv-lbl">conviction</div>
          </div>
          ${a.runner_up ? `<div class="sr-side-row"><span>Runner-up</span><b>${_srEsc(a.runner_up)}</b></div>` : ''}
          ${rotateOut ? `<div class="sr-side-row"><span>Rotate out</span><div>${rotateOut}</div></div>` : ''}
        </div>
      </div>
      ${r.fallback ? '<div class="sr-fallback">Quant-only ranking — LLM synthesis was unavailable for this run.</div>' : ''}
      <div class="sr-context">
        <span class="sr-ctx"><b>Regime</b> ${_srEsc(macro.regime || '?')}${macro.composite_score != null ? ' (' + macro.composite_score + ')' : ''}</span>
        <span class="sr-ctx"><b>Smart-money tilt</b> ${_srEsc(sm.tilt || 'n/a')}${sm.avg_put_call != null ? ' · P/C ' + sm.avg_put_call : ''}</span>
        <span class="sr-ctx"><b>Policy</b> ${_srEsc(policy.label || 'n/a')}</span>
      </div>`;

    // ── Leaderboard ──
    const rows = board.map((s, i) => {
        const top = (a.top_sector && s.label === a.top_sector);
        const trend = s.ma_stack ? '<span class="sr-badge sr-badge-on">MA-stack</span>'
            : (s.above_50d ? '<span class="sr-badge">&gt;50d</span>' : '<span class="sr-badge sr-badge-off">&lt;50d</span>');
        const cats = (s.catalysts && s.catalysts.length)
            ? `<div class="sr-row-cats">${s.catalysts.map(c => `<span class="sr-chip">${_srEsc(c)}</span>`).join('')}</div>` : '';
        return `
          <tr class="${top ? 'sr-row-top' : ''}">
            <td class="sr-rank">${i + 1}</td>
            <td class="sr-sector-cell">
              <div class="sr-sector-name">${_srEsc(s.label)} <span class="sr-etf">${_srEsc(s.etf)}</span></div>
              ${cats}
            </td>
            <td><div class="sr-scorebar"><div class="sr-scorebar-fill" style="width:${Math.max(0, Math.min(100, s.score))}%"></div><span>${s.score}</span></div></td>
            <td class="${_srPctClass(s.rs_1m)}">${_srPct(s.rs_1m)}</td>
            <td class="${_srPctClass(s.rs_3m)}">${_srPct(s.rs_3m)}</td>
            <td class="${_srPctClass(s.rs_6m)}">${_srPct(s.rs_6m)}</td>
            <td>${trend}</td>
            <td>${s.pct_from_60d_high != null ? s.pct_from_60d_high.toFixed(1) + '%' : '–'}</td>
            <td>${s.vol_surge != null ? s.vol_surge.toFixed(2) + 'x' : '–'}</td>
          </tr>`;
    }).join('');

    const boardHtml = `
      <div class="sr-board">
        <div class="sr-h3">Sector leaderboard <span class="sr-sub">ranked by composite momentum (RS vs SPY · trend · breakout · volume)</span></div>
        <div class="sr-table-wrap">
          <table class="sr-table">
            <thead><tr>
              <th>#</th><th>Sector</th><th>Score</th>
              <th>RS 1m</th><th>RS 3m</th><th>RS 6m</th>
              <th>Trend</th><th>% off high</th><th>Vol</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;

    // ── History ──
    let histHtml = '';
    if (history && history.length) {
        const items = history.map(h => `
          <div class="sr-hist-row">
            <span class="sr-hist-date">${_srEsc((h.run_date || '').slice(0, 10))}</span>
            <span class="sr-hist-sector">${_srEsc(h.top_sector || '—')}</span>
            <span class="sr-hist-mode">${_srEsc(h.mode || '')}</span>
            <span class="sr-hist-conv">${Math.round(Number(h.conviction) || 0)}</span>
          </div>`).join('');
        histHtml = `
          <div class="sr-history">
            <div class="sr-h3">Call history</div>
            <div class="sr-hist-head"><span>Date</span><span>Top sector</span><span>Mode</span><span>Conv.</span></div>
            ${items}
          </div>`;
    }

    let risksHtml = '';
    if (risks) risksHtml = `<div class="sr-risks"><div class="sr-h4">Key risks</div><ul>${risks}</ul></div>`;

    body.innerHTML = heroHtml + risksHtml + boardHtml + histHtml;
    updateSrMeta(r, running);
}

function updateSrMeta(report, running) {
    const meta = document.getElementById('sr-meta');
    const btn = document.getElementById('sr-run-btn');
    if (btn) {
        btn.disabled = !!running;
        btn.textContent = running ? 'Running…' : 'Run now';
    }
    if (!meta) return;
    if (running) {
        meta.textContent = 'Analyzing sectors…';
        return;
    }
    if (report) {
        const dt = (report.generated_at || '').replace('T', ' ').slice(0, 16);
        const model = (report.analyst && report.analyst.model) ? report.analyst.model.split('/').pop() : '';
        meta.textContent = `Updated ${dt} · ${report.mode || 'daily'}${model ? ' · ' + model : ''}`;
    } else {
        meta.textContent = '';
    }
}

function sectorRadarAnalyze(ticker) {
    if (typeof window.analyzeTicker === 'function') {
        if (typeof switchTab === 'function') switchTab('analyzer');
        const input = document.getElementById('ticker-input') || document.getElementById('tickerInput');
        if (input) input.value = ticker;
        window.analyzeTicker(ticker);
    } else if (typeof switchTab === 'function') {
        switchTab('analyzer');
    }
}

async function sectorRadarRun() {
    const btn = document.getElementById('sr-run-btn');
    const meta = document.getElementById('sr-meta');
    if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
    if (meta) meta.textContent = 'Starting analysis…';
    try {
        const resp = await fetch('/api/sector-radar/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ deep: false }),
        });
        const data = await resp.json();
        if (!resp.ok && resp.status !== 409) {
            if (meta) meta.textContent = (data && data.message) || 'Run failed.';
            if (btn) { btn.disabled = false; btn.textContent = 'Run now'; }
            return;
        }
        _srPollSectorRadar(0);
    } catch (err) {
        console.error('Sector Radar run failed', err);
        if (meta) meta.textContent = 'Run failed.';
        if (btn) { btn.disabled = false; btn.textContent = 'Run now'; }
    }
}

/* Poll /latest until the run finishes (running=false AND a newer report). */
function _srPollSectorRadar(attempt) {
    if (_srPollTimer) clearTimeout(_srPollTimer);
    if (attempt > 45) {  // ~3 min cap
        loadSectorRadar();
        return;
    }
    _srPollTimer = setTimeout(async () => {
        try {
            const resp = await fetch('/api/sector-radar/latest');
            const data = await resp.json();
            const gen = data && data.report && data.report.generated_at;
            const fresh = gen && gen !== _srLastGeneratedAt;
            if (!data.running && fresh) {
                const histResp = await fetch('/api/sector-radar/history?limit=15').catch(() => null);
                const hist = histResp ? await histResp.json().catch(() => ({ history: [] })) : { history: [] };
                renderSectorRadar(data, hist.history || []);
            } else {
                updateSrMeta(data.report, true);
                _srPollSectorRadar(attempt + 1);
            }
        } catch (err) {
            _srPollSectorRadar(attempt + 1);
        }
    }, 4000);
}
