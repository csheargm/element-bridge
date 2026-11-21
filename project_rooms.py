"""Who can find and join a project room: its visibility, set by its owners and maintainers in the
Hagency panel or by telling @pm, and kept in the room's own state (written by @pm only, like its repo):
  - private (the default): unlisted; joinable by invite, or by asking @pm with the exact repo name;
  - listed: in "Find a project" and the server's room directory; join rule "knock", so Element shows
    "Ask to join", and @pm asks the room's owners (👍 let in · 👎 decline: pm_projects.py);
  - open: listed, and anyone joins directly (join rule "public").
A room bound to a private GitHub repo can't be open, and a listing never shows a private repo's name.
Listed and open rooms are in this server's room directory, which other Matrix servers can read too.
Joining never gives anyone a say in how the room runs: mode, repo and approvals stay with moderators.
"""
import json
import logging
import re
import urllib.parse

from nio import AsyncClient, RoomGetStateResponse

import github
import room_state
from bridge_config import SERVER_NAME
from local_signup import WELCOME_ALIAS

log = logging.getLogger("loop")
LOBBY_LOCALPART = "lobby"
LOBBY_ALIAS = f"#{LOBBY_LOCALPART}:{SERVER_NAME}"
FRONT_DOORS = (WELCOME_ALIAS, LOBBY_ALIAS)    # @pm's own rooms: never a project room
VISIBILITY_EVENT = "xyz.hagencylabs.visibility"
PRIVATE, LISTED, OPEN = "private", "listed", "open"
VISIBILITIES = (PRIVATE, LISTED, OPEN)
JOIN_RULE = {PRIVATE: "invite", LISTED: "knock", OPEN: "public"}
DIRECTORY = {PRIVATE: "private", LISTED: "public", OPEN: "public"}
WORDS = {PRIVATE: "private: people join by invite, or by asking me with the exact repo name",
         LISTED: ("listed: shown in Find a project and the room directory (its name and topic are public); "
                  "people ask to join and an owner lets them in"),
         OPEN: "open: listed, and anyone can join"}
NOT_OPEN = "This room works on a private repo, so it can't be open to anyone; it can be listed (people ask to join)."


def plain(text: str) -> str:
    """A name someone chose (a room's), shown as text in my markdown messages: no links, emphasis or
    line breaks that could pass for my own instructions (GPT review)."""
    flat = " ".join(str(text).split())[:80]
    return re.sub(r"([\\`*_{}\[\]()<>+!|~-])", r"\\\1", flat)   # "#": github.linkify links issues


async def join_rule(client: AsyncClient, room_id: str) -> str:
    """The room's join rule now, asked of the server; "public" when unreadable (fail closed)."""
    r = await client.room_get_state_event(room_id, "m.room.join_rules", "")
    content = getattr(r, "content", None)
    rule = content.get("join_rule") if isinstance(content, dict) else None
    return rule if isinstance(rule, str) else "public"


def visibility_in(events: list, pm: str) -> str:
    """The room's visibility, only if @pm wrote it (anyone else's is ignored): private by default."""
    for ev in events:
        if ev.get("type") == VISIBILITY_EVENT and ev.get("state_key") == "" and ev.get("sender") == pm:
            value = (ev.get("content") or {}).get("visibility")
            return value if value in VISIBILITIES else PRIVATE
    return PRIVATE


async def visibility(client: AsyncClient, room_id: str) -> str:
    r = await client.room_get_state(room_id)
    return visibility_in(r.events, room_state.pm_id(client)) if isinstance(r, RoomGetStateResponse) else PRIVATE


async def repo_is_private(repo: str | None) -> bool:
    """True unless GitHub says the repo is public (an unreadable repo counts as private)."""
    if repo is None:
        return False
    try:
        info = await github.run_json(["api", f"repos/{repo}"])
    except github.GitHubError as e:
        log.warning("can't tell whether %s is public: %s", repo, str(e)[:160])
        return True
    return not (isinstance(info, dict) and info.get("private") is False)


def refusal(value: str, repo_private: bool) -> str | None:
    """Why `value` isn't allowed for a room (None: it is)."""
    if value not in VISIBILITIES:
        return "Say private, listed or open."
    if value == OPEN and repo_private:
        return NOT_OPEN
    return None


async def publish(client: AsyncClient, room_id: str, public: bool) -> bool:
    """List or unlist the room in this server's room directory (nio has no call for it)."""
    path = f"/_matrix/client/v3/directory/list/room/{urllib.parse.quote(room_id)}"
    resp = await client.send("PUT", path, json.dumps({"visibility": "public" if public else "private"}),
                             headers={"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"})
    status = getattr(resp, "status", 0)
    if hasattr(resp, "release"):
        resp.release()                        # the body isn't needed: free the connection
    return status == 200


async def apply(client: AsyncClient, room_id: str, value: str) -> str | None:
    """Join rule, directory entry, then the record (last: it says what is really in force).
    Returns an error line, or None when done."""
    rule = await client.room_put_state(room_id, "m.room.join_rules", {"join_rule": JOIN_RULE[value]})
    if not hasattr(rule, "event_id"):
        return f"⚠️ I couldn't change who can join ({getattr(rule, 'status_code', rule)}); am I a moderator here?"
    if not await publish(client, room_id, DIRECTORY[value] == "public"):
        return "⚠️ I changed who can join, but couldn't update the room directory; please try again."
    saved = await client.room_put_state(room_id, VISIBILITY_EVENT, {"visibility": value})
    return None if hasattr(saved, "event_id") else f"⚠️ I couldn't save the setting: {getattr(saved, 'status_code', saved)}"


def listing_line(name: str, room_id: str, server: str, repo: str | None, repo_private: bool) -> str:
    """One room in "Find a project": its name and a link; a repo's name only when the repo is public."""
    link = f"https://matrix.to/#/{urllib.parse.quote(room_id)}?via={urllib.parse.quote(server)}"
    shown = f" · {repo}" if repo and not repo_private else ""
    return f"- **{plain(name)}**{shown}: [open it]({link})"
