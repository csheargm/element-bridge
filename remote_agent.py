"""Bring your own agent from your own computer: the server side.

provision() makes <person>-agent (generated password in the git-ignored .env, like onboard.py),
records it in agents.json as "remote" (the supervisor never runs it here) and has the agent open a
private chat with its person. A one-time code (pairing.py) then lets connect.py on the person's
computer fetch the agent's sign-in once, from POST /connect on the console server; GET /connect.sh
serves the installer. The password is never logged; only the code's hash is stored.
"""
import os
import re

from aiohttp import web

import pairing
import registry
import setup_accounts
from bridge_config import ROOT, SERVER_NAME
from setup_accounts import AccountError, api, load_env, mark_direct, try_login

PUBLIC_HOMESERVER = os.environ.get("HAGENCY_PUBLIC_HOMESERVER", "http://localhost:8648")   # what people's computers reach
CODES = pairing.Codes(ROOT / "data" / "pairing.json")
LIMIT = pairing.Limiter()
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
INSTALLER = ROOT / "connect.sh"
CONSOLE_MARK = "__HAGENCY_CONSOLE__"
URL_RE = re.compile(r"^(https://[A-Za-z0-9.-]+|http://(localhost|127\.0\.0\.1))(:\d{1,5})?$")   # also safe inside sh


def console_url() -> str:
    """Where people's computers reach this console (the public server sets an https URL)."""
    import panel                              # late: panel serves these routes
    url = os.environ.get("HAGENCY_PUBLIC_CONSOLE", f"http://localhost:{panel.PORT}").rstrip("/")
    if not URL_RE.match(url):                 # it goes into a shell line and the installer's source
        raise RuntimeError("this server's HAGENCY_PUBLIC_CONSOLE must be https://host[:port] (http only for localhost)")
    return url


def install_line(code: str) -> str:
    return f"curl -fsSL {console_url()}/connect.sh | sh -s -- {code}"


def open_dm(agent: str, password: str, person: str) -> str:
    """The agent opens a private chat with its person (marked direct), so they can talk right away."""
    token = try_login(agent, password)
    if not token:
        raise AccountError(f"couldn't sign in as @{agent}")
    try:
        person_id, agent_id = f"@{person}:{SERVER_NAME}", f"@{agent}:{SERVER_NAME}"
        r = api("POST", "/_matrix/client/v3/createRoom", {"is_direct": True, "preset": "trusted_private_chat",
                                                         "invite": [person_id]}, token=token)
        if "room_id" not in r:
            raise AccountError(f"couldn't open a chat for @{agent}: {r.get('errcode')}")
        mark_direct(token, agent_id, person_id, r["room_id"])
        return r["room_id"]
    finally:
        api("POST", "/_matrix/client/v3/logout", {}, token=token)


def provision(person: str) -> str:
    """Create (or reuse) <person>-agent as a remote agent; returns its localpart. Blocking I/O."""
    if not registry.NAME_RE.match(person):
        raise AccountError("for now an agent needs an account name of lowercase letters and digits")
    people = registry.load()
    info = people.get(person, {})
    if person in people and info.get("agent") != registry.REMOTE:    # no "agent": a server-run default one
        raise AccountError("you already have an agent here, run by this server")
    agent = f"{person}-agent"
    password, _ = setup_accounts.ensure_user(load_env(), agent, setup_accounts.shared_secret(), verify=True)
    dm = info.get("dm") or open_dm(agent, password, person)
    people = registry.load()                  # re-read: keep anything written meanwhile
    people[person] = {**people.get(person, {}), "agent": registry.REMOTE, "dm": dm}
    registry.save(people)
    return agent


def credentials_for(person: str) -> dict | None:
    """What connect.py needs to run <person>-agent, or None if it isn't a remote agent any more."""
    if registry.load().get(person, {}).get("agent") != registry.REMOTE:
        return None
    password = load_env().get(setup_accounts.password_key(f"{person}-agent"))
    if not password:
        return None
    return {"homeserver": PUBLIC_HOMESERVER, "user_id": f"@{person}-agent:{SERVER_NAME}",
            "password": password, "owner": person}


def routes() -> list:
    async def connect(request):
        client = request.remote or "?"
        if request.content_type != "application/json" or request.headers.get("Origin"):
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
        text = INSTALLER.read_text().replace(CONSOLE_MARK, console_url())
        return web.Response(text=text, content_type="text/plain", headers=NO_STORE)
    return [web.post("/connect", connect), web.get("/connect.sh", installer)]
