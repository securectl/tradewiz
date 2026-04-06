// ═══════════════════════════════════════════════════════════════
// AUTO TRADING BOT — Dashboard Functions
// ═══════════════════════════════════════════════════════════════

let botPnlChart = null;
let botPnlSeries = null;

async function loadBotStatus() {
    try {
        const resp = await fetch('/api/bot/status');
        if (resp.status === 403) {
            const errData = await resp.json();
            if (errData.upgrade_url) { showPricingModal(); }
            return;
        }
        const data = await resp.json();

        // Status indicator
        const dot = document.getElementById('bot-dot');
        const statusText = document.getElementById('bot-status-text');
        if (data.running) {
            dot.className = 'bot-dot running';
            statusText.textContent = 'Running';
        } else if (data.kill_switch) {
            dot.className = 'bot-dot killed';
            statusText.textContent = 'KILLED';
        } else {
            dot.className = 'bot-dot stopped';
            statusText.textContent = 'Stopped';
        }

        // Market sensor status
        const sensorEl = document.getElementById('bot-sensor-status');
        if (sensorEl) {
            const sensor = data.market_sensor;
            if (sensor) {
                const colors = { HEALTHY: '#26a69a', CAUTION: '#ff9800', DANGER: '#ef5350' };
                sensorEl.textContent = sensor.status + (sensor.cached ? ' (cached)' : '');
                sensorEl.style.color = colors[sensor.status] || '';
                sensorEl.title = sensor.reasoning || '';
            } else {
                sensorEl.textContent = 'N/A';
                sensorEl.style.color = '';
            }
        }

        // Balance
        const bal = data.balance || {};
        document.getElementById('bot-balance').textContent = '$' + (bal.total_equity || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});

        // Daily P&L
        const dailyEl = document.getElementById('bot-daily-pnl');
        const dp = data.daily_pnl || 0;
        dailyEl.textContent = (dp >= 0 ? '+' : '') + '$' + dp.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
        dailyEl.className = 'bot-stat-value ' + (dp >= 0 ? 'positive' : 'negative');

        // Daily Goal progress
        const goalEl = document.getElementById('bot-daily-goal-progress');
        const dailyGoal = parseFloat((data.config || {}).daily_goal || '50');
        const goalPct = dailyGoal > 0 ? Math.min(100, (dp / dailyGoal * 100)) : 0;
        goalEl.textContent = `$${dp.toFixed(2)} / $${dailyGoal.toFixed(0)}`;
        goalEl.className = 'bot-stat-value ' + (dp >= dailyGoal ? 'positive' : dp > 0 ? 'positive' : dp < 0 ? 'negative' : '');

        // Open Positions (from broker)
        const posEl = document.getElementById('bot-positions');
        const positions = data.positions || [];
        // Also get open trades from DB for close buttons
        let openTradesMap = {};
        try {
            const otResp = await fetch('/api/bot/trades?status=open');
            const openTrades = await otResp.json();
            openTrades.forEach(t => { openTradesMap[t.coin] = t.id; });
        } catch (_) {}

        if (positions.length === 0) {
            posEl.innerHTML = '<div class="bot-empty">No open positions</div>';
        } else {
            posEl.innerHTML = positions.map(p => {
                const pnlClass = (p.unrealized_pnl || 0) >= 0 ? 'positive' : 'negative';
                const pnlSign = (p.unrealized_pnl || 0) >= 0 ? '+' : '';
                const tradeId = openTradesMap[p.coin];
                const closeBtn = tradeId ? `<button class="btn-close-trade" onclick="closeCryptoTrade(${tradeId})">Close</button>` : '';
                const size = Math.abs(p.size || 0);
                const entry = p.entry_price || 0;
                // For crypto: show margin (cost basis) not full notional
                const initMargin = (p.raw && p.raw.initialMargin) ? parseFloat(p.raw.initialMargin) : 0;
                const leverage = (p.raw && p.raw.leverage) ? parseInt(p.raw.leverage) : 1;
                const posValue = initMargin > 0 ? initMargin : (size * entry / leverage);
                // Map 'net' side to long/short based on position sign
                let side = p.side;
                if (side === 'net') side = (p.size || 0) < 0 ? 'short' : 'long';
                return `<div class="bot-position-row" style="flex-wrap:wrap;">
                    <span class="bot-pos-coin">${p.coin}</span>
                    <span class="bot-pos-side ${side}">${side}</span>
                    <span class="bot-pos-pnl ${pnlClass}">${pnlSign}$${(p.unrealized_pnl || 0).toFixed(2)}</span>
                    ${closeBtn}
                    <div class="bot-pos-details" style="width:100%;">
                        <span class="bot-pos-size">Qty: ${size}</span>
                        <span class="bot-pos-entry">Entry: $${entry.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</span>
                        <span class="bot-pos-value">Margin: $${posValue.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</span>
                    </div>
                </div>`;
            }).join('');
        }

        // Button states
        document.getElementById('btn-bot-start').disabled = data.running;
        document.getElementById('btn-bot-stop').disabled = !data.running;

        if (!data.blofin_configured) {
            document.getElementById('bot-balance').textContent = 'Not configured';
        }
    } catch (e) {
        console.error('Failed to load bot status:', e);
    }
}

async function startBot() {
    try {
        const resp = await fetch('/api/bot/start', {method: 'POST'});
        const data = await resp.json();
        if (!data.ok) alert(data.error || 'Failed to start bot');
        loadBotStatus();
    } catch (e) {
        alert('Failed to start bot: ' + e.message);
    }
}

async function stopBot() {
    try {
        const resp = await fetch('/api/bot/stop', {method: 'POST'});
        await resp.json();
        loadBotStatus();
    } catch (e) {
        alert('Failed to stop bot: ' + e.message);
    }
}

async function killSwitch() {
    if (!confirm('KILL SWITCH: This will stop the bot and close ALL open positions. Continue?')) return;
    try {
        const resp = await fetch('/api/bot/kill', {method: 'POST'});
        await resp.json();
        loadBotStatus();
        loadBotTrades();
    } catch (e) {
        alert('Kill switch failed: ' + e.message);
    }
}

async function loadBotTrades() {
    try {
        let url = '/api/bot/trades?limit=50';
        const assetFilter = document.getElementById('bot-asset-filter');
        if (assetFilter && assetFilter.value) {
            url += '&asset_type=' + encodeURIComponent(assetFilter.value);
        }
        const resp = await fetch(url);
        const trades = await resp.json();
        const tbody = document.getElementById('bot-trades-body');

        if (!trades.length) {
            tbody.innerHTML = '<tr><td colspan="10" class="bot-empty">No trades yet</td></tr>';
            return;
        }

        tbody.innerHTML = trades.map(t => {
            const pnlClass = t.pnl > 0 ? 'positive' : t.pnl < 0 ? 'negative' : '';
            const pnlStr = t.pnl != null ? (t.pnl >= 0 ? '+' : '') + '$' + t.pnl.toFixed(2) : '—';
            const exitStr = t.exit_price != null ? '$' + t.exit_price.toLocaleString() : '—';
            const entryStr = t.entry_price != null ? '$' + t.entry_price.toLocaleString() : '—';
            const statusClass = t.status === 'open' ? 'status-open' : t.status === 'closed' ? 'status-closed' : 'status-killed';
            const dt = t.opened_at ? new Date(t.opened_at).toLocaleString() : '';
            const stratStr = t.strategy || '—';
            const closeBtn = t.status === 'open' ? `<button class="btn-close-trade" onclick="closeCryptoTrade(${t.id})">Close</button>` : '';
            return `<tr>
                <td>${t.id}</td>
                <td>${t.coin}</td>
                <td class="side-${t.side}">${t.side}</td>
                <td>${stratStr}</td>
                <td>${entryStr}</td>
                <td>${exitStr}</td>
                <td class="${pnlClass}">${pnlStr}</td>
                <td><span class="${statusClass}">${t.status}</span></td>
                <td>${dt}</td>
                <td>${closeBtn}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        console.error('Failed to load bot trades:', e);
    }
}

async function loadTrending() {
    const feed = document.getElementById('bot-trending');
    const btn = document.getElementById('btn-refresh-trending');
    btn.disabled = true;
    btn.textContent = 'Scanning...';
    feed.innerHTML = `
        <div class="bot-trending-loading">
            <div class="spinner" style="width:18px;height:18px;margin:0 auto 8px;"></div>
            <div>Scanning 24 coins — analyzing with AI...</div>
        </div>`;

    try {
        const resp = await fetch('/api/bot/trending');
        const data = await resp.json();

        if (data.error) {
            feed.innerHTML = `<div class="bot-empty">${data.error}</div>`;
            btn.disabled = false;
            btn.textContent = 'Refresh';
            return;
        }

        const analysis = data.analysis || {};
        const trending = analysis.trending || [];
        const mood = analysis.market_mood || 'unknown';
        const summary = analysis.summary || '';
        const mktData = data.market_data || [];

        // Fetch current selected coins to show "In Bot" badges
        let currentSelected = [];
        try {
            const coinsResp = await fetch('/api/bot/coins');
            const coinsData = await coinsResp.json();
            currentSelected = coinsData.selected || [];
        } catch(e) {}

        let html = '';

        // Market mood badge
        const moodColors = {bullish: 'var(--accent-green)', bearish: 'var(--accent-red)', neutral: 'var(--text-secondary)', mixed: 'var(--accent-orange)'};
        html += `<div class="trending-mood">
            <span class="mood-badge" style="color:${moodColors[mood] || 'var(--text-secondary)'}">Market: ${mood}</span>
            <span class="mood-time">${data.timestamp || ''}</span>
        </div>`;

        // Summary
        if (summary) {
            html += `<div class="trending-summary">${summary}</div>`;
        }

        // Trending coins
        if (trending.length > 0) {
            html += '<div class="trending-coins">';
            trending.forEach(t => {
                const signalClass = t.signal === 'hot' ? 'signal-hot' : t.signal === 'warming' ? 'signal-warm' : 'signal-vol';
                const signalLabel = t.signal === 'hot' ? 'HOT' : t.signal === 'warming' ? 'WARMING' : 'VOL';
                // Find price data
                const md = mktData.find(m => m.ticker === t.ticker || m.coin === t.coin);
                const priceStr = md ? `$${md.price.toLocaleString()}` : '';
                const chgStr = md ? `${md.chg_2h >= 0 ? '+' : ''}${md.chg_2h}%` : '';
                const chgClass = md && md.chg_2h >= 0 ? 'positive' : 'negative';
                const volStr = md && md.vol_surge > 1.2 ? `${md.vol_surge}x vol` : '';

                const tTicker = t.ticker || (t.coin ? t.coin.toUpperCase().replace(/\s+/g,'') + '-USDT' : '');
                const isSelected = (data.auto_added_coins || []).includes(tTicker) || (currentSelected || []).includes(tTicker);
                const addBtnHtml = isSelected
                    ? '<span class="vol-added-badge" style="font-size:10px;margin-left:auto;">In Bot</span>'
                    : `<button class="btn-vol-add" style="font-size:10px;margin-left:auto;padding:2px 8px;" onclick="addTrendingCoin('${tTicker}', this)">+ Add to Bot</button>`;

                html += `<div class="trending-coin-row">
                    <div class="trending-coin-header" style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                        <span class="trending-coin-name">${t.coin}</span>
                        <span class="trending-signal ${signalClass}">${signalLabel}</span>
                        ${priceStr ? `<span class="trending-price">${priceStr}</span>` : ''}
                        ${chgStr ? `<span class="trending-chg ${chgClass}">${chgStr}</span>` : ''}
                        ${volStr ? `<span class="trending-vol">${volStr}</span>` : ''}
                        ${addBtnHtml}
                    </div>
                    <div class="trending-reason">${t.reason}</div>
                </div>`;
            });
            html += '</div>';
        }

        // Auto-added coins notification
        const autoAdded = data.auto_added_coins || [];
        if (autoAdded.length > 0) {
            html += `<div class="trending-auto-added" style="background:rgba(38,166,154,0.1);border:1px solid var(--accent-green);border-radius:6px;padding:8px 12px;margin:8px 0;font-size:12px;">
                <strong style="color:var(--accent-green);">Auto-added to bot:</strong>
                ${autoAdded.map(c => `<span style="color:var(--text-primary);margin-left:6px;">${c}</span>`).join(',')}
                <span style="color:var(--text-secondary);margin-left:6px;">(high volume)</span>
            </div>`;
        }

        // Top movers mini-table (price data)
        if (mktData.length > 0) {
            html += '<div class="trending-movers-title">Top Movers (2h)</div>';
            html += '<div class="trending-movers">';
            mktData.slice(0, 8).forEach(m => {
                const chgClass = m.chg_2h >= 0 ? 'positive' : 'negative';
                const volBadge = m.vol_surge >= 1.5 ? `<span class="vol-spike">${m.vol_surge}x</span>` : '';
                const mTicker = m.ticker || '';
                const mInBot = currentSelected.includes(mTicker) || (autoAdded || []).includes(mTicker);
                const mAddBtn = mInBot
                    ? '<span class="vol-added-badge" style="font-size:10px;">In Bot</span>'
                    : `<button class="btn-vol-add" style="font-size:10px;padding:1px 6px;" onclick="addTrendingCoin('${mTicker}', this)">+ Add</button>`;
                html += `<div class="trending-mover" style="display:flex;align-items:center;gap:6px;">
                    <span class="mover-name">${m.coin}</span>
                    <span class="mover-chg ${chgClass}">${m.chg_2h >= 0 ? '+' : ''}${m.chg_2h}%</span>
                    ${volBadge}
                    ${mAddBtn}
                </div>`;
            });
            html += '</div>';
        }

        feed.innerHTML = html;
        loadBillingStatus(); // Refresh usage gauge after LLM call

        // Refresh coin picker if coins were auto-added
        if (autoAdded.length > 0) {
            loadBotCoins();
        }
    } catch (e) {
        feed.innerHTML = `<div class="bot-empty">Failed to load trending: ${e.message}</div>`;
    }

    btn.disabled = false;
    btn.textContent = 'Refresh';
}

async function addTrendingCoin(ticker, btnEl) {
    if (!ticker) return;
    btnEl.disabled = true;
    btnEl.textContent = '...';
    try {
        const resp = await fetch('/api/bot/coins/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ticker}),
        });
        const data = await resp.json();
        if (data.ok) {
            btnEl.outerHTML = '<span class="vol-added-badge" style="font-size:10px;">In Bot</span>';
            loadBotCoins();
        } else {
            btnEl.textContent = 'Error';
        }
    } catch (e) {
        btnEl.textContent = '+ Add';
        btnEl.disabled = false;
    }
}

async function loadBotPnl() {
    try {
        const resp = await fetch('/api/bot/pnl?days=30');
        const data = await resp.json();

        const chartContainer = document.getElementById('bot-pnl-chart');
        if (!data.length) {
            chartContainer.innerHTML = '<div class="bot-empty" style="padding-top:80px;">No P&L data yet</div>';
            return;
        }

        // Destroy old chart
        if (botPnlChart) {
            botPnlChart.remove();
            botPnlChart = null;
        }

        chartContainer.innerHTML = '';
        botPnlChart = LightweightCharts.createChart(chartContainer, {
            width: chartContainer.clientWidth,
            height: 250,
            layout: {
                background: {type: 'solid', color: '#1e222d'},
                textColor: '#787b86',
            },
            grid: {
                vertLines: {color: '#2a2e39'},
                horzLines: {color: '#2a2e39'},
            },
            rightPriceScale: {borderColor: '#363a45'},
            timeScale: {borderColor: '#363a45'},
        });

        // Cumulative P&L as area chart
        let cumulative = 0;
        const seriesData = data.map(d => {
            cumulative += d.total_pnl;
            return {time: d.date, value: parseFloat(cumulative.toFixed(2))};
        });

        botPnlSeries = botPnlChart.addAreaSeries({
            lineColor: cumulative >= 0 ? '#26a69a' : '#ef5350',
            topColor: cumulative >= 0 ? 'rgba(38,166,154,0.3)' : 'rgba(239,83,80,0.3)',
            bottomColor: cumulative >= 0 ? 'rgba(38,166,154,0.05)' : 'rgba(239,83,80,0.05)',
            lineWidth: 2,
        });
        botPnlSeries.setData(seriesData);
        botPnlChart.timeScale().fitContent();

        // Resize observer
        const ro = new ResizeObserver(() => {
            if (botPnlChart) botPnlChart.applyOptions({width: chartContainer.clientWidth});
        });
        ro.observe(chartContainer);
    } catch (e) {
        console.error('Failed to load bot P&L:', e);
    }
}

function _formatPnl(val) {
    const sign = val >= 0 ? '+' : '';
    return sign + '$' + val.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function _setPnlEl(id, data) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = _formatPnl(data.pnl);
    el.className = 'pnl-period-value ' + (data.pnl >= 0 ? 'positive' : 'negative');
    // Add trade count tooltip
    const winRate = data.trades > 0 ? ((data.wins / data.trades) * 100).toFixed(0) : 0;
    el.title = `${data.trades} trades, ${winRate}% win rate`;
}

async function loadBotPnlSummary() {
    try {
        const resp = await fetch('/api/bot/pnl-summary');
        const data = await resp.json();
        _setPnlEl('pnl-day', data.day);
        _setPnlEl('pnl-week', data.week);
        _setPnlEl('pnl-month', data.month);
        _setPnlEl('pnl-year', data.year);
        _setPnlEl('pnl-all', data.all);
    } catch (e) {
        console.error('Failed to load P&L summary:', e);
    }
}

async function loadBotDashboard() {
    const container = document.getElementById('bot-report-dashboard');
    if (!container) return;

    try {
        const resp = await fetch('/api/bot/dashboard?asset=crypto');
        if (!resp.ok) {
            const text = await resp.text();
            try { const err = JSON.parse(text); container.innerHTML = `<div class="bot-empty">${err.error || 'Access denied'}</div>`; } catch(e) { container.innerHTML = '<div class="bot-empty">Login required for dashboard</div>'; }
            return;
        }
        const d = await resp.json();
        renderNarrativeDashboard(container, d, 'crypto');
    } catch (e) {
        container.innerHTML = `<div class="bot-empty">Failed to load dashboard: ${e.message}</div>`;
    }
}

async function loadTaxEstimate() {
    const valEl = document.getElementById('dash-tax-value');
    const subEl = document.getElementById('dash-tax-sub');
    if (!valEl) return;
    valEl.textContent = '...';
    subEl.textContent = 'Calculating...';
    try {
        const resp = await fetch('/api/bot/tax-estimate');
        const d = await resp.json();
        const tax = d.tax || {};
        const total = d.total || {};
        if (total.net_gains > 0) {
            valEl.textContent = '$' + tax.estimated_tax.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
            valEl.style.color = 'var(--accent-orange)';
            subEl.innerHTML = `${tax.bracket} on $${total.net_gains.toLocaleString(undefined, {maximumFractionDigits: 0})} gains` +
                (tax.niit > 0 ? ` (incl $${tax.niit.toFixed(0)} NIIT)` : '');
        } else if (total.net_gains < 0) {
            valEl.textContent = '$0.00';
            valEl.style.color = 'var(--accent-green)';
            const deduction = tax.loss_deduction || 0;
            subEl.innerHTML = `Net loss: $${Math.abs(total.net_gains).toFixed(0)}` +
                (deduction > 0 ? ` &middot; $${deduction.toFixed(0)} deductible` : '') +
                (tax.loss_carryover > 0 ? ` &middot; $${tax.loss_carryover.toFixed(0)} carryover` : '');
        } else {
            valEl.textContent = '$0.00';
            valEl.style.color = 'var(--text-secondary)';
            subEl.textContent = 'No realized gains yet';
        }
    } catch (e) {
        valEl.textContent = 'Error';
        subEl.textContent = e.message;
    }
}

async function loadStockBotPnlSummary() {
    try {
        const resp = await fetch('/api/stock-bot/pnl-summary');
        const data = await resp.json();
        _setPnlEl('spnl-day', data.day);
        _setPnlEl('spnl-week', data.week);
        _setPnlEl('spnl-month', data.month);
        _setPnlEl('spnl-year', data.year);
        _setPnlEl('spnl-all', data.all);
    } catch (e) {
        console.error('Failed to load stock P&L summary:', e);
    }
}

async function loadBotLog() {
    try {
        const resp = await fetch('/api/bot/log?limit=50');
        const logs = await resp.json();
        const logEl = document.getElementById('bot-log');

        if (!logs.length) {
            logEl.innerHTML = '<div class="bot-empty">No activity yet</div>';
            return;
        }

        logEl.innerHTML = logs.map(l => {
            const levelClass = l.level === 'error' ? 'log-error' : l.level === 'warn' ? 'log-warn' : 'log-info';
            const time = l.created_at ? new Date(l.created_at).toLocaleTimeString() : '';
            return `<div class="bot-log-entry ${levelClass}">
                <span class="bot-log-time">${time}</span>
                <span class="bot-log-level">[${l.level}]</span>
                <span class="bot-log-msg">${l.message}</span>
            </div>`;
        }).join('');
    } catch (e) {
        console.error('Failed to load bot log:', e);
    }
}

async function loadBotConfig() {
    try {
        const resp = await fetch('/api/bot/config');
        const cfg = await resp.json();
        const el = (id) => document.getElementById(id);
        if (cfg.daily_goal) el('bot-cfg-daily-goal').value = cfg.daily_goal;
        if (cfg.max_position_pct) el('bot-cfg-max-pct').value = cfg.max_position_pct;
        if (cfg.daily_loss_limit) el('bot-cfg-loss-limit').value = cfg.daily_loss_limit;
        if (cfg.max_open_positions) el('bot-cfg-max-open').value = cfg.max_open_positions;
        if (cfg.max_daily_trades) el('bot-cfg-max-daily-trades').value = cfg.max_daily_trades;
        if (cfg.scan_interval_sec) el('bot-cfg-interval').value = cfg.scan_interval_sec;
        if (cfg.platform) el('bot-cfg-platform').value = cfg.platform;
        if (cfg.trading_mode) el('bot-cfg-trading-mode').value = cfg.trading_mode;
        if (cfg.trade_mode) el('bot-cfg-trade-mode').value = cfg.trade_mode;
        if (cfg.direction_bias) el('bot-cfg-direction-bias').value = cfg.direction_bias;
        if (cfg.quick_trade_mode) el('bot-cfg-quick-trade').value = cfg.quick_trade_mode;
        updateTradingModeNote();
    } catch (e) {
        console.error('Failed to load bot config:', e);
    }
}

async function loadBotCoins() {
    try {
        const resp = await fetch('/api/bot/coins');
        const data = await resp.json();
        const picker = document.getElementById('bot-coin-picker');

        let html = data.coins.map(c => `
            <label class="bot-coin-chip ${c.selected ? 'selected' : ''} ${c.custom ? 'custom' : ''}">
                <input type="checkbox" value="${c.key}" ${c.selected ? 'checked' : ''} onchange="this.parentElement.classList.toggle('selected', this.checked)">
                <span>${c.name}</span>
                ${c.custom ? '<span class="chip-remove" onclick="event.preventDefault();removeCustomCoin(this)" title="Remove">&times;</span>' : ''}
            </label>
        `).join('');

        html += `<div class="bot-add-coin-row" style="display:flex;gap:6px;align-items:center;margin-top:8px;width:100%;">
            <input type="text" id="bot-custom-coin" placeholder="Add coin (e.g. SUI, PEPE, ARB)"
                style="flex:1;background:var(--bg-primary);border:1px solid var(--border-color);border-radius:6px;padding:6px 10px;color:var(--text-primary);font-size:12px;"
                onkeydown="if(event.key==='Enter'){event.preventDefault();addCustomCoin();}">
            <button onclick="addCustomCoin()" style="background:var(--accent-green);color:#000;border:none;border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">+ Add</button>
        </div>`;

        picker.innerHTML = html;
    } catch (e) {
        console.error('Failed to load bot coins:', e);
    }
}

async function addCustomCoin() {
    const input = document.getElementById('bot-custom-coin');
    const ticker = (input.value || '').trim().toUpperCase();
    if (!ticker) return;

    input.disabled = true;
    try {
        const resp = await fetch('/api/bot/coins/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ticker}),
        });
        const data = await resp.json();
        if (data.ok) {
            input.value = '';
            loadBotCoins();
        } else {
            alert(data.error || 'Failed to add coin');
        }
    } catch (e) {
        alert('Failed to add coin: ' + e.message);
    }
    input.disabled = false;
    input.focus();
}

async function removeCustomCoin(el) {
    const chip = el.closest('.bot-coin-chip');
    const cb = chip.querySelector('input[type="checkbox"]');
    cb.checked = false;
    chip.classList.remove('selected');
    chip.style.display = 'none';
}

async function updateBotCoins() {
    const checkboxes = document.querySelectorAll('#bot-coin-picker input[type="checkbox"]');
    const selected = Array.from(checkboxes).filter(cb => cb.checked).map(cb => cb.value);

    if (selected.length === 0) {
        alert('Select at least one coin.');
        return;
    }

    try {
        const resp = await fetch('/api/bot/coins', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({selected}),
        });
        const data = await resp.json();
        if (data.ok) {
            const btn = document.querySelector('.btn-bot-save-coins');
            btn.textContent = 'Saved!';
            btn.style.background = 'var(--accent-green)';
            setTimeout(() => { btn.textContent = 'Save Coins'; btn.style.background = ''; }, 1500);
        } else {
            alert(data.error || 'Failed to save coins');
        }
    } catch (e) {
        alert('Failed to save coins: ' + e.message);
    }
}

async function loadTopVolume() {
    const container = document.getElementById('bot-top-volume');
    const btn = document.getElementById('btn-refresh-volume');
    btn.disabled = true;
    btn.textContent = 'Loading...';
    container.innerHTML = '<div class="bot-trending-loading"><div class="spinner" style="width:18px;height:18px;margin:0 auto 8px;"></div><div>Fetching volume data...</div></div>';

    try {
        const resp = await fetch('/api/bot/top-volume');
        const data = await resp.json();

        if (data.error) {
            container.innerHTML = `<div class="bot-empty">${data.error}</div>`;
            btn.disabled = false;
            btn.textContent = 'Refresh';
            return;
        }

        const coins = data.coins || [];
        if (coins.length === 0) {
            container.innerHTML = '<div class="bot-empty">No volume data available</div>';
            btn.disabled = false;
            btn.textContent = 'Refresh';
            return;
        }

        let html = '<div class="top-volume-list">';
        coins.forEach((c, i) => {
            const chgClass = c.chg_24h >= 0 ? 'positive' : 'negative';
            const chgStr = `${c.chg_24h >= 0 ? '+' : ''}${c.chg_24h}%`;
            const volStr = c.vol_24h >= 1e9 ? `${(c.vol_24h / 1e9).toFixed(1)}B` : c.vol_24h >= 1e6 ? `${(c.vol_24h / 1e6).toFixed(0)}M` : c.vol_24h.toLocaleString();
            const addBtn = c.selected
                ? '<span class="vol-added-badge">Added</span>'
                : `<button class="btn-vol-add" onclick="addVolumeCoin('${c.ticker}', this)">+ Add</button>`;

            html += `<div class="top-volume-row">
                <span class="vol-rank">${i + 1}</span>
                <span class="vol-name">${c.coin}</span>
                <span class="vol-price">$${c.price.toLocaleString()}</span>
                <span class="vol-chg ${chgClass}">${chgStr}</span>
                <span class="vol-amount">$${volStr}</span>
                ${addBtn}
            </div>`;
        });
        html += '</div>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="bot-empty">Failed: ${e.message}</div>`;
    }

    btn.disabled = false;
    btn.textContent = 'Refresh';
}

async function addVolumeCoin(ticker, btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = '...';

    try {
        // Get current selected coins
        const resp = await fetch('/api/bot/coins');
        const data = await resp.json();
        const selected = data.selected || [];

        if (selected.includes(ticker)) {
            btnEl.outerHTML = '<span class="vol-added-badge">Added</span>';
            return;
        }

        selected.push(ticker);
        const saveResp = await fetch('/api/bot/coins', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({selected}),
        });
        const saveData = await saveResp.json();

        if (saveData.ok) {
            btnEl.outerHTML = '<span class="vol-added-badge">Added</span>';
            loadBotCoins(); // Refresh the coin picker
        } else {
            btnEl.textContent = 'Error';
            btnEl.disabled = false;
        }
    } catch (e) {
        btnEl.textContent = '+ Add';
        btnEl.disabled = false;
    }
}

async function updateBotConfig() {
    try {
        const payload = {
            daily_goal: document.getElementById('bot-cfg-daily-goal').value,
            max_position_pct: document.getElementById('bot-cfg-max-pct').value,
            daily_loss_limit: document.getElementById('bot-cfg-loss-limit').value,
            max_open_positions: document.getElementById('bot-cfg-max-open').value,
            max_daily_trades: document.getElementById('bot-cfg-max-daily-trades').value,
            scan_interval_sec: document.getElementById('bot-cfg-interval').value,
            trading_mode: document.getElementById('bot-cfg-trading-mode').value,
            trade_mode: document.getElementById('bot-cfg-trade-mode').value,
            direction_bias: document.getElementById('bot-cfg-direction-bias').value,
            quick_trade_mode: document.getElementById('bot-cfg-quick-trade').value,
        };
        const resp = await fetch('/api/bot/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data.ok) {
            // Flash save button green briefly
            const btn = document.querySelector('.btn-bot-save-config');
            btn.textContent = 'Saved!';
            btn.style.background = 'var(--accent-green)';
            setTimeout(() => {
                btn.textContent = 'Save Settings';
                btn.style.background = '';
            }, 1500);
        }
    } catch (e) {
        alert('Failed to save config: ' + e.message);
    }
}

const PLATFORM_NOTES = {
    blofin: 'Paper trading on BloFin demo API — no real funds',
    bybit: 'Testnet trading on Bybit — no real funds (coming soon)',
    okx: 'Demo trading on OKX — no real funds (coming soon)',
    binance: 'Testnet trading on Binance — no real funds (coming soon)',
};

function onPlatformChange() {
    const platform = document.getElementById('bot-cfg-platform').value;
    document.getElementById('bot-platform-note').textContent = PLATFORM_NOTES[platform] || '';
    // Show/hide passphrase field (BloFin needs it, others may not)
    const passRow = document.getElementById('bot-cfg-passphrase').closest('.bot-config-row');
    passRow.style.display = platform === 'blofin' ? 'flex' : 'none';
}

async function loadPlatformStatus() {
    try {
        const resp = await fetch('/api/bot/platform');
        const data = await resp.json();
        const statusEl = document.getElementById('bot-key-status');

        if (data.configured) {
            statusEl.innerHTML = '<span class="key-status-ok">Keys configured</span>';
        } else if (data.has_key || data.has_secret) {
            statusEl.innerHTML = '<span class="key-status-partial">Partially configured — missing fields</span>';
        } else {
            statusEl.innerHTML = '<span class="key-status-none">No API keys set</span>';
        }

        if (data.platform) {
            document.getElementById('bot-cfg-platform').value = data.platform;
            onPlatformChange();
        }
    } catch (e) {
        console.error('Failed to load platform status:', e);
    }
}

async function savePlatformKeys() {
    const platform = document.getElementById('bot-cfg-platform').value;
    const apiKey = document.getElementById('bot-cfg-api-key').value;
    const apiSecret = document.getElementById('bot-cfg-api-secret').value;
    const passphrase = document.getElementById('bot-cfg-passphrase').value;

    if (!apiKey && !apiSecret && !passphrase) {
        alert('Enter at least one field to save.');
        return;
    }

    try {
        const resp = await fetch('/api/bot/platform', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({platform, api_key: apiKey, api_secret: apiSecret, passphrase}),
        });
        const data = await resp.json();
        if (data.ok) {
            const btn = document.querySelector('.btn-bot-save-keys');
            btn.textContent = 'Saved!';
            btn.style.background = 'var(--accent-green)';
            setTimeout(() => { btn.textContent = 'Save Keys'; btn.style.background = ''; }, 1500);
            // Clear inputs after save
            document.getElementById('bot-cfg-api-key').value = '';
            document.getElementById('bot-cfg-api-secret').value = '';
            document.getElementById('bot-cfg-passphrase').value = '';
            loadPlatformStatus();
        } else {
            alert(data.error || 'Failed to save keys');
        }
    } catch (e) {
        alert('Failed to save keys: ' + e.message);
    }
}

async function testPlatformConnection() {
    const btn = document.querySelector('.btn-bot-test-keys');
    const statusEl = document.getElementById('bot-key-status');
    btn.disabled = true;
    btn.textContent = 'Testing...';
    statusEl.innerHTML = '<span class="key-status-testing">Connecting...</span>';

    try {
        const resp = await fetch('/api/bot/test-connection', {method: 'POST'});
        const data = await resp.json();
        if (data.ok) {
            statusEl.innerHTML = `<span class="key-status-ok">${data.message}</span>`;
        } else {
            statusEl.innerHTML = `<span class="key-status-error">${data.error}</span>`;
        }
    } catch (e) {
        statusEl.innerHTML = `<span class="key-status-error">Connection failed: ${e.message}</span>`;
    }

    btn.disabled = false;
    btn.textContent = 'Test Connection';
}

function updateTradingModeNote() {
    const mode = document.getElementById('bot-cfg-trading-mode').value;
    const note = document.getElementById('bot-trading-mode-note');
    const platform = document.getElementById('bot-cfg-platform').value;
    if (!note) return;
    const notes = {
        'blofin':  {futures: 'BloFin demo only supports futures (perpetual swaps)', spot: 'BloFin demo does not support spot trading — using futures'},
        'bybit':   {futures: 'Bybit testnet futures trading', spot: 'Bybit testnet spot trading'},
        'okx':     {futures: 'OKX demo futures trading', spot: 'OKX demo spot trading'},
        'binance': {futures: 'Binance testnet futures trading', spot: 'Binance testnet spot trading'},
    };
    const pn = notes[platform] || notes['blofin'];
    note.textContent = pn[mode] || pn.futures;
}

async function checkTradePermissions() {
    const statusEl = document.getElementById('bot-permission-status');
    statusEl.innerHTML = '<span class="key-status-testing">Checking permissions...</span>';
    try {
        const resp = await fetch('/api/bot/check-permissions', {method: 'POST'});
        const data = await resp.json();
        if (data.has_trade_permission) {
            statusEl.innerHTML = `<span class="key-status-ok">Trade permission OK (uid: ${data.uid})</span>`;
        } else {
            statusEl.innerHTML = `<span class="key-status-error">${data.message}</span>`;
        }
    } catch (e) {
        statusEl.innerHTML = `<span class="key-status-error">Check failed: ${e.message}</span>`;
    }
}

async function placeTestTrade() {
    const coin = document.getElementById('bot-test-coin').value;
    const side = document.getElementById('bot-test-side').value;
    const resultEl = document.getElementById('bot-test-trade-result');
    const btn = document.querySelector('.btn-bot-test-trade');

    btn.disabled = true;
    btn.textContent = 'Placing...';
    resultEl.innerHTML = '<span class="key-status-testing">Sending order...</span>';

    try {
        const resp = await fetch('/api/bot/test-trade', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({coin, side}),
        });
        const data = await resp.json();
        if (data.ok) {
            resultEl.innerHTML = `<span class="key-status-ok">${data.message}</span>`;
        } else {
            resultEl.innerHTML = `<span class="key-status-error">${data.error}</span>`;
        }
    } catch (e) {
        resultEl.innerHTML = `<span class="key-status-error">Failed: ${e.message}</span>`;
    }

    btn.disabled = false;
    btn.textContent = 'Place Test Trade (Min Size)';
}


// ─── Close Trade Functions ───────────────────────────────────

async function closeCryptoTrade(tradeId) {
    if (!confirm(`Close trade #${tradeId}? This will close the position at market price.`)) return;
    try {
        const resp = await fetch(`/api/bot/trades/${tradeId}/close`, {method: 'POST'});
        const data = await resp.json();
        if (data.ok) {
            loadBotTrades();
            loadBotStatus();
        } else {
            alert(data.error || 'Failed to close trade');
        }
    } catch (e) {
        alert('Failed to close trade: ' + e.message);
    }
}

