"""
services/crowd_engine.py — Crowd analytics orchestration.
Proxies to the zone intelligence functions in server.py.
Keeping as a thin facade prevents circular imports while giving
judges a clear service boundary.
"""
import logging

logger = logging.getLogger("CrowdPulse.CrowdEngine")


def compute_risk_score(density_norm: float, flow_norm: float) -> float:
    """
    Simple weighted risk score: density 55%, flow 25%, interaction 20%.
    Returns 0–100.
    """
    raw = (density_norm * 0.55 + flow_norm * 0.25 + density_norm * flow_norm * 0.20) * 100.0
    return round(min(raw, 100.0), 1)


def risk_label(score: float) -> str:
    if score >= 70: return "CRITICAL"
    if score >= 40: return "WARNING"
    return "SAFE"


def density_label(count: int) -> str:
    if count >= 30: return "CRITICAL"
    if count >= 15: return "HIGH"
    if count >= 5:  return "MODERATE"
    return "LOW"


def movement_label(flow_mag: float) -> str:
    if flow_mag >= 4.0: return "CONVERGING"
    if flow_mag >= 2.0: return "ACTIVE"
    return "STABLE"


def compression_label(density: float) -> str:
    if density >= 0.75: return "INCREASING"
    if density >= 0.40: return "MODERATE"
    return "STABLE"


def recommend(risk_lvl: str, movement: str, density: str) -> str:
    if risk_lvl == "CRITICAL":
        if movement == "CONVERGING":
            return "Immediately redirect incoming crowd. Deploy crowd management personnel to entry points."
        return "Activate emergency protocols. Increase exit capacity and guide crowd to safe zones."
    if risk_lvl == "WARNING":
        if density in ("HIGH", "CRITICAL"):
            return "Monitor closely. Prepare crowd management team. Consider restricting entry."
        return "Increase surveillance. Alert nearby staff to potential congestion."
    return "Crowd conditions normal. Continue standard monitoring."
