#!/usr/bin/env bash
# Run ON the VM, in ~/hagency/deploy, by the operator (it asks for passwords; nothing is stored).
#   ./invite.sh admin            create the first admin account (Synapse's own tool, interactive)
#   ./invite.sh token [uses]     a new invite token (default: 1 use, valid 7 days), via the admin
#                                API inside the container; /_synapse/admin is never public
set -euo pipefail
cd "$(dirname "$0")"
dc() { sudo docker compose --env-file .env "$@"; }

case "${1:-}" in
  admin)
    dc exec synapse register_new_matrix_user -c /data/homeserver.yaml --admin http://localhost:8008 ;;
  token)
    uses="${2:-1}"
    [[ "$uses" =~ ^[0-9]{1,3}$ ]] || { echo "uses: a number from 1 to 999"; exit 2; }
    dc exec synapse python -c '
import getpass, json, sys, time, urllib.request
HS = "http://localhost:8008"
def call(method, path, body=None, token=None):
    req = urllib.request.Request(HS + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")
user = input("admin username: ").strip()
login = call("POST", "/_matrix/client/v3/login", {"type": "m.login.password", "password": getpass.getpass("password: "),
                                                  "identifier": {"type": "m.id.user", "user": user}})
try:
    new = call("POST", "/_synapse/admin/v1/registration_tokens/new",
               {"uses_allowed": int(sys.argv[1]), "expiry_time": int((time.time() + 7 * 86400) * 1000)}, login["access_token"])
    print("invite token:", new["token"], "(uses:", sys.argv[1], ", expires in 7 days)")
finally:
    call("POST", "/_matrix/client/v3/logout", {}, login["access_token"])   # the admin session ends here
' "$uses" ;;
  *)
    sed -n '2,5p' "$0"; exit 2 ;;
esac
