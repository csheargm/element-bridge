"""Bring your own agent from your own computer: the server side.

provision() makes the person's agent account here (identity.py names it from their FULL Matrix ID,
so a person from another server gets one too; generated password in the git-ignored .env, like
onboard.py), records it in agents.json as "remote" (the supervisor never runs it here) and has the
agent open a private chat with its person. remote_people.py limits people from other servers. A one-time code (pairing.py) then lets connect.py on the person's
computer fetch the agent's sign-in once, from POST /connect on the console server; GET /connect.sh
serves the installer. The password is never logged; only the code's hash is stored.
"""
import os
import socket
import threading

from aiohttp import web

import console
import identity
import pairing
import registry
import remote_people
import setup_accounts
from bridge_config import ROOT, SERVER_NAME
from setup_accounts import AccountError, api, load_env, mark_direct, try_login

PUBLIC_HOMESERVER = os.environ.get("HAGENCY_PUBLIC_HOMESERVER", "http://localhost:8648")   # what people's computers reach
CODES = pairing.Codes(ROOT / "data" / "pairing.json")
LIMIT = pairing.Limiter()
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
INSTALLER = ROOT / "connect.sh"
CONSOLE_MARK = "__HAGENCY_CONSOLE__"
# Behind the public server's Caddy every request comes from Caddy; it sets X-Forwarded-For to the
# real client (Caddy trusts no incoming one). HAGENCY_TRUST_PROXY names the proxy (its Docker host
# name): the header counts only on a connection from that address, never from another container.
TRUST_PROXY = os.environ.get("HAGENCY_TRUST_PROXY", "")


def proxy_addresses() -> set[str]:
    try:
        return {info[4][0] for info in socket.getaddrinfo(TRUST_PROXY, None)} if TRUST_PROXY else set()
    except OSError:
        return set()                          # can't resolve the proxy: trust nobody's header


def client_of(request) -> str:
    """Who is asking, for the wrong-code limiter: the proxy's X-Forwarded-For when it came from the proxy."""
    peer = request.remote or "?"
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded and peer in proxy_addresses():
        return forwarded.split(",")[-1].strip()[:64] or peer
    return peer


def install_line(code: str) -> str:
    return f"curl -fsSL {console.public_url()}/connect.sh | sh -s -- {code}"


def open_dm(agent: str, password: str, person: str) -> str:
    """The agent opens a private chat with its person (marked direct), so they can talk right away."""
    token = try_login(agent, password)
    if not token:
        raise AccountError(f"couldn't sign in as @{agent}")
    try:
        agent_id = f"@{agent}:{SERVER_NAME}"
        r = api("POST", "/_matrix/client/v3/createRoom", {"is_direct": True, "preset": "trusted_private_chat",
                                                         "invite": [person]}, token=token)
        if "room_id" not in r:
            raise AccountError(f"couldn't open a chat for @{agent}: {r.get('errcode')}")
        mark_direct(token, agent_id, person, r["room_id"])
        return r["room_id"]
    finally:
        api("POST", "/_matrix/client/v3/logout", {}, token=token)


WRITING = threading.Lock()                    # .env and agents.json are read-modify-write: one set-up at a time


def provision(person: str) -> str:
    """Create (or reuse) the agent of `person` (a full Matrix ID) as a remote agent; returns its
    localpart. Blocking I/O."""
    with WRITING:
        return _provision(registry.person_id(person))


def _provision(person: str) -> str:
    local = registry.local_name(person)
    if local is not None and not registry.NAME_RE.match(local):
        raise AccountError("for now an agent needs an account name of lowercase letters and digits")
    people = registry.load()
    info = people.get(person, {})
    if person in people and info.get("agent") != registry.REMOTE:    # no "agent": a server-run default one
        raise AccountError("you already have an agent here, run by this server")
    agent = registry.agent_of(person)
    if registry.owner_of(agent) not in (None, person):
        raise AccountError("your agent's account name is taken here; ask this server's operator")
    if local is None:
        remote_people.check_new(people, person)   # other servers' people: allowed, and not too many at once
        if person not in people:              # counted and reserved BEFORE the account exists: a failed
            people[person] = {"agent": registry.REMOTE, "created": remote_people.now()}   # chat can't dodge the cap
            registry.save(people)
    password, _ = setup_accounts.ensure_user(load_env(), agent, setup_accounts.shared_secret(), verify=True)
    dm = info.get("dm") or open_dm(agent, password, person)
    people = registry.load()                  # re-read: keep anything written meanwhile
    people[person] = {**people.get(person, {}), "agent": registry.REMOTE, "dm": dm}
    registry.save(people)
    return agent


def credentials_for(person: str) -> dict | None:
    """What connect.py needs to run `person`'s agent, or None if it isn't a remote agent any more."""
    person = registry.person_id(person)       # codes issued before full IDs hold a bare name
    if registry.load().get(person, {}).get("agent") != registry.REMOTE:
        return None
    if not registry.local_name(person) and not remote_people.allowed():
        return None                           # turned off: no new sign-ins for other servers' people
    agent = registry.agent_of(person)
    if agent != identity.agent_localpart(person, SERVER_NAME) or not registry.owns(person, agent):
        return None                           # an odd record: connect.py checks the derived name, and only theirs
    password = load_env().get(setup_accounts.password_key(agent))
    if not password:
        return None
    return {"homeserver": PUBLIC_HOMESERVER, "user_id": f"@{agent}:{SERVER_NAME}", "password": password, "owner": person}


def routes() -> list:
    async def connect(request):
        client = client_of(request)
        if request.content_type != "application/json" or "Origin" in request.headers:
            return web.json_response({"error": "use connect.py"}, status=415, headers=NO_STORE)   # never a browser's
        if LIMIT.blocked(client):
            return web.json_response({"error": "too many wrong codes; wait 10 minutes"}, status=429, headers=NO_STORE)
        try:
            body = await request.json()
        except ValueError:                    # not JSON: the same answer as a wrong code
            body = {}
        person = CODES.redeem(body.get("code") if isinstance(body, dict) else None)
        creds = credentials_for(person) if person else None
        if not creds:
            LIMIT.failed(client)
            return web.json_response({"error": "that code is wrong, used or expired: ask @pm for a new one"},
                                     status=400, headers=NO_STORE)
        return web.json_response(creds, headers=NO_STORE)

    async def installer(request):
        text = INSTALLER.read_text().replace(CONSOLE_MARK, console.public_url())
        return web.Response(text=text, content_type="text/plain", headers=NO_STORE)
    return [web.post("/connect", connect), web.get("/connect.sh", installer)]
