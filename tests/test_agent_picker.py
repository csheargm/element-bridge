"""Which AI a further agent runs, chosen in "Add an agent" (the panel and the chat: agent_kinds.py), and a
removed agent's name used again. @pm records the choice and hands it to that agent's computer with its
sign-in (agent_kind, model, base_url); an API key is never taken here. Run: .venv/bin/python -m unittest tests.test_agent_picker"""
import json
import re
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

import agent_kinds
import console
import panel
import registry
import registry_removal
import remote_agent
from tests import test_more_agents_flows as flows
from tests.test_more_agents_flows import CX, text

OPENROUTER = "https://openrouter.ai/api/v1"


class PickInChat(flows.Provisioning):
    def tell(self, words):
        self.run_(self.lp.on_welcome_text(SimpleNamespace(room_id="!dm"), text(CX, words)))
        return self.said[-1][1]

    def test_each_kind_said_in_plain_words_is_recorded(self):
        for words, kind in (("add another agent called pi using claude", {"agent_kind": "claude"}),
                            ("add another agent called cx2 using claude code", {"agent_kind": "claude"}),
                            ("add another agent called lo using ollama llama3.2", {"agent_kind": "ollama", "model": "llama3.2"}),
                            ("add another agent called rm using remote https://openrouter.ai/api/v1 openai/gpt-4o",
                             {"agent_kind": "openai", "model": "openai/gpt-4o", "base_url": OPENROUTER})):
            with mock.patch.dict("os.environ", {"HAGENCY_AGENTS_PER_PERSON": "9"}):
                self.tell(words)
            label = words.split()[4]
            found = registry.find(CX, label)
            self.assertEqual({k: found[k] for k in agent_kinds.FIELDS if k in found}, kind, words)

    def test_the_kind_reaches_its_computer_with_the_sign_in(self):
        registry.add_agent(CX, "rm", picked={"agent_kind": "openai", "model": "openai/gpt-4o", "base_url": OPENROUTER})
        registry.update(CX, "rm", ready=True)
        with mock.patch.object(remote_agent, "load_env", return_value={"PW_CX_RM_AGENT": "pw"}):
            creds = remote_agent.credentials_for(CX, "cx-rm-agent")
        self.assertEqual((creds["agent_kind"], creds["model"], creds["base_url"]), ("openai", "openai/gpt-4o", OPENROUTER))
        self.assertNotIn("api_key", json.dumps(creds))
        registry.add_agent(CX, "plain")
        with mock.patch.object(remote_agent, "load_env", return_value={"PW_CX_PLAIN_AGENT": "pw"}):
            self.assertNotIn("agent_kind", remote_agent.credentials_for(CX, "cx-plain-agent"))   # no kind: as before

    def test_an_api_key_typed_in_the_chat_is_refused_and_never_repeated(self):
        answer = self.tell(f"add another agent called rm using remote {OPENROUTER} gpt-4o sk-or-v1-abcdef0123456789abcd")
        self.assertEqual(answer, agent_kinds.NO_KEYS)
        self.assertNotIn("sk-or", json.dumps(self.said))
        self.assertEqual(self.made, [])

    def test_unknown_or_unsafe_choices_say_how(self):
        for how in ("gpt5", "remote http://plain.example/v1 m", "remote https://u:p@x.example/v1 m",
                    "codex o3", "remote https://x.example/v1"):
            self.assertIsInstance(agent_kinds.from_words(how), str, how)
        self.tell("add another agent called pi using gpt5")
        self.assertEqual(self.made, [])

    def test_asked_for_a_name_the_choice_is_kept(self):
        self.tell("add another agent using hermes")
        self.tell("herm")
        self.assertEqual(registry.find(CX, "herm")["agent_kind"], "hermes")

    def test_the_panel_picker(self):
        loop = self.lp
        loop.client = SimpleNamespace(rooms={"!r": SimpleNamespace(users={CX: 0}, invited_users={})})
        loop.is_person = lambda r, u: True
        picked = json.dumps({"name": "lo", "agent_kind": "ollama", "model": "qwen2.5-coder:7b", "base_url": ""})
        self.assertIn("Setting up lo", self.run_(console.act(loop, "!r", CX, "add", picked)))
        self.assertEqual(registry.find(CX, "lo")["model"], "qwen2.5-coder:7b")
        keyed = json.dumps({"name": "rm", "agent_kind": "openai", "model": "sk-proj-abcdef0123456789xyz", "base_url": OPENROUTER})
        self.assertEqual(self.run_(console.act(loop, "!r", CX, "add", keyed)), agent_kinds.NO_KEYS)
        mine = console.my_agents(SimpleNamespace(agent_online=lambda a: None, ever_connected=lambda a: False),
                                 SimpleNamespace(users={}, invited_users={}), "!r", CX)
        self.assertEqual([a["runs"] for a in mine], ["", "Local model (Ollama): qwen2.5-coder:7b"])

    def test_remove_codex_then_add_codex_again(self):
        """Owner request: the name is theirs to use again; its closed account never comes back."""
        self.tell("add another agent called codex")
        registry_removal.forget_agent(CX, "cx-codex-agent")
        self.tell("add another agent called codex using codex")
        again = registry.find(CX, "codex")
        self.assertEqual((again["localpart"], registry.name_of(again), again["agent_kind"]), ("cx-codex2-agent", "codex", "codex"))
        self.assertEqual(self.codes[-1], "cx-codex2-agent")
        self.assertIn("**cx-codex2-agent**", self.said[-1][1])


@unittest.skipIf(shutil.which("node") is None, "node not installed")
class PanelPicker(unittest.TestCase):
    def node(self, call: str) -> str:
        esc = re.search(r"^const esc = .*$", panel.PAGE, re.M).group(0)
        source = "\n".join([esc, "const cap = t => t;", panel.SCRIPTS["panel-agents.js"].read_text(), call])
        done = subprocess.run([shutil.which("node"), "-e", source], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        return done.stdout

    def test_the_picker_lists_the_servers_kinds_and_each_agent_shows_what_it_runs(self):
        state = {"agents": [{"name": "cx-agent", "label": "main", "first": True, "follow": "always", "remote": True,
                             "online": True, "runs": "Remote: m at openrouter.ai"}], "can_add": True, "kinds": agent_kinds.KINDS}
        html = self.node(f"process.stdout.write(agentCards({json.dumps(state)}))")
        for kind, words in agent_kinds.KINDS.items():
            self.assertIn(f'<option value="{kind}">{words}</option>', html)
        self.assertIn('<span class="chip">Remote: m at openrouter.ai</span>', html)
        self.assertIn("API key is asked\n    for on your computer", html)

    def test_add_sends_the_choice_or_a_plain_name(self):
        fields = {"newagent": "lo", "newkind": "ollama", "newmodel": "llama3.2", "newbase": ""}
        out = self.node(f"const f = {json.dumps(fields)}; const document = {{getElementById: id => ({{value: f[id]}})}};"
                        "process.stdout.write(addValue() + '|'); f.newkind = ''; process.stdout.write(addValue());")
        picked, plain = out.split("|")
        self.assertEqual(json.loads(picked), {"name": "lo", "agent_kind": "ollama", "model": "llama3.2", "base_url": ""})
        self.assertEqual(plain, "lo")                                          # no kind: as before
        self.assertEqual(agent_kinds.from_panel(picked), ("lo", {"agent_kind": "ollama", "model": "llama3.2"}))


class Describe(unittest.TestCase):
    def test_what_my_agents_shows(self):
        self.assertEqual(agent_kinds.describe({"agent_kind": "claude"}), "Claude Code")
        self.assertEqual(agent_kinds.describe({"agent_kind": "openai", "model": "m", "base_url": OPENROUTER}),
                         "Remote: m at openrouter.ai")
        self.assertEqual(agent_kinds.describe({}), "")


if __name__ == "__main__":
    unittest.main()
