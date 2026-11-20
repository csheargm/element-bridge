"""A room's settings live in its own Matrix state, not in config files: the loop level
(github.LEVEL_EVENT). Only moderators may
change them — the server enforces that (state events need power 50) and @pm checks it (pm_chat.py).
"""
from nio import AsyncClient, MatrixRoom, RoomGetStateEventResponse, RoomGetStateResponse

import github

MODERATOR_LEVEL = 50


async def read(client: AsyncClient, room_id: str, event_type: str) -> dict:
    r = await client.room_get_state_event(room_id, event_type, "")
    return r.content if isinstance(r, RoomGetStateEventResponse) and isinstance(r.content, dict) else {}


def repo_in(content: dict) -> str | None:
    """The room's repo from its LEVEL_EVENT. Stored as a list (one repo per room today; several
    later), with the older single "repo" field still understood."""
    if not isinstance(content, dict):
        return None
    repos = content.get("repos")
    repo = repos[0] if isinstance(repos, list) and repos else content.get("repo")
    return github.allowed_repo(repo)     # also when someone wrote the state by hand: only allowed repos


def level_of(content: dict) -> str:
    """The loop level in a LEVEL_EVENT's content; a room without a repo is off."""
    lvl = content.get("level") if repo_in(content) else None
    return lvl if lvl in github.LEVELS else "off"


def content(level: str, repo: str) -> dict:
    return {"level": level, "repos": [repo], "repo": repo}


async def repo_of(client: AsyncClient, room_id: str) -> str | None:
    """The repo this room works on (one room, one repo), or None if nobody has set one."""
    return repo_in(await read(client, room_id, github.LEVEL_EVENT))


async def bound(client: AsyncClient, room_id: str) -> bool:
    """A moderator has given this room a repo. Only then does @pm show issues, status and the panel
    there: a room anyone can create must not become a window into a repo."""
    return await repo_of(client, room_id) is not None


async def level(client: AsyncClient, room_id: str) -> str:
    return level_of(await read(client, room_id, github.LEVEL_EVENT))


async def is_moderator(client: AsyncClient, room: MatrixRoom, sender: str) -> bool:
    if room.power_levels.get_user_level(sender) >= MODERATOR_LEVEL:
        return True
    # Room v12+: creators outrank everyone but are NOT listed in power_levels.users, and
    # matrix-nio doesn't model that, so read the create event ourselves.
    state = await client.room_get_state(room.room_id)
    if not isinstance(state, RoomGetStateResponse):
        return False
    for ev in state.events:
        if ev.get("type") == "m.room.create":
            content = ev.get("content", {})
            version = str(content.get("room_version", "1"))
            creators = {ev.get("sender"), *content.get("additional_creators", [])}
            return version.isdigit() and int(version) >= 12 and sender in creators
    return False
