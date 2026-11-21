"""A room's settings live in its own Matrix state, not in config files: its repo and loop level
(github.LEVEL_EVENT). @pm writes them after its own checks (pm_settings.py), and they count only
if @pm wrote them (binding_in).
"""
from nio import AsyncClient, MatrixRoom, RoomGetStateResponse

import github

MODERATOR_LEVEL = 50


def pm_id(client: AsyncClient) -> str:
    """@pm on this client's server (the bridge's agents read the binding too, as themselves)."""
    return f"@{github.PM_BOT}:{client.user_id.split(':', 1)[1]}"


def binding_in(events: list, pm: str) -> dict:
    """The room's LEVEL_EVENT content, only if @pm wrote it: @pm writes it after its own checks
    (moderator, allowed repo, the requester's agent can write there). A room's creator controls its
    power levels, so the server's own check alone would let anyone they choose write it by hand."""
    for ev in events:
        if ev.get("type") == github.LEVEL_EVENT and ev.get("state_key") == "" and ev.get("sender") == pm:
            return ev.get("content") if isinstance(ev.get("content"), dict) else {}
    return {}


async def read_binding(client: AsyncClient, room_id: str) -> dict:
    r = await client.room_get_state(room_id)
    return binding_in(r.events, pm_id(client)) if isinstance(r, RoomGetStateResponse) else {}


def repo_in(content: dict) -> str | None:
    """The room's repo from its LEVEL_EVENT. Stored as a list (one repo per room today; several
    later), with the older single "repo" field still understood."""
    if not isinstance(content, dict):
        return None
    if "repos" in content:               # the list wins; the single "repo" is only for older rooms
        repos = content["repos"]
        repo = repos[0] if isinstance(repos, list) and len(repos) == 1 else None
    else:
        repo = content.get("repo")
    return github.allowed_repo(repo)     # also when someone wrote the state by hand: only allowed repos


def runs_in(content: dict) -> str | None:
    """The room that runs this room's repo, when this one only discusses it (one project room per
    repo: a separate room may talk about the repo but never runs the build loop for it)."""
    other = content.get("runs_in") if isinstance(content, dict) else None
    return other if isinstance(other, str) and other.startswith("!") else None


def level_of(content: dict) -> str:
    """The loop level in a LEVEL_EVENT's content; a room without a repo, or one that only discusses
    a repo another room runs, is off."""
    lvl = content.get("level") if repo_in(content) and not runs_in(content) else None
    return lvl if lvl in github.LEVELS else "off"


def content(level: str, repo: str, runs_in_room: str | None = None) -> dict:
    return {"level": level, "repos": [repo], "repo": repo, **({"runs_in": runs_in_room} if runs_in_room else {})}


async def repo_of(client: AsyncClient, room_id: str) -> str | None:
    """The repo this room works on (one room, one repo), or None if nobody has set one."""
    return repo_in(await read_binding(client, room_id))


async def runs_in_of(client: AsyncClient, room_id: str) -> str | None:
    """The room that runs this room's repo, when this room only discusses it (else None)."""
    return runs_in(await read_binding(client, room_id))


async def bound(client: AsyncClient, room_id: str) -> bool:
    """A moderator has given this room a repo. Only then does @pm show issues, status and the panel
    there: a room anyone can create must not become a window into a repo."""
    return await repo_of(client, room_id) is not None


async def level(client: AsyncClient, room_id: str) -> str:
    return level_of(await read_binding(client, room_id))


async def moderators(client: AsyncClient, room: MatrixRoom) -> list[str]:
    """The room's owners and maintainers who are people (joined, never me, an agent or a bot)."""
    people = [u for u in room.users if u not in room.invited_users and u != client.user_id
              and not u.split(":")[0].endswith(("-agent", "-bot"))]
    return [u for u in sorted(people) if await is_moderator(client, room, u)]


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
