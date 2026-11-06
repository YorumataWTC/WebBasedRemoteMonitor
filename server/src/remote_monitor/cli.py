"""Command line entry point: ``remote-monitor [serve|viewer|models] ...``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .app import create_app
    from .certs import ensure_certificate, local_ips
    from .config import ServerConfig
    from .service import MonitorService

    overrides = {
        k: v
        for k, v in {
            "host": args.host,
            "port": args.port,
            "https_port": args.https_port,
            "source": args.source,
            "password": args.password,
            "encoder": args.encoder,
            "audio_device": args.audio_device,
        }.items()
        if v is not None
    }
    if args.no_https:
        overrides["https"] = False
    if args.no_audio:
        overrides["audio"] = False
    if args.synthetic_audio:
        overrides["synthetic_audio"] = True
    cfg = ServerConfig(**overrides)
    logging.basicConfig(
        level=cfg.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    service = MonitorService(cfg)
    app = create_app(service)

    servers = [
        uvicorn.Server(
            uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="warning", ws="websockets", lifespan="on")
        )
    ]
    if cfg.https:
        cert, key = ensure_certificate(cfg.data_dir / "certs")
        servers.append(
            uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=cfg.host,
                    port=cfg.https_port,
                    log_level="warning",
                    ws="websockets",
                    lifespan="off",
                    ssl_certfile=str(cert),
                    ssl_keyfile=str(key),
                )
            )
        )

    print(
        f"\n  Remote Monitor v{__version__}  -  source: {cfg.source}"
        f"{'  -  password protected' if cfg.password else ''}\n"
    )
    for ip in local_ips():
        host = "localhost" if ip == "127.0.0.1" else ip
        line = f"  http://{host}:{cfg.port}"
        if cfg.https:
            line += f"    https://{host}:{cfg.https_port}"
        print(line)
    print("\n  (use the https:// address from other PCs - browsers allow WebCodecs only in secure contexts)\n")

    async def run_all() -> None:
        tasks = [asyncio.create_task(s.serve()) for s in servers]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for s in servers:  # one stopped (Ctrl+C) -> stop the others; HTTP server shuts down last
            s.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run_all())


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="remote-monitor", description="Remote video monitoring server")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("serve", help="run the monitoring server (default)")
    sp.add_argument("--source", help='camera index (0), video file / URL, or "synthetic"')
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.add_argument("--https-port", type=int)
    sp.add_argument("--no-https", action="store_true", help="do not start the HTTPS listener")
    sp.add_argument("--password", help="require this password for viewers")
    sp.add_argument("--encoder", help="libx264 (default), h264_nvenc, h264_qsv, h264_amf, h264_mf")
    sp.add_argument("--no-audio", action="store_true")
    sp.add_argument("--audio-device", help="sounddevice input device name or index")
    sp.add_argument("--synthetic-audio", action="store_true", help="generate test tones instead of mic")

    vp = sub.add_parser("viewer", help="native reception software (OpenCV window)")
    vp.add_argument("url", nargs="?", default="http://localhost:8000")
    vp.add_argument("--password")
    vp.add_argument("--audio", action="store_true", help="also play the audio stream")
    vp.add_argument("--no-overlay", action="store_true")

    sub.add_parser("models", help="download the face / emotion models (OpenCV Model Zoo)")

    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        argv = ["serve", *argv]
    args = p.parse_args(argv)

    if args.cmd == "serve":
        _serve(args)
    elif args.cmd == "viewer":
        from .viewer import run_viewer

        run_viewer(args.url, args.password, args.audio, not args.no_overlay)
    elif args.cmd == "models":
        from .analytics.faces import download_models
        from .config import ServerConfig

        download_models(ServerConfig().models_dir)
    else:
        p.print_help()
