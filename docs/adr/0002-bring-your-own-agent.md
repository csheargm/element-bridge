# ADR 0002: Bring your own agent; the room is the product

Date: 2026-11-19 · Status: proposed (spike: `test_hermes_e2e.py`)

## Context
`bridge.py` and `agents.py` are a small agent harness. It runs Codex or Claude for each message,
with its own sandbox lockdown and session handling, and it was about to grow its own memory and
skills (ADR 0001). Agents like Hermes already do all of that: self-written skills, memory, MCP,
tool approvals, container isolation and a native Matrix gateway. Rebuilding a harness here would
duplicate them.

## Decision
- **People bring their own agent.** Hermes first, then any agent that speaks Matrix or MCP. It
  keeps its own memory, skills and tools under its owner's control, in a profile used only for
  rooms, holding no personal data.
- **The room is the product: Hagency Rooms.**
  - Roles come from Matrix power levels: the creator is owner, maintainers have 50, contributors 0.
  - Room intelligence: a shared project brief built in the conversation.
  - Project creation: the brief becomes a repo and issues.
  - The GitHub loop is opt-in per agent.
- **Agents plug in through `hagency_mcp.py`.**
  - It reads only @pm's tasks addressed to that agent.
  - It does every git and GitHub step under the agent's own GitHub identity, so the agent holds
    no token and needs no shell.
  - It posts the result event the loop already expects. @pm and the rules stay unchanged:
    a different person's agent reviews, and GitHub's ruleset is the merge gate.
- **`bridge.py` stays as a fallback** for people without an agent of their own. It is not the
  product.
- **What the room adds.**
  - Matrix federation lets organizations share a project room while each runs its own server.
  - Bridges reach people on WeChat, WhatsApp and Slack.
  - The room-intelligence layer, from requirements to issues to merged code, is reached over MCP,
    so it can run on other chat platforms later.

## Security
- The agent's owner decides what it can do.
- The recommended room profile obeys only its owner and @pm (`MATRIX_ALLOWED_USERS`). There is
  no room allowlist, so the owner can also DM their agent.
- (Superseded by ADR 0006: it now has its owner's own toolsets and approval settings.) It had no file, terminal, web or browser toolsets. It edits repos only
  through `hagency_mcp.py`'s `read_file` and `write_files`, which are confined to the task's
  checkout.
- The room never holds anyone's tools or secrets.

## Consequences
- Room skills from ADR 0001 are superseded: Hermes has its own skills, and the brief replaces the
  room's shared knowledge. setup-bot stays and grows roles and the brief.
- Hermes's Matrix gateway is Linux-first, because the encryption library has no macOS build. It
  runs on a Mac with E2EE off, or in its documented Linux proxy container.
- The MCP server covers build, review and merge. Verify and close for already-done issues are
  still to do.
