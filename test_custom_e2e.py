"""Live: Betty changes her agent's code from her private room, uses it, then goes back.
Needs ./start.sh running with betty on Codex or Claude Code. Takes a few minutes (a real agent edit).
Run: .venv/bin/python test_custom_e2e.py"""
import json
import time

import customize
from setup_accounts import HS, SERVER_NAME, load_env, login
from test_e2e import api, check, send

BETTY, AGENT = f"@betty:{SERVER_NAME}", f"@betty-agent:{SERVER_NAME}"
REQUEST = ("change your code so the voice transcript caption starts with \"🎙 Heard:\" instead of "
           "\"📝 Transcript:\"")
WAIT_S = 900


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
    token = login("betty", load_env()["PW_BETTY"])
    room = private_room(token)
    asked = send(token, room, REQUEST, AGENT)
    offer = wait_for(token, room, asked, "Your version is ready")
    check("the agent offers a changed version", True, offer["content"]["body"].splitlines()[0])
    check("the official files are untouched", "📝 Transcript" in (customize.ROOT / "pm_voice.py").read_text())
    click(token, room, offer["event_id"], "👍")
    done = wait_for(token, room, offer["event_id"], "Tests pass", timeout=600)
    check("its tests pass and it switches", "Switching" in done["content"]["body"])
    time.sleep(8)
    sha = customize.in_use().get("betty", "")
    check("betty's bridge runs her version", bool(sha) and customize.code_dir("betty") == customize.live_dir("betty"), sha[:7])
    asked = send(token, room, "go back to the official version", AGENT)
    wait_for(token, room, asked, "Going back", timeout=120)
    time.sleep(5)
    check("back on the official version", "betty" not in customize.in_use())
    print(json.dumps({"room": room}))


if __name__ == "__main__":
    main()
