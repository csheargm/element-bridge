"""One-time GitHub setup for the loop: check every agent identity, and apply the merge-gate ruleset.

  python github_setup.py check [agent ...]   # default: anna-agent betty-agent
  python github_setup.py ruleset             # as the repo owner (your normal `gh` login)

Never reads or prints tokens: it only asks GitHub "who am I" and "what may I do" per identity.
See docs/GITHUB_SETUP.md for the full walkthrough.
"""
import asyncio
import json
import sys

import github
from github import REPO, GitHubError, run, run_json

DEFAULT_AGENTS = ("anna-agent", "betty-agent")
RULESET_FILE = github.ROOT / "github-ruleset.json"
RULESET_NAME = json.loads(RULESET_FILE.read_text())["name"]


def evaluate(identities: dict[str, dict], main_rules: list[dict]) -> list[str]:
    """identities: agent -> {"login", "push"} (or {"error"}); main_rules: GET rules/branches/main."""
    problems = []
    for agent, ident in identities.items():
        if "error" in ident:
            problems.append(f"{agent}: not signed in ({ident['error']})")
        elif not ident.get("push"):
            problems.append(f"{agent} ({ident['login']}): no Write access to {REPO} — add it as a collaborator")
    logins = [i["login"] for i in identities.values() if "login" in i]
    if len(set(logins)) != len(logins):
        problems.append(f"agents share a GitHub account ({logins}); each needs its own, or nobody can approve")
    pr_rules = [r for r in main_rules if r.get("type") == "pull_request"]
    if not any(r.get("parameters", {}).get("required_approving_review_count", 0) >= 1 for r in pr_rules):
        problems.append(f"main on {REPO} doesn't require an approving review — run `github_setup.py ruleset`")
    return problems


async def identity(agent: str) -> dict:
    try:
        login = (await run_json(["api", "user"], agent))["login"]
        perms = (await run_json(["api", f"repos/{REPO}"], agent)).get("permissions", {})
        return {"login": login, "push": bool(perms.get("push"))}
    except GitHubError as e:
        return {"error": str(e)[:200]}   # reported as a problem by evaluate()


async def check(agents: list[str]) -> int:
    identities = {a: await identity(a) for a in agents}
    for a, i in identities.items():
        print(f"{a:12} {i.get('login', '-'):20} {'write' if i.get('push') else 'NO WRITE' if 'login' in i else i['error']}")
    rules = await run_json(["api", f"repos/{REPO}/rules/branches/main"])
    problems = evaluate(identities, rules)
    print("\n".join(f"✗ {p}" for p in problems) or f"✓ {REPO} ready for the loop")
    return 1 if problems else 0


async def apply_ruleset() -> int:
    existing = await run_json(["api", f"repos/{REPO}/rulesets"])
    if any(r.get("name") == RULESET_NAME for r in existing):
        print(f"✓ ruleset '{RULESET_NAME}' already on {REPO}")
        return 0
    await run(["api", "-X", "POST", f"repos/{REPO}/rulesets", "--input", str(RULESET_FILE)])
    print(f"✓ ruleset '{RULESET_NAME}' created on {REPO}")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["check"]:
        return asyncio.run(check(argv[1:] or list(DEFAULT_AGENTS)))
    if argv == ["ruleset"]:
        return asyncio.run(apply_ruleset())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
