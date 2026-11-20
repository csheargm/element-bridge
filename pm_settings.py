"""A room's settings, changed only through here (chat and the console both call these):
  - its repo: one room, one repo, chosen by the owner or a maintainer ("@pm this room works on
    owner/name" or the console). Stored as a list in the room's LEVEL_EVENT for multi-repo later;
    a project that spans repos is a Matrix Space with one room per repo;
  - its mode (off / assist / autonomous): stopping is open to anyone, never binds a room;
  - the one-time console link, sent in a private room (it is a credential).
Mixed into loop.Loop (client, user_id, say, level, state, room_repos, pending, dm_rooms, kick).
"""
import logging
import urllib.parse

from nio import MatrixRoom, RoomPreset

import console
import github
import github_setup
import registry
import room_state
from loop_progress import ACTIVE
from setup_accounts import DISPLAY_NAMES

log = logging.getLogger("loop")
# Codes and console links go to my private chats: only I can invite anyone there, and someone
# invited later can't read what was said before their invite (GPT review 23). "invited", not
# "joined": the person reads my offer, sent while they were still invited.
HISTORY = "invited"
PRIVATE_HISTORY = {"type": "m.room.history_visibility", "state_key": "", "content": {"history_visibility": HISTORY}}
PM_ONLY = 100
CHAT_NAMES = (DISPLAY_NAMES["pm"], "Hagency (private)")   # my private chats' names, now and before
NO_REPO = ("This room has no GitHub repo yet. Say \"@pm this room works on owner/name\" (owner or maintainer), "
           "or set it in the Hagency console.")
NEED_POWER = ("I can't save this room's settings yet: Matrix lets only moderators do that. Room owner, please "
              "make me a Moderator (Room settings → Roles & Permissions → @pm → Moderator), then ask again.")


async def may_write(user: str, repo: str) -> bool:
    """The person's own agent's GitHub account can write to `repo`: so binding a room to a repo needs
    real access to it, not just a room of one's own (anyone can create a room). A read with that
    agent's identity; nothing is changed."""
    agent = registry.agent_of(user)           # by full ID: @anna:elsewhere is not our anna
    if not registry.owns(user, agent):
        return False
    return bool((await github_setup.identity(agent, repo)).get("push"))


def is_safe_chat(room: MatrixRoom) -> bool:
    """One of my private chats made with the safeguards above (older ones are replaced, not reused)."""
    return getattr(room, "history_visibility", "") == HISTORY and room.power_levels.defaults.invite >= PM_ONLY


class Settings:
    async def set_repo(self, room: MatrixRoom, sender: str, repo: str) -> str:
        """One room, one repo: the owner or a maintainer names it; never swapped under running work.
        Says the answer in the room and returns it (the console shows it too)."""
        answer = await self.choose_repo(room, sender, repo)
        await self.say(room.room_id, answer)
        return answer

    async def choose_repo(self, room: MatrixRoom, sender: str, name: str) -> str:
        rid, repo = room.room_id, github.allowed_repo(name)
        if not await room_state.is_moderator(self.client, room, sender):
            return "Only the room owner or a maintainer can choose this room's repo."
        if not github.REPO_RE.match(name):
            return "Tell me the repo as owner/name, e.g. \"@pm this room works on octocat/hello\"."
        if repo is None or not await may_write(sender, repo):    # one answer for both: never confirms a name
            return (f"I can't use {name} for this room: this server must be set up for it (its operator lists repos "
                    "in HAGENCY_REPOS) and your agent's GitHub account must be able to write there.")
        async with self.lock:                     # the same lock as polling and results: nothing starts in between
            return await self.bind(rid, repo)

    async def bind(self, rid: str, repo: str) -> str:
        if any(v.get("room") == rid and v.get("repo") != repo and v["stage"] in ACTIVE for v in self.state.values()):
            return "Work is still running on the current repo; say \"stop\" and let it finish first."
        current = await room_state.repo_of(self.client, rid)      # the binding itself, not the poll's cache
        failed = await self.write_level(rid, await self.level(rid) if current == repo else "off", repo)
        if failed:
            return failed
        if current != repo:                       # a plan made for the old repo is never filed in the new one
            self.pending.pop(rid, None)
            self.plans.pop(rid, None)
            self.last_plans.pop(rid, None)
        self.room_repos[rid] = repo
        return (f"This room now works on **{repo}**. Check that it is ready (the agents' write access, the merge "
                "rule and the agent-ok label) with Check readiness under Project repo in the Hagency console.")

    async def send_console_link(self, room: MatrixRoom, sender: str) -> None:
        """A one-time sign-in link to the console for this room, sent privately (it's a credential)."""
        dm = await self.private_room(sender)
        if dm is None:
            await self.say(room.room_id, "I couldn't open a private chat with you; the console is also in this "
                                         "room's Hagency panel (room info → Extensions).")
            return
        code = console.new_link(sender, room.room_id)               # the session it gives is for this room only
        url = f"{console.public_url()}/console?room={urllib.parse.quote(room.room_id)}#login={code}"
        await self.say(dm, f"Your Hagency console for **{room.display_name}** (works once, for 10 minutes): {url}")
        await self.say(room.room_id, f"Sent you a private console link, {sender.split(':')[0].lstrip('@')}.")

    async def private_room(self, user: str) -> str | None:
        known = self.dm_rooms.get(user)
        room = self.client.rooms.get(known) if known else None
        if room is not None and set(room.users) <= {self.user_id, user} and is_safe_chat(room):
            return known                      # still just the two of us (joined or invited)
        # else a new chat: an older one ("Hagency (private)") lets them invite others, who'd read back
        resp = await self.client.room_create(is_direct=True, invite=[user], preset=RoomPreset.private_chat,
                                             name=DISPLAY_NAMES["pm"], initial_state=[PRIVATE_HISTORY],
                                             power_level_override={"invite": PM_ONLY})
        if not hasattr(resp, "room_id"):
            log.warning("couldn't open a DM with %s: %s", user, resp)
            return None
        self.dm_rooms[user] = resp.room_id
        return resp.room_id

    async def change_level(self, room_id: str, level: str) -> str | None:
        """Write the room's loop level; returns an error line, or None when done. Any explicit mode
        change cancels a confirmation still waiting (it was asked for under the old mode)."""
        if self.pending.get(room_id, {}).get("kind", "autonomous") in ("autonomous", "mode"):
            self.pending.pop(room_id, None)
        if level == "off" and not await room_state.bound(self.client, room_id):
            return None                       # stopping never binds a room to the repo (anyone may stop)
        return await self.write_level(room_id, level)

    async def write_level(self, room_id: str, level: str, repo: str | None = None) -> str | None:
        """Write the room's level (and repo): callers have checked that a moderator asked."""
        repo = repo or await room_state.repo_of(self.client, room_id)
        if repo is None:
            return NO_REPO
        resp = await self.client.room_put_state(room_id, github.LEVEL_EVENT, room_state.content(level, repo))
        if not hasattr(resp, "event_id"):
            if getattr(resp, "status_code", "") == "M_FORBIDDEN":    # a room someone made: I joined at power 0
                return NEED_POWER
            return f"⚠️ I couldn't save this room's settings: {resp}"
        self.kick()                           # start (or stop) now, not at the next poll
        return None
