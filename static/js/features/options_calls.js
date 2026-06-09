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
    html += '<div class="oc-meta">Source: ' + srcLabel +
        ' · ' + (t.increasing_count || 0) + ' increasing · ' +
        (t.decreasing_count || 0) + ' decreasing</div>';

    // Two tables side by side
    html += '<div class="oc-tables">';
    html += _ocTable('▲ Increasing volume', 'Fresh buying — volume large vs open interest',
        d.increasing, 'oc-up');
    html += _ocTable('▼ Decreasing volume', 'Waning interest — large OI, little new volume',
        d.decreasing, 'oc-down');
    html += '</div>';
    return html;
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
        html += '<tr>' +
            '<td>$' + r.strike + ' <span class="oc-tag ' + _ocMoneyClass(r.moneyness) + '">' +
                r.moneyness + '</span></td>' +
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
