// ─── Event Listeners ─────────────────────────────────────────
// This file must load LAST — after all feature modules are defined.

document.getElementById('search-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const ticker = document.getElementById('ticker-input').value.trim();
    if (!ticker) return;
    const period = document.getElementById('period-select').value;
    const interval = document.getElementById('interval-select').value;
    analyzeTicker(ticker, period, interval);
});

document.getElementById('btn-history').addEventListener('click', openHistory);
document.getElementById('btn-close-history').addEventListener('click', closeHistory);
document.getElementById('backdrop').addEventListener('click', closeHistory);
document.getElementById('btn-ai-validate').addEventListener('click', runAIValidation);
document.getElementById('btn-prediction').addEventListener('click', runPrediction);
document.getElementById('btn-earnings').addEventListener('click', runEarningsAnalysis);
document.getElementById('btn-backtest').addEventListener('click', runTickerBacktest);
document.getElementById('bt-modal-close').addEventListener('click', closeBacktestModal);
document.getElementById('bt-overlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeBacktestModal();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('bt-overlay').classList.contains('open')) closeBacktestModal();
});

// MA toggle listeners
for (const [key, config] of Object.entries(MA_CONFIG)) {
    const cb = document.getElementById(config.checkboxId);
    if (cb) cb.addEventListener('change', () => toggleMA(key));
}
document.getElementById('toggle-volume').addEventListener('change', toggleVolume);

// Check AI status on load
checkAIStatus();

// Init screener sector pills + hot sector period buttons
renderSectorPills('lowcap');
initHotSectorButtons();
initScreenerTimeframeButtons();

// Focus ticker input on load
document.getElementById('ticker-input').focus();

// ─── Feature flags (canary rollout) — reveal [data-flag] elements the current
//     user is entitled to; hide-by-default keeps unreleased features dark. ─────
async function applyFeatureFlags() {
    let flags = {};
    try {
        const res = await fetch('/api/feature-flags');
        if (res.ok) flags = await res.json();
    } catch (e) { /* fail closed — flagged elements stay hidden */ }
    document.querySelectorAll('[data-flag]').forEach(el => {
        if (flags[el.getAttribute('data-flag')]) el.style.display = '';
    });
    // If the restored tab is a now-hidden flagged tab, fall back to dashboard.
    const active = (location.hash.replace('#', '') || 'dashboard');
    const btn = document.querySelector(`.tab-btn[data-tab="${active}"][data-flag]`);
    if (btn && btn.style.display === 'none') switchTab('dashboard');
    return flags;
}

// ─── Restore tab from URL hash (default landing = Dashboard) ─────
(function _restoreTab() {
    const hash = location.hash.replace('#', '');
    switchTab(hash || 'dashboard');
})();

applyFeatureFlags();
