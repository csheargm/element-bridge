"""GitHub access for agents: every call runs the `gh` CLI (no shell) under that agent's own
identity, i.e. its own GH_CONFIG_DIR in .gh/<agent>/ (git-ignored; tokens stay in the keyring).

GitHub — not this code — is the merge gate: a PR is merge-ready only when GitHub itself
reports it MERGEABLE + CLEAN + APPROVED, which needs the repo's branch protection / ruleset.
Without required reviews GitHub never reports APPROVED, so the loop fails closed.
"""
import asyncio
import json
import os
import re
from pathlib import Path

import lifecycle

ROOT = Path(__file__).parent
REPO = os.environ.get("LOOP_REPO", "your-name/hagency-demo-sandbox")
LABEL = "agent-ok"
GH_BIN = os.environ.get("GH_BIN", "gh")          # tests point this at tests/fake_gh.py
IDENTITY_ROOT = Path(os.environ.get("GH_IDENTITY_ROOT", ROOT / ".gh"))
CMD_TIMEOUT_S = 120

# Event content keys shared by loop.py and the bridges.
TASK_KEY = "xyz.hagencylabs.task"
RESULT_KEY = "xyz.hagencylabs.result"
LEVEL_EVENT = "xyz.hagencylabs.loop"
STATUS_EVENT = "xyz.hagencylabs.status"   # @pm's live project status (the board text), for agents to read
LEVELS = ("off", "assist", "autonomous")
PM_BOT = "pm"                    # Matrix localpart of the project coordinator, @pm (loop.py)


# "PR #12" / "#12" in loop lines → links to the loop repo (markdown; not inside words or URLs).
REF_RE = re.compile(r"(?<![\w/\[])(PR )?#(\d+)\b")


def linkify(text: str) -> str:
    """Markdown with each issue/PR reference linked to github.com/REPO."""
    def link(m: re.Match) -> str:
        kind = "pull" if m.group(1) else "issues"
        return f"[{m.group(0)}](https://github.com/{REPO}/{kind}/{m.group(2)})"
    return REF_RE.sub(link, text)


class GitHubError(RuntimeError):
    pass


def identity_dir(agent: str) -> Path | None:
    """The agent's gh config dir, or None if the person hasn't signed that identity in."""
    d = IDENTITY_ROOT / agent
    return d if (d / "hosts.yml").exists() else None


# Never let ambient overrides pick a different account/host than the agent's own config dir.
SCRUBBED_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST")
# Host-side git must not run user/system config (filters, fsmonitor, hooks) on agent-edited trees.
ISOLATED_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


async def run(args: list[str], agent: str | None = None, cwd: Path | None = None,
              program: str | None = None, extra_env: dict | None = None) -> str:
    """Run gh (or git) as `agent`; agent=None uses the ambient login (read-only use only)."""
    env = dict(os.environ)
    if agent is not None:
        d = identity_dir(agent)
        if d is None:
            raise GitHubError(f"{agent} has no GitHub identity (.gh/{agent}/ not signed in)")
        env["GH_CONFIG_DIR"] = str(d)
        for k in SCRUBBED_ENV:
            env.pop(k, None)
    env.update(extra_env or {})
    argv = [program or GH_BIN, *args]
    proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await lifecycle.communicate(proc, None, CMD_TIMEOUT_S)
    except asyncio.TimeoutError as e:
        raise GitHubError(f"{argv[0]} {args[0]} timed out") from e
    if proc.returncode != 0:
        msg = (err.decode(errors="replace").strip() or out.decode(errors="replace").strip())[-300:]
        raise GitHubError(f"{Path(argv[0]).name} {' '.join(args[:2])} failed: {msg}")
    return out.decode(errors="replace")


async def run_json(args: list[str], agent: str | None = None) -> object:
    return json.loads(await run(args, agent))


async def git(args: list[str], agent: str, cwd: Path) -> str:
    """git with gh as the credential helper, so pushes use the agent's identity."""
    helper = f"!{GH_BIN} auth git-credential"
    safe = ["-c", "credential.helper=", "-c", f"credential.helper={helper}",
            "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false"]
    return await run([*safe, *args], agent, cwd, program="git", extra_env=ISOLATED_GIT_ENV)


async def merge_ready(pr: int, agent: str | None = None) -> tuple[bool, dict]:
    """GitHub's own verdict on whether PR can merge now (needs required-review protection)."""
    info = await run_json(["pr", "view", str(pr), "-R", REPO, "--json",
                           "state,mergeable,mergeStateStatus,reviewDecision,headRefOid"], agent)
    ok = (info.get("state") == "OPEN" and info.get("mergeable") == "MERGEABLE"
          and info.get("mergeStateStatus") == "CLEAN" and info.get("reviewDecision") == "APPROVED")
    return ok, info
