"""Live: Betty changes her agent's code from her private room, uses it, then goes back.
Needs ./start.sh running with betty on Codex or Claude Code. Takes a few minutes (a real agent edit).
It starts by deleting Betty's private copy (custom/betty): a test install, not someone's real one.
Run: .venv/bin/python test_custom_e2e.py"""
import json
import shutil
import subprocess
import time
from pathlib import Path

import customize
from setup_accounts import HS, SERVER_NAME, load_env, login
from test_e2e import api, check, send

BETTY, AGENT = f"@betty:{SERVER_NAME}", f"@betty-agent:{SERVER_NAME}"
REQUEST = ("change your code so the voice transcript caption starts with \"🎙 Heard:\" instead of "
           "\"📝 Transcript:\"")
WAIT_S = 900


def running_dir(owner: str) -> Path:
    """The folder betty's bridge process really runs in (not just what the state file says)."""
    pid = subprocess.run(["pgrep", "-f", f"bridge.py {owner} "], capture_output=True, text=True).stdout.split()[0]
    out = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
    return Path(next(line[1:] for line in out.splitlines() if line.startswith("n"))).resolve()


def private_room(token: str) -> str:
    room = api("POST", "/_matrix/client/v3/createRoom", token, {
        "preset": "trusted_private_chat", "is_direct": True, "invite": [AGENT], "name": "Betty's code (test)"})["room_id"]
    for _ in range(30):
        members = api("GET", f"/_matrix/client/v3/rooms/{room}/joined_members", token)["joined"]
        if AGENT in members:
            return room
        time.sleep(1)
    raise AssertionError("betty-agent didn't join the private room")


def wait_for(token: str, room: str, since: str, needle: str, timeout: float = WAIT_S) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = api("GET", f"/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=50", token)["chunk"]
        for ev in msgs:
            if ev.get("event_id") == since:
                break
            if ev.get("sender") == AGENT and needle in ev.get("content", {}).get("body", ""):
                return ev
        time.sleep(3)
    raise AssertionError(f"no {needle!r} from betty-agent within {timeout:.0f}s")


def click(token: str, room: str, event_id: str, key: str) -> None:
    api("PUT", f"/_matrix/client/v3/rooms/{room}/send/m.reaction/r{time.time_ns()}", token,
        {"m.relates_to": {"rel_type": "m.annotation", "event_id": event_id, "key": key}})


def main() -> None:
    shutil.rmtree(customize.COPIES / "betty", ignore_errors=True)   # start from a fresh private copy
    subprocess.run(["git", "-C", str(customize.ROOT), "worktree", "prune"], check=True)
    subprocess.run(["git", "-C", str(customize.ROOT), "branch", "-D", "custom/betty"], capture_output=True)
    token = login("betty", load_env()["PW_BETTY"])
    room = private_room(token)
    asked = send(token, room, REQUEST, AGENT)
    offer = wait_for(token, room, asked, "Your version is ready")
    check("the agent offers a changed version", True, offer["content"]["body"].splitlines()[0])
    check("the official files are untouched", "📝 Transcript" in (customize.ROOT / "pm_voice.py").read_text())
    if customize.why_not_use("betty"):    # this computer runs other people's agents too: PR or discard only
        check("no 'Use it myself' on a shared computer", "Using it here isn't possible" in offer["content"]["body"])
        click(token, room, offer["event_id"], "🗑️")
        wait_for(token, room, offer["event_id"], "Discarded", timeout=120)
        check("discarded", True)
        print(json.dumps({"room": room}))
        return
    click(token, room, offer["event_id"], "👍")
    done = wait_for(token, room, offer["event_id"], "tests pass", timeout=600)
    check("its tests pass and it switches", "Switching" in done["content"]["body"])
    time.sleep(10)
    live, sha = customize.code_dir("betty")
    check("betty's bridge runs her version", bool(sha) and running_dir("betty") == live.resolve(), (sha or "")[:7])
    asked = send(token, room, "go back to the official version", AGENT)
    wait_for(token, room, asked, "Going back", timeout=120)
    time.sleep(10)
    check("back on the official version", "betty" not in customize.in_use() and running_dir("betty") == customize.ROOT.resolve())
    print(json.dumps({"room": room}))


if __name__ == "__main__":
    main()
