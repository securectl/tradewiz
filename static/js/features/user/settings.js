// ─── Settings Modal ─────────────────────────────────────────

async function openSettings() {
    document.getElementById('settings-backdrop').style.display = 'block';
    document.getElementById('settings-modal').style.display = 'flex';
    await loadSettings();
}

function closeSettings() {
    document.getElementById('settings-backdrop').style.display = 'none';
    document.getElementById('settings-modal').style.display = 'none';
}

async function loadSettings() {
    try {
        const resp = await fetch('/api/settings');
        const data = await resp.json();

        // API Keys — show status badges, don't populate actual values
        const orStatus = document.getElementById('set-openrouter-status');
        if (data.api_keys && data.api_keys.openrouter) {
            if (data.api_keys.openrouter.configured) {
                orStatus.textContent = 'Configured (' + data.api_keys.openrouter.masked_value + ')';
                orStatus.className = 'settings-status configured';
            } else {
                orStatus.textContent = 'Not configured';
                orStatus.className = 'settings-status not-configured';
            }
        }

        const bfStatus = document.getElementById('set-blofin-key-status');
        if (data.api_keys && data.api_keys.blofin_api_key) {
            if (data.api_keys.blofin_api_key.configured) {
                bfStatus.textContent = 'Configured (' + data.api_keys.blofin_api_key.masked_value + ')';
                bfStatus.className = 'settings-status configured';
            } else {
                bfStatus.textContent = 'Not configured';
                bfStatus.className = 'settings-status not-configured';
            }
        }

        const alpacaStatus = document.getElementById('set-alpaca-key-status');
        if (alpacaStatus && data.api_keys && data.api_keys.alpaca_api_key) {
            if (data.api_keys.alpaca_api_key.configured) {
                alpacaStatus.textContent = 'Configured (' + data.api_keys.alpaca_api_key.masked_value + ')';
                alpacaStatus.className = 'settings-status configured';
            } else {
                alpacaStatus.textContent = 'Not configured';
                alpacaStatus.className = 'settings-status not-configured';
            }
        }

        const webullStatus = document.getElementById('set-webull-app-key-status');
        if (webullStatus && data.api_keys && data.api_keys.webull_app_key) {
            if (data.api_keys.webull_app_key.configured) {
                webullStatus.textContent = 'Configured (' + data.api_keys.webull_app_key.masked_value + ')';
                webullStatus.className = 'settings-status configured';
            } else {
                webullStatus.textContent = 'Not configured';
                webullStatus.className = 'settings-status not-configured';
            }
        }

        // LLM Settings (preferences)
        if (data.llm_settings) {
            document.getElementById('set-llm-fast-mode').checked = !!data.llm_settings.LLM_FAST_MODE;
        }

        // Ollama
        if (data.ollama) {
            document.getElementById('set-ollama-url').value = data.ollama.OLLAMA_URL || '';
            document.getElementById('set-ollama-model').value = data.ollama.OLLAMA_MODEL || '';
        }
    } catch (e) {
        console.error('Failed to load settings:', e);
    }
}

async function saveSettings() {
    const btn = document.querySelector('.btn-settings-save');
    const statusEl = document.getElementById('settings-save-status');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    const _val = (id) => (document.getElementById(id) || {}).value || '';
    const payload = {
        api_keys: {
            OPENROUTER_API_KEY: _val('set-openrouter-key'),
            BLOFIN_API_KEY: _val('set-blofin-key'),
            BLOFIN_API_SECRET: _val('set-blofin-secret'),
            BLOFIN_PASSPHRASE: _val('set-blofin-pass'),
            ALPACA_API_KEY: _val('set-alpaca-key'),
            ALPACA_SECRET_KEY: _val('set-alpaca-secret'),
            WEBULL_APP_KEY: _val('set-webull-app-key'),
            WEBULL_APP_SECRET: _val('set-webull-app-secret'),
            WEBULL_ACCOUNT_ID: _val('set-webull-account-id'),
        },
        llm_settings: {
            LLM_FAST_MODE: document.getElementById('set-llm-fast-mode').checked,
        },
        ollama: {
            OLLAMA_URL: document.getElementById('set-ollama-url').value,
            OLLAMA_MODEL: document.getElementById('set-ollama-model').value,
        },
    };

    try {
        const resp = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data.ok) {
            statusEl.textContent = 'Saved ' + (data.updated ? data.updated.length : 0) + ' settings';
            statusEl.style.color = '#26a69a';
            // Clear password fields after successful save
            ['set-openrouter-key', 'set-blofin-key', 'set-blofin-secret', 'set-blofin-pass',
             'set-alpaca-key', 'set-alpaca-secret',
             'set-webull-app-key', 'set-webull-app-secret', 'set-webull-account-id']
                .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
            // Refresh status badges
            await loadSettings();
        } else {
            statusEl.textContent = data.error || 'Save failed';
            statusEl.style.color = '#ef5350';
        }
    } catch (e) {
        statusEl.textContent = 'Save failed: ' + e.message;
        statusEl.style.color = '#ef5350';
    }

    btn.disabled = false;
    btn.textContent = 'Save All';
    setTimeout(function() { statusEl.textContent = ''; }, 3000);
}

