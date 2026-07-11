// ── DOM refs ────────────────────────────────────────────────────────────────
const videoStream         = document.getElementById('videoStream');
const videoContainer      = document.getElementById('videoContainer');
const noSignal            = document.getElementById('noSignal');
const reconnectingOverlay = document.getElementById('reconnectingOverlay');
const peopleCount         = document.getElementById('peopleCount');
const statusBadge         = document.getElementById('statusBadge');
const statusIndicator     = document.getElementById('statusIndicator');
const statusLabel         = document.getElementById('statusLabel');
const timestampDisplay    = document.getElementById('timestampDisplay');
const latencyCalc         = document.getElementById('latencyCalc');
const densityCard         = document.getElementById('densityCard');
const alertBox            = document.getElementById('alertBox');
const alertIcon           = document.getElementById('alertIcon');
const alertTitle          = document.getElementById('alertTitle');
const alertMessage        = document.getElementById('alertMessage');
const statusText          = document.getElementById('statusText');
const statusDot           = document.getElementById('statusDot');
const alertSound          = document.getElementById('alertSound');
const sessionInstructions = document.getElementById('sessionInstructions');
const connectedSessionCode = document.getElementById('connectedSessionCode');
const currentSessionCode  = document.getElementById('currentSessionCode');

// LLM panel DOM refs
const llmPanel            = document.getElementById('llmPanel');
const llmText             = document.getElementById('llmText');
const llmSpeakingBadge    = document.getElementById('llmSpeakingBadge');
const llmTimestamp        = document.getElementById('llmTimestamp');
const llmStatusBadge      = document.getElementById('llmStatusBadge');

// ── State ───────────────────────────────────────────────────────────────────
let ws;
let lastFrameTime = Date.now();
let checkConnectionInterval;
let redStateStartTime = 0;
let isAlertActive = false;
let activeSessionCode = null;
let activeServerHost  = null;

let currentFrameId = 0;

let pendingHeatmapImage = null;
let currentHeatmapId = 0;

const STORAGE_KEY      = 'crowdpulse_server_host';
const CODE_STORAGE_KEY = 'crowdpulse_session_code';

// ── Helpers ─────────────────────────────────────────────────────────────────
function getServerHost() {
    const raw = document.getElementById('serverUrlInput').value.trim();
    if (raw) return raw.replace(/^wss?:\/\//, '').replace(/^https?:\/\//, '');
    return localStorage.getItem(STORAGE_KEY) || '';
}

function getHttpBase(host) {
    const isLocal = host.startsWith('localhost') || host.startsWith('127.') || host.startsWith('10.0.');
    return isLocal ? `http://${host}` : `https://${host}`;
}

function getWsBase(host) {
    const isLocal = host.startsWith('localhost') || host.startsWith('127.') || host.startsWith('10.0.');
    return isLocal ? `ws://${host}` : `wss://${host}`;
}

// ── UI Connection ─────────────────────────────────────────────────────────
function connectFromUI() {
    const host = getServerHost();
    const code = document.getElementById('sessionCodeInput').value.trim().toUpperCase();

    if (!host) { alert('Please enter the backend server URL first.'); return; }
    if (code.length < 6) { alert('Please enter a valid 6-char session code.'); return; }

    localStorage.setItem(STORAGE_KEY, host);
    localStorage.setItem(CODE_STORAGE_KEY, code);

    sessionInstructions.classList.remove('hidden');
    connectedSessionCode.textContent = code;
    currentSessionCode.textContent   = code;

    connectToDashboard(host, code);
}

// ── WebSocket connection ───────────────────────────────────────────────────
function connectToDashboard(host, code) {
    activeServerHost  = host;
    activeSessionCode = code;

    const wsUrl = `${getWsBase(host)}/ws/dashboard/${code}`;

    statusText.textContent = 'Connecting...';
    statusDot.className    = 'w-3 h-3 rounded-full bg-amber-400 animate-pulse';

    if (ws) {
        ws.close();
        ws = null;
    }

    ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
        statusText.textContent = 'Connected';
        statusDot.className    = 'w-3 h-3 rounded-full bg-emerald-500 animate-pulse';
        clearInterval(checkConnectionInterval);
        checkConnectionInterval = setInterval(checkFallbackState, 1000);
    };

    ws.onclose = (event) => {
        statusText.textContent = 'Disconnected';
        statusDot.className    = 'w-3 h-3 rounded-full bg-red-500';
        clearInterval(checkConnectionInterval);

        if (event.code === 4404) {
            console.warn("Session expired or invalid code from server.");
            alert("This session has ended. Please reconnect with a new session code.");
            localStorage.removeItem(CODE_STORAGE_KEY);
            activeSessionCode = null;
            return;
        }

        setTimeout(() => {
            if (activeSessionCode && activeServerHost) {
                connectToDashboard(activeServerHost, activeSessionCode);
            }
        }, 4000);
    };

    ws.onerror = () => {
        statusText.textContent = 'Error';
        statusDot.className    = 'w-3 h-3 rounded-full bg-red-500';
    };

    ws.onmessage = (event) => {
        if (typeof event.data === "string") {
            const data = JSON.parse(event.data);

            // ── LLM TTS instruction ─────────────────────────────────────────
            if (data.type === "tts_instruction") {
                console.log(`%c[LLM] 🤖 TTS instruction received: "${data.text}"`, 'color: #8b5cf6; font-weight: bold;');
                speakInstruction(data.text);
                updateLlmPanel(data);
                return;
            }

            // ── Regular frame metadata ──────────────────────────────────────
            const serverToBrowserMs = Date.now() - (data.timestamp * 1000);
            console.log(`%c[NETWORK] Frame JSON. Transit: ~${Math.floor(serverToBrowserMs)}ms`, 'color: #3b82f6; font-weight: bold;');
            updateDashboard(data);

        } else if (event.data instanceof ArrayBuffer) {
            const view = new Uint8Array(event.data);
            if (view[0] === 0xFE) {
                renderHeatmap(event.data.slice(1));
            } else {
                console.log(`%c[RENDER] ArrayBuffer ${event.data.byteLength} bytes. Decoding...`, 'color: #8b5cf6;');
                renderFrame(event.data);
            }
        }
    };
}

// ── Frame rendering ───────────────────────────────────────────────────────────
function renderFrame(arrayBuffer) {
    const blob   = new Blob([arrayBuffer], { type: "image/jpeg" });
    const newUrl = URL.createObjectURL(blob);

    currentFrameId++;
    const thisFrameId = currentFrameId;
    const decodeStart = Date.now();

    const img = new Image();

    img.onload = () => {
        const decodeTime = Date.now() - decodeStart;
        if (thisFrameId !== currentFrameId) {
            console.log(`[RENDER] 🚨 Dropped older frame #${thisFrameId} (superseded by #${currentFrameId}).`);
            URL.revokeObjectURL(newUrl);
            return;
        }

        console.log(`%c[RENDER] ✅ Painted frame #${thisFrameId} in ${decodeTime}ms.`, 'color: #10b981;');

        if (window.previousImageUrl) URL.revokeObjectURL(window.previousImageUrl);

        videoStream.src         = newUrl;
        window.previousImageUrl = newUrl;

        videoStream.classList.remove('hidden');
        noSignal.classList.add('hidden');
        reconnectingOverlay.classList.replace('opacity-100', 'opacity-0');
        reconnectingOverlay.classList.add('pointer-events-none');

        lastFrameTime = Date.now();
    };

    img.onerror = () => URL.revokeObjectURL(newUrl);
    img.src = newUrl;
}

// ── Heatmap rendering ──────────────────────────────────────────────────────────
function renderHeatmap(arrayBuffer) {
    const blob   = new Blob([arrayBuffer], { type: "image/jpeg" });
    const newUrl = URL.createObjectURL(blob);

    currentHeatmapId++;
    const thisFrameId = currentHeatmapId;

    const img = new Image();

    img.onload = () => {
        if (thisFrameId !== currentHeatmapId) {
            URL.revokeObjectURL(newUrl);
            return;
        }

        if (window.previousHeatmapUrl) URL.revokeObjectURL(window.previousHeatmapUrl);

        const heatmapStream = document.getElementById('heatmapStream');
        if (!heatmapStream) return;
        
        heatmapStream.src = newUrl;
        window.previousHeatmapUrl = newUrl;

        heatmapStream.classList.remove('hidden');
        const heatmapNoSignal = document.getElementById('heatmapNoSignal');
        if (heatmapNoSignal) heatmapNoSignal.classList.add('hidden');
    };

    img.onerror = () => URL.revokeObjectURL(newUrl);
    img.src = newUrl;
}

// ── Dashboard update ──────────────────────────────────────────────────────────
function updateDashboard(data) {
    peopleCount.textContent   = data.count;
    timestampDisplay.textContent = new Date().toLocaleTimeString();

    applyStatusColor(data.status);

    if (data.status === "RED") {
        if (redStateStartTime === 0) redStateStartTime = Date.now();
        const duration = (Date.now() - redStateStartTime) / 1000;
        if (duration > 10 && !isAlertActive) triggerAlert();
    } else {
        redStateStartTime = 0;
        if (isAlertActive) clearAlert();
    }
}

// ── Status color + video frame border ────────────────────────────────────────
function applyStatusColor(status) {
    statusLabel.textContent = status;

    // Reset classes
    statusBadge.className   = "inline-flex w-full justify-center items-center gap-2 px-4 py-3 rounded-xl font-bold transition-colors duration-300";
    densityCard.className   = "glass-panel rounded-2xl p-6 transition-all duration-500 border-2";

    // Reset video container border
    videoContainer.className = videoContainer.className
        .replace(/border-\S+/g, '')
        .replace(/shadow-\S+/g, '')
        .replace(/\s+/g, ' ')
        .trim();

    if (status === "GREEN") {
        // Status badge
        statusIndicator.className = "w-2.5 h-2.5 rounded-full bg-emerald-500";
        statusBadge.classList.add('bg-emerald-100', 'text-emerald-700');
        densityCard.classList.add('border-emerald-200');
        // Video frame: green glow border
        videoContainer.style.boxShadow = '0 0 0 3px #10b981, 0 0 20px rgba(16,185,129,0.45)';
        videoContainer.style.borderRadius = '0.75rem';
    } else if (status === "YELLOW") {
        statusIndicator.className = "w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse";
        statusBadge.classList.add('bg-amber-100', 'text-amber-700');
        densityCard.classList.add('border-amber-200');
        // Video frame: amber glow border
        videoContainer.style.boxShadow = '0 0 0 3px #f59e0b, 0 0 24px rgba(245,158,11,0.5)';
        videoContainer.style.borderRadius = '0.75rem';
    } else if (status === "RED") {
        statusIndicator.className = "w-2.5 h-2.5 rounded-full bg-red-600 animate-ping";
        statusBadge.classList.add('bg-red-100', 'text-red-700');
        densityCard.classList.add('border-red-300', 'shadow-lg', 'shadow-red-500/20');
        // Video frame: red pulsing glow border
        videoContainer.style.boxShadow = '0 0 0 3px #ef4444, 0 0 30px rgba(239,68,68,0.65)';
        videoContainer.style.borderRadius = '0.75rem';
        videoContainer.classList.add('pulse-red-border');
    }

    if (status !== "RED") {
        videoContainer.classList.remove('pulse-red-border');
    }
}

// ── Web Speech TTS ────────────────────────────────────────────────────────────
function speakInstruction(text) {
    if (!('speechSynthesis' in window)) {
        console.warn('[TTS] Web Speech API not supported in this browser.');
        return;
    }
    // Cancel any ongoing speech before starting a new one
    window.speechSynthesis.cancel();

    const utterance   = new SpeechSynthesisUtterance(text);
    utterance.rate    = 1.0;
    utterance.pitch   = 1.0;
    utterance.volume  = 1.0;

    utterance.onstart = () => {
        if (llmSpeakingBadge) llmSpeakingBadge.classList.remove('hidden');
    };
    utterance.onend = () => {
        if (llmSpeakingBadge) llmSpeakingBadge.classList.add('hidden');
    };
    utterance.onerror = (e) => {
        console.warn('[TTS] Speech error:', e.error);
        if (llmSpeakingBadge) llmSpeakingBadge.classList.add('hidden');
    };

    window.speechSynthesis.speak(utterance);
}

// ── LLM Panel update ──────────────────────────────────────────────────────────
function updateLlmPanel(data) {
    if (!llmPanel) return;

    llmPanel.classList.remove('hidden', 'opacity-50');

    if (llmText) llmText.textContent = data.text;

    if (llmTimestamp) {
        const t = data.timestamp ? new Date(data.timestamp * 1000) : new Date();
        llmTimestamp.textContent = t.toLocaleTimeString();
    }

    if (llmStatusBadge) {
        llmStatusBadge.className = 'px-2 py-0.5 rounded-full text-xs font-bold';
        if (data.status === 'RED')    llmStatusBadge.classList.add('bg-red-100',    'text-red-700');
        if (data.status === 'YELLOW') llmStatusBadge.classList.add('bg-amber-100',  'text-amber-700');
        if (data.status === 'GREEN')  llmStatusBadge.classList.add('bg-emerald-100','text-emerald-700');
        llmStatusBadge.textContent = data.status;
    }

    // Trigger reason sub-label
    const llmReason = document.getElementById('llmReason');
    if (llmReason) {
        const reasonMap = {
            '30s_periodic':    '⏱ 30-second periodic check',
            'breach_YELLOW':   '⚡ Threshold breach — YELLOW',
            'breach_RED':      '🚨 Threshold breach — RED',
        };
        llmReason.textContent = reasonMap[data.reason] || `Trigger: ${data.reason}`;
    }
}

// ── Alert functions ───────────────────────────────────────────────────────────
function triggerAlert() {
    isAlertActive = true;
    alertBox.className = "glass-panel rounded-2xl p-6 border-l-4 border-red-500 opacity-100 transition-all duration-300 pulse-red bg-red-50";
    alertIcon.className = "p-2 rounded-lg bg-red-100 text-red-600 animate-bounce";
    alertIcon.innerHTML = `<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>`;
    alertTitle.textContent = "CRITICAL CROWD ALERT";
    alertTitle.classList.add('text-red-700');
    alertMessage.textContent = "High density threshold exceeded for >10 seconds. Immediate action required.";
    alertMessage.classList.replace('text-slate-500', 'text-red-600');

    try {
        alertSound.play().catch(e => console.log("Sound blocked by browser policy"));
    } catch(e) {}
}

function clearAlert() {
    isAlertActive = false;
    alertBox.className = "glass-panel rounded-2xl p-6 border-l-4 border-slate-300 opacity-50 transition-all duration-300";
    alertIcon.className = "p-2 rounded-lg bg-slate-100 text-slate-400";
    alertIcon.innerHTML = `<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>`;
    alertTitle.textContent = "System Monitoring";
    alertTitle.classList.remove('text-red-700');
    alertMessage.textContent = "Density stabilized.";
    alertMessage.classList.replace('text-red-600', 'text-slate-500');
}

function checkFallbackState() {
    if (videoStream.classList.contains('hidden')) return;

    const timeSinceLastFrame = Date.now() - lastFrameTime;
    latencyCalc.textContent  = `${Math.floor(timeSinceLastFrame / 1000)}s ago`;

    if (timeSinceLastFrame > 35000) {
        reconnectingOverlay.classList.replace('opacity-0', 'opacity-100');
        reconnectingOverlay.classList.remove('pointer-events-none');
    }
}

// ── On load: restore saved session ───────────────────────────────────────────
const savedHost = localStorage.getItem(STORAGE_KEY);
const savedCode = localStorage.getItem(CODE_STORAGE_KEY);

if (savedHost) document.getElementById('serverUrlInput').value = savedHost;
if (savedCode) document.getElementById('sessionCodeInput').value = savedCode;

if (savedHost && savedCode) connectFromUI();