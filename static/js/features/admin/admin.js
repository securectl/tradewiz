// ─── Admin Panel Functions ──────────────────────────────────

async function loadAdminUsers() {
    try {
        const resp = await fetch('/api/admin/users');
        if (!resp.ok) return;
        const users = await resp.json();
        const tbody = document.getElementById('admin-users-body');
        if (!tbody) return;
        if (!users.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="bot-empty">No users</td></tr>';
            return;
        }
        tbody.innerHTML = users.map(u => {
            const tier = u.tier || 'free';
            const isAdmin = (u.roles || []).includes('admin');
            const botAccess = (u.bot_access || 'none').split(',').map(b => b.trim()).filter(b => b && b !== 'none');
            const hasCrypto = botAccess.includes('crypto');
            const hasStock = botAccess.includes('stock');
            const hasWatchdog = botAccess.includes('watchdog');
            const isPro = tier === 'pro';
            return `
            <tr data-user-id="${u.id}" data-tier="${tier}" data-bot-crypto="${hasCrypto}" data-bot-stock="${hasStock}" data-bot-watchdog="${hasWatchdog}">
                <td>
                    <div>${u.name || '—'}${u.is_locked ? ' <span style="color:var(--accent-red);font-size:10px;font-weight:700">LOCKED</span>' : ''}</div>
                    <div style="font-size:10px;color:var(--text-secondary)">${u.email}</div>
                </td>
                <td>
                    <div class="tier-toggle-group" data-user="${u.id}"${isAdmin ? ' style="opacity:0.5;pointer-events:none"' : ''}>
                        <button class="tier-toggle-btn${tier === 'free' ? ' active' : ''}" data-tier="free" onclick="setUserTier(${u.id}, 'free', this)">Free</button>
                        <button class="tier-toggle-btn${tier === 'basic' ? ' active' : ''}" data-tier="basic" onclick="setUserTier(${u.id}, 'basic', this)">Basic</button>
                        <button class="tier-toggle-btn${tier === 'pro' ? ' active' : ''}" data-tier="pro" onclick="setUserTier(${u.id}, 'pro', this)">Pro</button>
                    </div>
                    ${isAdmin ? '<span class="invite-tier tier-admin" style="margin-left:4px">Admin</span>' : ''}
                </td>
                <td>
                    <div class="bot-toggle-group" id="bot-toggles-${u.id}" style="${isPro && !isAdmin ? '' : isAdmin ? '' : 'opacity:0.4;pointer-events:none'}">
                        <button class="bot-toggle-btn${hasCrypto ? ' active' : ''}" data-bot="crypto" onclick="toggleBotAccess(${u.id}, 'crypto', this)">Crypto</button>
                        <button class="bot-toggle-btn${hasStock ? ' active' : ''}" data-bot="stock" onclick="toggleBotAccess(${u.id}, 'stock', this)">Stock</button>
                        <button class="bot-toggle-btn${hasWatchdog ? ' active' : ''}" data-bot="watchdog" onclick="toggleBotAccess(${u.id}, 'watchdog', this)">ThunderBot</button>
                    </div>
                </td>
                <td>
                    ${u.last_login ? `<div>${new Date(u.last_login).toLocaleDateString()}</div><div style="font-size:10px;color:var(--text-secondary)">${new Date(u.last_login).toLocaleTimeString()}</div>` : '<span style="color:var(--text-secondary)">Never</span>'}
                </td>
                <td style="display:flex;gap:4px;align-items:center;flex-wrap:wrap;">
                    <button class="btn-save-user" id="save-user-${u.id}" onclick="saveUserSettings(${u.id})" style="display:none">Save</button>
                    ${!isAdmin ? `<button class="btn-lock-user" onclick="toggleUserLock(${u.id}, ${!u.is_locked})" style="padding:4px 8px;font-size:10px;border:1px solid ${u.is_locked ? 'var(--accent-green)' : 'var(--accent-red)'};background:transparent;color:${u.is_locked ? 'var(--accent-green)' : 'var(--accent-red)'};border-radius:4px;cursor:pointer;" title="${u.is_locked ? 'Unlock account' : 'Lock account'}">${u.is_locked ? 'Unlock' : 'Lock'}</button>` : ''}
                    ${!isAdmin ? `<button onclick="deleteUser(${u.id}, '${(u.email || '').replace(/'/g, "\\'")}')" style="padding:4px 8px;font-size:10px;border:1px solid var(--accent-red);background:var(--accent-red);color:#fff;border-radius:4px;cursor:pointer;" title="Permanently delete user (cascades to all their data)">Delete</button>` : ''}
                </td>
            </tr>`;
        }).join('');
    } catch (e) {
        console.error('Failed to load admin users:', e);
    }
}

async function toggleUserLock(userId, lock) {
    const action = lock ? 'lock' : 'unlock';
    if (!confirm(`Are you sure you want to ${action} this account?`)) return;
    try {
        const resp = await fetch(`/api/admin/users/${userId}/lock`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ lock }),
        });
        const data = await resp.json();
        if (data.ok) {
            loadAdminUsers();
        } else {
            alert(data.error || `Failed to ${action} account`);
        }
    } catch (e) {
        alert(`Failed to ${action} account: ${e.message}`);
    }
}

async function deleteUser(userId, email) {
    // Two confirms — destructive, cascades to all the user's bot trades, journal,
    // searches, etc. via ON DELETE CASCADE FKs.
    if (!confirm(`Permanently delete user ${email}?\n\nThis cascades to ALL their data: trades, journal, configs, history. There is no undo.`)) return;
    const typed = prompt(`Type the email exactly to confirm:\n${email}`);
    if (typed !== email) {
        if (typed !== null) alert('Email did not match — delete cancelled.');
        return;
    }
    try {
        const resp = await fetch(`/api/admin/users/${userId}/delete`, { method: 'DELETE' });
        const data = await resp.json();
        if (data.ok) {
            loadAdminUsers();
        } else {
            alert(data.error || 'Delete failed');
        }
    } catch (e) {
        alert(`Delete failed: ${e.message}`);
    }
}


// ─── Global Bot Defaults (admin-only) ───────────────────────

async function loadAdminBotDefaults() {
    const tbody = document.getElementById('admin-bot-defaults-body');
    if (!tbody) return;
    try {
        const resp = await fetch('/api/admin/bot-defaults');
        if (!resp.ok) return;
        const data = await resp.json();
        const defaults = data.defaults || [];
        if (!defaults.length) {
            tbody.innerHTML = '<tr><td colspan="3" style="padding:14px;color:var(--text-secondary);text-align:center;">No global defaults set. Each user falls back to hardcoded engine defaults.</td></tr>';
            return;
        }
        tbody.innerHTML = defaults.map(d => `
            <tr>
                <td style="padding:6px 10px;font-family:monospace;font-size:12px;color:var(--text-bright);">${d.key}</td>
                <td style="padding:6px 10px;"><input type="text" id="bd-${d.key}" value="${(d.value || '').replace(/"/g, '&quot;')}" style="width:100%;padding:4px 8px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:4px;font-size:12px;font-family:monospace;"/></td>
                <td style="padding:6px 10px;display:flex;gap:6px;">
                    <button onclick="saveBotDefault('${d.key}')" class="btn-analyze" style="padding:4px 10px;font-size:11px;">Save</button>
                    <button onclick="deleteBotDefault('${d.key}')" style="padding:4px 10px;font-size:11px;border:1px solid var(--accent-red);background:transparent;color:var(--accent-red);border-radius:4px;cursor:pointer;">Clear</button>
                </td>
            </tr>`).join('');
    } catch (e) {
        console.error('loadAdminBotDefaults failed:', e);
    }
}

async function saveBotDefault(key) {
    const val = (document.getElementById(`bd-${key}`) || {}).value;
    if (val == null) return;
    try {
        const resp = await fetch('/api/admin/bot-defaults', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, value: val }),
        });
        const data = await resp.json();
        if (!data.ok) alert(data.error || 'Save failed');
        else loadAdminBotDefaults();
    } catch (e) {
        alert(`Save failed: ${e.message}`);
    }
}

async function deleteBotDefault(key) {
    if (!confirm(`Clear global default for ${key}?\nUsers fall back to hardcoded engine defaults; per-user overrides remain.`)) return;
    try {
        await fetch(`/api/admin/bot-defaults/${encodeURIComponent(key)}`, { method: 'DELETE' });
        loadAdminBotDefaults();
    } catch (e) {
        alert(`Clear failed: ${e.message}`);
    }
}

async function addBotDefault() {
    const key = (document.getElementById('bd-new-key') || {}).value;
    const val = (document.getElementById('bd-new-value') || {}).value;
    if (!key) { alert('Key is required'); return; }
    try {
        const resp = await fetch('/api/admin/bot-defaults', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: key.trim(), value: val || '' }),
        });
        const data = await resp.json();
        if (!data.ok) alert(data.error || 'Add failed');
        else {
            document.getElementById('bd-new-key').value = '';
            document.getElementById('bd-new-value').value = '';
            loadAdminBotDefaults();
        }
    } catch (e) {
        alert(`Add failed: ${e.message}`);
    }
}


function setUserTier(userId, tier, btn) {
    const group = btn.parentElement;
    group.querySelectorAll('.tier-toggle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    // Enable/disable bot toggles based on tier
    const botGroup = document.getElementById('bot-toggles-' + userId);
    if (botGroup) {
        if (tier === 'pro') {
            botGroup.style.opacity = '';
            botGroup.style.pointerEvents = '';
        } else {
            botGroup.style.opacity = '0.4';
            botGroup.style.pointerEvents = 'none';
            // Deactivate bot toggles
            botGroup.querySelectorAll('.bot-toggle-btn').forEach(b => b.classList.remove('active'));
        }
    }
    // Show save button
    const saveBtn = document.getElementById('save-user-' + userId);
    if (saveBtn) saveBtn.style.display = '';
}

function toggleBotAccess(userId, bot, btn) {
    btn.classList.toggle('active');
    const saveBtn = document.getElementById('save-user-' + userId);
    if (saveBtn) saveBtn.style.display = '';
}

async function saveUserSettings(userId) {
    const row = document.querySelector(`tr[data-user-id="${userId}"]`);
    if (!row) return;
    const activeT = row.querySelector('.tier-toggle-btn.active');
    const tier = activeT ? activeT.dataset.tier : 'free';
    const botAccess = [];
    row.querySelectorAll('.bot-toggle-btn.active').forEach(b => botAccess.push(b.dataset.bot));
    const saveBtn = document.getElementById('save-user-' + userId);
    try {
        if (saveBtn) { saveBtn.textContent = '...'; saveBtn.disabled = true; }
        const resp = await fetch('/api/admin/users/' + userId + '/tier', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tier, bot_access: botAccess }),
        });
        const data = await resp.json();
        if (data.ok) {
            if (saveBtn) { saveBtn.textContent = 'Saved'; setTimeout(() => { saveBtn.style.display = 'none'; saveBtn.textContent = 'Save'; saveBtn.disabled = false; }, 1200); }
        } else {
            alert(data.error || 'Save failed');
            if (saveBtn) { saveBtn.textContent = 'Save'; saveBtn.disabled = false; }
        }
    } catch (e) {
        alert('Save failed: ' + e.message);
        if (saveBtn) { saveBtn.textContent = 'Save'; saveBtn.disabled = false; }
    }
}

async function loadAdminInvites() {
    try {
        const resp = await fetch('/api/admin/invite');
        if (!resp.ok) return;
        const data = await resp.json();
        const list = document.getElementById('admin-invite-list');
        if (!list) return;
        if (!data.invites || !data.invites.length) {
            list.innerHTML = '<div style="font-size:12px; color:var(--text-secondary); padding:8px 0;">No pending invites</div>';
            return;
        }
        list.innerHTML = data.invites.map(inv => {
            const tier = inv.tier || 'free';
            const botAccess = (inv.bot_access || 'none').split(',').map(b => b.trim()).filter(b => b && b !== 'none');
            const hasCrypto = botAccess.includes('crypto');
            const hasStock = botAccess.includes('stock');
            const isPro = tier === 'pro';
            const accepted = inv.accepted_at;
            const esc = inv.email.replace(/'/g, "\\'");
            return `
            <div class="admin-invite-item" data-invite-email="${inv.email}">
                <span class="invite-email">${inv.email}</span>
                ${accepted ? '<span class="invite-accepted-badge">Accepted</span>' : ''}
                <div class="tier-toggle-group tier-toggle-sm">
                    <button class="tier-toggle-btn${tier === 'free' ? ' active' : ''}" data-tier="free" onclick="setInviteTier('${esc}', 'free', this)">Free</button>
                    <button class="tier-toggle-btn${tier === 'basic' ? ' active' : ''}" data-tier="basic" onclick="setInviteTier('${esc}', 'basic', this)">Basic</button>
                    <button class="tier-toggle-btn${tier === 'pro' ? ' active' : ''}" data-tier="pro" onclick="setInviteTier('${esc}', 'pro', this)">Pro</button>
                </div>
                <div class="bot-toggle-group" id="inv-bot-${esc.replace(/[@.]/g,'_')}" style="${isPro ? '' : 'opacity:0.4;pointer-events:none'}">
                    <button class="bot-toggle-btn${hasCrypto ? ' active' : ''}" data-bot="crypto" onclick="toggleInviteBot('${esc}', this)">Crypto</button>
                    <button class="bot-toggle-btn${hasStock ? ' active' : ''}" data-bot="stock" onclick="toggleInviteBot('${esc}', this)">Stock</button>
                </div>
                <button class="btn-save-invite" id="save-inv-${esc.replace(/[@.]/g,'_')}" onclick="saveInviteSettings('${esc}')" style="display:none">Save</button>
                <button class="btn-revoke" onclick="adminRevokeInvite('${esc}')">Revoke</button>
            </div>`;
        }).join('');
    } catch (e) {
        console.error('Failed to load invites:', e);
    }
}

function _invKey(email) { return email.replace(/[@.]/g, '_'); }

function setInviteTier(email, tier, btn) {
    const group = btn.parentElement;
    group.querySelectorAll('.tier-toggle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const botGroup = document.getElementById('inv-bot-' + _invKey(email));
    if (botGroup) {
        if (tier === 'pro') {
            botGroup.style.opacity = '';
            botGroup.style.pointerEvents = '';
        } else {
            botGroup.style.opacity = '0.4';
            botGroup.style.pointerEvents = 'none';
            botGroup.querySelectorAll('.bot-toggle-btn').forEach(b => b.classList.remove('active'));
        }
    }
    const saveBtn = document.getElementById('save-inv-' + _invKey(email));
    if (saveBtn) saveBtn.style.display = '';
}

function toggleInviteBot(email, btn) {
    btn.classList.toggle('active');
    const saveBtn = document.getElementById('save-inv-' + _invKey(email));
    if (saveBtn) saveBtn.style.display = '';
}

async function saveInviteSettings(email) {
    const item = document.querySelector(`.admin-invite-item[data-invite-email="${email}"]`);
    if (!item) return;
    const activeT = item.querySelector('.tier-toggle-btn.active');
    const tier = activeT ? activeT.dataset.tier : 'free';
    const botAccess = [];
    item.querySelectorAll('.bot-toggle-btn.active').forEach(b => botAccess.push(b.dataset.bot));
    const saveBtn = document.getElementById('save-inv-' + _invKey(email));
    try {
        if (saveBtn) { saveBtn.textContent = '...'; saveBtn.disabled = true; }
        const resp = await fetch('/api/admin/invite/' + encodeURIComponent(email), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tier, bot_access: botAccess }),
        });
        const data = await resp.json();
        if (data.ok) {
            if (saveBtn) { saveBtn.textContent = 'Saved'; setTimeout(() => { saveBtn.style.display = 'none'; saveBtn.textContent = 'Save'; saveBtn.disabled = false; }, 1200); }
        } else {
            alert(data.error || 'Save failed');
            if (saveBtn) { saveBtn.textContent = 'Save'; saveBtn.disabled = false; }
        }
    } catch (e) {
        alert('Save failed: ' + e.message);
        if (saveBtn) { saveBtn.textContent = 'Save'; saveBtn.disabled = false; }
    }
}

function onInviteTierChange() {
    const tier = document.getElementById('admin-invite-tier').value;
    const botGroup = document.getElementById('invite-bot-access-group');
    if (botGroup) {
        botGroup.style.display = tier === 'pro' ? 'flex' : 'none';
    }
    // Uncheck bot checkboxes when not pro
    if (tier !== 'pro') {
        const cb1 = document.getElementById('invite-bot-crypto');
        const cb2 = document.getElementById('invite-bot-stock');
        const cb3 = document.getElementById('invite-bot-watchdog');
        if (cb1) cb1.checked = false;
        if (cb2) cb2.checked = false;
        if (cb3) cb3.checked = false;
    }
}

async function adminInviteUser() {
    const email = document.getElementById('admin-invite-email').value.trim();
    const tier = document.getElementById('admin-invite-tier').value;
    if (!email) return;

    // Collect bot access checkboxes
    const botAccess = [];
    const cryptoCb = document.getElementById('invite-bot-crypto');
    const stockCb = document.getElementById('invite-bot-stock');
    const watchdogCb = document.getElementById('invite-bot-watchdog');
    if (tier === 'pro') {
        if (cryptoCb && cryptoCb.checked) botAccess.push('crypto');
        if (stockCb && stockCb.checked) botAccess.push('stock');
        if (watchdogCb && watchdogCb.checked) botAccess.push('watchdog');
    }

    // Role is auto-determined by tier on the backend
    const role = tier === 'pro' ? 'trader' : 'user';

    try {
        const resp = await fetch('/api/admin/invite', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, role, tier, bot_access: botAccess }),
        });
        const data = await resp.json();
        if (data.ok) {
            document.getElementById('admin-invite-email').value = '';
            document.getElementById('admin-invite-tier').value = 'free';
            onInviteTierChange();
            loadAdminInvites();
        } else {
            alert(data.error || 'Invite failed');
        }
    } catch (e) {
        alert('Invite failed: ' + e.message);
    }
}

async function adminRevokeInvite(email) {
    try {
        await fetch('/api/admin/invite/' + encodeURIComponent(email), { method: 'DELETE' });
        loadAdminInvites();
    } catch (e) {
        console.error('Failed to revoke invite:', e);
    }
}

async function adminChangeRole(userId, role) {
    try {
        const resp = await fetch('/api/admin/users/' + userId + '/role', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role }),
        });
        const data = await resp.json();
        if (data.ok) {
            loadAdminUsers();
        } else {
            alert(data.error || 'Role change failed');
        }
    } catch (e) {
        alert('Role change failed: ' + e.message);
    }
}

async function loadAdminConfig() {
    try {
        const resp = await fetch('/api/admin/config');
        if (!resp.ok) return;
        const data = await resp.json();
        const models = data.llm_models || {};
        const settings = data.llm_settings || {};
        const modelFields = {
            'admin-llm-research': 'LLM_RESEARCH',
            'admin-llm-research-fast': 'LLM_RESEARCH_FAST',
            'admin-llm-pattern': 'LLM_PATTERN',
            'admin-llm-prediction': 'LLM_PREDICTION',
            'admin-llm-screener': 'LLM_SCREENER',
            'admin-llm-supervisor': 'LLM_SUPERVISOR',
            'admin-llm-bot-sentiment': 'LLM_BOT_SENTIMENT',
            'admin-llm-bot-risk': 'LLM_BOT_RISK',
        };
        const settingFields = {
            'admin-llm-max-tokens': 'LLM_MAX_TOKENS',
            'admin-llm-temperature': 'LLM_TEMPERATURE',
        };
        for (const [elemId, key] of Object.entries(modelFields)) {
            const el = document.getElementById(elemId);
            if (el && models[key] !== undefined) el.value = models[key];
        }
        for (const [elemId, key] of Object.entries(settingFields)) {
            const el = document.getElementById(elemId);
            if (el && settings[key] !== undefined) el.value = settings[key];
        }
        // Skill models
        const skillModels = data.skill_models || {};
        const skillFields = {
            'admin-llm-skill': 'LLM_SKILL',
            'admin-llm-skill-earnings': 'LLM_SKILL_EARNINGS',
        };
        for (const [elemId, key] of Object.entries(skillFields)) {
            const el = document.getElementById(elemId);
            if (el && skillModels[key] !== undefined) el.value = skillModels[key];
        }
        // Market sensor toggle
        const sensorCheck = document.getElementById('admin-sensor-enabled');
        if (sensorCheck) sensorCheck.checked = !!data.bot_sensor_enabled;
    } catch (e) {
        console.error('Failed to load admin config:', e);
    }
}

async function adminSaveConfig() {
    const statusEl = document.getElementById('admin-config-status');
    const payload = {
        llm_models: {
            LLM_RESEARCH: document.getElementById('admin-llm-research').value,
            LLM_RESEARCH_FAST: document.getElementById('admin-llm-research-fast').value,
            LLM_PATTERN: document.getElementById('admin-llm-pattern').value,
            LLM_PREDICTION: document.getElementById('admin-llm-prediction').value,
            LLM_SCREENER: document.getElementById('admin-llm-screener').value,
            LLM_SUPERVISOR: document.getElementById('admin-llm-supervisor').value,
            LLM_BOT_SENTIMENT: document.getElementById('admin-llm-bot-sentiment').value,
            LLM_BOT_RISK: document.getElementById('admin-llm-bot-risk').value,
        },
        llm_settings: {
            LLM_MAX_TOKENS: document.getElementById('admin-llm-max-tokens').value,
            LLM_TEMPERATURE: document.getElementById('admin-llm-temperature').value,
        },
        skill_models: {
            LLM_SKILL: document.getElementById('admin-llm-skill').value,
            LLM_SKILL_EARNINGS: document.getElementById('admin-llm-skill-earnings').value,
        },
        bot_sensor_enabled: document.getElementById('admin-sensor-enabled').checked,
    };
    try {
        const resp = await fetch('/api/admin/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data.ok) {
            statusEl.textContent = 'Saved';
            statusEl.style.color = '#26a69a';
        } else {
            statusEl.textContent = data.error || 'Save failed';
            statusEl.style.color = '#ef5350';
        }
    } catch (e) {
        statusEl.textContent = 'Save failed';
        statusEl.style.color = '#ef5350';
    }
    setTimeout(() => { statusEl.textContent = ''; }, 3000);
}

function adminExport(dataset) {
    const statusEl = document.getElementById('admin-export-status');
    statusEl.textContent = `Downloading ${dataset}...`;
    statusEl.style.color = 'var(--text-secondary)';

    const link = document.createElement('a');
    link.href = `/api/admin/export?dataset=${dataset}`;
    link.download = '';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    setTimeout(() => {
        statusEl.textContent = `${dataset}.json downloaded`;
        statusEl.style.color = 'var(--accent-green)';
        setTimeout(() => { statusEl.textContent = ''; }, 3000);
    }, 1000);
}


// ─── LLM Live Override + Snapshot/Revert ─────────────────────

async function loadAdminLlmOverrides() {
    try {
        const resp = await fetch('/api/admin/llm-models');
        if (!resp.ok) return;
        const data = await resp.json();
        renderLlmOverridesTable(data.models || {});
        renderLlmSnapshots(data.snapshots || []);
    } catch (e) {
        console.error('Failed to load LLM overrides:', e);
    }
}

function renderLlmOverridesTable(models) {
    const body = document.getElementById('admin-llm-roles-body');
    if (!body) return;
    const roles = Object.keys(models);
    if (!roles.length) {
        body.innerHTML = '<tr><td colspan="5" style="padding:12px;color:var(--text-secondary);">No roles registered.</td></tr>';
        return;
    }
    body.innerHTML = roles.map(role => {
        const m = models[role];
        const source = m.override_set ? 'DB override' : (m.env ? 'env var' : 'default');
        const sourceColor = m.override_set ? '#4f8aff' : '#636b7e';
        return `<tr style="border-bottom:1px solid var(--border-color);">
            <td style="padding:6px;font-weight:600;color:var(--text-bright);">${role}</td>
            <td style="padding:6px;color:var(--text-secondary);font-family:monospace;font-size:10px;">${m.current || '<em>(unset)</em>'}</td>
            <td style="padding:6px;">
                <input type="text" id="admin-llm-override-${role}" value="${m.override || ''}"
                       placeholder="${m.env || m.default || 'leave empty to clear'}"
                       style="width:100%;padding:3px 6px;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-bright);border-radius:3px;font-size:10px;font-family:monospace;">
            </td>
            <td style="padding:6px;color:${sourceColor};font-size:10px;">${source}</td>
            <td style="padding:6px;text-align:right;">
                <button onclick="adminLlmSetOverride('${role}')" class="btn-analyze" style="padding:3px 10px;font-size:10px;">Apply</button>
            </td>
        </tr>`;
    }).join('');
}

function renderLlmSnapshots(snapshots) {
    const list = document.getElementById('admin-llm-snapshots-list');
    if (!list) return;
    if (!snapshots.length) {
        list.innerHTML = '<div style="color:var(--text-secondary);padding:6px;">No snapshots yet.</div>';
        return;
    }
    list.innerHTML = snapshots.map(s => {
        const when = s.created_at ? s.created_at.split('.')[0] : '';
        const modelCount = Object.keys(s.models || {}).length;
        return `<div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--border-color);">
            <div style="flex:1;">
                <div style="color:var(--text-bright);font-weight:600;">${s.label}</div>
                <div style="color:var(--text-secondary);font-size:10px;">${when} · ${modelCount} roles captured</div>
            </div>
            <button onclick="adminLlmRevert(${s.id})" class="btn-analyze" style="padding:3px 10px;font-size:10px;">Revert</button>
            <button onclick="adminLlmDeleteSnapshot(${s.id})" style="padding:3px 8px;font-size:10px;background:none;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:3px;cursor:pointer;">Delete</button>
        </div>`;
    }).join('');
}

async function adminLlmSetOverride(role) {
    const input = document.getElementById(`admin-llm-override-${role}`);
    if (!input) return;
    const model = input.value.trim();
    try {
        const resp = await fetch('/api/admin/llm-models/set', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role, model }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            alert(data.error || 'Failed to set override');
            return;
        }
        loadAdminLlmOverrides();
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function adminLlmSaveSnapshot() {
    const labelEl = document.getElementById('admin-llm-snapshot-label');
    const label = labelEl ? labelEl.value.trim() : '';
    try {
        const resp = await fetch('/api/admin/llm-models/snapshot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label }),
        });
        if (!resp.ok) {
            const data = await resp.json();
            alert(data.error || 'Snapshot failed');
            return;
        }
        if (labelEl) labelEl.value = '';
        loadAdminLlmOverrides();
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function adminLlmRevert(snapshotId) {
    if (!confirm('Revert all LLM model overrides to this snapshot? Current overrides will be replaced.')) return;
    try {
        const resp = await fetch('/api/admin/llm-models/revert', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ snapshot_id: snapshotId }),
        });
        if (!resp.ok) {
            const data = await resp.json();
            alert(data.error || 'Revert failed');
            return;
        }
        loadAdminLlmOverrides();
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function adminLlmDeleteSnapshot(snapshotId) {
    if (!confirm('Delete this snapshot? This cannot be undone.')) return;
    try {
        await fetch(`/api/admin/llm-models/snapshot/${snapshotId}`, { method: 'DELETE' });
        loadAdminLlmOverrides();
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function adminLlmClearAllOverrides() {
    if (!confirm('Clear ALL LLM model overrides? Every role will fall back to env/default.')) return;
    try {
        await fetch('/api/admin/llm-models/clear', { method: 'POST' });
        loadAdminLlmOverrides();
    } catch (e) {
        alert('Error: ' + e.message);
    }
}


// ─── Ollama Cloud Configuration (DB-backed, SaaS-friendly) ───────────────

function _ollamaSrcLabel(src) {
    if (src === 'db') return '· from DB';
    if (src === 'env') return '· from env';
    return '· default';
}

async function loadAdminOllamaConfig() {
    try {
        const resp = await fetch('/api/admin/ollama-config');
        if (!resp.ok) return;
        const data = await resp.json();
        document.getElementById('admin-ollama-url').value = data.ollama_url?.value || '';
        document.getElementById('admin-ollama-url-source').textContent = _ollamaSrcLabel(data.ollama_url?.source);
        document.getElementById('admin-ollama-model').value = data.ollama_model?.value || '';
        document.getElementById('admin-ollama-model-source').textContent = _ollamaSrcLabel(data.ollama_model?.source);
        // API key is masked — only show placeholder if set
        const keyInput = document.getElementById('admin-ollama-api-key');
        keyInput.placeholder = data.ollama_api_key?.is_set ? data.ollama_api_key.value_masked : '(not set)';
        keyInput.value = '';
        document.getElementById('admin-ollama-api-key-source').textContent = _ollamaSrcLabel(data.ollama_api_key?.source);
        // OpenRouter→Ollama failover toggle + usage telemetry
        const fbVal = String(data.ollama_fallback_enabled?.value ?? '0').toLowerCase();
        const fbBox = document.getElementById('admin-ollama-fallback');
        if (fbBox) fbBox.checked = ['1', 'true', 'yes', 'on'].includes(fbVal);
        const fbSrc = document.getElementById('admin-ollama-fallback_enabled-source');
        if (fbSrc) fbSrc.textContent = _ollamaSrcLabel(data.ollama_fallback_enabled?.source);
        const st = data._fallback_stats || {};
        const stEl = document.getElementById('admin-ollama-fallback-stats');
        if (stEl) stEl.textContent = st.fallback_calls
            ? `Used ${st.fallback_calls}× (last: ${st.last_used || '—'}, ${st.last_reason || ''}).`
            : '';
    } catch (e) {
        console.error('loadAdminOllamaConfig failed:', e);
    }
}

async function adminOllamaSave() {
    const url = document.getElementById('admin-ollama-url').value.trim();
    const model = document.getElementById('admin-ollama-model').value.trim();
    const apiKey = document.getElementById('admin-ollama-api-key').value;
    const fallback = document.getElementById('admin-ollama-fallback');
    const payload = {
        ollama_url: url, ollama_model: model,
        ollama_fallback_enabled: (fallback && fallback.checked) ? '1' : '0',
    };
    // Only include key if the admin actually typed something (else preserve existing)
    if (apiKey.length > 0) payload.ollama_api_key = apiKey;
    const statusEl = document.getElementById('admin-ollama-status');
    statusEl.textContent = 'Saving…';
    try {
        const resp = await fetch('/api/admin/ollama-config/set', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (resp.ok) {
            statusEl.textContent = 'Saved';
            statusEl.style.color = 'var(--accent-green)';
            loadAdminOllamaConfig();
        } else {
            statusEl.textContent = data.error || 'Save failed';
            statusEl.style.color = 'var(--accent-red)';
        }
    } catch (e) {
        statusEl.textContent = 'Error: ' + e.message;
        statusEl.style.color = 'var(--accent-red)';
    }
}

async function adminOllamaTest() {
    const statusEl = document.getElementById('admin-ollama-status');
    statusEl.textContent = 'Testing…';
    statusEl.style.color = 'var(--text-secondary)';
    try {
        const resp = await fetch('/api/admin/ollama-config/test', { method: 'POST' });
        const data = await resp.json();
        if (data.ok) {
            statusEl.textContent = `OK (${data.latency_ms} ms)`;
            statusEl.style.color = 'var(--accent-green)';
        } else {
            statusEl.textContent = `Fail: ${data.message}`;
            statusEl.style.color = 'var(--accent-red)';
        }
    } catch (e) {
        statusEl.textContent = 'Error: ' + e.message;
        statusEl.style.color = 'var(--accent-red)';
    }
}

async function adminOllamaClear() {
    if (!confirm('Clear all DB-stored Ollama config? Settings will fall back to env vars / defaults.')) return;
    try {
        await fetch('/api/admin/ollama-config/clear', { method: 'POST' });
        loadAdminOllamaConfig();
    } catch (e) {
        alert('Error: ' + e.message);
    }
}


// ─── Platform (Docker vs Cloud Run) ──────────────────────────────────────

async function loadAdminPlatform() {
    try {
        const resp = await fetch('/api/admin/platform');
        if (!resp.ok) return;
        const data = await resp.json();
        document.getElementById('admin-platform-detected').textContent = data.detected;
        document.getElementById('admin-platform-effective').textContent = data.effective;
        document.getElementById('admin-platform-mode').value = data.override || 'auto';
        const ksvc = document.getElementById('admin-platform-kservice');
        if (data.k_service) {
            ksvc.textContent = `(K_SERVICE=${data.k_service})`;
        } else {
            ksvc.textContent = '(K_SERVICE not set)';
        }
        const chip = document.getElementById('admin-ollama-platform-chip');
        if (chip) {
            chip.textContent = data.effective === 'cloud_run' ? 'CLOUD RUN' : 'DOCKER';
            chip.style.background = data.effective === 'cloud_run' ? '#1a73e8' : 'var(--bg-input)';
            chip.style.color = data.effective === 'cloud_run' ? '#fff' : 'var(--text-secondary)';
        }
    } catch (e) {
        console.error('loadAdminPlatform failed:', e);
    }
}

async function adminPlatformSave() {
    const target = document.getElementById('admin-platform-mode').value;
    const statusEl = document.getElementById('admin-platform-status');
    statusEl.textContent = 'Saving…';
    try {
        const resp = await fetch('/api/admin/platform/set', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target }),
        });
        if (resp.ok) {
            statusEl.textContent = 'Saved';
            statusEl.style.color = 'var(--accent-green)';
            loadAdminPlatform();
        } else {
            const d = await resp.json();
            statusEl.textContent = d.error || 'Save failed';
            statusEl.style.color = 'var(--accent-red)';
        }
    } catch (e) {
        statusEl.textContent = 'Error: ' + e.message;
        statusEl.style.color = 'var(--accent-red)';
    }
}


// ─── Per-User Usage Report ───────────────────────────────────────────────

let _usageState = {
    range: '7d',
    sort: 'calls_desc',
    qDebounce: null,
};

function _rangeToDates(range) {
    const now = new Date();
    const to = now.toISOString().slice(0, 19);
    let from;
    if (range === '24h') from = new Date(now.getTime() - 24 * 3600e3);
    else if (range === '7d') from = new Date(now.getTime() - 7 * 86400e3);
    else if (range === '30d') from = new Date(now.getTime() - 30 * 86400e3);
    else { // all
        from = new Date('2020-01-01T00:00:00');
    }
    return { from: from.toISOString().slice(0, 19), to };
}

function setUsageRange(r) {
    _usageState.range = r;
    document.querySelectorAll('.usage-range-chip').forEach(b => {
        b.classList.toggle('active', b.dataset.range === r);
    });
    loadAdminUsersUsage();
}

function setUsageSort(s) {
    _usageState.sort = s;
    loadAdminUsersUsage();
}

function onUsageSearchInput() {
    if (_usageState.qDebounce) clearTimeout(_usageState.qDebounce);
    _usageState.qDebounce = setTimeout(loadAdminUsersUsage, 300);
}

function _buildUsageQuery(extra) {
    const { from, to } = _rangeToDates(_usageState.range);
    const params = new URLSearchParams({ from, to, sort: _usageState.sort });
    const tier = document.getElementById('usage-tier-filter')?.value || '';
    const q = document.getElementById('usage-q')?.value || '';
    const includeBot = document.getElementById('usage-include-bot')?.checked || false;
    if (tier) params.set('tier', tier);
    if (q) params.set('q', q);
    if (includeBot) params.set('include_bot', '1');
    if (extra) Object.entries(extra).forEach(([k, v]) => params.set(k, v));
    return params;
}

async function loadAdminUsersUsage() {
    const tbody = document.getElementById('admin-usage-body');
    if (!tbody) return;
    try {
        const params = _buildUsageQuery();
        const resp = await fetch('/api/admin/users/usage?' + params.toString());
        if (!resp.ok) {
            tbody.innerHTML = '<tr><td colspan="11" style="padding:12px;color:var(--accent-red);">Failed to load</td></tr>';
            return;
        }
        const data = await resp.json();
        // Show/hide bot columns based on toggle
        const includeBot = document.getElementById('usage-include-bot')?.checked;
        document.querySelectorAll('.usage-bot-col').forEach(el => {
            el.style.display = includeBot ? '' : 'none';
        });
        if (!data.rows.length) {
            tbody.innerHTML = '<tr><td colspan="11" class="bot-empty">No usage in window</td></tr>';
            return;
        }
        tbody.innerHTML = data.rows.map(r => {
            const fmt = n => n == null ? '—' : Number(n).toLocaleString();
            const last = r.last_seen ? r.last_seen.slice(0, 16).replace('T', ' ') : '—';
            const tierBadge = r.is_admin ? 'admin' : r.tier;
            const botCells = includeBot
                ? `<td style="padding:6px;text-align:right;">${fmt(r.bot_trades_count)}</td>
                   <td style="padding:6px;text-align:right;color:${r.bot_pnl_total >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'};">${r.bot_pnl_total != null ? '$' + Number(r.bot_pnl_total).toFixed(2) : '—'}</td>`
                : '';
            return `
                <tr style="border-bottom:1px solid var(--border-color);cursor:pointer;" onclick="openUsageDrilldown(${r.user_id})">
                    <td style="padding:6px;">${r.email || '—'}</td>
                    <td style="padding:6px;text-transform:capitalize;">${tierBadge}</td>
                    <td style="padding:6px;text-align:right;font-weight:600;">${fmt(r.calls_window)}</td>
                    <td style="padding:6px;text-align:right;">${fmt(r.calls_24h)}</td>
                    <td style="padding:6px;text-align:right;">${fmt(r.calls_7d)}</td>
                    <td style="padding:6px;text-align:right;">${fmt(r.calls_30d)}</td>
                    <td style="padding:6px;text-align:right;color:var(--text-secondary);">${fmt(r.calls_total)}</td>
                    <td style="padding:6px;font-family:monospace;font-size:10px;">${r.top_model || '—'}</td>
                    <td style="padding:6px;color:var(--text-secondary);">${last}</td>
                    ${botCells}
                </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="11" style="padding:12px;color:var(--accent-red);">Error: ${e.message}</td></tr>`;
    }
}

function adminUsageExportCSV() {
    const params = _buildUsageQuery({ format: 'csv' });
    window.location.href = '/api/admin/users/usage?' + params.toString();
}

async function openUsageDrilldown(userId) {
    try {
        const { from, to } = _rangeToDates(_usageState.range);
        const resp = await fetch(`/api/admin/users/${userId}/usage?from=${from}&to=${to}`);
        if (!resp.ok) {
            alert('Failed to load detail');
            return;
        }
        const data = await resp.json();
        // Simple modal: render daily counts + by-model breakdown + recent events
        const daily = data.daily.map(d => `${d.day}: ${d.cnt}`).join('\n');
        const models = data.by_model.map(m => `  ${m.model}: ${m.cnt}`).join('\n');
        const sources = data.by_source.map(s => `  ${s.source}: ${s.cnt}`).join('\n');
        const recent = data.recent.slice(0, 20).map(e =>
            `  ${(e.called_at || '').slice(0, 19)}  ${e.source || '—'}  ${e.model || '—'}`).join('\n');
        const text =
            `User: ${data.user.email} (${data.user.tier})\n` +
            `Window: ${data.from.slice(0, 10)} → ${data.to.slice(0, 10)}\n\n` +
            `Daily calls:\n${daily || '  (none)'}\n\n` +
            `By model:\n${models || '  (none)'}\n\n` +
            `By source:\n${sources || '  (none)'}\n\n` +
            `Recent (last 20):\n${recent || '  (none)'}`;
        // Render in a basic modal — reuse alert for now to keep this slice minimal.
        // A richer chart can come next if you want a sparkline.
        alert(text);
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

// ─── Admin sub-tab switching (Usage · Users · Models · System) ───
function switchAdminTab(name) {
    document.querySelectorAll('.admin-subtab').forEach(function (b) {
        b.classList.toggle('active', b.getAttribute('data-atab') === name);
    });
    document.querySelectorAll('.admin-tabpanel').forEach(function (p) {
        p.style.display = (p.getAttribute('data-apanel') === name) ? '' : 'none';
    });
    if (name === 'codes') {
        if (typeof loadPromoCodes === 'function') loadPromoCodes();
        if (typeof loadPricing === 'function') loadPricing();
    }
}

// ─── Plan pricing ───────────────────────────────────────────
async function loadPricing() {
    try {
        const r = await fetch('/billing/admin/pricing');
        const d = await r.json();
        const p = d.pricing || {};
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
        set('pr-starter-amt', (p.starter || {}).amount != null ? p.starter.amount : '');
        set('pr-starter-id', (p.starter || {}).stripe_price_id || '');
        set('pr-pro-amt', (p.pro || {}).amount != null ? p.pro.amount : '');
        set('pr-pro-id', (p.pro || {}).stripe_price_id || '');
        set('pr-trader-amt', (p.trader || {}).amount != null ? p.trader.amount : '');
        set('pr-trader-id', (p.trader || {}).stripe_price_id || '');
    } catch (e) { /* ignore */ }
}

async function savePricing() {
    const msg = document.getElementById('pr-msg');
    const v = id => (document.getElementById(id) || {}).value || '';
    const body = {
        starter_amount: v('pr-starter-amt'), starter_price_id: v('pr-starter-id'),
        pro_amount: v('pr-pro-amt'), pro_price_id: v('pr-pro-id'),
        trader_amount: v('pr-trader-amt'), trader_price_id: v('pr-trader-id'),
    };
    try {
        const r = await fetch('/billing/admin/pricing', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const d = await r.json();
        if (msg) { msg.style.color = d.ok ? 'var(--accent-green)' : 'var(--accent-red)'; msg.textContent = d.ok ? 'Pricing saved.' : (d.error || 'Save failed.'); }
    } catch (e) { if (msg) { msg.style.color = 'var(--accent-red)'; msg.textContent = 'Request failed.'; } }
}

// ─── Promo / access codes ───────────────────────────────────
function _pcEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}

async function loadPromoCodes() {
    const el = document.getElementById('pc-table');
    if (!el) return;
    try {
        const r = await fetch('/api/admin/promo-codes');
        const d = await r.json();
        const codes = d.codes || [];
        if (!codes.length) { el.innerHTML = '<div style="color:var(--text-secondary);padding:14px;">No codes yet — generate some above.</div>'; return; }
        let h = '<table class="admin-table" style="width:100%;font-size:13px;"><thead><tr>' +
            '<th style="text-align:left;">Code</th><th>Plan</th><th>Days</th><th>Uses</th><th>Expires</th><th>Status</th><th></th></tr></thead><tbody>';
        codes.forEach(function(c){
            const used = c.used_count + '/' + c.max_uses;
            const full = c.used_count >= c.max_uses;
            h += '<tr>' +
                '<td><code style="font-weight:700;letter-spacing:.5px;">' + _pcEsc(c.code) + '</code> ' +
                    '<button class="btn-ghost" style="padding:1px 7px;font-size:11px;" onclick="navigator.clipboard&&navigator.clipboard.writeText(\'' + _pcEsc(c.code) + '\')">copy</button></td>' +
                '<td style="text-align:center;text-transform:capitalize;">' + _pcEsc(c.tier) + '</td>' +
                '<td style="text-align:center;">' + c.days + '</td>' +
                '<td style="text-align:center;' + (full?'color:var(--accent-red);':'') + '">' + used + '</td>' +
                '<td style="text-align:center;color:var(--text-secondary);">' + (c.expires_at||'—') + '</td>' +
                '<td style="text-align:center;">' + (c.active ? '<span style="color:var(--accent-green);">Active</span>' : '<span style="color:var(--text-secondary);">Off</span>') + '</td>' +
                '<td style="text-align:right;"><button class="btn-ghost" style="padding:3px 10px;font-size:11px;" onclick="togglePromoCode(\'' + _pcEsc(c.code) + '\',' + (!c.active) + ')">' + (c.active?'Deactivate':'Activate') + '</button></td>' +
                '</tr>';
        });
        el.innerHTML = h + '</tbody></table>';
    } catch (e) { el.innerHTML = '<div style="color:var(--accent-red);padding:14px;">Failed to load codes.</div>'; }
}

async function generatePromoCodes() {
    const body = {
        tier: (document.getElementById('pc-tier')||{}).value || 'starter',
        days: parseInt((document.getElementById('pc-days')||{}).value || '30', 10),
        max_uses: parseInt((document.getElementById('pc-uses')||{}).value || '1', 10),
        expires_days: parseInt((document.getElementById('pc-exp')||{}).value || '90', 10),
        quantity: parseInt((document.getElementById('pc-qty')||{}).value || '1', 10),
    };
    const out = document.getElementById('pc-new');
    try {
        const r = await fetch('/api/admin/promo-codes', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const d = await r.json();
        const codes = d.codes || [];
        if (codes.length) {
            out.innerHTML = '<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:8px;padding:12px;">' +
                '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px;">New codes (share these):</div>' +
                codes.map(function(c){return '<code style="display:inline-block;margin:3px 8px 3px 0;font-weight:700;letter-spacing:.5px;font-size:15px;color:var(--accent-green);">' + _pcEsc(c) + '</code>';}).join('') +
                '</div>';
        } else { out.innerHTML = '<div style="color:var(--accent-red);">Could not generate codes.</div>'; }
        loadPromoCodes();
    } catch (e) { out.innerHTML = '<div style="color:var(--accent-red);">Request failed.</div>'; }
}

async function togglePromoCode(code, active) {
    try {
        await fetch('/api/admin/promo-codes/' + encodeURIComponent(code) + '/toggle',
            {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:active})});
        loadPromoCodes();
    } catch (e) { /* ignore */ }
}

// ─── Token & AI Usage dashboard ─────────────────────────────
function _aiFmt(n) {
    if (n === null || n === undefined) return '0';
    return Number(n).toLocaleString('en-US');
}
function _aiCost(n) {
    const v = Number(n || 0);
    if (v === 0) return '$0';
    if (v < 0.01) return '$' + v.toFixed(4);
    return '$' + v.toFixed(2);
}

async function loadAdminAiUsage() {
    const tiles = document.getElementById('ai-usage-tiles');
    if (!tiles) return;
    const days = parseInt((document.getElementById('ai-usage-range') || {}).value || '30', 10);
    const to = new Date();
    const from = new Date(to.getTime() - days * 86400000);
    const qs = 'from=' + from.toISOString().slice(0, 10) + '&to=' + to.toISOString().slice(0, 10);
    tiles.innerHTML = '<div class="ai-usage-empty">Loading…</div>';
    try {
        const resp = await fetch('/api/admin/ai-usage?' + qs);
        if (!resp.ok) {
            tiles.innerHTML = '<div class="ai-usage-empty">Could not load usage.</div>';
            return;
        }
        const d = await resp.json();
        const t = d.totals || {};
        // Explain a zero-token window: calls are logged but no tokens captured.
        const banner = document.getElementById('ai-usage-banner');
        if (banner) {
            if ((t.calls || 0) > 0 && (t.total_tokens || 0) === 0) {
                banner.innerHTML = '⚠️ <strong>' + _aiFmt(t.calls) +
                    ' calls logged, but 0 tokens captured in this window.</strong> ' +
                    'Token &amp; cost capture started Jun&nbsp;9, 2026 — older calls are counted but show 0 tokens. ' +
                    'If recent calls also show 0, check that the OpenRouter key has credit (a dead key returns 402 and no usage is recorded).';
                banner.style.display = '';
            } else if ((t.calls || 0) === 0) {
                banner.innerHTML = 'No LLM calls recorded in this window.';
                banner.style.display = '';
            } else {
                banner.style.display = 'none';
            }
        }
        tiles.innerHTML =
            _aiTile('Total cost (est.)', _aiCost(t.cost_usd)) +
            _aiTile('LLM calls', _aiFmt(t.calls)) +
            _aiTile('Total tokens', _aiFmt(t.total_tokens)) +
            _aiTile('Prompt tokens', _aiFmt(t.prompt_tokens)) +
            _aiTile('Completion tokens', _aiFmt(t.completion_tokens));

        _aiFillTable('ai-usage-by-model', (d.by_model || []).map(r =>
            [r.model, _aiFmt(r.calls), _aiFmt(r.total_tokens), _aiCost(r.cost_usd)]));
        _aiFillTable('ai-usage-by-source', (d.by_source || []).map(r =>
            [r.source, _aiFmt(r.calls), _aiFmt(r.total_tokens), _aiCost(r.cost_usd)]));
        _aiFillTable('ai-usage-top-users', (d.top_users || []).map(r =>
            [r.email || r.name || '—', _aiFmt(r.calls), _aiFmt(r.total_tokens), _aiCost(r.cost_usd)]));
    } catch (e) {
        tiles.innerHTML = '<div class="ai-usage-empty">Error loading usage.</div>';
    }
}

function _aiTile(label, value) {
    return '<div class="ai-usage-tile"><div class="ai-usage-tile-label">' + label +
        '</div><div class="ai-usage-tile-value">' + value + '</div></div>';
}

function _aiFillTable(id, rows) {
    const tb = document.getElementById(id);
    if (!tb) return;
    if (!rows.length) {
        tb.innerHTML = '<tr><td colspan="4" class="ai-usage-empty">No data</td></tr>';
        return;
    }
    tb.innerHTML = rows.map(cells =>
        '<tr>' + cells.map((c, i) =>
            '<td' + (i === 0 ? ' class="ai-usage-name"' : '') + '>' + c + '</td>').join('') + '</tr>'
    ).join('');
}

