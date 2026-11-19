"""One-time setup: create anna, betty and their agent users, plus a shared room.

Passwords are generated and written to the git-ignored .env; they are never printed.
Idempotent: existing users/room are reused.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
HS = "http://127.0.0.1:8648"
SERVER_NAME = "bridge.localhost"
USERS = ["anna", "betty", "anna-agent", "betty-agent"]
ROOM_ALIAS = "lounge"


def api(method, path, body=None, token=None):
    req = urllib.request.Request(HS + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"_status": e.code, **json.loads(e.read() or b"{}")}


def load_env():
    env_path = ROOT / ".env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    return env


def save_env(env):
    lines = [f"{k}={v}" for k, v in env.items()]
    path = ROOT / ".env"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # never world-readable
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    path.chmod(0o600)


def register(user, password, shared_secret):
    nonce = api("GET", "/_synapse/admin/v1/register")["nonce"]
    mac = hmac.new(shared_secret.encode(), digestmod=hashlib.sha1)
    mac.update(b"\x00".join([nonce.encode(), user.encode(), password.encode(), b"notadmin"]))
    return api("POST", "/_synapse/admin/v1/register",
               {"nonce": nonce, "username": user, "password": password, "mac": mac.hexdigest(), "admin": False})


def login(user, password):
    r = api("POST", "/_matrix/client/v3/login",
            {"type": "m.login.password", "identifier": {"type": "m.id.user", "user": user}, "password": password})
    if "access_token" not in r:
        raise SystemExit(f"login failed for {user}: {r.get('errcode')}")
    return r["access_token"]


def main():
    yaml = (ROOT / "data" / "homeserver.yaml").read_text()
    shared_secret = re.search(r'^registration_shared_secret: "?([^"\n]+)"?', yaml, re.M).group(1)
    env = load_env()
    for user in USERS:
        key = "PW_" + user.upper().replace("-", "_")
        if key not in env:
            env[key] = secrets.token_urlsafe(18)
            save_env(env)   # persist before registering so a partial failure isn't unrecoverable
            r = register(user, env[key], shared_secret)
            if "_status" in r:
                raise SystemExit(f"register {user} failed: {r.get('errcode')} {r.get('error')}")
            print(f"created @{user}:{SERVER_NAME}")
    save_env(env)

    anna = login("anna", env["PW_ANNA"])
    betty = login("betty", env["PW_BETTY"])
    alias = f"#{ROOM_ALIAS}:{SERVER_NAME}"
    existing = api("GET", f"/_matrix/client/v3/directory/room/{urllib.request.quote(alias)}", token=anna)
    room_id = existing.get("room_id")
    if not room_id:
        room_id = api("POST", "/_matrix/client/v3/createRoom", {
            "name": "Lounge", "room_alias_name": ROOM_ALIAS, "preset": "private_chat",
            "invite": [f"@{u}:{SERVER_NAME}" for u in ("betty", "anna-agent", "betty-agent")],
        }, token=anna)["room_id"]
        print(f"created room {alias}")
    api("POST", f"/_matrix/client/v3/join/{urllib.request.quote(room_id)}", {}, token=betty)
    print(f"room ready: {alias} ({room_id}); passwords in .env")


if __name__ == "__main__":
    main()
