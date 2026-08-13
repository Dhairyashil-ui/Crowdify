"""
ai/behaviour.py — Behaviour classification module.
Classifies crowd behaviour signals from tracking data.
"""
from typing import Dict

_INTENSITY_LOW  = 5.0
_INTENSITY_HIGH = 25.0


def classify_movement_intensity(avg_speed: float, count: int) -> str:
    raw = avg_speed * count
    if raw < _INTENSITY_LOW:   return "LOW"
    if raw < _INTENSITY_HIGH:  return "MODERATE"
    return "HIGH"


def classify_flow_convergence(net_flow: float) -> str:
    if net_flow > 0.3:  return "INWARD"
    if net_flow < -0.3: return "OUTWARD"
    return "MIXED"


def classify_compression(count_history: list) -> str:
    if len(count_history) < 3: return "STABLE"
    recent = count_history[-3:]
    delta  = recent[-1] - recent[0]
    if delta > 3:   return "INCREASING"
    if delta < -3:  return "DECREASING"
    return "STABLE"


def classify_exit_blockage(exit_count: int, total_count: int) -> str:
    if total_count == 0: return "NONE"
    ratio = exit_count / total_count
    if ratio > 0.5:  return "HIGH"
    if ratio > 0.25: return "MEDIUM"
    return "LOW"


def classify_density(density_norm: float) -> str:
    if density_norm >= 0.80: return "CRITICAL"
    if density_norm >= 0.60: return "HIGH"
    if density_norm >= 0.30: return "MODERATE"
    return "LOW"


def build_behaviour_signals(avg_speed, count, net_flow, count_history, exit_count, density_norm) -> Dict[str, str]:
    return {
        "movement_intensity": classify_movement_intensity(avg_speed, count),
        "flow_convergence":   classify_flow_convergence(net_flow),
        "compression":        classify_compression(count_history),
        "exit_blockage":      classify_exit_blockage(exit_count, count),
        "density":            classify_density(density_norm),
    }
