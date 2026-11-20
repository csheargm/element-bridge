"""Agents come with their people. @pm joins a project room when a local person invites it; when a
person joins a room @pm is in, their own agent follows according to their setting (registry.py):
  always (default): @pm invites it · ask: @pm asks them for a ✅ · never: nothing.
When the person leaves, their agent is removed too: an agent never works in a room without its
owner. People change the setting in plain words ("@pm don't bring my agent", "ask me first",
"always bring my agent"). Mixed into loop.Loop (client, user_id, say, pending, is_person, ...).
"""
import logging
import time

from nio import InviteMemberEvent, MatrixRoom, RoomGetStateEventResponse, RoomInviteResponse, RoomMemberEvent

import capabilities
import ownership
import registry
import room_state
from bridge import SERVER_NAME, is_stale
from pm_chat import CONFIRM_S

log = logging.getLogger("loop")
FOLLOW_WORDS = {"always": "I'll bring your agent along whenever you join a project room",
                "ask": "I'll ask you before bringing your agent into a room",
                "never": "I won't bring your agent into rooms; invite it yourself when you want it"}


class Follow:
    async def on_invite(self, room: MatrixRoom, event: InviteMemberEvent) -> None:
        """People create project rooms in Element and invite @pm: join if a local person asked. From
        another server, only a private chat (pm_welcome.py: how they ask for an agent)."""
        local = event.sender.split(":")[0]
        if not event.sender.endswith(f":{SERVER_NAME}"):
            await self.on_remote_invite(room, event)
            return
        if event.state_key != self.user_id or event.membership != "invite" or local.endswith(("-agent", "-bot")):
            return
        await self.client.join(room.room_id)
        r = await self.client.room_get_state_event(room.room_id, "m.room.power_levels", "")
        users = r.content.get("users", {}) if isinstance(r, RoomGetStateEventResponse) else {}
        if users.get(self.user_id, 0) >= room_state.MODERATOR_LEVEL:
            return
        try:
            await self.say(room.room_id, "Hi! I'm the Product Manager. To run this project I need to be a "
                                         "moderator: Room settings → Roles & Permissions → make **Product Manager** "
                                         "a Moderator. Then talk to me, or say \"help\".")
        except RuntimeError as e:             # loop.SendError
            log.error("couldn't greet %s: %s", room.room_id, e)   # the room still works once I'm a moderator

    async def on_member(self, room: MatrixRoom, event: RoomMemberEvent) -> None:
        """A person joins → their agent follows (per setting); a person leaves → their agent goes too."""
        if is_stale(event, self.started_ms) or not self.sync_point.first_time(event.event_id):
            return
        person = event.state_key
        if person == self.user_id or person.split(":")[0].endswith(("-agent", "-bot")):
            return                            # people only (from any server: ownership is by full ID)
        try:
            if event.membership == "join" and event.prev_membership != "join":
                await self.bring_agent(room, person)
            elif event.membership in ("leave", "ban") and event.prev_membership == "join":
                await self.remove_agent(room, person)
        except (RuntimeError, ValueError, TypeError, AttributeError, OSError) as e:
            # SendError, a damaged registry, a network hiccup: membership still works; the log shows why
            log.error("follow-me failed for %s in %s: %s", person, room.room_id, e)

    async def bring_agent(self, room: MatrixRoom, person: str) -> None:
        if self.is_welcome_room(room) or room.room_id in getattr(self, "dm_rooms", {}).values():
            return                            # Welcome (everyone's first room) and my private chats: no agents
        agent = ownership.agent_id(person)
        local = person.split(":")[0].lstrip("@")
        if (not registry.owns(person, agent.split(":")[0][1:]) or agent in room.users
                or registry.follow(person) == "never"):
            return                            # not their registered agent, already here, or they said never
        if registry.follow(person) == "ask":
            event_id = await self.say(room.room_id, f"Welcome {local}! Bring your agent ({agent.split(':')[0][1:]}) "
                                                    "into this room? Click 👍 below to bring it.")
            self.pending[room.room_id] = {"kind": "bring", "agent": agent, "event_id": event_id,
                                          "until": time.time() + CONFIRM_S, "by": person}
            await self.offer_thumb(room.room_id, event_id)
            return
        await self.invite_agent(room.room_id, agent, f"{local} joined, so their agent came along "
                                                     "(say \"don't bring my agent\" to change that).")

    async def invite_agent(self, room_id: str, agent: str, note: str) -> None:
        resp = await self.client.room_invite(room_id, agent)
        if not isinstance(resp, RoomInviteResponse):
            log.warning("couldn't invite %s to %s: %s", agent, room_id, resp)
            return
        await self.say(room_id, note)

    async def remove_agent(self, room: MatrixRoom, person: str) -> None:
        agent = ownership.own_agent_id(person)     # only their own: never someone else's on a bad record
        if agent and ownership.joined(room, agent):
            await self.client.room_kick(room.room_id, agent, reason="its owner left the room")

    async def set_follow(self, room: MatrixRoom, sender: str, value: str) -> None:
        """Only ever the sender's OWN setting (by full ID: from any server, never a same-named other)."""
        local = sender.lstrip("@").partition(":")[0]
        if not registry.owns(sender, registry.agent_of(sender)):
            await self.say(room.room_id, f"{local}, you don't have an agent registered here yet.")
            return
        registry.set_follow(sender, value)
        await self.say(room.room_id, f"OK {local}: {FOLLOW_WORDS[value]}.")

    async def set_grant(self, room: MatrixRoom, sender: str, cap: str, on: bool) -> None:
        """The owner lets their OWN agent use a capability in THIS room (or stops it)."""
        local = sender.lstrip("@").partition(":")[0]
        agent_local = registry.agent_of(sender)
        if not registry.owns(sender, agent_local):
            await self.say(room.room_id, "Only people with an agent registered on this server can do that.")
            return
        agent = f"@{agent_local}:{SERVER_NAME}"
        state = await self.client.room_get_state(room.room_id)
        current = capabilities.caps_in(getattr(state, "events", []) or [], agent)   # only what @pm wrote
        resp = await self.client.room_put_state(room.room_id, capabilities.GRANTS_EVENT,
                                                capabilities.record(current, cap, sender, on),
                                                state_key=capabilities.key_for(agent))
        if not hasattr(resp, "event_id"):
            await self.say(room.room_id, f"⚠️ I couldn't save that: {resp}")
            return
        self.grants_cache.setdefault(room.room_id, {})[agent] = {c: {} for c in (current | {cap} if on else current - {cap})}
        what = capabilities.CAPS[cap]
        warn = (" Note: it reads the room and the project, so anything it looks up leaves this room —"
                " each web request is shown here." if on else "")
        await self.say(room.room_id, f"OK {local}: your agent may now {what} in this room.{warn} Say \"stop "
                                     "letting my agent use the web\" to undo." if on else
                                     f"OK {local}: your agent may no longer {what} in this room.")
