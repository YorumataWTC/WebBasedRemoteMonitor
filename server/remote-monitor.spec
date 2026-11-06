# PyInstaller spec - builds a self-contained Windows executable folder:
#   dist/RemoteMonitor/RemoteMonitor.exe  (+ bundled web UI and AI models)
# Build:  uv run pyinstaller remote-monitor.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("av", "cv2", "sounddevice", "uvicorn", "websockets"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("remote_monitor")
datas += [("../web/dist", "web"), ("models", "models")]

a = Analysis(
    ["launcher.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pytest", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="RemoteMonitor", console=True, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="RemoteMonitor")
