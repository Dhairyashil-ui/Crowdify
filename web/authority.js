// ═══════════════════════════════════════════════════════════════
//  CROUDIFY — AUTHORITY PORTAL  |  authority.js
//  Emergency Operations Logic
// ═══════════════════════════════════════════════════════════════

// ── ZONE DEFINITIONS ─────────────────────────────────────────────────────
const ZONE_DEFS = [
    {
        id: 'A', name: 'MAIN STAGE', fullName: 'ZONE A — MAIN STAGE',
        mapX: 0.20, mapY: 0.35, mapR: 0.10,
        actions: [
            'Monitor crowd flow carefully',
            'Keep all emergency exits clearly marked',
            'Maintain current staffing levels'
        ]
    },
    {
        id: 'B', name: 'FOOD COURT', fullName: 'ZONE B — FOOD COURT',
        mapX: 0.50, mapY: 0.52, mapR: 0.09,
        actions: [
            'Redirect incoming crowd to south entrance',
            'Open additional service lanes',
            'Deploy crowd management staff',
            'Monitor exit throughput closely'
        ]
    },
    {
        id: 'C', name: 'EAST GATE', fullName: 'ZONE C — EAST GATE',
        mapX: 0.76, mapY: 0.67, mapR: 0.09,
        actions: [
            'Open secondary exit IMMEDIATELY',
            'Stop incoming crowd flow to East Gate',
            'Deploy response team now',
            'Alert medical standby team'
        ]
    },
    {
        id: 'D', name: 'NORTH ENTRANCE', fullName: 'ZONE D — NORTH ENTRANCE',
        mapX: 0.46, mapY: 0.14, mapR: 0.07,
        actions: [
            'Continue normal monitoring',
            'Manage entry flow systematically'
        ]
    }
];

// ── INITIAL ZONE STATES (demo baseline) ──────────────────────────────────
const DEMO_BASE = {
    A: { people: 342, density: 'MODERATE', compression: 45, movement: '→ DISPERSING', exitBlockage: 'LOW',      risk: 23, status: 'SAFE'     },
    B: { people: 201, density: 'MODERATE', compression: 58, movement: '← CONVERGING', exitBlockage: 'MODERATE', risk: 51, status: 'WATCH'    },
    C: { people: 183, density: 'HIGH',     compression: 82, movement: '→ EXIT',        exitBlockage: 'HIGH',     risk: 73, status: 'CRITICAL' },
    D: { people:  87, density: 'LOW',      compression: 21, movement: '↓ INWARD',      exitBlockage: 'LOW',      risk: 12, status: 'SAFE'     }
};

// ── INCIDENT REPLAY DATA ─────────────────────────────────────────────────
const REPLAYS = {
    '204': {
        events: [
            { time: '14:32:10', label: 'Normal crowd conditions',                   type: 'normal' },
            { time: '14:32:35', label: 'Density increasing near East Gate',         type: 'watch'  },
            { time: '14:32:51', label: 'Movement converging toward exit',           type: 'watch'  },
            { time: '14:33:02', label: 'Compression increasing — 68%',             type: 'watch'  },
            { time: '14:33:08', label: 'Risk threshold detected — 73%',            type: 'alert'  },
            { time: '14:33:16', label: 'Authority alerted',                         type: 'alert'  },
            { time: '14:33:29', label: 'Action acknowledged by Officer K. Sharma',  type: 'action' }
        ]
    },
    '203': {
        events: [
            { time: '11:45:00', label: 'Normal crowd conditions',                   type: 'normal' },
            { time: '11:47:30', label: 'Crowd buildup at Food Court entry',         type: 'watch'  },
            { time: '11:48:15', label: 'Flow rate decreasing',                      type: 'watch'  },
            { time: '11:49:02', label: 'Risk elevated to WARNING — 54%',           type: 'watch'  },
            { time: '11:49:44', label: 'Authority alerted',                         type: 'alert'  },
            { time: '11:50:11', label: 'Secondary lanes opened — action taken',     type: 'action' },
            { time: '11:51:30', label: 'Risk decreasing — situation resolved',      type: 'normal' }
        ]
    },
    '201': {
        events: [
            { time: '20:12:00', label: 'Normal crowd conditions',                   type: 'normal' },
            { time: '20:14:20', label: 'Event end surge — crowd density rising',    type: 'watch'  },
            { time: '20:15:05', label: 'Exit blockage detected — Main Stage',       type: 'alert'  },
            { time: '20:15:12', label: 'Authority alerted',                         type: 'alert'  },
            { time: '20:15:30', label: 'Response team deployed to Main Stage',      type: 'action' },
            { time: '20:17:00', label: 'Crowd cleared — situation resolved',        type: 'normal' }
        ]
    }
};

// ── LIVE STATE ─────────────────────────────────────────────────────────────
let zoneStates = JSON.parse(JSON.stringify(DEMO_BASE));
let selectedZoneId = null;
let critAlertZoneId = null;
let alertStartTime   = null;
let alertSecsLeft    = 60;
let alertTimerInt    = null;
let alertAcked       = false;
let isLive           = false;
let demoInt          = null;
let demoTick         = 0;
let pulsePhase       = 0;
let replayStepIdx    = -1;
let replayInt        = null;
let ws               = null;

const STORE_HOST = 'croudify_auth_host';
const STORE_CODE = 'croudify_auth_code';

// ── CANVAS SETUP ─────────────────────────────────────────────────────────
const canvas = document.getElementById('eventMap');
const ctx    = canvas.getContext('2d');

function resizeCanvas() {
    const panel = canvas.parentElement;
    const rect  = panel.getBoundingClientRect();
    // Header bar above canvas is 36px
    canvas.width  = rect.width;
    canvas.height = Math.max(200, rect.height - 36);
}

// ── MAP DRAW ──────────────────────────────────────────────────────────────
function getZoneColor(status) {
    if (status === 'CRITICAL') return { fill: 'rgba(255,59,59,0.16)',   stroke: '#ff3b3b', glow: 'rgba(255,59,59,0.55)',  text: '#ff3b3b' };
    if (status === 'WATCH')    return { fill: 'rgba(255,170,0,0.14)',   stroke: '#ffaa00', glow: 'rgba(255,170,0,0.5)',   text: '#ffaa00' };
    return                           { fill: 'rgba(0,230,118,0.11)',    stroke: '#00e676', glow: 'rgba(0,230,118,0.42)',  text: '#00e676' };
}

function drawMap() {
    const W = canvas.width, H = canvas.height;
    if (!W || !H) return;

    // Background
    ctx.fillStyle = '#060a0f';
    ctx.fillRect(0, 0, W, H);

    // Fine grid
    ctx.strokeStyle = 'rgba(0,183,255,0.035)';
    ctx.lineWidth = 1;
    for (let x = 0; x < W; x += 45) { ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,H); ctx.stroke(); }
    for (let y = 0; y < H; y += 45) { ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke(); }

    // Venue margins
    const ml = W*0.07, mr = W*0.07, mt = H*0.07, mb = H*0.10;
    const vx = ml, vy = mt, vw = W-ml-mr, vh = H-mt-mb;

    // Venue fill
    ctx.fillStyle = 'rgba(0,183,255,0.012)';
    ctx.fillRect(vx, vy, vw, vh);

    // Venue walls
    ctx.strokeStyle = 'rgba(0,183,255,0.22)';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([]);
    ctx.strokeRect(vx, vy, vw, vh);

    // Internal partition lines (dashed)
    ctx.strokeStyle = 'rgba(0,183,255,0.08)';
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 10]);
    // vertical divider ~40%
    ctx.beginPath(); ctx.moveTo(vx+vw*0.40, vy); ctx.lineTo(vx+vw*0.40, vy+vh); ctx.stroke();
    // horizontal in right half ~58%
    ctx.beginPath(); ctx.moveTo(vx+vw*0.40, vy+vh*0.58); ctx.lineTo(vx+vw, vy+vh*0.58); ctx.stroke();
    ctx.setLineDash([]);

    // Area labels (background)
    ctx.font = '600 10px Rajdhani, sans-serif';
    ctx.fillStyle = 'rgba(0,183,255,0.16)';
    ctx.textAlign = 'center';
    ctx.fillText('WEST WING', vx + vw*0.20, vy + vh*0.80);
    ctx.fillText('CENTRAL HALL', vx + vw*0.70, vy + vh*0.25);

    // Exit / Entrance wall markers
    drawWallMarker(ctx, vx + vw*0.46, vy,       '▲ NORTH ENTRANCE', true);
    drawWallMarker(ctx, vx + vw,      vy+vh*0.67,'→ EAST GATE EXIT', false, true);
    drawWallMarker(ctx, vx + vw*0.50, vy+vh,     '▼ MAIN EXIT',     false);

    // Draw zones
    ZONE_DEFS.forEach(z => {
        const s   = zoneStates[z.id];
        const zx  = vx + z.mapX * vw;
        const zy  = vy + z.mapY * vh;
        const zr  = Math.min(vw, vh) * z.mapR;
        drawZone(z, s, zx, zy, zr);
    });
}

function drawWallMarker(ctx, x, y, label, isTop, isRight) {
    ctx.font = '600 9px Rajdhani, sans-serif';
    ctx.fillStyle = 'rgba(74,122,155,0.55)';
    ctx.textAlign = isRight ? 'left' : 'center';
    const ox = isRight ? 6 : 0;
    const oy = isTop ? -5 : 13;
    ctx.fillText(label, x + ox, y + oy);
}

function drawZone(zone, state, cx, cy, r) {
    const c = getZoneColor(state.status);
    const isCrit = state.status === 'CRITICAL';
    const isWatch = state.status === 'WATCH';

    // Animated glow pulse radius
    let glowR = r;
    if (isCrit)  glowR = r + Math.sin(pulsePhase * 2.2) * r * 0.22;
    else if (isWatch) glowR = r + Math.sin(pulsePhase * 1.4) * r * 0.10;

    // Outer diffuse glow
    const g1 = ctx.createRadialGradient(cx, cy, 0, cx, cy, glowR * 2.0);
    const glowAlpha = isCrit ? '0.22' : isWatch ? '0.16' : '0.12';
    g1.addColorStop(0,  c.glow.replace(/[\d.]+\)$/, glowAlpha + ')'));
    g1.addColorStop(1, 'transparent');
    ctx.beginPath();
    ctx.arc(cx, cy, glowR * 2.0, 0, Math.PI*2);
    ctx.fillStyle = g1;
    ctx.fill();

    // Zone circle fill
    const g2 = ctx.createRadialGradient(cx, cy, 0, cx, cy, glowR);
    g2.addColorStop(0, c.fill.replace(/[\d.]+\)$/, '0.24)'));
    g2.addColorStop(1, c.fill);
    ctx.beginPath();
    ctx.arc(cx, cy, glowR, 0, Math.PI*2);
    ctx.fillStyle = g2;
    ctx.fill();

    // Ring stroke
    ctx.beginPath();
    ctx.arc(cx, cy, glowR, 0, Math.PI*2);
    ctx.strokeStyle = c.stroke;
    ctx.lineWidth = isCrit ? 2 : 1.5;
    ctx.stroke();

    // Center dot
    ctx.beginPath();
    ctx.arc(cx, cy, 5, 0, Math.PI*2);
    ctx.fillStyle = c.stroke;
    ctx.fill();

    // Zone letter above
    const fSize = Math.max(9, r * 0.28);
    ctx.font = `700 ${fSize}px Rajdhani, sans-serif`;
    ctx.fillStyle = c.text;
    ctx.textAlign = 'center';
    ctx.fillText(`ZONE ${zone.id}`, cx, cy - glowR - 7);

    // Zone name
    ctx.font = `500 ${Math.max(8, r*0.22)}px Rajdhani, sans-serif`;
    ctx.fillStyle = 'rgba(184,212,234,0.65)';
    ctx.fillText(zone.name, cx, cy - glowR - 18);

    // People count inside circle
    if (r > 28) {
        ctx.font = `700 ${Math.max(10, r*0.30)}px Share Tech Mono, monospace`;
        ctx.fillStyle = 'rgba(232,244,255,0.9)';
        ctx.fillText(state.people, cx, cy + 4);
        ctx.font = `500 ${Math.max(7, r*0.19)}px Rajdhani, sans-serif`;
        ctx.fillStyle = 'rgba(63,104,128,0.85)';
        ctx.fillText('people', cx, cy + 17);
    }

    // Risk % badge at top-right of circle
    if (state.risk > 0) {
        const bx = cx + glowR * 0.68;
        const by = cy - glowR * 0.68;
        const br = r * 0.27;
        ctx.beginPath();
        ctx.arc(bx, by, br, 0, Math.PI*2);
        ctx.fillStyle = 'rgba(6,10,15,0.92)';
        ctx.fill();
        ctx.strokeStyle = c.stroke;
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.font = `700 ${Math.max(7, r*0.20)}px Share Tech Mono, monospace`;
        ctx.fillStyle = c.text;
        ctx.textAlign = 'center';
        ctx.fillText(`${state.risk}%`, bx, by + 3);
    }
}

function mapLoop() {
    pulsePhase += 0.04;
    drawMap();
    requestAnimationFrame(mapLoop);
}

// ── CANVAS CLICK → ZONE HIT TEST ────────────────────────────────────────
canvas.addEventListener('click', e => {
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left) * (canvas.width  / rect.width);
    const my = (e.clientY - rect.top)  * (canvas.height / rect.height);

    const W = canvas.width, H = canvas.height;
    const ml = W*0.07, mt = H*0.07, vw = W-ml*2, vh = H-mt-H*0.10;

    let hit = null, minDist = Infinity;
    ZONE_DEFS.forEach(z => {
        const zx = ml + z.mapX * vw;
        const zy = mt + z.mapY * vh;
        const zr = Math.min(vw, vh) * z.mapR * 1.4; // generous hit area
        const d  = Math.hypot(mx-zx, my-zy);
        if (d <= zr && d < minDist) { minDist = d; hit = z; }
    });

    if (hit) openZoneDetail(hit.id);
});

// ── ZONE DETAIL PANEL ────────────────────────────────────────────────────
function openZoneDetail(zoneId) {
    const zone  = ZONE_DEFS.find(z => z.id === zoneId);
    const state = zoneStates[zoneId];
    if (!zone || !state) return;
    selectedZoneId = zoneId;

    document.getElementById('zdName').textContent    = zone.fullName;
    document.getElementById('zdPeople').textContent  = state.people;

    // Density
    const dEl = document.getElementById('zdDensity');
    dEl.textContent = state.density;
    dEl.className   = 'zd-val ' + (state.density === 'HIGH' ? 'c' : state.density === 'MODERATE' ? 'w' : 's');

    document.getElementById('zdMovement').textContent = state.movement;

    // Compression
    const cEl = document.getElementById('zdCompression');
    cEl.textContent = state.compression + '%';
    cEl.className   = 'zd-val ' + (state.compression > 70 ? 'c' : state.compression > 45 ? 'w' : 's');

    // Exit blockage
    const eEl = document.getElementById('zdExit');
    eEl.textContent = state.exitBlockage;
    eEl.className   = 'zd-val ' + (state.exitBlockage === 'HIGH' ? 'c' : state.exitBlockage === 'MODERATE' ? 'w' : 's');

    // Risk block
    const rBlock = document.getElementById('zdRiskBlock');
    const rPct   = document.getElementById('zdRiskPct');
    const rWin   = document.getElementById('zdRiskWin');
    rPct.textContent = state.risk + '%';

    if (state.status === 'CRITICAL') {
        rBlock.style.cssText = 'margin:0.8rem 0;padding:0.8rem;border:1px solid rgba(255,59,59,0.28);border-radius:4px;background:rgba(255,59,59,0.06);text-align:center;';
        rPct.style.cssText   = 'font-family:Orbitron,sans-serif;font-size:2.3rem;font-weight:900;line-height:1;color:#ff3b3b;text-shadow:0 0 24px rgba(255,59,59,0.5)';
        rWin.textContent     = 'within 60 seconds — ACTION REQUIRED';
    } else if (state.status === 'WATCH') {
        rBlock.style.cssText = 'margin:0.8rem 0;padding:0.8rem;border:1px solid rgba(255,170,0,0.28);border-radius:4px;background:rgba(255,170,0,0.06);text-align:center;';
        rPct.style.cssText   = 'font-family:Orbitron,sans-serif;font-size:2.3rem;font-weight:900;line-height:1;color:#ffaa00;text-shadow:0 0 20px rgba(255,170,0,0.4)';
        rWin.textContent     = 'within 90 seconds — monitor closely';
    } else {
        rBlock.style.cssText = 'margin:0.8rem 0;padding:0.8rem;border:1px solid rgba(0,230,118,0.28);border-radius:4px;background:rgba(0,230,118,0.06);text-align:center;';
        rPct.style.cssText   = 'font-family:Orbitron,sans-serif;font-size:2.3rem;font-weight:900;line-height:1;color:#00e676;text-shadow:0 0 20px rgba(0,230,118,0.35)';
        rWin.textContent     = 'currently stable';
    }

    // WHY section — show for CRITICAL/WATCH zones
    const whySec = document.getElementById('zdWhySection');
    const sigDiv = document.getElementById('zdSignals');
    if (state.status === 'CRITICAL' || state.status === 'WATCH') {
        sigDiv.innerHTML = '';
        buildWhySignals(state).forEach(sig => {
            const d = document.createElement('div');
            d.className = 'zd-sig';
            d.innerHTML = `<span class="zd-sig-check">✓</span><span>${sig}</span>`;
            sigDiv.appendChild(d);
        });
        whySec.style.display = 'block';
    } else {
        whySec.style.display = 'none';
    }

    // Actions
    const ul = document.getElementById('zdActions');
    ul.innerHTML = '';
    zone.actions.forEach(a => {
        const li = document.createElement('li');
        li.textContent = a;
        ul.appendChild(li);
    });

    document.getElementById('zoneDetail').classList.add('open');
}

function closeZoneDetail() {
    document.getElementById('zoneDetail').classList.remove('open');
    selectedZoneId = null;
}

// ── HEADER STATS ──────────────────────────────────────────────────────────
function updateHeader() {
    const counts = { SAFE:0, WATCH:0, CRITICAL:0 };
    Object.values(zoneStates).forEach(s => { counts[s.status] = (counts[s.status]||0) + 1; });

    document.getElementById('safeCount').textContent  = pad2(counts.SAFE  || 0);
    document.getElementById('watchCount').textContent = pad2(counts.WATCH || 0);
    document.getElementById('critCount').textContent  = pad2(counts.CRITICAL || 0);

    const incidents = (counts.WATCH||0) + (counts.CRITICAL||0);
    document.getElementById('activeIncCount').textContent = pad2(incidents);

    const badge = document.getElementById('incBadge');
    if (counts.CRITICAL > 0) badge.classList.add('hascrit');
    else badge.classList.remove('hascrit');
}

// ── INCIDENTS LIST ────────────────────────────────────────────────────────
function updateIncidentsList() {
    const list = document.getElementById('incList');

    const active = ZONE_DEFS
        .map(z => ({ zone: z, state: zoneStates[z.id] }))
        .filter(({ state }) => state.status === 'CRITICAL' || state.status === 'WATCH')
        .sort((a, b) => b.state.risk - a.state.risk);

    if (active.length === 0) {
        list.innerHTML = '<div class="inc-empty">● All zones within safe parameters</div>';
        document.getElementById('incTs').textContent = timeStr();
        return;
    }

    list.innerHTML = '';
    active.forEach(({ zone, state }) => {
        const card = document.createElement('div');
        card.className = `inc-card ${state.status === 'CRITICAL' ? 'c' : 'w'}`;
        card.onclick = () => openZoneDetail(zone.id);
        card.innerHTML = `
            <div class="ic-r1">
                <span class="ic-zone">${zone.name}</span>
                <span class="ic-badge ${state.status}">${state.status}</span>
            </div>
            <div class="ic-r2">
                <div class="ic-stat">People: <span>${state.people}</span></div>
                <div class="ic-stat">Compress: <span>${state.compression}%</span></div>
                <span class="ic-rpct ${state.status === 'CRITICAL' ? 'c' : 'w'}">Risk ${state.risk}%</span>
            </div>`;
        list.appendChild(card);
    });

    document.getElementById('incTs').textContent = timeStr();
}

// ── CRITICAL ALERT MODAL ─────────────────────────────────────────────────
function triggerCriticalAlert(zoneId) {
    // Don't re-trigger if already active for this zone
    if (critAlertZoneId === zoneId && !alertAcked) return;

    const zone  = ZONE_DEFS.find(z => z.id === zoneId);
    const state = zoneStates[zoneId];
    if (!zone || !state) return;

    critAlertZoneId  = zoneId;
    alertAcked       = false;
    alertStartTime   = new Date();
    alertSecsLeft    = 60;

    // Populate modal
    document.getElementById('cmZone').textContent  = zone.name;
    document.getElementById('cmRisk').textContent  = state.risk;
    document.getElementById('cmEscSec').textContent = Math.max(15, 70 - Math.floor(state.compression * 0.38));
    document.getElementById('cmCause').textContent = buildCause(state);
    document.getElementById('cmRiskScore').textContent = state.risk;

    // WHY signals
    const whyDiv = document.getElementById('cmWhy');
    whyDiv.innerHTML = '';
    buildWhySignals(state).forEach(sig => {
        const d = document.createElement('div');
        d.className = 'cm-why-sig';
        d.innerHTML = `<span class="cm-why-check">✓</span><span>${sig}</span>`;
        whyDiv.appendChild(d);
    });

    const actDiv = document.getElementById('cmActions');
    actDiv.innerHTML = '';
    zone.actions.forEach((a, i) => {
        const d = document.createElement('div');
        d.className = 'cm-action';
        d.innerHTML = `<span class="cm-action-n">${i+1}</span><span>${a}</span>`;
        actDiv.appendChild(d);
    });

    // Show escalation panel
    const esc = document.getElementById('escPanel');
    esc.style.display = 'flex';
    esc.style.flexDirection = 'column';
    document.getElementById('escIssued').textContent = alertStartTime.toTimeString().split(' ')[0];
    document.getElementById('escAck').textContent    = 'Pending';
    document.getElementById('escAck').className      = 'esc-val pend';
    document.getElementById('escBanner').classList.remove('show');

    // Show modal
    document.getElementById('critModal').classList.remove('hidden');

    // Audio
    try { document.getElementById('alertAudio').play().catch(()=>{}); } catch(e){}

    // Countdown
    clearInterval(alertTimerInt);
    refreshAlertTimer();
    alertTimerInt = setInterval(() => {
        alertSecsLeft--;
        refreshAlertTimer();
        if (alertSecsLeft <= 0) {
            clearInterval(alertTimerInt);
            triggerEscalation();
        }
    }, 1000);
}

function refreshAlertTimer() {
    const mm = pad2(Math.floor(alertSecsLeft / 60));
    const ss = pad2(alertSecsLeft % 60);
    const ts = `${mm}:${ss}`;
    const danger = alertSecsLeft <= 15;

    document.getElementById('cmTimer').textContent   = ts;
    document.getElementById('cmAckSec').textContent  = alertSecsLeft;
    document.getElementById('escTimer').textContent  = ts;
    document.getElementById('cmTimer').className     = 'cm-hdr-timer' + (danger ? ' red' : '');
    document.getElementById('escTimer').className    = 'esc-timer'    + (danger ? ' red' : '');
}

function acknowledgeAlert() {
    alertAcked = true;
    clearInterval(alertTimerInt);

    document.getElementById('critModal').classList.add('hidden');

    // Update escalation panel to confirmed
    const ackAt = new Date().toTimeString().split(' ')[0];
    document.getElementById('escAck').textContent = `Acknowledged — ${ackAt}`;
    document.getElementById('escAck').className   = 'esc-val acked';
    document.getElementById('escTimer').textContent = '—';
    document.getElementById('escTimer').className   = 'esc-timer';
}

function triggerEscalation() {
    document.getElementById('critModal').classList.add('hidden');
    document.getElementById('escAck').textContent = 'NO RESPONSE';
    document.getElementById('escAck').className   = 'esc-val escl';
    document.getElementById('escTimer').textContent = '00:00';
    document.getElementById('escTimer').className   = 'esc-timer red';
    document.getElementById('escBanner').classList.add('show');
}

function buildCause(state) {
    const parts = [];
    if (state.compression > 70) parts.push('High compression');
    if (state.movement.includes('CONVERGING') || state.movement.includes('EXIT')) parts.push('converging movement');
    if (state.exitBlockage === 'HIGH') parts.push('restricted exit capacity');
    if (state.density === 'HIGH' && parts.length < 2) parts.push('high crowd density');
    return parts.length > 0 ? parts.join(' + ') : 'Elevated crowd pressure detected';
}

// ── BEHAVIORAL SIGNAL EXPLANATIONS ───────────────────────────────────────
// Generates human-readable signals from zone state — the 'why' behind the alert.
// Authorities need to understand what changed, not just that a risk score went up.
function buildWhySignals(state) {
    const signals = [];

    // Density / compression change
    if (state.compression > 75) {
        const pct = Math.round((state.compression - 45) * 0.9);
        signals.push(`Crowd density increased ${pct}% above baseline`);
    } else if (state.compression > 55) {
        const pct = Math.round((state.compression - 40) * 0.7);
        signals.push(`Crowd density elevated — ${pct}% above normal`);
    }

    // Movement convergence
    if (state.movement.includes('CONVERGING')) {
        signals.push('Crowd movement converging — multiple flows meeting');
    } else if (state.movement.includes('EXIT')) {
        signals.push('Crowd movement directed toward exit at high rate');
    }

    // Speed signal (inferred from status)
    if (state.status === 'CRITICAL' || state.risk > 60) {
        signals.push('Average crowd speed increased significantly');
    } else if (state.risk > 40) {
        signals.push('Average crowd speed above normal');
    }

    // Local compression
    if (state.compression > 60) {
        signals.push(`Local compression reached ${state.compression}%`);
    }

    // Exit flow
    if (state.exitBlockage === 'HIGH') {
        signals.push('Exit flow severely reduced — bottleneck forming');
    } else if (state.exitBlockage === 'MODERATE') {
        signals.push('Exit flow restricted — throughput decreasing');
    }

    return signals;
}

// ── INCIDENT REPLAY ──────────────────────────────────────────────────────
function loadReplay(id) {
    clearInterval(replayInt);
    replayStepIdx = -1;
    renderTimeline(id, -1);
}

function renderTimeline(id, upTo) {
    const replay = REPLAYS[id];
    if (!replay) return;

    const tl = document.getElementById('rpTimeline');
    tl.innerHTML = '';

    const evts = upTo >= 0 ? replay.events.slice(0, upTo+1) : replay.events;
    evts.forEach((e, i) => {
        const div = document.createElement('div');
        div.className = 'rp-evt';
        div.style.opacity = (upTo >= 0 && i === upTo) ? '1' : (upTo >= 0 ? '0.65' : '1');
        div.innerHTML = `
            <div class="rp-dot ${e.type}"></div>
            <div class="rp-info">
                <div class="rp-t">${e.time}</div>
                <div class="rp-l">${e.label}</div>
            </div>`;
        tl.appendChild(div);
    });

    // Auto-scroll to latest
    const scroll = tl.parentElement;
    if (scroll) scroll.scrollTop = scroll.scrollHeight;
}

function startReplay() {
    const id = document.getElementById('rpSelect').value;
    const replay = REPLAYS[id];
    if (!replay) return;

    clearInterval(replayInt);
    replayStepIdx = 0;
    renderTimeline(id, replayStepIdx);

    replayInt = setInterval(() => {
        replayStepIdx++;
        if (replayStepIdx >= replay.events.length) {
            clearInterval(replayInt);
            renderTimeline(id, replay.events.length - 1);
            return;
        }
        renderTimeline(id, replayStepIdx);
    }, 1100);
}

// ── DEMO SIMULATION ──────────────────────────────────────────────────────
function runDemo() {
    demoTick++;

    // Realistic variance on each zone
    Object.keys(zoneStates).forEach(id => {
        const base  = DEMO_BASE[id];
        const state = zoneStates[id];
        const phase = demoTick * 0.18 + id.charCodeAt(0);
        state.people      = Math.max(0, base.people + Math.round(Math.sin(phase) * 9));
        state.compression = Math.min(100, Math.max(0, base.compression + Math.sin(phase * 0.9) * 5));
        state.risk        = Math.min(100, Math.max(0, base.risk + Math.sin(phase * 0.7) * 7));
    });

    // Trigger critical alert after ~10s in demo
    if (demoTick === 5 && !alertAcked && zoneStates['C'].status === 'CRITICAL') {
        triggerCriticalAlert('C');
    }

    updateHeader();
    updateIncidentsList();
    if (selectedZoneId) openZoneDetail(selectedZoneId);
}

function startDemo() {
    if (demoInt) return;
    demoInt = setInterval(runDemo, 2000);
}

function stopDemo() {
    clearInterval(demoInt);
    demoInt = null;
}

// ── WEBSOCKET CONNECTION ─────────────────────────────────────────────────
function authorityConnect() {
    const rawHost = document.getElementById('authUrl').value.trim();
    const code    = document.getElementById('authCode').value.trim().toUpperCase();
    const host    = rawHost.replace(/^wss?:\/\//, '').replace(/^https?:\/\//, '');

    if (!host || code.length < 6) {
        alert('Enter backend URL and a 6-character session code.');
        return;
    }

    localStorage.setItem(STORE_HOST, host);
    localStorage.setItem(STORE_CODE, code);

    const isLocal = host.startsWith('localhost') || host.startsWith('127.') || host.startsWith('10.');
    const wsUrl   = `${isLocal ? 'ws' : 'wss'}://${host}/ws/dashboard/${code}`;
    setConnStatus('linking');

    if (ws) { ws.close(); ws = null; }
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        setConnStatus('live');
        isLive = true;
        stopDemo();
        document.getElementById('demoPill').classList.add('hidden');
    };

    ws.onclose = () => {
        setConnStatus('offline');
        isLive = false;
        startDemo();
        document.getElementById('demoPill').classList.remove('hidden');
        setTimeout(() => { if (!isLive) authorityConnect(); }, 5000);
    };

    ws.onerror = () => setConnStatus('err');

    ws.onmessage = event => {
        if (typeof event.data !== 'string') return;
        try {
            const data = JSON.parse(event.data);
            if (data.type !== 'tts_instruction') processLiveData(data);
        } catch(e) {}
    };
}

function processLiveData(data) {
    const pred   = data.prediction || {};
    const score  = pred.risk_score || 0;
    const label  = pred.risk_label || 'SAFE';
    const zones  = data.zones || [];

    if (zones.length > 0) {
        const top = zones.reduce((m, z) => z.people > m.people ? z : m, zones[0]);
        const s   = zoneStates['C'];
        s.people      = data.count || top.people || 0;
        s.compression = Math.round((top.compression || 0) * 100);
        s.risk        = Math.round(score);
        s.status      = label;
        s.density     = score > 65 ? 'HIGH' : score > 35 ? 'MODERATE' : 'LOW';
        s.exitBlockage = (top.compression||0) > 0.7 ? 'HIGH' : (top.compression||0) > 0.4 ? 'MODERATE' : 'LOW';
    }

    if (label === 'CRITICAL' && !alertAcked) {
        triggerCriticalAlert('C');
    } else if (label !== 'CRITICAL' && critAlertZoneId === 'C' && alertAcked) {
        critAlertZoneId = null;
        alertAcked = false;
        document.getElementById('escPanel').style.display = 'none';
    }

    updateHeader();
    updateIncidentsList();
    if (selectedZoneId) openZoneDetail(selectedZoneId);
}

function setConnStatus(s) {
    const dot = document.getElementById('connDot');
    const txt = document.getElementById('connTxt');
    const map = {
        linking: { cls: 'cdot linking', t: 'CONNECTING...' },
        live:    { cls: 'cdot live',    t: 'LIVE' },
        offline: { cls: 'cdot',         t: 'OFFLINE' },
        err:     { cls: 'cdot err',     t: 'ERROR' }
    };
    const m = map[s] || map.offline;
    dot.className  = m.cls;
    txt.textContent = m.t;
}

// ── CLOCK ────────────────────────────────────────────────────────────────
function timeStr() {
    return new Date().toTimeString().split(' ')[0];
}

function pad2(n) {
    return String(n).padStart(2, '0');
}

function tickClock() {
    document.getElementById('mapClock').textContent = timeStr();
}

// ── INIT ─────────────────────────────────────────────────────────────────
window.addEventListener('load', () => {
    resizeCanvas();
    mapLoop();
    setInterval(tickClock, 1000);

    // Restore saved connection
    const sh = localStorage.getItem(STORE_HOST);
    const sc = localStorage.getItem(STORE_CODE);
    if (sh) document.getElementById('authUrl').value  = sh;
    if (sc) document.getElementById('authCode').value = sc;

    // Start demo mode
    updateHeader();
    updateIncidentsList();
    loadReplay('204');
    startDemo();

    // Resize canvas on window resize
    let resizeDebounce;
    window.addEventListener('resize', () => {
        clearTimeout(resizeDebounce);
        resizeDebounce = setTimeout(resizeCanvas, 80);
    });
});
