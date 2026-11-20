# Security

hagency-bridge runs agent CLIs (Codex, Claude Code, Hermes) on chat messages, on the owner's own
computer. The checks that matter most:

- **Owner only.** An agent acts only on its owner's messages (`bridge.should_ignore`), unless the
  owner starts it with `--allow-members`.
- **The CLI's own sandbox.** The bridge never widens the agent CLI's sandbox or approval settings,
  and never runs shell commands from chat itself.
- **No secrets in chat, logs or git.** Passwords are generated into a git-ignored `.env`.

A change touching any of these needs a test that shows the check still holds.

Please report a vulnerability privately via GitHub's **Report a vulnerability** button on the
repository's Security tab rather than in a public issue. Known open items are listed in
[COMPARISON.md](COMPARISON.md) under each review's deferred items.
