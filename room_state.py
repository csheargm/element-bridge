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


def level_of(content: dict) -> str:
    """The loop level in a LEVEL_EVENT's content, for github.REPO; anything else is off."""
    lvl = content.get("level") if isinstance(content, dict) and content.get("repo") == github.REPO else None
    return lvl if lvl in github.LEVELS else "off"


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
