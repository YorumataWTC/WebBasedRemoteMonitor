"""Wire protocol between server and viewers (one WebSocket per viewer: ``/ws``).

Binary messages carry media, text messages carry JSON control/metadata.

Binary media message (big endian)::

    0       1       2               4                               12
    +-------+-------+---------------+-------------------------------+---------------
    | type  | flags |   reserved    |   timestamp (uint64, us UTC)  |  payload ...
    +-------+-------+---------------+-------------------------------+---------------
    type : 1 = video (H.264 Annex-B access unit), 2 = audio (one Opus packet, 20 ms)
    flags: bit0 = keyframe (IDR)

JSON messages always have a ``t`` (type) field. Server -> client: ``hello``, ``video_config``,
``audio_config``, ``meta``, ``stats``, ``event``, ``settings``, ``pong``, ``recording``.
Client -> server: ``ping``, ``keyframe``, ``audio``.
"""

from __future__ import annotations

import struct

HEADER = struct.Struct(">BBHQ")
HEADER_SIZE = HEADER.size  # 12

TYPE_VIDEO = 1
TYPE_AUDIO = 2
FLAG_KEY = 0x01


def pack_media(kind: int, ts_us: int, payload: bytes, keyframe: bool = False) -> bytes:
    return HEADER.pack(kind, FLAG_KEY if keyframe else 0, 0, ts_us) + payload


def unpack_media(msg: bytes) -> tuple[int, bool, int, bytes]:
    kind, flags, _, ts = HEADER.unpack_from(msg)
    return kind, bool(flags & FLAG_KEY), ts, msg[HEADER_SIZE:]
