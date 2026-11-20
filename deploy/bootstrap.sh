#!/usr/bin/env bash
# Runs ON the Oracle VM (Ubuntu 24.04) as ~/hagency/deploy/bootstrap.sh, with the site in
# ~/hagency/site. Safe to run again. .env is read as data by envfile.py (never sourced), and
# secrets are never printed or put on a command line.
set -euo pipefail
cd "$(dirname "$0")"
umask 077
[ -f ../site/index.html ] || { echo "✗ ~/hagency/site/index.html missing (copy site/ next to deploy/)"; exit 1; }
get() { python3 envfile.py get "$1"; }

# 1. Docker and Compose from Ubuntu's own archive (no third-party scripts); each checked on its own.
pkgs=()
command -v docker >/dev/null || pkgs+=(docker.io)
docker compose version >/dev/null 2>&1 || sudo docker compose version >/dev/null 2>&1 || pkgs+=(docker-compose-v2)
if [ ${#pkgs[@]} -gt 0 ]; then
  sudo apt-get update -qq && sudo apt-get install -y -qq "${pkgs[@]}"
fi
# Firewall: OCI's security list opens 80/443/8448 (docs/DEPLOY_ORACLE.md). Docker publishes
# Caddy's ports through its own FORWARD rules, so we don't edit or persist host iptables.

# 2. Settings: created mode 600, secrets filled, every value validated.
python3 envfile.py check
dc() { sudo docker compose --env-file .env "$@"; }
dc config --quiet                                        # the compose file and .env agree
dc pull --quiet                                          # fails here on a wrong tag or architecture

# 3. Synapse's config: generated once, then configured; Synapse never starts without the marker.
SERVER_NAME=$(get SERVER_NAME)
if [ ! -f synapse/homeserver.yaml ]; then
  mkdir -p synapse
  sudo docker run --rm -v "$PWD/synapse:/data" -e SYNAPSE_SERVER_NAME="$SERVER_NAME" \
    -e SYNAPSE_REPORT_STATS=no "matrixdotorg/synapse:$(get SYNAPSE_VERSION)" generate
fi
sudo python3 configure_synapse.py synapse/homeserver.yaml   # every run: idempotent, checks the name
sudo test -f synapse/.hagency-configured || { echo "✗ Synapse isn't configured; not starting it"; exit 1; }

# 4. Element points at our homeserver.
MATRIX_DOMAIN=$(get MATRIX_DOMAIN) SERVER_NAME="$SERVER_NAME" python3 - <<'PY'
import json, os
cfg = {"default_server_config": {"m.homeserver": {"base_url": f"https://{os.environ['MATRIX_DOMAIN']}",
                                                  "server_name": os.environ["SERVER_NAME"]}},
       "disable_custom_urls": False, "disable_guests": True, "brand": "Hagency Rooms"}
fd = os.open("element-config.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)   # public by design
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2)
PY

# 5. Start or update, then load the current Caddyfile (a running Caddy keeps its old one otherwise).
dc up -d --remove-orphans
dc exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
dc exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile

# 6. Done only when the public endpoints answer (certificates can take a minute on first run).
check() {   # url, text the body must contain
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 5 "$1" 2>/dev/null | grep -q "$2"; then echo "✓ $1"; return 0; fi
    sleep 4
  done
  echo "✗ $1 didn't answer with '$2' (see: sudo docker compose logs --tail 50)"; return 1
}
ok=0
check "https://$(get SITE_DOMAIN)/" "Hagency" || ok=1
check "https://$(get ROOMS_DOMAIN)/config.json" "$(get MATRIX_DOMAIN)" || ok=1
check "https://$(get MATRIX_DOMAIN)/_matrix/client/versions" '"versions"' || ok=1
check "https://$(get MATRIX_DOMAIN)/.well-known/matrix/server" '"m.server"' || ok=1
check "https://$(get MATRIX_DOMAIN)/.well-known/matrix/client" '"m.homeserver"' || ok=1
# Sign-up without an invite token must be refused: every registration flow needs a token.
curl -sS --max-time 10 -X POST -H "Content-Type: application/json" -d '{}' \
  "https://$(get MATRIX_DOMAIN)/_matrix/client/v3/register" -o /tmp/hagency-register.json 2>/dev/null || true   # a 401 with the flows
if python3 -c 'import json,sys; f=json.load(open("/tmp/hagency-register.json")).get("flows") or [{}]
sys.exit(0 if all("m.login.registration_token" in x.get("stages", []) for x in f) else 1)' 2>/dev/null; then
  echo "✓ sign-up needs an invite token"
else
  echo "✗ sign-up may be open without a token: check homeserver.yaml"; ok=1
fi
[ $ok -eq 0 ] && echo "✓ Hagency Rooms is up" || { echo "✗ not everything is up yet"; exit 1; }
