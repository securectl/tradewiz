// ─── Status Page ─────────────────────────────────────────────

async function loadStatus() {
    try {
        const [statusResp, incidentsResp] = await Promise.all([
            fetch('/api/status'),
            fetch('/api/status/incidents'),
        ]);
        const statusData = await statusResp.json();
        const incidents = await incidentsResp.json();
        renderStatusPage(statusData, incidents);
    } catch (err) {
        document.getElementById('status-inner').innerHTML = `
            <div style="padding:60px; color:#ef5350; text-align:center;">
                <h3>Failed to load status</h3>
                <p style="color:#787b86; margin-top:8px;">${err.message}</p>
            </div>
        `;
    }
}

async function forceStatusCheck() {
    const btn = document.getElementById('btn-force-check');
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Checking...';
    }
    try {
        await fetch('/api/status/check', { method: 'POST' });
        await loadStatus();
    } catch (err) {
        console.error('Force check failed', err);
    }
    if (btn) {
        btn.disabled = false;
        btn.textContent = 'Force Check';
    }
}

function renderStatusPage(data, incidents) {
    const container = document.getElementById('status-inner');

    // Overall banner
    const bannerClass = data.overall === 'operational' ? 'operational' : data.overall === 'degraded' ? 'degraded' : 'outage';
    const bannerText = data.overall === 'operational' ? 'All Systems Operational' : data.overall === 'degraded' ? 'Service Degraded' : 'System Outage';

    let html = `
        <div class="status-banner ${bannerClass}">
            <div class="status-banner-dot"></div>
            <span>${bannerText}</span>
            <button class="btn-force-check" id="btn-force-check" onclick="forceStatusCheck()">Force Check</button>
        </div>
        <div class="status-cards-grid">
    `;

    // Service cards
    data.services.forEach(svc => {
        const dotClass = svc.status === 'operational' ? 'operational' : svc.status === 'degraded' ? 'degraded' : 'outage';
        const statusLabel = svc.status === 'operational' ? 'Operational' : svc.status === 'degraded' ? 'Degraded' : svc.status === 'outage' ? 'Outage' : 'Unknown';
        const rtText = svc.response_time_ms != null ? `${svc.response_time_ms}ms` : '-';
        const checkedText = svc.checked_at ? new Date(svc.checked_at).toLocaleTimeString() : 'Never';

        html += `
            <div class="status-card">
                <div class="status-card-header">
                    <div>
                        <div class="status-card-name">${svc.name}</div>
                        <div class="status-card-category">${svc.category}</div>
                    </div>
                    <div class="status-badge ${dotClass}">
                        <span class="status-dot ${dotClass}"></span>
                        ${statusLabel}
                    </div>
                </div>
                <div class="status-card-metrics">
                    <div><span class="status-metric-label">Response</span><span class="status-metric-value">${rtText}</span></div>
                    <div><span class="status-metric-label">Uptime (90d)</span><span class="status-metric-value">${svc.uptime_pct}%</span></div>
                    <div><span class="status-metric-label">Last Check</span><span class="status-metric-value">${checkedText}</span></div>
                </div>
                ${svc.error_message ? `<div class="status-error-msg">${svc.error_message}</div>` : ''}
                <div class="uptime-bar-row" id="uptime-bar-${svc.key}">
                    <div class="uptime-bar-loading">Loading uptime...</div>
                </div>
            </div>
        `;
    });

    html += '</div>';

    // Incidents section
    html += '<div class="status-incidents-section">';
    html += '<div class="status-section-title">Recent Incidents</div>';

    if (incidents.length === 0) {
        html += '<div class="status-no-incidents">No incidents in the last 90 days.</div>';
    } else {
        incidents.slice(0, 20).forEach(inc => {
            const typeClass = inc.incident_type === 'outage' ? 'outage' : 'degraded';
            const resolved = inc.resolved_at ? 'Resolved' : 'Ongoing';
            const resolvedClass = inc.resolved_at ? 'resolved' : 'ongoing';
            const duration = inc.duration_seconds ? formatDuration(inc.duration_seconds) : (inc.resolved_at ? '-' : 'Ongoing');
            const startTime = new Date(inc.started_at).toLocaleString();

            html += `
                <div class="incident-card ${typeClass}">
                    <div class="incident-header">
                        <span class="incident-service">${inc.service_display}</span>
                        <span class="incident-status ${resolvedClass}">${resolved}</span>
                    </div>
                    <div class="incident-detail">
                        <span class="incident-type">${inc.incident_type.toUpperCase()}</span>
                        <span class="incident-time">${startTime}</span>
                        <span class="incident-duration">Duration: ${duration}</span>
                    </div>
                    ${inc.error_message ? `<div class="incident-error">${inc.error_message}</div>` : ''}
                </div>
            `;
        });
    }
    html += '</div>';

    container.innerHTML = html;

    // Load uptime bars asynchronously
    data.services.forEach(svc => {
        loadUptimeBar(svc.key);
    });
}

function formatDuration(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
    return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

async function loadUptimeBar(serviceKey) {
    try {
        const resp = await fetch(`/api/status/uptime/${serviceKey}`);
        const days = await resp.json();
        const container = document.getElementById(`uptime-bar-${serviceKey}`);
        if (!container) return;

        let barHtml = '';
        days.forEach(d => {
            const color = d.total === 0 ? '#363a45' : d.uptime_pct >= 99 ? '#26a69a' : d.uptime_pct >= 90 ? '#ff9800' : '#ef5350';
            const title = `${d.date}: ${d.uptime_pct}% (${d.total} checks, ${d.failed} failed)`;
            barHtml += `<div class="uptime-bar-segment" style="background:${color};" title="${title}"></div>`;
        });

        container.innerHTML = `
            <div class="uptime-bar-segments">${barHtml}</div>
            <div class="uptime-bar-labels">
                <span>90 days ago</span>
                <span>Today</span>
            </div>
        `;
    } catch (err) {
        const container = document.getElementById(`uptime-bar-${serviceKey}`);
        if (container) container.innerHTML = '';
    }
}
