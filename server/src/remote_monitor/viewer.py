"""Native reception software.

A standalone desktop client speaking the same WebSocket protocol as the browser: H.264 is
decoded with FFmpeg (PyAV), Opus audio is decoded and played via PortAudio, detections from
the ``meta`` messages are drawn on top. Keys: q/Esc quit, s save snapshot, o toggle overlay,
f toggle fullscreen.
"""

from __future__ import annotations

import contextlib
import http.cookiejar
import json
import queue
import ssl
import threading
import time
import urllib.request
from datetime import datetime

import av
import cv2
import numpy as np

from .protocol import TYPE_AUDIO, TYPE_VIDEO, unpack_media

WINDOW = "Remote Monitor - native viewer"


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # server uses a self-signed certificate on the LAN
    return ctx


def _login(base: str, password: str | None) -> str | None:
    if not password:
        return None
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=_ssl_ctx())
    )
    req = urllib.request.Request(
        f"{base}/api/login", json.dumps({"password": password}).encode(), {"Content-Type": "application/json"}
    )
    opener.open(req, timeout=10).read()
    for c in jar:
        if c.name == "rm_session":
            return c.value
    raise RuntimeError("login failed")


class _State:
    def __init__(self) -> None:
        self.frame: np.ndarray | None = None
        self.meta: dict = {}
        self.stats: dict = {}
        self.lock = threading.Lock()
        self.running = True
        self.fps = 0.0
        self.latency_ms: float | None = None
        self.offset_us = 0.0
        self.status = "connecting"


def _receiver(url: str, token: str | None, want_audio: bool, st: _State, audio_q: queue.Queue) -> None:
    from websockets.sync.client import connect

    ws_url = url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/ws"
    if want_audio:
        ws_url += "?audio=1"
    headers = {"Cookie": f"rm_session={token}"} if token else None
    while st.running:
        try:
            ssl_ctx = _ssl_ctx() if ws_url.startswith("wss") else None
            with connect(ws_url, additional_headers=headers, ssl=ssl_ctx, max_size=None, open_timeout=10) as ws:
                st.status = "connected"
                decoder = None
                adec = None
                waiting_key = True
                frames, t_fps = 0, time.perf_counter()
                ws.send(json.dumps({"t": "ping", "c": time.time_ns() // 1000}))
                last_ping = time.monotonic()
                while st.running:
                    if time.monotonic() - last_ping > 2:
                        ws.send(json.dumps({"t": "ping", "c": time.time_ns() // 1000}))
                        last_ping = time.monotonic()
                    try:
                        msg = ws.recv(timeout=1.0)
                    except TimeoutError:
                        continue
                    if isinstance(msg, str):
                        m = json.loads(msg)
                        t = m.get("t")
                        if t == "video_config":
                            decoder = av.CodecContext.create("h264", "r")
                            waiting_key = True
                        elif t == "audio_config":
                            adec = av.CodecContext.create("libopus", "r")
                            adec.sample_rate, adec.layout = 48000, "mono"
                        elif t == "meta":
                            st.meta = m
                        elif t == "stats":
                            st.stats = m
                        elif t == "pong":
                            now = time.time_ns() // 1000
                            rtt = now - m["c"]
                            st.offset_us = m["s"] - (m["c"] + rtt / 2)
                        continue
                    kind, key, ts, payload = unpack_media(msg)
                    if kind == TYPE_VIDEO and decoder is not None:
                        if waiting_key and not key:
                            continue
                        waiting_key = False
                        try:
                            for fr in decoder.decode(av.Packet(payload)):
                                img = fr.to_ndarray(format="bgr24")
                                with st.lock:
                                    st.frame = img
                                frames += 1
                                st.latency_ms = (time.time_ns() // 1000 + st.offset_us - ts) / 1000
                        except av.error.FFmpegError:
                            waiting_key = True
                            ws.send(json.dumps({"t": "keyframe"}))
                        now = time.perf_counter()
                        if now - t_fps >= 1:
                            st.fps, frames, t_fps = frames / (now - t_fps), 0, now
                    elif kind == TYPE_AUDIO and adec is not None:
                        for af in adec.decode(av.Packet(payload)):
                            pcm = af.to_ndarray().reshape(-1).astype(np.float32)
                            with contextlib.suppress(queue.Full):
                                audio_q.put_nowait(pcm)
        except Exception as exc:
            st.status = f"reconnecting ({exc.__class__.__name__})"
            time.sleep(1.0)


def _audio_player(audio_q: queue.Queue, st: _State) -> None:
    import sounddevice as sd

    with sd.OutputStream(samplerate=48000, channels=1, dtype="float32", latency="low") as out:
        while st.running:
            try:
                out.write(audio_q.get(timeout=0.5))
            except queue.Empty:
                continue


def _draw_overlay(img: np.ndarray, meta: dict) -> None:
    h, w = img.shape[:2]

    def rect(b, color, label=None):
        x, y, bw, bh = int(b[0] * w), int(b[1] * h), int(b[2] * w), int(b[3] * h)
        cv2.rectangle(img, (x, y), (x + bw, y + bh), color, 2)
        if label:
            cv2.putText(img, label, (x, max(15, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)

    for t in meta.get("tracks", []):
        rect(t["box"], (0, 200, 255), f"#{t['id']}")
        pts = np.array([[int(x * w), int(y * h)] for x, y in t.get("trail", [])], np.int32)
        if len(pts) > 1:
            cv2.polylines(img, [pts], False, (0, 200, 255), 2, cv2.LINE_AA)
    for f in meta.get("faces", []):
        label = f.get("name") or "face"
        if f.get("emotion"):
            label += f" ({f['emotion']})"
        rect(f["box"], (80, 220, 80) if f.get("name") else (255, 180, 0), label)


def run_viewer(url: str, password: str | None, want_audio: bool, overlay: bool) -> None:
    url = url.rstrip("/")
    token = _login(url, password)
    st = _State()
    audio_q: queue.Queue = queue.Queue(maxsize=25)
    threading.Thread(target=_receiver, args=(url, token, want_audio, st, audio_q), daemon=True).start()
    if want_audio:
        threading.Thread(target=_audio_player, args=(audio_q, st), daemon=True).start()
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 1280, 720)
    fullscreen = False
    while st.running:
        with st.lock:
            img = None if st.frame is None else st.frame.copy()
        if img is None:
            img = np.zeros((360, 640, 3), np.uint8)
            cv2.putText(img, st.status, (20, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
        else:
            if overlay:
                _draw_overlay(img, st.meta)
            lat = f"{st.latency_ms:.0f} ms" if st.latency_ms is not None else "-"
            info = f"{st.fps:.1f} fps | latency {lat} | {st.stats.get('bitrateKbps', '-')} kbit/s | {st.status}"
            cv2.putText(img, info, (10, img.shape[0] - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
            cv2.putText(img, info, (10, img.shape[0] - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        cv2.imshow(WINDOW, img)
        k = cv2.waitKey(15) & 0xFF
        if k in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            st.running = False
        elif k == ord("s") and img is not None:
            name = datetime.now().strftime("snapshot_%Y%m%d_%H%M%S.jpg")
            cv2.imwrite(name, img)
            print("saved", name)
        elif k == ord("o"):
            overlay = not overlay
        elif k == ord("f"):
            fullscreen = not fullscreen
            cv2.setWindowProperty(
                WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL
            )
    cv2.destroyAllWindows()
