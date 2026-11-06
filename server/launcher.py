"""Entry point for the frozen (PyInstaller) executable.

Bundled resources (web UI, models) live next to the executable; user data (recordings,
events, settings, certificates) is written to ./data beside the .exe.
"""

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    home = Path(sys.executable).parent
    os.environ.setdefault("RM_WEB_DIR", str(bundle / "web"))
    os.environ.setdefault("RM_MODELS_DIR", str(bundle / "models"))
    os.environ.setdefault("RM_DATA_DIR", str(home / "data"))

from remote_monitor.cli import main  # noqa: E402

main()
