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
need python3
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "✗ needs Python 3.10 or newer"; exit 1; }

if [ -n "${HAGENCY_APP:-}" ]; then
  echo "Using $APP"
elif [ -d "$APP/.git" ]; then
  echo "Updating $APP"; git -C "$APP" pull -q --ff-only
else
  echo "Installing Hagency Rooms in $APP"; mkdir -p "$(dirname "$APP")"; git clone -q --depth 1 "$REPO" "$APP"
fi
[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"

# Piped from curl, this script's stdin is the download: read questions from the terminal if there is one.
if (: </dev/tty) 2>/dev/null; then
  exec "$APP/.venv/bin/python" "$APP/connect.py" --server "$CONSOLE" "$@" </dev/tty
fi
exec "$APP/.venv/bin/python" "$APP/connect.py" --server "$CONSOLE" "$@"
