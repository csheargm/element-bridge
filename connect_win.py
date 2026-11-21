"""Keep your agent running on Windows: what the Scheduled Task "Hagency agent <name>" runs.

  pythonw connect_win.py               # run the agent hidden, and again whenever it stops
  python connect_win.py --task-name    # print the task's name (connect.ps1 uses it)

connect.ps1 has connect.py save the sign-in first (--pair-only), then registers this to run at
logon. It reads that sign-in (~/.hagency/credentials.json), runs the bridge as a hidden child with
HAGENCY_CREDENTIALS pointing at the file (so no secret is ever on a command line), and starts it
again when it stops, waiting longer each time it fails quickly. The bridge's output goes to
~/.hagency/agent.log; ~/.hagency/agent.pid names this process and the bridge, so connect.ps1 can
stop both before it updates the app.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import bridge_config

APP = Path(__file__).resolve().parent
FIRST_DELAY_S = 5
MAX_DELAY_S = 300
HEALTHY_RUN_S = 3600                          # ran this long: the next stop starts the backoff over
LOG_MAX_BYTES = 5 * 1024 * 1024
CREATE_NO_WINDOW = 0x08000000                 # Windows: the bridge gets no console window


def task_name(creds: dict) -> str:
    """ "Hagency agent anna", from "@anna-agent:server" (connect.py checked it is [a-z0-9-])."""
    local = creds["user_id"].lstrip("@").partition(":")[0]
    return f"Hagency agent {local.removesuffix('-agent')}"


def child_python() -> str:
    """python.exe beside pythonw.exe (the bridge's output must go somewhere); else this one."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return str(console) if exe.name.lower() == "pythonw.exe" and console.exists() else str(exe)


def bridge_command(creds: dict, creds_path: Path) -> tuple[list[str], dict]:
    """argv and environment for the bridge: the sign-in travels by file path, never in argv."""
    argv = [child_python(), str(APP / "bridge.py"), creds["owner"], "--agent", creds.get("agent") or "codex"]
    return argv, {**os.environ, "HAGENCY_CREDENTIALS": str(creds_path)}


def next_delay(delay: float, ran_s: float) -> float:
    """How long to wait before starting again: doubles on quick failures, resets after a good run."""
    if ran_s >= HEALTHY_RUN_S:
        return FIRST_DELAY_S
    return min(MAX_DELAY_S, max(FIRST_DELAY_S, delay * 2))


def open_log(path: Path):
    """agent.log, with the previous one kept as agent.log.1 once it grows past LOG_MAX_BYTES."""
    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        os.replace(path, path.with_name(path.name + ".1"))
    return open(path, "ab")


def keep_running(home: Path, runs: int | None = None, spawn=subprocess.Popen, sleep=time.sleep,
                 clock=time.monotonic) -> None:
    """Run the bridge, and again each time it stops (`runs` limits it, for tests)."""
    creds_path = home / "credentials.json"
    delay = 0.0
    while runs is None or runs > 0:
        creds = bridge_config.load_credentials(str(creds_path))   # each time: a new code may have replaced it
        argv, env = bridge_command(creds, creds_path)
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        started = clock()
        with open_log(home / "agent.log") as log:
            proc = spawn(argv, cwd=str(APP), env=env, stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT, creationflags=flags)
            (home / "agent.pid").write_text(f"{os.getpid()}\n{proc.pid}\n")
            code = proc.wait()
            delay = next_delay(delay, clock() - started)
            log.write(f"[connect_win] the agent stopped (exit {code}); starting again in {delay:.0f}s\n".encode())
        if runs is not None:
            runs -= 1
        sleep(delay)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", type=Path, default=Path(os.environ.get("HAGENCY_DIR", Path.home() / ".hagency")),
                    help="where connect.py keeps the sign-in (default ~/.hagency)")
    ap.add_argument("--task-name", action="store_true", help="print the Scheduled Task's name and stop")
    args = ap.parse_args()
    if args.task_name:
        print(task_name(bridge_config.load_credentials(str(args.home / "credentials.json"))))
        return
    try:
        keep_running(args.home)
    except SystemExit as e:                   # pythonw has no console: say why in the log, then stop
        args.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open_log(args.home / "agent.log") as log:
            log.write(f"[connect_win] {e}\n".encode())
        raise


if __name__ == "__main__":
    main()
