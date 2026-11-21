"""Asking to join a project room. Three ways in, one answer: the room's owners and maintainers decide.
  - Element's "Ask to join" on a listed room (a Matrix knock: project_rooms.py);
  - "@pm ask to join owner/name": the room working on that exact repo, even a private one (my reply is
    the same whether or not such a room exists, so it never confirms a private repo's room);
  - 🙋 under my "one room per repo" answer (pm_projects.py).
Each of the room's moderators gets a private message: "<name> (<full ID>) asks to join <room>.
👍 Let in · 👎 Decline". Only that moderator's click counts, and only while they're still a moderator;
👍 invites the person (their agent follows them in when they join, per their own setting), 👎 turns
a knock down. One request per person and room a day, a few per person an hour.
Mixed into pm_projects.Projects (client, user_id, say, offer_thumb, private_room).
"""
import logging
import time

from nio import MatrixRoom, ReactionEvent, RoomInviteResponse

import room_state
from bridge import ROOT
from saved_dict import SavedDict

log = logging.getLogger("loop")
LET_IN, DECLINE = "👍", "👎"
REQUEST_S = 7 * 86400          # a moderator's click counts for a week
AGAIN_S = 86400                # the same person and room: once a day
ASKS_PER_HOUR = 5              # per person, all rooms
ASKED = ("I've asked the owners of the room working on that repo, if there is one here. You'll get an invite "
         "on the left (under Invites) if they let you in.")


def local_name(user_id: str) -> str:
    return user_id.split(":")[0].lstrip("@")


class Joining:
    def join_requests(self) -> dict:
        """event id of my question to a moderator → {room, person, moderator, dm, at, knock}."""
        if "_join_requests" not in self.__dict__:
            self._join_requests = SavedDict(ROOT / "data" / "join-requests.json")
        return self._join_requests

    def may_ask(self, room_id: str, person: str) -> bool:
        now, reqs = time.time(), self.join_requests().values()
        if any(r["room"] == room_id and r["person"] == person and now - r["at"] < AGAIN_S for r in reqs):
            return False
        recent = self.__dict__.setdefault("_asks", {}).setdefault(person, [])
        recent[:] = [t for t in recent if now - t < 3600]
        if len(recent) >= ASKS_PER_HOUR:
            return False
        recent.append(now)
        return True

    async def ask_to_join(self, room_id: str, person: str, knock: bool = False) -> bool:
        """Ask the room's moderators to let `person` in; True if at least one was asked."""
        room = self.client.rooms.get(room_id)
        if room is None or (person in room.users and person not in room.invited_users) or not self.may_ask(room_id, person):
            return False
        asked = False
        for mod in await room_state.moderators(self.client, room):
            if mod == person:
                continue
            try:
                dm = await self.private_room(mod)
                if dm is None:
                    continue
                event = await self.say(dm, f"{local_name(person)} ({person}) asks to join **{room.display_name}**. "
                                           f"{LET_IN} Let in · {DECLINE} Decline")
            except RuntimeError as e:         # loop.SendError: the other moderators may still be asked
                log.error("couldn't ask %s about %s: %s", mod, person, e)
                continue
            self.join_requests()[event] = {"room": room_id, "person": person, "moderator": mod, "dm": dm,
                                           "at": time.time(), "knock": knock}
            for key in (LET_IN, DECLINE):
                await self.offer_thumb(dm, event, key)
            asked = True
        return asked

    async def on_knock(self, room: MatrixRoom, person: str) -> None:
        """Element's "Ask to join" on a listed room."""
        if not await self.ask_to_join(room.room_id, person, knock=True):
            log.info("knock on %s by %s not forwarded (asked already, too often, or no moderator)", room.room_id, person)

    async def on_join_reaction(self, room: MatrixRoom, event: ReactionEvent) -> bool:
        """True if this was a click on one of my join questions (used or not)."""
        req = self.join_requests().get(event.reacts_to)
        if req is None:
            return False
        key = event.key.replace("️", "")
        if (event.sender != req["moderator"] or room.room_id != req["dm"] or key not in (LET_IN, DECLINE)
                or time.time() - req["at"] > REQUEST_S):
            return True
        target = self.client.rooms.get(req["room"])
        if target is None or not await room_state.is_moderator(self.client, target, event.sender):
            await self.say(room.room_id, "You're no longer an owner or maintainer of that room, so I can't act on this.")
            return True
        for eid in [e for e, r in self.join_requests().items() if r["room"] == req["room"] and r["person"] == req["person"]]:
            self.join_requests().pop(eid, None)          # one answer settles it for every moderator asked
        who = local_name(req["person"])
        if key == LET_IN:
            resp = await self.client.room_invite(req["room"], req["person"])
            await self.say(room.room_id, f"Invited {who} to **{target.display_name}**." if isinstance(resp, RoomInviteResponse)
                           else f"⚠️ I couldn't invite {who}: {getattr(resp, 'message', resp)}")
        else:
            if req.get("knock"):
                await self.client.room_kick(req["room"], req["person"], "An owner declined the request to join")
            await self.say(room.room_id, f"Declined: {who} isn't let in.")
        return True
