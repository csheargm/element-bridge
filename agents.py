"""Run a person's own agent CLI (Codex or Claude Code) for one room turn.

Each room gets its own working folder and a saved session id so the CLI resumes the
same conversation next time. The prompt goes in on stdin (never as argv, so chat text
can't be parsed as a CLI flag) and nothing runs through a shell. Chat turns run read-only
with no approvals; only loop build tasks (write=True) may edit files, and only inside the
task's own clone (Codex workspace-write sandbox, no network; Claude Edit/Write tools only).
"""
import asyncio
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import lifecycle

TURN_TIMEOUT_S = 600
SESSION_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{7,63}$")  # codex/claude session UUIDs


@dataclass
class AgentResult:
    text: str
    session_id: str | None


class AgentError(RuntimeError):
    pass


# The sandbox only limits files and shell. Codex can also reach the owner's ChatGPT connectors
# (Gmail, Drive, calendars...), plugins, MCP servers, computer/browser use and web search, while
# reading untrusted text (other people's chat lines, issue bodies). All of it is switched off.
CODEX_FEATURES_OFF = ("apps", "plugins", "computer_use", "browser_use", "in_app_browser",
                      "image_generation", "multi_agent")
BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _codex_lockdown(web: bool = False) -> list[str]:
    args = [x for f in CODEX_FEATURES_OFF for x in ("-c", f"features.{f}=false")]
    args += ["-c", 'web_search="live"' if web else 'web_search="disabled"']   # web only where the owner granted it
    cfg = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    try:
        servers = tomllib.loads(cfg.read_text()).get("mcp_servers", {}) if cfg.exists() else {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise AgentError(f"can't read {cfg} to switch its MCP servers off: {e}") from e   # fail closed
    for name in servers:
        if not BARE_KEY_RE.match(name):
            raise AgentError(f"can't safely switch off Codex MCP server {name!r}; rename it in {cfg}")
        args += ["-c", f"mcp_servers.{name}.enabled=false"]
    return args


def _codex_argv(workdir: Path, session_id: str | None, write: bool = False, web: bool = False) -> list[str]:
    sandbox = "workspace-write" if write else "read-only"
    common = ["--json", "--skip-git-repo-check", "-c", f'sandbox_mode="{sandbox}"',
              "-c", 'approval_policy="never"', *_codex_lockdown(web)]
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


def _claude_argv(workdir: Path, session_id: str | None, write: bool = False, web: bool = False) -> list[str]:
    # "default" permission mode in -p: tools that need approval are denied, not prompted.
    # --tools limits built-ins to read-only ones even if user settings pre-approve Bash/Edit;
    # --strict-mcp-config (with no --mcp-config) loads no MCP servers.
    tools, mode = ("Read,Grep,Glob,Edit,Write", "acceptEdits") if write else ("Read,Grep,Glob", "default")
    if web:                                   # granted by the owner for this room (capabilities.py)
        tools += ",WebSearch,WebFetch"
    # --setting-sources user: never load .claude/ settings or hooks from the (untrusted) project.
    argv = ["claude", "-p", "--output-format", "json", "--permission-mode", mode,
            "--tools", tools, "--strict-mcp-config", "--setting-sources", "user"]
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


def agent_env(workdir: Path) -> dict:
    """The CLI's git must not climb out of its folder into this project's own repo (rooms/ lives
    inside it): a loop checkout without .git would otherwise show our files as its "changes"."""
    return {**os.environ, "GIT_CEILING_DIRECTORIES": str(workdir.resolve().parent)}


async def run_turn(backend: str, workdir: Path, prompt: str, write: bool = False,
                   fresh: bool = False, web: bool = False) -> str:
    """Run one turn in workdir, resuming that room's saved session unless fresh=True."""
    argv_fn, parse_fn = BACKENDS[backend]
    workdir.mkdir(parents=True, exist_ok=True)
    state_file = workdir / ".bridge-session.json"
    state = {} if fresh else (json.loads(state_file.read_text()) if state_file.exists() else {})
    session_id = state.get(backend)
    if session_id is not None and not (isinstance(session_id, str) and SESSION_ID_RE.match(session_id)):
        session_id = None   # never pass a malformed id as a CLI positional; start a fresh session

    proc = await asyncio.create_subprocess_exec(
        *argv_fn(workdir, session_id, write, web), cwd=workdir, env=agent_env(workdir),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await lifecycle.communicate(proc, prompt.encode(), TURN_TIMEOUT_S)
    except asyncio.TimeoutError as e:
        raise AgentError(f"{backend} timed out after {TURN_TIMEOUT_S}s") from e
    stdout = out.decode(errors="replace")
    if proc.returncode != 0:
        parse_fn(stdout)  # CLIs that report errors as JSON on stdout (claude) raise a clear AgentError here
        tail = (err.decode(errors="replace").strip() or stdout.strip()).splitlines()[-3:]
        raise AgentError(f"{backend} exited {proc.returncode}: {' | '.join(tail)[:400]}")

    result = parse_fn(stdout)
    if not result.text.strip():
        raise AgentError(f"{backend} returned an empty reply")
    if not fresh and result.session_id and result.session_id != session_id:
        state[backend] = result.session_id
        tmp = state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, state_file)   # atomic: a crash can't leave a half-written state file
    return result.text
