"""Spike: bring-your-own Hermes. Who may drive the agent is MATRIX_ALLOWED_USERS (its owner, plus
pm for tasks); there is no room allowlist, so the owner can also DM it. Anna's agent is a Hermes profile (`annaroom`) talking to Matrix
through Hermes's own gateway and to the GitHub loop through hagency_mcp.py; betty's agent is the
Codex bridge. Real Matrix, real Hermes and Codex, fake GitHub. Stop ./start.sh first.
Run: .venv/bin/python test_hermes_e2e.py

A. Hermes answers its owner in the room, and ignores betty (MATRIX_ALLOWED_USERS).
B. #210 → Hermes builds (via MCP) → betty-agent reviews → Hermes merges (Autonomous).
C. #211 → betty-agent builds → Hermes reviews (via MCP) → betty-agent merges.
"""
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

import test_loop_e2e
from setup_accounts import (BOTS, SERVER_NAME, add_bot, api, load_env, login, register, save_env)
from test_e2e import check, send, wait_reply
from test_loop_e2e import make_fake_github, say, wait_for

ROOT = Path(__file__).parent
PY = str(ROOT / ".venv" / "bin" / "python")
HERMES = "anna-hermes-agent"
PROFILE = Path.home() / ".hermes" / "profiles" / "annaroom"
STEP_S = 900
test_loop_e2e.STEP_TIMEOUT_S = STEP_S
test_loop_e2e.ISSUES = {
    "210": {"title": "docs: add docs/HERMES.md", "state": "OPEN", "labels": ["agent-ok"],
            "body": "Acceptance criteria:\n- New file docs/HERMES.md whose only line is exactly '# Hermes'.\n- No other files change."},
    "211": {"title": "docs: add docs/BETTY.md", "state": "OPEN", "labels": ["agent-ok"],
            "body": "Acceptance criteria:\n- New file docs/BETTY.md whose only line is exactly '# Betty'.\n- No other files change."},
}
MATRIX_ENV_KEYS = ("MATRIX_HOME_ROOM", "MATRIX_HOMESERVER", "MATRIX_USER_ID", "MATRIX_PASSWORD", "MATRIX_DEVICE_ID",
                   "MATRIX_ALLOWED_USERS", "MATRIX_ALLOWED_ROOMS", "MATRIX_E2EE_MODE", "MATRIX_AUTO_THREAD",
                   "MATRIX_SESSION_SCOPE", "MATRIX_REQUIRE_MENTION")


def ensure_hermes_account(env: dict) -> str:
    key = "PW_" + HERMES.upper().replace("-", "_")
    if key not in env:
        env[key] = secrets.token_urlsafe(18)
        save_env(env)
        yaml = (ROOT / "data" / "homeserver.yaml").read_text()
        shared = yaml.split("registration_shared_secret:", 1)[1].split("\n", 1)[0].strip().strip('"')
        r = register(HERMES, env[key], shared)
        if "_status" in r:
            raise SystemExit(f"register {HERMES} failed: {r.get('errcode')}")
    return env[key]


def hermes_room(env: dict) -> str:
    """#hermes-test: anna (creator), betty, Hermes as anna's agent, betty's Codex agent, the bots."""
    anna = login("anna", env["PW_ANNA"])
    alias = f"#hermes-test:{SERVER_NAME}"
    room_id = api("GET", f"/_matrix/client/v3/directory/room/{urllib.parse.quote(alias)}", token=anna).get("room_id")
    if not room_id:
        room_id = api("POST", "/_matrix/client/v3/createRoom", {
            "name": "Hermes test", "room_alias_name": "hermes-test", "preset": "private_chat",
            "invite": [f"@{u}:{SERVER_NAME}" for u in ("betty", "betty-agent", HERMES)]}, token=anna)["room_id"]
    room = urllib.parse.quote(room_id)
    for user in ("betty", HERMES):
        api("POST", f"/_matrix/client/v3/join/{room}", {}, token=login(user, env["PW_" + user.upper().replace("-", "_")]))
    for bot, power in BOTS.items():
        add_bot(anna, login(bot, env["PW_" + bot.upper().replace("-", "_")]), room, bot, power)
    return room_id


def configure_profile(room_id: str, password: str, gh_env: dict) -> None:
    """The annaroom profile: Matrix (owner + pm only, this room only), no shell/web/browser,
    and the hagency MCP server with this run's fake GitHub."""
    mcp_env = {"HAGENCY_AGENT": HERMES, "HAGENCY_ROOM": room_id, "PATH": os.environ["PATH"],
               "HOME": os.environ["HOME"], **gh_env}
    env_lines = "\n".join(f"      {k}: '{v}'" for k, v in mcp_env.items())
    (PROFILE / "config.yaml").write_text(f"""model:
  default: gpt-oss:120b
  provider: ollama-cloud
  base_url: https://ollama.com/v1
toolsets: [hermes-cli]
platform_toolsets:
  matrix: [todo, memory, skills]      # no file/terminal/web: repo edits go through hagency's contained tools
approvals:
  mode: manual
display:                     # a shared room sees final replies only: no tool calls, paths or drafts
  tool_progress: "off"
  interim_assistant_messages: false
  memory_notifications: "off"   # Hermes still learns; it just doesn't announce it in the room
matrix:
  require_mention: true
mcp_servers:
  hagency:
    command: {PY}
    args: [{ROOT / 'hagency_mcp.py'}]
    timeout: 300
    env:
{env_lines}
""")
    values = {"MATRIX_HOMESERVER": "http://127.0.0.1:8648", "MATRIX_USER_ID": f"@{HERMES}:{SERVER_NAME}",
              "MATRIX_PASSWORD": password, "MATRIX_DEVICE_ID": "HERMES_ANNAROOM",
              "MATRIX_ALLOWED_USERS": f"@anna:{SERVER_NAME},@pm:{SERVER_NAME}",
              "MATRIX_HOME_ROOM": room_id, "MATRIX_E2EE_MODE": "off", "MATRIX_AUTO_THREAD": "false",
              "MATRIX_SESSION_SCOPE": "room", "MATRIX_REQUIRE_MENTION": "true"}
    path = PROFILE / ".env"
    kept = [ln for ln in path.read_text().splitlines() if ln.split("=", 1)[0] not in MATRIX_ENV_KEYS]
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join([*kept, *(f"{k}={v}" for k, v in values.items())]) + "\n")
    soul = PROFILE / "SOUL.md"
    note = ("\n\nIn Hagency rooms: when @pm gives you a task, ALWAYS call the hagency tool next_task "
            "first, even if the issue looks familiar from earlier in the conversation: each task is new "
            "work and the tools, not your memory, say what to do. Then do exactly what each answer's "
            "`next` field says. Edit repo files only with the hagency read_file/write_files tools. "
            "Other people read the room: never post file paths, folder names, tool calls or raw JSON "
            "there; reply with one short line about the outcome.\n")
    text = soul.read_text().split("\n\nIn Hagency rooms:")[0]
    soul.write_text(text + note)


def main():
    env = load_env()
    password = ensure_hermes_account(env)
    room_id = hermes_room(env)
    tmp = Path(tempfile.mkdtemp(prefix="hermes-e2e-"))
    gh_env = make_fake_github(tmp)
    for agent in (HERMES,):
        (Path(gh_env["GH_IDENTITY_ROOT"]) / agent).mkdir(parents=True, exist_ok=True)
        (Path(gh_env["GH_IDENTITY_ROOT"]) / agent / "hosts.yml").write_text("github.com: {}\n")
    configure_profile(room_id, password, gh_env)
    for stale in ("sessions", "state.db", "state.db-wal", "state.db-shm"):   # each run starts with no
        path = PROFILE / stale                                                 # memory of earlier runs
        shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
    run_env = {**os.environ, **gh_env}
    logs = open(tmp / "procs.log", "w")
    procs = [subprocess.Popen([PY, *a], cwd=ROOT, env=run_env, stdout=logs, stderr=logs)
             for a in (["bridge.py", "betty"], ["loop.py"])]
    procs.append(subprocess.Popen(["hermes", "-p", "annaroom", "gateway", "run"], stdout=logs, stderr=logs))
    anna, betty = login("anna", env["PW_ANNA"]), login("betty", env["PW_BETTY"])
    me = f"@{HERMES}:{SERVER_NAME}"
    agent = lambda who: f"@{who}-agent:{SERVER_NAME}"
    try:
        time.sleep(25)
        say(anna, room_id, "!level off")
        # A. owner chat
        ev = send(anna, room_id, f"@{HERMES} reply with just the word READY", me)
        reply, _ = wait_reply(anna, room_id, ev, me)
        check("Hermes answers its owner in the room", "READY" in reply.upper(), reply[:80])
        dm = api("POST", "/_matrix/client/v3/createRoom", {"is_direct": True, "preset": "trusted_private_chat",
                                                           "invite": [me]}, token=anna)["room_id"]
        time.sleep(5)                                              # Hermes accepts the invite
        ts = int(time.time() * 1000)
        say(anna, dm, "reply with just the word DMOK")            # a DM needs no @mention
        reply = wait_for(anna, dm, lambda s, b: s == me and "DMOK" in b.upper(), "DM reply", ts)
        check("anna can DM her own agent", True, reply[:80])
        ts = int(time.time() * 1000)
        send(betty, room_id, f"@{HERMES} reply with just the word NOPE", me)
        time.sleep(40)
        evs = api("GET", f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/messages?dir=b&limit=20", token=anna)["chunk"]
        # (a late edit of the READY reply is fine: Hermes streams replies as edits)
        nope = [e for e in evs if e["sender"] == me and "NOPE" in e.get("content", {}).get("body", "").upper()]
        check("Hermes ignores betty", not nope and "Unauthorized user: @betty" in
              (PROFILE / "logs" / "gateway.log").read_text())

        # B. Hermes builds #210 through the MCP server, betty-agent reviews, Hermes merges
        t0, ts = time.time(), int(time.time() * 1000)
        say(anna, room_id, "!level autonomous")
        body = wait_for(anna, room_id, lambda s, b: s == me and b.startswith(("PR #", "⚠️", "no change")), "Hermes PR", ts)
        check("Hermes opens a PR for #210 via MCP", "opened for #210" in body, f"{time.time()-t0:.0f}s: {body[:90]}")
        body = wait_for(anna, room_id, lambda s, b: s == agent("betty") and b.startswith(("approved", "changes", "⚠️")), "review", ts)
        check("betty-agent approves Hermes's PR", body.startswith("approved"), f"{time.time()-t0:.0f}s")
        body = wait_for(anna, room_id, lambda s, b: s == me and b.startswith(("merged", "⚠️")), "merge", ts)
        check("Hermes merges after GitHub says ready", body.startswith("merged"), f"{time.time()-t0:.0f}s")
        main_file = subprocess.run(["git", "--git-dir", str(tmp / "remote.git"), "show", "main:docs/HERMES.md"],
                                   capture_output=True, text=True).stdout
        check("docs/HERMES.md on main", main_file.strip() == "# Hermes", repr(main_file))

        # C. betty-agent builds #211, Hermes reviews through the MCP server
        body = wait_for(anna, room_id, lambda s, b: s == me and b.startswith(("approved", "changes", "⚠️")), "Hermes review", ts)
        check("Hermes reviews betty-agent's PR via MCP", body.startswith("approved"), f"{time.time()-t0:.0f}s: {body[:90]}")
        body = wait_for(anna, room_id, lambda s, b: s == agent("betty") and b.startswith(("merged", "⚠️")), "merge 2", ts)
        check("betty-agent merges #211", body.startswith("merged"), f"{time.time()-t0:.0f}s")
        evs = api("GET", f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/messages?dir=b&limit=100", token=anna)["chunk"]
        leaks = [e["content"]["body"][:80] for e in evs if e["sender"] == me and e.get("origin_server_ts", 0) >= ts
                 and any(w in e.get("content", {}).get("body", "") for w in (str(Path.home()), "mcp_hagency", '{"'))]
        check("Hermes posts no local paths, tool calls or JSON in the room", not leaks, "; ".join(leaks))
        print(f"ALL PASS ({time.time()-t0:.0f}s loop)")
    finally:
        say(anna, room_id, "!level off")
        time.sleep(3)
        for p in procs:
            p.terminate()
        print(f"process log: {tmp / 'procs.log'}", file=sys.stderr)


if __name__ == "__main__":
    main()
