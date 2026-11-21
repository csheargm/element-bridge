"""Run a person's own agent CLI (Codex or Claude Code) for one room turn.

Each room gets its own working folder (workdirs.py) and a saved session id so the CLI resumes
the same conversation next time. The prompt goes in on stdin (never as argv, so chat text can't be
parsed as a CLI flag) and nothing runs through a shell.

Two kinds of turn (docs/adr/0006-owner-cli-powers.md):
  - owner=True: the owner's own message. The CLI runs as the owner configured it on this machine
    (their sandbox, tools, MCP servers, permission/approval rules); the bridge adds no lockdown.
    What their rules leave to an interactive approval is denied (nobody can approve from a room),
    and the reply says so plainly.
  - owner=False (default): turns not driven by the owner's own words, on untrusted text (loop
    builds/reviews/plans, @pm's brain, a member's question under --allow-members): read-only, no
    approvals, no connectors/MCP/web; only builds (write=True) may edit, inside the task's own clone
    (Codex workspace-write sandbox, no network; Claude Edit/Write tools only).
"""
import asyncio
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import lifecycle
from workdirs import SESSION_FILE, SESSION_ID_RE

TURN_TIMEOUT_S = 600
MAX_DENIED_SHOWN = 8
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


@dataclass
class AgentResult:
    text: str
    session_id: str | None
    denied: tuple[str, ...] = ()       # tools the owner's own permission rules didn't allow this turn


class AgentError(RuntimeError):
    pass


# Locked turns only (owner=False). The sandbox only limits files and shell. Codex can also reach
# the owner's ChatGPT connectors (Gmail, Drive, calendars...), plugins, MCP servers, computer/browser
# use and web search, while reading untrusted text (other people's chat lines, issue bodies). All of
# it is switched off.
CODEX_FEATURES_OFF = ("apps", "plugins", "computer_use", "browser_use", "in_app_browser",
                      "image_generation", "multi_agent")
BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _codex_lockdown() -> list[str]:
    args = [x for f in CODEX_FEATURES_OFF for x in ("-c", f"features.{f}=false")]
    args += ["-c", 'web_search="disabled"']
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


def _codex_argv(workdir: Path, session_id: str | None, write: bool = False, owner: bool = False) -> list[str]:
    """owner: the owner's ~/.codex/config.toml decides sandbox, approvals, web search and MCP servers."""
    sandbox = "workspace-write" if write else "read-only"
    common = ["--json", "--skip-git-repo-check"]
    if not owner:
        common += ["-c", f'sandbox_mode="{sandbox}"', "-c", 'approval_policy="never"', *_codex_lockdown()]
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


def _claude_argv(workdir: Path, session_id: str | None, write: bool = False, owner: bool = False) -> list[str]:
    resume = ["--resume", session_id] if session_id else []
    if owner:     # the owner's settings (all their sources) decide tools, MCP and the permission mode
        return ["claude", "-p", "--output-format", "json", *resume]
    # "default" permission mode in -p: tools that need approval are denied, not prompted.
    # --tools limits built-ins to read-only ones even if user settings pre-approve Bash/Edit;
    # --strict-mcp-config (with no --mcp-config) loads no MCP servers.
    tools, mode = ("Read,Grep,Glob,Edit,Write", "acceptEdits") if write else ("Read,Grep,Glob", "default")
    # --setting-sources user: never load .claude/ settings or hooks from the (untrusted) project.
    argv = ["claude", "-p", "--output-format", "json", "--permission-mode", mode,
            "--tools", tools, "--strict-mcp-config", "--setting-sources", "user"]
    return argv + resume


def _parse_claude(stdout: str) -> AgentResult:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise AgentError(f"claude returned non-JSON output: {stdout[:200]!r}") from e
    if data.get("is_error"):
        raise AgentError(f"claude error: {str(data.get('result'))[:300]}")
    return AgentResult(data.get("result", ""), data.get("session_id"), _denied_tools(data))


def _denied_tools(data: dict) -> tuple[str, ...]:
    """The names (never the inputs: they can hold paths or secrets) of tools Claude Code refused."""
    names = [d.get("tool_name") for d in data.get("permission_denials") or [] if isinstance(d, dict)]
    return tuple(dict.fromkeys(n for n in names if isinstance(n, str) and TOOL_NAME_RE.match(n)))


def denial_note(denied: tuple[str, ...]) -> str:
    """Said plainly in the reply: the owner's own Claude Code rules blocked these, and nobody can
    approve a tool from a chat room. (Codex says so in its own reply.)"""
    if not denied:
        return ""
    shown = ", ".join(denied[:MAX_DENIED_SHOWN]) + (" …" if len(denied) > MAX_DENIED_SHOWN else "")
    return (f"\n\n⚠️ Your Claude Code permission settings didn't allow: {shown} (it can't ask you for "
            'approval from a chat room). To allow them, add them to "permissions": {"allow": [...]} '
            "in ~/.claude/settings.json.")


BACKENDS = {"codex": (_codex_argv, _parse_codex), "claude": (_claude_argv, _parse_claude)}


def agent_env(workdir: Path) -> dict:
    """The CLI's git must not climb out of its folder into an enclosing repo (loop checkouts still
    live in the app's rooms/): a checkout without .git would otherwise show our files as its "changes"."""
    return {**os.environ, "GIT_CEILING_DIRECTORIES": str(workdir.resolve().parent)}


async def run_turn(backend: str, workdir: Path, prompt: str, write: bool = False,
                   fresh: bool = False, owner: bool = False) -> str:
    """Run one turn in workdir, resuming that room's saved session unless fresh=True. owner=True
    only for a turn its owner's own message asked for (see the module docstring)."""
    argv_fn, parse_fn = BACKENDS[backend]
    workdir.mkdir(parents=True, exist_ok=True)
    state_file = workdir / SESSION_FILE
    state = {} if fresh else (json.loads(state_file.read_text()) if state_file.exists() else {})
    session_id = state.get(backend)
    if session_id is not None and not (isinstance(session_id, str) and SESSION_ID_RE.match(session_id)):
        session_id = None   # never pass a malformed id as a CLI positional; start a fresh session

    proc = await asyncio.create_subprocess_exec(
        *argv_fn(workdir, session_id, write, owner), cwd=workdir, env=agent_env(workdir),
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
    note = denial_note(result.denied)
    if not result.text.strip() and not note:
        raise AgentError(f"{backend} returned an empty reply")
    if not fresh and result.session_id and result.session_id != session_id:
        state[backend] = result.session_id
        tmp = state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, state_file)   # atomic: a crash can't leave a half-written state file
    return (result.text.strip() + note).strip()
