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

    const payload = {
        api_keys: {
            OPENROUTER_API_KEY: document.getElementById('set-openrouter-key').value,
            BLOFIN_API_KEY: document.getElementById('set-blofin-key').value,
            BLOFIN_API_SECRET: document.getElementById('set-blofin-secret').value,
            BLOFIN_PASSPHRASE: document.getElementById('set-blofin-pass').value,
            ALPACA_API_KEY: document.getElementById('set-alpaca-key').value,
            ALPACA_SECRET_KEY: document.getElementById('set-alpaca-secret').value,
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
            document.getElementById('set-openrouter-key').value = '';
            document.getElementById('set-blofin-key').value = '';
            document.getElementById('set-blofin-secret').value = '';
            document.getElementById('set-blofin-pass').value = '';
            document.getElementById('set-alpaca-key').value = '';
            document.getElementById('set-alpaca-secret').value = '';
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

