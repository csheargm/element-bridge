"""A room's settings, changed only through here (chat and the console both call these):
  - its repo: one room, one repo, chosen by the owner or a maintainer ("@pm this room works on
    owner/name" or the console). Stored as a list in the room's LEVEL_EVENT for multi-repo later;
    a project that spans repos is a Matrix Space with one room per repo;
  - its mode (off / assist / autonomous): stopping is open to anyone, never binds a room;
  - the one-time console link, sent in a private room (it is a credential).
Mixed into loop.Loop (client, user_id, say, level, state, room_repos, pending, dm_rooms, kick).
"""
import asyncio
import logging
import urllib.parse

from nio import MatrixRoom, RoomPreset

import console
import github
import github_app
import github_link
import github_setup
import registry
import room_state
from loop_progress import ACTIVE
from pm_projects import RUN_ELSEWHERE, Projects
from project_rooms import plain
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
INSTALL_RECHECK_S = 30        # after an install link: look again this often...
INSTALL_RECHECKS = 20         # ...this many times (10 minutes), then the person asks again
BUSY = "Work is still running on the current repo; say \"stop\" and let it finish first."
KEEP = object()               # write_level: keep the room's "only discusses its repo" as it is
NEED_POWER = ("I can't save this room's settings yet: Matrix lets only moderators do that. Room owner, please "
              "make me a Moderator (Room settings → Roles & Permissions → @pm → Moderator), then ask again.")


NOT_CONNECTED = ("First connect your GitHub account, so I know it's yours: click **Connect GitHub** in the Hagency "
                 "panel, or say \"connect my github\" in our private chat. Then ask again.")


def refusal(name: str) -> str:
    if not github_app.configured():
        return (f"I can't use {name} for this room: this server must be set up for it (its operator lists repos "
                "in HAGENCY_REPOS) and your agent's GitHub account must be able to write there.")
    return (f"I can't use {name} for this room. This server must list it (its operator sets HAGENCY_REPOS), the "
            f"Hagency Rooms GitHub App must be on it (read-only; the repo's owner clicks Install: "
            f"{github_app.install_url()}), and your connected GitHub account must be able to write there. If the "
            "App is being installed now, I'll finish setting up this room once it's there.")


async def may_write(user: str, repo: str) -> bool:
    """The person can write to `repo`: so binding a room to a repo needs real access to it, not just a
    room of one's own (anyone can create a room). With the GitHub App (the public server) that is the
    GitHub account they connected (github_link.py, ADR 0007), read as the App; else their own agent's
    GitHub identity (local dev: gh). Reads only; nothing is changed."""
    if github_app.configured():
        return await github_link.may_write(user, repo)
    agent = registry.agent_of(user)           # by full ID: @anna:elsewhere is not our anna
    if not registry.owns(user, agent):
        return False
    return bool((await github_setup.identity(agent, repo)).get("push"))


def is_safe_chat(room: MatrixRoom) -> bool:
    """One of my private chats made with the safeguards above (older ones are replaced, not reused)."""
    return getattr(room, "history_visibility", "") == HISTORY and room.power_levels.defaults.invite >= PM_ONLY


def is_my_chat(room: MatrixRoom, me: str, dm_rooms: dict) -> bool:
    """One of my private chats with someone (now or an older one): never a project room."""
    return len(room.users) <= 2 and me in room.users and (room.room_id in dm_rooms.values()
                                                           or getattr(room, "name", None) in CHAT_NAMES)


class Settings(Projects):
    async def set_repo(self, room: MatrixRoom, sender: str, repo: str) -> str:
        """One room, one repo: the owner or a maintainer names it; never swapped under running work.
        Says the answer in the room and returns it (the console shows it too)."""
        answer = await self.choose_repo(room, sender, repo)
        await self.offer_conflict_buttons(room.room_id, answer, await self.say(room.room_id, answer))   # pm_projects.py
        return answer

    async def bind_checked(self, rid: str, sender: str, repo: str, separate_from: str | None) -> str:
        """Under the lock: one project room per repo, a private repo's room never open, then bind."""
        if self.busy_on_other_repo(rid, repo):    # before anything changes (closing the room included)
            return BUSY
        try:
            other = await self.primary_room_for(repo, exclude=rid)   # pm_projects.py: every room, read now
        except room_state.StateUnreadable as e:   # never a second primary on a guess (GPT review)
            log.warning("one-room-per-repo check failed: %s", e)
            return "I couldn't check the other rooms on this repo just now; please try again in a minute."
        if other and other != separate_from:
            return self.conflict_answer(rid, sender, repo, other)
        refused, note = await self.close_for_private(rid, repo)
        return refused or (await self.bind(rid, repo, other)) + note

    async def choose_repo(self, room: MatrixRoom, sender: str, name: str, separate_from: str | None = None) -> str:
        rid, repo = room.room_id, github.allowed_repo(name)
        if not await room_state.is_moderator(self.client, room, sender):
            return "Only the room owner or a maintainer can choose this room's repo."
        if not github.REPO_RE.match(name):
            return "Tell me the repo as owner/name, e.g. \"@pm this room works on octocat/hello\"."
        if github_app.configured() and github_link.link_of(sender) is None:
            return NOT_CONNECTED                  # before the repo is looked at: says nothing about it
        try:
            allowed = repo is not None and await may_write(sender, repo)
        except github_app.NotInstalled:           # access is read as the App: not until it's there
            self.watch_install(room, sender, repo, quiet=True)
            allowed = False
        if not allowed:                           # one answer for each: never confirms a name
            return refusal(name)
        missing = await self.app_missing(room, sender, repo)
        if missing:
            return missing
        self.stop_install_watch(rid)              # this choice is final: an older install wait ends
        async with self.lock:                     # the same lock as polling and results: nothing starts in between
            binding = asyncio.ensure_future(self.bind_checked(rid, sender, repo, separate_from))
            try:
                return await asyncio.shield(binding)
            except asyncio.CancelledError:        # an install wait cancelled mid-bind: the bind (Matrix state
                await binding                     # and @pm's own records) finishes whole, under the lock
                raise

    async def app_missing(self, room: MatrixRoom, sender: str, repo: str) -> str | None:
        """None when @pm can read `repo`; else the one-click install link (and I look again after)."""
        try:
            if await github_app.installed(repo):
                return None
        except github.GitHubError as e:          # its text never carries the key or a token
            return f"I can't check {repo} on GitHub right now ({str(e)[:160]}); please try again in a minute."
        self.watch_install(room, sender, repo)
        minutes = INSTALL_RECHECK_S * INSTALL_RECHECKS // 60
        return (f"One step first: install the Hagency Rooms GitHub App on {repo} (read-only; the repo's owner or "
                f"an org admin clicks Install): {github_app.install_url()}\nI'll look again every "
                f"{INSTALL_RECHECK_S} seconds for {minutes} minutes and finish setting up this room once it's there.")

    def watch_install(self, room: MatrixRoom, sender: str, repo: str, quiet: bool = False) -> None:
        """quiet: their access isn't known yet (it's read as the App), so the wait says nothing unless
        it can bind: a reply only for listed repos would confirm the name (GPT review, Connect GitHub)."""
        watch = self.install_watches.get(room.room_id)
        if watch is None or watch[0] != repo or watch[1].done():   # a newer choice replaces an older wait
            self.stop_install_watch(room.room_id)
            self.install_watches[room.room_id] = (repo, asyncio.create_task(self.await_install(room, sender, repo, quiet)))

    async def await_install(self, room: MatrixRoom, sender: str, repo: str, quiet: bool = False) -> None:
        """After an install link: once the App is on `repo`, ask again for the person (every check
        re-runs: moderator, allowed repo, write access). The wait stays registered until it ends, so
        a newer choice in the room cancels it even during that last bind (GPT review, GitHub App)."""
        try:
            for _ in range(INSTALL_RECHECKS):
                await asyncio.sleep(INSTALL_RECHECK_S)
                try:
                    if await github_app.installed(repo):
                        break
                except github.GitHubError as e:  # GitHub unreachable for a moment: the next look may work
                    log.warning("install re-check for %s: %s", repo, str(e)[:160])
            else:
                if quiet:
                    return
                await self.say(room.room_id, f"I still don't see the Hagency Rooms app on {repo}. Once it's "
                                             f"installed, say \"@pm this room works on {repo}\" again.")
                return
            if quiet and not await self.may_bind_quietly(sender, repo):
                return
            await self.set_repo(room, sender, repo)
        finally:
            if self.install_watches.get(room.room_id, ("", None))[1] is asyncio.current_task():
                del self.install_watches[room.room_id]     # (a newer wait may have replaced this one)

    async def may_bind_quietly(self, sender: str, repo: str) -> bool:
        try:
            return await may_write(sender, repo)
        except github_app.NotInstalled:   # gone again already: stay quiet, they can ask again
            return False

    def stop_install_watch(self, rid: str) -> None:
        watch = self.install_watches.get(rid)
        if watch is None or watch[1] is asyncio.current_task():   # the wait itself binding: it stays registered
            return
        del self.install_watches[rid]
        watch[1].cancel()

    def busy_on_other_repo(self, rid: str, repo: str) -> bool:
        return any(v.get("room") == rid and v.get("repo") != repo and v["stage"] in ACTIVE for v in self.state.values())

    async def bind(self, rid: str, repo: str, runs_in: str | None = None) -> str:
        if self.busy_on_other_repo(rid, repo):
            return BUSY
        current = await room_state.repo_of(self.client, rid)      # the binding itself, not the poll's cache
        keep = current == repo and runs_in is None
        failed = await self.write_level(rid, None if keep else "off", repo, runs_in is not None)   # None: as it is, read under the lock
        if failed:
            return failed
        if runs_in:                               # which room runs it: mine to know, not this room's state
            self.discussions()[rid] = runs_in
        elif rid in self.discussions():           # (no write when there's nothing to forget)
            self.discussions().pop(rid, None)
        if current != repo:                       # a plan made for the old repo is never filed in the new one
            self.pending.pop(rid, None)
            self.plans.pop(rid, None)
            self.last_plans.pop(rid, None)
        self.room_repos[rid] = repo
        self.drop_conflict_offers(rid)            # pm_projects.py: an older 🙋/➕ question is settled
        if runs_in:
            return f"This room now discusses **{repo}**; its build loop runs in the room that already works on it."
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
        await self.say(dm, f"Your Hagency console for **{plain(room.display_name)}** (works once, for 10 minutes): {url}")
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
        return await self.write_level(room_id, level, unbound_ok=level == "off")   # stopping never binds a room (anyone may stop)

    async def write_level(self, room_id: str, level: str | None, repo: str | None = None, discussion=KEEP,
                          unbound_ok: bool = False) -> str | None:
        """Write the room's level (and repo): callers have checked that a moderator asked. A room that only
        discusses a repo another room runs never leaves "off". Every binding write reads and writes under
        one short lock (not the poll's), so a "stop" can't write back a repo a rebind just replaced."""
        async with self.__dict__.setdefault("_binding_lock", asyncio.Lock()):
            bound_repo, bound_discussion = await room_state.binding_of(self.client, room_id)   # one read
            repo = repo or bound_repo
            if repo is None:
                return None if unbound_ok else NO_REPO
            level = level or await self.level(room_id)   # "keep the level": the one in force now, not before the lock
            only_discusses = bound_discussion if discussion is KEEP else discussion
            if only_discusses and level != "off":
                return RUN_ELSEWHERE
            resp = await self.client.room_put_state(room_id, github.LEVEL_EVENT, room_state.content(level, repo, only_discusses))
        if not hasattr(resp, "event_id"):
            if getattr(resp, "status_code", "") == "M_FORBIDDEN":    # a room someone made: I joined at power 0
                return NEED_POWER
            return f"⚠️ I couldn't save this room's settings: {resp}"
        self.kick()                           # start (or stop) now, not at the next poll
        return None
