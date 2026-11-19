#!/usr/bin/env bash
# One command: start Synapse + Element Web (Docker, localhost only), create accounts/room
# on first run, then run anna's and betty's agent bridges in the foreground (Ctrl-C stops them).
# Usage: ./start.sh [codex|claude]      (agent CLI used by both bridges; default codex)
set -euo pipefail
cd "$(dirname "$0")"
AGENT="${1:-codex}"
IMAGE=matrixdotorg/synapse:latest

if [ ! -f data/homeserver.yaml ]; then
  mkdir -p data
  docker run --rm -v "$PWD/data:/data" -e SYNAPSE_SERVER_NAME=bridge.localhost \
    -e SYNAPSE_REPORT_STATS=no "$IMAGE" generate
  cat synapse-overrides.yaml >> data/homeserver.yaml
fi
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }

docker compose up -d
echo -n "waiting for Synapse"
until curl -sf http://127.0.0.1:8648/_matrix/client/versions >/dev/null; do echo -n .; sleep 1; done; echo
.venv/bin/python setup_accounts.py

mkdir -p logs
trap 'kill 0' EXIT INT TERM
.venv/bin/python bridge.py anna  --agent "$AGENT" 2>&1 | tee logs/anna.log &
.venv/bin/python bridge.py betty --agent "$AGENT" 2>&1 | tee logs/betty.log &
echo "Element Web: http://localhost:8649  (homeserver http://localhost:8648). Ctrl-C to stop bridges."
wait
