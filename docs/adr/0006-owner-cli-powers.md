# ADR 0006: Your agent is your CLI, as you configured it

Date: 2026-11-20 · Status: accepted (the owner's decision)

## Context
The bridge ran each person's agent CLI under its own lockdown on top of the CLI's settings.
Codex got `sandbox_mode="read-only"`, its features and MCP servers switched off, and web search off
unless @pm had recorded a per-room grant. Claude Code got `--tools Read,Grep,Glob`,
`--permission-mode default`, `--strict-mcp-config` and `--setting-sources user`. Hermes room
profiles had no file, terminal or web toolsets. The agent also worked inside the app's own checkout
(`ROOT/rooms/<owner>/<room>`), where it could read the app's files and git log.

Owners asked their agent for live news and got "I can't fetch live news… no web access". The
owner's decision: the agent should have full power, meaning the powers its owner already gave
their CLI.

## Decision
- **A turn its owner asked for runs the owner's CLI as they configured it** on their machine.
  - Codex: `codex exec --json --skip-git-repo-check -C <folder> -` (or `exec resume <id> -`).
    Sandbox, approval policy, web search, features and MCP servers come from
    `~/.codex/config.toml`.
  - Claude Code: `claude -p --output-format json [--resume <id>]`, with every settings source and
    the owner's own `defaultMode` and allow/deny rules. Nothing is bypassed. A tool their rules
    leave to an interactive approval is denied, because nobody can approve from a chat room.
    The reply then says which tools (from `permission_denials`, names only).
  - Hermes: the room profile takes the owner's default-profile `toolsets`, `platform_toolsets.matrix`
    (Hermes's own `hermes-matrix` if unset), `approvals` and `terminal` settings, with the terminal
    working in `~/.hagency/work/<owner>/hermes`. It still obeys only its owner and @pm
    (`MATRIX_ALLOWED_USERS`), keeps the invite allowlist, has no personal skills or memories, and
    still does loop repo work through `hagency_mcp.py`.
- **What the bridge keeps:**
  1. Owner only: only the owner's messages trigger a turn. Others' messages are context. With
     `--allow-members`, a member's question runs **locked down** (below), never with the owner's
     setup.
  2. No secrets in chat: errors go through `room_safe`. Replies now also go through `secret_safe`,
     which cuts credential-shaped text and the bridge's own token.
  3. The prompt goes on stdin. No shell, no chat text in argv, and session ids are validated.
  4. A work folder outside the app: `~/.hagency/work/<owner>/<room>` (`workdirs.py`, honours
     `HAGENCY_DIR`). On first use an old `ROOT/rooms/<owner>/<room>` folder moves there with its
     saved session ids. Codex sessions resume by id. Claude Code keys sessions by folder, so the
     transcript is copied to the new folder's entry, or the Claude id is dropped and the room starts
     a fresh conversation.
- **Locked-down turns stay as they were** (`agents.run_turn(owner=False)`, the default). These are
  loop builds, fixes, reviews and plans (`tasks.py`), @pm's brain (`pm_brain.py`), code changes
  (`bridge_custom.py`) and members' questions. They run on untrusted text (issue bodies, diffs,
  other people's chat) and nobody asked for them in the owner's own words. Builds keep their scoped
  write mode (Codex `workspace-write` without network, Claude `Edit,Write` under `acceptEdits`) in
  their own clone, with `.git` moved out of reach. Task folders stay under
  `ROOT/rooms/<owner>/work/` (shared with `hagency_mcp.py`).
- **Per-room web grants:** the bridge's own CLI no longer reads them. The owner's CLI does whatever
  its config allows, and locked turns never get web. The grant record, @pm's "let my agent search the
  web here" and the panel toggle remain only for the hagency MCP `web_search`/`fetch_page` tools.
  Retiring them touches `console.py` and `panel.py`, which another change has open, so that is a
  follow-up.

## Consequences
- The owner's agent can do in a room whatever their CLI can do at their terminal, and no more. The
  room can't steer it, because only the owner triggers it. A prompt injection hidden in context the
  owner's question pulls in (an earlier chat line, a project file) runs with the owner's powers. That
  is the same as pasting the text into their own terminal. The owner's CLI rules are the defence,
  and we no longer add one of our own.
- With default settings, Claude Code in `-p` denies WebSearch/WebFetch and other tools that need an
  approval. The reply says so, and the owner allows them in `~/.claude/settings.json`. Codex keeps
  whatever `sandbox_mode` its config has (read-only by default).
- To take effect, the bridge restarts (`supervise.py` reloads it on the code update) and Hermes
  profiles are rewritten by `onboard.py … --agent hermes`.
- `test_codex_lockdown_live.py` still checks the locked path, which loop tasks use.
