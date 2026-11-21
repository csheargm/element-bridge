"""Project rooms, in plain words to @pm (each also has a click path: the home page, the Hagency panel):
  - "make me a project room [called X]": I create it, the person is its owner, I moderate it; they
    accept its invite and pick the repo in the panel (their agent follows them in, per their setting);
  - "find a project": the listed and open rooms (project_rooms.py), with links; also Element's room directory;
  - "make this room private / listed / open" (owner or maintainer): who can find and join it;
  - "ask to join owner/name": pm_joining.py.
ONE PROJECT ROOM PER REPO: @pm's loop runs per room, so two rooms on one repo would hand the same
GitHub issues out twice. When a room picks a repo another room already runs, I answer with
🙋 "Ask to join <room>" and ➕ "Make a separate room anyway": a separate room may discuss the repo,
but its mode stays off (room_state.runs_in), and its panel says where the repo is run from.
Mixed into pm_settings.Settings (client, user_id, say, offer_thumb, addressed_text, room_repos, choose_repo).
"""
import logging
import re
import time

from nio import MatrixRoom, ReactionEvent, RoomMessageText, RoomPreset

import panel
import pm_settings                            # a module object: pm_settings imports this module too
import project_rooms
import room_state
from bridge import SERVER_NAME
from pm_chat import without_quote
from pm_joining import ASKED, Joining

log = logging.getLogger("loop")
ASK, SEPARATE = "🙋", "➕"
ROOMS_PER_HOUR = 3
MAX_LISTED = 20
MAKE_RE = re.compile(r"^\W*(please\s+)?(make|create|start|set up|open)\s+(me\s+)?(a\s+)?(new\s+)?project(\s+room)?"
                     r"(\s+(called|named)\s+(?P<name>[^\n]{1,60}?))?[\s.!]*$", re.I)
FIND_RE = re.compile(r"^\W*(please\s+)?(find|list|show|browse)\s+(me\s+)?(a\s+|the\s+|some\s+)?projects?(\s+to join)?\W*$", re.I)
ASK_RE = re.compile(r"\bask to join\s+(?:https?://github\.com/)?(?P<repo>[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100})", re.I)
VIS_RE = re.compile(r"\b(?:make|set)\s+(?:this|the)\s+(?:room|project)\s+(?:to\s+)?(?P<vis>private|listed|open|public)\b"
                    r"|\b(?P<un>un)?list this (?:room|project)\b", re.I)
RUN_ELSEWHERE = ("This room only discusses its repo: the build loop for it runs in **{name}**. Ask there, "
                 "or ask to join it.")


class Projects(Joining):
    async def on_project_text(self, room: MatrixRoom, event: RoomMessageText) -> bool:
        """True if the message was one of the project commands (answered here)."""
        text = self.addressed_text(event)
        if text is None and room.room_id in getattr(self, "dm_rooms", {}).values():
            text = without_quote(event.body)          # my private chat: no need to say "@pm"
        if text is None:
            return False
        if m := MAKE_RE.match(text):
            await self.make_project_room(room, event.sender, (m.group("name") or "").strip(" \"'"))
        elif FIND_RE.match(text):
            await self.say(room.room_id, await self.find_projects())
        elif m := ASK_RE.search(text):
            target = await self.primary_room_for(m.group("repo"))
            if target and target != room.room_id:
                await self.ask_to_join(target, event.sender)
            await self.say(room.room_id, ASKED)       # the same words either way: never confirms a room
        elif m := VIS_RE.search(text):
            vis = {"public": project_rooms.OPEN}.get((m.group("vis") or "").lower(), (m.group("vis") or "").lower())
            vis = vis or (project_rooms.PRIVATE if m.group("un") else project_rooms.LISTED)
            await self.say(room.room_id, await self.set_visibility(room, event.sender, vis))
        else:
            return False
        return True

    async def make_project_room(self, origin: MatrixRoom, person: str, name: str) -> None:
        local = person.split(":")[0].lstrip("@")
        recent = self.__dict__.setdefault("_made", {}).setdefault(person, [])
        recent[:] = [t for t in recent if time.time() - t < 3600]
        if local.endswith(("-agent", "-bot")) or len(recent) >= ROOMS_PER_HOUR:
            await self.say(origin.room_id, "That's enough new rooms for now; try again in an hour.")
            return
        recent.append(time.time())
        name = name or f"{local}'s project"
        resp = await self.client.room_create(name=name, preset=RoomPreset.private_chat, invite=[person],
                                             power_level_override={"users": {person: 100}})   # they own it; I made it
        if not hasattr(resp, "room_id"):
            await self.say(origin.room_id, f"⚠️ I couldn't create the room: {getattr(resp, 'message', resp)}")
            return
        await panel.add_to_room(self.client, resp.room_id)
        await self.say(resp.room_id, f"This is **{name}**. Next: pick its GitHub repo in the Hagency panel above (or say "
                                     "\"@pm this room works on owner/name\"), then invite people: Room info → Invite.")
        await self.say(origin.room_id, f"Made **{name}**: accept its invite on the left (under Invites), then pick "
                                       "its GitHub repo in the Hagency panel at the top. Your agent comes along when you join.")

    async def find_projects(self) -> str:
        lines = []
        for rid, room in list(self.client.rooms.items()):
            if len(lines) >= MAX_LISTED or await project_rooms.visibility(self.client, rid) == project_rooms.PRIVATE:
                continue
            repo = await room_state.repo_of(self.client, rid)
            lines.append(project_rooms.listing_line(room.display_name, rid, SERVER_NAME, repo,
                                                    await project_rooms.repo_is_private(repo)))
        if not lines:
            return "No projects are listed yet. Make your own: \"@pm make me a project room\"."
        return ("Projects you can join (open one, then Join or Ask to join):\n" + "\n".join(lines)
                + "\n\nThey're also under Explore rooms in Element.")

    async def set_visibility(self, room: MatrixRoom, sender: str, value: str) -> str:
        if not await room_state.is_moderator(self.client, room, sender):
            return "Only the room owner or a maintainer can change who can find and join this room."
        if (getattr(room, "canonical_alias", None) in project_rooms.FRONT_DOORS
                or pm_settings.is_my_chat(room, self.user_id, getattr(self, "dm_rooms", {}))):
            return "Only project rooms have this setting."
        repo = await room_state.repo_of(self.client, room.room_id)
        refused = project_rooms.refusal(value, value == project_rooms.OPEN and await project_rooms.repo_is_private(repo))
        failed = refused or await project_rooms.apply(self.client, room.room_id, value)
        return failed or f"This room is now {project_rooms.WORDS[value]}."

    async def keep_private_repo_closed(self, room_id: str, repo: str) -> str:
        """A room that becomes bound to a private repo can't stay open: it becomes listed."""
        if (await project_rooms.visibility(self.client, room_id) != project_rooms.OPEN
                or not await project_rooms.repo_is_private(repo)):
            return ""
        failed = await project_rooms.apply(self.client, room_id, project_rooms.LISTED)
        return f"\n{failed}" if failed else "\nIts repo is private, so the room is now listed instead of open (people ask to join)."

    async def primary_room_for(self, repo: str, exclude: str | None = None) -> str | None:
        """The room that runs `repo` here (bound to it, not a separate discussion room)."""
        for rid in [r for r, known in list(self.room_repos.items()) if known and known.lower() == repo.lower()]:
            binding = await room_state.read_binding(self.client, rid)
            found = room_state.repo_in(binding)
            if rid != exclude and found and found.lower() == repo.lower() and not room_state.runs_in(binding):
                return rid
        return None

    def room_name(self, room_id: str) -> str:
        room = self.client.rooms.get(room_id)
        return room.display_name if room is not None else "another room"

    def conflict_answer(self, room_id: str, sender: str, repo: str, other: str) -> str:
        """My answer when `repo` is already run from `other`; set_repo adds the two buttons under it."""
        self.__dict__.setdefault("_conflicts", {})[room_id] = {"repo": repo, "other": other, "by": sender, "event": None}
        name = self.room_name(other)
        return (f"**{repo}** is already the project of **{name}**: one room per repo, so two build loops never take "
                f"the same issues.\n{ASK} Ask to join {name} · {SEPARATE} Make a separate room anyway (it can discuss "
                f"{repo}, but its build loop runs in {name})")

    async def offer_conflict_buttons(self, room_id: str, event_id: str) -> None:
        offer = self.__dict__.get("_conflicts", {}).get(room_id)
        if offer is not None and offer["event"] is None:
            offer["event"] = event_id
            for key in (ASK, SEPARATE):
                await self.offer_thumb(room_id, event_id, key)

    async def on_conflict_reaction(self, room: MatrixRoom, event: ReactionEvent) -> bool:
        offer = self.__dict__.get("_conflicts", {}).get(room.room_id)
        if offer is None or event.reacts_to != offer["event"]:
            return False
        key = event.key.replace("️", "")
        if key not in (ASK, SEPARATE) or not await room_state.is_moderator(self.client, room, event.sender):
            return True                       # the choice is the room's owners' (as choosing the repo is)
        self._conflicts.pop(room.room_id, None)
        if key == ASK:
            await self.ask_to_join(offer["other"], event.sender)
            await self.say(room.room_id, f"I've asked the owners of **{self.room_name(offer['other'])}** to let you in.")
        else:
            await self.say(room.room_id, await self.choose_repo(room, event.sender, offer["repo"], separate_from=offer["other"]))
        return True

    async def on_project_reaction(self, room: MatrixRoom, event: ReactionEvent) -> bool:
        return await self.on_join_reaction(room, event) or await self.on_conflict_reaction(room, event)
