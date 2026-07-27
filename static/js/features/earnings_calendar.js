// Earnings Calendar — weekly "most anticipated reports" board.
// Backed by GET /api/earnings/calendar?week=<offset>&wide=<0|1>.
// Layout: 5 day columns (Mon–Fri), each split into Before Open / After Close /
// TBD; rows carry an RRG quadrant badge (LE/WE/IM/LAG), a near-highs dot and a
// 0–100 DX score.

let _ecWeek = 0;            // week offset (0 = current)
let _ecFilter = 'all';     // all | LE | IM | WE | LAG | near
let _ecLoading = false;
let _ecLoaded = false;
let _ecData = null;

const _EC_QNAMES = { LE: 'Leading', IM: 'Improving', WE: 'Weakening', LAG: 'Lagging' };

function initEarningsCalendar() {
  // Load once on first open; subsequent opens keep the last board.
  if (!_ecLoaded) ecReload(false);
}

function _ecEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function ecShiftWeek(delta) {
  _ecWeek += delta;
  if (_ecWeek < -8) _ecWeek = -8;
  if (_ecWeek > 8) _ecWeek = 8;
  ecReload(false);
}

function ecGoToday() {
  if (_ecWeek === 0) return;
  _ecWeek = 0;
  ecReload(false);
}

function ecSetFilter(f) {
  _ecFilter = f;
  document.querySelectorAll('#ec-filters .ec-chip').forEach(b => {
    b.classList.toggle('ec-chip-active', b.getAttribute('data-filter') === f);
  });
  if (_ecData) _ecRender(_ecData);   // client-side filter, no refetch
}

async function ecReload(force) {
  const board = document.getElementById('ec-board');
  if (!board) return;
  if (_ecLoading) return;
  _ecLoading = true;
  board.innerHTML = '<div class="ec-empty">Loading the week’s earnings board…</div>';
  const wide = document.getElementById('ec-wide');
  const params = new URLSearchParams({ week: String(_ecWeek), wide: wide && wide.checked ? '1' : '0' });
  if (force) params.set('refresh', '1');
  try {
    const res = await fetch('/api/earnings/calendar?' + params.toString());
    const data = await res.json();
    _ecData = data;
    _ecLoaded = true;
    _ecRenderLegend(data);
    _ecRender(data);
    _ecRenderFoot(data);
  } catch (e) {
    board.innerHTML = '<div class="ec-empty">Could not load the earnings board. '
      + _ecEsc(e && e.message ? e.message : '') + '</div>';
  } finally {
    _ecLoading = false;
  }
}

function _ecRenderLegend(data) {
  const label = document.getElementById('ec-week-label');
  if (label) label.textContent = (data && data.week_label ? data.week_label : 'Most anticipated reports')
    + (data && data.from_snapshot ? ' · cached' : '');
  const leg = document.getElementById('ec-legend');
  if (!leg) return;
  const items = (data && data.legend) || [
    { key: 'LE', label: 'Leading' }, { key: 'WE', label: 'Weakening' },
    { key: 'IM', label: 'Improving' }, { key: 'LAG', label: 'Lagging' }];
  let html = items.map(it =>
    '<span class="ec-leg-item"><span class="ec-badge q-' + it.key + '">' + it.key + '</span> '
    + _ecEsc(it.label) + '</span>').join('');
  html += '<span class="ec-leg-item"><span class="ec-dot"></span> Near highs</span>';
  leg.innerHTML = html;
}

function _ecPassesFilter(r) {
  if (_ecFilter === 'all') return true;
  if (_ecFilter === 'near') return !!r.near_highs;
  return r.quadrant === _ecFilter;
}

function _ecRow(r) {
  const q = r.quadrant || 'LAG';
  const dot = r.near_highs ? '<span class="ec-dot" title="Near 52-week high"></span>' : '';
  const name = _ecEsc(r.name || r.symbol);
  const badgeTitle = (_EC_QNAMES[q] || q) + ' · RS-Ratio ' + (r.rs_ratio != null ? r.rs_ratio : '—');
  return '<div class="ec-row q-' + q + '" title="' + _ecEsc(r.symbol) + ' — ' + name + '">'
    + '<div class="ec-row-main">'
    + '<div class="ec-row-top"><span class="ec-tkr">' + _ecEsc(r.symbol) + '</span>' + dot + '</div>'
    + '<div class="ec-name">' + name + '</div>'
    + '</div>'
    + '<div class="ec-row-right">'
    + '<span class="ec-badge q-' + q + '" title="' + _ecEsc(badgeTitle) + '">' + q + '</span>'
    + '<span class="ec-score">' + (r.score != null ? r.score : '—') + '</span>'
    + '</div>'
    + '</div>';
}

function _ecSessionBlock(sess) {
  const rows = (sess && sess.rows || []).filter(_ecPassesFilter);
  if (!rows.length) return '';   // hide empty sessions once filtered
  return '<div class="ec-session">'
    + '<div class="ec-session-label">' + _ecEsc(sess.label) + '</div>'
    + rows.map(_ecRow).join('')
    + '</div>';
}

function _ecRender(data) {
  const board = document.getElementById('ec-board');
  if (!board) return;
  const days = (data && data.days) || [];
  if (!days.length || !data.counts || !data.counts.total) {
    board.innerHTML = '<div class="ec-empty">No earnings scheduled for this week in the tracked universe. '
      + 'Try the next week or the wider universe.</div>';
    return;
  }
  const todayIso = new Date().toISOString().slice(0, 10);
  let html = '<div class="ec-grid">';
  for (const day of days) {
    const isToday = day.date === todayIso;
    let body = '';
    for (const key of ['bmo', 'amc', 'tbd']) {
      body += _ecSessionBlock(day.sessions && day.sessions[key]);
    }
    if (!body) body = '<div class="ec-session-empty">—</div>';
    html += '<div class="ec-col' + (isToday ? ' ec-col-today' : '') + '">'
      + '<div class="ec-col-head">' + _ecEsc(day.label)
      + ' <span class="ec-col-count">(' + (day.count || 0) + ')</span></div>'
      + body + '</div>';
  }
  html += '</div>';
  board.innerHTML = html;
}

function _ecRenderFoot(data) {
  const foot = document.getElementById('ec-foot');
  if (!foot) return;
  const c = (data && data.counts) || {};
  const summary = 'Sorted by DX Score · ' + (c.total || 0) + ' reporters this week · '
    + (c.near_highs || 0) + ' near highs';
  foot.innerHTML = '<span>' + _ecEsc(summary) + '</span>'
    + '<span>' + _ecEsc((data && data.note) || '') + '</span>';
}
