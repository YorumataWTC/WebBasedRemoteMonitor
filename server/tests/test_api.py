"""End-to-end tests: real service (synthetic camera + synthetic audio) behind the FastAPI app."""

import json
import time

import av
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from remote_monitor.app import create_app
from remote_monitor.config import ServerConfig
from remote_monitor.protocol import TYPE_AUDIO, TYPE_VIDEO, unpack_media
from remote_monitor.service import MonitorService


def make_client(tmp_path, password=None):
    cfg = ServerConfig(source="synthetic", data_dir=tmp_path, audio=True, synthetic_audio=True,
                       password=password, https=False, web_dir=tmp_path / "noweb")
    service = MonitorService(cfg)
    service.settings.update({"resolution": "640x360", "fps": 30})
    return TestClient(create_app(service)), service


def receive_until(ws, predicate, limit=400):
    for _ in range(limit):
        msg = ws.receive()
        data = msg.get("bytes") if msg.get("bytes") is not None else msg.get("text")
        if predicate(data):
            return data
    raise AssertionError("expected message not received")


def test_stream_end_to_end(tmp_path):
    client, service = make_client(tmp_path)
    with client:
        assert client.get("/api/health").json()["ok"]
        with client.websocket_connect("/ws?audio=1") as ws:
            hello = json.loads(ws.receive_text())
            assert hello["t"] == "hello" and hello["features"]["recording"]
            cfg = json.loads(receive_until(ws, lambda d: isinstance(d, str) and '"video_config"' in d))
            assert cfg["codec"].startswith("avc1.") and cfg["width"] == 640
            # first video frame delivered to a new viewer must be a keyframe
            first = receive_until(ws, lambda d: isinstance(d, bytes) and d[0] == TYPE_VIDEO)
            kind, key, ts, payload = unpack_media(first)
            assert key and abs(time.time() * 1e6 - ts) < 2e6
            dec = av.CodecContext.create("h264", "r")
            frames = list(dec.decode(av.Packet(payload)))
            got, audio = len(frames), 0
            t_end = time.time() + 2.5
            while time.time() < t_end:
                d = ws.receive()
                b = d.get("bytes")
                if b:
                    k, _, _, p = unpack_media(b)
                    if k == TYPE_VIDEO:
                        got += len(dec.decode(av.Packet(p)))
                    elif k == TYPE_AUDIO:
                        audio += 1
            assert frames and frames[0].width == 640 and frames[0].height == 360
            assert got >= 40, f"only {got} frames in 2.5 s"
            assert audio >= 50  # 20 ms Opus packets
            # clock sync
            ws.send_text(json.dumps({"t": "ping", "c": 123}))
            pong = json.loads(receive_until(ws, lambda d: isinstance(d, str) and '"pong"' in d))
            assert pong["c"] == 123 and pong["s"] > 0
            stats = service.stats()
            assert stats["viewers"] == 1 and stats["encodeFps"] > 20


def test_settings_snapshot_mjpeg_and_recording(tmp_path):
    client, service = make_client(tmp_path)
    with client:
        time.sleep(1.5)
        r = client.patch("/api/settings", json={"bitrate_kbps": 1200, "camera_name": "Lab"})
        assert r.status_code == 200 and r.json()["camera_name"] == "Lab"
        assert client.patch("/api/settings", json={"fps": 999}).status_code == 422
        snap = client.get("/api/snapshot.jpg")
        assert snap.status_code == 200 and snap.content[:2] == b"\xff\xd8"

        with client.stream("GET", "/api/stream.mjpg") as s:
            assert s.headers["content-type"].startswith("multipart/x-mixed-replace")
            buf = b""
            for chunk in s.iter_bytes():
                buf += chunk
                if buf.count(b"\xff\xd9") >= 2:
                    break
            assert b"--frame" in buf and b"Content-Type: image/jpeg" in buf

        time.sleep(1.0)
        start = client.post("/api/recordings/start")
        assert start.status_code == 200, start.text
        time.sleep(2.0)
        stop = client.post("/api/recordings/stop").json()
        assert stop["duration"] > 1.5
        service.recorder.wait_idle()
        items = client.get("/api/recordings").json()["items"]
        assert items and items[0]["file"] == stop["file"]
        mp4 = client.get(f"/api/recordings/{stop['file']}", headers={"Range": "bytes=0-99"})
        assert mp4.status_code == 206 and len(mp4.content) == 100
        assert client.get("/api/recordings/..%2Fsettings.json").status_code == 404
        kinds = {e["kind"] for e in client.get("/api/events").json()}
        assert {"system", "recording", "recording_saved"} <= kinds


def test_motion_event_from_synthetic_intruder(tmp_path):
    client, service = make_client(tmp_path)
    with client:
        deadline = time.time() + 14  # intruder appears every 12 s
        while time.time() < deadline:
            if any(e["kind"] == "motion" for e in client.get("/api/events").json()):
                break
            time.sleep(0.5)
        events = [e for e in client.get("/api/events").json() if e["kind"] == "motion"]
        assert events and events[0]["level"] == "alarm" and events[0]["snapshot"]
        assert client.get(f"/api/snapshots/{events[0]['snapshot']}").status_code == 200


@pytest.mark.parametrize("pw", ["s3cret"])
def test_authentication(tmp_path, pw):
    client, _ = make_client(tmp_path, password=pw)
    with client:
        assert client.get("/api/session").json() == {"authRequired": True, "authenticated": False,
                                                     "version": client.get("/api/health").json()["version"]}
        assert client.get("/api/status").status_code == 401
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws") as ws:
            ws.receive_text()
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"password": pw}).status_code == 200
        assert client.get("/api/status").status_code == 200
        with client.websocket_connect("/ws") as ws:
            assert json.loads(ws.receive_text())["t"] == "hello"
        client.post("/api/logout")
        client.cookies.clear()
        assert client.get("/api/status").status_code == 401
