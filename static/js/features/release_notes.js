/* What's New — in-app release notes drawer + unseen badge. */

const WN_SEEN_KEY = 'wn_seen_version';
let _wnReleases = null;
let _wnLatest = null;

function _wnEsc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function loadReleaseNotes() {
    try {
        const resp = await fetch('/api/release-notes');
        const data = await resp.json();
        _wnReleases = data.releases || [];
        _wnLatest = data.latest_version || '';
        renderReleaseNotes();
        updateWhatsNewBadge();
    } catch (err) {
        console.error('release notes load failed', err);
    }
}

function updateWhatsNewBadge() {
    const badge = document.getElementById('wn-badge');
    if (!badge) return;
    const seen = localStorage.getItem(WN_SEEN_KEY);
    const unseen = _wnLatest && seen !== _wnLatest;
    badge.style.display = unseen ? 'inline-block' : 'none';
}

function renderReleaseNotes() {
    const list = document.getElementById('wn-list');
    if (!list) return;
    if (!_wnReleases || !_wnReleases.length) {
        list.innerHTML = '<div class="wn-loading">No updates yet.</div>';
        return;
    }
    const tlabel = { new: 'NEW', improved: 'IMPROVED', fix: 'FIX' };
    list.innerHTML = _wnReleases.map(r => {
        const items = (r.items || []).map(it => {
            const t = (it.type || 'new').toLowerCase();
            return `<li><span class="wn-tag wn-${_wnEsc(t)}">${tlabel[t] || 'NEW'}</span>${_wnEsc(it.text)}</li>`;
        }).join('');
        return `<div class="wn-rel">
            <div><span class="wn-vtag">v${_wnEsc(r.version)}</span><span class="wn-date">${_wnEsc(r.date)}</span></div>
            <h4>${_wnEsc(r.title)}</h4>
            <ul>${items}</ul>
        </div>`;
    }).join('');
}

function openWhatsNew() {
    const d = document.getElementById('wn-drawer');
    const s = document.getElementById('wn-scrim');
    if (d) { d.classList.add('open'); d.setAttribute('aria-hidden', 'false'); }
    if (s) s.classList.add('open');
    // Mark current latest as seen → clear the badge.
    if (_wnLatest) localStorage.setItem(WN_SEEN_KEY, _wnLatest);
    updateWhatsNewBadge();
}

function closeWhatsNew() {
    const d = document.getElementById('wn-drawer');
    const s = document.getElementById('wn-scrim');
    if (d) { d.classList.remove('open'); d.setAttribute('aria-hidden', 'true'); }
    if (s) s.classList.remove('open');
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeWhatsNew(); });
document.addEventListener('DOMContentLoaded', loadReleaseNotes);
