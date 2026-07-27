/**
 * AI Stock Analyst — Frontend Application
 * TradingView Lightweight Charts with breakout pattern overlays.
 */

let chart = null;
let candleSeries = null;
let volumeSeries = null;
let maLines = {};
let currentAnalysis = null;
let trendlineOverlays = [];
let isLoading = false;
let currentUser = null;
let billingStatus = null;

// ─── Yahoo Finance ticker link (shared) ──────────────────────
//
// Renders a ticker as a Yahoo-Finance link with an external-link glyph.
// Used across ThunderBot, Screener, watchlist UIs, and the paper-trades
// table so every visible ticker is one click away from the YF quote page.
//
// opts:
//   stopPropagation: emit onclick="event.stopPropagation();" so the link
//     won't trigger an enclosing element's click handler (e.g. screener
//     cards that toggle details on row-click).
//   compact: smaller glyph + no underline (for chips/inline use).
function yahooFinanceLink(ticker, opts = {}) {
    if (!ticker) return '';
    const url = `https://finance.yahoo.com/quote/${encodeURIComponent(ticker)}`;
    const stop = opts.stopPropagation ? 'onclick="event.stopPropagation();"' : '';
    const glyph = opts.compact
        ? '<span style="font-size:8px;color:var(--text-secondary);margin-left:2px;">↗</span>'
        : '<span style="font-size:9px;color:var(--text-secondary);margin-left:3px;">↗</span>';
    const underline = opts.compact ? 'none' : '1px dashed var(--border-color)';
    return `<a href="${url}" target="_blank" rel="noopener noreferrer" ${stop} title="Open ${ticker} on Yahoo Finance" style="color:inherit;text-decoration:none;border-bottom:${underline};">${ticker}${glyph}</a>`;
}


// ─── Bot config modals (shared) ──────────────────────────────
//
// Pattern: each bot's config panel lives inside a hidden
// `.settings-modal` (and a `.settings-backdrop` sibling for click-to-close).
// Open with openBotConfig('cb'), close with closeBotConfig('cb').
// Reuses the same CSS that the user Settings modal already uses.

function openBotConfig(slug) {
    const m = document.getElementById(`${slug}-config-modal`);
    const b = document.getElementById(`${slug}-config-backdrop`);
    if (m) m.style.display = 'flex';
    if (b) b.style.display = 'block';
}

function closeBotConfig(slug) {
    const m = document.getElementById(`${slug}-config-modal`);
    const b = document.getElementById(`${slug}-config-backdrop`);
    if (m) m.style.display = 'none';
    if (b) b.style.display = 'none';
}

// Close any open bot config when Escape is pressed.
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        ['cb', 'crypto-bot', 'stock-bot', 'watchdog'].forEach(closeBotConfig);
    }
});


// ─── Auth: Load Current User ─────────────────────────────────

async function loadCurrentUser() {
    try {
        const resp = await fetch('/api/me');
        if (resp.status === 401) {
            window.location.href = '/auth/login';
            return;
        }
        currentUser = await resp.json();
        applyUserRole();
        loadBillingStatus();
        loadMarketPulse();
    } catch (e) {
        console.error('Failed to load user:', e);
    }
}

async function loadBillingStatus() {
    try {
        const resp = await fetch('/billing/status?t=' + Date.now());
        if (resp.ok) {
            billingStatus = await resp.json();
            updateUsageBadge();
        }
    } catch (e) {
        console.error('Failed to load billing status:', e);
    }
}

function updateUsageBadge() {
    if (!billingStatus) return;
    const label = document.getElementById('usage-badge');
    const fill = document.getElementById('usage-gauge-fill');
    const gauge = document.getElementById('usage-gauge');

    if (label) {
        if (billingStatus.limit === null) {
            label.textContent = 'Unlimited';
            if (fill) { fill.style.width = '0%'; fill.className = 'usage-gauge-fill'; }
            if (gauge) gauge.title = 'Admin — unlimited LLM calls';
        } else {
            const used = billingStatus.used || 0;
            const limit = billingStatus.limit || 1;
            const pct = Math.min(100, Math.round((used / limit) * 100));
            label.textContent = `${used}/${limit}`;

            if (fill) {
                fill.style.width = pct + '%';
                if (pct >= 90) {
                    fill.className = 'usage-gauge-fill critical';
                    label.style.color = '#ef5350';
                } else if (pct >= 70) {
                    fill.className = 'usage-gauge-fill warn';
                    label.style.color = '#ffa726';
                } else {
                    fill.className = 'usage-gauge-fill';
                    label.style.color = '#26a69a';
                }
            }
            if (gauge) gauge.title = `LLM calls: ${used} of ${limit} used (24h rolling) — ${billingStatus.tier} tier`;
        }
    }
    const tierBadge = document.getElementById('tier-badge');
    if (tierBadge) {
        const tier = billingStatus.tier || 'free';
        tierBadge.textContent = tier.charAt(0).toUpperCase() + tier.slice(1);
        tierBadge.className = 'tier-badge tier-' + tier;
    }
}

// ─── Market Pulse (header tiles) ────────────────────────────

async function loadMarketPulse() {
    try {
        const resp = await fetch('/api/market-pulse');
        if (!resp.ok) return;
        const d = await resp.json();
        renderMarketPulse(d);
    } catch (e) {
        console.error('Market pulse error:', e);
    }
    loadMarketGauge();  // BUY/HOLD/SELL signal tile — same cadence as the pulse
}

// ─── Market Gauge signal tile (BUY / HOLD / SELL) ───────────
async function loadMarketGauge() {
    const stance = document.getElementById('pulse-gauge-stance');
    if (!stance) return;
    try {
        const resp = await fetch('/api/market/gauge');
        if (!resp.ok) return;
        const g = await resp.json();
        const meter = document.getElementById('pulse-gauge-meter');
        const marker = document.getElementById('pulse-gauge-marker');
        const tile = document.getElementById('pulse-gauge');
        if (!g.available) {
            stance.textContent = '—';
            if (meter) meter.style.display = 'none';
            return;
        }
        stance.textContent = g.stance;            // BUY / HOLD / SELL
        stance.style.color = g.color;
        // Meter: red→orange→yellow→green band, marker at the score position.
        if (meter) meter.style.display = 'block';
        if (marker) {
            const pos = Math.max(0, Math.min(100, (g.score + 100) / 2));  // -100..100 → 0..100%
            marker.style.left = pos + '%';
        }
        if (tile) tile.title = (g.label || '') +
            (g.reasons && g.reasons.length ? '  —  ' + g.reasons.join('  •  ') : '');
    } catch (e) {
        console.error('Market gauge error:', e);
    }
}

// Position a header meter marker: where `val` sits in [lo, hi] as 0-100%.
function _meterMark(markerId, val, lo, hi) {
    const m = document.getElementById(markerId);
    if (!m) return;
    if (hi == null || lo == null || hi <= lo) { m.style.left = '50%'; return; }
    m.style.left = Math.max(0, Math.min(100, (val - lo) / (hi - lo) * 100)) + '%';
}

function renderMarketPulse(d) {
    // Track signals for regime synthesis
    let spyBull = 0;  // -2 to +2
    let vixFear = 0;  // -2 to +2 (positive = more fear)
    let fgSignal = 0; // -2 to +2
    let vixLevel = 20;

    // ── SPY ──
    if (d.spy) {
        const el = document.getElementById('pulse-spy-price');
        const chg = document.getElementById('pulse-spy-change');
        const rng = document.getElementById('pulse-spy-range');
        const tile = document.getElementById('pulse-spy');
        if (el) el.textContent = '$' + d.spy.price.toFixed(2);
        if (chg) {
            const pct = d.spy.change_pct;
            chg.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
            chg.className = 'pulse-change ' + (pct >= 0 ? 'pulse-green' : 'pulse-red');
            if (tile) tile.className = 'pulse-tile ' + (pct >= 0 ? 'tile-green' : 'tile-red');
            // Score: strong move = ±2, mild = ±1
            if (pct >= 1.0) spyBull = 2;
            else if (pct >= 0.3) spyBull = 1;
            else if (pct <= -1.0) spyBull = -2;
            else if (pct <= -0.3) spyBull = -1;
        }
        if (rng) rng.textContent = 'L ' + d.spy.day_low.toFixed(2) + ' — H ' + d.spy.day_high.toFixed(2);
        // Meter: where price sits in the day range (low → high, red → green).
        _meterMark('pulse-spy-marker', d.spy.price, d.spy.day_low, d.spy.day_high);
    }

    // ── VIX ── (inverted: high VIX = bearish)
    if (d.vix) {
        const el = document.getElementById('pulse-vix-value');
        const chg = document.getElementById('pulse-vix-change');
        const sub = document.getElementById('pulse-vix-sub');
        const tile = document.getElementById('pulse-vix');
        const v = d.vix.value;
        vixLevel = v;
        let color = 'green';
        let vixLabel = 'Low volatility';
        if (v >= 30) { color = 'red'; vixLabel = 'Extreme fear'; vixFear = 2; }
        else if (v >= 23) { color = 'orange'; vixLabel = 'Elevated fear'; vixFear = 1; }
        else if (v >= 17) { color = 'yellow'; vixLabel = 'Normal'; vixFear = 0; }
        else { vixLabel = 'Complacent'; vixFear = -1; }

        if (el) { el.textContent = v.toFixed(2); el.className = 'pulse-value pulse-' + color; }
        if (tile) tile.className = 'pulse-tile tile-' + color;
        if (sub) sub.textContent = vixLabel;
        // Meter: VIX in its 5-day range (low → high, green → red = calm → fear).
        if (d.vix.day_low != null && d.vix.day_high != null) {
            _meterMark('pulse-vix-marker', v, d.vix.day_low, d.vix.day_high);
        } else {
            _meterMark('pulse-vix-marker', v, 10, 40);  // fallback scale
        }
        if (chg) {
            const pct = d.vix.change_pct;
            chg.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
            chg.className = 'pulse-change ' + (pct <= 0 ? 'pulse-green' : 'pulse-red');
            // VIX rising fast = extra bearish signal
            if (pct >= 10) vixFear += 1;
            else if (pct <= -10) vixFear -= 1;
        }
    }

    // ── Fear & Greed ──
    if (d.fear_greed) {
        const el = document.getElementById('pulse-fg-score');
        const sub = document.getElementById('pulse-fg-rating');
        const tile = document.getElementById('pulse-fg');
        const s = d.fear_greed.score;
        let color = 'yellow';
        if (s >= 75) { color = 'green'; fgSignal = 2; }
        else if (s >= 55) { color = 'green'; fgSignal = 1; }
        else if (s <= 15) { color = 'red'; fgSignal = -2; }
        else if (s <= 30) { color = 'red'; fgSignal = -1; }
        else if (s <= 45) { color = 'orange'; fgSignal = -1; }

        if (el) { el.textContent = s; el.className = 'pulse-value pulse-' + color; }
        if (tile) tile.className = 'pulse-tile tile-' + color;
        if (sub) sub.textContent = d.fear_greed.rating;
        // Meter: 0 (extreme fear, red) → 100 (extreme greed, green).
        _meterMark('pulse-fg-marker', s, 0, 100);
    }

    // ── Market Regime Synthesis ──
    // Combine: SPY momentum + VIX fear level + Fear & Greed sentiment
    // Score range: -6 (extreme bear) to +6 (extreme bull)
    const regimeScore = spyBull - vixFear + fgSignal;
    const regimeEl = document.getElementById('pulse-regime-value');
    const regimeSub = document.getElementById('pulse-regime-sub');
    const regimeTile = document.getElementById('pulse-regime');

    let regime = '', regimeColor = '', regimeAdvice = '';

    if (regimeScore >= 4) {
        regime = 'STRONG BULL';
        regimeColor = '#00c896';
        regimeAdvice = 'Risk-on — favor longs, momentum plays';
    } else if (regimeScore >= 2) {
        regime = 'BULLISH';
        regimeColor = '#00c896';
        regimeAdvice = 'Cautious longs — buy dips, trail stops';
    } else if (regimeScore >= 1) {
        regime = 'LEAN BULLISH';
        regimeColor = '#8bc34a';
        regimeAdvice = 'Selective longs — quality setups only';
    } else if (regimeScore >= -1) {
        regime = 'NEUTRAL';
        regimeColor = '#ffc837';
        regimeAdvice = 'Range-bound — reduce size, wait for clarity';
    } else if (regimeScore >= -2) {
        regime = 'LEAN BEARISH';
        regimeColor = '#ff8c42';
        regimeAdvice = 'Defensive — tighten stops, reduce exposure';
    } else if (regimeScore >= -4) {
        regime = 'BEARISH';
        regimeColor = '#ff4757';
        regimeAdvice = 'Risk-off — hedge or go to cash';
    } else {
        regime = 'EXTREME FEAR';
        regimeColor = '#ff4757';
        regimeAdvice = 'Capitulation — protect capital, watch for reversal';
    }

    if (regimeEl) {
        regimeEl.textContent = regime;
        regimeEl.style.color = regimeColor;
    }
    if (regimeSub) {
        regimeSub.textContent = regimeAdvice;
        regimeSub.style.color = regimeColor;
    }
    if (regimeTile) {
        regimeTile.style.borderLeftColor = regimeColor;
    }
    // Meter: regime score -6 (bear, red) → +6 (bull, green).
    _meterMark('pulse-regime-marker', regimeScore, -6, 6);

    // ── Dynamic logo color based on market regime ──
    const logoEl = document.querySelector('.logo');
    if (logoEl) {
        logoEl.classList.remove('logo-bull', 'logo-bear', 'logo-warn');
        if (regimeScore >= 1) {
            logoEl.classList.add('logo-bull');      // green shimmer
        } else if (regimeScore <= -2) {
            logoEl.classList.add('logo-bear');      // red shimmer
        } else if (regimeScore <= -1) {
            logoEl.classList.add('logo-warn');      // orange shimmer
        }
        // neutral (0) = default blue gradient
    }

    // ── Poly/Kalshi Sentiment ──
    if (d.poly_sentiment) {
        const polyMood = document.getElementById('pulse-poly-mood');
        const polyDesc = document.getElementById('pulse-poly-desc');
        const polyTile = document.getElementById('pulse-poly');
        if (polyMood) {
            polyMood.textContent = d.poly_sentiment.mood;
            polyMood.style.color = d.poly_sentiment.color;
        }
        if (polyDesc) {
            polyDesc.textContent = d.poly_sentiment.description;
            polyDesc.style.color = d.poly_sentiment.color;
        }
        if (polyTile) polyTile.style.borderLeftColor = d.poly_sentiment.color;
        // Meter: net sentiment score (bearish → bullish); clamp to ±10 band.
        _meterMark('pulse-poly-marker', d.poly_sentiment.score || 0, -10, 10);
    }

    // ── Trump Mood ──
    if (d.trump_mood) {
        const tm = d.trump_mood;
        const trumpMood = document.getElementById('pulse-trump-mood');
        const trumpPat = document.getElementById('pulse-trump-pattern');
        const trumpTile = document.getElementById('pulse-trump');
        if (trumpMood) {
            trumpMood.textContent = tm.label;
            trumpMood.style.color = tm.color;
        }
        if (trumpPat) {
            const pat = tm.pattern || {};
            const trend = pat.trend || 'unknown';
            const arrow = trend === 'improving' ? '\u2191' : trend === 'deteriorating' ? '\u2193' : '\u2194';
            trumpPat.textContent = `${arrow} ${trend} | ${tm.posts_analyzed} posts`;
            trumpPat.style.color = tm.color;
        }
        if (trumpTile) {
            trumpTile.style.borderLeftColor = tm.color;
            // Glow animation based on mood intensity
            const intensity = Math.abs(tm.mood);
            if (intensity > 30) {
                trumpTile.className = `pulse-tile tile-${tm.mood > 0 ? 'green' : 'red'}`;
            } else if (intensity > 10) {
                trumpTile.className = `pulse-tile tile-${tm.mood > 0 ? 'green' : 'orange'}`;
            } else {
                trumpTile.className = 'pulse-tile tile-yellow';
            }
        }
        // Meter: mood -100 (aggressive, red) → +100 (market-friendly, green).
        _meterMark('pulse-trump-marker', tm.mood || 0, -100, 100);
    }
}

// ── Trump Mood Detail Popup ──
async function showTrumpMoodDetail() {
    try {
        const resp = await fetch('/api/trump-mood');
        if (!resp.ok) { alert('Failed to load Trump Mood (not logged in?)'); return; }
        const d = await resp.json();

        let html = `<div style="padding:20px;max-width:600px;">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
                <div style="font-size:28px;font-weight:800;color:${d.color}">${d.label}</div>
                <div style="font-size:36px;font-weight:800;color:${d.color}">${d.mood > 0 ? '+' : ''}${d.mood}</div>
            </div>
            <div style="font-size:13px;color:var(--text-secondary);margin-bottom:16px;">${d.description}</div>`;

        // 3-day pattern
        const pat = d.pattern || {};
        if (pat.day_scores && pat.day_scores.length === 3) {
            const labels = ['2d ago', 'Yesterday', 'Today'];
            const maxAbs = Math.max(...pat.day_scores.map(Math.abs), 1);
            html += `<div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:6px;">3-Day Pattern: ${pat.trend}</div>`;
            html += `<div style="display:flex;gap:8px;margin-bottom:16px;">`;
            pat.day_scores.forEach((s, i) => {
                const pct = Math.abs(s) / maxAbs * 100;
                const col = s >= 0 ? '#00c896' : '#ff4757';
                html += `<div style="flex:1;text-align:center;">
                    <div style="font-size:10px;color:var(--text-secondary)">${labels[i]}</div>
                    <div style="height:40px;display:flex;align-items:flex-end;justify-content:center;">
                        <div style="width:80%;height:${Math.max(pct, 5)}%;background:${col};border-radius:3px;"></div>
                    </div>
                    <div style="font-size:12px;font-weight:700;color:${col}">${s > 0 ? '+' : ''}${s}</div>
                </div>`;
            });
            html += `</div>`;
        }

        // Top signals
        if (d.top_signals && d.top_signals.length > 0) {
            html += `<div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:6px;">Key Signals Detected</div>`;
            html += `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:16px;">`;
            d.top_signals.forEach(s => {
                const col = s.type === 'bullish' ? '#00c896' : '#ff4757';
                const bg = s.type === 'bullish' ? 'rgba(0,200,150,0.1)' : 'rgba(255,71,87,0.1)';
                html += `<span style="padding:3px 8px;border-radius:4px;font-size:11px;font-weight:600;background:${bg};color:${col};border:1px solid ${col}33;">${s.text} (${s.score > 0 ? '+' : ''}${s.score})</span>`;
            });
            html += `</div>`;
        }

        // Notable posts
        if (d.notable_posts && d.notable_posts.length > 0) {
            html += `<div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:6px;">Notable Posts</div>`;
            d.notable_posts.forEach(p => {
                const srcIcon = p.source === 'truth_social' ? 'TS' : p.source === 'whitehouse' ? 'WH' : 'News';
                const col = p.score >= 0 ? '#00c896' : '#ff4757';
                html += `<div style="background:var(--bg-tertiary);border-radius:8px;padding:10px;margin-bottom:6px;font-size:12px;line-height:1.5;">
                    <span style="font-size:9px;font-weight:700;color:var(--text-secondary);text-transform:uppercase;">${srcIcon}</span>
                    <span style="color:${col};font-weight:700;margin-left:6px;">${p.score > 0 ? '+' : ''}${p.score}</span>
                    <div style="color:var(--text-primary);margin-top:4px;">${p.text}</div>
                </div>`;
            });
        }

        html += `<div style="font-size:9px;color:var(--text-secondary);margin-top:12px;">
            Sources: Truth Social (${d.sources?.truth_social || 0}) + News (${d.sources?.news || 0}) + White House (${d.sources?.whitehouse || 0})
            | ${d.posts_analyzed} posts analyzed | ${d.cached ? 'Cached' : 'Fresh'} | 15-min cache
        </div></div>`;

        // Show in a modal
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;';
        overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
        const modal = document.createElement('div');
        modal.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;max-width:90vw;max-height:85vh;overflow-y:auto;';
        modal.innerHTML = html;
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
    } catch (e) {
        alert('Failed to load Trump Mood: ' + e.message);
    }
}

// Refresh market pulse every 5 minutes
setInterval(loadMarketPulse, 300000);

// ── Trump Mood hourly auto-refresh ──
async function refreshTrumpMood() {
    try {
        const resp = await fetch('/api/trump-mood');
        if (!resp.ok) return;
        const tm = await resp.json();
        const trumpMood = document.getElementById('pulse-trump-mood');
        const trumpPat = document.getElementById('pulse-trump-pattern');
        const trumpTile = document.getElementById('pulse-trump');
        if (trumpMood) {
            trumpMood.textContent = tm.label;
            trumpMood.style.color = tm.color;
        }
        if (trumpPat) {
            const pat = tm.pattern || {};
            const trend = pat.trend || 'unknown';
            const arrow = trend === 'improving' ? '\u2191' : trend === 'deteriorating' ? '\u2193' : '\u2194';
            trumpPat.textContent = `${arrow} ${trend} | ${tm.posts_analyzed} posts`;
            trumpPat.style.color = tm.color;
        }
        if (trumpTile) {
            const intensity = Math.abs(tm.mood);
            if (intensity > 30) {
                trumpTile.className = `pulse-tile tile-${tm.mood > 0 ? 'green' : 'red'}`;
            } else if (intensity > 10) {
                trumpTile.className = `pulse-tile tile-${tm.mood > 0 ? 'green' : 'orange'}`;
            } else {
                trumpTile.className = 'pulse-tile tile-yellow';
            }
        }
        console.log(`Trump mood refreshed: ${tm.label} (${tm.mood}) — ${tm.posts_analyzed} posts`);
    } catch (e) {
        console.error('Trump mood refresh error:', e);
    }
}
// Refresh trump mood every hour (fetches fresh data from GDELT/Truth Social/WH)
setInterval(refreshTrumpMood, 3600000);


// ─── Trump Tab ──────────────────────────────────────────────

async function loadTrumpTab(force) {
    const days = document.getElementById('trump-history-days')?.value || 30;

    // Load current mood + history + backtracks in parallel
    const [moodResp, histResp, btResp] = await Promise.allSettled([
        fetch('/api/trump-mood' + (force ? '?force=1' : '')),
        fetch('/api/trump/history?days=' + days),
        fetch('/api/trump/backtracks?days=' + days),
    ]);

    // Check for subscription gate (403)
    if (moodResp.status === 'fulfilled' && moodResp.value.status === 403) {
        const err = await moodResp.value.json();
        document.getElementById('trump-current-mood').innerHTML = `
            <div style="text-align:center;padding:30px 0;">
                <div style="font-size:28px;margin-bottom:8px;">&#128274;</div>
                <div style="font-size:14px;font-weight:700;color:var(--text-bright);margin-bottom:6px;">Pro Feature</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:14px;">${err.error || 'Upgrade to Pro for Trump Market Indicator'}</div>
                <button onclick="showPricingModal()" class="btn-analyze" style="padding:8px 20px;font-size:12px;">Upgrade to Pro</button>
            </div>`;
        return;
    }

    // Render current mood card
    if (moodResp.status === 'fulfilled' && moodResp.value.ok) {
        const mood = await moodResp.value.json();
        renderTrumpActionable(mood);
        renderTrumpCurrentMood(mood);
        renderTrumpTradeSignals(mood);
        renderTrumpSignals(mood);
        renderTrumpNotable(mood);
    }

    // Parse backtracks first so we can pass them to chart
    let btData = null;
    if (btResp.status === 'fulfilled' && btResp.value.ok) {
        btData = await btResp.value.json();
    }

    // Render history chart + table (with backtrack milestones)
    if (histResp.status === 'fulfilled' && histResp.value.ok) {
        const history = await histResp.value.json();
        renderTrumpChart(history, btData ? btData.backtracks || [] : []);
        renderTrumpHistoryTable(history);
    }

    // Render backtracks
    if (btData) {
        renderBacktrackStats(btData);
        renderBacktrackTimeline(btData.backtracks || []);
    }

    // Auto-load prediction + backtrack prediction if not forced (cached)
    loadTrumpPrediction(false);
    loadBacktrackPrediction(false);

    // Auto-load ML forecast + correlation (cached by default)
    loadTrumpForecast(false);
    loadTrumpCorrelation();
}


// ─── Trump → Market ML Forecaster ────────────────────────────

async function loadTrumpForecast(force) {
    const grid = document.getElementById('trump-forecast-grid');
    const accEl = document.getElementById('trump-forecast-accuracy');
    if (!grid) return;

    if (force) {
        grid.innerHTML = `<div style="padding:24px;text-align:center;font-size:11px;color:var(--text-secondary);grid-column:1/-1;">
            <div class="spinner" style="margin:0 auto 8px;"></div>
            Running self-learning ensemble (Ridge + KNN + EWMA + Opus)...
        </div>`;
    }

    try {
        const resp = await fetch('/api/trump/forecast' + (force ? '?force=1' : ''));
        if (!resp.ok) {
            grid.innerHTML = `<div style="padding:16px;text-align:center;font-size:11px;color:#ff4757;grid-column:1/-1;">Forecast unavailable.</div>`;
            return;
        }
        const data = await resp.json();
        renderTrumpForecast(data);
        renderForecastAccuracySummary(data.accuracy || {}, accEl);
    } catch (e) {
        console.warn('Trump forecast failed:', e);
        grid.innerHTML = `<div style="padding:16px;text-align:center;font-size:11px;color:#ff4757;grid-column:1/-1;">Error loading forecast.</div>`;
    }
}

function renderTrumpForecast(data) {
    const grid = document.getElementById('trump-forecast-grid');
    if (!grid) return;
    // Make the grid take full row for our custom layout
    grid.style.display = 'block';

    const forecasts = data.forecasts || {};
    const keys = Object.keys(forecasts);
    if (!keys.length) {
        grid.innerHTML = `<div style="padding:16px;text-align:center;font-size:11px;color:var(--text-secondary);">No forecast data.</div>`;
        return;
    }

    const horizonOrder = ['1D','5D','21D'];
    const horizonLabel = {'1D':'1D','5D':'1W','21D':'1M'};

    // ── Erratic gauge strip (all visual) ─────────────────────
    const em = data.erratic_metrics || {};
    const persistPct = Math.round((em.persistence_score || 0.5) * 100);
    const persistColor = persistPct >= 70 ? '#00c896' : persistPct >= 45 ? '#ffc837' : '#ff4757';
    const persistLabel = persistPct >= 70 ? 'STABLE' : persistPct >= 45 ? 'MIXED' : 'ERRATIC';
    const volPct = Math.min(100, Math.round((em.mood_volatility_14d || 0) * 2));
    const flips = em.swing_count_14d || 0;
    const mrp = em.mean_reversion_pressure || 0;
    const mrpColor = mrp >= 1.5 ? '#ff4757' : mrp >= 1.0 ? '#ff8c42' : '#8bc34a';

    // Tiny arc gauge generator (SVG semi-circle)
    function arcGauge(pct, color, label, sublabel) {
        const angle = Math.PI * (pct / 100);
        const r = 26, cx = 32, cy = 30;
        const x = cx - r * Math.cos(angle);
        const y = cy - r * Math.sin(angle);
        const large = pct > 50 ? 1 : 0;
        return `<div style="display:flex;flex-direction:column;align-items:center;gap:2px;min-width:76px;">
            <svg width="64" height="38" viewBox="0 0 64 38">
                <path d="M 6 30 A 26 26 0 0 1 58 30" stroke="var(--border-color)" stroke-width="4" fill="none" stroke-linecap="round"/>
                <path d="M 6 30 A 26 26 0 ${large} 1 ${x.toFixed(1)} ${y.toFixed(1)}" stroke="${color}" stroke-width="4" fill="none" stroke-linecap="round"/>
                <text x="32" y="28" text-anchor="middle" fill="${color}" font-size="12" font-weight="800">${pct}</text>
            </svg>
            <div style="font-size:8px;color:var(--text-secondary);text-transform:uppercase;font-weight:700;letter-spacing:0.5px;">${label}</div>
            <div style="font-size:9px;color:${color};font-weight:700;">${sublabel}</div>
        </div>`;
    }

    // Dot-pattern for flip count
    function flipDots(n) {
        const max = 8;
        let d = '';
        for (let i = 0; i < max; i++) {
            const active = i < n;
            d += `<span style="width:8px;height:8px;border-radius:50%;background:${active ? '#ff8c42' : 'var(--border-color)'};display:inline-block;"></span>`;
        }
        return `<div style="display:flex;flex-direction:column;align-items:center;gap:4px;min-width:80px;">
            <div style="display:flex;gap:3px;align-items:center;height:38px;">${d}</div>
            <div style="font-size:8px;color:var(--text-secondary);text-transform:uppercase;font-weight:700;letter-spacing:0.5px;">Flips 14d</div>
            <div style="font-size:9px;color:${n > 2 ? '#ff8c42' : '#8bc34a'};font-weight:700;">${n}</div>
        </div>`;
    }

    // Mean reversion pressure as horizontal needle
    function needleGauge(zScore, color) {
        const pct = Math.min(100, Math.max(0, (zScore / 3) * 100));
        return `<div style="display:flex;flex-direction:column;align-items:center;gap:4px;min-width:90px;">
            <div style="height:38px;display:flex;align-items:center;width:74px;position:relative;">
                <div style="width:100%;height:6px;background:linear-gradient(to right,#8bc34a,#ffc837,#ff8c42,#ff4757);border-radius:3px;position:relative;">
                    <div style="position:absolute;left:${pct}%;top:-4px;width:3px;height:14px;background:var(--text-bright);transform:translateX(-50%);border-radius:2px;box-shadow:0 1px 3px rgba(0,0,0,0.6);"></div>
                </div>
            </div>
            <div style="font-size:8px;color:var(--text-secondary);text-transform:uppercase;font-weight:700;letter-spacing:0.5px;">Revert z</div>
            <div style="font-size:9px;color:${color};font-weight:700;">${zScore.toFixed(2)}</div>
        </div>`;
    }

    // Current vs 30d baseline — delta bar
    const cvb = em.current_vs_baseline || 0;
    const cvbAbs = Math.min(100, Math.abs(cvb));
    const cvbColor = cvb > 10 ? '#00c896' : cvb < -10 ? '#ff4757' : '#ffc837';
    function deltaBar(cur, baseline) {
        const a = Math.max(-100, Math.min(100, cur));
        const b = Math.max(-100, Math.min(100, baseline));
        const toX = v => 50 + (v / 2); // -100..100 → 0..100
        return `<div style="display:flex;flex-direction:column;align-items:center;gap:4px;min-width:120px;">
            <div style="height:38px;width:110px;position:relative;display:flex;align-items:center;">
                <div style="width:100%;height:6px;background:linear-gradient(to right,#ff4757,#ff8c42,#ffc837,#8bc34a,#00c896);border-radius:3px;position:relative;">
                    <div title="30d baseline" style="position:absolute;left:${toX(b)}%;top:-3px;width:2px;height:12px;background:var(--text-secondary);transform:translateX(-50%);"></div>
                    <div title="Current" style="position:absolute;left:${toX(a)}%;top:-6px;width:10px;height:18px;background:var(--text-bright);border:2px solid var(--bg-tertiary);border-radius:3px;transform:translateX(-50%);"></div>
                </div>
            </div>
            <div style="font-size:8px;color:var(--text-secondary);text-transform:uppercase;font-weight:700;letter-spacing:0.5px;">Mood vs 30d</div>
            <div style="font-size:9px;color:${cvbColor};font-weight:700;">${cvb > 0 ? '+' : ''}${cvb} pts</div>
        </div>`;
    }

    let html = `
        <div style="background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:10px;padding:12px 16px;margin-bottom:10px;">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;">
                <div style="display:flex;flex-direction:column;min-width:110px;">
                    <span style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;font-weight:700;letter-spacing:0.5px;">Behavior</span>
                    <span style="font-size:18px;font-weight:900;color:${persistColor};line-height:1;margin-top:2px;">${persistLabel}</span>
                    <span style="font-size:10px;color:var(--text-secondary);">${persistPct}% persistence</span>
                </div>
                <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;">
                    ${arcGauge(volPct, volPct > 70 ? '#ff4757' : volPct > 40 ? '#ff8c42' : '#8bc34a', 'Volatility', em.mood_volatility_14d || 0)}
                    ${flipDots(flips)}
                    ${needleGauge(mrp, mrpColor)}
                    ${deltaBar(data.mood_snapshot?.mood || 0, em.baseline_mood_30d || 0)}
                </div>
            </div>
        </div>`;

    // ── Forecast matrix (assets × horizons) ────────────────
    // Header row
    html += `<div style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:10px;overflow:hidden;">
        <div style="display:grid;grid-template-columns:110px repeat(3,1fr);gap:0;background:var(--bg-secondary);padding:8px 12px;border-bottom:1px solid var(--border-color);">
            <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;font-weight:800;letter-spacing:0.5px;">Asset</div>
            ${horizonOrder.map(h => `<div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;font-weight:800;letter-spacing:0.5px;text-align:center;">${horizonLabel[h]}</div>`).join('')}
        </div>`;

    // Compute global max pred for bar scaling
    let globalMax = 1;
    keys.forEach(a => horizonOrder.forEach(h => {
        const pct = Math.abs(forecasts[a]?.horizons?.[h]?.predicted_return_pct || 0);
        if (pct > globalMax) globalMax = pct;
    }));

    keys.forEach((asset, rowIdx) => {
        const f = forecasts[asset];
        const isCrypto = f.class === 'crypto';
        const accent = isCrypto ? '#f7931a' : '#4f8aff';

        html += `<div style="display:grid;grid-template-columns:110px repeat(3,1fr);gap:0;padding:10px 12px;border-bottom:${rowIdx < keys.length - 1 ? '1px solid var(--border-color)' : 'none'};align-items:stretch;">
            <div style="display:flex;flex-direction:column;justify-content:center;padding-right:8px;">
                <div style="display:flex;align-items:center;gap:6px;">
                    <span style="width:6px;height:22px;background:${accent};border-radius:3px;"></span>
                    <div>
                        <div style="font-size:14px;font-weight:900;color:var(--text-bright);line-height:1;">${asset}</div>
                        <div style="font-size:9px;color:var(--text-secondary);margin-top:2px;">${f.label}</div>
                    </div>
                </div>
            </div>`;

        horizonOrder.forEach(h => {
            const hf = f.horizons[h];
            if (!hf) {
                html += `<div style="text-align:center;color:var(--text-secondary);font-size:10px;">—</div>`;
                return;
            }
            const pct = hf.predicted_return_pct;
            const absPct = Math.abs(pct);
            const barW = Math.min(100, (absPct / globalMax) * 100);
            const color = pct > 0.15 ? '#00c896' : pct < -0.15 ? '#ff4757' : '#ffc837';
            const arrow = pct > 0.15 ? '&#9650;' : pct < -0.15 ? '&#9660;' : '&#9654;';
            const conf = Math.round(hf.confidence * 100);
            const rr = hf.reversal_risk || {};
            const rrP = Math.round((rr.probability || 0) * 100);
            const rrColor = rr.assessment === 'HIGH' ? '#ff4757' : rr.assessment === 'MEDIUM' ? '#ff8c42' : '#8bc34a';

            const sc = hf.scenarios || {};
            const persist = sc.persist || {};
            const flip = sc.flip || {};
            const persistP = Math.round((persist.probability || 0) * 100);
            const flipP = Math.round((flip.probability || 0) * 100);
            const persistColor2 = (persist.predicted_return_pct || 0) >= 0 ? '#00c896' : '#ff4757';
            const flipColor2 = (flip.predicted_return_pct || 0) >= 0 ? '#00c896' : '#ff4757';

            // Full tooltip (for hover only)
            const tipParts = [];
            if (hf.reasoning) tipParts.push(hf.reasoning);
            tipParts.push(`Persist ${persistP}%: ${persist.predicted_return_pct > 0 ? '+' : ''}${persist.predicted_return_pct}%`);
            tipParts.push(`Flip ${flipP}%: ${flip.predicted_return_pct > 0 ? '+' : ''}${flip.predicted_return_pct}%`);
            const mp = hf.model_predictions || {};
            tipParts.push(Object.keys(mp).map(m => `${m}:${mp[m] > 0 ? '+' : ''}${mp[m]}%`).join(' | '));
            const tip = tipParts.join('\n').replace(/"/g, '&quot;');

            html += `<div title="${tip}" style="padding:0 6px;display:flex;flex-direction:column;justify-content:center;align-items:center;cursor:help;">
                <!-- Big arrow + number -->
                <div style="display:flex;align-items:baseline;gap:4px;">
                    <span style="font-size:14px;color:${color};font-weight:900;">${arrow}</span>
                    <span style="font-size:22px;font-weight:900;color:${color};line-height:1;">${pct > 0 ? '+' : ''}${pct}<span style="font-size:13px;">%</span></span>
                </div>
                <!-- Magnitude bar -->
                <div style="width:80%;height:4px;background:var(--bg-secondary);border-radius:2px;margin:5px 0 3px;overflow:hidden;">
                    <div style="width:${barW}%;height:100%;background:${color};border-radius:2px;"></div>
                </div>
                <!-- Persist vs flip stacked bar -->
                <div style="width:80%;height:8px;background:var(--bg-secondary);border-radius:4px;display:flex;overflow:hidden;margin:3px 0;" title="Persist ${persistP}% / Flip ${flipP}%">
                    <div style="width:${persistP}%;background:${persistColor2};opacity:0.9;" title="Persist: ${persistP}%"></div>
                    <div style="width:${flipP}%;background:${flipColor2};opacity:0.55;border-left:1px solid rgba(255,255,255,0.2);" title="Flip: ${flipP}%"></div>
                </div>
                <!-- Confidence + flip risk chips -->
                <div style="display:flex;gap:5px;align-items:center;margin-top:4px;font-size:8px;text-transform:uppercase;letter-spacing:0.3px;font-weight:700;">
                    <span title="Confidence" style="color:var(--text-secondary);">
                        <span style="display:inline-block;width:5px;height:5px;border-radius:50%;background:${conf >= 50 ? '#00c896' : conf >= 30 ? '#ffc837' : '#ff4757'};margin-right:3px;vertical-align:middle;"></span>${conf}%
                    </span>
                    <span style="color:var(--text-secondary);opacity:0.5;">&middot;</span>
                    <span title="Reversal risk: ${rr.assessment}" style="color:${rrColor};">
                        &#8634; ${rrP}%
                    </span>
                </div>
            </div>`;
        });

        html += `</div>`;
    });
    html += `</div>`;

    // ── Bottom legend (compact) ──────────────────────────
    html += `<div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;font-size:9px;color:var(--text-secondary);flex-wrap:wrap;gap:10px;">
        <div style="display:flex;gap:12px;align-items:center;">
            <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#00c896;opacity:0.9;margin-right:4px;"></span>Persist</span>
            <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#ff4757;opacity:0.55;margin-right:4px;"></span>Flip scenario</span>
            <span>&#8634; = reversal probability</span>
            <span>&#8226; = confidence</span>
        </div>
        <div>${data.training_rows || 0} rows &middot; ${data.cached ? 'cached' : 'fresh'}</div>
    </div>`;

    grid.innerHTML = html;
}

function renderForecastAccuracySummary(accuracy, el) {
    if (!el) return;
    const assets = Object.keys(accuracy);
    if (!assets.length) { el.innerHTML = ''; return; }
    const hOrder = ['1D','5D','21D'];
    const hLabel = {'1D':'1D','5D':'1W','21D':'1M'};

    // Build compact badge matrix — one strip per asset
    let strips = '';
    let anyData = false;
    assets.forEach(a => {
        const h = accuracy[a] || {};
        const chips = hOrder.map(hk => {
            const s = h[hk];
            if (!s || !s.n) {
                return `<span style="padding:2px 6px;border-radius:4px;background:var(--bg-secondary);color:var(--text-secondary);opacity:0.5;font-size:9px;">${hLabel[hk]} —</span>`;
            }
            anyData = true;
            const dir = Math.round((s.direction_accuracy || 0) * 100);
            const dc = dir >= 60 ? '#00c896' : dir >= 45 ? '#ffc837' : '#ff4757';
            return `<span title="MAE ${s.mae}%, n=${s.n}" style="padding:2px 6px;border-radius:4px;background:${dc}22;color:${dc};font-weight:700;font-size:9px;">${hLabel[hk]} ${dir}%</span>`;
        }).join(' ');
        strips += `<span style="margin-right:10px;display:inline-flex;gap:4px;align-items:center;"><strong style="color:var(--text-bright);font-size:9px;">${a}</strong> ${chips}</span>`;
    });
    el.innerHTML = anyData
        ? `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;"><span style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;font-weight:700;">Accuracy:</span>${strips}</div>`
        : '';
}


// ─── Mood ↔ Market Correlation Timeline ─────────────────────

async function loadTrumpCorrelation() {
    const el = document.getElementById('trump-correlation');
    if (!el) return;
    try {
        const resp = await fetch('/api/trump/correlation?days=60');
        if (!resp.ok) { el.innerHTML = '<em>Correlation unavailable.</em>'; return; }
        const data = await resp.json();
        renderCorrelationHeatmap(data, el);
    } catch (e) {
        console.warn('Correlation failed:', e);
        el.innerHTML = '<em>Error loading correlation.</em>';
    }
}

function renderCorrelationHeatmap(data, el) {
    const assets = data.assets || {};
    const best = data.best_lag || {};
    const keys = Object.keys(assets);
    if (!keys.length) {
        el.innerHTML = `<div style="text-align:center;padding:20px;font-size:11px;color:var(--text-secondary);">${data.note || 'No correlation data yet.'}</div>`;
        return;
    }

    const lags = [0,1,2,3,4,5,6,7];

    function cellColor(c) {
        if (c === null || c === undefined || isNaN(c)) return 'transparent';
        const intensity = Math.min(1, Math.abs(c) * 2.5);
        if (c > 0) return `rgba(0,200,150,${intensity})`;
        return `rgba(255,71,87,${intensity})`;
    }

    let headerCells = '<th style="padding:6px;text-align:left;font-weight:700;color:var(--text-secondary);">Asset</th>' +
        lags.map(l => `<th style="padding:6px;text-align:center;font-weight:700;color:var(--text-secondary);">t-${l}d</th>`).join('') +
        '<th style="padding:6px;text-align:left;font-weight:700;color:var(--text-secondary);">Best Lag</th>';

    let rows = keys.map(k => {
        const a = assets[k];
        const bl = best[k];
        const cells = lags.map(l => {
            const c = a.lags[l];
            const txt = c !== undefined ? c.toFixed(2) : '—';
            const textColor = c !== undefined && Math.abs(c) > 0.15 ? '#fff' : 'var(--text-secondary)';
            return `<td style="padding:6px;text-align:center;background:${cellColor(c)};color:${textColor};font-family:monospace;font-size:11px;">${txt}</td>`;
        }).join('');
        const bestTxt = bl ? `<span style="color:${bl.corr > 0 ? '#00c896' : '#ff4757'};font-weight:700;">lag ${bl.lag_days}d (r=${bl.corr})</span>` : '—';
        return `<tr style="border-bottom:1px solid var(--border-color);">
            <td style="padding:6px;font-weight:700;color:var(--text-bright);">${k} <span style="font-size:9px;color:var(--text-secondary);">${a.label}</span></td>
            ${cells}
            <td style="padding:6px;font-size:10px;">${bestTxt}</td>
        </tr>`;
    }).join('');

    let interpretation = '';
    const sigPairs = [];
    keys.forEach(k => {
        const bl = best[k];
        if (bl && Math.abs(bl.corr) >= 0.20) {
            const dir = bl.corr > 0 ? 'leads up-moves' : 'leads down-moves';
            sigPairs.push(`${k} ${dir} in ${bl.lag_days}d (r=${bl.corr})`);
        }
    });
    if (sigPairs.length) {
        interpretation = `<div style="margin-top:10px;padding:8px 12px;background:var(--bg-secondary);border-radius:6px;border-left:3px solid #4f8aff;font-size:11px;color:var(--text-bright);">
            <strong>Mood Signal:</strong> ${sigPairs.join(' &middot; ')}
        </div>`;
    }

    el.innerHTML = `<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:11px;">
        <thead><tr style="border-bottom:2px solid var(--border-color);">${headerCells}</tr></thead>
        <tbody>${rows}</tbody>
    </table></div>
    <div style="font-size:9px;color:var(--text-secondary);margin-top:8px;">
        <strong>Interpretation:</strong> t-k = mood k days ago vs. today's return. Positive (green) = bullish mood preceded up-move. Negative (red) = rhetoric preceded drawdown. Best lag identifies how many days Trump rhetoric leads the asset.
    </div>
    ${interpretation}`;
}

function renderTrumpActionable(m) {
    const el = document.getElementById('trump-actionable');
    if (!el) return;
    const sig = m.actionable_signal;
    if (!sig) { el.innerHTML = ''; return; }

    const color = sig.color || '#787b86';
    const icon = sig.action === 'BUY' ? '⬆' : sig.action === 'TRIM' ? '⬇' : sig.action === 'WAIT_QUIET' ? '⏸' : '⏺';
    const reasons = (sig.reasons || []).map(r => `<li style="margin:4px 0;font-size:12px;color:var(--text-secondary);">${r}</li>`).join('');
    const factors = (sig.key_factors || []).filter(Boolean).map(f =>
        `<span style="display:inline-block;padding:3px 10px;background:rgba(255,255,255,0.06);border:1px solid var(--border-color);border-radius:12px;font-size:11px;margin:2px 4px 2px 0;color:var(--text-bright);">${f}</span>`
    ).join('');

    el.innerHTML = `
    <div style="background:linear-gradient(135deg,${color}1a,${color}05);border:2px solid ${color};border-radius:14px;padding:20px 24px;">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;">
            <div style="flex:1;min-width:280px;">
                <div style="display:flex;align-items:center;gap:14px;margin-bottom:8px;">
                    <div style="font-size:34px;line-height:1;color:${color};">${icon}</div>
                    <div>
                        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:var(--text-secondary);">Actionable Signal</div>
                        <div style="font-size:22px;font-weight:800;color:${color};line-height:1.1;">${sig.label}</div>
                    </div>
                </div>
                <div style="font-size:14px;color:var(--text-bright);font-weight:600;line-height:1.4;margin-bottom:10px;">${sig.headline}</div>
                <ul style="list-style:none;padding:0;margin:0 0 10px 0;">${reasons}</ul>
                ${factors ? `<div style="margin-top:10px;"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Driving signals</div>${factors}</div>` : ''}
                ${sig.next_watch ? `<div style="margin-top:12px;padding:8px 12px;background:rgba(0,0,0,0.15);border-radius:6px;font-size:11px;color:var(--text-secondary);"><strong style="color:var(--text-bright);">Next:</strong> ${sig.next_watch}</div>` : ''}
            </div>
            <div style="text-align:center;min-width:90px;">
                <div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.5px;">Confidence</div>
                <div style="font-size:32px;font-weight:800;color:${color};line-height:1.1;">${sig.confidence}<span style="font-size:14px;color:var(--text-secondary);">%</span></div>
            </div>
        </div>
    </div>`;
}


function renderTrumpCurrentMood(m) {
    const el = document.getElementById('trump-current-mood');
    if (!el) return;
    const moodPct = ((m.mood + 100) / 200) * 100; // normalize -100..+100 to 0..100
    const pat = m.pattern || {};
    const trendArrow = pat.trend === 'improving' ? '&#9650;' : pat.trend === 'deteriorating' ? '&#9660;' : '&#9654;';
    const trendColor = pat.trend === 'improving' ? '#00c896' : pat.trend === 'deteriorating' ? '#ff4757' : '#ffc837';

    el.innerHTML = `
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:12px;">Current Mood</div>
        <div style="text-align:center;">
            <div style="font-size:48px;font-weight:900;color:${m.color};line-height:1;">${m.mood > 0 ? '+' : ''}${m.mood}</div>
            <div style="font-size:16px;font-weight:800;color:${m.color};margin:6px 0;">${m.label}</div>
            <div style="font-size:11px;color:var(--text-secondary);margin-bottom:14px;">${m.description}</div>
        </div>
        <!-- Mood gauge bar -->
        <div style="position:relative;height:8px;background:linear-gradient(to right, #ff4757, #ff8c42, #ffc837, #8bc34a, #00c896);border-radius:4px;margin:12px 0 8px;">
            <div style="position:absolute;left:${moodPct}%;top:-4px;width:16px;height:16px;background:white;border-radius:50%;transform:translateX(-50%);box-shadow:0 1px 4px rgba(0,0,0,0.4);"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--text-secondary);">
            <span>BEARISH -100</span><span>NEUTRAL</span><span>BULLISH +100</span>
        </div>
        <!-- Trend -->
        <div style="margin-top:14px;display:flex;justify-content:space-between;align-items:center;">
            <div style="font-size:11px;color:var(--text-secondary);">
                3-Day Trend: <span style="color:${trendColor};font-weight:700;">${trendArrow} ${(pat.trend || 'unknown').toUpperCase()}</span>
            </div>
            <div style="font-size:11px;color:var(--text-secondary);">${m.posts_analyzed} posts analyzed</div>
        </div>
        ${pat.day_scores && pat.day_scores.length === 3 ? `
        <div style="display:flex;gap:6px;margin-top:8px;">
            ${['2d ago', 'Yesterday', 'Today'].map((lbl, i) => {
                const s = pat.day_scores[i];
                const c = s > 0 ? '#00c896' : s < 0 ? '#ff4757' : '#ffc837';
                return `<div style="flex:1;text-align:center;padding:6px 4px;background:var(--bg-secondary);border-radius:6px;">
                    <div style="font-size:9px;color:var(--text-secondary);">${lbl}</div>
                    <div style="font-size:14px;font-weight:700;color:${c};">${s > 0 ? '+' : ''}${s}</div>
                </div>`;
            }).join('')}
        </div>` : ''}
        <div style="font-size:9px;color:var(--text-secondary);margin-top:10px;text-align:right;">
            Sources: Truth Social (${m.sources?.truth_social || 0}) | News (${m.sources?.news || 0}) | White House (${m.sources?.whitehouse || 0})
        </div>`;
}

function renderTrumpTradeSignals(mood) {
    const el = document.getElementById('trump-trade-signals');
    if (!el) return;

    const ts = mood.trade_signals;
    if (!ts || (!ts.buy?.length && !ts.avoid?.length)) {
        el.innerHTML = '<div style="text-align:center;padding:10px 0;font-size:11px;color:var(--text-secondary);">No active trade signals — rhetoric is neutral or no policy-specific keywords detected.</div>';
        return;
    }

    const summary = ts.summary || '';
    const activePolicies = (ts.active_policies || []).map(p => {
        const colors = {
            'china_tariffs':'#ff4757','general_tariffs':'#ff8c42','eu_tariffs':'#ffc837',
            'trade_deals':'#00c896','iran_conflict':'#ff4757','crypto_policy':'#4f8aff',
            'mexico_tariffs':'#ff8c42','canada_tariffs':'#ffc837','tax_policy':'#00c896','fed_policy':'#8bc34a'
        };
        const c = colors[p] || '#636b7e';
        return `<span style="padding:2px 8px;border-radius:10px;font-size:9px;font-weight:700;background:${c}22;color:${c};border:1px solid ${c}44;">${p.replace(/_/g,' ')}</span>`;
    }).join(' ');

    function buildSignalRow(s, type) {
        const color = type === 'buy' ? '#00c896' : '#ff4757';
        const icon = type === 'buy' ? '&#9650;' : '&#9660;';
        const strength = Math.round((s.strength || 0.5) * 100);
        const strengthColor = strength >= 70 ? (type === 'buy' ? '#00c896' : '#ff4757') : strength >= 40 ? '#ffc837' : '#636b7e';
        const tickers = (s.tickers || []).slice(0, 5).map(t =>
            `<span style="padding:1px 6px;border-radius:4px;font-size:10px;font-weight:700;background:${color}12;color:${color};border:1px solid ${color}22;cursor:pointer;" onclick="window.analyzeStock && analyzeStock('${t}')">${t}</span>`
        ).join(' ');

        return `<div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-bottom:1px solid var(--border-color);">
            <div style="min-width:18px;text-align:center;color:${color};font-size:14px;padding-top:2px;">${icon}</div>
            <div style="flex:1;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">
                    <span style="font-size:12px;font-weight:700;color:var(--text-bright);">${s.sector}</span>
                    <span style="font-size:10px;font-weight:700;color:${strengthColor};">${strength}% signal</span>
                </div>
                <div style="font-size:10px;color:var(--text-secondary);margin-bottom:4px;">${s.reason}</div>
                <div style="display:flex;gap:4px;flex-wrap:wrap;">${tickers}</div>
            </div>
        </div>`;
    }

    let html = '';

    if (summary) {
        html += `<div style="font-size:12px;color:var(--text-bright);margin-bottom:10px;padding:8px 12px;background:var(--bg-secondary);border-radius:8px;border-left:3px solid ${mood.color || '#ffc837'};">${summary}</div>`;
    }

    if (activePolicies) {
        html += `<div style="margin-bottom:10px;display:flex;gap:4px;flex-wrap:wrap;align-items:center;">
            <span style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;font-weight:700;">Active:</span> ${activePolicies}
        </div>`;
    }

    html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;">';

    // BUY column
    if (ts.buy?.length) {
        html += `<div>
            <div style="font-size:11px;font-weight:800;text-transform:uppercase;color:#00c896;margin-bottom:6px;padding-bottom:4px;border-bottom:2px solid #00c89644;">BUY — Sectors to Watch</div>
            ${ts.buy.map(s => buildSignalRow(s, 'buy')).join('')}
        </div>`;
    }

    // AVOID column
    if (ts.avoid?.length) {
        html += `<div>
            <div style="font-size:11px;font-weight:800;text-transform:uppercase;color:#ff4757;margin-bottom:6px;padding-bottom:4px;border-bottom:2px solid #ff475744;">AVOID — Threatened Sectors</div>
            ${ts.avoid.map(s => buildSignalRow(s, 'avoid')).join('')}
        </div>`;
    }

    html += '</div>';
    el.innerHTML = html;
}

async function loadTrumpPrediction(force) {
    const el = document.getElementById('trump-ai-prediction');
    if (!el) return;

    if (force) {
        el.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);">AI Prediction</div>
                <button disabled style="background:none;border:1px solid var(--border-color);border-radius:6px;padding:4px 10px;color:var(--text-secondary);font-size:10px;">Generating...</button>
            </div>
            <div style="text-align:center;padding:20px 0;">
                <div class="spinner" style="margin:0 auto 8px;"></div>
                <div style="font-size:10px;color:var(--text-secondary);">Analyzing rhetoric patterns with AI...</div>
            </div>`;
    }

    try {
        const resp = await fetch('/api/trump/predict' + (force ? '?force=1' : ''));
        if (!resp.ok) return;
        const p = await resp.json();
        if (p.error) {
            if (!force) return; // Don't show errors on auto-load
            el.innerHTML = `
                <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:12px;">AI Prediction</div>
                <div style="color:#ff4757;font-size:12px;">${p.error}</div>
                <button onclick="loadTrumpPrediction(true)" style="margin-top:8px;background:none;border:1px solid var(--border-color);border-radius:6px;padding:4px 10px;color:var(--accent-blue);font-size:10px;cursor:pointer;">Retry</button>`;
            return;
        }
        renderTrumpPrediction(p);
    } catch (e) {
        console.error('Trump prediction error:', e);
    }
}

function renderTrumpPrediction(p) {
    const el = document.getElementById('trump-ai-prediction');
    if (!el) return;

    const mi = p.market_impact || {};
    const ti = p.trade_implications || {};
    const dirColor = mi.direction === 'bullish' ? '#00c896' : mi.direction === 'bearish' ? '#ff4757' : '#ffc837';
    const sevColor = mi.severity === 'extreme' ? '#ff4757' : mi.severity === 'high' ? '#ff8c42' : mi.severity === 'medium' ? '#ffc837' : '#636b7e';
    const confPct = Math.round((p.confidence || 0) * 100);

    el.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);">AI Prediction</div>
            <div style="display:flex;gap:6px;align-items:center;">
                <span style="font-size:9px;color:var(--text-secondary);">${p.stale ? 'stale (>12h)' : p.cached ? 'cached (12h)' : 'fresh'} | ${p.model || ''}</span>
                <button onclick="loadTrumpPrediction(true)" style="background:none;border:1px solid var(--border-color);border-radius:6px;padding:4px 10px;color:var(--accent-blue);font-size:10px;cursor:pointer;">Regenerate</button>
            </div>
        </div>
        <!-- Main prediction -->
        <div style="font-size:13px;color:var(--text-bright);line-height:1.5;margin-bottom:14px;font-weight:500;">${p.prediction || ''}</div>
        <!-- Market Impact -->
        <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
            <span style="padding:3px 10px;border-radius:12px;font-size:10px;font-weight:700;background:${dirColor}22;color:${dirColor};border:1px solid ${dirColor}44;">${(mi.direction || 'N/A').toUpperCase()}</span>
            <span style="padding:3px 10px;border-radius:12px;font-size:10px;font-weight:700;background:${sevColor}22;color:${sevColor};border:1px solid ${sevColor}44;">SEVERITY: ${(mi.severity || 'N/A').toUpperCase()}</span>
            <span style="padding:3px 10px;border-radius:12px;font-size:10px;font-weight:600;background:var(--bg-secondary);color:var(--text-secondary);border:1px solid var(--border-color);">Confidence: ${confPct}%</span>
            <span style="padding:3px 10px;border-radius:12px;font-size:10px;font-weight:600;background:var(--bg-secondary);color:var(--text-secondary);border:1px solid var(--border-color);">${p.timeframe || ''}</span>
        </div>
        ${mi.explanation ? `<div style="font-size:11px;color:var(--text-secondary);margin-bottom:10px;">${mi.explanation}</div>` : ''}
        ${mi.sectors_affected && mi.sectors_affected.length ? `
        <div style="font-size:10px;color:var(--text-secondary);margin-bottom:12px;">
            Sectors: ${mi.sectors_affected.map(s => `<span style="padding:2px 6px;background:var(--bg-secondary);border-radius:4px;margin:0 2px;">${s}</span>`).join('')}
        </div>` : ''}
        <!-- Trade implications -->
        <div style="border-top:1px solid var(--border-color);padding-top:10px;margin-top:4px;">
            ${ti.stocks ? `<div style="font-size:11px;margin-bottom:6px;"><span style="color:var(--accent-blue);font-weight:700;">Stocks:</span> <span style="color:var(--text-secondary);">${ti.stocks}</span></div>` : ''}
            ${ti.crypto ? `<div style="font-size:11px;margin-bottom:6px;"><span style="color:#f7931a;font-weight:700;">Crypto:</span> <span style="color:var(--text-secondary);">${ti.crypto}</span></div>` : ''}
            ${ti.hedging ? `<div style="font-size:11px;margin-bottom:6px;"><span style="color:#ff8c42;font-weight:700;">Hedge:</span> <span style="color:var(--text-secondary);">${ti.hedging}</span></div>` : ''}
        </div>
        ${p.rhetoric_trajectory ? `<div style="font-size:11px;color:var(--text-secondary);margin-top:8px;padding-top:8px;border-top:1px solid var(--border-color);"><strong style="color:var(--text-bright);">Trajectory:</strong> ${p.rhetoric_trajectory}</div>` : ''}
        ${p.wildcard_risk ? `<div style="font-size:11px;color:#ff8c42;margin-top:6px;"><strong>Wildcard:</strong> ${p.wildcard_risk}</div>` : ''}
        <div style="font-size:9px;color:var(--text-secondary);margin-top:10px;text-align:right;">${p.generated_at ? new Date(p.generated_at).toLocaleString() : ''}</div>`;
}

function renderTrumpSignals(mood) {
    const el = document.getElementById('trump-signals');
    if (!el) return;
    const signals = mood.top_signals || [];
    if (!signals.length) {
        el.innerHTML = '<div style="color:var(--text-secondary);font-size:11px;">No strong signals detected</div>';
        return;
    }
    el.innerHTML = signals.map(s => {
        const c = s.type === 'bullish' ? '#00c896' : '#ff4757';
        return `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border-color);">
            <span style="font-weight:600;color:var(--text-bright);">"${s.text}"</span>
            <span style="padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;background:${c}22;color:${c};border:1px solid ${c}44;">${s.score > 0 ? '+' : ''}${s.score}</span>
        </div>`;
    }).join('');
}

function renderTrumpNotable(mood) {
    const el = document.getElementById('trump-notable');
    if (!el) return;
    const posts = mood.notable_posts || [];
    if (!posts.length) {
        el.innerHTML = '<div style="color:var(--text-secondary);font-size:11px;">No notable posts</div>';
        return;
    }
    el.innerHTML = posts.map(p => {
        const srcBadge = p.source === 'truth_social' ? 'TS' : p.source === 'whitehouse' ? 'WH' : 'News';
        const srcColor = p.source === 'truth_social' ? '#7c5dfa' : p.source === 'whitehouse' ? '#4a90d9' : '#636b7e';
        const scoreColor = p.score > 0 ? '#00c896' : p.score < 0 ? '#ff4757' : '#ffc837';
        return `<div style="padding:8px 0;border-bottom:1px solid var(--border-color);">
            <div style="display:flex;gap:6px;align-items:center;margin-bottom:4px;">
                <span style="padding:1px 6px;border-radius:4px;font-size:9px;font-weight:700;background:${srcColor}22;color:${srcColor};border:1px solid ${srcColor}44;">${srcBadge}</span>
                <span style="font-size:10px;font-weight:700;color:${scoreColor};">${p.score > 0 ? '+' : ''}${p.score}</span>
            </div>
            <div style="font-size:11px;color:var(--text-secondary);line-height:1.4;">${p.text}</div>
        </div>`;
    }).join('');
}

function renderTrumpChart(history, backtracks) {
    const el = document.getElementById('trump-chart');
    if (!el || !history.length) {
        if (el) el.innerHTML = '<div style="text-align:center;color:var(--text-secondary);padding:40px;font-size:12px;">No history data yet. Mood snapshots are recorded every hour.</div>';
        return;
    }

    // Sort oldest first, then downsample to max ~120 points for chart readability
    // Keep 1 representative point per time bucket (more granular for recent data)
    const allSorted = [...history].reverse();
    let sorted;
    if (allSorted.length > 120) {
        const buckets = {};
        allSorted.forEach(h => {
            // Bucket by date + 6-hour window (4 points per day max)
            const d = h.created_at ? new Date(h.created_at) : new Date();
            const key = d.toISOString().slice(0, 10) + '-' + Math.floor(d.getHours() / 6);
            if (!buckets[key]) buckets[key] = h;
        });
        sorted = Object.values(buckets).sort((a, b) => {
            const da = a.created_at ? new Date(a.created_at) : new Date();
            const db = b.created_at ? new Date(b.created_at) : new Date();
            return da - db;
        });
    } else {
        sorted = allSorted;
    }
    const maxAbs = Math.max(...sorted.map(h => Math.abs(h.mood)), 1);
    const chartW = el.clientWidth || 600;
    const chartH = 240;
    const barW = Math.max(4, Math.min(20, (chartW - 40) / sorted.length - 2));
    const midY = (chartH - 30) / 2;  // leave room at bottom for labels

    // Build backtrack date lookup for TACO milestones
    const btMap = {};
    if (backtracks && backtracks.length) {
        backtracks.filter(bt => bt.policy_area !== '__prediction__').forEach(bt => {
            if (bt.backtrack_date) {
                const d = new Date(bt.backtrack_date).toISOString().slice(0, 10);
                btMap[d] = bt;
            }
        });
    }

    let barsHtml = '';
    let milestonesHtml = '';
    const barData = []; // Store bar positions for tooltip

    sorted.forEach((h, i) => {
        const x = 30 + i * (barW + 2);
        const barH = (Math.abs(h.mood) / Math.max(maxAbs, 1)) * (midY - 10);
        const y = h.mood >= 0 ? midY - barH : midY;
        const color = h.mood >= 30 ? '#00c896' : h.mood >= 10 ? '#8bc34a' : h.mood >= -10 ? '#ffc837' : h.mood >= -30 ? '#ff8c42' : '#ff4757';
        const dateStr = h.created_at ? new Date(h.created_at).toISOString().slice(0, 10) : '';

        barData.push({ x, y, w: barW, h: Math.max(barH, 1), mood: h.mood, label: h.label, created_at: h.created_at, signals: h.top_signals, trend: h.pattern_trend });

        barsHtml += `<rect class="mood-bar" data-idx="${i}" x="${x}" y="${y}" width="${barW}" height="${Math.max(barH, 1)}" rx="2" fill="${color}" opacity="0.85" style="cursor:crosshair;"/>`;

        // TACO milestone: check if this bar date matches a backtrack
        const bt = btMap[dateStr];
        if (bt) {
            const labelY = Math.min(y, midY) - 6;
            milestonesHtml += `<g class="taco-milestone" data-idx="${i}" style="cursor:pointer;">
                <line x1="${x + barW/2}" y1="${labelY}" x2="${x + barW/2}" y2="${Math.max(y, midY) + Math.max(barH,1)}" stroke="#ffc837" stroke-width="1.5" stroke-dasharray="3,2" opacity="0.7"/>
                <text x="${x + barW/2}" y="${labelY - 2}" text-anchor="middle" fill="#ffc837" font-size="12" style="cursor:pointer;">&#127790;</text>
                <text x="${x + barW/2}" y="${chartH - 4}" text-anchor="middle" fill="#ffc837" font-size="7" font-weight="700">${bt.policy_area.replace(/_/g,' ').toUpperCase().slice(0,12)}</text>
            </g>`;
            // Remove from map so we only show one marker per date
            delete btMap[dateStr];
        }
    });

    el.innerHTML = `
        <div style="position:relative;">
            <svg width="100%" height="${chartH}" viewBox="0 0 ${chartW} ${chartH}" preserveAspectRatio="none" id="trump-chart-svg">
                <!-- Zero line -->
                <line x1="25" y1="${midY}" x2="${chartW}" y2="${midY}" stroke="var(--border-color)" stroke-width="1" stroke-dasharray="4,4"/>
                <text x="2" y="${midY + 3}" fill="var(--text-secondary)" font-size="9">0</text>
                <text x="2" y="12" fill="#00c896" font-size="9">+${Math.round(maxAbs)}</text>
                <text x="2" y="${chartH - 34}" fill="#ff4757" font-size="9">-${Math.round(maxAbs)}</text>
                ${barsHtml}
                ${milestonesHtml}
            </svg>
            <div id="mood-tooltip" style="display:none;position:absolute;pointer-events:none;z-index:100;
                background:var(--bg-primary);border:1px solid var(--border-color);border-radius:8px;
                padding:10px 14px;box-shadow:0 4px 16px rgba(0,0,0,0.3);min-width:180px;font-size:11px;"></div>
        </div>`;

    // Attach hover events
    const svg = document.getElementById('trump-chart-svg');
    const tooltip = document.getElementById('mood-tooltip');
    if (!svg || !tooltip) return;

    svg.querySelectorAll('.mood-bar').forEach(bar => {
        bar.addEventListener('mouseenter', (e) => {
            const idx = parseInt(bar.getAttribute('data-idx'));
            const d = barData[idx];
            if (!d) return;
            bar.setAttribute('opacity', '1');
            bar.setAttribute('stroke', '#fff');
            bar.setAttribute('stroke-width', '1.5');
            const date = d.created_at ? new Date(d.created_at).toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric'}) : '';
            const time = d.created_at ? new Date(d.created_at).toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit'}) : '';
            const moodColor = d.mood >= 30 ? '#00c896' : d.mood >= 10 ? '#8bc34a' : d.mood >= -10 ? '#ffc837' : d.mood >= -30 ? '#ff8c42' : '#ff4757';
            const signals = (d.signals || []).slice(0, 3).map(s => {
                const txt = typeof s === 'string' ? s : s.text || '';
                const sc = typeof s === 'object' ? s.score : 0;
                const scColor = sc > 0 ? '#00c896' : sc < 0 ? '#ff4757' : 'var(--text-secondary)';
                return `<div style="display:flex;justify-content:space-between;gap:8px;"><span>${txt}</span><span style="color:${scColor};font-weight:600;">${sc > 0 ? '+' : ''}${sc || ''}</span></div>`;
            }).join('');
            tooltip.innerHTML = `
                <div style="font-weight:700;color:var(--text-bright);margin-bottom:4px;">${date} ${time}</div>
                <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:6px;">
                    <span style="font-size:22px;font-weight:800;color:${moodColor};">${d.mood > 0 ? '+' : ''}${d.mood}</span>
                    <span style="font-size:10px;font-weight:600;color:${moodColor};text-transform:uppercase;">${d.label}</span>
                </div>
                ${d.trend ? `<div style="color:var(--text-secondary);margin-bottom:4px;">Trend: <span style="font-weight:600;color:var(--text-bright);">${d.trend}</span></div>` : ''}
                ${signals ? `<div style="border-top:1px solid var(--border-color);margin-top:4px;padding-top:4px;font-size:10px;color:var(--text-secondary);">${signals}</div>` : ''}`;
            // Position tooltip near bar
            const rect = el.getBoundingClientRect();
            const barRect = bar.getBoundingClientRect();
            let left = barRect.left - rect.left + barRect.width + 8;
            if (left + 200 > rect.width) left = barRect.left - rect.left - 200;
            tooltip.style.left = Math.max(0, left) + 'px';
            tooltip.style.top = Math.max(0, d.y - 20) + 'px';
            tooltip.style.display = 'block';
        });
        bar.addEventListener('mouseleave', () => {
            bar.setAttribute('opacity', '0.85');
            bar.removeAttribute('stroke');
            bar.removeAttribute('stroke-width');
            tooltip.style.display = 'none';
        });
    });

    // TACO milestone hover
    svg.querySelectorAll('.taco-milestone').forEach(g => {
        g.addEventListener('mouseenter', (e) => {
            const idx = parseInt(g.getAttribute('data-idx'));
            const d = barData[idx];
            const dateStr = d && d.created_at ? new Date(d.created_at).toISOString().slice(0, 10) : '';
            // Find matching backtrack from original backtracks array
            const bt = (backtracks || []).find(b => b.backtrack_date && new Date(b.backtrack_date).toISOString().slice(0, 10) === dateStr);
            if (!bt) return;
            const moodSwing = bt.mood_swing || 0;
            const swingColor = moodSwing > 0 ? '#00c896' : '#ff4757';
            tooltip.innerHTML = `
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">
                    <span style="font-size:16px;">&#127790;</span>
                    <span style="font-weight:700;color:#ffc837;text-transform:uppercase;">TACO — Policy Reversal</span>
                </div>
                <div style="font-weight:600;color:var(--text-bright);margin-bottom:4px;">${bt.policy_area.replace(/_/g, ' ')}</div>
                <div style="font-size:10px;margin-bottom:2px;"><span style="color:#ff4757;">${bt.initial_stance || '?'}</span> <span style="color:var(--text-secondary);">→</span> <span style="color:#00c896;">${bt.backtrack_stance || '?'}</span></div>
                <div style="display:flex;gap:10px;margin-top:6px;font-size:10px;">
                    <span style="color:${swingColor};font-weight:700;">Swing: ${moodSwing > 0 ? '+' : ''}${moodSwing}</span>
                    <span style="color:var(--text-secondary);">${bt.days_to_reversal || '?'} days</span>
                    <span style="color:var(--text-secondary);">Conf: ${bt.confidence ? (bt.confidence * 100).toFixed(0) + '%' : '?'}</span>
                </div>`;
            const rect = el.getBoundingClientRect();
            const gRect = g.getBoundingClientRect();
            let left = gRect.left - rect.left + gRect.width/2 + 8;
            if (left + 220 > rect.width) left = gRect.left - rect.left - 220;
            tooltip.style.left = Math.max(0, left) + 'px';
            tooltip.style.top = '10px';
            tooltip.style.display = 'block';
        });
        g.addEventListener('mouseleave', () => {
            tooltip.style.display = 'none';
        });
    });
}

function renderTrumpHistoryTable(history) {
    const el = document.getElementById('trump-history-table');
    if (!el) return;
    if (!history.length) {
        el.innerHTML = '<div style="color:var(--text-secondary);">No history records yet</div>';
        return;
    }
    let rows = history.slice(0, 50).map(h => {
        const c = h.mood >= 30 ? '#00c896' : h.mood >= 10 ? '#8bc34a' : h.mood >= -10 ? '#ffc837' : h.mood >= -30 ? '#ff8c42' : '#ff4757';
        const date = h.created_at ? new Date(h.created_at).toLocaleString() : '—';
        const signals = (h.top_signals || []).map(s => s.text || s).slice(0, 3).join(', ');
        return `<tr style="border-bottom:1px solid var(--border-color);">
            <td style="padding:6px 8px;white-space:nowrap;">${date}</td>
            <td style="padding:6px 8px;font-weight:700;color:${c};">${h.mood > 0 ? '+' : ''}${h.mood}</td>
            <td style="padding:6px 8px;color:${c};font-weight:600;">${h.label}</td>
            <td style="padding:6px 8px;">${h.pattern_trend || '—'}</td>
            <td style="padding:6px 8px;">${h.posts_analyzed || 0}</td>
            <td style="padding:6px 8px;color:var(--text-secondary);font-size:10px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${signals || '—'}</td>
        </tr>`;
    }).join('');

    el.innerHTML = `<table style="width:100%;border-collapse:collapse;">
        <thead><tr style="border-bottom:2px solid var(--border-color);font-size:10px;text-transform:uppercase;color:var(--text-secondary);">
            <th style="padding:6px 8px;text-align:left;">Time</th>
            <th style="padding:6px 8px;text-align:left;">Mood</th>
            <th style="padding:6px 8px;text-align:left;">Label</th>
            <th style="padding:6px 8px;text-align:left;">Trend</th>
            <th style="padding:6px 8px;text-align:left;">Posts</th>
            <th style="padding:6px 8px;text-align:left;">Top Signals</th>
        </tr></thead>
        <tbody>${rows}</tbody>
    </table>`;
}


// ─── Backtrack Tracker Functions ─────────────────────────────

async function loadBacktrackPrediction(force) {
    const el = document.getElementById('trump-backtrack-prediction');
    if (!el) return;

    if (force) {
        el.querySelector('div:last-child').innerHTML = '<div style="text-align:center;padding:20px 0;"><div style="font-size:10px;color:var(--text-secondary);">Generating prediction...</div></div>';
    }

    try {
        const resp = await fetch('/api/trump/backtracks/predict' + (force ? '?force=1' : ''));
        if (!resp.ok) return;
        const p = await resp.json();
        renderBacktrackPrediction(p);
    } catch (e) {
        console.warn('Backtrack prediction failed:', e);
    }
}

function renderBacktrackPrediction(p) {
    const el = document.getElementById('trump-backtrack-prediction');
    if (!el || p.error) {
        if (el && p.error) {
            el.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                    <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);">Backtrack Predictor</div>
                    <button onclick="loadBacktrackPrediction(true)" class="btn-analyze" style="padding:4px 12px;font-size:10px;">Predict</button>
                </div>
                <div style="text-align:center;padding:20px 0;">
                    <div style="font-size:10px;color:var(--text-secondary);">${p.error}</div>
                </div>`;
        }
        return;
    }

    const policies = (p.active_policies || []).slice(0, 5);
    const policyRows = policies.map(pol => {
        const prob = (pol.backtrack_probability * 100).toFixed(0);
        const barColor = prob >= 70 ? '#00c896' : prob >= 40 ? '#ffc837' : '#ff4757';
        const impDir = pol.market_impact_if_reversed?.direction || 'neutral';
        const impColor = impDir === 'bullish' ? '#00c896' : impDir === 'bearish' ? '#ff4757' : '#ffc837';
        return `
            <div style="margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid var(--border-color);">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                    <span style="font-size:12px;font-weight:700;color:var(--text-bright);">${pol.policy || pol.policy_area || '?'}</span>
                    <span style="font-size:11px;font-weight:700;color:${barColor};">${prob}%</span>
                </div>
                <div style="background:#0d1117;border-radius:4px;height:6px;margin-bottom:6px;">
                    <div style="background:${barColor};height:100%;border-radius:4px;width:${prob}%;transition:width 0.5s;"></div>
                </div>
                <div style="font-size:10px;color:var(--text-secondary);margin-bottom:4px;">${pol.current_stance || ''}</div>
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                    <span style="padding:1px 6px;border-radius:8px;font-size:9px;font-weight:700;background:${impColor}22;color:${impColor};border:1px solid ${impColor}44;">${impDir}</span>
                    <span style="padding:1px 6px;border-radius:8px;font-size:9px;background:var(--bg-secondary);color:var(--text-secondary);">${pol.estimated_timeframe || pol.estimated_days + 'd'}</span>
                </div>
                ${pol.reasoning ? `<div style="font-size:10px;color:var(--text-secondary);margin-top:4px;font-style:italic;">${pol.reasoning}</div>` : ''}
            </div>`;
    }).join('');

    const conf = p.confidence ? (p.confidence * 100).toFixed(0) : '?';
    const nextRev = p.next_likely_reversal || '?';
    const staleTag = p.stale ? ' <span style="color:#ff8c42;font-size:9px;">(stale)</span>' : '';
    const cachedTag = p.cached ? ' <span style="color:var(--text-secondary);font-size:9px;">(cached)</span>' : '';

    el.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);">Backtrack Predictor${staleTag}${cachedTag}</div>
            <button onclick="loadBacktrackPrediction(true)" class="btn-analyze" style="padding:4px 12px;font-size:10px;">Predict</button>
        </div>
        ${p.overall_insight ? `<div style="font-size:11px;color:var(--text-bright);margin-bottom:12px;line-height:1.4;">${p.overall_insight}</div>` : ''}
        <div style="display:flex;gap:12px;margin-bottom:12px;">
            <div style="text-align:center;">
                <div style="font-size:18px;font-weight:800;color:var(--accent-blue);">${conf}%</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Confidence</div>
            </div>
            <div style="text-align:center;">
                <div style="font-size:12px;font-weight:700;color:var(--text-bright);padding:2px 8px;background:var(--bg-secondary);border-radius:6px;">${nextRev.replace('_', ' ')}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;margin-top:2px;">Next Reversal</div>
            </div>
        </div>
        ${policyRows || '<div style="font-size:10px;color:var(--text-secondary);">No active policies detected</div>'}`;
}

function renderBacktrackStats(data) {
    const el = document.getElementById('trump-backtrack-stats');
    if (!el) return;

    const stats = data.stats || {};
    const total = stats.total || 0;
    const avgDays = stats.avg_days || 0;
    const medDays = stats.median_days || 0;
    const avgSwing = stats.avg_mood_swing || 0;
    const topAreas = (stats.top_areas || []).slice(0, 5);

    const areaBadges = topAreas.map(a => {
        const colors = {
            'china_tariffs': '#ff4757', 'general_tariffs': '#ff8c42', 'eu_tariffs': '#ffc837',
            'trade_deals': '#00c896', 'iran_conflict': '#ff4757', 'crypto_policy': '#4f8aff',
            'mexico_tariffs': '#ff8c42', 'canada_tariffs': '#ffc837', 'tax_policy': '#00c896', 'fed_policy': '#8bc34a',
        };
        const c = colors[a.area] || '#636b7e';
        return `<span style="padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;background:${c}22;color:${c};border:1px solid ${c}44;">${a.area.replace('_', ' ')} (${a.count})</span>`;
    }).join(' ');

    el.innerHTML = `
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-secondary);margin-bottom:12px;">Reversal Stats</div>
        ${total === 0 ? '<div style="text-align:center;padding:15px 0;font-size:10px;color:var(--text-secondary);">No backtracks detected yet. Need more mood history data.</div>' : `
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px;">
            <div style="text-align:center;padding:8px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:22px;font-weight:800;color:var(--accent-blue);">${total}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Reversals</div>
            </div>
            <div style="text-align:center;padding:8px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:22px;font-weight:800;color:var(--accent-orange);">${avgDays}d</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Avg Days</div>
            </div>
            <div style="text-align:center;padding:8px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:22px;font-weight:800;color:var(--text-bright);">${medDays}d</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Median Days</div>
            </div>
            <div style="text-align:center;padding:8px;background:var(--bg-secondary);border-radius:8px;">
                <div style="font-size:22px;font-weight:800;color:${avgSwing >= 0 ? '#00c896' : '#ff4757'};">${avgSwing > 0 ? '+' : ''}${avgSwing}</div>
                <div style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;">Avg Swing</div>
            </div>
        </div>
        <div style="font-size:10px;font-weight:700;color:var(--text-secondary);text-transform:uppercase;margin-bottom:6px;">Most Reversed</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px;">${areaBadges || '<span style="font-size:10px;color:var(--text-secondary);">N/A</span>'}</div>
        `}`;
}

function renderBacktrackTimeline(backtracks) {
    const el = document.getElementById('trump-backtrack-timeline');
    if (!el) return;

    // Filter out the __prediction__ meta-row
    const items = (backtracks || []).filter(bt => bt.policy_area !== '__prediction__');

    if (!items.length) {
        el.innerHTML = '<div style="text-align:center;padding:15px 0;font-size:10px;color:var(--text-secondary);">No policy reversals detected yet. As mood data accumulates, backtracks will appear here.</div>';
        return;
    }

    const colors = {
        'china_tariffs': '#ff4757', 'general_tariffs': '#ff8c42', 'eu_tariffs': '#ffc837',
        'trade_deals': '#00c896', 'iran_conflict': '#ff4757', 'crypto_policy': '#4f8aff',
        'mexico_tariffs': '#ff8c42', 'canada_tariffs': '#ffc837', 'tax_policy': '#00c896', 'fed_policy': '#8bc34a',
    };

    const rows = items.slice(0, 20).map(bt => {
        const c = colors[bt.policy_area] || '#636b7e';
        const swing = bt.mood_swing || 0;
        const swingColor = swing > 0 ? '#00c896' : '#ff4757';
        const impact = typeof bt.market_impact === 'object' ? bt.market_impact : {};
        const impDir = impact.direction || (swing > 0 ? 'bullish' : 'bearish');
        const impSev = impact.severity || 'medium';
        const confPct = bt.confidence ? (bt.confidence * 100).toFixed(0) + '%' : '?';

        const initDate = bt.initial_date ? new Date(bt.initial_date).toLocaleDateString('en-US', {month:'short', day:'numeric'}) : '?';
        const btDate = bt.backtrack_date ? new Date(bt.backtrack_date).toLocaleDateString('en-US', {month:'short', day:'numeric'}) : '?';

        return `
            <div style="display:flex;gap:12px;padding:10px 0;border-bottom:1px solid var(--border-color);">
                <div style="min-width:3px;background:${c};border-radius:2px;"></div>
                <div style="flex:1;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                        <span style="padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;background:${c}22;color:${c};border:1px solid ${c}44;">${bt.policy_area.replace(/_/g, ' ')}</span>
                        <span style="font-size:10px;color:var(--text-secondary);">${bt.days_to_reversal || '?'}d | conf: ${confPct}</span>
                    </div>
                    <div style="font-size:11px;margin-bottom:4px;">
                        <span style="color:#ff4757;">${initDate}: ${bt.initial_stance || '?'}</span>
                        <span style="color:var(--text-secondary);margin:0 6px;">&#8594;</span>
                        <span style="color:#00c896;">${btDate}: ${bt.backtrack_stance || '?'}</span>
                    </div>
                    <div style="display:flex;gap:6px;">
                        <span style="font-size:9px;padding:1px 6px;border-radius:8px;background:${swingColor}22;color:${swingColor};">swing: ${swing > 0 ? '+' : ''}${swing}</span>
                        <span style="font-size:9px;padding:1px 6px;border-radius:8px;background:var(--bg-secondary);color:var(--text-secondary);">${impDir} ${impSev}</span>
                    </div>
                </div>
            </div>`;
    }).join('');

    el.innerHTML = rows;
}


function applyUserRole() {
    if (!currentUser) return;

    // Show user profile in header
    const profile = document.getElementById('user-profile');
    if (profile) {
        profile.style.display = 'flex';
        const avatar = document.getElementById('user-avatar');
        const name = document.getElementById('user-name');
        if (avatar && currentUser.picture_url) {
            avatar.src = currentUser.picture_url;
            avatar.alt = currentUser.name || '';
        } else if (avatar) {
            avatar.style.display = 'none';
        }
        if (name) name.textContent = currentUser.name || currentUser.email || '';
    }

    // Show/hide tabs based on roles and tier
    const isTrader = currentUser.is_trader;
    const isAdmin = currentUser.roles && currentUser.roles.includes('admin');

    const isPro = currentUser.tier === 'pro' || currentUser.tier === 'admin' || isAdmin;

    document.querySelectorAll('[data-role="trader"]').forEach(el => {
        el.style.display = isTrader ? '' : 'none';
    });
    document.querySelectorAll('[data-role="admin"]').forEach(el => {
        el.style.display = isAdmin ? '' : 'none';
    });
    document.querySelectorAll('[data-role="pro"]').forEach(el => {
        el.style.display = isPro ? '' : 'none';
    });
}

// Global 429 handler — show upgrade modal
function handle429(data) {
    const msg = `You've used ${data.used}/${data.limit} LLM calls in the last 24 hours (${data.tier} tier).\n\nUpgrade your plan for more calls.`;
    if (confirm(msg + '\n\nWould you like to see upgrade options?')) {
        showPricingModal();
    }
}

function showPricingModal() {
    const existing = document.getElementById('pricing-modal');
    if (existing) existing.remove();

    const tier = currentUser?.tier || 'free';
    const check = '<span style="color:#26a69a;margin-right:4px;">&#10003;</span>';
    const lock = '<span style="color:#363a45;margin-right:4px;">&#10007;</span>';

    const modal = document.createElement('div');
    modal.id = 'pricing-modal';
    modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);z-index:10000;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = `
        <div style="background:#1e222d;border-radius:16px;padding:36px;max-width:820px;width:95%;color:#d1d4dc;position:relative;">
            <button onclick="this.parentElement.parentElement.remove()" style="position:absolute;top:12px;right:16px;background:none;border:none;color:#787b86;font-size:22px;cursor:pointer;">&times;</button>
            <h2 style="color:#e0e3eb;margin:0 0 4px;font-size:22px;">Choose Your Plan</h2>
            <p style="color:#787b86;margin:0 0 28px;font-size:13px;">Unlock AI-powered market intelligence. Cancel anytime.</p>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;">
                <!-- Free -->
                <div style="background:#131722;border-radius:10px;padding:24px;border:1px solid ${tier==='free'?'#787b86':'#2a2e39'};">
                    ${tier==='free'?'<div style="font-size:9px;text-transform:uppercase;color:#787b86;font-weight:700;margin-bottom:8px;">Current Plan</div>':''}
                    <h3 style="color:#787b86;margin:0 0 4px;font-size:16px;">Free</h3>
                    <p style="color:#e0e3eb;font-size:28px;font-weight:800;margin:0 0 4px;">$0<span style="font-size:13px;font-weight:400;color:#787b86;">/mo</span></p>
                    <p style="color:#787b86;font-size:12px;margin:0 0 16px;">5 AI calls / day</p>
                    <ul style="list-style:none;padding:0;margin:0;font-size:12px;line-height:2;color:#9598a1;">
                        <li>${check} Stock Analyzer</li>
                        <li>${check} Breakout Scanner</li>
                        <li>${check} Trade Tracker</li>
                        <li>${check} Basic Screener</li>
                        <li>${lock} <span style="color:#363a45;">Markets & Predictions</span></li>
                        <li>${lock} <span style="color:#363a45;">Congress Trades</span></li>
                        <li>${lock} <span style="color:#363a45;">Trump Indicator & AI</span></li>
                        <li>${lock} <span style="color:#363a45;">Research & Fin Skills</span></li>
                    </ul>
                </div>
                <!-- Starter -->
                <div style="background:#131722;border-radius:10px;padding:24px;border:2px solid ${tier==='starter'?'#2962ff':'#2962ff44'};">
                    ${tier==='starter'?'<div style="font-size:9px;text-transform:uppercase;color:#2962ff;font-weight:700;margin-bottom:8px;">Current Plan</div>':''}
                    <h3 style="color:#2962ff;margin:0 0 4px;font-size:16px;">Starter</h3>
                    <p style="color:#e0e3eb;font-size:28px;font-weight:800;margin:0 0 4px;">$19<span style="font-size:13px;font-weight:400;color:#787b86;">/mo</span></p>
                    <p style="color:#787b86;font-size:12px;margin:0 0 16px;">30 AI calls / day</p>
                    <ul style="list-style:none;padding:0;margin:0;font-size:12px;line-height:2;color:#9598a1;">
                        <li>${check} Everything in Free</li>
                        <li>${check} Advanced Screener</li>
                        <li>${check} Research & Fin Skills</li>
                        <li>${check} IPO Tracker</li>
                        <li>${check} 12-Month Predictions</li>
                        <li>${lock} <span style="color:#363a45;">Markets & Predictions</span></li>
                        <li>${lock} <span style="color:#363a45;">Congress Trades</span></li>
                        <li>${lock} <span style="color:#363a45;">Trump Indicator & AI</span></li>
                    </ul>
                    ${tier==='free'?'<button onclick="startCheckout(\'starter\')" style="margin-top:16px;width:100%;padding:10px;background:#2962ff;color:white;border:none;border-radius:8px;cursor:pointer;font-weight:700;font-size:13px;">Get Starter</button>':''}
                </div>
                <!-- Pro -->
                <div style="background:linear-gradient(135deg,#131722,#1a1f2e);border-radius:10px;padding:24px;border:2px solid ${tier==='pro'||tier==='admin'?'#26a69a':'#26a69a'};position:relative;overflow:hidden;">
                    <div style="position:absolute;top:12px;right:-28px;background:#26a69a;color:#fff;font-size:9px;font-weight:800;padding:2px 32px;transform:rotate(45deg);text-transform:uppercase;">Best Value</div>
                    ${tier==='pro'||tier==='admin'?'<div style="font-size:9px;text-transform:uppercase;color:#26a69a;font-weight:700;margin-bottom:8px;">Current Plan</div>':''}
                    <h3 style="color:#26a69a;margin:0 0 4px;font-size:16px;">Pro</h3>
                    <p style="color:#e0e3eb;font-size:28px;font-weight:800;margin:0 0 4px;">$39<span style="font-size:13px;font-weight:400;color:#787b86;">/mo</span></p>
                    <p style="color:#787b86;font-size:12px;margin:0 0 16px;">100 AI calls / day</p>
                    <ul style="list-style:none;padding:0;margin:0;font-size:12px;line-height:2;color:#d1d4dc;">
                        <li>${check} Everything in Starter</li>
                        <li>${check} <strong>Trump Market Indicator</strong></li>
                        <li>${check} <strong>AI Prediction Engine</strong></li>
                        <li>${check} <strong>Congress Trades</strong></li>
                        <li>${check} <strong>Prediction Markets</strong></li>
                        <li>${check} <strong>Market Sentiment Pulse</strong></li>
                        <li>${check} Priority AI Models</li>
                        <li>${check} Unlimited History</li>
                    </ul>
                    ${tier!=='pro'&&tier!=='admin'?'<button onclick="startCheckout(\'pro\')" style="margin-top:16px;width:100%;padding:10px;background:#26a69a;color:white;border:none;border-radius:8px;cursor:pointer;font-weight:700;font-size:13px;">Get Pro</button>':''}
                </div>
            </div>
            <p style="text-align:center;color:#363a45;font-size:10px;margin:20px 0 0;">Secure payments via Stripe. Cancel anytime from your account settings.</p>
        </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
}

async function startCheckout(tier) {
    try {
        const resp = await fetch(`/billing/checkout/${tier}`, { method: 'POST' });
        const data = await resp.json();
        if (data.url) {
            window.location.href = data.url;
        } else {
            alert(data.error || 'Failed to create checkout session');
        }
    } catch (e) {
        alert('Failed to start checkout: ' + e.message);
    }
}

// Load user on page init
loadCurrentUser();

// Check for billing params in URL
(function() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('show') === 'pricing') {
        setTimeout(showPricingModal, 500);
    }
    if (params.get('billing') === 'success') {
        setTimeout(() => alert('Subscription activated! Your plan has been upgraded.'), 500);
    }
})();

// ─── Financial Glossary (5th-grader explanations) ────────────

const FINANCE_GLOSSARY = {
    "P/E": "Price-to-Earnings — How many dollars you pay for each dollar the company earns. Lower = cheaper stock.",
    "Fwd P/E": "Expected P/E based on next year's earnings. Shows if investors think profits will grow.",
    "Forward P/E": "Expected P/E based on next year's earnings. Shows if investors think profits will grow.",
    "EBITDA": "Earnings before loans, taxes, and equipment costs. Shows how much money the business actually makes from its work.",
    "Revenue": "Total money a company brings in from selling stuff — like a lemonade stand's total sales.",
    "Net Income": "The actual profit after paying ALL bills. Revenue minus every cost.",
    "FCF": "Free Cash Flow — Money left over after paying for everything. Like your allowance after buying lunch.",
    "FCF Yield": "Free cash flow as a percentage of the stock price. Higher = more cash bang for your buck.",
    "Market Cap": "Total value of the company. Share price x number of shares. Like the price tag on the whole business.",
    "Rev Growth": "How much faster the company is selling compared to last year. Higher = growing faster.",
    "Revenue Growth": "How much faster the company is selling compared to last year. Higher = growing faster.",
    "Earnings Growth": "How much more profit the company made vs last year.",
    "ROE": "Return on Equity — How good the company is at turning investor money into profits. Like getting $15 back for every $100 you invest.",
    "D/E Ratio": "Debt-to-Equity — How much the company borrowed vs what owners put in. High = risky, like having too many credit cards.",
    "Debt/Equity": "How much the company borrowed vs what owners put in. High = risky, like having too many credit cards.",
    "Profit Margin": "What percentage of sales becomes profit. 20% margin = keep 20 cents of every dollar.",
    "Expense Ratio": "Annual fee for owning an ETF. 0.5% = you pay $5 per year for every $1,000 invested.",
    "YTD Return": "Year-To-Date return — How much the investment gained since January 1st.",
    "AUM": "Assets Under Management — Total money invested in this fund. Bigger = more popular.",
    "Dilution": "Risk that the company prints more shares, making yours worth less — like splitting a pizza into more slices.",
    "Liquidity": "How easy it is to buy or sell the stock quickly. Low liquidity = hard to get out.",
    "Fair Value": "What the stock should really be worth based on the company's numbers, not just hype.",
    "Short Ratio": "How many people are betting AGAINST this stock. High = many people think it'll drop.",
    "Beta": "How jumpy the stock is compared to the market. 1.5 = moves 50% more than average.",
    "PEG Ratio": "P/E divided by growth rate. Under 1 = growth is cheap. Over 2 = you're overpaying for growth.",
    "Moat": "Competitive advantage that protects the company — like a castle's moat keeps enemies out.",
    "Confidence": "How sure the AI is about its verdict. Higher = more certain.",
    "Survival": "Chance the company stays alive for the next 12 months. Higher = safer.",
    "Upside": "How much the stock could go up from here, in percent.",
    "Risk": "Overall danger score. Lower = safer investment.",
    "Burning Cash": "Is the company spending more money than it makes? YES = warning sign.",
    "Cash Runway": "How many months the company can survive on its current cash before running out of money.",
    "Momentum": "Is the sector/theme getting stronger or weaker right now? Strong = good for this investment.",
    "Debt-to-Burn": "If the company is losing money, this shows how many months of cash burn the total debt represents. Higher = more dangerous.",
    "Interest Coverage": "How many times operating income covers interest payments. Below 1.5 = danger zone.",
    "Quick Ratio": "Can the company pay its short-term bills with liquid assets? Above 1 = safe.",
    "Gross Margin": "Percentage of revenue left after cost of goods sold. Higher = better pricing power.",
    "Operating Margin": "Percentage of revenue left after operating expenses. Shows core business efficiency.",
    "Earnings Growth": "How much more profit the company earned vs last year. Higher = faster growing.",
    "Sector Avg": "Average value for this metric across all companies in the same sector/industry.",
};

function explainTerm(term, label) {
    const explanation = FINANCE_GLOSSARY[term];
    if (!explanation) return label || term;
    return `<span class="finance-term" data-tooltip="${explanation}">${label || term}</span>`;
}
// ─── Loading State ───────────────────────────────────────────

function showLoading() {
    isLoading = true;
    const btn = document.getElementById('btn-analyze');
    btn.disabled = true;
    btn.textContent = 'Analyzing...';

    // Remove welcome screen if visible
    const welcome = document.getElementById('welcome-screen');
    if (welcome) welcome.style.display = 'none';

    // Show overlay on top of everything
    let overlay = document.getElementById('loading-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'loading-overlay';
        overlay.className = 'loading-overlay';
        overlay.innerHTML = `
            <div style="text-align:center;">
                <div class="spinner"></div>
                <div style="margin-top:16px; color:#787b86; font-size:13px;">Fetching data & analyzing patterns...</div>
            </div>`;
        document.getElementById('chart-area').appendChild(overlay);
    }
    overlay.style.display = 'flex';
}

function hideLoading() {
    isLoading = false;
    const btn = document.getElementById('btn-analyze');
    btn.disabled = false;
    btn.textContent = 'Analyze';

    const overlay = document.getElementById('loading-overlay');
    if (overlay) overlay.style.display = 'none';
}

function showError(message) {
    // Destroy chart if exists
    if (chart) {
        chart.remove();
        chart = null;
        trendlineOverlays = [];
    }

    document.getElementById('chart-header').style.display = 'none';
    document.getElementById('right-panel').style.display = 'none';

    const container = document.getElementById('chart-container');
    // Remove any existing welcome/error screen
    const existing = document.getElementById('welcome-screen');
    if (existing) existing.remove();

    const errorDiv = document.createElement('div');
    errorDiv.className = 'welcome';
    errorDiv.id = 'welcome-screen';
    errorDiv.innerHTML = `
        <h2 style="color:#ef5350;">Analysis Error</h2>
        <p style="color:#d1d4dc; font-size:15px;">${message}</p>
        <p style="margin-top:16px; font-size:12px; color:#787b86;">
            Double-check the ticker symbol and try again.
        </p>`;
    container.appendChild(errorDiv);
}
function _fmtLargeNum(n) {
    if (n == null) return 'N/A';
    if (typeof n !== 'number') return String(n);
    if (Math.abs(n) >= 1e12) return '$' + (n / 1e12).toFixed(1) + 'T';
    if (Math.abs(n) >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B';
    if (Math.abs(n) >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
    return '$' + n.toFixed(0);
}
// ─── Tracker / Journal ───────────────────────────────────────

let statusRefreshInterval = null;
let _activeTab = 'main';

// Resume polling when browser tab wakes from sleep
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        console.log('[Wake] Browser tab visible — resuming polling for tab:', _activeTab);
        if (_activeTab === 'autotrading') {
            loadBotStatus();
            loadBotTrades();
            loadBotPnl();
            loadBotPnlSummary();
            loadBotLog();
        } else if (_activeTab === 'stocktrading') {
            loadStockBotStatus();
            loadStockBotTrades();
            loadStockBotPnl();
            loadStockBotPnlSummary();
            loadStockBotLog();
        } else if (_activeTab === 'status') {
            loadStatus();
        }
    }
});

function switchTab(tab, skipHash) {
    _activeTab = tab;
    if (!skipHash) location.hash = tab === 'main' ? '' : tab;
    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    // Update status header button
    const statusHeaderBtn = document.getElementById('btn-status-header');
    if (statusHeaderBtn) statusHeaderBtn.classList.toggle('active', tab === 'status');

    const mainContent = document.querySelector('.main-content');
    const qullamaggieContent = document.getElementById('qullamaggie-content');
    const trackerContent = document.getElementById('tracker-content');
    const screenerContent = document.getElementById('screener-content');
    const statusContent = document.getElementById('status-content');
    const researchContent = document.getElementById('research-content');
    const autotradingContent = document.getElementById('autotrading-content');
    const stocktradingContent = document.getElementById('stocktrading-content');
    const adminContent = document.getElementById('admin-content');
    const finskillsContent = document.getElementById('finskills-content');
    const ipoContent = document.getElementById('ipo-content');
    const predictionsContent = document.getElementById('predictions-content');
    const congressContent = document.getElementById('congress-content');
    const smartMoneyContent = document.getElementById('smart-money-content');
    const trumpContent = document.getElementById('trump-content');
    const watchdogContent = document.getElementById('watchdog-content');
    const claudeBotContent = document.getElementById('claude-bot-content');
    const dashboardContent = document.getElementById('dashboard-content');
    const optionCallsContent = document.getElementById('option-calls-content');
    const earningsContent = document.getElementById('earnings-content');
    const newsContent = document.getElementById('news-content');

    mainContent.style.display = 'none';
    if (dashboardContent) dashboardContent.style.display = 'none';
    if (qullamaggieContent) qullamaggieContent.style.display = 'none';
    trackerContent.style.display = 'none';
    screenerContent.style.display = 'none';
    statusContent.style.display = 'none';
    if (researchContent) researchContent.style.display = 'none';
    if (finskillsContent) finskillsContent.style.display = 'none';
    if (ipoContent) ipoContent.style.display = 'none';
    if (predictionsContent) predictionsContent.style.display = 'none';
    if (congressContent) congressContent.style.display = 'none';
    if (smartMoneyContent) smartMoneyContent.style.display = 'none';
    if (trumpContent) trumpContent.style.display = 'none';
    if (watchdogContent) watchdogContent.style.display = 'none';
    if (claudeBotContent) claudeBotContent.style.display = 'none';
    if (optionCallsContent) optionCallsContent.style.display = 'none';
    if (earningsContent) earningsContent.style.display = 'none';
    if (newsContent) newsContent.style.display = 'none';
    if (autotradingContent) autotradingContent.style.display = 'none';
    if (stocktradingContent) stocktradingContent.style.display = 'none';
    if (adminContent) adminContent.style.display = 'none';

    // Stop status auto-refresh when leaving status tab
    if (statusRefreshInterval) {
        clearInterval(statusRefreshInterval);
        statusRefreshInterval = null;
    }
    // Stop bot auto-refresh when leaving autotrading tab
    if (window._botRefreshInterval) {
        clearInterval(window._botRefreshInterval);
        window._botRefreshInterval = null;
    }
    // Stop stock bot auto-refresh when leaving stocktrading tab
    if (window._stockBotRefreshInterval) {
        clearInterval(window._stockBotRefreshInterval);
        window._stockBotRefreshInterval = null;
    }
    // Stop watchdog auto-refresh when leaving watchdog tab
    if (window._watchdogRefreshInterval) {
        clearInterval(window._watchdogRefreshInterval);
        window._watchdogRefreshInterval = null;
    }
    // Stop claude bot auto-refresh when leaving claude-bot tab
    if (window._claudeBotRefreshInterval) {
        clearInterval(window._claudeBotRefreshInterval);
        window._claudeBotRefreshInterval = null;
    }

    if (tab === 'dashboard') {
        if (dashboardContent) dashboardContent.style.display = 'block';
        if (typeof loadDashboard === 'function') loadDashboard();
    } else if (tab === 'qullamaggie') {
        qullamaggieContent.style.display = 'flex';
    } else if (tab === 'tracker') {
        trackerContent.style.display = 'grid';
        loadJournal();
        loadGoals();
        loadTrackerBotTrades();
    } else if (tab === 'screener') {
        screenerContent.style.display = 'flex';
    } else if (tab === 'ipos') {
        ipoContent.style.display = 'flex';
    } else if (tab === 'predictions') {
        predictionsContent.style.display = 'block';
        loadPredictionMarkets(false);
    } else if (tab === 'congress') {
        congressContent.style.display = 'block';
    } else if (tab === 'smart-money') {
        smartMoneyContent.style.display = 'block';
        if (typeof loadSmartMoney === 'function') loadSmartMoney();
    } else if (tab === 'watchdog') {
        watchdogContent.style.display = 'block';
        loadWatchdogDashboard();
        window._watchdogRefreshInterval = setInterval(loadWatchdogDashboard, 60000);
    } else if (tab === 'claude-bot') {
        if (claudeBotContent) claudeBotContent.style.display = 'block';
        if (typeof loadClaudeBotDashboard === 'function') {
            loadClaudeBotDashboard();
            window._claudeBotRefreshInterval = setInterval(loadClaudeBotDashboard, 30000);
        }
    } else if (tab === 'trump') {
        trumpContent.style.display = 'block';
        loadTrumpTab();
    } else if (tab === 'status') {
        statusContent.style.display = 'flex';
        loadStatus();
        statusRefreshInterval = setInterval(loadStatus, 60000);
    } else if (tab === 'research') {
        researchContent.style.display = 'flex';
        // Default to the Sector Radar sub-view; Skills load lazily on switch.
        if (typeof initResearchTab === 'function') {
            initResearchTab();
        } else {
            loadSkillCatalog();
            loadSkillJobs();
        }
    } else if (tab === 'option-calls') {
        if (optionCallsContent) optionCallsContent.style.display = 'block';
        if (typeof initOptionCalls === 'function') initOptionCalls();
    } else if (tab === 'earnings') {
        if (earningsContent) earningsContent.style.display = 'block';
        if (typeof initEarningsCalendar === 'function') initEarningsCalendar();
    } else if (tab === 'news') {
        if (newsContent) newsContent.style.display = 'block';
        if (typeof loadNewsTab === 'function') loadNewsTab();
    } else if (tab === 'finskills') {
        finskillsContent.style.display = 'block';
        loadFinSkills();
    } else if (tab === 'autotrading') {
        autotradingContent.style.display = 'flex';
        loadBotStatus();
        loadBotDashboard();
        loadBotTrades();
        loadBotPnl();
        loadBotPnlSummary();
        loadBotLog();
        loadBotConfig();
        loadBotCoins();
        loadPlatformStatus();
        window._botRefreshInterval = setInterval(() => {
            loadBotStatus();
            loadBotLog();
        }, 5000);
    } else if (tab === 'stocktrading') {
        stocktradingContent.style.display = 'flex';
        loadStockBotStatus();
        loadStockDashboard();
        loadStockBotTrades();
        loadStockBotPnl();
        loadStockBotPnlSummary();
        loadStockBotLog();
        loadStockBotConfig();
        loadStockBotStocks();
        loadStockBrokerStatus();
        window._stockBotRefreshInterval = setInterval(() => {
            loadStockBotStatus();
            loadStockBotLog();
        }, 5000);
    } else if (tab === 'admin') {
        adminContent.style.display = 'block';
        if (typeof switchAdminTab === 'function') switchAdminTab('usage');
        loadAdminUsers();
        loadAdminInvites();
        loadAdminConfig();
        loadAdminBotDefaults();
        if (typeof loadAdminLlmOverrides === 'function') loadAdminLlmOverrides();
        if (typeof loadAdminOllamaConfig === 'function') loadAdminOllamaConfig();
        if (typeof loadAdminPlatform === 'function') loadAdminPlatform();
        if (typeof loadAdminUsersUsage === 'function') loadAdminUsersUsage();
        if (typeof loadAdminAiUsage === 'function') loadAdminAiUsage();
    } else {
        mainContent.style.display = 'grid';
    }
}
// ─── Font Size Toggle ────────────────────────────────────────
function setFontSize(size) {
    document.body.classList.remove('font-small', 'font-medium', 'font-large');
    document.body.classList.add('font-' + size);
    document.querySelectorAll('.font-size-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.size === size);
    });
    try { localStorage.setItem('fontSizePref', size); } catch(e) {}
}

document.querySelectorAll('.font-size-btn').forEach(btn => {
    btn.addEventListener('click', () => setFontSize(btn.dataset.size));
});

// Restore saved preference
(function() {
    try {
        const saved = localStorage.getItem('fontSizePref');
        if (saved && ['small','medium','large'].includes(saved)) {
            setFontSize(saved);
        }
    } catch(e) {}
})();
// ─── User Guide System ───────────────────────────────────────
const GUIDES = {
    analyzer: {
        title: 'Analyzer — User Guide',
        sections: [
            { heading: 'What It Does', body: 'The Analyzer runs a full technical + fundamental analysis on any stock or crypto ticker. It fetches price data, calculates 20+ indicators, detects chart patterns, and sends everything through a multi-LLM validation pipeline for an AI verdict.' },
            { heading: 'How to Use', body: '<ol><li>Type a ticker symbol (e.g., AAPL, BTC-USD) in the search bar</li><li>Select a time period (1M to 2Y) and interval (Daily/Weekly)</li><li>Click <strong>Analyze</strong></li><li>The chart loads with candlesticks, volume, and overlay indicators</li><li>The right panel shows the AI analysis results</li></ol>' },
            { heading: 'Reading the Results', body: '<ul><li><strong>Breakout Status</strong> — Green (bullish), Red (bearish), Orange (neutral). Shows detected chart pattern and confidence.</li><li><strong>Grade (A-D)</strong> — Overall setup quality. A = strong setup, D = avoid.</li><li><strong>Trade Plan</strong> — Entry, stop loss, take profit, and risk/reward ratio. These are ATR-based levels.</li><li><strong>Indicators</strong> — RSI, MACD, moving averages, Bollinger Bands, volume analysis. Green = bullish, Red = bearish.</li><li><strong>AI Verdict</strong> — Multi-model consensus from 3 LLMs (research, pattern, prediction). Shows agreement level.</li><li><strong>Fundamentals</strong> — P/E, revenue growth, margins, debt ratios for stocks. Market cap and volume for crypto.</li></ul>' },
            { heading: 'Chart Overlays', body: 'Use the toolbar below the chart to toggle: SMA 8/20/50/200, EMA 20, Bollinger Bands, VWAP, support/resistance levels, and Fibonacci retracements. Each overlay helps visualize different aspects of price action.' },
            { heading: 'Tips', body: '<ul><li>Use <strong>Fast Mode</strong> (top right) for quicker, cheaper analysis using faster LLM models</li><li>Weekly intervals give cleaner patterns for swing trades</li><li>Look for Grade A/B setups with bullish breakout status and RSI between 40-65 for the best entries</li></ul>' },
        ],
    },
    qullamaggie: {
        title: 'Breakout Scanner — User Guide',
        sections: [
            { heading: 'What It Does', body: 'The Breakout Scanner uses the Qullamaggie strategy (Kristjan Kullamägi) to find stocks forming tight consolidation patterns near highs — the classic "volatility contraction pattern" (VCP) that precedes explosive breakouts.' },
            { heading: 'How to Use', body: '<ol><li>Select a category (Low-Cap, Mid-Cap, Large-Cap, or All)</li><li>Click <strong>Scan</strong></li><li>The scanner checks 100+ tickers for VCP/flag patterns</li><li>Results show stocks ranked by pattern quality</li></ol>' },
            { heading: 'Reading the Results', body: '<ul><li><strong>Pattern Score</strong> — Higher = tighter consolidation near highs. 80+ is excellent.</li><li><strong>Relative Volume</strong> — Volume vs. 20-day average. >1.5x signals institutional interest.</li><li><strong>Distance from High</strong> — How far price is from 52-week high. <10% = leader pulling back.</li><li><strong>ADR%</strong> — Average Daily Range as %. Higher = more volatile/tradeable.</li><li><strong>Consolidation Days</strong> — How long the base has been forming. 10-30 days is ideal.</li></ul>' },
            { heading: 'The Qullamaggie Setup', body: 'The ideal breakout setup:<ol><li>Stock is in a strong uptrend (above rising 20 EMA)</li><li>Price consolidates in a tight range (flag/pennant)</li><li>Volume dries up during consolidation</li><li>Breakout on above-average volume</li></ol>Enter on the breakout candle, stop loss below the consolidation low.' },
        ],
    },
    tracker: {
        title: 'Tracker & Journal — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Track your trades, set weekly P&L goals, and review your journal. The bot auto-logs trades here too, so everything is in one place.' },
            { heading: 'How to Use', body: '<ol><li>Log trades manually: ticker, action (BUY/SELL), entry/exit price, shares, notes</li><li>Set weekly goals: target dollar amount and track actual vs. target</li><li>Review your journal history with P&L per trade</li></ol>' },
            { heading: 'Reading the Results', body: '<ul><li><strong>Weekly Goals</strong> — Green progress bar = on track, Red = behind. Set realistic targets.</li><li><strong>Journal Entries</strong> — Shows all trades chronologically. Bot trades are tagged with [Crypto Bot] or [Stock Bot].</li><li><strong>P&L</strong> — Green = profit, Red = loss. Running total helps track your account growth.</li></ul>' },
        ],
    },
    screener: {
        title: 'Screener — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Multi-category stock screener that scans 50+ tickers per category, runs technical analysis, and vets each candidate with AI. Categories include Low-Cap, Mid-Cap, Large-Cap, ETFs, Metals & Mining, Crypto, AI stocks, Top Gainers, and Top Losers.' },
            { heading: 'How to Use', body: '<ol><li>Select a category from the top bar</li><li>Optionally filter by sector, adjust price range and scan limit</li><li>Click <strong>Scan & Vet with AI</strong></li><li>Results split into Opportunities (AI-approved) and Risky (flagged)</li></ol>' },
            { heading: 'Hot Sectors Panel', body: 'Click a time period (1W to 1Y) to see which sectors are trending. The AI identifies the strongest sectors based on ETF performance, volume flows, and momentum. Use this to focus your screening.' },
            { heading: 'Reading the Results', body: '<ul><li><strong>Green cards (Opportunities)</strong> — AI sees a favorable setup. Check the summary for why.</li><li><strong>Orange cards (Risky)</strong> — Has potential but with red flags. Read the risks before entering.</li><li><strong>AI Summary</strong> — 2-3 sentence analysis covering technicals, fundamentals, and sentiment.</li><li><strong>Red Flags / Risks</strong> — Specific concerns (low volume, high debt, earnings miss, etc.)</li><li>Click <strong>Full Analysis</strong> on any card to run the complete Analyzer on that ticker.</li></ul>' },
        ],
    },
    ipos: {
        title: 'IPOs, Pre-IPO, VC & Startups — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Scans Reddit (r/ipos, r/wallstreetbets, r/venturecapital, r/startups + 17 keyword searches), Substack newsletters, TechCrunch, Crunchbase, and PitchBook for real-time IPO, VC, and startup deal intelligence. Feeds all social data to an AI analyst that identifies and rates opportunities. Includes a curated directory of 20 investment platforms.' },
            { heading: 'Categories', body: '<ul><li><strong>IPOs</strong> — Companies with S-1 filings or confirmed plans to go public in 1-6 months</li><li><strong>Pre-IPO</strong> — Secondary market access via Forge Global, EquityZen, Linqto, Hiive</li><li><strong>VC Deals</strong> — Late-stage VC-backed companies (Series C+) via Republic, AngelList, or secondary markets</li><li><strong>Startups</strong> — Early/mid-stage startups on crowdfunding platforms (Republic, Wefunder, StartEngine)</li><li><strong>Platforms</strong> — Directory of 20 best platforms for IPO, VC, and startup investing</li><li><strong>How to Start</strong> — Step-by-step guide to startup investing and connecting with deal flow</li></ul>' },
            { heading: 'How to Use', body: '<ol><li>Click <strong>Scan Opportunities</strong> (takes 30-60s — scraping 50+ sources)</li><li>Use the filter bar to focus on a category</li><li>Use advanced filters (Sector, Rating, Access, Risk) to narrow results</li><li>Browse <strong>Platforms</strong> tab anytime — no scan needed</li><li>Read <strong>How to Start</strong> for a complete startup investing guide</li></ol>' },
            { heading: 'Reading the Cards', body: '<ul><li><strong>Star Rating (1-5)</strong> — Weighted: Social Buzz 25%, Institutional Interest 25%, Market Fit 20%, Moat 20%, Risk (inverse) 10%</li><li><strong>Rating Bars</strong> — Visual breakdown of each dimension. Longer = stronger.</li><li><strong>Social Signals</strong> — Where the buzz is coming from (which subreddits, Substack coverage). Reddit post count shown if available.</li><li><strong>How to Invest</strong> — Specific platforms, minimum investment, accreditation requirements, and expected returns.</li><li><strong>Accreditation Badges</strong> — Green = open to everyone, Orange = accredited investors ($200K+ income or $1M+ net worth), Red = qualified purchaser ($5M+).</li><li><strong>VC Backers</strong> — Named investors. Tier-1 VCs (a16z, Sequoia) = stronger signal.</li></ul>' },
            { heading: 'Tips', body: '<ul><li>Start with the <strong>Platforms</strong> tab to understand your options before scanning</li><li>VC Deals carry the highest risk but also the highest return potential (3-10x)</li><li>Startup crowdfunding is open to everyone — minimums as low as $50</li><li>Pre-IPO secondary shares are illiquid — you may not be able to sell until the actual IPO</li><li>Always verify platform details and accreditation requirements directly on the platform before investing</li></ul>' },
        ],
    },
    autotrading: {
        title: 'Crypto Trading Bot — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Autonomous crypto trading bot running on BloFin (demo/paper mode). Scans your selected coins every 5 minutes, generates signals using 9 strategies, validates with multi-LLM consensus, and executes trades with automated stop-loss and take-profit.' },
            { heading: 'Dashboard', body: '<ul><li><strong>Total P&L</strong> — Cumulative profit/loss across all closed trades</li><li><strong>Win Rate</strong> — Percentage of profitable trades. >55% is good.</li><li><strong>Total Fees</strong> — Estimated trading fees (0.08% per side for BloFin). Net P&L = P&L minus fees.</li><li><strong>Daily Goal</strong> — Progress toward your $500/day target</li><li><strong>Strategy Performance</strong> — Which strategies are winning/losing. Strategies below 20% win rate get auto-blacklisted.</li></ul>' },
            { heading: '9 Strategies', body: '<ol><li><strong>MACD Cross</strong> — Bullish/bearish MACD crossovers with RSI confirmation</li><li><strong>EMA Trend</strong> — SMA8/EMA20 crossovers for trend shifts</li><li><strong>RSI Reversion</strong> — Oversold bounces / overbought fades</li><li><strong>Momentum</strong> — Volume surge + price above/below SMA50</li><li><strong>BB Reversion</strong> — Bollinger Band touches with MACD confirmation</li><li><strong>Grid Reversion</strong> — Price deviation from SMA50 in trending markets</li><li><strong>Trend DCA</strong> — Pullbacks to EMA20 in established trends</li><li><strong>Doji Reversal</strong> — Doji candles at RSI extremes</li><li><strong>Pump on Close</strong> — Volume surge + strong close position</li></ol>' },
            { heading: 'Safety Features', body: '<ul><li><strong>Kill Switch</strong> — Emergency stop. Auto-heals after 30 min cooldown.</li><li><strong>Daily Loss Limit</strong> — $500 default. Triggers kill switch if breached.</li><li><strong>Max Positions</strong> — 6 concurrent (prevents over-exposure).</li><li><strong>Self-Learning</strong> — Scales position size 0.7x-1.0x based on 7-day win rate. Blacklists losing strategies.</li><li><strong>Paper Trading Only</strong> — All trades execute on BloFin demo. No real money at risk.</li></ul>' },
            { heading: 'Config', body: 'Adjustable settings: scan interval, daily loss limit, max positions, position size %, direction bias (long/short/both/auto), quick trade mode (bypass LLM), coin selection.' },
        ],
    },
    stocktrading: {
        title: 'Stock Trading Bot — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Same architecture as the crypto bot but for US stocks via Alpaca paper trading. Respects market hours (9:30 AM - 4 PM ET), PDT rules, and uses slightly different ATR multipliers (1.5x SL, 2.5x TP).' },
            { heading: 'Key Differences from Crypto', body: '<ul><li><strong>Market Hours</strong> — Only trades when market is open (configurable extended hours)</li><li><strong>PDT Rule</strong> — If equity < $25K, limited to 3 day trades per 5 days</li><li><strong>Whole Shares</strong> — Positions are rounded to whole shares</li><li><strong>Fees</strong> — Estimated at $0.01/share (SEC + FINRA regulatory fees)</li><li><strong>Direction Bias</strong> — Defaults to Long Only (most stocks trend up)</li><li><strong>Time Exit</strong> — Positions open >24h with <0.3% P&L get closed as stale</li><li><strong>Max Positions</strong> — 8 (vs. 6 for crypto)</li></ul>' },
            { heading: 'Reading the Dashboard', body: 'Same layout as crypto: P&L tiles, strategy table, top assets, fees by period. Compare crypto vs. stock performance in the "Crypto vs Stock" section at the bottom.' },
        ],
    },
    research: {
        title: 'Research — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Run specialized research skills — earnings analysis, DCF models, comparable company analysis, sector reports, and more. Each skill runs multi-step LLM workflows and produces professional output (DOCX, XLSX, or detailed analysis).' },
            { heading: 'How to Use', body: '<ol><li>Browse the skill catalog on the left</li><li>Click a skill to load it into the launcher</li><li>Fill in the required inputs (ticker, data files, etc.)</li><li>Click <strong>Run</strong> and wait for the result</li><li>Download the output file when ready</li></ol>' },
        ],
    },
    finskills: {
        title: 'Fin Skills — User Guide',
        sections: [
            { heading: 'What It Does', body: 'A curated library of financial analysis skills organized by domain — Investment Banking, Equity Research, Private Equity, Wealth Management. Each skill is a specialized AI workflow.' },
            { heading: 'Domains', body: '<ul><li><strong>Financial Analysis</strong> — Comps, DCF, 3-statement models, LBO, competitive analysis</li><li><strong>Investment Banking</strong> — Pitch decks, CIMs, merger models, buyer lists, deal tracking</li><li><strong>Equity Research</strong> — Earnings updates, initiating coverage, sector reports, screens</li><li><strong>Private Equity</strong> — IC memos, due diligence, value creation plans, returns analysis</li><li><strong>Wealth Management</strong> — Financial plans, rebalancing, tax-loss harvesting, client reports</li></ul>' },
        ],
    },
    trump: {
        title: 'Trump Market Indicator — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Tracks presidential rhetoric in real-time from Truth Social, GDELT news, and White House briefings. Calculates a mood score (-100 to +100) and predicts market impact using ML models.' },
            { heading: 'Current Mood Card', body: '<ul><li><strong>Mood Score (-100 to +100)</strong> — Aggregate sentiment from all sources. Positive = bullish rhetoric (deals, tax cuts). Negative = bearish (tariffs, conflict).</li><li><strong>Label</strong> — BULLISH (>30), LEAN BULL (10-30), NEUTRAL (-10 to 10), LEAN BEAR (-30 to -10), BEARISH (<-30)</li><li><strong>Pattern</strong> — 3-day trend: IMPROVING (mood rising), STABLE, DETERIORATING (mood falling). Acceleration shows rate of change.</li><li><strong>Trade Signals</strong> — AI-derived BUY/AVOID sectors based on which policies are being discussed (tariffs → avoid manufacturing, tax cuts → buy financials).</li></ul>' },
            { heading: 'Mood Timeline', body: 'Bar chart showing mood history. <strong>Green bars</strong> = bullish days, <strong>red bars</strong> = bearish days. <strong>Hover</strong> over any bar to see the exact date, mood score, label, trend, and top detected signals.' },
            { heading: 'TACO Milestones (🌮)', body: 'Taco emojis on the timeline mark <strong>policy reversals</strong> — moments when Trump reversed a previous stance. Hover to see: what the original stance was, what it changed to, the mood swing (how many points the mood moved), and confidence level. Bigger swings = bigger market impact.' },
            { heading: 'Mood → Market Forecaster (ML)', body: 'Predicts returns for SPY, QQQ, BTC, ETH across 1-day, 5-day, and 21-day horizons.<ul><li><strong>Erratic Gauge</strong> — Persistence Score (70%+ = stable mood), Flip Count (sign reversals in 14 days), Mood Volatility (std dev), Mean Reversion Z-score (extreme = snap-back due). High erratic scores = lower forecast confidence.</li><li><strong>Forecast Matrix</strong> — Each cell shows predicted return % with two scenarios: PERSIST (mood stays, green bar) vs. FLIP (mood reverses, red bar). The bar widths show probability of each.</li><li><strong>Reversal Risk</strong> — LOW (<20%) = trust the forecast. MEDIUM (20-40%) = hedge. HIGH (>40%) = the FLIP scenario is likely.</li><li><strong>How to use</strong>: When persistence is high and reversal risk is low, trust the direction. When erratic metrics spike, expect wider uncertainty and weight the flip scenario.</li></ul>' },
            { heading: 'Mood ↔ Market Correlation (Lead/Lag)', body: 'Heatmap showing how many days Trump rhetoric <strong>leads</strong> asset price moves.<ul><li><strong>Columns (t-0 to t-7)</strong> — t-0 = same-day effect, t-1 = yesterday\'s mood vs today\'s price, t-2 = 2 days ago vs today, etc.</li><li><strong>Colors</strong> — Green (positive) = bullish mood led to price going UP. Red (negative) = bearish mood led to price going DOWN. Darker = stronger correlation.</li><li><strong>Best Lag row</strong> — The key takeaway. Example: "SPY: lag 2d, r=+0.35" means Trump\'s rhetoric from 2 days ago is the strongest predictor of SPY today. You have a 2-day window to position.</li><li><strong>How to use</strong>: If BTC has best lag at t-0 with r=+0.28, crypto reacts same-day — no lead time. If SPY is at t-2, you have 2 days to position after a mood shift. Correlations beyond t-3 are usually weak — rhetoric impact fades after ~3 days.</li></ul>' },
            { heading: 'Policy Reversal Timeline', body: 'Detailed list of detected policy reversals (TACOs). Shows: policy area, original stance, new stance, days to reversal, mood swing, market impact direction (bullish/bearish), and confidence. Useful for understanding Trump\'s reversal patterns — historically he reverses tariff threats within 5-14 days.' },
        ],
    },
    watchdog: {
        title: 'ThunderBot — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Combines market regime analysis with swing trade signals and automated paper trading. Monitors SPY, QQQ, and your custom watchlist using a 3-axis regime score.' },
            { heading: 'Market Regime', body: '<ul><li><strong>3 Axes</strong>: Market Structure (45%), Sentiment (25%), Technical (30%)</li><li><strong>RISK-ON</strong> (green, 70+) — Full trading, all signals active</li><li><strong>NEUTRAL</strong> (yellow, 50-70) — Trade with caution</li><li><strong>RISK-OFF</strong> (orange, 30-50) — Reduced confidence on BUY signals</li><li><strong>DANGER</strong> (red, <30) — No new trades, BUY → HEDGE override</li></ul>' },
            { heading: 'Swing Signals', body: '5 strategies: <strong>VCP</strong> (volatility contraction), <strong>HTF</strong> (high tight flag), <strong>Breakout</strong> (20-day high + volume), <strong>Earnings Gap</strong> (5%+ gap holding), <strong>Trend Pullback</strong> (EMA20 bounce in uptrend). Signals are color-coded: BUY (green), HEDGE (orange), WATCH (gray), SELL/AVOID (red).' },
            { heading: 'Screener Integration', body: 'Watchdog now automatically pulls high-confidence candidates from the Screener (gainers, AI, midcap, largecap) and runs them through an LLM vet + swing signal analysis. These appear with a [Screener: ...] tag in their reasoning.' },
            { heading: 'Options Flow — Calls vs Puts', body: '<ul><li><strong>Real-time tracker</strong> for SPY and QQQ options. Fetches call/put volumes every 30 seconds.</li><li><strong>P/C Ratio chart</strong> — Line chart with BEARISH zone (>1.2, red shading) and BULLISH zone (<0.7, green shading). Blue line = SPY, Gold line = QQQ.</li><li><strong>Shift notifications</strong> — Alerts when the P/C ratio crosses thresholds (puts overtaking calls or vice versa). Browser notifications if permitted.</li><li><strong>How to use</strong>: P/C ratio >1.2 = bearish options flow (more puts), signals market expects downside. P/C ratio <0.7 = bullish (more calls). Watch for sudden ratio surges as they precede price moves.</li></ul>' },
            { heading: 'Auto-Trader', body: 'Paper trading via Alpaca. Buy window: 9:30-10:30 AM CST only. Exit rules: 4-7% profit take, stop-loss, 14-day max hold. Regime gates prevent trading in DANGER mode. All trades are backtested against recent price history before execution.' },
            { heading: 'Pre-Trade Backtest', body: 'Before every trade, the bot simulates the signal at each of the last 5 price bars with the same SL/TP levels. If the backtest shows the signal would have lost money recently (win rate <30% AND negative avg P&L), the trade is blocked. This prevents repeating recent losing patterns.' },
        ],
    },
    congress: {
        title: 'Congress Trades — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Tracks US Congress member stock trades from official STOCK Act disclosures. House data comes from official PTR filings (PDFs); Senate data from news aggregation and QuiverQuant API.' },
            { heading: 'How to Use', body: '<ol><li>Filter by Chamber (House/Senate), member name, state, ticker, or time period</li><li>Click <strong>Search</strong> to load data (first scan takes 15-30s for PDF parsing)</li></ol>' },
            { heading: 'Reading the Data', body: '<ul><li><strong>Hot Tickers</strong> — Most-traded securities by politicians. Bar chart shows buy/sell split.</li><li><strong>Top Buys/Sells</strong> — Largest trades by dollar value (e.g., $1M+ purchases).</li><li><strong>Stats</strong> — House vs Senate trade counts, unique members, top traders by volume.</li><li><strong>Trade Table</strong> — Full list with date, name, ticker, type (Purchase/Sale), amount range, state, chamber, and PDF link to the original filing.</li></ul>' },
            { heading: 'Tips', body: 'Congressional trades are reported 30-45 days after execution. Track which tickers multiple members are buying — convergence = strong signal. Nancy Pelosi\'s trades historically correlate with 30-day returns.' },
        ],
    },
    'smart-money': {
        title: 'Smart Money Tracker — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Tracks the top 20 hedge funds and 20 most notable investors using SEC 13F filings (quarterly institutional holdings reports). Shows where the "smart money" is moving capital — new positions, increased stakes, and sells.' },
            { heading: 'Data Sources', body: '<ul><li><strong>SEC EDGAR 13F-HR</strong> — Quarterly filings required for all institutions managing $100M+. Updated within 45 days of quarter-end.</li><li><strong>Holdings parsed</strong> — Company name, shares, value (in thousands), put/call type</li><li><strong>Change detection</strong> — Compares current filing to previous filing to detect NEW, INCREASED, REDUCED, or HELD positions</li><li><strong>Daily refresh</strong> — The system checks for new filings every 24 hours</li></ul>' },
            { heading: 'Convergence Signals', body: 'The most powerful feature. When <strong>2+ funds are building positions in the same ticker</strong>, it appears as a convergence signal. ACCUMULATING (green) = multiple funds buying. DISTRIBUTING (red) = multiple funds selling. MIXED = split. Higher fund count = stronger signal.' },
            { heading: 'Hot Tickers', body: 'Tickers held across the most funds, with buy/sell breakdown. A ticker held by 10+ funds with mostly buys = strong institutional conviction.' },
            { heading: 'Fund List', body: 'Click any fund to drill into their full portfolio: every holding with ticker, company, value, shares, % of portfolio, action (NEW/INCREASED/REDUCED/HELD), and % change from prior filing.' },
            { heading: 'Top 20 Investors', body: 'Notable traders linked to their fund entities. Click to view their holdings. Includes: Warren Buffett, Ray Dalio, Ken Griffin, Jim Simons, Steve Cohen, Bill Ackman, David Tepper, Carl Icahn, George Soros, Seth Klarman, Dan Loeb, David Einhorn, Chase Coleman, Philippe Laffont, Andreas Halvorsen, Israel Englander, Paul Singer, Nancy Pelosi, Michael Burry, Cathie Wood.' },
            { heading: 'Tips', body: '<ul><li>13F filings are reported quarterly with a 45-day lag — these show where smart money <strong>was</strong>, not where it is now. Use as directional bias, not timing.</li><li>Convergence signals across 3+ funds = high conviction. These tend to outperform.</li><li>Watch for NEW positions — these are fresh ideas, not legacy holdings.</li><li>Reduced positions with high % change (>50% sold) = strong exit signal.</li><li>Compare with Congress trades for double-confirmation (politician + institutional buying).</li></ul>' },
        ],
    },
    predictions: {
        title: 'Markets & Predictions — User Guide',
        sections: [
            { heading: 'What It Does', body: 'Aggregated market predictions and price targets. Shows consensus forecasts from multiple models and analysts.' },
            { heading: 'How to Use', body: 'Browse prediction markets for various assets. Check the confidence level and supporting rationale. Higher confidence with multiple agreeing sources = stronger signal.' },
        ],
    },
    earnings: {
        title: 'Earnings Calendar — User Guide',
        sections: [
            { heading: 'What It Does', body: 'A weekly "most anticipated reports" board — every large/mid-cap company reporting earnings this week, laid out Mon–Fri and grouped by session (<strong>Before Open</strong>, <strong>After Close</strong>, <strong>Session TBD</strong>). Each name is scored and colour-coded so you can see, at a glance, which reporters are strong going into the print.' },
            { heading: 'How to Use', body: '<ol><li>Open the tab — it loads the current week automatically</li><li>Use <strong>&#8249; Prev / Next &#8250;</strong> to move week to week, or <strong>This week</strong> to jump back</li><li>Click a filter chip (<strong>Leading / Improving / Weakening / Lagging / Near highs</strong>) to narrow the board — filtering is instant (no refetch)</li><li>Tick <strong>Wide universe</strong> to add mid-caps; <strong>Refresh</strong> forces a live re-pull</li></ol>' },
            { heading: 'Reading the Badges', body: 'The badge is the stock\'s <strong>RRG quadrant</strong> — its rotation relative to the S&amp;P 500 (SPY):<ul><li><strong>LE — Leading</strong> (green): outperforming and still gaining strength</li><li><strong>IM — Improving</strong> (blue): lagging but turning up — early momentum</li><li><strong>WE — Weakening</strong> (amber): outperforming but losing steam</li><li><strong>LAG — Lagging</strong> (red): underperforming and still falling</li><li><strong>Green dot</strong> — price is within 3% of its 52-week high</li></ul>' },
            { heading: 'The DX Score (0–100)', body: 'The number on the right is a composite of 3-month momentum, relative strength vs SPY, and how close the stock is to its highs — then scaled 0–100 across this week\'s reporters so the board sorts strongest-first within each session. Higher = stronger technical setup <em>into</em> the report (it is not a prediction of the earnings result).' },
            { heading: 'Where the Data Comes From', body: 'Earnings dates, company names and the before/after-close session come from <strong>yfinance</strong>; prices for the RRG/score come from one batched yfinance download vs SPY. The board is cached for a few hours and mirrored to the database so it loads instantly after a restart. Session is best-effort — many names show <strong>TBD</strong> until the exact time is confirmed.' },
            { heading: 'Tips', body: '<ul><li>Filter to <strong>Leading + Near highs</strong> for names with momentum going into the print — these tend to have the most violent reactions.</li><li>A high DX Score with a <strong>Lagging</strong> badge is a divergence worth a closer look in the Analyzer.</li><li>Click through to the <strong>Analyzer</strong> or <strong>Option Calls</strong> tab on any ticker to dig deeper before the report.</li></ul>' },
        ],
    },
    overview: {
        title: 'How TradeWiz Works',
        sections: [
            { heading: 'The Big Picture', body:
                '<p style="margin:0 0 12px">TradeWiz pulls <strong>live market &amp; alternative data</strong> from many sources, runs it through <strong>analysis engines</strong>, has a <strong>panel of AI models</strong> vet the result, and surfaces it in the <strong>feature tabs</strong> you use. Same pipeline everywhere — only the data source and the question change.</p>' +
                '<div style="border:1px solid var(--border,#333);border-radius:10px;overflow:hidden;font-size:12px">' +
                  // Layer 1 — Data
                  '<div style="padding:10px 12px;background:var(--bg-tertiary,#1a1d24)">' +
                    '<div style="font-weight:700;letter-spacing:.5px;color:var(--accent-primary);margin-bottom:6px">1 · DATA SOURCES</div>' +
                    '<div style="display:flex;flex-wrap:wrap;gap:6px">' +
                      ['yfinance (prices/options/earnings)','SEC EDGAR 13F','Reddit + Substack','Truth Social / GDELT','Congress PTR filings','BloFin (crypto)','Alpaca (stocks)','OpenRouter + Ollama (LLMs)']
                        .map(function(s){return '<span style="padding:3px 8px;border-radius:999px;background:var(--bg-input,#12141a);border:1px solid var(--border,#333)">'+s+'</span>';}).join('') +
                    '</div></div>' +
                  '<div style="text-align:center;color:var(--text-muted,#8a90a0);padding:2px">&#9660;</div>' +
                  // Layer 2 — Engines
                  '<div style="padding:10px 12px;background:var(--bg-tertiary,#1a1d24)">' +
                    '<div style="font-weight:700;letter-spacing:.5px;color:var(--accent-primary);margin-bottom:6px">2 · ANALYSIS ENGINES</div>' +
                    '<div style="display:flex;flex-wrap:wrap;gap:6px">' +
                      ['analysis_engine (20+ indicators, patterns)','screener (multi-category scan)','earnings_calendar (RRG + DX score)','market_sensor (regime)','news_agent','smart_money (13F)']
                        .map(function(s){return '<span style="padding:3px 8px;border-radius:999px;background:var(--bg-input,#12141a);border:1px solid var(--border,#333)">'+s+'</span>';}).join('') +
                    '</div></div>' +
                  '<div style="text-align:center;color:var(--text-muted,#8a90a0);padding:2px">&#9660;</div>' +
                  // Layer 3 — AI
                  '<div style="padding:10px 12px;background:var(--bg-input,#12141a);border-top:1px solid var(--border,#333);border-bottom:1px solid var(--border,#333)">' +
                    '<div style="font-weight:700;letter-spacing:.5px;color:var(--accent-purple,#a78bfa);margin-bottom:6px">3 · MULTI-LLM VALIDATION</div>' +
                    '<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center">' +
                      ['Research model','Pattern model','Risk model'].map(function(s){return '<span style="padding:3px 8px;border-radius:6px;background:var(--bg-tertiary,#1a1d24);border:1px solid var(--accent-purple,#a78bfa)">'+s+'</span>';}).join('') +
                      '<span style="color:var(--text-muted,#8a90a0)">&#8594; consensus verdict (rule-based fallback if LLMs are down)</span>' +
                    '</div></div>' +
                  '<div style="text-align:center;color:var(--text-muted,#8a90a0);padding:2px">&#9660;</div>' +
                  // Layer 4 — Features
                  '<div style="padding:10px 12px;background:var(--bg-tertiary,#1a1d24)">' +
                    '<div style="font-weight:700;letter-spacing:.5px;color:var(--accent-green,#22c55e);margin-bottom:6px">4 · FEATURE TABS (what you click)</div>' +
                    '<div style="display:flex;flex-wrap:wrap;gap:6px">' +
                      ['Analyzer','Screener','Breakout Scanner','Earnings','Option Calls','Research','Smart Money','Congress','Trump','Crypto/Stock Bots','ThunderBot']
                        .map(function(s){return '<span style="padding:3px 8px;border-radius:999px;background:var(--bg-input,#12141a);border:1px solid var(--accent-green,#22c55e)">'+s+'</span>';}).join('') +
                    '</div></div>' +
                  '<div style="text-align:center;color:var(--text-muted,#8a90a0);padding:2px">&#9660;</div>' +
                  // Layer 5 — You
                  '<div style="padding:10px 12px;background:var(--bg-input,#12141a)">' +
                    '<div style="font-weight:700;letter-spacing:.5px;color:var(--text-primary,#fff);margin-bottom:6px">5 · YOU</div>' +
                    '<div style="color:var(--text-muted,#8a90a0)">Dashboard, Tracker/Journal, and Alerts tie it together — decisions, logged trades, and notifications.</div>' +
                  '</div>' +
                '</div>' },
            { heading: 'Where each data source connects', body:
                '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
                '<thead><tr style="text-align:left;color:var(--text-muted,#8a90a0)">' +
                  '<th style="padding:6px 8px;border-bottom:1px solid var(--border,#333)">Source</th>' +
                  '<th style="padding:6px 8px;border-bottom:1px solid var(--border,#333)">Feeds these tabs</th></tr></thead><tbody>' +
                [['yfinance','Analyzer, Screener, Breakout, Earnings, Option Calls, both Bots, ThunderBot'],
                 ['SEC EDGAR (13F)','Smart Money (fund holdings, convergence signals)'],
                 ['Congress PTR / QuiverQuant','Congress trades'],
                 ['Reddit · Substack · TechCrunch','IPOs / VC / Startups, News'],
                 ['Truth Social · GDELT','Trump market indicator'],
                 ['BloFin (demo)','Crypto Trading Bot — paper orders'],
                 ['Alpaca (paper)','Stock Trading Bot, ThunderBot — paper orders'],
                 ['OpenRouter · Ollama','Every "AI" verdict, screener vetting, bot trade votes']]
                 .map(function(r){return '<tr><td style="padding:6px 8px;border-bottom:1px solid var(--border,#222);font-weight:600">'+r[0]+'</td><td style="padding:6px 8px;border-bottom:1px solid var(--border,#222);color:var(--text-muted,#8a90a0)">'+r[1]+'</td></tr>';}).join('') +
                '</tbody></table>' },
            { heading: 'Worked example — analysing a stock', body:
                '<ol style="margin:0;padding-left:18px">' +
                '<li>You type <strong>AAPL</strong> in the Analyzer and click Analyze.</li>' +
                '<li><strong>yfinance</strong> returns OHLCV price history + fundamentals.</li>' +
                '<li><strong>analysis_engine</strong> computes 20+ indicators (RSI, MACD, moving averages, Bollinger, ATR) and detects chart patterns.</li>' +
                '<li>That package is sent to the <strong>multi-LLM panel</strong> (research + pattern + risk models), which returns a consensus verdict, grade, and an ATR-based trade plan.</li>' +
                '<li>The chart + AI verdict render in the tab. Nothing is a recommendation — it is analysis you act on.</li></ol>' },
            { heading: 'Worked example — the Earnings tab', body:
                '<ol style="margin:0;padding-left:18px">' +
                '<li>You open <strong>Earnings</strong>. The engine lists every universe name reporting this week (yfinance earnings dates).</li>' +
                '<li>One batched <strong>yfinance download</strong> pulls 6 months of prices for all reporters + SPY.</li>' +
                '<li>For each name it computes the <strong>RRG quadrant</strong> vs SPY, whether it is <strong>near its 52-week high</strong>, and a <strong>0–100 DX Score</strong>.</li>' +
                '<li>Names are placed into Mon–Fri columns by report day and Before-Open / After-Close by session, sorted by score.</li>' +
                '<li>The whole board is cached and saved to the database so it reloads instantly.</li></ol>' },
            { heading: 'How the trading bots fit in', body:
                'The Crypto and Stock bots run the <em>same</em> pipeline on a loop (every ~5 min): scan your coins/tickers &#8594; <strong>rule-based signal</strong> from 9 strategies &#8594; <strong>risk gates</strong> (kill switch, daily-loss limit, max positions) &#8594; a <strong>multi-LLM vote</strong> to approve/reject &#8594; <strong>paper order</strong> on BloFin demo / Alpaca paper. Everything is <strong>paper trading</strong> unless you explicitly opt into live mode. See the Crypto/Stock Bot guides for the full breakdown.' },
            { heading: 'One rule everywhere: AI has a fallback', body:
                'Every AI call path has a non-AI fallback. If the LLM providers are unreachable, the screener/analyzer/bots fall back to rule-based logic instead of failing — so the platform keeps working even when the models are down.' },
        ],
    },
    screener_default: {
        title: 'Feature Guide',
        sections: [
            { heading: 'Welcome', body: 'Select a tab to get started. Click the Guide button anytime to learn how to use the current feature — or hit <strong>How It All Works</strong> at the top of the guide for a full system overview.' },
        ],
    },
};

// Build the inner HTML (title + sections) for one guide object.
function _renderGuideBody(guide) {
    let html = `<h3 class="guide-inner-title" style="margin:0 0 14px;font-size:17px">${guide.title}</h3>`;
    guide.sections.forEach(s => {
        html += `<div class="guide-section">
            <h4 class="guide-section-heading">${s.heading}</h4>
            <div class="guide-section-body">${s.body}</div>
        </div>`;
    });
    return html;
}

// Toggle the guide modal between the current feature and the system overview.
function guideSwitch(which) {
    const inner = document.getElementById('guide-body-inner');
    if (!inner) return;
    const isOverview = which === 'overview';
    inner.innerHTML = isOverview ? (window._guideOverviewHtml || '') : (window._guideFeatureHtml || '');
    inner.scrollTop = 0;
    const ft = document.getElementById('guide-tab-feature');
    const ot = document.getElementById('guide-tab-overview');
    if (ft) ft.classList.toggle('guide-tab-active', !isOverview);
    if (ot) ot.classList.toggle('guide-tab-active', isOverview);
}

function showGuide() {
    const tab = _activeTab || 'analyzer';
    const guide = GUIDES[tab] || GUIDES[tab === 'main' ? 'analyzer' : 'screener_default'];
    if (!guide) return;
    const overview = GUIDES.overview;

    // Remove existing guide modal
    const existing = document.getElementById('guide-modal-overlay');
    if (existing) existing.remove();

    // Stash both views so the header tabs can swap between them without a rebuild.
    window._guideFeatureHtml = _renderGuideBody(guide);
    window._guideOverviewHtml = overview ? _renderGuideBody(overview) : '';

    const tabBtn = (id, label, active) =>
        `<button id="${id}" class="guide-tab${active ? ' guide-tab-active' : ''}" onclick="guideSwitch('${id === 'guide-tab-overview' ? 'overview' : 'feature'}')" ` +
        `style="background:${active ? 'var(--accent-primary)' : 'transparent'};color:${active ? '#fff' : 'var(--text-muted,#8a90a0)'};` +
        `border:1px solid var(--border,#333);border-radius:999px;padding:5px 14px;font-size:12px;font-weight:600;cursor:pointer;margin-right:8px">${label}</button>`;

    const overlay = document.createElement('div');
    overlay.id = 'guide-modal-overlay';
    overlay.className = 'guide-overlay';
    overlay.innerHTML = `
        <div class="guide-modal">
            <div class="guide-header">
                <div class="guide-tabs">
                    ${tabBtn('guide-tab-feature', '📖 This Feature', true)}
                    ${overview ? tabBtn('guide-tab-overview', '🗺️ How It All Works', false) : ''}
                </div>
                <button class="guide-close" onclick="document.getElementById('guide-modal-overlay').remove()">&times;</button>
            </div>
            <div class="guide-body"><div id="guide-body-inner">${window._guideFeatureHtml}</div></div>
        </div>
    `;
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
}

// ─── Hash-based tab persistence ─────
window.addEventListener('hashchange', () => {
    const tab = location.hash.replace('#', '') || 'analyzer';
    if (tab !== _activeTab) switchTab(tab, true);
});

// ─── Shared Narrative Dashboard Renderer ─────────────────────
// Used by both crypto and stock bot dashboards (identical design, no disparity).
// Follows Tufte minimalism + Cole Knaflic narrative arc.

function _fmtPnl(val) {
    const sign = val >= 0 ? '+' : '';
    return sign + '$' + val.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function renderNarrativeDashboard(container, d, assetFilter) {
    // assetFilter: 'all', 'crypto', or 'stock' — highlights the relevant asset type
    if (!container || !d) return;
    if (d.error) { container.innerHTML = `<div class="bot-empty">${d.error}</div>`; return; }

    const wr = d.win_rate || 0;
    const netPnl = d.net_pnl || (d.summary.all.pnl - (d.total_fees || 0));
    const totalFees = d.total_fees || 0;
    const ytd = d.summary.ytd || {pnl: 0, trades: 0, wins: 0, fees: 0};
    const goalPct = d.daily_goal.pct || 0;

    // 5-color palette
    const c = { profit: '#00c896', loss: '#ff4757', warn: '#ff8c42', neutral: '#636b7e', accent: '#4f8aff' };

    // Key headline
    let headline = '', headlineColor = c.neutral;
    const label = assetFilter === 'crypto' ? 'Crypto' : assetFilter === 'stock' ? 'Stock' : 'Combined';
    if (d.total_trades === 0) {
        headline = `No ${label.toLowerCase()} trades yet`;
    } else if (netPnl > 0 && wr >= 50) {
        headline = `${label}: Net positive ${_fmtPnl(netPnl)} across ${d.total_trades} trades`;
        headlineColor = c.profit;
    } else if (netPnl > 0 && wr < 50) {
        headline = `${label}: Profitable despite ${wr}% win rate — strong R:R`;
        headlineColor = c.warn;
    } else if (netPnl <= 0 && totalFees > Math.abs(netPnl) * 0.3) {
        headline = `${label}: Fees eroding gains — ${_fmtPnl(-totalFees)} fees on ${_fmtPnl(d.summary.all.pnl)} gross`;
        headlineColor = c.loss;
    } else if (netPnl <= 0) {
        headline = `${label}: Net ${_fmtPnl(netPnl)} — review strategies below`;
        headlineColor = c.loss;
    }

    let html = '';

    // ═══ ACT 1: SETUP — KPIs ═══
    html += `
    <div class="narr-headline" style="color:${headlineColor}">${headline}</div>
    <div class="narr-kpi-row">
        <div class="narr-kpi narr-kpi-hero">
            <span class="narr-kpi-label">Net P&L</span>
            <span class="narr-kpi-value" style="color:${netPnl >= 0 ? c.profit : c.loss}">${_fmtPnl(netPnl)}</span>
            <span class="narr-kpi-ctx">${d.total_trades} trades &middot; ${_fmtPnl(-totalFees)} fees</span>
        </div>
        <div class="narr-kpi">
            <span class="narr-kpi-label">Win Rate</span>
            <span class="narr-kpi-value" style="color:${wr >= 55 ? c.profit : wr >= 45 ? c.warn : c.loss}">${wr}%</span>
            <span class="narr-kpi-ctx">${d.summary.all.wins}W / ${d.total_trades - d.summary.all.wins}L</span>
        </div>
        <div class="narr-kpi">
            <span class="narr-kpi-label">Avg / Trade</span>
            <span class="narr-kpi-value" style="color:${d.avg_pnl >= 0 ? c.profit : c.loss}">${_fmtPnl(d.avg_pnl)}</span>
            <span class="narr-kpi-ctx">after fees</span>
        </div>
        <div class="narr-kpi">
            <span class="narr-kpi-label">Daily Goal</span>
            <span class="narr-kpi-value" style="color:${goalPct >= 100 ? c.profit : goalPct >= 50 ? c.warn : c.neutral}">${goalPct}%</span>
            <span class="narr-kpi-ctx">${_fmtPnl(d.daily_goal.actual)} / ${_fmtPnl(d.daily_goal.target)}</span>
        </div>
        <div class="narr-kpi narr-kpi-tax" onclick="loadTaxEstimate()" style="cursor:pointer">
            <span class="narr-kpi-label">Est. Tax</span>
            <span class="narr-kpi-value" id="dash-tax-value" style="color:${c.warn}">--</span>
            <span class="narr-kpi-ctx" id="dash-tax-sub">tap to calc</span>
        </div>
    </div>`;

    // ═══ ACT 2: TENSION — P&L by Period (diverging bars) ═══
    const periods = [
        {label: 'Today', ...d.summary.day},
        {label: '1W', ...d.summary.week},
        {label: '1M', ...d.summary.month},
        {label: 'YTD', ...ytd},
        {label: '1Y', ...d.summary.year},
    ];
    const maxAbsPnl = Math.max(...periods.map(p => Math.abs(p.pnl || 0)), 1);

    html += `<div class="narr-section-label">P&L by Period</div><div class="narr-bars">`;
    periods.forEach(p => {
        const pnl = p.pnl || 0;
        const pct = Math.abs(pnl) / maxAbsPnl * 100;
        const fees = p.fees || 0;
        const trades = p.trades || 0;
        const wins = p.wins || 0;
        const winRate = trades > 0 ? Math.round(wins / trades * 100) : 0;
        const barColor = pnl >= 0 ? c.profit : c.loss;
        const dir = pnl >= 0 ? 'right' : 'left';
        html += `<div class="narr-bar-row">
            <span class="narr-bar-label">${p.label}</span>
            <div class="narr-bar-track">
                <div class="narr-bar-zero"></div>
                <div class="narr-bar-fill narr-bar-${dir}" style="width:${Math.max(pct / 2, 1)}%;background:${barColor}"></div>
            </div>
            <span class="narr-bar-value" style="color:${barColor}">${_fmtPnl(pnl)}</span>
            <span class="narr-bar-ctx">${trades}t ${winRate}%w${fees > 0 ? ' -$' + fees.toFixed(0) + 'f' : ''}</span>
        </div>`;
    });
    html += `</div>`;

    // ═══ ACT 3: INSIGHT — Strategy + Asset ranking ═══
    if (d.strategies && d.strategies.length > 0) {
        const sorted = [...d.strategies].sort((a, b) => (b.total_pnl - (b.total_fees||0)) - (a.total_pnl - (a.total_fees||0)));
        const maxStratPnl = Math.max(...sorted.map(s => Math.abs(s.total_pnl - (s.total_fees||0))), 1);
        html += `<div class="narr-section-label">Strategy Performance <span style="color:${c.neutral};font-weight:400">ranked by net P&L</span></div><div class="narr-strats">`;
        sorted.forEach(s => {
            const fees = s.total_fees || 0;
            const net = s.total_pnl - fees;
            const pct = Math.abs(net) / maxStratPnl * 100;
            const barColor = net >= 0 ? c.profit : c.loss;
            const wrCls = s.win_rate >= 55 ? c.profit : s.win_rate < 40 ? c.loss : c.warn;
            html += `<div class="narr-strat-row">
                <div class="narr-strat-name">${s.name}</div>
                <div class="narr-strat-bar-track"><div class="narr-strat-bar" style="width:${Math.max(pct, 3)}%;background:${barColor}"></div></div>
                <div class="narr-strat-stats">
                    <span style="color:${barColor};font-weight:700">${_fmtPnl(net)}</span>
                    <span style="color:${wrCls}">${s.win_rate}%</span>
                    <span style="color:${c.neutral}">${s.trades}t</span>
                </div>
            </div>`;
        });
        html += `</div>`;
    }

    if (d.top_assets && d.top_assets.length > 0) {
        html += `<div class="narr-section-label">Top Assets</div><div class="narr-assets">`;
        d.top_assets.forEach((a, i) => {
            const net = a.total_pnl - (a.total_fees || 0);
            const wrCls = a.win_rate >= 55 ? c.profit : a.win_rate < 40 ? c.loss : c.warn;
            html += `<div class="narr-asset-row">
                <span class="narr-asset-rank">${i + 1}</span>
                <span class="narr-asset-name">${a.coin} <span style="color:${c.neutral};font-size:10px">${a.asset_type}</span></span>
                <span class="narr-asset-wr" style="color:${wrCls}">${a.win_rate}%</span>
                <span class="narr-asset-pnl" style="color:${net >= 0 ? c.profit : c.loss}">${_fmtPnl(net)}</span>
                <span class="narr-asset-ctx">${a.trades} trades</span>
            </div>`;
        });
        html += `</div>`;
    }

    // ═══ ACT 4: RESOLUTION — Extremes + Comparison ═══
    if (d.best_trade || d.worst_trade) {
        html += `<div class="narr-section-label">Extremes</div><div class="narr-extremes">`;
        if (d.best_trade) {
            html += `<div class="narr-extreme narr-best">
                <span class="narr-ext-label">Best</span>
                <span class="narr-ext-value" style="color:${c.profit}">${_fmtPnl(d.best_trade.pnl)}</span>
                <span class="narr-ext-detail">${d.best_trade.coin} ${d.best_trade.side} &middot; ${d.best_trade.strategy}</span>
            </div>`;
        }
        if (d.worst_trade) {
            html += `<div class="narr-extreme narr-worst">
                <span class="narr-ext-label">Worst</span>
                <span class="narr-ext-value" style="color:${c.loss}">${_fmtPnl(d.worst_trade.pnl)}</span>
                <span class="narr-ext-detail">${d.worst_trade.coin} ${d.worst_trade.side} &middot; ${d.worst_trade.strategy}</span>
            </div>`;
        }
        html += `</div>`;
    }

    if (d.by_asset && Object.keys(d.by_asset).length > 1) {
        html += `<div class="narr-section-label">Crypto vs Stock</div><div class="narr-asset-compare">`;
        for (const [type, stats] of Object.entries(d.by_asset)) {
            const fees = stats.total_fees || 0;
            const net = stats.total_pnl - fees;
            html += `<div class="narr-compare-card">
                <div class="narr-compare-type">${type === 'crypto' ? 'Crypto' : 'Stocks'}</div>
                <div class="narr-compare-pnl" style="color:${net >= 0 ? c.profit : c.loss}">${_fmtPnl(net)}</div>
                <div class="narr-compare-ctx">${stats.trades}t &middot; ${stats.win_rate}% win &middot; ${_fmtPnl(-fees)} fees</div>
            </div>`;
        }
        html += `</div>`;
    }

    html += `<div class="narr-footer">Data: closed ${assetFilter !== 'all' ? assetFilter + ' ' : ''}trades &middot; Updated ${new Date().toLocaleTimeString()} &middot; Fees estimated</div>`;

    container.innerHTML = html;
}

/* ─── Theme switcher ─────────────────────────────────────────
   Themes live in core.css as html[data-theme="..."] blocks. Persisted in
   localStorage('tw_theme') and applied before paint by the inline script in
   index.html. Selecting a theme only flips data-theme — every styled element
   follows via CSS custom properties (canonical tokens + bridge aliases). */
var TW_THEMES = ['aurora', 'midnight', 'ember', 'nord', 'terminal', 'daylight', 'sandstone'];

function setTheme(t) {
    if (TW_THEMES.indexOf(t) === -1) t = 'aurora';
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('tw_theme', t); } catch (e) {}
    var sel = document.getElementById('theme-select');
    if (sel && sel.value !== t) sel.value = t;
    // legacy button row (if still present anywhere)
    document.querySelectorAll('.theme-switch-btn').forEach(function (b) {
        b.classList.toggle('active', b.dataset.theme === t);
    });
}
document.addEventListener('DOMContentLoaded', function () {
    var t = document.documentElement.getAttribute('data-theme') || 'aurora';
    var sel = document.getElementById('theme-select');
    if (sel) sel.value = t;
    document.querySelectorAll('.theme-switch-btn').forEach(function (b) {
        b.classList.toggle('active', b.dataset.theme === t);
    });
});
