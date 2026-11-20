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
from collections import defaultdict
from pathlib import Path

import markdown
from nio import (AsyncClient, InviteMemberEvent, LoginResponse, MatrixRoom, MegolmEvent, ReactionEvent, RoomMessage,
                 RoomMessageAudio, RoomMessageText)

import agents
from bridge_custom import BridgeCustom
from bridge_voice import BridgeVoice
import capabilities
import github
import lifecycle
import room_context
from redact import room_safe
import room_state
import tasks

ROOT = Path(__file__).parent
HOMESERVER = "http://127.0.0.1:8648"
SERVER_NAME = "bridge.localhost"
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


class Bridge(BridgeVoice, BridgeCustom):
    def __init__(self, owner: str, backend: str, allow_members: bool = False, buttons_only: bool = False):
        self.owner, self.backend, self.allow_members, self.buttons_only = owner, backend, allow_members, buttons_only
        self.owner_id = f"@{owner}:{SERVER_NAME}"
        self.user_id = f"@{owner}-agent:{SERVER_NAME}"
        self.client = AsyncClient(HOMESERVER, self.user_id, device_id=f"BRIDGE_{owner.upper()}")
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.warned_e2ee: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.running_steps: set[tuple] = set()      # loop steps in progress, see step_key()
        self.turns = asyncio.Semaphore(MAX_CONCURRENT_TURNS)
        self.started_ms = int(time.time() * 1000)
        self.sync_point = lifecycle.SyncPoint(self.client)   # restart hand-off position
        self.seen_tasks: set[str] = set()     # event ids: a redelivered task never runs twice
        self.last_message: dict[str, str] = {}  # room → newest message event id (to answer without a quote)
        self.refused: dict[tuple, float] = {}   # (room, person) → when I last said "only my owner"

    # --- trigger rules -------------------------------------------------------------
    # is_addressed / named_me / refuse: bridge_voice.py

    def should_ignore(self, room: MatrixRoom, event: RoomMessageText) -> str | None:
        if event.sender == self.user_id:
            return "own message"
        if event.sender not in room.users or event.sender in room.invited_users:
            return "sender is not a joined room member"
        if event.sender.split(":")[0].endswith("-agent"):
            return "message from another agent (loop guard)"
        if not self.owner_here(room):
            return "my owner isn't in this room"
        # The CLI runs with the owner's file access, so by default only the owner drives it.
        if event.sender != self.owner_id and not self.allow_members:
            return "sender is not the agent's owner (start with --allow-members to open it up)"
        return None

    def owner_here(self, room: MatrixRoom) -> bool:
        return self.owner_id in room.users and self.owner_id not in room.invited_users

    # --- event handlers ------------------------------------------------------------
    async def on_invite(self, room: MatrixRoom, event: InviteMemberEvent) -> None:
        if event.state_key != self.user_id or event.membership != "invite":
            return
        if event.sender not in (self.owner_id, f"@{github.PM_BOT}:{SERVER_NAME}"):
            # Only my owner brings me into a room (or @pm, following my owner's own setting):
            # nobody else can pull their agent into a room.
            log.warning("ignoring invite to %s from %s (not my owner or @pm)", room.room_id, event.sender)
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
        if is_stale(event, self.started_ms) or not self.sync_point.first_time(event.event_id):
            return   # pre-start replays, or handled by the previous process before a restart
        task = event.source.get("content", {}).get(github.TASK_KEY)
        if isinstance(task, dict):
            self.on_task(room, event, task)
            return
        name = room.user_name(event.sender) or event.sender
        line = f"{name} ({event.sender}): {event.body}"
        if not self.is_addressed(room, event):
            return                            # it's still context: answer() reads the room's conversation
        reason = self.should_ignore(room, event)
        if reason:
            await self.refuse(room, event, reason)   # bridge_voice.py: "I only take requests from Betty"
            return
        work = self.change_code(room, event) if self.wants_change(room, event) else self.answer_locked(room, event, line)
        task = asyncio.create_task(work)      # don't block sync; change_code: bridge_custom.py
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def on_task(self, room: MatrixRoom, event: RoomMessageText, task: dict) -> None:
        """Loop tasks: only from pm, only addressed to this agent, only from a joined member."""
        if (event.sender != f"@{github.PM_BOT}:{SERVER_NAME}" or task.get("agent") != self.user_id
                or event.sender not in room.users or event.sender in room.invited_users):
            log.info("ignored loop task in %s from %s", room.room_id, event.sender)
            return
        if event.event_id in self.seen_tasks:
            return
        if not self.owner_here(room):        # an agent never works in a room without its owner
            log.info("ignored loop task in %s: my owner isn't in the room", room.room_id)
            return
        self.seen_tasks.add(event.event_id)
        step = (room.room_id, *step_key(task))     # two rooms' repos can both have an issue #7
        if step in self.running_steps:     # a resend while the original is still working on it:
            log.info("already running %s; its result answers the resend too", step)
            return                         # the loop accepts the original's task_id for this step
        self.running_steps.add(step)
        job = asyncio.create_task(self.run_task(room.room_id, task))
        job.add_done_callback(lambda _: self.running_steps.discard(step))
        self.tasks.add(job)
        job.add_done_callback(self.tasks.discard)

    async def run_task(self, room_id: str, task: dict) -> None:
        what = f"PR #{task.get('pr')}" if task.get("pr") else f"#{task.get('issue')}"
        claimed = {"plan": "planning from the discussion…", "file_issues": "opening the planned issues…"}.get(
            task.get("kind"), f"{task.get('kind')}: claimed {what}")
        await self.send(room_id, claimed, source=github.linkify(claimed, task.get("repo")))
        typing = asyncio.create_task(self.keep_typing(room_id))
        try:
            async with self.turns:
                kind = task.get("kind")
                room_repo = await room_state.repo_of(self.client, room_id)
                if not room_repo or task.get("repo") != room_repo:   # only the room's bound repo (never None)
                    result = {"kind": kind, "issue": task.get("issue"), "task_id": task.get("task_id"),
                              "error": "wrong repo", "line": f"{kind} refused: the task isn't for this room's repo"}
                elif kind in tasks.AUTONOMOUS_ONLY and await room_state.level(self.client, room_id) != "autonomous":
                    result = {"kind": kind, "issue": task.get("issue"), "task_id": task.get("task_id"),
                              "error": "room is no longer autonomous", "line": f"{kind} skipped: room is no longer autonomous"}
                else:
                    base = ROOT / "rooms" / self.owner / "work" / room_context.repo_dir(room_repo)
                    result = await tasks.handle(f"{self.owner}-agent", self.backend, base, task)
        finally:
            typing.cancel()
            await self.client.room_typing(room_id, False)
        await self.send(room_id, result["line"], extra={github.RESULT_KEY: result}, source=github.linkify(result["line"], task.get("repo")))

    async def answer_locked(self, room: MatrixRoom, event: RoomMessageText, line: str) -> None:
        async with self.locks[room.room_id], self.turns:   # one turn per room; global cap
            await self.answer(room, event, line)

    async def answer(self, room: MatrixRoom, event: RoomMessageText, line: str) -> None:
        """Answer from the room's reality: live project status, everyone's recent conversation, and a
        read-only copy of the project's files (room_context.py) — not just the agent's own memory."""
        workdir = self.workdir(room.room_id)
        try:
            snap = await asyncio.to_thread(room_context.snapshot, self.client.access_token, room.room_id)
        except OSError as e:
            log.warning("no room context for %s: %s", room.room_id, e)
            snap = {"status": "", "conversation": ""}
        repo = snap.get("repo")
        try:
            room_context.prune_projects(workdir / "projects", repo and room_context.repo_dir(repo))
        except OSError as e:                  # an old repo's copy may remain: answer without any files
            log.warning("couldn't remove old project copies in %s: %s", workdir, e)
            repo = None
        has_files = bool(repo) and await room_context.refresh_project(
            f"{self.owner}-agent", repo, workdir / "projects" / room_context.repo_dir(repo))
        try:
            web = "web" in await asyncio.to_thread(capabilities.granted, self.client.access_token,
                                                   room.room_id, self.user_id)
        except OSError:
            web = False                       # can't read the grants: no extra tools
        prompt = (f"You are {self.owner}'s personal AI agent ({self.user_id}) in the Matrix chat room "
                  f"\"{room.display_name}\". Reply in chat style, concisely, as plain markdown.\n\n"
                  + room_context.describe(snap)
                  + ("\nThe project's files (default branch) are in ./projects/" + room_context.repo_dir(repo) + ": read them when the question is "
                     "about what the project contains or does.\n" if has_files else "")
                  + f"\nMessage addressed to you:\n{line}\n")
        typing = asyncio.create_task(self.keep_typing(room.room_id))
        try:
            reply = await agents.run_turn(self.backend, workdir, prompt, web=web)
        except agents.AgentError as e:
            log.error("agent failed in %s: %s", room.room_id, e)
            reply = f"⚠️ {self.owner}'s agent ({self.backend}) failed: {room_safe(e)}"
        finally:
            typing.cancel()
            await self.client.room_typing(room.room_id, False)
        await self.send(room.room_id, reply[:MAX_REPLY_CHARS], reply_to=event)

    async def keep_typing(self, room_id: str) -> None:
        while True:
            await self.client.room_typing(room_id, True, timeout=TYPING_TIMEOUT_MS)
            await asyncio.sleep(TYPING_REFRESH_S)

    def workdir(self, room_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", room_id)[:40]
        digest = hashlib.sha256(room_id.encode()).hexdigest()[:12]   # sanitizing alone is lossy
        return ROOT / "rooms" / self.owner / f"{safe}-{digest}"

    async def send(self, room_id: str, body: str, reply_to: RoomMessageText | None = None,
                   extra: dict | None = None, source: str | None = None) -> str:
        """body is the plain text; source (default body) is the markdown rendered as HTML."""
        html = markdown.markdown(source or body, extensions=["fenced_code", "tables"])
        content = {"msgtype": "m.text", "body": body,
                   "format": "org.matrix.custom.html", "formatted_body": html}
        if reply_to is not None:              # bridge_voice.py: same thread; a quote only if others spoke
            content.update(self.reply_relation(room_id, reply_to), **{"m.mentions": {"user_ids": [reply_to.sender]}})
        content.update(extra or {})
        resp = await self.client.room_send(room_id, "m.room.message", content)
        if not hasattr(resp, "event_id"):
            raise RuntimeError(f"failed to send to {room_id}: {resp}")
        return resp.event_id

    async def note_last(self, room: MatrixRoom, event: RoomMessage) -> None:
        rel = event.source.get("content", {}).get("m.relates_to")
        if not (isinstance(rel, dict) and rel.get("rel_type") in ("m.replace", "m.thread")):
            self.last_message[room.room_id] = event.event_id    # edits (the status board) and threads don't count

    # --- lifecycle -----------------------------------------------------------------
    async def run(self) -> None:
        password = read_env()[f"PW_{self.owner.upper()}_AGENT"]
        resp = await self.client.login(password, device_name=f"{self.owner}-agent bridge")
        if not isinstance(resp, LoginResponse):
            raise SystemExit(f"login failed for {self.user_id}: {resp}")
        # Pending invites go through the same check as live ones. Messages from before startup are
        # skipped, except those sent while a previous process was restarting (lifecycle hand-off).
        self.client.add_event_callback(self.on_invite, InviteMemberEvent)

        def register() -> None:
            if self.buttons_only:     # a Hermes agent answers itself: this only handles code-change buttons
                return self.client.add_event_callback(self.on_custom_reaction, ReactionEvent)
            self.client.add_event_callback(self.note_last, RoomMessage)     # first: on_message reads it
            self.client.add_event_callback(self.on_message, RoomMessageText)
            self.client.add_event_callback(self.on_voice, RoomMessageAudio)     # bridge_voice.py
            self.client.add_event_callback(self.on_encrypted, MegolmEvent)
            self.client.add_event_callback(self.on_custom_reaction, ReactionEvent)  # bridge_custom.py
        await lifecycle.first_sync(self.client, f"{self.owner}-agent", self.sync_point, register,
                                   lambda ms: setattr(self, "started_ms", ms))
        log.info("%s online (backend=%s, allow_members=%s)", self.user_id, self.backend, self.allow_members)
        await lifecycle.serve(self.client, f"{self.owner}-agent", self.sync_point, self.tasks)


def step_key(task: dict) -> tuple:
    """A loop step's identity across resends (each resend has a new task_id)."""
    return (str(task.get("repo")), task.get("kind"), str(task.get("issue")), str(task.get("pr")))


def is_stale(event, started_ms: int) -> bool:
    return event.server_timestamp < started_ms - CLOCK_SKEW_MS


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("owner", help="person whose agent this is, e.g. anna")
    ap.add_argument("--agent", choices=sorted(agents.BACKENDS), default="codex")
    ap.add_argument("--allow-members", action="store_true",
                    help="let every joined room member drive this agent (default: owner only)")
    ap.add_argument("--buttons-only", action="store_true", help="only the code-change buttons (Hermes agents)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{args.owner}-agent] %(message)s")
    logging.getLogger("nio").setLevel(logging.WARNING)
    bridge = Bridge(args.owner, args.agent, args.allow_members, args.buttons_only)
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass  # Ctrl-C is the normal way to stop the bridge


if __name__ == "__main__":
    main()
