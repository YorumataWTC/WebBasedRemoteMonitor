"""Video recording with timing information.

The recorder stores the *already compressed* H.264 and Opus packets in an MP4 container
(remux only, zero extra CPU for encoding). Because the OSD time stamp is burned into the
video, every recording carries its timing information visibly; the MP4 additionally stores
the wall-clock start time in a JSON sidecar.

Pre-roll: the recorder permanently keeps the packets since the second-to-last keyframe in a
ring buffer. A motion-triggered recording therefore starts *before* the motion happened
(up to two GOPs earlier) and always begins with an IDR frame, so the file is decodable.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import av

from .encoder import EncodedPacket

log = logging.getLogger(__name__)

US = Fraction(1, 1_000_000)
MAX_SEGMENT_S = 30 * 60


@dataclass
class _Session:
    container: av.container.OutputContainer
    vstream: av.stream.Stream
    astream: av.stream.Stream | None
    path: Path
    reason: str
    t0_us: int
    started_wall: float
    last_ts: int
    last_audio_ts: int = -1
    frames: int = 0
    manual: bool = False


class Recorder:
    def __init__(self, directory: Path):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._preroll: deque[EncodedPacket] = deque()
        self._keyframes = 0
        self._session: _Session | None = None
        self.audio_enabled = True
        self.on_finished = None  # callback(dict)
        self._resume: tuple[str, bool] | None = None

    # ------------------------------------------------------------------ state
    @property
    def active(self) -> bool:
        return self._session is not None

    def status(self) -> dict | None:
        s = self._session
        if not s:
            return None
        return {
            "file": s.path.name,
            "reason": s.reason,
            "startedAt": s.started_wall,
            "duration": (s.last_ts - s.t0_us) / 1e6,
            "manual": s.manual,
        }

    # ------------------------------------------------------------------ packets
    def on_video(self, pkt: EncodedPacket) -> None:
        with self._lock:
            if pkt.keyframe:
                self._keyframes += 1
                if self._keyframes > 2:  # keep at most the last two GOPs: drop the oldest one
                    self._preroll.popleft()
                    while self._preroll and not self._preroll[0].keyframe:
                        self._preroll.popleft()
                    self._keyframes -= 1
            self._preroll.append(pkt)
            s = self._session
            if s is None:
                return
            if pkt.keyframe and (pkt.ts_us - s.t0_us) / 1e6 > MAX_SEGMENT_S:  # rotate long files
                reason, manual = s.reason, s.manual
                self._close_locked()
                self._open_locked(reason, manual, [pkt])
                return
            self._write_video(s, pkt)

    def on_audio(self, ts_us: int, payload: bytes) -> None:
        with self._lock:
            s = self._session
            if s is None or s.astream is None or ts_us < s.t0_us or ts_us <= s.last_audio_ts:
                return
            p = av.Packet(payload)
            p.stream = s.astream
            p.time_base = US
            p.pts = p.dts = ts_us - s.t0_us
            p.duration = 20_000
            s.last_audio_ts = ts_us
            try:
                s.container.mux(p)
            except Exception as exc:
                log.debug("audio mux failed: %s", exc)

    def _write_video(self, s: _Session, pkt: EncodedPacket) -> None:
        if pkt.ts_us <= s.last_ts and s.frames:
            return
        p = av.Packet(pkt.data)
        p.stream = s.vstream
        p.time_base = US
        p.pts = p.dts = pkt.ts_us - s.t0_us
        p.is_keyframe = pkt.keyframe
        s.container.mux(p)
        s.last_ts = pkt.ts_us
        s.frames += 1

    # ------------------------------------------------------------------ control
    def start(self, reason: str = "manual", manual: bool = True) -> dict | None:
        with self._lock:
            if self._session is not None:
                if manual:
                    self._session.manual = True
                return self.status()
            buf = list(self._preroll)
            first_key = next((i for i, p in enumerate(buf) if p.keyframe), None)
            if first_key is None:
                return None
            self._open_locked(reason, manual, buf[first_key:])
            return self.status()

    def _open_locked(self, reason: str, manual: bool, packets: list[EncodedPacket]) -> None:
        first = packets[0]
        stamp = datetime.fromtimestamp(first.ts_us / 1e6).strftime("%Y%m%d-%H%M%S")
        path = self.dir / f"{stamp}_{reason}.mp4"
        # Build the stream description from the in-band SPS/PPS of the first IDR frame.
        template = av.open(io.BytesIO(first.data), format="h264").streams.video[0]
        container = av.open(str(path), "w", format="mp4", options={"movflags": "+faststart"})
        vstream = container.add_stream_from_template(template)
        vstream.time_base = Fraction(1, 90_000)
        astream = None
        if self.audio_enabled:
            try:
                astream = container.add_stream("libopus", rate=48_000, layout="mono")
                astream.bit_rate = 32_000
            except Exception as exc:
                log.warning("recording without audio: %s", exc)
        s = _Session(
            container, vstream, astream, path, reason, first.ts_us, first.ts_us / 1e6, first.ts_us, manual=manual
        )
        self._session = s
        for pkt in packets:
            self._write_video(s, pkt)
        log.info("recording started: %s (pre-roll %.1fs)", path.name, (packets[-1].ts_us - first.ts_us) / 1e6)

    def stop(self) -> dict | None:
        with self._lock:
            return self._close_locked()

    def stream_changed(self) -> None:
        """Encoder re-opened with new parameters: old packets cannot be mixed with new ones."""
        with self._lock:
            session = self._session
            if session is not None:
                self._close_locked()
            self._preroll.clear()
            self._keyframes = 0
            self._resume = (session.reason, session.manual) if session else None

    def resume_if_needed(self) -> None:
        """Continue a recording that was split by a stream change (called after next IDR)."""
        resume = getattr(self, "_resume", None)
        if resume and self._keyframes:
            self._resume = None
            self.start(resume[0], resume[1])

    def _close_locked(self) -> dict | None:
        s = self._session
        if s is None:
            return None
        self._session = None
        info = {
            "file": s.path.name,
            "reason": s.reason,
            "startedAt": s.started_wall,
            "duration": round((s.last_ts - s.t0_us) / 1e6, 2),
            "frames": s.frames,
        }

        def finish() -> None:
            try:
                s.container.close()  # +faststart rewrites the moov atom to the front
                info["size"] = s.path.stat().st_size
                s.path.with_suffix(".json").write_text(json.dumps(info), "utf-8")
                log.info("recording finished: %s (%.1fs)", s.path.name, info["duration"])
                if self.on_finished:
                    self.on_finished(info)
            except Exception:
                log.exception("closing recording %s failed", s.path)

        threading.Thread(target=finish, name="rec-close", daemon=True).start()
        return info

    # ------------------------------------------------------------------ library
    def list(self) -> list[dict]:
        out = []
        for meta in self.dir.glob("*.json"):
            try:
                info = json.loads(meta.read_text("utf-8"))
            except Exception:
                continue
            mp4 = meta.with_suffix(".mp4")
            if mp4.exists():
                info["thumb"] = meta.with_suffix(".jpg").exists()
                out.append(info)
        return sorted(out, key=lambda i: i["startedAt"], reverse=True)

    def path_of(self, name: str) -> Path | None:
        p = (self.dir / name).resolve()
        if p.parent != self.dir.resolve() or not p.exists():
            return None
        return p

    def delete(self, name: str) -> bool:
        p = self.path_of(name)
        if p is None or p.suffix != ".mp4" or (self._session and self._session.path == p):
            return False
        for suffix in (".mp4", ".json", ".jpg"):
            p.with_suffix(suffix).unlink(missing_ok=True)
        return True

    def wait_idle(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline and any(t.name == "rec-close" for t in threading.enumerate()):
            time.sleep(0.05)
