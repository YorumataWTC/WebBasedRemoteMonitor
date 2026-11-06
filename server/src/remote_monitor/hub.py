"""Fan-out hub: encode once, deliver to N viewers.

Each viewer owns a ``SendQueue`` drained by its own asyncio task, so one slow viewer can never
block the encoder or the other viewers. Congestion control per viewer:

* If more than ~1 s of video is waiting for a viewer, all queued video is discarded and the
  viewer is marked ``need_key``. Nothing is sent to it until the next keyframe, which is
  requested immediately. This bounds latency instead of letting it grow without limit (the
  classic problem of TCP-based live streaming) and guarantees the decoder never sees a frame
  whose reference is missing.
* High-rate metadata (detections) is skipped while a viewer's queue is backed up.
* Control messages (events, settings) are never dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from .encoder import EncodedPacket
from .protocol import TYPE_AUDIO, TYPE_VIDEO, pack_media

log = logging.getLogger(__name__)

_ids = itertools.count(1)


@dataclass(slots=True)
class _Item:
    data: bytes | str
    kind: str  # "video" | "audio" | "meta" | "ctrl"


class SendQueue:
    def __init__(self) -> None:
        self._items: deque[_Item] = deque()
        self._event = asyncio.Event()
        self.video = 0
        self.audio = 0

    def __len__(self) -> int:
        return len(self._items)

    def put(self, item: _Item) -> None:
        self._items.append(item)
        if item.kind == "video":
            self.video += 1
        elif item.kind == "audio":
            self.audio += 1
        self._event.set()

    def put_front(self, item: _Item) -> None:
        """Bypass queued media (used for clock-sync pongs, which must not be delayed)."""
        self._items.appendleft(item)
        self._event.set()

    async def get(self) -> _Item:
        while not self._items:
            self._event.clear()
            await self._event.wait()
        item = self._items.popleft()
        if item.kind == "video":
            self.video -= 1
        elif item.kind == "audio":
            self.audio -= 1
        return item

    def purge(self, kinds: set[str]) -> int:
        before = len(self._items)
        self._items = deque(i for i in self._items if i.kind not in kinds)
        if "video" in kinds:
            self.video = 0
        if "audio" in kinds:
            self.audio = 0
        return before - len(self._items)


@dataclass
class Viewer:
    address: str
    user_agent: str = ""
    id: int = field(default_factory=lambda: next(_ids))
    connected_at: float = field(default_factory=time.time)
    queue: SendQueue = field(default_factory=SendQueue)
    want_audio: bool = False
    need_key: bool = True
    sent_bytes: int = 0
    dropped_frames: int = 0
    rtt_ms: float | None = None

    def info(self) -> dict:
        return {
            "id": self.id,
            "address": self.address,
            "userAgent": self.user_agent,
            "connectedAt": self.connected_at,
            "sentBytes": self.sent_bytes,
            "droppedFrames": self.dropped_frames,
            "audio": self.want_audio,
            "rttMs": self.rtt_ms,
        }


class Hub:
    def __init__(self, request_keyframe: Callable[[], None]):
        self.viewers: dict[int, Viewer] = {}
        self._request_keyframe = request_keyframe
        self.video_config: dict | None = None
        self.audio_config: dict | None = None
        self.max_video_queue = 30
        self._mjpeg: set[asyncio.Queue[bytes]] = set()

    # -- membership ---------------------------------------------------------------------
    def add(self, viewer: Viewer, hello: dict) -> None:
        self.viewers[viewer.id] = viewer
        viewer.queue.put(_Item(json.dumps(hello), "ctrl"))
        if self.video_config:
            viewer.queue.put(_Item(json.dumps(self.video_config), "ctrl"))
        if self.audio_config:
            viewer.queue.put(_Item(json.dumps(self.audio_config), "ctrl"))
        self._request_keyframe()
        log.info("viewer %d connected from %s (%d total)", viewer.id, viewer.address, len(self.viewers))

    def remove(self, viewer: Viewer) -> None:
        if self.viewers.pop(viewer.id, None) is not None:
            log.info("viewer %d disconnected (%d total)", viewer.id, len(self.viewers))

    # -- publishing (always called on the event loop thread) ---------------------------
    def set_video_config(self, cfg: dict) -> None:
        self.video_config = cfg
        self.broadcast(cfg)
        for v in self.viewers.values():  # decoder must be reconfigured -> wait for next IDR
            v.need_key = True
        self._request_keyframe()

    def set_audio_config(self, cfg: dict | None) -> None:
        self.audio_config = cfg
        if cfg:
            self.broadcast(cfg)

    def publish_video(self, pkt: EncodedPacket) -> None:
        msg = pack_media(TYPE_VIDEO, pkt.ts_us, pkt.data, pkt.keyframe)
        need_key = False
        for v in self.viewers.values():
            if v.need_key:
                if not pkt.keyframe:
                    continue
                v.need_key = False
            if v.queue.video >= self.max_video_queue:
                # congested viewer: drop everything queued, resume at next keyframe
                v.dropped_frames += v.queue.purge({"video", "meta"}) + 1
                v.need_key = True
                need_key = True
                continue
            v.queue.put(_Item(msg, "video"))
        if need_key:
            self._request_keyframe()

    def publish_audio(self, ts_us: int, payload: bytes) -> None:
        msg = pack_media(TYPE_AUDIO, ts_us, payload)
        for v in self.viewers.values():
            if v.want_audio and v.queue.audio < 50:
                v.queue.put(_Item(msg, "audio"))

    def publish_meta(self, meta: dict) -> None:
        text = json.dumps(meta, separators=(",", ":"))
        for v in self.viewers.values():
            if len(v.queue) < 8:
                v.queue.put(_Item(text, "meta"))

    def broadcast(self, obj: dict) -> None:
        text = json.dumps(obj, separators=(",", ":"))
        for v in self.viewers.values():
            v.queue.put(_Item(text, "ctrl"))

    # -- MJPEG fallback ---------------------------------------------------------------
    @property
    def mjpeg_clients(self) -> int:
        return len(self._mjpeg)

    def mjpeg_subscribe(self) -> asyncio.Queue[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self._mjpeg.add(q)
        return q

    def mjpeg_unsubscribe(self, q: asyncio.Queue[bytes]) -> None:
        self._mjpeg.discard(q)

    def publish_jpeg(self, jpeg: bytes) -> None:
        for q in self._mjpeg:
            if q.full():  # always deliver the newest picture
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(jpeg)
