"""Unit tests: onboarding pieces. Run: .venv/bin/python -m unittest test_onboard"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hermes_profile
import registry
import supervise


class Supervisor(unittest.TestCase):
    def kids(self, people, default="codex"):
        with mock.patch.object(registry, "load", return_value=people), \
                mock.patch.object(hermes_profile, "set_repo") as set_repo:     # never touch real profiles
            kids = {k.name: k.argv for k in supervise.children(default, False)}
        self.set_repo_calls = set_repo.call_args_list
        return kids

    def test_each_person_gets_the_agent_they_brought(self):
        kids = self.kids({"anna": {"agent": "hermes"}, "betty": {"agent": "claude"}, "carl": {}})
        self.assertEqual(kids["anna-agent"], ["hermes", "-p", "annaroom", "gateway", "run"])
        self.assertEqual(kids["betty-agent"][-3:], ["betty", "--agent", "claude"])
        self.assertEqual(kids["carl-agent"][-2:], ["--agent", "codex"])      # not onboarded: start.sh's choice
        self.assertIn("pm", kids)
        self.assertEqual(self.set_repo_calls[0].args, ("annaroom", supervise.github.REPO))   # this run's repo

    def test_registry_defaults_to_the_two_demo_people(self):
        with mock.patch.object(registry, "PATH", Path(tempfile.mkdtemp()) / "missing.json"):
            self.assertEqual(sorted(registry.load()), ["anna", "betty"])


class HermesProfile(unittest.TestCase):
    def setUp(self):
        home = Path(tempfile.mkdtemp())
        (home / "profiles" / "annaroom").mkdir(parents=True)
        (home / "profiles" / "annaroom" / "SOUL.md").write_text("You are helpful.")
        (home / "config.yaml").write_text("model:\n  default: gpt-oss:120b\n  provider: ollama-cloud\n"
                                          "  base_url: https://ollama.com/v1\nproviders: {}\n")
        (home / ".env").write_text("OLLAMA_API_KEY=model-key\nGITHUB_TOKEN=personal\nTELEGRAM_BOT_TOKEN=personal\n")
        p = mock.patch.object(hermes_profile, "HERMES_HOME", home)
        p.start()
        self.addCleanup(p.stop)
        self.profile = hermes_profile.configure("annaroom", owner="anna", agent="anna-agent", password="pw",
                                                home_room="!dm:x")

    def test_only_the_model_key_comes_along(self):
        env = (self.profile / ".env").read_text()
        self.assertIn("OLLAMA_API_KEY=model-key", env)
        self.assertNotIn("personal", env)
        self.assertEqual((self.profile / ".env").stat().st_mode & 0o777, 0o600)

    def test_obeys_only_its_owner_and_pm(self):
        env = (self.profile / ".env").read_text()
        self.assertIn("MATRIX_ALLOWED_USERS=@anna:bridge.localhost,@pm:bridge.localhost", env)
        self.assertNotIn("MATRIX_ALLOWED_ROOMS", env)                        # the owner can DM it anywhere

    def test_no_file_terminal_or_web_tools_and_a_quiet_room(self):
        cfg = (self.profile / "config.yaml").read_text()
        self.assertIn("matrix: [todo, memory, skills]", cfg)
        self.assertIn('tool_progress: "off"', cfg)
        self.assertIn('memory_notifications: "off"', cfg)
        self.assertIn("provider: ollama-cloud", cfg)

    def test_a_model_chosen_for_the_profile_is_kept(self):
        cfg = self.profile / "config.yaml"
        cfg.write_text(cfg.read_text().replace("provider: ollama-cloud", "provider: openai-codex"))
        hermes_profile.configure("annaroom", owner="anna", agent="anna-agent", password="pw", home_room=None)
        self.assertIn("provider: openai-codex", cfg.read_text())

    def test_set_repo_points_the_mcp_server_at_this_runs_repo(self):
        """Seen live: Hermes's MCP server didn't get LOOP_REPO and opened issues in the default repo."""
        cfg = self.profile / "config.yaml"
        hermes_profile.set_repo("annaroom", "me/project-a")
        hermes_profile.set_repo("annaroom", "me/project-b")
        self.assertEqual(cfg.read_text().count("LOOP_REPO:"), 1)
        self.assertIn("LOOP_REPO: 'me/project-b'", cfg.read_text())

    def test_room_rules_are_written_once(self):
        hermes_profile.configure("annaroom", owner="anna", agent="anna-agent", password="pw", home_room=None)
        self.assertEqual((self.profile / "SOUL.md").read_text().count("In Hagency rooms:"), 1)


if __name__ == "__main__":
    unittest.main()
