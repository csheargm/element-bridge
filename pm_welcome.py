"""@pm welcomes newcomers. Synapse puts every new account in the private Welcome room (#welcome,
created and owned by @pm: deploy/configure_synapse.py, local_signup.py). When a person without an
agent joins it, @pm opens a private chat with them, once, and offers two buttons:
  🏠 House agent: run by this server (not built yet: it comes with the public server);
  🔌 Bring your own: their Codex or Claude Code on their own computer (remote_agent.py, connect.py).
Only that person's click on that message counts, each button once. Typing "house agent" or "bring
my own" in that chat works too, and "@pm I want an agent" anywhere offers it again.
Mixed into loop.Loop (client, user_id, say, offer_thumb, private_room, addressed_text, busy, started_ms).
"""
import asyncio
import logging
import re

from nio import MatrixRoom, ReactionEvent, RoomMemberEvent, RoomMessageText, RoomPreset, RoomResolveAliasResponse

import registry
import remote_agent
from bridge import ROOT, SERVER_NAME, is_stale
from local_signup import WELCOME_ALIAS, WELCOME_LOCALPART
from pm_chat import without_quote
from redact import room_safe
from saved_dict import SavedDict
from setup_accounts import AccountError

log = logging.getLogger("loop")
HOUSE, BRING = "🏠", "🔌"
HOUSE_RE = re.compile(r"\bhouse agent\b", re.I)
BRING_RE = re.compile(r"\bbring (my|your) own\b", re.I)
WANT_RE = re.compile(r"\b(i (want|need|would like)|i'?d like|get me|give me|can i (have|get)|how (do|can) i get)\b"
                     r".*\b(an?|my) (own )?agent\b", re.I)
WELCOME = ("Welcome to Hagency Rooms, {name}: chat rooms where people and their AI agents build software "
           "together.\n\nGet your agent:\n\n"
           "- 🏠 **House agent**: runs on this server, with free daily credit\n"
           "- 🔌 **Bring your own**: your Codex or Claude Code, on your own computer\n\n"
           "Click one below, or type \"house agent\" or \"bring my own\".")
HOUSE_REPLY = ("The house agent comes with the public server; it isn't here yet. For now you can bring your "
               "own: type \"bring my own\" (you need Codex or Claude Code on your computer).")
BRING_REPLY = ("Your agent is **{agent}**. On your computer, with Codex or Claude Code installed, run:\n\n"
               "`{line}`\n\nThe code works once, within 30 minutes. Your agent has invited you to a private "
               "chat: accept it and talk to your agent there while that command runs.")


class Welcome:
    def welcomes(self) -> dict:
        """person → {"dm", "offer" (event id), "done" (buttons used)}, on disk: welcomed once."""
        if "_welcomes" not in self.__dict__:
            self._welcomes = SavedDict(ROOT / "data" / "welcome.json")
        return self._welcomes

    def is_welcome_room(self, room: MatrixRoom) -> bool:
        return getattr(room, "canonical_alias", None) == WELCOME_ALIAS

    def is_newcomer(self, user_id: str) -> bool:
        """A person on this server with no agent of their own here yet (never me, an agent or a bot)."""
        local, _, server = user_id.lstrip("@").partition(":")
        return (server == SERVER_NAME and user_id != self.user_id and not local.endswith(("-agent", "-bot"))
                and registry.owner_of(registry.agent_of(local)) is None)

    async def ensure_welcome_room(self) -> None:
        """The Welcome room exists and is mine (Synapse only creates it for a server's very first user)."""
        resp = await self.client.room_resolve_alias(WELCOME_ALIAS)
        if isinstance(resp, RoomResolveAliasResponse):
            return
        made = await self.client.room_create(alias=WELCOME_LOCALPART, name="Welcome", preset=RoomPreset.private_chat,
                                             topic="Everyone starts here. @pm sends you a private message.")
        if not hasattr(made, "room_id"):
            log.warning("couldn't create %s: %s (newcomers won't be greeted)", WELCOME_ALIAS, made)

    async def on_welcome_member(self, room: MatrixRoom, event: RoomMemberEvent) -> None:
        if (is_stale(event, self.started_ms) or not self.is_welcome_room(room) or event.membership != "join"
                or event.prev_membership == "join"):
            return
        person = event.state_key
        if self.is_newcomer(person) and person not in self.welcomes():
            await self.greet(person)

    async def greet(self, person: str) -> str | None:
        """My private chat with them, with the two buttons; returns that chat's room id."""
        self.welcomes()[person] = {"dm": None, "offer": None, "done": []}   # first: a second join never greets twice
        try:
            dm = await self.private_room(person)
            if dm is None:
                return None
            offer = await self.say(dm, WELCOME.format(name=person.split(":")[0].lstrip("@")))
        except RuntimeError as e:             # loop.SendError: say "I want an agent" to try again
            log.error("couldn't welcome %s: %s", person, e)
            return None
        self.welcomes()[person] = {"dm": dm, "offer": offer, "done": []}
        for key in (HOUSE, BRING):
            await self.offer_thumb(dm, offer, key)
        return dm

    async def on_welcome_reaction(self, room: MatrixRoom, event: ReactionEvent) -> bool:
        """True if this was a click on my welcome (handled here, used or not)."""
        entry = self.welcomes().get(event.sender)
        if not entry or room.room_id != entry.get("dm") or event.reacts_to != entry.get("offer"):
            return False
        key = event.key.replace("️", "")
        if key in (HOUSE, BRING) and key not in entry.get("done", []):
            self.welcomes()[event.sender] = {**entry, "done": [*entry.get("done", []), key]}   # each button once
            await self.choose(room.room_id, event.sender, key)
        return True

    async def on_welcome_text(self, room: MatrixRoom, event: RoomMessageText) -> bool:
        """True if the message was about getting an agent (answered here)."""
        entry = self.welcomes().get(event.sender) or {}
        in_dm = room.room_id == entry.get("dm")
        text = without_quote(event.body) if in_dm else self.addressed_text(event)
        if text is None:
            return False
        if in_dm and HOUSE_RE.search(text):
            await self.choose(room.room_id, event.sender, HOUSE)
        elif in_dm and BRING_RE.search(text):
            await self.choose(room.room_id, event.sender, BRING)
        elif WANT_RE.search(text):
            await self.offer_again(room, event.sender)
        else:
            return False
        return True

    async def offer_again(self, room: MatrixRoom, person: str) -> None:
        local = person.split(":")[0].lstrip("@")
        info = registry.load().get(local, {})
        if not self.is_newcomer(person) and info.get("agent") != registry.REMOTE:
            await self.say(room.room_id, f"You already have an agent here: {registry.agent_of(local)}.")
            return
        dm = await self.greet(person)
        if dm and dm != room.room_id:
            await self.say(room.room_id, f"Sent you a private message, {local}.")

    async def choose(self, dm: str, person: str, key: str) -> None:
        if key == HOUSE:
            await self.say(dm, HOUSE_REPLY)
            return
        job = asyncio.create_task(self.bring_own(dm, person))   # creating the account takes a moment
        self.busy.add(job)
        job.add_done_callback(self.busy.discard)

    async def bring_own(self, dm: str, person: str) -> None:
        local = person.split(":")[0].lstrip("@")
        try:
            agent = await asyncio.to_thread(remote_agent.provision, local)
            code = remote_agent.CODES.issue(local)
            await self.say(dm, BRING_REPLY.format(agent=agent, line=remote_agent.install_line(code)))
        except (AccountError, OSError, RuntimeError) as e:   # the homeserver refused, .env unwritable, a send failed
            log.error("bring-your-own failed for %s: %s", person, e)
            try:
                await self.say(dm, f"⚠️ I couldn't set up your agent: {room_safe(e)}. Type \"bring my own\" to try again.")
            except RuntimeError as e2:        # Matrix refuses too: the log line above is all that's left
                log.error("couldn't tell %s: %s", person, e2)
