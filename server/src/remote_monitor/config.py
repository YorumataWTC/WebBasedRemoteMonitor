"""Configuration.

Two layers:

* ``ServerConfig``   - static, process-wide options (ports, camera source, paths, password).
                        Read from CLI flags, environment variables (``RM_*``) or a ``.env`` file.
* ``RuntimeSettings`` - options a logged-in user may change live from the web UI (resolution,
                        bitrate, detectors, ...). Validated by pydantic, persisted as JSON so they
                        survive a restart, and broadcast to every connected viewer on change.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
SERVER_DIR = PACKAGE_DIR.parent.parent  # .../server


class ServerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RM_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    https_port: int = 8443
    https: bool = True  # also serve HTTPS (self-signed) - WebCodecs needs a secure context on LAN
    password: str | None = None  # None -> no login required
    secret: str | None = None  # HMAC key for session tokens (random if unset)

    source: str = "0"  # camera index, file/URL, or "synthetic"
    audio: bool = True
    audio_device: str | None = None  # sounddevice name/index; None = default input
    synthetic_audio: bool = False

    data_dir: Path = SERVER_DIR / "data"
    models_dir: Path = SERVER_DIR / "models"
    web_dir: Path = SERVER_DIR.parent / "web" / "dist"

    encoder: str = "libx264"  # libx264 | h264_nvenc | h264_qsv | h264_amf | h264_mf
    max_viewers: int = 0  # 0 = unlimited
    log_level: str = "info"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"


Resolution = Literal["640x360", "640x480", "1280x720", "1920x1080"]


class RuntimeSettings(BaseModel):
    camera_name: str = Field("Camera 1", max_length=40)
    resolution: Resolution = "1280x720"
    fps: int = Field(30, ge=5, le=60)
    bitrate_kbps: int = Field(2500, ge=200, le=12000)
    gop_seconds: float = Field(2.0, ge=0.5, le=10)
    mirror: bool = False
    flip: bool = False
    osd: bool = True

    motion_enabled: bool = True
    motion_sensitivity: int = Field(60, ge=1, le=100)
    motion_cooldown_s: float = Field(3.0, ge=0.5, le=60)
    tracking_enabled: bool = True
    auto_record: bool = False
    post_roll_s: float = Field(5.0, ge=1, le=60)

    faces_enabled: bool = True
    recognition_enabled: bool = True
    emotion_enabled: bool = True
    alert_unknown_faces: bool = False

    audio_enabled: bool = True
    sound_alarm_enabled: bool = True
    sound_threshold_db: float = Field(-25.0, ge=-60, le=0)

    @property
    def size(self) -> tuple[int, int]:
        w, h = self.resolution.split("x")
        return int(w), int(h)


class SettingsStore:
    """Thread-safe holder of RuntimeSettings with JSON persistence and change listeners."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._listeners: list[Callable[[RuntimeSettings, RuntimeSettings], None]] = []
        self._value = self._load()

    def _load(self) -> RuntimeSettings:
        try:
            return RuntimeSettings.model_validate_json(self._path.read_text("utf-8"))
        except FileNotFoundError:
            return RuntimeSettings()
        except Exception as exc:  # corrupt file -> defaults, never crash the server
            log.warning("ignoring invalid settings file %s: %s", self._path, exc)
            return RuntimeSettings()

    @property
    def value(self) -> RuntimeSettings:
        return self._value

    def subscribe(self, fn: Callable[[RuntimeSettings, RuntimeSettings], None]) -> None:
        self._listeners.append(fn)

    def update(self, patch: dict) -> RuntimeSettings:
        with self._lock:
            old = self._value
            new = RuntimeSettings.model_validate({**old.model_dump(), **patch})
            self._value = new
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(new.model_dump(), indent=2), "utf-8")
        for fn in self._listeners:
            try:
                fn(old, new)
            except Exception:
                log.exception("settings listener failed")
        return new
