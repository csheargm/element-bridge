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
import secrets
import time
import urllib.parse
import urllib.request

from aiohttp import web

import github
import ownership
import registry
import room_state
from setup_accounts import HS, SERVER_NAME

SESSION_S = 12 * 3600
LINK_S = 600
_sessions: dict[str, tuple[str, float]] = {}      # session → (user_id, expires)
_links: dict[str, tuple[str, float]] = {}         # one-time code → (user_id, expires)


def new_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = (user_id, time.time() + SESSION_S)
    return token


def user_of(request) -> str | None:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user, expires = _sessions.get(token, (None, 0))
    return user if user and time.time() < expires else None


def openid_user(access_token: str) -> str | None:
    """Ask our homeserver who an OpenID token belongs to (only local users count)."""
    url = f"{HS}/_matrix/federation/v1/openid/userinfo?access_token={urllib.parse.quote(access_token)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            sub = json.loads(r.read()).get("sub", "")
    except (OSError, ValueError):
        return None
    return sub if isinstance(sub, str) and sub.endswith(f":{SERVER_NAME}") else None


def new_link(user_id: str) -> str:
    code = secrets.token_urlsafe(24)
    _links[code] = (user_id, time.time() + LINK_S)
    return code


def redeem(code: str) -> str | None:
    user, expires = _links.pop(code, (None, 0))      # single use
    return user if user and time.time() < expires else None


async def view(loop, room_id: str, user: str) -> dict:
    """What this user may see and change in this room."""
    room = loop.client.rooms.get(room_id)
    if room is None or not ownership.joined(room, user):
        return {"error": "you're not in that room"}
    local = user.split(":")[0].lstrip("@")
    agent = ownership.agent_id(user) if registry.owner_of(registry.agent_of(local)) == local else None
    waiting = loop.pending.get(room_id, {})
    return {"user": local, "room": room.display_name, "repo": github.REPO,
            "moderator": await room_state.is_moderator(loop.client, room, user),
            "mode": await loop.level(room_id), "agent": agent and agent.split(":")[0][1:],
            "follow": registry.follow(local) if agent else None,
            "web": bool(agent and "web" in loop.grants_cache.get(room_id, {}).get(agent, {})),
            "waiting": {"kind": waiting.get("kind") or "mode", "mine": waiting.get("by") == user,
                        "items": [i["title"] for i in waiting.get("items", [])]} if waiting else None}


async def act(loop, room_id: str, user: str, action: str, value: str) -> str:
    """Do one change as `user`, through @pm's own checks (each also posts its usual chat line)."""
    room = loop.client.rooms.get(room_id)
    if room is None or not ownership.joined(room, user):
        return "you're not in that room"
    if action == "mode" and value in github.LEVELS:
        if value != "off" and not await room_state.is_moderator(loop.client, room, user):
            return "only the room owner or a maintainer can change the mode"
        await loop.say(room_id, await loop.change_level(room_id, value) or
                       f"Mode changed to {value} by {user.split(':')[0][1:]} (from the console).")
        return "ok"
    if action == "follow" and value in registry.FOLLOW:
        await loop.set_follow(room, user, value)
        return "ok"
    if action == "web" and value in ("on", "off"):
        await loop.set_grant(room, user, "web", value == "on")
        return "ok"
    if action == "approve":
        return "ok" if await loop.confirm(room, user) else "nothing of yours is waiting for approval"
    return "unknown action"


def routes(loop) -> list:
    async def auth_openid(request):
        user = await asyncio.to_thread(openid_user, str((await request.json()).get("access_token", "")))
        return web.json_response({"session": new_session(user)} if user else {"error": "couldn't verify you"})

    async def auth_link(request):
        user = redeem(str((await request.json()).get("code", "")))
        return web.json_response({"session": new_session(user)} if user else {"error": "that link expired or was used"})

    async def state(request):
        user = user_of(request)
        if not user:
            return web.json_response({"error": "sign in"}, status=401)
        return web.json_response(await view(loop, request.query.get("room", ""), user))

    async def action(request):
        user = user_of(request)
        if not user:
            return web.json_response({"error": "sign in"}, status=401)
        body = await request.json()
        result = await act(loop, str(body.get("room", "")), user, str(body.get("action", "")), str(body.get("value", "")))
        return web.json_response({"result": result})
    return [web.post("/auth/openid", auth_openid), web.post("/auth/link", auth_link),
            web.get("/console/state", state), web.post("/console/action", action)]
