import asyncio
import base64
import json
import logging
import os
import random
import string
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import cv2
import numpy as np
from bson import ObjectId
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CrowdPulse")

app = FastAPI(title="CrowdPulse AI Backend")

# ── MongoDB Atlas Config ───────────────────────────────────────────────────────
MONGO_URI             = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB              = os.environ.get("MONGO_DB", "crowdpulse")
MONGO_TTL_SECONDS     = int(os.environ.get("MONGO_TTL_SECONDS", 7 * 24 * 3600))  # 7 days
MAX_RECORDS_PER_SESSION = int(os.environ.get("MAX_RECORDS", 200))  # rolling cap per session

# ── Groq LLM Config ────────────────────────────────────────────────────────────
GROQ_API_KEY          = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL            = "llama-3.1-8b-instant"
GROQ_URL              = "https://api.groq.com/openai/v1/chat/completions"

# LLM trigger intervals (seconds)
LLM_PERIODIC_INTERVAL = 30    # periodic call every 30s
LLM_BREACH_DEBOUNCE   = 5     # minimum gap between instant breach triggers

# ── Twilio Config ──────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "+12183797671")
TWILIO_TO_NUMBER   = os.environ.get("TWILIO_TO_NUMBER", "+919021174588")

mongo_client: AsyncIOMotorClient = None
db = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── YOLO model ────────────────────────────────────────────────────────────────
ML_NODE_URL = os.environ.get("ML_NODE_URL")

if ML_NODE_URL:
    logger.info(f"Relay Mode Enabled: Forwarding inference to {ML_NODE_URL}")
    model = None
else:
    logger.info("Local ML Mode Enabled: Loading YOLO model...")
    import torch
    _original_load = torch.load
    def _patched_load(*args, **kwargs):
        kwargs['weights_only'] = False
        return _original_load(*args, **kwargs)
    torch.load = _patched_load
    from ultralytics import YOLO
    model = YOLO("yolov8m.pt")
    logger.info("YOLO medium model loaded for high accuracy.")

# ── Session Store ─────────────────────────────────────────────────────────────
SESSION_TTL_SECONDS = 86400  # 24 hours

class Session:
    def __init__(self, code: str):
        self.code = code
        self.created_at = time.time()
        self.last_active = time.time()
        self.camera_connections: List[WebSocket] = []
        self.dashboard_connections: List[WebSocket] = []
        # ── LLM state ───────────────────────────────────────────────────────
        self.frame_buffer: List[dict] = []   # {count, status, timestamp} per frame
        self.last_llm_call: float = 0.0      # epoch time of last triggered LLM call
        self.last_status: str = "GREEN"      # track transitions for instant triggers
        self.llm_lock: asyncio.Lock = asyncio.Lock()

    def touch(self):
        self.last_active = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_active) > SESSION_TTL_SECONDS

sessions: Dict[str, Session] = {}


def generate_code(length: int = 6) -> str:
    """Generate a unique uppercase alphanumeric session code."""
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choices(chars, k=length))
        if code not in sessions:
            return code


def get_session(code: str) -> Optional[Session]:
    session = sessions.get(code.upper())
    if session and session.is_expired():
        del sessions[code.upper()]
        return None
    return session


# ── Background cleanup task ────────────────────────────────────────────────────
async def cleanup_expired_sessions():
    while True:
        await asyncio.sleep(3600)  # run every hour
        expired = [c for c, s in sessions.items() if s.is_expired()]
        for code in expired:
            del sessions[code]
            logger.info(f"Session {code} expired and removed.")


@app.on_event("startup")
async def startup():
    global mongo_client, db
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = mongo_client[MONGO_DB]
        # TTL index: MongoDB auto-deletes documents older than MONGO_TTL_SECONDS
        await db.frame_analytics.create_index(
            "timestamp", expireAfterSeconds=MONGO_TTL_SECONDS
        )
        # Compound index for fast per-session queries
        await db.frame_analytics.create_index(
            [("session_code", 1), ("timestamp", -1)]
        )
        # ── Org reports indexes ───────────────────────────────────────────────
        await db.org_reports.create_index(
            "timestamp", expireAfterSeconds=MONGO_TTL_SECONDS
        )
        await db.org_reports.create_index(
            [("session_code", 1), ("timestamp", -1)]
        )
        await db.org_reports.create_index([("read", 1)])
        logger.info(
            f"MongoDB Atlas connected. DB='{MONGO_DB}' TTL={MONGO_TTL_SECONDS}s "
            f"cap={MAX_RECORDS_PER_SESSION}"
        )
    except Exception as e:
        logger.error(f"MongoDB connection failed: {e}. Frames will NOT be persisted.")
    asyncio.create_task(cleanup_expired_sessions())


@app.on_event("shutdown")
async def shutdown():
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB connection closed.")


# ── MongoDB helpers ───────────────────────────────────────────────────────────
async def save_frame_to_mongo(session_code: str, count: int, status: str,
                               frame_ts: float, jpeg_bytes: bytes):
    """Fire-and-forget: persist one processed frame to MongoDB Atlas."""
    if db is None:
        return
    try:
        doc = {
            "session_code": session_code,
            "count":        count,
            "status":       status,
            "timestamp":    datetime.utcfromtimestamp(frame_ts),
            "jpeg":         jpeg_bytes,
        }
        await db.frame_analytics.insert_one(doc)
        logger.info(f"[{session_code}] [MONGO] Frame saved. count={count} status={status}")

        # ── Rolling window: keep only the latest MAX_RECORDS_PER_SESSION ──────
        total = await db.frame_analytics.count_documents({"session_code": session_code})
        if total > MAX_RECORDS_PER_SESSION:
            excess = total - MAX_RECORDS_PER_SESSION
            cursor = db.frame_analytics.find(
                {"session_code": session_code},
                sort=[("timestamp", 1)],
                projection={"_id": 1}
            ).limit(excess)
            oldest_docs = await cursor.to_list(excess)
            ids_to_delete = [d["_id"] for d in oldest_docs]
            result = await db.frame_analytics.delete_many({"_id": {"$in": ids_to_delete}})
            logger.info(
                f"[{session_code}] [MONGO] Rolling window trimmed {result.deleted_count} old frame(s)."
            )
    except Exception as e:
        logger.error(f"[{session_code}] [MONGO] Failed to save frame: {e}")


async def save_org_report(session_code: str, report_text: str, status: str, avg_count: float):
    """Save LLM-generated org status report to MongoDB org_reports collection."""
    if db is None:
        logger.warning(f"[{session_code}] [LLM] DB not available — org report discarded.")
        return
    try:
        doc = {
            "session_code": session_code,
            "report":       report_text,
            "status":       status,
            "avg_count":    round(avg_count, 1),
            "timestamp":    datetime.utcnow(),
            "read":         False,
        }
        result = await db.org_reports.insert_one(doc)
        logger.info(f"[{session_code}] [LLM] Org report saved. id={result.inserted_id}")

        # ── Start Twilio timer if status is RED ────
        if status == "RED":
            asyncio.create_task(alert_escalation_worker(result.inserted_id, report_text, session_code))

    except Exception as e:
        logger.error(f"[{session_code}] [LLM] Failed to save org report: {e}")


# ── Twilio Escalation ────────────────────────────────────────────────────────
async def trigger_twilio_call(report_text: str, session_code: str):
    """Makes a POST request to Twilio API to call the organization member."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json"
    
    # Twiml to speak the alert
    twiml = f"<Response><Say voice='Polly.Matthew'>Urgent Alert from CrowdPulse. Session {session_code} is experiencing high crowd density. {report_text} Please check the dashboard immediately.</Say></Response>"
    
    data = {
        "To": TWILIO_TO_NUMBER,
        "From": TWILIO_FROM_NUMBER,
        "Twiml": twiml
    }
    
    auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, data=data, auth=auth)
            resp.raise_for_status()
            logger.info(f"[{session_code}] [TWILIO] Successfully requested automated call!")
    except Exception as e:
        logger.error(f"[{session_code}] [TWILIO] Failed to trigger call: {e}")


async def alert_escalation_worker(report_id, report_text: str, session_code: str):
    """Waits 60 seconds. If the report is still unread, calls the member."""
    logger.info(f"[{session_code}] [ESCALATION] 60s timer started for RED report {report_id}.")
    await asyncio.sleep(60)

    if db is None:
        return
        
    try:
        doc = await db.org_reports.find_one({"_id": report_id})
        if doc and not doc.get("read", False):
            logger.warning(f"[{session_code}] [ESCALATION] Report {report_id} UNREAD after 60s! TRIGGERING TWILIO CALL.")
            await trigger_twilio_call(report_text, session_code)
        else:
            logger.info(f"[{session_code}] [ESCALATION] Report {report_id} was acknowledged. No call needed.")
    except Exception as e:
        logger.error(f"[{session_code}] [ESCALATION] Validation check failed: {e}")


# ── Groq LLM helpers ─────────────────────────────────────────────────────────
async def call_groq(system_prompt: str, user_content: str) -> str:
    """Make a single async call to Groq llama-3.1-8b-instant and return the response text."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        "max_tokens":  150,
        "temperature": 0.7,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(GROQ_URL, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


async def trigger_llm_calls(session: "Session", code: str, reason: str):
    """
    Dual-LLM trigger:
      LLM-1 → Operator crowd management instruction (broadcast as TTS to dashboards)
      LLM-2 → Org status report (saved to MongoDB org_reports)

    Uses a per-session asyncio.Lock to prevent race conditions when both the
    30-sec timer and an instant threshold breach fire at nearly the same time.
    """
    async with session.llm_lock:
        # Prune buffer to last 30 seconds
        cutoff = time.time() - 30
        window = [f for f in session.frame_buffer if f["timestamp"] >= cutoff]

        if not window:
            logger.info(f"[{code}] [LLM] Skipping trigger — buffer is empty.")
            return

        # Update timestamp BEFORE the awaits so re-entrant calls see it immediately
        session.last_llm_call = time.time()

        counts   = [f["count"]  for f in window]
        statuses = [f["status"] for f in window]
        avg_cnt  = sum(counts) / len(counts)
        max_cnt  = max(counts)
        min_cnt  = min(counts)

        # Dominant status = highest severity in the window
        if "RED" in statuses:
            dominant = "RED"
        elif "YELLOW" in statuses:
            dominant = "YELLOW"
        else:
            dominant = "GREEN"

        data_summary = (
            f"Monitoring window: {len(window)} frames over the last ~30 seconds. "
            f"Crowd count — average: {avg_cnt:.1f}, peak: {max_cnt}, minimum: {min_cnt}. "
            f"Current alert status: {dominant}. Trigger: {reason}."
        )

        logger.info(
            f"[{code}] [LLM] 🤖 Triggering dual LLM calls. "
            f"reason={reason} dominant={dominant} avg={avg_cnt:.1f} peak={max_cnt}"
        )

        try:
            # ── Fire both LLM calls in parallel ─────────────────────────────
            op_task = asyncio.create_task(call_groq(
                system_prompt=(
                    "You are a crowd safety AI for event operators. "
                    "Based on the crowd data, give 1-2 SHORT, actionable crowd management "
                    "instructions. This will be spoken aloud via text-to-speech. "
                    "Be calm, direct, and clear. No markdown. No bullet points."
                ),
                user_content=data_summary,
            ))
            org_task = asyncio.create_task(call_groq(
                system_prompt=(
                    "You are a crowd monitoring AI for an event management organization. "
                    "Based on the crowd data, write a SHORT factual status report (2-3 sentences max). "
                    "Be professional and concise. State the situation clearly. "
                    "No markdown. No instructions."
                ),
                user_content=data_summary,
            ))

            op_text, org_text = await asyncio.gather(op_task, org_task)

            logger.info(f"[{code}] [LLM] ✅ Operator instruction: {op_text[:100]}...")
            logger.info(f"[{code}] [LLM] ✅ Org report: {org_text[:100]}...")

            # ── Broadcast TTS instruction to all live dashboards ─────────────
            tts_payload = json.dumps({
                "type":      "tts_instruction",
                "text":      op_text,
                "status":    dominant,
                "avg_count": round(avg_cnt, 1),
                "reason":    reason,
                "timestamp": time.time(),
            })
            for dash_ws in session.dashboard_connections.copy():
                try:
                    await dash_ws.send_text(tts_payload)
                except Exception:
                    pass  # disconnected dashboard — will be cleaned up on next frame send

            # ── Persist org report to MongoDB (non-blocking) ─────────────────
            asyncio.create_task(save_org_report(code, org_text, dominant, avg_cnt))

        except Exception as e:
            logger.error(f"[{code}] [LLM] ❌ Groq call failed: {e}")


# ── REST Endpoints ─────────────────────────────────────────────────────────────
class SessionResponse(BaseModel):
    code: str
    expires_in_hours: int = 24


@app.post("/session/create", response_model=SessionResponse)
async def create_session():
    """Web dashboard calls this to get a fresh 6-char session code."""
    code = generate_code()
    sessions[code] = Session(code)
    logger.info(f"Session created: {code}")
    return SessionResponse(code=code)


@app.get("/session/{code}/exists")
async def session_exists(code: str):
    """Mobile app calls this to validate a code before connecting."""
    session = get_session(code.upper())
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    session.touch()
    return {"valid": True, "code": code.upper()}


@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(sessions)}


@app.delete("/session/{code}")
async def delete_session(code: str):
    """Explicitly terminate a session and drop all connected clients instantly."""
    code = code.upper()
    if code in sessions:
        session = sessions[code]
        for cam_ws in session.camera_connections:
            try:
                await cam_ws.close(code=4404, reason="Session terminated explicitly")
            except Exception:
                pass
        for dash_ws in session.dashboard_connections:
            try:
                await dash_ws.close(code=4404, reason="Session terminated explicitly")
            except Exception:
                pass
        del sessions[code]
        logger.info(f"Session {code} instantly terminated by user request.")
        return {"status": "deleted"}
    return {"status": "not_found"}


@app.get("/session/{code}/history")
async def get_session_history(code: str, limit: int = 50):
    """
    Returns the most recent `limit` analytics records for a session.
    JPEG binary is excluded — this is for stats/dashboard use only.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    code = code.upper()
    cursor = db.frame_analytics.find(
        {"session_code": code},
        sort=[("timestamp", -1)],
        projection={"_id": 0, "jpeg": 0}
    ).limit(limit)
    docs = await cursor.to_list(limit)
    for d in docs:
        if isinstance(d.get("timestamp"), datetime):
            d["timestamp"] = d["timestamp"].timestamp()
    return docs


@app.post("/process_inference")
async def process_inference_route(request: Request):
    """Endpoint for the cloud node to send frames to the local ML node."""
    if ML_NODE_URL:
        raise HTTPException(status_code=400, detail="This node is configured as a relay.")
    data = await request.body()
    result = await asyncio.to_thread(process_frame, data)
    result["jpeg_bytes"] = base64.b64encode(result["jpeg_bytes"]).decode("utf-8")
    result["heatmap_bytes"] = base64.b64encode(result["heatmap_bytes"]).decode("utf-8")
    return result


# ── Org Report Endpoints ───────────────────────────────────────────────────────
def _doc_to_json(doc: dict) -> dict:
    """Convert a MongoDB org_reports document to a JSON-safe dict."""
    doc["id"] = str(doc.pop("_id"))
    if isinstance(doc.get("timestamp"), datetime):
        doc["timestamp"] = doc["timestamp"].isoformat() + "Z"
    doc.pop("jpeg", None)
    return doc


@app.get("/org/reports")
async def get_org_reports(limit: int = 50, session_code: str = None):
    """Return most recent org reports, optionally filtered by session."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    query = {}
    if session_code:
        query["session_code"] = session_code.upper()
    cursor = db.org_reports.find(query, sort=[("timestamp", -1)]).limit(limit)
    docs = await cursor.to_list(limit)
    return [_doc_to_json(d) for d in docs]


@app.get("/org/reports/unread")
async def get_unread_org_reports():
    """Return all unread org reports (newest first)."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    cursor = db.org_reports.find({"read": False}, sort=[("timestamp", -1)]).limit(100)
    docs = await cursor.to_list(100)
    return [_doc_to_json(d) for d in docs]


@app.post("/org/reports/{report_id}/mark_read")
async def mark_report_read(report_id: str):
    """Mark a single org report as read."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        oid = ObjectId(report_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid report ID")
    result = await db.org_reports.update_one({"_id": oid}, {"$set": {"read": True}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Report not found")
    return {"status": "marked_read"}


@app.post("/org/reports/mark_all_read")
async def mark_all_reports_read():
    """Mark every unread org report as read."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    result = await db.org_reports.update_many({"read": False}, {"$set": {"read": True}})
    return {"status": "ok", "updated": result.modified_count}


# ── Recording / Playback Endpoints ─────────────────────────────────────────────
@app.get("/org/sessions")
async def get_org_sessions():
    """
    List all sessions that have stored frames in MongoDB, with their time range
    and frame count. Used by the org portal session picker.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    pipeline = [
        {"$group": {
            "_id":         "$session_code",
            "first_frame": {"$min": "$timestamp"},
            "last_frame":  {"$max": "$timestamp"},
            "frame_count": {"$sum": 1},
            "statuses":    {"$addToSet": "$status"},
        }},
        {"$sort": {"last_frame": -1}},
        {"$limit": 100},
    ]
    cursor = db.frame_analytics.aggregate(pipeline)
    docs = await cursor.to_list(100)
    result = []
    for d in docs:
        result.append({
            "session_code":   d["_id"],
            "first_frame_ts": d["first_frame"].timestamp() if isinstance(d["first_frame"], datetime) else None,
            "last_frame_ts":  d["last_frame"].timestamp()  if isinstance(d["last_frame"],  datetime) else None,
            "frame_count":    d["frame_count"],
            "statuses":       list(d["statuses"]),
        })
    return result


@app.get("/org/playback/frames")
async def get_playback_frames(
    session_code: str,
    start_ts: float = None,
    end_ts:   float = None,
):
    """
    Return ordered frame metadata (no JPEG) for the given session and optional
    time window. Max 500 frames to keep response fast.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")

    query: dict = {"session_code": session_code.upper()}
    if start_ts or end_ts:
        ts_filter: dict = {}
        if start_ts:
            ts_filter["$gte"] = datetime.utcfromtimestamp(start_ts)
        if end_ts:
            ts_filter["$lte"] = datetime.utcfromtimestamp(end_ts)
        query["timestamp"] = ts_filter

    cursor = db.frame_analytics.find(
        query,
        projection={"_id": 1, "timestamp": 1, "count": 1, "status": 1},
        sort=[("timestamp", 1)],
    ).limit(500)

    docs = await cursor.to_list(500)
    return [
        {
            "id":        str(d["_id"]),
            "timestamp": d["timestamp"].timestamp() if isinstance(d["timestamp"], datetime) else d["timestamp"],
            "count":     d.get("count",  0),
            "status":    d.get("status", "GREEN"),
        }
        for d in docs
    ]


@app.get("/org/playback/frame/{frame_id}")
async def get_playback_frame(frame_id: str):
    """
    Return raw JPEG bytes for a single stored frame.
    The org portal fetches these lazily during recording playback.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        oid = ObjectId(frame_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid frame ID")

    doc = await db.frame_analytics.find_one({"_id": oid}, projection={"jpeg": 1})
    if not doc or not doc.get("jpeg"):
        raise HTTPException(status_code=404, detail="Frame not found or has no image")

    return Response(content=bytes(doc["jpeg"]), media_type="image/jpeg")


# ── Frame processing ───────────────────────────────────────────────────────────
# PERF FIX: Pre-resize to 640x640 before YOLO inference.
INFERENCE_SIZE = 640

def paint_gaussian(heat, cx, cy, sigma):
    """Paint a radial Gaussian blob centered at (cx, cy)."""
    h, w = heat.shape
    x = np.arange(0, w, 1, np.float32)
    y = np.arange(0, h, 1, np.float32)[:, np.newaxis]
    heat += np.exp(-((x - cx)**2 + (y - cy)**2) / (2 * sigma**2))

def generate_heatmap_overlay(img, boxes):
    """Generate a density heatmap from bounding box centroids and blend it."""
    h, w = img.shape[:2]
    heat = np.zeros((h, w), dtype=np.float32)
    for box in boxes:
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
        cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
        sigma = max(int((x2 - x1) / 3), 12)   # spread scales with bbox size
        paint_gaussian(heat, cx, cy, sigma)
    
    if heat.max() == 0:
        return img   # no detections -> return plain frame

    heat = cv2.normalize(heat, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    colormap = cv2.applyColorMap(heat, cv2.COLORMAP_JET)
    return cv2.addWeighted(img, 0.55, colormap, 0.45, 0)

def process_frame(frame_bytes: bytes) -> Dict:
    np_arr = np.frombuffer(frame_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("Failed to decode image")

    small_img = cv2.resize(img, (INFERENCE_SIZE, INFERENCE_SIZE), interpolation=cv2.INTER_LINEAR)
    results = model(small_img, classes=0, verbose=False, imgsz=INFERENCE_SIZE)

    count = 0
    annotated_img = small_img.copy()

    if len(results) > 0:
        result = results[0]
        count = len(result.boxes)
        annotated_img = result.plot()

    # Generate heatmap version in parallel
    if len(results) > 0 and count > 0:
        heatmap_img = generate_heatmap_overlay(annotated_img.copy(), results[0].boxes)
    else:
        heatmap_img = annotated_img.copy()

    # ── Crowd density thresholds ──────────────────────────────────────────────
    # GREEN  : count < 5   (low density — safe)
    # YELLOW : 5 ≤ count ≤ 10  (moderate — monitor)
    # RED    : count > 10  (high density — alert)
    if count < 5:
        status = "GREEN"
    elif count <= 10:
        status = "YELLOW"
    else:
        status = "RED"

    _, encoded_img = cv2.imencode(".jpg", annotated_img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    _, encoded_heatmap = cv2.imencode(".jpg", heatmap_img, [cv2.IMWRITE_JPEG_QUALITY, 50])

    return {
        "status":        status,
        "count":         count,
        "jpeg_bytes":    encoded_img.tobytes(),
        "heatmap_bytes": encoded_heatmap.tobytes(),
        "timestamp":     time.time(),
    }


# ── WebSocket: Camera (Mobile App) ────────────────────────────────────────────
@app.websocket("/ws/camera/{code}")
async def websocket_camera(websocket: WebSocket, code: str):
    code = code.upper()
    session = get_session(code)
    if session is None:
        sessions[code] = Session(code)
        session = sessions[code]
        logger.info(f"[{code}] Session auto-created from camera device.")

    await websocket.accept()
    session.camera_connections.append(websocket)
    session.touch()
    logger.info(f"[{code}] Camera connected. Total cams: {len(session.camera_connections)}")

    processing_state = {
        "latest_frame":      None,
        "latest_frame_time": None,
        "running":           True,
    }

    async def receive_worker():
        """Continuously receives frames from the camera, always keeping only the newest."""
        try:
            while processing_state["running"]:
                data = await websocket.receive_bytes()
                recv_time = time.time()
                session.touch()
                # Always overwrite — old unprocessed frames are discarded (no buffer buildup)
                processing_state["latest_frame"]      = data
                processing_state["latest_frame_time"] = recv_time
                logger.info(f"[{code}] [RECEIVER] Frame arrived from Android.")
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"[{code}] Receive worker error: {e}")
        finally:
            processing_state["running"] = False

    async def process_worker():
        """
        Processes frames as fast as YOLO allows and forwards to dashboards.
        Manages 30-sec LLM periodic cycle + instant threshold breach triggers.
        """
        active_sends    = set()
        last_sent_payload = None
        last_sent_jpeg    = None

        try:
            while processing_state["running"]:
                data = processing_state["latest_frame"]

                if data is None:
                    await asyncio.sleep(0.01)
                    # ── 30-sec check even when camera is idle (no new frames) ──
                    if (time.time() - session.last_llm_call) >= LLM_PERIODIC_INTERVAL:
                        if session.frame_buffer:
                            asyncio.create_task(
                                trigger_llm_calls(session, code, "30s_periodic")
                            )
                    continue

                # Claim and clear the frame atomically
                processing_state["latest_frame"] = None
                frame_born_time = processing_state.get("latest_frame_time") or time.time()
                queue_wait_ms   = int((time.time() - frame_born_time) * 1000)

                try:
                    logger.info(
                        f"[{code}] [PROCESS] Yanked frame from queue. "
                        f"Waited {queue_wait_ms}ms. Starting YOLO inference..."
                    )
                    inf_start = time.time()

                    if ML_NODE_URL:
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            resp = await client.post(
                                f"{ML_NODE_URL.rstrip('/')}/process_inference",
                                content=data
                            )
                            resp.raise_for_status()
                            result = resp.json()
                            result["jpeg_bytes"] = base64.b64decode(result["jpeg_bytes"])
                            result["heatmap_bytes"] = base64.b64decode(result["heatmap_bytes"])
                    else:
                        result = await asyncio.to_thread(process_frame, data)

                    inf_time_ms = int((time.time() - inf_start) * 1000)
                    logger.info(
                        f"[{code}] [PROCESS] YOLO done in {inf_time_ms}ms. "
                        f"count={result['count']} status={result['status']}"
                    )

                    # ── Append to 30-sec sliding window buffer ─────────────────
                    session.frame_buffer.append({
                        "count":     result["count"],
                        "status":    result["status"],
                        "timestamp": result.get("timestamp", time.time()),
                    })
                    # Prune entries older than 30 seconds
                    cutoff = time.time() - 30
                    session.frame_buffer = [
                        f for f in session.frame_buffer if f["timestamp"] >= cutoff
                    ]

                    last_sent_jpeg    = result["jpeg_bytes"]
                    last_sent_heatmap = result.get("heatmap_bytes")
                    del result["jpeg_bytes"]
                    if "heatmap_bytes" in result:
                        del result["heatmap_bytes"]
                    last_sent_payload = json.dumps(result)

                    # ── LLM Trigger Decision ───────────────────────────────────
                    current_status = result["status"]
                    time_since_llm = time.time() - session.last_llm_call

                    # Instant breach trigger: crowd goes non-GREEN (with debounce)
                    if (
                        current_status != "GREEN"
                        and time_since_llm >= LLM_BREACH_DEBOUNCE
                        and (session.last_status == "GREEN" or current_status == "RED")
                    ):
                        logger.info(
                            f"[{code}] [LLM] ⚡ Instant breach trigger! "
                            f"status={current_status} prev={session.last_status}"
                        )
                        asyncio.create_task(
                            trigger_llm_calls(session, code, f"breach_{current_status}")
                        )
                    # 30-sec periodic trigger
                    elif time_since_llm >= LLM_PERIODIC_INTERVAL:
                        asyncio.create_task(
                            trigger_llm_calls(session, code, "30s_periodic")
                        )

                    session.last_status = current_status

                except Exception as e:
                    logger.error(f"[{code}] Inference error: {e}")
                    continue

                # ── Forward annotated frame + metadata to all dashboards ───────
                if last_sent_jpeg is not None:
                    async def send_to_dash(ws, payload, jpeg, heatmap):
                        try:
                            if payload is not None:
                                await ws.send_text(payload)
                            await ws.send_bytes(jpeg)
                            if heatmap is not None:
                                await ws.send_bytes(b'\xff' + heatmap)
                        except Exception:
                            if ws in session.dashboard_connections:
                                session.dashboard_connections.remove(ws)
                        finally:
                            active_sends.discard(ws)

                    for dash_ws in session.dashboard_connections.copy():
                        if dash_ws in active_sends:
                            logger.info(
                                f"[{code}] [NETWORK] 🚨 DROPPED frame for dashboard "
                                "(still sending previous frame — downlink too slow)."
                            )
                            continue

                        active_sends.add(dash_ws)
                        logger.info(f"[{code}] [NETWORK] 🚀 Dispatching new frame to dashboard.")
                        asyncio.create_task(send_to_dash(dash_ws, last_sent_payload, last_sent_jpeg, last_sent_heatmap))

                    # ── Persist to MongoDB Atlas (non-blocking) ────────────────
                    asyncio.create_task(
                        save_frame_to_mongo(
                            session_code=code,
                            count=result.get("count", 0),
                            status=result.get("status", "UNKNOWN"),
                            frame_ts=result.get("timestamp", time.time()),
                            jpeg_bytes=last_sent_jpeg,
                        )
                    )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[{code}] Process worker error: {e}")

    receive_task = asyncio.create_task(receive_worker())
    process_task = asyncio.create_task(process_worker())

    await receive_task
    processing_state["running"] = False
    process_task.cancel()
    try:
        await process_task
    except asyncio.CancelledError:
        pass

    if websocket in session.camera_connections:
        session.camera_connections.remove(websocket)
    logger.info(f"[{code}] Camera disconnected.")


# ── WebSocket: Dashboard (Web Browser) ────────────────────────────────────────
@app.websocket("/ws/dashboard/{code}")
async def websocket_dashboard(websocket: WebSocket, code: str):
    code = code.upper()
    session = get_session(code)
    if session is None:
        sessions[code] = Session(code)
        session = sessions[code]
        logger.info(f"[{code}] Session auto-created from dashboard.")

    await websocket.accept()
    session.dashboard_connections.append(websocket)
    session.touch()
    logger.info(f"[{code}] Dashboard connected. Total dashboards: {len(session.dashboard_connections)}")

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in session.dashboard_connections:
            session.dashboard_connections.remove(websocket)
        logger.info(f"[{code}] Dashboard disconnected.")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)