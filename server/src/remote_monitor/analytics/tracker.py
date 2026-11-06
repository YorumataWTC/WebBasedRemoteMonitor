"""Multi-object tracking of moving objects.

Detections (merged motion boxes) are associated to existing tracks frame by frame using a
greedy assignment on a cost that combines IoU and centroid distance. Each track has a
constant-velocity prediction (alpha-beta filter) so short occlusions / missed detections do
not break the identity. A track is *confirmed* after ``min_hits`` consecutive matches (hides
one-frame noise) and deleted after ``max_missed`` frames without a match. Each track keeps a
short trail of its centre positions which the viewer draws as a path.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

Box = tuple[float, float, float, float]  # normalised x, y, w, h


def iou(a: Box, b: Box) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def center(b: Box) -> tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3] / 2


@dataclass
class Track:
    id: int
    box: Box
    vx: float = 0.0
    vy: float = 0.0
    hits: int = 1
    missed: int = 0
    age: int = 1
    trail: list[tuple[float, float]] = field(default_factory=list)

    def predict(self) -> Box:
        x, y, w, h = self.box
        return (x + self.vx, y + self.vy, w, h)


class ObjectTracker:
    def __init__(self, max_missed: int = 8, min_hits: int = 3, max_dist: float = 0.25, trail_len: int = 40):
        self.max_missed, self.min_hits, self.max_dist, self.trail_len = max_missed, min_hits, max_dist, trail_len
        self.tracks: list[Track] = []
        self._ids = itertools.count(1)

    def reset(self) -> None:
        self.tracks.clear()

    def update(self, detections: list[Box]) -> list[Track]:
        # cost matrix (lower is better); greedy pick of best pairs
        pairs = []
        for ti, t in enumerate(self.tracks):
            p = t.predict()
            pc = center(p)
            for di, d in enumerate(detections):
                dc = center(d)
                dist = ((pc[0] - dc[0]) ** 2 + (pc[1] - dc[1]) ** 2) ** 0.5
                ov = iou(p, d)
                if ov <= 0 and dist > self.max_dist:
                    continue
                pairs.append((dist - ov, ti, di))
        pairs.sort()
        used_t, used_d = set(), set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            self._correct(self.tracks[ti], detections[di])

        for ti, t in enumerate(self.tracks):
            if ti not in used_t:
                t.missed += 1
                t.box = t.predict()
                t.age += 1
        for di, d in enumerate(detections):
            if di not in used_d:
                self.tracks.append(Track(next(self._ids), d, trail=[center(d)]))
        self.tracks = [t for t in self.tracks if t.missed <= self.max_missed]
        return [t for t in self.tracks if t.hits >= self.min_hits and t.missed == 0]

    def _correct(self, t: Track, d: Box, alpha: float = 0.75, beta: float = 0.3) -> None:
        p = t.predict()
        pc, dc = center(p), center(d)
        rx, ry = dc[0] - pc[0], dc[1] - pc[1]
        t.vx += beta * rx
        t.vy += beta * ry
        # alpha-beta filter: corrected centre and smoothed size
        cx, cy = pc[0] + alpha * rx, pc[1] + alpha * ry
        w, h = p[2] + alpha * (d[2] - p[2]), p[3] + alpha * (d[3] - p[3])
        t.box = (cx - w / 2, cy - h / 2, w, h)
        t.hits += 1
        t.missed = 0
        t.age += 1
        t.trail.append(center(t.box))
        if len(t.trail) > self.trail_len:
            del t.trail[0]
