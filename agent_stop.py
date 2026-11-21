"""Stop and Start: an agent's owner pauses it from the Hagency panel (docs/adr/0009-agent-stop-start.md).

The server can't start or stop a program on someone's computer, so Stop doesn't end the agent's
process: it stays signed in and online, and it ignores messages and @pm's loop tasks until Start.
The switch is one state event that only @pm can send (Welcome's power levels), in the Welcome room
every account shares with @pm: type STOP_TYPE, state_key the agent's Matrix ID, {"stopped": bool}.
The agent reads it (with its sender) before it answers or works; pm_stop.py writes it.

Hermes agents answer through Hermes's own gateways, not bridge.py, so Stop doesn't reach them yet.
"""
import asyncio
import logging
import urllib.parse

from aiohttp import ClientError
from nio import MatrixRoom, RoomMessageText

import github
import matrix_calls
from bridge_config import SERVER_NAME
from local_signup import WELCOME_ALIAS

STOP_TYPE = "xyz.hagencylabs.agent.stopped"
REPLY = ("I'm stopped right now. {owner} can start me again in the Hagency panel "
         "(My agents → Start), or by telling Product Manager \"start my agent\".")
TASK_LINE = "{kind} skipped: my owner stopped me (Start in the Hagency panel)"
PM_ID = f"@{github.PM_BOT}:{SERVER_NAME}"

log = logging.getLogger("bridge")


def stopped_in(event: dict) -> bool:
    """A state event (format=event) says stopped only if @pm sent it and its content says so."""
    content = event.get("content")
    return event.get("sender") == PM_ID and isinstance(content, dict) and content.get("stopped") is True


async def read_stopped(client, agent_id: str) -> bool:
    """Has this agent's owner stopped it? Asked afresh each time (two small reads), so Start works at once."""
    if not getattr(client, "access_token", None):
        return False                          # not signed in yet: nothing to ask with (and nobody to answer)
    try:
        welcome = await matrix_calls.room_of_alias(client, WELCOME_ALIAS)
        if welcome is None:
            return False
        path = (f"/_matrix/client/v3/rooms/{urllib.parse.quote(welcome)}/state/{STOP_TYPE}/"
                f"{urllib.parse.quote(agent_id)}?format=event")
        status, event = await matrix_calls.get_json(client, path)
    except (RuntimeError, OSError, asyncio.TimeoutError, ClientError) as e:
        # Fails open, on purpose: Stop is the owner's own pause, not a security control (only they direct
        # this agent anyway), and a network hiccup must not silence an agent nobody stopped. Logged.
        log.warning("couldn't check whether my owner stopped me: %s", e)
        return False
    return status == 200 and stopped_in(event)   # 404: never stopped; 403: not in Welcome (an old account)


class BridgeStop:
    """Mixed into bridge.Bridge: the stop check before a reply or a loop task."""

    async def stopped_now(self, room: MatrixRoom, event: RoomMessageText) -> bool:
        """True while stopped; then it says so once per room, until it is started again."""
        told = self.__dict__.setdefault("_told_stopped", set())
        if not await read_stopped(self.client, self.user_id):
            told.clear()                      # started again: a later Stop says so once more
            return False
        if room.room_id not in told:
            told.add(room.room_id)
            await self.send(room.room_id, REPLY.format(owner=self.owner_name), reply_to=event)
        return True

    async def task_unless_stopped(self, room_id: str, task: dict) -> None:
        """run_task, or (stopped) the loop's answer that this step was skipped, so it never waits on me."""
        if not await read_stopped(self.client, self.user_id):
            await self.run_task(room_id, task)
            return
        result = {"kind": task.get("kind"), "issue": task.get("issue"), "task_id": task.get("task_id"),
                  "error": "agent stopped", "line": TASK_LINE.format(kind=task.get("kind"))}
        await self.send(room_id, result["line"], extra={github.RESULT_KEY: result})
