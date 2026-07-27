/* News tab — full scrollable feed of ingested RSS + Reddit articles,
   filterable by category and ticker, with a trending strip on top. */

let _newsCat = '';
let _newsTicker = '';

function _nEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function _newsSentColor(s) { return s === 'bullish' ? '#26a69a' : s === 'bearish' ? '#ef5350' : '#787b86'; }
function _newsAgo(iso) {
    if (!iso) return '';
    const t = new Date(iso).getTime();
    if (isNaN(t)) return '';
    const m = Math.max(0, Math.round((Date.now() - t) / 60000));
    if (m < 60) return m + 'm ago';
    const h = Math.round(m / 60);
    if (h < 24) return h + 'h ago';
    return Math.round(h / 24) + 'd ago';
}

function loadNewsTab() {
    renderNewsShell();
    fetchNews();
    loadNewsTrendingStrip();
}

function renderNewsShell() {
    const el = document.getElementById('news-content');
    if (!el || el.dataset.init) return;
    el.dataset.init = '1';
    const cats = [['', 'All'], ['market', 'Market'], ['stocks', 'Stocks'],
                  ['reddit', 'Reddit'], ['crypto', 'Crypto'], ['macro', 'Macro']];
    el.innerHTML = `
        <div class="news-wrap">
            <div class="news-header">
                <div class="news-title">Market News
                    <span class="news-sub">RSS + Reddit · updates every 10 min · 30-day history</span></div>
                <div class="news-controls">
                    <input id="news-search" class="news-search" placeholder="Filter by ticker (e.g. NVDA)…"
                           onkeydown="if(event.key==='Enter')newsSearch()" />
                    <button class="btn-analyze" style="padding:8px 16px;font-size:12px;" onclick="newsSearch()">Search</button>
                    <button class="news-clear" onclick="newsClear()">Clear</button>
                </div>
            </div>
            <div class="news-cats">${cats.map(c =>
                `<button class="news-cat${c[0] === _newsCat ? ' active' : ''}" data-cat="${c[0]}"
                    onclick="newsSetCat('${c[0]}')">${c[1]}</button>`).join('')}</div>
            <div class="news-trending" id="news-trending"></div>
            <div class="news-list" id="news-list"><div class="dash-empty">Loading…</div></div>
        </div>`;
}

function newsSetCat(c) {
    _newsCat = c;
    document.querySelectorAll('.news-cat').forEach(b => b.classList.toggle('active', b.dataset.cat === c));
    fetchNews();
}
function newsSearch() {
    _newsTicker = (document.getElementById('news-search').value || '').trim().toUpperCase();
    fetchNews();
}
function newsClear() {
    _newsTicker = '';
    const s = document.getElementById('news-search'); if (s) s.value = '';
    fetchNews();
}
function newsFilterTicker(t) {
    const s = document.getElementById('news-search'); if (s) s.value = t;
    _newsTicker = t;
    fetchNews();
}

async function fetchNews() {
    const list = document.getElementById('news-list'); if (!list) return;
    list.innerHTML = '<div class="dash-empty">Loading…</div>';
    const p = new URLSearchParams({ limit: '120' });
    if (_newsCat) p.set('category', _newsCat);
    if (_newsTicker) p.set('ticker', _newsTicker);
    try {
        const data = await (await fetch('/api/news/feed?' + p.toString())).json();
        renderNewsFeed(data.items || []);
    } catch (e) {
        list.innerHTML = '<div class="dash-empty">Failed to load news.</div>';
    }
}

function renderNewsFeed(items) {
    const list = document.getElementById('news-list'); if (!list) return;
    if (!items.length) {
        list.innerHTML = '<div class="dash-empty">No articles match — try a different filter, or wait for the next poll.</div>';
        return;
    }
    list.innerHTML = items.map(it => {
        const tickers = (it.tickers || '').split(',').filter(Boolean).slice(0, 6)
            .map(t => `<span class="news-tk" onclick="event.preventDefault();event.stopPropagation();newsFilterTicker('${_nEsc(t)}')">${_nEsc(t)}</span>`).join('');
        const badge = it.category === 'reddit'
            ? `<span class="news-src-badge news-reddit">${_nEsc(it.source)}</span>`
            : `<span class="news-src-badge">${_nEsc(it.source)}</span>`;
        return `<a class="news-item" href="${_nEsc(it.url)}" target="_blank" rel="noopener">
            <div class="news-item-top">
                ${badge}
                <span class="news-cat-chip">${_nEsc(it.category)}</span>
                <span class="news-dot" title="${_nEsc(it.sentiment)}" style="background:${_newsSentColor(it.sentiment)}"></span>
                <span class="news-time">${_newsAgo(it.published_at)}</span>
            </div>
            <div class="news-item-title">${_nEsc(it.title)}</div>
            ${it.summary ? `<div class="news-item-sum">${_nEsc((it.summary || '').slice(0, 200))}</div>` : ''}
            ${tickers ? `<div class="news-tks">${tickers}</div>` : ''}
        </a>`;
    }).join('');
}

async function loadNewsTrendingStrip() {
    const el = document.getElementById('news-trending'); if (!el) return;
    try {
        const data = await (await fetch('/api/news/trending?hours=24&limit=8')).json();
        const stocks = (data.stocks || []).map(s =>
            `<span class="news-trend-chip" onclick="newsFilterTicker('${_nEsc(s.ticker)}')">
                ${_nEsc(s.ticker)} <b>${s.mentions}×</b>${s.reddit_mentions ? ' 🔥' : ''}</span>`).join('');
        const secs = (data.sectors || []).slice(0, 5).map(s =>
            `<span class="news-trend-sec">${_nEsc(s.sector)} <b>${s.mentions}</b></span>`).join('');
        el.innerHTML = (stocks || secs)
            ? `<div class="news-trend-row"><span class="news-trend-lbl">Trending 24h:</span>${stocks}${secs}</div>`
            : '';
    } catch (e) { el.innerHTML = ''; }
}
