"""Audio capture + Opus compression.

Microphone samples (48 kHz, mono, float32) arrive from PortAudio in 20 ms blocks. Each block
is (1) measured (RMS level in dBFS for the VU meter and the loud-noise alarm) and
(2) compressed with Opus (32 kbit/s, ``application=lowdelay``), the codec designed for
interactive real-time audio and natively decodable by WebCodecs ``AudioDecoder``.
One Opus packet = 20 ms = 960 samples; it is timestamped with the same wall clock as video
so the viewer can keep both streams aligned.
"""

from __future__ import annotations

import contextlib
import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from fractions import Fraction

import av
import numpy as np

from .clock import now_us

log = logging.getLogger(__name__)

SAMPLE_RATE = 48_000
BLOCK = 960  # 20 ms
BITRATE = 32_000


def dbfs(block: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64)))) if block.size else 0.0
    return 20 * math.log10(max(rms, 1e-6))


class OpusEncoder:
    def __init__(self) -> None:
        ctx = av.CodecContext.create("libopus", "w")
        ctx.sample_rate = SAMPLE_RATE
        ctx.layout = "mono"
        ctx.format = "flt"
        ctx.bit_rate = BITRATE
        ctx.time_base = Fraction(1, SAMPLE_RATE)
        ctx.options = {"application": "lowdelay", "frame_duration": "20"}
        ctx.open()
        self.ctx = ctx
        self._pts = 0

    def encode(self, block: np.ndarray) -> list[bytes]:
        frame = av.AudioFrame.from_ndarray(block.reshape(1, -1).astype(np.float32), format="flt", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = Fraction(1, SAMPLE_RATE)
        self._pts += block.size
        return [bytes(p) for p in self.ctx.encode(frame)]


class AudioThread(threading.Thread):
    def __init__(
        self,
        on_packet: Callable[[int, bytes], None],
        on_level: Callable[[float], None],
        device: str | None = None,
        synthetic: bool = False,
    ):
        super().__init__(name="audio", daemon=True)
        self.on_packet, self.on_level = on_packet, on_level
        self.device = int(device) if device and device.isdigit() else device
        self.synthetic = synthetic
        self.status = "starting"
        self.level_db = -120.0
        self._q: queue.Queue[tuple[np.ndarray, int]] = queue.Queue(maxsize=50)
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def _callback(self, indata, frames, time_info, status) -> None:  # PortAudio thread
        with contextlib.suppress(queue.Full):
            self._q.put_nowait((indata[:, 0].copy(), now_us() - frames * 1_000_000 // SAMPLE_RATE))

    def run(self) -> None:
        try:
            encoder = OpusEncoder()
        except Exception as exc:
            self.status = f"error: {exc}"
            log.warning("opus encoder unavailable: %s", exc)
            return
        backoff = 1.0
        while not self._halt.is_set():
            try:
                if self.synthetic:
                    self._run_synthetic(encoder)
                else:
                    self._run_device(encoder)
                backoff = 1.0
            except Exception as exc:
                self.status = f"error: {exc}"
                log.warning("audio input error (%s), retrying in %.0fs", exc, backoff)
                self._halt.wait(backoff)
                backoff = min(backoff * 2, 30)

    def _run_device(self, encoder: OpusEncoder) -> None:
        import sounddevice as sd

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCK,
            device=self.device,
            callback=self._callback,
            latency="low",
        ):
            self.status = "ok"
            log.info("audio capture started (device=%s)", self.device or "default")
            while not self._halt.is_set():
                try:
                    block, ts = self._q.get(timeout=1.0)
                except queue.Empty as exc:
                    raise RuntimeError("audio device stopped delivering samples") from exc
                self._handle(encoder, block, ts)

    def _run_synthetic(self, encoder: OpusEncoder) -> None:
        """440 Hz beeps every 2 s plus faint noise - for tests and camera-less demos."""
        self.status = "ok (synthetic)"
        n = 0
        t_next = time.perf_counter()
        while not self._halt.is_set():
            t = (np.arange(BLOCK) + n * BLOCK) / SAMPLE_RATE
            beep = (t % 2.0) < 0.25
            block = (0.2 * np.sin(2 * np.pi * 440 * t) * beep + 0.003 * np.random.randn(BLOCK)).astype(np.float32)
            n += 1
            self._handle(encoder, block, now_us())
            t_next += BLOCK / SAMPLE_RATE
            self._halt.wait(max(0.0, t_next - time.perf_counter()))

    def _handle(self, encoder: OpusEncoder, block: np.ndarray, ts: int) -> None:
        self.level_db = dbfs(block)
        self.on_level(self.level_db)
        for pkt in encoder.encode(block):
            self.on_packet(ts, pkt)
