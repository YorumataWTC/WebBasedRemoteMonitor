"""Video capture stage.

A dedicated thread pulls frames from the source as fast as the source delivers them and
publishes only the *latest* frame into a ``FrameSlot``. Consumers (encoder, analytics) always
take the newest frame; if a consumer is slower than the camera, old frames are dropped
instead of queued. This keeps the end-to-end latency bounded - the most important property
of a live monitoring system.
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .clock import now_us
from .stats import RateMeter

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Frame:
    image: np.ndarray  # BGR, HxWx3, uint8
    ts_us: int  # wall-clock capture time
    seq: int


class FrameSlot:
    """Single-element mailbox: writers overwrite, readers wait for a newer sequence number."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: Frame | None = None

    def put(self, frame: Frame) -> None:
        with self._cond:
            self._frame = frame
            self._cond.notify_all()

    def latest(self) -> Frame | None:
        return self._frame

    def wait_newer(self, seq: int, timeout: float = 0.5) -> Frame | None:
        with self._cond:
            if not self._cond.wait_for(lambda: self._frame is not None and self._frame.seq > seq, timeout):
                return None
            return self._frame


# --------------------------------------------------------------------------- sources


class VideoSource:
    name = "source"

    def open(self, width: int, height: int, fps: int) -> None: ...
    def read(self) -> np.ndarray | None: ...
    def close(self) -> None: ...


class CameraSource(VideoSource):
    """Webcam via OpenCV. DirectShow on Windows (fast open, supports MJPG at 720p/1080p)."""

    def __init__(self, index: int):
        self.index = index
        self.name = f"camera:{index}"
        self.cap: cv2.VideoCapture | None = None

    def open(self, width: int, height: int, fps: int) -> None:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open camera {self.index}")
        # MJPG lets USB-2 webcams deliver HD at full frame rate (raw YUY2 is bandwidth-limited).
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap = cap
        log.info(
            "camera %s opened: %dx%d @ %.0f fps (requested %dx%d @ %d)",
            self.index,
            cap.get(cv2.CAP_PROP_FRAME_WIDTH),
            cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
            cap.get(cv2.CAP_PROP_FPS),
            width,
            height,
            fps,
        )

    def read(self) -> np.ndarray | None:
        if self.cap is None:
            return None
        ok, img = self.cap.read()
        return img if ok else None

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class FileSource(VideoSource):
    """Loops a video file (or reads a network stream URL) paced at its native frame rate."""

    def __init__(self, path: str):
        self.path = path
        self.name = f"file:{Path(path).name}"
        self.cap: cv2.VideoCapture | None = None
        self._interval = 1 / 30
        self._next = 0.0

    def open(self, width: int, height: int, fps: int) -> None:
        self.cap = cv2.VideoCapture(self.path)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open {self.path}")
        src_fps = self.cap.get(cv2.CAP_PROP_FPS) or fps
        self._interval = 1 / min(fps, src_fps if src_fps > 0 else fps)
        self._next = time.perf_counter()

    def read(self) -> np.ndarray | None:
        assert self.cap is not None
        delay = self._next - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        self._next = max(self._next + self._interval, time.perf_counter() - self._interval)
        ok, img = self.cap.read()
        if not ok:  # end of file -> loop
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, img = self.cap.read()
        return img if ok else None

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class SyntheticSource(VideoSource):
    """Animated test pattern with a periodically appearing 'intruder'.

    Used for automated tests and for demos on machines without a camera. The intruder makes
    motion detection and object tracking observable in a deterministic way.
    """

    name = "synthetic"

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self._next = 0.0
        self._interval = 1 / 30
        self._bg: np.ndarray | None = None

    def open(self, width: int, height: int, fps: int) -> None:
        self._w, self._h = width, height
        self._interval = 1 / fps
        self._next = time.perf_counter()
        # static "room": vertical gradient + floor + some furniture blocks
        y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
        bg = np.empty((height, width, 3), np.uint8)
        bg[..., 0] = (90 + 60 * y).astype(np.uint8)
        bg[..., 1] = (80 + 50 * y).astype(np.uint8)
        bg[..., 2] = (70 + 40 * y).astype(np.uint8)
        cv2.rectangle(bg, (0, int(height * 0.75)), (width, height), (60, 70, 80), -1)
        cv2.rectangle(
            bg, (int(width * 0.06), int(height * 0.3)), (int(width * 0.22), int(height * 0.75)), (40, 60, 110), -1
        )
        cv2.rectangle(
            bg, (int(width * 0.7), int(height * 0.45)), (int(width * 0.95), int(height * 0.75)), (100, 90, 60), -1
        )
        self._bg = bg
        # pre-computed sensor-noise layers (cheap per frame, keeps the encoder realistic)
        rng = np.random.default_rng(1)
        self._noise = [
            cv2.resize(rng.integers(0, 6, (height // 8, width // 8, 3), np.uint8), (width, height),
                       interpolation=cv2.INTER_NEAREST)
            for _ in range(8)
        ]
        self._n = 0

    def read(self) -> np.ndarray | None:
        delay = self._next - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        self._next = max(self._next + self._interval, time.perf_counter() - self._interval)
        assert self._bg is not None
        self._n += 1
        img = cv2.add(self._bg, self._noise[self._n % len(self._noise)])
        t = time.perf_counter() - self._t0
        w, h = self._w, self._h
        # intruder walks across the room for 6 s out of every 12 s
        phase = t % 12
        if phase < 6:
            x = int(-0.1 * w + (phase / 6) * 1.2 * w)
            bob = int(math.sin(t * 8) * h * 0.01)
            bw, bh = int(w * 0.07), int(h * 0.38)
            top = int(h * 0.37) + bob
            cv2.rectangle(img, (x, top), (x + bw, top + bh), (30, 30, 200), -1)
            cv2.circle(img, (x + bw // 2, top - int(bh * 0.12)), int(bw * 0.45), (150, 180, 220), -1)
        return img

    def close(self) -> None:
        pass


def make_source(spec: str) -> VideoSource:
    if spec == "synthetic":
        return SyntheticSource()
    if spec.isdigit():
        return CameraSource(int(spec))
    return FileSource(spec)


# --------------------------------------------------------------------------- thread


class CaptureThread(threading.Thread):
    """Owns the source; re-opens it on errors (unplugged camera) and on resolution changes."""

    def __init__(
        self,
        source_spec: str,
        slot: FrameSlot,
        width: int,
        height: int,
        fps: int,
        mirror: bool = False,
        flip: bool = False,
    ):
        super().__init__(name="capture", daemon=True)
        self.source_spec = source_spec
        self.slot = slot
        self.width, self.height, self.fps = width, height, fps
        self.mirror, self.flip = mirror, flip
        self.rate = RateMeter()
        self.status = "starting"
        self.source_name = source_spec
        self._halt = threading.Event()
        self._reopen = threading.Event()
        self._seq = 0

    def configure(self, width: int, height: int, fps: int, mirror: bool, flip: bool) -> None:
        self.mirror, self.flip = mirror, flip
        if (width, height, fps) != (self.width, self.height, self.fps):
            self.width, self.height, self.fps = width, height, fps
            self._reopen.set()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        backoff = 0.5
        while not self._halt.is_set():
            source = make_source(self.source_spec)
            self.source_name = source.name
            try:
                source.open(self.width, self.height, self.fps)
                self.status = "ok"
                backoff = 0.5
                self._reopen.clear()
                self._loop(source)
            except Exception as exc:
                self.status = f"error: {exc}"
                log.warning("capture error (%s), retrying in %.1fs", exc, backoff)
                self._halt.wait(backoff)
                backoff = min(backoff * 2, 5.0)
            finally:
                source.close()

    def _loop(self, source: VideoSource) -> None:
        failures = 0
        min_interval = 1 / self.fps
        last = 0.0
        while not self._halt.is_set() and not self._reopen.is_set():
            img = source.read()
            if img is None:
                failures += 1
                if failures > 30:
                    raise RuntimeError("source stopped delivering frames")
                time.sleep(0.01)
                continue
            failures = 0
            # Frame-rate limiter (camera may deliver more than the configured fps).
            now = time.perf_counter()
            if now - last < min_interval * 0.9:
                continue
            last = now
            if img.shape[1] != self.width or img.shape[0] != self.height:
                img = cv2.resize(img, (self.width, self.height), interpolation=cv2.INTER_AREA)
            if self.mirror and self.flip:
                img = cv2.flip(img, -1)
            elif self.mirror:
                img = cv2.flip(img, 1)
            elif self.flip:
                img = cv2.flip(img, 0)
            self._seq += 1
            self.rate.tick()
            self.slot.put(Frame(img, now_us(), self._seq))
