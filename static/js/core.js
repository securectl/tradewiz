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

    // Load current mood + history in parallel
    const [moodResp, histResp] = await Promise.allSettled([
        fetch('/api/trump-mood' + (force ? '?force=1' : '')),
        fetch('/api/trump/history?days=' + days),
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
        renderTrumpCurrentMood(mood);
        renderTrumpSignals(mood);
        renderTrumpNotable(mood);
    }

    // Render history chart + table
    if (histResp.status === 'fulfilled' && histResp.value.ok) {
        const history = await histResp.value.json();
        renderTrumpChart(history);
        renderTrumpHistoryTable(history);
    }

    // Auto-load prediction if not forced (cached)
    loadTrumpPrediction(false);
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

function renderTrumpChart(history) {
    const el = document.getElementById('trump-chart');
    if (!el || !history.length) {
        if (el) el.innerHTML = '<div style="text-align:center;color:var(--text-secondary);padding:40px;font-size:12px;">No history data yet. Mood snapshots are recorded every hour.</div>';
        return;
    }

    // Sort oldest first for chart
    const sorted = [...history].reverse();
    const maxAbs = Math.max(...sorted.map(h => Math.abs(h.mood)), 1);
    const chartW = el.clientWidth || 600;
    const chartH = 200;
    const barW = Math.max(4, Math.min(20, (chartW - 40) / sorted.length - 2));
    const midY = chartH / 2;

    let barsHtml = '';
    sorted.forEach((h, i) => {
        const x = 30 + i * (barW + 2);
        const barH = (Math.abs(h.mood) / Math.max(maxAbs, 1)) * (midY - 10);
        const y = h.mood >= 0 ? midY - barH : midY;
        const color = h.mood >= 30 ? '#00c896' : h.mood >= 10 ? '#8bc34a' : h.mood >= -10 ? '#ffc837' : h.mood >= -30 ? '#ff8c42' : '#ff4757';
        const date = h.created_at ? new Date(h.created_at).toLocaleDateString('en-US', {month:'short',day:'numeric'}) : '';
        const time = h.created_at ? new Date(h.created_at).toLocaleTimeString('en-US', {hour:'2-digit',minute:'2-digit'}) : '';
        barsHtml += `<rect x="${x}" y="${y}" width="${barW}" height="${Math.max(barH, 1)}" rx="2" fill="${color}" opacity="0.85">
            <title>${date} ${time}: ${h.mood} (${h.label})</title></rect>`;
    });

    el.innerHTML = `<svg width="100%" height="${chartH}" viewBox="0 0 ${chartW} ${chartH}" preserveAspectRatio="none">
        <!-- Zero line -->
        <line x1="25" y1="${midY}" x2="${chartW}" y2="${midY}" stroke="var(--border-color)" stroke-width="1" stroke-dasharray="4,4"/>
        <text x="2" y="${midY + 3}" fill="var(--text-secondary)" font-size="9">0</text>
        <text x="2" y="12" fill="#00c896" font-size="9">+${Math.round(maxAbs)}</text>
        <text x="2" y="${chartH - 4}" fill="#ff4757" font-size="9">-${Math.round(maxAbs)}</text>
        ${barsHtml}
    </svg>`;
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
    const trumpContent = document.getElementById('trump-content');

    mainContent.style.display = 'none';
    if (qullamaggieContent) qullamaggieContent.style.display = 'none';
    trackerContent.style.display = 'none';
    screenerContent.style.display = 'none';
    statusContent.style.display = 'none';
    if (researchContent) researchContent.style.display = 'none';
    if (finskillsContent) finskillsContent.style.display = 'none';
    if (ipoContent) ipoContent.style.display = 'none';
    if (predictionsContent) predictionsContent.style.display = 'none';
    if (congressContent) congressContent.style.display = 'none';
    if (trumpContent) trumpContent.style.display = 'none';
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

    if (tab === 'qullamaggie') {
        qullamaggieContent.style.display = 'flex';
    } else if (tab === 'tracker') {
        trackerContent.style.display = 'grid';
        loadJournal();
        loadGoals();
    } else if (tab === 'screener') {
        screenerContent.style.display = 'flex';
    } else if (tab === 'ipos') {
        ipoContent.style.display = 'flex';
    } else if (tab === 'predictions') {
        predictionsContent.style.display = 'block';
        loadPredictionMarkets(false);
    } else if (tab === 'congress') {
        congressContent.style.display = 'block';
    } else if (tab === 'trump') {
        trumpContent.style.display = 'block';
        loadTrumpTab();
    } else if (tab === 'status') {
        statusContent.style.display = 'flex';
        loadStatus();
        statusRefreshInterval = setInterval(loadStatus, 60000);
    } else if (tab === 'research') {
        researchContent.style.display = 'flex';
        loadSkillCatalog();
        loadSkillJobs();
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
        loadAdminUsers();
        loadAdminInvites();
        loadAdminConfig();
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
    screener_default: {
        title: 'Feature Guide',
        sections: [
            { heading: 'Welcome', body: 'Select a tab to get started. Click the Guide button anytime to learn how to use the current feature.' },
        ],
    },
};

function showGuide() {
    const tab = _activeTab || 'analyzer';
    const guide = GUIDES[tab] || GUIDES[tab === 'main' ? 'analyzer' : 'screener_default'];
    if (!guide) return;

    // Remove existing guide modal
    const existing = document.getElementById('guide-modal-overlay');
    if (existing) existing.remove();

    let contentHtml = '';
    guide.sections.forEach(s => {
        contentHtml += `<div class="guide-section">
            <h4 class="guide-section-heading">${s.heading}</h4>
            <div class="guide-section-body">${s.body}</div>
        </div>`;
    });

    const overlay = document.createElement('div');
    overlay.id = 'guide-modal-overlay';
    overlay.className = 'guide-overlay';
    overlay.innerHTML = `
        <div class="guide-modal">
            <div class="guide-header">
                <h3>${guide.title}</h3>
                <button class="guide-close" onclick="document.getElementById('guide-modal-overlay').remove()">&times;</button>
            </div>
            <div class="guide-body">${contentHtml}</div>
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
