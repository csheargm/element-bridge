# Hagency Rooms — chat where each person brings their own agent (Matrix + Element)

One-day prototype: a stock Matrix homeserver (Synapse) and Element as the chat app, plus a
~270-line Python bridge that lets each person's **own** agent CLI (Codex or Claude Code, using
their local login) join rooms as a Matrix user (`@anna-agent`, `@betty-agent`).

## Get started (no setup)

1. Ask the owner for an invite token, then sign up at **https://rooms.hagencylabs.xyz/#/register**
   (or in any Element app, with homeserver `https://matrix.hagencylabs.xyz`). Your ID is
   `@you:matrix.hagencylabs.xyz`.
2. You land in **Welcome**, and @pm sends you a private message: *Get your agent*.
3. Click 🔌 **Bring your own** (🏠 House agent, run by the server, isn't built yet).
4. @pm gives you a one-time code and a link to the **Connect page**
   (`https://rooms.hagencylabs.xyz/connect/start#ABCDE-FGHJK`). Open it on the computer where Codex
   or Claude Code is installed: it sees whether that's a Mac, Windows or Linux, shows the one line
   to paste (with a Copy button, and tabs for the other systems), and tells you where to paste it.
   The code stays in your browser: it's after the `#`, which is never sent to the server.

   ```bash
   curl -fsSL https://rooms.hagencylabs.xyz/connect.sh | sh -s -- ABCDE-FGHJK                  # Mac, Linux
   ```
   ```powershell
   & ([scriptblock]::Create((irm -UseBasicParsing https://rooms.hagencylabs.xyz/connect.ps1))) -Code ABCDE-FGHJK  # Windows
   ```

   It installs Hagency Rooms in `~/.hagency/app` (on Windows `%LOCALAPPDATA%\Hagency\app`), keeps
   your agent's sign-in in `~/.hagency/credentials.json` (only you can read it) and starts your
   agent, `<you>-agent`, in the background (launchd on macOS, systemd on Linux, a Scheduled Task
   "Hagency agent <you>" on Windows): you can close the window, and it starts again when you log
   in. Accept its chat invite and talk to it; it works on your computer and obeys only you. If it
   goes offline, @pm tells you and sends the line again (running it again is safe). To stop it:
   `~/.hagency/app/.venv/bin/python ~/.hagency/app/connect.py --stop`, or Disconnect under My agent
   in the Hagency panel ("@pm disconnect my agent" works too). On Windows the same `--stop` works
   with `%LOCALAPPDATA%\Hagency\app\.venv\Scripts\python.exe %LOCALAPPDATA%\Hagency\app\connect.py --stop`
   (it ends and removes the Scheduled Task).
   Windows needs Claude Code from its installer (`claude.exe`) or a `codex.exe` on the PATH: the
   agent starts the CLI without a shell, so npm's `.cmd` wrappers aren't enough yet.
5. **More agents** (up to 3 each, e.g. Codex as well as Claude Code): click ➕ under @pm's Connect
   message (or type "add another agent called codex" in @pm's chat, or **Add an agent** under My
   agents in the panel). @pm asks for a one-word name if you gave none, then sends that agent's own
   Connect page and code. It runs beside the first one (`<you>-codex-agent`, its files in
   `~/.hagency/<its name>/`; stop it with `connect.py --name <its name> --stop`). The panel's My
   agents shows each one Online or Offline with its own Disconnect, "comes along" setting (only the
   first comes along by default) and web research. @pm never lets two of your agents review each
   other: a review always needs a different person's agent.

**Already on matrix.org (or another Matrix server)?** No sign-up needed: start a private chat with
`@pm:matrix.hagencylabs.xyz` in your own Element. @pm answers in your chat and sends you its own
private chat with the buttons: accept it under Invites, then steps 3 and 4 as above. Your agent
gets an account on this server (e.g. `@alice-at-matrix-org-…-agent:matrix.hagencylabs.xyz`) and
obeys only your full ID. For settings, say "@pm send me my console link" in a room you share with @pm.

The site is https://www.hagencylabs.xyz. How the public server is run and deployed:
[docs/DEPLOY_ORACLE.md](docs/DEPLOY_ORACLE.md). On a local server (below), `python local_signup.py setup`
turns on sign-up by token (and the Welcome room), and `python local_signup.py token` makes a token
for http://localhost:8649/#/register.

## Developers

| What | Where |
|---|---|
| Synapse homeserver | http://localhost:8648 (server name `bridge.localhost`, Docker, 127.0.0.1 only) |
| Element Web | http://localhost:8649 |
| Accounts | `anna`, `betty`, `anna-agent`, `betty-agent` — passwords generated into `.env` (git-ignored) |
| Room | `#lounge:bridge.localhost` (anna, betty, both agents; unencrypted) |

## Run it locally

1. Start everything — server, accounts (first run only) and both agent bridges:

   ```bash
   cd hagency-rooms && ./start.sh      # in your clone
   ```

   Leave it running (Ctrl-C stops the bridges; the server keeps running in Docker).
   To use Claude Code instead of Codex: `./start.sh claude` (run `claude` → `/login` first).

2. Look up anna's password (it is never printed by the scripts):

   ```bash
   grep PW_ANNA .env      # in your clone
   ```

3. Open **http://localhost:8649** in your browser (or Element desktop: on the sign-in screen
   click **Edit** next to the homeserver and enter `http://localhost:8648`).
   Sign in with username `anna` and that password. Dismiss the "back up your chats" prompt.

4. Open the **Lounge** room. Type `@anna-ag`, wait a second, press **Tab** to turn it into a
   mention pill, then type your question and press Enter, e.g.
   `@anna-agent what files are in your working folder?`
   You'll see "anna-agent is typing…" and then the reply (Codex, ~5–40 s).

5. Optional: sign in as `betty` in a private window (`grep PW_BETTY .env`) and mention
   `@betty-agent` — each agent runs its owner's CLI, keeps its own per-room session, and stays
   quiet when it isn't mentioned.

   **An agent only obeys its owner** (betty mentioning `@anna-agent` is ignored), because for
   its owner it runs their CLI exactly as they configured it, with their tools and access
   (docs/adr/0006-owner-cli-powers.md). With `ALLOW_MEMBERS=1 ./start.sh` other joined members
   can ask it too, but their questions run locked down (read-only, no web, no MCP).

**Invite the agent somewhere else:** create a room, *turn encryption off* in the create dialog,
and invite `@anna-agent:bridge.localhost` — it joins automatically. A DM with the agent works
without a mention. In an encrypted room the agent says it can't read it (no E2EE in v1).

## How it works

- `bridge.py <owner> [--agent codex|claude]` logs in as `<owner>-agent`, auto-joins invites from
  local users, and reacts only to messages that **@mention it** (pill or plain `anna-agent`) or
  arrive in a 2-person DM, sent by its **owner** (or, with `--allow-members`, any joined room
  member) that is not itself an agent (loop guard). Pending invites at startup get the same
  local-server check as live ones; at most 3 CLI turns run at once per bridge. Earlier un-addressed room chatter (last 20 lines) is passed as context.
- `agents.py` runs the CLI with the prompt on **stdin**, no shell, in
  `~/.hagency/work/<owner>/<room>/` (outside the app; `workdirs.py` moves an old
  `rooms/<owner>/<room>/` folder there with its sessions), and resumes that room's saved session
  (`codex exec resume <id>` / `claude --resume <id>`).
  - **The owner's own message:** the CLI runs as the owner set it up on that machine, with no
    extra flags from us: Codex uses `~/.codex/config.toml` (sandbox, approvals, web search, MCP
    servers); Claude Code runs `claude -p` with all its settings sources and the owner's
    permission rules. Anything their rules leave to an interactive approval is denied (nobody can
    approve from a chat room) and the reply names it, e.g. "didn't allow: WebFetch". To allow
    web for Claude Code, add `"permissions": {"allow": ["WebSearch", "WebFetch"]}` to
    `~/.claude/settings.json`; for more than read-only Codex, set `sandbox_mode` in its config.
  - **Everything else** (a member's question under `--allow-members`, loop builds/reviews/plans,
    @pm's brain, code changes): Codex `sandbox_mode=read-only` (`workspace-write` for builds),
    `approval_policy=never`, connectors/plugins/MCP/web off; Claude `--tools Read,Grep,Glob`
    (`+Edit,Write` for builds) `--strict-mcp-config --setting-sources user`.
  - Replies are scrubbed of anything shaped like a credential (`redact.secret_safe`). Saved
    session ids are validated before they reach the CLI's argv.
- Typing indicator is refreshed every 20 s while the CLI runs; the reply is posted as a Matrix
  reply with markdown rendered to HTML.

## Live code changes

`start.sh` runs the bridges and @pm under `supervise.py`. Save any `.py` file and they
restart by themselves within a second; you never restart the server. A file with a syntax error
is not loaded; the running version stays up and the error is printed. On restart, each process
finishes the answer or loop step it is in the middle of, and the new one picks up messages sent
in between, so nothing is lost or answered twice (`lifecycle.py`). A process that crashes is
restarted with a growing delay.

The loop never waits forever either. A step that gets no answer is sent again once, then the
loop stops on that issue. An approved PR with merge conflicts, or one GitHub keeps blocking for
15 minutes, also stops the issue. A stopped issue always posts "a person takes over" and the loop
moves on to the next one.

## Tests

```bash
.venv/bin/python -m unittest test_rules test_agents test_loop_rules test_loop_watchdog test_lifecycle test_github_setup test_pm_chat test_hagency_mcp test_status_board
```

```bash
.venv/bin/python test_e2e.py
```

`test_rules`/`test_agents` cover who gets answered (owner-only, mentions, DMs, invitees,
non-members, other agents, malformed mentions) and CLI argv/session-state safety. `test_e2e`
drives the real flow over the Matrix API with the bridges running: anna and betty @mention their
agents, checks replies, typing, per-room memory across turns, that the other agent stays quiet,
that anna's agent ignores betty, and the encrypted-room warning.

`test_pm_e2e.py` (stop `start.sh` first) talks to @pm in plain words: anyone can stop, a
contributor can't switch to autonomous, and the owner can, just by saying so.

`test_restart_e2e.py` (stop `start.sh` first) saves a code file between two questions and checks
both are answered exactly once, then kills the builder's bridge mid-step and checks the loop
resends the step and the issue is still merged.

## Talking to @pm

Each room has one bot, `@pm` ("Product Manager"). People talk to it in plain words; the commands
`!help`, `!status` and `!level` still work as shortcuts.

| Say | What happens | Who |
|---|---|---|
| "@pm take it all the way" | agents build, review and merge on their own, at once | owner or maintainer |
| "@pm let us approve each change" | each change waits for a person to merge | owner or maintainer |
| "@pm stop" / "turn it all off" | no new work and no merges, at once | anyone |
| "@pm where are we?" | mode, agents and a link to the pinned Project status | anyone |

`@pm` understands from context. Every message addressed to it goes to an understanding step
(`pm_brain.py`): the recent conversation, the message being replied to, what's waiting for
approval, the plan and the board. The model picks one action from a fixed list and writes a
short reply.
- Code still checks who may do what, and code confirms every action, so the model can't claim
  something happened.
- "stop" is matched by plain rules (`intents.py`) and never waits for a model.
- If the model fails or is slow, @pm uses the rules.
- The model is the host's agent CLI (`PM_BRAIN=codex|claude|off`), run in the agents'
  lockdown.

## Stop / reset

```bash
docker compose down      # in your clone
```

Delete `data/`, `.env` and `rooms/` to start from scratch.

## Step 2: the autonomous GitHub loop

`loop.py` runs a coordinator bot, `@pm`, in the room. It watches open issues labelled
`agent-ok` on **the room's own repo** and drives each issue through the room, one step per line.

**One room, one repo.** The room owner or a maintainer picks it: say `@pm this room works on
owner/name`, or use **Project repo** in the Hagency console. The console's **Check readiness**
button runs the same read-only check as `github_setup.py check`: each agent can write, merges
need an approving review, and the `agent-ok` label exists. The repo is stored as a list in the
room's `xyz.hagencylabs.loop` state, so a room can hold several repos later. A project that spans
repos is a **Matrix Space** with one room per repo. Create the Space in Element (＋ → Create a
space) and add the rooms to it. Each room keeps its own board, mode and agents.

**Which repos.** Rooms choose from the repos this server allows: `LOOP_REPO` plus any in
`HAGENCY_REPOS="owner/a owner/b"`. Set both in `local.env` (copy `local.env.example`; git-ignored),
which `start.sh` loads; the code's own default is only a placeholder. Anyone here can
create a room, and @pm reads with the host's own `gh` login. A free choice would let any room
see any repo that login can read, private ones included. The demo room starts on `LOOP_REPO`.

A step in the loop looks like this:

```
@pm     @betty-agent: build #136 docs: add docs/MAINTAINERS_NOTE.md (autonomous)
betty-agent  build: claimed #136
betty-agent  PR #140 opened for #136: https://github.com/…/pull/140
@pm     review requested from anna-agent
anna-agent   approved PR #140            (or: changes requested on PR #140 → loop stops)
@pm     PR #140 ready — a person merges          (Assist)
betty-agent  merged PR #140 (closes #136)               (Autonomous)
```

**Room level** is one Matrix state event (`xyz.hagencylabs.loop`) that a room moderator sets by
talking to `@pm` (see "Talking to @pm"); `!level off|assist|autonomous` is the shortcut.
It starts at `off`.
- Off: the loop does nothing.
- Assist: agents build and review, then a person merges.
- Autonomous: the builder agent merges.

**Who does what.** The builder is picked round-robin (issue number mod number of agents) and the
reviewer is always a *different* person's agent. Each step runs in that person's bridge with
**that agent's own GitHub identity**. @pm holds no GitHub credentials; it only reads.
- Build: the agent CLI edits a fresh clone with `.git` moved out of its sandbox. Codex runs with
  `workspace-write` and no network. The bridge then commits, pushes and opens the PR.
- Review: the agent checks the diff read-only against the issue's acceptance criteria. It must end
  with `VERDICT: APPROVE`; anything else becomes "request changes".
- Fix: on "changes requested", the builder's agent gets the reviewer's reasons and revises its
  own PR branch (a new commit, so earlier approvals don't count), and the same reviewer looks
  again. After 2 rounds without approval, a person takes over.
- Merge: the builder merges only when **GitHub itself** reports the PR `MERGEABLE` + `CLEAN` +
  `APPROVED`, re-checked in its bridge just before merging.

The merge gate is GitHub's own. Without a required-review rule GitHub never reports `APPROVED`,
so the loop stops and says so instead of merging.

### One-time GitHub setup (anyone running the loop)

Follow [docs/GITHUB_SETUP.md](docs/GITHUB_SETUP.md). In short:
1. Sign in one GitHub account per agent in `.gh/<agent>/`.
2. Give each account Write access to the repo.
3. Run `github_setup.py ruleset` to make GitHub the merge gate.
4. Run `github_setup.py check` and confirm you see `✓ ready`.

An agent without a signed-in identity refuses loop tasks with "no GitHub identity", and the
loop stops on that issue.

### Loop tests

```bash
.venv/bin/python -m unittest test_loop_rules
```

The full loop test below runs real Codex agents against a fake GitHub (`tests/fake_gh.py` plus a
local bare repo, with the same rules: you can't approve your own PR, and reviews are required).
Stop `start.sh`'s bridges first, because the test starts its own:

```bash
.venv/bin/python test_loop_e2e.py
```

## License and contributing

MIT, see [LICENSE](LICENSE). Pull requests welcome: [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).
