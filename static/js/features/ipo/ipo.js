// ─── IPO Scanner ─────────────────────────────────────────────
let _ipoCache = null;
let _ipoCacheTime = 0;
const IPO_CACHE_TTL = 300000; // 5 min

function _renderStars(rating) {
    const full = Math.floor(rating);
    const half = rating - full >= 0.5 ? 1 : 0;
    const empty = 5 - full - half;
    let html = '';
    for (let i = 0; i < full; i++) html += '<span class="ipo-star full">&#9733;</span>';
    if (half) html += '<span class="ipo-star half">&#9733;</span>';
    for (let i = 0; i < empty; i++) html += '<span class="ipo-star empty">&#9734;</span>';
    return html;
}

function _buzzLabel(level) {
    const labels = { 1: 'Minimal', 2: 'Low', 3: 'Moderate', 4: 'High', 5: 'Viral' };
    return labels[level] || 'Unknown';
}

function _riskColor(level) {
    if (level <= 2) return 'var(--accent-green)';
    if (level <= 3) return 'var(--accent-orange)';
    return 'var(--accent-red)';
}

async function loadIPOs(force) {
    if (!force && _ipoCache && (Date.now() - _ipoCacheTime < IPO_CACHE_TTL)) {
        renderIPOs(_ipoCache);
        return;
    }

    const btn = document.getElementById('btn-ipo-scan');
    const results = document.getElementById('ipo-results');

    btn.disabled = true;
    btn.textContent = 'Scanning...';
    results.innerHTML = `
        <div style="text-align:center; padding:60px;">
            <div class="spinner" style="margin:0 auto 16px;"></div>
            <div style="color:#787b86; font-size:13px;">Scanning upcoming IPOs and analyzing social sentiment...</div>
            <div style="color:#787b86; font-size:11px; margin-top:8px;">This may take 30-60 seconds</div>
        </div>
    `;

    try {
        const resp = await fetch('/api/ipos');
        if (resp.status === 429) {
            const errData = await resp.json();
            handle429(errData);
            loadBillingStatus();
            return;
        }
        const data = await resp.json();
        // Always cache even on fallback — platforms + guide are always present
        _ipoCache = data;
        _ipoCacheTime = Date.now();
        renderIPOs(data);
    } catch (err) {
        results.innerHTML = `
            <div style="text-align:center; padding:40px; color:#ef5350;">
                <h3>Scan Error</h3>
                <p style="color:#787b86; margin-top:8px;">${err.message}</p>
                <div style="display:flex; gap:12px; justify-content:center; margin-top:16px;">
                    <button class="btn-ipo-scan" onclick="filterIPOs('platforms')" style="padding:8px 20px; font-size:12px;">Browse Platforms</button>
                    <button class="btn-ipo-scan" onclick="filterIPOs('guide')" style="padding:8px 20px; font-size:12px; background:linear-gradient(135deg, #4caf50, #2e7d32);">How to Start</button>
                </div>
            </div>
        `;
    }

    btn.disabled = false;
    btn.textContent = 'Scan Opportunities';
}

let _ipoFilter = 'all';

function filterIPOs(type) {
    _ipoFilter = type;
    document.querySelectorAll('.ipo-filter-btn').forEach(b => b.classList.toggle('active', b.dataset.filter === type));
    // Show/hide advanced filters for non-special tabs
    const advFilters = document.getElementById('ipo-adv-filters');
    if (advFilters) advFilters.style.display = (type === 'platforms' || type === 'guide') ? 'none' : 'flex';
    if (type === 'platforms') {
        if (_ipoCache) { renderPlatformsDirectory(_ipoCache); }
        else { _loadStaticIPOData().then(d => renderPlatformsDirectory(d)); }
    } else if (type === 'guide') {
        if (_ipoCache) { renderStartupGuide(_ipoCache); }
        else { _loadStaticIPOData().then(d => renderStartupGuide(d)); }
    } else if (_ipoCache) {
        renderIPOs(_ipoCache);
    }
}

function applyIPOFilters() {
    if (_ipoCache) renderIPOs(_ipoCache);
}

async function _loadStaticIPOData() {
    try {
        const resp = await fetch('/api/ipo-platforms');
        if (resp.ok) {
            const data = await resp.json();
            // Merge into cache if we have one, or create minimal cache
            if (_ipoCache) {
                _ipoCache.platforms = data.platforms;
                _ipoCache.startup_guide = data.startup_guide;
            } else {
                _ipoCache = { ipos: [], platforms: data.platforms, startup_guide: data.startup_guide };
            }
            return _ipoCache;
        }
    } catch (e) { console.error('Failed to load platform data:', e); }
    return { ipos: [], platforms: [], startup_guide: {} };
}

function _typeLabel(t) {
    return { ipo: 'IPO', pre_ipo: 'Pre-IPO', vc: 'VC Deal', startup: 'Startup' }[t] || 'IPO';
}
function _typeColor(t) {
    return { ipo: '#7c4dff', pre_ipo: '#ff9800', vc: '#00bcd4', startup: '#4caf50' }[t] || '#7c4dff';
}
function _vehicleIcon(t) {
    const icons = { pre_ipo_secondary: 'Secondary Mkt', ipo_access: 'IPO Access', vc_fund: 'VC Fund', etf: 'ETF', spac: 'SPAC', crowdfund: 'Crowdfund', direct: 'Direct' };
    return icons[t] || t;
}
function _accredBadge(a) {
    if (a === 'none') return '<span class="ipo-accred open">Open to All</span>';
    if (a === 'accredited') return '<span class="ipo-accred accred">Accredited</span>';
    return '<span class="ipo-accred qp">Qualified Purchaser</span>';
}

function renderIPOs(data) {
    const results = document.getElementById('ipo-results');

    // Handle special views
    if (_ipoFilter === 'platforms') { renderPlatformsDirectory(data); return; }
    if (_ipoFilter === 'guide') { renderStartupGuide(data); return; }

    let ipos = data.ipos || [];

    // Apply type filter
    if (_ipoFilter !== 'all') {
        ipos = ipos.filter(i => (i.opportunity_type || 'ipo') === _ipoFilter);
    }

    // Apply advanced filters
    const sectorFilter = document.getElementById('ipo-filter-sector')?.value || '';
    const ratingFilter = parseInt(document.getElementById('ipo-filter-rating')?.value || '0');
    const accredFilter = document.getElementById('ipo-filter-accred')?.value || '';
    const riskFilter = document.getElementById('ipo-filter-risk')?.value || '0';

    if (sectorFilter) {
        ipos = ipos.filter(i => (i.sector || '').toLowerCase().includes(sectorFilter.toLowerCase()));
    }
    if (ratingFilter > 0) {
        ipos = ipos.filter(i => (i.overall_rating || 0) >= ratingFilter);
    }
    if (accredFilter) {
        ipos = ipos.filter(i => {
            const vehicles = i.investment_vehicles || [];
            if (accredFilter === 'none') return vehicles.some(v => v.accreditation === 'none');
            return vehicles.some(v => v.accreditation === accredFilter);
        });
    }
    if (riskFilter !== '0') {
        ipos = ipos.filter(i => {
            const r = i.risk_level || 3;
            if (riskFilter === 'low') return r <= 2;
            if (riskFilter === 'med') return r === 3;
            if (riskFilter === 'high') return r >= 4;
            return true;
        });
    }

    // Show fallback message if LLM failed but we have platforms
    if (ipos.length === 0 && data.fallback_reason) {
        results.innerHTML = `
            <div style="text-align:center; padding:40px;">
                <div style="color:var(--accent-orange); font-size:14px; font-weight:600; margin-bottom:12px;">${data.fallback_reason}</div>
                <div style="display:flex; gap:12px; justify-content:center; margin-top:16px;">
                    <button class="btn-ipo-scan" onclick="filterIPOs('platforms')" style="padding:8px 20px; font-size:12px;">Browse Platforms</button>
                    <button class="btn-ipo-scan" onclick="filterIPOs('guide')" style="padding:8px 20px; font-size:12px; background:linear-gradient(135deg, #4caf50, #2e7d32);">How to Start Investing</button>
                </div>
            </div>`;
        return;
    }

    if (ipos.length === 0) {
        const allCount = (data.ipos || []).length;
        results.innerHTML = `<div style="text-align:center; padding:60px; color:#787b86;"><h3>No ${_ipoFilter === 'all' ? '' : _typeLabel(_ipoFilter) + ' '}Opportunities Found</h3><p style="margin-top:8px;">${allCount > 0 ? 'Try adjusting filters or select a different category.' : 'No opportunities detected. Try again later or browse Platforms & How to Start tabs.'}</p></div>`;
        return;
    }

    // Count by type
    const allIpos = data.ipos || [];
    const counts = { ipo: 0, pre_ipo: 0, vc: 0, startup: 0 };
    allIpos.forEach(i => { const t = i.opportunity_type || 'ipo'; counts[t] = (counts[t] || 0) + 1; });

    const socialPosts = data.social_posts_found || 0;
    const streamCounts = data.stream_counts || {};
    const crossRef = data.cross_referenced || [];
    const streamInfo = Object.entries(streamCounts).filter(([,v]) => v > 0).map(([k,v]) => `${k}: ${v}`).join(', ');
    let html = `<div class="ipo-summary">${allIpos.length} opportunities &middot; ${counts.ipo} IPOs &middot; ${counts.pre_ipo} Pre-IPO &middot; ${counts.vc} VC &middot; ${counts.startup} Startups<span class="ipo-source-tag">${socialPosts} posts (${streamInfo || 'AI knowledge'})</span></div>`;
    if (crossRef.length > 0) {
        html += `<div class="ipo-crossref">Cross-referenced across sources: ${crossRef.slice(0, 15).map(c => '<span class="ipo-crossref-chip">' + c + '</span>').join('')}</div>`;
    }
    html += '<div class="ipo-grid">';

    ipos.forEach(ipo => {
        const rating = ipo.overall_rating || 0;
        const buzz = ipo.social_buzz || 0;
        const instInterest = ipo.institutional_interest || 0;
        const risk = ipo.risk_level || 3;
        const riskLabel = risk <= 2 ? 'Low Risk' : risk <= 3 ? 'Medium Risk' : 'High Risk';
        const oType = ipo.opportunity_type || 'ipo';
        const vehicles = ipo.investment_vehicles || [];
        const vcBackers = ipo.vc_backers || [];
        const lastRound = ipo.last_funding_round || null;

        html += `
        <div class="ipo-card" data-type="${oType}">
            <div class="ipo-card-header">
                <div class="ipo-card-title">
                    <span class="ipo-type-badge" style="background:${_typeColor(oType)}">${_typeLabel(oType)}</span>
                    <span class="ipo-company">${ipo.company_name || 'Unknown'}</span>
                    <span class="ipo-ticker">${ipo.ticker || 'TBD'}</span>
                </div>
                <div class="ipo-card-stars">${_renderStars(rating)}</div>
            </div>
            <div class="ipo-card-sector">${ipo.sector || ''}</div>
            <div class="ipo-card-desc">${ipo.description || ''}</div>
            <div class="ipo-card-metrics">
                <div class="ipo-metric">
                    <span class="ipo-metric-label">${oType === 'vc' ? 'Timeline' : 'Expected Date'}</span>
                    <span class="ipo-metric-value">${ipo.expected_date || 'TBD'}</span>
                </div>
                <div class="ipo-metric">
                    <span class="ipo-metric-label">Valuation</span>
                    <span class="ipo-metric-value">${ipo.expected_valuation || 'TBD'}</span>
                </div>
                <div class="ipo-metric">
                    <span class="ipo-metric-label">${oType === 'ipo' ? 'Price Range' : 'Share Price'}</span>
                    <span class="ipo-metric-value">${ipo.expected_price_range || 'TBD'}</span>
                </div>
                <div class="ipo-metric">
                    <span class="ipo-metric-label">Risk</span>
                    <span class="ipo-metric-value" style="color:${_riskColor(risk)}">${riskLabel}</span>
                </div>
            </div>`;

        // VC Backers
        if (vcBackers.length > 0) {
            html += `<div class="ipo-vc-backers"><span class="ipo-vc-label">Backed by:</span> ${vcBackers.map(v => '<span class="ipo-vc-chip">' + v + '</span>').join('')}</div>`;
        }

        // Last Funding Round
        if (lastRound) {
            html += `<div class="ipo-last-round">${lastRound}</div>`;
        }

        html += `
            <div class="ipo-card-ratings">
                <div class="ipo-rating-bar">
                    <span class="ipo-rating-label">Social Buzz</span>
                    <div class="ipo-bar-track"><div class="ipo-bar-fill buzz" style="width:${buzz * 20}%"></div></div>
                    <span class="ipo-rating-tag">${_buzzLabel(buzz)}</span>
                </div>
                <div class="ipo-rating-bar">
                    <span class="ipo-rating-label">Institutional</span>
                    <div class="ipo-bar-track"><div class="ipo-bar-fill inst" style="width:${instInterest * 20}%"></div></div>
                    <span class="ipo-rating-tag">${instInterest}/5</span>
                </div>
                <div class="ipo-rating-bar">
                    <span class="ipo-rating-label">Market Fit</span>
                    <div class="ipo-bar-track"><div class="ipo-bar-fill fit" style="width:${(ipo.market_fit || 3) * 20}%"></div></div>
                    <span class="ipo-rating-tag">${ipo.market_fit || 3}/5</span>
                </div>
                <div class="ipo-rating-bar">
                    <span class="ipo-rating-label">Moat</span>
                    <div class="ipo-bar-track"><div class="ipo-bar-fill moat" style="width:${(ipo.moat || 3) * 20}%"></div></div>
                    <span class="ipo-rating-tag">${ipo.moat || 3}/5</span>
                </div>
            </div>`;

        // Investment Vehicles
        if (vehicles.length > 0) {
            html += `<div class="ipo-vehicles">
                <div class="ipo-vehicles-label">How to Invest</div>
                <div class="ipo-vehicles-grid">`;
            vehicles.forEach(v => {
                html += `<div class="ipo-vehicle-card">
                    <div class="ipo-vehicle-header">
                        <span class="ipo-vehicle-type">${_vehicleIcon(v.type)}</span>
                        <span class="ipo-vehicle-platform">${v.platform || 'Unknown'}</span>
                    </div>
                    <div class="ipo-vehicle-details">
                        <div class="ipo-vehicle-row"><span>Min Investment</span><span class="ipo-vehicle-val">${v.min_investment || 'Varies'}</span></div>
                        <div class="ipo-vehicle-row"><span>Expected Return</span><span class="ipo-vehicle-val positive">${v.expected_return || 'TBD'}</span></div>
                        <div class="ipo-vehicle-row">${_accredBadge(v.accreditation || 'none')}</div>
                    </div>
                    <div class="ipo-vehicle-notes">${v.access_notes || ''}</div>
                </div>`;
            });
            html += '</div></div>';
        }

        const redditMentions = ipo.reddit_mentions || 0;
        html += `
            <div class="ipo-card-social">
                <div class="ipo-social-label">Social Signals${redditMentions > 0 ? ' <span class="ipo-reddit-count">' + redditMentions + ' Reddit posts</span>' : ''}</div>
                <div class="ipo-social-text">${ipo.social_signals || 'No data'}</div>
            </div>
            <div class="ipo-card-reason">${ipo.rating_reason || ''}</div>
            <div class="ipo-card-footer">
                <div class="ipo-footer-col">
                    <div class="ipo-footer-label">Catalysts</div>
                    <ul class="ipo-footer-list">${(ipo.catalysts || []).map(c => '<li>' + c + '</li>').join('')}</ul>
                </div>
                <div class="ipo-footer-col">
                    <div class="ipo-footer-label">Key Risks</div>
                    <ul class="ipo-footer-list risk">${(ipo.key_risks || []).map(r => '<li>' + r + '</li>').join('')}</ul>
                </div>
            </div>
        </div>`;
    });

    html += '</div>';
    results.innerHTML = html;
}

// ─── Platforms Directory & Startup Guide ─────────────────────

function _platformCategoryLabel(cat) {
    return { ipo_access: 'IPO Access', pre_ipo: 'Pre-IPO / Secondary', startup: 'Startup Crowdfunding', vc_fund: 'VC Fund Access' }[cat] || cat;
}
function _platformCategoryColor(cat) {
    return { ipo_access: '#7c4dff', pre_ipo: '#ff9800', startup: '#4caf50', vc_fund: '#00bcd4' }[cat] || '#7c4dff';
}

function renderPlatformsDirectory(data) {
    const results = document.getElementById('ipo-results');
    const platforms = (data && data.platforms) || [];

    if (platforms.length === 0) {
        results.innerHTML = '<div style="text-align:center; padding:60px; color:#787b86;"><p>Platform data not available. Run a scan first.</p></div>';
        return;
    }

    // Group by category
    const groups = {};
    platforms.forEach(p => {
        const cat = p.category || 'other';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(p);
    });

    const order = ['ipo_access', 'pre_ipo', 'startup', 'vc_fund'];
    let html = '<div class="ipo-summary">Top 20 Investment Platforms &mdash; IPO Access, Pre-IPO Secondary, Startup Crowdfunding & VC Funds</div>';

    order.forEach(cat => {
        const items = groups[cat];
        if (!items || items.length === 0) return;
        const color = _platformCategoryColor(cat);
        html += `<div class="plat-section">
            <div class="plat-section-title" style="border-left:3px solid ${color}; padding-left:12px; color:${color};">${_platformCategoryLabel(cat)}</div>
            <div class="plat-grid">`;
        items.forEach(p => {
            const accredBadge = p.accreditation === 'none' ? '<span class="ipo-accred open">Open to All</span>'
                : p.accreditation === 'accredited' ? '<span class="ipo-accred accred">Accredited</span>'
                : '<span class="ipo-accred qp">Varies</span>';
            html += `<div class="plat-card">
                <div class="plat-card-header">
                    <div class="plat-name">${p.name}</div>
                    ${accredBadge}
                </div>
                <div class="plat-desc">${p.description}</div>
                <div class="plat-details">
                    <div class="plat-detail-row"><span>Min Investment</span><span class="plat-val">${p.min_investment}</span></div>
                    <div class="plat-detail-row"><span>Fees</span><span class="plat-val">${p.fees}</span></div>
                    <div class="plat-detail-row"><span>Liquidity</span><span class="plat-val">${p.liquidity}</span></div>
                    <div class="plat-detail-row"><span>Focus</span><span class="plat-val">${p.focus}</span></div>
                </div>
                <div class="plat-url">${p.url}</div>
            </div>`;
        });
        html += '</div></div>';
    });

    results.innerHTML = html;
}

function renderStartupGuide(data) {
    const results = document.getElementById('ipo-results');
    const guide = (data && data.startup_guide) || {};
    const steps = guide.getting_started || [];
    const channels = guide.connecting_with_startups || [];

    let html = '';

    // Getting Started section
    html += '<div class="guide-section"><div class="guide-section-title">Getting Started with Startup Investing</div>';
    html += '<div class="guide-steps">';
    steps.forEach(s => {
        html += `<div class="guide-step-card">
            <div class="guide-step-num">Step ${s.step}</div>
            <div class="guide-step-title">${s.title}</div>
            <div class="guide-step-desc">${s.description}</div>
            <ul class="guide-step-tips">${s.tips.map(t => '<li>' + t + '</li>').join('')}</ul>
        </div>`;
    });
    html += '</div></div>';

    // Connecting with Startups section
    html += '<div class="guide-section"><div class="guide-section-title">How to Connect with Startups &amp; Find Deals</div>';
    html += '<div class="guide-channels">';
    const channelIcons = {
        globe: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
        users: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
        calendar: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
        social: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 3a10.9 10.9 0 0 1-3.14 1.53 4.48 4.48 0 0 0-7.86 3v1A10.66 10.66 0 0 1 3 4s-4 9 5 13a11.64 11.64 0 0 1-7 2c9 5 20 0 20-11.5a4.5 4.5 0 0 0-.08-.83A7.72 7.72 0 0 0 23 3z"/></svg>',
        rocket: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/></svg>',
        pin: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>',
        search: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
        database: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>',
    };
    channels.forEach(ch => {
        const icon = channelIcons[ch.icon] || channelIcons.globe;
        html += `<div class="guide-channel-card">
            <div class="guide-channel-icon">${icon}</div>
            <div class="guide-channel-body">
                <div class="guide-channel-title">${ch.channel}</div>
                <div class="guide-channel-desc">${ch.description}</div>
                <div class="guide-channel-action">${ch.action}</div>
            </div>
        </div>`;
    });
    html += '</div></div>';

    // Quick links to platforms
    html += `<div class="guide-section">
        <div class="guide-section-title">Ready to Start?</div>
        <div class="guide-cta">
            <button class="btn-ipo-scan" onclick="filterIPOs('platforms')" style="padding:10px 24px;">Browse All 20 Platforms</button>
            <button class="btn-ipo-scan" onclick="filterIPOs('all'); loadIPOs(true);" style="padding:10px 24px; background:linear-gradient(135deg, #ff9800, #f57c00);">Scan Live Opportunities</button>
        </div>
    </div>`;

    results.innerHTML = html;
}
