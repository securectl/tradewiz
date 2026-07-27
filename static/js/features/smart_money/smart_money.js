// ─── Smart Money Tracker ─────────────────────────────────────

let _smLoading = false;
let _smDays = 30;

function _smGetDays() {
    const sel = document.getElementById('sm-days-filter');
    return sel ? parseInt(sel.value) : _smDays;
}

async function loadSmartMoney(force) {
    if (_smLoading) return;
    _smLoading = true;

    const days = _smGetDays();
    _smDays = days;

    const container = document.getElementById('smart-money-dashboard');
    if (!container) { _smLoading = false; return; }

    container.innerHTML = '<div style="text-align:center;padding:40px;font-size:12px;color:var(--text-secondary);">Loading institutional data...</div>';

    try {
        const [summaryResp, signalsResp] = await Promise.allSettled([
            fetch('/api/smart-money/summary?days=' + days),
            fetch('/api/smart-money/signals?days=' + days),
        ]);

        if (summaryResp.status === 'fulfilled' && summaryResp.value.status === 403) {
            container.innerHTML = `<div style="text-align:center;padding:40px 0;">
                <div style="font-size:28px;margin-bottom:8px;">&#128274;</div>
                <div style="font-size:14px;font-weight:700;color:var(--text-bright);margin-bottom:6px;">Pro Feature</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:14px;">Upgrade to Pro for Smart Money Tracker</div>
                <button onclick="showPricingModal()" class="btn-analyze" style="padding:8px 20px;font-size:12px;">Upgrade to Pro</button>
            </div>`;
            _smLoading = false;
            return;
        }

        let html = '';

        // Summary data
        let summary = null;
        if (summaryResp.status === 'fulfilled' && summaryResp.value.ok) {
            summary = await summaryResp.value.json();
        }

        // Signals data
        let signals = null;
        if (signalsResp.status === 'fulfilled' && signalsResp.value.ok) {
            signals = await signalsResp.value.json();
        }

        if (summary) {
            html += _smRenderActionable(summary);
            html += _smRenderStats(summary);
            html += _smRenderSignals(signals);
            html += _smRenderHotTickers(summary);
            html += _smRenderNewPositions(summary);
            html += _smRenderFundList(summary);
            html += _smRenderTraders(summary);
            html += _smRenderActivity(summary);
        } else {
            html = `<div style="text-align:center;padding:40px;">
                <div style="font-size:24px;margin-bottom:8px;">&#128202;</div>
                <div style="font-size:14px;font-weight:700;color:var(--text-bright);margin-bottom:8px;">No Data Yet</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:14px;">Click "Fetch Latest 13F" to pull institutional holdings from SEC EDGAR.</div>
                <button onclick="_smRefreshData()" class="btn-analyze" style="padding:8px 20px;font-size:12px;">Fetch Latest 13F Filings</button>
            </div>`;
        }

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="text-align:center;padding:40px;color:#ef5350;">Error: ${e.message}</div>`;
    }

    _smLoading = false;
}

function _smRenderActionable(d) {
    const sig = d.actionable_signal;
    if (!sig) return '';

    const color = sig.color || '#787b86';
    const icon = sig.action === 'ROTATE_IN' ? '⬆'
               : sig.action === 'ROTATE_OUT' ? '⬇'
               : sig.action === 'WAIT_STALE' ? '⏸'
               : '⏺';

    const reasons = (sig.reasons || []).map(r =>
        `<li style="margin:4px 0;font-size:12px;color:var(--text-secondary);">${r}</li>`
    ).join('');

    const renderPick = (p, isWarning) => {
        const c = isWarning ? '#ff4757' : '#00c896';
        return `<span style="display:inline-block;padding:5px 11px;background:${c}1a;border:1px solid ${c}66;border-radius:14px;font-size:11px;margin:3px 4px 3px 0;color:${c};font-weight:700;cursor:pointer;" onclick="event.stopPropagation();analyzeFromScreener && analyzeFromScreener('${p.ticker}')" title="${p.fund_count} funds, $${p.total_value}M flow">${p.ticker} <span style="opacity:0.7;font-weight:400;">×${p.fund_count}</span></span>`;
    };
    const picks = (sig.top_picks || []).map(p => renderPick(p, false)).join('');
    const warnings = (sig.warnings || []).map(p => renderPick(p, true)).join('');

    return `
    <div style="background:linear-gradient(135deg,${color}1a,${color}05);border:2px solid ${color};border-radius:14px;padding:20px 24px;margin-bottom:18px;">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;">
            <div style="flex:1;min-width:300px;">
                <div style="display:flex;align-items:center;gap:14px;margin-bottom:10px;">
                    <div style="font-size:34px;line-height:1;color:${color};">${icon}</div>
                    <div>
                        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:var(--text-secondary);">Smart Money Signal</div>
                        <div style="font-size:22px;font-weight:800;color:${color};line-height:1.1;">${sig.label}</div>
                    </div>
                </div>
                <div style="font-size:14px;color:var(--text-bright);font-weight:600;line-height:1.4;margin-bottom:10px;">${sig.headline}</div>
                <ul style="list-style:none;padding:0;margin:0 0 12px 0;">${reasons}</ul>
                ${picks ? `<div style="margin-top:10px;"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:5px;">Top conviction picks (whales accumulating)</div>${picks}</div>` : ''}
                ${warnings ? `<div style="margin-top:10px;"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:5px;">Distribution warnings (whales reducing)</div>${warnings}</div>` : ''}
                ${sig.next_watch ? `<div style="margin-top:14px;padding:8px 12px;background:rgba(0,0,0,0.15);border-radius:6px;font-size:11px;color:var(--text-secondary);"><strong style="color:var(--text-bright);">Next:</strong> ${sig.next_watch}</div>` : ''}
            </div>
            <div style="text-align:center;min-width:90px;">
                <div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.5px;">Confidence</div>
                <div style="font-size:32px;font-weight:800;color:${color};line-height:1.1;">${sig.confidence}<span style="font-size:14px;color:var(--text-secondary);">%</span></div>
            </div>
        </div>
    </div>`;
}


function _smRenderStats(d) {
    const entities = d.entities || [];
    const activity = d.recent_activity || [];
    const hot = d.hot_tickers || [];
    const days = d.days || _smDays;
    return `
    <!-- Time Filter + Data Freshness -->
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);">Showing data for last <strong style="color:var(--text-bright);">${days === 7 ? '1 Week' : days === 14 ? '2 Weeks' : days === 30 ? '1 Month' : '1 Quarter'}</strong></div>
        <select id="sm-days-filter" onchange="loadSmartMoney(true)" style="padding:6px 12px;background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:8px;color:var(--text-bright);font-size:12px;font-weight:600;">
            <option value="7" ${days===7?'selected':''}>1 Week</option>
            <option value="14" ${days===14?'selected':''}>2 Weeks</option>
            <option value="30" ${days===30?'selected':''}>1 Month</option>
            <option value="90" ${days===90?'selected':''}>1 Quarter</option>
        </select>
    </div>
    ${d.latest_filing_date ? `<div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-secondary);border-radius:8px;border:1px solid var(--border-color);margin-bottom:12px;font-size:10px;">
        <span style="color:var(--text-secondary);">Latest 13F Filing:</span>
        <strong style="color:var(--text-bright);font-size:11px;">${new Date(d.latest_filing_date).toLocaleDateString('en-US', {month:'long', day:'numeric', year:'numeric'})}</strong>
        <span style="color:var(--text-secondary);margin-left:8px;">|</span>
        <span style="color:var(--text-secondary);">${d.data_note || '13F filings are quarterly with 45-day lag.'}</span>
    </div>` : ''}
    <!-- Stats -->
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;">
        <div style="flex:1;min-width:100px;background:var(--bg-tertiary);border-radius:10px;padding:12px;text-align:center;border:1px solid var(--border-color);">
            <div style="font-size:22px;font-weight:800;color:var(--text-bright);">${entities.length}</div>
            <div style="font-size:9px;text-transform:uppercase;color:var(--text-secondary);">Funds Tracked</div>
        </div>
        <div style="flex:1;min-width:100px;background:var(--bg-tertiary);border-radius:10px;padding:12px;text-align:center;border:1px solid var(--border-color);">
            <div style="font-size:22px;font-weight:800;color:var(--text-bright);">${hot.length}</div>
            <div style="font-size:9px;text-transform:uppercase;color:var(--text-secondary);">Tickers Active</div>
        </div>
        <div style="flex:1;min-width:100px;background:var(--bg-tertiary);border-radius:10px;padding:12px;text-align:center;border:1px solid var(--border-color);">
            <div style="font-size:22px;font-weight:800;color:var(--text-bright);">${activity.length}</div>
            <div style="font-size:9px;text-transform:uppercase;color:var(--text-secondary);">Moves (${days}d)</div>
        </div>
        <div style="flex:1;min-width:100px;background:var(--bg-tertiary);border-radius:10px;padding:12px;text-align:center;border:1px solid var(--border-color);">
            <div style="font-size:22px;font-weight:800;color:#00c896;">${(d.new_positions || []).length}</div>
            <div style="font-size:9px;text-transform:uppercase;color:var(--text-secondary);">New Positions</div>
        </div>
    </div>`;
}

function _smRenderSignals(data) {
    if (!data || !data.signals || !data.signals.length) {
        return `<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:12px;padding:16px;margin-bottom:16px;">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:8px;">Convergence Signals</div>
            <div style="font-size:11px;color:var(--text-secondary);text-align:center;padding:10px;">No convergence signals yet. Signals appear when multiple funds build positions in the same ticker.</div>
        </div>`;
    }

    const rows = data.signals.map(s => {
        const barW = Math.min(100, s.fund_count * 20);
        const dateRange = s.first_date && s.last_date
            ? `${new Date(s.first_date).toLocaleDateString('en-US',{month:'short',day:'numeric'})} — ${new Date(s.last_date).toLocaleDateString('en-US',{month:'short',day:'numeric'})}`
            : '';
        return `<div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border-color);">
            <div style="min-width:60px;">
                <div style="font-weight:800;color:var(--text-bright);font-size:14px;">${s.ticker}</div>
                ${s.company ? `<div style="font-size:8px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:80px;">${s.company}</div>` : ''}
            </div>
            <div style="min-width:90px;font-size:10px;font-weight:800;color:${s.color};padding:3px 10px;border-radius:8px;background:${s.color}22;border:1px solid ${s.color}44;text-align:center;">${s.signal}</div>
            <div style="flex:1;">
                <div style="font-size:11px;color:var(--text-bright);font-weight:700;">${s.fund_count} funds | <span style="color:#00c896;font-weight:800;">${s.buys} buys</span>, <span style="color:#ff4757;font-weight:800;">${s.sells} sells</span></div>
                ${dateRange ? `<div style="font-size:9px;color:var(--text-secondary);margin-top:2px;">Filed: <strong style="color:var(--text-bright);">${dateRange}</strong></div>` : ''}
            </div>
            <div style="width:100px;height:6px;border-radius:3px;background:var(--bg-primary);overflow:hidden;">
                <div style="width:${barW}%;height:100%;background:${s.color};border-radius:3px;"></div>
            </div>
            <div style="min-width:80px;text-align:right;font-size:11px;font-weight:700;color:var(--text-bright);">$${(s.total_value / 1e6).toFixed(1)}M</div>
        </div>`;
    }).join('');

    return `<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:12px;padding:16px;margin-bottom:16px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:10px;">Convergence Signals — Multiple Funds Same Direction</div>
        ${rows}
    </div>`;
}

function _smRenderHotTickers(d) {
    const hot = d.hot_tickers || [];
    if (!hot.length) return '';

    const rows = hot.slice(0, 15).map(t => {
        const total = (t.buy_count || 0) + (t.sell_count || 0);
        const buyPct = total > 0 ? (t.buy_count / total * 100) : 50;
        const dateRange = t.first_filing && t.last_filing
            ? `${new Date(t.first_filing).toLocaleDateString('en-US',{month:'short',day:'numeric'})} — ${new Date(t.last_filing).toLocaleDateString('en-US',{month:'short',day:'numeric'})}`
            : '';
        return `<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border-color);">
            <div style="min-width:55px;font-weight:800;color:var(--text-bright);font-size:13px;">${t.ticker}</div>
            <div style="flex:1;">
                <div style="height:7px;border-radius:3px;background:var(--bg-primary);overflow:hidden;display:flex;">
                    <div style="width:${buyPct}%;background:#00c896;"></div>
                    <div style="width:${100-buyPct}%;background:#ff4757;"></div>
                </div>
                ${dateRange ? `<div style="font-size:8px;color:var(--text-secondary);margin-top:2px;">${dateRange}</div>` : ''}
            </div>
            <div style="min-width:70px;font-size:10px;text-align:right;">
                <span style="color:#00c896;font-weight:700;">${t.buy_count || 0} buy</span> /
                <span style="color:#ff4757;font-weight:700;">${t.sell_count || 0} sell</span>
            </div>
            <div style="min-width:50px;font-size:10px;color:var(--text-bright);text-align:right;font-weight:700;">${t.fund_count} funds</div>
        </div>`;
    }).join('');

    return `<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:12px;padding:16px;margin-bottom:16px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:10px;">Hot Tickers — Most Active Across Funds</div>
        ${rows}
    </div>`;
}

function _smRenderNewPositions(d) {
    const positions = d.new_positions || [];
    if (!positions.length) return '';

    const rows = positions.slice(0, 10).map(p => {
        const val = p.value_usd ? '$' + (p.value_usd / 1e6).toFixed(1) + 'M' : '—';
        const filed = p.filing_date ? new Date(p.filing_date).toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'}) : '—';
        return `<tr style="border-bottom:1px solid var(--border-color);">
            <td style="padding:6px 8px;font-weight:800;color:var(--text-bright);font-size:12px;">${p.ticker}</td>
            <td style="padding:6px 8px;font-size:10px;color:var(--text-secondary);">${p.company_name || '—'}</td>
            <td style="padding:6px 8px;font-size:10px;font-weight:700;color:var(--text-bright);">${p.fund_name || '—'}</td>
            <td style="padding:6px 8px;font-size:11px;color:#00c896;font-weight:800;">${val}</td>
            <td style="padding:6px 8px;font-size:10px;font-weight:600;color:var(--text-bright);">${(p.shares || 0).toLocaleString()}</td>
            <td style="padding:6px 8px;font-size:10px;font-weight:700;color:var(--text-bright);">${filed}</td>
        </tr>`;
    }).join('');

    return `<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:12px;padding:16px;margin-bottom:16px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:10px;">New Positions — Fresh Buys by Institutions</div>
        <div style="overflow-x:auto;">
            <table style="width:100%;border-collapse:collapse;font-size:11px;">
                <thead><tr style="border-bottom:2px solid var(--border-color);font-size:9px;text-transform:uppercase;color:var(--text-secondary);">
                    <th style="padding:6px 8px;text-align:left;">Ticker</th>
                    <th style="padding:6px 8px;text-align:left;">Company</th>
                    <th style="padding:6px 8px;text-align:left;">Fund</th>
                    <th style="padding:6px 8px;text-align:left;">Value</th>
                    <th style="padding:6px 8px;text-align:left;">Shares</th>
                    <th style="padding:6px 8px;text-align:left;">Filed</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    </div>`;
}

function _smRenderFundList(d) {
    const entities = d.entities || [];
    if (!entities.length) return '';

    const rows = entities.map(e => {
        const aum = e.aum_billions ? '$' + e.aum_billions + 'B' : '—';
        const updated = e.last_updated ? new Date(e.last_updated).toLocaleDateString('en-US', {month:'short', day:'numeric'}) : '—';
        return `<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border-color);cursor:pointer;" onclick="_smLoadHoldings(${e.id}, '${e.name.replace(/'/g, "\\'")}')">
            <div style="flex:1;">
                <div style="font-weight:700;color:var(--text-bright);font-size:12px;">${e.name}</div>
                <div style="font-size:9px;color:var(--text-secondary);">${e.description || e.entity_type}</div>
            </div>
            <div style="min-width:60px;text-align:right;font-size:11px;font-weight:600;color:var(--text-bright);">${aum}</div>
            <div style="min-width:50px;text-align:right;font-size:9px;color:var(--text-secondary);">${updated}</div>
            <div style="font-size:10px;color:var(--accent-blue);cursor:pointer;">View &#8250;</div>
        </div>`;
    }).join('');

    return `<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:12px;padding:16px;margin-bottom:16px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:10px;">Tracked Funds (${entities.length})</div>
        <div style="max-height:400px;overflow-y:auto;">${rows}</div>
    </div>`;
}

function _smRenderTraders(d) {
    const traders = d.top_traders || [];
    if (!traders.length) return '';

    const rows = traders.map(t => {
        const aum = t.aum ? `$${t.aum}B` : '';
        const holdings = t.holdings_count || 0;
        const clickAttr = t.entity_id ? `onclick="_smLoadHoldings(${t.entity_id}, '${t.entity.replace(/'/g, "\\'")}')" style="cursor:pointer;"` : '';
        return `<div style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--border-color);" ${clickAttr}>
            <div style="flex:1;">
                <div style="font-weight:700;color:var(--text-bright);font-size:12px;">${t.name}</div>
                <div style="font-size:9px;color:var(--text-secondary);">${t.entity} ${aum ? '| ' + aum : ''}</div>
            </div>
            ${holdings > 0 ? `<span style="font-size:9px;color:var(--accent-blue);">${holdings} holdings</span>` : '<span style="font-size:9px;color:var(--text-secondary);">No 13F</span>'}
            <span style="font-size:9px;padding:2px 8px;border-radius:8px;background:var(--bg-secondary);color:var(--text-secondary);border:1px solid var(--border-color);">${t.style}</span>
            ${t.entity_id ? '<span style="font-size:10px;color:var(--accent-blue);">&#8250;</span>' : ''}
        </div>`;
    }).join('');

    return `<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:12px;padding:16px;margin-bottom:16px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:10px;">Top 20 Notable Investors</div>
        <div style="max-height:350px;overflow-y:auto;">${rows}</div>
    </div>`;
}

function _smRenderActivity(d) {
    const activity = d.recent_activity || [];
    if (!activity.length) return '';

    const rows = activity.slice(0, 20).map(a => {
        const actionColor = a.action === 'NEW' ? '#00c896' : a.action === 'INCREASED' ? '#8bc34a' : a.action === 'REDUCED' ? '#ff4757' : '#ffc837';
        const val = a.value_usd ? '$' + (a.value_usd / 1e6).toFixed(1) + 'M' : '—';
        const date = a.filing_date ? new Date(a.filing_date).toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'}) : '—';
        const created = a.created_at ? new Date(a.created_at).toLocaleDateString('en-US', {month:'short', day:'numeric'}) : '';
        return `<tr style="border-bottom:1px solid var(--border-color);">
            <td style="padding:5px 8px;font-size:10px;font-weight:700;color:var(--text-bright);">${date}</td>
            <td style="padding:5px 8px;font-weight:800;color:var(--text-bright);">${a.fund_name || '—'}</td>
            <td style="padding:5px 8px;font-weight:800;color:var(--text-bright);font-size:12px;">${a.ticker}</td>
            <td style="padding:5px 8px;"><span style="font-size:10px;font-weight:800;color:${actionColor};padding:2px 8px;border-radius:6px;background:${actionColor}22;">${a.action}</span></td>
            <td style="padding:5px 8px;font-size:10px;font-weight:600;color:var(--text-bright);">${(a.shares || 0).toLocaleString()}</td>
            <td style="padding:5px 8px;font-size:11px;font-weight:700;color:var(--text-bright);">${val}</td>
        </tr>`;
    }).join('');

    return `<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:12px;padding:16px;margin-bottom:16px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:10px;">Recent Activity</div>
        <div style="overflow-x:auto;">
            <table style="width:100%;border-collapse:collapse;font-size:11px;">
                <thead><tr style="border-bottom:2px solid var(--border-color);font-size:9px;text-transform:uppercase;color:var(--text-secondary);">
                    <th style="padding:5px 8px;text-align:left;">Date</th>
                    <th style="padding:5px 8px;text-align:left;">Fund</th>
                    <th style="padding:5px 8px;text-align:left;">Ticker</th>
                    <th style="padding:5px 8px;text-align:left;">Action</th>
                    <th style="padding:5px 8px;text-align:left;">Shares</th>
                    <th style="padding:5px 8px;text-align:left;">Value</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    </div>`;
}

async function _smLoadHoldings(entityId, name) {
    const container = document.getElementById('smart-money-dashboard');
    if (!container) return;

    container.innerHTML = `<div style="text-align:center;padding:20px;font-size:12px;color:var(--text-secondary);">Loading holdings for ${name}...</div>`;

    try {
        const resp = await fetch(`/api/smart-money/holdings/${entityId}`);
        const data = await resp.json();
        const holdings = data.holdings || [];

        let rows = holdings.slice(0, 50).map((h, i) => {
            const actionColor = h.action === 'NEW' ? '#00c896' : h.action === 'INCREASED' ? '#8bc34a' : h.action === 'REDUCED' ? '#ff4757' : 'var(--text-secondary)';
            const val = h.value_usd ? '$' + (h.value_usd / 1e6).toFixed(1) + 'M' : '—';
            const changePct = h.change_pct ? (h.change_pct > 0 ? '+' : '') + h.change_pct.toFixed(1) + '%' : '—';
            return `<tr style="border-bottom:1px solid var(--border-color);">
                <td style="padding:5px 8px;">${i + 1}</td>
                <td style="padding:5px 8px;font-weight:700;color:var(--text-bright);">${h.ticker}</td>
                <td style="padding:5px 8px;font-size:10px;color:var(--text-secondary);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${h.company_name || ''}</td>
                <td style="padding:5px 8px;font-size:10px;">${val}</td>
                <td style="padding:5px 8px;font-size:10px;">${(h.shares || 0).toLocaleString()}</td>
                <td style="padding:5px 8px;font-size:10px;">${(h.pct_of_portfolio || 0).toFixed(1)}%</td>
                <td style="padding:5px 8px;"><span style="font-size:9px;font-weight:700;color:${actionColor};">${h.action || '—'}</span></td>
                <td style="padding:5px 8px;font-size:10px;color:${h.change_pct > 0 ? '#00c896' : h.change_pct < 0 ? '#ff4757' : 'var(--text-secondary)'};">${changePct}</td>
            </tr>`;
        }).join('');

        container.innerHTML = `
            <div style="margin-bottom:16px;">
                <button onclick="loadSmartMoney()" style="font-size:11px;color:var(--accent-blue);background:none;border:none;cursor:pointer;padding:0;">&larr; Back to Summary</button>
            </div>
            <div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:12px;padding:16px;">
                <div style="font-size:14px;font-weight:800;color:var(--text-bright);margin-bottom:4px;">${name}</div>
                <div style="font-size:10px;color:var(--text-secondary);margin-bottom:12px;">${holdings.length} holdings | Latest filing: ${holdings[0]?.filing_date || '—'}</div>
                <div style="overflow-x:auto;">
                    <table style="width:100%;border-collapse:collapse;font-size:11px;">
                        <thead><tr style="border-bottom:2px solid var(--border-color);font-size:9px;text-transform:uppercase;color:var(--text-secondary);">
                            <th style="padding:5px 8px;">#</th>
                            <th style="padding:5px 8px;text-align:left;">Ticker</th>
                            <th style="padding:5px 8px;text-align:left;">Company</th>
                            <th style="padding:5px 8px;text-align:left;">Value</th>
                            <th style="padding:5px 8px;text-align:left;">Shares</th>
                            <th style="padding:5px 8px;text-align:left;">% Port</th>
                            <th style="padding:5px 8px;text-align:left;">Action</th>
                            <th style="padding:5px 8px;text-align:left;">Change</th>
                        </tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            </div>`;
    } catch (e) {
        container.innerHTML = `<div style="color:#ef5350;padding:20px;">Failed to load holdings: ${e.message}</div>`;
    }
}

async function _smRefreshData() {
    try {
        const resp = await fetch('/api/smart-money/refresh', { method: 'POST' });
        const data = await resp.json();
        alert(data.message || 'Refresh started');
        // Reload after a delay
        setTimeout(() => loadSmartMoney(true), 30000);
    } catch (e) {
        alert('Failed to start refresh: ' + e.message);
    }
}

// ─── Sector Options Flow sub-tab ────────────────────────────
let _sfPollTimer = null;

function switchSmartMoneySub(sub) {
    document.querySelectorAll('.sm-subnav-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.smsub === sub));
    const inst = document.getElementById('smart-money-dashboard');
    const sect = document.getElementById('smart-money-sectorflow');
    const buff = document.getElementById('smart-money-buffett');
    [inst, sect, buff].forEach(el => { if (el) el.style.display = 'none'; });
    if (sub === 'sectorflow') {
        if (sect) sect.style.display = 'block';
        loadSectorFlow();
    } else if (sub === 'buffett') {
        clearTimeout(_sfPollTimer);
        if (buff) buff.style.display = 'block';
        loadBuffettTracker();
    } else {
        clearTimeout(_sfPollTimer);
        if (inst) inst.style.display = 'block';
    }
}

function _sfMoney(v) {
    const n = Math.abs(Number(v) || 0);
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return '$' + (n / 1e3).toFixed(0) + 'K';
    return '$' + n.toFixed(0);
}

async function loadSectorFlow(force) {
    const c = document.getElementById('smart-money-sectorflow');
    if (!c) return;
    if (!c.dataset.loaded) {
        c.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-secondary);">Loading sector options flow…</div>';
    }
    try {
        const resp = await fetch('/api/smart-money/sector-flow' + (force ? '?refresh=1' : ''));
        const d = await resp.json();
        if (d.computing && (!d.sectors || !d.sectors.length)) {
            c.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-secondary);">'
                + 'Computing sector options flow…<div style="margin-top:8px;font-size:11px;">'
                + 'First load reads 11 sector option chains — refreshes automatically.</div></div>';
            clearTimeout(_sfPollTimer);
            _sfPollTimer = setTimeout(() => loadSectorFlow(), 5000);
            return;
        }
        c.dataset.loaded = '1';
        c.innerHTML = renderSectorFlow(d);
    } catch (e) {
        c.innerHTML = '<div style="color:#ef5350;padding:20px;">Failed to load sector flow: ' + e.message + '</div>';
    }
}

function renderSectorFlow(d) {
    if (!d.sectors || !d.sectors.length) {
        return '<div style="padding:40px;text-align:center;color:var(--text-secondary);">No sector options data available right now.</div>';
    }
    const tiltColor = d.tilt === 'RISK-ON' ? '#00c896' : d.tilt === 'RISK-OFF' ? '#ff4757' : '#ffc837';
    // Scale net-premium meters against the largest absolute net across sectors.
    const maxAbs = Math.max(1, ...d.sectors.map(s => Math.abs(s.net_premium || 0)));

    let html = '<div class="sf-wrap">';
    html += '<div class="sf-top">'
        + '<span>Market tilt: <b style="color:' + tiltColor + '">' + d.tilt + '</b></span>'
        + '<span>Net call premium ' + (d.total_net_premium >= 0 ? '+' : '') + _sfMoney(d.total_net_premium) + '</span>'
        + '<span style="color:var(--text-secondary);">' + (d.count || 0) + ' sectors · '
        + (d.stale ? 'updating…' : 'live') + ' · <a href="#" onclick="loadSectorFlow(true);return false;">refresh</a></span>'
        + '</div>';

    // Two groups: money in / being sold
    const grp = (title, list, cls) => {
        let h = '<div class="sf-col"><div class="sf-col-title ' + cls + '">' + title + '</div>';
        if (!list.length) { h += '<div class="sf-empty">None</div></div>'; return h; }
        list.forEach(s => {
            h += '<div class="sf-row"><span class="sf-sector">' + s.sector + ' <span class="sf-etf">' + s.etf + '</span></span>'
                + '<span class="sf-net" style="color:' + s.color + '">' + (s.net_premium >= 0 ? '+' : '') + _sfMoney(s.net_premium) + '</span></div>';
        });
        return h + '</div>';
    };
    html += '<div class="sf-cols">'
        + grp('▲ Money flowing in', d.money_in, 'sf-in')
        + grp('▼ Being sold', d.selling, 'sf-out')
        + '</div>';

    // Full ranked table with a centered net-premium meter per sector
    html += '<table class="sf-table"><thead><tr><th>Sector</th><th>Flow</th><th>Calls$</th><th>Puts$</th><th>Net premium</th><th>P/C</th></tr></thead><tbody>';
    d.sectors.forEach(s => {
        const pct = Math.min(50, Math.round(Math.abs(s.net_premium) / maxAbs * 50));
        const bar = s.net_premium >= 0
            ? '<span class="sf-bar-pos" style="width:' + pct + '%"></span>'
            : '<span class="sf-bar-neg" style="width:' + pct + '%;margin-left:' + (50 - pct) + '%"></span>';
        html += '<tr>'
            + '<td>' + s.sector + ' <span class="sf-etf">' + s.etf + '</span></td>'
            + '<td><span class="sf-badge" style="color:' + s.color + '">' + s.flow_signal + '</span></td>'
            + '<td>' + _sfMoney(s.call_value) + '</td>'
            + '<td>' + _sfMoney(s.put_value) + '</td>'
            + '<td><div class="sf-meter"><span class="sf-mid"></span>' + bar + '</div>'
            + '<span class="sf-net" style="color:' + s.color + '">' + (s.net_premium >= 0 ? '+' : '') + _sfMoney(s.net_premium) + '</span></td>'
            + '<td>' + (s.pc_ratio != null ? Number(s.pc_ratio).toFixed(2) : '—')
            + (s.pc_divergent ? ' <span class="sf-warn" title="Dollar flow and volume put/call disagree — read with caution">⚠</span>' : '') + '</td>'
            + '</tr>';
    });
    html += '</tbody></table>';
    html += '<div class="sf-note">Source: ' + (d.source || 'options chains') + ' · premium-weighted call vs put dollar flow per SPDR sector ETF.</div>';
    html += '</div>';
    return html;
}

// ─── Buffett "copy & paste" tracker ──────────────────────────
let _buffettLoading = false;
let _buffettData = null;

async function loadBuffettTracker(force) {
    if (_buffettLoading) return;
    _buffettLoading = true;
    const box = document.getElementById('smart-money-buffett');
    if (!box) { _buffettLoading = false; return; }
    box.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-secondary);">Loading Buffett portfolio…</div>';
    try {
        const resp = await fetch('/api/smart-money/buffett' + (force ? '?refresh=1' : ''));
        if (resp.status === 403) {
            box.innerHTML = '<div style="text-align:center;padding:40px 0;">' +
                '<div style="font-size:28px;margin-bottom:8px;">&#128274;</div>' +
                '<div style="font-size:14px;font-weight:700;color:var(--text-bright);margin-bottom:6px;">Pro Feature</div>' +
                '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:14px;">Upgrade to Pro for the Buffett Tracker.</div>' +
                '<button onclick="showPricingModal&&showPricingModal()" class="btn-analyze" style="padding:8px 20px;font-size:12px;">Upgrade to Pro</button></div>';
            _buffettLoading = false; return;
        }
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        if (data.error) {
            box.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-secondary);">' + data.error + '</div>';
            _buffettLoading = false; return;
        }
        _buffettData = data;
        box.innerHTML = renderBuffettTracker(data);
        applyBuffettCalc();
    } catch (e) {
        box.innerHTML = '<div style="text-align:center;padding:40px;color:#ff5d73;">Could not load Buffett portfolio (' + e.message + ').</div>';
    }
    _buffettLoading = false;
}

function _bfAction(a) {
    const m = { NEW: 'new', ADD: 'add', TRIM: 'trim', EXIT: 'exit', HELD: 'held' };
    return '<span class="bf-act bf-' + (m[a] || 'held') + '">' + a + '</span>';
}

function renderBuffettTracker(d) {
    const h = d.holdings || [];
    let html = '<div class="bf">';
    html += '<div class="bf-head">' +
        '<div><div class="bf-name">Warren Buffett · Berkshire Hathaway</div>' +
        '<div class="bf-sub">Copy &amp; paste his allocation — ' + h.length + ' holdings · 13F as of <b>' + (d.as_of || '—') + '</b></div></div>' +
        '<div class="bf-actions">' +
        '<button class="bf-copy-btn" onclick="copyBuffettList()">⧉ Copy list</button>' +
        '<button class="bf-copy-btn ghost" onclick="loadBuffettTracker(true)">↻ Refresh</button>' +
        '</div></div>';

    // Mirror calculator
    html += '<div class="bf-calc">' +
        '<label class="bf-calc-label">Mirror his weights with</label>' +
        '<div class="bf-calc-input">$<input id="bf-amount" type="number" min="0" step="100" value="10000" ' +
        'oninput="applyBuffettCalc()" inputmode="numeric"></div>' +
        '<span class="bf-calc-hint">→ shares to buy per name (live prices)</span></div>';

    html += '<div class="bf-table-wrap"><table class="bf-table"><thead><tr>' +
        '<th>#</th><th>Ticker</th><th class="bf-l">Company</th><th>Move</th>' +
        '<th>Weight</th><th>Price</th><th>Allocation</th><th>Shares</th>' +
        '</tr></thead><tbody>';
    h.forEach(r => {
        const px = (r.price != null) ? '$' + Number(r.price).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : '—';
        html += '<tr>' +
            '<td class="bf-rank">' + r.rank + '</td>' +
            '<td class="bf-tk">' + r.ticker + '</td>' +
            '<td class="bf-l bf-co">' + r.company + '</td>' +
            '<td>' + _bfAction(r.action) + '</td>' +
            '<td class="bf-wt">' + r.weight.toFixed(2) + '%</td>' +
            '<td class="mono">' + px + '</td>' +
            '<td class="mono bf-alloc" data-weight="' + r.weight + '">—</td>' +
            '<td class="mono bf-shares" data-weight="' + r.weight + '" data-price="' + (r.price || 0) + '">—</td>' +
            '</tr>';
    });
    html += '</tbody></table></div>';
    html += '<div class="bf-note">ⓘ ' + (d.note || '') + ' Not investment advice.</div>';
    html += '</div>';
    return html;
}

function applyBuffettCalc() {
    const inp = document.getElementById('bf-amount');
    if (!inp) return;
    const amount = Math.max(0, Number(inp.value) || 0);
    document.querySelectorAll('#smart-money-buffett .bf-alloc').forEach(td => {
        const w = Number(td.dataset.weight) || 0;
        td.textContent = '$' + Math.round(amount * w / 100).toLocaleString('en-US');
    });
    document.querySelectorAll('#smart-money-buffett .bf-shares').forEach(td => {
        const w = Number(td.dataset.weight) || 0;
        const px = Number(td.dataset.price) || 0;
        if (px <= 0) { td.textContent = '—'; return; }
        const dollars = amount * w / 100;
        const sh = dollars / px;
        td.textContent = sh >= 10 ? Math.round(sh).toLocaleString('en-US') : sh.toFixed(2);
    });
}

function copyBuffettList() {
    if (!_buffettData) return;
    const amount = Math.max(0, Number((document.getElementById('bf-amount') || {}).value) || 0);
    const lines = ['Warren Buffett / Berkshire Hathaway — 13F as of ' + (_buffettData.as_of || ''),
        'Mirror amount: $' + amount.toLocaleString('en-US'), ''];
    (_buffettData.holdings || []).forEach(r => {
        const dollars = amount * r.weight / 100;
        const sh = (r.price > 0) ? (dollars / r.price) : 0;
        const shTxt = r.price > 0 ? (sh >= 10 ? Math.round(sh) : sh.toFixed(2)) + ' sh' : '(no price)';
        lines.push('#' + r.rank + '  ' + r.ticker + '  ' + r.weight.toFixed(2) + '%  ' +
            r.action + '  $' + Math.round(dollars).toLocaleString('en-US') + '  ' + shTxt);
    });
    const text = lines.join('\n');
    const done = btn => { const b = document.querySelector('.bf-copy-btn'); if (b) { const t = b.textContent; b.textContent = '✓ Copied'; setTimeout(() => b.textContent = t, 1500); } };
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(() => {});
    } else {
        const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta);
        ta.select(); try { document.execCommand('copy'); done(); } catch (e) {} document.body.removeChild(ta);
    }
}
