#!/bin/sh
# Hagency Rooms: connect your own Codex or Claude Code as your agent, from this computer.
#   curl -fsSL __HAGENCY_CONSOLE__/connect.sh | sh -s -- CODE [--agent codex|claude]
# Installs (or updates) Hagency Rooms in ~/.hagency/app, then runs connect.py with @pm's one-time
# code. Your agent's sign-in is kept in ~/.hagency/credentials.json (only you can read it).
# Later, to bring your agent online again: ~/.hagency/app/.venv/bin/python ~/.hagency/app/connect.py
set -eu
CONSOLE="${HAGENCY_CONSOLE:-__HAGENCY_CONSOLE__}"
REPO="${HAGENCY_REPO:-https://github.com/csheargm/hagency-rooms}"
APP="${HAGENCY_APP:-$HOME/.hagency/app}"     # HAGENCY_APP=/path/to/a/checkout uses that one as it is

need() { command -v "$1" >/dev/null 2>&1 || { echo "✗ please install $1 first"; exit 1; }; }
need git
PY=""
for p in python3.13 python3.12 python3.11 python3; do      # macOS's own python3 may be too old
  if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then PY="$p"; break; fi
done
[ -n "$PY" ] || { echo "✗ needs Python 3.11 or newer (e.g. brew install python@3.12)"; exit 1; }

if [ -n "${HAGENCY_APP:-}" ]; then
  echo "Using $APP"
elif [ -d "$APP/.git" ]; then
  echo "Updating $APP"; git -C "$APP" pull -q --ff-only
else
  echo "Installing Hagency Rooms in $APP"; mkdir -p "$(dirname "$APP")"; git clone -q --depth 1 "$REPO" "$APP"
fi
[ -x "$APP/.venv/bin/python" ] || "$PY" -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"

umask 077                                    # ~/.hagency and the sign-in: only you
# Piped from curl, this script's stdin is the download: read questions from the terminal if there is one.
if (: </dev/tty) 2>/dev/null; then
  exec "$APP/.venv/bin/python" "$APP/connect.py" --server "$CONSOLE" "$@" </dev/tty
fi
exec "$APP/.venv/bin/python" "$APP/connect.py" --server "$CONSOLE" "$@"
