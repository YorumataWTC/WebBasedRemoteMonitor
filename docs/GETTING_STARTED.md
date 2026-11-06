# Getting Started: How to Start the Remote Monitor

This guide shows how to start the system and lists every option you have.
For how the system works internally, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Fastest way (Windows)

Open **PowerShell** in the project folder (`WebBasedRemoteMonitor`) and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

On the first run the script installs everything it needs: the Python packages, the face models (≈ 43 MB) and the web UI build. This takes a few minutes. Later starts take a few seconds.

When the server is ready, it prints its addresses:

```
  Remote Monitor v1.0.0  -  source: 0

  http://localhost:8000    https://localhost:8443
  http://192.168.1.23:8000    https://192.168.1.23:8443
```

Open **http://localhost:8000** in Chrome, Edge or Firefox **on the same PC**. Stop the server with **Ctrl + C**.

> If Windows Firewall asks whether Python may communicate on networks, click **Allow** for *private networks*. Otherwise other PCs cannot connect.

Linux / macOS: `./start.sh` (it accepts the same options).

---

## 2. Requirements (one-time)

| Software | Version | Check with | Download |
|---|---|---|---|
| Python | 3.12 or newer | `python --version` | https://www.python.org/downloads/ |
| Node.js | 20 or newer | `node --version` | https://nodejs.org/ |
| uv (Python package manager) | any | `uv --version` | installed automatically by `start.ps1` |
| Webcam + microphone | optional | – | use `--source synthetic` without a camera |

You do **not** need Python or Node if you use the standalone executable (section 7).

---

## 3. Common ways to start

You can add any option to `start.ps1` (or `uv run remote-monitor serve`).

| Goal | Command |
|---|---|
| Normal start: webcam 0 + default microphone | `.\start.ps1` |
| Demo **without a camera** (animated test scene with a walking "intruder") | `.\start.ps1 --source synthetic` |
| Demo without camera **and** without microphone (test beeps) | `.\start.ps1 --source synthetic --synthetic-audio` |
| Require a **login password** | `.\start.ps1 --password eie4428` |
| Use a **second webcam** | `.\start.ps1 --source 1` |
| Play a **video file** in a loop as the camera | `.\start.ps1 --source C:\videos\demo.mp4` |
| Use a **network camera** stream (RTSP/HTTP) | `.\start.ps1 --source rtsp://192.168.1.50:554/stream` |
| **No audio** at all | `.\start.ps1 --no-audio` |
| Choose another **microphone** | `.\start.ps1 --audio-device 2` (see section 6.2) |
| Use the **NVIDIA GPU** encoder (less CPU) | `.\start.ps1 --encoder h264_nvenc` |
| Use **other ports** (for example if 8000 is busy) | `.\start.ps1 --port 9000 --https-port 9443` |
| Allow at most 5 viewers | `.\start.ps1 --max-viewers 5` |
| Recommended for the **demonstration** | `.\start.ps1 --password eie4428` (webcam), or add `--source synthetic` as a backup |

---

## 4. Opening the live view

| Where is the viewer? | Address to open | Notes |
|---|---|---|
| Same PC as the server | `http://localhost:8000` | Works directly. |
| Another PC in the **same network** (LAN / Wi-Fi) | `https://<server-ip>:8443` | Use the IP that the server printed at start-up. The browser warns about the certificate: click **Advanced → Proceed** (only once). |
| From the **Internet** | see section 5 | |
| Smartphone / tablet | same as LAN: `https://<server-ip>:8443` | |

**Why https on other PCs?** Browsers allow the fast video decoder (WebCodecs) only on secure pages: `https://…`, or `http://localhost`. If you open `http://<server-ip>:8000` from another PC, the page still works, but it automatically switches to the slower **MJPEG compatibility mode** (more bandwidth and lower frame rate).

The certificate is created automatically. It is self-signed, which is why the browser shows a warning.

---

## 5. Access from the Internet

Always start with a password when the system is reachable from the Internet:

```powershell
.\start.ps1 --password <a-strong-password>
```

**Option A: Cloudflare Tunnel (easiest, no router configuration needed)**

1. Install cloudflared once: `winget install Cloudflare.cloudflared`
2. While the server is running, open a second PowerShell window and run:
   ```powershell
   cloudflared tunnel --url http://localhost:8000
   ```
3. cloudflared prints an address like `https://random-words.trycloudflare.com`. Open it on any device. The certificate is valid, so there is no browser warning.

**Option B: Router port forwarding**

1. In your router, forward **TCP port 8443** to the server PC's LAN IP.
2. Open `https://<your-public-ip>:8443` from outside. You will see the certificate warning once.

> University networks usually block incoming connections. In that case, use option A.

---

## 6. All options

### 6.1 Server options (`serve`)

| Option | Default | Description |
|---|---|---|
| `--source <x>` | `0` | `0`, `1`, … = webcam index · `synthetic` = test scene · a file path or a stream URL |
| `--password <pw>` | none | Require a login. Without this option, anyone who can reach the server can watch. |
| `--port <n>` | `8000` | HTTP port |
| `--https-port <n>` | `8443` | HTTPS port |
| `--no-https` | HTTPS on | Do not start the HTTPS listener |
| `--host <ip>` | `0.0.0.0` | Network interface to listen on (`127.0.0.1` = this PC only) |
| `--encoder <name>` | `libx264` | `libx264` (CPU) · `h264_nvenc` (NVIDIA) · `h264_qsv` (Intel) · `h264_amf` (AMD) · `h264_mf` (Windows). If the chosen encoder is not available, the server falls back to libx264 automatically. |
| `--no-audio` | audio on | Disable the microphone |
| `--audio-device <x>` | system default | Microphone by index or by name |
| `--synthetic-audio` | off | Test beeps instead of the microphone |
| `--max-viewers <n>` | `0` (= unlimited) | Maximum number of simultaneous browser viewers |

### 6.2 Helper commands

Run these inside the `server` folder:

| Command | What it does |
|---|---|
| `uv run remote-monitor serve --help` | Show all server options |
| `uv run remote-monitor models` | Download the face / emotion models (done automatically by `start.ps1`) |
| `uv run python -m sounddevice` | List all microphones with their index (for `--audio-device`) |
| `uv run remote-monitor viewer <url>` | Start the native desktop viewer (section 8) |
| `uv run pytest` | Run the server tests |

### 6.3 Environment variables / `.env` file

Instead of command-line options, you can set environment variables. You can also write them into a file `server/.env`, which is read automatically:

```ini
# server/.env
RM_SOURCE=0
RM_PASSWORD=eie4428
RM_PORT=8000
RM_HTTPS_PORT=8443
RM_ENCODER=libx264
RM_AUDIO=true
RM_MAX_VIEWERS=0
RM_LOG_LEVEL=info          # debug | info | warning
RM_DATA_DIR=./data         # recordings, events, settings, certificates
RM_SECRET=some-long-random-string   # keeps logins valid across restarts
```

Command-line options take priority over the `.env` file.

### 6.4 Settings in the web interface

Many settings are changed **live** in the browser under **Settings**, without restarting. They are saved on the server and apply to all viewers:

- camera name, resolution (640×360 … 1920×1080), frame rate, bit rate, keyframe interval;
- mirror / flip, time-stamp overlay;
- motion alarm, sensitivity, alarm hold time, object tracking, record on motion, post-roll;
- face detection, face recognition, emotion recognition, alarm on unknown person;
- audio streaming, loud-noise alarm, noise threshold.

These preferences are stored per viewer, in that browser only: alarm sound, desktop notifications, MJPEG compatibility mode, and the overlay toggles.

---

## 7. Standalone executable (no Python / Node needed)

Build it once on a PC that has Python and Node:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
```

The result is the folder `server\dist\RemoteMonitor\`. Copy the **whole folder** (≈ 300 MB) to any Windows PC and run:

```powershell
.\RemoteMonitor.exe                          # webcam
.\RemoteMonitor.exe --source synthetic       # without a camera
.\RemoteMonitor.exe --password eie4428
```

The executable accepts all options from section 6.1. Recordings and settings are stored in a `data` folder next to the `.exe`.

---

## 8. Other ways to watch

| Client | How |
|---|---|
| **Native desktop viewer** (part of this project) | `cd server` then `uv run remote-monitor viewer https://<server-ip>:8443 --audio`. Add `--password <pw>` if the server uses a password, or `--no-overlay` to hide the detection boxes. Keys: `q` quit · `s` snapshot · `o` overlay on/off · `f` fullscreen |
| **VLC** or any MJPEG player | Open the network stream `http://<server-ip>:8000/api/stream.mjpg` (only without `--password`) |
| **Single picture** | `http://<server-ip>:8000/api/snapshot.jpg` |
| **REST API documentation** | `http://localhost:8000/api/docs` |

---

## 9. Manual start (step by step, for developers)

```powershell
# 1. Web UI (only needed once, or after you change the frontend)
cd web
npm ci
npm run build

# 2. Server
cd ..\server
uv sync                         # install Python dependencies into .venv
uv run remote-monitor models    # download face models (once)
uv run remote-monitor serve --source synthetic
```

To work on the frontend with live reload, keep the server running and in a second window run `cd web; npm run dev`. Then open `http://localhost:5173`; API and WebSocket calls are proxied to port 8000.

---

## 10. Troubleshooting

| Problem | Solution |
|---|---|
| `start.ps1 cannot be loaded because running scripts is disabled` | Start it with `powershell -ExecutionPolicy Bypass -File .\start.ps1` |
| Black picture or "camera disabled" icon | Windows Settings → Privacy & security → Camera: allow camera access for desktop apps. Also check for a physical privacy shutter on the laptop camera. Close other apps that use the camera (Teams, Zoom). |
| `cannot open camera 0` | Try `--source 1`, or use `--source synthetic` to check that the rest of the system works. |
| Other PCs cannot connect | Use the **https://…:8443** address, allow Python in Windows Firewall (private networks), and make sure both PCs are in the same network (guest Wi-Fi often isolates devices). |
| Browser shows "MJPEG fallback" | You opened `http://<ip>:8000` from another PC. Use `https://<ip>:8443` instead. |
| `address already in use` | Another program uses the port. Start with `--port 9000 --https-port 9443`. |
| No sound | Click the **speaker button** (browsers block audio until you click). Check that the server uses the right microphone (`--audio-device`). |
| Face features greyed out in Settings | Models are missing. Run `uv run remote-monitor models` in `server`. |
| High CPU load | Lower the resolution or frame rate in Settings, disable face recognition, or use `--encoder h264_nvenc` / `h264_qsv`. |
| Video stutters on a weak Wi-Fi link | Lower the bit rate in Settings (for example 1000 kbit/s). The system skips to the newest frame instead of building up delay. |
| Login is lost after a server restart | Set `RM_SECRET` in `server/.env` (section 6.3). |
