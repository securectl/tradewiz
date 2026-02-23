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

    // Set visible range — show all data with proper bar spacing instead of compressing
    const ohlcvLen = data.ohlcv.length;
    if (ohlcvLen > 0) {
        // For shorter datasets (< 80 bars), show all; for longer, show last ~80 bars to avoid compression
        if (ohlcvLen <= 80) {
            chart.timeScale().fitContent();
        } else {
            const from = data.ohlcv[Math.max(0, ohlcvLen - 80)].time;
            const to = data.ohlcv[ohlcvLen - 1].time;
            chart.timeScale().setVisibleRange({ from, to });
        }
    }
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
        container.innerHTML = '<div style="color:#787b86; font-size:12px;">No Inside Day or Outside Day patterns detected recently.</div>';
        return;
    }

    let html = '';
    recent.forEach(cp => {
        const isInside = cp.type === 'inside_day';
        const color = isInside ? '#42a5f5' : '#e040fb';
        const icon = isInside ? '◇' : '◈';
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

        const data = await resp.json();

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

        const data = await resp.json();

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
        scoreBar('Business Viability', v.business_score || 0, '#42a5f5') +
        scoreBar('Financial Health', v.health_score || 0, '#26a69a') +
        scoreBar('Valuation', v.valuation_score || 0, '#ff9800');
    container.appendChild(scoresDiv);

    // Business Viability Section
    if (data.business_viability && !data.business_viability.error) {
        container.appendChild(buildBizViabilitySection(data.business_viability));
    }

    // Financial Health Section
    if (data.financial_health && !data.financial_health.error) {
        container.appendChild(buildFinHealthSection(data.financial_health));
    }

    // Valuation Section
    if (data.valuation_price && !data.valuation_price.error) {
        container.appendChild(buildValuationSection(data.valuation_price));
    }

    // Models footer
    if (data.models) {
        const footer = document.createElement('div');
        footer.className = 'ai-models-used';
        footer.innerHTML = `Models: ${data.models.business_viability.split('/')[1] || data.models.business_viability} · ${data.models.financial_health.split('/')[1] || data.models.financial_health} · ${data.models.valuation_price.split('/')[1] || data.models.valuation_price}`;
        container.appendChild(footer);
    }
}

function buildBizViabilitySection(biz) {
    const section = document.createElement('div');
    section.className = 'ai-model-section';
    const vClass = biz.verdict === 'INVEST' ? 'bullish' : biz.verdict === 'PASS' ? 'bearish' : 'warning';

    let catalystTags = '';
    (biz.catalysts_12m || []).forEach(c => { catalystTags += `<span class="ai-tag bullish">${c}</span>`; });
    let threatTags = '';
    (biz.competitive_threats || []).forEach(t => { threatTags += `<span class="ai-tag bearish">${t}</span>`; });

    section.innerHTML = `
        <div class="ai-model-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
            <span class="ai-model-title">Business Viability</span>
            <span class="ai-model-badge ai-tag ${vClass}">${biz.verdict} ${biz.confidence}%</span>
        </div>
        <div class="ai-model-body">
            <div style="margin-bottom:8px;">${biz.summary || ''}</div>
            ${biz.moat_assessment ? `<div class="indicator-row"><span class="indicator-label">Moat</span><span class="indicator-value" style="font-size:11px;">${biz.moat_assessment} (${biz.moat_score || 0}/100)</span></div>` : ''}
            ${biz.sector_outlook ? `<div class="indicator-row"><span class="indicator-label">Sector Outlook</span><span class="indicator-value" style="font-size:11px;">${biz.sector_outlook}</span></div>` : ''}
            ${biz.growth_durability ? `<div class="indicator-row"><span class="indicator-label">Growth Durability</span><span class="indicator-value" style="font-size:11px;">${biz.growth_durability}</span></div>` : ''}
            ${biz.revenue_trajectory ? `<div class="indicator-row"><span class="indicator-label">Revenue Trajectory</span><span class="indicator-value" style="font-size:11px;">${biz.revenue_trajectory}</span></div>` : ''}
            ${biz.bull_thesis ? `<div style="margin-top:8px; padding:6px 8px; background:rgba(38,166,154,0.1); border-radius:4px; font-size:11px;"><b style="color:#26a69a;">Bull:</b> ${biz.bull_thesis}</div>` : ''}
            ${biz.bear_thesis ? `<div style="margin-top:4px; padding:6px 8px; background:rgba(239,83,80,0.1); border-radius:4px; font-size:11px;"><b style="color:#ef5350;">Bear:</b> ${biz.bear_thesis}</div>` : ''}
            ${catalystTags ? `<div style="margin-top:8px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">12M CATALYSTS</div>${catalystTags}</div>` : ''}
            ${threatTags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">COMPETITIVE THREATS</div>${threatTags}</div>` : ''}
        </div>
    `;
    return section;
}

function buildFinHealthSection(h) {
    const section = document.createElement('div');
    section.className = 'ai-model-section';
    const vClass = h.verdict === 'INVEST' ? 'bullish' : h.verdict === 'PASS' ? 'bearish' : 'warning';

    let redFlags = '';
    (h.red_flags || []).forEach(f => { redFlags += `<span class="ai-tag bearish">${f}</span>`; });
    let greenFlags = '';
    (h.green_flags || []).forEach(f => { greenFlags += `<span class="ai-tag bullish">${f}</span>`; });

    section.innerHTML = `
        <div class="ai-model-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
            <span class="ai-model-title">Financial Health</span>
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
            ${redFlags ? `<div style="margin-top:8px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">RED FLAGS</div>${redFlags}</div>` : ''}
            ${greenFlags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">GREEN FLAGS</div>${greenFlags}</div>` : ''}
        </div>
    `;
    return section;
}

function buildValuationSection(val) {
    const section = document.createElement('div');
    section.className = 'ai-model-section';
    const vClass = val.verdict === 'INVEST' ? 'bullish' : val.verdict === 'PASS' ? 'bearish' : 'warning';

    let catalystTags = '';
    (val.catalysts_for_rerating || []).forEach(c => { catalystTags += `<span class="ai-tag info">${c}</span>`; });

    const pt = val.price_targets || {};

    section.innerHTML = `
        <div class="ai-model-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
            <span class="ai-model-title">Valuation & Price Target</span>
            <span class="ai-model-badge ai-tag ${vClass}">${val.verdict} ${val.confidence}%</span>
        </div>
        <div class="ai-model-body">
            <div style="margin-bottom:8px;">${val.summary || ''}</div>
            ${val.fair_value ? `<div class="indicator-row"><span class="indicator-label">Fair Value</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${val.fair_value}</span></div>` : ''}
            ${val.current_vs_fair ? `<div class="indicator-row"><span class="indicator-label">Current vs Fair</span><span class="indicator-value" style="font-size:11px;">${val.current_vs_fair}</span></div>` : ''}
            ${val.margin_of_safety_pct != null ? `<div class="indicator-row"><span class="indicator-label">Margin of Safety</span><span class="indicator-value" style="font-size:11px; color:${val.margin_of_safety_pct > 15 ? '#26a69a' : val.margin_of_safety_pct > 0 ? '#ff9800' : '#ef5350'};">${val.margin_of_safety_pct}%</span></div>` : ''}
            ${val.peer_comparison ? `<div class="indicator-row"><span class="indicator-label">vs Peers</span><span class="indicator-value" style="font-size:11px;">${val.peer_comparison}</span></div>` : ''}
            ${val.valuation_assessment ? `<div class="indicator-row"><span class="indicator-label">Growth Priced In?</span><span class="indicator-value" style="font-size:11px;">${val.valuation_assessment}</span></div>` : ''}
            ${val.entry_attractiveness ? `<div class="indicator-row"><span class="indicator-label">Entry Timing</span><span class="indicator-value" style="font-size:11px; color:#2962ff;">${val.entry_attractiveness}</span></div>` : ''}
            ${val.dcf_notes ? `<div style="margin-top:8px; padding:6px 8px; background:rgba(41,98,255,0.08); border-radius:4px; font-size:11px;"><b style="color:#42a5f5;">DCF Notes:</b> ${val.dcf_notes}</div>` : ''}
            ${'bear' in pt || 'base' in pt || 'bull' in pt ? `
            <div style="margin:10px 0;">
                <div style="font-size:10px; color:#787b86; margin-bottom:4px;">12-MONTH SCENARIOS</div>
                ${'bear' in pt ? `<div class="ai-scenario-box bear">Bear: $${pt.bear} (${pt.bear_probability || '?'}% probability)</div>` : ''}
                ${'base' in pt ? `<div class="ai-scenario-box base">Base: $${pt.base} (${pt.base_probability || '?'}% probability)</div>` : ''}
                ${'bull' in pt ? `<div class="ai-scenario-box bull">Bull: $${pt.bull} (${pt.bull_probability || '?'}% probability)</div>` : ''}
            </div>` : ''}
            ${catalystTags ? `<div style="margin-top:6px;"><div style="font-size:10px; color:#787b86; margin-bottom:4px;">RE-RATING CATALYSTS</div>${catalystTags}</div>` : ''}
        </div>
    `;
    return section;
}

// ─── Tracker / Journal ───────────────────────────────────────

let statusRefreshInterval = null;

function switchTab(tab) {
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
    const autotradingContent = document.getElementById('autotrading-content');

    mainContent.style.display = 'none';
    if (qullamaggieContent) qullamaggieContent.style.display = 'none';
    trackerContent.style.display = 'none';
    screenerContent.style.display = 'none';
    statusContent.style.display = 'none';
    if (autotradingContent) autotradingContent.style.display = 'none';

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

    if (tab === 'qullamaggie') {
        qullamaggieContent.style.display = 'flex';
    } else if (tab === 'tracker') {
        trackerContent.style.display = 'grid';
        loadJournal();
        loadGoals();
    } else if (tab === 'screener') {
        screenerContent.style.display = 'flex';
    } else if (tab === 'status') {
        statusContent.style.display = 'flex';
        loadStatus();
        statusRefreshInterval = setInterval(loadStatus, 60000);
    } else if (tab === 'autotrading') {
        autotradingContent.style.display = 'flex';
        loadBotStatus();
        loadBotTrades();
        loadBotPnl();
        loadBotLog();
        loadBotConfig();
        loadBotCoins();
        loadPlatformStatus();
        window._botRefreshInterval = setInterval(() => {
            loadBotStatus();
            loadBotLog();
        }, 5000);
    } else {
        mainContent.style.display = 'grid';
    }
}

function toggleTradeFields() {
    const fields = document.getElementById('journal-trade-fields');
    const btn = document.getElementById('btn-toggle-trade');
    const isHidden = fields.style.display === 'none';
    fields.style.display = isHidden ? 'flex' : 'none';
    btn.classList.toggle('active', isHidden);
}

async function loadJournal() {
    try {
        const resp = await fetch('/api/journal');
        const entries = await resp.json();
        renderJournalEntries(entries);
    } catch (err) {
        console.error('Failed to load journal', err);
    }
}

async function addJournalEntry() {
    const ticker = document.getElementById('journal-ticker').value.trim();
    if (!ticker) return;

    const notes = document.getElementById('journal-notes').value.trim();
    const tradeFields = document.getElementById('journal-trade-fields');
    const isTrade = tradeFields.style.display !== 'none';

    const body = { ticker, notes };

    if (isTrade) {
        body.action = document.getElementById('journal-action').value;
        const ep = document.getElementById('journal-entry-price').value;
        const xp = document.getElementById('journal-exit-price').value;
        const sh = document.getElementById('journal-shares').value;
        if (ep) body.entry_price = parseFloat(ep);
        if (xp) body.exit_price = parseFloat(xp);
        if (sh) body.shares = parseFloat(sh);
    }

    try {
        await fetch('/api/journal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        // Reset form
        document.getElementById('journal-ticker').value = '';
        document.getElementById('journal-notes').value = '';
        document.getElementById('journal-entry-price').value = '';
        document.getElementById('journal-exit-price').value = '';
        document.getElementById('journal-shares').value = '';
        document.getElementById('journal-trade-fields').style.display = 'none';
        document.getElementById('btn-toggle-trade').classList.remove('active');

        loadJournal();
        loadGoals();
    } catch (err) {
        console.error('Failed to add journal entry', err);
    }
}

async function deleteJournalEntry(id) {
    try {
        await fetch(`/api/journal/${id}`, { method: 'DELETE' });
        loadJournal();
        loadGoals();
    } catch (err) {
        console.error('Failed to delete journal entry', err);
    }
}

function renderJournalEntries(entries) {
    const list = document.getElementById('journal-list');
    if (!entries || entries.length === 0) {
        list.innerHTML = '<div style="padding:40px; color:#787b86; text-align:center;">No journal entries yet. Add your first note above.</div>';
        return;
    }

    list.innerHTML = entries.map(e => {
        const date = new Date(e.created_at + 'Z');
        const timeStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        const isTrade = !!e.action;
        const actionClass = e.action === 'BUY' ? 'buy' : e.action === 'SELL' ? 'sell' : '';

        let tradeHtml = '';
        if (isTrade) {
            tradeHtml = `
                <div class="journal-trade-info">
                    <span class="journal-action-tag ${actionClass}">${e.action}</span>
                    ${e.entry_price != null ? `<span class="journal-price">Entry: $${e.entry_price}</span>` : ''}
                    ${e.exit_price != null ? `<span class="journal-price">Exit: $${e.exit_price}</span>` : ''}
                    ${e.shares != null ? `<span class="journal-price">${e.shares} shares</span>` : ''}
                    ${e.pnl != null ? `<span class="journal-pnl ${e.pnl >= 0 ? 'positive' : 'negative'}">${e.pnl >= 0 ? '+' : ''}$${e.pnl.toFixed(2)}</span>` : ''}
                </div>`;
        }

        return `
            <div class="journal-card">
                <div class="journal-card-header">
                    <div class="journal-card-left">
                        <span class="journal-ticker-badge">${e.ticker}</span>
                        <span class="journal-time">${timeStr}</span>
                    </div>
                    <button class="journal-delete-btn" onclick="deleteJournalEntry(${e.id})">&times;</button>
                </div>
                ${e.notes ? `<div class="journal-notes-text">${e.notes}</div>` : ''}
                ${tradeHtml}
            </div>`;
    }).join('');
}

async function loadGoals() {
    try {
        const resp = await fetch('/api/goals');
        const data = await resp.json();
        renderGoalDashboard(data);
    } catch (err) {
        console.error('Failed to load goals', err);
    }
}

async function updateWeeklyTarget() {
    const input = document.getElementById('goal-weekly-target');
    const val = parseFloat(input.value);
    if (isNaN(val) || val <= 0) return;
    try {
        await fetch('/api/goals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ weekly_target: val }),
        });
        loadGoals();
    } catch (err) {
        console.error('Failed to update weekly target', err);
    }
}

async function updateBalance() {
    const input = document.getElementById('goal-current-balance');
    const val = parseFloat(input.value);
    if (isNaN(val) || val < 0) return;
    try {
        await fetch('/api/goals/balance', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ balance: val }),
        });
        loadGoals();
    } catch (err) {
        console.error('Failed to update balance', err);
    }
}

function renderGoalDashboard(data) {
    const container = document.getElementById('goal-dashboard');

    const weeklyPct = Math.min(100, Math.max(0, data.weekly_pct));
    const monthlyPct = Math.min(100, Math.max(0, data.monthly_pct));
    const weeklyBarColor = weeklyPct >= 100 ? '#26a69a' : weeklyPct >= 50 ? '#ff9800' : '#ef5350';
    const monthlyBarColor = monthlyPct >= 100 ? '#26a69a' : monthlyPct >= 50 ? '#ff9800' : '#ef5350';

    let milestonesHtml = data.milestones.map(m => {
        return `
            <div class="milestone-node ${m.reached ? 'reached' : ''}">
                <div class="milestone-dot"></div>
                <div class="milestone-label">${m.label}</div>
            </div>`;
    }).join('');

    container.innerHTML = `
        <!-- Account Balance -->
        <div class="goal-section">
            <div class="goal-section-title">Account Balance</div>
            <div class="goal-balance-row">
                <input type="number" id="goal-current-balance" class="goal-balance-input" value="${data.current_balance}" step="0.01">
                <button class="goal-update-btn" onclick="updateBalance()">Update</button>
            </div>
            <div class="goal-sub-text">Starting: $${data.starting_balance.toLocaleString()}</div>
        </div>

        <!-- Weekly Goal -->
        <div class="goal-section">
            <div class="goal-section-title">Weekly Goal</div>
            <div class="goal-progress-header">
                <span>$${data.weekly_actual.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
                <span>of $${data.weekly_target.toLocaleString()}</span>
            </div>
            <div class="goal-progress-bar">
                <div class="goal-progress-fill" style="width:${weeklyPct}%; background:${weeklyBarColor};"></div>
            </div>
            <div class="goal-sub-text">${data.weekly_pct.toFixed(1)}% complete &middot; Week of ${data.week_start}</div>
            <div class="goal-balance-row" style="margin-top:8px;">
                <input type="number" id="goal-weekly-target" class="goal-balance-input" value="${data.weekly_target}" step="1">
                <button class="goal-update-btn" onclick="updateWeeklyTarget()">Set Target</button>
            </div>
        </div>

        <!-- Monthly Rollup -->
        <div class="goal-section">
            <div class="goal-section-title">Monthly Rollup</div>
            <div class="goal-progress-header">
                <span>$${data.monthly_actual.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
                <span>of $${data.monthly_target.toLocaleString()}</span>
            </div>
            <div class="goal-progress-bar">
                <div class="goal-progress-fill" style="width:${monthlyPct}%; background:${monthlyBarColor};"></div>
            </div>
            <div class="goal-sub-text">${data.monthly_pct.toFixed(1)}% complete</div>
        </div>

        <!-- Milestone Tracker -->
        <div class="goal-section">
            <div class="goal-section-title">Journey: $25K &rarr; $1.5M</div>
            <div class="goal-progress-bar" style="margin-bottom:12px;">
                <div class="goal-progress-fill" style="width:${data.progress_pct}%; background:var(--accent-blue);"></div>
            </div>
            <div class="goal-sub-text" style="margin-bottom:10px;">${data.progress_pct.toFixed(2)}% to goal</div>
            <div class="milestone-track">
                ${milestonesHtml}
            </div>
        </div>
    `;
}

// ─── Screener ────────────────────────────────────────────────

let screenerRunning = false;
let screenerCategory = 'lowcap';
let screenerCache = {};  // keyed by category + sectors combo
const SCREENER_CACHE_TTL = 30 * 60 * 1000;  // 30 minutes

const SCREENER_CATEGORY_CONFIG = {
    lowcap: { label: 'Low-Cap', banner: 'Small-cap stocks ($2-$15) carry elevated risk. Never allocate more than 2% of your account per position. AI vetting does not guarantee safety.', showPrice: true, minDefault: 2, maxDefault: 15 },
    midcap: { label: 'Mid-Cap', banner: 'Mid-cap stocks ($15-$100) offer balanced growth/value. Position sizing up to 5% of account per position.', showPrice: true, minDefault: 15, maxDefault: 100 },
    largecap: { label: 'Large-Cap', banner: 'Large-cap growth stocks ($50+). Growth-focused analysis evaluating revenue acceleration, earnings momentum, and competitive moat.', showPrice: false },
    etf: { label: 'ETFs', banner: 'Growth-focused ETFs. Analysis evaluates sector momentum, thematic tailwinds, expense efficiency, and holdings quality.', showPrice: false },
    metals_mining: { label: 'Metals & Mining', banner: 'Mining stocks are commodity-sensitive. Prices fluctuate with gold, silver, copper, lithium, and uranium markets.', showPrice: false },
    crypto: { label: 'Crypto', banner: 'Cryptocurrencies are highly volatile. Prices can swing 10-20% in a day. Never invest more than you can afford to lose.', showPrice: false },
    ai: { label: 'Artificial Intelligence', banner: 'AI stocks carry high growth expectations and elevated valuations. Many are priced for perfection — earnings misses can cause sharp drops.', showPrice: false },
};

// ─── Sector Filter ───────────────────────────────────────────
const SECTOR_OPTIONS = {
    lowcap: ["Technology","Healthcare","Energy","Financial Services","Industrials","Consumer Cyclical","Basic Materials","Real Estate","Cannabis","Communication Services"],
    midcap: ["Technology","Healthcare","Financial Services","Industrials","Consumer Cyclical","Communication Services","Energy"],
    largecap: ["Technology","Healthcare","Financial Services","Industrials","Consumer Cyclical","Communication Services","Energy","Semiconductors"],
    etf: ["Growth","Semiconductors","AI/Robotics","Cybersecurity","Clean Energy","Biotech","Blockchain","Thematic"],
    metals_mining: ["Gold","Silver","Copper","Lithium","Uranium","Diversified","Royalty/Streaming"],
    crypto: ["Layer 1","Layer 2","DeFi","Meme","Infrastructure"],
    ai: ["Pure-Play AI","AI Chips/Infra","AI Cloud/SaaS","AI Tools","AI Robotics","AI Security"],
};
let selectedSectors = [];

function getScreenerCacheKey() {
    return screenerCategory + '|' + selectedSectors.slice().sort().join(',');
}

function renderSectorPills(category) {
    const container = document.getElementById('sector-pills');
    if (!container) return;
    const options = SECTOR_OPTIONS[category] || [];
    selectedSectors = [];
    let html = '<button class="sector-pill clear-pill" onclick="clearSectorFilter()">Clear All</button>';
    options.forEach(s => {
        html += `<button class="sector-pill" data-sector="${s}" onclick="toggleSectorPill(this, '${s}')">${s}</button>`;
    });
    container.innerHTML = html;
}

function toggleSectorPill(el, sector) {
    const idx = selectedSectors.indexOf(sector);
    if (idx >= 0) {
        selectedSectors.splice(idx, 1);
        el.classList.remove('active');
    } else {
        selectedSectors.push(sector);
        el.classList.add('active');
    }
}

function clearSectorFilter() {
    selectedSectors = [];
    document.querySelectorAll('.sector-pill').forEach(p => p.classList.remove('active'));
}

// ─── Hot Sectors ─────────────────────────────────────────────
let hotSectorsCache = {};  // keyed by period
const HOT_SECTORS_CACHE_TTL = 60 * 60 * 1000;  // 60 minutes
let hotSectorsPeriod = '1mo';
let hotSectorsLoading = false;

function initHotSectorButtons() {
    document.querySelectorAll('#hot-sectors-periods .period-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const period = btn.dataset.period;
            document.querySelectorAll('#hot-sectors-periods .period-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            hotSectorsPeriod = period;
            loadHotSectors(period);
        });
    });
}

async function loadHotSectors(period, force = false) {
    if (hotSectorsLoading) return;

    const cached = hotSectorsCache[period];
    if (!force && cached && (Date.now() - cached.fetchedAt < HOT_SECTORS_CACHE_TTL)) {
        renderHotSectors(cached.data, true, cached.fetchedAt);
        return;
    }

    hotSectorsLoading = true;
    const grid = document.getElementById('hot-sectors-grid');
    grid.innerHTML = `
        <div style="grid-column: 1/-1; padding:20px; text-align:center;">
            <div class="spinner" style="margin:0 auto 12px;"></div>
            <div style="color:#787b86; font-size:12px;">Identifying trending sectors with AI...</div>
        </div>`;

    try {
        const resp = await fetch(`/api/screener/hot-sectors?period=${period}`);
        const data = await resp.json();
        if (data.error && data.sectors.length === 0) {
            grid.innerHTML = `<div style="grid-column:1/-1; padding:20px; color:#ef5350; text-align:center; font-size:13px;">${data.error}</div>`;
        } else {
            hotSectorsCache[period] = { data, fetchedAt: Date.now() };
            renderHotSectors(data, false);
        }
    } catch (err) {
        grid.innerHTML = `<div style="grid-column:1/-1; padding:20px; color:#ef5350; text-align:center; font-size:13px;">Failed to load hot sectors: ${err.message}</div>`;
    }

    hotSectorsLoading = false;
}

function renderHotSectors(data, fromCache = false, fetchedAt = null) {
    const grid = document.getElementById('hot-sectors-grid');
    const sectors = data.sectors || [];

    if (sectors.length === 0) {
        grid.innerHTML = '<div style="grid-column:1/-1; padding:20px; color:#787b86; text-align:center; font-size:13px;">No trending sectors found.</div>';
        return;
    }

    let html = '';
    sectors.forEach(s => {
        const trendClass = (s.trend || 'neutral').toLowerCase();
        const trendArrow = trendClass === 'bullish' ? '↑' : trendClass === 'bearish' ? '↓' : '→';
        const momClass = (s.momentum || 'moderate').toLowerCase();

        const catalystsHtml = (s.catalysts || []).slice(0, 3).map(c => `<li>${c}</li>`).join('');
        const tickersHtml = (s.top_tickers || []).map(t =>
            `<span class="hot-sector-ticker" onclick="analyzeFromScreener('${t}')">${t}</span>`
        ).join('');

        html += `
            <div class="hot-sector-card">
                <div class="hot-sector-rank">${s.rank || ''}</div>
                <div class="hot-sector-name">${s.name || 'Unknown'}</div>
                <span class="hot-sector-trend ${trendClass}">${trendArrow} ${s.trend || 'neutral'}</span>
                <span class="hot-sector-momentum ${momClass}">${s.momentum || 'moderate'}</span>
                ${catalystsHtml ? `<ul class="hot-sector-catalysts">${catalystsHtml}</ul>` : ''}
                ${tickersHtml ? `<div class="hot-sector-tickers">${tickersHtml}</div>` : ''}
                ${s.outlook ? `<div class="hot-sector-outlook">${s.outlook}</div>` : ''}
            </div>`;
    });

    if (fromCache && fetchedAt) {
        const ageMin = Math.floor((Date.now() - fetchedAt) / 60000);
        const ageText = ageMin < 1 ? 'Just fetched' : `Cached ${ageMin}m ago`;
        html += `<div class="hot-sectors-cache-note" style="grid-column:1/-1;">${ageText} · <a href="#" onclick="loadHotSectors('${data.period}', true); return false;" style="color:#2962ff; text-decoration:underline;">Refresh</a></div>`;
    }

    grid.innerHTML = html;
}

function switchScreenerCategory(cat) {
    screenerCategory = cat;
    document.querySelectorAll('.screener-category-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.category === cat);
    });

    const config = SCREENER_CATEGORY_CONFIG[cat];
    document.getElementById('screener-risk-banner').textContent = config.banner;

    // Update sector pills for this category
    renderSectorPills(cat);

    // Show/hide price controls
    const minGroup = document.getElementById('screener-min-group');
    const maxGroup = document.getElementById('screener-max-group');
    if (config.showPrice) {
        minGroup.style.display = '';
        maxGroup.style.display = '';
        document.getElementById('screener-min-price').value = config.minDefault;
        document.getElementById('screener-max-price').value = config.maxDefault;
    } else {
        minGroup.style.display = 'none';
        maxGroup.style.display = 'none';
    }

    // Update risk banner color
    const banner = document.getElementById('screener-risk-banner');
    if (cat === 'lowcap') {
        banner.style.background = 'rgba(239, 83, 80, 0.1)';
        banner.style.borderColor = 'rgba(239, 83, 80, 0.3)';
        banner.style.color = '#ef5350';
    } else if (cat === 'crypto') {
        banner.style.background = 'rgba(247, 185, 36, 0.1)';
        banner.style.borderColor = 'rgba(247, 185, 36, 0.3)';
        banner.style.color = '#f7b924';
    } else if (cat === 'metals_mining') {
        banner.style.background = 'rgba(205, 127, 50, 0.1)';
        banner.style.borderColor = 'rgba(205, 127, 50, 0.3)';
        banner.style.color = '#cd7f32';
    } else if (cat === 'ai') {
        banner.style.background = 'rgba(224, 64, 251, 0.1)';
        banner.style.borderColor = 'rgba(224, 64, 251, 0.3)';
        banner.style.color = '#e040fb';
    } else if (cat === 'midcap') {
        banner.style.background = 'rgba(255, 152, 0, 0.1)';
        banner.style.borderColor = 'rgba(255, 152, 0, 0.3)';
        banner.style.color = '#ff9800';
    } else {
        banner.style.background = 'rgba(41, 98, 255, 0.08)';
        banner.style.borderColor = 'rgba(41, 98, 255, 0.3)';
        banner.style.color = '#2962ff';
    }

    // Show cached results if available for this category + sector combo
    const cacheKey = getScreenerCacheKey();
    const cached = screenerCache[cacheKey];
    if (cached && (Date.now() - cached.timestamp < SCREENER_CACHE_TTL)) {
        renderScreenerResults(cached.data, true);
    }
}

async function runScreener(force = false) {
    if (screenerRunning) return;

    // Check cache first (unless forcing a new scan)
    const cacheKey = getScreenerCacheKey();
    const cached = screenerCache[cacheKey];
    if (!force && cached && (Date.now() - cached.timestamp < SCREENER_CACHE_TTL)) {
        renderScreenerResults(cached.data, true);
        return;
    }

    screenerRunning = true;

    const btn = document.getElementById('btn-screener-run');
    const results = document.getElementById('screener-results');

    btn.disabled = true;
    btn.textContent = 'Scanning...';

    const catLabel = SCREENER_CATEGORY_CONFIG[screenerCategory].label;
    results.innerHTML = `
        <div style="text-align:center; padding:60px;">
            <div class="spinner" style="margin:0 auto 16px;"></div>
            <div style="color:#787b86; font-size:13px;">Scanning ${catLabel} candidates and running AI vetting...</div>
            <div style="color:#787b86; font-size:11px; margin-top:8px;">This may take 30-60 seconds</div>
        </div>
    `;

    const minPrice = parseFloat(document.getElementById('screener-min-price').value) || 2;
    const maxPrice = parseFloat(document.getElementById('screener-max-price').value) || 15;
    const limit = parseInt(document.getElementById('screener-limit').value) || 20;

    try {
        const resp = await fetch('/api/screener', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ min_price: minPrice, max_price: maxPrice, limit, category: screenerCategory, sectors: selectedSectors }),
        });

        const data = await resp.json();

        // Cache the results with sector-aware key
        screenerCache[getScreenerCacheKey()] = { data, timestamp: Date.now() };

        renderScreenerResults(data, false);
    } catch (err) {
        results.innerHTML = `
            <div style="text-align:center; padding:60px; color:#ef5350;">
                <h3>Screener Error</h3>
                <p style="color:#787b86; margin-top:8px;">${err.message}</p>
            </div>
        `;
    }

    btn.disabled = false;
    btn.textContent = 'Scan & Vet with AI';
    screenerRunning = false;
}

function renderScreenerResults(data, fromCache = false) {
    const results = document.getElementById('screener-results');
    const cat = data.category || screenerCategory;

    if (data.error && data.candidates_scanned === 0) {
        results.innerHTML = `
            <div style="text-align:center; padding:60px; color:#ef5350;">
                <h3>No Results</h3>
                <p style="color:#787b86; margin-top:8px;">${data.error}</p>
            </div>
        `;
        return;
    }

    // Category-specific labels
    const positiveLabel = cat === 'largecap' ? 'Strong Growth' : cat === 'etf' ? 'Strong Buy' : cat === 'crypto' ? 'Bullish' : 'Opportunities';
    const cautiousLabel = cat === 'largecap' ? 'Steady' : cat === 'etf' ? 'Accumulate' : cat === 'crypto' ? 'Neutral' : 'Risky';

    // Cache age indicator
    let cacheIndicator = '';
    if (fromCache) {
        const cacheKey = getScreenerCacheKey();
        const cached = screenerCache[cacheKey];
        if (cached) {
            const ageMin = Math.floor((Date.now() - cached.timestamp) / 60000);
            const ageText = ageMin < 1 ? 'Just scanned' : `Cached ${ageMin} min ago`;
            cacheIndicator = ` · <span style="color:#42a5f5;">${ageText}</span> · <a href="#" onclick="runScreener(true); return false;" style="color:#2962ff; text-decoration:underline; cursor:pointer;">Rescan</a>`;
        }
    }

    let html = `
        <div class="screener-summary">
            Scanned ${data.candidates_scanned} candidates |
            <span style="color:#26a69a;">${data.opportunities.length} ${positiveLabel}</span> |
            <span style="color:#ff9800;">${data.risky.length} ${cautiousLabel}</span> |
            <span style="color:#ef5350;">${data.avoided} Avoided</span>
            ${cacheIndicator}
        </div>
    `;

    if (data.opportunities.length > 0) {
        html += `<div class="screener-section-title" style="color:#26a69a;">${positiveLabel}</div>`;
        html += '<div class="screener-grid">';
        data.opportunities.forEach(c => { html += buildScreenerCard(c, 'opportunity', cat); });
        html += '</div>';
    }

    if (data.risky.length > 0) {
        const cautiousSubtitle = cat === 'largecap' ? 'Steady — Moderate Growth' : cat === 'etf' ? 'Accumulate — Gradual Position' : 'Risky — Proceed with Caution';
        html += `<div class="screener-section-title" style="color:#ff9800;">${cautiousSubtitle}</div>`;
        html += '<div class="screener-grid">';
        data.risky.forEach(c => { html += buildScreenerCard(c, 'risky', cat); });
        html += '</div>';
    }

    if (data.opportunities.length === 0 && data.risky.length === 0) {
        html += `<div style="text-align:center; padding:40px; color:#787b86;">No viable opportunities found. All candidates were filtered by AI vetting.</div>`;
    }

    results.innerHTML = html;
}

function buildScreenerCard(c, type, cat) {
    cat = cat || screenerCategory;
    const verdictColor = type === 'opportunity' ? '#26a69a' : '#ff9800';

    // Category-specific catalysts/risks field names
    const catalystList = c.growth_catalysts || c.catalysts || [];
    const flagList = c.red_flags || c.risks || [];
    const catalysts = catalystList.slice(0, 3).map(g => `<span class="ai-tag bullish">${g}</span>`).join('');
    const flags = flagList.slice(0, 3).map(f => `<span class="ai-tag bearish">${f}</span>`).join('');

    // Category-specific metrics (with glossary tooltips)
    let metricsHtml = '';
    if (cat === 'largecap') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Rev Growth")}</div><div class="metric-value" style="color:#26a69a;">${c.revenue_growth_trend || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Earnings Growth", "Earnings")}</div><div class="metric-value" style="color:#42a5f5;">${c.earnings_momentum || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Moat")}</div><div class="metric-value">${c.moat_strength || 'N/A'}</div></div>
            </div>`;
    } else if (cat === 'etf') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Momentum")}</div><div class="metric-value" style="color:#26a69a;">${c.sector_momentum || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">Theme</div><div class="metric-value" style="color:#42a5f5;">${c.thematic_strength || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Expense Ratio", "Expense")}</div><div class="metric-value">${c.expense_efficiency || c.expense_ratio || 'N/A'}</div></div>
            </div>`;
    } else if (cat === 'midcap') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Rev Growth", "Rev Trend")}</div><div class="metric-value" style="color:#26a69a;">${c.revenue_growth_trend || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Upside")}</div><div class="metric-value" style="color:#26a69a;">+${c.upside_pct || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Risk")}</div><div class="metric-value" style="color:${(c.risk_score || 0) >= 60 ? '#ef5350' : (c.risk_score || 0) >= 40 ? '#ff9800' : '#26a69a'};">${c.risk_score || 0}</div></div>
            </div>`;
    } else if (cat === 'metals_mining') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">Commodity</div><div class="metric-value" style="color:#f7b924;">${c.commodity_outlook || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">Production</div><div class="metric-value" style="color:#26a69a;">${c.production_trend || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Upside")}</div><div class="metric-value" style="color:#26a69a;">+${c.upside_pct || 0}%</div></div>
            </div>`;
    } else if (cat === 'crypto') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">Network</div><div class="metric-value" style="color:#42a5f5;">${c.network_strength || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">Adoption</div><div class="metric-value" style="color:#26a69a;">${c.adoption_trend || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Upside")}</div><div class="metric-value" style="color:#26a69a;">+${c.upside_pct || 0}%</div></div>
            </div>`;
    } else if (cat === 'ai') {
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">AI Exposure</div><div class="metric-value" style="color:#e040fb;">${c.ai_exposure || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">Growth</div><div class="metric-value" style="color:#26a69a;">${c.growth_trajectory || 'N/A'}</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Risk")}</div><div class="metric-value" style="color:${(c.risk_score || 0) >= 60 ? '#ef5350' : (c.risk_score || 0) >= 40 ? '#ff9800' : '#26a69a'};">${c.risk_score || 0}</div></div>
            </div>`;
    } else {
        // lowcap
        metricsHtml = `
            <div class="screener-card-metrics">
                <div class="screener-metric"><div class="metric-label">${explainTerm("Confidence")}</div><div class="metric-value">${c.confidence || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Survival")}</div><div class="metric-value" style="color:${(c.survival_12m || 0) >= 80 ? '#26a69a' : (c.survival_12m || 0) >= 60 ? '#ff9800' : '#ef5350'};">${c.survival_12m || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Upside")}</div><div class="metric-value" style="color:#26a69a;">+${c.upside_pct || 0}%</div></div>
                <div class="screener-metric"><div class="metric-label">${explainTerm("Risk")}</div><div class="metric-value" style="color:${(c.risk_score || 0) >= 60 ? '#ef5350' : (c.risk_score || 0) >= 40 ? '#ff9800' : '#26a69a'};">${c.risk_score || 0}</div></div>
            </div>`;
    }

    // Category-specific details (with glossary tooltips)
    let detailHtml = '';
    if (cat === 'lowcap') {
        detailHtml = `
            <div class="indicator-row"><span class="indicator-label">${explainTerm("Dilution")}</span><span class="indicator-value" style="font-size:11px;">${c.dilution_risk || 'N/A'}</span></div>
            <div class="indicator-row"><span class="indicator-label">${explainTerm("Liquidity")}</span><span class="indicator-value" style="font-size:11px;">${c.liquidity_risk || 'N/A'}</span></div>
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    } else if (cat === 'midcap') {
        detailHtml = `
            <div class="indicator-row"><span class="indicator-label">${explainTerm("Earnings Growth", "Earnings")}</span><span class="indicator-value" style="font-size:11px;">${c.earnings_momentum || 'N/A'}</span></div>
            <div class="indicator-row"><span class="indicator-label">${explainTerm("Moat")}</span><span class="indicator-value" style="font-size:11px;">${c.moat_strength || 'N/A'}</span></div>
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    } else if (cat === 'largecap') {
        detailHtml = `
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}
            ${c.upside_pct ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Upside")}</span><span class="indicator-value" style="font-size:11px; color:#26a69a;">+${c.upside_pct}%</span></div>` : ''}
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    } else if (cat === 'etf') {
        detailHtml = `
            ${c.ytd_return ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("YTD Return")}</span><span class="indicator-value" style="font-size:11px; color:#26a69a;">${c.ytd_return}</span></div>` : ''}
            ${c.expense_ratio ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Expense Ratio")}</span><span class="indicator-value" style="font-size:11px;">${c.expense_ratio}</span></div>` : ''}
            <div class="indicator-row"><span class="indicator-label">Holdings Quality</span><span class="indicator-value" style="font-size:11px;">${c.top_holdings_quality || 'N/A'}</span></div>
            ${c.target_allocation_pct ? `<div class="indicator-row"><span class="indicator-label">Target Allocation</span><span class="indicator-value" style="font-size:11px;">${c.target_allocation_pct}%</span></div>` : ''}`;
    } else if (cat === 'metals_mining') {
        detailHtml = `
            <div class="indicator-row"><span class="indicator-label">Reserve Quality</span><span class="indicator-value" style="font-size:11px;">${c.reserve_quality || 'N/A'}</span></div>
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}
            <div class="indicator-row"><span class="indicator-label">${explainTerm("Risk")}</span><span class="indicator-value" style="font-size:11px; color:${(c.risk_score || 0) >= 60 ? '#ef5350' : (c.risk_score || 0) >= 40 ? '#ff9800' : '#26a69a'};">${c.risk_score || 0}</span></div>
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    } else if (cat === 'crypto') {
        detailHtml = `
            <div class="indicator-row"><span class="indicator-label">Tokenomics</span><span class="indicator-value" style="font-size:11px;">${c.tokenomics_rating || 'N/A'}</span></div>
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}`;
    } else if (cat === 'ai') {
        detailHtml = `
            <div class="indicator-row"><span class="indicator-label">Competitive</span><span class="indicator-value" style="font-size:11px;">${c.competitive_position || 'N/A'}</span></div>
            ${c.fair_value ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Fair Value")}</span><span class="indicator-value" style="font-size:11px; color:#42a5f5;">$${c.fair_value}</span></div>` : ''}
            ${c.upside_pct ? `<div class="indicator-row"><span class="indicator-label">${explainTerm("Upside")}</span><span class="indicator-value" style="font-size:11px; color:#26a69a;">+${c.upside_pct}%</span></div>` : ''}
            ${c.position_limit_pct ? `<div class="indicator-row"><span class="indicator-label">Max Position</span><span class="indicator-value" style="font-size:11px;">${c.position_limit_pct}% of account</span></div>` : ''}`;
    }

    return `
        <div class="screener-card ${type}">
            <div class="screener-card-header">
                <div>
                    <span class="screener-ticker">${c.ticker}</span>
                    <span class="screener-name">${c.name || ''}</span>
                </div>
                <span class="screener-verdict" style="color:${verdictColor};">${c.verdict}</span>
            </div>
            <div class="screener-card-price">
                <span>$${c.price}</span>
                <span class="screener-sector">${c.sector || ''}</span>
            </div>
            ${metricsHtml}
            <div class="screener-card-detail">${detailHtml}</div>
            ${catalysts ? `<div style="margin-top:6px;"><div style="font-size:9px; color:#787b86; margin-bottom:3px;">CATALYSTS</div>${catalysts}</div>` : ''}
            ${flags ? `<div style="margin-top:4px;"><div style="font-size:9px; color:#787b86; margin-bottom:3px;">${cat === 'lowcap' ? 'RED FLAGS' : 'RISKS'}</div>${flags}</div>` : ''}
            <div style="margin-top:8px; font-size:11px; color:#787b86; line-height:1.5;">${c.summary || ''}</div>
            <button class="btn-screener-analyze" onclick="analyzeFromScreener('${c.ticker}')">Full Analysis</button>
        </div>
    `;
}

function analyzeFromScreener(ticker) {
    // Switch to analyzer tab and run analysis
    switchTab('analyzer');
    document.getElementById('ticker-input').value = ticker;
    analyzeTicker(ticker, document.getElementById('period-select').value, document.getElementById('interval-select').value);
}

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

// ─── Qullamaggie Breakout Scanner ─────────────────────────────

let qullamaggieRunning = false;

function showQullamaggieSection() {
    // Legacy no-op — Qullamaggie is now its own tab
}

function hideQullamaggieSection() {
    // Legacy no-op — Qullamaggie is now its own tab
}

async function runQullamaggieScan() {
    if (qullamaggieRunning) return;
    qullamaggieRunning = true;
    _updateQullamaggieTabBadge();

    const btn = document.getElementById('btn-qullamaggie-scan');
    const results = document.getElementById('qullamaggie-results');
    const universe = document.getElementById('qullamaggie-universe').value;

    btn.disabled = true;
    btn.textContent = 'Scanning...';
    results.innerHTML = `
        <div style="text-align:center; padding:30px;">
            <div class="spinner" style="margin:0 auto 12px;"></div>
            <div style="color:#787b86; font-size:12px;">Scanning for Qullamaggie setups (HTF, VCP, EP)...</div>
            <div style="color:#787b86; font-size:11px; margin-top:4px;">This may take 1-2 minutes — safe to switch tabs</div>
        </div>`;

    try {
        const resp = await fetch('/api/qullamaggie', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ category: universe }),
        });
        const data = await resp.json();
        renderQullamaggieResults(data);
    } catch (err) {
        results.innerHTML = `<div style="text-align:center; padding:30px; color:#ef5350;">Scan failed: ${err.message}</div>`;
    }

    btn.disabled = false;
    btn.textContent = 'Scan for Setups';
    qullamaggieRunning = false;
    _updateQullamaggieTabBadge();
}

function _updateQullamaggieTabBadge() {
    const scannerBtn = document.querySelector('.tab-btn[data-tab="qullamaggie"]');
    if (!scannerBtn) return;
    if (qullamaggieRunning) {
        if (!scannerBtn.querySelector('.tab-badge')) {
            scannerBtn.insertAdjacentHTML('beforeend', '<span class="tab-badge scanning"></span>');
        }
    } else {
        const badge = scannerBtn.querySelector('.tab-badge');
        if (badge) badge.remove();
    }
}

function renderQullamaggieResults(data) {
    const container = document.getElementById('qullamaggie-results');
    const results = data.results || [];

    if (results.length === 0) {
        container.innerHTML = `
            <div style="text-align:center; padding:30px; color:#787b86;">
                No qualifying setups found out of ${data.scanned || 0} stocks scanned.
                <br><span style="font-size:11px;">Try again later — setups appear when stocks are consolidating after strong moves.</span>
            </div>`;
        return;
    }

    let html = `<div style="font-size:12px; color:#787b86; margin-bottom:12px;">Found ${results.length} setups from ${data.scanned} stocks scanned</div>`;
    html += '<div class="qullamaggie-grid">';

    results.forEach(r => {
        const badgeClass = r.setup_type === 'HTF' ? 'setup-badge-htf' : r.setup_type === 'VCP' ? 'setup-badge-vcp' : 'setup-badge-ep';
        const scoreColor = r.score >= 8 ? '#26a69a' : r.score >= 6 ? '#ff9800' : '#787b86';

        html += `
            <div class="qullamaggie-card" onclick="analyzeFromScreener('${r.ticker}')">
                <div class="qullamaggie-card-header">
                    <div>
                        <span class="qullamaggie-ticker">${r.ticker}</span>
                        <span class="qullamaggie-name">${r.name || ''}</span>
                    </div>
                    <span class="${badgeClass}">${r.setup_type}</span>
                </div>
                <div class="qullamaggie-card-price">$${r.current_price} <span style="color:#787b86; font-size:11px;">${r.sector || ''}</span></div>
                <div class="qullamaggie-card-metrics">
                    <div class="qullamaggie-metric"><div class="metric-label">Score</div><div class="metric-value" style="color:${scoreColor};">${r.score}/10</div></div>
                    <div class="qullamaggie-metric"><div class="metric-label">Entry</div><div class="metric-value" style="color:#2962ff;">$${r.entry_price}</div></div>
                    <div class="qullamaggie-metric"><div class="metric-label">Stop</div><div class="metric-value" style="color:#ef5350;">$${r.stop_loss}</div></div>
                    <div class="qullamaggie-metric"><div class="metric-label">ADR%</div><div class="metric-value">${r.adr_pct}%</div></div>
                </div>
                <div class="qullamaggie-card-detail">
                    <div class="indicator-row"><span class="indicator-label">1M RS</span><span class="indicator-value" style="font-size:11px; color:${r.rel_strength_1m >= 25 ? '#26a69a' : 'inherit'};">${r.rel_strength_1m}%</span></div>
                    <div class="indicator-row"><span class="indicator-label">3M RS</span><span class="indicator-value" style="font-size:11px; color:${r.rel_strength_3m >= 50 ? '#26a69a' : 'inherit'};">${r.rel_strength_3m}%</span></div>
                    <div class="indicator-row"><span class="indicator-label">Vol Ratio</span><span class="indicator-value" style="font-size:11px;">${r.volume_ratio}x</span></div>
                    <div class="indicator-row"><span class="indicator-label">$ Vol</span><span class="indicator-value" style="font-size:11px;">${r.dollar_volume}M</span></div>
                    ${r.consolidation_days ? `<div class="indicator-row"><span class="indicator-label">Consol.</span><span class="indicator-value" style="font-size:11px;">${r.consolidation_days}d / -${r.consolidation_depth_pct}%</span></div>` : ''}
                    ${r.prior_move_pct ? `<div class="indicator-row"><span class="indicator-label">Prior Move</span><span class="indicator-value" style="font-size:11px; color:#26a69a;">+${r.prior_move_pct}%</span></div>` : ''}
                    <div class="indicator-row"><span class="indicator-label">MA Aligned</span><span class="indicator-value" style="font-size:11px; color:${r.ma_aligned ? '#26a69a' : '#ef5350'};">${r.ma_aligned ? 'Yes' : 'No'}</span></div>
                </div>
                <div style="margin-top:6px; padding:6px 8px; background:rgba(41,98,255,0.08); border-radius:4px; font-size:10px; color:#787b86;">
                    ${r.shares} shares @ $${r.entry_price} = $${r.position_value} | Risk: $${r.risk_amount}
                    <br>${r.sell_plan}
                </div>
            </div>`;
    });

    html += '</div>';
    container.innerHTML = html;
}

// ─── Event Listeners ─────────────────────────────────────────

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

// MA toggle listeners
for (const [key, config] of Object.entries(MA_CONFIG)) {
    const cb = document.getElementById(config.checkboxId);
    if (cb) cb.addEventListener('change', () => toggleMA(key));
}
document.getElementById('toggle-volume').addEventListener('change', toggleVolume);

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

// Check AI status on load
checkAIStatus();

// Init screener sector pills + hot sector period buttons
renderSectorPills('lowcap');
initHotSectorButtons();

// Focus ticker input on load
document.getElementById('ticker-input').focus();


// ═══════════════════════════════════════════════════════════════
// AUTO TRADING BOT — Dashboard Functions
// ═══════════════════════════════════════════════════════════════

let botPnlChart = null;
let botPnlSeries = null;

async function loadBotStatus() {
    try {
        const resp = await fetch('/api/bot/status');
        const data = await resp.json();

        // Status indicator
        const dot = document.getElementById('bot-dot');
        const statusText = document.getElementById('bot-status-text');
        if (data.running) {
            dot.className = 'bot-dot running';
            statusText.textContent = 'Running';
        } else if (data.kill_switch) {
            dot.className = 'bot-dot killed';
            statusText.textContent = 'KILLED';
        } else {
            dot.className = 'bot-dot stopped';
            statusText.textContent = 'Stopped';
        }

        // Balance
        const bal = data.balance || {};
        document.getElementById('bot-balance').textContent = '$' + (bal.total_equity || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});

        // Daily P&L
        const dailyEl = document.getElementById('bot-daily-pnl');
        const dp = data.daily_pnl || 0;
        dailyEl.textContent = (dp >= 0 ? '+' : '') + '$' + dp.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
        dailyEl.className = 'bot-stat-value ' + (dp >= 0 ? 'positive' : 'negative');

        // Daily Goal progress
        const goalEl = document.getElementById('bot-daily-goal-progress');
        const dailyGoal = parseFloat((data.config || {}).daily_goal || '50');
        const goalPct = dailyGoal > 0 ? Math.min(100, (dp / dailyGoal * 100)) : 0;
        goalEl.textContent = `$${dp.toFixed(2)} / $${dailyGoal.toFixed(0)}`;
        goalEl.className = 'bot-stat-value ' + (dp >= dailyGoal ? 'positive' : dp > 0 ? 'positive' : dp < 0 ? 'negative' : '');

        // Open Positions
        const posEl = document.getElementById('bot-positions');
        const positions = data.positions || [];
        if (positions.length === 0) {
            posEl.innerHTML = '<div class="bot-empty">No open positions</div>';
        } else {
            posEl.innerHTML = positions.map(p => {
                const pnlClass = (p.unrealized_pnl || 0) >= 0 ? 'positive' : 'negative';
                const pnlSign = (p.unrealized_pnl || 0) >= 0 ? '+' : '';
                return `<div class="bot-position-row">
                    <span class="bot-pos-coin">${p.coin}</span>
                    <span class="bot-pos-side ${p.side}">${p.side}</span>
                    <span class="bot-pos-pnl ${pnlClass}">${pnlSign}$${(p.unrealized_pnl || 0).toFixed(2)}</span>
                </div>`;
            }).join('');
        }

        // Button states
        document.getElementById('btn-bot-start').disabled = data.running;
        document.getElementById('btn-bot-stop').disabled = !data.running;

        if (!data.blofin_configured) {
            document.getElementById('bot-balance').textContent = 'Not configured';
        }
    } catch (e) {
        console.error('Failed to load bot status:', e);
    }
}

async function startBot() {
    try {
        const resp = await fetch('/api/bot/start', {method: 'POST'});
        const data = await resp.json();
        if (!data.ok) alert(data.error || 'Failed to start bot');
        loadBotStatus();
    } catch (e) {
        alert('Failed to start bot: ' + e.message);
    }
}

async function stopBot() {
    try {
        const resp = await fetch('/api/bot/stop', {method: 'POST'});
        await resp.json();
        loadBotStatus();
    } catch (e) {
        alert('Failed to stop bot: ' + e.message);
    }
}

async function killSwitch() {
    if (!confirm('KILL SWITCH: This will stop the bot and close ALL open positions. Continue?')) return;
    try {
        const resp = await fetch('/api/bot/kill', {method: 'POST'});
        await resp.json();
        loadBotStatus();
        loadBotTrades();
    } catch (e) {
        alert('Kill switch failed: ' + e.message);
    }
}

async function loadBotTrades() {
    try {
        const resp = await fetch('/api/bot/trades?limit=50');
        const trades = await resp.json();
        const tbody = document.getElementById('bot-trades-body');

        if (!trades.length) {
            tbody.innerHTML = '<tr><td colspan="8" class="bot-empty">No trades yet</td></tr>';
            return;
        }

        tbody.innerHTML = trades.map(t => {
            const pnlClass = t.pnl > 0 ? 'positive' : t.pnl < 0 ? 'negative' : '';
            const pnlStr = t.pnl != null ? (t.pnl >= 0 ? '+' : '') + '$' + t.pnl.toFixed(2) : '—';
            const exitStr = t.exit_price != null ? '$' + t.exit_price.toLocaleString() : '—';
            const entryStr = t.entry_price != null ? '$' + t.entry_price.toLocaleString() : '—';
            const statusClass = t.status === 'open' ? 'status-open' : t.status === 'closed' ? 'status-closed' : 'status-killed';
            const dt = t.opened_at ? new Date(t.opened_at).toLocaleString() : '';
            return `<tr>
                <td>${t.id}</td>
                <td>${t.coin}</td>
                <td class="side-${t.side}">${t.side}</td>
                <td>${entryStr}</td>
                <td>${exitStr}</td>
                <td class="${pnlClass}">${pnlStr}</td>
                <td><span class="${statusClass}">${t.status}</span></td>
                <td>${dt}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        console.error('Failed to load bot trades:', e);
    }
}

async function loadTrending() {
    const feed = document.getElementById('bot-trending');
    const btn = document.getElementById('btn-refresh-trending');
    btn.disabled = true;
    btn.textContent = 'Scanning...';
    feed.innerHTML = `
        <div class="bot-trending-loading">
            <div class="spinner" style="width:18px;height:18px;margin:0 auto 8px;"></div>
            <div>Scanning 24 coins — analyzing with AI...</div>
        </div>`;

    try {
        const resp = await fetch('/api/bot/trending');
        const data = await resp.json();

        if (data.error) {
            feed.innerHTML = `<div class="bot-empty">${data.error}</div>`;
            btn.disabled = false;
            btn.textContent = 'Refresh';
            return;
        }

        const analysis = data.analysis || {};
        const trending = analysis.trending || [];
        const mood = analysis.market_mood || 'unknown';
        const summary = analysis.summary || '';
        const mktData = data.market_data || [];

        let html = '';

        // Market mood badge
        const moodColors = {bullish: 'var(--accent-green)', bearish: 'var(--accent-red)', neutral: 'var(--text-secondary)', mixed: 'var(--accent-orange)'};
        html += `<div class="trending-mood">
            <span class="mood-badge" style="color:${moodColors[mood] || 'var(--text-secondary)'}">Market: ${mood}</span>
            <span class="mood-time">${data.timestamp || ''}</span>
        </div>`;

        // Summary
        if (summary) {
            html += `<div class="trending-summary">${summary}</div>`;
        }

        // Trending coins
        if (trending.length > 0) {
            html += '<div class="trending-coins">';
            trending.forEach(t => {
                const signalClass = t.signal === 'hot' ? 'signal-hot' : t.signal === 'warming' ? 'signal-warm' : 'signal-vol';
                const signalLabel = t.signal === 'hot' ? 'HOT' : t.signal === 'warming' ? 'WARMING' : 'VOL';
                // Find price data
                const md = mktData.find(m => m.ticker === t.ticker || m.coin === t.coin);
                const priceStr = md ? `$${md.price.toLocaleString()}` : '';
                const chgStr = md ? `${md.chg_2h >= 0 ? '+' : ''}${md.chg_2h}%` : '';
                const chgClass = md && md.chg_2h >= 0 ? 'positive' : 'negative';
                const volStr = md && md.vol_surge > 1.2 ? `${md.vol_surge}x vol` : '';

                html += `<div class="trending-coin-row">
                    <div class="trending-coin-header">
                        <span class="trending-coin-name">${t.coin}</span>
                        <span class="trending-signal ${signalClass}">${signalLabel}</span>
                        ${priceStr ? `<span class="trending-price">${priceStr}</span>` : ''}
                        ${chgStr ? `<span class="trending-chg ${chgClass}">${chgStr}</span>` : ''}
                        ${volStr ? `<span class="trending-vol">${volStr}</span>` : ''}
                    </div>
                    <div class="trending-reason">${t.reason}</div>
                </div>`;
            });
            html += '</div>';
        }

        // Top movers mini-table (price data)
        if (mktData.length > 0) {
            html += '<div class="trending-movers-title">Top Movers (2h)</div>';
            html += '<div class="trending-movers">';
            mktData.slice(0, 6).forEach(m => {
                const chgClass = m.chg_2h >= 0 ? 'positive' : 'negative';
                const volBadge = m.vol_surge >= 1.5 ? `<span class="vol-spike">${m.vol_surge}x</span>` : '';
                html += `<div class="trending-mover">
                    <span class="mover-name">${m.coin}</span>
                    <span class="mover-chg ${chgClass}">${m.chg_2h >= 0 ? '+' : ''}${m.chg_2h}%</span>
                    ${volBadge}
                </div>`;
            });
            html += '</div>';
        }

        feed.innerHTML = html;
    } catch (e) {
        feed.innerHTML = `<div class="bot-empty">Failed to load trending: ${e.message}</div>`;
    }

    btn.disabled = false;
    btn.textContent = 'Refresh';
}

async function loadBotPnl() {
    try {
        const resp = await fetch('/api/bot/pnl?days=30');
        const data = await resp.json();

        const chartContainer = document.getElementById('bot-pnl-chart');
        if (!data.length) {
            chartContainer.innerHTML = '<div class="bot-empty" style="padding-top:80px;">No P&L data yet</div>';
            return;
        }

        // Destroy old chart
        if (botPnlChart) {
            botPnlChart.remove();
            botPnlChart = null;
        }

        chartContainer.innerHTML = '';
        botPnlChart = LightweightCharts.createChart(chartContainer, {
            width: chartContainer.clientWidth,
            height: 250,
            layout: {
                background: {type: 'solid', color: '#1e222d'},
                textColor: '#787b86',
            },
            grid: {
                vertLines: {color: '#2a2e39'},
                horzLines: {color: '#2a2e39'},
            },
            rightPriceScale: {borderColor: '#363a45'},
            timeScale: {borderColor: '#363a45'},
        });

        // Cumulative P&L as area chart
        let cumulative = 0;
        const seriesData = data.map(d => {
            cumulative += d.total_pnl;
            return {time: d.date, value: parseFloat(cumulative.toFixed(2))};
        });

        botPnlSeries = botPnlChart.addAreaSeries({
            lineColor: cumulative >= 0 ? '#26a69a' : '#ef5350',
            topColor: cumulative >= 0 ? 'rgba(38,166,154,0.3)' : 'rgba(239,83,80,0.3)',
            bottomColor: cumulative >= 0 ? 'rgba(38,166,154,0.05)' : 'rgba(239,83,80,0.05)',
            lineWidth: 2,
        });
        botPnlSeries.setData(seriesData);
        botPnlChart.timeScale().fitContent();

        // Resize observer
        const ro = new ResizeObserver(() => {
            if (botPnlChart) botPnlChart.applyOptions({width: chartContainer.clientWidth});
        });
        ro.observe(chartContainer);
    } catch (e) {
        console.error('Failed to load bot P&L:', e);
    }
}

async function loadBotLog() {
    try {
        const resp = await fetch('/api/bot/log?limit=50');
        const logs = await resp.json();
        const logEl = document.getElementById('bot-log');

        if (!logs.length) {
            logEl.innerHTML = '<div class="bot-empty">No activity yet</div>';
            return;
        }

        logEl.innerHTML = logs.map(l => {
            const levelClass = l.level === 'error' ? 'log-error' : l.level === 'warn' ? 'log-warn' : 'log-info';
            const time = l.created_at ? new Date(l.created_at).toLocaleTimeString() : '';
            return `<div class="bot-log-entry ${levelClass}">
                <span class="bot-log-time">${time}</span>
                <span class="bot-log-level">[${l.level}]</span>
                <span class="bot-log-msg">${l.message}</span>
            </div>`;
        }).join('');
    } catch (e) {
        console.error('Failed to load bot log:', e);
    }
}

async function loadBotConfig() {
    try {
        const resp = await fetch('/api/bot/config');
        const cfg = await resp.json();
        const el = (id) => document.getElementById(id);
        if (cfg.daily_goal) el('bot-cfg-daily-goal').value = cfg.daily_goal;
        if (cfg.max_position_pct) el('bot-cfg-max-pct').value = cfg.max_position_pct;
        if (cfg.daily_loss_limit) el('bot-cfg-loss-limit').value = cfg.daily_loss_limit;
        if (cfg.max_open_positions) el('bot-cfg-max-open').value = cfg.max_open_positions;
        if (cfg.scan_interval_sec) el('bot-cfg-interval').value = cfg.scan_interval_sec;
        if (cfg.platform) el('bot-cfg-platform').value = cfg.platform;
        if (cfg.trading_mode) el('bot-cfg-trading-mode').value = cfg.trading_mode;
        if (cfg.direction_bias) el('bot-cfg-direction-bias').value = cfg.direction_bias;
        updateTradingModeNote();
    } catch (e) {
        console.error('Failed to load bot config:', e);
    }
}

async function loadBotCoins() {
    try {
        const resp = await fetch('/api/bot/coins');
        const data = await resp.json();
        const picker = document.getElementById('bot-coin-picker');

        picker.innerHTML = data.coins.map(c => `
            <label class="bot-coin-chip ${c.selected ? 'selected' : ''}">
                <input type="checkbox" value="${c.key}" ${c.selected ? 'checked' : ''} onchange="this.parentElement.classList.toggle('selected', this.checked)">
                <span>${c.name}</span>
            </label>
        `).join('');
    } catch (e) {
        console.error('Failed to load bot coins:', e);
    }
}

async function updateBotCoins() {
    const checkboxes = document.querySelectorAll('#bot-coin-picker input[type="checkbox"]');
    const selected = Array.from(checkboxes).filter(cb => cb.checked).map(cb => cb.value);

    if (selected.length === 0) {
        alert('Select at least one coin.');
        return;
    }

    try {
        const resp = await fetch('/api/bot/coins', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({selected}),
        });
        const data = await resp.json();
        if (data.ok) {
            const btn = document.querySelector('.btn-bot-save-coins');
            btn.textContent = 'Saved!';
            btn.style.background = 'var(--accent-green)';
            setTimeout(() => { btn.textContent = 'Save Coins'; btn.style.background = ''; }, 1500);
        } else {
            alert(data.error || 'Failed to save coins');
        }
    } catch (e) {
        alert('Failed to save coins: ' + e.message);
    }
}

async function updateBotConfig() {
    try {
        const payload = {
            daily_goal: document.getElementById('bot-cfg-daily-goal').value,
            max_position_pct: document.getElementById('bot-cfg-max-pct').value,
            daily_loss_limit: document.getElementById('bot-cfg-loss-limit').value,
            max_open_positions: document.getElementById('bot-cfg-max-open').value,
            scan_interval_sec: document.getElementById('bot-cfg-interval').value,
            trading_mode: document.getElementById('bot-cfg-trading-mode').value,
            direction_bias: document.getElementById('bot-cfg-direction-bias').value,
        };
        const resp = await fetch('/api/bot/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data.ok) {
            // Flash save button green briefly
            const btn = document.querySelector('.btn-bot-save-config');
            btn.textContent = 'Saved!';
            btn.style.background = 'var(--accent-green)';
            setTimeout(() => {
                btn.textContent = 'Save Settings';
                btn.style.background = '';
            }, 1500);
        }
    } catch (e) {
        alert('Failed to save config: ' + e.message);
    }
}

const PLATFORM_NOTES = {
    blofin: 'Paper trading on BloFin demo API — no real funds',
    bybit: 'Testnet trading on Bybit — no real funds (coming soon)',
    okx: 'Demo trading on OKX — no real funds (coming soon)',
    binance: 'Testnet trading on Binance — no real funds (coming soon)',
};

function onPlatformChange() {
    const platform = document.getElementById('bot-cfg-platform').value;
    document.getElementById('bot-platform-note').textContent = PLATFORM_NOTES[platform] || '';
    // Show/hide passphrase field (BloFin needs it, others may not)
    const passRow = document.getElementById('bot-cfg-passphrase').closest('.bot-config-row');
    passRow.style.display = platform === 'blofin' ? 'flex' : 'none';
}

async function loadPlatformStatus() {
    try {
        const resp = await fetch('/api/bot/platform');
        const data = await resp.json();
        const statusEl = document.getElementById('bot-key-status');

        if (data.configured) {
            statusEl.innerHTML = '<span class="key-status-ok">Keys configured</span>';
        } else if (data.has_key || data.has_secret) {
            statusEl.innerHTML = '<span class="key-status-partial">Partially configured — missing fields</span>';
        } else {
            statusEl.innerHTML = '<span class="key-status-none">No API keys set</span>';
        }

        if (data.platform) {
            document.getElementById('bot-cfg-platform').value = data.platform;
            onPlatformChange();
        }
    } catch (e) {
        console.error('Failed to load platform status:', e);
    }
}

async function savePlatformKeys() {
    const platform = document.getElementById('bot-cfg-platform').value;
    const apiKey = document.getElementById('bot-cfg-api-key').value;
    const apiSecret = document.getElementById('bot-cfg-api-secret').value;
    const passphrase = document.getElementById('bot-cfg-passphrase').value;

    if (!apiKey && !apiSecret && !passphrase) {
        alert('Enter at least one field to save.');
        return;
    }

    try {
        const resp = await fetch('/api/bot/platform', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({platform, api_key: apiKey, api_secret: apiSecret, passphrase}),
        });
        const data = await resp.json();
        if (data.ok) {
            const btn = document.querySelector('.btn-bot-save-keys');
            btn.textContent = 'Saved!';
            btn.style.background = 'var(--accent-green)';
            setTimeout(() => { btn.textContent = 'Save Keys'; btn.style.background = ''; }, 1500);
            // Clear inputs after save
            document.getElementById('bot-cfg-api-key').value = '';
            document.getElementById('bot-cfg-api-secret').value = '';
            document.getElementById('bot-cfg-passphrase').value = '';
            loadPlatformStatus();
        } else {
            alert(data.error || 'Failed to save keys');
        }
    } catch (e) {
        alert('Failed to save keys: ' + e.message);
    }
}

async function testPlatformConnection() {
    const btn = document.querySelector('.btn-bot-test-keys');
    const statusEl = document.getElementById('bot-key-status');
    btn.disabled = true;
    btn.textContent = 'Testing...';
    statusEl.innerHTML = '<span class="key-status-testing">Connecting...</span>';

    try {
        const resp = await fetch('/api/bot/test-connection', {method: 'POST'});
        const data = await resp.json();
        if (data.ok) {
            statusEl.innerHTML = `<span class="key-status-ok">${data.message}</span>`;
        } else {
            statusEl.innerHTML = `<span class="key-status-error">${data.error}</span>`;
        }
    } catch (e) {
        statusEl.innerHTML = `<span class="key-status-error">Connection failed: ${e.message}</span>`;
    }

    btn.disabled = false;
    btn.textContent = 'Test Connection';
}

function updateTradingModeNote() {
    const mode = document.getElementById('bot-cfg-trading-mode').value;
    const note = document.getElementById('bot-trading-mode-note');
    const platform = document.getElementById('bot-cfg-platform').value;
    if (!note) return;
    const notes = {
        'blofin':  {futures: 'BloFin demo only supports futures (perpetual swaps)', spot: 'BloFin demo does not support spot trading — using futures'},
        'bybit':   {futures: 'Bybit testnet futures trading', spot: 'Bybit testnet spot trading'},
        'okx':     {futures: 'OKX demo futures trading', spot: 'OKX demo spot trading'},
        'binance': {futures: 'Binance testnet futures trading', spot: 'Binance testnet spot trading'},
    };
    const pn = notes[platform] || notes['blofin'];
    note.textContent = pn[mode] || pn.futures;
}

async function checkTradePermissions() {
    const statusEl = document.getElementById('bot-permission-status');
    statusEl.innerHTML = '<span class="key-status-testing">Checking permissions...</span>';
    try {
        const resp = await fetch('/api/bot/check-permissions', {method: 'POST'});
        const data = await resp.json();
        if (data.has_trade_permission) {
            statusEl.innerHTML = `<span class="key-status-ok">Trade permission OK (uid: ${data.uid})</span>`;
        } else {
            statusEl.innerHTML = `<span class="key-status-error">${data.message}</span>`;
        }
    } catch (e) {
        statusEl.innerHTML = `<span class="key-status-error">Check failed: ${e.message}</span>`;
    }
}

async function placeTestTrade() {
    const coin = document.getElementById('bot-test-coin').value;
    const side = document.getElementById('bot-test-side').value;
    const resultEl = document.getElementById('bot-test-trade-result');
    const btn = document.querySelector('.btn-bot-test-trade');

    btn.disabled = true;
    btn.textContent = 'Placing...';
    resultEl.innerHTML = '<span class="key-status-testing">Sending order...</span>';

    try {
        const resp = await fetch('/api/bot/test-trade', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({coin, side}),
        });
        const data = await resp.json();
        if (data.ok) {
            resultEl.innerHTML = `<span class="key-status-ok">${data.message}</span>`;
        } else {
            resultEl.innerHTML = `<span class="key-status-error">${data.error}</span>`;
        }
    } catch (e) {
        resultEl.innerHTML = `<span class="key-status-error">Failed: ${e.message}</span>`;
    }

    btn.disabled = false;
    btn.textContent = 'Place Test Trade (Min Size)';
}


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
        if (data.api_keys.openrouter.configured) {
            orStatus.textContent = 'Configured (' + data.api_keys.openrouter.masked_value + ')';
            orStatus.className = 'settings-status configured';
        } else {
            orStatus.textContent = 'Not configured';
            orStatus.className = 'settings-status not-configured';
        }

        const bfStatus = document.getElementById('set-blofin-key-status');
        if (data.api_keys.blofin_api_key.configured) {
            bfStatus.textContent = 'Configured (' + data.api_keys.blofin_api_key.masked_value + ')';
            bfStatus.className = 'settings-status configured';
        } else {
            bfStatus.textContent = 'Not configured';
            bfStatus.className = 'settings-status not-configured';
        }

        // LLM Models — not secret, show current values
        document.getElementById('set-llm-research').value = data.llm_models.LLM_RESEARCH || '';
        document.getElementById('set-llm-research-fast').value = data.llm_models.LLM_RESEARCH_FAST || '';
        document.getElementById('set-llm-pattern').value = data.llm_models.LLM_PATTERN || '';
        document.getElementById('set-llm-prediction').value = data.llm_models.LLM_PREDICTION || '';
        document.getElementById('set-llm-screener').value = data.llm_models.LLM_SCREENER || '';
        document.getElementById('set-llm-bot-sentiment').value = data.llm_models.LLM_BOT_SENTIMENT || '';
        document.getElementById('set-llm-bot-risk').value = data.llm_models.LLM_BOT_RISK || '';

        // LLM Settings
        document.getElementById('set-llm-max-tokens').value = data.llm_settings.LLM_MAX_TOKENS || 2048;
        document.getElementById('set-llm-temperature').value = data.llm_settings.LLM_TEMPERATURE || 0.2;
        document.getElementById('set-llm-fast-mode').checked = !!data.llm_settings.LLM_FAST_MODE;

        // Ollama
        document.getElementById('set-ollama-url').value = data.ollama.OLLAMA_URL || '';
        document.getElementById('set-ollama-model').value = data.ollama.OLLAMA_MODEL || '';
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
        },
        llm_models: {
            LLM_RESEARCH: document.getElementById('set-llm-research').value,
            LLM_RESEARCH_FAST: document.getElementById('set-llm-research-fast').value,
            LLM_PATTERN: document.getElementById('set-llm-pattern').value,
            LLM_PREDICTION: document.getElementById('set-llm-prediction').value,
            LLM_SCREENER: document.getElementById('set-llm-screener').value,
            LLM_BOT_SENTIMENT: document.getElementById('set-llm-bot-sentiment').value,
            LLM_BOT_RISK: document.getElementById('set-llm-bot-risk').value,
        },
        llm_settings: {
            LLM_MAX_TOKENS: parseInt(document.getElementById('set-llm-max-tokens').value) || null,
            LLM_TEMPERATURE: parseFloat(document.getElementById('set-llm-temperature').value),
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
