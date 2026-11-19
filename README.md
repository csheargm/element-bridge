# element-bridge — chat + bring your own agent (Matrix + Element prototype)

One-day prototype: a stock Matrix homeserver (Synapse) and Element as the chat app, plus a
~270-line Python bridge that lets each person's **own** agent CLI (Codex or Claude Code, using
their local login) join rooms as a Matrix user (`@anna-agent`, `@betty-agent`).

| What | Where |
|---|---|
| Synapse homeserver | http://localhost:8648 (server name `bridge.localhost`, Docker, 127.0.0.1 only) |
| Element Web | http://localhost:8649 |
| Accounts | `anna`, `betty`, `anna-agent`, `betty-agent` — passwords generated into `.env` (git-ignored) |
| Room | `#lounge:bridge.localhost` (anna, betty, both agents; unencrypted) |

## Run it (the owner)

1. Start everything — server, accounts (first run only) and both agent bridges:

   ```bash
   cd ~/element-bridge && ./start.sh
   ```

   Leave it running (Ctrl-C stops the bridges; the server keeps running in Docker).
   To use Claude Code instead of Codex: `./start.sh claude` (run `claude` → `/login` first).

2. Look up anna's password (it is never printed by the scripts):

   ```bash
   grep PW_ANNA ~/element-bridge/.env
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

   **By default an agent only obeys its owner** (betty mentioning `@anna-agent` is ignored),
   because the CLI runs with the owner's read access to their disk (Codex read-only can still
   read `~/.ssh`, this `.env`, …). To let every joined room member use the agents:
   `ALLOW_MEMBERS=1 ./start.sh`.

**Invite the agent somewhere else:** create a room, *turn encryption off* in the create dialog,
and invite `@anna-agent:bridge.localhost` — it joins automatically. A DM with the agent works
without a mention. In an encrypted room the agent says it can't read it (no E2EE in v1).

## How it works

- `bridge.py <owner> [--agent codex|claude]` logs in as `<owner>-agent`, auto-joins invites from
  local users, and reacts only to messages that **@mention it** (pill or plain `anna-agent`) or
  arrive in a 2-person DM, sent by its **owner** (or, with `--allow-members`, any joined room
  member) that is not itself an agent (loop guard). Pending invites at startup get the same
  local-server check as live ones; at most 3 CLI turns run at once per bridge. Earlier un-addressed room chatter (last 20 lines) is passed as context.
- `agents.py` runs the CLI with the prompt on **stdin**, no shell, in `rooms/<owner>/<room>/`,
  and resumes that room's saved session (`codex exec resume <id>` / `claude --resume <id>`).
  Codex runs `sandbox_mode=read-only`, `approval_policy=never`; Claude runs `-p` in `default`
  permission mode with `--tools Read,Grep,Glob --strict-mcp-config` (no shell/edit tools, no MCP,
  even if the user's settings pre-approve them). Chat can't widen either. Saved session ids are
  validated before they reach the CLI's argv.
- Typing indicator is refreshed every 20 s while the CLI runs; the reply is posted as a Matrix
  reply with markdown rendered to HTML.

## Tests

```bash
.venv/bin/python -m unittest test_rules test_agents
```

```bash
.venv/bin/python test_e2e.py
```

`test_rules`/`test_agents` cover who gets answered (owner-only, mentions, DMs, invitees,
non-members, other agents, malformed mentions) and CLI argv/session-state safety. `test_e2e`
drives the real flow over the Matrix API with the bridges running: anna and betty @mention their
agents, checks replies, typing, per-room memory across turns, that the other agent stays quiet,
that anna's agent ignores betty, and the encrypted-room warning.

## Stop / reset

```bash
docker compose -f ~/element-bridge/docker-compose.yml down
```

Delete `data/`, `.env` and `rooms/` to start from scratch.
