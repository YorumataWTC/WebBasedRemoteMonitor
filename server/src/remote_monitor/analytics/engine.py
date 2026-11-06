"""Analytics thread.

Runs decoupled from the encoder: it always grabs the newest captured frame, so heavy models
can never reduce the streamed frame rate - at worst the overlay updates less often. Results
are published as a ``meta`` message (normalised coordinates) that every viewer draws as a
vector overlay on top of the decoded video, which keeps the video itself clean and lets each
viewer toggle overlays independently.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import cv2

from ..capture import Frame, FrameSlot
from ..config import RuntimeSettings
from ..stats import Ewma, RateMeter
from .faces import FaceAnalyzer, FaceResult
from .motion import MotionDetector, MotionResult
from .tracker import ObjectTracker

log = logging.getLogger(__name__)

MOTION_WIDTH = 320


class AnalyticsThread(threading.Thread):
    def __init__(
        self,
        slot: FrameSlot,
        faces: FaceAnalyzer,
        get_settings: Callable[[], RuntimeSettings],
        on_result: Callable[[Frame, MotionResult | None, list, list[FaceResult] | None], None],
        max_hz: float = 15.0,
        face_every: int = 2,
    ):
        super().__init__(name="analytics", daemon=True)
        self.slot, self.faces, self.get_settings, self.on_result = slot, faces, get_settings, on_result
        s = get_settings()
        self.motion = MotionDetector(s.motion_sensitivity, s.motion_cooldown_s)
        self.tracker = ObjectTracker()
        self.max_hz, self.face_every = max_hz, face_every
        self.rate = RateMeter()
        self.cost_ms = Ewma(0.1)
        self._halt = threading.Event()
        self._shape: tuple[int, int] | None = None
        self._last_faces: list[FaceResult] = []

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        seq, n = 0, 0
        interval = 1 / self.max_hz
        while not self._halt.is_set():
            frame = self.slot.wait_newer(seq, 0.5)
            if frame is None:
                continue
            seq = frame.seq
            t0 = time.perf_counter()
            try:
                self._process(frame, n)
            except Exception:
                log.exception("analytics failed on frame %d", frame.seq)
            n += 1
            dt = time.perf_counter() - t0
            self.cost_ms.add(dt * 1000)
            self.rate.tick()
            if dt < interval:
                self._halt.wait(interval - dt)

    def _process(self, frame: Frame, n: int) -> None:
        s = self.get_settings()
        img = frame.image
        h, w = img.shape[:2]
        if self._shape != (w, h):  # resolution changed -> background model is invalid
            self._shape = (w, h)
            self.motion.reset()
            self.tracker.reset()
        self.motion.cooldown_s = s.motion_cooldown_s

        motion = None
        tracks: list = []
        if s.motion_enabled or s.tracking_enabled:
            small = cv2.resize(img, (MOTION_WIDTH, int(h * MOTION_WIDTH / w)), interpolation=cv2.INTER_AREA)
            gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
            motion = self.motion.process(gray)
            if s.tracking_enabled:
                tracks = self.tracker.update(motion.boxes)

        faces: list[FaceResult] | None = None
        if s.faces_enabled and self.faces.detector is not None:
            if n % self.face_every == 0:
                self._last_faces = self.faces.analyze(img, recognize=s.recognition_enabled, emotion=s.emotion_enabled)
            faces = self._last_faces
        self.on_result(frame, motion, tracks, faces)
