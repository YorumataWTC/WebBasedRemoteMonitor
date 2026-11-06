"""Unit tests for the media / analytics building blocks."""

import asyncio
import io
import time

import av
import numpy as np
import pytest

from remote_monitor.analytics.motion import MotionDetector, merge_boxes
from remote_monitor.analytics.tracker import ObjectTracker, iou
from remote_monitor.audio import BLOCK, OpusEncoder, dbfs
from remote_monitor.auth import Auth
from remote_monitor.capture import SyntheticSource
from remote_monitor.config import RuntimeSettings, SettingsStore
from remote_monitor.encoder import EncodedPacket, H264Encoder, avc_codec_string, iter_nals
from remote_monitor.hub import Hub, Viewer
from remote_monitor.protocol import HEADER_SIZE, TYPE_AUDIO, TYPE_VIDEO, pack_media, unpack_media
from remote_monitor.recorder import Recorder


def test_protocol_roundtrip():
    msg = pack_media(TYPE_VIDEO, 1_793_000_000_123_456, b"\x00\x00\x00\x01e", keyframe=True)
    assert len(msg) == HEADER_SIZE + 5
    kind, key, ts, payload = unpack_media(msg)
    assert (kind, key, ts, payload) == (TYPE_VIDEO, True, 1_793_000_000_123_456, b"\x00\x00\x00\x01e")
    assert unpack_media(pack_media(TYPE_AUDIO, 5, b"x"))[1] is False


def _frames(n, w=640, h=360):
    src = SyntheticSource()
    src.open(w, h, 1000)
    return [src.read() for _ in range(n)]


def test_encoder_annexb_keyframes_and_codec_string():
    enc = H264Encoder("libx264", 640, 360, 30, 1_000_000, gop_seconds=1.0)
    pkts = []
    for i, img in enumerate(_frames(45)):
        pkts += enc.encode(img, 1_000_000 + i * 33_333, force_keyframe=(i == 10))
    assert len(pkts) == 45, "zerolatency: one packet per frame, no encoder delay"
    keys = [i for i, p in enumerate(pkts) if p.keyframe]
    assert keys[:3] == [0, 10, 40]  # first, forced, GOP restarts after forced IDR
    assert enc.codec_string and enc.codec_string.startswith("avc1.4D")  # main profile
    nal_types = [n[0] & 0x1F for n in iter_nals(pkts[10].data)]
    assert 7 in nal_types and 8 in nal_types and 5 in nal_types  # SPS, PPS, IDR in-band
    assert pkts[5].ts_us == 1_000_000 + 5 * 33_333
    # stream decodes
    dec = av.CodecContext.create("h264", "r")
    decoded = sum(len(dec.decode(av.Packet(p.data))) for p in pkts)
    assert decoded >= 44


def test_avc_codec_string_parse():
    sps = b"\x00\x00\x00\x01\x67\x42\xc0\x1f\xda"
    assert avc_codec_string(sps) == "avc1.42C01F"
    assert avc_codec_string(b"\x00\x00\x01\x65\x88") is None


def test_opus_encoder_and_level():
    enc = OpusEncoder()
    t = np.arange(BLOCK * 10) / 48_000
    sig = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    packets = []
    for k in range(10):
        packets += enc.encode(sig[k * BLOCK:(k + 1) * BLOCK])
    assert packets and all(len(p) < 400 for p in packets)  # ~32 kbit/s -> ~80 bytes / 20 ms
    assert -10 < dbfs(sig) < -5  # sine of amplitude 0.5 is -9 dBFS
    assert dbfs(np.zeros(BLOCK, np.float32)) < -100


def test_motion_detector_detects_intruder_and_hysteresis():
    md = MotionDetector(sensitivity=60, cooldown_s=1.0)
    bg = np.full((180, 320), 100, np.uint8)
    t = 0.0
    for _ in range(40):
        r = md.process(bg + np.random.randint(0, 3, bg.shape, np.uint8), t)
        t += 0.1
        assert not r.active
    img = bg.copy()
    img[60:140, 100:140] = 220  # object enters
    r = md.process(img, t)
    assert r.started and r.active and r.boxes
    x, y, w, h = r.boxes[0]
    assert 0.25 < x < 0.35 and 0.25 < y < 0.4
    t += 0.5
    r = md.process(bg, t)  # object gone, still within cooldown
    assert r.active and not r.ended
    t += 1.5
    r = md.process(bg, t)
    assert r.ended and not r.active


def test_motion_ignores_global_illumination_change():
    md = MotionDetector()
    bg = np.full((180, 320), 80, np.uint8)
    for i in range(40):
        md.process(bg, i * 0.1)
    r = md.process(np.full((180, 320), 200, np.uint8), 5.0)
    assert not r.active


def test_merge_boxes():
    merged = merge_boxes([(0.1, 0.1, 0.1, 0.1), (0.19, 0.12, 0.1, 0.1), (0.7, 0.7, 0.05, 0.05)])
    assert len(merged) == 2


def test_tracker_keeps_identity():
    tr = ObjectTracker(min_hits=2)
    ids = set()
    for k in range(20):
        dets = [(0.05 + k * 0.02, 0.4, 0.1, 0.3), (0.8 - k * 0.01, 0.1, 0.08, 0.1)]
        for t in tr.update(dets):
            ids.add(t.id)
    assert ids == {1, 2}
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == 1.0
    # disappears -> removed after max_missed
    for _ in range(tr.max_missed + 2):
        tr.update([])
    assert tr.tracks == []


def test_auth_tokens():
    a = Auth("secret-pw", "key")
    assert a.enabled and a.check_password("secret-pw") and not a.check_password("nope")
    tok = a.issue()
    assert a.verify(tok)
    assert not a.verify(tok[:-2] + "xx")
    assert not Auth("secret-pw", "other-key").verify(tok)
    assert Auth(None).verify(None)  # auth disabled


def test_settings_store_validation_and_persistence(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    seen = []
    store.subscribe(lambda old, new: seen.append((old.fps, new.fps)))
    store.update({"fps": 15})
    assert seen == [(30, 15)]
    with pytest.raises(ValueError):
        store.update({"fps": 500})
    with pytest.raises(ValueError):
        store.update({"resolution": "123x45"})
    assert SettingsStore(tmp_path / "s.json").value.fps == 15
    assert RuntimeSettings(resolution="1920x1080").size == (1920, 1080)


async def test_hub_congestion_drops_until_keyframe():
    requests = []
    hub = Hub(lambda: requests.append(1))
    hub.max_video_queue = 5
    v = Viewer("test")
    hub.add(v, {"t": "hello"})
    ctrl = len(v.queue)
    hub.publish_video(EncodedPacket(b"P", 1, False))  # waits for keyframe
    assert len(v.queue) == ctrl
    hub.publish_video(EncodedPacket(b"K", 2, True))
    for i in range(4):
        hub.publish_video(EncodedPacket(b"P", 3 + i, False))
    assert v.queue.video == 5
    n_req = len(requests)
    hub.publish_video(EncodedPacket(b"P", 9, False))  # overflow
    assert v.need_key and v.queue.video == 0 and len(requests) > n_req and v.dropped_frames >= 5
    hub.publish_video(EncodedPacket(b"P", 10, False))
    assert v.queue.video == 0
    hub.publish_video(EncodedPacket(b"K", 11, True))
    assert v.queue.video == 1 and not v.need_key
    # control messages survive congestion
    hub.broadcast({"t": "event"})
    items = [await asyncio.wait_for(v.queue.get(), 1) for _ in range(len(v.queue))]
    assert any(isinstance(i.data, str) and '"event"' in i.data for i in items)


def test_recorder_preroll_mp4_with_audio(tmp_path):
    rec = Recorder(tmp_path)
    enc = H264Encoder("libx264", 640, 360, 30, 800_000, gop_seconds=1.0)
    aenc = OpusEncoder()
    t0 = 1_800_000_000_000_000
    frames = _frames(90)
    tone = (0.1 * np.sin(np.arange(BLOCK) / 5)).astype(np.float32)
    for i, img in enumerate(frames):
        ts = t0 + i * 33_333
        for p in enc.encode(img, ts):
            rec.on_video(p)
        if i == 45:
            assert rec.start("test", manual=True) is not None
        if i % 3 == 0 and i > 45:
            for _ in range(2):
                for ap in aenc.encode(tone):
                    rec.on_audio(ts, ap)
    info = rec.stop()
    rec.wait_idle()
    assert info and info["frames"] >= 45 + 15  # pre-roll from previous keyframe (frame 30)
    mp4 = tmp_path / info["file"]
    with av.open(str(mp4)) as c:
        assert c.streams.video[0].codec_context.name == "h264"
        assert len(c.streams.audio) == 1
        n = sum(1 for _ in c.decode(video=0))
    assert n == info["frames"]
    assert rec.list()[0]["file"] == info["file"]
    assert rec.delete(info["file"]) and not mp4.exists()


def test_synthetic_source_moves():
    a, b = _frames(2)[0], None
    time.sleep(0.2)
    src = SyntheticSource()
    src.open(320, 180, 1000)
    b = src.read()
    assert a.shape == (360, 640, 3) and b.shape == (180, 320, 3)
    assert isinstance(io.BytesIO, type)
