"""
ai/prediction.py — Risk prediction and forecasting.
The PredictionEngine class lives in server.py.
This module documents the prediction interface and provides utility functions.
"""
from typing import Tuple


# Risk level thresholds
RISK_CRITICAL  = 70.0
RISK_WARNING   = 40.0


def score_to_level(score: float) -> str:
    """Convert a 0-100 risk score to a risk level string."""
    if score >= RISK_CRITICAL: return "CRITICAL"
    if score >= RISK_WARNING:  return "WARNING"
    return "SAFE"


def level_color(level: str) -> str:
    """Return a hex colour for a risk level (for UI rendering)."""
    return {"CRITICAL": "#ff3b3b", "WARNING": "#ffaa00"}.get(level, "#00e676")


def compute_risk_score(
    density_norm:  float,   # 0-1
    flow_norm:     float,   # 0-1
    compression:   float = 0.0,   # 0-1
    exit_blockage: float = 0.0,   # 0-1
) -> float:
    """
    Weighted multi-factor risk score.
    Returns 0-100 float.
    """
    raw = (
        density_norm  * 0.45 +
        flow_norm     * 0.25 +
        compression   * 0.20 +
        exit_blockage * 0.10
    ) * 100.0
    return round(min(raw, 100.0), 1)


def forecast_risk(history: list, horizon: int = 5) -> Tuple[float, float]:
    """
    Simple linear extrapolation over recent risk history.
    Returns (forecast_score, confidence_pct).
    """
    if len(history) < 3:
        return (history[-1] if history else 0.0, 30.0)

    recent = history[-min(10, len(history)):]
    n      = len(recent)
    xs     = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(recent) / n

    numerator   = sum((xs[i] - x_mean) * (recent[i] - y_mean) for i in range(n))
    denominator = sum((xs[i] - x_mean) ** 2 for i in range(n)) or 1.0
    slope = numerator / denominator

    forecast = y_mean + slope * (n + horizon - 1 - x_mean)
    forecast = max(0.0, min(100.0, round(forecast, 1)))

    # Rough confidence: higher when recent values are consistent
    variance    = sum((v - y_mean) ** 2 for v in recent) / n
    confidence  = max(20.0, min(95.0, 80.0 - variance * 0.1))

    return (forecast, round(confidence, 1))
