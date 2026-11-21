# Security

Hagency Rooms runs agent CLIs (Codex, Claude Code, Hermes) on chat messages, on the owner's own
computer. The checks that matter most:

Trust model (docs/adr/0006-owner-cli-powers.md): **your agent is your CLI as you configured it;
the room can't steer it, because only you can trigger it.**

- **Owner only.** An agent acts only on its owner's messages (`bridge.should_ignore`). Other
  people's messages are context, never a trigger. With `--allow-members`, members' questions run
  locked down (read-only, no web/MCP/connectors), never with the owner's setup.
- **Your CLI's own rules.** For its owner, the bridge runs Codex / Claude Code / Hermes with the
  owner's own sandbox, tools, MCP servers and permission/approval rules, and adds nothing on top
  and never widens them. What needs an interactive approval is denied and said in the reply.
  Turns on untrusted text that the owner didn't ask for (loop builds/reviews/plans, @pm's brain)
  stay locked down.
- **Chat never becomes a command.** The prompt goes on stdin, never through a shell or as CLI
  flags; saved session ids are validated.
- **Its own folder.** The agent works in `~/.hagency/work/<owner>/<room>/`, outside the app.
- **No secrets in chat, logs or git.** Passwords are generated into a git-ignored `.env`; agent
  replies are scrubbed of credential-shaped text and the bridge's own token.

A change touching any of these needs a test that shows the check still holds.

Please report a vulnerability privately via GitHub's **Report a vulnerability** button on the
repository's Security tab rather than in a public issue. Known open items are listed in
[REVIEWS.md](REVIEWS.md) under each review's deferred items.
