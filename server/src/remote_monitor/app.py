"""HTTP / WebSocket interface (FastAPI on uvicorn).

REST (JSON) for control and library functions, one WebSocket per viewer for media and live
metadata, multipart MJPEG as universal fallback (works in any <img> tag, VLC, or browsers
without WebCodecs), and the compiled single-page web app as static files.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import __version__
from .auth import COOKIE
from .clock import now_us
from .hub import Viewer, _Item
from .service import MonitorService

log = logging.getLogger(__name__)


class LoginBody(BaseModel):
    password: str


class EnrollBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)


def create_app(service: MonitorService, manage_lifecycle: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if manage_lifecycle:
            await service.start()
        yield
        if manage_lifecycle:
            await service.stop()

    app = FastAPI(
        title="Remote Monitor",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.service = service
    auth = service.auth

    def require_auth(request: Request) -> None:
        if not auth.verify(request.cookies.get(COOKIE)):
            raise HTTPException(401, "login required")

    guarded = [Depends(require_auth)]

    # ------------------------------------------------------------------ session
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/session")
    def session(request: Request):
        return {
            "authRequired": auth.enabled,
            "authenticated": auth.verify(request.cookies.get(COOKIE)),
            "version": __version__,
        }

    @app.post("/api/login")
    async def login(body: LoginBody, request: Request):
        if not auth.enabled:
            return {"ok": True}
        if not auth.check_password(body.password):
            await asyncio.sleep(1.0)  # slow down brute force
            service.events.add(
                "security", f"Failed login from {request.client.host if request.client else '?'}", "warning"
            )
            raise HTTPException(401, "wrong password")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            COOKIE,
            auth.issue(),
            httponly=True,
            samesite="strict",
            max_age=12 * 3600,
            secure=request.url.scheme == "https",
        )
        return resp

    @app.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    # ------------------------------------------------------------------ status & settings
    @app.get("/api/status", dependencies=guarded)
    def status():
        return {
            "version": __version__,
            "settings": service.settings.value.model_dump(),
            "features": service.features(),
            "stats": service.stats(),
            "viewers": [v.info() for v in service.hub.viewers.values()],
            "videoConfig": service.hub.video_config,
        }

    @app.get("/api/settings", dependencies=guarded)
    def get_settings():
        return service.settings.value.model_dump()

    @app.patch("/api/settings", dependencies=guarded)
    def patch_settings(patch: dict):
        try:
            return service.settings.update(patch).model_dump()
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json(include_url=False))) from exc

    # ------------------------------------------------------------------ live media
    @app.get("/api/snapshot.jpg", dependencies=guarded)
    def snapshot():
        jpeg = service.snapshot_jpeg()
        if jpeg is None:
            raise HTTPException(503, "no frame yet")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/stream.mjpg", dependencies=guarded)
    async def mjpeg(request: Request):
        q = service.hub.mjpeg_subscribe()

        async def gen():
            try:
                while not await request.is_disconnected():
                    try:
                        jpeg = await asyncio.wait_for(q.get(), 2.0)
                    except TimeoutError:
                        continue
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode()
                        + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
            finally:
                service.hub.mjpeg_unsubscribe(q)

        return StreamingResponse(
            gen(), media_type="multipart/x-mixed-replace; boundary=frame", headers={"Cache-Control": "no-store"}
        )

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        token = ws.cookies.get(COOKIE) or ws.query_params.get("token")
        if not auth.verify(token):
            await ws.close(code=4401, reason="login required")
            return
        if service.cfg.max_viewers and len(service.hub.viewers) >= service.cfg.max_viewers:
            await ws.close(code=4429, reason="too many viewers")
            return
        await ws.accept()
        viewer = Viewer(ws.client.host if ws.client else "?", ws.headers.get("user-agent", ""))
        viewer.want_audio = ws.query_params.get("audio") == "1"
        hello = {
            "t": "hello",
            "viewerId": viewer.id,
            "serverTime": now_us(),
            "version": __version__,
            "settings": service.settings.value.model_dump(),
            "features": service.features(),
            "recording": service.recorder.status(),
        }
        service.hub.add(viewer, hello)
        service.events.add("viewer", f"Viewer #{viewer.id} connected from {viewer.address}")

        async def sender():
            while True:
                item = await viewer.queue.get()
                if isinstance(item.data, bytes):
                    await ws.send_bytes(item.data)
                else:
                    await ws.send_text(item.data)
                viewer.sent_bytes += len(item.data)

        task = asyncio.create_task(sender())
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                kind = msg.get("t")
                if kind == "ping":  # NTP-style clock sync: client computes offset & RTT
                    viewer.queue.put_front(_Item(json.dumps({"t": "pong", "c": msg.get("c"), "s": now_us()}), "ctrl"))
                    if isinstance(msg.get("rtt"), (int, float)):
                        viewer.rtt_ms = msg["rtt"]
                elif kind == "keyframe":
                    viewer.need_key = True
                    service.request_keyframe()
                elif kind == "audio":
                    viewer.want_audio = bool(msg.get("on"))
        except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
            pass
        finally:
            task.cancel()
            service.hub.remove(viewer)

    # ------------------------------------------------------------------ events
    @app.get("/api/events", dependencies=guarded)
    def list_events(limit: int = 100, before: int | None = None, kind: str | None = None):
        return service.events.list(limit, before, kind)

    @app.delete("/api/events", dependencies=guarded)
    def clear_events():
        service.events.clear()
        return {"ok": True}

    @app.get("/api/snapshots/{name}", dependencies=guarded)
    def get_snapshot(name: str):
        p = (service.cfg.snapshots_dir / name).resolve()
        if p.parent != service.cfg.snapshots_dir.resolve() or not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg")

    # ------------------------------------------------------------------ recordings
    @app.get("/api/recordings", dependencies=guarded)
    def list_recordings():
        return {"active": service.recorder.status(), "items": service.recorder.list()}

    @app.post("/api/recordings/start", dependencies=guarded)
    def start_recording():
        status = service.start_recording()
        if status is None:
            raise HTTPException(409, "no keyframe available yet, try again")
        return status

    @app.post("/api/recordings/stop", dependencies=guarded)
    def stop_recording():
        return service.stop_recording() or {}

    @app.get("/api/recordings/{name}", dependencies=guarded)
    def get_recording(name: str, download: bool = False):
        p = service.recorder.path_of(name)
        if p is None or p.suffix not in (".mp4", ".jpg"):
            raise HTTPException(404)
        media = "video/mp4" if p.suffix == ".mp4" else "image/jpeg"
        return FileResponse(p, media_type=media, filename=p.name if download else None)

    @app.delete("/api/recordings/{name}", dependencies=guarded)
    def delete_recording(name: str):
        if not service.recorder.delete(name):
            raise HTTPException(404)
        return {"ok": True}

    # ------------------------------------------------------------------ faces
    @app.get("/api/faces", dependencies=guarded)
    def list_faces():
        return {"available": service.faces.available, "items": service.gallery.list()}

    @app.post("/api/faces", dependencies=guarded)
    async def enroll_face(body: EnrollBody):
        frame = service.latest_raw_frame()
        if frame is None:
            raise HTTPException(503, "no frame yet")
        try:
            entry = await asyncio.to_thread(service.faces.enroll, frame.image, body.name.strip())
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        service.events.add("face_enrolled", f"Enrolled face of {entry['name']}")
        return entry

    @app.get("/api/faces/{entry_id}.jpg", dependencies=guarded)
    def face_thumb(entry_id: str):
        p = (service.gallery.dir / f"{entry_id}.jpg").resolve()
        if p.parent != service.gallery.dir.resolve() or not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg")

    @app.delete("/api/faces/{entry_id}", dependencies=guarded)
    def delete_face(entry_id: str):
        if not service.gallery.remove(entry_id):
            raise HTTPException(404)
        return {"ok": True}

    @app.get("/api/viewers", dependencies=guarded)
    def viewers():
        return [v.info() for v in service.hub.viewers.values()]

    # ------------------------------------------------------------------ web app
    web_dir: Path = service.cfg.web_dir
    if (web_dir / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = (web_dir / path).resolve()
            if path and f.is_file() and web_dir.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(web_dir / "index.html", headers={"Cache-Control": "no-cache"})
    else:

        @app.get("/", include_in_schema=False)
        def no_ui():
            return HTMLResponse(
                "<h1>Remote Monitor</h1><p>Web UI not built. Run <code>npm run build</code> in "
                "<code>web/</code>. Fallback stream: <a href='/api/stream.mjpg'>MJPEG</a></p>"
            )

    return app
