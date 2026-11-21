# ADR 0004: @pm reads GitHub as a read-only GitHub App on the public server

Date: 2026-11-20 · Status: accepted

## Context
@pm reads repos (issues, PRs, checks, readiness, the room-binding check) with the host's `gh`
login. The public VM has none, and a personal token there would read every repo its owner can see.

## Decision
- **A GitHub App, read-only** (Metadata, Contents, Issues, Pull requests, Checks, Commit statuses,
  Pages): each repo's owner installs it on their repo with one click. @pm gets a token for one repo
  at a time (`repositories: [name]`), valid for an hour and cached until 5 minutes before it expires.
- **One switch, at `github.run_json`**: @pm's own reads (agent=None) go to `github_app_reads`,
  which gives the same JSON as `gh` for the read shapes @pm uses and refuses anything else.
  Callers (loop, loop_progress, pm_plan, github_setup) are unchanged. Without `GITHUB_APP_ID`
  @pm keeps using `gh`, so local dev is unchanged.
- **PyJWT[crypto]** signs the JWT. It is already installed (mcp depends on it), so it adds nothing
  new. It is pinned in requirements.txt because @pm imports it directly. We don't hand-roll RS256.
- **Plain urllib, no redirects**: a token never follows a redirect to another host.
- **Binding UX**: a room whose repo lacks the App gets the install link in the chat and the console
  (the existing set_repo answer). @pm checks again for 10 minutes and finishes the binding with
  every check re-run.

## Consequences
- The App can't write, so agents keep their own GitHub identities for pushes, PRs and reviews.
- `HAGENCY_REPOS` still limits which repos a room may choose. The App only decides what @pm *can*
  read among them.
- Deferred: the "write access to bind" check still uses the person's agent identity, which isn't
  on the server. It needs a verified Matrix user → GitHub login link (ROADMAP).
