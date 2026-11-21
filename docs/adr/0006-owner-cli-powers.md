# ADR 0006: Your agent is your CLI, with full power, and only you can trigger it

Date: 2026-11-20 · Status: accepted (the owner's decision)

## Context
The bridge ran each person's agent CLI under its own lockdown on top of the CLI's settings.
Codex got `sandbox_mode="read-only"`, its features and MCP servers switched off, and web search off
unless @pm had recorded a per-room grant. Claude Code got `--tools Read,Grep,Glob`,
`--permission-mode default`, `--strict-mcp-config` and `--setting-sources user`. Hermes room
profiles had no file, terminal or web toolsets. The agent also worked inside the app's own checkout
(`ROOT/rooms/<owner>/<room>`), where it could read the app's files and git log.

Owners asked their agent for live news and got "I can't fetch live news… no web access". Removing
the lockdown alone wasn't enough. In a live test, `claude -p` with only the owner's settings still
denied WebSearch and WebFetch, because a non-interactive CLI denies every tool that would need an
approval, and nobody can approve from a chat room. The owner's decision: the agent has full power.

## Decision
**A turn its owner asked for runs the owner's CLI with their settings, plus what a chat room needs
to work without interactive approvals** (`owner_cli.py`):
- Claude Code: `claude -p --output-format json --permission-mode acceptEdits
  --allowedTools WebSearch,WebFetch,Edit,Write,Bash [--resume <id>]`. We pass no `--tools`, no
  `--strict-mcp-config` and no `--setting-sources`, so all the owner's settings, MCP servers and
  rules load. If the owner's `defaultMode` is already `bypassPermissions`, we pass no
  `--permission-mode` and theirs stays.
  - **Deny rules win:** Claude Code applies deny rules before allow rules, so anything the owner
    denied in their settings stays denied.
  - **Bash runs in the work folder.** Claude Code can't scope the Bash tool to a folder, so it is
    allowed, and its working directory is the room's work folder.
  - **Refusals are named.** Anything still refused is listed in the reply from
    `permission_denials`, by tool name only, with the setting that blocked it. A tool in our
    allowed set can only be blocked by one of the owner's deny rules. Any other tool, such as an
    MCP tool, needs an allow rule.
- Codex: `codex exec --json --skip-git-repo-check -c approval_policy="never"`, plus three settings
  only where the owner's `config.toml` doesn't set them: `sandbox_mode="workspace-write"`,
  `sandbox_workspace_write.network_access=true` and `web_search="live"`. `exec` can't ask for an
  approval, so `never` is the only setting that doesn't block. An explicit `sandbox_mode` of the
  owner's is kept, whether read-only or `danger-full-access`. So are `web_search="disabled"` and
  `network_access=false`. Their features and MCP servers stay on.
- Hermes gives one toolset to every turn, whoever sent it, and @pm's loop tasks carry other
  people's issue text. A Hermes person must still be in the GitHub loop *and* have a full-power
  personal agent (owner decision). So their agent runs as **two profiles side by side, logged in
  as the same Matrix account** with different device IDs, so the room sees one agent:
  - **Personal, `<person>room`:** `MATRIX_ALLOWED_USERS` is the owner only. It takes the owner's
    default-profile `toolsets`, `platform_toolsets.matrix` (Hermes's own `hermes-matrix` if they
    set none), `approvals` and `terminal` settings. Its terminal works in
    `~/.hagency/work/<owner>/hermes`, and its DM home room is the owner's.
  - **Loop, `<person>loop`:** `MATRIX_ALLOWED_USERS` is @pm only. It keeps the old contained
    tools (`todo, memory, skills` plus the hagency MCP build/review/plan tools; no
    file/terminal/web).
  - **No double replies:** Hermes acts only on senders in its allowlist. Others are logged as
    unauthorized and ignored, in groups always and in DMs with `unauthorized_dm_behavior: ignore`
    (no pairing codes). So an owner message is handled only by the personal profile, a @pm task
    only by the loop one, and anyone else's by neither.
  - **Invites:** with `docs/hermes-matrix-invite-allowlist.patch`, Hermes accepts invites only
    from its allowlist. The personal profile joins the owner's invites and the loop profile
    @pm's follow-me invites. Membership belongs to the account, so both then see the room.
  - **Starting them:** `onboard.py <name> --agent hermes` sets up both (`--loop` is kept as a
    no-op). `supervise.py` starts both as `<name>-agent` and `<name>-loop`. It skips a profile that
    isn't set up yet and says to re-run onboard.
  - **Remote people:** `connect.py` doesn't run Hermes on a person's own computer yet (Codex and
    Claude Code only). Hermes stays server-side until it does.

**What the bridge keeps:**
1. **Owner only.** Only the owner's messages trigger a turn, and others' messages are context.
   With `--allow-members`, a member's question runs **locked down** (below) in its own session,
   never in the owner's conversation.
2. **No secrets in chat.** Errors are scrubbed (`secret_safe`) and then cut short (`room_safe`).
   Replies go through `secret_safe`, which removes credential-shaped text and the bridge's own
   token.
3. **Chat stays text.** The prompt goes on stdin. Nothing runs through a shell, no chat text is
   passed as an argument, and session ids are validated.
4. **A work folder outside the app:** `~/.hagency/work/<owner>/<room>` (`workdirs.py`, which
   honours `HAGENCY_DIR`). On first use, an old `ROOT/rooms/<owner>/<room>` folder moves there
   with its saved session ids.
   - The move is a rename. Only a cross-disk error falls back to copying, via a unique staging
     folder. Symlinks are never adopted.
   - Codex sessions resume by id.
   - Claude Code keys its sessions by folder, so the transcript and its session folder are copied
     to the new folder's entry. If no transcript is found, the Claude id is dropped and the room
     starts a fresh conversation.

**Answers without an @mention** (`bridge_followup.py`). A question is open when the agent's latest
reply to its owner in a room has a sentence ending in "?", ignoring code and quotes. It stays open
for 30 minutes and for at most 5 messages.
- While it is open, each message from the owner there that doesn't @mention or reply to someone
  else is offered to the agent, with the room conversation, as "this may answer your question".
- The agent decides. If the message isn't for it, it replies exactly `NO_REPLY` and the bridge
  posts nothing.
- A real reply closes the question, or renews it if the reply asks again.
- We chose the "?" signal over a marker the agent must add. The live failure was an agent asking
  naturally ("Tell me a city or zip code"), and no prompt change is needed. A false positive costs
  at most 5 extra turns, and each one can answer `NO_REPLY`.

**Locked-down turns stay as they were** (`agents.run_turn(owner=False)`, the default):
- **Which turns:** loop builds, fixes, reviews and plans (`tasks.py`), @pm's brain (`pm_brain.py`),
  code changes (`bridge_custom.py`) and members' questions. They run on untrusted text (issue
  bodies, diffs, other people's chat), and nobody asked for them in the owner's own words.
- **Settings:** read-only, no approvals, no connectors, MCP or web.
- **Builds:** they keep their scoped write mode in their own clone, with `.git` moved out of
  reach: Codex `workspace-write` with network off and no extra writable roots, even if the
  owner's config widens them; Claude `Edit,Write` under `acceptEdits`.
- **Output:** their output is never changed. No refusal note is added, because `VERDICT:` and
  plan JSON must stay intact.
- **Task folders** stay under `ROOT/rooms/<owner>/work/`, shared with `hagency_mcp.py`.

**Per-room web grants are retired.** The owner's turns always have web, and locked turns never do,
so a per-room switch has nothing left to switch. The grant record (`capabilities.py`), the panel's
web toggle and "uses:" chip, and @pm's grant code are gone. The hagency MCP `web_search` and
`fetch_page` tools (Hermes) follow the same rule: the personal profile may use them in the room its
owner is talking to it in (each request is still shown there), and the loop profile never may.
"@pm let my agent search the web here" (or "stop letting…") is still recognised, but only answered:
@pm explains that there is no switch and that the owner's own CLI settings are what limit it.
Grant records already in rooms are ignored.

## Consequences
- **What the owner's agent can do:** in a room, roughly what the owner's CLI can do at their
  terminal: web, edits and commands in its work folder, minus their deny rules.
- **Who controls it:** only the owner. The room can't steer it, because only the owner triggers it.
- **The remaining risk:** a prompt injection hidden in context the owner's question pulls in (an
  earlier chat line, a project file, a web page) runs with those powers. That is the same as
  pasting the text into their own terminal. Their deny rules and their CLI's sandbox are the
  defence, and we add none of our own.
- **Hermes:** two gateways per Hermes person (one more process each). The personal profile never
  sees @pm's tasks; the loop profile never sees the owner's words. Hermes's own replies are not scrubbed by
  the bridge, since Hermes posts directly.
- **Taking effect:** the bridge restarts on the code update (`supervise.py`). Hermes profiles are
  rewritten, and the loop profile created, by re-running `onboard.py <name> --agent hermes`. Until
  then the old single profile keeps running as before.
- `test_codex_lockdown_live.py` still checks the locked path, which loop tasks use.
