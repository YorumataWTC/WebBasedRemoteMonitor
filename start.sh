#!/usr/bin/env sh
# One-command start on Linux/macOS:  ./start.sh [server options]
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
command -v uv >/dev/null 2>&1 || pip install --user uv
[ -f "$ROOT/web/dist/index.html" ] || (cd "$ROOT/web" && npm ci && npm run build)
cd "$ROOT/server"
uv sync
uv run remote-monitor models
exec uv run remote-monitor serve "$@"
