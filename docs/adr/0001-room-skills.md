# ADR 0001: Room skills are reviewed text owned by the room

Date: 2026-11-19 · Status: accepted

## Context
Agents in a Hagency room should get better over time by building capabilities. The agent CLIs
run on a person's Mac, where personal skills, connectors (Gmail, Drive, calendars), MCP servers
and computer use exist. Agents read untrusted text (other members' chat lines, issue bodies), so
anything reachable from a turn is reachable by whoever writes that text. Commit 820fb83 switched
all of that off for Codex. Claude was already limited to Read/Grep/Glob with no MCP.

## Decision
- A room capability is a **skill: instruction text**, `skills/<name>.md` on the loop repo's
  default branch. It is not code and not a tool.
- It reaches the default branch only through the normal gate: a PR, another person's agent's
  review, and GitHub's ruleset.
- A room moderator enables it per room with `!skill enable <name>` (setup-bot). This pins the
  file's blob sha in the room state event `xyz.hagencylabs.skills`. The homeserver only lets
  power ≥ 50 write state.
- A bridge uses a skill only while the default branch still has that exact sha. Any edit means
  re-approval. There are at most 10 skills, each at most 8 KB, and they are prefixed with a note
  that they grant no tools.
- Room settings, including the loop level, move to setup-bot. loop-bot no longer parses commands
  and no longer needs moderator power.

## Rejected
- **Skills as executable tools or MCP servers per room.** This needs a per-tool sandbox and
  network policy. Out of scope until there is a real need, and then it gets its own ADR.
- **Loading the owner's ~/.claude or ~/.codex skills in rooms.** This is the exposure we just
  closed.
- **Unpinned "whatever is on main".** A merged edit would change agent behaviour in every room
  without a moderator seeing it.

## Consequences
- Each turn with skills enabled makes one GitHub listing call (cached for 60 s) plus a blob fetch
  per new sha. Blobs are cached forever, because they are content-addressed.
- If GitHub is unreachable, turns run without skills rather than failing. A skill only adds
  instructions, so dropping it is safe.
- Personal tools in a private DM, with the owner's opt-in, remain a possible later feature and
  are not built.
