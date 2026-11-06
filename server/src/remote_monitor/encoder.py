"""H.264 compression stage (FFmpeg via PyAV).

Design choices (see docs/ARCHITECTURE.md):

* H.264 is the only codec that every browser can hardware-decode through WebCodecs.
* ``tune=zerolatency`` disables B-frames, frame-threading look-ahead and mb-tree, so every
  input frame produces exactly one output packet immediately (no encoder delay).
* Annex-B byte stream with SPS/PPS repeated in front of every IDR frame: a viewer can join at
  any keyframe without out-of-band configuration.
* Keyframes are inserted every ``gop_seconds`` *and on demand* (new viewer, recovering
  viewer, recording start) so joining is fast without paying for a short GOP all the time.
* CBR-like rate control with a VBV buffer of ~0.5 s caps bitrate spikes, which keeps the
  socket send buffers - and therefore latency - small.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from fractions import Fraction

import av
import cv2
import numpy as np

log = logging.getLogger(__name__)

TIME_BASE = Fraction(1, 1_000_000)


@dataclass(slots=True)
class EncodedPacket:
    data: bytes
    ts_us: int
    keyframe: bool


def iter_nals(data: bytes):
    """Yield NAL units (without start code) of an Annex-B byte stream."""
    i, n = 0, len(data)
    starts = []
    while i < n - 3:
        if data[i] == 0 and data[i + 1] == 0 and (data[i + 2] == 1 or (data[i + 2] == 0 and data[i + 3] == 1)):
            sc = 3 if data[i + 2] == 1 else 4
            starts.append((i, i + sc))
            i += sc
        else:
            i += 1
    for k, (_, payload) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else n
        yield data[payload:end]


def avc_codec_string(data: bytes) -> str | None:
    """RFC 6381 codec string (``avc1.PPCCLL``) from the SPS in an Annex-B access unit."""
    for nal in iter_nals(data):
        if nal and nal[0] & 0x1F == 7 and len(nal) >= 4:
            return f"avc1.{nal[1]:02X}{nal[2]:02X}{nal[3]:02X}"
    return None


def _encoder_options(name: str, fps: int, bitrate: int, gop: int) -> dict[str, str]:
    if name == "libx264":
        vbv = bitrate // 1000
        return {
            "preset": "veryfast",
            "tune": "zerolatency",
            "profile": "main",
            "forced-idr": "1",
            "x264-params": f"scenecut=0:vbv-maxrate={vbv}:vbv-bufsize={vbv // 2}:repeat-headers=1",
        }
    if name == "h264_nvenc":
        return {"preset": "p2", "tune": "ull", "zerolatency": "1", "forced-idr": "1", "rc": "cbr"}
    if name == "h264_qsv":
        return {"preset": "veryfast", "forced_idr": "1", "look_ahead": "0"}
    if name == "h264_amf":
        return {"usage": "ultralowlatency", "rc": "cbr"}
    if name == "h264_mf":
        return {"rate_control": "cbr", "scenario": "display_remoting"}
    return {}


class H264Encoder:
    def __init__(self, name: str, width: int, height: int, fps: int, bitrate: int, gop_seconds: float):
        self.width, self.height, self.fps, self.bitrate = width, height, fps, bitrate
        self.gop = max(1, int(round(fps * gop_seconds)))
        self.codec_string: str | None = None
        self.ctx = self._open(name)
        self._t0: int | None = None
        self._pts_to_ts: dict[int, int] = {}

    def _open(self, name: str) -> av.VideoCodecContext:
        for candidate in dict.fromkeys([name, "libx264"]):
            try:
                ctx = av.CodecContext.create(candidate, "w")
                ctx.width, ctx.height = self.width, self.height
                ctx.pix_fmt = "yuv420p" if candidate != "h264_qsv" else "nv12"
                ctx.time_base = TIME_BASE
                ctx.framerate = Fraction(self.fps, 1)
                ctx.bit_rate = self.bitrate
                ctx.gop_size = self.gop
                ctx.max_b_frames = 0
                ctx.options = _encoder_options(candidate, self.fps, self.bitrate, self.gop)
                ctx.open()
                self.name = candidate
                log.info(
                    "encoder %s opened %dx%d@%d %d kbit/s gop=%d",
                    candidate,
                    self.width,
                    self.height,
                    self.fps,
                    self.bitrate // 1000,
                    self.gop,
                )
                return ctx
            except Exception as exc:
                log.warning("encoder %s unavailable: %s", candidate, exc)
        raise RuntimeError("no H.264 encoder available")

    def encode(self, img_bgr: np.ndarray, ts_us: int, force_keyframe: bool = False) -> list[EncodedPacket]:
        if self._t0 is None:
            self._t0 = ts_us
        if self.ctx.pix_fmt == "yuv420p":
            # OpenCV's SIMD colour conversion releases the GIL and is faster than swscale here
            yuv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2YUV_I420)
            frame = av.VideoFrame.from_ndarray(yuv, format="yuv420p")
        else:
            frame = av.VideoFrame.from_ndarray(img_bgr, format="bgr24").reformat(format=self.ctx.pix_fmt)
        pts = ts_us - self._t0
        frame.pts = pts
        frame.time_base = TIME_BASE
        if force_keyframe:
            frame.pict_type = av.video.frame.PictureType.I
        self._pts_to_ts[pts] = ts_us
        out = []
        for pkt in self.ctx.encode(frame):
            data = bytes(pkt)
            ts = self._pts_to_ts.pop(pkt.pts, ts_us) if pkt.pts is not None else ts_us
            if pkt.is_keyframe and self.codec_string is None:
                self.codec_string = avc_codec_string(data)
            out.append(EncodedPacket(data, ts, bool(pkt.is_keyframe)))
        if len(self._pts_to_ts) > 64:  # safety: never grow unbounded
            self._pts_to_ts.clear()
        return out

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.ctx.close()
