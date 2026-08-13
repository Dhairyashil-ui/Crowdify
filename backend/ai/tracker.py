"""
ai/tracker.py — ByteTrack session tracker interface.
The actual tracker instances live on CrowdSession objects in server.py.
This module provides helper types and documents the tracking interface.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class TrackedPerson:
    """Represents a single tracked person in the current frame."""
    track_id:   int
    bbox:       Tuple[float, float, float, float]   # x1, y1, x2, y2
    confidence: float = 1.0
    age:        int   = 0          # frames since first seen


@dataclass
class TrackMemory:
    """Rolling position history for velocity/flow calculation."""
    positions: List[Tuple[float, float]] = field(default_factory=list)
    max_len:   int = 30

    def push(self, cx: float, cy: float):
        self.positions.append((cx, cy))
        if len(self.positions) > self.max_len:
            self.positions.pop(0)

    @property
    def velocity(self) -> Tuple[float, float]:
        """Mean velocity (vx, vy) over last N positions."""
        if len(self.positions) < 2:
            return (0.0, 0.0)
        dx = [self.positions[i][0] - self.positions[i-1][0] for i in range(1, len(self.positions))]
        dy = [self.positions[i][1] - self.positions[i-1][1] for i in range(1, len(self.positions))]
        return (sum(dx) / len(dx), sum(dy) / len(dy))

    @property
    def speed(self) -> float:
        vx, vy = self.velocity
        return (vx**2 + vy**2) ** 0.5
