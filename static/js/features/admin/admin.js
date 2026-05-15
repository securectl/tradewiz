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

