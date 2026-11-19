"""One-time setup: create anna, betty, their agent users and the bots, plus a shared room.

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

from github import LEVEL_EVENT, REPO

ROOT = Path(__file__).parent
HS = "http://127.0.0.1:8648"
SERVER_NAME = "bridge.localhost"
PEOPLE = ["anna", "betty"]                       # each gets a <person>-agent and a bridge
# Bot → exact power level. @pm is the room's one bot: it writes the room's mode and pins its
# status board, so it is a moderator (state events need 50); people decide, it applies.
BOTS = {"pm": 50}
DISPLAY_NAMES = {"pm": "Product Manager"}        # what people see in Element
USERS = [*PEOPLE, *(f"{p}-agent" for p in PEOPLE), *BOTS]
ROOM_ALIAS = "lounge"
TEST_ROOM_ALIAS = "bridge-test"          # automated tests talk here, never in the Lounge


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

    room_id = ensure_room(env, ROOM_ALIAS, "Lounge")
    print(f"room ready: #{ROOM_ALIAS}:{SERVER_NAME} ({room_id}); passwords in .env")


def ensure_room(env: dict, alias_name: str, name: str) -> str:
    """A room with everyone in it (anna creates it; betty, the agents and the bots join)."""
    anna = login("anna", env["PW_ANNA"])
    betty = login("betty", env["PW_BETTY"])
    alias = f"#{alias_name}:{SERVER_NAME}"
    existing = api("GET", f"/_matrix/client/v3/directory/room/{urllib.request.quote(alias)}", token=anna)
    room_id = existing.get("room_id")
    if not room_id:
        room_id = api("POST", "/_matrix/client/v3/createRoom", {
            "name": name, "room_alias_name": alias_name, "preset": "private_chat",
            "invite": [f"@{u}:{SERVER_NAME}" for u in ("betty", *(f"{p}-agent" for p in PEOPLE))],
        }, token=anna)["room_id"]
        print(f"created room {alias}")
    api("POST", f"/_matrix/client/v3/join/{urllib.request.quote(room_id)}", {}, token=betty)
    room = urllib.request.quote(room_id)
    for bot, power in BOTS.items():
        add_bot(anna, login(bot, env["PW_" + bot.upper().replace("-", "_")]), room, bot, power)
    level = api("GET", f"/_matrix/client/v3/rooms/{room}/state/{LEVEL_EVENT}/", token=anna)
    if "level" not in level:
        api("PUT", f"/_matrix/client/v3/rooms/{room}/state/{LEVEL_EVENT}/", {"level": "off", "repo": REPO}, token=anna)
    return room_id


def add_bot(admin, bot, room, name, power):
    """The bot joins the room at exactly `power`."""
    bot_id = f"@{name}:{SERVER_NAME}"
    if name in DISPLAY_NAMES:
        api("PUT", f"/_matrix/client/v3/profile/{urllib.request.quote(bot_id)}/displayname",
            {"displayname": DISPLAY_NAMES[name]}, token=bot)
    api("POST", f"/_matrix/client/v3/rooms/{room}/invite", {"user_id": bot_id}, token=admin)
    api("POST", f"/_matrix/client/v3/join/{room}", {}, token=bot)
    pl = api("GET", f"/_matrix/client/v3/rooms/{room}/state/m.room.power_levels/", token=admin)
    users = pl.setdefault("users", {})
    if users.get(bot_id, 0) == power:
        return
    users[bot_id] = power
    r = api("PUT", f"/_matrix/client/v3/rooms/{room}/state/m.room.power_levels/", pl, token=admin)
    if "event_id" not in r:
        raise SystemExit(f"couldn't set {name}'s power level in {room}: {r.get('errcode')}")


if __name__ == "__main__":
    main()
