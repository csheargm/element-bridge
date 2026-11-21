"""A person can have several agents (ADR 0005): @pm adds one more, in its private chat.

"➕" under @pm's Connect message (or under "You already have an agent"), typing "add another agent"
(optionally "... called codex"), or "Add an agent" in the console. Without a name @pm asks ONE
plain question ("What should I call it?") and takes the next short answer in that chat. Then the
same as the first agent: remote_agent.provision_more makes the account (counted against
HAGENCY_AGENTS_PER_PERSON before it exists), a fresh one-time code for THAT agent, and the Connect
page link. Codes go only to @pm's private chat with that person.
Mixed into loop.Loop (client, user_id, say, offer_thumb, private_room, only_us, busy, dm_rooms,
welcomes, addressed_text: pm_welcome.py, pm_chat.py, pm_settings.py).
"""
import asyncio
import logging
import re
import time

from nio import MatrixRoom, ReactionEvent, RoomMessageText

import connect_page
import identity
import registry
import remote_agent
from bridge import ROOT, SERVER_NAME
from pm_chat import without_quote
from redact import room_safe
from saved_dict import SavedDict
from setup_accounts import AccountError

log = logging.getLogger("loop")
ADD = "➕"
ADD_RE = re.compile(r"^\W*(please\W+)?(add|make|create|get me|i want) (another|a second|one more|a new|a further) agent"
                    r"(\s+(called|named)\s+(?P<label>[\w-]+))?(\s+please)?\W*$", re.I)
CANCEL_RE = re.compile(r"^\W*(cancel|stop|never ?mind|no)\W*$", re.I)
NAMING_S = 10 * 60                    # how long @pm waits for the name it asked for
NAME_ANSWER_RE = re.compile(r"^\W*((call|name) it|let'?s call it|it'?s)?\s*(?P<label>[A-Za-z][\w-]*)\W*$", re.I)   # "codex"
ADD_HINT = "\n\nWant another agent too (for example Codex as well as Claude Code)? Click ➕ below."
ASK_NAME = ("What should I call your new agent? One short word, letters and digits (up to 9), for example "
            "**codex** or **laptop**. (Say \"cancel\" to stop.)")
BRING_REPLY = ("Your agent is **{agent}**. On the computer where Codex or Claude Code is installed, open "
               "[your Connect page]({link}): it shows the one line to paste there (Mac, Windows or Linux).\n\n"
               "Your code: `{code}` (it works once, within 30 minutes). On a Mac or Linux this is the same line:\n\n"
               "`{line}`\n\nYour agent then runs in the background. It has invited you to a private chat: "
               "accept it and talk to your agent there.")
HAVE = "You already have an agent here: {agents}." + ADD_HINT


class MoreAgents:
    def add_offers(self) -> dict:
        """➕ message event id → {"person", "dm"}, on disk: only that person's click there counts."""
        if "_add_offers" not in self.__dict__:
            self._add_offers = SavedDict(ROOT / "data" / "add-agent-offers.json")
        return self._add_offers

    async def offer_add(self, dm: str, event_id: str, person: str) -> None:
        self.add_offers()[event_id] = {"person": person, "dm": dm}
        await self.offer_thumb(dm, event_id, ADD)

    async def say_have_agents(self, room_id: str, person: str) -> None:
        names = ", ".join(a["localpart"] for a in registry.agents_of(person))
        event_id = await self.say(room_id, HAVE.format(agents=names))
        await self.offer_add(room_id, event_id, person)

    async def on_add_reaction(self, room: MatrixRoom, event: ReactionEvent) -> bool:
        """True if this was a click on one of my ➕ buttons (by its person, in that chat)."""
        offer = self.add_offers().get(event.reacts_to)
        if not offer or offer.get("person") != event.sender or offer.get("dm") != room.room_id:
            return False
        if event.key.replace("️", "") == ADD:
            await self.start_add(room.room_id, event.sender)
        return True

    def in_private(self, room: MatrixRoom, person: str) -> bool:
        return room.room_id in ((self.welcomes().get(person) or {}).get("dm"), getattr(self, "dm_rooms", {}).get(person))

    async def on_add_text(self, room: MatrixRoom, event: RoomMessageText) -> bool:
        """True if the message asked for another agent, or answered my "what should I call it?"."""
        private = self.in_private(room, event.sender)
        text = without_quote(event.body) if private else self.addressed_text(event)
        if text is None:
            return False
        naming = self.__dict__.setdefault("_naming", {})
        asked = naming.get(event.sender)
        if private and asked and asked["dm"] == room.room_id and time.time() < asked["until"]:
            if CANCEL_RE.search(text):
                naming.pop(event.sender, None)
                await self.say(room.room_id, "OK, no new agent.")
                return True
            answer = NAME_ANSWER_RE.search(text)
            if answer:
                await self.start_add(room.room_id, event.sender, answer.group("label"))
                return True
        m = ADD_RE.search(text)
        if not m:
            return False
        dm = room.room_id if private else await self.private_room(event.sender)
        if dm is None:
            await self.say(room.room_id, "I couldn't open a private chat with you; please try again.")
        else:
            await self.start_add(dm, event.sender, m.group("label"))
        return True

    async def start_add(self, dm: str, person: str, typed: str | None = None) -> str:
        """Ask for a name, or set the agent up (in the background). Returns what was said, for the console."""
        naming = self.__dict__.setdefault("_naming", {})
        have = registry.agents_of(person)
        if not have or not registry.owns(person, have[0]["localpart"]):
            return await self._tell(dm, "You don't have an agent here yet: type \"bring my own\" to get your first one.")
        if len(have) >= registry.per_person():
            naming.pop(person, None)
            return await self._tell(dm, f"You have {len(have)} agents, the most one person can have here. "
                                        "Disconnect one in the console first if you want a different one.")
        if not typed:
            naming[person] = {"dm": dm, "until": time.time() + NAMING_S}
            return await self._tell(dm, ASK_NAME)
        label = registry.clean_label(typed)
        if label is None or any(a["label"] == label for a in have):
            naming[person] = {"dm": dm, "until": time.time() + NAMING_S}
            why = f"You already have an agent called {label}." if label else "That doesn't work as a name."
            return await self._tell(dm, f"{why} {ASK_NAME}")
        naming.pop(person, None)
        busy = self.__dict__.setdefault("_provisioning", set())   # shared with pm_welcome.choose: one at a time
        if person in busy:
            return "I'm already setting up an agent for you; one moment."
        busy.add(person)
        job = asyncio.create_task(self.add_agent(dm, person, label))
        job.add_done_callback(lambda _: busy.discard(person))
        self.busy.add(job)
        job.add_done_callback(self.busy.discard)
        return (f"Setting up {identity.agent_localpart(person, SERVER_NAME, label)}: your Connect page is coming "
                "in our private chat.")

    async def _tell(self, dm: str, text: str) -> str:
        await self.say(dm, text)
        return text

    async def add_agent(self, dm: str, person: str, label: str) -> None:
        try:
            agent = await asyncio.to_thread(remote_agent.provision_more, person, label)
            await self.send_code(person, agent)
        except (AccountError, OSError, RuntimeError) as e:   # refused, cap reached, .env unwritable, send failed
            log.error("adding an agent failed for %s: %s", person, e)
            try:
                await self.say(dm, f"⚠️ I couldn't add your agent: {room_safe(e)}.")
            except RuntimeError as e2:        # Matrix refuses too: the log line above is all that's left
                log.error("couldn't tell %s: %s", person, e2)

    async def send_code(self, person: str, agent: str | None) -> None:
        """A fresh one-time code and the Connect page for this agent of `person` (None: their first),
        in my private chat with just them. Raises RuntimeError when there is no such chat."""
        chat = await self.private_room(person)
        if chat is None or not await self.only_us(chat, person):
            raise RuntimeError("I couldn't open a private chat with just you")
        code = remote_agent.CODES.issue(person, agent=agent)
        event_id = await self.say(chat, BRING_REPLY.format(
            agent=agent or registry.agent_of(person), link=connect_page.page_link(code), code=code,
            line=remote_agent.install_line(code)) + ADD_HINT)
        await self.offer_add(chat, event_id, person)
