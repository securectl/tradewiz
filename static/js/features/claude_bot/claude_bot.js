// ─── Claude Trading Bot Dashboard ────────────────────────────

let _cbLoading = false;

async function loadClaudeBotDashboard() {
    if (_cbLoading) return;
    _cbLoading = true;

    const container = document.getElementById('claude-bot-dashboard');
    if (!container) { _cbLoading = false; return; }

    try {
        const [statusResp, balanceResp, oversoldResp, watchlistResp, tradesResp, logsResp] = await Promise.allSettled([
            fetch('/api/claude_bot/status'),
            fetch('/api/claude_bot/balance'),
            fetch('/api/claude_bot/oversold-tracker?limit=25'),
            fetch('/api/claude_bot/watchlist'),
            fetch('/api/bot/trades?asset_type=claude&limit=20'),
            fetch('/api/claude_bot/logs?limit=80'),
        ]);

        if (statusResp.status === 'fulfilled' && statusResp.value.status === 403) {
            container.innerHTML = `<div style="text-align:center;padding:40px 0;">
                <div style="font-size:28px;margin-bottom:8px;">&#128274;</div>
                <div style="font-size:14px;font-weight:700;color:var(--text-bright);margin-bottom:6px;">Bot access required</div>
                <div style="font-size:12px;color:var(--text-secondary);">Claude Bot is invite-only. Contact admin.</div>
            </div>`;
            _cbLoading = false;
            return;
        }

        let html = '';

        if (statusResp.status === 'fulfilled' && statusResp.value.ok) {
            const s = await statusResp.value.json();
            html += renderCbHeader(s);
            if (balanceResp.status === 'fulfilled' && balanceResp.value.ok) {
                const b = await balanceResp.value.json();
                html += renderCbBalance(b);
            }
            // Config moved into a modal — header has a "⚙ Config" trigger.
            html += renderCbConfigModal(s.config || {});
        }

        if (oversoldResp.status === 'fulfilled' && oversoldResp.value.ok) {
            const o = await oversoldResp.value.json();
            html += renderCbOversoldTracker(o.tickers || []);
        }

        if (watchlistResp.status === 'fulfilled' && watchlistResp.value.ok) {
            const w = await watchlistResp.value.json();
            html += renderCbWatchlist(w.watchlist || []);
        }

        if (tradesResp.status === 'fulfilled' && tradesResp.value.ok) {
            const t = await tradesResp.value.json();
            html += renderCbTrades(t.trades || t || []);
        }

        if (logsResp.status === 'fulfilled' && logsResp.value.ok) {
            const l = await logsResp.value.json();
            html += renderCbLogs(l.logs || []);
        }

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="text-align:center;padding:40px;color:#ef5350;">Claude Bot failed: ${e.message}</div>`;
    }

    _cbLoading = false;
}


// ─── Header / status / controls ─────────────────────────────

function renderCbHeader(s) {
    const running = s.running;
    const kill = s.kill_switch;
    const dot = running ? '#00c896' : '#ff8c42';
    const label = running ? 'RUNNING' : 'STOPPED';
    const pnl = Number(s.daily_pnl || 0);
    const pnlColor = pnl >= 0 ? '#00c896' : '#ff4757';
    const pnlSign = pnl >= 0 ? '+' : '';

    return `
    <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:16px;margin-bottom:14px;">
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:14px;">
            <div style="display:flex;align-items:center;gap:14px;">
                <div style="width:10px;height:10px;border-radius:50%;background:${dot};box-shadow:0 0 6px ${dot};"></div>
                <div>
                    <div style="font-size:18px;font-weight:700;color:var(--text-bright);">Claude Bot — ${label}</div>
                    <div style="font-size:11px;color:var(--text-secondary);">scalp-and-rotate · paper · stocks via Alpaca</div>
                </div>
            </div>
            <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap;">
                <div><div style="font-size:10px;color:var(--text-secondary);">OPEN</div><div style="font-size:16px;font-weight:700;">${s.open_positions || 0}</div></div>
                <div><div style="font-size:10px;color:var(--text-secondary);">WATCHING</div><div style="font-size:16px;font-weight:700;">${s.watchlist_count || 0}</div></div>
                <div><div style="font-size:10px;color:var(--text-secondary);">TODAY P&L</div><div style="font-size:16px;font-weight:700;color:${pnlColor};">${pnlSign}$${pnl.toFixed(2)}</div></div>
                <div style="display:flex;gap:8px;">
                    ${running
                        ? `<button onclick="cbStop()" class="btn-analyze" style="padding:7px 14px;font-size:12px;background:#ff8c42;">Stop</button>`
                        : `<button onclick="cbStart()" class="btn-analyze" style="padding:7px 14px;font-size:12px;background:#00c896;">Start</button>`}
                    <button onclick="openBotConfig('cb')" class="btn-analyze" style="padding:7px 14px;font-size:12px;background:transparent;border:1px solid var(--border-color);">⚙ Config</button>
                    <button onclick="cbToggleKill()" class="btn-analyze" style="padding:7px 14px;font-size:12px;background:${kill ? '#ff4757' : 'transparent'};border:1px solid ${kill ? '#ff4757' : 'var(--border-color)'};">${kill ? 'Killed (clear)' : 'Kill switch'}</button>
                </div>
            </div>
        </div>
    </div>`;
}


function renderCbBalance(b) {
    const eqColor = b.total_equity >= b.paper_start ? '#00c896' : '#ff4757';
    const todayColor = b.today_pnl >= 0 ? '#00c896' : '#ff4757';
    const weekColor = b.week_pnl >= 0 ? '#00c896' : '#ff4757';
    const realizedColor = b.total_realized_pnl >= 0 ? '#00c896' : '#ff4757';
    const unrealizedColor = b.total_unrealized_pnl >= 0 ? '#00c896' : '#ff4757';
    const sign = (n) => (n >= 0 ? '+' : '') + '$' + Number(n).toFixed(2);

    return `
    <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:14px 18px;margin-bottom:14px;">
        <div style="font-size:13px;font-weight:700;color:var(--text-bright);margin-bottom:10px;">Balance & P&L (paper)</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:14px;">
            <div><div style="font-size:10px;color:var(--text-secondary);">EQUITY</div>
                 <div style="font-size:18px;font-weight:700;color:${eqColor};">$${Number(b.total_equity).toLocaleString()}</div>
                 <div style="font-size:10px;color:var(--text-secondary);">start $${Number(b.paper_start).toLocaleString()}</div></div>
            <div><div style="font-size:10px;color:var(--text-secondary);">REALIZED</div>
                 <div style="font-size:16px;font-weight:700;color:${realizedColor};">${sign(b.total_realized_pnl)}</div></div>
            <div><div style="font-size:10px;color:var(--text-secondary);">UNREALIZED</div>
                 <div style="font-size:16px;font-weight:700;color:${unrealizedColor};">${sign(b.total_unrealized_pnl)}</div></div>
            <div><div style="font-size:10px;color:var(--text-secondary);">TODAY</div>
                 <div style="font-size:16px;font-weight:700;color:${todayColor};">${sign(b.today_pnl)}</div></div>
            <div><div style="font-size:10px;color:var(--text-secondary);">WEEK</div>
                 <div style="font-size:16px;font-weight:700;color:${weekColor};">${sign(b.week_pnl)}</div></div>
            <div><div style="font-size:10px;color:var(--text-secondary);">MONTH</div>
                 <div style="font-size:16px;font-weight:700;color:${b.month_pnl >= 0 ? '#00c896' : '#ff4757'};">${sign(b.month_pnl)}</div></div>
            <div><div style="font-size:10px;color:var(--text-secondary);">WIN RATE</div>
                 <div style="font-size:16px;font-weight:700;">${b.win_rate}% <span style="font-size:11px;color:var(--text-secondary);">(${b.wins}/${b.total_trades})</span></div></div>
            <div><div style="font-size:10px;color:var(--text-secondary);">OPEN POSITIONS</div>
                 <div style="font-size:16px;font-weight:700;">${b.open_positions}</div></div>
        </div>
    </div>`;
}


function renderCbLogs(logs) {
    if (!logs || !logs.length) {
        return `<div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:14px 18px;margin-top:14px;">
            <div style="font-size:13px;font-weight:700;color:var(--text-bright);margin-bottom:8px;">Bot Log</div>
            <div style="font-size:12px;color:var(--text-secondary);">No log entries yet.</div>
        </div>`;
    }
    const colorFor = (lvl) => ({error:'#ff4757', warning:'#ff8c42', warn:'#ff8c42', info:'var(--text-bright)'}[lvl] || 'var(--text-secondary)');
    const rows = logs.map(l => `<div style="display:flex;gap:10px;padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.04);font-family:monospace;font-size:11px;">
        <span style="color:var(--text-bright);min-width:140px;">${(l.created_at || '').slice(0, 19).replace('T',' ')}</span>
        <span style="color:${colorFor(l.level)};min-width:55px;text-transform:uppercase;font-weight:700;">${l.level}</span>
        <span style="color:var(--text-bright);flex:1;word-break:break-word;">${(l.message || '').replace(/</g,'&lt;')}</span>
    </div>`).join('');

    return `
    <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:14px 18px;margin-top:14px;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
            <div style="font-size:13px;font-weight:700;color:var(--text-bright);">Bot Log <span style="color:var(--text-secondary);font-weight:400;">(${logs.length} most recent)</span></div>
            <button onclick="loadClaudeBotDashboard()" class="btn-analyze" style="padding:4px 10px;font-size:11px;">Refresh</button>
        </div>
        <div style="max-height:340px;overflow-y:auto;background:var(--bg-input);padding:8px 10px;border-radius:4px;">
            ${rows}
        </div>
    </div>`;
}


function renderCbConfigModal(cfg) {
    // Wraps the existing `renderCbConfig` panel in the shared modal pattern
    // (.settings-backdrop + .settings-modal). Hidden by default; opened via
    // the ⚙ Config button in the header. Click backdrop or X to close.
    const inner = renderCbConfig(cfg);
    return `
    <div class="settings-backdrop" id="cb-config-backdrop" onclick="closeBotConfig('cb')" style="display:none;"></div>
    <div class="settings-modal" id="cb-config-modal" style="display:none;">
        <div style="display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid var(--border-color);">
            <div style="font-size:14px;font-weight:700;color:var(--text-bright);">Claude Bot — Configuration</div>
            <button onclick="closeBotConfig('cb')" style="background:transparent;border:0;color:var(--text-secondary);font-size:20px;cursor:pointer;line-height:1;">×</button>
        </div>
        <div style="padding:14px 18px;overflow-y:auto;">${inner}</div>
    </div>`;
}


function renderCbConfig(cfg) {
    const fields = [
        ['cb_take_profit_pct', 'Take Profit %', cfg.take_profit_pct, 0.5, 1, 20],
        ['cb_scalp_stop_pct', 'Scalp Stop %', cfg.scalp_stop_pct, 0.5, 0.5, 10],
        ['cb_hard_stop_pct', 'Hard Stop %', cfg.hard_stop_pct, 0.5, 2, 25],
        ['cb_max_positions', 'Max Positions', cfg.max_positions, 1, 1, 20],
        ['cb_max_position_pct', 'Per-Position % of Funds', cfg.max_position_pct, 1, 1, 100],
        ['cb_max_total_exposure_pct', 'Max Total Exposure %', cfg.max_total_exposure_pct, 5, 10, 100],
        ['cb_daily_loss_limit', 'Daily Loss $', cfg.daily_loss_limit, 50, 50, 5000],
        ['cb_min_confidence', 'Min Confidence', cfg.min_confidence, 5, 30, 100],
        ['cb_reentry_size_factor', 'Re-entry Size ×', cfg.reentry_size_factor, 0.05, 0.1, 1.0],
        ['cb_reentry_max_attempts', 'Re-entry Attempts', cfg.reentry_max_attempts, 1, 1, 5],
        ['cb_reentry_window_days', 'Re-entry Window (d)', cfg.reentry_window_days, 1, 1, 14],
        ['cb_scan_interval', 'Scan Interval (s)', cfg.scan_interval, 30, 60, 1800],
    ];
    const rows = fields.map(([k, label, val, step, min, max]) => `
        <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-color);">
            <div style="flex:1;font-size:12px;color:var(--text-secondary);">${label}</div>
            <input type="number" id="cfg-${k}" value="${val}" step="${step}" min="${min}" max="${max}"
                   style="width:90px;padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:12px;">
            <button onclick="cbSaveConfig('${k}')" class="btn-analyze" style="padding:4px 10px;font-size:11px;">Save</button>
        </div>`).join('');

    // Broker + mode selectors. Live mode now routes real orders to the
    // broker (Apr 2026 — CLAUDE.md rule #1 relaxed for per-user opt-in).
    // Picking Live shows a clear visual warning so users don't toggle by
    // accident; risk gates still apply.
    const broker = (cfg.broker || 'alpaca').toLowerCase();
    const mode = (cfg.mode || 'paper').toLowerCase();
    const liveWarning = mode === 'live'
        ? `<div style="font-size:10px;color:#ef5350;margin-top:4px;font-weight:600;">⚠ LIVE — real orders on your ${broker.toUpperCase()} account. Verify daily loss limit + position % before starting the bot.</div>`
        : '';

    const brokerRow = `
        <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-color);">
            <div style="flex:1;font-size:12px;color:var(--text-secondary);">Broker</div>
            <select id="cfg-cb_broker" style="width:140px;padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:12px;">
                <option value="alpaca" ${broker === 'alpaca' ? 'selected' : ''}>Alpaca</option>
                <option value="webull" ${broker === 'webull' ? 'selected' : ''}>Webull</option>
            </select>
            <button onclick="cbSaveConfig('cb_broker')" class="btn-analyze" style="padding:4px 10px;font-size:11px;">Save</button>
        </div>`;

    const modeRow = `
        <div style="padding:6px 0;border-bottom:1px solid var(--border-color);">
            <div style="display:flex;align-items:center;gap:8px;">
                <div style="flex:1;font-size:12px;color:var(--text-secondary);">Account</div>
                <select id="cfg-cb_mode" style="width:140px;padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:12px;">
                    <option value="paper" ${mode === 'paper' ? 'selected' : ''}>Paper</option>
                    <option value="live" ${mode === 'live' ? 'selected' : ''}>Live (real orders)</option>
                </select>
                <button onclick="cbSaveConfig('cb_mode')" class="btn-analyze" style="padding:4px 10px;font-size:11px;">Save</button>
            </div>
            ${liveWarning}
        </div>`;

    return `
    <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:14px 18px;margin-bottom:14px;">
        <div style="font-size:13px;font-weight:700;color:var(--text-bright);margin-bottom:10px;">Configuration</div>
        ${brokerRow}
        ${modeRow}
        ${rows}
    </div>`;
}


function renderCbOversoldTracker(items) {
    if (!items || !items.length) {
        return `<div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:14px 18px;margin-bottom:14px;">
            <div style="font-size:13px;font-weight:700;color:var(--text-bright);margin-bottom:8px;">Oversold Day-Over-Day Tracker</div>
            <div style="font-size:12px;color:var(--text-secondary);">No oversold scans cached yet. Run the screener Oversold scan to populate.</div>
        </div>`;
    }
    const qualifying = items.filter(t => t.qualifies);
    const buckets = { '4d+': 0, '3d': 0, '2d': 0, '1d': 0 };
    items.forEach(t => {
        if (t.days_tracked >= 4) buckets['4d+']++;
        else if (t.days_tracked === 3) buckets['3d']++;
        else if (t.days_tracked === 2) buckets['2d']++;
        else buckets['1d']++;
    });

    const rsiColor = (v) => {
        if (v == null) return 'var(--text-secondary)';
        if (v < 30) return '#00c896';      // deeply oversold (buy zone)
        if (v < 40) return '#26a69a';      // oversold
        if (v < 60) return 'var(--text-bright)';
        if (v < 70) return '#ff9800';
        return '#ff4757';                  // overbought
    };

    const opportunityCount = items.filter(t => t.is_opportunity).length;
    const rows = items.map((t, idx) => {
        const days = t.days_tracked || 1;
        const persistColor = days >= 4 ? '#00c896' : (days === 3 ? '#ffc837' : 'var(--text-secondary)');
        const qualifies = t.qualifies ? '✓' : '';
        const trend = (t.price_trend || '').replace('_', ' ');
        const trendColor = trend === 'bouncing' || trend === 'first bounce' ? '#00c896'
                         : trend === 'falling' ? '#ff4757'
                         : trend === 'stabilizing' ? '#ffc837' : 'var(--text-secondary)';

        const wwChange = t.rsi_weekly_change;
        const wwColor = wwChange == null ? 'var(--text-secondary)' : wwChange > 0 ? '#00c896' : wwChange < 0 ? '#ff4757' : 'var(--text-secondary)';
        const wwSign = wwChange != null && wwChange > 0 ? '+' : '';

        const opp = t.is_opportunity;
        const rowBg = opp ? 'background:rgba(0,200,150,0.10);' : (t.qualifies ? 'background:rgba(0,200,150,0.04);' : '');
        const oppBadge = opp ? `<span style="display:inline-block;padding:1px 6px;background:#00c896;color:#000;border-radius:8px;font-size:9px;font-weight:800;margin-left:6px;">★ OPP</span>` : '';

        return `<tr style="${rowBg}">
            <td style="padding:6px 8px;font-weight:700;">${t.ticker}${oppBadge}</td>
            <td style="padding:6px 8px;text-align:center;font-weight:700;color:${persistColor};">${days}d</td>
            <td style="padding:6px 8px;text-align:center;color:#00c896;font-weight:700;">${qualifies}</td>
            <td style="padding:6px 8px;font-family:monospace;">$${t.price ? Number(t.price).toFixed(2) : '—'}</td>
            <td style="padding:6px 8px;font-family:monospace;text-align:right;color:${rsiColor(t.rsi)};font-weight:600;" title="RSI-14 on daily candles (last 14 daily closes)">${t.rsi != null ? Number(t.rsi).toFixed(1) : '—'}</td>
            <td style="padding:6px 8px;font-family:monospace;text-align:right;color:${rsiColor(t.rsi_weekly)};font-weight:600;" title="RSI-14 on weekly (W-FRI) closes — last 14 weeks">${t.rsi_weekly != null ? Number(t.rsi_weekly).toFixed(1) : '—'}</td>
            <td style="padding:6px 8px;font-family:monospace;text-align:right;color:${wwColor};font-weight:600;" title="Change in weekly RSI vs prior week">${wwChange != null ? wwSign + Number(wwChange).toFixed(1) : '—'}</td>
            <td style="padding:6px 8px;font-family:monospace;text-align:right;color:${rsiColor(t.rsi_monthly)};font-weight:600;" title="RSI-14 on monthly closes — last 14 months">${t.rsi_monthly != null ? Number(t.rsi_monthly).toFixed(1) : '—'}</td>
            <td style="padding:6px 8px;font-family:monospace;text-align:right;color:${rsiColor(t.rsi_composite)};font-weight:800;border-left:2px solid var(--border-color);" title="Average of D/W/M RSIs — bot's primary opportunity ranker">${t.rsi_composite != null ? Number(t.rsi_composite).toFixed(1) : '—'}</td>
            <td style="padding:6px 8px;font-size:11px;">${t.verdict || '—'}</td>
            <td style="padding:6px 8px;font-size:11px;color:${trendColor};">${trend || '—'}</td>
            <td style="padding:6px 8px;font-size:11px;color:var(--text-secondary);">${t.status || '—'}</td>
            <td style="padding:6px 8px;font-size:11px;color:var(--text-secondary);">${(t.first_seen || '').slice(0, 10)}</td>
        </tr>`;
    }).join('');

    return `
    <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:14px 18px;margin-bottom:14px;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:8px;">
            <div style="font-size:13px;font-weight:700;color:var(--text-bright);">Oversold Day-Over-Day Tracker</div>
            <div style="display:flex;gap:14px;font-size:11px;">
                <span><span style="color:#00c896;font-weight:700;">${buckets['4d+']}</span> @4d+</span>
                <span><span style="color:#ffc837;font-weight:700;">${buckets['3d']}</span> @3d</span>
                <span><span style="color:var(--text-secondary);font-weight:700;">${buckets['2d']}</span> @2d</span>
                <span><span style="color:var(--text-secondary);font-weight:700;">${buckets['1d']}</span> @1d</span>
                <span style="color:var(--text-secondary);">·</span>
                <span><span style="color:#00c896;font-weight:700;">${qualifying.length}</span> qualifying (≥3d)</span>
                <span style="color:var(--text-secondary);">·</span>
                <span><span style="color:#00c896;font-weight:800;">★ ${opportunityCount}</span> strong opportunities (avg RSI &lt; 35)</span>
            </div>
        </div>
        <div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px;">Sorted by composite RSI (lowest first). Bot enters on multi-TF oversold confluence — composite + daily both must be &lt; 40.</div>
        <div style="font-size:10px;color:var(--text-secondary);margin-bottom:10px;line-height:1.5;">
            <strong style="color:var(--text-bright);">RSI methodology:</strong>
            <span style="color:#26a69a;">D</span> = RSI-14 on last 14 <em>daily</em> closes (simple-mean) ·
            <span style="color:#26a69a;">W</span> = RSI-14 on weekly (W-FRI) closes, last 14 weeks ·
            <span style="color:#26a69a;">W/W Δ</span> = current week's RSI − prior week's RSI ·
            <span style="color:#26a69a;">M</span> = RSI-14 on monthly closes, last 14 months ·
            <span style="color:#00c896;font-weight:700;">Avg</span> = mean of D/W/M (bot's primary opportunity ranker)
        </div>
        <div style="overflow-x:auto;">
            <table style="width:100%;font-size:12px;border-collapse:collapse;">
                <thead><tr style="text-align:left;border-bottom:1px solid var(--border-color);color:var(--text-secondary);">
                    <th style="padding:6px 8px;">Ticker</th>
                    <th style="padding:6px 8px;text-align:center;">Days</th>
                    <th style="padding:6px 8px;text-align:center;">Qualifies</th>
                    <th style="padding:6px 8px;">Price</th>
                    <th style="padding:6px 8px;text-align:right;" title="RSI-14 on daily candles">RSI D</th>
                    <th style="padding:6px 8px;text-align:right;" title="RSI-14 on weekly candles">RSI W</th>
                    <th style="padding:6px 8px;text-align:right;" title="Change in weekly RSI vs prior week">W/W Δ</th>
                    <th style="padding:6px 8px;text-align:right;" title="RSI-14 on monthly candles">RSI M</th>
                    <th style="padding:6px 8px;text-align:right;border-left:2px solid var(--border-color);color:#00c896;" title="Average of D/W/M RSIs — bot's opportunity ranker">RSI Avg</th>
                    <th style="padding:6px 8px;">Verdict</th>
                    <th style="padding:6px 8px;">Trend</th>
                    <th style="padding:6px 8px;">Status</th>
                    <th style="padding:6px 8px;">First Seen</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    </div>`;
}


function renderCbWatchlist(items) {
    if (!items || !items.length) {
        return `<div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:14px 18px;margin-bottom:14px;">
            <div style="font-size:13px;font-weight:700;color:var(--text-bright);margin-bottom:8px;">Re-entry Watchlist</div>
            <div style="font-size:12px;color:var(--text-secondary);">No tickers being watched. After exits, names appear here for up to 3 days.</div>
        </div>`;
    }
    const rows = items.map(w => {
        const pnl = Number(w.exit_pnl_pct || 0);
        const pnlColor = pnl >= 0 ? '#00c896' : '#ff4757';
        return `<tr>
            <td style="padding:6px 8px;font-weight:700;">${w.ticker}</td>
            <td style="padding:6px 8px;font-family:monospace;">$${Number(w.exit_price || 0).toFixed(2)}</td>
            <td style="padding:6px 8px;font-family:monospace;color:${pnlColor};">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%</td>
            <td style="padding:6px 8px;font-size:11px;color:var(--text-secondary);">${w.exit_reason || ''}</td>
            <td style="padding:6px 8px;text-align:center;">${w.attempts || 0}</td>
            <td style="padding:6px 8px;font-size:11px;color:var(--text-bright);">${(w.created_at || '').slice(0, 16)}</td>
        </tr>`;
    }).join('');

    return `
    <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:14px 18px;margin-bottom:14px;">
        <div style="font-size:13px;font-weight:700;color:var(--text-bright);margin-bottom:10px;">Re-entry Watchlist (${items.length})</div>
        <table style="width:100%;font-size:12px;border-collapse:collapse;">
            <thead><tr style="text-align:left;border-bottom:1px solid var(--border-color);color:var(--text-secondary);">
                <th style="padding:6px 8px;">Ticker</th><th style="padding:6px 8px;">Exit Price</th>
                <th style="padding:6px 8px;">Exit P&L</th><th style="padding:6px 8px;">Exit Reason</th>
                <th style="padding:6px 8px;text-align:center;">Attempts</th><th style="padding:6px 8px;">Since</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>
    </div>`;
}


function renderCbTrades(trades) {
    if (!trades || !trades.length) {
        return `<div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:14px 18px;">
            <div style="font-size:13px;font-weight:700;color:var(--text-bright);margin-bottom:8px;">Recent Trades</div>
            <div style="font-size:12px;color:var(--text-secondary);">No trades yet.</div>
        </div>`;
    }
    const rows = trades.slice(0, 20).map(t => {
        const status = t.status || '';
        const isOpen = status === 'open';

        // Closed: show realized P&L. Open: fall back to unrealized (live mark
        // minus entry × size). Italicized + suffixed "live" so it's clear it's
        // a mark-to-market estimate, not booked.
        const realizedPnl = Number(t.pnl || 0);
        const realizedPct = Number(t.pnl_pct || 0);
        const unrealPnl = t.pnl_unrealized != null ? Number(t.pnl_unrealized) : null;
        const unrealPct = t.pnl_pct_unrealized != null ? Number(t.pnl_pct_unrealized) : 0;

        let pnlCell = '—';
        if (!isOpen && realizedPnl) {
            const c = realizedPnl >= 0 ? '#00c896' : '#ff4757';
            pnlCell = `<span style="color:${c};">${realizedPnl >= 0 ? '+' : ''}$${realizedPnl.toFixed(2)} (${realizedPct.toFixed(2)}%)</span>`;
        } else if (isOpen && unrealPnl != null) {
            const c = unrealPnl >= 0 ? '#00c896' : '#ff4757';
            pnlCell = `<span style="color:${c};font-style:italic;">${unrealPnl >= 0 ? '+' : ''}$${unrealPnl.toFixed(2)} (${unrealPct.toFixed(2)}%) <span style="font-size:10px;opacity:0.7;">live</span></span>`;
        }

        // Exit column: closed → exit price; open with mark → current price (dimmed); else —.
        let exitCell = '—';
        if (!isOpen && t.exit_price) {
            exitCell = `$${Number(t.exit_price).toFixed(2)}`;
        } else if (isOpen && t.current_price != null) {
            exitCell = `<span style="color:var(--text-secondary);">$${Number(t.current_price).toFixed(2)}</span>`;
        }

        const actionCell = isOpen
            ? `<button onclick="closeClaudeBotTrade(${t.id}, '${(t.coin || '').replace(/'/g, "&#39;")}')" style="background:#ff4757;color:#fff;border:0;padding:3px 9px;border-radius:4px;font-size:11px;font-weight:600;cursor:pointer;">Close</button>`
            : '';

        return `<tr>
            <td style="padding:6px 8px;font-weight:700;">${t.coin || t.ticker || ''}</td>
            <td style="padding:6px 8px;">${t.side || ''}</td>
            <td style="padding:6px 8px;font-family:monospace;">${Number(t.size || 0).toFixed(0)}</td>
            <td style="padding:6px 8px;font-family:monospace;">$${Number(t.entry_price || 0).toFixed(2)}</td>
            <td style="padding:6px 8px;font-family:monospace;">${exitCell}</td>
            <td style="padding:6px 8px;font-family:monospace;">${pnlCell}</td>
            <td style="padding:6px 8px;font-size:11px;color:var(--text-secondary);">${status}</td>
            <td style="padding:6px 8px;font-size:11px;color:var(--text-bright);">${(t.opened_at || '').toString().slice(0, 16)}</td>
            <td style="padding:6px 8px;text-align:center;">${actionCell}</td>
        </tr>`;
    }).join('');

    return `
    <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:14px 18px;">
        <div style="font-size:13px;font-weight:700;color:var(--text-bright);margin-bottom:10px;">Recent Trades</div>
        <table style="width:100%;font-size:12px;border-collapse:collapse;">
            <thead><tr style="text-align:left;border-bottom:1px solid var(--border-color);color:var(--text-secondary);">
                <th style="padding:6px 8px;">Ticker</th><th style="padding:6px 8px;">Side</th>
                <th style="padding:6px 8px;">Qty</th><th style="padding:6px 8px;">Entry</th>
                <th style="padding:6px 8px;">Exit</th><th style="padding:6px 8px;">P&L</th>
                <th style="padding:6px 8px;">Status</th><th style="padding:6px 8px;">Opened</th>
                <th style="padding:6px 8px;text-align:center;">Action</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>
    </div>`;
}


// ─── Actions ─────────────────────────────────────────────────

async function cbStart() {
    const r = await fetch('/api/claude_bot/start', { method: 'POST' });
    const j = await r.json();
    if (!j.ok) alert('Start failed: ' + (j.error || 'unknown'));
    loadClaudeBotDashboard();
}

async function cbStop() {
    const r = await fetch('/api/claude_bot/stop', { method: 'POST' });
    const j = await r.json();
    if (!j.ok) alert('Stop failed: ' + (j.error || 'unknown'));
    loadClaudeBotDashboard();
}

async function closeClaudeBotTrade(tradeId, ticker) {
    if (!confirm(`Close ${ticker || 'this position'} at the current market price?`)) return;
    try {
        const r = await fetch(`/api/claude_bot/trades/${tradeId}/close`, { method: 'POST' });
        const j = await r.json();
        if (!j.ok) {
            alert('Close failed: ' + (j.error || 'unknown'));
            return;
        }
        loadClaudeBotDashboard();
    } catch (e) {
        alert('Close failed: ' + e.message);
    }
}

async function cbToggleKill() {
    // Read current then toggle
    const sresp = await fetch('/api/claude_bot/status');
    const s = sresp.ok ? await sresp.json() : { kill_switch: false };
    const next = s.kill_switch ? '0' : '1';
    await fetch('/api/claude_bot/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cb_kill_switch: next }),
    });
    loadClaudeBotDashboard();
}

async function cbSaveConfig(key) {
    const el = document.getElementById('cfg-' + key);
    if (!el) return;
    const body = {};
    body[key] = el.value;
    const r = await fetch('/api/claude_bot/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const j = await r.json();
    if (j.ok) {
        el.style.outline = '2px solid #00c896';
        setTimeout(() => { el.style.outline = ''; }, 700);
    } else {
        alert('Save failed');
    }
}
