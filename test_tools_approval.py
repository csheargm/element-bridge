"""Unit tests: tools beyond the project, with the owner's say-so. Run: .venv/bin/python -m unittest test_tools_approval"""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("HAGENCY_AGENT", "anna-agent")
import agents  # noqa: E402
import capabilities  # noqa: E402
import github  # noqa: E402
import intents  # noqa: E402
import mcp_core as core  # noqa: E402
import mcp_web  # noqa: E402
import registry  # noqa: E402
from loop import Loop  # noqa: E402

S = "bridge.localhost"
ANNA, BETTY, AGENT = f"@anna:{S}", f"@betty:{S}", f"@anna-agent:{S}"
PM = capabilities.PM_ID


class Grants(unittest.TestCase):
    def state(self, sender, grants):
        return [{"type": capabilities.GRANTS_EVENT, "state_key": "", "sender": sender,
                 "content": {"repo": github.REPO, "grants": grants}}]

    def test_trusted_only_from_pm(self):
        grants = {AGENT: {"web": {"by": ANNA}}}
        with mock.patch.object(capabilities, "api", lambda *a, **k: self.state(PM, grants)):
            self.assertEqual(capabilities.granted("t", "!r", AGENT), {"web"})
        with mock.patch.object(capabilities, "api", lambda *a, **k: self.state("@mallory:x", grants)):
            self.assertEqual(capabilities.granted("t", "!r", AGENT), set())

    def test_grant_and_revoke(self):
        on = capabilities.with_grant({}, AGENT, "web", ANNA, True)
        self.assertIn("web", on["grants"][AGENT])
        off = capabilities.with_grant(on, AGENT, "web", ANNA, False)
        self.assertEqual(off["grants"][AGENT], {})

    def test_phrases(self):
        self.assertEqual(intents.classify("let my agent search the web here"), intents.GRANT_WEB)
        self.assertEqual(intents.classify("stop letting my agent use the web"), intents.REVOKE_WEB)
        self.assertEqual(intents.classify("stop"), intents.OFF)


class PmGrants(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp()) / "agents.json"
        path.write_text(json.dumps({"anna": {}, "betty": {}}))
        p = mock.patch.object(registry, "PATH", path)
        p.start()
        self.addCleanup(p.stop)
        self.lp = Loop.__new__(Loop)
        self.said, self.written = [], []

        async def say(room_id, body, *a, **k):
            self.said.append(body)

        async def get_state(room_id, kind, key):
            return SimpleNamespace()

        async def put_state(room_id, kind, content, **kw):
            self.written.append(content)
            return SimpleNamespace(event_id="$s")
        self.lp.say = say
        self.lp.client = SimpleNamespace(room_get_state_event=get_state, room_put_state=put_state)

    def test_an_owner_grants_for_their_own_agent_only(self):
        asyncio.run(self.lp.set_grant(SimpleNamespace(room_id="!r"), BETTY, "web", True))
        self.assertIn(f"@betty-agent:{S}", self.written[-1]["grants"])
        self.assertNotIn(AGENT, self.written[-1]["grants"])
        self.assertIn("your agent may now search the web", self.said[-1])

    def test_remote_lookalikes_cant_grant(self):
        asyncio.run(self.lp.set_grant(SimpleNamespace(room_id="!r"), "@anna:evil.example", "web", True))
        self.assertEqual(self.written, [])


class WebTools(unittest.TestCase):
    def test_refused_without_the_owners_grant(self):
        with mock.patch.object(mcp_web, "owners_room", lambda: "!r"), \
                mock.patch.object(capabilities, "granted", lambda *a: set()), mock.patch.object(core, "token", lambda: "t"):
            self.assertIn("hasn't allowed web access", mcp_web.fetch_page("https://example.com")["error"])
            self.assertIn("hasn't allowed", mcp_web.web_search("x")["error"])

    def test_never_this_machine_or_its_network(self):
        for url in ("http://localhost:8648/_synapse/admin/v1/users", "http://127.0.0.1:8650/data",
                    "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"):
            self.assertFalse(mcp_web.public_url(url), url)
            with self.assertRaises(ValueError):
                mcp_web.fetch(url)

    def test_a_redirect_into_the_network_is_refused(self):
        handler = mcp_web._CheckedRedirects()
        with self.assertRaises(Exception):
            handler.redirect_request(SimpleNamespace(), None, 302, "Found", {}, "http://127.0.0.1:8648/")

    def test_page_text(self):
        self.assertEqual(mcp_web.page_text("<p>Hi <b>there</b></p><script>evil()</script>"), "Hi there")


class AskOwner(unittest.TestCase):
    def run_with(self, reactions, wait=0.01):
        def api(method, path, body=None, token=None):
            if method == "PUT":
                return {"event_id": "$ask"}
            return {"chunk": [{"sender": s, "content": {"m.relates_to": {"key": k}}} for s, k in reactions]}
        with mock.patch.object(core, "api", api), mock.patch.object(core, "token", lambda: "t"), \
                mock.patch.object(mcp_web, "owners_room", lambda: "!r"), \
                mock.patch.object(core, "owner_id", lambda: ANNA), mock.patch.object(mcp_web, "APPROVAL_WAIT_S", wait), \
                mock.patch.object(mcp_web.time, "sleep", lambda s: None):
            return mcp_web.ask_owner("write the plan into the team's Google Doc")

    def test_the_owners_ok_approves(self):
        self.assertTrue(self.run_with([(ANNA, "✅")], wait=1)["approved"])

    def test_someone_elses_ok_doesnt(self):
        self.assertFalse(self.run_with([(BETTY, "✅")])["approved"])

    def test_no_or_silence_means_no(self):
        self.assertIn("said no", self.run_with([(ANNA, "👎")], wait=1)["why"])
        self.assertIn("didn't answer", self.run_with([])["why"])


class BridgeWeb(unittest.TestCase):
    def test_web_tools_only_when_granted(self):
        self.assertNotIn("WebSearch", " ".join(agents._claude_argv(Path("/w"), None)))
        self.assertIn("WebSearch,WebFetch", " ".join(agents._claude_argv(Path("/w"), None, web=True)))
        with mock.patch.object(agents, "_codex_lockdown", lambda web=False: [f"web={web}"]):
            self.assertIn("web=True", agents._codex_argv(Path("/w"), None, web=True))


if __name__ == "__main__":
    unittest.main()
