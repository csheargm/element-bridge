# Notes and review log: Matrix + Element + thin bridge

Measured 2026-11-19 on the owner's Mac. Details in [TIMELOG.md](TIMELOG.md).

## What works

- anna and betty in `#lounge` with `@anna-agent` / `@betty-agent`; each agent runs its owner's
  local Codex login (Claude Code is one flag: `--agent claude`, blocked only by the expired login).
- Trigger: Element mention pill, plain-text name, or DM. Only the owner drives their agent by
  default (`--allow-members` opens it to joined members); non-members, invitees, other agents
  (loop guard) and own messages are refused. Per-room working folder + resumed CLI session (survives restarts).
- Typing indicator while working; reply threaded to the question with rendered markdown.
- Safety: prompt on stdin, no shell; Codex `read-only` sandbox + `approval_policy=never`; Claude
  `-p` default permission mode (approval-needing tools denied). Chat can't widen these.
- Verified by `test_e2e.py` (real Matrix API, 7 checks), 17 unit tests, and in the Element Web UI.

## GPT review (R15) — what changed

Codex reviewed the first commit. Found no shell/flag injection from chat (prompt on stdin).
Adopted (each verified in code, with tests): owner-only default (CLI can read the owner's disk);
Claude limited to `Read,Grep,Glob` + no MCP; nio's `room.users`/`member_count` include
*invitees* → DM and membership checks now use joined counts and exclude invitees; pending invites
at startup now pass the same check; malformed `m.mentions` no longer crash the callback;
workdir names get a hash (sanitising was lossy); saved session ids are validated before reaching
argv (my first regex let `--x` through — the new test caught it); atomic state writes; reap the
CLI after a timeout kill; global cap of 3 concurrent CLI turns; `.env` created 0600 and saved
before each registration.
Not adopted: running the CLIs under a separate OS user/container (right for production, out of
scope for a one-day prototype — owner-only is the stop-gap); sanitising markdown HTML (Element
sanitises `formatted_body` per the Matrix spec); generic error messages (CLI errors in chat are
what make the fix cycle fast here; they show to the owner's room only).

## Step 2: the autonomous GitHub loop

Built in about 11 minutes of wall-clock, from start to a passing end-to-end loop. The new code is
about 400 lines: `loop.py` 203, `tasks.py` 112, `github.py` 81, and 35 lines in the bridge. Tests
are another 350 lines: a fake `gh`, the loop e2e and the loop unit tests.

**Flow:** loop-bot picks an open `agent-ok` issue and assigns a builder agent round-robin. That
agent claims the issue, edits a clone, and its bridge commits, pushes and opens the PR as that
agent's GitHub identity. loop-bot then asks a *different* person's agent to review against the
issue's acceptance criteria; the review is read-only and needs an explicit `VERDICT: APPROVE`.
When GitHub reports the PR merge-ready:
- Assist: loop-bot posts "ready — a person merges".
- Autonomous: the builder's bridge re-checks with GitHub and merges.

The room level is one Matrix state event, changed with `!level`. Only room moderators can change
it, and in room v12 that includes creators. Verified end to end with real Codex agents and a fake
GitHub: Autonomous goes from issue to merged in 20 s; Assist stops at ready and doesn't merge.
**Not yet run against the real sandbox repo**, because betty-agent's identity and the ruleset are
waiting on the owner.
