async function loadStockTopMovers() {
    const container = document.getElementById('sbot-top-movers');
    const btn = document.getElementById('btn-sbot-refresh-movers');
    if (btn) { btn.disabled = true; btn.textContent = 'Loading...'; }
    container.innerHTML = '<div class="bot-trending-loading"><div class="spinner" style="width:18px;height:18px;margin:0 auto 8px;"></div><div>Scanning top movers...</div></div>';

    try {
        const resp = await fetch('/api/stock-bot/top-movers');
        const data = await resp.json();
        if (data.error) {
            container.innerHTML = `<div class="bot-empty">${data.error}</div>`;
        } else {
            const stocks = data.stocks || [];
            if (stocks.length === 0) {
                container.innerHTML = '<div class="bot-empty">No mover data</div>';
            } else {
                let html = '<div class="top-volume-list">';
                stocks.forEach((s, i) => {
                    const chgClass = s.chg_pct >= 0 ? 'positive' : 'negative';
                    const chgStr = `${s.chg_pct >= 0 ? '+' : ''}${s.chg_pct}%`;
                    const volStr = s.vol_usd >= 1e9 ? `$${(s.vol_usd / 1e9).toFixed(1)}B` : s.vol_usd >= 1e6 ? `$${(s.vol_usd / 1e6).toFixed(0)}M` : `$${s.vol_usd.toLocaleString()}`;
                    const addBtn = s.selected
                        ? '<span class="vol-added-badge">Added</span>'
                        : `<button class="btn-vol-add" onclick="addStockMover('${s.symbol}', this)">+ Add</button>`;
                    html += `<div class="top-volume-row">
                        <span class="vol-rank">${i + 1}</span>
                        <span class="vol-name">${s.symbol}</span>
                        <span class="vol-price">$${s.price.toLocaleString()}</span>
                        <span class="vol-chg ${chgClass}">${chgStr}</span>
                        <span class="vol-amount">${volStr}</span>
                        ${addBtn}
                    </div>`;
                });
                html += '</div>';
                container.innerHTML = html;
            }
        }
    } catch (e) {
        container.innerHTML = `<div class="bot-empty">Failed: ${e.message}</div>`;
    }
    if (btn) { btn.disabled = false; btn.textContent = 'Refresh'; }
}

async function addStockMover(symbol, btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = '...';
    try {
        const resp = await fetch('/api/stock-bot/stocks/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ symbol }),
        });
        const data = await resp.json();
        if (data.valid) {
            btnEl.textContent = 'Added';
            btnEl.className = 'vol-added-badge';
            loadStockBotStocks();
        } else {
            btnEl.textContent = data.error || 'Error';
            setTimeout(() => { btnEl.textContent = '+ Add'; btnEl.disabled = false; }, 2000);
        }
    } catch (e) {
        btnEl.textContent = 'Error';
        setTimeout(() => { btnEl.textContent = '+ Add'; btnEl.disabled = false; }, 2000);
    }
}

async function placeStockTestTrade() {
    const symbol = (document.getElementById('sbot-test-symbol').value || 'AAPL').toUpperCase();
    const side = document.getElementById('sbot-test-side').value;
    const qty = parseInt(document.getElementById('sbot-test-qty').value) || 1;
    const extended = document.getElementById('sbot-test-extended').checked;
    const resultEl = document.getElementById('sbot-test-result');

    if (!confirm(`Place TEST trade: ${side.toUpperCase()} ${qty}x ${symbol}?`)) return;

    resultEl.innerHTML = '<span class="key-status-testing">Placing order...</span>';

    try {
        const resp = await fetch('/api/stock-bot/test-trade', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ symbol, side, qty, extended_hours: extended }),
        });
        const data = await resp.json();
        if (data.ok) {
            resultEl.innerHTML = `<span class="key-status-ok">${data.message}</span>`;
            loadStockBotStatus();
            loadStockBotTrades();
        } else {
            resultEl.innerHTML = `<span class="key-status-error">${data.error}</span>`;
        }
    } catch (e) {
        resultEl.innerHTML = `<span class="key-status-error">Failed: ${e.message}</span>`;
    }
}

async function loadStockDashboard() {
    const container = document.getElementById('sbot-report-dashboard');
    if (!container) return;

    try {
        const resp = await fetch('/api/bot/dashboard?asset=stock');
        if (!resp.ok) {
            const text = await resp.text();
            try { const err = JSON.parse(text); container.innerHTML = `<div class="bot-empty">${err.error || 'Access denied'}</div>`; } catch(e) { container.innerHTML = '<div class="bot-empty">Login required</div>'; }
            return;
        }
        const d = await resp.json();
        renderNarrativeDashboard(container, d, 'stock');
    } catch (e) {
        container.innerHTML = `<div class="bot-empty">Failed to load dashboard: ${e.message}</div>`;
    }
}

async function closeStockTrade(tradeId) {
    if (!confirm(`Close stock trade #${tradeId}? This will close the position at market price.`)) return;
    try {
        const resp = await fetch(`/api/stock-bot/trades/${tradeId}/close`, {method: 'POST'});
        const data = await resp.json();
        if (data.ok) {
            loadStockBotTrades();
            loadStockBotStatus();
        } else {
            alert(data.error || 'Failed to close trade');
        }
    } catch (e) {
        alert('Failed to close trade: ' + e.message);
    }
}

// ─── Stock Trading Bot Functions ────────────────────────────

let stockBotPnlChart = null;
let stockBotPnlSeries = null;

async function loadStockBotStatus() {
    try {
        const resp = await fetch('/api/stock-bot/status');
        const data = await resp.json();

        // Status indicator
        const dot = document.getElementById('sbot-dot');
        const statusText = document.getElementById('sbot-status-text');
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
        const sSensorEl = document.getElementById('sbot-sensor-status');
        if (sSensorEl) {
            const sensor = data.market_sensor;
            if (sensor) {
                const colors = { HEALTHY: '#26a69a', CAUTION: '#ff9800', DANGER: '#ef5350' };
                sSensorEl.textContent = sensor.status + (sensor.cached ? ' (cached)' : '');
                sSensorEl.style.color = colors[sensor.status] || '';
                sSensorEl.title = sensor.reasoning || '';
            } else {
                sSensorEl.textContent = 'N/A';
                sSensorEl.style.color = '';
            }
        }

        // Market hours indicator
        const market = data.market || {};
        const marketDot = document.getElementById('sbot-market-dot');
        const marketText = document.getElementById('sbot-market-text');
        if (market.is_open) {
            marketDot.className = 'bot-dot running';
            marketText.textContent = 'Open';
        } else if (market.status === 'pre_market') {
            marketDot.className = 'bot-dot stopped';
            marketText.textContent = 'Pre-Market';
            marketText.style.color = 'var(--accent-orange)';
        } else if (market.status === 'after_hours') {
            marketDot.className = 'bot-dot stopped';
            marketText.textContent = 'After Hours';
            marketText.style.color = 'var(--accent-orange)';
        } else {
            marketDot.className = 'bot-dot stopped';
            marketText.textContent = 'Closed';
            marketText.style.color = '';
        }

        // Balance
        const bal = data.balance || {};
        document.getElementById('sbot-balance').textContent = '$' + (bal.total_equity || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});

        // Daily P&L
        const dailyEl = document.getElementById('sbot-daily-pnl');
        const dp = data.daily_pnl || 0;
        dailyEl.textContent = (dp >= 0 ? '+' : '') + '$' + dp.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
        dailyEl.className = 'bot-stat-value ' + (dp >= 0 ? 'positive' : 'negative');

        // Daily Goal progress
        const goalEl = document.getElementById('sbot-daily-goal-progress');
        const dailyGoal = parseFloat((data.config || {}).stock_daily_goal || '50');
        goalEl.textContent = `$${dp.toFixed(2)} / $${dailyGoal.toFixed(0)}`;
        goalEl.className = 'bot-stat-value ' + (dp >= dailyGoal ? 'positive' : dp > 0 ? 'positive' : dp < 0 ? 'negative' : '');

        // Open Positions (from broker)
        const posEl = document.getElementById('sbot-positions');
        const positions = data.positions || [];
        // Also get open stock trades from DB for close buttons
        let openStockTradesMap = {};
        try {
            const otResp = await fetch('/api/stock-bot/trades?status=open');
            const openTrades = await otResp.json();
            openTrades.forEach(t => { openStockTradesMap[t.coin] = t.id; });
        } catch (_) {}

        if (positions.length === 0) {
            posEl.innerHTML = '<div class="bot-empty">No open positions</div>';
        } else {
            posEl.innerHTML = positions.map(p => {
                const pnlClass = (p.unrealized_pnl || 0) >= 0 ? 'positive' : 'negative';
                const pnlSign = (p.unrealized_pnl || 0) >= 0 ? '+' : '';
                const tradeId = openStockTradesMap[p.coin];
                const closeBtn = tradeId ? `<button class="btn-close-trade" onclick="closeStockTrade(${tradeId})">Close</button>` : '';
                const size = Math.abs(p.size || 0);
                const entry = p.entry_price || 0;
                const mktVal = p.market_value || (size * entry);
                return `<div class="bot-position-row" style="flex-wrap:wrap;">
                    <span class="bot-pos-coin">${p.coin}</span>
                    <span class="bot-pos-side ${p.side}">${p.side}</span>
                    <span class="bot-pos-pnl ${pnlClass}">${pnlSign}$${(p.unrealized_pnl || 0).toFixed(2)}</span>
                    ${closeBtn}
                    <div class="bot-pos-details" style="width:100%;">
                        <span class="bot-pos-size">Qty: ${size}</span>
                        <span class="bot-pos-entry">Entry: $${entry.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</span>
                        <span class="bot-pos-value">Value: $${mktVal.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</span>
                    </div>
                </div>`;
            }).join('');
        }

        // Button states
        document.getElementById('btn-sbot-start').disabled = data.running;
        document.getElementById('btn-sbot-stop').disabled = !data.running;

        if (!data.broker_configured) {
            const brokerLabel = (data.broker || 'alpaca') === 'webull' ? 'Webull' : 'Alpaca';
            document.getElementById('sbot-balance').textContent = `${brokerLabel} not configured`;
        }
    } catch (e) {
        console.error('Failed to load stock bot status:', e);
    }
}

async function startStockBot() {
    try {
        const resp = await fetch('/api/stock-bot/start', {method: 'POST'});
        const data = await resp.json();
        if (!data.ok) alert(data.error || 'Failed to start stock bot');
        loadStockBotStatus();
    } catch (e) {
        alert('Failed to start stock bot: ' + e.message);
    }
}

async function stopStockBot() {
    try {
        const resp = await fetch('/api/stock-bot/stop', {method: 'POST'});
        await resp.json();
        loadStockBotStatus();
    } catch (e) {
        alert('Failed to stop stock bot: ' + e.message);
    }
}

async function killStockBot() {
    if (!confirm('KILL SWITCH: This will stop the stock bot and close ALL open stock positions. Continue?')) return;
    try {
        const resp = await fetch('/api/stock-bot/kill', {method: 'POST'});
        await resp.json();
        loadStockBotStatus();
        loadStockBotTrades();
    } catch (e) {
        alert('Kill switch failed: ' + e.message);
    }
}

async function loadStockBotTrades() {
    try {
        const resp = await fetch('/api/stock-bot/trades?limit=50');
        const trades = await resp.json();
        const tbody = document.getElementById('sbot-trades-body');

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
            const closeBtn = t.status === 'open' ? `<button class="btn-close-trade" onclick="closeStockTrade(${t.id})">Close</button>` : '';
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
        console.error('Failed to load stock bot trades:', e);
    }
}

async function loadStockBotPnl() {
    try {
        const resp = await fetch('/api/stock-bot/pnl?days=30');
        const data = await resp.json();

        const chartContainer = document.getElementById('sbot-pnl-chart');
        if (!data.length) {
            chartContainer.innerHTML = '<div class="bot-empty" style="padding-top:80px;">No P&L data yet</div>';
            return;
        }

        if (stockBotPnlChart) {
            stockBotPnlChart.remove();
            stockBotPnlChart = null;
        }

        chartContainer.innerHTML = '';
        stockBotPnlChart = LightweightCharts.createChart(chartContainer, {
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

        let cumulative = 0;
        const seriesData = data.map(d => {
            cumulative += d.total_pnl;
            return {time: d.date, value: parseFloat(cumulative.toFixed(2))};
        });

        stockBotPnlSeries = stockBotPnlChart.addAreaSeries({
            lineColor: cumulative >= 0 ? '#26a69a' : '#ef5350',
            topColor: cumulative >= 0 ? 'rgba(38,166,154,0.3)' : 'rgba(239,83,80,0.3)',
            bottomColor: cumulative >= 0 ? 'rgba(38,166,154,0.05)' : 'rgba(239,83,80,0.05)',
            lineWidth: 2,
        });
        stockBotPnlSeries.setData(seriesData);
        stockBotPnlChart.timeScale().fitContent();

        const ro = new ResizeObserver(() => {
            if (stockBotPnlChart) stockBotPnlChart.applyOptions({width: chartContainer.clientWidth});
        });
        ro.observe(chartContainer);
    } catch (e) {
        console.error('Failed to load stock bot P&L:', e);
    }
}

async function loadStockBotLog() {
    try {
        const resp = await fetch('/api/stock-bot/log?limit=50');
        const logs = await resp.json();
        const logEl = document.getElementById('sbot-log');

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
        console.error('Failed to load stock bot log:', e);
    }
}

async function loadStockBotConfig() {
    try {
        const resp = await fetch('/api/stock-bot/config');
        const cfg = await resp.json();
        const el = (id) => document.getElementById(id);
        if (cfg.stock_daily_goal) el('sbot-cfg-daily-goal').value = cfg.stock_daily_goal;
        if (cfg.stock_max_position_pct) el('sbot-cfg-max-pct').value = cfg.stock_max_position_pct;
        if (cfg.stock_daily_loss_limit) el('sbot-cfg-loss-limit').value = cfg.stock_daily_loss_limit;
        if (cfg.stock_max_open_positions) el('sbot-cfg-max-open').value = cfg.stock_max_open_positions;
        if (cfg.stock_max_daily_trades) el('sbot-cfg-max-daily-trades').value = cfg.stock_max_daily_trades;
        if (cfg.stock_scan_interval_sec) el('sbot-cfg-interval').value = cfg.stock_scan_interval_sec;
        if (cfg.stock_trade_mode) el('sbot-cfg-trade-mode').value = cfg.stock_trade_mode;
        if (cfg.stock_direction_bias) el('sbot-cfg-direction-bias').value = cfg.stock_direction_bias;
        el('sbot-cfg-extended-hours').checked = cfg.stock_extended_hours === '1';
    } catch (e) {
        console.error('Failed to load stock bot config:', e);
    }
}

async function addStockTicker() {
    const input = document.getElementById('sbot-add-ticker');
    const statusEl = document.getElementById('sbot-add-status');
    const symbol = (input.value || '').trim().toUpperCase();
    if (!symbol) return;

    statusEl.style.display = 'block';
    statusEl.style.color = 'var(--text-secondary)';
    statusEl.textContent = `Validating ${symbol}...`;

    try {
        const resp = await fetch('/api/stock-bot/stocks/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({symbol}),
        });
        const data = await resp.json();
        if (data.valid) {
            statusEl.style.color = 'var(--accent-green)';
            const priceStr = data.price ? ` ($${data.price})` : '';
            statusEl.textContent = `Added ${symbol} — ${data.name}${priceStr}`;
            input.value = '';
            await loadStockBotStocks();
            // Auto-select the new ticker
            const cb = document.querySelector(`#sbot-stock-picker input[value="${symbol}"]`);
            if (cb && !cb.checked) { cb.checked = true; cb.parentElement.classList.add('selected'); }
        } else {
            statusEl.style.color = 'var(--accent-red)';
            statusEl.textContent = data.error || `Ticker '${symbol}' not found`;
        }
    } catch (e) {
        statusEl.style.color = 'var(--accent-red)';
        statusEl.textContent = 'Failed to validate ticker: ' + e.message;
    }
    setTimeout(() => { statusEl.style.display = 'none'; }, 5000);
}

async function loadStockBotStocks() {
    try {
        const resp = await fetch('/api/stock-bot/stocks');
        const data = await resp.json();
        const picker = document.getElementById('sbot-stock-picker');

        picker.innerHTML = data.stocks.map(s => `
            <label class="bot-coin-chip ${s.selected ? 'selected' : ''}">
                <input type="checkbox" value="${s.key}" ${s.selected ? 'checked' : ''} onchange="this.parentElement.classList.toggle('selected', this.checked)">
                <span>${s.key} — ${s.name}</span>
            </label>
        `).join('');
    } catch (e) {
        console.error('Failed to load stock bot stocks:', e);
    }
}

async function updateStockBotStocks() {
    const checkboxes = document.querySelectorAll('#sbot-stock-picker input[type="checkbox"]');
    const selected = Array.from(checkboxes).filter(cb => cb.checked).map(cb => cb.value);

    if (selected.length === 0) {
        alert('Select at least one stock.');
        return;
    }

    try {
        const resp = await fetch('/api/stock-bot/stocks', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({selected}),
        });
        const data = await resp.json();
        if (data.ok) {
            const btn = document.querySelector('#stocktrading-content .btn-bot-save-coins');
            btn.textContent = 'Saved!';
            btn.style.background = 'var(--accent-green)';
            setTimeout(() => { btn.textContent = 'Save Stocks'; btn.style.background = ''; }, 1500);
        } else {
            alert(data.error || 'Failed to save stocks');
        }
    } catch (e) {
        alert('Failed to save stocks: ' + e.message);
    }
}

async function updateStockBotConfig() {
    try {
        const payload = {
            stock_daily_goal: document.getElementById('sbot-cfg-daily-goal').value,
            stock_max_position_pct: document.getElementById('sbot-cfg-max-pct').value,
            stock_daily_loss_limit: document.getElementById('sbot-cfg-loss-limit').value,
            stock_max_open_positions: document.getElementById('sbot-cfg-max-open').value,
            stock_max_daily_trades: document.getElementById('sbot-cfg-max-daily-trades').value,
            stock_scan_interval_sec: document.getElementById('sbot-cfg-interval').value,
            stock_trade_mode: document.getElementById('sbot-cfg-trade-mode').value,
            stock_direction_bias: document.getElementById('sbot-cfg-direction-bias').value,
            stock_extended_hours: document.getElementById('sbot-cfg-extended-hours').checked ? '1' : '0',
        };
        const resp = await fetch('/api/stock-bot/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data.ok) {
            const btn = document.querySelector('#stocktrading-content .btn-bot-save-config');
            btn.textContent = 'Saved!';
            btn.style.background = 'var(--accent-green)';
            setTimeout(() => { btn.textContent = 'Save Settings'; btn.style.background = ''; }, 1500);
        }
    } catch (e) {
        alert('Failed to save stock config: ' + e.message);
    }
}

function onStockBrokerChange() {
    const broker = document.getElementById('sbot-cfg-broker').value;
    const alpacaFields = document.getElementById('sbot-alpaca-fields');
    const webullFields = document.getElementById('sbot-webull-fields');
    const noteEl = document.getElementById('sbot-broker-note');

    if (broker === 'webull') {
        alpacaFields.style.display = 'none';
        webullFields.style.display = 'block';
        noteEl.textContent = 'Webull trading via OpenAPI';
    } else {
        alpacaFields.style.display = 'block';
        webullFields.style.display = 'none';
        noteEl.textContent = 'Paper trading on Alpaca — no real funds';
    }
}

async function loadStockBrokerStatus() {
    try {
        const resp = await fetch('/api/stock-bot/broker');
        const data = await resp.json();
        const statusEl = document.getElementById('sbot-key-status');
        const brokerSelect = document.getElementById('sbot-cfg-broker');

        // Set broker dropdown to match server
        if (data.broker) {
            brokerSelect.value = data.broker;
            onStockBrokerChange();
        }

        const label = data.broker === 'webull' ? 'Webull' : 'Alpaca';
        if (data.configured) {
            statusEl.innerHTML = `<span class="key-status-ok">${label} keys configured</span>`;
        } else if (data.has_key || data.has_secret) {
            statusEl.innerHTML = `<span class="key-status-partial">Partially configured — missing fields</span>`;
        } else {
            statusEl.innerHTML = '<span class="key-status-none">No API keys set</span>';
        }
    } catch (e) {
        console.error('Failed to load stock broker status:', e);
    }
}

async function saveStockBrokerKeys() {
    const broker = document.getElementById('sbot-cfg-broker').value;
    let body = { broker };

    if (broker === 'webull') {
        const appKey = document.getElementById('sbot-cfg-wb-app-key').value;
        const appSecret = document.getElementById('sbot-cfg-wb-app-secret').value;
        const accountId = document.getElementById('sbot-cfg-wb-account-id').value;
        if (!appKey && !appSecret) {
            alert('Enter App Key and App Secret to save.');
            return;
        }
        body.api_key = appKey;
        body.api_secret = appSecret;
        if (accountId) body.account_id = accountId;
    } else {
        const apiKey = document.getElementById('sbot-cfg-api-key').value;
        const apiSecret = document.getElementById('sbot-cfg-api-secret').value;
        if (!apiKey && !apiSecret) {
            alert('Enter at least one field to save.');
            return;
        }
        body.api_key = apiKey;
        body.api_secret = apiSecret;
    }

    try {
        const resp = await fetch('/api/stock-bot/broker', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.ok) {
            const btn = document.querySelector('#stocktrading-content .btn-bot-save-keys');
            btn.textContent = 'Saved!';
            btn.style.background = 'var(--accent-green)';
            setTimeout(() => { btn.textContent = 'Save Keys'; btn.style.background = ''; }, 1500);
            // Clear sensitive fields (keep account dropdown selection)
            if (broker === 'webull') {
                document.getElementById('sbot-cfg-wb-app-key').value = '';
                document.getElementById('sbot-cfg-wb-app-secret').value = '';
            } else {
                document.getElementById('sbot-cfg-api-key').value = '';
                document.getElementById('sbot-cfg-api-secret').value = '';
            }
            loadStockBrokerStatus();
        } else {
            alert(data.error || 'Failed to save keys');
        }
    } catch (e) {
        alert('Failed to save keys: ' + e.message);
    }
}

async function fetchWebullAccounts() {
    const statusEl = document.getElementById('sbot-key-status');
    const select = document.getElementById('sbot-cfg-wb-account-id');
    statusEl.innerHTML = '<span class="key-status-testing">Fetching Webull accounts...</span>';

    try {
        const resp = await fetch('/api/stock-bot/webull-accounts', {method: 'POST'});
        const data = await resp.json();
        if (data.ok && data.accounts && data.accounts.length > 0) {
            // Clear and populate dropdown
            select.innerHTML = '<option value="">-- Select an account --</option>';
            data.accounts.forEach(acc => {
                const opt = document.createElement('option');
                opt.value = acc.account_id;
                let label = acc.label || acc.account_id;
                if (acc.is_paper) label = '(Paper) ' + label;
                if (acc.account_status) label += ' [' + acc.account_status + ']';
                opt.textContent = label;
                select.appendChild(opt);
            });
            // Auto-select paper account if available, otherwise first
            const paperAcc = data.accounts.find(a => a.is_paper);
            if (paperAcc) {
                select.value = paperAcc.account_id;
                statusEl.innerHTML = `<span class="key-status-ok">Found ${data.accounts.length} account(s) — paper account auto-selected</span>`;
            } else {
                select.value = data.accounts[0].account_id;
                statusEl.innerHTML = `<span class="key-status-ok">Found ${data.accounts.length} account(s) — select one above</span>`;
            }
        } else {
            statusEl.innerHTML = `<span class="key-status-error">${data.error || 'No accounts found'}</span>`;
        }
    } catch (e) {
        statusEl.innerHTML = `<span class="key-status-error">Failed to fetch accounts: ${e.message}</span>`;
    }
}

async function testStockConnection() {
    const broker = document.getElementById('sbot-cfg-broker').value;
    const label = broker === 'webull' ? 'Webull' : 'Alpaca';
    const btn = document.querySelector('#stocktrading-content .btn-bot-test-keys');
    const statusEl = document.getElementById('sbot-key-status');
    btn.disabled = true;
    btn.textContent = 'Testing...';
    statusEl.innerHTML = `<span class="key-status-testing">Connecting to ${label}...</span>`;

    try {
        const resp = await fetch('/api/stock-bot/test-connection', {method: 'POST'});
        const data = await resp.json();
        if (data.ok) {
            statusEl.innerHTML = `<span class="key-status-ok">${data.message}</span>`;
            // If Webull returned an auto-discovered account_id, show it in dropdown
            if (data.account_id && broker === 'webull') {
                const select = document.getElementById('sbot-cfg-wb-account-id');
                if (!select.querySelector(`option[value="${data.account_id}"]`)) {
                    const opt = document.createElement('option');
                    opt.value = data.account_id;
                    opt.textContent = `Connected — ${data.account_id.substring(0, 8)}...`;
                    select.appendChild(opt);
                }
                select.value = data.account_id;
            }
        } else {
            statusEl.innerHTML = `<span class="key-status-error">${data.error}</span>`;
        }
    } catch (e) {
        statusEl.innerHTML = `<span class="key-status-error">Connection failed: ${e.message}</span>`;
    }

    btn.disabled = false;
    btn.textContent = 'Test Connection';
}


