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

const _OC_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// 'YYYY-MM-DD' -> 'Jun 12' (friendlier than the ISO date).
function _ocDate(s) {
    if (!s) return '—';
    const p = String(s).slice(0, 10).split('-');
    if (p.length < 3) return s;
    const m = parseInt(p[1], 10) - 1;
    if (m < 0 || m > 11) return s;
    return _OC_MONTHS[m] + ' ' + parseInt(p[2], 10);
}

// Plain-English "most active call" banner: a one-line summary + labeled details.
function _ocTopBanner(c) {
    const cost = Math.round((c.last || 0) * 100);            // $ per contract (×100 shares)
    const costTxt = cost > 0 ? '~$' + _ocFmt(cost) : '<$1';
    const cheap = cost > 0 && cost < 50 ? 'cheap (' + costTxt + ') ' : '';
    const date = _ocDate(c.expiry);
    // This banner is calls only: OTM = strike above price, ITM = below, ATM = near.
    let where, bet;
    if (c.moneyness === 'ITM') { where = 'below price'; bet = 'the stock stays above $' + c.strike; }
    else if (c.moneyness === 'ATM') { where = 'near price'; bet = 'the stock pushes past $' + c.strike; }
    else { where = 'above price'; bet = 'the stock rises above $' + c.strike; }
    const vo = c.vol_oi || 0;
    const activity = vo >= 0.5 ? 'heavily traded today'
        : (vo >= 0.15 ? 'actively traded today' : 'lightly traded today');
    const sentence = '🔥 Busiest call — ' + cheap + 'bet ' + bet + ' by ' + date + ', ' + activity + '.';
    const details =
        '<span class="oc-plain-item">Strike <b>$' + c.strike + '</b> (' + where + ')</span>' +
        '<span class="oc-plain-item">Expires <b>' + date + '</b></span>' +
        '<span class="oc-plain-item" title="Contracts traded today">Traded today <b>' + _ocFmt(c.volume) + '</b></span>' +
        '<span class="oc-plain-item" title="Open interest — contracts already held open from before">Open <b>' + _ocFmt(c.open_interest) + '</b></span>' +
        '<span class="oc-plain-item" title="Today&#39;s volume ÷ open interest. Higher = busier, more fresh money">Activity <b>' + vo.toFixed(2) + '×</b></span>' +
        '<span class="oc-plain-item" title="Price per share × 100 shares">' + costTxt + ' per contract</span>';
    return '<div class="oc-top-banner oc-plain">' +
        '<div class="oc-plain-headline">' + sentence + '</div>' +
        '<div class="oc-plain-details">' + details + '</div>' +
        '</div>';
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
        const enc = encodeURIComponent(symbol);
        // Directional flow (long/naked × call/put) and call-activity load together.
        const [callsResp, flowResp] = await Promise.all([
            fetch('/api/options/calls?symbol=' + enc),
            fetch('/api/options/flow?symbol=' + enc).catch(function () { return null; }),
        ]);
        const data = await callsResp.json();
        if (!callsResp.ok || data.error) {
            body.innerHTML = '<div class="oc-empty oc-err">' +
                (data.error || 'Could not load options data for ' + symbol) + '</div>';
            return;
        }
        let flow = null;
        try { flow = flowResp ? await flowResp.json() : null; } catch (e) { flow = null; }
        let html = '';
        if (flow && !flow.error) html += renderOptionFlow(flow);
        html += renderOptionCalls(data);
        body.innerHTML = html;
    } catch (e) {
        body.innerHTML = '<div class="oc-empty oc-err">Network error loading ' + symbol + '.</div>';
    } finally {
        _ocLoading = false;
    }
}

// ── Directional flow: 4 ranked buckets (Long/Naked × Call/Put) ──────────
function _ocBiasClass(bias) {
    return bias === 'bullish' ? 'oc-bull' : (bias === 'bearish' ? 'oc-bear' : '');
}

function _ocConfLabel(conf) {
    if (conf >= 0.7) return 'strong';
    if (conf >= 0.4) return 'medium';
    return 'weak';
}

function _ocFlowBucket(b) {
    if (!b) return '';
    let html = '<div class="oc-flow-card ' + _ocBiasClass(b.bias) + '">';
    html += '<div class="oc-flow-head"><span class="oc-flow-name">' + b.label + '</span>' +
        '<span class="oc-flow-bias ' + _ocBiasClass(b.bias) + '">' + b.bias + '</span></div>';
    const rows = b.rows || [];
    if (!rows.length) {
        html += '<div class="oc-empty">No directional ' + b.label.toLowerCase() + ' today.</div></div>';
        return html;
    }
    html += '<ol class="oc-flow-list">';
    rows.forEach(r => {
        html += '<li class="oc-flow-item">' +
            '<span class="oc-rank">#' + r.rank + '</span>' +
            '<span class="oc-flow-contract">$' + r.strike +
                ' <span class="oc-tag ' + _ocMoneyClass(r.moneyness) + '">' + r.moneyness + '</span>' +
                ' <span class="oc-flow-exp">' + _ocDate(r.expiry) + '</span></span>' +
            '<span class="oc-flow-vol">' + _ocFmt(r.volume) + ' vol</span>' +
            '<span class="oc-conf oc-conf-' + _ocConfLabel(r.confidence) + '" ' +
                'title="Estimate confidence (bid/ask position)">est. ' + _ocConfLabel(r.confidence) + '</span>' +
            '</li>';
    });
    html += '</ol></div>';
    return html;
}

function renderOptionFlow(f) {
    const s = f.summary || {};
    const buckets = f.buckets || {};
    const leanCls = s.lean === 'BULLISH' ? 'oc-bull' : (s.lean === 'BEARISH' ? 'oc-bear' : '');
    let html = '<div class="oc-flow">';
    html += '<div class="oc-flow-title">Directional Flow ' +
        '<span class="oc-flow-lean ' + leanCls + '">' + (s.lean || '—') + '</span>' +
        '<span class="oc-flow-est">estimated from bid/ask</span></div>';
    html += '<div class="oc-flow-grid">';
    // Order: Long Calls, Naked Calls, Naked Puts, Long Puts (as requested).
    ['long_calls', 'naked_calls', 'naked_puts', 'long_puts'].forEach(k => {
        html += _ocFlowBucket(buckets[k]);
    });
    html += '</div>';
    if (f.note) html += '<div class="oc-flow-note">ⓘ ' + f.note + '</div>';
    html += '</div>';
    return html;
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

    // Most-interest contract — the single most actively traded call, in plain English.
    if (d.top_contract) {
        html += _ocTopBanner(d.top_contract);
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
    html += '<div class="oc-table-title" title="' + subtitle + '">' + title +
        ' <span class="oc-hint" title="' + subtitle + '">ⓘ</span></div>';
    if (!rows || !rows.length) {
        html += '<div class="oc-empty">No contracts in this bucket.</div></div>';
        return html;
    }
    html += '<table class="oc-table"><thead><tr>' +
        '<th title="The buy price this call locks in">Strike</th>' +
        '<th>Expires</th>' +
        '<th title="Contracts traded today">Traded</th>' +
        '<th title="Open interest — contracts already held open from before">Open</th>' +
        '<th title="Today&#39;s volume ÷ open interest. Higher = busier, more fresh money">Activity</th>' +
        '<th title="Price per share (× 100 = cost per contract)">Price</th>' +
        '</tr></thead><tbody>';
    rows.forEach(r => {
        const div = r.divergent ? ' <span class="oc-tag oc-divergent" title="Sources disagree on this contract">⚠ divergent</span>' : '';
        html += '<tr' + (r.top ? ' class="oc-top-row"' : '') + '>' +
            '<td>' + (r.top ? '🔥 ' : '') + '$' + r.strike +
                ' <span class="oc-tag ' + _ocMoneyClass(r.moneyness) + '">' +
                r.moneyness + '</span>' + div + '</td>' +
            '<td>' + _ocDate(r.expiry) + '</td>' +
            '<td>' + _ocFmt(r.volume) + '</td>' +
            '<td>' + _ocFmt(r.open_interest) + '</td>' +
            '<td>' + r.vol_oi.toFixed(2) + '×</td>' +
            '<td>$' + r.last + '</td>' +
            '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
}
