"""The agent's work folders live outside the app (~/.hagency/work/<owner>/), old ones move there with
their sessions, and the owner's Hermes gets their own tool settings (ADR 0006).
Run: .venv/bin/python -m unittest test_workdirs"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hermes_profile
import workdirs

ROOM = "!gkon9:bridge.localhost"
CODEX_ID, CLAUDE_ID = "0123abcd-codex", "4567efab-claude"


class Folders(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.app = self.tmp / "app"
        for p in (mock.patch.object(workdirs, "WORK_HOME", self.tmp / "home" / ".hagency" / "work"),
                  mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(self.tmp / "claude")})):
            p.start()
            self.addCleanup(p.stop)

    def old_folder(self, state: dict | None) -> Path:
        old = self.app / "rooms" / "anna" / workdirs.room_key(ROOM)
        (old / "projects" / "repo").mkdir(parents=True)
        (old / "projects" / "repo" / "README.md").write_text("hi")
        if state is not None:
            (old / workdirs.SESSION_FILE).write_text(json.dumps(state))
        return old

    def test_new_folder_is_outside_the_app(self):
        d = workdirs.room_dir("anna", ROOM, legacy_root=self.app)
        self.assertEqual(d, workdirs.WORK_HOME / "anna" / workdirs.room_key(ROOM))
        self.assertNotIn(self.app, d.parents)

    def test_ids_that_sanitize_alike_stay_apart(self):
        self.assertNotEqual(workdirs.room_key("!a:b.c"), workdirs.room_key("!a_b:c"))

    def test_an_old_folder_moves_with_its_sessions(self):
        old = self.old_folder({"codex": CODEX_ID, "claude": CLAUDE_ID})
        src = workdirs.claude_project(old)
        src.mkdir(parents=True)
        (src / f"{CLAUDE_ID}.jsonl").write_text('{"x":1}\n')
        new = workdirs.room_dir("anna", ROOM, legacy_root=self.app)
        self.assertFalse(old.exists())
        self.assertEqual((new / "projects" / "repo" / "README.md").read_text(), "hi")
        self.assertEqual(json.loads((new / workdirs.SESSION_FILE).read_text()),
                         {"codex": CODEX_ID, "claude": CLAUDE_ID})
        self.assertEqual((workdirs.claude_project(new) / f"{CLAUDE_ID}.jsonl").read_text(), '{"x":1}\n')

    def test_a_claude_session_without_its_transcript_is_dropped_not_broken(self):
        self.old_folder({"codex": CODEX_ID, "claude": CLAUDE_ID})
        new = workdirs.room_dir("anna", ROOM, legacy_root=self.app)
        self.assertEqual(json.loads((new / workdirs.SESSION_FILE).read_text()), {"codex": CODEX_ID})

    def test_a_malformed_session_id_is_never_used_as_a_path(self):
        self.old_folder({"claude": "../../etc/passwd"})
        new = workdirs.room_dir("anna", ROOM, legacy_root=self.app)
        self.assertEqual(json.loads((new / workdirs.SESSION_FILE).read_text()), {"claude": "../../etc/passwd"})
        self.assertFalse((self.tmp / "claude").exists())     # nothing copied anywhere

    def test_moves_once_and_never_over_a_newer_folder(self):
        old = self.old_folder({"codex": CODEX_ID})
        newer = workdirs.WORK_HOME / "anna" / workdirs.room_key(ROOM)
        newer.mkdir(parents=True)
        self.assertFalse(workdirs.migrate(old, newer))
        self.assertTrue(old.exists())
        self.assertFalse((newer / workdirs.SESSION_FILE).exists())

    def test_a_move_across_disks_copies_then_swaps_in(self):
        old = self.old_folder({"codex": CODEX_ID})
        real_rename = os.rename
        calls = []

        def rename(a, b):
            calls.append(a)
            if Path(a) == old:
                raise OSError(18, "Invalid cross-device link")
            return real_rename(a, b)
        with mock.patch.object(workdirs.os, "rename", rename):
            new = workdirs.room_dir("anna", ROOM, legacy_root=self.app)
        self.assertEqual(json.loads((new / workdirs.SESSION_FILE).read_text()), {"codex": CODEX_ID})
        self.assertFalse(old.exists())
        self.assertFalse(new.with_name(new.name + ".moving").exists())

    def test_a_failed_move_starts_fresh_and_keeps_the_old_folder(self):
        old = self.old_folder({"codex": CODEX_ID})
        with mock.patch.object(workdirs, "_move", side_effect=OSError("disk full")):
            new = workdirs.room_dir("anna", ROOM, legacy_root=self.app)
        self.assertTrue(old.exists())
        self.assertFalse(new.exists())


class HermesOwnerTools(unittest.TestCase):
    def tools(self, config: str | None) -> str:
        home = Path(tempfile.mkdtemp())
        if config is not None:
            (home / "config.yaml").write_text(config)
        with mock.patch.object(hermes_profile, "HERMES_HOME", home), \
                mock.patch.object(workdirs, "WORK_HOME", Path("/w")):
            return hermes_profile.owner_tools("anna")

    def test_the_owners_own_settings_come_along(self):
        out = self.tools("toolsets:\n- hermes-cli\n- web\nplatform_toolsets:\n  cli:\n  - hermes-cli\n  matrix:\n"
                         "  - hermes-matrix\n  - browser\n  slack:\n  - hermes-slack\napprovals:\n  mode: smart\n"
                         "  timeout: 60\nterminal:\n  backend: docker\n  cwd: .\n  timeout: 180\nother: 1\n")
        self.assertIn("toolsets: [hermes-cli, web]", out)
        self.assertIn("  matrix: [hermes-matrix, browser]", out)
        self.assertIn("approvals:\n  mode: smart\n  timeout: 60", out)
        self.assertIn("  backend: docker", out)
        self.assertNotIn("cwd: .", out)
        self.assertTrue(out.endswith("  cwd: '/w/anna/hermes'"))   # the terminal never works in the app
        self.assertNotIn("hermes-slack", out)

    def test_hermes_defaults_when_the_owner_set_nothing(self):
        out = self.tools(None)
        self.assertIn("toolsets: [hermes-cli]", out)
        self.assertIn("  matrix: [hermes-matrix]", out)
        self.assertIn("approvals:\n  mode: manual", out)

    def test_odd_toolset_names_never_reach_the_yaml(self):
        out = self.tools("platform_toolsets:\n  matrix: [hermes-matrix, \"x]\\nevil: 1\"]\n")
        self.assertNotIn("evil", out)
        self.assertIn("  matrix: [hermes-matrix]", out)


if __name__ == "__main__":
    unittest.main()
