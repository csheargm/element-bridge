#!/usr/bin/env bash
# Runs ON the Oracle VM (Ubuntu 24.04) in ~/hagency/app/deploy, a checkout of one commit of master
# (deploy.sh, from the operator's computer, puts it there; nobody edits code on the server). @pm runs
# from ~/hagency/pm (that commit's files plus @pm's state). Safe to run again. .env is read as data
# by envfile.py (never sourced), and secrets are never printed or put on a command line.
set -euo pipefail
cd "$(dirname "$0")"
umask 077
PM_DIR="$(cd ../.. && pwd)/pm"
[ -f "$PM_DIR/loop.py" ] || { echo "✗ $PM_DIR has no @pm code: run deploy.sh from your computer"; exit 1; }
get() { python3 envfile.py get "$1"; }

# 1. Docker, Compose and vnstat (traffic counts for guard.py) from Ubuntu's own archive.
pkgs=()
command -v docker >/dev/null || pkgs+=(docker.io)
docker compose version >/dev/null 2>&1 || sudo docker compose version >/dev/null 2>&1 || pkgs+=(docker-compose-v2)
command -v vnstat >/dev/null || pkgs+=(vnstat)
if [ ${#pkgs[@]} -gt 0 ]; then
  sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${pkgs[@]}"
fi
# The journal stays small on the 45 GB disk (Docker's own logs rotate: docker-compose.yml).
journal=$'[Journal]\nSystemMaxUse=200M\n'
if [ "$(cat /etc/systemd/journald.conf.d/hagency.conf 2>/dev/null)" != "${journal%$'\n'}" ]; then
  sudo mkdir -p /etc/systemd/journald.conf.d
  printf '%s' "$journal" | sudo tee /etc/systemd/journald.conf.d/hagency.conf >/dev/null
  sudo systemctl restart systemd-journald
fi
# Firewall: OCI's security list opens 80/443/8448. Oracle's host iptables reject other INPUT, but
# Docker's published ports go through its own FORWARD rules (checked by health.py from outside).

# 2. Settings: created mode 600, secrets filled, every value validated.
python3 envfile.py check
dc() { sudo docker compose --env-file .env "$@"; }
dc config --quiet                                        # the compose file and .env agree
dc pull --quiet                                          # fails here on a wrong tag or architecture

# 3. Synapse's config: generated once, then configured; Synapse never starts without the marker.
if ! sudo test -f synapse/homeserver.yaml; then
  mkdir -p synapse
  dc run --rm --no-deps -e SYNAPSE_SERVER_NAME="$(get SERVER_NAME)" -e SYNAPSE_REPORT_STATS=no synapse generate
fi
sudo python3 configure_synapse.py synapse/homeserver.yaml   # every run: idempotent, checks the name
sudo test -f synapse/.hagency-configured || { echo "✗ Synapse isn't configured; not starting it"; exit 1; }

# 4. @pm's copy of the registration secret (it creates its own account and people's agents);
#    @pm can't read Synapse's config, which also holds the database password.
mkdir -p "$PM_DIR/data" && chmod 700 "$PM_DIR" "$PM_DIR/data"
sudo python3 - synapse/homeserver.yaml "$PM_DIR/data/registration_shared_secret" "$(id -u)" <<'PY'
import os, re, sys
found = re.search(r'(?m)^registration_shared_secret: "?([^"\n]+)"?', open(sys.argv[1]).read())
if not found:
    raise SystemExit("✗ homeserver.yaml has no registration_shared_secret")
fd = os.open(sys.argv[2] + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(found.group(1))
os.chown(sys.argv[2] + ".tmp", int(sys.argv[3]), int(sys.argv[3]))
os.replace(sys.argv[2] + ".tmp", sys.argv[2])
PY

# 5. Element points at our homeserver.
MATRIX_DOMAIN=$(get MATRIX_DOMAIN) SERVER_NAME=$(get SERVER_NAME) python3 - <<'PY'
import json, os
cfg = {"default_server_config": {"m.homeserver": {"base_url": f"https://{os.environ['MATRIX_DOMAIN']}",
                                                  "server_name": os.environ["SERVER_NAME"]}},
       "disable_custom_urls": False, "disable_guests": True, "brand": "Hagency Rooms"}
fd = os.open("element-config.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)   # public by design
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2)
PY

# 6. Start or update (@pm restarts on the new code), then load the current Caddyfile.
mkdir -p flags && chmod 755 flags
dc stop pm >/dev/null 2>&1 || true                       # its files changed under it (deploy.sh): a clean start
dc up -d --remove-orphans
dc exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
dc exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile

# 7. The daily free-tier check (guard.py): a systemd timer, as root.
unit="[Unit]
Description=Hagency Rooms: disk and traffic within Oracle's free limits
[Service]
Type=oneshot
WorkingDirectory=$PWD
ExecStart=/usr/bin/python3 $PWD/guard.py"
timer="[Unit]
Description=Hagency Rooms: daily free-tier check
[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target"
printf '%s\n' "$unit" | sudo tee /etc/systemd/system/hagency-guard.service >/dev/null
printf '%s\n' "$timer" | sudo tee /etc/systemd/system/hagency-guard.timer >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now hagency-guard.timer >/dev/null 2>&1

# 8. Done only when everything answers inside (deploy.sh then checks HTTPS from outside).
for _ in $(seq 1 100); do
  dc exec -T pm .venv/bin/python deploy/health.py --internal "$(get MATRIX_DOMAIN)" "$(get SERVER_NAME)" >/dev/null 2>&1 && break
  sleep 3                                                # @pm installs its packages on a first start
done
dc exec -T pm .venv/bin/python deploy/health.py --internal "$(get MATRIX_DOMAIN)" "$(get SERVER_NAME)"
