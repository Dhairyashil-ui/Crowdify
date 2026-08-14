import asyncio
import base64
import json
import logging
import os

# Load .env file (if present) before anything else reads os.environ
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # dotenv optional — env vars can be set externally

import random
import string
import math
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from bson import ObjectId
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from wallet import wallet_router, set_wallet_db, consume_credits, get_demo_org, get_org_features_internal, FEATURE_PRICING, record_x402_usage
from crowd_api import crowd_api_router
import x402_config

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

# ── Razorpay Config (consumed by wallet.py via env vars directly) ───────────────
# Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in your environment.
# Use Razorpay Test Mode keys for the hackathon — no real money is charged.

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

# ── Wallet router (organisations + Croudify Credits + Razorpay) ────────────────
# Mounted beside the pipeline — zero coupling to frame processing.
app.include_router(wallet_router)

# ── Crowd Intelligence API (x402-gated) ───────────────────────────────────────
# POST /api/v1/crowd/predict — programmatic crowd intelligence for AI agents.
# The x402 middleware is applied at startup() after the DB is ready.
app.include_router(crowd_api_router)

# ── YOLO model + ByteTrack ────────────────────────────────────────────────────
ML_NODE_URL = os.environ.get("ML_NODE_URL")

if ML_NODE_URL:
    logger.info(f"Relay Mode Enabled: Forwarding inference to {ML_NODE_URL}")
    model = None
    BYTETracker = None
else:
    logger.info("Local ML Mode Enabled: Loading YOLO model...")
    import torch
    _original_load = torch.load
    def _patched_load(*args, **kwargs):
        kwargs['weights_only'] = False
        return _original_load(*args, **kwargs)
    torch.load = _patched_load
    from ultralytics import YOLO
    from ultralytics.trackers.byte_tracker import BYTETracker
    from ultralytics.utils import IterableSimpleNamespace
    model = YOLO("yolov8m.pt")
    logger.info("YOLO medium model loaded for high accuracy.")
    logger.info("ByteTrack tracker loaded.")

# ── ByteTrack default config ───────────────────────────────────────────────────
def make_tracker_args():
    """Return a minimal IterableSimpleNamespace config for BYTETracker."""
    return IterableSimpleNamespace(
        tracker_type="bytetrack",
        track_high_thresh=0.5,    # high-confidence detection threshold
        track_low_thresh=0.1,     # low-confidence detection threshold (2nd assoc.)
        new_track_thresh=0.6,     # minimum conf to initialise a new track
        track_buffer=30,          # frames to keep a lost track alive
        match_thresh=0.8,         # IoU threshold for Hungarian matching
        fuse_score=True,          # multiply IoU cost by confidence score
    )

# ── Session Store ─────────────────────────────────────────────────────────────
SESSION_TTL_SECONDS = 86400  # 24 hours

# ── Temporal Memory constants ─────────────────────────────────────────────────
MEMORY_WINDOW_SECONDS = 5.0    # rolling history window kept per person (seconds)
MEMORY_MAX_SAMPLES    = 120    # hard deque cap (120 ≈ 24fps × 5s — never exceeded)
TRACK_STALE_SECONDS   = 5.0   # seconds of absence before evicting a track ID
MIN_MOVE_PIXELS       = 2.0   # minimum displacement (px) to update direction
PREDICT_HORIZON_S     = 0.5   # seconds ahead for linear position prediction


class PersonMemory:
    """
    Temporal motion memory for a single tracked person.

    Maintains a rolling window (MEMORY_WINDOW_SECONDS) of observations.
    Each call to update() appends a new (cx, cy, timestamp) and computes:
      - speed        (Euclidean px/s between consecutive positions)
      - direction    (degrees, 0=East / right, 90=South / down in screen coords)
      - acceleration (Δspeed / Δtime, px/s²)

    The analytics() method summarises the current window into a compact dict
    suitable for the WebSocket broadcast payload.
    The to_dict()   method returns the full history for debugging / REST APIs.
    """

    __slots__ = (
        "timestamps", "positions", "speeds", "directions", "accelerations"
    )

    def __init__(self):
        self.timestamps:    deque = deque(maxlen=MEMORY_MAX_SAMPLES)  # float epoch
        self.positions:     deque = deque(maxlen=MEMORY_MAX_SAMPLES)  # (cx, cy)
        self.speeds:        deque = deque(maxlen=MEMORY_MAX_SAMPLES)  # px/s
        self.directions:    deque = deque(maxlen=MEMORY_MAX_SAMPLES)  # degrees 0-360
        self.accelerations: deque = deque(maxlen=MEMORY_MAX_SAMPLES)  # px/s²

    # ── public API ────────────────────────────────────────────────────────────

    def update(self, cx: float, cy: float, ts: float) -> None:
        """Append a new observation and derive speed / direction / acceleration."""
        if self.timestamps:
            prev_cx, prev_cy = self.positions[-1]
            prev_ts          = self.timestamps[-1]
            dt = ts - prev_ts

            if dt > 1e-6:   # guard against duplicate timestamps
                dx   = cx - prev_cx
                dy   = cy - prev_cy
                dist = math.hypot(dx, dy)
                spd  = dist / dt

                # Direction — only update if the person actually moved
                if dist >= MIN_MOVE_PIXELS:
                    # atan2 in screen coords: right=0°, down=90°, left=180°, up=270°
                    direction = math.degrees(math.atan2(dy, dx)) % 360.0
                else:
                    direction = float(self.directions[-1]) if self.directions else 0.0

                # Acceleration (signed — positive = speeding up)
                if self.speeds:
                    accel = (spd - self.speeds[-1]) / dt
                else:
                    accel = 0.0

                self.speeds.append(round(spd,   2))
                self.directions.append(round(direction, 1))
                self.accelerations.append(round(accel, 2))

        self.positions.append((round(cx, 1), round(cy, 1)))
        self.timestamps.append(ts)

    def prune(self, cutoff_ts: float) -> None:
        """Drop all entries older than cutoff_ts from the left of each deque."""
        while self.timestamps and self.timestamps[0] < cutoff_ts:
            self.timestamps.popleft()
            self.positions.popleft()
            # speeds / directions / accelerations have one fewer entry than positions
            # (no derivative on the first point), so prune conservatively
            if len(self.speeds) >= len(self.timestamps):
                self.speeds.popleft()
            if len(self.directions) >= len(self.timestamps):
                self.directions.popleft()
            if len(self.accelerations) >= len(self.timestamps):
                self.accelerations.popleft()

    def analytics(self) -> dict:
        """
        Compute a compact summary over the current window.

        Returns
        -------
        dict with keys:
          speed         – average of the last 3 speed readings (px/s)
          direction     – most recent heading (degrees)
          acceleration  – most recent acceleration (px/s²)
          consistency   – circular R-value ∈ [0,1]; 1 = perfectly straight line
          predicted_cx  – linear extrapolation PREDICT_HORIZON_S seconds ahead
          predicted_cy  – same, y-axis
        """
        n = len(self.positions)

        # Defaults for tracks with only one observation
        cx0, cy0 = self.positions[-1] if n else (0.0, 0.0)
        result = {
            "speed":        0.0,
            "direction":    round(float(self.directions[-1]), 1) if self.directions else 0.0,
            "acceleration": 0.0,
            "consistency":  1.0,
            "predicted_cx": cx0,
            "predicted_cy": cy0,
        }

        if n < 2 or not self.speeds:
            return result

        # ── Average speed (last 3 readings for noise resistance) ─────────────
        tail_spd = list(self.speeds)[-3:]
        result["speed"] = round(sum(tail_spd) / len(tail_spd), 1)

        # ── Current direction ────────────────────────────────────────────────
        result["direction"] = round(float(self.directions[-1]), 1)

        # ── Current acceleration ─────────────────────────────────────────────
        result["acceleration"] = round(float(self.accelerations[-1]), 1)

        # ── Movement consistency: mean resultant length (circular statistics) ─
        # R close to 1.0 → all headings agree (straight-line motion)
        # R close to 0.0 → random / erratic motion
        if len(self.directions) >= 2:
            dirs_rad = [math.radians(d) for d in self.directions]
            sin_mean = sum(math.sin(r) for r in dirs_rad) / len(dirs_rad)
            cos_mean = sum(math.cos(r) for r in dirs_rad) / len(dirs_rad)
            result["consistency"] = round(math.hypot(sin_mean, cos_mean), 3)

        # ── Linear position prediction ───────────────────────────────────────
        if len(self.positions) >= 2 and len(self.timestamps) >= 2:
            x1, y1 = self.positions[-1]
            x0, y0 = self.positions[-2]
            t1 = self.timestamps[-1]
            t0 = self.timestamps[-2]
            dt = t1 - t0
            if dt > 1e-6:
                vx = (x1 - x0) / dt
                vy = (y1 - y0) / dt
                result["predicted_cx"] = round(x1 + vx * PREDICT_HORIZON_S, 1)
                result["predicted_cy"] = round(y1 + vy * PREDICT_HORIZON_S, 1)

        return result

    def to_dict(self, track_id: int) -> dict:
        """Serialise full temporal memory — for REST APIs / debug endpoints."""
        return {
            "id":            track_id,
            "positions":     list(self.positions),
            "speeds":        list(self.speeds),
            "directions":    list(self.directions),
            "accelerations": list(self.accelerations),
            "timestamps":    list(self.timestamps),
        }


# ── Scene Prediction Engine ──────────────────────────────────────────────────
# Builds a 12-element feature vector every second from the live analytics data
# already computed by PersonMemory and compute_zone_analytics.  No ML model
# training: risk is a weighted linear combination of normalised features,
# smoothed with an EMA, and the trend is a linear regression slope over the
# most-recent 10 samples.
#
# Feature vector layout (indices match FEATURE_WEIGHTS below):
#   0  density            – people / frame area,  normalised to [0,1] at 30 p
#   1  density_change     – Δdensity vs. previous tick (signed)
#   2  average_speed      – mean PersonMemory speed,  normalised at 200 px/s
#   3  speed_change       – Δavg_speed vs. previous tick (signed)
#   4  direction_variance – mean zone direction_variance [0,1]  (1 = chaotic)
#   5  flow_magnitude     – global optical-flow mean,  normalised at 5 px/fr
#   6  flow_convergence   – fraction of zones at HIGH convergence [0,1]
#   7  compression        – fraction of zones with INCREASING compression [0,1]
#   8  compression_change – Δcompression vs. previous tick (signed)
#   9  exit_occupancy     – mean exit-zone occupancy pct,  normalised to [0,1]
#  10  exit_flow          – Δexit_occupancy vs. previous tick (signed)
#  11  movement_intensity – fraction of zones at HIGH intensity [0,1]

_FEATURE_NAMES = [
    "density", "density_change", "average_speed", "speed_change",
    "direction_variance", "flow_magnitude", "flow_convergence",
    "compression", "compression_change",
    "exit_occupancy", "exit_flow", "movement_intensity",
]

# Weights must sum to 1.0  (verified: 0.25+0.10+0.10+0.05+0.05+0.05+0.05+0.15+0.05+0.05+0.03+0.07 = 1.00)
_FEATURE_WEIGHTS = [
    0.25,   # density
    0.10,   # density_change
    0.10,   # average_speed
    0.05,   # speed_change
    0.05,   # direction_variance
    0.05,   # flow_magnitude
    0.05,   # flow_convergence
    0.15,   # compression
    0.05,   # compression_change
    0.05,   # exit_occupancy
    0.03,   # exit_flow
    0.07,   # movement_intensity
]

# Normalisation caps (values at or above cap map to 1.0)
_DENSITY_CAP        = 30.0   # total people in frame
_SPEED_CAP          = 200.0  # px/s
_FLOW_MAG_CAP       = 5.0    # px/frame (Farneback mean magnitude)
_DELTA_DENSITY_CAP  = 10.0   # change in people count per tick
_DELTA_SPEED_CAP    = 80.0   # px/s change per tick
_DELTA_COMP_CAP     = 0.5    # fraction-of-zones change per tick
_DELTA_EXIT_CAP     = 0.30   # fraction change in exit occupancy per tick

# EMA smoothing factor (0 = no smoothing, 1 = ignore new data)
_EMA_ALPHA = 0.35

# Trend: linear regression slope thresholds (risk-score units per second)
_TREND_RISE_THRESHOLD = 1.5
_TREND_FALL_THRESHOLD = -1.5
_TREND_WINDOW         = 10    # number of recent ticks used for slope

# ── Configurable risk bands ──────────────────────────────────────────────────
# These thresholds map the 0–100 risk score to three human-readable alert
# levels.  They are intentionally named "configurable" because they should be
# calibrated against your specific venue / crowd type.  Do NOT treat these
# numbers as scientifically validated safety thresholds for production use.
RISK_BAND_CRITICAL = int(os.environ.get("RISK_BAND_CRITICAL", 70))  # 70–100 → CRITICAL
RISK_BAND_WARNING  = int(os.environ.get("RISK_BAND_WARNING",  40))  # 40–70  → WARNING
#                                                                      0–40   → SAFE

_RISK_LABELS = [
    (RISK_BAND_CRITICAL, "CRITICAL"),
    (RISK_BAND_WARNING,  "WARNING"),
    (0,                  "SAFE"),
]


def _clamp01(v: float) -> float:
    """Clamp a value to [0, 1]."""
    return max(0.0, min(1.0, v))


def _norm(value: float, cap: float) -> float:
    """Linearly normalise *value* to [0,1] using *cap* as the upper bound."""
    if cap <= 0:
        return 0.0
    return _clamp01(value / cap)


def _norm_signed(value: float, cap: float) -> float:
    """
    Map a signed delta into [0,1] where 0.5 = no change, 1.0 = full increase,
    0.0 = full decrease.  Only the positive (increasing) half contributes to
    risk, so values < 0 collapse to 0.0 and values > 0 scale to (0.5, 1.0].
    This ensures that *reductions* in a metric never inflate the risk score.
    """
    if value <= 0:
        return 0.0
    return _clamp01(value / cap)


def _risk_label(score: float) -> str:
    for threshold, label in _RISK_LABELS:
        if score >= threshold:
            return label
    return "SAFE"


def _linear_slope(xs: List[float], ys: List[float]) -> float:
    """
    Return the slope of the OLS regression line through (xs, ys).
    Returns 0.0 if fewer than 2 points.
    """
    n = len(xs)
    if n < 2:
        return 0.0
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs)
    return num / den if den > 1e-9 else 0.0


class ScenePredictionEngine:
    """
    Per-session prediction engine.

    Call ``tick()`` once per processed frame (or at most once per second;
    the engine itself rate-limits internally).  It returns a compact dict
    that is injected into the WebSocket broadcast payload under the key
    ``"prediction"``.

    Attributes
    ----------
    _last_tick_ts     : float   – Unix time of the last accepted tick
    _prev_fv          : list    – feature vector from the previous tick
    _smoothed_risk    : float   – current EMA-smoothed risk score
    _history          : deque   – ring buffer of (timestamp, risk_score) pairs
    """

    TICK_INTERVAL = 1.0   # minimum seconds between ticks

    __slots__ = ("_last_tick_ts", "_prev_fv", "_smoothed_risk", "_history")

    def __init__(self):
        self._last_tick_ts:  float          = 0.0
        self._prev_fv:       Optional[List[float]] = None
        self._smoothed_risk: float          = 0.0
        self._history:       deque          = deque(maxlen=60)  # 60s at 1 Hz

    # ── Public API ────────────────────────────────────────────────────────────

    def tick(
        self,
        enriched_tracks: List[dict],
        zones: List[dict],
        global_flow_mag: float,
        now: float,
    ) -> dict:
        """
        Build the feature vector, compute the risk score, update history.

        Parameters
        ----------
        enriched_tracks : per-person dicts with speed, direction, …
        zones           : zone analytics dicts from compute_zone_analytics()
        global_flow_mag : global optical-flow mean magnitude (px/frame)
        now             : current Unix timestamp

        Returns
        -------
        dict with keys: feature_vector, feature_names, risk_score,
                        risk_label, trend, smoothed_risk, history
        """
        # Rate-limit: skip if called more than once per TICK_INTERVAL
        if now - self._last_tick_ts < self.TICK_INTERVAL:
            return self._current_snapshot(now)

        self._last_tick_ts = now

        # ── 1. Extract raw scene metrics ─────────────────────────────────────
        n_people      = len(enriched_tracks)
        speeds        = [t.get("speed", 0.0) for t in enriched_tracks
                         if t.get("speed") is not None]
        avg_speed     = sum(speeds) / len(speeds) if speeds else 0.0

        # Zone-level aggregates
        dir_variances: List[float] = []
        flow_conv_high: int = 0
        comp_increasing: int = 0
        intensity_high: int = 0
        exit_occupancies: List[float] = []
        n_zones = max(len(zones), 1)

        for z in zones:
            dv = z.get("direction_variance")
            if dv is not None:
                dir_variances.append(float(dv))

            if z.get("flow_convergence") == "HIGH":
                flow_conv_high += 1

            if z.get("compression") == "INCREASING":
                comp_increasing += 1

            if z.get("movement_intensity") == "HIGH":
                intensity_high += 1

            eo = z.get("exit_occupancy")
            if z.get("is_exit") and eo is not None:
                exit_occupancies.append(float(eo))

        avg_dir_variance   = sum(dir_variances) / len(dir_variances) if dir_variances else 0.0
        frac_conv_high     = flow_conv_high  / n_zones
        frac_comp_incr     = comp_increasing / n_zones
        frac_intens_high   = intensity_high  / n_zones
        avg_exit_occ       = (sum(exit_occupancies) / len(exit_occupancies)
                              if exit_occupancies else 0.0)   # 0–100

        # ── 2. Normalise to [0, 1] ────────────────────────────────────────────
        density_norm     = _norm(float(n_people),   _DENSITY_CAP)
        speed_norm       = _norm(avg_speed,          _SPEED_CAP)
        flow_mag_norm    = _norm(global_flow_mag,    _FLOW_MAG_CAP)
        dir_var_norm     = _clamp01(avg_dir_variance)           # already [0,1]
        conv_norm        = _clamp01(frac_conv_high)
        comp_norm        = _clamp01(frac_comp_incr)
        exit_norm        = _norm(avg_exit_occ, 100.0)           # pct → [0,1]
        intens_norm      = _clamp01(frac_intens_high)

        # ── 3. Compute Δ features vs. previous tick ───────────────────────────
        prev = self._prev_fv
        if prev is not None:
            d_density  = _norm_signed(density_norm  - prev[0], _norm(_DELTA_DENSITY_CAP, _DENSITY_CAP))
            d_speed    = _norm_signed(speed_norm    - prev[2], _norm(_DELTA_SPEED_CAP,   _SPEED_CAP))
            d_comp     = _norm_signed(comp_norm     - prev[7], _norm(_DELTA_COMP_CAP,    1.0))
            d_exit     = _norm_signed(exit_norm     - prev[9], _norm(_DELTA_EXIT_CAP,    1.0))
        else:
            d_density = d_speed = d_comp = d_exit = 0.0

        # ── 4. Assemble feature vector ────────────────────────────────────────
        fv = [
            round(density_norm,   4),   # 0  density
            round(d_density,      4),   # 1  density_change
            round(speed_norm,     4),   # 2  average_speed
            round(d_speed,        4),   # 3  speed_change
            round(dir_var_norm,   4),   # 4  direction_variance
            round(flow_mag_norm,  4),   # 5  flow_magnitude
            round(conv_norm,      4),   # 6  flow_convergence
            round(comp_norm,      4),   # 7  compression
            round(d_comp,         4),   # 8  compression_change
            round(exit_norm,      4),   # 9  exit_occupancy
            round(d_exit,         4),   # 10 exit_flow
            round(intens_norm,    4),   # 11 movement_intensity
        ]
        self._prev_fv = fv

        # ── 5. Weighted linear risk score ─────────────────────────────────────
        raw_risk = sum(w * f for w, f in zip(_FEATURE_WEIGHTS, fv)) * 100.0
        raw_risk = _clamp01(raw_risk / 100.0) * 100.0   # ensure 0–100

        # EMA smoothing
        self._smoothed_risk = (
            _EMA_ALPHA * raw_risk
            + (1.0 - _EMA_ALPHA) * self._smoothed_risk
        )
        smoothed = round(self._smoothed_risk, 2)

        # ── 6. Update history ─────────────────────────────────────────────────
        self._history.append((now, smoothed))

        # ── 7. Trend: linear regression slope over last _TREND_WINDOW ticks ──
        trend = self._compute_trend()

        logger.info(
            f"[PREDICT] risk={smoothed:.1f} ({_risk_label(smoothed)}) "
            f"trend={trend}  "
            f"density={density_norm:.2f} speed={speed_norm:.2f} "
            f"comp={comp_norm:.2f} exit={exit_norm:.2f}"
        )

        return {
            "feature_vector":  fv,
            "feature_names":   _FEATURE_NAMES,
            "risk_score":      smoothed,
            "risk_label":      _risk_label(smoothed),
            "trend":           trend,
            "history":         list(self._history),   # [(ts, score), …]
        }

    def current_state(self) -> dict:
        """Return the most-recent prediction snapshot without advancing the tick."""
        score = round(self._smoothed_risk, 2)
        return {
            "feature_vector":  self._prev_fv or [0.0] * 12,
            "feature_names":   _FEATURE_NAMES,
            "risk_score":      score,
            "risk_label":      _risk_label(score),
            "trend":           self._compute_trend(),
            "history":         list(self._history),
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _compute_trend(self) -> str:
        window = list(self._history)[-_TREND_WINDOW:]
        if len(window) < 2:
            return "STABLE"
        xs = [entry[0] for entry in window]
        ys = [entry[1] for entry in window]
        slope = _linear_slope(xs, ys)
        if slope >= _TREND_RISE_THRESHOLD:
            return "RISING"
        if slope <= _TREND_FALL_THRESHOLD:
            return "FALLING"
        return "STABLE"

    def _current_snapshot(self, now: float) -> dict:
        """Return latest values when the tick rate-limit prevents a full update."""
        return self.current_state()


# ── Zone Analytics Engine ─────────────────────────────────────────────────────
# The inference frame (INFERENCE_SIZE × INFERENCE_SIZE) is divided into a
# ZONE_GRID_ROWS × ZONE_GRID_COLS grid.  Each cell is named A, B, C … row-major.
#
#   A | B | C
#   ---------
#   D | E | F
#   ---------
#   G | H | I   (for the default 3×3 grid)
#
# Outer-ring cells are flagged as exit zones (they include frame edges).

ZONE_GRID_ROWS        = 3      # rows in the zone grid
ZONE_GRID_COLS        = 3      # columns
ZONE_HISTORY_FRAMES   = 10     # frames of count history for compression detection

# People per 10k px² thresholds for density label
_DENSITY_LOW    = 0.5
_DENSITY_MEDIUM = 1.0
_DENSITY_HIGH   = 2.0

# Average nearest-neighbour distance thresholds (px) for visual compression
_COMPRESSION_CRITICAL = 20.0
_COMPRESSION_HIGH     = 40.0

# Intensity bracket thresholds (avg_speed × people, arbitrary units)
_INTENSITY_LOW    = 50
_INTENSITY_MEDIUM = 200


def _direction_label(deg: float) -> str:
    """Map a heading in degrees to an 8-directional Unicode arrow."""
    # Normalise to [0, 360)
    deg = deg % 360
    arrows = ["→", "↘", "↓", "↙", "←", "↖", "↑", "↗"]
    idx = int((deg + 22.5) / 45) % 8
    return arrows[idx]


def _circular_mean_and_R(degrees: List[float]):
    """
    Return (mean_direction_deg, R) where R is the mean resultant length [0,1].
    R≈1 → tight consensus direction; R≈0 → uniform spread.
    """
    if not degrees:
        return 0.0, 0.0
    rads     = [math.radians(d) for d in degrees]
    sin_mean = sum(math.sin(r) for r in rads) / len(rads)
    cos_mean = sum(math.cos(r) for r in rads) / len(rads)
    R        = math.hypot(sin_mean, cos_mean)
    mean_deg = math.degrees(math.atan2(sin_mean, cos_mean)) % 360
    return round(mean_deg, 1), round(R, 3)


def _population_variance(values: List[float]) -> float:
    """Return population variance of a numeric list.  0.0 if fewer than 2 values."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return round(sum((v - mean) ** 2 for v in values) / n, 2)


def _nearest_neighbour_distance(points: List[Tuple[float, float]]) -> Optional[float]:
    """Calculate the average Euclidean distance to the nearest neighbour for a set of points."""
    if len(points) < 2:
        return None
    total_min_dist = 0.0
    for i, p1 in enumerate(points):
        min_dist = float('inf')
        for j, p2 in enumerate(points):
            if i == j:
                continue
            dist = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
            if dist < min_dist:
                min_dist = dist
        total_min_dist += min_dist
    return round(total_min_dist / len(points), 2)


def compute_zone_analytics(
    enriched_tracks: List[dict],
    person_memory:   Dict[int, "PersonMemory"],
    zone_count_history: Dict[str, deque],
    img_size: int = 640,
    custom_zones: Optional[List[dict]] = None,
) -> List[dict]:
    """
    Compute crowd-level behavioural metrics for every zone.

    If *custom_zones* is provided (list of ZoneDefinition-serialised dicts),
    uses those named zones.  Otherwise falls back to the default ZONE_GRID_ROWS
    × ZONE_GRID_COLS mechanical grid.

    Parameters
    ----------
    enriched_tracks  : per-person dicts with speed, direction, cx, cy, …
    person_memory    : session memory store
    zone_count_history : Dict[zone_id → deque[int]] for compression detection
    img_size         : square inference frame size in pixels
    custom_zones     : optional list of ZoneDefinition dicts (percentage-based)
    """
    zones_out: List[dict] = []

    if custom_zones:
        # ── Named zones path ──────────────────────────────────────────────────
        zone_people: Dict[str, List[dict]] = {zd["zone_id"]: [] for zd in custom_zones}
        zone_meta:   Dict[str, dict]       = {}

        for zd in custom_zones:
            zid   = zd["zone_id"]
            x1_px = int(zd["x1_pct"] * img_size)
            y1_px = int(zd["y1_pct"] * img_size)
            x2_px = int(zd["x2_pct"] * img_size)
            y2_px = int(zd["y2_pct"] * img_size)
            zone_meta[zid] = {
                "label":    zd["label"],
                "type":     zd.get("type", "custom"),
                "bounds":   {"x1": x1_px, "y1": y1_px, "x2": x2_px, "y2": y2_px},
                "is_exit":  zd.get("type") in ("exit", "gate"),
                "capacity": zd.get("capacity"),
            }

        # Assign each person to ALL zones whose bounds contain their centroid
        # (a person can be in multiple overlapping zones)
        for t in enriched_tracks:
            cx, cy = t.get("cx", 0), t.get("cy", 0)
            for zd in custom_zones:
                zid   = zd["zone_id"]
                x1_px = int(zd["x1_pct"] * img_size)
                y1_px = int(zd["y1_pct"] * img_size)
                x2_px = int(zd["x2_pct"] * img_size)
                y2_px = int(zd["y2_pct"] * img_size)
                if x1_px <= cx <= x2_px and y1_px <= cy <= y2_px:
                    zone_people[zid].append(t)

        total_in_frame = len(enriched_tracks)

        for zd in custom_zones:
            zid    = zd["zone_id"]
            people = zone_people[zid]
            meta   = zone_meta[zid]
            n      = len(people)
            record = _compute_zone_record(
                zid, n, people, meta, zone_count_history, total_in_frame
            )
            # ── Capacity metrics (only for named zones) ────────────────────
            cap = meta.get("capacity")
            if cap and cap > 0:
                occ_pct = round(n / cap * 100, 1)
                if occ_pct >= 90:
                    cap_status = "CRITICAL"
                elif occ_pct >= 70:
                    cap_status = "WARNING"
                else:
                    cap_status = "NORMAL"
                record["capacity"]        = cap
                record["occupancy_pct"]   = occ_pct
                record["capacity_status"] = cap_status
            else:
                record["capacity"]        = None
                record["occupancy_pct"]   = None
                record["capacity_status"] = None
            record["label"] = meta["label"]
            record["type"]  = meta["type"]
            zones_out.append(record)

        # Preserve the order the operator defined
        return zones_out

    # ── Default 3×3 grid path ──────────────────────────────────────────────────
    cell_w = img_size / ZONE_GRID_COLS
    cell_h = img_size / ZONE_GRID_ROWS

    zone_people_grid: Dict[str, List[dict]] = {}
    zone_meta_grid:   Dict[str, dict]       = {}

    label_idx = 0
    for row in range(ZONE_GRID_ROWS):
        for col in range(ZONE_GRID_COLS):
            zid = chr(ord('A') + label_idx)
            label_idx += 1
            x1 = int(col       * cell_w)
            y1 = int(row       * cell_h)
            x2 = int((col + 1) * cell_w)
            y2 = int((row + 1) * cell_h)
            is_exit = (
                row == 0 or row == ZONE_GRID_ROWS - 1 or
                col == 0 or col == ZONE_GRID_COLS - 1
            )
            zone_meta_grid[zid] = {
                "label":    zid,
                "type":     "exit" if is_exit else "open_area",
                "bounds":   {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                "is_exit":  is_exit,
                "capacity": None,
                "row":      row,
                "col":      col,
            }
            zone_people_grid[zid] = []

    for t in enriched_tracks:
        cx, cy = t.get("cx", 0), t.get("cy", 0)
        col = min(int(cx / cell_w), ZONE_GRID_COLS - 1)
        row = min(int(cy / cell_h), ZONE_GRID_ROWS - 1)
        zid = chr(ord('A') + row * ZONE_GRID_COLS + col)
        zone_people_grid[zid].append(t)

    total_in_frame = len(enriched_tracks)

    for zid, people in zone_people_grid.items():
        meta = zone_meta_grid[zid]
        n    = len(people)
        record = _compute_zone_record(
            zid, n, people, meta, zone_count_history, total_in_frame
        )
        record["row"]   = meta["row"]
        record["col"]   = meta["col"]
        record["label"] = zid
        record["type"]  = meta["type"]
        zones_out.append(record)

    zones_out.sort(key=lambda z: (z["row"], z["col"]))
    return zones_out


def _compute_zone_record(
    zid: str,
    n: int,
    people: List[dict],
    meta: dict,
    zone_count_history: Dict[str, deque],
    total_in_frame: int,
) -> dict:
    """Shared metric computation for a single zone.  Called by both code paths."""

    # ── 1. Density label ──────────────────────────────────────────────────
    x1 = meta.get("bounds", {}).get("x1", 0)
    y1 = meta.get("bounds", {}).get("y1", 0)
    x2 = meta.get("bounds", {}).get("x2", 100)
    y2 = meta.get("bounds", {}).get("y2", 100)
    
    area_px = max((x2 - x1) * (y2 - y1), 1)
    effective_area = area_px / 10000.0  # mock standard unit (~1m2)
    local_density = round(n / effective_area, 2)

    if local_density <= _DENSITY_LOW:
        density = "LOW"
    elif local_density <= _DENSITY_MEDIUM:
        density = "MEDIUM"
    elif local_density <= _DENSITY_HIGH:
        density = "HIGH"
    else:
        density = "CRITICAL"

    # ── 2. Speed metrics ──────────────────────────────────────────────────
    speeds        = [t.get("speed", 0.0) for t in people if t.get("speed") is not None]
    avg_speed     = round(sum(speeds) / len(speeds), 1) if speeds else 0.0
    speed_variance = _population_variance(speeds)

    # ── 3. Direction / flow metrics ───────────────────────────────────────
    dirs             = [t.get("direction", 0.0) for t in people if t.get("direction") is not None]
    dominant_dir_deg, dir_R = _circular_mean_and_R(dirs)
    dir_label         = _direction_label(dominant_dir_deg) if dirs else "·"
    direction_variance = round(1.0 - dir_R, 3)

    # ── 4. Flow magnitude ─────────────────────────────────────────────────
    flow_magnitude = round(avg_speed * dir_R, 1)

    # ── 5. Flow convergence ───────────────────────────────────────────────
    if dir_R >= 0.75 and flow_magnitude > 30:
        flow_convergence = "HIGH"
    elif dir_R >= 0.45 or flow_magnitude > 10:
        flow_convergence = "MEDIUM"
    else:
        flow_convergence = "LOW"

    # ── 6. Compression trend ──────────────────────────────────────────────
    if zid not in zone_count_history:
        zone_count_history[zid] = deque(maxlen=ZONE_HISTORY_FRAMES)
    hist = zone_count_history[zid]

    if len(hist) >= 3:
        recent_avg = sum(list(hist)[-3:]) / 3
        if n > recent_avg * 1.15:
            compression = "INCREASING"
        elif n < recent_avg * 0.85:
            compression = "DECREASING"
        else:
            compression = "STABLE"
    else:
        compression = "STABLE"

    hist.append(n)

    # ── 7. Exit occupancy ─────────────────────────────────────────────────
    if meta.get("is_exit") and total_in_frame > 0:
        exit_occupancy = round(n / total_in_frame * 100, 1)
    else:
        exit_occupancy = None

    # ── 8. Movement intensity ─────────────────────────────────────────────
    raw_intensity = avg_speed * n
    if raw_intensity < _INTENSITY_LOW:
        movement_intensity = "LOW"
    elif raw_intensity < _INTENSITY_MEDIUM:
        movement_intensity = "MEDIUM"
    else:
        movement_intensity = "HIGH"

    # ── 9. Average nearest-neighbour distance (physical compression proxy) ───
    points = [(t.get("cx", 0), t.get("cy", 0)) for t in people]
    ann_distance = _nearest_neighbour_distance(points)  # None if n < 2

    return {
        "id":                  zid,
        "bounds":              meta.get("bounds", {}),
        "is_exit":             meta.get("is_exit", False),
        # Core metrics
        "people":              n,
        "local_density":       local_density,
        "density":             density,
        # Speed
        "avg_speed":           avg_speed,
        "speed_variance":      speed_variance,
        # Direction / Flow
        "dominant_direction":  dominant_dir_deg,
        "direction_label":     dir_label,
        "direction_variance":  direction_variance,
        "flow_magnitude":      flow_magnitude,
        "flow_convergence":    flow_convergence,
        # Trend
        "compression":         compression,
        "ann_distance":        ann_distance,
        "exit_occupancy":      exit_occupancy,
        "movement_intensity":  movement_intensity,
    }



# ── Zone type colours (BGR) ───────────────────────────────────────────────────────
_ZONE_TYPE_COLORS = {
    "entrance":  (100, 220,  60),   # lime green
    "exit":      ( 50,  50, 230),   # red
    "gate":      ( 30, 160, 255),   # amber
    "corridor":  (220, 100,  30),   # blue
    "staircase": (200,  60, 200),   # violet
    "open_area": (200, 200,   0),   # cyan
    "custom":    (180, 180, 180),   # light grey
}

ZONE_TYPE_LABELS = [
    "entrance", "exit", "gate", "corridor", "staircase", "open_area", "custom"
]


class ZoneDefinition(BaseModel):
    """
    A named logical zone within the camera frame.

    Coordinates are expressed as fractions of the frame width/height (0.0–1.0)
    so that zone definitions remain valid regardless of the stream resolution.

    Example
    -------
    {
      "zone_id":  "gate",
      "label":    "Main Gate",
      "type":     "gate",
      "x1_pct":   0.0,
      "y1_pct":   0.0,
      "x2_pct":   0.5,
      "y2_pct":   0.4,
      "capacity": 20
    }
    """
    zone_id:  str
    label:    str
    type:     str = "custom"    # one of ZONE_TYPE_LABELS
    x1_pct:   float             # left   edge as fraction [0,1]
    y1_pct:   float             # top    edge as fraction [0,1]
    x2_pct:   float             # right  edge as fraction [0,1]
    y2_pct:   float             # bottom edge as fraction [0,1]
    capacity: Optional[int] = None   # max occupancy; None = uncapped


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
        # ── Multi-Object Tracking state ─────────────────────────────────────
        # One BYTETracker per session — maintains cross-frame person IDs.
        # In relay mode this will be None (tracker lives on the ML node).
        self.tracker = BYTETracker(make_tracker_args(), frame_rate=10) if BYTETracker else None
        self.frame_index: int = 0   # monotonically increasing, fed to ByteTrack
        # ── Temporal memory: track_id → PersonMemory ─────────────────────────
        # PersonMemory holds a rolling MEMORY_WINDOW_SECONDS of positions,
        # speeds, directions, and accelerations for each tracked person.
        self.person_memory: Dict[int, PersonMemory] = {}
        # last_seen: track_id → last timestamp it appeared (for stale eviction)
        self.last_seen: Dict[int, float] = {}
        # ── Zone analytics history ─────────────────────────────────────────
        # Stores a rolling count of people per zone for compression detection.
        self.zone_count_history: Dict[str, deque] = {}
        # ── Custom named zones ──────────────────────────────────────────────
        # When set, overrides the default 3×3 grid in compute_zone_analytics().
        # Each entry is a ZoneDefinition dict (serialised from the Pydantic model).
        self.custom_zones: Optional[List[dict]] = None
        # ── Optical Flow state ──────────────────────────────────────────────
        self.prev_gray: Optional[np.ndarray] = None
        # ── Prediction Engine ───────────────────────────────────────────────
        # Maintains a 12-feature scene-level vector sampled every second,
        # an EMA-smoothed risk score (0–100), and a linear-regression trend.
        self.prediction_engine: ScenePredictionEngine = ScenePredictionEngine()

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
        # ── Zone analytics indexes ────────────────────────────────────────────
        # Rich per-zone crowd intelligence time-series
        await db.zone_analytics.create_index(
            "timestamp", expireAfterSeconds=MONGO_TTL_SECONDS
        )
        await db.zone_analytics.create_index(
            [("session_code", 1), ("zone", 1), ("timestamp", -1)]
        )
        await db.zone_analytics.create_index(
            [("alert_level", 1), ("timestamp", -1)]
        )
        # ── Wallet & Organisation indexes ─────────────────────────────────────
        await db.organizations.create_index([("email", 1)], unique=True)
        await db.organizations.create_index([("status", 1), ("created_at", -1)])
        await db.wallets.create_index([("organization_id", 1)], unique=True)
        await db.wallet_transactions.create_index(
            [("organization_id", 1), ("created_at", -1)]
        )
        await db.wallet_transactions.create_index([("type", 1), ("status", 1)])
        await db.cameras.create_index([("organization_id", 1)])
        await db.cameras.create_index([("session_code", 1)], unique=True, sparse=True)
        # ── Feature config index ─────────────────────────────────────────────────
        await db.org_features.create_index([("organization_id", 1)], unique=True)
        # ── Hand DB reference to wallet module ────────────────────────────────
        set_wallet_db(db)
        logger.info(
            f"MongoDB Atlas connected. DB='{MONGO_DB}' TTL={MONGO_TTL_SECONDS}s "
            f"cap={MAX_RECORDS_PER_SESSION}"
        )
    except Exception as e:
        logger.error(f"MongoDB connection failed: {e}. Frames will NOT be persisted.")

    # ── x402 payment middleware (programmatic API) ─────────────────────────────
    # Conditionally gates POST /api/v1/crowd/predict with USDC micropayments.
    # If X402_PAY_TO_ADDRESS env var is not set, the endpoint remains open.
    x402_enabled = x402_config.init_x402()
    if x402_enabled:
        try:
            from x402.http.middleware import PaymentMiddlewareASGI
            app.add_middleware(
                PaymentMiddlewareASGI,
                routes=x402_config.get_route_config(),
                server=x402_config.x402_server,
            )
            logger.info("[x402] PaymentMiddlewareASGI applied to /api/v1/crowd/predict")
        except Exception as e:
            logger.error(f"[x402] Middleware registration failed: {e}")

    asyncio.create_task(cleanup_expired_sessions())


@app.on_event("shutdown")
async def shutdown():
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB connection closed.")


# ── MongoDB helpers ───────────────────────────────────────────────────────────
async def save_frame_to_mongo(session_code: str, count: int, status: str,
                               frame_ts: float, jpeg_bytes: bytes):
    """Fire-and-forget: persist one processed frame (JPEG + minimal metadata) to MongoDB."""
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


async def save_zone_analytics_to_mongo(
    session_code: str,
    frame_ts:     float,
    zones:        List[dict],
    prediction:   dict,
    people_count: int,
    alert_level:  str,
):
    """
    Persist a rich per-zone crowd intelligence snapshot to the ``zone_analytics``
    collection.  One document is written per zone per frame, giving the org portal
    a full time-series of every metric the risk engine computed.

    Schema (per document)::

        {
          session_code, timestamp, zone, alert_level,
          people_count, density, local_density,
          average_speed, dominant_direction,
          flow_magnitude, flow_convergence,
          compression, exit_occupancy,
          risk_score, risk_trend,
          direction_variance, movement_intensity
        }
    """
    if db is None:
        return
    try:
        risk_score = prediction.get("risk_score", 0.0)
        risk_trend = prediction.get("trend", "STABLE")
        ts_dt      = datetime.utcfromtimestamp(frame_ts)

        docs = []
        for z in zones:
            doc = {
                "session_code":      session_code,
                "timestamp":         ts_dt,
                "zone":              z.get("id",              "?"),
                "zone_label":        z.get("label",           z.get("id", "?")),
                "zone_type":         z.get("type",            "custom"),
                "alert_level":       alert_level,
                # People / density
                "people_count":      z.get("people",          0),
                "total_people":      people_count,
                "density":           z.get("density",         "LOW"),
                "local_density":     z.get("local_density",   0.0),
                # Speed
                "average_speed":     z.get("avg_speed",       0.0),
                "speed_variance":    z.get("speed_variance",  0.0),
                # Direction / flow
                "dominant_direction":z.get("dominant_direction", 0.0),
                "direction_variance":z.get("direction_variance", 0.0),
                "flow_magnitude":    z.get("flow_magnitude",  0.0),
                "flow_convergence":  z.get("flow_convergence","LOW"),
                # Compression / exit
                "compression":       z.get("compression",    "STABLE"),
                "exit_occupancy":    z.get("exit_occupancy"),    # None for non-exit zones
                "movement_intensity":z.get("movement_intensity", "LOW"),
                # Risk engine output
                "risk_score":        risk_score,
                "risk_trend":        risk_trend,
            }
            docs.append(doc)

        if docs:
            await db.zone_analytics.insert_many(docs)
            logger.debug(
                f"[{session_code}] [MONGO] Zone analytics saved — "
                f"{len(docs)} zone(s), risk={risk_score:.1f}, level={alert_level}"
            )
    except Exception as e:
        logger.error(f"[{session_code}] [MONGO] Failed to save zone analytics: {e}")


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


async def trigger_llm_calls(
    session: "Session",
    code: str,
    reason: str,
    risk_snapshot: Optional[dict] = None,
):
    """
    Dual-LLM trigger — the LLM is the *communication* layer, not the detector.

    The risk engine (ScenePredictionEngine) already determined whether an alert
    is needed.  This function receives the structured risk context and asks the
    LLM to:
      LLM-1 → Produce a SHORT, actionable operator instruction (spoken via TTS)
      LLM-2 → Produce a factual org status report (saved to MongoDB)

    ``risk_snapshot`` contains the pre-computed analytics so the LLM does not
    need to decide whether something is dangerous — it only needs to explain
    what is happening and what to do about it.

    Uses a per-session asyncio.Lock to prevent race conditions.
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

        counts    = [f["count"]      for f in window]
        avg_cnt   = sum(counts) / len(counts)
        max_cnt   = max(counts)

        # ── Build context from risk snapshot (if available) ───────────────────
        if risk_snapshot:
            risk_score   = risk_snapshot.get("risk_score", 0.0)
            alert_level  = risk_snapshot.get("alert_level", "SAFE")
            trend        = risk_snapshot.get("trend", "STABLE")
            top_zone     = risk_snapshot.get("top_zone")      # highest-pressure zone dict
            fv           = risk_snapshot.get("feature_vector", [])
            fnames       = risk_snapshot.get("feature_names", [])

            # Structured context lines (what the risk engine observed)
            ctx_lines = [
                f"Alert level  : {alert_level}  (risk score = {risk_score:.1f}/100, trend = {trend})",
                f"People in scene : {int(avg_cnt)} avg, {max_cnt} peak over last 30s",
                f"Trigger reason  : {reason}",
            ]

            if top_zone:
                zone_lbl = top_zone.get("label", top_zone.get("id", "Unknown zone"))
                ctx_lines += [
                    "",
                    f"Highest-pressure zone : {zone_lbl}  (type: {top_zone.get('type','?')})",
                    f"  People count        : {top_zone.get('people', 0)}",
                    f"  Density             : {top_zone.get('density', '?')}",
                    f"  Average speed       : {top_zone.get('avg_speed', 0.0):.1f} px/s",
                    f"  Flow convergence    : {top_zone.get('flow_convergence', '?')}",
                    f"  Compression trend   : {top_zone.get('compression', '?')}",
                    f"  Exit occupancy      : {top_zone.get('exit_occupancy') or 'N/A'}",
                    f"  Direction variance  : {top_zone.get('direction_variance', 0.0):.3f}",
                    f"  Movement intensity  : {top_zone.get('movement_intensity', '?')}",
                ]

            if fv and fnames:
                fv_lines = [f"    {n}: {v:.3f}" for n, v in zip(fnames, fv)]
                ctx_lines += ["", "Feature vector (all 12 dimensions):"] + fv_lines

            data_summary = "\n".join(ctx_lines)
            dominant     = alert_level

        else:
            # Fallback: no snapshot (e.g. called from idle 30s timer)
            alert_levels = [f["alert_level"] for f in window if "alert_level" in f]
            if "CRITICAL" in alert_levels:
                dominant = "CRITICAL"
            elif "WARNING" in alert_levels:
                dominant = "WARNING"
            else:
                dominant = "SAFE"
            data_summary = (
                f"Monitoring window: {len(window)} frames (~30s). "
                f"Crowd count — avg: {avg_cnt:.1f}, peak: {max_cnt}. "
                f"Alert level: {dominant}. Reason: {reason}."
            )

        logger.info(
            f"[{code}] [LLM] 🤖 Triggering dual LLM calls. "
            f"reason={reason} level={dominant} avg={avg_cnt:.1f} peak={max_cnt}"
        )

        try:
            # ── Fire both LLM calls in parallel ──────────────────────────────
            # LLM-1: Operator instruction (spoken via TTS on dashboards)
            op_task = asyncio.create_task(call_groq(
                system_prompt=(
                    "You are a real-time crowd safety assistant for event operators. "
                    "The risk detection system has already flagged a potential crowd hazard — "
                    "your job is ONLY to communicate it clearly. "
                    "Based on the structured crowd analytics below, give 1-2 SHORT, actionable "
                    "operator instructions. This will be spoken aloud via text-to-speech. "
                    "Be calm, direct, specific (mention the zone if known), and clear. "
                    "No markdown. No bullet points. No disclaimers."
                ),
                user_content=data_summary,
            ))
            # LLM-2: Org status report (saved to MongoDB for management)
            org_task = asyncio.create_task(call_groq(
                system_prompt=(
                    "You are a crowd intelligence reporting system for event management. "
                    "The risk engine has computed the following crowd analytics. "
                    "Your job is to write a SHORT, factual status report (2-3 sentences) "
                    "that summarises what is happening, which zone is most at risk, "
                    "and the current risk trend. Be professional and precise. "
                    "No markdown. No bullet points. No instructions."
                ),
                user_content=data_summary,
            ))

            op_text, org_text = await asyncio.gather(op_task, org_task)

            logger.info(f"[{code}] [LLM] ✅ Operator instruction: {op_text[:120]}...")
            logger.info(f"[{code}] [LLM] ✅ Org report: {org_text[:120]}...")

            # ── Broadcast TTS instruction to all live dashboards ──────────────
            tts_payload = json.dumps({
                "type":        "tts_instruction",
                "text":        op_text,
                "alert_level": dominant,
                "risk_score":  risk_snapshot.get("risk_score", 0.0) if risk_snapshot else 0.0,
                "trend":       risk_snapshot.get("trend", "STABLE") if risk_snapshot else "STABLE",
                "reason":      reason,
                "timestamp":   time.time(),
            })
            for dash_ws in session.dashboard_connections.copy():
                try:
                    await dash_ws.send_text(tts_payload)
                except Exception:
                    pass  # disconnected — cleaned up on next frame send

            # ── Persist org report to MongoDB (non-blocking) ──────────────────
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


# ── Zone management endpoints ──────────────────────────────────────────────────

class ZoneList(BaseModel):
    zones: List[ZoneDefinition]


@app.post("/session/{code}/zones")
async def set_session_zones(code: str, body: ZoneList):
    """
    Define (or replace) named logical zones for a session.

    The dashboard or operator sends a list of ZoneDefinition objects.
    These immediately replace the default 3×3 grid for this session.

    Example body::

        {
          "zones": [
            {"zone_id":"entrance","label":"Main Entrance","type":"entrance",
             "x1_pct":0.0,"y1_pct":0.0,"x2_pct":0.5,"y2_pct":0.35},
            {"zone_id":"gate","label":"Gate","type":"gate",
             "x1_pct":0.5,"y1_pct":0.0,"x2_pct":1.0,"y2_pct":0.35,"capacity":20},
            {"zone_id":"open","label":"Open Area","type":"open_area",
             "x1_pct":0.0,"y1_pct":0.35,"x2_pct":1.0,"y2_pct":1.0}
          ]
        }
    """
    code = code.upper()
    session = get_session(code)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate zone_id uniqueness
    ids = [z.zone_id for z in body.zones]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="zone_id values must be unique")

    # Serialise to plain dicts and store
    session.custom_zones = [z.model_dump() for z in body.zones]
    # Clear stale compression history so new zones start fresh
    session.zone_count_history = {}

    logger.info(
        f"[{code}] Custom zones set: "
        + ", ".join(f"{z.zone_id}({z.type})" for z in body.zones)
    )
    return {"status": "ok", "zones": session.custom_zones}


@app.get("/session/{code}/zones")
async def get_session_zones(code: str):
    """Return the current zone definitions for a session."""
    code = code.upper()
    session = get_session(code)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.custom_zones is None:
        return {"mode": "grid", "zones": None,
                "grid": {"rows": ZONE_GRID_ROWS, "cols": ZONE_GRID_COLS}}
    return {"mode": "custom", "zones": session.custom_zones}


@app.delete("/session/{code}/zones")
async def clear_session_zones(code: str):
    """Remove custom zones and revert to the default 3×3 grid."""
    code = code.upper()
    session = get_session(code)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.custom_zones = None
    session.zone_count_history = {}
    logger.info(f"[{code}] Custom zones cleared. Reverting to default grid.")
    return {"status": "ok", "mode": "grid"}


# ── Prediction REST Endpoint ───────────────────────────────────────────────────

@app.get("/session/{code}/prediction")
async def get_session_prediction(code: str):
    """
    Return the latest prediction state for a session.

    Useful for REST polling (org portal, mobile app).
    Response schema::

        {
          "feature_vector":  [f0, f1, …, f11],
          "feature_names":   ["density", "density_change", …],
          "risk_score":      41.2,
          "risk_label":      "CAUTION",
          "trend":           "RISING",
          "history":         [[ts, score], …]
        }
    """
    code = code.upper()
    session = get_session(code)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.prediction_engine.current_state()


@app.post("/process_inference")
async def process_inference_route(request: Request):
    """
    Endpoint for the cloud (relay) node to send frames to this local ML node.
    The ML node has no session context so the tracker is None here; the caller
    (cloud node) manages the BYTETracker per-session and passes frame_idx.
    """
    if ML_NODE_URL:
        raise HTTPException(status_code=400, detail="This node is configured as a relay.")
    data      = await request.body()
    frame_idx = int(request.headers.get("X-Frame-Index", 0))
    result = await asyncio.to_thread(process_frame, data, None, frame_idx)
    result.pop("next_gray", None)
    result["jpeg_bytes"]    = base64.b64encode(result["jpeg_bytes"]).decode("utf-8")
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

# Palette of distinct BGR colours — one per track ID (cycles)
_TRACK_COLORS = [
    (0, 255, 128),   # mint green
    (0, 191, 255),   # deep sky blue
    (255, 128, 0),   # orange
    (180, 0, 255),   # violet
    (255, 0, 128),   # hot pink
    (0, 255, 255),   # cyan
    (255, 220, 0),   # gold
    (128, 255, 0),   # chartreuse
]

def _track_color(track_id: int):
    return _TRACK_COLORS[track_id % len(_TRACK_COLORS)]

def paint_gaussian(heat, cx, cy, sigma):
    """Paint a radial Gaussian blob centered at (cx, cy)."""
    h, w = heat.shape
    x = np.arange(0, w, 1, np.float32)
    y = np.arange(0, h, 1, np.float32)[:, np.newaxis]
    heat += np.exp(-((x - cx)**2 + (y - cy)**2) / (2 * sigma**2))

def generate_heatmap_overlay(img, track_data: list):
    """
    Generate a density heatmap from tracked person centroids and blend it.
    track_data: list of {id, x1, y1, x2, y2, cx, cy} dicts.
    Falls back to accepting YOLO boxes if track_data is empty.
    """
    h, w = img.shape[:2]
    heat = np.zeros((h, w), dtype=np.float32)
    for t in track_data:
        cx, cy = t["cx"], t["cy"]
        box_w  = t["x2"] - t["x1"]
        sigma  = max(int(box_w / 3), 12)
        paint_gaussian(heat, cx, cy, sigma)

    if heat.max() == 0:
        return img   # no detections — return plain frame

    heat = cv2.normalize(heat, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    colormap = cv2.applyColorMap(heat, cv2.COLORMAP_JET)
    return cv2.addWeighted(img, 0.55, colormap, 0.45, 0)

def _draw_zone_overlays(
    img: np.ndarray,
    zone_defs: List[dict],
    img_w: int,
    img_h: int,
) -> np.ndarray:
    """
    Draw semi-transparent zone overlays with type-coloured borders and labels
    directly on *img*.  Zone coordinates are in percentage format [0,1].
    """
    overlay = img.copy()

    for zd in zone_defs:
        color = _ZONE_TYPE_COLORS.get(zd.get("type", "custom"), (180, 180, 180))
        x1 = int(zd["x1_pct"] * img_w)
        y1 = int(zd["y1_pct"] * img_h)
        x2 = int(zd["x2_pct"] * img_w)
        y2 = int(zd["y2_pct"] * img_h)

        # Semi-transparent fill (15 % opacity)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)

        # Solid 2px border
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)

        # Zone label at top-left corner
        label = zd.get("label", zd.get("zone_id", "?"))
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        # Label background chip
        cv2.rectangle(img, (x1, y1), (x1 + tw + 6, y1 + th + 8), color, -1)
        cv2.putText(
            img, label,
            (x1 + 3, y1 + th + 3),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5,
            (0, 0, 0), 1, cv2.LINE_AA,
        )

        # Zone type badge (small, top-right)
        badge = zd.get("type", "").upper()[:3]
        (bw, bh), _ = cv2.getTextSize(badge, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
        cv2.rectangle(img, (x2 - bw - 6, y1), (x2, y1 + bh + 6), color, -1)
        cv2.putText(
            img, badge,
            (x2 - bw - 3, y1 + bh + 1),
            cv2.FONT_HERSHEY_SIMPLEX, 0.38,
            (0, 0, 0), 1, cv2.LINE_AA,
        )

    # Blend the fill overlay at 15 % opacity
    img = cv2.addWeighted(overlay, 0.15, img, 0.85, 0)
    return img


def _draw_flow(img: np.ndarray, flow: np.ndarray, step: int = 16) -> np.ndarray:
    """Draw sparse optical flow vectors on the image."""
    h, w = img.shape[:2]
    y, x = np.mgrid[step/2:h:step, step/2:w:step].reshape(2, -1).astype(int)
    fx, fy = flow[y, x].T
    lines = np.vstack([x, y, x + fx, y + fy]).T.reshape(-1, 2, 2)
    lines = np.int32(lines + 0.5)
    vis = img.copy()
    cv2.polylines(vis, lines, 0, (0, 255, 0), 1, cv2.LINE_AA)
    for (x1, y1), (_x2, _y2) in lines:
        cv2.circle(vis, (x1, y1), 1, (0, 255, 0), -1)
    return vis


def _draw_tracks(img: np.ndarray, tracks: list) -> np.ndarray:
    """
    Draw bounding boxes + track IDs on *img* in-place and return it.
    Each unique track ID gets a stable colour from the palette.
    """
    for t in tracks:
        tid  = t["id"]
        x1, y1, x2, y2 = int(t["x1"]), int(t["y1"]), int(t["x2"]), int(t["y2"])
        color = _track_color(tid)

        # Bounding box
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)

        # Label background + text
        label  = f"ID:{tid}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(img, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(
            img, label,
            (x1 + 2, y1 - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55,
            (0, 0, 0), 2, cv2.LINE_AA,
        )
    return img

def process_frame(
    frame_bytes: bytes,
    tracker=None,
    frame_idx: int = 0,
    zone_defs: Optional[List[dict]] = None,
    prev_gray: Optional[np.ndarray] = None,
) -> Dict:
    """
    Run YOLO inference on *frame_bytes*, feed into ByteTrack, and (optionally)
    draw named zone overlays on the annotated frame.

    Parameters
    ----------
    frame_bytes : raw JPEG bytes from the camera
    tracker     : BYTETracker instance (per-session); None → ID-less fallback
    frame_idx   : monotonically increasing frame counter for ByteTrack
    zone_defs   : list of ZoneDefinition dicts (pct-based); drawn as overlays
    """
    np_arr = np.frombuffer(frame_bytes, np.uint8)
    img    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("Failed to decode image")

    small_img = cv2.resize(img, (INFERENCE_SIZE, INFERENCE_SIZE),
                           interpolation=cv2.INTER_LINEAR)

    # ── Step 0: Optical Flow ──────────────────────────────────────────────────
    gray = cv2.cvtColor(small_img, cv2.COLOR_BGR2GRAY)
    flow_mag = 0.0
    flow_dir = 0.0
    flow = None
    if prev_gray is not None:
        flow = cv2.calcOpticalFlowFarneback(
            prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0
        )
        mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
        flow_mag = float(np.mean(mag))
        flow_dir = float(np.mean(ang)) * 180 / np.pi / 2  # Convert to standard degrees

    # ── Step 1: YOLO detection ─────────────────────────────────────────────────
    results = model(small_img, classes=0, verbose=False, imgsz=INFERENCE_SIZE)

    tracks: List[Dict] = []
    annotated_img = small_img.copy()

    if results and len(results[0].boxes) > 0:
        result    = results[0]
        raw_boxes = result.boxes  # ultralytics Boxes object

        # ── Step 2: ByteTrack update ───────────────────────────────────────────
        if tracker is not None:
            # Build detection tensor: [x1, y1, x2, y2, conf, cls]
            import torch as _torch
            xyxy  = raw_boxes.xyxy.cpu()            # (N, 4)
            conf  = raw_boxes.conf.cpu().unsqueeze(1) # (N, 1)
            cls   = raw_boxes.cls.cpu().unsqueeze(1)  # (N, 1)  — all 0 (person)
            dets  = _torch.cat([xyxy, conf, cls], dim=1).numpy()  # (N, 6)

            img_h, img_w = small_img.shape[:2]
            active = tracker.update(dets, (img_h, img_w), (img_h, img_w))

            # active is a list of STrack objects
            for t in active:
                x1, y1, x2, y2 = [int(v) for v in t.tlbr]
                # Clamp to image bounds
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(img_w - 1, x2), min(img_h - 1, y2)
                tid = int(t.track_id)
                score = float(t.score)
                cx  = int((x1 + x2) / 2)
                cy  = int((y1 + y2) / 2)
                tracks.append({
                    "id":    tid,
                    "x1":   x1, "y1": y1,
                    "x2":   x2, "y2": y2,
                    "cx":   cx, "cy": cy,
                    "score": round(score, 3),
                })

            # Draw track-aware annotations
            annotated_img = _draw_tracks(small_img.copy(), tracks)

        else:
            # Fallback: no tracker — use plain YOLO plot + synthetic IDs
            annotated_img = result.plot()
            for i, box in enumerate(raw_boxes):
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].cpu()]
                tracks.append({
                    "id":    i,
                    "x1":   x1, "y1": y1,
                    "x2":   x2, "y2": y2,
                    "cx":   int((x1 + x2) / 2),
                    "cy":   int((y1 + y2) / 2),
                    "score": round(float(box.conf[0].cpu()), 3),
                })

    # ── Step 3: Zone overlays (if custom zones are defined) ─────────────────
    img_h, img_w = annotated_img.shape[:2]
    if zone_defs:
        annotated_img = _draw_zone_overlays(annotated_img, zone_defs, img_w, img_h)

    # ── Step 3.5: Optical Flow Overlay ─────────────────────────────────────────
    if flow is not None:
        annotated_img = _draw_flow(annotated_img, flow)

    # ── Step 4: Heatmap ────────────────────────────────────────────────────────
    if tracks:
        heatmap_img = generate_heatmap_overlay(annotated_img.copy(), tracks)
    else:
        heatmap_img = annotated_img.copy()

    count       = len(tracks)
    track_count = len(tracks)  # active tracks this frame

    # ── Preliminary status (overridden by risk engine in process_worker) ────
    # process_frame runs in a thread without session context, so we cannot
    # access the prediction engine here.  A neutral marker is returned; the
    # async process_worker replaces it with the risk-band label immediately
    # after the prediction tick runs.
    status = "PENDING"

    _, encoded_img     = cv2.imencode(".jpg", annotated_img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    _, encoded_heatmap = cv2.imencode(".jpg", heatmap_img,  [cv2.IMWRITE_JPEG_QUALITY, 50])

    return {
        "status":        status,
        "count":         count,
        "track_count":   track_count,
        "tracks":        tracks,          # list of {id, x1,y1,x2,y2, cx,cy, score}
        "jpeg_bytes":    encoded_img.tobytes(),
        "heatmap_bytes": encoded_heatmap.tobytes(),
        "timestamp":     time.time(),
        "next_gray":     gray,
        "global_flow_magnitude": round(flow_mag, 3),
        "global_flow_direction": round(flow_dir, 3),
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
                    # ── 30-sec periodic check even when camera is idle ─────────────
                    if (time.time() - session.last_llm_call) >= LLM_PERIODIC_INTERVAL:
                        if session.frame_buffer:
                            # Pass last known prediction state as snapshot
                            snap = session.prediction_engine.current_state()
                            asyncio.create_task(
                                trigger_llm_calls(
                                    session, code, "30s_periodic",
                                    {
                                        "risk_score":     snap["risk_score"],
                                        "alert_level":    snap["risk_label"],
                                        "trend":          snap["trend"],
                                        "feature_vector": snap["feature_vector"],
                                        "feature_names":  snap["feature_names"],
                                        "top_zone":       None,  # not available here
                                    },
                                )
                            )
                    continue

                # Claim and clear the frame atomically
                processing_state["latest_frame"] = None
                frame_born_time = processing_state.get("latest_frame_time") or time.time()
                queue_wait_ms   = int((time.time() - frame_born_time) * 1000)

                try:
                    logger.info(
                        f"[{code}] [PROCESS] Yanked frame from queue. "
                        f"Waited {queue_wait_ms}ms. Starting YOLO+ByteTrack inference..."
                    )
                    inf_start = time.time()

                    # Bump per-session frame counter (ByteTrack needs this)
                    session.frame_index += 1
                    cur_frame_idx = session.frame_index

                    if ML_NODE_URL:
                        # Relay mode: cloud node has the tracker, ML node is stateless
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            resp = await client.post(
                                f"{ML_NODE_URL.rstrip('/')}/process_inference",
                                content=data,
                                headers={"X-Frame-Index": str(cur_frame_idx)},
                            )
                            resp.raise_for_status()
                            result = resp.json()
                            result["jpeg_bytes"]    = base64.b64decode(result["jpeg_bytes"])
                            result["heatmap_bytes"] = base64.b64decode(result["heatmap_bytes"])
                            # ML node is stateless (no tracker) — run tracking locally
                            # on the returned raw track data if available
                            # (ML node returns id-less tracks; cloud applies its tracker)
                            # For now relay mode gets plain count, future: pass raw dets back
                    else:
                        result = await asyncio.to_thread(
                            process_frame, data, session.tracker, cur_frame_idx,
                            session.custom_zones, session.prev_gray,
                        )
                        session.prev_gray = result.pop("next_gray", None)

                    inf_time_ms = int((time.time() - inf_start) * 1000)
                    tracks      = result.get("tracks", [])
                    logger.info(
                        f"[{code}] [PROCESS] YOLO+ByteTrack done in {inf_time_ms}ms. "
                        f"count={result['count']} active_tracks={len(tracks)} "
                        f"status={result['status']}"
                    )

                    # ── Update temporal memory for each tracked person ──────────
                    now          = result.get("timestamp", time.time())
                    mem_cutoff   = now - MEMORY_WINDOW_SECONDS
                    stale_cutoff = now - TRACK_STALE_SECONDS

                    for t in tracks:
                        tid = t["id"]
                        if tid not in session.person_memory:
                            session.person_memory[tid] = PersonMemory()
                        # 1. Append new observation (also derives speed/dir/accel)
                        session.person_memory[tid].update(
                            float(t["cx"]), float(t["cy"]), now
                        )
                        # 2. Prune entries that have fallen outside the 5s window
                        session.person_memory[tid].prune(mem_cutoff)
                        session.last_seen[tid] = now

                    # Evict tracks that haven't been seen in TRACK_STALE_SECONDS
                    stale_ids = [
                        tid for tid, ts in session.last_seen.items()
                        if ts < stale_cutoff
                    ]
                    for tid in stale_ids:
                        session.person_memory.pop(tid, None)
                        session.last_seen.pop(tid, None)
                    if stale_ids:
                        logger.info(
                            f"[{code}] [MEMORY] Evicted {len(stale_ids)} stale "
                            f"track(s): {stale_ids}"
                        )

                    total_tracked = len(session.person_memory)  # unique IDs alive now
                    logger.info(
                        f"[{code}] [MEMORY] Active IDs: "
                        f"{sorted(session.person_memory.keys())} "
                        f"(window={MEMORY_WINDOW_SECONDS}s)"
                    )

                    # ── 30-sec buffer is updated after prediction tick (see below)

                    last_sent_jpeg    = result["jpeg_bytes"]
                    last_sent_heatmap = result.get("heatmap_bytes")
                    del result["jpeg_bytes"]
                    if "heatmap_bytes" in result:
                        del result["heatmap_bytes"]

                    # ── Enrich payload with per-person motion analytics ─────────
                    result["track_count"]   = len(tracks)
                    result["total_tracked"] = total_tracked

                    enriched_tracks = []
                    for t in tracks:
                        tid  = t["id"]
                        mem  = session.person_memory.get(tid)
                        info = {
                            "id":  tid,
                            "cx":  t["cx"],
                            "cy":  t["cy"],
                        }
                        if mem:
                            info.update(mem.analytics())  # speed, direction, accel,
                                                          # consistency, predicted_cx/cy
                        enriched_tracks.append(info)

                    result["tracks"] = enriched_tracks

                    # ── Compute zone-level crowd behaviour ─────────────────────
                    zones = compute_zone_analytics(
                        enriched_tracks       = enriched_tracks,
                        person_memory         = session.person_memory,
                        zone_count_history    = session.zone_count_history,
                        img_size              = INFERENCE_SIZE,
                        custom_zones          = session.custom_zones,
                    )
                    result["zones"] = zones

                    # ── AI Billing Engine (1-second Intelligence Unit) ─────────
                    last_charged = getattr(session, "last_charged_ts", 0)
                    has_credits = True
                    if now - last_charged >= 1.0:
                        try:
                            # 1. Identify organization
                            if hasattr(session, "org_id") and session.org_id:
                                org_id = session.org_id
                            else:
                                demo_org = await get_demo_org()
                                org_id = demo_org["id"]

                            # 2. Fetch enabled feature flags for this org
                            features = await get_org_features_internal(org_id)

                            # 3. Calculate cost based on enabled features only
                            total_cost = sum(
                                FEATURE_PRICING[k]
                                for k, enabled in features.items()
                                if enabled and k in FEATURE_PRICING
                            )

                            # 4. Debit wallet
                            if total_cost > 0:
                                has_credits = await consume_credits(org_id, "INTELLIGENCE_BUNDLE", total_cost)
                            session.last_charged_ts = now
                            # Cache features on the session for gating pipeline steps
                            session._features_cache = features
                        except Exception as e:
                            logger.error(f"[{code}] [BILLING] Engine error: {e}")
                            has_credits = False
                    else:
                        # Use cached features if available
                        features = getattr(session, "_features_cache", {k: True for k in FEATURE_PRICING})

                    # ── Run prediction engine tick ─────────────────────────
                    if has_credits:
                        prediction = session.prediction_engine.tick(
                            enriched_tracks = enriched_tracks,
                            zones           = zones,
                            global_flow_mag = result.get("global_flow_magnitude", 0.0),
                            now             = now,
                        )
                        result["prediction"] = prediction
    
                        # ── Derive alert_level from risk score (risk engine owns status) ──
                        risk_score  = prediction["risk_score"]
                        alert_level = prediction["risk_label"]   # SAFE / WARNING / CRITICAL
                        result["status"]      = alert_level  # override PENDING from process_frame
                        result["alert_level"] = alert_level
    
                        logger.info(
                            f"[{code}] [PREDICT] risk={risk_score:.1f} → {alert_level} "
                            f"trend={prediction['trend']}"
                        )
                    else:
                        # Insufficient funds: degrade gracefully
                        prediction = session.prediction_engine.current_state()
                        result["prediction"] = prediction
                        
                        risk_score = prediction.get("risk_score", 0)
                        alert_level = "INSUFFICIENT_FUNDS"
                        
                        result["status"] = alert_level
                        result["alert_level"] = alert_level
                        
                        logger.warning(
                            f"[{code}] [BILLING] Suspended advanced analytics due to insufficient funds."
                        )
                    logger.info(
                        f"[{code}] [ZONES] "
                        + "  ".join(
                            f"{z['id']}:{z['people']}p/{z['density'][:1]}/{z['compression'][:2]}"
                            for z in zones if z['people'] > 0
                        )
                    )

                    # ── Update 30-sec sliding window buffer ────────────────────
                    # Includes risk_score so LLM trigger can report it accurately
                    session.frame_buffer.append({
                        "count":       result["count"],
                        "alert_level": alert_level,
                        "risk_score":  risk_score,
                        "timestamp":   now,
                    })
                    # Prune entries older than 30 seconds
                    frame_cutoff = now - 30
                    session.frame_buffer = [
                        f for f in session.frame_buffer
                        if f["timestamp"] >= frame_cutoff
                    ]

                    # ── Find the highest-pressure zone for LLM context ─────────
                    # Use people count as primary sort; exit zones get priority tie-break
                    def _zone_pressure(z: dict) -> tuple:
                        is_exit = 1 if z.get("is_exit") else 0
                        return (z.get("people", 0), is_exit)

                    top_zone = max(zones, key=_zone_pressure, default=None) if zones else None

                    # Snapshot passed to LLM so it explains the risk engine's verdict
                    risk_snapshot_for_llm = {
                        "risk_score":     risk_score,
                        "alert_level":    alert_level,
                        "trend":          prediction["trend"],
                        "feature_vector": prediction["feature_vector"],
                        "feature_names":  prediction["feature_names"],
                        "top_zone":       top_zone,
                    }

                    last_sent_payload = json.dumps(result)

                    # ── LLM Trigger Decision (risk-band based, not count-based) ───
                    time_since_llm = time.time() - session.last_llm_call

                    # Instant breach trigger: score enters WARNING or CRITICAL band
                    breach = (
                        alert_level != "SAFE"
                        and time_since_llm >= LLM_BREACH_DEBOUNCE
                        and (
                            session.last_status == "SAFE"
                            or alert_level == "CRITICAL"
                        )
                    )
                    if breach:
                        logger.info(
                            f"[{code}] [LLM] ⚡ Risk breach! "
                            f"level={alert_level} risk={risk_score:.1f} "
                            f"prev={session.last_status}"
                        )
                        asyncio.create_task(
                            trigger_llm_calls(
                                session, code,
                                f"breach_{alert_level}",
                                risk_snapshot_for_llm,
                            )
                        )
                    # 30-sec periodic trigger (always pass current snapshot)
                    elif time_since_llm >= LLM_PERIODIC_INTERVAL:
                        asyncio.create_task(
                            trigger_llm_calls(
                                session, code,
                                "30s_periodic",
                                risk_snapshot_for_llm,
                            )
                        )

                    session.last_status = alert_level

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
                                await ws.send_bytes(b'\xfe' + heatmap)
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

                    # ── Persist JPEG frame to MongoDB (for playback) ───────────
                    asyncio.create_task(
                        save_frame_to_mongo(
                            session_code=code,
                            count=result.get("count", 0),
                            status=result.get("status", "SAFE"),
                            frame_ts=result.get("timestamp", time.time()),
                            jpeg_bytes=last_sent_jpeg,
                        )
                    )
                    # ── Persist rich zone analytics (crowd intelligence) ────────
                    asyncio.create_task(
                        save_zone_analytics_to_mongo(
                            session_code=code,
                            frame_ts=result.get("timestamp", time.time()),
                            zones=result.get("zones", []),
                            prediction=result.get("prediction", {}),
                            people_count=result.get("count", 0),
                            alert_level=result.get("alert_level", "SAFE"),
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
async def websocket_dashboard(websocket: WebSocket, code: str, org_id: str = None):
    code = code.upper()
    session = get_session(code)
    if session is None:
        sessions[code] = Session(code)
        session = sessions[code]
        logger.info(f"[{code}] Session auto-created from dashboard.")
        
    if org_id:
        session.org_id = org_id

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