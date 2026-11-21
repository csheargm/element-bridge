"""Unit tests: the Windows keep-alive (connect_win.py) that the Scheduled Task runs.
Run: .venv/bin/python -m unittest test_connect_win"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import connect_win

CREDS = {"homeserver": "https://matrix.example.org", "user_id": "@anna-agent:matrix.example.org",
         "password": "s3cret-pw", "owner": "anna", "agent": "claude"}


def home_with(creds: dict | None = CREDS) -> Path:
    home = Path(tempfile.mkdtemp())
    if creds is not None:
        (home / "credentials.json").write_text(json.dumps(creds))
    return home


class FakeProc:
    def __init__(self, code: int):
        self.pid, self.code = 4242, code

    def wait(self):
        return self.code


class KeepRunning(unittest.TestCase):
    def run_it(self, home: Path, runs: int, lifetimes: list[float]):
        calls, sleeps, ticks = [], [], iter([t for life in lifetimes for t in (0, life)])

        def spawn(argv, **kw):
            calls.append((argv, kw))
            kw["stdout"].write(b"bridge output\n")
            return FakeProc(1)
        connect_win.keep_running(home, runs=runs, spawn=spawn, sleep=sleeps.append, clock=lambda: next(ticks))
        return calls, sleeps

    def test_runs_the_bridge_with_the_sign_in_by_file_not_on_the_command_line(self):
        home = home_with()
        (argv, kw), = self.run_it(home, 1, [10])[0]
        self.assertEqual(argv[1:], [str(connect_win.APP / "bridge.py"), "anna", "--agent", "claude"])
        self.assertNotIn("s3cret-pw", " ".join(argv))
        self.assertEqual(kw["env"]["HAGENCY_CREDENTIALS"], str(home / "credentials.json"))
        self.assertEqual((kw["cwd"], kw["stdin"], kw["stderr"]), (str(connect_win.APP), subprocess.DEVNULL, subprocess.STDOUT))
        self.assertEqual((home / "agent.pid").read_text().split()[1], "4242")
        log = (home / "agent.log").read_text()
        self.assertIn("bridge output", log)
        self.assertIn("the agent stopped (exit 1)", log)

    def test_restarts_with_a_growing_wait_that_resets_after_a_good_run(self):
        _, sleeps = self.run_it(home_with(), 5, [1, 1, 1, connect_win.HEALTHY_RUN_S, 1])
        self.assertEqual(sleeps, [5, 10, 20, 5, 10])

    def test_the_wait_never_passes_the_cap(self):
        delay = 0.0
        for _ in range(20):
            delay = connect_win.next_delay(delay, 0)
        self.assertEqual(delay, connect_win.MAX_DELAY_S)

    def test_no_sign_in_stops_with_a_reason_in_the_log(self):
        home = home_with(None)
        with mock.patch.object(sys, "argv", ["connect_win.py", "--home", str(home)]), self.assertRaises(SystemExit):
            connect_win.main()
        self.assertIn("credentials file", (home / "agent.log").read_text())

    def test_a_big_log_is_kept_once_and_started_over(self):
        home = home_with()
        (home / "agent.log").write_bytes(b"x" * (connect_win.LOG_MAX_BYTES + 1))
        self.run_it(home, 1, [10])
        self.assertEqual((home / "agent.log.1").stat().st_size, connect_win.LOG_MAX_BYTES + 1)
        self.assertLess((home / "agent.log").stat().st_size, 1000)


class Names(unittest.TestCase):
    def test_task_name(self):
        self.assertEqual(connect_win.task_name(CREDS), "Hagency agent anna")
        self.assertEqual(connect_win.task_name({**CREDS, "user_id": "@alice-at-matrix-org-1a2b-agent:x.org"}),
                         "Hagency agent alice-at-matrix-org-1a2b")

    def test_child_python_is_the_console_one_beside_pythonw(self):
        d = Path(tempfile.mkdtemp())
        (d / "python.exe").write_text("")
        with mock.patch.object(sys, "executable", str(d / "pythonw.exe")):
            self.assertEqual(connect_win.child_python(), str(d / "python.exe"))
        with mock.patch.object(sys, "executable", "/usr/bin/python3"):
            self.assertEqual(connect_win.child_python(), "/usr/bin/python3")


if __name__ == "__main__":
    unittest.main()
