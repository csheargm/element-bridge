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
    """The capabilities granted to `agent_id` in the room (only trusted if @pm wrote them)."""
    state = api("GET", f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/state", token=token)
    for ev in state if isinstance(state, list) else []:
        if ev.get("type") == GRANTS_EVENT and ev.get("state_key") == "" and ev.get("sender") == PM_ID:
            content = ev.get("content", {})
            if content.get("repo") != github.REPO:
                return set()
            caps = content.get("grants", {}).get(agent_id, {})
            return {c for c in caps if c in CAPS} if isinstance(caps, dict) else set()
    return set()


def with_grant(content: dict, agent_id: str, cap: str, owner: str, on: bool) -> dict:
    """New state content with `cap` switched on/off for `agent_id`."""
    grants = dict(content.get("grants", {})) if content.get("repo") == github.REPO else {}
    caps = dict(grants.get(agent_id, {}))
    if on:
        caps[cap] = {"by": owner, "ts": int(time.time())}
    else:
        caps.pop(cap, None)
    grants[agent_id] = caps
    return {"repo": github.REPO, "grants": grants}
