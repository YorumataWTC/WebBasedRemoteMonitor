"""MonitorService - wires all pipeline stages together.

Threads (blocking, CPU-heavy work; OpenCV/FFmpeg release the GIL)::

    capture ──► FrameSlot ──┬──► encoder thread  (OSD, H.264, MJPEG, recorder) ──┐
                            └──► analytics thread (motion, tracking, faces)  ─────┤
    audio thread (mic, level, Opus) ─────────────────────────────────────────────┤
                                                                                  ▼
                                  asyncio event loop: Hub ──► one sender task per viewer

Threads hand results to the event loop with ``loop.call_soon_threadsafe`` - the only
cross-thread hand-off point - so the Hub itself needs no locks.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import __version__
from .analytics.engine import AnalyticsThread
from .analytics.faces import FaceAnalyzer, FaceGallery, FaceResult
from .analytics.motion import MotionResult
from .audio import AudioThread
from .auth import Auth
from .capture import CaptureThread, Frame, FrameSlot
from .clock import now_us
from .config import RuntimeSettings, ServerConfig, SettingsStore
from .encoder import EncodedPacket, H264Encoder
from .events import EventLog
from .hub import Hub
from .osd import draw_osd
from .recorder import Recorder
from .stats import Ewma, RateMeter

log = logging.getLogger(__name__)

MJPEG_FPS = 15
KEYFRAME_MIN_INTERVAL = 0.3  # s, rate-limit for on-demand IDR frames


class MonitorService:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.settings = SettingsStore(cfg.data_dir / "settings.json")
        self.settings.subscribe(self._on_settings)
        self.auth = Auth(cfg.password, cfg.secret)
        self.events = EventLog(cfg.data_dir / "events.db")
        self.gallery = FaceGallery(cfg.data_dir)
        self.faces = FaceAnalyzer(cfg.models_dir, self.gallery)
        self.recorder = Recorder(cfg.recordings_dir)
        self.recorder.on_finished = self._on_recording_finished
        self.hub = Hub(self.request_keyframe)
        self.slot = FrameSlot()

        self.loop: asyncio.AbstractEventLoop | None = None
        self.started_at = time.time()
        self._running = threading.Event()
        self._key_request = threading.Event()
        self._threads: list[threading.Thread] = []
        self.capture: CaptureThread | None = None
        self.analytics: AnalyticsThread | None = None
        self.audio: AudioThread | None = None
        self.encoder: H264Encoder | None = None

        # live state / metrics
        self.encode_rate = RateMeter()
        self.byte_rate = RateMeter()
        self.encode_ms = Ewma(0.1)
        self.audio_level = -120.0
        self.motion_active = False
        self._last_osd: tuple[np.ndarray, int] | None = None
        self._auto_stop_at: float | None = None
        self._face_seen: dict[str, float] = {}
        self._faces_present = 0
        self._last_sound_alarm = 0.0
        self._last_level_sent = 0.0
        self._stats_task: asyncio.Task | None = None
        self._config_sent = False

    # ================================================================== lifecycle
    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._running.set()
        s = self.settings.value
        w, h = s.size
        self.capture = CaptureThread(self.cfg.source, self.slot, w, h, s.fps, s.mirror, s.flip)
        enc = threading.Thread(target=self._encode_loop, name="encoder", daemon=True)
        self.analytics = AnalyticsThread(self.slot, self.faces, lambda: self.settings.value, self._on_analytics)
        self._threads = [self.capture, enc, self.analytics]
        if self.cfg.audio:
            self.audio = AudioThread(
                self._on_audio_packet, self._on_audio_level, self.cfg.audio_device, self.cfg.synthetic_audio
            )
            self._threads.append(self.audio)
            self.hub.set_audio_config({"t": "audio_config", "codec": "opus", "sampleRate": 48000, "channels": 1})
        for t in self._threads:
            t.start()
        self._stats_task = asyncio.create_task(self._stats_loop())
        self.events.add("system", f"Server started (v{__version__}, source {self.cfg.source})")
        log.info("monitor service started")

    async def stop(self) -> None:
        self._running.clear()
        if self._stats_task:
            self._stats_task.cancel()
        for t in self._threads:
            stop = getattr(t, "stop", None)
            if stop:
                stop()
        for t in self._threads:
            t.join(timeout=3)
        self.recorder.stop()
        self.recorder.wait_idle()
        if self.encoder:
            self.encoder.close()
        self.events.close()
        log.info("monitor service stopped")

    # ================================================================== helpers
    def _post(self, fn, *args) -> None:
        loop = self.loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(fn, *args)
            except RuntimeError:  # loop shutting down
                pass

    def request_keyframe(self) -> None:
        self._key_request.set()

    def _emit_event(
        self, kind: str, message: str, level: str = "info", snapshot: str | None = None, data: dict | None = None
    ) -> dict:
        ev = self.events.add(kind, message, level, snapshot, data)
        self._post(self.hub.broadcast, {"t": "event", "event": ev})
        return ev

    def _save_snapshot(self, prefix: str, boxes: list | None = None) -> str | None:
        latest = self._last_osd
        if latest is None:
            return None
        img = latest[0].copy()
        h, w = img.shape[:2]
        for b in boxes or []:
            x, y, bw, bh = (np.array(b) * [w, h, w, h]).astype(int)
            cv2.rectangle(img, (x, y), (x + bw, y + bh), (0, 165, 255), max(2, w // 400))
        scale = 640 / w
        if scale < 1:
            img = cv2.resize(img, (640, int(h * scale)), interpolation=cv2.INTER_AREA)
        name = f"{prefix}_{latest[1]}.jpg"
        cv2.imwrite(str(self.cfg.snapshots_dir / name), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return name

    def snapshot_jpeg(self, quality: int = 92) -> bytes | None:
        latest = self._last_osd
        if latest is None:
            return None
        ok, buf = cv2.imencode(".jpg", latest[0], [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def latest_raw_frame(self) -> Frame | None:
        return self.slot.latest()

    # ================================================================== encoder thread
    def _encode_loop(self) -> None:
        seq = 0
        last_forced = 0.0
        last_mjpeg = 0.0
        params: tuple | None = None
        while self._running.is_set():
            frame = self.slot.wait_newer(seq, 0.5)
            if frame is None:
                continue
            seq = frame.seq
            s = self.settings.value
            h, w = frame.image.shape[:2]
            want = (w, h, s.fps, s.bitrate_kbps, s.gop_seconds, self.cfg.encoder)
            try:
                if params != want:
                    self._reopen_encoder(w, h, s)
                    params = want
                img = frame.image.copy()
                if s.osd:
                    draw_osd(img, s.camera_name, frame.ts_us, self.recorder.active, self.motion_active)
                self._last_osd = (img, frame.ts_us)

                force = False
                now = time.perf_counter()
                if self._key_request.is_set() and now - last_forced >= KEYFRAME_MIN_INTERVAL:
                    self._key_request.clear()
                    force, last_forced = True, now
                t0 = time.perf_counter()
                packets = self.encoder.encode(img, frame.ts_us, force)
                self.encode_ms.add((time.perf_counter() - t0) * 1000)
                for pkt in packets:
                    self._on_packet(pkt)

                if self.hub.mjpeg_clients and now - last_mjpeg >= 1 / MJPEG_FPS:
                    last_mjpeg = now
                    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ok:
                        self._post(self.hub.publish_jpeg, buf.tobytes())
            except Exception:
                log.exception("encoder stage failed - re-opening encoder")
                params = None
                time.sleep(0.2)

    def _reopen_encoder(self, w: int, h: int, s: RuntimeSettings) -> None:
        if self.encoder:
            self.encoder.close()
        self.encoder = H264Encoder(self.cfg.encoder, w, h, s.fps, s.bitrate_kbps * 1000, s.gop_seconds)
        self._config_sent = False
        self.recorder.stream_changed()
        self.hub.max_video_queue = max(8, s.fps)
        self.request_keyframe()

    def _on_packet(self, pkt: EncodedPacket) -> None:
        enc = self.encoder
        if pkt.keyframe and not self._config_sent and enc.codec_string:
            self._config_sent = True
            cfg = {
                "t": "video_config",
                "codec": enc.codec_string,
                "width": enc.width,
                "height": enc.height,
                "fps": enc.fps,
                "bitrate": enc.bitrate,
                "encoder": enc.name,
            }
            self._post(self.hub.set_video_config, cfg)
        if not self._config_sent:
            return  # never send frames before the decoder configuration
        self.encode_rate.tick()
        self.byte_rate.tick(len(pkt.data))
        self.recorder.on_video(pkt)
        if pkt.keyframe:
            self.recorder.resume_if_needed()
        self._post(self.hub.publish_video, pkt)

    # ================================================================== analytics callbacks
    def _on_analytics(
        self, frame: Frame, motion: MotionResult | None, tracks: list, faces: list[FaceResult] | None
    ) -> None:
        s = self.settings.value
        meta = {"t": "meta", "ts": frame.ts_us}
        if motion is not None:
            meta["motion"] = {
                "active": motion.active and s.motion_enabled,
                "score": round(motion.score, 4),
                "boxes": [[round(v, 4) for v in b] for b in motion.boxes] if s.motion_enabled else [],
            }
        if tracks:
            meta["tracks"] = [
                {
                    "id": t.id,
                    "box": [round(v, 4) for v in t.box],
                    "trail": [[round(x, 4), round(y, 4)] for x, y in t.trail[-25:]],
                }
                for t in tracks
            ]
        if faces is not None:
            meta["faces"] = [f.to_json() for f in faces]
        self._post(self.hub.publish_meta, meta)

        if motion is not None and s.motion_enabled:
            self._handle_motion(motion, s)
        elif self.motion_active:
            self.motion_active = False
        if faces is not None:
            self._handle_faces(faces, s)
        if self._auto_stop_at and time.monotonic() >= self._auto_stop_at and not self.motion_active:
            self._auto_stop_at = None
            status = self.recorder.status()
            if status and not status["manual"]:
                self.recorder.stop()
                self._post(self.hub.broadcast, {"t": "recording", "status": None})

    def _handle_motion(self, motion: MotionResult, s: RuntimeSettings) -> None:
        if motion.started:
            self.motion_active = True
            self._auto_stop_at = None
            snap = self._save_snapshot("motion", motion.boxes)
            self._emit_event(
                "motion",
                "Motion detected",
                "alarm",
                snap,
                {"score": round(motion.score, 4), "objects": len(motion.boxes)},
            )
            if s.auto_record and not self.recorder.active:
                self._start_recording("motion", manual=False)
        elif motion.ended:
            self.motion_active = False
            self._emit_event("motion_end", "Motion ended", "info")
            if self.recorder.active:
                self._auto_stop_at = time.monotonic() + s.post_roll_s

    def _handle_faces(self, faces: list[FaceResult], s: RuntimeSettings) -> None:
        now = time.monotonic()
        if faces and self._faces_present == 0:
            names = sorted({f.name for f in faces if f.name})
            msg = f"Face detected: {', '.join(names)}" if names else f"{len(faces)} face(s) detected"
            self._emit_event(
                "face",
                msg,
                "info",
                self._save_snapshot("face", [f.box for f in faces]),
                {"count": len(faces), "names": names},
            )
        self._faces_present = len(faces)
        for f in faces:
            key = f.name or "__unknown__"
            if now - self._face_seen.get(key, -1e9) < (60 if f.name else 20):
                continue
            if f.name:
                self._face_seen[key] = now
                self._emit_event(
                    "person",
                    f"Recognized {f.name}",
                    "info",
                    self._save_snapshot("person", [f.box]),
                    {"name": f.name, "similarity": f.similarity, "emotion": f.emotion},
                )
            elif s.alert_unknown_faces and s.recognition_enabled and self.gallery.entries:
                self._face_seen[key] = now
                self._emit_event(
                    "unknown_person",
                    "Unknown person detected",
                    "alarm",
                    self._save_snapshot("unknown", [f.box]),
                    {"emotion": f.emotion},
                )

    # ================================================================== audio callbacks
    def _on_audio_packet(self, ts_us: int, payload: bytes) -> None:
        if not self.settings.value.audio_enabled:
            return
        self.recorder.on_audio(ts_us, payload)
        self._post(self.hub.publish_audio, ts_us, payload)

    def _on_audio_level(self, db: float) -> None:
        self.audio_level = db
        s = self.settings.value
        now = time.monotonic()
        if now - self._last_level_sent >= 0.1:
            self._last_level_sent = now
            self._post(self.hub.broadcast, {"t": "level", "db": round(db, 1)})
        if (
            s.sound_alarm_enabled
            and s.audio_enabled
            and db > s.sound_threshold_db
            and now - self._last_sound_alarm > 10
        ):
            self._last_sound_alarm = now
            self._emit_event(
                "sound",
                f"Loud noise detected ({db:.0f} dBFS)",
                "alarm",
                self._save_snapshot("sound"),
                {"db": round(db, 1)},
            )

    # ================================================================== recording
    def _start_recording(self, reason: str, manual: bool) -> dict | None:
        status = self.recorder.start(reason, manual)
        if status is None:
            return None
        if self._last_osd is not None:
            p = self.cfg.recordings_dir / Path(status["file"]).with_suffix(".jpg").name
            small = cv2.resize(
                self._last_osd[0], (320, int(320 * self._last_osd[0].shape[0] / self._last_osd[0].shape[1]))
            )
            cv2.imwrite(str(p), small)
        self._emit_event("recording", f"Recording started ({reason})", "info", data={"file": status["file"]})
        self._post(self.hub.broadcast, {"t": "recording", "status": status})
        return status

    def start_recording(self) -> dict | None:
        self.request_keyframe()
        return self._start_recording("manual", manual=True)

    def stop_recording(self) -> dict | None:
        info = self.recorder.stop()
        self._auto_stop_at = None
        if info:
            self._post(self.hub.broadcast, {"t": "recording", "status": None})
        return info

    def _on_recording_finished(self, info: dict) -> None:
        self._emit_event(
            "recording_saved", f"Recording saved: {info['file']} ({info['duration']:.0f}s)", "info", data=info
        )

    # ================================================================== settings
    def _on_settings(self, old: RuntimeSettings, new: RuntimeSettings) -> None:
        if self.capture:
            w, h = new.size
            self.capture.configure(w, h, new.fps, new.mirror, new.flip)
        if self.analytics:
            self.analytics.motion.set_sensitivity(new.motion_sensitivity)
        self.recorder.audio_enabled = new.audio_enabled
        if old.auto_record and not new.auto_record and self.recorder.active:
            status = self.recorder.status()
            if status and not status["manual"]:
                self.stop_recording()
        self._post(self.hub.broadcast, {"t": "settings", "settings": new.model_dump()})

    # ================================================================== status
    def features(self) -> dict:
        return {
            "audio": self.cfg.audio,
            "faces": self.faces.available,
            "auth": self.auth.enabled,
            "mjpeg": True,
            "recording": True,
        }

    def stats(self) -> dict:
        cap = self.capture
        enc = self.encoder
        return {
            "t": "stats",
            "serverTime": now_us(),
            "uptime": round(time.time() - self.started_at),
            "captureFps": round(cap.rate.rate, 1) if cap else 0,
            "encodeFps": round(self.encode_rate.rate, 1),
            "analyticsFps": round(self.analytics.rate.rate, 1) if self.analytics else 0,
            "bitrateKbps": round(self.byte_rate.rate * 8 / 1000),
            "encodeMs": round(self.encode_ms.value, 1),
            "analyticsMs": round(self.analytics.cost_ms.value, 1) if self.analytics else 0,
            "viewers": len(self.hub.viewers),
            "mjpegViewers": self.hub.mjpeg_clients,
            "audioLevel": round(self.audio_level, 1),
            "motion": self.motion_active,
            "recording": self.recorder.status(),
            "encoder": enc.name if enc else None,
            "resolution": f"{enc.width}x{enc.height}" if enc else None,
            "source": cap.source_name if cap else None,
            "captureStatus": cap.status if cap else "stopped",
            "audioStatus": self.audio.status if self.audio else "disabled",
        }

    async def _stats_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                self.hub.broadcast(self.stats())
            except Exception:
                log.exception("stats broadcast failed")
