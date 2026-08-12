// ═══════════════════════════════════════════════════════════════
//  CROUDIFY — AUTHORITY PORTAL  |  authority.js
//  Emergency Operations Command Center — Live Data Only
// ═══════════════════════════════════════════════════════════════

'use strict';

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

// ── INCIDENT REPLAY DATA (historical — always available) ─────────────────
const REPLAYS = {
    '204': {
        events: [
            { time: '14:32:10', label: 'Normal crowd conditions',                    type: 'normal' },
            { time: '14:32:35', label: 'Density increasing near East Gate',          type: 'watch'  },
            { time: '14:32:51', label: 'Movement converging toward exit',            type: 'watch'  },
            { time: '14:33:02', label: 'Compression increasing — 68%',              type: 'watch'  },
            { time: '14:33:08', label: 'Risk threshold detected — 73%',             type: 'alert'  },
            { time: '14:33:16', label: 'Authority alerted',                          type: 'alert'  },
            { time: '14:33:29', label: 'Action acknowledged by Officer K. Sharma',   type: 'action' }
        ]
    },
    '203': {
        events: [
            { time: '11:45:00', label: 'Normal crowd conditions',                    type: 'normal' },
            { time: '11:47:30', label: 'Crowd buildup at Food Court entry',          type: 'watch'  },
            { time: '11:48:15', label: 'Flow rate decreasing',                       type: 'watch'  },
            { time: '11:49:02', label: 'Risk elevated to WARNING — 54%',            type: 'watch'  },
            { time: '11:49:44', label: 'Authority alerted',                          type: 'alert'  },
            { time: '11:50:11', label: 'Secondary lanes opened — action taken',      type: 'action' },
            { time: '11:51:30', label: 'Risk decreasing — situation resolved',       type: 'normal' }
        ]
    },
    '201': {
        events: [
            { time: '20:12:00', label: 'Normal crowd conditions',                    type: 'normal' },
            { time: '20:14:20', label: 'Event end surge — crowd density rising',     type: 'watch'  },
            { time: '20:15:05', label: 'Exit blockage detected — Main Stage',        type: 'alert'  },
            { time: '20:15:12', label: 'Authority alerted',                          type: 'alert'  },
            { time: '20:15:30', label: 'Response team deployed to Main Stage',       type: 'action' },
            { time: '20:17:00', label: 'Crowd cleared — situation resolved',         type: 'normal' }
        ]
    }
};

// ── STATE ──────────────────────────────────────────────────────────────────
let zoneStates      = {};      // Populated only from live backend data
let selectedZoneId  = null;
let critAlertZoneId = null;
let alertStartTime  = null;
let alertSecsLeft   = 60;
let alertTimerInt   = null;
let alertAcked      = false;
let isConnected     = false;   // No demo mode — only live connection
let pulsePhase      = 0;
let replayStepIdx   = -1;
let replayInt       = null;
let ws              = null;

const STORE_HOST = 'croudify_auth_host';
const STORE_CODE = 'croudify_auth_code';

// ── CANVAS SETUP ─────────────────────────────────────────────────────────
const canvas = document.getElementById('eventMap');
const ctx    = canvas.getContext('2d');

function resizeCanvas() {
    const panel = canvas.parentElement;
    const rect  = panel.getBoundingClientRect();
    canvas.width  = rect.width;
    canvas.height = Math.max(200, rect.height - 36);
}

// ── ZONE COLOR HELPERS ────────────────────────────────────────────────────
function getZoneColor(status) {
    if (status === 'CRITICAL') return { fill:'rgba(255,59,59,0.16)',  stroke:'#ff3b3b', glow:'rgba(255,59,59,0.55)',  text:'#ff3b3b' };
    if (status === 'WATCH')    return { fill:'rgba(255,170,0,0.14)',  stroke:'#ffaa00', glow:'rgba(255,170,0,0.5)',   text:'#ffaa00' };
    return                           { fill:'rgba(0,230,118,0.11)',   stroke:'#00e676', glow:'rgba(0,230,118,0.42)',  text:'#00e676' };
}

// ── MAP DRAW ──────────────────────────────────────────────────────────────
function drawMap() {
    const W = canvas.width, H = canvas.height;
    if (!W || !H) return;

    ctx.fillStyle = '#060a0f';
    ctx.fillRect(0, 0, W, H);

    // Grid
    ctx.strokeStyle = 'rgba(0,183,255,0.028)';
    ctx.lineWidth = 1;
    for (let x = 0; x < W; x += 45) { ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,H); ctx.stroke(); }
    for (let y = 0; y < H; y += 45) { ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke(); }

    // Venue bounds
    const ml = W*0.07, mt = H*0.07, vw = W-ml*2, vh = H-mt-H*0.10;
    const vx = ml, vy = mt;

    ctx.fillStyle = 'rgba(0,183,255,0.012)';
    ctx.fillRect(vx, vy, vw, vh);

    // Walls — brighter when live
    const wallAlpha = isConnected ? '0.22' : '0.07';
    ctx.strokeStyle = `rgba(0,183,255,${wallAlpha})`;
    ctx.lineWidth = 1.5; ctx.setLineDash([]);
    ctx.strokeRect(vx, vy, vw, vh);

    // Internal partitions
    const partAlpha = isConnected ? '0.08' : '0.03';
    ctx.strokeStyle = `rgba(0,183,255,${partAlpha})`;
    ctx.lineWidth = 1; ctx.setLineDash([6,10]);
    ctx.beginPath(); ctx.moveTo(vx+vw*0.40,vy); ctx.lineTo(vx+vw*0.40,vy+vh); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(vx+vw*0.40,vy+vh*0.58); ctx.lineTo(vx+vw,vy+vh*0.58); ctx.stroke();
    ctx.setLineDash([]);

    if (isConnected) {
        // Area labels
        ctx.font = '600 10px Rajdhani, sans-serif';
        ctx.fillStyle = 'rgba(0,183,255,0.16)';
        ctx.textAlign = 'center';
        ctx.fillText('WEST WING', vx+vw*0.20, vy+vh*0.80);
        ctx.fillText('CENTRAL HALL', vx+vw*0.70, vy+vh*0.25);
        // Exit markers
        drawWallMarker(vx+vw*0.46, vy,       '▲ NORTH ENTRANCE', true,  false);
        drawWallMarker(vx+vw,      vy+vh*0.67,'→ EAST GATE EXIT', false, true );
        drawWallMarker(vx+vw*0.50, vy+vh,     '▼ MAIN EXIT',      false, false);
        // Live zones
        ZONE_DEFS.forEach(z => {
            const s = zoneStates[z.id];
            if (!s) return;
            drawZone(z, s, vx+z.mapX*vw, vy+z.mapY*vh, Math.min(vw,vh)*z.mapR);
        });
    } else {
        // Standby — ghost (inactive) zones
        ZONE_DEFS.forEach(z => {
            drawZoneInactive(z, vx+z.mapX*vw, vy+z.mapY*vh, Math.min(vw,vh)*z.mapR);
        });
    }
}

function drawWallMarker(x, y, label, isTop, isRight) {
    ctx.font = '600 9px Rajdhani, sans-serif';
    ctx.fillStyle = 'rgba(74,122,155,0.52)';
    ctx.textAlign = isRight ? 'left' : 'center';
    ctx.fillText(label, x + (isRight ? 6 : 0), y + (isTop ? -5 : 13));
}

function drawZoneInactive(zone, cx, cy, r) {
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI*2);
    ctx.fillStyle = 'rgba(18,32,50,0.45)'; ctx.fill();
    ctx.strokeStyle = 'rgba(63,104,128,0.18)'; ctx.lineWidth = 1; ctx.stroke();

    ctx.font = `700 ${Math.max(9,r*0.28)}px Rajdhani, sans-serif`;
    ctx.fillStyle = 'rgba(63,104,128,0.32)'; ctx.textAlign = 'center';
    ctx.fillText(`ZONE ${zone.id}`, cx, cy-r-7);
    ctx.font = `500 ${Math.max(8,r*0.22)}px Rajdhani, sans-serif`;
    ctx.fillStyle = 'rgba(63,104,128,0.22)';
    ctx.fillText(zone.name, cx, cy-r-18);
    ctx.font = `600 ${Math.max(12,r*0.32)}px Share Tech Mono, monospace`;
    ctx.fillStyle = 'rgba(63,104,128,0.22)';
    ctx.fillText('—', cx, cy+5);
}

function drawZone(zone, state, cx, cy, r) {
    const c = getZoneColor(state.status);
    const isCrit  = state.status === 'CRITICAL';
    const isWatch = state.status === 'WATCH';

    let glowR = r;
    if (isCrit)  glowR = r + Math.sin(pulsePhase*2.2)*r*0.22;
    else if (isWatch) glowR = r + Math.sin(pulsePhase*1.4)*r*0.10;

    // Outer glow
    const ga = isCrit ? '0.22' : isWatch ? '0.16' : '0.12';
    const g1 = ctx.createRadialGradient(cx,cy,0,cx,cy,glowR*2);
    g1.addColorStop(0, c.glow.replace(/[\d.]+\)$/,ga+')'));
    g1.addColorStop(1, 'transparent');
    ctx.beginPath(); ctx.arc(cx,cy,glowR*2,0,Math.PI*2); ctx.fillStyle=g1; ctx.fill();

    // Circle fill
    const g2 = ctx.createRadialGradient(cx,cy,0,cx,cy,glowR);
    g2.addColorStop(0, c.fill.replace(/[\d.]+\)$/,'0.24)'));
    g2.addColorStop(1, c.fill);
    ctx.beginPath(); ctx.arc(cx,cy,glowR,0,Math.PI*2); ctx.fillStyle=g2; ctx.fill();

    // Ring
    ctx.beginPath(); ctx.arc(cx,cy,glowR,0,Math.PI*2);
    ctx.strokeStyle=c.stroke; ctx.lineWidth=isCrit?2:1.5; ctx.stroke();
    ctx.beginPath(); ctx.arc(cx,cy,5,0,Math.PI*2); ctx.fillStyle=c.stroke; ctx.fill();

    // Labels
    ctx.font=`700 ${Math.max(9,r*0.28)}px Rajdhani,sans-serif`; ctx.fillStyle=c.text; ctx.textAlign='center';
    ctx.fillText(`ZONE ${zone.id}`, cx, cy-glowR-7);
    ctx.font=`500 ${Math.max(8,r*0.22)}px Rajdhani,sans-serif`; ctx.fillStyle='rgba(184,212,234,0.65)';
    ctx.fillText(zone.name, cx, cy-glowR-18);

    // People count
    if (r>28) {
        ctx.font=`700 ${Math.max(10,r*0.30)}px Share Tech Mono,monospace`;
        ctx.fillStyle='rgba(232,244,255,0.9)'; ctx.fillText(state.people, cx, cy+4);
        ctx.font=`500 ${Math.max(7,r*0.19)}px Rajdhani,sans-serif`;
        ctx.fillStyle='rgba(63,104,128,0.85)'; ctx.fillText('people', cx, cy+17);
    }

    // Risk % badge
    if (state.risk>0) {
        const bx=cx+glowR*0.68, by=cy-glowR*0.68, br=r*0.27;
        ctx.beginPath(); ctx.arc(bx,by,br,0,Math.PI*2);
        ctx.fillStyle='rgba(6,10,15,0.92)'; ctx.fill();
        ctx.strokeStyle=c.stroke; ctx.lineWidth=1; ctx.stroke();
        ctx.font=`700 ${Math.max(7,r*0.20)}px Share Tech Mono,monospace`;
        ctx.fillStyle=c.text; ctx.textAlign='center';
        ctx.fillText(`${state.risk}%`, bx, by+3);
    }
}

function mapLoop() {
    pulsePhase += 0.04;
    drawMap();
    requestAnimationFrame(mapLoop);
}

// ── CANVAS CLICK → ZONE HIT TEST ─────────────────────────────────────────
canvas.addEventListener('click', e => {
    if (!isConnected) return;
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX-rect.left)*(canvas.width/rect.width);
    const my = (e.clientY-rect.top)*(canvas.height/rect.height);
    const W=canvas.width, H=canvas.height;
    const ml=W*0.07, mt=H*0.07, vw=W-ml*2, vh=H-mt-H*0.10;

    let hit=null, minDist=Infinity;
    ZONE_DEFS.forEach(z => {
        const zx=ml+z.mapX*vw, zy=mt+z.mapY*vh;
        const zr=Math.min(vw,vh)*z.mapR*1.4;
        const d=Math.hypot(mx-zx,my-zy);
        if (d<=zr && d<minDist) { minDist=d; hit=z; }
    });
    if (hit) openZoneDetail(hit.id);
});

// ── ZONE DETAIL PANEL ────────────────────────────────────────────────────
function openZoneDetail(zoneId) {
    const zone  = ZONE_DEFS.find(z=>z.id===zoneId);
    const state = zoneStates[zoneId];
    if (!zone||!state) return;
    selectedZoneId = zoneId;

    document.getElementById('zdName').textContent   = zone.fullName;
    document.getElementById('zdPeople').textContent = state.people;

    const dEl = document.getElementById('zdDensity');
    dEl.textContent = state.density;
    dEl.className   = 'zd-val '+(state.density==='HIGH'?'c':state.density==='MODERATE'?'w':'s');

    document.getElementById('zdMovement').textContent = state.movement;

    const cEl = document.getElementById('zdCompression');
    cEl.textContent = state.compression+'%';
    cEl.className   = 'zd-val '+(state.compression>70?'c':state.compression>45?'w':'s');

    const eEl = document.getElementById('zdExit');
    eEl.textContent = state.exitBlockage;
    eEl.className   = 'zd-val '+(state.exitBlockage==='HIGH'?'c':state.exitBlockage==='MODERATE'?'w':'s');

    const rBlock=document.getElementById('zdRiskBlock');
    const rPct=document.getElementById('zdRiskPct');
    const rWin=document.getElementById('zdRiskWin');
    rPct.textContent = state.risk+'%';

    if (state.status==='CRITICAL') {
        rBlock.style.cssText='margin:0.8rem 0;padding:0.8rem;border:1px solid rgba(255,59,59,0.28);border-radius:4px;background:rgba(255,59,59,0.06);text-align:center;';
        rPct.style.cssText='font-family:Orbitron,sans-serif;font-size:2.3rem;font-weight:900;line-height:1;color:#ff3b3b;text-shadow:0 0 24px rgba(255,59,59,0.5)';
        rWin.textContent='within 60 seconds — ACTION REQUIRED';
    } else if (state.status==='WATCH') {
        rBlock.style.cssText='margin:0.8rem 0;padding:0.8rem;border:1px solid rgba(255,170,0,0.28);border-radius:4px;background:rgba(255,170,0,0.06);text-align:center;';
        rPct.style.cssText='font-family:Orbitron,sans-serif;font-size:2.3rem;font-weight:900;line-height:1;color:#ffaa00;text-shadow:0 0 20px rgba(255,170,0,0.4)';
        rWin.textContent='within 90 seconds — monitor closely';
    } else {
        rBlock.style.cssText='margin:0.8rem 0;padding:0.8rem;border:1px solid rgba(0,230,118,0.28);border-radius:4px;background:rgba(0,230,118,0.06);text-align:center;';
        rPct.style.cssText='font-family:Orbitron,sans-serif;font-size:2.3rem;font-weight:900;line-height:1;color:#00e676;text-shadow:0 0 20px rgba(0,230,118,0.35)';
        rWin.textContent='currently stable';
    }

    // WHY section — only for WATCH/CRITICAL
    const whySec=document.getElementById('zdWhySection');
    const sigDiv=document.getElementById('zdSignals');
    const sigs=buildWhySignals(state);
    if ((state.status==='CRITICAL'||state.status==='WATCH') && sigs.length>0) {
        sigDiv.innerHTML='';
        sigs.forEach(sig=>{
            const d=document.createElement('div');
            d.className='zd-sig';
            d.innerHTML=`<span class="zd-sig-check">✓</span><span>${sig}</span>`;
            sigDiv.appendChild(d);
        });
        whySec.style.display='block';
    } else {
        whySec.style.display='none';
    }

    const ul=document.getElementById('zdActions');
    ul.innerHTML='';
    zone.actions.forEach(a=>{
        const li=document.createElement('li'); li.textContent=a; ul.appendChild(li);
    });

    document.getElementById('zoneDetail').classList.add('open');
}

function closeZoneDetail() {
    document.getElementById('zoneDetail').classList.remove('open');
    selectedZoneId=null;
}

// ── HEADER STATS ──────────────────────────────────────────────────────────
function updateHeader() {
    if (!isConnected) {
        ['safeCount','watchCount','critCount'].forEach(id=>document.getElementById(id).textContent='—');
        document.getElementById('activeIncCount').textContent='—';
        document.getElementById('incBadge').classList.remove('hascrit');
        return;
    }
    const counts={SAFE:0,WATCH:0,CRITICAL:0};
    Object.values(zoneStates).forEach(s=>{counts[s.status]=(counts[s.status]||0)+1;});
    document.getElementById('safeCount').textContent  = pad2(counts.SAFE||0);
    document.getElementById('watchCount').textContent = pad2(counts.WATCH||0);
    document.getElementById('critCount').textContent  = pad2(counts.CRITICAL||0);
    const inc=(counts.WATCH||0)+(counts.CRITICAL||0);
    document.getElementById('activeIncCount').textContent=pad2(inc);
    const badge=document.getElementById('incBadge');
    if (counts.CRITICAL>0) badge.classList.add('hascrit'); else badge.classList.remove('hascrit');
}

// ── INCIDENTS LIST ────────────────────────────────────────────────────────
function updateIncidentsList() {
    const list=document.getElementById('incList');
    if (!isConnected) {
        list.innerHTML='<div class="inc-empty">No active session — connect to view incidents</div>';
        document.getElementById('incTs').textContent='—';
        return;
    }
    const active=ZONE_DEFS
        .map(z=>({zone:z,state:zoneStates[z.id]}))
        .filter(({state})=>state&&(state.status==='CRITICAL'||state.status==='WATCH'))
        .sort((a,b)=>b.state.risk-a.state.risk);

    if (active.length===0) {
        list.innerHTML='<div class="inc-empty">● All zones within safe parameters</div>';
        document.getElementById('incTs').textContent=timeStr();
        return;
    }
    list.innerHTML='';
    active.forEach(({zone,state})=>{
        const card=document.createElement('div');
        card.className=`inc-card ${state.status==='CRITICAL'?'c':'w'}`;
        card.onclick=()=>openZoneDetail(zone.id);
        card.innerHTML=`
            <div class="ic-r1">
                <span class="ic-zone">${zone.name}</span>
                <span class="ic-badge ${state.status}">${state.status}</span>
            </div>
            <div class="ic-r2">
                <div class="ic-stat">People: <span>${state.people}</span></div>
                <div class="ic-stat">Compress: <span>${state.compression}%</span></div>
                <span class="ic-rpct ${state.status==='CRITICAL'?'c':'w'}">Risk ${state.risk}%</span>
            </div>`;
        list.appendChild(card);
    });
    document.getElementById('incTs').textContent=timeStr();
}

// ── OFFLINE / ONLINE STATE ────────────────────────────────────────────────
function setOfflineUI() {
    isConnected=false;
    document.getElementById('standbyOverlay').style.display='flex';
    document.getElementById('escPanel').style.display='none';
    document.getElementById('zoneDetail').classList.remove('open');
    document.getElementById('critModal').classList.add('hidden');
    selectedZoneId=null; critAlertZoneId=null;
    clearInterval(alertTimerInt);
    // Sys-pill — standby (muted)
    const pill=document.getElementById('sysPill');
    const dot =document.getElementById('sysDot');
    const txt =document.getElementById('sysTxt');
    if (pill) { pill.style.borderColor='rgba(63,104,128,0.2)'; pill.style.background='rgba(63,104,128,0.04)'; pill.style.color='rgba(63,104,128,0.55)'; }
    if (dot)  { dot.style.background='rgba(63,104,128,0.5)'; dot.style.boxShadow='none'; dot.style.animation='none'; }
    if (txt)  { txt.textContent='SYSTEM STANDBY'; }
    updateHeader();
    updateIncidentsList();
}

function setOnlineUI() {
    isConnected=true;
    document.getElementById('standbyOverlay').style.display='none';
    // Sys-pill — operational (green)
    const pill=document.getElementById('sysPill');
    const dot =document.getElementById('sysDot');
    const txt =document.getElementById('sysTxt');
    if (pill) { pill.style.borderColor=''; pill.style.background=''; pill.style.color=''; }
    if (dot)  { dot.style.background=''; dot.style.boxShadow=''; dot.style.animation=''; }
    if (txt)  { txt.textContent='ALL SYSTEMS OPERATIONAL'; }
    updateHeader();
    updateIncidentsList();
}

// ── CRITICAL ALERT MODAL ─────────────────────────────────────────────────
function triggerCriticalAlert(zoneId) {
    if (!isConnected) return;
    if (critAlertZoneId===zoneId && !alertAcked) return;
    const zone=ZONE_DEFS.find(z=>z.id===zoneId);
    const state=zoneStates[zoneId];
    if (!zone||!state) return;

    critAlertZoneId=zoneId; alertAcked=false;
    alertStartTime=new Date(); alertSecsLeft=60;

    document.getElementById('cmZone').textContent   = zone.name;
    document.getElementById('cmRisk').textContent   = state.risk;
    document.getElementById('cmEscSec').textContent = Math.max(15,70-Math.floor(state.compression*0.38));
    document.getElementById('cmCause').textContent  = buildCause(state);
    document.getElementById('cmRiskScore').textContent = state.risk;

    const whyDiv=document.getElementById('cmWhy');
    whyDiv.innerHTML='';
    buildWhySignals(state).forEach(sig=>{
        const d=document.createElement('div'); d.className='cm-why-sig';
        d.innerHTML=`<span class="cm-why-check">✓</span><span>${sig}</span>`;
        whyDiv.appendChild(d);
    });

    const actDiv=document.getElementById('cmActions');
    actDiv.innerHTML='';
    zone.actions.forEach((a,i)=>{
        const d=document.createElement('div'); d.className='cm-action';
        d.innerHTML=`<span class="cm-action-n">${i+1}</span><span>${a}</span>`;
        actDiv.appendChild(d);
    });

    const esc=document.getElementById('escPanel');
    esc.style.display='flex'; esc.style.flexDirection='column';
    document.getElementById('escIssued').textContent = alertStartTime.toTimeString().split(' ')[0];
    document.getElementById('escAck').textContent    = 'Pending';
    document.getElementById('escAck').className      = 'esc-val pend';
    document.getElementById('escBanner').classList.remove('show');
    document.getElementById('critModal').classList.remove('hidden');

    try { document.getElementById('alertAudio').play().catch(()=>{}); } catch(e){}

    clearInterval(alertTimerInt);
    refreshAlertTimer();
    alertTimerInt=setInterval(()=>{
        alertSecsLeft--;
        refreshAlertTimer();
        if (alertSecsLeft<=0) { clearInterval(alertTimerInt); triggerEscalation(); }
    },1000);
}

function refreshAlertTimer() {
    const ts=`${pad2(Math.floor(alertSecsLeft/60))}:${pad2(alertSecsLeft%60)}`;
    const danger=alertSecsLeft<=15;
    document.getElementById('cmTimer').textContent   = ts;
    document.getElementById('cmAckSec').textContent  = alertSecsLeft;
    document.getElementById('escTimer').textContent  = ts;
    document.getElementById('cmTimer').className     = 'cm-hdr-timer'+(danger?' red':'');
    document.getElementById('escTimer').className    = 'esc-timer'+(danger?' red':'');
}

function acknowledgeAlert() {
    alertAcked=true; clearInterval(alertTimerInt);
    document.getElementById('critModal').classList.add('hidden');
    document.getElementById('escAck').textContent = `Acknowledged — ${new Date().toTimeString().split(' ')[0]}`;
    document.getElementById('escAck').className   = 'esc-val acked';
    document.getElementById('escTimer').textContent='—';
    document.getElementById('escTimer').className='esc-timer';
}

function triggerEscalation() {
    document.getElementById('critModal').classList.add('hidden');
    document.getElementById('escAck').textContent='NO RESPONSE';
    document.getElementById('escAck').className='esc-val escl';
    document.getElementById('escTimer').textContent='00:00';
    document.getElementById('escTimer').className='esc-timer red';
    document.getElementById('escBanner').classList.add('show');
}

// ── BEHAVIORAL SIGNAL EXPLANATIONS ───────────────────────────────────────
// Translates raw zone metrics into plain English signals.
// Authorities understand *what changed*, not just *that a number went up*.
function buildWhySignals(state) {
    const s=[];
    if (state.compression>75) s.push(`Crowd density increased ${Math.round((state.compression-45)*0.9)}% above baseline`);
    else if (state.compression>55) s.push(`Crowd density elevated — ${Math.round((state.compression-40)*0.7)}% above normal`);
    if (state.movement.includes('CONVERGING')) s.push('Crowd movement converging — multiple flows meeting');
    else if (state.movement.includes('EXIT'))  s.push('Crowd movement directed toward exit at high rate');
    if (state.status==='CRITICAL'||state.risk>60) s.push('Average crowd speed increased significantly');
    else if (state.risk>40)                        s.push('Average crowd speed above normal');
    if (state.compression>60) s.push(`Local compression reached ${state.compression}%`);
    if (state.exitBlockage==='HIGH')     s.push('Exit flow severely reduced — bottleneck forming');
    else if (state.exitBlockage==='MODERATE') s.push('Exit flow restricted — throughput decreasing');
    return s;
}

function buildCause(state) {
    const p=[];
    if (state.compression>70) p.push('High compression');
    if (state.movement.includes('CONVERGING')||state.movement.includes('EXIT')) p.push('converging movement');
    if (state.exitBlockage==='HIGH') p.push('restricted exit capacity');
    if (state.density==='HIGH'&&p.length<2) p.push('high crowd density');
    return p.length>0?p.join(' + '):'Elevated crowd pressure detected';
}

// ── INCIDENT REPLAY ──────────────────────────────────────────────────────
function loadReplay(id) {
    clearInterval(replayInt); replayStepIdx=-1; renderTimeline(id,-1);
}

function renderTimeline(id,upTo) {
    const replay=REPLAYS[id];
    if (!replay) return;
    const tl=document.getElementById('rpTimeline');
    tl.innerHTML='';
    const evts=upTo>=0?replay.events.slice(0,upTo+1):replay.events;
    evts.forEach((e,i)=>{
        const div=document.createElement('div'); div.className='rp-evt';
        div.style.opacity=(upTo>=0&&i===upTo)?'1':(upTo>=0?'0.65':'1');
        div.innerHTML=`<div class="rp-dot ${e.type}"></div><div class="rp-info"><div class="rp-t">${e.time}</div><div class="rp-l">${e.label}</div></div>`;
        tl.appendChild(div);
    });
    const scroll=tl.parentElement;
    if (scroll) scroll.scrollTop=scroll.scrollHeight;
}

function startReplay() {
    const id=document.getElementById('rpSelect').value;
    const replay=REPLAYS[id];
    if (!replay) return;
    clearInterval(replayInt); replayStepIdx=0; renderTimeline(id,0);
    replayInt=setInterval(()=>{
        replayStepIdx++;
        if (replayStepIdx>=replay.events.length) { clearInterval(replayInt); renderTimeline(id,replay.events.length-1); return; }
        renderTimeline(id,replayStepIdx);
    },1100);
}

// ── WEBSOCKET CONNECTION ─────────────────────────────────────────────────
function authorityConnect() {
    const rawHost=document.getElementById('authUrl').value.trim();
    const code=document.getElementById('authCode').value.trim().toUpperCase();
    const host=rawHost.replace(/^wss?:\/\//,'').replace(/^https?:\/\//,'');
    if (!host||code.length<6) { alert('Enter backend URL and a 6-character session code.'); return; }

    localStorage.setItem(STORE_HOST,host);
    localStorage.setItem(STORE_CODE,code);

    const isLocal=host.startsWith('localhost')||host.startsWith('127.')||host.startsWith('10.');
    const wsUrl=`${isLocal?'ws':'wss'}://${host}/ws/dashboard/${code}`;
    setConnStatus('linking');
    if (ws) { ws.close(); ws=null; }
    ws=new WebSocket(wsUrl);

    ws.onopen=()=>{ setConnStatus('live'); setOnlineUI(); };

    ws.onclose=ev=>{
        setConnStatus('offline'); setOfflineUI();
        if (ev.code!==4404) setTimeout(()=>{ if (!isConnected) authorityConnect(); },5000);
    };

    ws.onerror=()=>{ setConnStatus('err'); setOfflineUI(); };

    ws.onmessage=ev=>{
        if (typeof ev.data!=='string') return;
        try { const d=JSON.parse(ev.data); if (d.type!=='tts_instruction') processLiveData(d); } catch(e){}
    };
}

function processLiveData(data) {
    const pred=data.prediction||{};
    const score=pred.risk_score||0;
    const label=pred.risk_label||'SAFE';
    const zones=data.zones||[];

    // Initialise all zones on first data packet
    if (Object.keys(zoneStates).length===0) {
        ZONE_DEFS.forEach(z=>{
            zoneStates[z.id]={people:0,density:'LOW',compression:0,movement:'—',exitBlockage:'LOW',risk:0,status:'SAFE'};
        });
    }

    // Update Zone C (East Gate) from the live camera feed
    const s=zoneStates['C'];
    if (zones.length>0) {
        const top=zones.reduce((m,z)=>z.people>m.people?z:m,zones[0]);
        s.people      =data.count||top.people||0;
        s.compression =Math.round((top.compression||0)*100);
        s.risk        =Math.round(score);
        s.status      =label;
        s.density     =score>65?'HIGH':score>35?'MODERATE':'LOW';
        s.movement    =(top.avg_speed||0)>0.5?'→ EXIT':'↔ MIXED';
        s.exitBlockage=(top.compression||0)>0.7?'HIGH':(top.compression||0)>0.4?'MODERATE':'LOW';
    } else if (data.count!==undefined) {
        s.people=data.count; s.risk=Math.round(score); s.status=label;
        s.density=score>65?'HIGH':score>35?'MODERATE':'LOW';
    }

    if (label==='CRITICAL'&&!alertAcked) triggerCriticalAlert('C');
    else if (label!=='CRITICAL'&&critAlertZoneId==='C'&&alertAcked) {
        critAlertZoneId=null; alertAcked=false;
        document.getElementById('escPanel').style.display='none';
    }

    updateHeader(); updateIncidentsList();
    if (selectedZoneId) openZoneDetail(selectedZoneId);
}

function setConnStatus(s) {
    const dot=document.getElementById('connDot'), txt=document.getElementById('connTxt');
    const m={linking:{cls:'cdot linking',t:'CONNECTING...'},live:{cls:'cdot live',t:'LIVE'},offline:{cls:'cdot',t:'OFFLINE'},err:{cls:'cdot err',t:'ERROR'}};
    const v=m[s]||m.offline; dot.className=v.cls; txt.textContent=v.t;
}

// ── UTILITIES ─────────────────────────────────────────────────────────────
function timeStr() { return new Date().toTimeString().split(' ')[0]; }
function pad2(n)   { return String(n).padStart(2,'0'); }
function tickClock() { document.getElementById('mapClock').textContent=timeStr(); }

// ── INIT ─────────────────────────────────────────────────────────────────
window.addEventListener('load',()=>{
    resizeCanvas();
    mapLoop();
    setInterval(tickClock,1000);

    const sh=localStorage.getItem(STORE_HOST);
    const sc=localStorage.getItem(STORE_CODE);
    if (sh) document.getElementById('authUrl').value=sh;
    if (sc) document.getElementById('authCode').value=sc;

    // Start in proper standby — no fake data ever shown
    setOfflineUI();
    loadReplay('204');

    let rdeb;
    window.addEventListener('resize',()=>{ clearTimeout(rdeb); rdeb=setTimeout(resizeCanvas,80); });
});
