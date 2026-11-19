"""Bring-your-own-agent bridge: logs in as <owner>-agent and answers @mentions/DMs
by running the owner's local agent CLI (Codex or Claude Code) per room.

Usage: python bridge.py anna [--agent codex|claude]
"""
import argparse
import asyncio
import hashlib
import logging
import re
import time
from collections import defaultdict, deque
from pathlib import Path

import markdown
from nio import (AsyncClient, InviteMemberEvent, LoginResponse, MatrixRoom, MegolmEvent,
                 RoomGetStateEventResponse, RoomMessageText)

import agents
import github
import tasks

ROOT = Path(__file__).parent
HOMESERVER = "http://127.0.0.1:8648"
SERVER_NAME = "bridge.localhost"
CONTEXT_LINES = 20          # recent room lines handed to the agent as context
TYPING_REFRESH_S = 20       # re-send typing before the 30s server timeout lapses
TYPING_TIMEOUT_MS = 30_000
MAX_REPLY_CHARS = 30_000
MAX_CONCURRENT_TURNS = 3     # CLI processes per bridge across all rooms
CLOCK_SKEW_MS = 5_000        # tolerance when dropping events from before startup

log = logging.getLogger("bridge")


def read_env() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


class Bridge:
    def __init__(self, owner: str, backend: str, allow_members: bool = False):
        self.owner, self.backend, self.allow_members = owner, backend, allow_members
        self.owner_id = f"@{owner}:{SERVER_NAME}"
        self.user_id = f"@{owner}-agent:{SERVER_NAME}"
        self.client = AsyncClient(HOMESERVER, self.user_id, device_id=f"BRIDGE_{owner.upper()}")
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=CONTEXT_LINES))
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.warned_e2ee: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.turns = asyncio.Semaphore(MAX_CONCURRENT_TURNS)
        self.started_ms = int(time.time() * 1000)
        self.seen_tasks: set[str] = set()     # event ids: a redelivered task never runs twice

    # --- trigger rules -------------------------------------------------------------
    def is_addressed(self, room: MatrixRoom, event: RoomMessageText) -> bool:
        if room.joined_count == 2 and room.invited_count == 0:   # DM with the agent
            return True
        mentions = event.source.get("content", {}).get("m.mentions")
        user_ids = mentions.get("user_ids") if isinstance(mentions, dict) else None
        if isinstance(user_ids, list) and self.user_id in user_ids:
            return True
        localpart = re.escape(f"{self.owner}-agent")
        return re.search(rf"@?{localpart}\b", event.body, re.I) is not None

    def should_ignore(self, room: MatrixRoom, event: RoomMessageText) -> str | None:
        if event.sender == self.user_id:
            return "own message"
        if event.sender not in room.users or event.sender in room.invited_users:
            return "sender is not a joined room member"
        if event.sender.split(":")[0].endswith("-agent"):
            return "message from another agent (loop guard)"
        # The CLI runs with the owner's file access, so by default only the owner drives it.
        if event.sender != self.owner_id and not self.allow_members:
            return "sender is not the agent's owner (start with --allow-members to open it up)"
        return None

    # --- event handlers ------------------------------------------------------------
    async def on_invite(self, room: MatrixRoom, event: InviteMemberEvent) -> None:
        if event.state_key != self.user_id or event.membership != "invite":
            return
        if not event.sender.endswith(f":{SERVER_NAME}"):
            log.warning("ignoring invite to %s from remote user %s", room.room_id, event.sender)
            return
        log.info("joining %s (invited by %s)", room.room_id, event.sender)
        await self.client.join(room.room_id)

    async def on_encrypted(self, room: MatrixRoom, event: MegolmEvent) -> None:
        if room.room_id in self.warned_e2ee or event.sender == self.user_id:
            return
        self.warned_e2ee.add(room.room_id)
        await self.send(room.room_id, "I can't read encrypted rooms yet (bridge v1 has no E2EE). "
                        "Please use an unencrypted room.")

    async def on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        if is_stale(event, self.started_ms):
            return   # seen replays of pre-start events after restarts; never re-run old tasks/turns
        task = event.source.get("content", {}).get(github.TASK_KEY)
        if isinstance(task, dict):
            self.on_task(room, event, task)
            return
        name = room.user_name(event.sender) or event.sender
        line = f"{name} ({event.sender}): {event.body}"
        if not self.is_addressed(room, event):
            self.history[room.room_id].append(line)
            return
        reason = self.should_ignore(room, event)
        if reason:
            log.info("ignored message in %s: %s", room.room_id, reason)
            self.history[room.room_id].append(line)
            return
        task = asyncio.create_task(self.answer_locked(room, event, line))  # don't block sync
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def on_task(self, room: MatrixRoom, event: RoomMessageText, task: dict) -> None:
        """Loop tasks: only from loop-bot, only addressed to this agent, only from a joined member."""
        if (event.sender != f"@{github.LOOP_BOT}:{SERVER_NAME}" or task.get("agent") != self.user_id
                or event.sender not in room.users or event.sender in room.invited_users):
            log.info("ignored loop task in %s from %s", room.room_id, event.sender)
            return
        if event.event_id in self.seen_tasks:
            return
        self.seen_tasks.add(event.event_id)
        job = asyncio.create_task(self.run_task(room.room_id, task))
        self.tasks.add(job)
        job.add_done_callback(self.tasks.discard)

    async def run_task(self, room_id: str, task: dict) -> None:
        what = f"PR #{task.get('pr')}" if task.get("pr") else f"#{task.get('issue')}"
        await self.send(room_id, f"{task.get('kind')}: claimed {what}")
        typing = asyncio.create_task(self.keep_typing(room_id))
        try:
            async with self.turns:
                base = ROOT / "rooms" / self.owner / "work"
                if task.get("kind") == "merge" and await self.room_level(room_id) != "autonomous":
                    result = {"kind": "merge", "issue": task.get("issue"), "task_id": task.get("task_id"),
                              "error": "room is no longer autonomous", "line": "merge skipped: room is no longer autonomous"}
                else:
                    result = await tasks.handle(f"{self.owner}-agent", self.backend, base, task)
        finally:
            typing.cancel()
            await self.client.room_typing(room_id, False)
        await self.send(room_id, result["line"], extra={github.RESULT_KEY: result})

    async def room_level(self, room_id: str) -> str:
        r = await self.client.room_get_state_event(room_id, github.LEVEL_EVENT, "")
        return r.content.get("level", "off") if isinstance(r, RoomGetStateEventResponse) else "off"

    async def answer_locked(self, room: MatrixRoom, event: RoomMessageText, line: str) -> None:
        async with self.locks[room.room_id], self.turns:   # one turn per room; global cap
            await self.answer(room, event, line)

    async def answer(self, room: MatrixRoom, event: RoomMessageText, line: str) -> None:
        context = "\n".join(self.history[room.room_id])
        self.history[room.room_id].clear()
        prompt = (f"You are {self.owner}'s personal AI agent ({self.user_id}) in the Matrix chat room "
                  f"\"{room.display_name}\". Reply in chat style, concisely, as plain markdown.\n"
                  + (f"\nRecent room messages since your last reply:\n{context}\n" if context else "")
                  + f"\nMessage addressed to you:\n{line}\n")
        typing = asyncio.create_task(self.keep_typing(room.room_id))
        try:
            reply = await agents.run_turn(self.backend, self.workdir(room.room_id), prompt)
        except agents.AgentError as e:
            log.error("agent failed in %s: %s", room.room_id, e)
            reply = f"⚠️ {self.owner}'s agent ({self.backend}) failed: {e}"
        finally:
            typing.cancel()
            await self.client.room_typing(room.room_id, False)
        await self.send(room.room_id, reply[:MAX_REPLY_CHARS], reply_to=event)
        self.history[room.room_id].append(f"{self.user_id} (you): {reply[:500]}")

    async def keep_typing(self, room_id: str) -> None:
        while True:
            await self.client.room_typing(room_id, True, timeout=TYPING_TIMEOUT_MS)
            await asyncio.sleep(TYPING_REFRESH_S)

    def workdir(self, room_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", room_id)[:40]
        digest = hashlib.sha256(room_id.encode()).hexdigest()[:12]   # sanitizing alone is lossy
        return ROOT / "rooms" / self.owner / f"{safe}-{digest}"

    async def send(self, room_id: str, body: str, reply_to: RoomMessageText | None = None,
                   extra: dict | None = None) -> None:
        html = markdown.markdown(body, extensions=["fenced_code", "tables"])
        content = {"msgtype": "m.text", "body": body,
                   "format": "org.matrix.custom.html", "formatted_body": html}
        if reply_to is not None:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to.event_id}}
            content["m.mentions"] = {"user_ids": [reply_to.sender]}
        content.update(extra or {})
        resp = await self.client.room_send(room_id, "m.room.message", content)
        if not hasattr(resp, "event_id"):
            raise RuntimeError(f"failed to send to {room_id}: {resp}")

    # --- lifecycle -----------------------------------------------------------------
    async def run(self) -> None:
        password = read_env()[f"PW_{self.owner.upper()}_AGENT"]
        resp = await self.client.login(password, device_name=f"{self.owner}-agent bridge")
        if not isinstance(resp, LoginResponse):
            raise SystemExit(f"login failed for {self.user_id}: {resp}")
        # Pending invites go through the same check as live ones; messages from before
        # startup are skipped (message callbacks are registered after the first sync).
        self.client.add_event_callback(self.on_invite, InviteMemberEvent)
        await self.client.sync(timeout=0, full_state=True)
        self.client.add_event_callback(self.on_message, RoomMessageText)
        self.client.add_event_callback(self.on_encrypted, MegolmEvent)
        log.info("%s online (backend=%s, allow_members=%s)", self.user_id, self.backend, self.allow_members)
        await self.client.sync_forever(timeout=30_000)


def is_stale(event, started_ms: int) -> bool:
    return event.server_timestamp < started_ms - CLOCK_SKEW_MS


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("owner", help="person whose agent this is, e.g. anna")
    ap.add_argument("--agent", choices=sorted(agents.BACKENDS), default="codex")
    ap.add_argument("--allow-members", action="store_true",
                    help="let every joined room member drive this agent (default: owner only)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{args.owner}-agent] %(message)s")
    logging.getLogger("nio").setLevel(logging.WARNING)
    bridge = Bridge(args.owner, args.agent, args.allow_members)
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass  # Ctrl-C is the normal way to stop the bridge


if __name__ == "__main__":
    main()
