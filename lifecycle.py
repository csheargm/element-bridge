"""Graceful restarts for the bridges and loop-bot (driven by supervise.py).

On SIGTERM a process stops syncing, writes its Matrix sync position to a hand-off file, finishes
the turns/tasks it already started, and exits. The next process resumes syncing from there, so
messages sent during the restart are handled exactly once instead of being skipped as history.

The saved position is the last sync batch whose events were ALL handled, plus the ids handled
after it: matrix-nio advances client.next_batch before it runs event callbacks, so saving
next_batch after a mid-batch stop would silently skip the rest of that batch.
"""
import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from collections import deque
from collections.abc import Callable, Coroutine
from pathlib import Path

from nio import AsyncClient, SyncResponse

HANDOFF_DIR = Path(__file__).parent / "rooms" / ".handoff"      # git-ignored (rooms/)
HANDOFF_MAX_AGE_S = 900          # older hand-offs are ignored: treat it as a cold start
DRAIN_TIMEOUT_S = 1200           # agent turn limit (600 s) plus its gh/git calls
SYNC_SLACK_MS = 60_000           # events since the token are at most one long-poll older than it

log = logging.getLogger("lifecycle")


def _path(name: str) -> Path:
    return HANDOFF_DIR / f"{name}.json"


class SyncPoint:
    """Tracks the last fully handled sync batch and the events handled since (see module doc)."""
    def __init__(self, client: AsyncClient, handled: list[str] | None = None) -> None:
        self.token: str | None = None
        self.handled: deque[str] = deque(handled or [], maxlen=1000)
        client.add_response_callback(self._batch_done, SyncResponse)   # runs after event callbacks

    async def _batch_done(self, response: SyncResponse) -> None:
        self.token = response.next_batch
        self.handled.clear()

    def first_time(self, event_id: str) -> bool:
        """False for an event already handled before a restart; call once per event handled."""
        if event_id in self.handled:
            return False
        self.handled.append(event_id)
        return True


def save_handoff(name: str, point: SyncPoint) -> None:
    token = point.token
    if not token:
        return
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(name).with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"token": token, "handled": list(point.handled), "ms": int(time.time() * 1000)}, f)
    os.replace(tmp, _path(name))


def take_handoff(name: str) -> dict | None:
    """The previous process's sync token, if it handed one off recently (consumed on read)."""
    p = _path(name)
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("ignoring unreadable hand-off %s: %s", p, e)   # cold start is the safe fallback
        data = None
    p.unlink(missing_ok=True)
    if data and time.time() * 1000 - data["ms"] < HANDOFF_MAX_AGE_S * 1000:
        return data
    return None


async def first_sync(client: AsyncClient, name: str, point: SyncPoint, register: Callable[[], None]) -> int:
    """Resume from a hand-off (callbacks see what was missed) or skip history (callbacks added after).
    Returns the cutoff in ms before which events count as stale."""
    handoff = take_handoff(name)
    if handoff:
        point.handled.extend(handoff.get("handled", []))
        register()
        await client.sync(timeout=0, since=handoff["token"], full_state=True)
        log.info("%s resumed from hand-off", name)
        return handoff["ms"] - SYNC_SLACK_MS
    started = int(time.time() * 1000)
    await client.sync(timeout=0, full_state=True)    # sets point.token: nothing before is ours
    register()
    return started


async def serve(client: AsyncClient, name: str, point: SyncPoint, busy: set[asyncio.Task],
                side: Coroutine | None = None) -> None:
    """Sync until SIGTERM, then hand off the sync token and let `busy` tasks finish."""
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    runners = {asyncio.create_task(client.sync_forever(timeout=30_000))}
    if side is not None:
        runners.add(asyncio.create_task(side))
    stopper = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait({*runners, stopper}, return_when=asyncio.FIRST_COMPLETED)
    for t in (*runners, stopper):
        t.cancel()
    for t in runners:
        if t in done and t.exception():
            raise t.exception()          # a crash, not a restart: let the supervisor see it
        with contextlib.suppress(asyncio.CancelledError):
            await t
    save_handoff(name, point)
    if busy:
        log.info("%s finishing %d running task(s) before restart", name, len(busy))
        await asyncio.wait(busy, timeout=DRAIN_TIMEOUT_S)
    await client.close()
