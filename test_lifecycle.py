"""Unit tests for restart hand-off. Run: .venv/bin/python -m unittest test_lifecycle"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import lifecycle


class FakeClient:
    def __init__(self):
        self.callbacks = []

    def add_response_callback(self, cb, kind):
        self.callbacks.append(cb)


class HandOff(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        patcher = mock.patch.object(lifecycle, "HANDOFF_DIR", Path(d))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_mid_batch_stop_keeps_the_unhandled_rest_of_the_batch(self):
        """nio advances next_batch before callbacks; we save the last FULLY handled batch instead."""
        client = FakeClient()
        point = lifecycle.SyncPoint(client)
        asyncio.run(client.callbacks[0](SimpleNamespace(next_batch="B1")))   # batch 1 fully handled
        self.assertTrue(point.first_time("$a"))                             # batch 2: $a handled, then stop
        lifecycle.save_handoff("x", point)
        data = lifecycle.take_handoff("x")
        self.assertEqual((data["token"], data["handled"]), ("B1", ["$a"]))  # resume at B1, skip $a

    def test_resumed_process_skips_events_already_handled(self):
        point = lifecycle.SyncPoint(FakeClient(), ["$a"])
        self.assertFalse(point.first_time("$a"))
        self.assertTrue(point.first_time("$b"))
        self.assertFalse(point.first_time("$b"))

    def test_finished_batch_clears_handled_ids(self):
        client = FakeClient()
        point = lifecycle.SyncPoint(client, ["$a"])
        asyncio.run(client.callbacks[0](SimpleNamespace(next_batch="B2")))
        self.assertEqual((point.token, list(point.handled)), ("B2", []))

    def test_handoff_survives_until_resume_succeeds(self):
        point = lifecycle.SyncPoint(FakeClient())
        point.token = "T"
        lifecycle.save_handoff("x", point)
        self.assertIsNotNone(lifecycle.take_handoff("x"))   # a process killed during startup...
        self.assertIsNotNone(lifecycle.take_handoff("x"))   # ...leaves it for the next one
        lifecycle.drop_handoff("x")
        self.assertIsNone(lifecycle.take_handoff("x"))

    def test_handoff_outlives_a_full_drain(self):
        self.assertGreater(lifecycle.HANDOFF_MAX_AGE_S, lifecycle.DRAIN_TIMEOUT_S)

    def test_handled_ids_are_not_evicted_within_a_batch(self):
        point = lifecycle.SyncPoint(FakeClient())
        point.first_time("$question")
        for i in range(5000):
            point.first_time(f"$chatter{i}")
        self.assertFalse(point.first_time("$question"))

    def test_old_handoff_means_cold_start(self):
        path = lifecycle.HANDOFF_DIR / "x.json"
        path.write_text(json.dumps({"token": "T", "handled": [], "ms": 0}))
        self.assertIsNone(lifecycle.take_handoff("x"))

    def test_no_token_writes_nothing(self):
        lifecycle.save_handoff("x", lifecycle.SyncPoint(FakeClient()))
        self.assertIsNone(lifecycle.take_handoff("x"))

    def test_handoff_file_is_private(self):
        point = lifecycle.SyncPoint(FakeClient())
        point.token = "T"
        lifecycle.save_handoff("x", point)
        self.assertEqual((lifecycle.HANDOFF_DIR / "x.json").stat().st_mode & 0o777, 0o600)
        self.assertLess(time.time() * 1000 - json.loads((lifecycle.HANDOFF_DIR / "x.json").read_text())["ms"], 5000)


if __name__ == "__main__":
    unittest.main()
