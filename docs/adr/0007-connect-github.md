# ADR 0007: People prove their GitHub account with "Connect GitHub" (device flow)

Date: 2026-11-20 · Status: accepted

## Context
Binding a room to a repo needs write access to that repo (`pm_settings.may_write`). That check used
the person's own agent's `gh` identity, which lives on their computer. On the public server @pm has
no such identity, so the check could never pass there (ADR 0004, "Deferred"). @pm needs a proven
link from a Matrix user to a GitHub account, made with clicks (GUI first: the Hagency panel).

## Decision
- **GitHub's device flow, with the existing "Hagency Rooms" App.** It needs only the App's Client ID,
  which is public (`GITHUB_APP_CLIENT_ID`), no client secret and no callback URL on our server. The
  owner ticks *Enable Device Flow* on the App once (docs/GITHUB_APP.md).
- **Clicks first.** The Hagency panel has a *GitHub* card: *Connect GitHub* → the code and the
  github.com/login/device link appear on the card and in @pm's private chat → the person enters the
  code on GitHub and approves → the card says *GitHub: @login ✓*, with *Disconnect GitHub*. Typing
  "connect my github" / "disconnect my github" to @pm works too. The code never goes to a project
  room: there @pm only says it sent it privately.
- **No user token is kept.** The token from the device flow is used once, for `GET /user`, and dropped
  (never stored, logged or shown). @pm keeps `{login, id}` per Matrix user (`data/github-links.json`).
  Write access is read later as the App: `GET /repos/{owner}/{repo}/collaborators/{login}/permission`
  with the repo's installation token. `admin` and `write` pass (GitHub reports *maintain* as
  `write`). The reported user's **id** must match the stored one, so a username that was renamed and
  then registered by someone else never passes.
- **`may_write` switches on `GITHUB_APP_ID`**, like ADR 0004: with the App, the connected account;
  without it (local dev), the agent's `gh` identity as before.
- **Choosing a repo** says "connect your GitHub first" before it looks at the repo (so it says nothing
  about which repos the server allows). Every other refusal uses the same words, which name all three
  conditions (the server lists it, the App is on it, your account can write). When the App isn't on
  the repo yet, access can't be read, so @pm waits for the install as before and then runs every
  check again.
- **Limits.** One sign-in per person at a time (asking again shows the same code; two clicks at once
  ask GitHub once). At most 5 new codes per person per hour and 40 for the server (GitHub
  rate-limits an App's device codes). Polls follow GitHub's interval and `slow_down`. A code is
  never waited on past its expiry (15 minutes at most). Anything unexpected ends the sign-in and
  connects nothing. Pending sign-ins live in memory: a restart drops them and the person clicks again.

## Consequences
- Agents still push with their own GitHub identities. The link only answers "may this person bind
  or run this repo".
- The user token isn't revoked (that needs the client secret, which the server doesn't hold).
  GitHub App user tokens expire after 8 hours unless the owner turned expiry off, and @pm drops it
  at once.
- Device-code phishing: someone could send a person their own code and ask them to enter it. GitHub
  shows the App's name and the person must type the code; @pm's message says to enter a code only if
  you asked for it yourself. The worst case is that someone binds a room to a repo the victim can
  write to; agents still can't push without their own access.
- A link isn't re-checked against GitHub until the next bind. Disconnecting is one click.
