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

## GPT review 6 (17b654d: @pm's understanding step)

Adopted, with tests:
- **1. "stop" could be held up behind a slow model call.** The model now runs in the
  background, so the sync loop never waits. "stop" bumps a per-room generation that cancels
  in-flight decisions. Homeserver calls have a 30 s timeout; typing is best effort.
- **2. Indirect injection through other people's messages in the context.** Mode changes and
  approvals the model proposes happen at once only when the person's own words say so (the rules
  agree, or an OK reply to the waiting question). Otherwise @pm asks for a ✅.
- **3. Model text in @pm's voice; Codex's read-only sandbox can still read files.** The
  understanding step now runs Claude Code with NO tools (verified: it can't read a file), no MCP,
  no project settings and a minimal environment. Model replies are posted as plain text with
  links removed. Action confirmations are always code-written.
- **4. Failures that skipped the fallback.** A missing CLI, an unknown PM_BRAIN, non-string
  actions and history errors all fall back to the rules.

Deferred:
- **5. Killing the CLI's descendants on timeout.** lifecycle.communicate kills the direct child.
- **6. A merge already in flight when "stop" lands may complete** (documented).

## GPT review 7 (ownership, follow-me, agent context, side panel)

Adopted, with tests:
- **Any local account could invite @pm and read the repo's issues.** Rooms must be bound to the
  repo (a moderator asked @pm for a plan or a mode) before @pm shows a board, queue or panel there.
- **project_status crossed rooms.** It answers only for the room where the agent's owner last spoke
  to it.
- **Panel and board XSS.** Issue titles and stop reasons are HTML-escaped (and brackets and stars
  neutralised) before markdown. The panel sends CSP frame-ancestors (Element only), no-store and
  nosniff, and answers only to a localhost Host.
- **Untrusted status.** Agents trust the published status only when @pm wrote it; the conversation
  shows full sender IDs.
- **Work after the owner left.** The bridge refuses chat turns and tasks without its owner in the
  room; MCP claim() checks the owner's membership before every step.
- **Anyone could invite someone else's bridge agent.** It accepts invites only from its owner or
  @pm.
- **read_project_file path handling.** The raw path is validated, every segment is a plain name and
  URL-encoded, the root endpoint is explicit, and a test proves bad paths never reach gh api.
- **Follow settings via the model, remote look-alikes, a damaged registry.** A follow change needs
  the person's own words; only local people; malformed registry entries are tolerated.

Deferred (docs/ROADMAP.md):
- Panel viewer identity through Element's OpenID hand-off.
- Per-owner pending approvals instead of one slot per room.
- Membership reconciliation at startup.
- Rejecting duplicate agent_user entries.

## GPT review 8 (tools with owner approval)

Adopted, with tests:
- **1, 2. SSRF.** safe_fetch.py resolves each hop once, requires every address to be public
  unicast (IPv4-mapped normalised; multicast, reserved and unspecified refused), connects to that
  exact IP (Host/SNI/cert keep the name), uses no proxies, and has a 25 s overall deadline.
- **4. Planted grants republished.** One record per agent (state key `agent:<id>`; "@" keys are
  reserved by Matrix). @pm reads back only records it wrote itself.
- **5. Borrowed room.** owners_room() needs an explicit @mention from the owner in the last
  15 min, and refuses when two rooms qualify.
- **6. Ambiguous consent.** A grant must be a direct instruction (not a question or quote); the
  owner must match the registry exactly; a model revoke also needs the person's words.
- **7. Approval scope.** The full request is shown (max 1000 chars, or refused), a denial wins, the
  owner can't be the agent, and approved results carry the request text.
- **8. Blocking.** The web tools and ask_owner are async (to_thread / asyncio.sleep).
- Also fixed live: "@" state keys were refused by Synapse, and background errors are now logged
  with tracebacks.

Partly adopted:
- **3. Exfiltration through web requests.** Every web request is posted in the room (visible
  audit), the grant message warns, and web is never on by default. Full fix (a research worker
  with no project or room context) is on the roadmap.

## GPT review 9 (the console)

Adopted, with tests:
- **Widget identity swap.** The page talks only to Element's exact origin (injected by the
  server) and its parent frame, only when embedded. It accepts only the reply to its own OpenID
  request, once, and never echoes credentials.
- **"off" bound an unbound room.** Stopping never binds; binding happens only through
  write_level after a moderator asked. Agents and bots can't use the console (is_person).
- **/data without a session.** Removed; the board comes from the signed-in, membership-checked
  state route.
- **The DM carrying the link.** It is reused only while @pm and the person are its sole members,
  and is created as private_chat (no elevated rights). A link's session is scoped to its room.
- **Login code in the URL query.** It is now after "#", removed before any request, and the page
  sends no-referrer and no-store.
- **Approval detached from what was seen.** The button carries the waiting event's ID, and the
  server checks it.
- **Origin boundary.** Every route checks the host; POSTs need JSON from our origin; "Bearer"
  required.
- **OpenID verification.** No redirects, an exact @name:server match, fail closed on odd
  responses.

Deferred:
- Remove inline script and unsafe-inline (board HTML is already escaped server-side).
- Encrypted DM transport for the link (@pm's Matrix client has no E2EE yet).

## GPT review 10 (one room, one repo — 9e45126)

GPT would have blocked the commit. Adopted (65892f9):
- **A room creator could bind any allowed repo.** Anyone can create and own a room, so the
  server allowlist alone exposed every allowed repo. Binding now also needs the requester's own
  agent's GitHub account to be able to write there. The answer is the same for "not allowed" and
  "no access", so it never confirms a repo name.
- **Hand-written state.** A room creator controls power levels, so anyone they choose could write
  the binding by hand. @pm and the agents now trust the binding (repo and mode) only if @pm wrote
  it.
- **A plan filed into another repo.** Plans carry their repo, and a repo change clears waiting
  plans. Results and polling only touch the room's current repo's items.
- **`None == None` passed the task guards.** A room with no repo now refuses every task.
- **Bugs.** Hermes's issue filing and both review paths lost their repo argument (TypeError), and
  "All done" crashed on the new keys. The tests' mocks had the old signatures, which is why these
  slipped through; the mocks now assert the repo.
- **Bridge.** The in-flight dedup is keyed by room and repo, and the old repo's project copy is
  removed after a switch.
- **Console.** The repo action shows @pm's answer. Readiness checks the default branch, not
  `main`, and shows no raw CLI errors. Chat takes the repo from the command's own argument.

Rejected:
- Migrating old state by historical repo. The old code only ever ran on `LOOP_REPO`, and changing
  it between runs had the same effect before this change.

Deferred (as before, GPT 8 #5):
- Hermes still infers its room from recent mentions, because the MCP call doesn't say which room
  it came from.

Found in the browser, not by any review: an apostrophe in a button label broke the console's
whole script (fbb4a41). `node --check` now runs on the page script in the tests.
