"""@pm notices when a person's own agent (bring your own, connect.py) goes offline, and tells them.

Every CHECK_S, @pm asks Synapse for each such agent's presence (a syncing bridge is "online"; @pm
shares Welcome with every agent, so it may see it). An agent that was online and has now been away
for OFFLINE_S gets ONE private message to its owner, at most once per ALERT_EVERY_S, with a 🔁
button: the install line again with a fresh code (running it again is safe). The console's
"My agent" shows Online / Offline from the same view. "@pm disconnect my agent" (or Disconnect in
the console) gives the agent a new password, so its computer can't sign in until a new code.
Mixed into loop.Loop (client, say, offer_thumb, private_room, choose: pm_welcome.py).
"""
import asyncio
import logging
import re
import time

from nio import MatrixRoom, PresenceGetResponse, ReactionEvent

import registry
import remote_agent
from bridge import ROOT, SERVER_NAME
from saved_dict import SavedDict
from setup_accounts import AccountError

log = logging.getLogger("loop")
CHECK_S, OFFLINE_S, ALERT_EVERY_S = 60, 5 * 60, 24 * 3600
AGAIN = "🔁"
OFFLINE = ("Your agent **{agent}** is offline: its computer is off or asleep, or its connector stopped. To bring "
           "it back, run the install line again on that computer (running it again is safe). Click 🔁 below "
           "and I'll send you the line with a fresh code.")
DISCONNECT_RE = re.compile(r"^\W*(please\W+)?disconnect my agent\W*$", re.I)
DISCONNECTED = ("Disconnected: {agent} can't sign in from its computer any more. To connect it again, type "
                "\"bring my own\" here for a new install line.")


class Presence:
    def seen(self) -> dict:
        """agent localpart → {"online", "since", "ever", "alerted", "offer", "owner"}, on disk."""
        if "_seen" not in self.__dict__:
            self._seen = SavedDict(ROOT / "data" / "agent-presence.json")
        return self._seen

    def agent_online(self, agent: str) -> bool | None:
        """For the console: True / False once checked, None if never seen."""
        entry = self.seen().get(agent)
        return entry.get("online") if isinstance(entry, dict) else None

    async def check_agents(self, now: float | None = None) -> None:
        """At most every CHECK_S (called from each tick)."""
        now = time.time() if now is None else now
        if now - self.__dict__.get("_checked", 0) < CHECK_S:
            return
        self._checked = now
        for person, info in registry.load().items():
            if info.get("agent") == registry.REMOTE:
                await self.check_agent(person, registry.agent_of(person), now)

    async def check_agent(self, person: str, agent: str, now: float) -> None:
        resp = await self.client.get_presence(f"@{agent}:{SERVER_NAME}")
        if not isinstance(resp, PresenceGetResponse):
            return                            # Synapse can't say now: no news is no alert
        old = self.seen().get(agent) or {}
        online = resp.presence == "online" or bool(resp.currently_active)
        entry = {**old, "owner": person, "online": online, "ever": old.get("ever") or online,
                 "since": old.get("since", now) if old.get("online") == online else now}
        self.seen()[agent] = entry
        alerted = entry.get("alerted")
        if (online or not entry["ever"] or now - entry["since"] < OFFLINE_S
                or (alerted is not None and (alerted >= entry["since"] or now - alerted < ALERT_EVERY_S))):
            return                            # online, never connected, just left, or told already
        dm = await self.private_room(person)
        if dm is None:
            return
        try:
            offer = await self.say(dm, OFFLINE.format(agent=agent))
            await self.offer_thumb(dm, offer, AGAIN)
        except RuntimeError as e:             # loop.SendError: tried again at the next check
            log.error("couldn't tell %s their agent is offline: %s", person, e)
            return
        self.seen()[agent] = {**entry, "alerted": now, "offer": offer, "dm": dm}

    async def on_offline_reaction(self, room: MatrixRoom, event: ReactionEvent) -> bool:
        """True if this was 🔁 on my "offline" message, by that agent's owner (handled here)."""
        mine = next((e for e in self.seen().values() if isinstance(e, dict) and e.get("offer") == event.reacts_to), None)
        if mine is None or mine.get("owner") != event.sender or mine.get("dm") != room.room_id:
            return False
        if event.key.replace("️", "") == AGAIN:
            await self.choose(room.room_id, event.sender, "🔌")    # pm_welcome.py: a fresh code, its own checks
        return True

    async def disconnect(self, room_id: str, person: str) -> None:
        """The person's own remote agent gets a new password: its computer is signed out for good."""
        agent = registry.agent_of(person)
        if registry.load().get(person, {}).get("agent") != registry.REMOTE or not registry.owns(person, agent):
            await self.say(room_id, "You have no agent connected from your own computer here.")
            return
        try:
            await asyncio.to_thread(remote_agent.disconnect, person)
        except (AccountError, OSError, SystemExit) as e:   # Synapse refused, .env unwritable, admin-bot can't sign in
            log.error("couldn't disconnect %s's agent: %s", person, e)
            await self.say(room_id, "⚠️ I couldn't disconnect your agent just now; please try again.")
            return
        await self.say(room_id, DISCONNECTED.format(agent=agent))
