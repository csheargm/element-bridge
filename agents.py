"""Run a person's own agent CLI (Codex or Claude Code) for one room turn.

Each room gets its own working folder and a saved session id so the CLI resumes the
same conversation next time. The prompt goes in on stdin (never as argv, so chat text
can't be parsed as a CLI flag) and nothing runs through a shell. Both CLIs run in
their read-only / non-approving defaults: chat can't grant shell or write access.
"""
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

TURN_TIMEOUT_S = 600


@dataclass
class AgentResult:
    text: str
    session_id: str | None


class AgentError(RuntimeError):
    pass


def _codex_argv(workdir: Path, session_id: str | None) -> list[str]:
    common = ["--json", "--skip-git-repo-check", "-c", 'sandbox_mode="read-only"',
              "-c", 'approval_policy="never"']
    if session_id:
        return ["codex", "exec", "resume", *common, session_id, "-"]
    return ["codex", "exec", *common, "-C", str(workdir), "-"]


def _parse_codex(stdout: str) -> AgentResult:
    session_id, messages = None, []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue  # codex may print non-JSON banner lines; they carry no reply text
        if ev.get("type") == "thread.started":
            session_id = ev.get("thread_id")
        item = ev.get("item") or {}
        if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
            messages.append(item.get("text", ""))
    return AgentResult("\n\n".join(m for m in messages if m.strip()), session_id)


def _claude_argv(workdir: Path, session_id: str | None) -> list[str]:
    # "default" permission mode in -p: tools that need approval are denied, not prompted.
    argv = ["claude", "-p", "--output-format", "json", "--permission-mode", "default"]
    return argv + (["--resume", session_id] if session_id else [])


def _parse_claude(stdout: str) -> AgentResult:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise AgentError(f"claude returned non-JSON output: {stdout[:200]!r}") from e
    if data.get("is_error"):
        raise AgentError(f"claude error: {str(data.get('result'))[:300]}")
    return AgentResult(data.get("result", ""), data.get("session_id"))


BACKENDS = {"codex": (_codex_argv, _parse_codex), "claude": (_claude_argv, _parse_claude)}


async def run_turn(backend: str, workdir: Path, prompt: str) -> str:
    """Run one turn in workdir, resuming that room's saved session if any."""
    argv_fn, parse_fn = BACKENDS[backend]
    workdir.mkdir(parents=True, exist_ok=True)
    state_file = workdir / ".bridge-session.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    session_id = state.get(backend)

    proc = await asyncio.create_subprocess_exec(
        *argv_fn(workdir, session_id), cwd=workdir,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), TURN_TIMEOUT_S)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise AgentError(f"{backend} timed out after {TURN_TIMEOUT_S}s") from e
    stdout = out.decode(errors="replace")
    if proc.returncode != 0:
        parse_fn(stdout)  # CLIs that report errors as JSON on stdout (claude) raise a clear AgentError here
        tail = (err.decode(errors="replace").strip() or stdout.strip()).splitlines()[-3:]
        raise AgentError(f"{backend} exited {proc.returncode}: {' | '.join(tail)[:400]}")

    result = parse_fn(stdout)
    if not result.text.strip():
        raise AgentError(f"{backend} returned an empty reply")
    if result.session_id and result.session_id != session_id:
        state[backend] = result.session_id
        state_file.write_text(json.dumps(state))
    return result.text
