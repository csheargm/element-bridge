"""Run every bridge and loop-bot, and restart them by themselves when the code changes.

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
from setup_accounts import PEOPLE

ROOT = Path(__file__).parent
PY = str(ROOT / ".venv" / "bin" / "python")
WATCH_S = 1.0
BACKOFF_S = (2, 5, 15, 60)       # restart delays after successive crashes
STABLE_S = 120                   # a process alive this long resets its crash count


def code_files() -> dict[Path, float]:
    return {p: p.stat().st_mtime for p in ROOT.glob("*.py") if not p.name.startswith("test_")}


class Child:
    def __init__(self, name: str, argv: list[str]) -> None:
        self.name, self.argv = name, argv
        self.proc: asyncio.subprocess.Process | None = None
        self.started = 0.0
        self.crashes = 0
        self.restarting = False

    async def start(self) -> None:
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

    async def restart(self) -> None:
        """Graceful: the old process drains (may take minutes if an agent turn is running)."""
        self.restarting = True
        try:
            if self.proc and self.proc.returncode is None:
                self.proc.send_signal(signal.SIGTERM)
                await self.proc.wait()
            await self.start()
        finally:
            self.restarting = False


def children(agent: str, allow_members: bool) -> list[Child]:
    extra = ["--allow-members"] if allow_members else []
    kids = [Child(f"{p}-agent", [PY, "bridge.py", p, "--agent", agent, *extra]) for p in PEOPLE]
    return [*kids, Child("loop-bot", [PY, "loop.py"])]


def compiles(paths: list[Path]) -> bool:
    for p in paths:
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"[supervise] {p.name} doesn't compile; keeping the running version:\n{e.msg}", flush=True)
            return False
    return True


async def revive(kid: Child) -> None:
    """A process exited on its own: restart it after a back-off that grows with repeated crashes."""
    kid.crashes = 0 if time.time() - kid.started > STABLE_S else kid.crashes + 1
    delay = BACKOFF_S[min(kid.crashes, len(BACKOFF_S) - 1)]
    print(f"[supervise] {kid.name} exited ({kid.proc.returncode}); restarting in {delay}s", flush=True)
    kid.restarting = True
    await asyncio.sleep(delay)
    kid.restarting = False
    await kid.start()


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
                if not kid.restarting:
                    asyncio.create_task(kid.restart())
        elif changed:
            seen = now                       # wait for the next save to try again
        for kid in kids:
            if not kid.restarting and kid.proc and kid.proc.returncode is not None:
                asyncio.create_task(revive(kid))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=sorted(agents.BACKENDS), default="codex")
    ap.add_argument("--allow-members", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(supervise(children(args.agent, args.allow_members)))
    except KeyboardInterrupt:
        pass  # Ctrl-C reaches the children too (same process group); they exit at once


if __name__ == "__main__":
    main()
