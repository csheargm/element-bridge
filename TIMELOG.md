# TIMELOG — element-bridge prototype (2026-11-19, times from `date`, local)

| Time | Event | Elapsed from start |
|---|---|---|
| 08:47:32 | Start (env check: Docker 28.1, Python 3.12, codex-cli 0.169, Claude signed out) | 0:00 |
| 08:48:38 | Synapse + Element Web containers up (`/_matrix/client/versions` OK, :8649 200) | 1:06 |
| 08:48:59 | anna, betty, anna-agent, betty-agent + `#lounge` created (`setup_accounts.py`) | 1:27 |
| 08:50:00 | Both bridges online (first run of `bridge.py`, no edits needed) | 2:28 |
| 08:50:28 | **First agent reply** — anna-agent answered via Codex (e2e test, 6.2 s turn); whole e2e suite passed on first run | 2:56 |
| ~08:50:50 | **First Element login** — anna in Element Web (in-app browser), Lounge history rendered | ~3:20 |
| 08:51–08:52 | First reply to a real Element pill @mention (typing indicator visible; ~40 s Codex turn) | ~4:30 |

Wall-clock for the session includes writing ~270 lines of bridge code; the clock times above
are what `date` reported.

## Fix cycles (edit → rerun → verified)

| # | Fix | Edit start | Bridge rerun | Verified | Cycle |
|---|---|---|---|---|---|
| 1 | Replies showed raw `**markdown**` in Element → render to `formatted_body` (adds `markdown` dep) | 08:52:16 | 08:52:22 (restart < 1 s) | 08:53:00 in Element UI | **6 s** edit→running; 44 s incl. a Codex turn and UI driving |
| 2 | Claude CLI failure showed empty error (`claude exited 1:`) → parse JSON error from stdout | 08:53:05 | n/a (direct call) | 08:53:15 (`OAuth session expired…` surfaced) | **10 s** incl. a CLI call |

No compile step: Python process restart is < 1 s; per-room Codex sessions survive bridge
restarts (stored in `rooms/<owner>/<room>/.bridge-session.json`), verified by the follow-up
"same list again please" after fix 1.

## Later checks
| Time | Event |
|---|---|
| 08:54:53 | Encrypted-room path verified (agent posts "can't read encrypted rooms"), added to `test_e2e.py` |
| 08:56:01 | `./start.sh` verified end to end (idempotent re-run, both bridges online) |
| 08:56:35 | Full `test_e2e.py` (6 checks) + `test_rules` (8 tests) pass against `start.sh` bridges |
