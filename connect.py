"""Connect your own Codex or Claude Code to Hagency Rooms as your agent, from your own computer.

  python connect.py CODE [--agent codex|claude]   # once, with the code @pm sent you; then it runs
  python connect.py                               # later: run again with the saved sign-in

It trades @pm's one-time code for your agent's sign-in (POST <server>/connect), saves it in
~/.hagency/credentials.json (mode 600, never in this folder), and runs the bridge (bridge.py) in
the foreground: your agent is online while this runs, works on this computer, and obeys only you.
connect.sh (served by the Hagency console at /connect.sh) installs this and starts it.
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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
            and credentials_match(data["user_id"], owner) and isinstance(data.get("password"), str)):
        raise SystemExit("✗ the server's answer doesn't look like an agent's sign-in; nothing was saved")
    return {k: data[k] for k in ("homeserver", "user_id", "password", "owner")}


def save(path: Path, data: dict) -> None:
    """Written as 0600 (in a 0700 folder, whatever the umask made it) and swapped in whole."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        print(f"note: {path.parent} can be read by others; the sign-in file itself is only yours", file=sys.stderr)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".credentials-")   # 0600, a new file: no symlink games
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def pick_backend(given: str | None, saved: str | None, ask=input) -> str:
    """--agent, else the last choice, else the one CLI installed here (or ask when both are)."""
    for choice in (given, saved):
        if choice in BACKENDS:
            if not shutil.which(choice):
                raise SystemExit(f"✗ {choice} isn't installed here (or not on PATH)")
            return choice
    found = [b for b in BACKENDS if shutil.which(b)]
    if len(found) == 1:
        return found[0]
    if not found:
        raise SystemExit("✗ install Codex (npm i -g @openai/codex) or Claude Code (npm i -g @anthropic-ai/claude-code) first")
    if not sys.stdin.isatty():
        raise SystemExit("✗ both Codex and Claude Code are installed: choose one with --agent codex or --agent claude")
    while (choice := ask("Which one should be your agent, codex or claude? ").strip().lower()) not in BACKENDS:
        print("Please type codex or claude.")
    return choice


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("code", nargs="?", help="the one-time code from @pm (not needed once connected)")
    ap.add_argument("--agent", choices=BACKENDS)
    ap.add_argument("--server", default=os.environ.get("HAGENCY_CONSOLE", "http://localhost:8650"),
                    help="the Hagency console that sent the code")
    ap.add_argument("--home", type=Path, default=Path(os.environ.get("HAGENCY_DIR", Path.home() / ".hagency")),
                    help="where the sign-in is kept (default ~/.hagency)")
    ap.add_argument("--pair-only", action="store_true", help="save the sign-in, don't start the agent")
    args = ap.parse_args()
    path = args.home / "credentials.json"
    try:
        saved = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        saved = {}                            # damaged: a new code replaces it
    saved = saved if isinstance(saved, dict) else {}
    if not args.code and not saved:
        raise SystemExit("✗ not connected yet: run this with the code @pm sent you")
    backend = pick_backend(args.agent, saved.get("agent"))      # before the code is used up
    if args.code:
        if not safe_url(args.server):
            raise SystemExit(f"✗ {args.server}: use https (plain http only for this computer)")
        saved = redeem(args.server, args.code)
    creds = {**saved, "agent": backend}
    save(path, creds)
    print(f"✓ connected as {creds['user_id']} ({creds['agent']}); only {creds['owner']} can direct it")
    if args.pair_only:
        return
    print("Your agent is online while this runs (Ctrl-C stops it).", flush=True)
    os.chdir(APP)
    os.execve(sys.executable, [sys.executable, str(APP / "bridge.py"), creds["owner"], "--agent", creds["agent"]],
              {**os.environ, "HAGENCY_CREDENTIALS": str(path)})


if __name__ == "__main__":
    main()
