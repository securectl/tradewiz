// ─── Bot Trades panel (per-user, source-filterable) ────────

let _trackerBotSource = 'all';  // 'all' | 'crypto' | 'stock' | 'claude' | 'watchdog'

const _BOT_SOURCE_LABELS = {
    all: 'All',
    crypto: 'Crypto Bot',
    stock: 'Stock Bot',
    claude: 'Claude Bot',
    watchdog: 'Watchdog',
};
const _BOT_SOURCE_COLORS = {
    crypto: '#ff7043',
    stock: '#42a5f5',
    claude: '#ab47bc',
    watchdog: '#26a69a',
};

async function loadTrackerBotTrades() {
    const tableEl = document.getElementById('tracker-bot-table');
    const pillsEl = document.getElementById('tracker-bot-pills');
    const overallEl = document.getElementById('tracker-bot-overall');
    if (!tableEl || !pillsEl || !overallEl) return;
    overallEl.textContent = 'Loading…';
    tableEl.innerHTML = '';
    try {
        const resp = await fetch(`/api/tracker/bot-trades?source=${_trackerBotSource}&limit=200`);
        if (!resp.ok) {
            tableEl.innerHTML = `<div style="padding:18px;color:#ef5350;font-size:12px;">Failed to load bot trades (HTTP ${resp.status})</div>`;
            overallEl.textContent = '';
            return;
        }
        const data = await resp.json();
        const overall = data.overall || {};
        const sourceCount = data.by_source || {};

        // Overall P/L line
        const pnlColor = (overall.pnl || 0) >= 0 ? '#26a69a' : '#ef5350';
        overallEl.innerHTML = `
            Overall <strong style="color:${pnlColor};">${overall.pnl >= 0 ? '+' : ''}$${(overall.pnl || 0).toFixed(2)}</strong>
            · ${overall.trades || 0} trades · ${overall.closed || 0} closed
            · win rate <strong>${(overall.win_rate || 0).toFixed(1)}%</strong>`;

        // Filter pills (with counts)
        const pillKeys = ['all', 'crypto', 'stock', 'claude', 'watchdog'];
        pillsEl.innerHTML = pillKeys.map(k => {
            const active = _trackerBotSource === k;
            const count = k === 'all'
                ? overall.trades || 0
                : (sourceCount[k] && sourceCount[k].trades) || 0;
            const pnl = k === 'all'
                ? overall.pnl || 0
                : (sourceCount[k] && sourceCount[k].total_pnl) || 0;
            const color = _BOT_SOURCE_COLORS[k] || '#787b86';
            const bg = active ? `${color}33` : 'var(--bg-card, #1a1d26)';
            const border = active ? color : 'var(--border-color)';
            const label = _BOT_SOURCE_LABELS[k] || k;
            const pnlBadge = count > 0
                ? `<span style="color:${pnl >= 0 ? '#26a69a' : '#ef5350'};font-size:9px;">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(0)}</span>`
                : '';
            return `<button onclick="setTrackerBotSource('${k}')"
                style="padding:5px 11px;border:1px solid ${border};background:${bg};color:var(--text-bright);border-radius:14px;font-size:11px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px;">
                <span>${label}</span>
                <span style="color:var(--text-secondary);font-weight:500;">${count}</span>
                ${pnlBadge}
            </button>`;
        }).join('');

        // Trade list
        const trades = data.trades || [];
        if (!trades.length) {
            tableEl.innerHTML = `<div style="padding:24px;color:var(--text-secondary);text-align:center;font-size:12px;">No trades for this filter.</div>`;
            return;
        }
        const rows = trades.map(t => {
            const c = _BOT_SOURCE_COLORS[t.source] || '#787b86';
            const sourceLabel = _BOT_SOURCE_LABELS[t.source] || t.source;
            const pnl = t.pnl != null ? Number(t.pnl) : null;
            const pnlPct = t.pnl_pct != null ? Number(t.pnl_pct) : null;
            const pnlColor = pnl == null ? 'var(--text-secondary)' : pnl >= 0 ? '#26a69a' : '#ef5350';
            const pnlText = pnl == null
                ? '—'
                : `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}${pnlPct != null ? ` (${pnlPct.toFixed(2)}%)` : ''}`;
            return `<tr style="border-bottom:1px solid var(--border-color);">
                <td style="padding:5px 8px;"><span style="background:${c}26;color:${c};padding:2px 7px;border-radius:8px;font-size:10px;font-weight:700;">${sourceLabel}</span></td>
                <td style="padding:5px 8px;font-weight:700;">${t.coin || '—'}</td>
                <td style="padding:5px 8px;font-size:11px;">${t.side || ''}</td>
                <td style="padding:5px 8px;font-family:monospace;text-align:right;">${Number(t.size || 0).toFixed(0)}</td>
                <td style="padding:5px 8px;font-family:monospace;text-align:right;">$${Number(t.entry_price || 0).toFixed(2)}</td>
                <td style="padding:5px 8px;font-family:monospace;text-align:right;">${t.exit_price ? '$' + Number(t.exit_price).toFixed(2) : '—'}</td>
                <td style="padding:5px 8px;font-family:monospace;text-align:right;color:${pnlColor};font-weight:600;">${pnlText}</td>
                <td style="padding:5px 8px;font-size:10px;color:var(--text-secondary);">${t.status || ''}</td>
                <td style="padding:5px 8px;font-size:10px;color:var(--text-bright);">${(t.opened_at || '').slice(0, 16)}</td>
            </tr>`;
        }).join('');
        tableEl.innerHTML = `
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
                <thead><tr style="border-bottom:1px solid var(--border-color);color:var(--text-secondary);font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">
                    <th style="padding:5px 8px;text-align:left;">Source</th>
                    <th style="padding:5px 8px;text-align:left;">Ticker</th>
                    <th style="padding:5px 8px;text-align:left;">Side</th>
                    <th style="padding:5px 8px;text-align:right;">Qty</th>
                    <th style="padding:5px 8px;text-align:right;">Entry</th>
                    <th style="padding:5px 8px;text-align:right;">Exit</th>
                    <th style="padding:5px 8px;text-align:right;">P&amp;L</th>
                    <th style="padding:5px 8px;text-align:left;">Status</th>
                    <th style="padding:5px 8px;text-align:left;">Opened</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
    } catch (e) {
        tableEl.innerHTML = `<div style="padding:18px;color:#ef5350;font-size:12px;">Error: ${e.message}</div>`;
        overallEl.textContent = '';
    }
}

function setTrackerBotSource(src) {
    _trackerBotSource = src;
    loadTrackerBotTrades();
}


function toggleTradeFields() {
    const fields = document.getElementById('journal-trade-fields');
    const btn = document.getElementById('btn-toggle-trade');
    const isHidden = fields.style.display === 'none';
    fields.style.display = isHidden ? 'flex' : 'none';
    btn.classList.toggle('active', isHidden);
}

async function loadJournal() {
    try {
        const resp = await fetch('/api/journal');
        const entries = await resp.json();
        renderJournalEntries(entries);
    } catch (err) {
        console.error('Failed to load journal', err);
    }
}

async function addJournalEntry() {
    const ticker = document.getElementById('journal-ticker').value.trim();
    if (!ticker) return;

    const notes = document.getElementById('journal-notes').value.trim();
    const tradeFields = document.getElementById('journal-trade-fields');
    const isTrade = tradeFields.style.display !== 'none';

    const body = { ticker, notes };

    if (isTrade) {
        body.action = document.getElementById('journal-action').value;
        const ep = document.getElementById('journal-entry-price').value;
        const xp = document.getElementById('journal-exit-price').value;
        const sh = document.getElementById('journal-shares').value;
        if (ep) body.entry_price = parseFloat(ep);
        if (xp) body.exit_price = parseFloat(xp);
        if (sh) body.shares = parseFloat(sh);
    }

    try {
        await fetch('/api/journal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        // Reset form
        document.getElementById('journal-ticker').value = '';
        document.getElementById('journal-notes').value = '';
        document.getElementById('journal-entry-price').value = '';
        document.getElementById('journal-exit-price').value = '';
        document.getElementById('journal-shares').value = '';
        document.getElementById('journal-trade-fields').style.display = 'none';
        document.getElementById('btn-toggle-trade').classList.remove('active');

        loadJournal();
        loadGoals();
    } catch (err) {
        console.error('Failed to add journal entry', err);
    }
}

async function deleteJournalEntry(id) {
    try {
        await fetch(`/api/journal/${id}`, { method: 'DELETE' });
        loadJournal();
        loadGoals();
    } catch (err) {
        console.error('Failed to delete journal entry', err);
    }
}

function renderJournalEntries(entries) {
    const list = document.getElementById('journal-list');
    if (!entries || entries.length === 0) {
        list.innerHTML = '<div style="padding:40px; color:#787b86; text-align:center;">No journal entries yet. Add your first note above.</div>';
        return;
    }

    list.innerHTML = entries.map(e => {
        const raw = e.created_at || '';
        const date = raw.includes('Z') || raw.includes('+') ? new Date(raw) : new Date(raw + 'Z');
        const timeStr = isNaN(date.getTime()) ? raw : date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        const isTrade = !!e.action;
        const actionClass = e.action === 'BUY' ? 'buy' : e.action === 'SELL' ? 'sell' : '';

        let tradeHtml = '';
        if (isTrade) {
            tradeHtml = `
                <div class="journal-trade-info">
                    <span class="journal-action-tag ${actionClass}">${e.action}</span>
                    ${e.entry_price != null ? `<span class="journal-price">Entry: $${e.entry_price}</span>` : ''}
                    ${e.exit_price != null ? `<span class="journal-price">Exit: $${e.exit_price}</span>` : ''}
                    ${e.shares != null ? `<span class="journal-price">${e.shares} shares</span>` : ''}
                    ${e.pnl != null ? `<span class="journal-pnl ${e.pnl >= 0 ? 'positive' : 'negative'}">${e.pnl >= 0 ? '+' : ''}$${e.pnl.toFixed(2)}</span>` : ''}
                </div>`;
        }

        return `
            <div class="journal-card">
                <div class="journal-card-header">
                    <div class="journal-card-left">
                        <span class="journal-ticker-badge">${e.ticker}</span>
                        <span class="journal-time">${timeStr}</span>
                    </div>
                    <button class="journal-delete-btn" onclick="deleteJournalEntry(${e.id})">&times;</button>
                </div>
                ${e.notes ? `<div class="journal-notes-text">${e.notes}</div>` : ''}
                ${tradeHtml}
            </div>`;
    }).join('');
}

async function loadGoals() {
    try {
        const resp = await fetch('/api/goals');
        const data = await resp.json();
        renderGoalDashboard(data);
    } catch (err) {
        console.error('Failed to load goals', err);
    }
}

async function updateWeeklyTarget() {
    const input = document.getElementById('goal-weekly-target');
    const val = parseFloat(input.value);
    if (isNaN(val) || val <= 0) return;
    try {
        await fetch('/api/goals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ weekly_target: val }),
        });
        loadGoals();
    } catch (err) {
        console.error('Failed to update weekly target', err);
    }
}

async function updateBalance() {
    const input = document.getElementById('goal-current-balance');
    const val = parseFloat(input.value);
    if (isNaN(val) || val < 0) return;
    try {
        await fetch('/api/goals/balance', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ balance: val }),
        });
        loadGoals();
    } catch (err) {
        console.error('Failed to update balance', err);
    }
}

function renderGoalDashboard(data) {
    const container = document.getElementById('goal-dashboard');

    const weeklyPct = Math.min(100, Math.max(0, data.weekly_pct));
    const monthlyPct = Math.min(100, Math.max(0, data.monthly_pct));
    const weeklyBarColor = weeklyPct >= 100 ? '#26a69a' : weeklyPct >= 50 ? '#ff9800' : '#ef5350';
    const monthlyBarColor = monthlyPct >= 100 ? '#26a69a' : monthlyPct >= 50 ? '#ff9800' : '#ef5350';

    let milestonesHtml = data.milestones.map(m => {
        return `
            <div class="milestone-node ${m.reached ? 'reached' : ''}">
                <div class="milestone-dot"></div>
                <div class="milestone-label">${m.label}</div>
            </div>`;
    }).join('');

    container.innerHTML = `
        <!-- Account Balance -->
        <div class="goal-section">
            <div class="goal-section-title">Account Balance</div>
            <div class="goal-balance-row">
                <input type="number" id="goal-current-balance" class="goal-balance-input" value="${data.current_balance}" step="0.01">
                <button class="goal-update-btn" onclick="updateBalance()">Update</button>
            </div>
            <div class="goal-sub-text">Starting: $${data.starting_balance.toLocaleString()}</div>
        </div>

        <!-- Weekly Goal -->
        <div class="goal-section">
            <div class="goal-section-title">Weekly Goal</div>
            <div class="goal-progress-header">
                <span>$${data.weekly_actual.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
                <span>of $${data.weekly_target.toLocaleString()}</span>
            </div>
            <div class="goal-progress-bar">
                <div class="goal-progress-fill" style="width:${weeklyPct}%; background:${weeklyBarColor};"></div>
            </div>
            <div class="goal-sub-text">${data.weekly_pct.toFixed(1)}% complete &middot; Week of ${data.week_start}</div>
            <div class="goal-balance-row" style="margin-top:8px;">
                <input type="number" id="goal-weekly-target" class="goal-balance-input" value="${data.weekly_target}" step="1">
                <button class="goal-update-btn" onclick="updateWeeklyTarget()">Set Target</button>
            </div>
        </div>

        <!-- Monthly Rollup -->
        <div class="goal-section">
            <div class="goal-section-title">Monthly Rollup</div>
            <div class="goal-progress-header">
                <span>$${data.monthly_actual.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
                <span>of $${data.monthly_target.toLocaleString()}</span>
            </div>
            <div class="goal-progress-bar">
                <div class="goal-progress-fill" style="width:${monthlyPct}%; background:${monthlyBarColor};"></div>
            </div>
            <div class="goal-sub-text">${data.monthly_pct.toFixed(1)}% complete</div>
        </div>

        <!-- Milestone Tracker -->
        <div class="goal-section">
            <div class="goal-section-title">Journey: $25K &rarr; $1.5M</div>
            <div class="goal-progress-bar" style="margin-bottom:12px;">
                <div class="goal-progress-fill" style="width:${data.progress_pct}%; background:var(--accent-blue);"></div>
            </div>
            <div class="goal-sub-text" style="margin-bottom:10px;">${data.progress_pct.toFixed(2)}% to goal</div>
            <div class="milestone-track">
                ${milestonesHtml}
            </div>
        </div>
    `;
}
