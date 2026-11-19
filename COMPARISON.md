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

**Flow:** @pm picks an open `agent-ok` issue and assigns a builder agent round-robin. That
agent claims the issue, edits a clone, and its bridge commits, pushes and opens the PR as that
agent's GitHub identity. @pm then asks a *different* person's agent to review against the
issue's acceptance criteria; the review is read-only and needs an explicit `VERDICT: APPROVE`.
When GitHub reports the PR merge-ready:
- Assist: @pm posts "ready — a person merges".
- Autonomous: the builder's bridge re-checks with GitHub and merges.

The room level is one Matrix state event, changed with `!level`. Only room moderators can change
it, and in room v12 that includes creators. Verified end to end with real Codex agents and a fake
GitHub: Autonomous goes from issue to merged in 20 s; Assist stops at ready and doesn't merge.
**Not yet run against the real sandbox repo**, because betty-agent's identity and the ruleset are
waiting on the owner.

### GPT review of the loop (R15)

GPT found 10 issues. Adopted, each verified in the code and covered by a test:
- **Reviews:** the verdict is only an exact final line. A diff that's too large to review in full
  is never approved.
- **Commit pinning:** the review is pinned to the head commit (`commit_id`), and the merge uses
  `--match-head-commit` with that same commit, so a push after approval can't slip in.
- **Builder's PR:** the loop checks that the PR the builder reports is really the builder's own
  `agent/<agent>/issue-<n>` branch from the same repo.
- **Results:** each result must echo its task ID and match the issue's current stage, its
  assigned agent and its PR. A redelivered task runs once. Malformed results can't break the
  sync loop.
- **Merge timing:** the bridge re-reads the room level just before merging. The loop marks an
  issue done only once GitHub reports `MERGED`.
- **Isolation:** Claude runs with `--setting-sources user`, so it never loads project `.claude/`
  hooks or settings from the cloned repo. Host-side git runs without user or system config,
  without hooks and with fsmonitor off, so an agent-written `.gitattributes` can't trigger a
  filter.
- **Identity:** `GH_TOKEN`, `GH_ENTERPRISE_TOKEN` and `GH_HOST` overrides are scrubbed for
  agent calls.

Not adopted, deferred to a real deployment:
- OS-level containment (a separate user or container) for the agent CLIs.
- Durable dispatch with reconciliation after a crash between "send" and "save". Today, after a
  crash a person may need to retry an issue.
- Restricting who may write the level state event to @pm only. Today any room moderator
  can, which is the same group allowed to use `!level`.

### GPT review 3 (live reload, hand-off, watchdog — 80a0a8f)

Adopted, with tests: cutoff set before resumed callbacks run (#1); one lifecycle owner per child in
the supervisor, no respawn during shutdown, SIGTERM stops children (#2, #6); a resend of a running
step is coalesced in the bridge and the original's result still counts (#3); loop transitions are
serialized by one lock and shielded from restart cancellation (#4, #8); CLI/gh children are killed
on cancellation, not only on timeout (#6); handled ids unbounded per batch (#9); the GitHub timeout
is judged after GitHub's latest state (#10). Partly: the hand-off is kept until resume succeeds and
outlives a full drain (#5); Matrix send errors are now raised and retried (#7).
Deferred: a rolling crash checkpoint and a durable outbox for dispatches (#5, #7), and reconciling
an existing branch/PR when a resent build finds one (#3) — today such a retry fails and the loop
stops for a person.

## GPT review 4 (820fb83..3d41fd0: setup-bot, Hermes MCP, status board, @pm)

Adopted, with tests:
- **1. A stray "yes" could confirm autonomous.** Confirmation now comes only from the person who
  asked (reaction or "yes"); any mode change cancels it; authority is re-checked at confirm time.
- **3. Historical tasks stayed usable over MCP.** Tools act only on the agent's current
  (newest, unanswered) task.
- **5. "Stop" didn't stop follow-ups.** A step decided while the room is off is held, and sent
  when the room resumes.
- **8. Diff/approval race.** The PR head is re-read after the diff; if it moved, the review is
  refused.
- **9. Local paths in room messages.** All error text goes through `redact.room_safe`.
- **10. Malformed input could kill @pm.** `m.mentions` shape is validated; GitHub errors in
  result handling and chat are caught.
- **12. Stale README line about setup-bot.** Fixed.

Deferred:
- **2. Maintainers can write the level state directly.** They can already do the same through @pm.
- **4. One person could act through two agent accounts.** Needs explicit agent enrollment, which
  onboarding (`agents.json`) is the start of.
- **6. A watchdog resend can replace an active checkout.** Only after the 20-minute step timeout.
- **7. Filesystem race on write_files paths.** Needs local FS access, which room agents no longer
  have.
- **11. A failed pin is cached as success.** Cosmetic.
