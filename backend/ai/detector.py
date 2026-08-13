"""
ai/detector.py — YOLO person detector wrapper.
The model instance lives in server.py (loaded once at startup).
This module provides helper functions and documents the detection interface.
"""
import logging
from typing import Optional

logger = logging.getLogger("CrowdPulse.AI.Detector")

# Model reference — set by server.py after startup
_model  = None
_imgsz  = 640
_conf   = 0.35


def set_model(model, imgsz: int = 640, conf: float = 0.35):
    """Called by server.py once the YOLO model is loaded."""
    global _model, _imgsz, _conf
    _model  = model
    _imgsz  = imgsz
    _conf   = conf
    logger.info(f"[Detector] YOLO model registered. imgsz={imgsz} conf={conf}")


def detect(frame) -> list:
    """
    Run person detection on a numpy BGR frame.
    Returns list of bounding boxes in xyxy format with confidence.

    Returns [] if model is not loaded.
    """
    if _model is None:
        return []
    try:
        results = _model(frame, classes=0, verbose=False,
                         imgsz=_imgsz, conf=_conf)
        if results and len(results[0].boxes) > 0:
            return results[0].boxes.xyxy.tolist()
        return []
    except Exception as e:
        logger.error(f"[Detector] Inference error: {e}")
        return []


def is_loaded() -> bool:
    return _model is not None
