"""Unit tests for the loop's trust rules. Run: .venv/bin/python -m unittest test_loop_rules"""
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import github
import tasks
from bridge import Bridge

ANNA, BETTY = "@anna:bridge.localhost", "@betty:bridge.localhost"
AGENT, BOT = "@anna-agent:bridge.localhost", f"@{github.LOOP_BOT}:bridge.localhost"


def room(members, invited=()):
    users = {m: None for m in [*members, *invited]}
    return SimpleNamespace(room_id="!r", users=users, invited_users={m: None for m in invited})


class BridgeTaskTrust(unittest.TestCase):
    def setUp(self):
        self.b = Bridge("anna", "codex")
        self.b.run_task = mock.Mock(return_value=None)
        self.started = []
        patcher = mock.patch("asyncio.create_task", side_effect=lambda c: self.started.append(c) or mock.Mock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def task_from(self, sender, task, r=None):
        ev = SimpleNamespace(sender=sender, event_id=f"$e{len(self.started)}{sender}{task.get('agent')}")
        self.b.on_task(r or room([ANNA, AGENT, BOT]), ev, task)

    def test_loop_bot_task_for_me_runs(self):
        self.task_from(BOT, {"kind": "build", "agent": AGENT})
        self.assertEqual(len(self.started), 1)

    def test_redelivered_task_runs_once(self):
        ev = SimpleNamespace(sender=BOT, event_id="$same")
        for _ in range(2):
            self.b.on_task(room([ANNA, AGENT, BOT]), ev, {"kind": "build", "agent": AGENT})
        self.assertEqual(len(self.started), 1)

    def test_task_from_a_human_is_ignored(self):
        self.task_from(ANNA, {"kind": "build", "agent": AGENT})
        self.assertEqual(self.started, [])

    def test_task_for_another_agent_is_ignored(self):
        self.task_from(BOT, {"kind": "build", "agent": "@betty-agent:bridge.localhost"})
        self.assertEqual(self.started, [])

    def test_task_from_invited_not_joined_bot_is_ignored(self):
        self.task_from(BOT, {"kind": "build", "agent": AGENT}, room([ANNA, AGENT], invited=[BOT]))
        self.assertEqual(self.started, [])


class TaskRules(unittest.TestCase):
    def test_wrong_repo_refused(self):
        r = asyncio.run(tasks.handle("anna-agent", "codex", Path("/tmp"), {"kind": "merge", "repo": "evil/repo",
                                                                          "issue": 1, "pr": 2}))
        self.assertIn("not this bridge's loop repo", r["error"])

    def test_unknown_kind_refused(self):
        r = asyncio.run(tasks.handle("anna-agent", "codex", Path("/tmp"), {"kind": "rm", "repo": github.REPO}))
        self.assertIn("unknown task kind", r["error"])

    def test_no_identity_means_no_github_writes(self):
        with mock.patch.object(github, "IDENTITY_ROOT", Path("/nonexistent")):
            with self.assertRaises(github.GitHubError):
                asyncio.run(github.run(["api", "user"], "anna-agent"))

    def test_verdict_must_be_exact_last_line(self):
        self.assertEqual(tasks.parse_verdict("looks fine\nVERDICT: APPROVE\n"), "APPROVE")
        self.assertEqual(tasks.parse_verdict("VERDICT: APPROVE\nbut actually no"), "REQUEST_CHANGES")
        self.assertEqual(tasks.parse_verdict("I would say VERDICT: APPROVE"), "REQUEST_CHANGES")
        self.assertEqual(tasks.parse_verdict(""), "REQUEST_CHANGES")

    def test_git_runs_without_user_config_or_hooks(self):
        seen = {}

        async def fake_run(args, agent=None, cwd=None, program=None, extra_env=None):
            seen.update(args=args, env=extra_env)
            return ""
        with mock.patch.object(github, "run", fake_run):
            asyncio.run(github.git(["status"], "anna-agent", Path("/tmp")))
        self.assertEqual(seen["env"]["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertIn("core.fsmonitor=false", seen["args"])
        self.assertTrue(any(a.startswith("core.hooksPath=") for a in seen["args"]))

    def test_ambient_token_overrides_scrubbed(self):
        self.assertTrue({"GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GH_HOST"} <= set(github.SCRUBBED_ENV))

    def test_merge_ready_needs_github_approval(self):
        async def fake(args, agent=None):
            return {"state": "OPEN", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": ""}
        with mock.patch.object(github, "run_json", fake):
            ok, _ = asyncio.run(github.merge_ready(5))
        self.assertFalse(ok, "no required-review protection → never merge-ready")


class LoopModerator(unittest.TestCase):
    def check(self, sender, version, pl=0):
        import loop
        lp = loop.Loop.__new__(loop.Loop)
        state = loop.RoomGetStateResponse([{"type": "m.room.create", "sender": ANNA, "state_key": "",
                                            "content": {"room_version": version}}], "!r")
        lp.client = SimpleNamespace(room_get_state=mock.AsyncMock(return_value=state))
        r = SimpleNamespace(room_id="!r", power_levels=SimpleNamespace(get_user_level=lambda u: pl))
        return asyncio.run(lp.is_moderator(r, sender))

    def test_v12_creator_is_moderator_without_pl_entry(self):
        self.assertTrue(self.check(ANNA, "12"))

    def test_non_creator_without_pl_is_not(self):
        self.assertFalse(self.check(BETTY, "12"))

    def test_old_room_creator_needs_pl(self):
        self.assertFalse(self.check(ANNA, "10"))

    def test_pl_50_is_moderator(self):
        self.assertTrue(self.check(BETTY, "12", pl=50))


class LoopResults(unittest.TestCase):
    def setUp(self):
        import loop
        self.lp = loop.Loop.__new__(loop.Loop)
        self.lp.state = {"7": {"room": "!r", "stage": "reviewing", "builder": "@b-agent:x",
                               "reviewer": "@r-agent:x", "pr": 9, "task_id": "T"}}
        self.lp.save = lambda: None
        self.lp.say = mock.AsyncMock()

    def deliver(self, sender, result):
        asyncio.run(self.lp.on_result(SimpleNamespace(room_id="!r"), sender, result))
        return self.lp.state["7"]["stage"]

    def approval(self, **kw):
        return {"kind": "review", "issue": 7, "pr": 9, "sha": "abc", "verdict": "approve", "task_id": "T", **kw}

    def test_reviewer_approval_advances(self):
        self.assertEqual(self.deliver("@r-agent:x", self.approval()), "approved")
        self.assertEqual(self.lp.state["7"]["sha"], "abc")

    def test_approval_from_builder_is_ignored(self):
        self.assertEqual(self.deliver("@b-agent:x", self.approval()), "reviewing")

    def test_approval_for_old_task_id_is_ignored(self):
        self.assertEqual(self.deliver("@r-agent:x", self.approval(task_id="OLD")), "reviewing")

    def test_approval_for_another_pr_is_ignored(self):
        self.assertEqual(self.deliver("@r-agent:x", self.approval(pr=10)), "reviewing")

    def test_build_result_must_be_builders_own_branch(self):
        self.lp.state["7"].update(stage="building")
        async def fake(args, agent=None):
            return {"headRefName": "agent/x-agent/issue-7", "isCrossRepository": False, "state": "OPEN"}
        with mock.patch.object(github, "run_json", fake):
            stage = self.deliver("@b-agent:x", {"kind": "build", "issue": 7, "pr": 9, "task_id": "T"})
        self.assertEqual(stage, "stopped")

    def test_late_build_result_is_ignored(self):
        self.assertEqual(self.deliver("@b-agent:x", {"kind": "build", "issue": 7, "error": "x"}), "reviewing")

    def test_stale_events_dropped(self):
        from bridge import is_stale
        self.assertTrue(is_stale(SimpleNamespace(server_timestamp=1_000), 100_000))
        self.assertFalse(is_stale(SimpleNamespace(server_timestamp=99_000), 100_000))


if __name__ == "__main__":
    unittest.main()
