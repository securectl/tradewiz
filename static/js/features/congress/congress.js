// Congress Trades — Politician Holdings & Disclosures with actual trade data

async function loadCongressTrades(force) {
    const filingsEl = document.getElementById('cong-filings');
    filingsEl.innerHTML = '<div class="bot-empty"><div class="spinner" style="width:18px;height:18px;margin:0 auto 8px;"></div>Downloading & parsing official disclosures (may take 15-30s)...</div>';

    const chamber = document.getElementById('cong-chamber').value;
    const name = document.getElementById('cong-name').value.trim();
    const state = document.getElementById('cong-state').value.trim();
    const ticker = document.getElementById('cong-ticker').value.trim();
    const days = document.getElementById('cong-days').value;

    const params = new URLSearchParams();
    if (chamber) params.set('chamber', chamber);
    if (name) params.set('name', name);
    if (state) params.set('state', state);
    if (ticker) params.set('ticker', ticker);
    params.set('days', days);

    try {
        const resp = await fetch(`/api/congress/trades?${params}`);
        const d = await resp.json();
        renderCongressData(d);
    } catch (e) {
        filingsEl.innerHTML = `<div class="bot-empty">Failed: ${e.message}</div>`;
    }
}

function _fmtAmt(low, high) {
    if (!high) return '—';
    if (high >= 1e6) return `$${(low/1e3).toFixed(0)}K–$${(high/1e6).toFixed(1)}M`;
    if (high >= 1e3) return `$${(low/1e3).toFixed(0)}K–$${(high/1e3).toFixed(0)}K`;
    return `$${low}–$${high}`;
}

function renderCongressData(d) {
    const stats = d.stats || {};

    // Show Senate API notice if no structured trade data
    const notice = document.getElementById('cong-senate-notice');
    if (notice) {
        const hasTradeData = (d.trades || []).some(t => t.source === 'quiver_api');
        notice.style.display = hasTradeData ? 'none' : 'block';
    }

    // ── Stats Bar ──
    const statsEl = document.getElementById('cong-stats');
    if (statsEl) {
        statsEl.innerHTML = `
            <div class="cong-stat"><div class="cong-stat-value">${stats.house_trades || 0}</div><div class="cong-stat-label">House Trades</div></div>
            <div class="cong-stat"><div class="cong-stat-value">${stats.senate_news || 0}</div><div class="cong-stat-label">Senate (News)</div></div>
            <div class="cong-stat"><div class="cong-stat-value" style="color:#00c896">${stats.total_buys || 0}</div><div class="cong-stat-label">Buys</div></div>
            <div class="cong-stat"><div class="cong-stat-value" style="color:#ff4757">${stats.total_sells || 0}</div><div class="cong-stat-label">Sells</div></div>
            <div class="cong-stat"><div class="cong-stat-value">${stats.unique_members || 0}</div><div class="cong-stat-label">Members</div></div>
        `;
    }

    // ── Hot Tickers (most traded by politicians) ──
    const topEl = document.getElementById('cong-top-traders');
    const hotTickers = d.hot_tickers || [];
    if (topEl && hotTickers.length > 0) {
        const maxVal = hotTickers[0].total_high || 1;
        let html = '<div style="font-size:12px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:8px;">Most Traded Tickers by Politicians</div>';
        html += '<div class="cong-top-list">';
        hotTickers.forEach((t, i) => {
            const pct = (t.total_high / maxVal) * 100;
            html += `<div class="cong-top-row">
                <span class="cong-top-rank">${i + 1}</span>
                <span class="cong-top-name" style="font-weight:800;color:var(--accent-blue);">${t.ticker}</span>
                <div class="cong-top-bar-track">
                    <div class="cong-top-bar" style="width:${pct}%;background:linear-gradient(90deg, #00c896 ${t.buys/(t.buys+t.sells)*100}%, #ff4757 0%)"></div>
                </div>
                <span class="cong-top-count" style="font-size:10px;color:var(--text-secondary);">
                    <span style="color:#00c896">${t.buys}B</span> / <span style="color:#ff4757">${t.sells}S</span>
                </span>
            </div>`;
        });
        html += '</div>';
        topEl.innerHTML = html;
    } else if (topEl) {
        // Fallback to top traders by filing count
        const traders = stats.top_traders || [];
        if (traders.length > 0) {
            const maxF = traders[0].trades;
            let html = '<div style="font-size:12px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:8px;">Most Active Traders</div><div class="cong-top-list">';
            traders.slice(0, 10).forEach((t, i) => {
                html += `<div class="cong-top-row">
                    <span class="cong-top-rank">${i+1}</span>
                    <span class="cong-top-name">${t.name}</span>
                    <div class="cong-top-bar-track"><div class="cong-top-bar" style="width:${(t.trades/maxF)*100}%"></div></div>
                    <span class="cong-top-count">${t.trades}</span>
                </div>`;
            });
            html += '</div>';
            topEl.innerHTML = html;
        } else {
            topEl.innerHTML = '';
        }
    }

    // ── Top Buys & Sells Side by Side ──
    const newsEl = document.getElementById('cong-news');
    const buys = d.top_buys || [];
    const sells = d.top_sells || [];
    if (newsEl && (buys.length > 0 || sells.length > 0)) {
        let html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">';

        // Top Buys
        html += '<div>';
        html += '<div style="font-size:12px;font-weight:700;text-transform:uppercase;color:#00c896;margin-bottom:8px;">Top Buys</div>';
        if (buys.length === 0) {
            html += '<div class="bot-empty">No buys found</div>';
        } else {
            buys.slice(0, 10).forEach(t => {
                const tk = t.ticker ? `<span style="color:var(--accent-blue);font-weight:800;margin-right:4px;">${t.ticker}</span>` : '';
                html += `<div class="cong-trade-row cong-buy">
                    ${tk}<span class="cong-trade-name">${t.name}</span>
                    <span class="cong-trade-amount">${t.amount_str || _fmtAmt(t.amount_low, t.amount_high)}</span>
                    <span class="cong-trade-date">${t.date || t.filing_date || ''}</span>
                </div>`;
            });
        }
        html += '</div>';

        // Top Sells
        html += '<div>';
        html += '<div style="font-size:12px;font-weight:700;text-transform:uppercase;color:#ff4757;margin-bottom:8px;">Top Sells</div>';
        if (sells.length === 0) {
            html += '<div class="bot-empty">No sells found</div>';
        } else {
            sells.slice(0, 10).forEach(t => {
                const tk = t.ticker ? `<span style="color:var(--accent-blue);font-weight:800;margin-right:4px;">${t.ticker}</span>` : '';
                html += `<div class="cong-trade-row cong-sell">
                    ${tk}<span class="cong-trade-name">${t.name}</span>
                    <span class="cong-trade-amount">${t.amount_str || _fmtAmt(t.amount_low, t.amount_high)}</span>
                    <span class="cong-trade-date">${t.date || t.filing_date || ''}</span>
                </div>`;
            });
        }
        html += '</div></div>';

        // Senate News
        const senateNews = d.senate_news || [];
        if (senateNews.length > 0) {
            html += '<div style="grid-column:1/-1;margin-top:12px;">';
            html += '<div style="font-size:12px;font-weight:700;text-transform:uppercase;color:#7c5dfa;margin-bottom:8px;">Senate Trade News</div>';
            html += '<div class="cong-news-grid">';
            senateNews.slice(0, 6).forEach(n => {
                const typeColor = n.type === 'Purchase' ? '#00c896' : n.type === 'Sale' ? '#ff4757' : 'var(--text-secondary)';
                html += `<a href="${n.url}" target="_blank" rel="noopener" class="cong-news-card">
                    <div class="cong-news-title">${n.headline || n.title || ''}</div>
                    <div class="cong-news-meta">
                        ${n.name ? `<strong>${n.name}</strong> &middot; ` : ''}
                        <span style="color:${typeColor}">${n.type}</span>
                        ${n.ticker ? ` &middot; <span style="color:var(--accent-blue);font-weight:700;">${n.ticker}</span>` : ''}
                        ${n.date ? ` &middot; ${n.date}` : ''}
                    </div>
                </a>`;
            });
            html += '</div></div>';
        }

        newsEl.innerHTML = html;
    } else if (newsEl) {
        newsEl.innerHTML = '';
    }

    // ── All Trades Table ──
    const filingsEl = document.getElementById('cong-filings');
    const trades = d.trades || [];
    if (trades.length === 0) {
        filingsEl.innerHTML = '<div class="bot-empty">No trades found for these filters. Try a wider date range.</div>';
    } else {
        let html = `<div style="font-size:12px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:8px;">
            All Trades <span style="font-weight:400;">(${trades.length})</span>
        </div>`;
        html += '<table class="cong-table"><thead><tr>';
        html += '<th>Date</th><th>Name</th><th>Ticker</th><th>Type</th><th>Amount</th><th>State</th><th>Chamber</th><th></th>';
        html += '</tr></thead><tbody>';
        trades.forEach(t => {
            const typeColor = (t.type || '').toLowerCase().includes('purchase') ? '#00c896' :
                              (t.type || '').toLowerCase().includes('sale') ? '#ff4757' : 'var(--text-secondary)';
            const chamberCls = (t.chamber || '').toLowerCase();
            html += `<tr>
                <td>${t.date || t.filing_date || '—'}</td>
                <td><strong>${t.name || '—'}</strong></td>
                <td style="color:var(--accent-blue);font-weight:700;">${t.ticker || '—'}</td>
                <td style="color:${typeColor};font-weight:600;">${t.type || '—'}</td>
                <td>${t.amount_str || _fmtAmt(t.amount_low || 0, t.amount_high || 0)}</td>
                <td>${t.state || ''}${t.district ? '-' + t.district : ''}</td>
                <td><span class="cong-chamber-badge cong-${chamberCls}">${t.chamber || '—'}</span></td>
                <td>${t.pdf_url ? `<a href="${t.pdf_url}" target="_blank" class="cong-pdf-link">PDF</a>` : (t.url ? `<a href="${t.url}" target="_blank" class="cong-pdf-link">Source</a>` : '')}</td>
            </tr>`;
        });
        html += '</tbody></table>';
        filingsEl.innerHTML = html;
    }

    // ── Footer ──
    const footer = document.getElementById('cong-footer');
    if (footer) {
        footer.innerHTML = `${(d.sources || []).join(' | ')} &middot; ${new Date(d.updated_at).toLocaleTimeString()} &middot; ${d.cached ? 'Cached (1h)' : 'Fresh'}
            ${d._note ? `<br>${d._note}` : ''}`;
    }
}
