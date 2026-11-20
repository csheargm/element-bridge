"""Changing your agent's own code from your private room (customize.py, bridge_custom.py).
Run: .venv/bin/python -m unittest test_customize"""
import asyncio
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import bridge
import bridge_custom
import customize
import supervise

BETTY, AGENT, ANNA = "@betty:x", "@betty-agent:x", "@anna:x"
PASSING = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"


class Sandbox(unittest.TestCase):
    """A throwaway "official" repo standing in for this folder."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        root = self.tmp / "official"
        root.mkdir()
        (root / "test_x.py").write_text(PASSING)
        (root / "caption.py").write_text("CAPTION = 'Transcript'\n")
        (root / "bridge.py").write_text("")
        (root / ".env").write_text("PW=secret\n")
        (root / "data").mkdir()
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text(".env\ndata/\ncustom/\n")
        subprocess.run([*git, "-C", str(root), "add", "-A"], check=True)
        subprocess.run([*git, "-C", str(root), "commit", "-qm", "init"], check=True)
        self.root = root
        for name, value in (("ROOT", root), ("COPIES", root / "custom"), ("USE_FILE", root / "data" / "custom-use.json"),
                            ("PY", sys.executable)):
            p = mock.patch.object(customize, name, value)
            p.start()
            self.addCleanup(p.stop)

    def change(self, text: str, test: str = PASSING) -> str:
        work = asyncio.run(customize.prepare("betty"))
        (work / "caption.py").write_text(text)
        (work / "test_x.py").write_text(test)
        return asyncio.run(customize.commit("betty", "make the caption shorter"))


class PrivateCopy(Sandbox):
    def test_the_agent_edits_a_copy_and_nothing_runs_until_chosen(self):
        sha = self.change("CAPTION = '📝'\n")
        self.assertEqual(len(sha), 40)
        self.assertEqual((self.root / "caption.py").read_text(), "CAPTION = 'Transcript'\n")   # official untouched
        self.assertEqual(asyncio.run(customize.changed_files("betty")), ["caption.py"])
        self.assertEqual(customize.code_dir("betty"), self.root)              # still the official version

    def test_the_copy_follows_the_official_version(self):
        """Seen live: a copy made before a feature was committed ran without that feature."""
        self.change("CAPTION = 'mine'\n")
        (self.root / "new.py").write_text("NEW = 1\n")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                        "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                        "-C", str(self.root), "commit", "-qm", "official moves on"], check=True)
        work = asyncio.run(customize.prepare("betty"))
        self.assertTrue((work / "new.py").exists())                          # has the new official code
        self.assertEqual((work / "caption.py").read_text(), "CAPTION = 'mine'\n")   # keeps the owner's change
        self.assertEqual(asyncio.run(customize.changed_files("betty")), ["caption.py"])

    def test_nothing_changed_is_no_commit(self):
        asyncio.run(customize.prepare("betty"))
        self.assertIsNone(asyncio.run(customize.commit("betty", "do nothing")))

    def test_use_tests_then_pins_that_commit(self):
        sha = self.change("CAPTION = '📝'\n")
        ok, summary = asyncio.run(customize.use("betty", sha))
        self.assertTrue(ok, summary)
        live = customize.live_dir("betty")
        self.assertEqual(customize.code_dir("betty"), live)
        self.assertEqual((live / "caption.py").read_text(), "CAPTION = '📝'\n")
        self.assertTrue((live / ".env").is_symlink())                       # shares passwords and state
        (customize.work_dir("betty") / "caption.py").write_text("CAPTION = 'later edit'\n")
        self.assertEqual((live / "caption.py").read_text(), "CAPTION = '📝'\n")   # later edits never go live

    def test_tests_never_see_passwords(self):
        leak = PASSING.replace("self.assertTrue(True)", "self.assertFalse(__import__('pathlib').Path('.env').exists())")
        ok, summary = asyncio.run(customize.use("betty", self.change("CAPTION = 'x'\n", leak)))
        self.assertTrue(ok, summary)

    def test_failing_tests_keep_the_current_version(self):
        failing = PASSING.replace("assertTrue(True)", "assertTrue(False)")
        ok, _ = asyncio.run(customize.use("betty", self.change("CAPTION = 'x'\n", failing)))
        self.assertFalse(ok)
        self.assertEqual(customize.in_use(), {})

    def test_go_back(self):
        asyncio.run(customize.use("betty", self.change("CAPTION = 'x'\n")))
        self.assertTrue(customize.go_back("betty"))
        self.assertEqual(customize.code_dir("betty"), self.root)
        self.assertFalse(customize.go_back("betty"))

    def test_discard_only_the_newest(self):
        sha = self.change("CAPTION = 'x'\n")
        self.assertTrue(asyncio.run(customize.discard("betty", sha)))
        self.assertEqual(asyncio.run(customize.changed_files("betty")), [])
        self.assertFalse(asyncio.run(customize.discard("betty", sha)))

    def test_safety_files_are_flagged(self):
        self.assertEqual(customize.sensitive(["caption.py", "bridge.py", ".github/workflows/tests.yml"]),
                         ["bridge.py", ".github/workflows/tests.yml"])

    def test_bad_names_and_commits_are_refused(self):
        with self.assertRaises(customize.CustomError):
            customize.work_dir("../anna")
        with self.assertRaises(customize.CustomError):
            asyncio.run(customize.use("betty", "--help"))


class PullRequest(unittest.TestCase):
    def test_forks_pushes_and_opens_a_pr_as_the_agent(self):
        calls = []

        async def run(args, agent=None, cwd=None, program=None, extra_env=None):
            calls.append((args[:2], agent))
            if args[:2] == ["api", "user"]:
                return "second-account\n"
            return "https://github.com/csheargm/hagency-rooms/pull/7\n" if args[0] == "pr" else ""
        with mock.patch.object(customize.github, "run", run), \
                mock.patch.object(customize.github, "git", mock.AsyncMock()) as git:
            url = asyncio.run(customize.send_pr("betty", "a" * 40, "shorter caption", "body"))
        self.assertEqual(url, "https://github.com/csheargm/hagency-rooms/pull/7")
        self.assertIn((["repo", "fork"], "betty-agent"), calls)
        self.assertEqual(git.call_args.args[0][1], "https://github.com/second-account/hagency-rooms.git")
        self.assertIn((["pr", "create"], "betty-agent"), calls)


def dm(sender=BETTY, joined=2):
    return SimpleNamespace(room_id="!dm", joined_count=joined, invited_count=0)


def msg(body, sender=BETTY):
    return SimpleNamespace(body=body, sender=sender, event_id="$m", source={"content": {}})


class InChat(unittest.TestCase):
    def setUp(self):
        self.b = bridge.Bridge.__new__(bridge.Bridge)
        self.b.owner, self.b.owner_id, self.b.user_id, self.b.backend = "betty", BETTY, AGENT, "claude"
        self.b.tasks, self.b.started_ms, self.b.last_message = set(), 0, {}
        self.b.sync_point = SimpleNamespace(first_time=lambda _id: True)
        self.sent, self.reactions, self.store = [], [], {}

        async def send(room_id, body, reply_to=None, **kw):
            self.sent.append(body)
            return f"$s{len(self.sent)}"

        async def room_send(room_id, kind, content):
            self.reactions.append((content["m.relates_to"]["event_id"], content["m.relates_to"]["key"]))
        self.b.send = send
        self.b.client = SimpleNamespace(room_send=room_send)
        self.b.offers = lambda: self.store

    def test_what_counts_as_a_change_request(self):
        yes = ["change your code so the transcript caption is shorter", "Please add a weather command to the bridge",
               "go back to the official version"]
        no = ["what does your code do?", "change the deadline to Friday", "make me a summary"]
        for text in yes:
            self.assertTrue(self.b.wants_change(dm(), msg(text)), text)
        for text in no:
            self.assertFalse(self.b.wants_change(dm(), msg(text)), text)
        self.assertFalse(self.b.wants_change(dm(joined=3), msg(yes[0])))    # only the private room
        self.assertFalse(self.b.wants_change(dm(), msg(yes[0], ANNA)))       # only the owner

    def ask(self, files):
        with mock.patch.object(customize, "prepare", mock.AsyncMock(return_value=Path("/w"))), \
                mock.patch.object(bridge_custom.agents, "run_turn", mock.AsyncMock(return_value="Shorter caption.")), \
                mock.patch.object(customize, "commit", mock.AsyncMock(return_value="b" * 40)), \
                mock.patch.object(customize, "changed_files", mock.AsyncMock(return_value=files)):
            asyncio.run(self.b.change_code(dm(), msg("change your code so the caption is shorter")))

    def test_a_change_is_offered_with_three_buttons(self):
        self.ask(["pm_voice.py"])
        offer = self.sent[-1]
        self.assertIn("Your version is ready", offer)
        self.assertNotIn("safety checks", offer)
        self.assertEqual([k for _, k in self.reactions], ["👍", "📤", "🗑️"])
        self.assertEqual(self.store[self.reactions[0][0]]["sha"], "b" * 40)

    def test_a_change_to_the_safety_checks_is_flagged(self):
        self.ask(["bridge.py"])
        self.assertIn("This touches the safety checks", self.sent[-1])

    def click(self, key, sender=BETTY, on="$offer"):
        ev = SimpleNamespace(sender=sender, reacts_to=on, key=key, event_id="$r", server_timestamp=10**13)

        async def go():
            await self.b.on_custom_reaction(dm(), ev)
            await asyncio.gather(*self.b.tasks)
        asyncio.run(go())

    def test_only_the_owners_click_counts(self):
        self.store["$offer"] = {"sha": "c" * 40, "request": "r", "summary": "s", "risky": []}
        with mock.patch.object(customize, "use", mock.AsyncMock(return_value=(True, "Ran 3 tests — OK"))) as use:
            self.click("👍", sender=ANNA)
            use.assert_not_called()
            self.click("👍")
        use.assert_awaited_once_with("betty", "c" * 40)
        self.assertIn("Switching to your version", self.sent[-1])
        self.assertEqual(self.reactions[-1][1], "↩️")                       # one click goes back
        with mock.patch.object(customize, "go_back", return_value=True) as back:
            self.click("↩️", on=self.reactions[-1][0])
        back.assert_called_once_with("betty")

    def test_failing_tests_say_so_and_stay(self):
        self.store["$offer"] = {"sha": "c" * 40, "request": "r", "summary": "s", "risky": []}
        with mock.patch.object(customize, "use", mock.AsyncMock(return_value=(False, "Ran 3 tests — FAILED"))):
            self.click("👍")
        self.assertIn("staying on the version you use now", self.sent[-1])

    def test_send_as_pull_request(self):
        self.store["$offer"] = {"sha": "c" * 40, "request": "shorter caption", "summary": "s", "risky": ["bridge.py"]}
        with mock.patch.object(customize, "send_pr", mock.AsyncMock(return_value="https://github.com/o/r/pull/1")) as pr:
            self.click("📤")
        self.assertIn("bridge.py", pr.call_args.args[3])                     # reviewers are told too
        self.assertIn("https://github.com/o/r/pull/1", self.sent[-1])


class Supervisor(unittest.TestCase):
    def test_a_bridge_runs_from_its_owners_chosen_version(self):
        with mock.patch.object(customize, "code_dir", return_value=Path("/live")):
            self.assertEqual(supervise.Child("betty-agent", ["x"], owner="betty").cwd, Path("/live"))
        self.assertEqual(supervise.Child("pm", ["x"]).cwd, supervise.ROOT)

    def test_a_crashing_own_version_goes_back_to_official(self):
        kid = supervise.Child("betty-agent", ["x"], owner="betty")
        kid.cwd, kid.crashes, kid.started = Path("/live"), 1, time.time()   # crashed right after starting
        kid.proc, kid.start = SimpleNamespace(returncode=1), mock.AsyncMock()
        kid.claim()
        with mock.patch.object(supervise, "BACKOFF_S", (0,)), \
                mock.patch.object(customize, "go_back", return_value=True) as back:
            asyncio.run(supervise.revive(kid))
        back.assert_called_once_with("betty")


if __name__ == "__main__":
    unittest.main()
