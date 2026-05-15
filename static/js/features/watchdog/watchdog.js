// ─── ThunderBot (formerly Watchdog / TraderBot) ────────────────────────────

let _wdLoading = false;
// User pref: options-flow section is hidden by default (May 2026 — was
// taking up too much real estate above the trading content).
let _wdOptionsFlowVisible = false;

async function loadWatchdogDashboard() {
    if (_wdLoading) return;
    _wdLoading = true;

    const container = document.getElementById('watchdog-dashboard');
    if (!container) { _wdLoading = false; return; }

    try {
        const [summaryResp, signalsResp, sentimentResp, tradesResp, healthResp, autoResp, balanceResp, oppsResp, flowResp] = await Promise.allSettled([
            fetch('/api/watchdog/summary'),
            fetch('/api/watchdog/candidates'),
            fetch('/api/watchdog/sentiment'),
            fetch('/api/watchdog/paper-trades'),
            fetch('/api/watchdog/health'),
            fetch('/api/watchdog/auto/status'),
            fetch('/api/watchdog/balance'),
            fetch('/api/watchdog/opportunities'),
            fetch('/api/watchdog/options-flow'),
        ]);

        // Check subscription gate
        if (summaryResp.status === 'fulfilled' && summaryResp.value.status === 403) {
            container.innerHTML = `<div style="text-align:center;padding:40px 0;">
                <div style="font-size:28px;margin-bottom:8px;">&#128274;</div>
                <div style="font-size:14px;font-weight:700;color:var(--text-bright);margin-bottom:6px;">Pro Feature</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:14px;">Upgrade to Pro for ThunderBot</div>
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

        // 1. Balance & P/L Bar
        if (balanceResp.status === 'fulfilled' && balanceResp.value.ok) {
            const bal = await balanceResp.value.json();
            html += renderWdBalanceBar(bal);
        }

        // 2. Regime Bar
        if (summaryResp.status === 'fulfilled' && summaryResp.value.ok) {
            const s = await summaryResp.value.json();
            html += renderWdRegimeBar(s);
        }

        // 2.5 Options Flow Tracker — collapsible (default hidden).
        // Always renders the toggle button; the panel itself is mounted
        // inside #wd-options-flow-panel and shown/hidden by wdToggleOptionsFlow().
        let _flowDataPayload = null;
        if (flowResp.status === 'fulfilled' && flowResp.value.ok) {
            _flowDataPayload = await flowResp.value.json();
        }
        html += `<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
            <button id="wd-options-flow-toggle" onclick="wdToggleOptionsFlow()" class="btn-analyze"
                    style="padding:6px 14px;font-size:11px;display:flex;align-items:center;gap:6px;">
                <span id="wd-options-flow-icon">▸</span>
                <span>Options Flow</span>
                <span style="font-size:9px;opacity:0.7;">(${_flowDataPayload && _flowDataPayload.scanner_running ? 'LIVE' : 'paused'})</span>
            </button>
            <div style="font-size:10px;color:var(--text-secondary);">Real-time calls vs puts P/C ratio tracker — click to expand</div>
        </div>
        <div id="wd-options-flow-panel" style="display:${_wdOptionsFlowVisible ? 'block' : 'none'};">
            ${_flowDataPayload ? renderWdOptionsFlow(_flowDataPayload) : ''}
        </div>`;

        // 3. Intraday Opportunities
        if (oppsResp.status === 'fulfilled' && oppsResp.value.ok) {
            const opps = await oppsResp.value.json();
            html += renderWdOpportunities(opps);
        }

        // 4. Sentiment + Risk panels
        html += '<div class="wd-grid-2">';
        if (sentimentResp.status === 'fulfilled' && sentimentResp.value.ok) {
            const sent = await sentimentResp.value.json();
            html += renderWdSentimentPanel(sent);
            html += renderWdRiskPanel(sent, summaryResp.status === 'fulfilled' && summaryResp.value.ok ? null : null);
        }
        html += '</div>';

        // 5. Signal Board
        if (signalsResp.status === 'fulfilled' && signalsResp.value.ok) {
            const sig = await signalsResp.value.json();
            html += renderWdSignalBoard(sig);
        }

        // 6. Watchlist Manager
        html += renderWdWatchlistManager();

        // 7. Paper Trading
        if (tradesResp.status === 'fulfilled' && tradesResp.value.ok) {
            const trades = await tradesResp.value.json();
            html += renderWdPaperTrading(trades);
        }

        // 8. System Health
        if (healthResp.status === 'fulfilled' && healthResp.value.ok) {
            const h = await healthResp.value.json();
            html += renderWdHealth(h);
        }

        container.innerHTML = html;

        // Load watchlist after render
        _wdLoadWatchlist();

        // Render initial options flow chart and start SSE stream
        _wdRenderFlowChart();
        _wdStartOptionsFlowStream();
    } catch (e) {
        container.innerHTML = `<div style="text-align:center;padding:40px;color:#ef5350;">ThunderBot failed: ${e.message}</div>`;
    }

    _wdLoading = false;
}


// ─── Balance & P/L Bar ─────────────────────────────────────

function renderWdBalanceBar(bal) {
    const eqColor = bal.total_equity >= 100000 ? '#00c896' : '#ff4757';
    const todayColor = bal.today_pnl >= 0 ? '#00c896' : '#ff4757';
    const weekColor = bal.week_pnl >= 0 ? '#00c896' : '#ff4757';
    const monthColor = bal.month_pnl >= 0 ? '#00c896' : '#ff4757';
    const totalColor = bal.total_realized_pnl >= 0 ? '#00c896' : '#ff4757';
    const unrealizedColor = bal.total_unrealized_pnl >= 0 ? '#00c896' : '#ff4757';

    const goalPct = Math.min(100, Math.max(-100, bal.daily_goal_pct || 0));
    const goalBarColor = goalPct >= 0 ? '#00c896' : '#ff4757';
    const goalBarWidth = Math.abs(goalPct);

    const streakText = bal.streak > 0 ? `${bal.streak} ${bal.streak_type === 'win' ? 'W' : 'L'}` : '--';
    const streakColor = bal.streak_type === 'win' ? '#00c896' : bal.streak_type === 'loss' ? '#ff4757' : 'var(--text-secondary)';

    let bestWorstHtml = '';
    if (bal.best_trade) {
        bestWorstHtml += `<span style="font-size:10px;color:#00c896;">Best: ${bal.best_trade.ticker} +$${bal.best_trade.pnl.toFixed(2)}</span>`;
    }
    if (bal.worst_trade) {
        bestWorstHtml += `<span style="font-size:10px;color:#ff4757;margin-left:12px;">Worst: ${bal.worst_trade.ticker} $${bal.worst_trade.pnl.toFixed(2)}</span>`;
    }

    return `<div class="wd-panel" style="padding:14px 20px;">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:12px;">
            <span style="font-size:11px;font-weight:800;color:var(--text-secondary);text-transform:uppercase;letter-spacing:1px;">Account Overview</span>
            <span style="font-size:9px;padding:2px 8px;border-radius:10px;background:#4f8aff22;color:#4f8aff;font-weight:700;">PAPER MODE</span>
        </div>

        <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:14px;">
            <div style="text-align:center;padding:10px 18px;background:var(--bg-secondary);border-radius:10px;min-width:110px;">
                <div style="font-size:22px;font-weight:900;color:${eqColor};">$${(bal.total_equity || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.5px;">Total Equity</div>
            </div>
            <div style="text-align:center;padding:10px 18px;background:var(--bg-secondary);border-radius:10px;">
                <div style="font-size:18px;font-weight:800;color:${todayColor};">${bal.today_pnl >= 0 ? '+' : ''}$${(bal.today_pnl || 0).toFixed(2)}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Today</div>
            </div>
            <div style="text-align:center;padding:10px 18px;background:var(--bg-secondary);border-radius:10px;">
                <div style="font-size:18px;font-weight:800;color:${weekColor};">${bal.week_pnl >= 0 ? '+' : ''}$${(bal.week_pnl || 0).toFixed(2)}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">This Week</div>
            </div>
            <div style="text-align:center;padding:10px 18px;background:var(--bg-secondary);border-radius:10px;">
                <div style="font-size:18px;font-weight:800;color:${monthColor};">${bal.month_pnl >= 0 ? '+' : ''}$${(bal.month_pnl || 0).toFixed(2)}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">This Month</div>
            </div>
            <div style="text-align:center;padding:10px 18px;background:var(--bg-secondary);border-radius:10px;">
                <div style="font-size:18px;font-weight:800;color:${totalColor};">${bal.total_realized_pnl >= 0 ? '+' : ''}$${(bal.total_realized_pnl || 0).toFixed(2)}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Total Realized</div>
            </div>
            <div style="text-align:center;padding:10px 18px;background:var(--bg-secondary);border-radius:10px;">
                <div style="font-size:18px;font-weight:800;color:${unrealizedColor};">${bal.total_unrealized_pnl >= 0 ? '+' : ''}$${(bal.total_unrealized_pnl || 0).toFixed(2)}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Unrealized</div>
            </div>
        </div>

        <div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;">
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:10px;color:var(--text-secondary);">Win Rate:</span>
                <span style="font-size:13px;font-weight:800;color:var(--text-bright);">${bal.win_rate || 0}%</span>
                <span style="font-size:10px;color:var(--text-secondary);">(${bal.wins || 0}W / ${bal.losses || 0}L)</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:10px;color:var(--text-secondary);">Trades:</span>
                <span style="font-size:13px;font-weight:800;color:var(--text-bright);">${bal.total_trades || 0}</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:10px;color:var(--text-secondary);">Open:</span>
                <span style="font-size:13px;font-weight:800;color:var(--accent-blue);">${bal.open_positions || 0}</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:10px;color:var(--text-secondary);">Streak:</span>
                <span style="font-size:13px;font-weight:800;color:${streakColor};">${streakText}</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:10px;color:var(--text-secondary);">Fees:</span>
                <span style="font-size:11px;color:var(--text-secondary);">$${(bal.total_fees || 0).toFixed(2)}</span>
            </div>
            <div style="flex:1;"></div>
            ${bestWorstHtml}
        </div>

        <div style="margin-top:10px;">
            <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-secondary);margin-bottom:3px;">
                <span>Daily Goal Progress</span>
                <span style="color:${goalBarColor};font-weight:700;">${bal.today_pnl >= 0 ? '+' : ''}$${(bal.today_pnl || 0).toFixed(2)} / $${(bal.daily_goal || 1000).toFixed(0)}</span>
            </div>
            <div style="height:6px;background:var(--bg-secondary);border-radius:3px;overflow:hidden;">
                <div style="height:100%;width:${Math.min(goalBarWidth, 100)}%;background:${goalBarColor};border-radius:3px;transition:width 0.3s;"></div>
            </div>
        </div>
    </div>`;
}


// ─── Auto-Trader Control Bar + Settings Panel ──────────────

function renderWdAutoTraderBar(auto) {
    const running = auto.running;
    const mode = auto.mode || 'paper';
    const killSwitch = auto.kill_switch;
    const cfg = auto.config || {};

    const statusColor = killSwitch ? '#ff4757' : running ? '#00c896' : '#636b7e';
    const statusText = killSwitch ? 'KILL SWITCH' : running ? 'RUNNING' : 'STOPPED';
    const modeColor = mode === 'live' ? '#ff4757' : '#4f8aff';
    const modeLabel = mode === 'live' ? 'LIVE' : 'PAPER';
    // Buy window: green when open, amber in last 15 min before 13:00 ET cutoff,
    // red when fully closed. State comes from server (buy_window_state); falls
    // back to in_buy_window for older payloads.
    const buyWindowState = auto.buy_window_state || (auto.in_buy_window ? 'open' : 'closed');
    const minsToClose = auto.buy_window_minutes_to_close;
    const buyWindowMap = {
        open:         { color: '#00c896', text: 'BUY WINDOW OPEN' },
        closing_soon: { color: '#ff8c42', text: `CLOSING SOON${minsToClose != null ? ` · ${Math.ceil(minsToClose)}m` : ''}` },
        closed:       { color: '#ff4757', text: 'BUY WINDOW CLOSED' },
    };
    const buyWindowColor = buyWindowMap[buyWindowState].color;
    const buyWindowText  = buyWindowMap[buyWindowState].text;

    // All knobs the user can tune. Pulled from auto.config (server returns
    // resolved values incl. defaults). User asked for visible settings —
    // this panel exposes everything in one place rather than hiding things.
    const maxPositionPct = cfg.max_position_pct ?? 15;
    const maxExposurePct = cfg.max_total_exposure_pct ?? 50;
    const maxPositions   = cfg.max_positions ?? 3;
    const dailyLossLim   = cfg.daily_loss_limit ?? 1000;
    const minConfidence  = cfg.min_confidence ?? 65;
    const scanInterval   = cfg.scan_interval ?? 300;

    return `<div class="wd-panel" style="padding:14px 20px;">
        <!-- Header: status + start/stop -->
        <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">
            <div style="font-size:14px;font-weight:800;color:var(--text-bright);letter-spacing:0.4px;">ThunderBot</div>

            <div style="display:flex;align-items:center;gap:8px;">
                <span style="width:10px;height:10px;border-radius:50%;background:${statusColor};${running ? 'box-shadow:0 0 8px ' + statusColor + ';' : ''}"></span>
                <span style="font-size:13px;font-weight:800;color:${statusColor};letter-spacing:0.5px;">${statusText}</span>
            </div>

            <span style="padding:2px 10px;border-radius:10px;font-size:10px;font-weight:800;background:${modeColor}22;color:${modeColor};border:1px solid ${modeColor}44;">${modeLabel} MODE</span>

            <span style="padding:2px 10px;border-radius:10px;font-size:10px;font-weight:800;background:${buyWindowColor}22;color:${buyWindowColor};border:1px solid ${buyWindowColor}44;">${buyWindowText}</span>

            <div style="flex:1;"></div>

            <div style="font-size:10px;color:var(--text-secondary);">
                Buy: ${auto.buy_window || '9:45-13:00 ET (8:45-12:00 CST)'} | Exit: ${auto.profit_exit || '4-7%'} | Now: ${auto.current_time_et || auto.current_time_cst || '?'}
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

        <!-- Trading Settings Panel -->
        <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border-color);">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
                <span style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);letter-spacing:0.5px;">Trading Settings</span>
                <span style="flex:1;height:1px;background:var(--border-color);"></span>
            </div>

            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px 16px;font-size:11px;color:var(--text-secondary);">
                <label style="display:flex;flex-direction:column;gap:3px;">
                    <span>Mode</span>
                    <select id="wd-mode-select" onchange="wdSetMode(this.value)" style="padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:11px;">
                        <option value="paper" ${mode === 'paper' ? 'selected' : ''}>Paper Trading</option>
                        <option value="live" ${mode === 'live' ? 'selected' : ''}>Live Trading</option>
                    </select>
                </label>
                <label style="display:flex;flex-direction:column;gap:3px;">
                    <span>Per-position % of equity</span>
                    <input type="number" id="wd-cfg-max-pct" value="${maxPositionPct}" min="1" max="50" step="1"
                           style="padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:11px;">
                </label>
                <label style="display:flex;flex-direction:column;gap:3px;">
                    <span>Max total exposure %</span>
                    <input type="number" id="wd-cfg-max-exposure" value="${maxExposurePct}" min="10" max="100" step="5"
                           style="padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:11px;">
                </label>
                <label style="display:flex;flex-direction:column;gap:3px;">
                    <span>Max open positions</span>
                    <input type="number" id="wd-cfg-max-positions" value="${maxPositions}" min="1" max="10" step="1"
                           style="padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:11px;">
                </label>
                <label style="display:flex;flex-direction:column;gap:3px;">
                    <span>Daily loss limit ($)</span>
                    <input type="number" id="wd-cfg-loss-limit" value="${dailyLossLim}" min="50" max="10000" step="50"
                           style="padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:11px;">
                </label>
                <label style="display:flex;flex-direction:column;gap:3px;">
                    <span>Min signal confidence</span>
                    <input type="number" id="wd-cfg-min-conf" value="${minConfidence}" min="30" max="100" step="5"
                           style="padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:11px;">
                </label>
                <label style="display:flex;flex-direction:column;gap:3px;">
                    <span>Scan interval (sec)</span>
                    <input type="number" id="wd-cfg-scan-interval" value="${scanInterval}" min="60" max="3600" step="60"
                           style="padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:11px;">
                </label>
                <div style="display:flex;align-items:flex-end;">
                    <button onclick="wdSaveSettings()" class="btn-analyze" style="padding:6px 14px;font-size:11px;width:100%;">Save All Settings</button>
                </div>
            </div>
        </div>
    </div>`;
}

async function wdSaveSettings() {
    const get = (id) => document.getElementById(id).value;
    const payload = {
        wd_max_position_pct: get('wd-cfg-max-pct'),
        wd_max_total_exposure_pct: get('wd-cfg-max-exposure'),
        wd_max_positions: get('wd-cfg-max-positions'),
        wd_daily_loss_limit: get('wd-cfg-loss-limit'),
        wd_min_confidence: get('wd-cfg-min-conf'),
        wd_scan_interval: get('wd-cfg-scan-interval'),
    };
    try {
        await fetch('/api/watchdog/auto/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        loadWatchdogDashboard();
    } catch (e) { alert('Error: ' + e.message); }
}

function wdToggleOptionsFlow() {
    _wdOptionsFlowVisible = !_wdOptionsFlowVisible;
    const panel = document.getElementById('wd-options-flow-panel');
    const icon  = document.getElementById('wd-options-flow-icon');
    if (panel) panel.style.display = _wdOptionsFlowVisible ? 'block' : 'none';
    if (icon)  icon.textContent = _wdOptionsFlowVisible ? '▾' : '▸';
    // First reveal: ensure the chart renders now that the panel has dimensions
    if (_wdOptionsFlowVisible) _wdRenderFlowChart();
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


// ─── Intraday Opportunities ────────────────────────────────

function renderWdOpportunities(data) {
    const opps = data.opportunities || [];
    if (!opps.length) {
        return `<div class="wd-panel">
            <div class="wd-panel-title">Intraday Opportunities (Dip Recovery)</div>
            <div style="color:var(--text-secondary);font-size:12px;padding:8px 0;">No dip-recovery setups detected right now. Scanning ${data.scanned || 0} tickers...</div>
        </div>`;
    }

    const rows = opps.map(o => {
        const scoreColor = o.score >= 60 ? '#00c896' : o.score >= 40 ? '#ffc837' : '#ff8c42';
        const changeColor = o.change_from_close_pct >= 0 ? '#00c896' : '#ff4757';
        const recColor = '#00c896';

        return `<tr>
            <td style="font-weight:700;color:var(--text-bright);">${o.ticker}</td>
            <td style="color:var(--text-bright);">$${o.current_price.toFixed(2)}</td>
            <td style="color:#ff4757;">$${o.day_low.toFixed(2)}</td>
            <td style="color:${recColor};font-weight:700;">+${o.recovery_pct.toFixed(1)}%</td>
            <td style="color:${changeColor};">${o.change_from_close_pct >= 0 ? '+' : ''}${o.change_from_close_pct.toFixed(1)}%</td>
            <td style="text-align:center;">
                <span style="color:${scoreColor};font-weight:700;">${o.score}</span>
                ${o.is_v_recovery ? '<span style="margin-left:4px;font-size:9px;color:#00c896;font-weight:700;">V</span>' : ''}
            </td>
            <td style="font-size:10px;color:var(--text-secondary);">${o.rel_volume.toFixed(1)}x</td>
            <td style="font-size:10px;color:var(--text-secondary);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${o.reasons.join(' | ')}">${o.reasons[0] || ''}</td>
            <td style="font-size:10px;color:var(--text-secondary);">$${o.suggested_stop} / $${o.suggested_target}</td>
            <td style="white-space:nowrap;">
                <button onclick="wdQuickDipTrade('${o.ticker}', ${o.suggested_stop}, ${o.suggested_target})" class="btn-analyze" style="padding:3px 10px;font-size:10px;">Quick Trade</button>
                <button onclick="wdPredictLow('${o.ticker}')" style="background:#ffc83722;border:1px solid #ffc83744;color:#ffc837;border-radius:4px;padding:3px 8px;font-size:10px;cursor:pointer;margin-left:2px;">Predict Low</button>
                <button onclick="wdShowPatterns('${o.ticker}')" style="background:none;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:4px;padding:3px 8px;font-size:10px;cursor:pointer;margin-left:2px;">Patterns</button>
            </td>
        </tr>`;
    }).join('');

    return `<div class="wd-panel">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div class="wd-panel-title" style="margin-bottom:0;">Intraday Opportunities (Dip Recovery)</div>
            <div style="font-size:10px;color:var(--text-secondary);">Found ${data.found} of ${data.scanned} scanned</div>
        </div>
        <div style="overflow-x:auto;">
            <table class="wd-table">
                <thead><tr>
                    <th>Ticker</th><th>Price</th><th>Day Low</th><th>Recovery</th><th>vs Close</th>
                    <th style="text-align:center;">Score</th><th>Vol</th><th>Reason</th><th>SL / TP</th><th></th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    </div>`;
}

function wdQuickDipTrade(ticker, sl, tp) {
    const tickerInput = document.getElementById('wd-trade-ticker');
    const slInput = document.getElementById('wd-trade-sl');
    const tpInput = document.getElementById('wd-trade-tp');
    if (tickerInput) tickerInput.value = ticker;
    if (slInput && sl) slInput.value = sl;
    if (tpInput && tp) tpInput.value = tp;
    document.getElementById('wd-trade-form')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}


// ─── LLM Daily Low Prediction Modal ───────────────────────

async function wdPredictLow(ticker) {
    const overlay = document.createElement('div');
    overlay.id = 'wd-predict-overlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const modal = document.createElement('div');
    modal.style.cssText = 'background:var(--bg-primary);border:1px solid var(--border-color);border-radius:12px;padding:24px;max-width:550px;width:95%;max-height:80vh;overflow-y:auto;';
    modal.innerHTML = `<div style="text-align:center;padding:30px;">
        <div style="font-size:14px;color:var(--text-bright);margin-bottom:8px;">Analyzing ${ticker}...</div>
        <div style="font-size:11px;color:var(--text-secondary);">LLM analyzing volume pressure, candlesticks, support levels, VIX/SPY/Nasdaq correlation</div>
        <div style="margin-top:12px;"><div class="spinner"></div></div>
    </div>`;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    try {
        const resp = await fetch(`/api/watchdog/predict-low/${ticker}`);
        const data = await resp.json();

        if (data.error) {
            modal.innerHTML = `<div style="padding:20px;">
                <div style="color:#ff4757;font-size:14px;margin-bottom:12px;">Prediction Error: ${data.error}</div>
                <button onclick="document.getElementById('wd-predict-overlay').remove()" style="padding:6px 16px;background:var(--bg-secondary);color:var(--text-bright);border:1px solid var(--border-color);border-radius:6px;cursor:pointer;">Close</button>
            </div>`;
            return;
        }

        const confColor = data.confidence >= 70 ? '#00c896' : data.confidence >= 50 ? '#ffc837' : '#ff4757';
        const pressureColors = {
            'HEAVY_SELLING': '#ff4757', 'MODERATE_SELLING': '#ff8c42',
            'NEUTRAL': '#ffc837', 'MODERATE_BUYING': '#8bc34a', 'HEAVY_BUYING': '#00c896'
        };
        const pressureColor = pressureColors[data.volume_pressure] || '#ffc837';
        const bottomedColor = data.has_bottomed ? '#00c896' : '#ff8c42';
        const entryZone = data.entry_zone || [0, 0];
        const lowRange = data.predicted_low_range || [0, 0];

        modal.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
            <div>
                <div style="font-size:18px;font-weight:900;color:var(--text-bright);">${data.ticker} Daily Low Prediction</div>
                <div style="font-size:11px;color:var(--text-secondary);">Current: $${data.current_price} | Day Low: $${data.day_low} | RSI: ${data.rsi} | Vol: ${data.rel_volume}x</div>
            </div>
            <button onclick="document.getElementById('wd-predict-overlay').remove()" style="background:none;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px;">Close</button>
        </div>

        <!-- Predicted Low -->
        <div style="text-align:center;padding:18px;background:var(--bg-secondary);border-radius:10px;margin-bottom:16px;">
            <div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:1px;">Predicted Daily Low</div>
            <div style="font-size:28px;font-weight:900;color:${confColor};margin:6px 0;">$${(data.predicted_low || 0).toFixed(2)}</div>
            <div style="font-size:11px;color:var(--text-secondary);">Range: $${lowRange[0]?.toFixed(2)} — $${lowRange[1]?.toFixed(2)} | Confidence: <span style="color:${confColor};font-weight:700;">${data.confidence}%</span></div>
            <div style="margin-top:8px;font-size:12px;font-weight:700;color:${bottomedColor};">${data.has_bottomed ? 'LIKELY BOTTOMED — may be safe to enter' : 'MORE DOWNSIDE POSSIBLE — wait for entry zone'}</div>
        </div>

        <!-- Key Metrics -->
        <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;">
            <div style="flex:1;min-width:130px;padding:10px;background:var(--bg-secondary);border-radius:8px;text-align:center;">
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Entry Zone</div>
                <div style="font-size:14px;font-weight:800;color:#4f8aff;">$${entryZone[0]?.toFixed(2)} — $${entryZone[1]?.toFixed(2)}</div>
            </div>
            <div style="flex:1;min-width:100px;padding:10px;background:var(--bg-secondary);border-radius:8px;text-align:center;">
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Target</div>
                <div style="font-size:14px;font-weight:800;color:#00c896;">$${(data.target_price || 0).toFixed(2)}</div>
            </div>
            <div style="flex:1;min-width:100px;padding:10px;background:var(--bg-secondary);border-radius:8px;text-align:center;">
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Stop Loss</div>
                <div style="font-size:14px;font-weight:800;color:#ff4757;">$${(data.stop_loss || 0).toFixed(2)}</div>
            </div>
        </div>

        <!-- Volume & Pattern -->
        <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;">
            <div style="flex:1;padding:10px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Volume Pressure</div>
                <div style="font-size:13px;font-weight:800;color:${pressureColor};">${(data.volume_pressure || 'N/A').replace(/_/g, ' ')}</div>
            </div>
            <div style="flex:1;padding:10px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Candle Pattern</div>
                <div style="font-size:13px;font-weight:800;color:var(--text-bright);">${data.candle_pattern || 'none'}</div>
            </div>
            <div style="flex:1;padding:10px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Key Support</div>
                <div style="font-size:13px;font-weight:800;color:#ffc837;">$${(data.key_support || 0).toFixed(2)}</div>
            </div>
        </div>

        <!-- Reasoning -->
        <div style="padding:12px;background:var(--bg-secondary);border-radius:8px;margin-bottom:16px;">
            <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;margin-bottom:4px;">AI Reasoning</div>
            <div style="font-size:12px;color:var(--text-bright);line-height:1.5;">${data.reasoning || 'No reasoning provided'}</div>
            ${data.fallback ? '<div style="font-size:10px;color:#ff8c42;margin-top:6px;">Rule-based fallback (LLM unavailable)</div>' : ''}
        </div>

        <!-- Quick Trade Button -->
        <div style="text-align:center;">
            <button onclick="wdQuickDipTrade('${data.ticker}', ${data.stop_loss || 0}, ${data.target_price || 0}); document.getElementById('wd-predict-overlay').remove();" class="btn-analyze" style="padding:8px 24px;font-size:13px;">
                Trade at Entry Zone ($${entryZone[0]?.toFixed(2)} — $${entryZone[1]?.toFixed(2)})
            </button>
        </div>
        `;
    } catch (e) {
        modal.innerHTML = `<div style="padding:20px;">
            <div style="color:#ff4757;">Failed: ${e.message}</div>
            <button onclick="document.getElementById('wd-predict-overlay').remove()" style="margin-top:10px;padding:6px 16px;background:var(--bg-secondary);color:var(--text-bright);border:1px solid var(--border-color);border-radius:6px;cursor:pointer;">Close</button>
        </div>`;
    }
}


// ─── Pattern Analysis Modal ────────────────────────────────

async function wdShowPatterns(ticker) {
    const overlay = document.createElement('div');
    overlay.id = 'wd-pattern-overlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const modal = document.createElement('div');
    modal.style.cssText = 'background:var(--bg-primary);border:1px solid var(--border-color);border-radius:12px;padding:24px;max-width:700px;width:95%;max-height:85vh;overflow-y:auto;';
    modal.innerHTML = `<div style="text-align:center;padding:20px;color:var(--text-secondary);">Loading patterns for ${ticker}...</div>`;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    try {
        const resp = await fetch(`/api/watchdog/patterns/${ticker}`);
        const data = await resp.json();

        if (data.error) {
            modal.innerHTML = `<div style="color:#ff4757;padding:20px;">Error: ${data.error}</div><button onclick="document.getElementById('wd-pattern-overlay').remove()" style="margin:10px;padding:6px 16px;background:#ff4757;color:#fff;border:none;border-radius:6px;cursor:pointer;">Close</button>`;
            return;
        }

        const trends = data.trends || {};
        const trendColor = (dir) => dir === 'UP' ? '#00c896' : '#ff4757';
        const predColor = data.prediction_score > 10 ? '#00c896' : data.prediction_score < -10 ? '#ff4757' : '#ffc837';
        const alignColor = data.trend_alignment > 0 ? '#00c896' : data.trend_alignment < 0 ? '#ff4757' : '#ffc837';

        // Day of week table
        const dowRows = (data.day_of_week || []).map(d => {
            const c = d.avg_return_pct >= 0 ? '#00c896' : '#ff4757';
            const barW = Math.min(100, Math.abs(d.avg_return_pct) * 200);
            return `<tr>
                <td style="font-weight:700;color:var(--text-bright);">${d.day}</td>
                <td style="color:${c};font-weight:700;">${d.avg_return_pct >= 0 ? '+' : ''}${d.avg_return_pct.toFixed(3)}%</td>
                <td>${d.up_pct.toFixed(0)}%</td>
                <td><div style="height:8px;width:${barW}%;background:${c};border-radius:4px;"></div></td>
            </tr>`;
        }).join('');

        // Monthly table
        const monthRows = (data.monthly || []).map(m => {
            const c = m.avg_return_pct >= 0 ? '#00c896' : '#ff4757';
            return `<tr>
                <td style="font-weight:700;color:var(--text-bright);">${m.month}</td>
                <td style="color:${c};">${m.avg_return_pct >= 0 ? '+' : ''}${m.avg_return_pct.toFixed(3)}%</td>
                <td>${m.up_pct.toFixed(0)}%</td>
            </tr>`;
        }).join('');

        // Weekly returns mini chart
        const weeklyBars = (data.weekly_returns || []).map(r => {
            const c = r >= 0 ? '#00c896' : '#ff4757';
            const h = Math.min(40, Math.abs(r) * 8);
            return `<div style="display:flex;flex-direction:column;align-items:center;gap:2px;">
                <div style="width:14px;height:${h}px;background:${c};border-radius:2px;"></div>
                <span style="font-size:8px;color:${c};">${r > 0 ? '+' : ''}${r.toFixed(1)}</span>
            </div>`;
        }).join('');

        // Support / Resistance
        const supports = (data.support_levels || []).map(s => `<span style="display:inline-block;padding:2px 8px;margin:2px;background:#00c89622;color:#00c896;border-radius:4px;font-size:11px;font-weight:700;">$${s}</span>`).join('');
        const resistances = (data.resistance_levels || []).map(r => `<span style="display:inline-block;padding:2px 8px;margin:2px;background:#ff475722;color:#ff4757;border-radius:4px;font-size:11px;font-weight:700;">$${r}</span>`).join('');

        modal.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
            <div>
                <div style="font-size:18px;font-weight:900;color:var(--text-bright);">${data.ticker} Pattern Analysis</div>
                <div style="font-size:12px;color:var(--text-secondary);">Current: $${data.current_price} | Avg Daily Range: ${data.avg_daily_range_pct || '?'}%</div>
            </div>
            <button onclick="document.getElementById('wd-pattern-overlay').remove()" style="background:none;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px;">Close</button>
        </div>

        <!-- Prediction -->
        <div style="text-align:center;padding:16px;background:var(--bg-secondary);border-radius:10px;margin-bottom:16px;">
            <div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:1px;">Predicted Direction</div>
            <div style="font-size:24px;font-weight:900;color:${predColor};margin:4px 0;">${data.predicted_direction || '?'}</div>
            <div style="font-size:11px;color:var(--text-secondary);">Score: ${data.prediction_score || 0} | Alignment: <span style="color:${alignColor};font-weight:700;">${data.trend_verdict || '?'}</span></div>
        </div>

        <!-- Multi-Timeframe Trends -->
        <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;">
            <div style="flex:1;min-width:140px;padding:10px;background:var(--bg-secondary);border-radius:8px;text-align:center;">
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Daily (SMA20)</div>
                <div style="font-size:16px;font-weight:800;color:${trendColor(trends.daily?.direction)};">${trends.daily?.direction || '?'}</div>
                <div style="font-size:10px;color:var(--text-secondary);">$${trends.daily?.sma20 || '?'} (${trends.daily?.dist_pct > 0 ? '+' : ''}${trends.daily?.dist_pct || 0}%)</div>
            </div>
            <div style="flex:1;min-width:140px;padding:10px;background:var(--bg-secondary);border-radius:8px;text-align:center;">
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Weekly (SMA50)</div>
                <div style="font-size:16px;font-weight:800;color:${trendColor(trends.weekly?.direction)};">${trends.weekly?.direction || '?'}</div>
                <div style="font-size:10px;color:var(--text-secondary);">$${trends.weekly?.sma50 || '?'} (${trends.weekly?.dist_pct > 0 ? '+' : ''}${trends.weekly?.dist_pct || 0}%)</div>
            </div>
            <div style="flex:1;min-width:140px;padding:10px;background:var(--bg-secondary);border-radius:8px;text-align:center;">
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Monthly (SMA200)</div>
                <div style="font-size:16px;font-weight:800;color:${trendColor(trends.monthly?.direction)};">${trends.monthly?.direction || '?'}</div>
                <div style="font-size:10px;color:var(--text-secondary);">$${trends.monthly?.sma200 || '?'} (${trends.monthly?.dist_pct > 0 ? '+' : ''}${trends.monthly?.dist_pct || 0}%)</div>
            </div>
        </div>

        <!-- Weekly Returns Chart -->
        <div style="margin-bottom:16px;">
            <div style="font-size:11px;font-weight:700;color:var(--text-secondary);text-transform:uppercase;margin-bottom:6px;">Weekly Returns (last 12 weeks) — ${data.weekly_up_ratio || 0}% weeks up</div>
            <div style="display:flex;gap:4px;align-items:flex-end;padding:8px;background:var(--bg-secondary);border-radius:8px;min-height:60px;">
                ${weeklyBars}
            </div>
        </div>

        <!-- Day of Week Patterns -->
        <div style="display:flex;gap:16px;margin-bottom:16px;flex-wrap:wrap;">
            <div style="flex:1;min-width:250px;">
                <div style="font-size:11px;font-weight:700;color:var(--text-secondary);text-transform:uppercase;margin-bottom:6px;">Day-of-Week Pattern <span style="color:var(--text-bright);">(Best: ${data.best_day || '?'} | Worst: ${data.worst_day || '?'})</span></div>
                <table class="wd-table" style="font-size:12px;">
                    <thead><tr><th>Day</th><th>Avg Return</th><th>Up %</th><th></th></tr></thead>
                    <tbody>${dowRows}</tbody>
                </table>
            </div>
            <div style="flex:1;min-width:250px;">
                <div style="font-size:11px;font-weight:700;color:var(--text-secondary);text-transform:uppercase;margin-bottom:6px;">Monthly Seasonality</div>
                <div style="max-height:200px;overflow-y:auto;">
                <table class="wd-table" style="font-size:12px;">
                    <thead><tr><th>Month</th><th>Avg Return</th><th>Up %</th></tr></thead>
                    <tbody>${monthRows}</tbody>
                </table>
                </div>
            </div>
        </div>

        <!-- Support / Resistance -->
        <div style="display:flex;gap:16px;flex-wrap:wrap;">
            <div style="flex:1;">
                <div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;margin-bottom:4px;">Support Levels</div>
                ${supports || '<span style="font-size:11px;color:var(--text-secondary);">No clear supports</span>'}
            </div>
            <div style="flex:1;">
                <div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;margin-bottom:4px;">Resistance Levels</div>
                ${resistances || '<span style="font-size:11px;color:var(--text-secondary);">No clear resistances</span>'}
            </div>
        </div>
        `;
    } catch (e) {
        modal.innerHTML = `<div style="color:#ff4757;padding:20px;">Failed to load patterns: ${e.message}</div>
            <button onclick="document.getElementById('wd-pattern-overlay').remove()" style="margin:10px;padding:6px 16px;background:#ff4757;color:#fff;border:none;border-radius:6px;cursor:pointer;">Close</button>`;
    }
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


// ─── Signal Board: ThunderBot Identified Candidates ─────────
// Replaces the legacy multi-strategy Signal Board. Source: /api/watchdog/candidates
// — top 5-6 RSI+Volume+BullFlag setups, refreshed every 15 min by the rolling
// candidate scan. Same list the auto-trader executes from.

function renderWdSignalBoard(data) {
    const cands = data.candidates || [];
    const scannedAt = data.scan_started_at ? new Date(data.scan_started_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '?';
    const cohort = data.cohort || '';
    const refreshMin = Math.round((data.next_refresh_sec || 900) / 60);
    const isStale = !!data.stale;
    const isPending = !!data.pending;
    const isRefreshing = !!data.refreshing;

    // First-ever load — no cache at all yet, scan kicked off in background.
    // Auto-retry after 20s so the user doesn't have to refresh manually.
    if (isPending) {
        if (!window._wdPendingRetryTimer) {
            window._wdPendingRetryTimer = setTimeout(() => {
                window._wdPendingRetryTimer = null;
                loadWatchdogDashboard();
            }, 20000);
        }
        return `<div class="wd-panel">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <div class="wd-panel-title" style="margin-bottom:0;">Identified Candidates</div>
                <div style="font-size:10px;color:var(--text-secondary);">Cohort ${cohort}</div>
            </div>
            <div style="color:var(--text-secondary);font-size:12px;padding:18px 4px;display:flex;align-items:center;gap:10px;">
                <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#ffc837;animation:wdPulse 1.2s ease-in-out infinite;"></span>
                Scanning ${data.scanned || 200}+ tickers for RSI + volume + bull-flag setups… ~30–50s on the first call. Auto-refreshing in 20s.
            </div>
            <style>@keyframes wdPulse{0%,100%{opacity:0.4;}50%{opacity:1;}}</style>
        </div>`;
    }

    if (!cands.length) {
        // Surface rejection breakdown so the user sees WHY there are no
        // candidates instead of a blank panel. Each stage maps to one gate.
        const rej = data.rejections || {};
        const stageLabels = {
            no_data: 'No data',
            daily_data: 'Insufficient daily history',
            rsi_data: 'Insufficient RSI bars',
            intraday_data: 'No/short 5m frame',
            rsi_band: 'RSI outside 40-72',
            rel_volume: 'Volume pace below 1.2x',
            intra_range: 'Intraday range below 0.8%',
            bull_flag: 'No bull-flag pattern',
            error: 'Scoring error',
        };
        const rejRows = Object.keys(stageLabels)
            .filter(k => (rej[k] || 0) > 0)
            .map(k => `<tr><td style="color:var(--text-secondary);padding:2px 8px 2px 0;">${stageLabels[k]}</td><td style="color:var(--text-bright);font-weight:700;text-align:right;">${rej[k]}</td></tr>`)
            .join('');
        const samples = (data.rejection_samples || []).slice(0, 6).map(s =>
            `<div style="font-size:10px;color:var(--text-secondary);padding:2px 0;">
                <span style="color:var(--text-bright);font-weight:700;">${yahooFinanceLink(s.ticker, {compact: true})}</span>:
                ${s.reason}
            </div>`).join('');

        return `<div class="wd-panel">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <div class="wd-panel-title" style="margin-bottom:0;">Identified Candidates</div>
                <div style="font-size:10px;color:var(--text-secondary);">Cohort ${cohort} | ${data.scanned || 0} scanned | refresh ${refreshMin}m</div>
            </div>
            <div style="color:var(--text-secondary);font-size:12px;margin-bottom:12px;">
                No bull-flag candidates yet — gates: RSI 40-72 + volume pace ≥1.2x + intraday range ≥0.8% + bull-flag (pole ≥0.8%, flag ≤2.2% range).
            </div>
            ${rejRows ? `<table style="font-size:11px;margin-bottom:10px;">${rejRows}</table>` : ''}
            ${samples ? `<div style="border-top:1px solid var(--border-color);padding-top:8px;"><div style="font-size:10px;font-weight:700;color:var(--text-bright);margin-bottom:4px;">Sample rejections:</div>${samples}</div>` : ''}
        </div>`;
    }

    const chipHTML = c => (c.reason_chips || []).map(t => `<span style="display:inline-block;padding:2px 8px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:10px;font-size:10px;color:var(--text-bright);margin-right:4px;">${t}</span>`).join('');

    const rows = cands.map(c => {
        const scoreColor = c.score >= 70 ? '#00c896' : c.score >= 50 ? '#ffc837' : '#ff8c42';
        const pmColor = (c.premarket_pct || 0) >= 0 ? '#00c896' : '#ff4757';
        const flag = c.bull_flag || {};
        return `<tr>
            <td style="font-weight:700;color:var(--text-bright);">${yahooFinanceLink(c.ticker)}</td>
            <td style="color:var(--text-bright);">$${(c.price || 0).toFixed(2)}</td>
            <td style="text-align:center;"><span style="color:${scoreColor};font-weight:800;font-size:14px;">${c.score}</span></td>
            <td style="color:var(--text-secondary);font-size:11px;">${c.rsi || '—'}</td>
            <td style="color:var(--text-secondary);font-size:11px;">${(c.rel_volume || 0).toFixed(1)}x</td>
            <td style="color:var(--text-secondary);font-size:11px;">+${(flag.pole_pct || 0).toFixed(1)}%</td>
            <td style="color:${pmColor};font-size:11px;font-weight:700;">${c.premarket_pct ? (c.premarket_pct > 0 ? '+' : '') + c.premarket_pct.toFixed(1) + '%' : '—'}</td>
            <td>${chipHTML(c)}</td>
            <td style="white-space:nowrap;">
                <button onclick="wdQuickTrade('${c.ticker}','ThunderBot',0,0)" class="btn-analyze" style="padding:3px 10px;font-size:10px;">Paper Trade</button>
                <button onclick="wdShowPatterns('${c.ticker}')" style="background:none;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:4px;padding:3px 8px;font-size:10px;cursor:pointer;margin-left:2px;">Patterns</button>
            </td>
        </tr>`;
    }).join('');

    const staleTag = isStale
        ? `<span style="margin-left:6px;padding:1px 6px;background:#ffc83722;border:1px solid #ffc83755;color:#ffc837;border-radius:8px;font-size:9px;font-weight:700;">PREV COHORT</span>` : '';
    const refreshingTag = isRefreshing
        ? `<span style="margin-left:6px;padding:1px 6px;background:#4f8aff22;border:1px solid #4f8aff55;color:#4f8aff;border-radius:8px;font-size:9px;font-weight:700;">REFRESHING…</span>` : '';

    return `<div class="wd-panel">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div class="wd-panel-title" style="margin-bottom:0;">Identified Candidates <span style="font-size:10px;font-weight:500;color:var(--text-secondary);">(RSI + Volume + Bull Flag)</span>${staleTag}${refreshingTag}</div>
            <div style="font-size:10px;color:var(--text-secondary);">Cohort ${cohort} @ ${scannedAt} | ${data.scanned || 0} scanned | refresh ${refreshMin}m | ${data.cached ? 'cached' : 'live'}</div>
        </div>
        <div style="overflow-x:auto;">
            <table class="wd-table">
                <thead><tr>
                    <th>Ticker</th><th>Price</th><th style="text-align:center;">Score</th>
                    <th>RSI</th><th>RelVol</th><th>Pole</th><th>Pre-Mkt</th><th>Why</th><th></th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    </div>`;
}


// ─── Watchlist Manager ─────────────────────────────────────

function renderWdWatchlistManager() {
    return `<div class="wd-panel">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div class="wd-panel-title" style="margin-bottom:0;">My Watchlist</div>
            <div style="font-size:10px;color:var(--text-secondary);">Add your own stocks to scan & trade from</div>
        </div>
        <div id="wd-watchlist-tags" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;min-height:30px;"></div>
        <div style="display:flex;gap:8px;align-items:center;">
            <input type="text" id="wd-watchlist-input" placeholder="Enter ticker (e.g. APLD, TSLA)" style="flex:1;max-width:240px;padding:6px 12px;background:var(--bg-secondary);border:1px solid var(--border-color);color:var(--text-bright);border-radius:6px;font-size:12px;" onkeydown="if(event.key==='Enter')wdAddTicker()">
            <button onclick="wdAddTicker()" class="btn-analyze" style="padding:6px 14px;font-size:12px;">Add</button>
            <button onclick="wdResetWatchlist()" style="background:none;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:6px;padding:6px 12px;font-size:11px;cursor:pointer;">Reset to Defaults</button>
        </div>
    </div>`;
}

async function _wdLoadWatchlist() {
    try {
        const resp = await fetch('/api/watchdog/watchlist');
        const data = await resp.json();
        const container = document.getElementById('wd-watchlist-tags');
        if (!container) return;

        const tickers = data.watchlist || [];
        container.innerHTML = tickers.map(t => `
            <span style="display:inline-flex;align-items:center;gap:4px;padding:4px 10px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:16px;font-size:11px;font-weight:700;color:var(--text-bright);">
                ${yahooFinanceLink(t, {compact: true})}
                <span onclick="wdRemoveTicker('${t}')" style="cursor:pointer;color:#ff4757;font-size:14px;line-height:1;margin-left:2px;">&times;</span>
            </span>
        `).join('');
    } catch (e) {
        console.error('Watchlist load failed:', e);
    }
}

async function wdAddTicker() {
    const input = document.getElementById('wd-watchlist-input');
    const ticker = (input?.value || '').trim().toUpperCase();
    if (!ticker) return;

    try {
        const resp = await fetch('/api/watchdog/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'add', tickers: [ticker] }),
        });
        const data = await resp.json();
        if (data.ok) {
            input.value = '';
            _wdLoadWatchlist();
        } else {
            alert(data.error || 'Failed to add ticker');
        }
    } catch (e) { alert('Error: ' + e.message); }
}

async function wdRemoveTicker(ticker) {
    try {
        const resp = await fetch('/api/watchdog/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'remove', tickers: [ticker] }),
        });
        const data = await resp.json();
        if (data.ok) _wdLoadWatchlist();
    } catch (e) { alert('Error: ' + e.message); }
}

async function wdResetWatchlist() {
    if (!confirm('Reset watchlist to defaults (SPY, QQQ, IWM, DIA, XLK, XLF, XLE, XLV, GLD, TLT)?')) return;
    try {
        const resp = await fetch('/api/watchdog/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set', tickers: ['SPY','QQQ','IWM','DIA','XLK','XLF','XLE','XLV','GLD','TLT'] }),
        });
        const data = await resp.json();
        if (data.ok) _wdLoadWatchlist();
    } catch (e) { alert('Error: ' + e.message); }
}


// ─── Paper Trading ──────────────────────────────────────────

function renderWdPaperTrading(data) {
    const trades = data.trades || [];
    const summary = data.summary || {};
    const openTrades = trades.filter(t => t.status === 'open');
    const closedTrades = trades.filter(t => t.status === 'closed').slice(0, 20);

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
                <td style="font-weight:700;color:var(--text-bright);">${yahooFinanceLink(t.ticker)}</td>
                <td style="font-size:12px;color:var(--text-secondary);">${t.side?.toUpperCase()}</td>
                <td>${t.shares}</td>
                <td>$${(t.entry_price || 0).toFixed(2)}</td>
                <td style="color:var(--text-bright);">$${(t.current_price || 0).toFixed(2)}</td>
                <td style="color:${pnlColor};font-weight:700;">$${(t.unrealized_pnl || 0).toFixed(2)} (${(t.unrealized_pct || 0).toFixed(1)}%)</td>
                <td style="font-size:11px;color:var(--text-secondary);">${t.stop_loss ? '$' + t.stop_loss : '—'} / ${t.take_profit ? '$' + t.take_profit : '—'}</td>
                <td style="white-space:nowrap;">
                    <button onclick="wdCloseTrade(${t.id})" style="background:#ff4757;color:#fff;border:none;border-radius:4px;padding:3px 10px;font-size:10px;cursor:pointer;">Close</button>
                    <button onclick="wdShowPatterns('${t.ticker}')" style="background:none;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:4px;padding:3px 8px;font-size:10px;cursor:pointer;margin-left:2px;">Patterns</button>
                </td>
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
                <td style="font-weight:700;color:var(--text-bright);">${yahooFinanceLink(t.ticker)}</td>
                <td style="font-size:12px;color:var(--text-secondary);">${t.side?.toUpperCase()}</td>
                <td>$${(t.entry_price || 0).toFixed(2)}</td>
                <td>$${(t.exit_price || 0).toFixed(2)}</td>
                <td style="color:${pnlColor};font-weight:700;">$${(t.pnl || 0).toFixed(2)} (${(t.pnl_pct || 0).toFixed(1)}%)</td>
                <td style="font-size:11px;color:var(--text-secondary);">${t.strategy || '—'}</td>
                <td style="font-size:11px;color:var(--text-bright);">${t.closed_at ? new Date(t.closed_at).toLocaleDateString() : '—'}</td>
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
    document.getElementById('wd-trade-form')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}


// ─── Options Flow Tracker (Calls vs Puts) ──────────────────────

let _wdFlowSSE = null;

// In-memory history for real-time chart (stores SSE updates)
let _wdFlowHistory = {};  // symbol -> [{ratio, callPct, timestamp}, ...]

function _wdFmtMoney(v) {
    if (v == null || isNaN(v)) return '—';
    const a = Math.abs(v);
    if (a >= 1e9) return '$' + (v / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return '$' + (v / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return '$' + (v / 1e3).toFixed(1) + 'K';
    return '$' + v.toFixed(0);
}

function _wdFmtMoneySigned(v) {
    if (v == null || isNaN(v)) return '—';
    return (v >= 0 ? '+' : '') + _wdFmtMoney(v);
}

// Strike distribution SVG: green call $vol bars left of axis, red put $vol bars right.
// Centerline marks the current spot. Width-aware (responsive within container).
function _wdStrikeDistChart(strikes, currentPrice) {
    if (!strikes || !strikes.length) {
        return `<div style="font-size:10px;color:var(--text-secondary);text-align:center;padding:16px;">No strike-level activity</div>`;
    }
    const W = 360, padL = 50, padR = 8, padT = 6, padB = 6;
    const rowH = 12;
    const H = padT + padB + strikes.length * rowH;
    const chartW = W - padL - padR;
    const halfW = chartW / 2;
    const maxVol = Math.max(...strikes.map(s => Math.max(s.call_dollar_vol || 0, s.put_dollar_vol || 0))) || 1;

    let bars = '';
    strikes.forEach((s, i) => {
        const y = padT + i * rowH + 1;
        const callW = (s.call_dollar_vol || 0) / maxVol * halfW;
        const putW = (s.put_dollar_vol || 0) / maxVol * halfW;
        const isAtm = currentPrice && Math.abs(s.strike - currentPrice) < 0.5;
        const labelColor = isAtm ? '#ffc837' : 'var(--text-secondary)';
        const labelWeight = isAtm ? '700' : '400';
        bars += `<text x="${padL - 4}" y="${y + 8}" font-size="9" fill="${labelColor}" font-weight="${labelWeight}" text-anchor="end">$${s.strike.toFixed(0)}</text>`;
        // Calls bar grows left from center
        bars += `<rect x="${padL + halfW - callW}" y="${y}" width="${callW}" height="${rowH - 2}" fill="#00c896" opacity="0.85"/>`;
        // Puts bar grows right from center
        bars += `<rect x="${padL + halfW}" y="${y}" width="${putW}" height="${rowH - 2}" fill="#ff4757" opacity="0.85"/>`;
    });

    // Center axis (spot price marker)
    const axis = `<line x1="${padL + halfW}" y1="${padT}" x2="${padL + halfW}" y2="${H - padB}" stroke="var(--border-color)" stroke-width="1"/>`;

    return `<svg width="100%" viewBox="0 0 ${W} ${H}" style="display:block;">
        ${axis}${bars}
        <text x="${padL + halfW * 0.5}" y="${padT - 1}" font-size="8" fill="#00c896" text-anchor="middle" font-weight="700">CALLS $</text>
        <text x="${padL + halfW * 1.5}" y="${padT - 1}" font-size="8" fill="#ff4757" text-anchor="middle" font-weight="700">PUTS $</text>
    </svg>`;
}

function _wdTopContractsRows(contracts) {
    if (!contracts || !contracts.length) {
        return `<tr><td colspan="6" style="text-align:center;padding:8px;color:var(--text-secondary);font-size:10px;">No contracts</td></tr>`;
    }
    return contracts.slice(0, 8).map(c => {
        const typeColor = c.type === 'call' ? '#00c896' : '#ff4757';
        const typeLabel = c.type === 'call' ? 'C' : 'P';
        return `<tr style="border-bottom:1px solid var(--border-color);">
            <td style="padding:3px 6px;font-weight:700;color:${typeColor};">$${c.strike.toFixed(0)}${typeLabel}</td>
            <td style="padding:3px 6px;font-size:10px;color:var(--text-secondary);">${c.expiry || '—'}</td>
            <td style="padding:3px 6px;text-align:right;font-family:monospace;">${(c.volume || 0).toLocaleString()}</td>
            <td style="padding:3px 6px;text-align:right;font-family:monospace;color:var(--text-secondary);">${(c.oi || 0).toLocaleString()}</td>
            <td style="padding:3px 6px;text-align:right;font-family:monospace;">$${(c.mid || 0).toFixed(2)}</td>
            <td style="padding:3px 6px;text-align:right;font-family:monospace;font-weight:700;color:${typeColor};">${_wdFmtMoney(c.dollar_vol)}</td>
        </tr>`;
    }).join('');
}

// Premium-weighted card per ticker — replaces the "cheap" compact tile.
function _wdRenderFlowCard(sym, f) {
    const sentColor = f.sentiment_color || '#ffc837';
    const netCol = (f.net_premium || 0) >= 0 ? '#00c896' : '#ff4757';
    const netLabel = (f.net_premium || 0) >= 0 ? 'INTO CALLS' : 'INTO PUTS';

    return `<div style="background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:10px;" id="wd-flow-card-${sym}">
        <!-- Header -->
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
            <div>
                <div style="font-size:14px;font-weight:800;color:var(--text-bright);">${sym}</div>
                <div style="font-size:10px;color:var(--text-secondary);">${f.label || sym}${f.price ? ' · $' + f.price.toFixed(2) : ''}</div>
            </div>
            <span style="font-size:10px;font-weight:700;color:${sentColor};padding:3px 10px;border-radius:8px;background:${sentColor}22;border:1px solid ${sentColor}44;white-space:nowrap;">${f.sentiment || 'NEUTRAL'}</span>
        </div>

        <!-- Hero net-premium number -->
        <div style="text-align:center;padding:6px 0;border-top:1px solid var(--border-color);border-bottom:1px solid var(--border-color);">
            <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:2px;">Net Premium</div>
            <div style="font-size:26px;font-weight:800;color:${netCol};line-height:1;">${_wdFmtMoneySigned(f.net_premium)}</div>
            <div style="font-size:9px;color:${netCol};letter-spacing:0.4px;margin-top:2px;">${netLabel}</div>
        </div>

        <!-- 4 sub-KPIs -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:10px;">
            <div><span style="color:var(--text-secondary);">Avg Call:</span> <span style="color:#00c896;font-weight:700;">$${(f.avg_call_price || 0).toFixed(2)}</span></div>
            <div><span style="color:var(--text-secondary);">Avg Put:</span> <span style="color:#ff4757;font-weight:700;">$${(f.avg_put_price || 0).toFixed(2)}</span></div>
            <div><span style="color:var(--text-secondary);">Call $Vol:</span> <span style="color:#00c896;font-weight:700;">${_wdFmtMoney(f.call_value)}</span></div>
            <div><span style="color:var(--text-secondary);">Put $Vol:</span> <span style="color:#ff4757;font-weight:700;">${_wdFmtMoney(f.put_value)}</span></div>
        </div>

        <!-- Volume share bar -->
        <div>
            <div style="height:7px;border-radius:4px;background:var(--bg-primary);overflow:hidden;display:flex;">
                <div style="width:${f.call_pct || 50}%;background:#00c896;"></div>
                <div style="width:${f.put_pct || 50}%;background:#ff4757;"></div>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:9px;margin-top:2px;">
                <span style="color:#00c896;">Calls ${(f.call_volume || 0).toLocaleString()} (${(f.call_pct || 0).toFixed(0)}%)</span>
                <span style="color:#ff4757;">Puts ${(f.put_volume || 0).toLocaleString()} (${(f.put_pct || 0).toFixed(0)}%)</span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--text-secondary);margin-top:3px;">
                <span>P/C Vol: <strong style="color:var(--text-bright);">${(f.pc_ratio || 0).toFixed(2)}</strong></span>
                <span>P/C OI: <strong style="color:var(--text-bright);">${(f.pc_oi_ratio || 0).toFixed(2)}</strong></span>
            </div>
        </div>

        <!-- Strike distribution -->
        <div>
            <div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Strike Distribution (±10%)</div>
            ${_wdStrikeDistChart(f.strike_distribution, f.price)}
        </div>

        <!-- Top contracts table -->
        <div>
            <div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Top Contracts by Premium</div>
            <table style="width:100%;font-size:11px;border-collapse:collapse;">
                <thead><tr style="border-bottom:1px solid var(--border-color);color:var(--text-secondary);font-size:9px;text-transform:uppercase;letter-spacing:0.4px;">
                    <th style="padding:3px 6px;text-align:left;">Strike</th>
                    <th style="padding:3px 6px;text-align:left;">Exp</th>
                    <th style="padding:3px 6px;text-align:right;">Vol</th>
                    <th style="padding:3px 6px;text-align:right;">OI</th>
                    <th style="padding:3px 6px;text-align:right;">Mid</th>
                    <th style="padding:3px 6px;text-align:right;">Premium</th>
                </tr></thead>
                <tbody>${_wdTopContractsRows(f.top_contracts)}</tbody>
            </table>
        </div>
    </div>`;
}

function renderWdOptionsFlow(data) {
    const flow = data.flow || {};
    const symbols = Object.keys(flow);
    if (!symbols.length) {
        return `<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:12px;padding:16px;margin-bottom:16px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);">Options Flow — Calls vs Puts (Real-Time)</div>
                <button onclick="_wdStartFlowScanner()" class="btn-analyze" style="padding:4px 12px;font-size:10px;">Start Scanner</button>
            </div>
            <div style="text-align:center;padding:20px;font-size:11px;color:var(--text-secondary);">No options flow data yet. Start the scanner to begin tracking.</div>
        </div>`;
    }

    // Seed history for the time-series chart
    for (const sym of symbols) {
        const f = flow[sym];
        if (!_wdFlowHistory[sym]) _wdFlowHistory[sym] = [];
        _wdFlowHistory[sym].push({ ratio: f.pc_ratio, callPct: f.call_pct, putPct: f.put_pct, ts: f.timestamp });
    }

    // Per-ticker cards (responsive grid: 2 cols on wide, 1 on narrow)
    const cards = symbols.map(sym => _wdRenderFlowCard(sym, flow[sym])).join('');

    return `<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:12px;padding:16px;margin-bottom:16px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="display:flex;align-items:center;gap:8px;">
                <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);">Options Flow — Calls vs Puts (Real-Time)</div>
                <span id="wd-flow-live-dot" style="width:6px;height:6px;border-radius:50%;background:${data.scanner_running ? '#00c896' : '#636b7e'};display:inline-block;animation:${data.scanner_running ? 'pulse 2s infinite' : 'none'};"></span>
                <span style="font-size:9px;color:var(--text-secondary);">${data.scanner_running ? 'LIVE' : 'PAUSED'}</span>
                <span style="font-size:9px;color:var(--text-secondary);margin-left:6px;">Mid-price weighted · ${symbols.length} ticker${symbols.length === 1 ? '' : 's'}</span>
            </div>
            <button onclick="${data.scanner_running ? '_wdStopFlowScanner()' : '_wdStartFlowScanner()'}" class="btn-analyze" style="padding:4px 12px;font-size:10px;">${data.scanner_running ? 'Stop' : 'Start'} Scanner</button>
        </div>

        <!-- P/C ratio time-series -->
        <div id="wd-flow-chart" style="height:160px;position:relative;margin-bottom:14px;background:var(--bg-secondary);border-radius:8px;padding:8px;"></div>

        <!-- Per-ticker premium cards -->
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px;">${cards}</div>

        <!-- Shift notifications -->
        <div id="wd-flow-notifications" style="margin-top:12px;"></div>
    </div>`;
}

function _wdRenderFlowChart() {
    const el = document.getElementById('wd-flow-chart');
    if (!el) return;

    const symbols = Object.keys(_wdFlowHistory);
    if (!symbols.length) {
        el.innerHTML = '<div style="text-align:center;padding:40px;font-size:11px;color:var(--text-secondary);">Collecting data points...</div>';
        return;
    }

    const W = el.clientWidth || 500;
    const H = 150;
    const padL = 35, padR = 10, padT = 10, padB = 20;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;

    // Find global Y range across all symbols
    let allRatios = [];
    for (const sym of symbols) {
        allRatios.push(..._wdFlowHistory[sym].map(d => d.ratio));
    }
    const minR = Math.min(0.4, ...allRatios);
    const maxR = Math.max(1.6, ...allRatios);
    const range = maxR - minR || 1;

    const toY = (r) => padT + chartH - ((r - minR) / range * chartH);
    const maxPoints = Math.max(...symbols.map(s => _wdFlowHistory[s].length));

    // Colors per symbol
    const colors = { SPY: '#4f8aff', QQQ: '#ffc837' };

    // Threshold zones
    const bearY = toY(1.2);
    const bullY = toY(0.7);
    const neutralY = toY(1.0);

    let zones = `
        <rect x="${padL}" y="${padT}" width="${chartW}" height="${Math.max(0, bearY - padT)}" fill="#ff475711"/>
        <rect x="${padL}" y="${bullY}" width="${chartW}" height="${Math.max(0, padT + chartH - bullY)}" fill="#00c89611"/>
        <line x1="${padL}" y1="${bearY}" x2="${W - padR}" y2="${bearY}" stroke="#ff4757" stroke-width="1" stroke-dasharray="4,3" opacity="0.5"/>
        <line x1="${padL}" y1="${bullY}" x2="${W - padR}" y2="${bullY}" stroke="#00c896" stroke-width="1" stroke-dasharray="4,3" opacity="0.5"/>
        <line x1="${padL}" y1="${neutralY}" x2="${W - padR}" y2="${neutralY}" stroke="var(--border-color)" stroke-width="1" stroke-dasharray="2,2" opacity="0.4"/>
        <text x="${padL - 2}" y="${bearY + 3}" fill="#ff4757" font-size="8" text-anchor="end">1.2</text>
        <text x="${padL - 2}" y="${neutralY + 3}" fill="var(--text-secondary)" font-size="8" text-anchor="end">1.0</text>
        <text x="${padL - 2}" y="${bullY + 3}" fill="#00c896" font-size="8" text-anchor="end">0.7</text>
        <text x="${W - padR}" y="${bearY - 3}" fill="#ff4757" font-size="8" text-anchor="end">BEARISH</text>
        <text x="${W - padR}" y="${bullY + 10}" fill="#00c896" font-size="8" text-anchor="end">BULLISH</text>`;

    // Draw lines + dots for each symbol
    let lines = '';
    let legend = '';
    let legendX = padL + 5;
    for (const sym of symbols) {
        const hist = _wdFlowHistory[sym];
        const color = colors[sym] || '#8bc34a';
        if (hist.length < 2) {
            // Single dot
            const x = padL + chartW / 2;
            const y = toY(hist[0].ratio);
            lines += `<circle cx="${x}" cy="${y}" r="4" fill="${color}" opacity="0.9"/>`;
        } else {
            const step = chartW / (hist.length - 1);
            let path = '';
            for (let i = 0; i < hist.length; i++) {
                const x = padL + i * step;
                const y = toY(hist[i].ratio);
                path += (i === 0 ? 'M' : 'L') + `${x.toFixed(1)},${y.toFixed(1)}`;
                // Draw dot on last point (current)
                if (i === hist.length - 1) {
                    lines += `<circle cx="${x}" cy="${y}" r="4" fill="${color}" stroke="#fff" stroke-width="1.5"/>`;
                    lines += `<text x="${x - 6}" y="${y - 8}" fill="${color}" font-size="9" font-weight="700">${hist[i].ratio.toFixed(2)}</text>`;
                }
            }
            lines += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>`;
            // Area fill under line
            const areaPath = path + `L${(padL + (hist.length - 1) * step).toFixed(1)},${(padT + chartH).toFixed(1)}L${padL},${(padT + chartH).toFixed(1)}Z`;
            lines += `<path d="${areaPath}" fill="${color}" opacity="0.08"/>`;
        }
        // Legend entry
        legend += `<g><rect x="${legendX}" y="${H - 12}" width="10" height="3" rx="1" fill="${color}"/><text x="${legendX + 14}" y="${H - 8}" fill="var(--text-secondary)" font-size="9">${sym}</text></g>`;
        legendX += 55;
    }

    // Y-axis labels
    let yLabels = '';
    const steps = 5;
    for (let i = 0; i <= steps; i++) {
        const val = minR + (range / steps) * i;
        const y = toY(val);
        yLabels += `<text x="${padL - 2}" y="${y + 3}" fill="var(--text-secondary)" font-size="7" text-anchor="end">${val.toFixed(2)}</text>`;
    }

    el.innerHTML = `<svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}">
        ${zones}${yLabels}${lines}${legend}
    </svg>`;
}

function _wdStartOptionsFlowStream() {
    if (_wdFlowSSE) {
        _wdFlowSSE.close();
        _wdFlowSSE = null;
    }

    try {
        _wdFlowSSE = new EventSource('/api/watchdog/options-flow/stream');

        _wdFlowSSE.addEventListener('flow_update', (e) => {
            try {
                const data = JSON.parse(e.data);
                _wdUpdateFlowPanel(data);
            } catch (err) {}
        });

        _wdFlowSSE.addEventListener('shift', (e) => {
            try {
                const shift = JSON.parse(e.data);
                _wdShowShiftNotification(shift);
            } catch (err) {}
        });

        _wdFlowSSE.addEventListener('snapshot', (e) => {
            try {
                const snapshot = JSON.parse(e.data);
                // Populate history from snapshot
                for (const [sym, data] of Object.entries(snapshot)) {
                    if (!_wdFlowHistory[sym]) _wdFlowHistory[sym] = [];
                    _wdFlowHistory[sym].push({ ratio: data.pc_ratio, callPct: data.call_pct, putPct: data.put_pct, ts: data.timestamp });
                }
                _wdRenderFlowChart();

                // Update live dot
                const dot = document.getElementById('wd-flow-live-dot');
                if (dot) dot.style.background = '#00c896';
            } catch (err) {}
        });

        _wdFlowSSE.onerror = () => {
            // Auto-reconnect after 5 seconds
            setTimeout(() => {
                if (document.getElementById('watchdog-content')?.style.display !== 'none') {
                    _wdStartOptionsFlowStream();
                }
            }, 5000);
        };
    } catch (e) {
        // SSE not supported or connection failed — degrade gracefully
    }
}

function _wdUpdateFlowPanel(data) {
    const sym = data.symbol;
    if (!sym) return;

    // Push to history for chart (keep last 100 points)
    if (!_wdFlowHistory[sym]) _wdFlowHistory[sym] = [];
    _wdFlowHistory[sym].push({ ratio: data.pc_ratio, callPct: data.call_pct, putPct: data.put_pct, ts: data.timestamp });
    if (_wdFlowHistory[sym].length > 100) _wdFlowHistory[sym] = _wdFlowHistory[sym].slice(-100);

    // Re-render time-series chart
    _wdRenderFlowChart();

    // Replace the entire ticker card so all new fields (net premium, strike
    // distribution, top contracts) refresh together. Cheap — one DOM swap per ticker.
    const cardEl = document.getElementById(`wd-flow-card-${sym}`);
    if (cardEl) {
        const tmp = document.createElement('div');
        tmp.innerHTML = _wdRenderFlowCard(sym, data);
        const newCard = tmp.firstElementChild;
        if (newCard) cardEl.replaceWith(newCard);
    }
}

function _wdShowShiftNotification(shift) {
    const container = document.getElementById('wd-flow-notifications');
    if (!container) return;

    const isBearish = shift.type.includes('BEARISH') || shift.type.includes('PUT');
    const color = isBearish ? '#ff4757' : '#00c896';
    const icon = isBearish ? '&#9888;' : '&#9889;';
    const time = new Date(shift.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    const notif = document.createElement('div');
    notif.style.cssText = `display:flex;align-items:center;gap:8px;padding:8px 12px;margin-bottom:6px;
        background:${color}11;border:1px solid ${color}33;border-radius:8px;animation:fadeIn 0.3s;`;
    notif.innerHTML = `
        <span style="font-size:14px;">${icon}</span>
        <div style="flex:1;">
            <div style="font-size:11px;font-weight:700;color:${color};">${shift.type.replace(/_/g, ' ')}</div>
            <div style="font-size:10px;color:var(--text-secondary);">${shift.message}</div>
        </div>
        <span style="font-size:9px;color:var(--text-bright);">${time}</span>`;

    container.prepend(notif);

    // Keep max 5 notifications
    while (container.children.length > 5) {
        container.removeChild(container.lastChild);
    }

    // Browser notification (if permitted)
    if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(`Options Flow: ${shift.type.replace(/_/g, ' ')}`, {
            body: shift.message,
            icon: '/static/favicon.ico',
            tag: 'options-flow-' + shift.symbol,
        });
    } else if ('Notification' in window && Notification.permission !== 'denied') {
        Notification.requestPermission();
    }
}

async function _wdStartFlowScanner() {
    await fetch('/api/watchdog/options-flow/start', { method: 'POST' });
    _wdStartOptionsFlowStream();
    const dot = document.getElementById('wd-flow-live-dot');
    if (dot) dot.style.background = '#00c896';
}

async function _wdStopFlowScanner() {
    await fetch('/api/watchdog/options-flow/stop', { method: 'POST' });
    if (_wdFlowSSE) { _wdFlowSSE.close(); _wdFlowSSE = null; }
    const dot = document.getElementById('wd-flow-live-dot');
    if (dot) dot.style.background = '#636b7e';
}
