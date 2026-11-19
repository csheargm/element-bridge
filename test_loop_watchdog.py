"""Unit tests: the loop never waits forever. Run: .venv/bin/python -m unittest test_loop_watchdog"""
import asyncio
import time
import unittest
from unittest import mock

import github
import loop_progress
from loop import Loop

AGENT = "@b-agent:x"


class Watchdog(unittest.TestCase):
    def setUp(self):
        self.lp = Loop.__new__(Loop)
        self.lp.level = mock.AsyncMock(return_value="autonomous")   # dispatch checks the mode
        self.lp.state = {}
        self.lp.save = lambda: None
        self.lp.say = mock.AsyncMock()
        self.lp.open_issues = {}

    def item(self, stage, age_s, **kw):
        sent = time.time() - age_s
        item = {"room": "!r", "stage": stage, "seen_stage": stage, "since": sent, "builder": AGENT,
                "agent": AGENT, "body": "b", "task": {"kind": "build", "issue": 7}, "sent_at": sent,
                "sends": 1, "task_id": "OLD", "task_ids": ["OLD"], **kw}
        self.lp.state["7"] = item
        return item

    def run_watchdog(self, item):
        return asyncio.run(self.lp.watchdog("!r", "7", item))

    def test_answer_in_time_is_left_alone(self):
        item = self.item("building", 10)
        self.assertFalse(self.run_watchdog(item))
        self.lp.say.assert_not_called()

    def test_overdue_step_is_resent_with_a_new_task_id(self):
        item = self.item("building", loop_progress.STEP_TIMEOUT_S + 1)
        self.assertTrue(self.run_watchdog(item))
        self.assertEqual((item["stage"], item["sends"]), ("building", 2))
        self.assertNotEqual(item["task_id"], "OLD")
        self.assertEqual(item["task_ids"], ["OLD", item["task_id"]])   # the slow original still counts
        _, _, agent, task = self.lp.say.call_args_list[0].args
        self.assertEqual((agent, task["kind"]), (AGENT, "build"))

    def test_second_silence_stops_the_item(self):
        item = self.item("reviewing", loop_progress.STEP_TIMEOUT_S + 1, sends=2)
        self.assertTrue(self.run_watchdog(item))
        self.assertEqual(item["stage"], "stopped")
        self.assertIn("a person takes over", self.lp.say.call_args.args[1])

    def test_item_from_before_the_watchdog_stops_instead_of_guessing(self):
        item = self.item("building", loop_progress.STEP_TIMEOUT_S + 1)
        del item["task"]
        self.run_watchdog(item)
        self.assertEqual(item["stage"], "stopped")

    def gh(self, item, info):
        with mock.patch.object(github, "merge_ready", mock.AsyncMock(return_value=(False, info))):
            with mock.patch.object(self.lp, "level", mock.AsyncMock(return_value="autonomous")), \
                    mock.patch.object(github, "run_json", mock.AsyncMock(return_value=[])):
                self.lp.client = mock.Mock(rooms={"!r": None})
                asyncio.run(self.lp.tick())

    def test_github_blocking_too_long_stops(self):
        item = self.item("approved", loop_progress.GITHUB_WAIT_S + 1, pr=9)
        self.gh(item, {"state": "OPEN", "mergeable": "UNKNOWN", "reviewDecision": "APPROVED"})
        self.assertEqual(item["stage"], "stopped")

    def test_github_finishing_at_the_deadline_is_not_stopped(self):
        item = self.item("merge_sent", loop_progress.GITHUB_WAIT_S + 1, pr=9)
        self.gh(item, {"state": "MERGED"})
        self.assertEqual(item["stage"], "done")

    def test_original_result_still_counts_after_a_resend(self):
        from types import SimpleNamespace
        item = self.item("building", 5, task_ids=["OLD", "NEW"], task_id="NEW")
        result = {"kind": "build", "issue": 7, "already_done": True, "task_id": "OLD"}
        self.lp.agents_in = lambda room: [AGENT, "@r-agent:x"]
        asyncio.run(self.lp.on_result(SimpleNamespace(room_id="!r"), AGENT, result))
        self.assertEqual(item["stage"], "verifying")

    def test_person_stages_never_time_out(self):
        for stage in ("ready", "close_ready"):
            item = self.item(stage, 10 * loop_progress.GITHUB_WAIT_S)
            self.assertFalse(self.run_watchdog(item))

    def test_new_stage_restarts_the_clock(self):
        item = self.item("approved", loop_progress.GITHUB_WAIT_S + 1, seen_stage="reviewing")
        self.assertFalse(self.run_watchdog(item))
        self.assertEqual(item["seen_stage"], "approved")

    def test_a_step_decided_while_stopped_is_held_then_sent_on_resume(self):
        """GPT review: a result arriving after "stop" used to send the next step anyway."""
        item = self.item("reviewing", 0)
        with mock.patch.object(self.lp, "level", mock.AsyncMock(return_value="off")):
            asyncio.run(self.lp.dispatch("!r", item, AGENT, "review it", {"kind": "review", "issue": 7}))
        self.assertTrue(item["held"])
        self.lp.say.assert_not_called()
        self.gh(item, {"state": "OPEN"})                      # tick with the room back on
        self.assertNotIn("held", item)
        self.assertEqual(self.lp.say.call_args.args[1], "review it")

    def test_merge_conflict_stops_instead_of_blocking_the_queue(self):
        item = self.item("approved", 5, pr=9, sha="abc")
        info = {"state": "OPEN", "mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY", "reviewDecision": "APPROVED"}
        with mock.patch.object(github, "merge_ready", mock.AsyncMock(return_value=(False, info))):
            asyncio.run(self.lp.advance("!r", "7", item, "autonomous"))
        self.assertEqual(item["stage"], "stopped")
        self.assertIn("merge conflicts", self.lp.say.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
