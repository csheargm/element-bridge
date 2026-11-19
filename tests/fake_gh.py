#!/usr/bin/env python3
"""Minimal stand-in for the `gh` CLI, for test_loop_e2e.py. NOT used in production.

State lives in $FAKE_GH_STATE (JSON); the "GitHub" repo is a local bare git repo. The caller's
identity is the basename of $GH_CONFIG_DIR (like one gh login per agent). Mirrors the GitHub
rules the loop relies on: you can't approve your own PR, and with required reviews
(state["protected"]) reviewDecision is APPROVED only after a non-author approval.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

STATE = Path(os.environ["FAKE_GH_STATE"])


def load():
    return json.loads(STATE.read_text())


def save(s):
    STATE.write_text(json.dumps(s, indent=1))


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def login():
    d = os.environ.get("GH_CONFIG_DIR")
    return Path(d).name if d else "ambient"


def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


def head_sha(s, pr):
    return git("--git-dir", s["remote"], "rev-parse", pr["head"]).strip()


def fields(a):
    return dict(a[i + 1].split("=", 1) for i, x in enumerate(a) if x == "-f")


def decision(s, pr):
    if not s.get("protected"):
        return ""
    last = {}
    for r in pr["reviews"]:
        last[r["by"]] = r["state"]
    if "CHANGES_REQUESTED" in last.values():
        return "CHANGES_REQUESTED"
    return "APPROVED" if "APPROVED" in last.values() else "REVIEW_REQUIRED"


def main(a):
    s = load()
    cmd = a[:2]
    if cmd == ["api", "user"]:
        print(json.dumps({"login": login()}))
    elif cmd == ["repo", "clone"]:
        git("clone", "--quiet", s["remote"], a[3])
    elif cmd == ["issue", "list"]:
        print(json.dumps([{"number": int(n), "title": i["title"]} for n, i in s["issues"].items()
                          if i["state"] == "OPEN" and opt(a, "--label") in i["labels"]]))
    elif cmd == ["issue", "view"]:
        i = s["issues"][a[2]]
        print(json.dumps({**i, "labels": [{"name": lb} for lb in i["labels"]]}))
    elif cmd == ["pr", "create"]:
        n = s["next"]
        s["next"] += 1
        s["prs"][str(n)] = {"head": opt(a, "--head"), "title": opt(a, "--title"), "author": login(),
                            "reviews": [], "state": "OPEN"}
        save(s)
        print(f"https://github.com/{opt(a, '-R')}/pull/{n}")
    elif cmd == ["pr", "diff"]:
        print(git("--git-dir", s["remote"], "diff", f"main...{s['prs'][a[2]]['head']}"))
    elif a[0] == "api" and a[-1:] != ["user"] and "/reviews" in " ".join(a):
        n = [x for x in a if "/pulls/" in x][0].split("/pulls/")[1].split("/")[0]
        pr, f = s["prs"][n], fields(a)
        if f["event"] == "APPROVE" and login() == pr["author"]:
            sys.exit("gh: Can not approve your own pull request (HTTP 422)")
        if f["commit_id"] != head_sha(s, pr):
            sys.exit("gh: commit_id is not the PR head (test expects reviews pinned to head)")
        pr["reviews"].append({"by": login(), "state": "APPROVED" if f["event"] == "APPROVE" else "CHANGES_REQUESTED"})
        save(s)
    elif cmd == ["pr", "view"]:
        pr = s["prs"][a[2]]
        dec = decision(s, pr)
        clean = pr["state"] == "OPEN" and (dec == "APPROVED" or not s.get("protected"))
        print(json.dumps({"state": pr["state"], "mergeable": "MERGEABLE", "reviewDecision": dec,
                          "mergeStateStatus": "CLEAN" if clean else "BLOCKED", "headRefOid": head_sha(s, pr),
                          "headRefName": pr["head"], "isCrossRepository": False}))
    elif cmd == ["pr", "merge"]:
        pr = s["prs"][a[2]]
        if s.get("protected") and decision(s, pr) != "APPROVED":
            sys.exit("Pull request is not mergeable: the base branch policy prohibits the merge")
        if opt(a, "--match-head-commit") != head_sha(s, pr):
            sys.exit("Head branch was modified. Review and try the merge again.")
        with tempfile.TemporaryDirectory() as d:
            git("clone", "--quiet", s["remote"], d)
            git("merge", "--squash", f"origin/{pr['head']}", cwd=d)
            git("-c", "user.name=fake", "-c", "user.email=f@x", "commit", "--quiet", "-m", pr["title"], cwd=d)
            git("push", "--quiet", "origin", "main", cwd=d)
        pr["state"] = "MERGED"
        save(s)
    else:
        sys.exit(f"fake_gh: unsupported {a[:3]}")


if __name__ == "__main__":
    main(sys.argv[1:])
