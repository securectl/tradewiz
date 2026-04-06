// ─── Research Skills ─────────────────────────────────────────────

let _skillCatalog = [];
let _selectedSkill = null;
let _activeSSE = {};  // job_id -> EventSource

async function loadSkillCatalog() {
    try {
        const resp = await fetch('/api/skills/catalog');
        const data = await resp.json();
        _skillCatalog = data.skills || [];
        renderSkillCatalog(_skillCatalog);
        populateDomainFilter(_skillCatalog);
    } catch (err) {
        console.error('Failed to load skill catalog', err);
        document.getElementById('research-catalog-list').innerHTML =
            '<div style="color:#ef5350; padding:20px;">Failed to load skills.</div>';
    }
}

function populateDomainFilter(skills) {
    const domains = [...new Set(skills.map(s => s.domain).filter(Boolean))];
    const sel = document.getElementById('research-domain-filter');
    sel.innerHTML = '<option value="">All Domains</option>';
    domains.forEach(d => {
        sel.innerHTML += `<option value="${d}">${d}</option>`;
    });
}

function filterSkillCatalog() {
    const domain = document.getElementById('research-domain-filter').value;
    const filtered = domain ? _skillCatalog.filter(s => s.domain === domain) : _skillCatalog;
    renderSkillCatalog(filtered);
}

function renderSkillCatalog(skills) {
    const container = document.getElementById('research-catalog-list');
    if (!skills.length) {
        container.innerHTML = '<div style="color:#787b86; padding:20px; text-align:center;">No skills available.</div>';
        return;
    }
    const tierColors = { free: '#26a69a', basic: '#2962ff', pro: '#f7b924' };
    container.innerHTML = skills.map(s => `
        <div class="skill-card ${s.available ? '' : 'skill-locked'} ${_selectedSkill && _selectedSkill.id === s.id ? 'skill-selected' : ''}"
             onclick="${s.available ? `selectSkill('${s.id}')` : `showPricingModal()`}">
            <div class="skill-card-header">
                <span class="skill-card-name">${s.name}</span>
                <span class="tier-badge" style="background:${tierColors[s.tier_required] || '#2a2e39'}; color:#fff; font-size:9px; padding:1px 5px;">
                    ${s.tier_required}
                </span>
            </div>
            <div class="skill-card-domain">${s.domain}</div>
            <div class="skill-card-desc">${s.description}</div>
            <div class="skill-card-meta">
                <span>${s.estimated_minutes} min</span>
                <span>${s.llm_calls_required} AI calls</span>
                ${!s.available ? '<span style="color:#ef5350;">Upgrade required</span>' : ''}
            </div>
        </div>
    `).join('');
}

function selectSkill(skillId) {
    _selectedSkill = _skillCatalog.find(s => s.id === skillId);
    if (!_selectedSkill) return;

    // Highlight selected card
    renderSkillCatalog(document.getElementById('research-domain-filter').value
        ? _skillCatalog.filter(s => s.domain === document.getElementById('research-domain-filter').value)
        : _skillCatalog);

    // Show launcher form
    document.getElementById('research-launcher-placeholder').style.display = 'none';
    document.getElementById('research-launcher-form').style.display = 'block';
    document.getElementById('research-skill-name').textContent = _selectedSkill.name;
    document.getElementById('research-skill-desc').textContent = _selectedSkill.description;
    document.getElementById('research-est-time').textContent = `~${_selectedSkill.estimated_minutes} min | ${_selectedSkill.llm_calls_required} AI calls`;

    const tierBadge = document.getElementById('research-skill-tier');
    tierBadge.textContent = _selectedSkill.tier_required;
    const tierColors = { free: '#26a69a', basic: '#2962ff', pro: '#f7b924' };
    tierBadge.style.background = tierColors[_selectedSkill.tier_required] || '#2a2e39';
    tierBadge.style.color = '#fff';

    // Build dynamic form
    buildSkillForm(_selectedSkill.input_schema);
}

function buildSkillForm(schema) {
    const container = document.getElementById('research-form-fields');
    container.innerHTML = schema.map(field => {
        if (field.type === 'select') {
            const options = (field.options || []).map(o =>
                `<option value="${o.value}" ${o.value === field.default ? 'selected' : ''}>${o.label}</option>`
            ).join('');
            return `
                <div class="research-form-group">
                    <label>${field.label}${field.required ? ' *' : ''}</label>
                    <select id="skill-input-${field.name}" class="skill-input">${options}</select>
                </div>`;
        }
        if (field.type === 'file') {
            return `
                <div class="research-form-group">
                    <label>${field.label}${field.required ? ' *' : ''}</label>
                    <div class="csv-upload-zone" id="csv-zone-${field.name}">
                        <input type="file" id="skill-input-${field.name}" accept="${field.accept || '.csv'}"
                               onchange="handleCsvUpload('${field.name}', this)" style="display:none;">
                        <button type="button" class="btn-sm" onclick="document.getElementById('skill-input-${field.name}').click()">
                            Choose File
                        </button>
                        <span class="csv-file-name" id="csv-name-${field.name}">${field.placeholder || 'No file selected'}</span>
                    </div>
                    <div class="csv-preview" id="csv-preview-${field.name}" style="display:none;"></div>
                </div>`;
        }
        return `
            <div class="research-form-group">
                <label>${field.label}${field.required ? ' *' : ''}</label>
                <input type="text" id="skill-input-${field.name}" class="skill-input"
                       placeholder="${field.placeholder || ''}" value="${field.default || ''}">
            </div>`;
    }).join('');
}

let _csvParsedData = {};

function handleCsvUpload(fieldName, input) {
    const file = input.files[0];
    const nameEl = document.getElementById(`csv-name-${fieldName}`);
    const previewEl = document.getElementById(`csv-preview-${fieldName}`);

    if (!file) {
        nameEl.textContent = 'No file selected';
        previewEl.style.display = 'none';
        delete _csvParsedData[fieldName];
        return;
    }

    nameEl.textContent = file.name;

    const reader = new FileReader();
    reader.onload = function(e) {
        const text = e.target.result;
        const parsed = parseCsv(text);
        _csvParsedData[fieldName] = parsed;

        // Show preview
        if (parsed.rows.length > 0) {
            const headers = parsed.headers;
            const previewRows = parsed.rows.slice(0, 5);
            let html = `<div class="csv-preview-info">${parsed.rows.length} rows, ${headers.length} columns</div>`;
            html += '<table class="csv-preview-table"><thead><tr>';
            headers.forEach(h => html += `<th>${h}</th>`);
            html += '</tr></thead><tbody>';
            previewRows.forEach(row => {
                html += '<tr>';
                headers.forEach(h => html += `<td>${row[h] || ''}</td>`);
                html += '</tr>';
            });
            if (parsed.rows.length > 5) html += `<tr><td colspan="${headers.length}" style="text-align:center;color:var(--text-secondary)">... ${parsed.rows.length - 5} more rows</td></tr>`;
            html += '</tbody></table>';
            previewEl.innerHTML = html;
            previewEl.style.display = 'block';

            // Auto-fill ticker field if CSV has a ticker/symbol column
            const tickerCol = headers.find(h => /^(ticker|symbol|stock)$/i.test(h.trim()));
            if (tickerCol) {
                const tickers = parsed.rows.map(r => r[tickerCol]).filter(Boolean).join(', ');
                const tickerInput = document.getElementById('skill-input-ticker');
                if (tickerInput && !tickerInput.value) {
                    tickerInput.value = tickers;
                }
            }
        }
    };
    reader.readAsText(file);
}

function parseCsv(text) {
    const lines = text.trim().split(/\r?\n/);
    if (lines.length < 2) return { headers: [], rows: [] };

    // Detect delimiter
    const firstLine = lines[0];
    const delimiter = firstLine.includes('\t') ? '\t' : ',';

    const headers = firstLine.split(delimiter).map(h => h.trim().replace(/^["']|["']$/g, ''));
    const rows = [];
    for (let i = 1; i < lines.length; i++) {
        const vals = lines[i].split(delimiter).map(v => v.trim().replace(/^["']|["']$/g, ''));
        if (vals.length === headers.length && vals.some(v => v)) {
            const row = {};
            headers.forEach((h, idx) => row[h] = vals[idx]);
            rows.push(row);
        }
    }
    return { headers, rows };
}

async function launchSkill() {
    if (!_selectedSkill) return;

    const inputs = {};
    _selectedSkill.input_schema.forEach(field => {
        if (field.type === 'file') {
            // Include parsed CSV data if uploaded
            if (_csvParsedData[field.name]) {
                inputs[field.name] = _csvParsedData[field.name];
            }
        } else {
            const el = document.getElementById(`skill-input-${field.name}`);
            if (el) inputs[field.name] = el.value.trim();
        }
    });

    // Validate required fields
    for (const field of _selectedSkill.input_schema) {
        if (field.type === 'file') {
            if (field.required && !_csvParsedData[field.name]) {
                alert(`Please upload: ${field.label}`);
                return;
            }
        } else if (field.required && !inputs[field.name]) {
            alert(`Please fill in: ${field.label}`);
            return;
        }
    }

    const btn = document.getElementById('btn-launch-skill');
    btn.disabled = true;
    btn.textContent = 'Launching...';

    try {
        const resp = await fetch('/api/skills/launch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ skill_id: _selectedSkill.id, inputs }),
        });
        const data = await resp.json();

        if (!resp.ok) {
            if (resp.status === 429) handle429(data);
            else alert(data.error || 'Failed to launch skill');
            return;
        }

        // Start SSE tracking
        trackJobProgress(data.job_id);
        loadSkillJobs();

    } catch (err) {
        console.error('Failed to launch skill', err);
        alert('Failed to launch skill');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Launch Analysis';
    }
}

function trackJobProgress(jobId) {
    // Close existing SSE for this job if any
    if (_activeSSE[jobId]) {
        _activeSSE[jobId].close();
    }

    const es = new EventSource(`/api/skills/jobs/${jobId}/stream`);
    _activeSSE[jobId] = es;

    es.addEventListener('status', (e) => updateJobInList(JSON.parse(e.data)));
    es.addEventListener('progress', (e) => updateJobInList(JSON.parse(e.data)));
    es.addEventListener('started', (e) => updateJobInList(JSON.parse(e.data)));
    es.addEventListener('completed', (e) => {
        updateJobInList(JSON.parse(e.data));
        es.close();
        delete _activeSSE[jobId];
    });
    es.addEventListener('failed', (e) => {
        updateJobInList(JSON.parse(e.data));
        es.close();
        delete _activeSSE[jobId];
    });
    es.addEventListener('cancelled', (e) => {
        updateJobInList(JSON.parse(e.data));
        es.close();
        delete _activeSSE[jobId];
    });
    es.addEventListener('done', (e) => {
        updateJobInList(JSON.parse(e.data));
        es.close();
        delete _activeSSE[jobId];
    });
    es.onerror = () => {
        es.close();
        delete _activeSSE[jobId];
        loadSkillJobs();
    };
}

async function loadSkillJobs() {
    try {
        const resp = await fetch('/api/skills/jobs');
        const data = await resp.json();
        renderSkillJobs(data.jobs || []);
    } catch (err) {
        console.error('Failed to load skill jobs', err);
    }
}

function renderSkillJobs(jobs) {
    const container = document.getElementById('research-jobs-list');
    const badge = document.getElementById('research-jobs-badge');
    const running = jobs.filter(j => j.status === 'running' || j.status === 'pending').length;
    if (badge) badge.textContent = running > 0 ? `${running} running` : (jobs.length > 0 ? `${jobs.length} total` : '');
    if (!jobs.length) {
        container.innerHTML = '<div style="color:#787b86; padding:20px; text-align:center; grid-column: 1 / -1;">No jobs yet. Launch a skill to get started.</div>';
        return;
    }
    container.innerHTML = jobs.map(j => {
        const skill = _skillCatalog.find(s => s.id === j.skill_id);
        const skillName = skill ? skill.name : j.skill_id;
        const ticker = j.inputs.ticker || '';
        const statusColor = {
            pending: '#787b86', running: '#2962ff',
            completed: '#26a69a', failed: '#ef5350', cancelled: '#787b86'
        }[j.status] || '#787b86';

        return `
            <div class="skill-job-card" id="skill-job-${j.id}">
                <div class="skill-job-header">
                    <span class="skill-job-name">${skillName}${ticker ? ` — ${ticker}` : ''}</span>
                    <span class="skill-job-status" style="color:${statusColor}">${j.status}</span>
                </div>
                ${j.status === 'running' ? `
                    <div class="skill-job-progress">
                        <div class="skill-job-progress-bar" style="width:${Math.round(j.progress * 100)}%"></div>
                    </div>
                    <div class="skill-job-message">${j.message || ''}</div>
                ` : ''}
                ${j.status === 'completed' ? `
                    <div class="skill-job-actions">
                        <button class="btn-sm btn-green" onclick="viewSkillResult('${j.id}')">View</button>
                        <button class="btn-sm" onclick="downloadSkillResult('${j.id}', 'docx')">DOCX</button>
                        <button class="btn-sm" onclick="downloadSkillResult('${j.id}', 'xlsx')">XLSX</button>
                        <button class="btn-sm btn-red" onclick="deleteSkillJob('${j.id}')">Delete</button>
                    </div>
                ` : ''}
                ${j.status === 'failed' ? `
                    <div class="skill-job-error">${j.error || 'Unknown error'}</div>
                    <div class="skill-job-actions">
                        <button class="btn-sm btn-red" onclick="deleteSkillJob('${j.id}')">Delete</button>
                    </div>
                ` : ''}
                ${j.status === 'running' || j.status === 'pending' ? `
                    <div class="skill-job-actions">
                        <button class="btn-sm btn-red" onclick="cancelSkillJob('${j.id}')">Cancel</button>
                    </div>
                ` : ''}
            </div>`;
    }).join('');
}

function updateJobInList(jobData) {
    const card = document.getElementById(`skill-job-${jobData.id}`);
    if (!card) {
        // Card doesn't exist yet — do a full reload once
        loadSkillJobs();
        return;
    }

    const skill = _skillCatalog.find(s => s.id === jobData.skill_id);
    const skillName = skill ? skill.name : jobData.skill_id;
    const ticker = jobData.inputs && jobData.inputs.ticker || '';
    const statusColor = {
        pending: '#787b86', running: '#2962ff',
        completed: '#26a69a', failed: '#ef5350', cancelled: '#787b86'
    }[jobData.status] || '#787b86';

    // Update status badge
    const statusEl = card.querySelector('.skill-job-status');
    if (statusEl) {
        statusEl.textContent = jobData.status;
        statusEl.style.color = statusColor;
    }

    // Update progress bar and message for running jobs
    if (jobData.status === 'running') {
        let progressDiv = card.querySelector('.skill-job-progress');
        let messageDiv = card.querySelector('.skill-job-message');
        const actionsDiv = card.querySelector('.skill-job-actions');

        if (!progressDiv) {
            // Insert progress bar after header
            const header = card.querySelector('.skill-job-header');
            progressDiv = document.createElement('div');
            progressDiv.className = 'skill-job-progress';
            progressDiv.innerHTML = '<div class="skill-job-progress-bar"></div>';
            header.after(progressDiv);

            messageDiv = document.createElement('div');
            messageDiv.className = 'skill-job-message';
            progressDiv.after(messageDiv);
        }
        const bar = progressDiv.querySelector('.skill-job-progress-bar');
        if (bar) bar.style.width = Math.round(jobData.progress * 100) + '%';
        if (messageDiv) messageDiv.textContent = jobData.message || '';

        // Ensure cancel button exists
        if (!actionsDiv) {
            const ad = document.createElement('div');
            ad.className = 'skill-job-actions';
            ad.innerHTML = `<button class="btn-sm btn-red" onclick="cancelSkillJob('${jobData.id}')">Cancel</button>`;
            card.appendChild(ad);
        }
    }

    // Terminal states: re-render the card fully for action buttons
    if (['completed', 'failed', 'cancelled'].includes(jobData.status)) {
        loadSkillJobs();
    }
}

async function viewSkillResult(jobId) {
    try {
        const resp = await fetch(`/api/skills/jobs/${jobId}/result`);
        const data = await resp.json();
        if (!resp.ok) { alert(data.error || 'Failed to load result'); return; }
        showSkillResultModal(data.result, data.output_files);
    } catch (err) {
        console.error('Failed to load skill result', err);
    }
}

function showSkillResultModal(result, outputFiles) {
    // Remove existing modal
    const existing = document.getElementById('skill-result-modal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'skill-result-modal';
    modal.className = 'modal-overlay';
    modal.onclick = (e) => { if (e.target === modal) modal.remove(); };

    const meta = result._meta || {};
    let html = `
        <div class="modal-content skill-result-modal-content">
            <div class="modal-header">
                <h2>${meta.skill_id || 'Research Report'}: ${meta.ticker || ''}</h2>
                <button class="modal-close" onclick="document.getElementById('skill-result-modal').remove()">&times;</button>
            </div>
            <div class="skill-result-body">`;

    // Render each result section
    for (const [key, value] of Object.entries(result)) {
        if (key === '_meta' || key.endsWith('_error')) continue;
        html += `<div class="skill-result-section">
            <h3>${key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</h3>
            <div class="skill-result-content">${renderResultValue(value)}</div>
        </div>`;
    }

    html += `</div></div>`;
    modal.innerHTML = html;
    document.body.appendChild(modal);
}

function renderResultValue(val) {
    if (val === null || val === undefined) return '<span style="color:#787b86">N/A</span>';
    if (typeof val === 'string') return `<p>${val}</p>`;
    if (typeof val === 'number') return `<span class="skill-result-number">${val}</span>`;
    if (typeof val === 'boolean') return `<span>${val ? 'Yes' : 'No'}</span>`;
    if (Array.isArray(val)) {
        if (!val.length) return '<span style="color:#787b86">None</span>';
        if (typeof val[0] === 'string') return '<ul>' + val.map(v => `<li>${v}</li>`).join('') + '</ul>';
        return '<pre>' + JSON.stringify(val, null, 2) + '</pre>';
    }
    if (typeof val === 'object') {
        let html = '<table class="skill-result-table">';
        for (const [k, v] of Object.entries(val)) {
            const label = k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            if (typeof v === 'object' && v !== null && !Array.isArray(v)) {
                html += `<tr><td colspan="2" style="font-weight:bold; padding-top:8px;">${label}</td></tr>`;
                for (const [sk, sv] of Object.entries(v)) {
                    const sublabel = sk.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                    html += `<tr><td style="padding-left:16px;">${sublabel}</td><td>${renderSimpleValue(sv)}</td></tr>`;
                }
            } else {
                html += `<tr><td>${label}</td><td>${renderSimpleValue(v)}</td></tr>`;
            }
        }
        html += '</table>';
        return html;
    }
    return String(val);
}

function renderSimpleValue(v) {
    if (v === null || v === undefined) return '<span style="color:#787b86">N/A</span>';
    if (Array.isArray(v)) return v.join(', ');
    if (typeof v === 'number') {
        if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
        if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
        if (Math.abs(v) < 1 && v !== 0) return `${(v * 100).toFixed(1)}%`;
        return v.toFixed(2);
    }
    return String(v);
}

function downloadSkillResult(jobId, fmt) {
    window.open(`/api/skills/jobs/${jobId}/download/${fmt}`, '_blank');
}

async function cancelSkillJob(jobId) {
    try {
        await fetch(`/api/skills/jobs/${jobId}`, { method: 'DELETE' });
        loadSkillJobs();
    } catch (err) {
        console.error('Failed to cancel job', err);
    }
}

async function deleteSkillJob(jobId) {
    try {
        await fetch(`/api/skills/jobs/${jobId}`, { method: 'DELETE' });
        loadSkillJobs();
    } catch (err) {
        console.error('Failed to delete job', err);
    }
}

// ─── Fin Skills Hub ─────────────────────────────────────────────

const FIN_SKILLS_BLUEPRINT = {
    "Financial Analysis": {
        icon: "chart-bar",
        color: "#2962ff",
        skills: [
            { id: "dcf-valuation", name: "DCF Valuation", description: "Discounted cash flow model with sensitivity analysis", tier: "pro" },
            { id: "comparable-analysis", name: "Comparable Analysis", description: "Trading comps with multiples benchmarking", tier: "pro" },
            { id: "financial-health", name: "Financial Health Check", description: "Balance sheet strength and liquidity analysis", tier: "free" },
            { id: "3-statement-model", name: "3-Statement Model", description: "Integrated IS/BS/CF financial model", tier: "pro" },
            { id: "lbo-model", name: "LBO Model", description: "Leveraged buyout returns analysis", tier: "pro" },
            { id: "competitive-analysis", name: "Competitive Analysis", description: "Market positioning and competitor benchmarking", tier: "pro" },
            { id: "debug-model", name: "Model Debugger", description: "Audit financial models for errors and inconsistencies", tier: "pro" }
        ]
    },
    "Equity Research": {
        icon: "search",
        color: "#26a69a",
        skills: [
            { id: "earnings-analysis", name: "Earnings Analysis", description: "Quarterly earnings deep-dive with beat/miss analysis", tier: "free" },
            { id: "sector-analysis", name: "Sector Analysis", description: "Industry overview with competitive landscape", tier: "free" },
            { id: "initiating-coverage", name: "Initiating Coverage", description: "Full initiation report with valuation and thesis", tier: "pro" },
            { id: "earnings-preview", name: "Earnings Preview", description: "Pre-earnings scenario analysis and expectations", tier: "pro" },
            { id: "morning-note", name: "Morning Note", description: "Daily market brief and key developments", tier: "pro" },
            { id: "model-update", name: "Model Update", description: "Update financial model with new data points", tier: "pro" },
            { id: "investment-thesis", name: "Investment Thesis", description: "Bull/bear thesis with catalyst identification", tier: "pro" },
            { id: "catalyst-calendar", name: "Catalyst Calendar", description: "Track upcoming events and price catalysts", tier: "pro" },
            { id: "stock-screen", name: "Stock Screen", description: "Quantitative screening with custom criteria", tier: "pro" }
        ]
    },
    "Investment Banking": {
        icon: "briefcase",
        color: "#f7b924",
        skills: [
            { id: "teaser", name: "Deal Teaser", description: "Anonymous one-page teaser for sell-side processes", tier: "pro" },
            { id: "cim", name: "CIM Draft", description: "Confidential Information Memorandum creation", tier: "pro" },
            { id: "merger-model", name: "Merger Model", description: "Accretion/dilution analysis for M&A transactions", tier: "pro" },
            { id: "buyer-list", name: "Buyer List", description: "Strategic and financial buyer universe mapping", tier: "pro" },
            { id: "process-letter", name: "Process Letter", description: "Bid instructions and process timeline drafting", tier: "pro" },
            { id: "strip-profile", name: "Company Profile", description: "One-page company strip for pitch books", tier: "pro" },
            { id: "deal-tracker", name: "Deal Tracker", description: "Live deal pipeline tracking and review", tier: "pro" }
        ]
    },
    "Private Equity": {
        icon: "trending-up",
        color: "#ab47bc",
        skills: [
            { id: "ic-memo", name: "IC Memo", description: "Investment committee memorandum drafting", tier: "pro" },
            { id: "screen-deal", name: "Deal Screening", description: "Inbound deal evaluation from CIM or teaser", tier: "pro" },
            { id: "value-creation", name: "Value Creation Plan", description: "Post-acquisition operational improvement roadmap", tier: "pro" },
            { id: "dd-prep", name: "DD Meeting Prep", description: "Diligence meeting and expert call preparation", tier: "pro" },
            { id: "unit-economics", name: "Unit Economics", description: "ARR cohorts, LTV/CAC, and retention analysis", tier: "pro" },
            { id: "returns-analysis", name: "Returns Analysis", description: "IRR/MOIC sensitivity tables and waterfall", tier: "pro" },
            { id: "dd-checklist", name: "DD Checklist", description: "Comprehensive due diligence checklist generation", tier: "pro" },
            { id: "portfolio-review", name: "Portfolio Review", description: "Portfolio company performance monitoring", tier: "pro" },
            { id: "deal-sourcing", name: "Deal Sourcing", description: "Company discovery and founder outreach", tier: "pro" }
        ]
    },
    "Wealth Management": {
        icon: "shield",
        color: "#ef5350",
        skills: [
            { id: "investment-proposal", name: "Investment Proposal", description: "Client prospect investment proposal creation", tier: "pro" },
            { id: "portfolio-rebalance", name: "Portfolio Rebalance", description: "Drift analysis and rebalancing trade generation", tier: "pro" },
            { id: "financial-plan", name: "Financial Plan", description: "Comprehensive financial planning and projections", tier: "pro" },
            { id: "tax-loss-harvest", name: "Tax-Loss Harvesting", description: "Identify tax-loss harvesting opportunities", tier: "pro" },
            { id: "client-report", name: "Client Report", description: "Performance report generation for client meetings", tier: "pro" },
            { id: "client-review", name: "Client Review Prep", description: "Preparation materials for client review meetings", tier: "pro" }
        ]
    }
};

let _finSkillsLoaded = false;

async function loadFinSkills() {
    if (_finSkillsLoaded) return;
    try {
        const resp = await fetch('/api/skills/catalog');
        const data = await resp.json();
        const liveCatalog = data.skills || [];
        renderFinSkillsDomains(liveCatalog);
        _finSkillsLoaded = true;
    } catch (err) {
        console.error('Failed to load fin skills catalog', err);
        renderFinSkillsDomains([]);
        _finSkillsLoaded = true;
    }
}

function renderFinSkillsDomains(liveCatalog) {
    const container = document.getElementById('finskills-domains');
    const liveMap = {};
    liveCatalog.forEach(s => { liveMap[s.id] = s; });

    let html = '';
    for (const [domainName, domain] of Object.entries(FIN_SKILLS_BLUEPRINT)) {
        const liveCount = domain.skills.filter(s => liveMap[s.id]).length;
        const totalCount = domain.skills.length;

        html += `<div class="finskills-domain-card">
            <div class="finskills-domain-header" onclick="toggleFinDomain(this)" style="border-left: 4px solid ${domain.color};">
                <div class="finskills-domain-title">
                    ${getFinSkillIcon(domain.icon, domain.color)}
                    <span>${domainName}</span>
                    <span class="finskills-count-badge" style="background:${domain.color}20; color:${domain.color};">${liveCount}/${totalCount} available</span>
                </div>
                <svg class="finskills-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
            <div class="finskills-skill-grid" style="display:none;">
                ${domain.skills.map(skill => renderFinSkillCard(skill, liveMap[skill.id], liveMap)).join('')}
            </div>
        </div>`;
    }
    container.innerHTML = html;
}

function renderFinSkillCard(skill, liveSkill, liveMap) {
    let statusClass, badge, onclick;
    if (liveSkill && liveSkill.available) {
        statusClass = 'finskill-live';
        badge = '<span class="finskill-badge finskill-badge-live">Available</span>';
        onclick = `onclick="openFinSkill('${skill.id}')"`;
    } else if (liveSkill && !liveSkill.available) {
        statusClass = 'finskill-locked';
        badge = '<span class="finskill-badge finskill-badge-locked">Upgrade Required</span>';
        onclick = `onclick="showPricingModal()"`;
    } else {
        statusClass = 'finskill-coming';
        badge = '<span class="finskill-badge finskill-badge-coming">Coming Soon</span>';
        onclick = '';
    }

    return `<div class="finskill-card ${statusClass}" ${onclick}>
        <div class="finskill-card-top">
            <span class="finskill-name">${skill.name}</span>
            ${badge}
        </div>
        <p class="finskill-desc">${skill.description}</p>
        <span class="finskill-tier">${skill.tier === 'free' ? 'Free' : 'Pro'}</span>
    </div>`;
}

function toggleFinDomain(header) {
    const grid = header.nextElementSibling;
    const chevron = header.querySelector('.finskills-chevron');
    const isOpen = grid.style.display !== 'none';
    grid.style.display = isOpen ? 'none' : 'grid';
    chevron.style.transform = isOpen ? '' : 'rotate(180deg)';
}

function openFinSkill(skillId) {
    switchTab('research');
    setTimeout(() => selectSkill(skillId), 100);
}

function filterFinSkills() {
    const query = document.getElementById('finskills-search-input').value.toLowerCase().trim();
    const cards = document.querySelectorAll('.finskills-domain-card');

    cards.forEach(card => {
        const header = card.querySelector('.finskills-domain-header');
        const grid = card.querySelector('.finskills-skill-grid');
        const chevron = header.querySelector('.finskills-chevron');
        const skillCards = grid.querySelectorAll('.finskill-card');
        let domainHasMatch = false;

        skillCards.forEach(sc => {
            const name = sc.querySelector('.finskill-name').textContent.toLowerCase();
            const desc = sc.querySelector('.finskill-desc').textContent.toLowerCase();
            const match = !query || name.includes(query) || desc.includes(query);
            sc.style.display = match ? '' : 'none';
            if (match) domainHasMatch = true;
        });

        card.style.display = domainHasMatch ? '' : 'none';
        if (query && domainHasMatch) {
            grid.style.display = 'grid';
            chevron.style.transform = 'rotate(180deg)';
        } else if (!query) {
            grid.style.display = 'none';
            chevron.style.transform = '';
        }
    });
}

function getFinSkillIcon(name, color) {
    const icons = {
        'chart-bar': `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><rect x="3" y="12" width="4" height="9"/><rect x="10" y="7" width="4" height="14"/><rect x="17" y="3" width="4" height="18"/></svg>`,
        'search': `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
        'briefcase': `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/></svg>`,
        'trending-up': `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>`,
        'shield': `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`
    };
    return icons[name] || '';
}

