#!/usr/bin/env bash
# Runs ON the Oracle VM (Ubuntu 24.04), from ~/hagency, after deploy/ and site/ are copied there.
# Idempotent: safe to run again. Never prints secrets; they live only in ~/hagency/.env (mode 600).
set -euo pipefail
cd "$(dirname "$0")"

need() { grep -qE "^$1=.+" .env || { echo "✗ set $1 in ~/hagency/.env, then run me again"; exit 1; }; }
secret() {   # fill an empty secret with a random value, without echoing it
  if ! grep -qE "^$1=.+" .env; then
    sed -i "s|^$1=.*|$1=$(openssl rand -hex 32)|" .env
  fi
}

# 1. Docker from Ubuntu's own archive (no third-party install scripts).
if ! command -v docker >/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker.io docker-compose-v2 netfilter-persistent iptables-persistent
  sudo usermod -aG docker "$USER"
fi

# 2. Oracle's Ubuntu images reject everything but SSH in iptables, even when the VCN security
#    list allows it. Open 80/443/8448 just before the REJECT rule, and keep it across reboots.
for port in 80 443 8448; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    reject=$(sudo iptables -L INPUT --line-numbers | awk '$2=="REJECT"{print $1; exit}')
    sudo iptables -I INPUT "${reject:-1}" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
  fi
done
sudo netfilter-persistent save >/dev/null

# 3. Settings and secrets.
[ -f .env ] || { cp .env.example .env; chmod 600 .env; }
secret POSTGRES_PASSWORD
for v in SITE_DOMAIN ROOMS_DOMAIN MATRIX_DOMAIN SERVER_NAME ACME_EMAIL CADDY_VERSION SYNAPSE_VERSION ELEMENT_VERSION; do need "$v"; done
if grep -qE "^(CADDY|SYNAPSE|ELEMENT|POSTGRES)_VERSION=latest" .env; then echo "✗ pin image versions (no 'latest')"; exit 1; fi
set -a; . ./.env; set +a

# 4. Synapse's config, generated once, then our settings on top (Postgres, invite-only signup).
if [ ! -f synapse/homeserver.yaml ]; then
  mkdir -p synapse
  sudo docker run --rm -v "$PWD/synapse:/data" -e SYNAPSE_SERVER_NAME="$SERVER_NAME" \
    -e SYNAPSE_REPORT_STATS=no "matrixdotorg/synapse:$SYNAPSE_VERSION" generate
  sudo python3 configure_synapse.py synapse/homeserver.yaml
fi

# 5. Element points at our homeserver.
python3 - <<'PY'
import json, os
cfg = {"default_server_config": {"m.homeserver": {"base_url": f"https://{os.environ['MATRIX_DOMAIN']}",
                                                  "server_name": os.environ["SERVER_NAME"]}},
       "disable_custom_urls": False, "disable_guests": True, "brand": "Hagency Rooms"}
open("element-config.json", "w").write(json.dumps(cfg, indent=2))
PY

# 6. Start (or update) the stack.
sudo docker compose --env-file .env up -d
sudo docker compose ps --format "table {{.Service}}\t{{.State}}"
echo "✓ up. Check: https://$SITE_DOMAIN  https://$ROOMS_DOMAIN  https://$MATRIX_DOMAIN/_matrix/client/versions"
