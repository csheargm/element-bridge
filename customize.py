"""Your own version of Hagency Rooms, made by your agent from your private room (bridge_custom.py).

Three folders per owner under custom/<owner>/ (git-ignored):
  work/  a git worktree on branch custom/<owner>: the agent edits only here, in its sandbox.
  live/  a pinned checkout of the commit the owner chose with "Use it myself"; supervise.py runs
         the owner's bridge from here. Agent edits in work/ never reach live/ without a new click.
Nothing the agent wrote runs on this computer before the owner clicks: "Use it myself" runs the
tests and switches only if they pass; "Send as pull request" is tested by GitHub, not here.
State (data/custom-use.json) is read by supervise.py and @pm, which both run the official code,
so "go back" works even when a custom version is broken.
"""
import asyncio
import json
import os
import re
from pathlib import Path

import github
import lifecycle
from saved_dict import SavedDict

ROOT = Path(__file__).parent
COPIES = ROOT / "custom"
USE_FILE = ROOT / "data" / "custom-use.json"     # owner → commit their bridge runs (absent: official)
UPSTREAM = os.environ.get("HAGENCY_UPSTREAM", "csheargm/hagency-rooms")
PY = str(ROOT / ".venv" / "bin" / "python")
TEST_TIMEOUT_S = 600
MAX_DIFF_FILES = 15
# The checks that keep the agent safe: a change here is flagged in red before anyone clicks.
SENSITIVE = {"bridge.py", "bridge_voice.py", "bridge_custom.py", "agents.py", "capabilities.py", "redact.py",
             "safe_fetch.py", "supervise.py", "customize.py", "lifecycle.py", "ownership.py", "github.py",
             "requirements.txt", ".gitignore", "start.sh"}
# Git-ignored things the live copy shares with the main folder (passwords, sessions, state, identities).
SHARED = (".env", ".venv", ".gh", "data", "rooms", "logs", "agents.json")
OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
GO_BACK = re.compile(r"\b(go|switch) back to the official\b", re.I)   # typed to the agent or to @pm


class CustomError(RuntimeError):
    pass


def _owner(owner: str) -> str:
    if not OWNER_RE.match(owner):
        raise CustomError(f"bad owner name {owner!r}")
    return owner


def work_dir(owner: str) -> Path:
    return COPIES / _owner(owner) / "work"


def live_dir(owner: str) -> Path:
    return COPIES / _owner(owner) / "live"


def in_use() -> dict[str, str]:
    """owner → commit, for everyone running their own version (fresh read: other processes write it)."""
    try:
        data = json.loads(USE_FILE.read_text())
    except (OSError, ValueError):
        return {}                             # no file (nobody customised) or damaged: everyone official
    return {o: c for o, c in data.items() if isinstance(c, str) and OWNER_RE.match(o)}


def code_dir(owner: str) -> Path:
    """Where the owner's bridge runs from: their pinned version, or the official folder."""
    return live_dir(owner) if owner in in_use() and (live_dir(owner) / "bridge.py").exists() else ROOT


def go_back(owner: str) -> bool:
    """Run the official version again. True if anything changed."""
    use = SavedDict(USE_FILE)
    if owner not in use:
        return False
    use.pop(owner)
    return True


async def _git(args: list[str], cwd: Path) -> str:
    """Local git on agent-edited trees: no hooks, filters or user config (github.ISOLATED_GIT_ENV)."""
    safe = ["-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", "-c", "commit.gpgsign=false",
            "-c", "user.name=Hagency Rooms", "-c", "user.email=agent@hagency.invalid"]
    return (await github.run([*safe, *args], None, cwd, program="git", extra_env=github.ISOLATED_GIT_ENV)).strip()


async def prepare(owner: str) -> Path:
    """The owner's private copy, created from the official version the first time."""
    work = work_dir(owner)
    if not (work / ".git").exists():
        work.parent.mkdir(parents=True, exist_ok=True)
        await _git(["worktree", "add", "--force", "-B", f"custom/{owner}", str(work), "HEAD"], ROOT)
        base = await _git(["rev-parse", "HEAD"], work)
        (work.parent / "base").write_text(base)
        return work
    official = await _git(["rev-parse", "HEAD"], ROOT)
    if (work.parent / "base").read_text().strip() != official:   # the official version moved on: follow it
        try:
            await _git(["rebase", "-q", official], work)
        except github.GitHubError as e:
            await _git(["rebase", "--abort"], work)
            raise CustomError("your earlier changes clash with the new official version; "
                              "discard them (🗑️) and ask again") from e
        (work.parent / "base").write_text(official)
    return work


async def commit(owner: str, request: str) -> str | None:
    """Commit what the agent changed in the copy; None if it changed nothing."""
    work = work_dir(owner)
    await _git(["add", "-A"], work)
    if not await _git(["status", "--porcelain"], work):
        return None
    await _git(["commit", "-q", "-m", f"custom: {request.splitlines()[0][:72]}"], work)
    return await _git(["rev-parse", "HEAD"], work)


async def changed_files(owner: str) -> list[str]:
    base = (work_dir(owner).parent / "base").read_text().strip()
    out = await _git(["diff", "--name-only", f"{base}..HEAD"], work_dir(owner))
    return [f for f in out.splitlines() if f]


def sensitive(files: list[str]) -> list[str]:
    return [f for f in files if Path(f).name in SENSITIVE or f.startswith((".github/", "deploy/"))]


async def run_tests(cwd: Path) -> tuple[bool, str]:
    """The unit tests of that version; (passed, last line of the summary)."""
    proc = await asyncio.create_subprocess_exec(PY, "-m", "unittest", "discover", "-p", "test_*.py", cwd=cwd,
                                                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = await lifecycle.communicate(proc, None, TEST_TIMEOUT_S)
    except asyncio.TimeoutError:
        return False, f"tests took longer than {TEST_TIMEOUT_S // 60} minutes"
    lines = [ln for ln in out.decode(errors="replace").splitlines() if ln.strip()]
    ran = next((ln for ln in reversed(lines) if ln.startswith("Ran ")), "no tests ran")
    return proc.returncode == 0, f"{ran} — {lines[-1] if lines else 'no output'}"


async def _checkout(path: Path, sha: str) -> None:
    if (path / ".git").exists():
        await _git(["checkout", "-q", "--force", "--detach", sha], path)
    else:
        await _git(["worktree", "add", "--force", "--detach", str(path), sha], ROOT)


async def use(owner: str, sha: str) -> tuple[bool, str]:
    """Test that commit and, if it passes, pin it in live/ and switch the owner's bridge to it."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise CustomError("bad commit")
    trial = COPIES / _owner(owner) / "trial"
    await _checkout(trial, sha)           # its own folder: no passwords or state, and live/ keeps running
    ok, summary = await run_tests(trial)
    if not ok:
        return False, summary
    live = live_dir(owner)
    await _checkout(live, sha)
    for name in SHARED:
        link, target = live / name, ROOT / name
        if target.exists() and not link.is_symlink():
            link.symlink_to(target)
    SavedDict(USE_FILE)[owner] = sha
    return True, summary


async def send_pr(owner: str, sha: str, title: str, body: str) -> str:
    """Open a pull request to the official repo from the owner's agent's GitHub identity."""
    agent = f"{owner}-agent"
    login = (await github.run(["api", "user", "-q", ".login"], agent)).strip()
    up_owner, name = UPSTREAM.split("/")
    if login.lower() != up_owner.lower():
        await github.run(["repo", "fork", UPSTREAM, "--clone=false"], agent)   # no-op when it exists
    branch = f"custom/{owner}-{sha[:7]}"
    await github.git(["push", f"https://github.com/{login}/{name}.git", f"{sha}:refs/heads/{branch}"],
                     agent, work_dir(owner))
    out = await github.run(["pr", "create", "-R", UPSTREAM, "--head", f"{login}:{branch}",
                            "--title", title[:100], "--body", body], agent)
    url = next((w for w in out.split() if w.startswith("https://github.com/")), "")
    if not url:
        raise CustomError("GitHub didn't return the pull request's link")
    return url


async def discard(owner: str, sha: str) -> bool:
    """Undo that change in the private copy, if it's still the newest one there."""
    work = work_dir(owner)
    if await _git(["rev-parse", "HEAD"], work) != sha:
        return False
    await _git(["reset", "-q", "--hard", "HEAD~1"], work)
    return True
