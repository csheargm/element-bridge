"""Connect your own Codex or Claude Code to Hagency Rooms as your agent, from your own computer.

  python connect.py CODE [--agent codex|claude]   # once, with the code @pm sent you
  python connect.py                               # later: start it again with the saved sign-in
  python connect.py --stop                        # stop it and remove the background service
  python connect.py --name cx-codex-agent [--stop] # the same for a further agent of yours

It trades @pm's one-time code for your agent's sign-in (POST <server>/connect), saves it in
~/.hagency/credentials.json (mode 600, never in this folder), and runs the bridge (bridge.py) as a
background service (agent_service.py: launchd on macOS, systemd --user on Linux; --foreground, or
neither available, runs it here instead). It works on this computer and obeys only you. A further
agent of yours (one @pm made with ➕) keeps its own sign-in, log and service in ~/.hagency/<its name>/,
so several run side by side.
connect.sh (served by the Hagency console at /connect.sh) installs this and starts it.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import agent_service
import cli_path
import identity
from bridge_config import credentials_match

APP = Path(__file__).resolve().parent
BACKENDS = ("codex", "claude")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
TIMEOUT_S = 30
USER_RE = re.compile(r"^@[a-z0-9-]{2,80}-agent:[A-Za-z0-9.-]+(:\d+)?$")


def safe_url(url: str) -> bool:
    """https, or plain http only to this computer (a password travels over it)."""
    parts = urllib.parse.urlsplit(url)
    return bool(parts.hostname) and (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS))


def redeem(server: str, code: str) -> dict:
    """The agent's sign-in, from a one-time code (the server forgets the code once used)."""
    req = urllib.request.Request(server.rstrip("/") + "/connect", method="POST", data=json.dumps({"code": code}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read()).get("error")
        except ValueError:
            reason = None
        raise SystemExit(f"✗ {reason or f'the server said {e.code}'}") from e
    except (OSError, ValueError) as e:
        raise SystemExit(f"✗ couldn't reach {server}: {e}") from e
    owner = data.get("owner") if isinstance(data, dict) else None
    if not (isinstance(data, dict) and USER_RE.match(str(data.get("user_id", ""))) and safe_url(str(data.get("homeserver", "")))
            and isinstance(owner, str) and (identity.USER_RE.match(owner) or identity.NAME_RE.match(owner))
            and credentials_match(data["user_id"], owner, data.get("label")) and isinstance(data.get("password"), str)):
        raise SystemExit("✗ the server's answer doesn't look like an agent's sign-in; nothing was saved")
    return {k: data[k] for k in ("homeserver", "user_id", "password", "owner", "label") if k in data}


def agent_dir(home: Path, creds: dict) -> Path:
    """Where this agent's sign-in, log and service live: ~/.hagency for a first agent, its own
    folder for a further one (its name was checked: [a-z0-9-] only)."""
    return home / agent_name(creds) if creds.get("label") else home


def read_saved(path: Path) -> dict:
    try:
        saved = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        saved = {}                            # damaged: a new code replaces it
    return saved if isinstance(saved, dict) else {}


def save(path: Path, data: dict) -> None:
    """Written as 0600 (in a 0700 folder, whatever the umask made it) and swapped in whole."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt" and path.parent.stat().st_mode & 0o077:   # Windows: no mode bits; connect.ps1 sets the ACL
        print(f"note: {path.parent} can be read by others; the sign-in file itself is only yours", file=sys.stderr)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".credentials-")   # 0600, a new file: no symlink games
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def pick_backend(given: str | None, saved: str | None, ask=input) -> str:
    """--agent, else the last choice, else the one CLI installed here (or ask when both are)."""
    for choice in (given, saved):
        if choice in BACKENDS:
            if not cli_path.startable(choice):
                raise SystemExit(f"✗ {choice} isn't installed here (or not on PATH)")
            return choice
    found = [b for b in BACKENDS if cli_path.startable(b)]
    if len(found) == 1:
        return found[0]
    if not found:
        raise SystemExit("✗ install Codex (npm i -g @openai/codex) or Claude Code (npm i -g @anthropic-ai/claude-code) first")
    if not sys.stdin.isatty():
        raise SystemExit("✗ both Codex and Claude Code are installed: choose one with --agent codex or --agent claude")
    while (choice := ask("Which one should be your agent, codex or claude? ").strip().lower()) not in BACKENDS:
        print("Please type codex or claude.")
    return choice


def agent_name(creds: dict) -> str:
    return str(creds.get("user_id", "")).lstrip("@").partition(":")[0]


def background(creds: dict, path: Path) -> bool:
    """Start the agent as a background service; False when this computer has none (run it here)."""
    try:
        how = agent_service.install(APP, creds["owner"], creds["agent"], agent_name(creds), path)
    except (OSError, RuntimeError) as e:     # said plainly, then it runs in the foreground instead
        print(f"note: couldn't start it in the background ({e}); running it here instead", file=sys.stderr)
        return False
    if how is None:
        return False
    print(f"✓ Your agent now runs in the background ({how}): you can close this Terminal. It starts again when\n"
          f"  you log in. Its log: {path.parent / 'logs' / (agent_name(creds) + '.log')}\n"
          f"  To stop it: {sys.executable} {APP / 'connect.py'} {name_arg(creds)}--stop")
    return True


def name_arg(creds: dict) -> str:
    return f"--name {agent_name(creds)} " if creds.get("label") else ""


def stop(saved: dict, home: Path) -> None:
    name = agent_name(saved)
    if not name:
        raise SystemExit("✗ not connected on this computer: nothing to stop")
    print(f"✓ stopped {name} and removed its background service" if agent_service.stop(name, home=home, app=APP)
          else f"✓ {name} had no background service here (if it runs in a Terminal, press Ctrl-C there)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("code", nargs="?", help="the one-time code from @pm (not needed once connected)")
    ap.add_argument("--agent", choices=BACKENDS)
    ap.add_argument("--server", default=os.environ.get("HAGENCY_CONSOLE", "http://localhost:8650"),
                    help="the Hagency console that sent the code")
    ap.add_argument("--home", type=Path, default=Path(os.environ.get("HAGENCY_DIR", Path.home() / ".hagency")),
                    help="where the sign-in is kept (default ~/.hagency)")
    ap.add_argument("--pair-only", action="store_true", help="save the sign-in, don't start the agent")
    ap.add_argument("--foreground", action="store_true", help="run here (Ctrl-C stops it), not as a background service")
    ap.add_argument("--stop", action="store_true", help="stop the agent and remove its background service")
    ap.add_argument("--name", help="a further agent of yours, by its name (e.g. cx-codex-agent)")
    args = ap.parse_args()
    if args.name and not USER_RE.match(f"@{args.name}:x"):
        raise SystemExit(f"✗ {args.name} isn't an agent's name (like cx-codex-agent)")
    home = args.home.resolve()
    path = (home / args.name if args.name else home) / "credentials.json"
    saved = read_saved(path)
    if args.stop:
        stop(saved, path.parent)
        return
    if not args.code and not saved:
        raise SystemExit("✗ not connected yet: run this with the code @pm sent you")
    backend = pick_backend(args.agent, saved.get("agent"))      # before the code is used up
    if args.code:
        if not safe_url(args.server):
            raise SystemExit(f"✗ {args.server}: use https (plain http only for this computer)")
        saved = redeem(args.server, args.code)
        path = agent_dir(home, saved) / "credentials.json"       # a further agent: its own folder
        if saved.get("label") and not args.agent:   # its own last choice, or what it's called ("codex"), if installed
            backend = next((b for b in (read_saved(path).get("agent"), saved["label"]) if b in BACKENDS and cli_path.startable(b)),
                           backend)
    creds = {**saved, "agent": backend}
    save(path, creds)
    print(f"✓ connected as {creds['user_id']} ({creds['agent']}); only {creds['owner']} can direct it")
    if args.pair_only:
        if creds.get("label") and os.name == "nt":   # connect.ps1 registers only the first agent's task (for now)
            raise SystemExit(f"✓ saved. On Windows a further agent runs in a window for now: "
                             f"{sys.executable} {APP / 'connect.py'} --name {agent_name(creds)} --foreground")
        return
    if not args.foreground and background(creds, path):
        return
    print("Your agent is online while this runs (Ctrl-C stops it).", flush=True)
    os.chdir(APP)
    os.execve(sys.executable, [sys.executable, str(APP / "bridge.py"), creds["owner"], "--agent", creds["agent"]],
              {**os.environ, "HAGENCY_CREDENTIALS": str(path)})


if __name__ == "__main__":
    main()
