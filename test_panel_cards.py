"""Unit tests: the panel's cards as a person sees them, rendered by node from the real page helpers
and card scripts (static/panel_*.js). Run: .venv/bin/python -m unittest test_panel_cards"""
import json
import re
import shutil
import subprocess
import unittest

import panel

NODE = shutil.which("node")


def render(script: str, call: str) -> str:
    """The HTML `call` (JavaScript) returns, with panel.PAGE's own esc / cap / buttons and that card script."""
    helpers = [re.search(rf"^const {name} = .*$", panel.PAGE, re.M).group(0) for name in ("esc", "cap")]
    buttons = re.search(r"^function buttons\(.*?`</span>`; }$", panel.PAGE, re.S | re.M).group(0)
    source = "\n".join([*helpers, buttons, panel.SCRIPTS[script].read_text(encoding="utf-8"),
                        f"process.stdout.write({call});"])
    done = subprocess.run([NODE, "-e", source], capture_output=True, text=True)
    if done.returncode:
        raise AssertionError(done.stderr[-500:])
    return done.stdout


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


@unittest.skipIf(NODE is None, "node not installed")
class WhoCanJoin(unittest.TestCase):
    def card(self, visibility: str) -> str:
        return render("panel-project.js", f"projectCard({json.dumps({'visibility': visibility, 'moderator': True})})")

    def test_the_help_text_names_the_option_that_is_selected(self):
        """Screen test: "Private" was selected and the help text began "Unlisted: ...": two words for
        one setting. Each option's help starts with its own button label, and never says Unlisted."""
        for value in ("private", "listed", "open"):
            page = self.card(value)
            selected = re.search(r"class=on\s*>([^<]+)</button>", page).group(1)
            hint = text_of(re.search(r'<div class="hint">(.*?)</div>', page, re.S).group(1))
            self.assertEqual(selected, value.capitalize())
            self.assertTrue(hint.startswith(f"{selected}:"), hint)
            self.assertNotIn("Unlisted", page)

    def test_no_room_setting_yet_reads_as_private(self):
        self.assertIn("Private: not listed anywhere", render("panel-project.js", "projectCard({moderator: false})"))


NEVER = {"name": "newbie1-agent", "label": "main", "first": True, "follow": "always", "remote": True,
         "online": False, "connected": False, "here": False, "invited": True}


@unittest.skipIf(NODE is None, "node not installed")
class MyAgents(unittest.TestCase):
    """Screen test, a newcomer's "My agents": the label "main", "Run the connect line again ... (🔁)" for an
    agent that had never connected, and "Code it runs: the official version" nobody had changed."""
    def card(self, agents: list, version: str | None = "") -> str:
        state = {"agents": agents, "can_add": True, "version": version}
        return text_of(render("panel-agents.js", f"agentCards({json.dumps(state)})"))

    def test_one_agent_that_never_connected(self):
        card = self.card([NEVER])
        self.assertIn("newbie1-agent", card)
        self.assertNotIn("main", card)                                 # a label only matters with several
        self.assertIn("Not connected yet: open your chat with Product Manager and follow the 🔌 steps.", card)
        self.assertNotIn("Run the connect line again", card)
        self.assertNotIn("Code it runs", card)                         # nothing customized: nothing to say
        self.assertNotIn("official version", card)

    def test_a_connected_agent_gone_offline_gets_the_reconnect_hint(self):
        card = self.card([{**NEVER, "connected": True, "here": True, "invited": False}])
        self.assertIn("Run the connect line again on its computer", card)
        self.assertIn("Offline", card)
        self.assertIn("in this room", card)

    def test_labels_show_when_there_are_several_and_a_custom_version_shows(self):
        codex = {**NEVER, "name": "newbie1-codex-agent", "label": "codex", "first": False}
        card = self.card([NEVER, codex], version="abc1234")
        self.assertIn("main · newbie1-agent", card)
        self.assertIn("codex · newbie1-codex-agent", card)
        self.assertIn("Code it runs: your own version (abc1234)", card)
        self.assertIn("Go back to official", card)


if __name__ == "__main__":
    unittest.main()
