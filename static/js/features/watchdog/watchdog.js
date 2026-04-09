// ─── Watchdog Trader ─────────────────────────────────────────

let _wdLoading = false;

async function loadWatchdogDashboard() {
    if (_wdLoading) return;
    _wdLoading = true;

    const container = document.getElementById('watchdog-dashboard');
    if (!container) { _wdLoading = false; return; }

    try {
        const [summaryResp, signalsResp, sentimentResp, tradesResp, healthResp, autoResp] = await Promise.allSettled([
            fetch('/api/watchdog/summary'),
            fetch('/api/watchdog/signals'),
            fetch('/api/watchdog/sentiment'),
            fetch('/api/watchdog/paper-trades'),
            fetch('/api/watchdog/health'),
            fetch('/api/watchdog/auto/status'),
        ]);

        // Check subscription gate
        if (summaryResp.status === 'fulfilled' && summaryResp.value.status === 403) {
            container.innerHTML = `<div style="text-align:center;padding:40px 0;">
                <div style="font-size:28px;margin-bottom:8px;">&#128274;</div>
                <div style="font-size:14px;font-weight:700;color:var(--text-bright);margin-bottom:6px;">Pro Feature</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:14px;">Upgrade to Pro for Watchdog Trader</div>
                <button onclick="showPricingModal()" class="btn-analyze" style="padding:8px 20px;font-size:12px;">Upgrade to Pro</button>
            </div>`;
            _wdLoading = false;
            return;
        }

        let html = '';

        // 0. Auto-Trader Control Bar
        if (autoResp.status === 'fulfilled' && autoResp.value.ok) {
            const auto = await autoResp.value.json();
            html += renderWdAutoTraderBar(auto);
        }

        // 1. Regime Bar
        if (summaryResp.status === 'fulfilled' && summaryResp.value.ok) {
            const s = await summaryResp.value.json();
            html += renderWdRegimeBar(s);
        }

        // 2. Sentiment + Risk panels
        html += '<div class="wd-grid-2">';
        if (sentimentResp.status === 'fulfilled' && sentimentResp.value.ok) {
            const sent = await sentimentResp.value.json();
            html += renderWdSentimentPanel(sent);
            html += renderWdRiskPanel(sent, summaryResp.status === 'fulfilled' && summaryResp.value.ok ? null : null);
        }
        html += '</div>';

        // 3. Signal Board
        if (signalsResp.status === 'fulfilled' && signalsResp.value.ok) {
            const sig = await signalsResp.value.json();
            html += renderWdSignalBoard(sig);
        }

        // 4. Paper Trading
        if (tradesResp.status === 'fulfilled' && tradesResp.value.ok) {
            const trades = await tradesResp.value.json();
            html += renderWdPaperTrading(trades);
        }

        // 5. System Health
        if (healthResp.status === 'fulfilled' && healthResp.value.ok) {
            const h = await healthResp.value.json();
            html += renderWdHealth(h);
        }

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="text-align:center;padding:40px;color:#ef5350;">Watchdog failed: ${e.message}</div>`;
    }

    _wdLoading = false;
}


// ─── Auto-Trader Control Bar ────────────────────────────────

function renderWdAutoTraderBar(auto) {
    const running = auto.running;
    const mode = auto.mode || 'paper';
    const killSwitch = auto.kill_switch;
    const cfg = auto.config || {};

    const statusColor = killSwitch ? '#ff4757' : running ? '#00c896' : '#636b7e';
    const statusText = killSwitch ? 'KILL SWITCH' : running ? 'RUNNING' : 'STOPPED';
    const modeColor = mode === 'live' ? '#ff4757' : '#4f8aff';
    const modeLabel = mode === 'live' ? 'LIVE' : 'PAPER';

    return `<div class="wd-panel" style="padding:14px 20px;">
        <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="width:10px;height:10px;border-radius:50%;background:${statusColor};${running ? 'box-shadow:0 0 8px ' + statusColor + ';' : ''}"></span>
                <span style="font-size:13px;font-weight:800;color:${statusColor};letter-spacing:0.5px;">${statusText}</span>
            </div>

            <span style="padding:2px 10px;border-radius:10px;font-size:10px;font-weight:800;background:${modeColor}22;color:${modeColor};border:1px solid ${modeColor}44;">${modeLabel} MODE</span>

            <div style="flex:1;"></div>

            <select id="wd-mode-select" onchange="wdSetMode(this.value)" style="background:var(--bg-secondary);border:1px solid var(--border-color);color:var(--text-bright);padding:4px 8px;border-radius:6px;font-size:11px;">
                <option value="paper" ${mode === 'paper' ? 'selected' : ''}>Paper Trading</option>
                <option value="live" ${mode === 'live' ? 'selected' : ''}>Live Trading</option>
            </select>

            <div style="font-size:10px;color:var(--text-secondary);">
                Scan: ${cfg.scan_interval || 300}s | Max Pos: ${cfg.max_positions || 3} | Loss Limit: $${cfg.daily_loss_limit || 500} | Min Conf: ${cfg.min_confidence || 65}%
            </div>

            ${running
                ? `<button onclick="wdStopAutoTrader()" style="background:#ff4757;color:#fff;border:none;border-radius:6px;padding:6px 16px;font-size:12px;font-weight:700;cursor:pointer;">Stop</button>`
                : `<button onclick="wdStartAutoTrader()" class="btn-analyze" style="padding:6px 16px;font-size:12px;">Start Auto-Trader</button>`
            }

            ${killSwitch
                ? `<button onclick="wdResetKillSwitch()" style="background:#ff8c42;color:#fff;border:none;border-radius:6px;padding:6px 12px;font-size:11px;font-weight:700;cursor:pointer;">Reset Kill Switch</button>`
                : `<button onclick="wdActivateKillSwitch()" style="background:none;border:1px solid #ff4757;color:#ff4757;border-radius:6px;padding:6px 12px;font-size:11px;cursor:pointer;">Kill Switch</button>`
            }
        </div>
    </div>`;
}

async function wdStartAutoTrader() {
    try {
        const resp = await fetch('/api/watchdog/auto/start', { method: 'POST' });
        const data = await resp.json();
        if (data.ok) loadWatchdogDashboard();
        else alert(data.error || 'Failed to start');
    } catch (e) { alert('Error: ' + e.message); }
}

async function wdStopAutoTrader() {
    try {
        const resp = await fetch('/api/watchdog/auto/stop', { method: 'POST' });
        const data = await resp.json();
        if (data.ok) loadWatchdogDashboard();
        else alert(data.error || 'Failed to stop');
    } catch (e) { alert('Error: ' + e.message); }
}

async function wdSetMode(mode) {
    if (mode === 'live' && !confirm('Switch to LIVE trading? Real money will be used. Confirm?')) {
        document.getElementById('wd-mode-select').value = 'paper';
        return;
    }
    try {
        await fetch('/api/watchdog/auto/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wd_mode: mode }),
        });
        loadWatchdogDashboard();
    } catch (e) { alert('Error: ' + e.message); }
}

async function wdActivateKillSwitch() {
    if (!confirm('Activate kill switch? This will stop all trading and close no positions.')) return;
    try {
        await fetch('/api/watchdog/auto/kill', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active: true }),
        });
        loadWatchdogDashboard();
    } catch (e) { alert('Error: ' + e.message); }
}

async function wdResetKillSwitch() {
    try {
        await fetch('/api/watchdog/auto/kill', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active: false }),
        });
        loadWatchdogDashboard();
    } catch (e) { alert('Error: ' + e.message); }
}


// ─── Regime Bar ─────────────────────────────────────────────

function renderWdRegimeBar(s) {
    const axes = s.axes || {};
    const axisBar = (label, value, color) => `
        <div style="flex:1;min-width:120px;">
            <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-secondary);margin-bottom:2px;">
                <span>${label}</span><span style="color:${color};font-weight:700;">${value}</span>
            </div>
            <div class="wd-axis-bar"><div class="wd-axis-fill" style="width:${value}%;background:${color};"></div></div>
        </div>`;

    const mktColor = axes.market >= 60 ? '#00c896' : axes.market >= 40 ? '#ffc837' : '#ff4757';
    const sentColor = axes.sentiment >= 60 ? '#00c896' : axes.sentiment >= 40 ? '#ffc837' : '#ff4757';
    const techColor = axes.technical >= 60 ? '#00c896' : axes.technical >= 40 ? '#ffc837' : '#ff4757';

    const tradeStatus = s.trade_allowed
        ? '<span style="color:#00c896;font-weight:700;">TRADING ALLOWED</span>'
        : `<span style="color:#ff4757;font-weight:700;">TRADING BLOCKED</span> <span style="color:var(--text-secondary);font-size:10px;">${s.trade_reason || ''}</span>`;

    const spy = s.spy || {};
    const spyChange = spy.change_1d || 0;
    const spyColor = spyChange >= 0 ? '#00c896' : '#ff4757';

    return `<div class="wd-panel">
        <div class="wd-regime-bar">
            <div>
                <div class="wd-regime-badge" style="color:${s.color};">${s.regime}</div>
                <div style="font-size:11px;color:var(--text-secondary);">Composite: <span style="color:var(--text-bright);font-weight:700;">${s.composite_score}</span>/100</div>
            </div>
            <div style="flex:1;display:flex;gap:16px;flex-wrap:wrap;">
                ${axisBar('Market Structure', axes.market, mktColor)}
                ${axisBar('Sentiment', axes.sentiment, sentColor)}
                ${axisBar('Technical', axes.technical, techColor)}
            </div>
            <div style="text-align:right;min-width:200px;">
                <div style="font-size:11px;color:var(--text-secondary);">SPY <span style="color:var(--text-bright);font-weight:700;">$${(spy.price || 0).toFixed(2)}</span> <span style="color:${spyColor};font-size:10px;">${spyChange >= 0 ? '+' : ''}${spyChange?.toFixed(1) || '0'}%</span></div>
                <div style="font-size:11px;color:var(--text-secondary);">VIX <span style="color:var(--text-bright);font-weight:700;">${(s.vix || 0).toFixed(1)}</span></div>
                <div style="font-size:11px;margin-top:4px;">${tradeStatus}</div>
            </div>
        </div>
    </div>`;
}


// ─── Sentiment Panel ────────────────────────────────────────

function renderWdSentimentPanel(sent) {
    const t = sent.trump || {};
    const moodColor = t.color || '#ffc837';
    const ts = t.trade_signals || {};
    const buySignals = (ts.buy || []).slice(0, 3);
    const avoidSignals = (ts.avoid || []).slice(0, 3);

    let signalsHtml = '';
    if (buySignals.length) {
        signalsHtml += buySignals.map(s => `<div style="font-size:10px;color:#00c896;margin-bottom:2px;">&#9650; ${s.sector} <span style="color:var(--text-secondary);">(${s.tickers?.slice(0,3).join(', ')})</span></div>`).join('');
    }
    if (avoidSignals.length) {
        signalsHtml += avoidSignals.map(s => `<div style="font-size:10px;color:#ff4757;margin-bottom:2px;">&#9660; ${s.sector} <span style="color:var(--text-secondary);">(${s.tickers?.slice(0,3).join(', ')})</span></div>`).join('');
    }

    const combColors = { BULLISH: '#00c896', CAUTIOUSLY_BULLISH: '#8bc34a', MIXED: '#ffc837', CAUTIOUSLY_BEARISH: '#ff8c42', BEARISH: '#ff4757' };
    const combColor = combColors[sent.combined_sentiment] || '#ffc837';

    return `<div class="wd-panel">
        <div class="wd-panel-title">Sentiment</div>
        <div style="display:flex;gap:16px;align-items:center;margin-bottom:12px;">
            <div style="text-align:center;">
                <div style="font-size:32px;font-weight:900;color:${moodColor};">${t.mood > 0 ? '+' : ''}${t.mood || 0}</div>
                <div style="font-size:10px;color:${moodColor};font-weight:700;">${t.label || 'N/A'}</div>
            </div>
            <div style="flex:1;">
                <div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">Combined: <span style="font-weight:700;color:${combColor};">${(sent.combined_sentiment || 'MIXED').replace('_', ' ')}</span></div>
                <div style="font-size:11px;color:var(--text-secondary);">Market Health: <span style="font-weight:700;color:${sent.market_health?.status === 'HEALTHY' ? '#00c896' : sent.market_health?.status === 'CAUTION' ? '#ffc837' : '#ff4757'};">${sent.market_health?.status || '?'}</span></div>
                <div style="font-size:10px;color:var(--text-secondary);margin-top:4px;">Posts: ${t.posts_analyzed || 0} | Trend: ${t.pattern?.trend || '?'}</div>
            </div>
        </div>
        ${signalsHtml ? `<div style="border-top:1px solid var(--border-color);padding-top:8px;">${signalsHtml}</div>` : ''}
    </div>`;
}


// ─── Risk Panel ─────────────────────────────────────────────

function renderWdRiskPanel(sent) {
    const mh = sent.market_health || {};
    const vixWarnings = [];

    // Parse VIX from regime data if available
    const warnings = [];
    if (mh.status === 'PANIC') warnings.push({ level: 'CRITICAL', msg: 'Market in PANIC — all positions should be reviewed' });
    if (mh.status === 'DANGER') warnings.push({ level: 'HIGH', msg: 'Market under stress — new entries not recommended' });
    if (mh.status === 'CAUTION') warnings.push({ level: 'MEDIUM', msg: 'Elevated risk — reduce position sizes' });

    const trump = sent.trump || {};
    if (trump.mood && trump.mood < -30) warnings.push({ level: 'HIGH', msg: `Bearish political sentiment (mood: ${trump.mood})` });
    if (trump.pattern?.trend === 'deteriorating') warnings.push({ level: 'MEDIUM', msg: 'Sentiment deteriorating — watch for further downside' });

    if (!warnings.length) warnings.push({ level: 'LOW', msg: 'No active risk events — conditions normal' });

    const levelColors = { CRITICAL: '#ff4757', HIGH: '#ff4757', MEDIUM: '#ff8c42', LOW: '#00c896' };

    const warningsHtml = warnings.map(w => `
        <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-color);">
            <span style="width:8px;height:8px;border-radius:50%;background:${levelColors[w.level]};flex-shrink:0;"></span>
            <span style="font-size:10px;font-weight:700;color:${levelColors[w.level]};min-width:55px;">${w.level}</span>
            <span style="font-size:11px;color:var(--text-secondary);">${w.msg}</span>
        </div>
    `).join('');

    return `<div class="wd-panel">
        <div class="wd-panel-title">Risk Monitor</div>
        ${warningsHtml}
    </div>`;
}


// ─── Signal Board ───────────────────────────────────────────

function renderWdSignalBoard(data) {
    const signals = data.signals || [];
    if (!signals.length) return '<div class="wd-panel"><div class="wd-panel-title">Signal Board</div><div style="color:var(--text-secondary);font-size:12px;">No signals — loading...</div></div>';

    const rows = signals.map(s => {
        const cls = `wd-signal-${(s.signal || 'watch').toLowerCase()}`;
        const confColor = s.confidence >= 65 ? '#00c896' : s.confidence >= 40 ? '#ffc837' : '#ff4757';
        const ind = s.indicators || {};
        return `<tr>
            <td style="font-weight:700;color:var(--text-bright);">${s.ticker}</td>
            <td style="color:var(--text-bright);">$${(ind.price || 0).toFixed(2)}</td>
            <td><span class="wd-signal-badge ${cls}">${s.signal}</span></td>
            <td style="text-align:center;">
                <span style="color:${confColor};font-weight:700;">${s.confidence}%</span>
            </td>
            <td style="color:var(--text-secondary);font-size:12px;">${s.strategy || '—'}</td>
            <td style="color:var(--text-secondary);font-size:11px;">RSI ${ind.rsi || '—'}</td>
            <td style="color:var(--text-secondary);font-size:11px;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${(s.reasoning || '').replace(/"/g, '&quot;')}">${s.reasoning || '—'}</td>
            <td style="color:var(--text-secondary);font-size:11px;">${s.stop_loss ? '$' + s.stop_loss : '—'}</td>
            <td style="color:var(--text-secondary);font-size:11px;">${s.take_profit ? '$' + s.take_profit : '—'}</td>
            ${s.signal === 'BUY' ? `<td><button onclick="wdQuickTrade('${s.ticker}','${s.strategy}',${s.stop_loss || 0},${s.take_profit || 0})" class="btn-analyze" style="padding:3px 10px;font-size:10px;">Paper Trade</button></td>` : '<td></td>'}
        </tr>`;
    }).join('');

    return `<div class="wd-panel">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div class="wd-panel-title" style="margin-bottom:0;">Signal Board</div>
            <div style="font-size:10px;color:var(--text-secondary);">Regime: <span style="font-weight:700;color:var(--text-bright);">${data.regime || '?'}</span> | ${data.cached ? 'cached' : 'live'}</div>
        </div>
        <div style="overflow-x:auto;">
            <table class="wd-table">
                <thead><tr>
                    <th>Ticker</th><th>Price</th><th>Signal</th><th style="text-align:center;">Conf</th>
                    <th>Strategy</th><th>RSI</th><th>Reasoning</th><th>SL</th><th>TP</th><th></th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    </div>`;
}


// ─── Paper Trading ──────────────────────────────────────────

function renderWdPaperTrading(data) {
    const trades = data.trades || [];
    const summary = data.summary || {};
    const openTrades = trades.filter(t => t.status === 'open');
    const closedTrades = trades.filter(t => t.status === 'closed').slice(0, 20);

    // Summary stats
    const unrealizedColor = summary.total_unrealized_pnl >= 0 ? '#00c896' : '#ff4757';
    const realizedColor = summary.total_realized_pnl >= 0 ? '#00c896' : '#ff4757';

    let html = `<div class="wd-panel">
        <div class="wd-panel-title">Paper Trading</div>

        <div style="display:flex;gap:16px;margin-bottom:14px;flex-wrap:wrap;">
            <div style="text-align:center;padding:8px 16px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:18px;font-weight:800;color:var(--accent-blue);">${summary.open_count || 0}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Open</div>
            </div>
            <div style="text-align:center;padding:8px 16px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:18px;font-weight:800;color:${unrealizedColor};">$${(summary.total_unrealized_pnl || 0).toFixed(2)}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Unrealized</div>
            </div>
            <div style="text-align:center;padding:8px 16px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:18px;font-weight:800;color:${realizedColor};">$${(summary.total_realized_pnl || 0).toFixed(2)}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Realized</div>
            </div>
            <div style="text-align:center;padding:8px 16px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:18px;font-weight:800;color:var(--text-bright);">${summary.win_rate || 0}%</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Win Rate</div>
            </div>
        </div>`;

    // Trade entry form
    html += `<div class="wd-trade-form" id="wd-trade-form">
        <div><label>Ticker</label><input type="text" id="wd-trade-ticker" placeholder="SPY" style="width:70px;"></div>
        <div><label>Side</label><select id="wd-trade-side"><option value="long">Long</option><option value="short">Short</option></select></div>
        <div><label>Shares</label><input type="number" id="wd-trade-shares" value="10" min="1" style="width:70px;"></div>
        <div><label>Stop Loss</label><input type="number" id="wd-trade-sl" step="0.01" placeholder="optional" style="width:90px;"></div>
        <div><label>Take Profit</label><input type="number" id="wd-trade-tp" step="0.01" placeholder="optional" style="width:90px;"></div>
        <div><label>&nbsp;</label><button onclick="wdOpenTrade()" class="btn-analyze" style="padding:6px 16px;font-size:12px;">Open Trade</button></div>
    </div>`;

    // Open positions
    if (openTrades.length) {
        const openRows = openTrades.map(t => {
            const pnlColor = (t.unrealized_pnl || 0) >= 0 ? '#00c896' : '#ff4757';
            return `<tr>
                <td style="font-weight:700;color:var(--text-bright);">${t.ticker}</td>
                <td style="font-size:12px;color:var(--text-secondary);">${t.side?.toUpperCase()}</td>
                <td>${t.shares}</td>
                <td>$${(t.entry_price || 0).toFixed(2)}</td>
                <td style="color:var(--text-bright);">$${(t.current_price || 0).toFixed(2)}</td>
                <td style="color:${pnlColor};font-weight:700;">$${(t.unrealized_pnl || 0).toFixed(2)} (${(t.unrealized_pct || 0).toFixed(1)}%)</td>
                <td style="font-size:11px;color:var(--text-secondary);">${t.stop_loss ? '$' + t.stop_loss : '—'} / ${t.take_profit ? '$' + t.take_profit : '—'}</td>
                <td><button onclick="wdCloseTrade(${t.id})" style="background:#ff4757;color:#fff;border:none;border-radius:4px;padding:3px 10px;font-size:10px;cursor:pointer;">Close</button></td>
            </tr>`;
        }).join('');

        html += `<div style="margin-bottom:14px;">
            <div style="font-size:11px;font-weight:700;color:var(--text-secondary);text-transform:uppercase;margin-bottom:6px;">Open Positions</div>
            <div style="overflow-x:auto;"><table class="wd-table">
                <thead><tr><th>Ticker</th><th>Side</th><th>Shares</th><th>Entry</th><th>Current</th><th>P&L</th><th>SL/TP</th><th></th></tr></thead>
                <tbody>${openRows}</tbody>
            </table></div>
        </div>`;
    }

    // Closed history
    if (closedTrades.length) {
        const closedRows = closedTrades.map(t => {
            const pnlColor = (t.pnl || 0) >= 0 ? '#00c896' : '#ff4757';
            return `<tr>
                <td style="font-weight:700;color:var(--text-bright);">${t.ticker}</td>
                <td style="font-size:12px;color:var(--text-secondary);">${t.side?.toUpperCase()}</td>
                <td>$${(t.entry_price || 0).toFixed(2)}</td>
                <td>$${(t.exit_price || 0).toFixed(2)}</td>
                <td style="color:${pnlColor};font-weight:700;">$${(t.pnl || 0).toFixed(2)} (${(t.pnl_pct || 0).toFixed(1)}%)</td>
                <td style="font-size:11px;color:var(--text-secondary);">${t.strategy || '—'}</td>
                <td style="font-size:11px;color:var(--text-secondary);">${t.closed_at ? new Date(t.closed_at).toLocaleDateString() : '—'}</td>
            </tr>`;
        }).join('');

        html += `<div>
            <div style="font-size:11px;font-weight:700;color:var(--text-secondary);text-transform:uppercase;margin-bottom:6px;">Trade History</div>
            <div style="overflow-x:auto;"><table class="wd-table">
                <thead><tr><th>Ticker</th><th>Side</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Strategy</th><th>Closed</th></tr></thead>
                <tbody>${closedRows}</tbody>
            </table></div>
        </div>`;
    }

    html += '</div>';
    return html;
}


// ─── System Health ──────────────────────────────────────────

function renderWdHealth(h) {
    const freshness = h.data_freshness || {};
    const dots = Object.values(freshness).map(f => {
        const dotColor = f.fresh ? '#00c896' : f.age_seconds !== null ? '#ff8c42' : '#ff4757';
        const age = f.age_seconds !== null ? (f.age_seconds < 60 ? `${f.age_seconds}s ago` : `${Math.round(f.age_seconds / 60)}m ago`) : 'no data';
        return `<span style="display:inline-flex;align-items:center;gap:4px;margin-right:14px;font-size:11px;color:var(--text-secondary);">
            <span class="wd-health-dot" style="background:${dotColor};"></span>
            ${f.label}: <span style="color:var(--text-bright);">${age}</span>
        </span>`;
    }).join('');

    return `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 16px;background:var(--bg-tertiary);border-radius:8px;border:1px solid var(--border-color);">
        <div>${dots}</div>
        <div style="font-size:10px;color:var(--text-secondary);">Watchlist: ${(h.watchlist || []).join(', ')}</div>
    </div>`;
}


// ─── Trade Actions ──────────────────────────────────────────

async function wdOpenTrade() {
    const ticker = document.getElementById('wd-trade-ticker')?.value?.trim().toUpperCase();
    const side = document.getElementById('wd-trade-side')?.value || 'long';
    const shares = parseFloat(document.getElementById('wd-trade-shares')?.value) || 0;
    const sl = parseFloat(document.getElementById('wd-trade-sl')?.value) || null;
    const tp = parseFloat(document.getElementById('wd-trade-tp')?.value) || null;

    if (!ticker || shares <= 0) { alert('Enter ticker and shares'); return; }

    try {
        const resp = await fetch('/api/watchdog/paper-trades', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'open', ticker, side, shares, stop_loss: sl, take_profit: tp }),
        });
        const data = await resp.json();
        if (data.ok) {
            document.getElementById('wd-trade-ticker').value = '';
            loadWatchdogDashboard();
        } else {
            alert(data.error || 'Failed to open trade');
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function wdCloseTrade(tradeId) {
    const notes = prompt('Close notes (optional):') || '';
    try {
        const resp = await fetch('/api/watchdog/paper-trades', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'close', trade_id: tradeId, notes }),
        });
        const data = await resp.json();
        if (data.ok) {
            loadWatchdogDashboard();
        } else {
            alert(data.error || 'Failed to close trade');
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

function wdQuickTrade(ticker, strategy, sl, tp) {
    const tickerInput = document.getElementById('wd-trade-ticker');
    const slInput = document.getElementById('wd-trade-sl');
    const tpInput = document.getElementById('wd-trade-tp');
    if (tickerInput) tickerInput.value = ticker;
    if (slInput && sl) slInput.value = sl;
    if (tpInput && tp) tpInput.value = tp;
    // Scroll to form
    document.getElementById('wd-trade-form')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
