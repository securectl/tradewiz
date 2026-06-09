// Option Calls — which call contracts are gaining vs losing volume for a stock.
// Backed by GET /api/options/calls?symbol=…  (Webull → yfinance fallback).

let _ocLoading = false;

function initOptionCalls() {
    // Lazily focus the input the first time the tab opens.
    const input = document.getElementById('oc-symbol');
    if (input && !input.value) input.focus();
}

function _ocFmt(n) {
    if (n === null || n === undefined) return '—';
    return Number(n).toLocaleString('en-US');
}

function _ocMoneyClass(m) {
    if (m === 'ITM') return 'oc-itm';
    if (m === 'ATM') return 'oc-atm';
    if (m === 'OTM') return 'oc-otm';
    return '';
}

async function loadOptionCalls() {
    const input = document.getElementById('oc-symbol');
    const body = document.getElementById('oc-body');
    if (!input || !body) return;
    const symbol = (input.value || '').trim().toUpperCase();
    if (!symbol) {
        body.innerHTML = '<div class="oc-empty">Enter a stock ticker to see its call-option activity.</div>';
        return;
    }
    if (_ocLoading) return;
    _ocLoading = true;
    body.innerHTML = '<div class="oc-empty">Loading call activity for ' + symbol + '…</div>';

    try {
        const resp = await fetch('/api/options/calls?symbol=' + encodeURIComponent(symbol));
        const data = await resp.json();
        if (!resp.ok || data.error) {
            body.innerHTML = '<div class="oc-empty oc-err">' +
                (data.error || 'Could not load options data for ' + symbol) + '</div>';
            return;
        }
        body.innerHTML = renderOptionCalls(data);
    } catch (e) {
        body.innerHTML = '<div class="oc-empty oc-err">Network error loading ' + symbol + '.</div>';
    } finally {
        _ocLoading = false;
    }
}

function renderOptionCalls(d) {
    const t = d.totals || {};
    const srcLabel = d.source === 'webull' ? 'Webull' : 'yfinance';
    let html = '';

    // Summary tiles
    html += '<div class="oc-summary">';
    html += _ocTile('Read', d.read || '—', d.read_color || '#ccc');
    html += _ocTile('Price', d.price ? '$' + d.price : '—');
    html += _ocTile('Call Volume', _ocFmt(t.call_volume));
    html += _ocTile('Call OI', _ocFmt(t.call_oi));
    html += _ocTile('Vol / OI', t.vol_oi_ratio != null ? t.vol_oi_ratio.toFixed(2) : '—');
    html += _ocTile('Contracts', _ocFmt(t.contracts));
    html += '</div>';
    html += '<div class="oc-meta">Primary source: ' + srcLabel +
        ' · ' + (t.increasing_count || 0) + ' increasing · ' +
        (t.decreasing_count || 0) + ' decreasing</div>';

    // Cross-validation across Webull / Alpaca / Yahoo
    if (d.validation) {
        const v = d.validation;
        let xv = '<div class="oc-xv">';
        if (v.multi_source) {
            const va = v.volume_agreement_pct;
            const oa = v.oi_agreement_pct;
            xv += '<span class="oc-xv-ok">✓ Cross-validated</span> across <b>' +
                (v.sources || []).join(', ') + '</b>';
            if (va != null) xv += ' · volume agreement ' + va + '% (' + v.volume_compared + ' contracts)';
            if (oa != null) xv += ' · OI agreement ' + oa + '%';
            if (v.divergent_contracts) xv += ' · <span class="oc-xv-warn">⚠ ' +
                v.divergent_contracts + ' divergent</span>';
        } else {
            xv += '<span class="oc-xv-single">ⓘ ' + (v.note || 'Single source') + '</span>';
        }
        xv += '</div>';
        html += xv;
    }

    // Most-interest contract — the single most actively traded call.
    if (d.top_contract) {
        const c = d.top_contract;
        html += '<div class="oc-top-banner">' +
            '<span class="oc-top-flag">🔥 Most active call</span>' +
            '<span class="oc-top-detail"><b>$' + c.strike + ' ' + c.moneyness + '</b> · ' +
            c.expiry + ' · <b>' + _ocFmt(c.volume) + '</b> vol · ' +
            _ocFmt(c.open_interest) + ' OI · ' + c.vol_oi.toFixed(2) + ' vol/OI · $' + c.last +
            '</span></div>';
    }

    // Two tables side by side
    html += '<div class="oc-tables">';
    html += _ocTable('▲ Increasing volume', 'Fresh buying — volume large vs open interest',
        d.increasing, 'oc-up');
    html += _ocTable('▼ Decreasing volume', 'Waning interest — large OI, little new volume',
        d.decreasing, 'oc-down');
    html += '</div>';

    // 30-day trend (one daily snapshot per day)
    html += renderOcTrend(d.history || []);
    return html;
}

function renderOcTrend(history) {
    if (!history || history.length < 2) {
        return '<div class="oc-trend"><div class="oc-trend-title">30-Day Trend</div>' +
            '<div class="oc-empty">Tracking builds over time — check back after a few days of activity.</div></div>';
    }
    const vols = history.map(h => h.call_volume);
    const max = Math.max.apply(null, vols) || 1;
    let bars = '';
    history.forEach(h => {
        const pct = Math.max(2, Math.round((h.call_volume / max) * 100));
        const title = h.date + ': ' + _ocFmt(h.call_volume) + ' call vol · ' +
            h.vol_oi_ratio.toFixed(2) + ' vol/OI' + (h.read ? ' · ' + h.read : '');
        bars += '<div class="oc-bar" style="height:' + pct + '%" title="' + title + '"></div>';
    });
    const first = history[0], last = history[history.length - 1];
    const delta = last.call_volume - first.call_volume;
    const arrow = delta > 0 ? '▲' : (delta < 0 ? '▼' : '–');
    const cls = delta > 0 ? 'oc-up-txt' : (delta < 0 ? 'oc-down-txt' : '');
    return '<div class="oc-trend">' +
        '<div class="oc-trend-title">30-Day Trend · daily call volume ' +
        '<span class="' + cls + '">' + arrow + ' ' + _ocFmt(Math.abs(delta)) +
        ' vs ' + first.date + '</span></div>' +
        '<div class="oc-bars">' + bars + '</div>' +
        '<div class="oc-trend-note">' + history.length + ' day(s) tracked · hover a bar for detail</div>' +
        '</div>';
}

function _ocTile(label, value, color) {
    const style = color ? ' style="color:' + color + '"' : '';
    return '<div class="oc-tile"><div class="oc-tile-label">' + label +
        '</div><div class="oc-tile-value"' + style + '>' + value + '</div></div>';
}

function _ocTable(title, subtitle, rows, cls) {
    let html = '<div class="oc-table-wrap ' + cls + '">';
    html += '<div class="oc-table-title">' + title + '</div>';
    html += '<div class="oc-table-sub">' + subtitle + '</div>';
    if (!rows || !rows.length) {
        html += '<div class="oc-empty">No contracts in this bucket.</div></div>';
        return html;
    }
    html += '<table class="oc-table"><thead><tr>' +
        '<th>Strike</th><th>Expiry</th><th>Vol</th><th>OI</th><th>Vol/OI</th><th>Last</th>' +
        '</tr></thead><tbody>';
    rows.forEach(r => {
        const div = r.divergent ? ' <span class="oc-tag oc-divergent" title="Sources disagree on this contract">⚠ divergent</span>' : '';
        html += '<tr' + (r.top ? ' class="oc-top-row"' : '') + '>' +
            '<td>' + (r.top ? '🔥 ' : '') + '$' + r.strike +
                ' <span class="oc-tag ' + _ocMoneyClass(r.moneyness) + '">' +
                r.moneyness + '</span>' + div + '</td>' +
            '<td>' + r.expiry + '</td>' +
            '<td>' + _ocFmt(r.volume) + '</td>' +
            '<td>' + _ocFmt(r.open_interest) + '</td>' +
            '<td>' + r.vol_oi.toFixed(2) + '</td>' +
            '<td>$' + r.last + '</td>' +
            '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
}
