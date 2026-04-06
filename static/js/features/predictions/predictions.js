// Prediction Markets — Modern Kalshi-inspired visualization

let _predData = null;
let _predFilter = 'all';

const _catLabels = {
    crypto: 'Crypto', politics: 'Politics', economy: 'Economy', markets: 'Markets',
    oil_energy: 'Oil & Energy', geopolitics: 'Geopolitics', tech: 'Tech',
    climate: 'Climate', other: 'Other'
};

const _catIcons = {
    crypto: '\u20BF', politics: '\u2696', economy: '\uD83D\uDCC8', markets: '\uD83D\uDCCA',
    oil_energy: '\u26FD', geopolitics: '\uD83C\uDF0D', tech: '\u2699', climate: '\u2601', other: '\u2022'
};

async function loadPredictionMarkets(force) {
    const loading = document.getElementById('pred-loading');
    const eventsEl = document.getElementById('pred-events');
    if (loading) loading.style.display = 'block';

    try {
        const url = force ? '/api/predictions?force=true' : '/api/predictions';
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('Failed to load');
        _predData = await resp.json();
        renderPredictions(_predData);
    } catch (e) {
        if (eventsEl) eventsEl.innerHTML = `<div class="bot-empty">Failed to load: ${e.message}</div>`;
    } finally {
        if (loading) loading.style.display = 'none';
    }
}

function renderPredictions(d) {
    // ── Sentiment Banner ──
    const s = d.sentiment || {};
    const moodEl = document.getElementById('pred-mood');
    const descEl = document.getElementById('pred-mood-desc');
    const signalsEl = document.getElementById('pred-signals');
    if (moodEl) { moodEl.textContent = s.mood || '\u2014'; moodEl.style.color = s.color || ''; }
    if (descEl) { descEl.textContent = s.description || ''; }
    if (signalsEl) {
        signalsEl.innerHTML = (s.signals || []).map(sig =>
            `<span class="pred-signal-pill ${sig.type === 'bullish' ? 'pred-signal-bull' : 'pred-signal-bear'}">${sig.text}</span>`
        ).join('');
    }

    // ── Category Filter (pill tabs) ──
    const cats = Object.keys(d.by_category || {});
    const catsEl = document.getElementById('pred-categories');
    if (catsEl) {
        let html = `<button class="pred-cat-btn${_predFilter === 'all' ? ' active' : ''}" onclick="filterPredictions('all')">All ${d.total}</button>`;
        cats.sort((a, b) => (d.by_category[b] || []).length - (d.by_category[a] || []).length);
        cats.forEach(c => {
            const count = (d.by_category[c] || []).length;
            html += `<button class="pred-cat-btn${_predFilter === c ? ' active' : ''}" onclick="filterPredictions('${c}')">${_catLabels[c] || c} ${count}</button>`;
        });
        catsEl.innerHTML = html;
    }

    // ── Major Movers ──
    const moversEl = document.getElementById('pred-movers');
    const movers = d.movers || [];
    if (moversEl) {
        if (movers.length === 0) {
            moversEl.innerHTML = '<div class="bot-empty" style="grid-column:1/-1;">No major movers today</div>';
        } else {
            moversEl.innerHTML = movers.map(e => _renderCard(e, true)).join('');
        }
    }

    // ── Events grid ──
    renderFilteredEvents(d);

    // ── Footer ──
    const footer = document.getElementById('pred-footer');
    if (footer) {
        const srcCounts = d.sources || {};
        footer.textContent = `Polymarket ${srcCounts.polymarket || 0} + Kalshi ${srcCounts.kalshi || 0} = ${d.total} events | ${new Date(d.updated_at).toLocaleTimeString()} | ${d.cached ? 'cached' : 'fresh'}`;
    }
}

function filterPredictions(cat) {
    _predFilter = cat;
    if (_predData) renderPredictions(_predData);
}

function renderFilteredEvents(d) {
    const el = document.getElementById('pred-events');
    if (!el) return;
    let events = _predFilter === 'all' ? (d.events || []) : ((d.by_category || {})[_predFilter] || []);
    if (events.length === 0) {
        el.innerHTML = '<div class="bot-empty" style="grid-column:1/-1;">No events in this category</div>';
    } else {
        el.innerHTML = events.map(e => _renderCard(e, false)).join('');
    }
}

function _renderCard(e, isMover) {
    const yes = e.yes_price;
    const no = e.no_price;
    const gaugeColor = yes >= 50 ? '#00c896' : '#ff4757';
    const r = 22; // radius
    const circ = 2 * Math.PI * r;
    const offset = circ - (yes / 100) * circ;

    // Volume formatting
    const vol = e.volume_24h >= 1e6 ? `$${(e.volume_24h / 1e6).toFixed(1)}M`
              : e.volume_24h >= 1e3 ? `$${(e.volume_24h / 1e3).toFixed(0)}K`
              : `$${Math.round(e.volume_24h)}`;

    // Change badge
    let changeBadge = '';
    if (e.day_change && e.day_change !== 0) {
        const sign = e.day_change > 0 ? '+' : '';
        const cls = e.day_change > 0 ? 'pred-change-up' : 'pred-change-down';
        changeBadge = `<span class="pred-change ${cls}">${sign}${e.day_change}pp</span>`;
    }

    const srcLetter = e.source === 'polymarket' ? 'P' : 'K';
    const cat = _catLabels[e.category] || e.category;

    return `<a href="${e.url}" target="_blank" rel="noopener" class="pred-card${isMover ? ' pred-mover' : ''}">
        <div class="pred-card-top">
            <span class="pred-src pred-src-${e.source}">${srcLetter}</span>
            <span class="pred-cat">${cat}</span>
            ${changeBadge}
        </div>
        <div class="pred-card-title">${e.title}</div>
        <div class="pred-gauge-row">
            <div class="pred-gauge">
                <svg viewBox="0 0 48 48">
                    <circle class="pred-gauge-bg" cx="24" cy="24" r="${r}"/>
                    <circle class="pred-gauge-fill" cx="24" cy="24" r="${r}"
                        stroke="${gaugeColor}"
                        stroke-dasharray="${circ}"
                        stroke-dashoffset="${offset}"/>
                </svg>
                <div class="pred-gauge-pct" style="color:${gaugeColor}">${Math.round(yes)}</div>
            </div>
            <div class="pred-yesno">
                <div class="pred-btn-yes">Yes ${yes.toFixed(1)}%</div>
                <div class="pred-btn-no">No ${no.toFixed(1)}%</div>
            </div>
        </div>
        <div class="pred-card-footer">
            <div class="pred-vol-badge">${vol} vol</div>
            <div>${e.num_markets > 1 ? e.num_markets + ' markets' : ''}</div>
        </div>
    </a>`;
}
