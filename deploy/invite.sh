#!/usr/bin/env bash
# Run ON the VM in ~/hagency/app/deploy, or from your computer: ssh hagency-vm ~/hagency/app/deploy/invite.sh token 5
#   ./invite.sh token [uses]     a new invite token (default: 1 use, valid 7 days), made by @pm's container
#                                through Synapse's admin API inside Docker; /_synapse/admin is never public
#   ./invite.sh admin            create your own admin account (Synapse's tool; asks for the password)
# Hand the token out with the sign-up page: https://<ROOMS_DOMAIN>/#/register
set -euo pipefail
cd "$(dirname "$0")"
dc() { sudo docker compose --env-file .env "$@"; }

case "${1:-}" in
  token)
    uses="${2:-1}"
    [[ "$uses" =~ ^[1-9][0-9]{0,2}$ ]] || { echo "uses: a number from 1 to 999"; exit 2; }
    dc exec -T pm .venv/bin/python local_signup.py token "$uses" ;;
  admin)
    dc exec synapse register_new_matrix_user -c /data/homeserver.yaml --admin http://localhost:8008 ;;
  *)
    sed -n '2,6p' "$0"; exit 2 ;;
esac
