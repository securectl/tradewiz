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

// ─── Restore tab from URL hash (default landing = Dashboard) ─────
(function _restoreTab() {
    const hash = location.hash.replace('#', '');
    switchTab(hash || 'dashboard');
})();
