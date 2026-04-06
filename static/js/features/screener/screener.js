// ─── Screener ────────────────────────────────────────────────

let screenerRunning = false;
let screenerCategory = 'lowcap';
let screenerCache = {};  // keyed by category + sectors combo
const SCREENER_CACHE_TTL = 30 * 60 * 1000;  // 30 minutes

const SCREENER_CATEGORY_CONFIG = {
    lowcap: { label: 'Low-Cap', banner: 'Small-cap stocks ($2-$15) carry elevated risk. Never allocate more than 2% of your account per position. AI vetting does not guarantee safety.', showPrice: true, minDefault: 2, maxDefault: 15 },
    midcap: { label: 'Mid-Cap', banner: 'Mid-cap stocks ($15-$100) offer balanced growth/value. Position sizing up to 5% of account per position.', showPrice: true, minDefault: 15, maxDefault: 100 },
    largecap: { label: 'Large-Cap', banner: 'Large-cap growth stocks ($50+). Growth-focused analysis evaluating revenue acceleration, earnings momentum, and competitive moat.', showPrice: false },
    etf: { label: 'ETFs', banner: 'Growth-focused ETFs. Analysis evaluates sector momentum, thematic tailwinds, expense efficiency, and holdings quality.', showPrice: false },
    metals_mining: { label: 'Metals & Mining', banner: 'Mining stocks are commodity-sensitive. Prices fluctuate with gold, silver, copper, lithium, and uranium markets.', showPrice: false },
    crypto: { label: 'Crypto', banner: 'Cryptocurrencies are highly volatile. Prices can swing 10-20% in a day. Never invest more than you can afford to lose.', showPrice: false },
    ai: { label: 'Artificial Intelligence', banner: 'AI stocks carry high growth expectations and elevated valuations. Many are priced for perfection — earnings misses can cause sharp drops.', showPrice: false },
    gainers: { label: 'Top Gainers', banner: 'Top gaining stocks across all major indices. AI evaluates momentum sustainability, overbought risk, and continuation probability.', showPrice: false, showTimeframe: true },
    losers: { label: 'Top Losers', banner: 'Top losing stocks across all major indices. AI evaluates oversold recovery potential vs falling knife risk, balance sheet strength, and support levels.', showPrice: false, showTimeframe: true },
};

// ─── Sector Filter ───────────────────────────────────────────
const SECTOR_OPTIONS = {
    lowcap: ["Technology","Healthcare","Energy","Financial Services","Industrials","Consumer Cyclical","Basic Materials","Real Estate","Cannabis","Communication Services"],
    midcap: ["Technology","Healthcare","Financial Services","Industrials","Consumer Cyclical","Communication Services","Energy"],
    largecap: ["Technology","Healthcare","Financial Services","Industrials","Consumer Cyclical","Communication Services","Energy","Semiconductors"],
    etf: ["Growth","Semiconductors","AI/Robotics","Cybersecurity","Clean Energy","Biotech","Blockchain","Thematic"],
    metals_mining: ["Gold","Silver","Copper","Lithium","Uranium","Diversified","Royalty/Streaming"],
    crypto: ["Layer 1","Layer 2","DeFi","Meme","Infrastructure"],
    ai: ["Pure-Play AI","AI Chips/Infra","AI Cloud/SaaS","AI Tools","AI Robotics","AI Security"],
    gainers: ["Technology","Healthcare","Financial Services","Energy","Industrials","Consumer Cyclical","Basic Materials","Communication Services"],
    losers: ["Technology","Healthcare","Financial Services","Energy","Industrials","Consumer Cyclical","Basic Materials","Communication Services"],
};
let selectedSectors = [];
let screenerTimeframe = '1d';

function getScreenerCacheKey() {
    const config = SCREENER_CATEGORY_CONFIG[screenerCategory];
    const base = screenerCategory + '|' + selectedSectors.slice().sort().join(',');
    return config && config.showTimeframe ? base + '|' + screenerTimeframe : base;
}

function renderSectorPills(category) {
    const container = document.getElementById('sector-pills');
    if (!container) return;
    const options = SECTOR_OPTIONS[category] || [];
    selectedSectors = [];
    let html = '<button class="sector-pill clear-pill" onclick="clearSectorFilter()">Clear All</button>';
    options.forEach(s => {
        html += `<button class="sector-pill" data-sector="${s}" onclick="toggleSectorPill(this, '${s}')">${s}</button>`;
    });
    container.innerHTML = html;
}

function toggleSectorPill(el, sector) {
    const idx = selectedSectors.indexOf(sector);
    if (idx >= 0) {
        selectedSectors.splice(idx, 1);
        el.classList.remove('active');
    } else {
        selectedSectors.push(sector);
        el.classList.add('active');
    }
}

function clearSectorFilter() {
    selectedSectors = [];
    document.querySelectorAll('.sector-pill').forEach(p => p.classList.remove('active'));
}

// ─── Hot Sectors ─────────────────────────────────────────────
let hotSectorsCache = {};  // keyed by period
const HOT_SECTORS_CACHE_TTL = 60 * 60 * 1000;  // 60 minutes
let hotSectorsPeriod = '1mo';
let hotSectorsLoading = false;

function initHotSectorButtons() {
    document.querySelectorAll('#hot-sectors-periods .period-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const period = btn.dataset.period;
            document.querySelectorAll('#hot-sectors-periods .period-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            hotSectorsPeriod = period;
            loadHotSectors(period);
        });
    });
}

async function loadHotSectors(period, force = false) {
    if (hotSectorsLoading) return;

    const cached = hotSectorsCache[period];
    if (!force && cached && (Date.now() - cached.fetchedAt < HOT_SECTORS_CACHE_TTL)) {
        renderHotSectors(cached.data, true, cached.fetchedAt);
        return;
    }

    hotSectorsLoading = true;
    const grid = document.getElementById('hot-sectors-grid');
    grid.innerHTML = `
        <div style="grid-column: 1/-1; padding:20px; text-align:center;">
            <div class="spinner" style="margin:0 auto 12px;"></div>
            <div style="color:#787b86; font-size:12px;">Identifying trending sectors with AI...</div>
        </div>`;

    try {
        const resp = await fetch(`/api/screener/hot-sectors?period=${period}`);
        const data = await resp.json();
        if (data.error && data.sectors.length === 0) {
            grid.innerHTML = `<div style="grid-column:1/-1; padding:20px; color:#ef5350; text-align:center; font-size:13px;">${data.error}</div>`;
        } else {
            hotSectorsCache[period] = { data, fetchedAt: Date.now() };
            renderHotSectors(data, false);
        }
        loadBillingStatus(); // Refresh usage gauge after LLM call
    } catch (err) {
        grid.innerHTML = `<div style="grid-column:1/-1; padding:20px; color:#ef5350; text-align:center; font-size:13px;">Failed to load hot sectors: ${err.message}</div>`;
    }

    hotSectorsLoading = false;
}

function renderHotSectors(data, fromCache = false, fetchedAt = null) {
    const grid = document.getElementById('hot-sectors-grid');
    const sectors = data.sectors || [];

    if (sectors.length === 0) {
        grid.innerHTML = '<div style="grid-column:1/-1; padding:20px; color:#787b86; text-align:center; font-size:13px;">No trending sectors found.</div>';
        return;
    }

    let html = '';
    sectors.forEach(s => {
        const trendClass = (s.trend || 'neutral').toLowerCase();
        const trendArrow = trendClass === 'bullish' ? '↑' : trendClass === 'bearish' ? '↓' : '→';
        const momClass = (s.momentum || 'moderate').toLowerCase();

        const catalystsHtml = (s.catalysts || []).slice(0, 3).map(c => `<li>${c}</li>`).join('');
        const tickersHtml = (s.top_tickers || []).map(t =>
            `<span class="hot-sector-ticker" onclick="analyzeFromScreener('${t}')">${t}</span>`
        ).join('');

        html += `
            <div class="hot-sector-card">
                <div class="hot-sector-rank">${s.rank || ''}</div>
                <div class="hot-sector-name">${s.name || 'Unknown'}</div>
                <span class="hot-sector-trend ${trendClass}">${trendArrow} ${s.trend || 'neutral'}</span>
                <span class="hot-sector-momentum ${momClass}">${s.momentum || 'moderate'}</span>
                ${catalystsHtml ? `<ul class="hot-sector-catalysts">${catalystsHtml}</ul>` : ''}
                ${tickersHtml ? `<div class="hot-sector-tickers">${tickersHtml}</div>` : ''}
                ${s.outlook ? `<div class="hot-sector-outlook">${s.outlook}</div>` : ''}
            </div>`;
    });

    if (fromCache && fetchedAt) {
        const ageMin = Math.floor((Date.now() - fetchedAt) / 60000);
        const ageText = ageMin < 1 ? 'Just fetched' : `Cached ${ageMin}m ago`;
        html += `<div class="hot-sectors-cache-note" style="grid-column:1/-1;">${ageText} · <a href="#" onclick="loadHotSectors('${data.period}', true); return false;" style="color:#2962ff; text-decoration:underline;">Refresh</a></div>`;
    }

    grid.innerHTML = html;
}

function initScreenerTimeframeButtons() {
    document.querySelectorAll('#screener-timeframe-bar .timeframe-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#screener-timeframe-bar .timeframe-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            screenerTimeframe = btn.dataset.timeframe;
        });
    });
}

function switchScreenerCategory(cat) {
    screenerCategory = cat;
    document.querySelectorAll('.screener-category-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.category === cat);
    });

    const config = SCREENER_CATEGORY_CONFIG[cat];
    document.getElementById('screener-risk-banner').textContent = config.banner;

    // Update sector pills for this category
    renderSectorPills(cat);

    // Show/hide price controls
    const minGroup = document.getElementById('screener-min-group');
    const maxGroup = document.getElementById('screener-max-group');
    if (config.showPrice) {
        minGroup.style.display = '';
        maxGroup.style.display = '';
        document.getElementById('screener-min-price').value = config.minDefault;
        document.getElementById('screener-max-price').value = config.maxDefault;
    } else {
        minGroup.style.display = 'none';
        maxGroup.style.display = 'none';
    }

    // Show/hide timeframe bar for gainers/losers
    const timeframeBar = document.getElementById('screener-timeframe-bar');
    if (timeframeBar) {
        timeframeBar.style.display = config.showTimeframe ? 'flex' : 'none';
    }

    // Update risk banner color
    const banner = document.getElementById('screener-risk-banner');
    if (cat === 'lowcap') {
        banner.style.background = 'rgba(239, 83, 80, 0.1)';
        banner.style.borderColor = 'rgba(239, 83, 80, 0.3)';
        banner.style.color = '#ef5350';
    } else if (cat === 'crypto') {
        banner.style.background = 'rgba(247, 185, 36, 0.1)';
        banner.style.borderColor = 'rgba(247, 185, 36, 0.3)';
        banner.style.color = '#f7b924';
    } else if (cat === 'metals_mining') {
        banner.style.background = 'rgba(205, 127, 50, 0.1)';
        banner.style.borderColor = 'rgba(205, 127, 50, 0.3)';
        banner.style.color = '#cd7f32';
    } else if (cat === 'ai') {
        banner.style.background = 'rgba(224, 64, 251, 0.1)';
        banner.style.borderColor = 'rgba(224, 64, 251, 0.3)';
        banner.style.color = '#e040fb';
    } else if (cat === 'midcap') {
        banner.style.background = 'rgba(255, 152, 0, 0.1)';
        banner.style.borderColor = 'rgba(255, 152, 0, 0.3)';
        banner.style.color = '#ff9800';
    } else if (cat === 'gainers') {
        banner.style.background = 'rgba(38, 166, 154, 0.1)';
        banner.style.borderColor = 'rgba(38, 166, 154, 0.3)';
        banner.style.color = '#26a69a';
    } else if (cat === 'losers') {
        banner.style.background = 'rgba(239, 83, 80, 0.1)';
        banner.style.borderColor = 'rgba(239, 83, 80, 0.3)';
        banner.style.color = '#ef5350';
    } else {
        banner.style.background = 'rgba(41, 98, 255, 0.08)';
        banner.style.borderColor = 'rgba(41, 98, 255, 0.3)';
        banner.style.color = '#2962ff';
    }

    // Show cached results if available for this category + sector combo
    const cacheKey = getScreenerCacheKey();
    const cached = screenerCache[cacheKey];
    if (cached && (Date.now() - cached.timestamp < SCREENER_CACHE_TTL)) {
        renderScreenerResults(cached.data, true);
    }
}

async function runScreener(force = false) {
    if (screenerRunning) return;

    // Check cache first (unless forcing a new scan)
    const cacheKey = getScreenerCacheKey();
    const cached = screenerCache[cacheKey];
    if (!force && cached && (Date.now() - cached.timestamp < SCREENER_CACHE_TTL)) {
        renderScreenerResults(cached.data, true);
        return;
    }

    screenerRunning = true;

    const btn = document.getElementById('btn-screener-run');
    const results = document.getElementById('screener-results');

    btn.disabled = true;
    btn.textContent = 'Scanning...';

    const catLabel = SCREENER_CATEGORY_CONFIG[screenerCategory].label;
    results.innerHTML = `
        <div style="text-align:center; padding:60px;">
            <div class="spinner" style="margin:0 auto 16px;"></div>
            <div style="color:#787b86; font-size:13px;">Scanning ${catLabel} candidates and running AI vetting...</div>
            <div style="color:#787b86; font-size:11px; margin-top:8px;">This may take 30-60 seconds</div>
        </div>
    `;

    const minPrice = parseFloat(document.getElementById('screener-min-price').value) || 2;
    const maxPrice = parseFloat(document.getElementById('screener-max-price').value) || 15;
    const limit = parseInt(document.getElementById('screener-limit').value) || 20;

    try {
        const resp = await fetch('/api/screener', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ min_price: minPrice, max_price: maxPrice, limit, category: screenerCategory, sectors: selectedSectors, timeframe: screenerTimeframe }),
        });

        if (resp.status === 429) {
            const errData = await resp.json();
            handle429(errData);
            loadBillingStatus();
            return;
        }

        const text = await resp.text();
        let data;
        try {
            data = JSON.parse(text);
        } catch (parseErr) {
            throw new Error(resp.status === 200 ? 'Server returned an invalid response. Please try again.' : `Server error (${resp.status}). Please try again.`);
        }

        if (data.error) {
            throw new Error(data.error);
        }

        // Cache the results with sector-aware key
        screenerCache[getScreenerCacheKey()] = { data, timestamp: Date.now() };

        renderScreenerResults(data, false);
    } catch (err) {
        results.innerHTML = `
            <div style="text-align:center; padding:60px; color:#ef5350;">
                <h3>Screener Error</h3>
                <p style="color:#787b86; margin-top:8px;">${err.message}</p>
            </div>
        `;
    }

    btn.disabled = false;
    btn.textContent = 'Scan & Vet with AI';
    screenerRunning = false;
}

function renderScreenerResults(data, fromCache = false) {
    const results = document.getElementById('screener-results');
    const cat = data.category || screenerCategory;

    if (data.error && data.candidates_scanned === 0) {
        results.innerHTML = `
            <div style="text-align:center; padding:60px; color:#ef5350;">
                <h3>No Results</h3>
                <p style="color:#787b86; margin-top:8px;">${data.error}</p>
            </div>
        `;
        return;
    }

    // Category-specific labels
    const positiveLabel = cat === 'largecap' ? 'Strong Growth' : cat === 'etf' ? 'Strong Buy' : cat === 'crypto' ? 'Bullish' : cat === 'gainers' ? 'Momentum Buy' : cat === 'losers' ? 'Recovery Buy' : 'Opportunities';
    const cautiousLabel = cat === 'largecap' ? 'Steady' : cat === 'etf' ? 'Accumulate' : cat === 'crypto' ? 'Neutral' : cat === 'gainers' ? 'Watch' : cat === 'losers' ? 'Watch' : 'Risky';

    // Cache age indicator
    let cacheIndicator = '';
    if (fromCache) {
        const cacheKey = getScreenerCacheKey();
        const cached = screenerCache[cacheKey];
        if (cached) {
            const ageMin = Math.floor((Date.now() - cached.timestamp) / 60000);
            const ageText = ageMin < 1 ? 'Just scanned' : `Cached ${ageMin} min ago`;
            cacheIndicator = ` · <span style="color:#42a5f5;">${ageText}</span> · <a href="#" onclick="runScreener(true); return false;" style="color:#2962ff; text-decoration:underline; cursor:pointer;">Rescan</a>`;
        }
    }

    let html = `
        <div class="screener-summary">
            Scanned ${data.candidates_scanned} candidates |
            <span style="color:#26a69a;">${data.opportunities.length} ${positiveLabel}</span> |
            <span style="color:#ff9800;">${data.risky.length} ${cautiousLabel}</span> |
            <span style="color:#ef5350;">${data.avoided} Avoided</span>
            ${cacheIndicator}
        </div>
    `;

    if (data.opportunities.length > 0) {
        html += `<div class="screener-section-title" style="color:#26a69a;">${positiveLabel}</div>`;
        html += '<div class="screener-grid">';
        data.opportunities.forEach(c => { html += buildScreenerCard(c, 'opportunity', cat); });
        html += '</div>';
    }

    if (data.risky.length > 0) {
        const cautiousSubtitle = cat === 'largecap' ? 'Steady — Moderate Growth' : cat === 'etf' ? 'Accumulate — Gradual Position' : 'Risky — Proceed with Caution';
        html += `<div class="screener-section-title" style="color:#ff9800;">${cautiousSubtitle}</div>`;
        html += '<div class="screener-grid">';
        data.risky.forEach(c => { html += buildScreenerCard(c, 'risky', cat); });
        html += '</div>';
    }

    if (data.opportunities.length === 0 && data.risky.length === 0) {
        html += `<div style="text-align:center; padding:40px; color:#787b86;">No viable opportunities found. All candidates were filtered by AI vetting.</div>`;
    }

    results.innerHTML = html;
}

function buildScreenerCard(c, type, cat) {
    cat = cat || screenerCategory;
    const verdictColor = type === 'opportunity' ? '#26a69a' : '#ff9800';

    // Category-specific catalysts/risks field names
    const catalystList = c.growth_catalysts || c.catalysts || [];
    const flagList = c.red_flags || c.risks || [];
    const catalysts = catalystList.slice(0, 3).map(g => `<span class="ai-tag bullish">${g}</span>`).join('');
    const flags = flagList.slice(0, 3).map(f => `<span class="ai-tag bearish">${f}</span>`).join('');

    // Category-specific metrics (with glossary tooltips)
    let metricsHtml = '';
    if (cat === 'largecap') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Rev Growth")}</div><div class="metric-value" style="color:#26a69a;">${c.revenue_growth_trend || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Earnings Growth", "Earnings")}</div><div class="metric-value" style="color:#42a5f5;">${c.earnings_momentum || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Moat")}</div><div class="metric-value">${c.moat_strength || 'N/A'}</div></div>
            </div>`;
    } else if (cat === 'etf') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Momentum")}</div><div class="metric-value" style="color:#26a69a;">${c.sector_momentum || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">Theme</div><div class="metric-value" style="color:#42a5f5;">${c.thematic_strength || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Expense Ratio", "Expense")}</div><div class="metric-value">${c.expense_efficiency || c.expense_ratio || 'N/A'}</div></div>
            </div>`;
    } else if (cat === 'midcap') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Rev Growth", "Rev Trend")}</div><div class="metric-value" style="color:#26a69a;">${c.revenue_growth_trend || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Upside")}</div><div class="metric-value" style="color:#26a69a;">+${c.upside_pct || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Risk")}</div><div class="metric-value" style="color:${(c.risk_score || 0) >= 60 ? '#ef5350' : (c.risk_score || 0) >= 40 ? '#ff9800' : '#26a69a'};">${c.risk_score || 0}</div></div>
            </div>`;
    } else if (cat === 'metals_mining') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">Commodity</div><div class="metric-value" style="color:#f7b924;">${c.commodity_outlook || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">Production</div><div class="metric-value" style="color:#26a69a;">${c.production_trend || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Upside")}</div><div class="metric-value" style="color:#26a69a;">+${c.upside_pct || 0}%</div></div>
            </div>`;
    } else if (cat === 'crypto') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">Network</div><div class="metric-value" style="color:#42a5f5;">${c.network_strength || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">Adoption</div><div class="metric-value" style="color:#26a69a;">${c.adoption_trend || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Upside")}</div><div class="metric-value" style="color:#26a69a;">+${c.upside_pct || 0}%</div></div>
            </div>`;
    } else if (cat === 'ai') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">AI Exposure</div><div class="metric-value" style="color:#e040fb;">${c.ai_exposure || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">Growth</div><div class="metric-value" style="color:#26a69a;">${c.growth_trajectory || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Risk")}</div><div class="metric-value" style="color:${(c.risk_score || 0) >= 60 ? '#ef5350' : (c.risk_score || 0) >= 40 ? '#ff9800' : '#26a69a'};">${c.risk_score || 0}</div></div>
            </div>`;
    } else if (cat === 'gainers') {
        const chgColor = (c.pct_change || 0) >= 0 ? '#26a69a' : '#ef5350';
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">Change</div><div class="metric-value" style="color:${chgColor}; font-size:16px; font-weight:bold;">${c.pct_change >= 0 ? '+' : ''}${(c.pct_change || 0).toFixed(2)}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">Momentum</div><div class="metric-value" style="color:#26a69a;">${c.momentum_quality || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">Overbought</div><div class="metric-value" style="color:${c.overbought_risk === 'HIGH' ? '#ef5350' : c.overbought_risk === 'MEDIUM' ? '#ff9800' : '#26a69a'};">${c.overbought_risk || 'N/A'}</div></div>
            </div>`;
    } else if (cat === 'losers') {
        const chgColor = (c.pct_change || 0) >= 0 ? '#26a69a' : '#ef5350';
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">Change</div><div class="metric-value" style="color:${chgColor}; font-size:16px; font-weight:bold;">${c.pct_change >= 0 ? '+' : ''}${(c.pct_change || 0).toFixed(2)}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">Oversold</div><div class="metric-value" style="color:#42a5f5;">${c.oversold_level || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">Balance Sheet</div><div class="metric-value" style="color:${c.balance_sheet === 'WEAK' || c.balance_sheet === 'CRITICAL' ? '#ef5350' : '#26a69a'};">${c.balance_sheet || 'N/A'}</div></div>
            </div>`;
    } else {
        // lowcap
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Survival")}</div><div class="metric-value" style="color:${(c.survival_12m || 0) >= 80 ? '#26a69a' : (c.survival_12m || 0) >= 60 ? '#ff9800' : '#ef5350'};">${c.survival_12m || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Upside")}</div><div class="metric-value" style="color:#26a69a;">+${c.upside_pct || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Risk")}</div><div class="metric-value" style="color:${(c.risk_score || 0) >= 60 ? '#ef5350' : (c.risk_score || 0) >= 40 ? '#ff9800' : '#26a69a'};">${c.risk_score || 0}</div></div>
            </div>`;
    }

    // Category-specific details (with glossary tooltips)
    let detailHtml = '';
    if (cat === 'lowcap') {
        detailHtml = `
            <div class="indicator-row"><span class="indicator-label">${explainTerm("Dilution")}</span><span class="indicator-value" style="font-size:11px;">${c.dilution_risk || 'N/A'}</span></div>
            <div class="indicator-row"><span class="indicator-label">${explainTerm("Liquidity")}</span><span class="indicator-value" style="font-size:11px;">${c.liquidity_risk || 'N/A'}</span></div>
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    } else if (cat === 'midcap') {
        detailHtml = `
            <div class="indicator-row"><span class="indicator-label">${explainTerm("Earnings Growth", "Earnings")}</span><span class="indicator-value" style="font-size:11px;">${c.earnings_momentum || 'N/A'}</span></div>
            <div class="indicator-row"><span class="indicator-label">${explainTerm("Moat")}</span><span class="indicator-value" style="font-size:11px;">${c.moat_strength || 'N/A'}</span></div>
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    } else if (cat === 'largecap') {
        detailHtml = `
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}
            ${c.upside_pct ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Upside")}</span><span class="indicator-value" style="font-size:11px; color:#26a69a;">+${c.upside_pct}%</span></div>` : ''}
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    } else if (cat === 'etf') {
        detailHtml = `
            ${c.ytd_return ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("YTD Return")}</span><span class="indicator-value" style="font-size:11px; color:#26a69a;">${c.ytd_return}</span></div>` : ''}
            ${c.expense_ratio ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Expense Ratio")}</span><span class="indicator-value" style="font-size:11px;">${c.expense_ratio}</span></div>` : ''}
            <div class="indicator-row"><span class="indicator-label">Holdings Quality</span><span class="indicator-value" style="font-size:11px;">${c.top_holdings_quality || 'N/A'}</span></div>
            ${c.target_allocation_pct ? `<div class="indicator-row"><span class="indicator-label">Target Allocation</span><span class="indicator-value" style="font-size:11px;">${c.target_allocation_pct}%</span></div>` : ''}`;
    } else if (cat === 'metals_mining') {
        detailHtml = `
            <div class="indicator-row"><span class="indicator-label">Reserve Quality</span><span class="indicator-value" style="font-size:11px;">${c.reserve_quality || 'N/A'}</span></div>
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}
            <div class="indicator-row"><span class="indicator-label">${explainTerm("Risk")}</span><span class="indicator-value" style="font-size:11px; color:${(c.risk_score || 0) >= 60 ? '#ef5350' : (c.risk_score || 0) >= 40 ? '#ff9800' : '#26a69a'};">${c.risk_score || 0}</span></div>
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    } else if (cat === 'crypto') {
        detailHtml = `
            <div class="indicator-row"><span class="indicator-label">Tokenomics</span><span class="indicator-value" style="font-size:11px;">${c.tokenomics_rating || 'N/A'}</span></div>
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}`;
    } else if (cat === 'ai') {
        detailHtml = `
            <div class="indicator-row"><span class="indicator-label">Competitive</span><span class="indicator-value" style="font-size:11px;">${c.competitive_position || 'N/A'}</span></div>
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}
            ${c.upside_pct ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Upside")}</span><span class="indicator-value" style="font-size:11px; color:#26a69a;">+${c.upside_pct}%</span></div>` : ''}
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    } else if (cat === 'gainers') {
        detailHtml = `
            ${c.catalyst ? `<div class="indicator-row"><span class="indicator-label">Catalyst</span><span class="indicator-value" style="font-size:11px;">${c.catalyst}</span></div>` : ''}
            ${c.reversal_probability != null ? `<div class="indicator-row"><span class="indicator-label">Reversal Risk</span><span class="indicator-value" style="font-size:11px; color:${c.reversal_probability > 50 ? '#ef5350' : '#26a69a'};">${c.reversal_probability}%</span></div>` : ''}
            ${c.continuation_target ? `<div class="indicator-row"><span class="indicator-label">Target</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.continuation_target}</span></div>` : ''}
            ${c.support_level ? `<div class="indicator-row"><span class="indicator-label">Support</span><span class="indicator-value" style="font-size:11px;">$${c.support_level}</span></div>` : ''}
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    } else if (cat === 'losers') {
        detailHtml = `
            ${c.decline_reason ? `<div class="indicator-row"><span class="indicator-label">Decline Reason</span><span class="indicator-value" style="font-size:11px;">${c.decline_reason}</span></div>` : ''}
            ${c.recovery_catalyst ? `<div class="indicator-row"><span class="indicator-label">Recovery Catalyst</span><span class="indicator-value" style="font-size:11px;">${c.recovery_catalyst}</span></div>` : ''}
            ${c.support_level ? `<div class="indicator-row"><span class="indicator-label">Support</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.support_level}</span></div>` : ''}
            ${c.downside_remaining != null ? `<div class="indicator-row"><span class="indicator-label">Downside Left</span><span class="indicator-value" style="font-size:11px; color:#ef5350;">-${c.downside_remaining}%</span></div>` : ''}
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    }

    return `
        <div class="screener-card ${type}">
            <div class="screener-card-header">
                <div>
                    <span class="screener-ticker">${c.ticker}</span>
                    <span class="screener-name">${c.name || ''}</span>
                </div>
                <span class="screener-verdict" style="color:${verdictColor};">${c.verdict}</span>
            </div>
            <div class="screener-card-price">
                <span>$${c.price}</span>
                <span class="screener-sector">${c.sector || ''}</span>
            </div>
            ${metricsHtml}
            <div class="screener-card-detail">${detailHtml}</div>
            ${catalysts ? `<div style="margin-top:6px;"><div style="font-size:9px; color:#787b86; margin-bottom:3px;">CATALYSTS</div>${catalysts}</div>` : ''}
            ${flags ? `<div style="margin-top:4px;"><div style="font-size:9px; color:#787b86; margin-bottom:3px;">${cat === 'lowcap' ? 'RED FLAGS' : 'RISKS'}</div>${flags}</div>` : ''}
            <div style="margin-top:8px; font-size:11px; color:#787b86; line-height:1.5;">${c.summary || ''}</div>
            <button class="btn-screener-analyze" onclick="analyzeFromScreener('${c.ticker}')">Full Analysis</button>
        </div>
    `;
}

function analyzeFromScreener(ticker) {
    // Switch to analyzer tab and run analysis
    switchTab('analyzer');
    document.getElementById('ticker-input').value = ticker;
    analyzeTicker(ticker, document.getElementById('period-select').value, document.getElementById('interval-select').value);
}

// ─── Qullamaggie Breakout Scanner ─────────────────────────────

let qullamaggieRunning = false;

function showQullamaggieSection() {
    // Legacy no-op — Qullamaggie is now its own tab
}

function hideQullamaggieSection() {
    // Legacy no-op — Qullamaggie is now its own tab
}

async function runQullamaggieScan() {
    if (qullamaggieRunning) return;
    qullamaggieRunning = true;
    _updateQullamaggieTabBadge();

    const btn = document.getElementById('btn-qullamaggie-scan');
    const results = document.getElementById('qullamaggie-results');
    const universe = document.getElementById('qullamaggie-universe').value;

    btn.disabled = true;
    btn.textContent = 'Scanning...';
    results.innerHTML = `
        <div style="text-align:center; padding:30px;">
            <div class="spinner" style="margin:0 auto 12px;"></div>
            <div style="color:#787b86; font-size:12px;">Scanning for Qullamaggie setups (HTF, VCP, EP)...</div>
            <div style="color:#787b86; font-size:11px; margin-top:4px;">This may take 1-2 minutes — safe to switch tabs</div>
        </div>`;

    try {
        const resp = await fetch('/api/qullamaggie', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ category: universe }),
        });
        const data = await resp.json();
        renderQullamaggieResults(data);
    } catch (err) {
        results.innerHTML = `<div style="text-align:center; padding:30px; color:#ef5350;">Scan failed: ${err.message}</div>`;
    }

    btn.disabled = false;
    btn.textContent = 'Scan for Setups';
    qullamaggieRunning = false;
    _updateQullamaggieTabBadge();
}

function _updateQullamaggieTabBadge() {
    const scannerBtn = document.querySelector('.tab-btn[data-tab="qullamaggie"]');
    if (!scannerBtn) return;
    if (qullamaggieRunning) {
        if (!scannerBtn.querySelector('.tab-badge')) {
            scannerBtn.insertAdjacentHTML('beforeend', '<span class="tab-badge scanning"></span>');
        }
    } else {
        const badge = scannerBtn.querySelector('.tab-badge');
        if (badge) badge.remove();
    }
}

function renderQullamaggieResults(data) {
    const container = document.getElementById('qullamaggie-results');
    const results = data.results || [];

    if (results.length === 0) {
        container.innerHTML = `
            <div style="text-align:center; padding:30px; color:#787b86;">
                No qualifying setups found out of ${data.scanned || 0} stocks scanned.
                <br><span style="font-size:11px;">Try again later — setups appear when stocks are consolidating after strong moves.</span>
            </div>`;
        return;
    }

    let html = `<div style="font-size:12px; color:#787b86; margin-bottom:12px;">Found ${results.length} setups from ${data.scanned} stocks scanned</div>`;
    html += '<div class="qullamaggie-grid">';

    results.forEach(r => {
        const badgeClass = r.setup_type === 'HTF' ? 'setup-badge-htf' : r.setup_type === 'VCP' ? 'setup-badge-vcp' : 'setup-badge-ep';
        const scoreColor = r.score >= 8 ? '#26a69a' : r.score >= 6 ? '#ff9800' : '#787b86';

        html += `
            <div class="qullamaggie-card" onclick="analyzeFromScreener('${r.ticker}')">
                <div class="qullamaggie-card-header">
                    <div>
                        <span class="qullamaggie-ticker">${r.ticker}</span>
                        <span class="qullamaggie-name">${r.name || ''}</span>
                    </div>
                    <span class="${badgeClass}">${r.setup_type}</span>
                </div>
                <div class="qullamaggie-card-price">$${r.current_price} <span style="color:#787b86; font-size:11px;">${r.sector || ''}</span></div>
                <div class="qullamaggie-card-metrics">
                    <div class="qullamaggie-metric"><div class="metric-label">Score</div><div class="metric-value" style="color:${scoreColor};">${r.score}/10</div></div>
                    <div class="qullamaggie-metric"><div class="metric-label">Entry</div><div class="metric-value" style="color:#2962ff;">$${r.entry_price}</div></div>
                    <div class="qullamaggie-metric"><div class="metric-label">Stop</div><div class="metric-value" style="color:#ef5350;">$${r.stop_loss}</div></div>
                    <div class="qullamaggie-metric"><div class="metric-label">ADR%</div><div class="metric-value">${r.adr_pct}%</div></div>
                </div>
                <div class="qullamaggie-card-detail">
                    <div class="indicator-row"><span class="indicator-label">1M RS</span><span class="indicator-value" style="font-size:11px; color:${r.rel_strength_1m >= 25 ? '#26a69a' : 'inherit'};">${r.rel_strength_1m}%</span></div>
                    <div class="indicator-row"><span class="indicator-label">3M RS</span><span class="indicator-value" style="font-size:11px; color:${r.rel_strength_3m >= 50 ? '#26a69a' : 'inherit'};">${r.rel_strength_3m}%</span></div>
                    <div class="indicator-row"><span class="indicator-label">Vol Ratio</span><span class="indicator-value" style="font-size:11px;">${r.volume_ratio}x</span></div>
                    <div class="indicator-row"><span class="indicator-label">$ Vol</span><span class="indicator-value" style="font-size:11px;">${r.dollar_volume}M</span></div>
                    ${r.consolidation_days ? `<div class="indicator-row"><span class="indicator-label">Consol.</span><span class="indicator-value" style="font-size:11px;">${r.consolidation_days}d / -${r.consolidation_depth_pct}%</span></div>` : ''}
                    ${r.prior_move_pct ? `<div class="indicator-row"><span class="indicator-label">Prior Move</span><span class="indicator-value" style="font-size:11px; color:#26a69a;">+${r.prior_move_pct}%</span></div>` : ''}
                    <div class="indicator-row"><span class="indicator-label">MA Aligned</span><span class="indicator-value" style="font-size:11px; color:${r.ma_aligned ? '#26a69a' : '#ef5350'};">${r.ma_aligned ? 'Yes' : 'No'}</span></div>
                </div>
                <div style="margin-top:6px; padding:6px 8px; background:rgba(41,98,255,0.08); border-radius:4px; font-size:10px; color:#787b86;">
                    ${r.shares} shares @ $${r.entry_price} = $${r.position_value} | Risk: $${r.risk_amount}
                    <br>${r.sell_plan}
                </div>
            </div>`;
    });

    html += '</div>';
    container.innerHTML = html;
}
