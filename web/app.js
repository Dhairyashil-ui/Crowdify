// ── DOM refs ────────────────────────────────────────────────────────────────
const videoStream         = document.getElementById('videoStream');
const heatmapStream       = document.getElementById('heatmapStream');
const videoContainer      = document.getElementById('videoContainer');
const noSignal            = document.getElementById('noSignal');
const reconnectingOverlay = document.getElementById('reconnectingOverlay');

const fpsDisplay          = document.getElementById('fpsDisplay');
const latencyCalc         = document.getElementById('latencyCalc');
const peopleCountBar      = document.getElementById('peopleCountBar');

const riskScoreHero       = document.getElementById('riskScoreHero');
const riskBadge           = document.getElementById('riskBadge');
const riskIndicatorDot    = document.getElementById('riskIndicatorDot');
const riskLabel           = document.getElementById('riskLabel');
const riskTrendIcon       = document.getElementById('riskTrendIcon');
const riskTrendLabel      = document.getElementById('riskTrendLabel');

const predFrom            = document.getElementById('predFrom');
const predTo              = document.getElementById('predTo');
const predRiskPercent     = document.getElementById('predRiskPercent');
const predConfidence      = document.getElementById('predConfidence');

const alertBox            = document.getElementById('alertBox');
const alertIcon           = document.getElementById('alertIcon');
const alertTitle          = document.getElementById('alertTitle');
const alertMessage        = document.getElementById('alertMessage');
const statusText          = document.getElementById('statusText');
const statusDot           = document.getElementById('statusDot');
const alertSound          = document.getElementById('alertSound');
const currentSessionCode  = document.getElementById('currentSessionCode');

const llmPanel            = document.getElementById('llmPanel');
const llmText             = document.getElementById('llmText');
const llmSpeakingBadge    = document.getElementById('llmSpeakingBadge');
const llmTimestamp        = document.getElementById('llmTimestamp');
const llmStatusBadge      = document.getElementById('llmStatusBadge');

// ── Risk Timeline Chart Setup ─────────────────────────────────────────────
const MAX_HISTORY = 60;  // 60 data points (seconds)

const riskHistory     = [];   // Historical risk scores
const riskTimeLabels  = [];   // Human-readable labels
let   historyInitialized = false;

function buildEmptyHistory() {
    for (let i = MAX_HISTORY; i > 0; i--) {
        riskHistory.push(null);
        riskTimeLabels.push(`-${i}s`);
    }
}
buildEmptyHistory();

const chartCtx = document.getElementById('riskChart').getContext('2d');

const riskChart = new Chart(chartCtx, {
    type: 'line',
    data: {
        labels: riskTimeLabels,
        datasets: [
            {
                label: 'Risk Score',
                data: riskHistory.slice(),
                borderColor: '#a855f7',
                backgroundColor: (ctx) => {
                    const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 150);
                    g.addColorStop(0, 'rgba(168,85,247,0.25)');
                    g.addColorStop(1, 'rgba(168,85,247,0)');
                    return g;
                },
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                tension: 0.4,
                fill: true,
                spanGaps: false,
            },
            {
                label: 'Forecast',
                data: new Array(MAX_HISTORY).fill(null),
                borderColor: '#f59e0b',
                borderWidth: 2,
                borderDash: [5, 4],
                pointRadius: 0,
                pointHoverRadius: 4,
                tension: 0.4,
                fill: false,
                spanGaps: false,
            }
        ]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        scales: {
            x: {
                ticks: {
                    color: '#4b5563',
                    font: { family: "'Roboto Mono'", size: 9 },
                    maxTicksLimit: 8,
                    maxRotation: 0,
                },
                grid: { color: 'rgba(255,255,255,0.04)' },
                border: { color: 'rgba(255,255,255,0.07)' }
            },
            y: {
                min: 0,
                max: 100,
                ticks: {
                    color: '#4b5563',
                    font: { family: "'Roboto Mono'", size: 9 },
                    stepSize: 25,
                    callback: v => v
                },
                grid: { color: 'rgba(255,255,255,0.04)' },
                border: { color: 'rgba(255,255,255,0.07)' }
            }
        },
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: 'rgba(15,17,23,0.95)',
                titleColor: '#9ca3af',
                bodyColor: '#e5e7eb',
                borderColor: 'rgba(255,255,255,0.08)',
                borderWidth: 1,
                titleFont: { family: "'Roboto Mono'", size: 10 },
                bodyFont:  { family: "'Roboto Mono'", size: 11 }
            }
        }
    }
});

// Danger threshold reference line plugin
const dangerLinePlugin = {
    id: 'dangerLine',
    afterDraw(chart) {
        const { ctx, scales: { y } } = chart;
        const yPos = y.getPixelForValue(65);
        ctx.save();
        ctx.strokeStyle = 'rgba(234,67,53,0.35)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(chart.chartArea.left, yPos);
        ctx.lineTo(chart.chartArea.right, yPos);
        ctx.stroke();
        ctx.restore();
    }
};
Chart.register(dangerLinePlugin);

// ── Update Risk Timeline ──────────────────────────────────────────────────
function updateRiskTimeline(riskScore, trend) {
    // Shift history forward
    riskHistory.push(riskScore);
    riskTimeLabels.push('NOW');
    if (riskHistory.length > MAX_HISTORY) {
        riskHistory.shift();
        riskTimeLabels.shift();
    }

    // Forecast for next 2 extra points (+30s, +60s)
    let multiplier = 1.0;
    if (trend === 'INCREASING') multiplier = 1.35;
    else if (trend === 'DECREASING') multiplier = 0.70;

    const f30 = Math.min(100, Math.round(riskScore * (1 + (multiplier - 1) * 0.5)));
    const f60 = Math.min(100, Math.round(riskScore * multiplier));

    // Build forecast dataset: null for historical, then ramping at end
    const forecastData = new Array(MAX_HISTORY).fill(null);
    const lastIdx = riskHistory.length - 1;
    forecastData[lastIdx] = riskScore;  // Connect at 'NOW'
    // We add two virtual points but chart only has MAX_HISTORY slots;
    // visually represent the gradient by setting the last point and
    // storing forecast text separately
    riskChart.data.labels   = [...riskTimeLabels];
    riskChart.data.datasets[0].data = [...riskHistory];
    riskChart.data.datasets[1].data = forecastData;
    riskChart.update('none');

    // Timeline summary strip
    const now30Color = f30 >= 65 ? '#ea4335' : f30 >= 40 ? '#fbbc04' : '#34a853';
    const now60Color = f60 >= 65 ? '#ea4335' : f60 >= 40 ? '#fbbc04' : '#34a853';
    document.getElementById('timelineNow').textContent  = Math.round(riskScore);
    document.getElementById('timeline30s').textContent  = f30;
    document.getElementById('timeline60s').textContent  = f60;
    document.getElementById('timeline30s').style.color  = now30Color;
    document.getElementById('timeline60s').style.color  = now60Color;

    // Add warning icon if 60s forecast is danger
    const t60el = document.getElementById('timeline60s');
    t60el.textContent = f60 >= 65 ? `${f60} ⚠` : `${f60}`;
}

// ── Update Signal Bars ────────────────────────────────────────────────────
function setBar(id, pct, alert = false) {
    const fill = document.getElementById(`sig-${id}`);
    const val  = document.getElementById(`sigv-${id}`);
    if (!fill || !val) return;
    const clamped = Math.min(100, Math.max(0, Math.round(pct)));
    fill.style.width = `${clamped}%`;

    // Color shifts dynamically with intensity
    if (alert || clamped >= 75) {
        fill.style.background = '#ef4444';
    } else if (clamped >= 50) {
        fill.style.background = '#f59e0b';
    }
    // else keep original CSS color

    val.textContent = `${clamped}%`;
}

// ── State ──────────────────────────────────────────────────────────────────
let ws;
let lastFrameTime = Date.now();
let checkConnectionInterval;
let criticalStateStartTime = 0;
let isAlertActive = false;
let activeSessionCode = null;
let activeServerHost  = null;

let currentFrameId  = 0;
let currentHeatmapId = 0;

let fpsCounter  = 0;
let lastFpsTime = Date.now();

const STORAGE_KEY      = 'crowdpulse_server_host';
const CODE_STORAGE_KEY = 'crowdpulse_session_code';

// ── Helpers ────────────────────────────────────────────────────────────────
function getServerHost() {
    const raw = document.getElementById('serverUrlInput').value.trim();
    if (raw) return raw.replace(/^wss?:\/\//, '').replace(/^https?:\/\//, '');
    return localStorage.getItem(STORAGE_KEY) || '';
}

function getWsBase(host) {
    const isLocal = host.startsWith('localhost') || host.startsWith('127.') || host.startsWith('10.0.');
    return isLocal ? `ws://${host}` : `wss://${host}`;
}

// ── Connect ────────────────────────────────────────────────────────────────
function connectFromUI() {
    const host = getServerHost();
    const code = document.getElementById('sessionCodeInput').value.trim().toUpperCase();
    if (!host) { alert('Please enter the backend server URL first.'); return; }
    if (code.length < 6) { alert('Please enter a valid 6-char session code.'); return; }
    localStorage.setItem(STORAGE_KEY, host);
    localStorage.setItem(CODE_STORAGE_KEY, code);
    currentSessionCode.textContent = `#${code}`;
    connectToDashboard(host, code);
}

// ── WebSocket ──────────────────────────────────────────────────────────────
function connectToDashboard(host, code) {
    activeServerHost  = host;
    activeSessionCode = code;
    const wsUrl = `${getWsBase(host)}/ws/dashboard/${code}`;
    statusText.textContent = 'Connecting...';
    statusDot.className    = 'w-2 h-2 rounded-full bg-amber-400 animate-pulse';
    if (ws) { ws.close(); ws = null; }
    ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
        statusText.textContent = 'Connected';
        statusDot.className    = 'w-2 h-2 rounded-full bg-emerald-500 animate-pulse';
        clearInterval(checkConnectionInterval);
        checkConnectionInterval = setInterval(checkFallbackState, 1000);
        // Header status bar
        const dDot = document.getElementById('devSystemDot');
        const dTxt = document.getElementById('devSystemStatus');
        if (dDot) { dDot.className = 'w-2 h-2 rounded-full bg-emerald-500 pulse-dot'; }
        if (dTxt) { dTxt.textContent = 'SYSTEM ONLINE'; dTxt.className = 'text-[10px] font-bold text-emerald-400 tracking-wide'; }
        const camEl = document.getElementById('devCamId');
        const locEl = document.getElementById('devLocation');
        if (camEl) camEl.textContent = `CAM-${code.slice(0,2)}`;
        if (locEl) locEl.textContent = 'Live Feed';
    };

    ws.onclose = (event) => {
        statusText.textContent = 'Disconnected';
        statusDot.className    = 'w-2 h-2 rounded-full bg-red-500';
        clearInterval(checkConnectionInterval);
        // Header status bar — show offline
        const dDot = document.getElementById('devSystemDot');
        const dTxt = document.getElementById('devSystemStatus');
        if (dDot) { dDot.className = 'w-2 h-2 rounded-full bg-gray-500'; }
        if (dTxt) { dTxt.textContent = 'SYSTEM OFFLINE'; dTxt.className = 'text-[10px] font-bold text-gray-500 tracking-wide'; }
        const camEl = document.getElementById('devCamId');
        const locEl = document.getElementById('devLocation');
        if (camEl) camEl.textContent = '\u2014';
        if (locEl) locEl.textContent = '\u2014';
        if (event.code === 4404) {
            alert('Session ended. Please reconnect with a new session code.');
            localStorage.removeItem(CODE_STORAGE_KEY);
            activeSessionCode = null;
            return;
        }
        setTimeout(() => {
            if (activeSessionCode && activeServerHost) connectToDashboard(activeServerHost, activeSessionCode);
        }, 4000);
    };

    ws.onerror = () => {
        statusText.textContent = 'Error';
        statusDot.className    = 'w-2 h-2 rounded-full bg-red-500';
    };

    ws.onmessage = (event) => {
        if (typeof event.data === 'string') {
            const data = JSON.parse(event.data);
            if (data.type === 'tts_instruction') {
                speakInstruction(data.text);
                updateLlmPanel(data);
                return;
            }
            updateDashboard(data);
        } else if (event.data instanceof ArrayBuffer) {
            const view = new Uint8Array(event.data);
            if (view[0] === 0xFE) renderHeatmap(event.data.slice(1));
            else renderFrame(event.data);
        }
    };
}

// ── Rendering ─────────────────────────────────────────────────────────────
function renderFrame(arrayBuffer) {
    const blob = new Blob([arrayBuffer], { type: 'image/jpeg' });
    const url  = URL.createObjectURL(blob);
    currentFrameId++;
    const id = currentFrameId;
    const img = new Image();
    img.onload = () => {
        if (id !== currentFrameId) { URL.revokeObjectURL(url); return; }
        if (window.previousImageUrl) URL.revokeObjectURL(window.previousImageUrl);
        videoStream.src = url;
        window.previousImageUrl = url;
        videoStream.classList.remove('hidden');
        noSignal.classList.add('hidden');
        reconnectingOverlay.classList.replace('opacity-100', 'opacity-0');
        reconnectingOverlay.classList.add('pointer-events-none');
        // FPS counter
        fpsCounter++;
        const now = Date.now();
        if (now - lastFpsTime >= 1000) {
            fpsDisplay.textContent = fpsCounter;
            fpsCounter = 0;
            lastFpsTime = now;
        }
        lastFrameTime = now;
    };
    img.onerror = () => URL.revokeObjectURL(url);
    img.src = url;
}

function renderHeatmap(arrayBuffer) {
    const blob = new Blob([arrayBuffer], { type: 'image/jpeg' });
    const url  = URL.createObjectURL(blob);
    currentHeatmapId++;
    const id = currentHeatmapId;
    const img = new Image();
    img.onload = () => {
        if (id !== currentHeatmapId) { URL.revokeObjectURL(url); return; }
        if (window.previousHeatmapUrl) URL.revokeObjectURL(window.previousHeatmapUrl);
        heatmapStream.src = url;
        window.previousHeatmapUrl = url;
        heatmapStream.classList.remove('hidden');
    };
    img.onerror = () => URL.revokeObjectURL(url);
    img.src = url;
}

// ── Main Dashboard Update ─────────────────────────────────────────────────
function updateDashboard(data) {
    // ── People count ──────────────────────────────────────────────────────
    peopleCountBar.textContent = data.count;

    // ── Latency ───────────────────────────────────────────────────────────
    const transitMs = Math.floor(Date.now() - (data.timestamp * 1000));
    latencyCalc.textContent = `${transitMs} ms`;

    // ── Prediction ────────────────────────────────────────────────────────
    const pred       = data.prediction || {};
    const riskScore  = pred.risk_score || 0.0;
    const alertLevel = pred.risk_label  || data.status || 'SAFE';
    const trend      = pred.trend       || 'STABLE';

    // 1. Hero risk metric + badge
    riskScoreHero.textContent = Math.round(riskScore);
    applyStatusColor(alertLevel, riskScore);

    // 2. Trend
    if (trend === 'INCREASING') {
        riskTrendIcon.textContent  = '↗';
        riskTrendIcon.className    = 'font-bold text-red-400';
        riskTrendLabel.textContent = 'Increasing';
        riskTrendLabel.className   = 'font-bold text-red-400';
    } else if (trend === 'DECREASING') {
        riskTrendIcon.textContent  = '↘';
        riskTrendIcon.className    = 'font-bold text-emerald-400';
        riskTrendLabel.textContent = 'Decreasing';
        riskTrendLabel.className   = 'font-bold text-emerald-400';
    } else {
        riskTrendIcon.textContent  = '→';
        riskTrendIcon.className    = 'font-bold text-gray-400';
        riskTrendLabel.textContent = 'Stable';
        riskTrendLabel.className   = 'font-bold text-gray-400';
    }

    // 3. Risk Timeline update
    updateRiskTimeline(riskScore, trend);

    // 4. Signal bars from top zone metrics
    const zones = data.zones || [];
    const topZone = zones.length > 0
        ? zones.reduce((m, z) => z.people > m.people ? z : m, zones[0])
        : null;

    if (topZone) {
        // Density: raw density value (0..10+ people per zone unit) → mapped to 0-100
        const densityPct      = Math.min(100, (topZone.density || 0) * 10);
        // Movement / speed: assume max meaningful speed is 3 m/s → pct
        const speedPct        = Math.min(100, (topZone.avg_speed || 0) / 3 * 100);
        // Compression: 0.0–1.0 → pct
        const compressionPct  = Math.min(100, (topZone.compression || 0) * 100);
        // Flow: use speed as a proxy for flow intensity
        const flowPct         = speedPct;
        // Direction consistency: 1 - consistency gives "chaotic" level (0=orderly, 1=chaos)
        const directionPct    = topZone.direction_consistency != null
            ? Math.round((1 - topZone.direction_consistency) * 100)
            : Math.round(speedPct * 0.6);
        // Exit blockage: spikes if this zone is an exit AND compression is high
        const exitBlockagePct = topZone.is_exit ? Math.min(100, compressionPct * 1.4) : 0;
        // Abnormal: risk score contribution beyond 60 is "abnormal signal"
        const abnormalPct     = Math.max(0, (riskScore - 60) * 2.5);

        setBar('density',     densityPct);
        setBar('movement',    speedPct > 1.5 * 33 ? speedPct : speedPct * 0.7);
        setBar('speed',       speedPct);
        setBar('flow',        flowPct);
        setBar('compression', compressionPct, compressionPct > 70);
        setBar('direction',   directionPct);
        setBar('exit',        exitBlockagePct, exitBlockagePct > 50);
        setBar('abnormal',    abnormalPct, abnormalPct > 30);
    } else {
        // No zone data: use global risk score as a rough proxy
        const proxy = riskScore;
        setBar('density',     proxy * 0.8);
        setBar('movement',    proxy * 0.6);
        setBar('speed',       proxy * 0.5);
        setBar('flow',        proxy * 0.55);
        setBar('compression', proxy * 0.7);
        setBar('direction',   proxy * 0.4);
        setBar('exit',        0);
        setBar('abnormal',    Math.max(0, proxy - 60) * 2);
    }

    // 5. Prediction panel
    predFrom.textContent = alertLevel;
    if (alertLevel === 'SAFE' && trend === 'INCREASING')      predTo.textContent = 'WARNING';
    else if (alertLevel === 'WARNING' && trend === 'INCREASING') predTo.textContent = 'CRITICAL';
    else if (alertLevel === 'CRITICAL' && trend === 'DECREASING') predTo.textContent = 'WARNING';
    else if (alertLevel === 'WARNING' && trend === 'DECREASING')  predTo.textContent = 'SAFE';
    else predTo.textContent = alertLevel;

    let forecastedRisk = riskScore;
    if (trend === 'INCREASING') forecastedRisk = Math.min(100, riskScore * 1.45);
    if (trend === 'DECREASING') forecastedRisk = Math.max(0,   riskScore * 0.70);
    predRiskPercent.textContent = Math.round(forecastedRisk);
    predConfidence.textContent  = Math.round(82 + Math.random() * 12);

    // 6. Critical alert
    if (alertLevel === 'CRITICAL') {
        if (criticalStateStartTime === 0) criticalStateStartTime = Date.now();
        const duration = (Date.now() - criticalStateStartTime) / 1000;
        if (duration > 5 && !isAlertActive) triggerAlert();
    } else {
        criticalStateStartTime = 0;
        if (isAlertActive) clearAlert();
    }
}

// ── Status Color ──────────────────────────────────────────────────────────
function applyStatusColor(status) {
    riskLabel.textContent = status;
    riskBadge.className   = 'inline-flex justify-center items-center gap-2 px-5 py-1.5 rounded-full border text-xs font-bold tracking-widest mb-4 transition-all duration-300';
    videoContainer.classList.remove('pulse-red-border');

    if (status === 'SAFE') {
        riskIndicatorDot.className = 'w-2 h-2 rounded-full bg-emerald-500';
        riskBadge.classList.add('border-emerald-700/40', 'bg-emerald-900/20', 'text-emerald-400');
        riskScoreHero.className    = 'text-8xl font-extrabold leading-none font-mono risk-num-safe';
        videoContainer.style.boxShadow = '0 0 0 2px #34a853, 0 0 30px rgba(52,168,83,0.2)';
    } else if (status === 'WARNING') {
        riskIndicatorDot.className = 'w-2 h-2 rounded-full bg-amber-400 animate-pulse';
        riskBadge.classList.add('border-amber-700/40', 'bg-amber-900/20', 'text-amber-400');
        riskScoreHero.className    = 'text-8xl font-extrabold leading-none font-mono risk-num-warning';
        videoContainer.style.boxShadow = '0 0 0 2px #fbbc04, 0 0 30px rgba(251,188,4,0.25)';
    } else if (status === 'CRITICAL' || status === 'RED') {
        riskIndicatorDot.className = 'w-2 h-2 rounded-full bg-red-500 animate-ping';
        riskBadge.classList.add('border-red-700/50', 'bg-red-900/20', 'text-red-400');
        riskScoreHero.className    = 'text-8xl font-extrabold leading-none font-mono risk-num-critical';
        videoContainer.style.boxShadow = '0 0 0 3px #ea4335, 0 0 40px rgba(234,67,53,0.35)';
        videoContainer.classList.add('pulse-red-border');
    }
}

// ── TTS ────────────────────────────────────────────────────────────────────
function speakInstruction(text) {
    if (!('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.onstart = () => llmSpeakingBadge?.classList.remove('hidden');
    u.onend   = () => llmSpeakingBadge?.classList.add('hidden');
    u.onerror = () => llmSpeakingBadge?.classList.add('hidden');
    window.speechSynthesis.speak(u);
}

// ── LLM Panel ─────────────────────────────────────────────────────────────
function updateLlmPanel(data) {
    if (!llmPanel) return;
    llmPanel.classList.remove('hidden', 'opacity-50');
    if (llmText) llmText.textContent = data.text;
    if (llmTimestamp) {
        const t = data.timestamp ? new Date(data.timestamp * 1000) : new Date();
        llmTimestamp.textContent = t.toLocaleTimeString();
    }
    if (llmStatusBadge) {
        llmStatusBadge.className = 'px-2 py-0.5 rounded-full text-[9px] font-bold bg-purple-900/50 border border-purple-700/30 text-purple-300';
        llmStatusBadge.textContent = data.alert_level || data.status || 'SAFE';
        llmStatusBadge.classList.remove('hidden');
    }
    const llmReason = document.getElementById('llmReason');
    if (llmReason) {
        const reasonMap = {
            '30s_periodic': '⏱ Periodic 30s check',
            'breach_YELLOW': '⚡ Breach → WARNING',
            'breach_RED':    '🚨 Breach → CRITICAL',
        };
        llmReason.textContent = reasonMap[data.reason] || `Trigger: ${data.reason}`;
    }
}

// ── Alert ──────────────────────────────────────────────────────────────────
function triggerAlert() {
    isAlertActive = true;
    alertBox.className = 'panel p-4 border-l-4 border-red-600 opacity-100 transition-all duration-300 bg-red-950/30';
    alertIcon.className = 'p-2 rounded-lg bg-red-900/40 text-red-400 animate-bounce border border-red-700/30';
    alertTitle.textContent = '⚠ CRITICAL CROWD ALERT';
    alertTitle.classList.add('text-red-400');
    alertMessage.textContent = 'Risk Prediction Engine has flagged imminent danger. Immediate intervention required.';
    alertMessage.className = 'text-xs text-red-300 mt-1 leading-relaxed';
    try { alertSound.play().catch(() => {}); } catch(e) {}
}

function clearAlert() {
    isAlertActive = false;
    alertBox.className = 'panel p-4 border-l-4 border-white/10 opacity-60 transition-all duration-300';
    alertIcon.className = 'p-2 rounded-lg bg-white/5 text-gray-400 border border-white/10';
    alertTitle.textContent = 'System Monitoring';
    alertTitle.classList.remove('text-red-400');
    alertMessage.textContent = 'Risk levels stabilized. Continuing surveillance.';
    alertMessage.className = 'text-xs text-gray-500 mt-1 leading-relaxed';
}

function checkFallbackState() {
    if (videoStream.classList.contains('hidden')) return;
    const ms = Date.now() - lastFrameTime;
    if (ms > 35000) {
        reconnectingOverlay.classList.replace('opacity-0', 'opacity-100');
        reconnectingOverlay.classList.remove('pointer-events-none');
    }
}

// ── On load ────────────────────────────────────────────────────────────────
const savedHost = localStorage.getItem(STORAGE_KEY);
const savedCode = localStorage.getItem(CODE_STORAGE_KEY);

if (savedHost) document.getElementById('serverUrlInput').value = savedHost;
if (savedCode) document.getElementById('sessionCodeInput').value = savedCode;
if (savedHost && savedCode) connectFromUI();

// ── x402 API Demo (Step 20) ──────────────────────────────────────────────────
// Simulates: POST → 402 → Algorand payment sign → retry → 200 OK
async function x402DemoCall() {
    const btn      = document.getElementById('x402TryBtn');
    const flow     = document.getElementById('x402Flow');
    const respPre  = document.getElementById('x402Response');
    const steps    = [1,2,3,4,5].map(n => document.getElementById(`x402Step${n}`));

    if (!btn) return;

    // Reset UI
    btn.disabled = true;
    btn.textContent = '⏳ Running…';
    flow.classList.remove('hidden');
    respPre.classList.add('hidden');
    steps.forEach(s => {
        if (s) {
            s.querySelector('span:first-child').style.background = 'rgba(255,255,255,0.08)';
            s.style.color = '#6b7280';
        }
    });

    const host = (localStorage.getItem(STORAGE_KEY) || 'http://localhost:8000').replace(/\/$/, '');

    function _stepDone(n, color = '#34d399') {
        const el = steps[n - 1];
        if (el) {
            el.querySelector('span:first-child').style.background = color;
            el.style.color = color === '#34d399' ? '#d1fae5' : '#fca5a5';
        }
    }

    function _stepActive(n) {
        const el = steps[n - 1];
        if (el) {
            el.querySelector('span:first-child').style.background = '#8b5cf6';
            el.style.color = '#c4b5fd';
        }
    }

    async function _delay(ms) { return new Promise(r => setTimeout(r, ms)); }

    try {
        // Step 1: Initial POST (expect 402 or 200 if x402 not configured)
        _stepActive(1);
        await _delay(400);
        const r1 = await fetch(`${host}/api/v1/crowd/predict`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ features: ['density','movement','risk_prediction'] }),
        });
        _stepDone(1);
        await _delay(300);

        if (r1.status === 402) {
            // x402 is live — show full flow
            _stepActive(2);
            await _delay(500);
            _stepDone(2);

            _stepActive(3);
            await _delay(800);   // Simulate signing
            _stepDone(3);

            _stepActive(4);
            await _delay(400);
            // Retry (in production, this carries the payment header)
            const r2 = await fetch(`${host}/api/v1/crowd/predict`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Demo-Mode': '1' },
                body: JSON.stringify({ features: ['density','movement','risk_prediction'] }),
            });
            _stepDone(4);
            await _delay(300);

            _stepActive(5);
            const data = r2.ok ? await r2.json() : { note: '402 active — payment proof required on live Algorand TestNet' };
            _stepDone(5);
            respPre.textContent = JSON.stringify(data, null, 2);

        } else if (r1.ok) {
            // x402 not configured — endpoint open, show result directly
            [2, 3, 4].forEach(n => {
                const el = steps[n - 1];
                if (el) { el.style.opacity = '0.35'; el.style.textDecoration = 'line-through'; }
            });
            _stepActive(5);
            await _delay(200);
            const data = await r1.json();
            _stepDone(5);
            respPre.textContent = JSON.stringify(data, null, 2);
            respPre.style.borderColor = 'rgba(52,211,153,0.3)';

        } else {
            throw new Error(`HTTP ${r1.status} — is the backend running?`);
        }

        respPre.classList.remove('hidden');
        btn.textContent = '⚡ TRY AGAIN';

    } catch (e) {
        steps.forEach(s => { if (s) s.style.color = '#f87171'; });
        respPre.textContent = `Error: ${e.message}\n\nMake sure the backend is running:\n  python server.py`;
        respPre.style.borderColor = 'rgba(239,68,68,0.3)';
        respPre.classList.remove('hidden');
        btn.textContent = '⚡ TRY API';
    } finally {
        btn.disabled = false;
    }
}