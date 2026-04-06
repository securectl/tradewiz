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
