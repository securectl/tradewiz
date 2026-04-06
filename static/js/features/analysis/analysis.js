// ─── Chart Setup ─────────────────────────────────────────────

function createChart() {
    const container = document.getElementById('chart-container');

    // Remove welcome/error screen
    const welcome = document.getElementById('welcome-screen');
    if (welcome) welcome.remove();

    // Destroy existing chart
    if (chart) {
        chart.remove();
        chart = null;
        trendlineOverlays = [];
    }

    chart = LightweightCharts.createChart(container, {
        layout: {
            background: { type: 'solid', color: '#131722' },
            textColor: '#d1d4dc',
        },
        grid: {
            vertLines: { color: '#1e222d' },
            horzLines: { color: '#1e222d' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: '#363a45',
            scaleMargins: { top: 0.1, bottom: 0.15 },
            autoScale: true,
        },
        timeScale: {
            borderColor: '#363a45',
            timeVisible: true,
            rightOffset: 12,
            barSpacing: 8,
            minBarSpacing: 4,
            fixLeftEdge: false,
            fixRightEdge: false,
        },
        handleScroll: { vertTouchDrag: false },
    });

    // Candlestick series
    candleSeries = chart.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
    });

    // Volume series
    volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
        scaleMargins: { top: 0.85, bottom: 0 },
    });

    // Resize handler
    const resizeObserver = new ResizeObserver(entries => {
        if (!chart) return;
        for (const entry of entries) {
            const { width, height } = entry.contentRect;
            if (width > 0 && height > 0) {
                chart.applyOptions({ width, height });
            }
        }
    });
    resizeObserver.observe(container);
}

// ─── Render Analysis ─────────────────────────────────────────

function renderAnalysis(data) {
    currentAnalysis = data;

    // Show chart header + toolbar
    document.getElementById('chart-header').style.display = 'flex';
    document.getElementById('chart-toolbar').style.display = 'flex';
    document.getElementById('right-panel').style.display = 'flex';

    // Header info
    document.getElementById('ticker-title').textContent = data.ticker;
    document.getElementById('ticker-name').textContent =
        `${data.info.name} · ${data.info.exchange} · ${data.interval}`;

    const priceEl = document.getElementById('current-price');
    const changeEl = document.getElementById('price-change');
    priceEl.textContent = `$${data.current_price}`;
    priceEl.className = `current-price ${data.change >= 0 ? 'price-up' : 'price-down'}`;
    const sign = data.change >= 0 ? '+' : '';
    changeEl.textContent = `${sign}${data.change} (${sign}${data.change_pct}%)`;
    changeEl.className = `price-change ${data.change >= 0 ? 'price-up' : 'price-down'}`;

    // Create chart and set data
    createChart();
    candleSeries.setData(data.ohlcv);
    volumeSeries.setData(data.volumes);

    // Add moving averages
    addMovingAverages(data.moving_averages);

    // Draw pattern overlays
    if (data.pattern) {
        drawPatternOverlay(data);
    }

    // Right panel
    renderBreakoutStatus(data.breakout_status);
    renderPatternInfo(data.pattern);
    renderTrendlineTests(data.trendline_tests || []);
    renderCandlePatterns(data.candle_patterns || []);
    renderTradePlan(data.trade_plan);
    renderIndicators(data.indicators);
    renderFinancials(data.fundamentals);
    resetAIValidation();
    resetPrediction();
    resetEarnings();

    // Auto-fit chart: show pattern area with S/R context, not arbitrary 80 bars
    autoFitChart(data);
}

function autoFitChart(data) {
    const ohlcvLen = data.ohlcv.length;
    if (ohlcvLen === 0) return;

    // Determine the ideal visible range based on pattern and S/R levels
    let startIdx = 0;
    const endIdx = ohlcvLen - 1;

    if (data.pattern && data.pattern.pattern_start_idx != null) {
        // Show from pattern start with 20% padding before it for context
        const patStart = data.pattern.pattern_start_idx;
        const patternBars = endIdx - patStart;
        const padding = Math.max(Math.floor(patternBars * 0.3), 15);
        startIdx = Math.max(0, patStart - padding);
    } else {
        // No pattern: show last 60-100 bars depending on data size
        const barsToShow = Math.min(ohlcvLen, Math.max(60, Math.floor(ohlcvLen * 0.4)));
        startIdx = ohlcvLen - barsToShow;
    }

    // If trade plan has S/R levels, ensure they're visible by checking
    // if swing lows supporting the stop loss are within view
    if (data.pattern && data.pattern.swing_lows) {
        const swingLows = data.pattern.swing_lows;
        // Include at least the last 4-5 swing lows in the view
        if (swingLows.length >= 4) {
            const fourthFromEnd = swingLows[Math.max(0, swingLows.length - 5)];
            const padding = 10;
            startIdx = Math.min(startIdx, Math.max(0, fourthFromEnd - padding));
        }
    }
    // Also ensure swing highs (resistance) are visible
    if (data.pattern && data.pattern.swing_highs) {
        const swingHighs = data.pattern.swing_highs;
        if (swingHighs.length >= 4) {
            const fourthFromEnd = swingHighs[Math.max(0, swingHighs.length - 5)];
            const padding = 10;
            startIdx = Math.min(startIdx, Math.max(0, fourthFromEnd - padding));
        }
    }

    // Ensure minimum 40 bars visible, max ~150 to avoid compression
    const visibleBars = endIdx - startIdx;
    if (visibleBars > 150) {
        startIdx = endIdx - 150;
    } else if (visibleBars < 40 && ohlcvLen >= 40) {
        startIdx = Math.max(0, endIdx - 40);
    }

    const from = data.ohlcv[startIdx].time;
    const to = data.ohlcv[endIdx].time;

    // Set range with right offset for breathing room
    chart.timeScale().setVisibleRange({ from, to });
    chart.applyOptions({
        rightPriceScale: {
            autoScale: true,
            scaleMargins: { top: 0.08, bottom: 0.12 },
        },
    });
}

const MA_CONFIG = {
    ema_9:   { color: '#ffb74d',                   title: 'EMA 9',   style: 'solid',  checkboxId: 'ma-ema9' },
    sma_20:  { color: '#ce93d8',                   title: 'SMA 20',  style: 'solid',  checkboxId: 'ma-sma20' },
    sma_50:  { color: '#42a5f5',                   title: 'SMA 50',  style: 'dashed', checkboxId: 'ma-sma50' },
    sma_100: { color: '#66bb6a',                   title: 'SMA 100', style: 'dashed', checkboxId: 'ma-sma100' },
    sma_200: { color: '#ef5350',                   title: 'SMA 200', style: 'dashed', checkboxId: 'ma-sma200' },
};

let storedMaData = null;

function addMovingAverages(maData) {
    if (!maData) return;
    storedMaData = maData;

    for (const [key, config] of Object.entries(MA_CONFIG)) {
        const data = maData[key];
        if (!data || data.length === 0) continue;

        const checkbox = document.getElementById(config.checkboxId);
        const visible = checkbox ? checkbox.checked : (key === 'ema_9' || key === 'ema_21' || key === 'sma_50');

        const line = chart.addLineSeries({
            color: config.color,
            lineWidth: 1,
            lineStyle: config.style === 'dashed' ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
            title: config.title,
            priceLineVisible: false,
            lastValueVisible: false,
            visible: visible,
        });
        line.setData(data);
        maLines[key] = line;
    }
}

function toggleMA(key) {
    if (maLines[key]) {
        const checkbox = document.getElementById(MA_CONFIG[key].checkboxId);
        maLines[key].applyOptions({ visible: checkbox.checked });
    }
}

function toggleVolume() {
    if (volumeSeries) {
        const checkbox = document.getElementById('toggle-volume');
        volumeSeries.applyOptions({ visible: checkbox.checked });
    }
}

function drawPatternOverlay(data) {
    const pattern = data.pattern;
    if (!pattern) return;

    // Upper trendline (resistance)
    const upperPoints = pattern.upper_trendline.points.filter(p => p.time);
    if (upperPoints.length >= 2) {
        const upperLine = chart.addLineSeries({
            color: '#2962ff',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        const step = Math.max(1, Math.floor(upperPoints.length / 30));
        const sampled = upperPoints.filter((_, i) => i % step === 0 || i === upperPoints.length - 1);
        upperLine.setData(sampled.map(p => ({ time: p.time, value: p.value })));
        trendlineOverlays.push(upperLine);
    }

    // Lower trendline (support)
    const lowerPoints = pattern.lower_trendline.points.filter(p => p.time);
    if (lowerPoints.length >= 2) {
        const lowerLine = chart.addLineSeries({
            color: '#2962ff',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        const step = Math.max(1, Math.floor(lowerPoints.length / 30));
        const sampled = lowerPoints.filter((_, i) => i % step === 0 || i === lowerPoints.length - 1);
        lowerLine.setData(sampled.map(p => ({ time: p.time, value: p.value })));
        trendlineOverlays.push(lowerLine);
    }

    // Breakout level horizontal line
    const lastTime = data.ohlcv[data.ohlcv.length - 1].time;
    const startIdx = Math.max(0, pattern.pattern_start_idx);
    const firstRelevant = startIdx < data.ohlcv.length ? data.ohlcv[startIdx].time : data.ohlcv[0].time;

    const breakoutLine = chart.addLineSeries({
        color: 'rgba(41, 98, 255, 0.7)',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'Breakout',
        crosshairMarkerVisible: false,
    });
    breakoutLine.setData([
        { time: firstRelevant, value: pattern.breakout_level },
        { time: lastTime, value: pattern.breakout_level },
    ]);
    trendlineOverlays.push(breakoutLine);

    // TP and Stop lines from trade plan
    if (data.trade_plan) {
        const plan = data.trade_plan;

        // Helper to add a horizontal price line
        const addPriceLine = (value, color, title, lineStyle) => {
            const line = chart.addLineSeries({
                color: color,
                lineWidth: 1,
                lineStyle: lineStyle || LightweightCharts.LineStyle.Dashed,
                priceLineVisible: false,
                lastValueVisible: true,
                title: title,
                crosshairMarkerVisible: false,
            });
            line.setData([
                { time: firstRelevant, value: value },
                { time: lastTime, value: value },
            ]);
            trendlineOverlays.push(line);
        };

        // Entry price
        addPriceLine(plan.entry_price, 'rgba(41, 98, 255, 0.9)', 'Entry', LightweightCharts.LineStyle.Dotted);

        // TP1 / TP2 / TP3
        addPriceLine(plan.tp1, 'rgba(38, 166, 154, 0.6)', `TP1 $${plan.tp1}`);
        addPriceLine(plan.tp2, 'rgba(38, 166, 154, 0.8)', `TP2 $${plan.tp2}`);
        addPriceLine(plan.tp3, 'rgba(38, 166, 154, 1.0)', `TP3 $${plan.tp3}`);

        // Stop loss
        addPriceLine(plan.stop_loss, 'rgba(239, 83, 80, 0.9)', `Stop $${plan.stop_loss}`);
    }

    // Swing point markers + trendline test markers
    const markers = [];
    if (pattern.swing_highs_ts) {
        pattern.swing_highs_ts.forEach(pt => {
            markers.push({
                time: pt.time,
                position: 'aboveBar',
                color: '#ef5350',
                shape: 'arrowDown',
                size: 0.5,
            });
        });
    }
    if (pattern.swing_lows_ts) {
        pattern.swing_lows_ts.forEach(pt => {
            markers.push({
                time: pt.time,
                position: 'belowBar',
                color: '#26a69a',
                shape: 'arrowUp',
                size: 0.5,
            });
        });
    }

    // Trendline test markers (circles like expert draws)
    if (data.trendline_tests && data.trendline_tests.length > 0) {
        data.trendline_tests.forEach(test => {
            if (!test.time) return;
            const isSupport = test.type === 'support_test';
            markers.push({
                time: test.time,
                position: isSupport ? 'belowBar' : 'aboveBar',
                color: isSupport ? '#ff9800' : '#e040fb',
                shape: 'circle',
                size: 2,
                text: isSupport ? 'Support Test' : 'Resistance Test',
            });
        });
    }

    if (markers.length > 0) {
        markers.sort((a, b) => a.time - b.time);
        candleSeries.setMarkers(markers);
    }
}

// ─── Right Panel Renderers ───────────────────────────────────

function renderBreakoutStatus(status) {
    const box = document.getElementById('breakout-status-box');
    const dir = status.direction || 'no-pattern';
    const cssClass = status.status === 'no_pattern' ? 'no-pattern' : dir;

    // Breakout confirmation details (multi-candle rule from Trend Breaker strategy)
    let confirmHtml = '';
    if (status.status === 'breakout_up' || status.status === 'breakdown') {
        const candles = status.candles_above || status.candles_below || 0;
        const volOk = status.volume_confirmed;
        const candleLabel = candles >= 3 ? '3/3' : `${candles}/3`;
        confirmHtml = `
            <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">
                <span style="font-size:10px; padding:2px 8px; border-radius:10px;
                    background:${candles >= 3 ? 'rgba(38,166,154,0.15)' : 'rgba(255,152,0,0.15)'};
                    color:${candles >= 3 ? '#26a69a' : '#ff9800'};">Candles: ${candleLabel}</span>
                <span style="font-size:10px; padding:2px 8px; border-radius:10px;
                    background:${volOk ? 'rgba(38,166,154,0.15)' : 'rgba(255,152,0,0.15)'};
                    color:${volOk ? '#26a69a' : '#ff9800'};">Volume: ${volOk ? 'Confirmed' : 'Pending'}</span>
            </div>`;
    }

    box.innerHTML = `
        <div class="breakout-status ${cssClass}">
            <div class="breakout-label">${status.status === 'no_pattern' ? 'No Pattern' :
                status.status === 'breakout_up' ? 'BREAKOUT' :
                status.status === 'breakdown' ? 'BREAKDOWN' :
                status.urgency || 'CONSOLIDATING'}</div>
            <div class="breakout-message">${status.message}</div>
            ${confirmHtml}
            ${status.compression_pct !== undefined ? `
                <div class="risk-meter" style="margin-top:10px;">
                    <div class="risk-meter-fill" style="width:${Math.min(100, (10 - status.compression_pct) * 12)}%;
                        background: ${status.compression_pct < 3 ? '#ef5350' : status.compression_pct < 6 ? '#ff9800' : '#26a69a'};"></div>
                </div>
                <div style="font-size:10px; color:#787b86; margin-top:4px;">Compression: ${status.compression_pct}%</div>
            ` : ''}
        </div>
    `;
}

function renderPatternInfo(pattern) {
    const container = document.getElementById('pattern-info');
    if (!pattern) {
        container.innerHTML = '<div style="color:#787b86; font-size:13px;">No clear pattern detected. Try a different timeframe or period.</div>';
        return;
    }

    const typeLabels = {
        'symmetrical_triangle': 'Symmetrical Triangle',
        'ascending_triangle': 'Ascending Triangle',
        'descending_triangle': 'Descending Triangle',
        'falling_wedge': 'Falling Wedge',
        'rising_wedge': 'Rising Wedge',
        'pennant': 'Pennant',
    };

    container.innerHTML = `
        <div class="pattern-badge">${typeLabels[pattern.type] || pattern.type}</div>
        <div class="score-bar">
            <div style="font-size:11px; color:#787b86;">Confidence</div>
            <div class="score-track">
                <div class="score-fill" style="width:${pattern.score}%;
                    background: ${pattern.score >= 60 ? '#26a69a' : pattern.score >= 40 ? '#ff9800' : '#ef5350'};"></div>
            </div>
            <div class="score-value">${pattern.score}%</div>
        </div>
        <div style="margin-top:12px;">
            <div class="indicator-row">
                <span class="indicator-label">Breakout Level</span>
                <span class="indicator-value" style="color:#2962ff;">$${pattern.breakout_level}</span>
            </div>
            <div class="indicator-row">
                <span class="indicator-label">Support Level</span>
                <span class="indicator-value">$${pattern.support_level}</span>
            </div>
            <div class="indicator-row">
                <span class="indicator-label">Price Target</span>
                <span class="indicator-value" style="color:#26a69a;">$${pattern.price_target}</span>
            </div>
            <div class="indicator-row">
                <span class="indicator-label">Stop Loss</span>
                <span class="indicator-value" style="color:#ef5350;">$${pattern.stop_loss}</span>
            </div>
            <div class="indicator-row">
                <span class="indicator-label">Pattern Height</span>
                <span class="indicator-value">$${pattern.pattern_height}</span>
            </div>
            <div class="indicator-row">
                <span class="indicator-label">Risk/Reward</span>
                <span class="indicator-value" style="color:${pattern.risk_reward_ratio >= 2 ? '#26a69a' : '#ff9800'};">
                    1:${pattern.risk_reward_ratio}</span>
            </div>
        </div>
    `;
}

function renderTrendlineTests(tests) {
    let container = document.getElementById('trendline-tests-box');

    // Create section if it doesn't exist
    if (!container) {
        const section = document.createElement('div');
        section.className = 'panel-section';
        section.id = 'section-trendline-tests';
        section.innerHTML = `
            <div class="panel-section-title">Trendline Tests</div>
            <div id="trendline-tests-box"></div>
        `;
        // Insert after pattern section
        const patternSection = document.getElementById('section-pattern');
        if (patternSection && patternSection.nextSibling) {
            patternSection.parentNode.insertBefore(section, patternSection.nextSibling);
        } else {
            document.getElementById('right-panel').appendChild(section);
        }
        container = document.getElementById('trendline-tests-box');
    }

    if (!tests || tests.length === 0) {
        container.innerHTML = '<div style="color:#787b86; font-size:12px;">No significant trendline tests detected.</div>';
        return;
    }

    let html = '';
    tests.forEach((test, i) => {
        const isSupport = test.type === 'support_test';
        const color = isSupport ? '#ff9800' : '#e040fb';
        const heldColor = test.held ? '#26a69a' : '#ef5350';
        const heldText = test.held ? (isSupport ? 'Support Held' : 'Resistance Held') :
                                     (isSupport ? 'Support Broken' : 'Resistance Broken');

        // Strength dots
        let strengthDots = '';
        for (let s = 0; s < 5; s++) {
            strengthDots += `<span style="color:${s < test.strength ? color : '#363a45'}; font-size:8px;">&#9679;</span>`;
        }

        html += `
            <div class="trendline-test-item" style="padding:8px 10px; margin-bottom:6px; background:var(--bg-primary); border-radius:6px; border-left:3px solid ${color};">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                    <span style="font-size:12px; font-weight:600; color:${color};">${test.label}</span>
                    <span style="font-size:10px; color:#787b86;">${test.date || ''}</span>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:11px; color:var(--text-primary);">Low $${test.price} → Line $${test.trendline_value}</span>
                    <span style="font-size:10px; color:${heldColor}; font-weight:600;">${heldText}</span>
                </div>
                <div style="margin-top:4px; display:flex; gap:8px; align-items:center;">
                    <span style="font-size:10px; color:#787b86;">Strength: ${strengthDots}</span>
                    <span style="font-size:10px; color:#787b86;">Vol: ${test.volume_ratio}x</span>
                    ${test.candle_change_pct > 0 ? `<span style="font-size:10px; color:#ef5350;">-${test.candle_change_pct}%</span>` : ''}
                </div>
                <div style="margin-top:4px; font-size:10px; color:#787b86; line-height:1.4;">
                    ${test.reasons.join(' · ')}
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

function renderCandlePatterns(patterns) {
    let container = document.getElementById('candle-patterns-box');

    if (!container) {
        const section = document.createElement('div');
        section.className = 'panel-section';
        section.id = 'section-candle-patterns';
        section.innerHTML = `
            <div class="panel-section-title">Candlestick Signals</div>
            <div id="candle-patterns-box"></div>
        `;
        const testSection = document.getElementById('section-trendline-tests');
        const patternSection = document.getElementById('section-pattern');
        const insertAfter = testSection || patternSection;
        if (insertAfter && insertAfter.nextSibling) {
            insertAfter.parentNode.insertBefore(section, insertAfter.nextSibling);
        } else {
            document.getElementById('right-panel').appendChild(section);
        }
        container = document.getElementById('candle-patterns-box');
    }

    // Show only recent patterns (last 5)
    const recent = patterns.slice(-5);
    if (!recent || recent.length === 0) {
        container.innerHTML = '<div style="color:#787b86; font-size:12px;">No candlestick patterns detected recently.</div>';
        return;
    }

    let html = '';
    recent.forEach(cp => {
        let color, icon;
        if (cp.type === 'inside_day') { color = '#42a5f5'; icon = '◇'; }
        else if (cp.type === 'outside_day') { color = '#e040fb'; icon = '◈'; }
        else if (cp.type === 'doji') { color = '#ffd54f'; icon = '✦'; }
        else if (cp.type === 'pump_close' && cp.direction === 'bullish') { color = '#26a69a'; icon = '⬆'; }
        else if (cp.type === 'pump_close' && cp.direction === 'bearish') { color = '#ef5350'; icon = '⬇'; }
        else { color = '#787b86'; icon = '•'; }
        html += `
            <div style="padding:6px 10px; margin-bottom:4px; background:var(--bg-primary); border-radius:6px; border-left:3px solid ${color};">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:11px; font-weight:600; color:${color};">${icon} ${cp.label}</span>
                    <span style="font-size:10px; color:#787b86;">${cp.date || ''}</span>
                </div>
                <div style="font-size:10px; color:var(--text-secondary); margin-top:2px;">${cp.description}</div>
            </div>
        `;
    });
    container.innerHTML = html;
}

function renderTradePlan(plan) {
    const container = document.getElementById('trade-plan');
    if (!plan) {
        container.innerHTML = '<div style="color:#787b86; font-size:13px;">No trade plan — no pattern detected.</div>';
        return;
    }

    const gradeClass = plan.grade.startsWith('A') ? 'grade-a' :
                       plan.grade.startsWith('B') ? 'grade-b' :
                       plan.grade.startsWith('C') ? 'grade-c' : 'grade-d';

    // R:R warning from grounding data (min 1:2 Breakout Strategy, 1:3 Trend Breaker)
    const rrWarnHtml = plan.rr_warning ? `
        <div style="margin-bottom:10px; padding:6px 10px; background:rgba(239,83,80,0.1); border-radius:6px; border-left:3px solid #ef5350;">
            <div style="font-size:11px; color:#ef5350; line-height:1.4;">${plan.rr_warning}</div>
        </div>` : '';

    // MACD / MA crossover signals (Trend Breaker confirmation)
    let signalHtml = '';
    if (plan.macd_bullish_cross || plan.sma8_ema20_cross) {
        const signals = [];
        if (plan.macd_bullish_cross) signals.push('MACD Bullish Cross');
        if (plan.sma8_ema20_cross) signals.push('SMA(8)/EMA(20) Cross');
        signalHtml = `<div style="margin-bottom:8px; font-size:10px; color:#26a69a;">${signals.join(' · ')}</div>`;
    }

    container.innerHTML = `
        ${rrWarnHtml}
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:14px;">
            <div class="grade-badge ${gradeClass}">${plan.grade}</div>
            <div>
                <div style="font-size:13px; font-weight:600; color:var(--text-bright);">Setup Grade</div>
                <div style="font-size:11px; color:var(--text-secondary);">R:R ${plan.risk_reward_ratio}:1 · ${(plan.win_rate_assumed*100).toFixed(0)}% est. WR</div>
                ${signalHtml}
            </div>
        </div>

        <!-- Entry & Stop -->
        <div class="trade-grid">
            <div class="trade-item">
                <div class="trade-item-label">Entry Price</div>
                <div class="trade-item-value blue">$${plan.entry_price}</div>
            </div>
            <div class="trade-item">
                <div class="trade-item-label">Stop Loss (-${plan.stop_pct}%)</div>
                <div class="trade-item-value red">$${plan.stop_loss}</div>
            </div>
        </div>

        <!-- TP Levels -->
        <div style="margin-top:10px; padding:10px; background:var(--bg-primary); border-radius:8px;">
            <div style="font-size:10px; font-weight:700; color:var(--text-secondary); margin-bottom:8px; text-transform:uppercase; letter-spacing:1px;">Take Profit Targets</div>
            <div class="indicator-row">
                <span class="indicator-label">TP1 <span style="color:#787b86;">(0.618 Fib)</span></span>
                <span class="indicator-value" style="color:#26a69a;">$${plan.tp1} <span style="font-size:10px; color:#787b86;">+${plan.tp1_pct}% · R:R 1:${plan.rr_tp1}</span></span>
            </div>
            <div class="indicator-row">
                <span class="indicator-label">TP2 <span style="color:#787b86;">(Measured)</span></span>
                <span class="indicator-value" style="color:#26a69a;">$${plan.tp2} <span style="font-size:10px; color:#787b86;">+${plan.tp2_pct}% · R:R 1:${plan.rr_tp2}</span></span>
            </div>
            <div class="indicator-row">
                <span class="indicator-label">TP3 <span style="color:#787b86;">(1.618 Fib)</span></span>
                <span class="indicator-value" style="color:#26a69a;">$${plan.tp3} <span style="font-size:10px; color:#787b86;">+${plan.tp3_pct}% · R:R 1:${plan.rr_tp3}</span></span>
            </div>
        </div>

        <!-- Exit Strategy -->
        <div style="margin-top:8px; padding:8px 10px; background:rgba(41,98,255,0.08); border-radius:6px; border-left:3px solid #2962ff;">
            <div style="font-size:10px; font-weight:700; color:#2962ff; margin-bottom:4px;">EXIT STRATEGY</div>
            <div style="font-size:11px; color:var(--text-primary); line-height:1.5;">${plan.exit_strategy}</div>
        </div>

        <!-- Profit Targets by TP level -->
        <div style="margin-top:10px; padding:10px; background:var(--bg-primary); border-radius:8px;">
            <div style="font-size:10px; font-weight:700; color:var(--text-secondary); margin-bottom:6px; text-transform:uppercase; letter-spacing:1px;">Profit if Full Position Hits</div>
            <div class="position-row">
                <span class="position-label">@ TP1</span>
                <span class="position-value" style="color:#26a69a;">+$${plan.profit_tp1.toLocaleString()}</span>
            </div>
            <div class="position-row">
                <span class="position-label">@ TP2</span>
                <span class="position-value" style="color:#26a69a;">+$${plan.profit_tp2.toLocaleString()}</span>
            </div>
            <div class="position-row">
                <span class="position-label">@ TP3</span>
                <span class="position-value" style="color:#26a69a;">+$${plan.profit_tp3.toLocaleString()}</span>
            </div>
            <div class="position-row" style="border-top:1px solid #363a45; padding-top:6px; margin-top:4px;">
                <span class="position-label">Max Loss</span>
                <span class="position-value" style="color:#ef5350;">-$${plan.potential_loss.toLocaleString()}</span>
            </div>
        </div>

        <!-- Position Sizing -->
        <div class="position-summary" style="margin-top:10px;">
            <div style="font-size:10px; font-weight:700; color:var(--text-secondary); margin-bottom:6px; text-transform:uppercase; letter-spacing:1px;">Position Sizing</div>
            <div class="position-row">
                <span class="position-label">Shares</span>
                <span class="position-value">${plan.position_size_shares}</span>
            </div>
            <div class="position-row">
                <span class="position-label">Position Value</span>
                <span class="position-value">$${plan.position_value.toLocaleString()}</span>
            </div>
            <div class="position-row">
                <span class="position-label">Risk (1.5% of $25K)</span>
                <span class="position-value" style="color:#ef5350;">$${plan.risk_amount}</span>
            </div>
            <div class="position-row">
                <span class="position-label">Expected Value</span>
                <span class="position-value" style="color:${plan.expected_value >= 0 ? '#26a69a' : '#ef5350'};">
                    ${plan.expected_value >= 0 ? '+' : ''}$${plan.expected_value.toLocaleString()}</span>
            </div>
        </div>
    `;
}

function renderIndicators(ind) {
    const container = document.getElementById('indicators-box');
    const formatVol = (v) => {
        if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
        if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
        return v.toString();
    };

    container.innerHTML = `
        <div class="indicator-row">
            <span class="indicator-label">VOL</span>
            <span class="indicator-value">${formatVol(ind.volume)}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">VOL(10)</span>
            <span class="indicator-value">${formatVol(ind.vol_ma_10)}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">Rel. Volume</span>
            <span class="indicator-value" style="color:${ind.relative_volume >= 1.5 ? '#26a69a' : ind.relative_volume < 0.7 ? '#ef5350' : 'inherit'}">
                ${ind.relative_volume}x</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">ATR(14)</span>
            <span class="indicator-value">${ind.atr_14} pts</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">TR(4)</span>
            <span class="indicator-value">${ind.tr_4} pts</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">ADR %</span>
            <span class="indicator-value">${ind.adr_pct}%</span>
        </div>
        <div class="indicator-row" style="border-top:1px solid #363a45; padding-top:8px; margin-top:4px;">
            <span class="indicator-label">RSI(14)</span>
            <span class="indicator-value" style="color:${ind.rsi_14 > 70 ? '#ef5350' : ind.rsi_14 < 30 ? '#26a69a' : 'inherit'}">
                ${ind.rsi_14}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">MACD</span>
            <span class="indicator-value" style="color:${ind.macd_histogram > 0 ? '#26a69a' : '#ef5350'}">
                ${ind.macd > 0 ? '+' : ''}${ind.macd}</span>
        </div>
        ${ind.macd_bullish_cross ? '<div style="font-size:10px; color:#26a69a; margin:2px 0;">MACD Bullish Crossover</div>' : ''}
        ${ind.macd_bearish_cross ? '<div style="font-size:10px; color:#ef5350; margin:2px 0;">MACD Bearish Crossover</div>' : ''}
        <div class="indicator-row" style="border-top:1px solid #363a45; padding-top:8px; margin-top:4px;">
            <span class="indicator-label">8 SMA</span>
            <span class="indicator-value">${ind.sma_8}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">9 EMA <span style="color:#ffb74d;">&#9679;</span></span>
            <span class="indicator-value">${ind.ema_9}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">20 EMA</span>
            <span class="indicator-value">${ind.ema_20}</span>
        </div>
        ${ind.sma8_ema20_cross ? '<div style="font-size:10px; color:#42a5f5; margin:2px 0;">SMA(8)/EMA(20) Crossover</div>' : ''}
        <div class="indicator-row">
            <span class="indicator-label">20 SMA <span style="color:#ce93d8;">&#9679;</span></span>
            <span class="indicator-value">${ind.sma_20}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">50 SMA <span style="color:#42a5f5;">&#9679;</span></span>
            <span class="indicator-value">${ind.sma_50}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">200 SMA <span style="color:#ef5350;">&#9679;</span></span>
            <span class="indicator-value">${ind.sma_200}</span>
        </div>
    `;
}

function renderFinancials(fund) {
    const container = document.getElementById('financials-box');
    if (!fund) {
        container.innerHTML = '<div style="color:#787b86; font-size:12px;">Financials not available for this ticker.</div>';
        return;
    }

    // Crypto-specific rendering
    if (fund.is_crypto) {
        const md = fund.market_data || {};
        const perf = fund.performance || {};
        const fmtCrypto = (v, prefix) => {
            if (v == null) return '<span style="color:#787b86;">N/A</span>';
            const p = prefix || '';
            if (typeof v === 'number') {
                if (Math.abs(v) >= 1e12) return `${p}${(v/1e12).toFixed(2)}T`;
                if (Math.abs(v) >= 1e9) return `${p}${(v/1e9).toFixed(1)}B`;
                if (Math.abs(v) >= 1e6) return `${p}${(v/1e6).toFixed(1)}M`;
                if (Math.abs(v) >= 1e3) return `${p}${(v/1e3).toFixed(1)}K`;
                return `${p}${v.toLocaleString()}`;
            }
            return `${p}${v}`;
        };

        container.innerHTML = `
            <div style="padding:8px 10px; margin-bottom:10px; background:rgba(255,152,0,0.08); border-radius:6px; border-left:3px solid #ff9800;">
                <div style="font-size:11px; color:#ff9800; line-height:1.4;">Crypto assets don't have traditional financial metrics like P/E or revenue.</div>
            </div>
            <div style="font-size:10px; font-weight:700; color:var(--text-secondary); margin-bottom:6px; text-transform:uppercase; letter-spacing:1px;">Market Data</div>
            <div class="indicator-row">
                <span class="indicator-label">${explainTerm("Market Cap")}</span>
                <span class="indicator-value">${fmtCrypto(md.market_cap, '$')}</span>
            </div>
            <div class="indicator-row">
                <span class="indicator-label">24h Volume</span>
                <span class="indicator-value">${fmtCrypto(md.volume_24h, '$')}</span>
            </div>
            <div class="indicator-row">
                <span class="indicator-label">Circulating Supply</span>
                <span class="indicator-value">${fmtCrypto(md.circulating_supply)}</span>
            </div>
            <div class="indicator-row">
                <span class="indicator-label">Max Supply</span>
                <span class="indicator-value">${md.max_supply ? fmtCrypto(md.max_supply) : '<span style="color:#787b86;">Unlimited</span>'}</span>
            </div>
            <div style="font-size:10px; font-weight:700; color:var(--text-secondary); margin-top:10px; margin-bottom:6px; text-transform:uppercase; letter-spacing:1px;">Performance</div>
            <div class="indicator-row">
                <span class="indicator-label">52W Range</span>
                <span class="indicator-value" style="font-size:10px;">$${perf.fifty_two_week_low || '?'} — $${perf.fifty_two_week_high || '?'}</span>
            </div>
        `;
        return;
    }

    const fmt = (v, prefix, suffix) => {
        if (v == null || v === 'N/A') return '<span style="color:#787b86;">N/A</span>';
        const p = prefix || '';
        const s = suffix || '';
        if (typeof v === 'number') {
            if (Math.abs(v) >= 1e9) return `${p}${(v/1e9).toFixed(1)}B${s}`;
            if (Math.abs(v) >= 1e6) return `${p}${(v/1e6).toFixed(1)}M${s}`;
            if (Math.abs(v) >= 1e3) return `${p}${(v/1e3).toFixed(1)}K${s}`;
            return `${p}${v.toFixed ? v.toFixed(2) : v}${s}`;
        }
        return `${p}${v}${s}`;
    };
    const pct = (v) => v != null ? `${(v * 100).toFixed(1)}%` : '<span style="color:#787b86;">N/A</span>';
    const clr = (v, invert) => {
        if (v == null) return '';
        const good = invert ? v < 0 : v > 0;
        return good ? 'color:#26a69a;' : 'color:#ef5350;';
    };

    const val = fund.valuation || {};
    const prof = fund.profitability || {};
    const bs = fund.balance_sheet || {};
    const cf = fund.cash_flow || {};
    const risk = fund.risk || {};
    const derived = fund.derived || {};
    const analyst = fund.analyst || {};
    const cmp = fund.comparison || {};

    // Helper: render comparison badge for "lower is better" metrics (P/E, P/B, EV/EBITDA, D/E)
    const cmpBadge = (c) => {
        if (!c || !c.verdict) return '';
        const label = c.verdict === 'cheap' ? 'Below Avg' : c.verdict === 'expensive' ? 'Above Avg' : '~ Avg';
        return `<span class="cmp-badge cmp-${c.verdict}">${label}</span>`;
    };
    // Helper: render comparison detail line (yellow for sector avg, green/red for diff)
    const cmpLine = (c, higherIsBetter) => {
        if (!c) return '';
        const avg = typeof c.industry_avg === 'number' && c.industry_avg < 1 ? (c.industry_avg * 100).toFixed(1) + '%' : c.industry_avg;
        const diffPositive = higherIsBetter ? c.diff_pct > 0 : c.diff_pct < 0;
        const diffColor = c.diff_pct === 0 ? '#ff9800' : diffPositive ? '#26a69a' : '#ef5350';
        return `<div class="cmp-detail">${explainTerm("Sector Avg")}: ${avg} <span style="color:${diffColor};">(${c.diff_pct > 0 ? '+' : ''}${c.diff_pct}%)</span></div>`;
    };
    // Helper: render comparison badge for "higher is better" metrics (Margin, ROE)
    const cmpBadgeHigher = (c) => {
        if (!c || c.diff_pct == null) return '';
        const verdict = c.diff_pct > 15 ? 'cheap' : c.diff_pct < -15 ? 'expensive' : 'fair';
        const label = verdict === 'cheap' ? 'Above Avg' : verdict === 'expensive' ? 'Below Avg' : '~ Avg';
        return `<span class="cmp-badge cmp-${verdict}">${label}</span>`;
    };

    container.innerHTML = `
        <!-- Valuation: How expensive is this stock? -->
        <div style="font-size:10px; font-weight:700; color:var(--text-secondary); margin-bottom:6px; text-transform:uppercase; letter-spacing:1px;">How Expensive Is It?</div>
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("P/E")}</span>
            <span class="indicator-value">${fmt(val.pe_trailing)}${cmpBadge(cmp.pe)}</span>
        </div>
        ${cmpLine(cmp.pe, false)}
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Fwd P/E")}</span>
            <span class="indicator-value">${fmt(val.pe_forward)}${cmpBadge(cmp.pe_forward)}</span>
        </div>
        ${cmpLine(cmp.pe_forward, false)}
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("PEG Ratio")}</span>
            <span class="indicator-value" style="${val.peg_ratio != null && val.peg_ratio < 1 ? 'color:#26a69a;' : val.peg_ratio > 2 ? 'color:#ef5350;' : ''}">${fmt(val.peg_ratio)}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("EBITDA", "EV/EBITDA")}</span>
            <span class="indicator-value">${fmt(val.ev_to_ebitda)}${cmpBadge(cmp.ev_ebitda)}</span>
        </div>
        ${cmpLine(cmp.ev_ebitda, false)}

        <!-- Profitability: Is it making money? -->
        <div style="font-size:10px; font-weight:700; color:var(--text-secondary); margin-top:10px; margin-bottom:6px; text-transform:uppercase; letter-spacing:1px;">Is It Making Money?</div>
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Revenue Growth")}</span>
            <span class="indicator-value" style="${clr(prof.revenue_growth)}">${pct(prof.revenue_growth)}</span>
        </div>
        ${derived.earnings_growth != null ? `
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Earnings Growth")}</span>
            <span class="indicator-value" style="${clr(derived.earnings_growth)}">${pct(derived.earnings_growth)}</span>
        </div>` : ''}
        ${derived.gross_margin != null ? `
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Gross Margin")}</span>
            <span class="indicator-value" style="${clr(derived.gross_margin)}">${pct(derived.gross_margin)}</span>
        </div>` : ''}
        ${derived.operating_margin != null ? `
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Operating Margin")}</span>
            <span class="indicator-value" style="${clr(derived.operating_margin)}">${pct(derived.operating_margin)}</span>
        </div>` : ''}
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Profit Margin", "Net Margin")}</span>
            <span class="indicator-value" style="${clr(prof.net_margin)}">${pct(prof.net_margin)}${cmpBadgeHigher(cmp.margin)}</span>
        </div>
        ${cmpLine(cmp.margin, true)}
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("ROE")}</span>
            <span class="indicator-value" style="${clr(prof.roe)}">${pct(prof.roe)}${cmpBadgeHigher(cmp.roe)}</span>
        </div>
        ${cmpLine(cmp.roe, true)}
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("FCF Yield")}</span>
            <span class="indicator-value" style="${clr(derived.fcf_yield_pct)}">${derived.fcf_yield_pct != null ? derived.fcf_yield_pct + '%' : '<span style="color:#787b86;">N/A</span>'}</span>
        </div>

        <!-- Health: Can it survive? -->
        <div style="font-size:10px; font-weight:700; color:var(--text-secondary); margin-top:10px; margin-bottom:6px; text-transform:uppercase; letter-spacing:1px;">Can It Survive?</div>
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Debt/Equity", "D/E Ratio")}</span>
            <span class="indicator-value" style="${bs.debt_to_equity != null && bs.debt_to_equity > 100 ? 'color:#ef5350;' : ''}">${fmt(bs.debt_to_equity)}${cmpBadge(cmp.de)}</span>
        </div>
        ${cmpLine(cmp.de, false)}
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Burning Cash", "Cash Burn")}</span>
            <span class="indicator-value" style="color:${derived.is_burning_cash ? '#ef5350' : '#26a69a'};">${derived.is_burning_cash ? 'YES' : 'No'}</span>
        </div>
        ${derived.cash_runway_months != null ? `
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Cash Runway")}</span>
            <span class="indicator-value" style="color:${derived.cash_runway_months < 12 ? '#ef5350' : derived.cash_runway_months < 24 ? '#ff9800' : '#26a69a'};">${derived.cash_runway_months.toFixed(0)} months</span>
        </div>` : ''}
        ${derived.debt_to_burn != null ? `
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Debt-to-Burn")}</span>
            <span class="indicator-value" style="color:${derived.debt_to_burn > 36 ? '#ef5350' : derived.debt_to_burn > 18 ? '#ff9800' : '#26a69a'};">${derived.debt_to_burn} months</span>
        </div>` : ''}
        ${derived.interest_coverage != null ? `
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Interest Coverage")}</span>
            <span class="indicator-value" style="color:${derived.interest_coverage < 1.5 ? '#ef5350' : derived.interest_coverage < 3 ? '#ff9800' : '#26a69a'};">${derived.interest_coverage}x</span>
        </div>` : ''}
        ${derived.quick_ratio != null ? `
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Quick Ratio")}</span>
            <span class="indicator-value" style="color:${derived.quick_ratio < 1 ? '#ef5350' : '#26a69a'};">${derived.quick_ratio.toFixed ? derived.quick_ratio.toFixed(2) : derived.quick_ratio}</span>
        </div>` : ''}
        <div class="indicator-row">
            <span class="indicator-label">Cash</span>
            <span class="indicator-value">${fmt(bs.total_cash, '$')}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">Debt</span>
            <span class="indicator-value">${fmt(bs.total_debt, '$')}</span>
        </div>

        <!-- Risk: How risky? -->
        <div style="font-size:10px; font-weight:700; color:var(--text-secondary); margin-top:10px; margin-bottom:6px; text-transform:uppercase; letter-spacing:1px;">How Risky?</div>
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Beta")}</span>
            <span class="indicator-value" style="${risk.beta != null && risk.beta > 1.5 ? 'color:#ef5350;' : ''}">${fmt(risk.beta)}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">${explainTerm("Short Ratio")}</span>
            <span class="indicator-value">${fmt(risk.short_ratio)}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">52W Range</span>
            <span class="indicator-value" style="font-size:10px;">$${risk.fifty_two_week_low || '?'} — $${risk.fifty_two_week_high || '?'}</span>
        </div>

        <!-- Analyst: What do pros think? -->
        ${analyst.target_mean ? `
        <div style="font-size:10px; font-weight:700; color:var(--text-secondary); margin-top:10px; margin-bottom:6px; text-transform:uppercase; letter-spacing:1px;">What Do Analysts Think?</div>
        <div class="indicator-row">
            <span class="indicator-label">Price Target</span>
            <span class="indicator-value" style="color:#42a5f5;">$${analyst.target_mean}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">Range</span>
            <span class="indicator-value" style="font-size:10px;">$${analyst.target_low || '?'} — $${analyst.target_high || '?'}</span>
        </div>
        <div class="indicator-row">
            <span class="indicator-label">Rating</span>
            <span class="indicator-value" style="text-transform:uppercase; font-size:10px; font-weight:600;
                color:${analyst.recommendation === 'buy' || analyst.recommendation === 'strong_buy' ? '#26a69a' :
                        analyst.recommendation === 'sell' || analyst.recommendation === 'strong_sell' ? '#ef5350' : '#ff9800'};">
                ${(analyst.recommendation || 'N/A').replace('_', ' ')}</span>
        </div>
        ${analyst.num_analysts ? `<div style="font-size:10px; color:#787b86; margin-top:2px;">${analyst.num_analysts} analysts</div>` : ''}
        ` : ''}
    `;
}

// ─── API Calls ───────────────────────────────────────────────

async function analyzeTicker(ticker, period, interval) {
    if (isLoading) return;
    showLoading();

    try {
        const resp = await fetch('/api/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker, period, interval }),
        });

        const data = await resp.json();

        if (!resp.ok) {
            throw new Error(data.error || 'Analysis failed');
        }

        hideLoading();
        renderAnalysis(data);
    } catch (err) {
        hideLoading();
        showError(err.message);
    }
}

async function loadHistory() {
    try {
        const resp = await fetch('/api/history');
        const data = await resp.json();
        renderHistory(data);
    } catch (err) {
        console.error('Failed to load history', err);
    }
}

async function loadHistoryItem(id) {
    try {
        const resp = await fetch(`/api/history/${id}`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error);
        renderAnalysis(data);
        closeHistory();
    } catch (err) {
        showError(err.message);
        closeHistory();
    }
}

async function deleteHistoryItem(id, ev) {
    ev.stopPropagation();
    try {
        await fetch(`/api/history/${id}`, { method: 'DELETE' });
        loadHistory();
    } catch (err) {
        console.error('Failed to delete', err);
    }
}

function renderHistory(items) {
    const list = document.getElementById('history-list');
    if (items.length === 0) {
        list.innerHTML = '<div style="padding:20px; color:#787b86; text-align:center;">No recent searches</div>';
        return;
    }

    list.innerHTML = items.map(item => {
        const date = new Date(item.created_at + 'Z');
        const expires = new Date(item.expires_at);
        const now = new Date();
        const hoursLeft = Math.max(0, Math.round((expires - now) / 3600000));

        return `
            <div class="history-item" onclick="loadHistoryItem(${item.id})">
                <div>
                    <div class="history-ticker">${item.ticker}</div>
                    <div class="history-meta">${item.period} · ${item.interval} · ${date.toLocaleString()}</div>
                    <div class="history-meta">Expires in ${hoursLeft}h</div>
                </div>
                <button class="history-delete" onclick="deleteHistoryItem(${item.id}, event)" title="Delete">&times;</button>
            </div>
        `;
    }).join('');
}

function openHistory() {
    document.getElementById('history-panel').classList.add('open');
    document.getElementById('backdrop').classList.add('open');
    loadHistory();
}

function closeHistory() {
    document.getElementById('history-panel').classList.remove('open');
    document.getElementById('backdrop').classList.remove('open');
}

// ─── AI Validation ───────────────────────────────────────────

let aiConfigured = false;

async function checkAIStatus() {
    try {
        const resp = await fetch('/api/ai-status');
        const data = await resp.json();
        aiConfigured = data.configured;
    } catch (e) {
        aiConfigured = false;
    }
}

function resetAIValidation() {
    const box = document.getElementById('ai-validation-box');
    const btn = document.getElementById('btn-ai-validate');
    const notConfigured = document.getElementById('ai-not-configured');

    if (aiConfigured) {
        btn.style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Validate with AI';
        notConfigured.style.display = 'none';
        box.querySelectorAll('.ai-verdict-banner, .ai-model-section, .ai-models-used').forEach(el => el.remove());
    } else {
        btn.style.display = 'none';
        notConfigured.style.display = 'block';
    }
}

async function runAIValidation() {
    if (!currentAnalysis) return;

    const btn = document.getElementById('btn-ai-validate');
    const box = document.getElementById('ai-validation-box');

    btn.disabled = true;
    btn.textContent = 'Validating...';

    // Show loading state
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'ai-loading';
    loadingDiv.id = 'ai-loading';
    loadingDiv.innerHTML = `
        <div class="spinner"></div>
        <div style="color:#787b86; font-size:12px;">Running 3 AI models in parallel...</div>
        <div style="color:#787b86; font-size:11px; margin-top:4px;">Research + Pattern + Prediction</div>
    `;
    box.appendChild(loadingDiv);

    try {
        const fastMode = document.getElementById('fast-mode-toggle').checked;
        const validatePayload = Object.assign({}, currentAnalysis, { fast_mode: fastMode || undefined });
        const resp = await fetch('/api/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(validatePayload),
        });

        const text = await resp.text();
        let data;
        try {
            data = JSON.parse(text);
        } catch (parseErr) {
            const loader = document.getElementById('ai-loading');
            if (loader) loader.remove();
            throw new Error(resp.status === 429 ? 'Rate limit exceeded. Please wait before trying again.' : resp.status === 504 ? 'Request timed out — AI models took too long. Please retry.' : `Server error (${resp.status}). Please try again.`);
        }

        if (resp.status === 429) {
            const loader = document.getElementById('ai-loading');
            if (loader) loader.remove();
            handle429(data);
            loadBillingStatus();
            return;
        }

        // Remove loading
        const loader = document.getElementById('ai-loading');
        if (loader) loader.remove();

        if (data.error && !data.configured) {
            btn.style.display = 'none';
            document.getElementById('ai-not-configured').style.display = 'block';
            document.getElementById('ai-not-configured').textContent = data.error;
            return;
        }

        btn.style.display = 'none';
        renderAIValidation(data, box);
        loadBillingStatus(); // Refresh usage count
    } catch (err) {
        const loader = document.getElementById('ai-loading');
        if (loader) loader.remove();
        btn.disabled = false;
        btn.textContent = 'Retry Validation';
        const errDiv = document.createElement('div');
        errDiv.style.cssText = 'color:#ef5350; font-size:12px; margin-top:8px;';
        errDiv.textContent = `Error: ${err.message}`;
        box.appendChild(errDiv);
    }
}

function renderAIValidation(data, container) {
    const v = data.verdict || {};
    const verdictColor = v.color || 'yellow';
    const verdictColorMap = { green: '#26a69a', red: '#ef5350', yellow: '#ff9800' };
    const verdictTextColor = verdictColorMap[verdictColor] || '#ff9800';

    // Final Verdict Banner
    const banner = document.createElement('div');
    banner.className = `ai-verdict-banner ${verdictColor}`;
    banner.innerHTML = `
        <div class="ai-verdict-text" style="color:${verdictTextColor}">${v.final_verdict || 'N/A'}</div>
        <div class="ai-verdict-score">Composite Score: ${v.composite_score || 0}/100</div>
        <div style="display:flex; justify-content:center; gap:16px; margin-top:8px;">
            <span style="font-size:11px; color:#787b86;">Research: <b style="color:${verdictTextColor}">${v.research_score || 0}</b></span>
            <span style="font-size:11px; color:#787b86;">Pattern: <b style="color:${verdictTextColor}">${v.pattern_score || 0}</b></span>
            <span style="font-size:11px; color:#787b86;">Prediction: <b style="color:${verdictTextColor}">${v.prediction_score || 0}</b></span>
        </div>
    `;
    container.appendChild(banner);

    // Risk Flags Banner
    if (v.risk_flags && v.risk_flags.length > 0) {
        const flagsBanner = document.createElement('div');
        flagsBanner.className = 'risk-flags-banner';
        flagsBanner.innerHTML = `
            <div class="risk-flags-title">RISK GATES TRIGGERED</div>
            ${v.risk_flags.map(f => `<div class="risk-flag-item">${f}</div>`).join('')}
        `;
        container.appendChild(flagsBanner);
    }

    // Research Section
    if (data.research && !data.research.error) {
        container.appendChild(buildResearchSection(data.research));
    }

    // Pattern Section
    if (data.pattern && !data.pattern.error) {
        container.appendChild(buildPatternSection(data.pattern));
    }

    // Prediction Section
    if (data.prediction && !data.prediction.error) {
        container.appendChild(buildPredictionSection(data.prediction));
    }

    // Models used footer
    if (data.models) {
        const footer = document.createElement('div');
        footer.className = 'ai-models-used';
        footer.innerHTML = `
            Models: ${data.models.research.split('/')[1] || data.models.research} ·
            ${data.models.pattern.split('/')[1] || data.models.pattern} ·
            ${data.models.prediction.split('/')[1] || data.models.prediction}
        `;
        container.appendChild(footer);
    }
}

function buildResearchSection(r) {
    const section = document.createElement('div');
    section.className = 'ai-model-section';

    const verdictClass = r.verdict === 'BULLISH' ? 'bullish' : r.verdict === 'BEARISH' ? 'bearish' : 'warning';

    let catalystsTags = '';
    (r.catalysts_bullish || []).forEach(c => { catalystsTags += `<span class="ai-tag bullish">${c}</span>`; });
    (r.catalysts_bearish || []).forEach(c => { catalystsTags += `<span class="ai-tag bearish">${c}</span>`; });

    let macroTags = '';
    (r.macro_tailwinds || []).forEach(m => { macroTags += `<span class="ai-tag bullish">${m}</span>`; });
    (r.macro_headwinds || []).forEach(m => { macroTags += `<span class="ai-tag bearish">${m}</span>`; });

    let redFlagTags = '';
    (r.red_flags || []).forEach(f => { redFlagTags += `<span class="ai-tag bearish">${f}</span>`; });

    section.innerHTML = `
        <div class="ai-model-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
            <span class="ai-model-title">Research & Fundamentals</span>
            <span class="ai-model-badge ai-tag ${verdictClass}">${r.verdict} ${r.confidence}%</span>
        </div>
        <div class="ai-model-body">
            <div style="margin-bottom:8px;">${r.summary || ''}</div>
            ${r.sector_analysis ? `<div class="indicator-row"><span class="indicator-label">Sector</span><span class="indicator-value" style="font-size:11px;">${r.sector_analysis}</span></div>` : ''}
            ${r.earnings_risk ? `<div class="indicator-row"><span class="indicator-label">Earnings Risk</span><span class="indicator-value" style="font-size:11px;">${r.earnings_risk}</span></div>` : ''}
            ${r.key_risk ? `<div style="margin-top:8px; padding:6px 8px; background:rgba(239,83,80,0.1); border-radius:4px; font-size:11px;"><b style="color:#ef5350;">Key Risk:</b> ${r.key_risk}</div>` : ''}
            ${catalystsTags ? `<div style="margin-top:8px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">CATALYSTS</div>${catalystsTags}</div>` : ''}
            ${redFlagTags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">RED FLAGS</div>${redFlagTags}</div>` : ''}
        </div>
    `;
    return section;
}

function buildPatternSection(p) {
    const section = document.createElement('div');
    section.className = 'ai-model-section';

    const validClass = p.pattern_valid ? 'bullish' : 'bearish';
    const validText = p.pattern_valid ? 'VALID' : 'INVALID';

    let fbReasons = '';
    (p.false_breakout_reasons || []).forEach(r => { fbReasons += `<span class="ai-tag warning">${r}</span>`; });

    let srLevels = '';
    (p.support_resistance_key_levels || []).forEach(l => { srLevels += `<span class="ai-tag info">${l}</span>`; });

    section.innerHTML = `
        <div class="ai-model-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
            <span class="ai-model-title">Pattern Validation</span>
            <span class="ai-model-badge ai-tag ${validClass}">${validText} ${p.pattern_confidence}%</span>
        </div>
        <div class="ai-model-body">
            <div style="margin-bottom:8px;">${p.summary || ''}</div>
            <div class="indicator-row"><span class="indicator-label">AI Detected</span><span class="indicator-value" style="font-size:11px;">${p.detected_pattern || 'N/A'}</span></div>
            <div class="indicator-row"><span class="indicator-label">Agrees with Algo</span><span class="indicator-value" style="font-size:11px; color:${p.algo_agreement ? '#26a69a' : '#ef5350'};">${p.algo_agreement ? 'Yes' : 'No'}</span></div>
            <div class="indicator-row"><span class="indicator-label">Breakout Prob</span><span class="indicator-value" style="font-size:11px;">${p.breakout_probability}%</span></div>
            <div class="indicator-row"><span class="indicator-label">False BO Risk</span><span class="indicator-value" style="font-size:11px; color:${p.false_breakout_risk === 'HIGH' ? '#ef5350' : p.false_breakout_risk === 'LOW' ? '#26a69a' : '#ff9800'};">${p.false_breakout_risk}</span></div>
            ${p.trendline_quality ? `<div class="indicator-row"><span class="indicator-label">Trendlines</span><span class="indicator-value" style="font-size:11px;">${p.trendline_quality}</span></div>` : ''}
            ${p.optimal_entry ? `<div class="indicator-row"><span class="indicator-label">Optimal Entry</span><span class="indicator-value" style="font-size:11px; color:#2962ff;">${p.optimal_entry}</span></div>` : ''}
            ${p.invalidation_level ? `<div class="indicator-row"><span class="indicator-label">Invalidation</span><span class="indicator-value" style="font-size:11px; color:#ef5350;">${p.invalidation_level}</span></div>` : ''}
            ${fbReasons ? `<div style="margin-top:8px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">FALSE BREAKOUT RISKS</div>${fbReasons}</div>` : ''}
            ${srLevels ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">KEY S/R LEVELS</div>${srLevels}</div>` : ''}
        </div>
    `;
    return section;
}

function buildPredictionSection(pr) {
    const section = document.createElement('div');
    section.className = 'ai-model-section';

    const verdictClass = pr.trade_verdict === 'TAKE' ? 'bullish' : pr.trade_verdict === 'SKIP' ? 'bearish' : 'warning';

    let pressureHtml = '';
    (pr.pressure_points || []).forEach(pp => {
        pressureHtml += `
            <div class="ai-pressure-item">
                <div class="ai-impact-dot ${pp.impact || 'LOW'}"></div>
                <div>
                    <div style="font-weight:600; font-size:11px;">${pp.factor}</div>
                    <div style="font-size:11px; color:#787b86;">${pp.detail}</div>
                </div>
            </div>`;
    });

    const targets = pr.price_targets || {};
    const stop = pr.stop_loss_assessment || pr.stop_loss || {};
    const timing = pr.timing || {};
    const scenarios = pr.scenario_analysis || pr.scenarios || {};
    const sizing = pr.position_sizing_review || {};

    section.innerHTML = `
        <div class="ai-model-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
            <span class="ai-model-title">Prediction & Risk</span>
            <span class="ai-model-badge ai-tag ${verdictClass}">${pr.trade_verdict} ${pr.overall_probability}%</span>
        </div>
        <div class="ai-model-body">
            <div style="margin-bottom:8px;">${pr.summary || ''}</div>

            ${targets.conservative ? `
            <div style="margin-bottom:10px;">
                <div style="font-size:10px; color:#787b86; margin-bottom:4px;">PRICE TARGETS</div>
                <div class="indicator-row"><span class="indicator-label">Conservative</span><span class="indicator-value" style="color:#26a69a;">$${targets.conservative}</span></div>
                <div class="indicator-row"><span class="indicator-label">Moderate</span><span class="indicator-value" style="color:#26a69a;">$${targets.moderate}</span></div>
                <div class="indicator-row"><span class="indicator-label">Aggressive</span><span class="indicator-value" style="color:#26a69a;">$${targets.aggressive}</span></div>
            </div>` : ''}

            ${(stop.recommended_stop || stop.recommended) ? `
            <div class="indicator-row"><span class="indicator-label">AI Stop Loss</span><span class="indicator-value" style="color:#ef5350;">$${stop.recommended_stop || stop.recommended}</span></div>
            <div style="font-size:11px; color:#787b86; margin-bottom:8px;">${stop.reason || ''}</div>` : ''}

            ${scenarios.bull_case ? `
            <div style="margin:10px 0;">
                <div style="font-size:10px; color:#787b86; margin-bottom:4px;">SCENARIOS</div>
                <div class="ai-scenario-box bull">${scenarios.bull_case}</div>
                <div class="ai-scenario-box base">${scenarios.base_case}</div>
                <div class="ai-scenario-box bear">${scenarios.bear_case}</div>
            </div>` : ''}

            ${pressureHtml ? `
            <div style="margin:10px 0;">
                <div style="font-size:10px; color:#787b86; margin-bottom:4px;">PRESSURE POINTS</div>
                ${pressureHtml}
            </div>` : ''}

            ${sizing.recommended_adjustment ? `
            <div class="indicator-row"><span class="indicator-label">Position Size</span><span class="indicator-value" style="font-size:11px;">${sizing.recommended_adjustment}${sizing.reason ? ' — ' + sizing.reason : ''}</span></div>` : ''}
            ${pr.position_size_ok !== undefined && !sizing.recommended_adjustment ? `
            <div class="indicator-row"><span class="indicator-label">Position Size</span><span class="indicator-value" style="font-size:11px; color:${pr.position_size_ok ? '#26a69a' : '#ef5350'};">${pr.position_size_ok ? 'OK' : 'Reduce'}</span></div>` : ''}

            ${timing.time_horizon ? `
            <div class="indicator-row"><span class="indicator-label">Time Horizon</span><span class="indicator-value" style="font-size:11px;">${timing.time_horizon}</span></div>` : ''}
            ${timing.wait_for && !timing.enter_now ? `
            <div style="padding:6px 8px; background:rgba(255,152,0,0.1); border-radius:4px; font-size:11px; margin-top:6px;"><b style="color:#ff9800;">Wait for:</b> ${timing.wait_for}</div>` : ''}

            ${pr.expected_value_per_trade ? `
            <div class="indicator-row" style="margin-top:8px; border-top:1px solid #363a45; padding-top:8px;">
                <span class="indicator-label">Expected Value</span>
                <span class="indicator-value" style="color:${pr.expected_value_per_trade >= 0 ? '#26a69a' : '#ef5350'};">$${pr.expected_value_per_trade}</span>
            </div>` : ''}
            ${pr.kelly_criterion_pct ? `
            <div class="indicator-row"><span class="indicator-label">Kelly %</span><span class="indicator-value">${pr.kelly_criterion_pct}%</span></div>` : ''}
        </div>
    `;
    return section;
}

// ─── 12-Month Investment Prediction ──────────────────────────

function resetPrediction() {
    const box = document.getElementById('prediction-box');
    const btn = document.getElementById('btn-prediction');
    const notConfigured = document.getElementById('prediction-not-configured');

    if (aiConfigured && currentAnalysis) {
        btn.style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Run 12-Month Prediction';
        notConfigured.style.display = 'none';
        box.querySelectorAll('.prediction-verdict-banner, .prediction-metrics-grid, .ai-model-section, .ai-models-used').forEach(el => el.remove());
    } else if (!aiConfigured) {
        btn.style.display = 'none';
        notConfigured.style.display = 'block';
    }
}

async function runPrediction() {
    if (!currentAnalysis) return;

    const btn = document.getElementById('btn-prediction');
    const box = document.getElementById('prediction-box');

    btn.disabled = true;
    btn.textContent = 'Analyzing fundamentals...';

    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'ai-loading';
    loadingDiv.id = 'prediction-loading';
    loadingDiv.innerHTML = `
        <div class="spinner"></div>
        <div style="color:#787b86; font-size:12px;">Running 3 AI models on fundamentals...</div>
        <div style="color:#787b86; font-size:11px; margin-top:4px;">Business Viability + Financial Health + Valuation</div>
    `;
    box.appendChild(loadingDiv);

    try {
        const fastMode = document.getElementById('fast-mode-toggle').checked;
        const resp = await fetch('/api/predict', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker: currentAnalysis.ticker, fast_mode: fastMode || undefined }),
        });

        const text = await resp.text();
        let data;
        try {
            data = JSON.parse(text);
        } catch (parseErr) {
            const loader = document.getElementById('prediction-loading');
            if (loader) loader.remove();
            throw new Error(resp.status === 429 ? 'Rate limit exceeded. Please wait before trying again.' : resp.status === 504 ? 'Prediction timed out — AI models took too long. Please retry.' : `Server error (${resp.status}). Please try again.`);
        }

        if (resp.status === 429) {
            const loader = document.getElementById('prediction-loading');
            if (loader) loader.remove();
            handle429(data);
            loadBillingStatus();
            return;
        }

        const loader = document.getElementById('prediction-loading');
        if (loader) loader.remove();

        if (data.error && !data.configured) {
            btn.style.display = 'none';
            document.getElementById('prediction-not-configured').style.display = 'block';
            document.getElementById('prediction-not-configured').textContent = data.error;
            return;
        }

        btn.style.display = 'none';
        renderPrediction(data, box);
        loadBillingStatus(); // Refresh usage count
    } catch (err) {
        const loader = document.getElementById('prediction-loading');
        if (loader) loader.remove();
        btn.disabled = false;
        btn.textContent = 'Retry Prediction';
        const errDiv = document.createElement('div');
        errDiv.style.cssText = 'color:#ef5350; font-size:12px; margin-top:8px;';
        errDiv.textContent = `Error: ${err.message}`;
        box.appendChild(errDiv);
    }
}

function renderPrediction(data, container) {
    const v = data.verdict || {};
    const verdictColor = v.color || 'yellow';
    const colorMap = { green: '#26a69a', red: '#ef5350', yellow: '#ff9800' };
    const textColor = colorMap[verdictColor] || '#ff9800';
    const pt = v.price_targets || {};

    // Verdict Banner with price targets
    const banner = document.createElement('div');
    banner.className = `prediction-verdict-banner ${verdictColor}`;
    banner.innerHTML = `
        <div class="prediction-verdict-text" style="color:${textColor}">${v.final_verdict || 'N/A'}</div>
        <div class="prediction-verdict-sub">Composite Score: ${v.composite_score || 0}/100 | ${v.models_invest || 0}/${v.total_models || 0} models say INVEST</div>
        ${v.survival_probability ? `<div class="prediction-verdict-sub">Survival Probability: ${v.survival_probability}%</div>` : ''}
        ${'bear' in pt || 'base' in pt || 'bull' in pt ? `
        <div class="prediction-price-targets">
            ${'bear' in pt ? `<div class="prediction-price-target"><div class="label">Bear</div><div class="value" style="color:#ef5350;">$${pt.bear}</div>${pt.bear_probability ? `<div class="label">${pt.bear_probability}%</div>` : ''}</div>` : ''}
            ${'base' in pt ? `<div class="prediction-price-target"><div class="label">Base</div><div class="value" style="color:#42a5f5;">$${pt.base}</div>${pt.base_probability ? `<div class="label">${pt.base_probability}%</div>` : ''}</div>` : ''}
            ${'bull' in pt ? `<div class="prediction-price-target"><div class="label">Bull</div><div class="value" style="color:#26a69a;">$${pt.bull}</div>${pt.bull_probability ? `<div class="label">${pt.bull_probability}%</div>` : ''}</div>` : ''}
        </div>` : ''}
        ${v.fair_value ? `<div class="prediction-verdict-sub" style="margin-top:6px;">Fair Value: $${v.fair_value} | Upside: ${v.upside_pct || 'N/A'}% | Downside: ${v.downside_pct || 'N/A'}%</div>` : ''}
    `;
    container.appendChild(banner);

    // Risk Flags Banner
    if (v.risk_flags && v.risk_flags.length > 0) {
        const flagsBanner = document.createElement('div');
        flagsBanner.className = 'risk-flags-banner';
        flagsBanner.innerHTML = `
            <div class="risk-flags-title">RISK GATES TRIGGERED</div>
            ${v.risk_flags.map(f => `<div class="risk-flag-item">${f}</div>`).join('')}
        `;
        container.appendChild(flagsBanner);
    }

    // Key Metrics Snapshot
    const snap = data.fundamentals_snapshot || {};
    const metricsGrid = document.createElement('div');
    metricsGrid.className = 'prediction-metrics-grid';
    metricsGrid.innerHTML = `
        <div class="prediction-metric">
            <div class="metric-label">${explainTerm("Fwd P/E")}</div>
            <div class="metric-value">${snap.pe_forward != null ? snap.pe_forward.toFixed(1) : 'N/A'}</div>
        </div>
        <div class="prediction-metric">
            <div class="metric-label">${explainTerm("FCF Yield")}</div>
            <div class="metric-value" style="color:${snap.fcf_yield > 5 ? '#26a69a' : snap.fcf_yield < 0 ? '#ef5350' : 'inherit'}">${snap.fcf_yield != null ? snap.fcf_yield.toFixed(1) + '%' : 'N/A'}</div>
        </div>
        <div class="prediction-metric">
            <div class="metric-label">${explainTerm("D/E Ratio")}</div>
            <div class="metric-value" style="color:${snap.de_ratio > 200 ? '#ef5350' : snap.de_ratio > 100 ? '#ff9800' : 'inherit'}">${snap.de_ratio != null ? snap.de_ratio.toFixed(0) : 'N/A'}</div>
        </div>
        <div class="prediction-metric">
            <div class="metric-label">${explainTerm("Rev Growth")}</div>
            <div class="metric-value" style="color:${snap.revenue_growth > 0 ? '#26a69a' : '#ef5350'}">${snap.revenue_growth != null ? (snap.revenue_growth * 100).toFixed(1) + '%' : 'N/A'}</div>
        </div>
        <div class="prediction-metric">
            <div class="metric-label">${explainTerm("Burning Cash")}</div>
            <div class="metric-value" style="color:${snap.is_burning ? '#ef5350' : '#26a69a'}">${snap.is_burning ? 'YES' : 'No'}</div>
        </div>
        <div class="prediction-metric">
            <div class="metric-label">${explainTerm("Cash Runway")}</div>
            <div class="metric-value" style="color:${snap.cash_runway && snap.cash_runway < 12 ? '#ef5350' : 'inherit'}">${snap.cash_runway != null ? snap.cash_runway.toFixed(0) + 'mo' : 'N/A'}</div>
        </div>
    `;
    container.appendChild(metricsGrid);

    // Model score bars
    const scoreBar = (label, score, color) => `
        <div style="margin-bottom:6px;">
            <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:3px;">
                <span style="color:var(--text-secondary);">${label}</span>
                <span style="font-weight:700; color:${color};">${score}/100</span>
            </div>
            <div class="health-bar">
                <div class="health-bar-fill" style="width:${score}%; background:${color};"></div>
            </div>
        </div>
    `;
    const scoresDiv = document.createElement('div');
    scoresDiv.style.cssText = 'margin-bottom:12px; padding:10px; background:var(--bg-primary); border-radius:8px;';
    scoresDiv.innerHTML =
        scoreBar('Fact Gatherer', v.fact_gatherer_score || 0, '#42a5f5') +
        scoreBar('Company Health', v.health_score || 0, '#26a69a') +
        scoreBar('Price Action', v.price_action_score || 0, '#ff9800') +
        scoreBar('Supervisor', v.supervisor_score || 0, '#ab47bc');
    container.appendChild(scoresDiv);

    // Fact Gatherer Section
    if (data.fact_gatherer && !data.fact_gatherer.error) {
        container.appendChild(buildFactGathererSection(data.fact_gatherer));
    }

    // Company Health Section
    if (data.company_health && !data.company_health.error) {
        container.appendChild(buildCompanyHealthSection(data.company_health));
    }

    // Price Action Section
    if (data.price_action && !data.price_action.error) {
        container.appendChild(buildPriceActionSection(data.price_action));
    }

    // Supervisor Section
    if (data.supervisor && !data.supervisor.error) {
        container.appendChild(buildSupervisorSection(data.supervisor));
    }

    // Models footer
    if (data.models) {
        const fmtModel = m => m ? (m.split('/')[1] || m) : '?';
        const footer = document.createElement('div');
        footer.className = 'ai-models-used';
        footer.innerHTML = `Models: ${fmtModel(data.models.fact_gatherer)} · ${fmtModel(data.models.company_health)} · ${fmtModel(data.models.price_action)} · ${fmtModel(data.models.supervisor)}`;
        container.appendChild(footer);
    }
}

function buildFactGathererSection(fg) {
    const section = document.createElement('div');
    section.className = 'ai-model-section';
    const vClass = fg.verdict === 'INVEST' ? 'bullish' : fg.verdict === 'PASS' ? 'bearish' : 'warning';

    let catalystTags = '';
    (fg.recent_catalysts || []).forEach(c => { catalystTags += `<span class="ai-tag bullish">${c}</span>`; });
    let riskTags = '';
    (fg.risk_factors || []).forEach(r => { riskTags += `<span class="ai-tag bearish">${r}</span>`; });
    let finTags = '';
    (fg.key_financials || []).forEach(f => { finTags += `<span class="ai-tag info">${f}</span>`; });
    let techTags = '';
    (fg.key_technicals || []).forEach(t => { techTags += `<span class="ai-tag info">${t}</span>`; });

    section.innerHTML = `
        <div class="ai-model-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
            <span class="ai-model-title">Fact Gatherer</span>
            <span class="ai-model-badge ai-tag ${vClass}">${fg.verdict} ${fg.confidence}%</span>
        </div>
        <div class="ai-model-body">
            <div style="margin-bottom:8px;">${fg.fact_summary || ''}</div>
            ${fg.competitive_position ? `<div class="indicator-row"><span class="indicator-label">Competitive Position</span><span class="indicator-value" style="font-size:11px;">${fg.competitive_position}</span></div>` : ''}
            ${fg.bull_case ? `<div style="margin-top:8px; padding:6px 8px; background:rgba(38,166,154,0.1); border-radius:4px; font-size:11px;"><b style="color:#26a69a;">Bull:</b> ${fg.bull_case}</div>` : ''}
            ${fg.bear_case ? `<div style="margin-top:4px; padding:6px 8px; background:rgba(239,83,80,0.1); border-radius:4px; font-size:11px;"><b style="color:#ef5350;">Bear:</b> ${fg.bear_case}</div>` : ''}
            ${finTags ? `<div style="margin-top:8px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">KEY FINANCIALS</div>${finTags}</div>` : ''}
            ${techTags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">KEY TECHNICALS</div>${techTags}</div>` : ''}
            ${catalystTags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">RECENT CATALYSTS</div>${catalystTags}</div>` : ''}
            ${riskTags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">RISK FACTORS</div>${riskTags}</div>` : ''}
        </div>
    `;
    return section;
}

function buildCompanyHealthSection(h) {
    const section = document.createElement('div');
    section.className = 'ai-model-section';
    const vClass = h.verdict === 'INVEST' ? 'bullish' : h.verdict === 'PASS' ? 'bearish' : 'warning';

    let redFlags = '';
    (h.red_flags || []).forEach(f => { redFlags += `<span class="ai-tag bearish">${f}</span>`; });
    let greenFlags = '';
    (h.green_flags || []).forEach(f => { greenFlags += `<span class="ai-tag bullish">${f}</span>`; });

    section.innerHTML = `
        <div class="ai-model-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
            <span class="ai-model-title">Company Health</span>
            <span class="ai-model-badge ai-tag ${vClass}">${h.verdict} ${h.confidence}%</span>
        </div>
        <div class="ai-model-body">
            <div style="margin-bottom:8px;">${h.summary || ''}</div>
            ${h.financial_grade ? `<div class="indicator-row"><span class="indicator-label">Financial Grade</span><span class="indicator-value" style="font-size:11px;">${h.financial_grade}</span></div>` : ''}
            ${h.survival_probability ? `<div class="indicator-row"><span class="indicator-label">Survival (12mo)</span><span class="indicator-value" style="font-size:11px; color:${h.survival_probability >= 80 ? '#26a69a' : h.survival_probability >= 60 ? '#ff9800' : '#ef5350'};">${h.survival_probability}%</span></div>` : ''}
            ${h.cash_position ? `<div class="indicator-row"><span class="indicator-label">Cash Position</span><span class="indicator-value" style="font-size:11px;">${h.cash_position}</span></div>` : ''}
            ${h.debt_risk ? `<div class="indicator-row"><span class="indicator-label">Debt Risk</span><span class="indicator-value" style="font-size:11px;">${h.debt_risk}</span></div>` : ''}
            ${h.fcf_trajectory ? `<div class="indicator-row"><span class="indicator-label">FCF Trajectory</span><span class="indicator-value" style="font-size:11px;">${h.fcf_trajectory}</span></div>` : ''}
            ${h.dilution_risk ? `<div class="indicator-row"><span class="indicator-label">Dilution Risk</span><span class="indicator-value" style="font-size:11px;">${h.dilution_risk}</span></div>` : ''}
            ${h.revenue_quality ? `<div class="indicator-row"><span class="indicator-label">Revenue Quality</span><span class="indicator-value" style="font-size:11px;">${h.revenue_quality}</span></div>` : ''}
            ${h.moat_assessment ? `<div class="indicator-row"><span class="indicator-label">Moat</span><span class="indicator-value" style="font-size:11px;">${h.moat_assessment} (${h.moat_score || 0}/100)</span></div>` : ''}
            ${h.growth_durability ? `<div class="indicator-row"><span class="indicator-label">Growth Durability</span><span class="indicator-value" style="font-size:11px;">${h.growth_durability}</span></div>` : ''}
            ${redFlags ? `<div style="margin-top:8px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">RED FLAGS</div>${redFlags}</div>` : ''}
            ${greenFlags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">GREEN FLAGS</div>${greenFlags}</div>` : ''}
        </div>
    `;
    return section;
}

function buildPriceActionSection(pa) {
    const section = document.createElement('div');
    section.className = 'ai-model-section';
    const vClass = pa.verdict === 'INVEST' ? 'bullish' : pa.verdict === 'PASS' ? 'bearish' : 'warning';

    let catalystTags = '';
    (pa.catalysts_for_rerating || []).forEach(c => { catalystTags += `<span class="ai-tag info">${c}</span>`; });
    let techRiskTags = '';
    (pa.technical_risks || []).forEach(r => { techRiskTags += `<span class="ai-tag bearish">${r}</span>`; });

    const pt = pa.price_targets || {};

    section.innerHTML = `
        <div class="ai-model-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
            <span class="ai-model-title">Price Action & Valuation</span>
            <span class="ai-model-badge ai-tag ${vClass}">${pa.verdict} ${pa.confidence}%</span>
        </div>
        <div class="ai-model-body">
            <div style="margin-bottom:8px;">${pa.summary || ''}</div>
            ${pa.trend_assessment ? `<div class="indicator-row"><span class="indicator-label">Trend</span><span class="indicator-value" style="font-size:11px;">${pa.trend_assessment}</span></div>` : ''}
            ${pa.momentum_signal ? `<div class="indicator-row"><span class="indicator-label">Momentum</span><span class="indicator-value" style="font-size:11px;">${pa.momentum_signal}</span></div>` : ''}
            ${pa.volume_assessment ? `<div class="indicator-row"><span class="indicator-label">Volume</span><span class="indicator-value" style="font-size:11px;">${pa.volume_assessment}</span></div>` : ''}
            ${pa.support_resistance ? `<div class="indicator-row"><span class="indicator-label">S/R Levels</span><span class="indicator-value" style="font-size:11px;">${pa.support_resistance}</span></div>` : ''}
            ${pa.fair_value ? `<div class="indicator-row"><span class="indicator-label">Fair Value</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${pa.fair_value}</span></div>` : ''}
            ${pa.current_vs_fair ? `<div class="indicator-row"><span class="indicator-label">Current vs Fair</span><span class="indicator-value" style="font-size:11px;">${pa.current_vs_fair}</span></div>` : ''}
            ${pa.margin_of_safety_pct != null ? `<div class="indicator-row"><span class="indicator-label">Margin of Safety</span><span class="indicator-value" style="font-size:11px; color:${pa.margin_of_safety_pct > 15 ? '#26a69a' : pa.margin_of_safety_pct > 0 ? '#ff9800' : '#ef5350'};">${pa.margin_of_safety_pct}%</span></div>` : ''}
            ${pa.entry_attractiveness ? `<div class="indicator-row"><span class="indicator-label">Entry Timing</span><span class="indicator-value" style="font-size:11px; color:#2962ff;">${pa.entry_attractiveness}</span></div>` : ''}
            ${pa.dcf_notes ? `<div style="margin-top:8px; padding:6px 8px; background:rgba(41,98,255,0.08); border-radius:4px; font-size:11px;"><b style="color:#42a5f5;">DCF Notes:</b> ${pa.dcf_notes}</div>` : ''}
            ${'bear' in pt || 'base' in pt || 'bull' in pt ? `
            <div style="margin:10px 0;">
                <div style="font-size:10px; color:#787b86; margin-bottom:4px;">12-MONTH SCENARIOS</div>
                ${'bear' in pt ? `<div class="ai-scenario-box bear">Bear: $${pt.bear} (${pt.bear_probability || '?'}% probability)</div>` : ''}
                ${'base' in pt ? `<div class="ai-scenario-box base">Base: $${pt.base} (${pt.base_probability || '?'}% probability)</div>` : ''}
                ${'bull' in pt ? `<div class="ai-scenario-box bull">Bull: $${pt.bull} (${pt.bull_probability || '?'}% probability)</div>` : ''}
            </div>` : ''}
            ${techRiskTags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">TECHNICAL RISKS</div>${techRiskTags}</div>` : ''}
            ${catalystTags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">RE-RATING CATALYSTS</div>${catalystTags}</div>` : ''}
        </div>
    `;
    return section;
}

function buildSupervisorSection(sup) {
    const section = document.createElement('div');
    section.className = 'ai-model-section';
    const vClass = sup.verdict === 'INVEST' ? 'bullish' : sup.verdict === 'PASS' ? 'bearish' : 'warning';

    let riskTags = '';
    (sup.risk_flags || []).forEach(r => { riskTags += `<span class="ai-tag bearish">${r}</span>`; });
    let overrideTags = '';
    (sup.override_flags || []).forEach(o => { overrideTags += `<span class="ai-tag bearish">${o}</span>`; });

    section.innerHTML = `
        <div class="ai-model-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
            <span class="ai-model-title">Supervisor Review</span>
            <span class="ai-model-badge ai-tag ${vClass}">${sup.verdict} ${sup.confidence}%</span>
        </div>
        <div class="ai-model-body">
            <div style="margin-bottom:8px;">${sup.reasoning || sup.summary || ''}</div>
            <div class="indicator-row"><span class="indicator-label">Agrees w/ Health</span><span class="indicator-value" style="font-size:11px; color:${sup.agrees_with_health ? '#26a69a' : '#ef5350'};">${sup.agrees_with_health ? 'Yes' : 'No'}</span></div>
            <div class="indicator-row"><span class="indicator-label">Agrees w/ Price Action</span><span class="indicator-value" style="font-size:11px; color:${sup.agrees_with_price_action ? '#26a69a' : '#ef5350'};">${sup.agrees_with_price_action ? 'Yes' : 'No'}</span></div>
            ${overrideTags ? `<div style="margin-top:8px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">OVERRIDE FLAGS</div>${overrideTags}</div>` : ''}
            ${riskTags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">RISK FLAGS</div>${riskTags}</div>` : ''}
        </div>
    `;
    return section;
}

// ─── Earnings Analysis (Skill-backed) ────────────────────────

let _earningsJobId = null;
let _earningsSSE = null;

function resetEarnings() {
    const box = document.getElementById('earnings-box');
    const btn = document.getElementById('btn-earnings');
    const notConfigured = document.getElementById('earnings-not-configured');

    // Close any active SSE
    if (_earningsSSE) { _earningsSSE.close(); _earningsSSE = null; }
    _earningsJobId = null;

    // Remove previous results
    box.querySelectorAll('.earnings-progress, .earnings-result, .earnings-error').forEach(el => el.remove());

    if (aiConfigured && currentAnalysis) {
        btn.style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Run Earnings Analysis';
        notConfigured.style.display = 'none';
    } else if (!aiConfigured) {
        btn.style.display = 'none';
        notConfigured.style.display = 'block';
    }
}

async function runEarningsAnalysis() {
    if (!currentAnalysis) return;

    const btn = document.getElementById('btn-earnings');
    const box = document.getElementById('earnings-box');

    btn.disabled = true;
    btn.textContent = 'Launching...';

    // Remove old results/errors
    box.querySelectorAll('.earnings-progress, .earnings-result, .earnings-error').forEach(el => el.remove());

    try {
        const resp = await fetch('/api/skills/launch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                skill_id: 'earnings-analysis',
                inputs: { ticker: currentAnalysis.ticker }
            }),
        });
        const data = await resp.json();

        if (!resp.ok) {
            if (resp.status === 429) {
                handle429(data);
            } else if (resp.status === 403) {
                btn.textContent = 'Upgrade Required';
                const err = document.createElement('div');
                err.className = 'earnings-error';
                err.textContent = data.error || 'Pro tier required for Earnings Analysis';
                box.appendChild(err);
            } else {
                throw new Error(data.error || 'Failed to launch');
            }
            return;
        }

        _earningsJobId = data.job_id;
        btn.style.display = 'none';

        // Show progress tracker
        const progress = document.createElement('div');
        progress.className = 'earnings-progress';
        progress.innerHTML = `
            <div class="earnings-progress-header">
                <div class="spinner"></div>
                <span class="earnings-progress-text">Starting analysis...</span>
            </div>
            <div class="earnings-progress-bar-container">
                <div class="earnings-progress-bar" style="width:0%"></div>
            </div>
        `;
        box.appendChild(progress);

        // Start SSE tracking
        trackEarningsJob(data.job_id);

    } catch (err) {
        btn.disabled = false;
        btn.textContent = 'Retry Earnings Analysis';
        const errDiv = document.createElement('div');
        errDiv.className = 'earnings-error';
        errDiv.textContent = `Error: ${err.message}`;
        box.appendChild(errDiv);
    }
}

function trackEarningsJob(jobId) {
    if (_earningsSSE) _earningsSSE.close();

    const es = new EventSource(`/api/skills/jobs/${jobId}/stream`);
    _earningsSSE = es;

    const updateProgress = (data) => {
        const bar = document.querySelector('.earnings-progress-bar');
        const text = document.querySelector('.earnings-progress-text');
        if (bar) bar.style.width = Math.round((data.progress || 0) * 100) + '%';
        if (text) text.textContent = data.message || data.current_phase || 'Processing...';
    };

    es.addEventListener('status', (e) => updateProgress(JSON.parse(e.data)));
    es.addEventListener('progress', (e) => updateProgress(JSON.parse(e.data)));
    es.addEventListener('started', (e) => updateProgress(JSON.parse(e.data)));

    es.addEventListener('completed', (e) => {
        es.close();
        _earningsSSE = null;
        fetchEarningsResult(jobId);
    });

    es.addEventListener('failed', (e) => {
        es.close();
        _earningsSSE = null;
        const data = JSON.parse(e.data);
        showEarningsError(data.error || 'Analysis failed');
    });

    es.addEventListener('cancelled', (e) => {
        es.close();
        _earningsSSE = null;
        showEarningsError('Analysis was cancelled');
    });

    es.addEventListener('done', (e) => {
        const data = JSON.parse(e.data);
        es.close();
        _earningsSSE = null;
        if (data.status === 'completed') fetchEarningsResult(jobId);
        else if (data.status === 'failed') showEarningsError(data.error || 'Analysis failed');
    });

    es.onerror = () => {
        es.close();
        _earningsSSE = null;
        // Fallback: poll once
        setTimeout(() => fetchEarningsResult(jobId), 1000);
    };
}

function showEarningsError(msg) {
    const box = document.getElementById('earnings-box');
    const btn = document.getElementById('btn-earnings');
    box.querySelectorAll('.earnings-progress').forEach(el => el.remove());
    btn.style.display = 'block';
    btn.disabled = false;
    btn.textContent = 'Retry Earnings Analysis';
    const errDiv = document.createElement('div');
    errDiv.className = 'earnings-error';
    errDiv.textContent = msg;
    box.appendChild(errDiv);
}

async function fetchEarningsResult(jobId) {
    const box = document.getElementById('earnings-box');
    box.querySelectorAll('.earnings-progress').forEach(el => el.remove());

    try {
        const resp = await fetch(`/api/skills/jobs/${jobId}/result`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || 'Failed to load result');
        renderEarningsResult(data.result, jobId, box);
    } catch (err) {
        showEarningsError(err.message);
    }
}

function renderEarningsResult(result, jobId, container) {
    const earnings = result.earnings || {};
    const ratios = result.computed_ratios || {};
    const header = earnings.header || {};
    const charts = result._charts || [];

    const wrapper = document.createElement('div');
    wrapper.className = 'earnings-result';

    // Helper: chart image HTML
    const chartImg = (filename, caption) => {
        const match = charts.find(c => c.filename === filename);
        if (!match) return '';
        return `<div class="earnings-chart">
            <img src="/api/skills/jobs/${jobId}/charts/${filename}" alt="${caption}" loading="lazy" />
            <div class="earnings-chart-caption">${match.caption || caption}</div>
        </div>`;
    };

    // Rating banner with price target
    const rating = header.rating || earnings.rating || 'N/A';
    const score = earnings.overall_score || 0;
    const ratingColors = {
        'Strong Buy': '#26a69a', 'Buy': '#4caf50', 'Hold': '#ff9800',
        'Sell': '#ef5350', 'Strong Sell': '#d32f2f'
    };
    const ratingColor = ratingColors[rating] || '#787b86';

    let priceLine = '';
    if (header.current_price) priceLine += `<span>Price: $${header.current_price}</span>`;
    if (header.price_target) priceLine += `<span>PT: $${header.price_target}</span>`;
    if (header.upside_pct != null) {
        const up = header.upside_pct;
        priceLine += `<span style="color:${up >= 0 ? '#26a69a' : '#ef5350'}">${up > 0 ? '+' : ''}${up}%</span>`;
    }

    let html = `
        <div class="earnings-rating-banner" style="border-left:4px solid ${ratingColor};">
            <div class="earnings-rating-row">
                <span class="earnings-rating-label" style="color:${ratingColor};">${rating}</span>
                <span class="earnings-score-badge">${score}/10</span>
            </div>
            ${priceLine ? `<div class="earnings-price-line">${priceLine}</div>` : ''}
            <div class="earnings-summary">${earnings.summary || ''}</div>
        </div>`;

    // Earnings Summary table (beat/miss)
    const es = earnings.earnings_summary || {};
    const est = es.table || [];
    if (est.length) {
        const headline = es.headline || '';
        html += `
        <div class="earnings-section">
            <div class="earnings-section-title">${headline ? 'Earnings Summary: ' + headline : 'Earnings Summary'}</div>
            <table class="earnings-table">
                <thead><tr><th>Metric</th><th>Actual</th><th>Consensus</th><th>Variance</th><th>Result</th></tr></thead>
                <tbody>${est.map(r => {
                    const res = (r.result || '').toUpperCase();
                    const resColor = res.includes('BEAT') ? '#26a69a' : res.includes('MISS') ? '#ef5350' : 'var(--text-bright)';
                    return `<tr><td>${r.metric||''}</td><td>${r.actual||''}</td><td>${r.consensus||''}</td><td>${r.variance||''}</td><td style="color:${resColor};font-weight:600">${r.result||''}</td></tr>`;
                }).join('')}</tbody>
            </table>
        </div>`;
    }

    // Key Takeaways — bold lead sentences
    const takeaways = es.key_takeaways || [];
    if (takeaways.length) {
        html += `
        <div class="earnings-section">
            <div class="earnings-section-title">Key Takeaways</div>
            <ul class="earnings-takeaways">${takeaways.map(t => {
                const text = String(t);
                const colonPos = text.indexOf(':');
                const dotPos = text.indexOf('.');
                let splitPos = -1;
                if (colonPos > 4 && colonPos < 80) splitPos = colonPos + 1;
                else if (dotPos > 4 && dotPos < 120) splitPos = dotPos + 1;
                if (splitPos > 0) {
                    return `<li><strong>${text.substring(0, splitPos)}</strong> ${text.substring(splitPos).trim()}</li>`;
                }
                return `<li>${text}</li>`;
            }).join('')}</ul>
        </div>`;
    }

    // Updated Estimates table
    const ue = earnings.updated_estimates || {};
    const uet = ue.table || [];
    if (uet.length) {
        const first = uet[0];
        const cols = ['Metric'];
        const keys = ['metric'];
        if (first.fy_actual_label) { cols.push(first.fy_actual_label); keys.push('fy_actual'); }
        if (first.fy1_label) { cols.push(first.fy1_label); keys.push('fy1_estimate'); }
        if (first.fy2_label) { cols.push(first.fy2_label); keys.push('fy2_estimate'); }
        html += `<div class="earnings-section">
            <div class="earnings-section-title">Updated Financial Estimates</div>
            <table class="earnings-table"><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead>
            <tbody>${uet.map(r => `<tr>${keys.map(k => `<td>${r[k]||''}</td>`).join('')}</tr>`).join('')}</tbody></table>
        </div>`;
    }

    // Revenue Analysis narrative + chart
    const ra = earnings.revenue_analysis || {};
    if (ra.narrative) {
        html += `
        <div class="earnings-section">
            <div class="earnings-section-title">Revenue Analysis</div>
            <div class="earnings-narrative">${ra.narrative}</div>
            ${chartImg('chart1_revenue.png', 'Quarterly Revenue')}
        </div>`;
    }

    // Segment Analysis + chart
    const sa = earnings.segment_analysis || {};
    if (typeof sa === 'object' && (sa.narrative || (sa.segments && sa.segments.length))) {
        html += `<div class="earnings-section"><div class="earnings-section-title">Segment Analysis</div>`;
        if (sa.segments && sa.segments.length) {
            html += `<table class="earnings-table"><thead><tr><th>Segment</th><th>Revenue</th><th>Growth</th><th>Key Drivers</th></tr></thead><tbody>`;
            html += sa.segments.map(s => `<tr><td>${s.name||''}</td><td>${s.revenue||''}</td><td>${s.growth||''}</td><td style="font-size:11px">${s.key_drivers||''}</td></tr>`).join('');
            html += `</tbody></table>`;
        }
        if (sa.narrative) html += `<div class="earnings-narrative">${sa.narrative}</div>`;
        html += chartImg('chart3_segments.png', 'Segment Breakdown');
        html += `</div>`;
    } else if (typeof sa === 'string' && sa) {
        html += `<div class="earnings-section"><div class="earnings-section-title">Segment Analysis</div><div class="earnings-narrative">${sa}</div></div>`;
    }

    // Profitability Analysis + charts
    const pa = earnings.profitability_analysis || {};
    if (pa.narrative) {
        html += `
        <div class="earnings-section">
            <div class="earnings-section-title">Profitability</div>
            <div class="earnings-kv-grid">
                ${pa.gross_margin_current != null ? `<div class="earnings-kv"><span class="earnings-kv-label">Gross Margin</span><span>${typeof pa.gross_margin_current === 'number' ? pa.gross_margin_current.toFixed(1) + '%' : pa.gross_margin_current}</span></div>` : ''}
                ${pa.operating_margin_current != null ? `<div class="earnings-kv"><span class="earnings-kv-label">Op Margin</span><span>${typeof pa.operating_margin_current === 'number' ? pa.operating_margin_current.toFixed(1) + '%' : pa.operating_margin_current}</span></div>` : ''}
                ${pa.eps_current != null ? `<div class="earnings-kv"><span class="earnings-kv-label">EPS</span><span>$${typeof pa.eps_current === 'number' ? pa.eps_current.toFixed(2) : pa.eps_current}</span></div>` : ''}
                ${pa.eps_growth != null ? `<div class="earnings-kv"><span class="earnings-kv-label">EPS Growth</span><span style="color:${pa.eps_growth >= 0 ? '#26a69a' : '#ef5350'}">${typeof pa.eps_growth === 'number' ? (pa.eps_growth > 1 ? pa.eps_growth.toFixed(1) : (pa.eps_growth * 100).toFixed(1)) + '%' : pa.eps_growth}</span></div>` : ''}
            </div>
            <div class="earnings-narrative">${pa.narrative}</div>
            ${chartImg('chart2_eps.png', 'EPS Trend')}
            ${chartImg('chart4_margin.png', 'Gross Margin Trend')}
        </div>`;
    }

    // Operating Highlights + chart
    const oh = earnings.operating_highlights || {};
    const oht = oh.table || [];
    if (oht.length || oh.free_cash_flow_narrative) {
        html += `<div class="earnings-section"><div class="earnings-section-title">Operating Highlights</div>`;
        if (oht.length) {
            html += `<table class="earnings-table"><thead><tr><th>Metric</th><th>Latest Qtr</th><th>FY Current</th><th>FY Prior</th><th>YoY</th></tr></thead><tbody>`;
            html += oht.map(r => `<tr><td>${r.metric||''}</td><td>${r.q_value||''}</td><td>${r.fy_value||''}</td><td>${r.prior_fy_value||''}</td><td>${r.yoy_change||''}</td></tr>`).join('');
            html += `</tbody></table>`;
        }
        if (oh.free_cash_flow_narrative) html += `<div class="earnings-narrative">${oh.free_cash_flow_narrative}</div>`;
        if (oh.capital_allocation) html += `<div class="earnings-narrative">${oh.capital_allocation}</div>`;
        html += chartImg('chart5_fcf.png', 'Operating Highlights');
        html += `</div>`;
    }

    // Guidance
    const gu = earnings.guidance || {};
    const gut = gu.table || [];
    if (gut.length || gu.narrative) {
        html += `<div class="earnings-section"><div class="earnings-section-title">Forward Guidance</div>`;
        if (gut.length) {
            html += `<table class="earnings-table"><thead><tr><th>Metric</th><th>Guidance</th><th>Notes</th></tr></thead><tbody>`;
            html += gut.map(r => `<tr><td>${r.metric||''}</td><td>${r.guidance_range||''}</td><td style="font-size:10px;color:var(--text-secondary)">${r.notes||r.scenario_framework||''}</td></tr>`).join('');
            html += `</tbody></table>`;
        }
        if (gu.narrative) html += `<div class="earnings-narrative">${gu.narrative}</div>`;
        html += chartImg('chart7_estimates.png', 'Estimates');
        html += `</div>`;
    }

    // Investment Thesis Pillars
    const it = earnings.investment_thesis || {};
    const pillars = it.pillars || [];
    if (pillars.length) {
        html += `<div class="earnings-section"><div class="earnings-section-title">Investment Thesis</div>`;
        pillars.forEach(p => {
            const statusColor = (p.status || '').toUpperCase().includes('STRENGTH') ? '#26a69a' :
                               (p.status || '').toUpperCase().includes('WEAKEN') ? '#ef5350' : '#ff9800';
            html += `<div class="earnings-pillar">
                <div class="earnings-pillar-header">
                    <span class="earnings-pillar-name">${p.name || ''}</span>
                    <span class="earnings-pillar-status" style="color:${statusColor}">${p.status || ''}</span>
                </div>
                ${p.detail ? `<div class="earnings-pillar-detail">${p.detail}</div>` : ''}
            </div>`;
        });
        html += `</div>`;
    }

    // Key Risks
    const risks = it.key_risks || [];
    if (risks.length) {
        html += `<div class="earnings-section"><div class="earnings-section-title">Key Risks</div>
            <ul class="earnings-takeaways">${risks.map(r => {
                const text = String(r);
                const colonPos = text.indexOf(':');
                if (colonPos > 2 && colonPos < 60) {
                    return `<li><strong>${text.substring(0, colonPos + 1)}</strong>${text.substring(colonPos + 1)}</li>`;
                }
                return `<li>${text}</li>`;
            }).join('')}</ul></div>`;
    }

    // Peer Valuation + chart
    const val = earnings.valuation || {};
    const peers = val.peer_table || [];
    if (peers.length) {
        html += `<div class="earnings-section"><div class="earnings-section-title">Peer Valuation</div>
            <table class="earnings-table"><thead><tr><th>Company</th><th>Mkt Cap</th><th>Fwd P/E</th><th>EV/EBITDA</th><th>Growth</th><th>Gross Margin</th></tr></thead><tbody>`;
        html += peers.map(p => `<tr><td>${p.company||''}</td><td>${p.market_cap||''}</td><td>${p.forward_pe||''}</td><td>${p.ev_ebitda||''}</td><td>${p.growth||''}</td><td>${p.gross_margin||''}</td></tr>`).join('');
        html += `</tbody></table>`;
        if (val.narrative) html += `<div class="earnings-narrative">${val.narrative}</div>`;
        html += chartImg('chart6_peer_pe.png', 'Peer P/E Comparison');
        html += `</div>`;
    }

    // Price Target Methodology
    const ptm = val.price_target_methodology || [];
    if (ptm.length) {
        html += `<div class="earnings-section"><div class="earnings-section-title">Price Target Methodology</div>
            <table class="earnings-table"><thead><tr><th>Methodology</th><th>Implied Value</th><th>Weight</th><th>Contribution</th></tr></thead><tbody>`;
        html += ptm.map(m => `<tr><td>${m.methodology||''}</td><td>$${m.implied_value||''}</td><td>${m.weight||''}%</td><td>$${m.contribution||''}</td></tr>`).join('');
        if (val.blended_target) html += `<tr style="font-weight:700"><td>Blended Price Target</td><td></td><td></td><td>$${val.blended_target}</td></tr>`;
        html += `</tbody></table>`;
        html += chartImg('chart8_pt.png', 'Price Target');
        html += `</div>`;
    }

    // Key Ratios
    if (Object.keys(ratios).length > 0) {
        html += `<div class="earnings-section"><div class="earnings-section-title">Key Ratios</div><div class="earnings-kv-grid">`;
        const ratioDisplay = [
            ['pe_ratio', 'P/E', 1, false], ['forward_pe', 'Fwd P/E', 1, false],
            ['peg_ratio', 'PEG', 2, false], ['gross_margin', 'Gross Margin', 1, true],
            ['operating_margin', 'Op Margin', 1, true], ['net_margin', 'Net Margin', 1, true],
            ['roe', 'ROE', 1, true], ['debt_to_equity', 'D/E', 2, false]
        ];
        ratioDisplay.forEach(([key, label, dec, isPct]) => {
            if (ratios[key] != null) {
                const v = isPct ? (ratios[key] * 100).toFixed(dec) + '%' : ratios[key].toFixed(dec);
                html += `<div class="earnings-kv"><span class="earnings-kv-label">${label}</span><span>${v}</span></div>`;
            }
        });
        html += `</div></div>`;
    }

    // Appendix
    const appendix = earnings.appendix || {};
    const callHighlights = appendix.earnings_call_highlights || [];
    if (callHighlights.length || appendix.notable_items || appendix.analyst_activity) {
        html += `<div class="earnings-section"><div class="earnings-section-title">Appendix</div>`;
        if (callHighlights.length) {
            html += `<div class="earnings-subsection-title">Earnings Call Highlights</div>`;
            html += callHighlights.map(h => `<div class="earnings-narrative" style="font-style:italic;font-size:11px;margin-bottom:6px">${h}</div>`).join('');
        }
        if (appendix.notable_items) {
            html += `<div class="earnings-subsection-title">Notable Items</div><div class="earnings-narrative">${appendix.notable_items}</div>`;
        }
        if (appendix.analyst_activity) {
            html += `<div class="earnings-subsection-title">Analyst Activity</div><div class="earnings-narrative" style="font-style:italic;font-size:11px">${appendix.analyst_activity}</div>`;
        }
        html += `</div>`;
    }

    // Download buttons
    html += `
        <div class="earnings-download-row">
            <button class="btn-sm btn-green" onclick="window.open('/api/skills/jobs/${jobId}/download/docx','_blank')">Download DOCX</button>
            <button class="btn-sm" onclick="window.open('/api/skills/jobs/${jobId}/download/xlsx','_blank')">Download XLSX</button>
        </div>`;

    wrapper.innerHTML = html;
    container.appendChild(wrapper);
}
