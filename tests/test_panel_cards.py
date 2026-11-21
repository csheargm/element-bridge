"""Unit tests: the panel's cards as a person sees them, rendered by node from the real page helpers
and card scripts (static/panel_*.js). Run: .venv/bin/python -m unittest tests.test_panel_cards"""
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


@unittest.skipIf(NODE is None, "node not installed")
class ProjectRepo(unittest.TestCase):
    """The owner asked: a repo could be switched, never removed. Remove repo asks twice in the page."""
    OWNER = {"repo": "a/x", "moderator": True}

    def test_the_owner_sees_remove_repo_only_when_a_repo_is_set(self):
        self.assertIn("Remove repo", text_of(render("panel-project.js", f"repoCard({json.dumps(self.OWNER)})")))
        self.assertNotIn("Remove repo", render("panel-project.js", "repoCard({repo: null, moderator: true})"))
        self.assertIn("Pick a project repo", render("panel-project.js", "repoCard({repo: null, moderator: true})"))
        self.assertNotIn("Remove repo", render("panel-project.js", 'repoCard({repo: "a/x", moderator: false})'))

    def test_the_first_click_only_arms_it_and_the_second_removes(self):
        owner = json.dumps(self.OWNER)
        clicks = ('(() => { const b = {dataset: {a: "unrepo", v: ""}, textContent: ""}; const first = repoConfirmed(b);'
                  f' const shown = repoCard({owner}); const second = repoConfirmed(b);'
                  ' return [first, b.dataset.v, shown.includes("Click again to remove the repo"), second, '
                  f'repoCard({owner}).includes("Click again")].join(","); }})()')
        self.assertEqual(render("panel-project.js", clicks), "false,sure,true,true,false")

    def test_the_page_never_sends_it_without_its_script(self):
        ok = re.search(r"^const ok = .*?; };$", panel.PAGE, re.S | re.M).group(0)
        done = subprocess.run([NODE, "-e", f"const window = {{}}; {ok}\nprocess.stdout.write(String("
                               'ok({dataset: {a: "unrepo", v: "sure"}})) + String(ok({dataset: {a: "mode"}})))'],
                              capture_output=True, text=True)
        self.assertEqual(done.stdout, "falsetrue", done.stderr)


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
        self.assertIn("Not connected yet: open your chat with Product Manager and follow the 🔌 steps", card)
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
        self.assertIn('type "resend me the code" there for a fresh one', card)              # its first agent
        self.assertIn('follow its Connect message (for a fresh code, type "add another agent called codex" there)', card)
        self.assertEqual(card.count("🔌"), 1)                          # GPT review: 🔌 sets up the first one only
        self.assertIn("Go back to official", card)


if __name__ == "__main__":
    unittest.main()
