"""Motion detection in a static background (Gaussian-mixture background subtraction).

Steps per analysed frame (on a 320-px-wide grey image for speed):

1. MOG2 background model -> foreground mask (pixels that do not fit the learned background).
2. Morphological opening (remove sensor noise) + dilation (join fragments of one object).
3. Contours -> bounding boxes, small blobs filtered by a minimum area derived from sensitivity.
4. Alarm state machine with hysteresis: ``active`` turns on as soon as a relevant blob is
   seen and turns off only after ``cooldown`` seconds without motion (no flickering alarms).
5. Global illumination changes (light switched on, camera auto-exposure) make most of the
   image foreground; such frames are treated as a scene change, the model is re-learned and
   no alarm is raised.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class MotionResult:
    active: bool
    started: bool  # rising edge this frame
    ended: bool  # falling edge this frame
    score: float  # fraction of the image that is foreground (0..1)
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)  # normalised x,y,w,h


class MotionDetector:
    WARMUP_FRAMES = 25

    def __init__(self, sensitivity: int = 60, cooldown_s: float = 3.0):
        self.cooldown_s = cooldown_s
        self.set_sensitivity(sensitivity)
        self.reset()

    def set_sensitivity(self, sensitivity: int) -> None:
        s = min(max(sensitivity, 1), 100) / 100
        # higher sensitivity -> smaller objects trigger, smaller luminance difference needed
        self.min_area = float(np.interp(s, [0, 1], [0.02, 0.0004]))
        self.var_threshold = float(np.interp(s, [0, 1], [60, 12]))
        if hasattr(self, "_bg"):
            self._bg.setVarThreshold(self.var_threshold)

    def reset(self) -> None:
        self._bg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=self.var_threshold, detectShadows=False)
        self._frames = 0
        self._active = False
        self._last_motion = 0.0
        self._kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))

    def process(self, small_gray: np.ndarray, now: float | None = None) -> MotionResult:
        now = time.monotonic() if now is None else now
        h, w = small_gray.shape[:2]
        self._frames += 1
        warm = self._frames <= self.WARMUP_FRAMES
        fg = self._bg.apply(small_gray, learningRate=0.05 if warm else -1)
        score = float(np.count_nonzero(fg)) / fg.size
        boxes: list[tuple[float, float, float, float]] = []
        detected = False
        if warm:
            pass
        elif score > 0.6:  # global illumination change -> relearn quickly
            self._bg = cv2.createBackgroundSubtractorMOG2(
                history=300, varThreshold=self.var_threshold, detectShadows=False
            )
            self._bg.apply(small_gray, learningRate=1.0)
            self._frames = 1
        else:
            mask = cv2.morphologyEx(fg, cv2.MORPH_OPEN, self._kernel_open)
            mask = cv2.dilate(mask, self._kernel_dilate, iterations=2)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            min_px = self.min_area * w * h
            for c in contours:
                if cv2.contourArea(c) < min_px:
                    continue
                x, y, bw, bh = cv2.boundingRect(c)
                boxes.append((x / w, y / h, bw / w, bh / h))
            detected = bool(boxes)

        started = ended = False
        if detected:
            self._last_motion = now
            if not self._active:
                self._active = started = True
        elif self._active and now - self._last_motion > self.cooldown_s:
            self._active = False
            ended = True
        return MotionResult(self._active, started, ended, score, merge_boxes(boxes))


def merge_boxes(boxes: list[tuple[float, float, float, float]], gap: float = 0.02):
    """Merge overlapping / nearly touching boxes (one person often yields several blobs)."""
    rects = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out: list[list[float]] = []
        while rects:
            a = rects.pop()
            i = 0
            while i < len(rects):
                b = rects[i]
                if (
                    a[0] - gap < b[0] + b[2]
                    and b[0] - gap < a[0] + a[2]
                    and a[1] - gap < b[1] + b[3]
                    and b[1] - gap < a[1] + a[3]
                ):
                    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
                    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
                    a = [x0, y0, x1 - x0, y1 - y0]
                    rects.pop(i)
                    changed = True
                else:
                    i += 1
            out.append(a)
        rects = out
    return [tuple(r) for r in rects]
