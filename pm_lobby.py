"""@pm's front doors besides Welcome, so nobody has to guess the next step:
  - the Lobby (#lobby:<server>): a public room in the server's room directory that anyone joins,
    people from other Matrix servers too (the site links to it: "Already on Matrix?");
  - in any room I'm in (the Lobby, a project room), someone without an agent who joins gets ONE
    line from me, once ever, with a 👍 "Get your agent" button: their click opens my private offer;
  - the Welcome room's topic points at my private chat ("Start here 👉 …").
Mixed into pm_welcome.Welcome (client, user_id, say, offer_thumb, offer_again, is_newcomer, dm_rooms).
"""
import asyncio
import logging

from nio import MatrixRoom, ReactionEvent, RoomPreset, RoomResolveAliasResponse, RoomVisibility

import identity
import server_admins
from bridge import ROOT, SERVER_NAME
from project_rooms import LOBBY_ALIAS, LOBBY_LOCALPART
from saved_dict import SavedDict

log = logging.getLogger("loop")
LOBBY_TOPIC = ("Hagency Rooms' lobby: people and their AI agents build software together. New here? Click 👍 "
               "under my greeting and I'll message you how to get your agent.")
WELCOME_TOPIC = "Start here 👉 open 'Product Manager' on the left"
GET = "👍"
GREET = "Hi {person}! Click 👍 below to get your agent: I'll send you a private message with the next step."


class Lobby:
    def lobby_greeted(self) -> dict:
        """person → {"room", "event", "done"}: my one greeting line for them, ever (on disk)."""
        if "_lobby_greeted" not in self.__dict__:
            self._lobby_greeted = SavedDict(ROOT / "data" / "lobby-greeted.json")
        return self._lobby_greeted

    async def ensure_lobby(self) -> None:
        """The Lobby exists: public, in the room directory, joinable from any server."""
        resp = await self.client.room_resolve_alias(LOBBY_ALIAS)
        if isinstance(resp, RoomResolveAliasResponse):
            return
        made = await self.client.room_create(alias=LOBBY_LOCALPART, name="Lobby", topic=LOBBY_TOPIC,
                                             preset=RoomPreset.public_chat, visibility=RoomVisibility.public)
        if not hasattr(made, "room_id"):
            log.warning("couldn't create %s: %s", LOBBY_ALIAS, made)

    async def ensure_welcome_topic(self, room_id: str) -> None:
        room = self.client.rooms.get(room_id)
        if room is not None and getattr(room, "topic", None) != WELCOME_TOPIC:
            resp = await self.client.room_put_state(room_id, "m.room.topic", {"topic": WELCOME_TOPIC})
            if not hasattr(resp, "event_id"):        # the room still works; its topic is only a pointer
                log.warning("couldn't set Welcome's topic: %s", resp)

    async def on_room_join(self, room: MatrixRoom, person: str) -> None:
        """Someone without an agent joined a room I'm in: one line, once ever, with a 👍."""
        greeting = self.__dict__.setdefault("_lobby_greeting", set())
        if (person in self.lobby_greeted() or person in greeting or not self.is_newcomer(person)
                or self.is_private_chat(room)):
            return
        greeting.add(person)
        try:
            if identity.split(person)[1] == SERVER_NAME and await asyncio.to_thread(server_admins.is_admin, person):
                return                        # the server's admins run it: no greeting
            event = await self.say(room.room_id, GREET.format(person=person), mention=person)
            self.lobby_greeted()[person] = {"room": room.room_id, "event": event}
            await self.offer_thumb(room.room_id, event, GET)
        except RuntimeError as e:             # loop.SendError: they're greeted at their next join instead
            log.error("couldn't greet %s in %s: %s", person, room.room_id, e)
        finally:
            greeting.discard(person)

    async def on_lobby_reaction(self, room: MatrixRoom, event: ReactionEvent) -> bool:
        """True if this was a click on my greeting (only the person greeted counts, once)."""
        entry = self.lobby_greeted().get(event.sender)
        if not entry or entry.get("event") != event.reacts_to or entry.get("room") != room.room_id:
            return False
        clicking = self.__dict__.setdefault("_lobby_clicking", set())
        if event.key.replace("️", "") != GET or entry.get("done") or event.sender in clicking:
            return True
        clicking.add(event.sender)
        try:
            if await self.offer_again(room, event.sender):   # pm_welcome.py: my private offer, and where it is
                self.lobby_greeted()[event.sender] = {**entry, "done": True}   # only once it went out (GPT review)
        except RuntimeError as e:             # loop.SendError: the button still works for another click
            log.error("couldn't send %s my offer: %s", event.sender, e)
        finally:
            clicking.discard(event.sender)
        return True
