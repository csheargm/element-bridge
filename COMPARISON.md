# Notes and review log: Matrix + Element + thin bridge

Measured 2026-11-19 on the owner's Mac. Details in [TIMELOG.md](TIMELOG.md).

## What works

- anna and betty in `#lounge` with `@anna-agent` / `@betty-agent`; each agent runs its owner's
  local Codex login (Claude Code is one flag: `--agent claude`, blocked only by the expired login).
- Trigger: Element mention pill, plain-text name, or DM. Non-members, other agents (loop guard)
  and own messages are refused. Per-room working folder + resumed CLI session (survives restarts).
- Typing indicator while working; reply threaded to the question with rendered markdown.
- Safety: prompt on stdin, no shell; Codex `read-only` sandbox + `approval_policy=never`; Claude
  `-p` default permission mode (approval-needing tools denied). Chat can't widen these.
- Verified by `test_e2e.py` (real Matrix API, 6 checks) and in the Element Web UI.
