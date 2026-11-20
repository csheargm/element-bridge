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
import intents
from pm_chat import without_quote
from redact import room_safe
from saved_dict import SavedDict
from setup_accounts import AccountError

log = logging.getLogger("loop")
HOUSE, BRING = "🏠", "🔌"
HOUSE_RE = re.compile(r"\bhouse agent\b", re.I)
BRING_RE = re.compile(r"\bbring (my|your) own\b", re.I)
# The whole message, so "@pm I need an agent to review #7" stays a project request (GPT review 21).
WANT_RE = re.compile(r"^\W*(please\W+)?(i (want|need|would like)|i'?d like|get me|give me|can i (have|get)|how (do|can) i get)"
                     r"\s+(an?|my)\s+(own\s+)?agent(\s+please)?\W*$", re.I)
WELCOME = ("Welcome to Hagency Rooms, {name}: chat rooms where people and their AI agents build software "
           "together.\n\nGet your agent:\n\n"
           "- 🏠 **House agent**: runs on this server, with free daily credit\n"
           "- 🔌 **Bring your own**: your Codex or Claude Code, on your own computer\n\n"
           "Click one below, or type \"house agent\" or \"bring my own\".")
POINTER = ("Welcome, {person}! I've sent you a private message with how to get your agent: accept it under "
           "Invites (top left).")
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

    def is_local_person(self, user_id: str) -> bool:
        """A person on THIS server (never me, an agent, a bot, or @same-name:another-server)."""
        local, _, server = user_id.lstrip("@").partition(":")
        return server == SERVER_NAME and user_id != self.user_id and not local.endswith(("-agent", "-bot"))

    def is_newcomer(self, user_id: str) -> bool:
        """A local person with no agent of their own here yet."""
        local = user_id.lstrip("@").partition(":")[0]
        return self.is_local_person(user_id) and registry.owner_of(registry.agent_of(local)) is None

    async def ensure_welcome_room(self) -> None:
        """The Welcome room exists and is mine (Synapse only creates it for a server's very first user),
        and anyone who joined while I was down is welcomed now."""
        resp = await self.client.room_resolve_alias(WELCOME_ALIAS)
        if not isinstance(resp, RoomResolveAliasResponse):
            made = await self.client.room_create(alias=WELCOME_LOCALPART, name="Welcome", preset=RoomPreset.private_chat,
                                                 topic="Everyone starts here. @pm sends you a private message.")
            if not hasattr(made, "room_id"):
                log.warning("couldn't create %s: %s (newcomers won't be greeted)", WELCOME_ALIAS, made)
            return
        room = self.client.rooms.get(resp.room_id)
        joined = [u for u in getattr(room, "users", {}) if u not in getattr(room, "invited_users", {})]
        for person in joined:
            if self.is_newcomer(person) and person not in self.welcomes():
                await self.greet_from_welcome(resp.room_id, person)

    async def on_welcome_member(self, room: MatrixRoom, event: RoomMemberEvent) -> None:
        if (is_stale(event, self.started_ms) or not self.is_welcome_room(room) or event.membership != "join"
                or event.prev_membership == "join"):
            return
        person = event.state_key
        if self.is_newcomer(person) and person not in self.welcomes():
            await self.greet_from_welcome(room.room_id, person)

    async def greet_from_welcome(self, welcome_id: str, person: str) -> None:
        """The private offer, plus ONE line in Welcome naming them: Element lists my private chat as
        an invite to accept, which newcomers miss, so the buttons would go unseen."""
        if await self.greet(person) is None or self.welcomes()[person].get("pointed"):
            return
        self.welcomes()[person] = {**self.welcomes()[person], "pointed": True}   # once, even if the send fails
        try:
            await self.say(welcome_id, POINTER.format(person=person), mention=person)
        except RuntimeError as e:             # loop.SendError: the private offer is still there
            log.error("couldn't point %s to my private message: %s", person, e)

    async def greet(self, person: str) -> str | None:
        """My private chat with them, with the two buttons; returns that chat's room id. Remembered only
        once it worked (a failure is retried at my next start, or with "I want an agent")."""
        greeting = self.__dict__.setdefault("_greeting", set())
        if person in greeting:                # a second join while the first greeting is underway
            return None
        greeting.add(person)
        try:
            dm = await self.private_room(person)
            if dm is None:
                return None
            offer = await self.say(dm, WELCOME.format(name=person.split(":")[0].lstrip("@")))
        except RuntimeError as e:             # loop.SendError
            log.error("couldn't welcome %s: %s", person, e)
            return None
        finally:
            greeting.discard(person)
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
        if in_dm and intents.NEGATED.search(text) and (HOUSE_RE.search(text) or BRING_RE.search(text)):
            return False                      # "I don't want to bring my own": not a choice
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
        if not self.is_local_person(person):  # @newbie1:elsewhere is not our newbie1 (federation)
            await self.say(room.room_id, "Agents are only for people with an account on this server.")
            return
        if not self.is_newcomer(person) and info.get("agent") != registry.REMOTE:
            await self.say(room.room_id, f"You already have an agent here: {registry.agent_of(local)}.")
            return
        dm = await self.greet(person)
        if dm and dm != room.room_id:
            await self.say(room.room_id, f"Sent you a private message, {local}.")

    async def only_us(self, room_id: str, person: str) -> bool:
        """Asked of the server now, not my cached view: nobody but the two of us joined or invited."""
        resp = await self.client.room_get_state(room_id)
        members = {e.get("state_key") for e in getattr(resp, "events", None) or []
                   if e.get("type") == "m.room.member" and e.get("content", {}).get("membership") in ("join", "invite")}
        return bool(getattr(resp, "events", None)) and members <= {self.user_id, person}

    async def choose(self, dm: str, person: str, key: str) -> None:
        if key == HOUSE:
            await self.say(dm, HOUSE_REPLY)
            return
        busy = self.__dict__.setdefault("_provisioning", set())
        if person in busy or not self.is_local_person(person):
            return                            # one at a time per person; only this server's people
        busy.add(person)
        job = asyncio.create_task(self.bring_own(dm, person))   # creating the account takes a moment
        job.add_done_callback(lambda _: busy.discard(person))
        self.busy.add(job)
        job.add_done_callback(self.busy.discard)

    async def bring_own(self, dm: str, person: str) -> None:
        local = person.split(":")[0].lstrip("@")
        try:
            agent = await asyncio.to_thread(remote_agent.provision, local)
            chat = await self.private_room(person)        # re-checked: still just the two of us, or a new chat
            if chat is None or not await self.only_us(chat, person):
                raise RuntimeError("I couldn't open a private chat with just you")
            code = remote_agent.CODES.issue(local)
            await self.say(chat, BRING_REPLY.format(agent=agent, line=remote_agent.install_line(code)))
        except (AccountError, OSError, RuntimeError) as e:   # homeserver refused, .env unwritable, send failed, bad URL
            log.error("bring-your-own failed for %s: %s", person, e)
            try:
                await self.say(dm, f"⚠️ I couldn't set up your agent: {room_safe(e)}. Type \"bring my own\" to try again.")
            except RuntimeError as e2:        # Matrix refuses too: the log line above is all that's left
                log.error("couldn't tell %s: %s", person, e2)
