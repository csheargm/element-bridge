# Review log

GPT reviews of each change (R15): what was adopted, with tests, and what was deferred.

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

## GPT review of the loop (R15)

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

## GPT review 12 (the site and the Oracle deploy kit — faf34a2)

No XSS from visitors and no exposed admin or internal ports. Adopted:
- **`.env` was shell code.** `envfile.py` now reads it as data and validates every value:
  domains, the e-mail, pinned versions (no `latest`) and a hex password. The file is created
  mode 600 and secrets are filled without a command line.
- **An interrupted first run could leave Synapse on SQLite.** The config swap is atomic and keeps
  the file's owner. A "configured" marker gates the start, the script runs safely every time, and
  a server-name mismatch is refused.
- **No admin or invite path.** `invite.sh admin` and `invite.sh token N` use the internal admin
  API only. Bootstrap checks that sign-up without a token is refused.
- **The site folder could fall outside Caddy's mount.** The layout is now fixed
  (`~/hagency/{deploy,site}`) and checked.
- **iptables vs Docker.** The host-iptables edits and their persistence are gone; OCI's security
  list is the firewall.
- **Success claimed too early.** Bootstrap now runs `compose config` and `pull`, validates and
  reloads Caddy, and checks HTTPS content on every endpoint. Postgres sits on an internal network.
- **Site claims.** The trust rules are scoped to agents registered on the server, "stop" means
  new work and merges, and the web-fetch wording is narrower. The preview is "by invitation",
  the hero room is labelled an example, there are no copy buttons for unpublished commands, and
  the unclaimed AUR package name was removed because someone else could squat it.

Self-caught while fixing: `curl -f` would have hidden the 401 body the invite-only check reads.

## GPT review 13: approvals, retry, Hermes verify (70db407..4a3377f)

Adopted:
- **Talk read as consent.** "good for you", "let us approve", "yes?" and "ok 👎" no longer count.
  A question is never a yes, and a bare yes may only end in spaces, "." or "!".
- **The model path** now needs the person's own approving words, even in a reply to the question.
- **"do not go autonomous"** no longer classifies as autonomous (or assist).
- **A stale mode question** is cancelled by any explicit mode change (the "mode" kind was missed).
- **Retry** refuses while the paused item's PR is still open, so there is never a second PR for
  one issue. "retry 9" is read as #9, and a number it can't read gets a question instead of
  a guess. It reads the room's repo when the cache is empty.
- **Verify is bounded.** File sizes are checked before reading, headers and placeholders count,
  and there are at most 500 files. Symlinks were already fixed in 4a3377f.
- **The last direct stop** (GitHub not counting an approval) goes through `stop`, with retry help.

Deferred: a close task whose dispatch @pm already timed out can still run, because liveness comes
from recent events. Close also doesn't re-check that the issue text is unchanged since the
verdict. Both need task expiry in mcp_core; see ROADMAP.

## GPT review 14: voice and settings phrasing (2cd9d2b..d91d271)

Adopted:
- **Spoken words never act by themselves.** A transcript may hold someone else's voice or a
  misheard word, so a spoken "ok" to a waiting question gets "click 👍", and spoken mode or
  settings changes are asked with a 👍. Spoken repo names and issue numbers must be typed, and
  spoken "!" commands are ignored.
- **Loose wording only asks.** "allow Betty to quote 'let my agent search the web'" or a
  question matched the order-free web rules. Only the plain forms apply at once ("let my agent
  search the web here", "enable web search for my agent"). Anything else gets a 👍 question.
- **One question at a time.** A settings question no longer replaces a waiting plan or mode
  question.
- **Voice resources.** One transcription at a time and at most 3 queued. The download times out
  after 30 s, and decoding stops after 5 min whatever the event claims. The worker gets a
  minimal environment and its own process group, killed on timeout or cancel. Transcripts are
  capped, and every failure is said in the room.
- **Slow input.** Rules read at most 1000 characters, and the grant rule no longer backtracks on
  trailing spaces.

Deferred: idempotency of a voice job across a crash mid-transcription (it is then lost, not
repeated). A confirmed question is marked "approved" before its change runs, so a failed write
leaves it used up: ask again.

## GPT review 15: plain answers, voice per listener, owner-only note (2f5bcb4..7113832)

GPT found no owner-check bypass: typed and spoken requests both reach `should_ignore` with the
real Matrix sender. Adopted:
- **Transcribing before checking.** A bridge now transcribes a voice message only if it could
  act on it: from its owner, or a reply to it or a DM. Other people's clips are never
  transcribed on the owner's computer.
- **Names count only at the start.** "PM, tell Betty's agent to stop" goes to @pm only.
  "PM-related" isn't "PM,", and a typed `betty-agent` needs a left boundary and is ignored
  inside the quoted part of a reply. Edits are never new questions.
- **Thread fallbacks** don't count as replies, and malformed reply data no longer crashes the job.
- **Thread questions are answered in their thread.**
- **The refusal note** is "I only take requests from Betty." (no file-access detail, no display
  name). A failed send can't stop the sync loop, and the rate-limit table stays small. Voice
  errors show only our own wording.
- **test_e2e** expects the refusal note instead of silence.

Deferred: web notices and grants still infer the room and question from the owner's recent
@mention (mcp_context.owners_room). Binding them to the event that started the turn needs
Hermes to tell the MCP server which message it is answering. Room history still enters the
model prompt as context; that is a prompt-injection risk the prompt only labels. Event
de-duplication lasts one sync batch.

### GPT reviews 16–18 (2026-11-20): the public repo, and changing your agent's own code

**16, the repository as published.** No secrets in the files or history, and the test workflow is
safe for pull requests from forks. Fixed: CI actions pinned to commit SHAs, README commands relative
to a clone, CONTRIBUTING's clone line, LICENSE name, agent names escaped on the status board, board
text can't carry raw HTML into the console, and #N links to the room's repo there. Left for the
owner to decide: the docs name the real GitHub accounts behind each agent and a demo run log.

**17, the customize feature (blocked rollout).** "Use it myself" ran agent-written code as this
computer's account, which on a shared computer holds every owner's passwords and GitHub logins.
Fixed: it is offered only where the owner is the only person whose agent runs here (operator
opt-in HAGENCY_CUSTOM_SHARED_OK=1); one folder per version; tests with no environment, killed as a
process group, zero tests is a failure; git names its own metadata, links/.gitattributes/.pth are
refused; buttons work once, in their own private room only; ↩️ and auto-revert undo only their
own version; HAGENCY_HOME for custom bridges; remote users can't use the console's go-back.

**18, re-check.** Confirmed fixed or partly fixed; also fixed: a PR must be the owner's own change
(not an official commit), restricted rooms aren't private, old-layout copies are refused clearly.

Deferred: real isolation (a separate OS user or container) is what would make "Use it myself"
safe on a shared computer; until then it's off there. Rebase/discard still run git in the
editable tree; existing trial/live checkouts find metadata through their own .git. Shared JSON
state has no cross-process lock. A failed confirmation message can leave a used button active.
Pruning keeps the chosen and previous version; a third quick switch could remove one still draining.
Test stderr is buffered in memory.

### GPT review 20 (2026-11-20): Hermes changing its own code, and the Hermes invite patch

Range origin/master..HEAD (eb7fa8c Hermes code-change tools, 0ceaec5 invite allowlist patch).

Fixed (each with a test):
- **The invite allowlist had a second way in (blocking).** Hermes also joins rooms left in
  `rooms.invite` after each sync (`_join_pending_invites`), never through `_on_invite`. Both paths
  now share one check, and the reconciler reads the inviter from our own `m.room.member` invite in
  `invite_state`; an unknown inviter fails a restrictive list. The patch file was regenerated.
- **A withdrawn opt-in kept working.** Unsetting HAGENCY_CUSTOM_SHARED_OK left the old value in the
  Hermes profile, and a version chosen while it was set kept launching. `set_repos` now removes it,
  and `code_dir` re-checks `why_not_use` at every launch (official code until it's given again).
- **The button helper joined invites too.** Logged in as the Hermes agent, it could join a DM before
  Hermes saw the invite, so Hermes never recorded the DM. In `--buttons-only` mode it no longer
  handles invites.
- **Paths:** reserved names (.git, custom/, .gitattributes, .pth) are checked again after resolving,
  so a symlink in the copy can't reach .git. Reads stop at MAX_READ instead of loading the whole file.
  An empty request is refused instead of crashing the commit message.
- The Hermes tool and profile modules (hagency_mcp.py, mcp_core.py, mcp_custom.py,
  hermes_profile.py) now get the sensitive-file red flag.
- Re-check of those fixes: Hermes re-saves a profile's `args` as a block list, which
  `set_code_dir` didn't match, so a withdrawn opt-in or "go back" could leave Hermes on the custom
  tools. Both forms are matched now, and a profile with no hagency args line is reported by the
  supervisor instead of passing silently. Reserved names are compared lower-cased (this Mac's disk
  ignores case: `.GIT` is `.git`).

Deferred: the MCP tools trust that the turn came from the owner (Hermes answers only
MATRIX_ALLOWED_USERS, and nothing goes live without the owner's 👍 in the private room; a change
ID bound to the owner's event would be stronger). Isolation for running a chosen version on a shared
computer (as in 17). TOCTOU/hard-link races from another local process, cross-process locking of
the offer and `_change` state, a commit stranded when publishing the offer fails, unchecked
reaction sends, and private_room ignoring pending invites. A DM race inside Hermes (from our DM
patch, not the invite patch): its sync reconciler can join a DM invite before `_on_invite` records
`m.direct`, so that DM needs an @mention until Hermes refreshes its DM cache.

### GPT review 21 (2026-11-20): a newcomer gets an agent from chat (welcome, bring your own)

Range origin/master..HEAD (c25d5d2 welcome + pairing + connect.py). 15 findings; each verified in the code.

Fixed (each with a test):
- **A same-named person from another server could get our agent's password (high).** "@pm I want
  an agent" from `@newbie1:elsewhere` matched our remote `newbie1` by localpart. Offering and
  provisioning now require a person on this server (full Matrix ID), checked in both places.
- **The code went to the remembered chat (high).** It is now sent to `private_room()` re-checked at
  that moment (still just @pm and the person, or a fresh private chat).
- **The console URL went into a shell line unchecked.** HAGENCY_PUBLIC_CONSOLE must be
  `https://host[:port]` (http only for localhost) before it's put in the install line or connect.sh.
- **Concurrent set-ups.** One bring-your-own job per person at a time; `.env` and agents.json temp
  files are unique per process and thread.
- **Cross-site POSTs could burn the attempt budget.** /connect refuses non-JSON and any request
  with an Origin header (browsers) before the limiter counts anything.
- **The code was used up before the client was ready.** connect.py picks the CLI first; a missing
  CLI no longer costs the code.
- **Test output.** The live test no longer prints the install line or /connect's answer.
- **Joins while @pm was down were never welcomed; a failed greeting was remembered anyway.** @pm
  welcomes current Welcome members without a welcome at each start, and records one only after it
  was sent.
- **A saved password that never registered poisoned retries.** `ensure_user(verify=True)` (used for
  agents @pm creates) signs in to check and registers again if it can't.
- **People with a server-run default agent (a registry entry without "agent") could be switched to
  remote.** Refused now.
- **Welcome text could swallow project requests.** "@pm I want an agent" must be the whole
  message ("@pm I need an agent to review #7" goes on to the usual chat), and "I don't want to
  bring my own" isn't a choice.
- **Credential file permissions.** ~/.hagency is chmod 700 whatever the umask; the file is written
  via a new mkstemp file (0600) and swapped in; connect.sh sets umask 077.
- **Python version.** Markdown 3.11.3 needs Python 3.11: connect.sh now looks for python3.13 … 3.11
  before python3 (macOS's own python3 is often older).

Deferred, with reasons:
- **The public deploy can't serve it yet (high).** True: deploy/ runs no @pm or console and Caddy has
  no /connect route; the house agent and the public server are the next step. configure_synapse.py
  already sets the Welcome room (@pm must exist before the first sign-up).
- **Installer runs an unpinned clone.** curl over https from the server, then a git clone of the
  public repo: pinning a signed release comes with the public server.
- **Limiter identity behind a proxy / counters reset on restart.** Local dev isn't proxied; a code has
  ~49 bits and lives 30 minutes, so the limiter is defence in depth. Trusted X-Forwarded-For comes
  with the deploy.
- **Re-pairing returns the same password; the code is in chat history and shell history.** Rotating
  the agent's password on every redeem needs an admin token in @pm; next step, with revocation.
- **Remote agents take @pm's loop tasks.** By design, as for agents run here: only @pm's tasks,
  only with the owner in the room. The supervisor reads agents.json at start; a person never moves
  from a server-run agent to remote (refused), so there's no transition to reconcile.
- **Multiple console workers.** There is one process (@pm's); codes are atomic within it.

Re-check of those fixes (GPT, HEAD~1..HEAD), fixed with tests:
- The code's chat was approved from @pm's cached membership: `only_us()` now asks the server for the
  room's members (joined or invited) right before the code is sent; anyone else there and no code.
- Set-ups for different people raced on .env/agents.json: one set-up at a time in @pm (a lock).
  (onboard.py run by hand at the same moment is still a separate process: deferred.)
- `--agent` or a saved choice skipped the "is it installed" check; a damaged credentials file blocked
  a fresh code; connect.py no longer chmods an existing folder (it warns), connect.sh sets umask 077
  before creating anything and rebuilds a venv made with an older Python.
- URL check uses fullmatch (no trailing newline); an empty Origin header counts as a browser.

### GPT review 22 (2026-11-20): the public VM runs the shared side (and Synapse Admin)

Range origin/master..HEAD (0023615 deploy: @pm, console, /connect behind Caddy; pinned images;
deploy.sh from GitHub only; guard.py). Findings, each checked in the code; fixed with tests:
- **Root wrote through a path @pm controls (high).** bootstrap opened a predictable `.tmp` as root
  inside @pm's writable tree. The secret now goes to a root-owned `deploy/pm-secret/` mounted
  read-only, written by `hostfiles.replace` (mkstemp, O_EXCL, rename). configure_synapse.py's config
  and marker in Synapse's writable folder go through the same function (a planted link is never
  followed: test_host_tools).
- **Element's config was 0600 under umask 077 (high).** Element reads it as nginx: configs are
  chmod 0644 explicitly, and element/synapse-admin are recreated so the new file is mounted.
- **A compromised @pm could persist across deploys (high).** `release.py` clears ~/hagency/pm to the
  commit's files plus @pm's named state before unpacking: dropped files, planted modules (e.g. a
  `json.py`), links at tracked paths and the virtualenv all go (rebuilt from requirements.txt).
- **The VM checked `origin/master` (medium).** It now fetches the GitHub URL itself and checks
  FETCH_HEAD; `git status` failing is a failure; a deploy lock (flock); `.deployed` only after
  bootstrap succeeds; @pm's stop failing stops the deploy.
- **Synapse kept its old config; Caddy validated after `up` (medium).** Synapse restarts when
  homeserver.yaml's hash changed; the Caddyfile is validated in a throwaway container first.
- **Any container on `edge` could spoof X-Forwarded-For (medium).** The header counts only on a
  connection from Caddy's own address (HAGENCY_TRUST_PROXY=caddy, resolved).
- **Missing traffic figures cleared an active pause (medium).** The flag records its month; no
  figure keeps this month's pause (and warns); an interface vnstat doesn't watch is an error.
- **Origin compared by hostname only (low).** Exact origins now (scheme, host, port).
- **Shell:** `apt-get update && install` split (set -e), values read before use, `--end-of-options`,
  `ssh --`. **Tests:** deploy.sh refuses a real commit that isn't on master (a dangling commit).
- **Oracle's free A1 allowance (high, verified on Oracle's page):** now 1,500 OCPU-h / 9,000 GB-h a
  month = 2 OCPU / 12 GB. The VM is 4 / 24: the owner's call (resize to 2 / 12); documented.

Added at the owner's request: Synapse Admin at admin.<domain> behind Cloudflare Access. Caddy takes
only Cloudflare's addresses with a Cf-Access-Jwt-Assertion header (403 otherwise); /_synapse/admin is
404 on matrix.* and rooms.*. `@owner` is made with a random password kept nowhere; the owner sets
one with `invite.sh owner-password` (typed in their own ssh terminal).

Deferred, with reasons:
- **Verifying the Access JWT's signature.** Not in stock Caddy; it needs a plugin build or a
  forward_auth verifier, plus the Access team domain and AUD tag. Access checks it; the IP filter
  stops bypasses. Next step once the owner shares the AUD tag.
- **@pm holds the registration secret and admin-bot's password.** It creates people's agents and
  invite tokens; moving those to a separate admin service is the next hardening step.
- **The invite token and an agent's password leave the VM.** By design: the operator hands tokens
  out, and /connect gives a person their own agent's sign-in (GPT review 21).
- **Guard is daily and advisory;** media retention bounds age, not total size. Enough at today's
  scale (42 GB free, nothing near 1 TB); a tighter loop comes with real traffic.
- **The global wrong-code limit can block real redemptions under attack.** Unchanged from review 21.
- **Migration from the old ~/hagency/deploy layout.** The VM was fresh; nothing to migrate.

Re-check of those fixes (GPT, HEAD~1..HEAD), fixed with tests:
- Mode and owner are set on the open file (fchmod/fchown), and a linked homeserver.yaml is refused,
  so root never reads or writes through a name a container swapped.
- `.deployed` lives in ~/hagency, outside @pm's folder; settings are checked (and new ones get their
  defaults, e.g. ADMIN_DOMAIN) before @pm stops; commits older than release.py are refused.
- release.py drops links or wrong types at state names and refuses a commit with files there.
- Synapse restarts when its config differs from the one it last started with (a failed run too).
- `ensure owner` fails if @owner exists but isn't an admin. An empty Origin is refused. Last
  month's media pause is cleared before measuring. The direct-to-VM probe sends an Access header to
  the admin API path, so a 403 proves the address filter.
Deferred: JWT signature checking (as above); the owner's password is set through @pm's container
(a compromised @pm could see it; a separate admin tool is the next step); the admin host's media is
not paused (it's behind Access, owner only).

Live deploy and the whole-batch review (GPT, 707dd84..HEAD):
- First live deploy: Oracle's `ubuntu` is uid 1001, so @pm (hardcoded 1000) couldn't read its
  0700 folder. PM_UID is now recorded in .env (envfile.py) and used by compose.
- GPT on that fix, adopted with tests: bootstrap refuses to run as root and refuses a PM_UID that
  isn't the deploy user's; compose reads PM_UID only from .env (`env -u PM_UID`); PM_UID 0 is
  invalid; deploy.sh refuses commits older than the PM_UID layout (a rollback that couldn't start @pm).
- Live: certificates for rooms and matrix issued; the admin host's HTTP-01 is blocked by Cloudflare
  Access until the owner bypasses /.well-known/acme-challenge/ (see DEPLOY_ORACLE.md).
- Live: the second deploy's `ensure owner` got 429 on admin-bot's sign-in. @pm, admin-bot and new
  agents all sign in from @pm's container address, and Synapse's default per-address login limit
  (burst 5, then one per ~5 min) ran out. `rc_login.address` is now burst 20, then one per 50 s (GPT: 0.1/s would allow 360 sprayed guesses an hour per address; 0.02/s is 72); the per-account
  and failed-attempt limits stay Synapse's defaults.
- Live: `.deployed` was never written: bootstrap's `docker compose exec` inherited the ssh session's
  stdin, which is deploy.sh's remote script, and read the rest of it. bootstrap now gets /dev/null.

### GPT review 23 (2026-11-20): the Welcome pointer, and people from other servers

Welcome UX (GPT, origin/master..HEAD), adopted with tests:
- The Welcome pointer ("accept it under Invites") was marked sent before sending: a failed send or
  a restart in between lost it for good. Now saved only once sent; retried at the person's next
  join or @pm's next start; never for someone who already clicked a button.
- A reused private chat kept its old "Hagency (private)" name: renamed on reuse.
- Re-check: "I want an agent" re-greets, which dropped "pointed" and could post the line twice: kept.
Rejected or deferred:
- The pointer is an m.notice, which Matrix's default rules don't notify for: the newcomer is looking
  at Welcome (their only room) when it arrives, and the pill still shows; not worth a new send path.
- A crash after the server accepts the pointer but before "pointed" is saved can repeat it once:
  one repeated line is better than a lost one.

People from other servers, ownership by full Matrix ID (GPT, the feature commit), adopted with tests:
- Codes and console links go to @pm's private chats, where the person could invite someone later
  who'd read them back: @pm's new private chats are invite-by-@pm-only and history "joined".
- An agent claimed by two records (an operator's agent_user) went to the first match: now it is
  nobody's. /connect, follow-me removal (kick) and planning check the agent is the asker's own.
- The hourly cap counted only finished set-ups, so a server refusing the agent's chat invite made
  unlimited accounts: a remote person's entry (and its "created") is saved before registering.
- "is_direct" is only the sender's word: after joining, @pm leaves a chat that isn't just the two
  of them. One person can't use the hour's joins (3 each, 30 in all).
- HAGENCY_ALLOW_REMOTE=0 now also stops new codes and sign-ins for other servers' people.
- /connect refuses a remote record whose account isn't the derived name (connect.py would refuse it
  after the code was spent). Half-migrated registry entries are merged, not replaced.
Rejected or deferred:
- The person's own homeserver can read the code sent to them: it is their server, their trust.
- Registry read-modify-write races between @pm and onboard.py (the CLI): pre-existing, needs a
  file lock across processes; deferred.
- "@pm I want an agent" in a shared room isn't counted by the join limit: that person is already in
  a room with @pm, and @pm reuses its chat with them.
- A pre-upgrade code stays valid for its 30 minutes after a new one is issued; checking the saved
  homeserver URL again when reading one's own 0600 credentials file; an owner whose derived name
  collides with another's (needs a 40-bit hash collision; refused at set-up anyway).
- Console sign-in inside Element for other servers' people (their server's OpenID token): it means
  fetching a URL their server names; they use "@pm send me my console link" (ADR 0003).

Whole batch (GPT, origin/master..HEAD), adopted with tests:
- History "joined" hid @pm's own offer from a newcomer (sent while they were invited): "invited".
- Older @pm chats (no safeguards) are no longer reused for codes and links: a new chat is made.
- @pm leaves a chat someone from another server opened, after answering: it could grow later.
- Follow-me invited an owner's agent into @pm's private chat: @pm's chats are skipped.
- /connect returns a bare name for people here, as an older connect.py expects.
- Set-up refuses an agent name claimed twice or overridden (one /connect would then refuse).
Rejected or deferred:
- A reservation that failed registration is exempt from later counts: it was counted once, and
  it can only ever make that one person's account.
- Registry races with follow-setting writes; the 30-minute pre-upgrade code: as above.
- Re-check: follow-me skipped only the current chat, so an old one replaced by a new chat could
  still pull the agent in: any two-person chat named as @pm's chats (now or before) is skipped.
  A failed leave is logged; a failing membership check still ends in leaving.

@pm moderates new rooms, the panel comes first, no welcome for admins (GPT, the two commits),
adopted with tests:
- Co-creators are in creation_content.additional_creators, not a top-level field: read there
  (Synapse refuses a creator in "users"). Requests the module doesn't expect (a null or odd
  override, non-list invites, power levels in initial_state) are left exactly as they came.
- The dev server's Synapse is recreated (`compose up`) so it gets the module mount before restart.
- The panel's moderator check is room_state.is_moderator (v12 creators count).
- Admin status is remembered for a day, not forever.
Rejected or deferred:
- Rolling back past this commit leaves the `modules:` line with no /modules mount: a rollback
  must drop that line (noted here; rollbacks are by commit and rare).
- The widget layout write replaces other widgets' placements and isn't retried on failure;
  replacing a pre-existing multi-line `modules:` block; `.synapse-loaded` written before Synapse
  is healthy: all pre-existing or not present on this server's generated config.

Background agent, offline notice, Disconnect (GPT, the feature commit), adopted with tests:
- A code redeemed during a Disconnect could pick up the new password: codes are revoked first (on
  @pm's own thread, no SavedDict race), and /connect reads .env under the set-up lock.
- Disconnect when admin-bot can't sign in left .env ahead of Synapse: treated as a refusal, and
  only that one line is put back, on a fresh read.
- systemd expands % and $ inside quotes: doubled in the unit, so a federated owner ID arrives as sent.
- daemon-reload / restart failures are errors, not silence. A presence timeout or network error
  never stops @pm's tick. The panel says when Disconnect failed.
Rejected or deferred:
- `connect.py --stop` reports removal even if launchctl/systemctl couldn't stop a running process
  (it then stops at the next login); the 🔁 button isn't retried if its reaction send fails (the
  message still says what to run, and "bring my own" works).

Outage (2026-11-21, ~00:13–00:33 UTC) and its fix (GPT, 22b502e..HEAD), adopted with tests:
- Several deploys in an hour used Synapse's per-account sign-in burst (3) for @pm, which signed in
  three times per start. setup_accounts read the 429 as a wrong password, tried to re-create @pm
  (M_USER_IN_USE) and exited; Docker restarted it, and it signed in again: a loop. Now 429s are
  waited out (retry_after_ms, up to 15 minutes), ensure_pm needs no sign-in when @pm exists with
  its name, and a limit that outlasts the wait is an error, never "wrong password" (GPT: the
  first fix still fell through to re-registering after the wait).
Deferred: matrix-nio's own unlimited 429 retry inside client.login makes lifecycle.login's cap
  soft; set-up holds WRITING while a remote agent's sign-in waits, delaying /connect meanwhile.

Connect page and the Windows installer (GPT, the feature commit), adopted with tests:
- Two keepers could run one agent (a Startup entry beside a task, a task left under an old name):
  connect_win.py holds agent.lock, ties the bridge and its CLI to itself with a Windows job, and
  connect.ps1 removes earlier tasks and Startup entries of this installation first.
- Stopping the old agent could hit an unrelated process with a reused PID: only a command line
  naming this installation's connect_win.py or bridge.py, killed with its children.
- The ACL could protect another folder than the one holding the sign-in: one absolute folder
  (HAGENCY_DIR honoured), passed to connect.py and the task; `icacls /reset` first, so no other
  grants survive.
- An npm `codex.cmd` passed the CLI check but the bridge (no shell) can't start it: only a real
  `.exe` counts, with a plain hint. A clean bridge exit (Disconnect) now stays stopped, as with
  launchd/systemd; logs go to ~/.hagency/logs/ like agent_service.py.
- `irm -UseBasicParsing` in the line shown (Windows PowerShell 5.1); native stderr no longer
  stops the installer before its exit code is checked; a changed fragment refreshes the code box.
Rejected or deferred:
- @pm still sends the raw curl line: pm_welcome.py/remote_agent.py belong to the other change;
  `connect_page.page_link(code)` is ready for it.
- The code in shell history / process arguments: it works once, for 30 minutes, and is used
  within seconds, as with connect.sh. TLS 1.2 before the first `irm`: Windows 10/11 default to it.
- X11/BSD shown as Linux, and the installer reporting failure only on screen (it must not close
  the person's window): the tabs and the red message cover them.

Connect page and the Windows installer (GPT re-check), adopted with tests:
- connect.py's check mark crashed Python's piped output on a cp1252 PC after the code was used:
  PYTHONIOENCODING=utf-8 for the installer's Python calls (restored after) and for the bridge.
- `icacls /reset` briefly let the folder's contents inherit the parent's access: the folder's
  whole access list is replaced first (only you), then its contents reset to inherit just that.
- -RestartCount 255 (Task Scheduler's maximum). Old tasks and Startup entries are matched by
  this installation's keeper AND sign-in folder, case-insensitively; a PID is stopped only if it
  is a Python running our keeper or bridge. A refused Windows job is logged, not fatal.
Rejected or deferred:
- A CLI reachable only through a PowerShell profile's PATH: the hint says it must be on the PATH.
- README describes the link @pm will send: it lands with the @pm change (PR notes).
