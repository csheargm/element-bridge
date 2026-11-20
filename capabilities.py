"""What an owner lets their own agent do in a room, beyond the project itself. Grants are room
state written only by @pm (on the owner's own words, for the owner's own agent) and trusted only
when @pm wrote them. Anything outside the project that isn't a read (posting elsewhere, writing a
shared document, contacting someone) also needs the owner's ✅ per action (mcp_web.ask_owner).

  web — search the web and read pages (never this machine or its network)
"""
import time
import urllib.parse

import github
from setup_accounts import SERVER_NAME, api

GRANTS_EVENT = "xyz.hagencylabs.grants"
CAPS = {"web": "search the web and read pages"}
PM_ID = f"@{github.PM_BOT}:{SERVER_NAME}"


def granted(token: str, room_id: str, agent_id: str) -> set[str]:
    """The capabilities granted to `agent_id` in the room. One state record per agent (key_for), trusted
    only if @pm wrote it."""
    state = api("GET", f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/state", token=token)
    return caps_in(state if isinstance(state, list) else [], agent_id)


def key_for(agent_id: str) -> str:
    """The grant record's state key. (Keys starting with "@" are reserved by Matrix for that user.)"""
    return f"agent:{agent_id}"


def agent_from_key(key: str) -> str:
    return key.removeprefix("agent:")


def caps_in(state: list, agent_id: str) -> set[str]:
    for ev in state:
        if ev.get("type") == GRANTS_EVENT and ev.get("state_key") == key_for(agent_id) and ev.get("sender") == PM_ID:
            content = ev.get("content", {})
            caps = content.get("caps", {}) if content.get("repo") == github.REPO else {}
            return {c for c in caps if c in CAPS} if isinstance(caps, dict) else set()
    return set()


def record(current: set[str], cap: str, owner: str, on: bool) -> dict:
    """The agent's new grant record (built from what @pm itself wrote, never from anyone else's)."""
    caps = (current | {cap}) if on else (current - {cap})
    return {"repo": github.REPO, "caps": {c: {"by": owner, "ts": int(time.time())} for c in sorted(caps)}}
