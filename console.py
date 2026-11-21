"""The Hagency console: room settings in the side panel (Element widget) or as a standalone page
(phones, other clients) — instead of config files.

Who is looking:
  - in Element: the widget asks Element for an OpenID token (Matrix widget API, one-time "allow"),
    and we verify it with the homeserver (/_matrix/federation/v1/openid/userinfo);
  - anywhere else: "@pm send me my console link" → @pm DMs a one-time link (10 min, single use).
Either way the page gets a short-lived session bound to that Matrix user.

What they can do goes through @pm's own code paths and checks (mode: owner/maintainer; my agent's
follow-me and web grant: only my own agent; approve: only what I asked for), and each change is
also announced in the room. Nothing here bypasses the chat's rules.
"""
import asyncio
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request

from aiohttp import web

import customize
import github
import github_setup
import ownership
import project_rooms
import registry
import room_state
from bridge_config import ELEMENT_ORIGIN
from setup_accounts import HS, SERVER_NAME

SESSION_S = 12 * 3600
LINK_S = 600
_sessions: dict[str, tuple[str, float, str | None]] = {}   # session → (user, expires, room scope or None)
_links: dict[str, tuple[str, float, str]] = {}             # one-time code → (user, expires, room)
USER_RE = re.compile(r"^@[a-z0-9._=/+-]+:" + re.escape(SERVER_NAME) + "$")
LOCAL_HOSTS = ("localhost", "127.0.0.1")
# Element's sign-in token is checked with OUR homeserver, which knows only its own people. Someone
# from another server signs in with a one-time link instead (checking their server's token over
# federation is future work: it means fetching a URL their server names).
NOT_VERIFIED = "couldn't verify you here; from another server? Say \"@pm send me my console link\" in the room"
PORT = int(os.environ.get("PANEL_PORT", "8650"))
# Also safe inside sh: it goes into the install line and connect.sh (GPT review 21).
URL_RE = re.compile(r"^(https://[A-Za-z0-9.-]+|http://(localhost|127\.0\.0\.1))(:\d{1,5})?$")


def public_url() -> str:
    """Where people reach this console: HAGENCY_PUBLIC_CONSOLE (https on the public server, behind its
    proxy), else this machine. Links, the widget, the install line and the allowed Host all use it."""
    url = os.environ.get("HAGENCY_PUBLIC_CONSOLE", f"http://localhost:{PORT}").rstrip("/")
    if not URL_RE.fullmatch(url):
        raise RuntimeError("this server's HAGENCY_PUBLIC_CONSOLE must be https://host[:port] (http only for localhost)")
    return url


def allowed_hosts() -> tuple[str, ...]:
    """Hosts a request may be addressed to (guards against DNS rebinding): this machine and the public name."""
    return (*LOCAL_HOSTS, urllib.parse.urlsplit(public_url()).hostname)


def allowed_origins() -> set[str]:
    """Exact origins (scheme, host, port) our own page is served from."""
    return {public_url(), *(f"http://{h}:{PORT}" for h in LOCAL_HOSTS)}


def new_session(user_id: str, room: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = (user_id, time.time() + SESSION_S, room)
    return token


def session_of(request) -> tuple[str, str | None] | None:
    """(user, room scope) for a valid "Bearer <session>" header, else None."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    user, expires, scope = _sessions.get(header[len("Bearer "):].strip(), (None, 0, None))
    return (user, scope) if user and time.time() < expires else None


def user_of(request) -> str | None:
    found = session_of(request)
    return found[0] if found else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                           # verification stays on our own homeserver


def openid_user(access_token: str) -> str | None:
    """Ask our homeserver who an OpenID token belongs to (only local users count; fail closed)."""
    if not isinstance(access_token, str) or not access_token or len(access_token) > 512:
        return None
    url = f"{HS}/_matrix/federation/v1/openid/userinfo?access_token={urllib.parse.quote(access_token, safe='')}"
    try:
        with urllib.request.build_opener(_NoRedirect).open(url, timeout=10) as r:
            data = json.loads(r.read())
    except (OSError, ValueError):
        return None
    sub = data.get("sub") if isinstance(data, dict) else None
    return sub if isinstance(sub, str) and USER_RE.match(sub) else None


def new_link(user_id: str, room: str) -> str:
    code = secrets.token_urlsafe(24)
    _links[code] = (user_id, time.time() + LINK_S, room)
    return code


def redeem(code: str) -> tuple[str, str] | None:
    """(user, room) for a fresh one-time code; the code is gone after this either way."""
    user, expires, room = _links.pop(code, (None, 0, ""))
    return (user, room) if user and time.time() < expires else None


def my_agents(loop, room, room_id: str, user: str) -> list[dict]:
    """"My agents": each agent registered to exactly `user` (by full ID), the first first."""
    grants, found = loop.grants_cache.get(room_id, {}), []
    for a in registry.agents_of(user):
        if registry.owns(user, a["localpart"]):
            aid = f"@{a['localpart']}:{SERVER_NAME}"
            found.append({"name": a["localpart"], "label": a["label"], "first": a["first"], "follow": a["follow"],
                          "remote": a["agent"] == registry.REMOTE, "online": loop.agent_online(a["localpart"]),
                          "web": "web" in grants.get(aid, {}), "here": aid in room.users})
    return found


async def view(loop, room_id: str, user: str) -> dict:
    """What this user may see and change in this room."""
    room = loop.client.rooms.get(room_id)
    if room is None or not loop.is_person(room, user):
        return {"error": "you're not in that room"}
    local = user.split(":")[0].lstrip("@")
    agent = ownership.agent_id(user) if registry.owns(user, registry.agent_of(user)) else None   # by full ID
    waiting = loop.pending.get(room_id, {})
    mine = my_agents(loop, room, room_id, user)
    return {"user": local, "agents": mine, "can_add": bool(mine) and len(registry.agents_of(user)) < registry.per_person(), "room": room.display_name, "repo": await room_state.repo_of(loop.client, room_id),
            "moderator": await room_state.is_moderator(loop.client, room, user),
            "mode": await loop.level(room_id), "agent": agent and agent.split(":")[0][1:],
            "visibility": await project_rooms.visibility(loop.client, room_id), "runs_in": await runs_in(loop, room_id, user),
            "follow": registry.follow(user) if agent else None,
            "version": (customize.in_use().get(registry.local_name(user) or "") or "")[:7] if agent else None,
            "web": bool(agent and "web" in loop.grants_cache.get(room_id, {}).get(agent, {})),
            "remote": bool(agent and registry.load().get(user, {}).get("agent") == registry.REMOTE),   # on their computer
            "online": loop.agent_online(agent.split(":")[0][1:]) if agent else None,
            "waiting": {"kind": waiting.get("kind") or "mode", "mine": waiting.get("by") == user,
                        "id": waiting.get("event_id"), "items": [i["title"] for i in waiting.get("items", [])]}
                       if waiting else None,
            "html": panel_html(loop, room_id) if panel_html else ""}


async def runs_in(loop, room_id: str, user: str) -> dict | None:
    """Where this room's repo is run from, when this room only discusses it (pm_projects.py): that
    room's name and link only for its own members (others may not be allowed to know it)."""
    other = await room_state.runs_in_of(loop.client, room_id)
    if not other:
        return None
    there = loop.client.rooms.get(other)
    if there is None or user not in there.users or user in there.invited_users:
        return {"name": "another room", "link": ""}
    return {"name": loop.room_name(other), "link": f"{ELEMENT_ORIGIN}/#/room/{urllib.parse.quote(other)}"}


async def act(loop, room_id: str, user: str, action: str, value: str, agent: str = "") -> str:
    """Do one change as `user`, through @pm's own checks (each also posts its usual chat line);
    `agent`: which of their agents (empty: their first)."""
    room = loop.client.rooms.get(room_id)
    if room is None or not loop.is_person(room, user):     # a joined human (never an agent or a bot)
        return "you're not in that room"
    if action == "mode" and value in github.LEVELS:
        if value != "off" and not await room_state.is_moderator(loop.client, room, user):
            return "only the room owner or a maintainer can change the mode"
        await loop.say(room_id, await loop.change_level(room_id, value) or
                       f"Mode changed to {value} by {user.split(':')[0][1:]} (from the console).")
        return "ok"
    if action == "follow" and value in registry.FOLLOW:
        done = await loop.set_follow(room, user, value, agent or None)
        return "ok" if done is not False else "that isn't one of your agents"
    if action == "web" and value in ("on", "off"):
        done = await loop.set_grant(room, user, "web", value == "on", agent or None)
        return "ok" if done is not False else "that isn't one of your agents (or I couldn't save it)"
    if action == "add":                   # pm_agents.py: the Connect page goes to our private chat
        dm = await loop.private_room(user)
        return await loop.start_add(dm, user, value.strip() or None) if dm else "I couldn't open a private chat with you"
    if action == "repo":
        return await loop.set_repo(room, user, value.strip())   # its own checks; also answered in the chat
    if action == "check":
        return await check_repo(loop, room, user)
    if action == "visibility":            # pm_projects.py: who can find and join this room
        answer = await loop.set_visibility(room, user, value)
        await loop.say(room_id, answer)
        return answer
    if action == "goback":                # customize.py: official code, so it works when theirs is broken
        local, _, server = user.lstrip("@").partition(":")
        if server != loop.user_id.partition(":")[2]:   # a person from another server never maps to ours
            return "only agents this server runs can go back to the official version"
        customize.go_back(local)
        return "ok"
    if action == "disconnect":            # pm_presence.py: their own computer's agent signs out for good
        done = await loop.disconnect(await loop.private_room(user) or room_id, user, agent or None)
        return "ok" if done else "couldn't disconnect it (see my message)"
    if action == "approve":
        if loop.pending.get(room_id, {}).get("event_id") != value:
            return "that's no longer what's waiting; look again"   # approve exactly what you saw
        return "ok" if await loop.confirm(room, user) else "nothing of yours is waiting for approval"
    return "unknown action"


async def check_repo(loop, room, user: str) -> str:
    """Is this room's repo ready for the loop? Read-only: changing the repo's settings stays with its
    GitHub owner (github_setup.py ruleset), never with @pm."""
    if not await room_state.is_moderator(loop.client, room, user):
        return "only the room owner or a maintainer can run the check"
    repo = await room_state.repo_of(loop.client, room.room_id)
    if repo is None:
        return "this room has no repo yet"
    agents = [a.split(":")[0].lstrip("@") for a in loop.agents_in(room)]
    if not agents:
        return "no agents in this room yet (each person's agent joins with them)"
    _, problems = await github_setup.readiness(repo, agents, detail=False)
    if not problems:
        return f"✓ {repo} is ready: {', '.join(agents)} can write, merges need an approving review, agent-ok exists."
    return "\n".join([f"{repo} isn't ready yet:", *(f"✗ {p}" for p in problems),
                      f"The repo's owner fixes the rule and label with: python github_setup.py ruleset --repo {repo}"])


def allowed(request, post: bool) -> bool:
    """Only requests addressed to this machine; POSTs must be JSON from our own page."""
    hosts = allowed_hosts()
    if request.host.split(":")[0] not in hosts:
        return False
    if post:
        origin = request.headers.get("Origin")
        if request.content_type != "application/json" or (origin is not None and origin not in allowed_origins()):
            return False
    return True


NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def routes(loop, panel_html_fn) -> list:
    import connect_page                       # here, not at the top: it uses this module (an import cycle)
    global panel_html
    panel_html = panel_html_fn

    def reply(data, status=200):
        return web.json_response(data, status=status, headers=NO_STORE)

    async def auth_openid(request):
        if not allowed(request, True):
            return reply({"error": "refused"}, 403)
        user = await asyncio.to_thread(openid_user, (await request.json()).get("access_token", ""))
        return reply({"session": new_session(user)} if user else {"error": NOT_VERIFIED})

    async def auth_link(request):
        if not allowed(request, True):
            return reply({"error": "refused"}, 403)
        found = redeem(str((await request.json()).get("code", "")))
        return reply({"session": new_session(*found)} if found else {"error": "that link expired or was used"})

    def scoped(request, room):
        found = session_of(request)
        if not found or (found[1] and found[1] != room):
            return None                       # a link's session works only for its own room
        return found[0]

    async def state(request):
        user = scoped(request, request.query.get("room", "")) if allowed(request, False) else None
        if not user:
            return reply({"error": "sign in"}, 401)
        return reply(await view(loop, request.query.get("room", ""), user))

    async def action(request):
        body = await request.json() if allowed(request, True) else {}
        user = scoped(request, str(body.get("room", ""))) if body else None
        if not user:
            return reply({"error": "sign in"}, 401)
        return reply({"result": await act(loop, str(body.get("room", "")), user, str(body.get("action", "")),
                                          str(body.get("value", "")), str(body.get("agent", ""))[:120])})
    return [web.post("/auth/openid", auth_openid), web.post("/auth/link", auth_link),
            web.get("/console/state", state), web.post("/console/action", action),
            *connect_page.routes()]                # the Connect page and the Windows installer


panel_html = None                             # set by routes(): panel.render, kept out of an import cycle
