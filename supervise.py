"""Run every bridge and @pm, and restart them by themselves when the code changes.

  python supervise.py [--agent codex|claude] [--allow-members]

Every second it checks the .py files here. On a change it first compiles them (a syntax error
keeps the old processes running), then sends SIGTERM: each process hands off its Matrix sync
token and finishes the turns it already started (lifecycle.py), and a fresh one takes over, so
nothing sent meanwhile is lost. A process that crashes is restarted with a growing back-off.
Output goes to this terminal and to logs/<name>.log. Ctrl-C stops everything.
"""
import argparse
import asyncio
import py_compile
import signal
import sys
import time
from pathlib import Path

import agents
import lifecycle
from setup_accounts import PEOPLE

ROOT = Path(__file__).parent
PY = str(ROOT / ".venv" / "bin" / "python")
WATCH_S = 1.0
BACKOFF_S = (2, 5, 15, 60)       # restart delays after successive crashes
STABLE_S = 120                   # a process alive this long resets its crash count
DRAIN_LIMIT_S = lifecycle.DRAIN_TIMEOUT_S + 60   # a child drains itself; this is the backstop


def code_files() -> dict[Path, float]:
    return {p: p.stat().st_mtime for p in ROOT.glob("*.py") if not p.name.startswith("test_")}


class Child:
    def __init__(self, name: str, argv: list[str]) -> None:
        self.name, self.argv = name, argv
        self.proc: asyncio.subprocess.Process | None = None
        self.started = 0.0
        self.crashes = 0
        self.restarting = False
        self.closing = False             # supervisor shutting down: never spawn again

    async def start(self) -> None:
        if self.closing:
            return
        self.proc = await asyncio.create_subprocess_exec(*self.argv, cwd=ROOT, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.STDOUT)
        self.started = time.time()
        asyncio.create_task(self.pump(self.proc))

    async def pump(self, proc: asyncio.subprocess.Process) -> None:
        with open(ROOT / "logs" / f"{self.name}.log", "a") as logf:
            async for line in proc.stdout:
                text = line.decode(errors="replace")
                sys.stdout.write(text)
                logf.write(text)
                logf.flush()

    def claim(self) -> bool:
        """One lifecycle owner at a time: whoever claims the child (restart or revive) owns it
        until its new process is running. Claimed synchronously, before any await."""
        if self.restarting:
            return False
        self.restarting = True
        return True

    async def restart(self) -> None:
        """Graceful: the old process drains (may take minutes if an agent turn is running).
        Caller must have claim()ed the child."""
        try:
            await self.stop()
            await self.start()
        finally:
            self.restarting = False

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self.proc.wait(), DRAIN_LIMIT_S)
            except asyncio.TimeoutError:
                print(f"[supervise] {self.name} didn't exit in {DRAIN_LIMIT_S}s; killing it", flush=True)
                self.proc.kill()
                await self.proc.wait()


def children(agent: str, allow_members: bool) -> list[Child]:
    extra = ["--allow-members"] if allow_members else []
    kids = [Child(f"{p}-agent", [PY, "bridge.py", p, "--agent", agent, *extra]) for p in PEOPLE]
    return [*kids, Child("pm", [PY, "loop.py"])]


def compiles(paths: list[Path]) -> bool:
    for p in paths:
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"[supervise] {p.name} doesn't compile; keeping the running version:\n{e.msg}", flush=True)
            return False
    return True


async def revive(kid: Child) -> None:
    """A process exited on its own: restart it after a back-off that grows with repeated crashes.
    Caller must have claim()ed the child; the claim is held through the back-off and the spawn."""
    try:
        kid.crashes = 0 if time.time() - kid.started > STABLE_S else kid.crashes + 1
        delay = BACKOFF_S[min(kid.crashes, len(BACKOFF_S) - 1)]
        print(f"[supervise] {kid.name} exited ({kid.proc.returncode}); restarting in {delay}s", flush=True)
        await asyncio.sleep(delay)
        await kid.start()
    finally:
        kid.restarting = False


async def supervise(kids: list[Child]) -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    seen = code_files()
    for kid in kids:
        await kid.start()
    while True:
        await asyncio.sleep(WATCH_S)
        now = code_files()
        changed = sorted(p for p in now if seen.get(p) != now[p])
        if changed and compiles(changed):
            seen = now
            print(f"[supervise] code changed ({', '.join(p.name for p in changed)}): restarting", flush=True)
            for kid in kids:
                if kid.claim():          # a child already restarting/reviving picks up the new code anyway
                    asyncio.create_task(kid.restart())
        elif changed:
            seen = now                       # wait for the next save to try again
        for kid in kids:
            if kid.proc and kid.proc.returncode is not None and kid.claim():
                asyncio.create_task(revive(kid))


async def run(kids: list[Child]) -> None:
    """Supervise until SIGTERM, then stop every child (each drains) before exiting."""
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    watcher = asyncio.create_task(supervise(kids))
    await asyncio.wait({watcher, asyncio.create_task(stop.wait())}, return_when=asyncio.FIRST_COMPLETED)
    if watcher.done() and watcher.exception():
        raise watcher.exception()
    watcher.cancel()
    for kid in kids:
        kid.closing = True               # an in-flight restart/revive won't spawn a replacement
    await asyncio.gather(*(kid.stop() for kid in kids))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=sorted(agents.BACKENDS), default="codex")
    ap.add_argument("--allow-members", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(run(children(args.agent, args.allow_members)))
    except KeyboardInterrupt:
        pass  # Ctrl-C reaches the children too (same process group); they exit at once


if __name__ == "__main__":
    main()
