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
    def state(self, sender, agent=AGENT, caps=("web",)):
        return [{"type": capabilities.GRANTS_EVENT, "state_key": capabilities.key_for(agent), "sender": sender,
                 "content": {"repo": github.REPO, "caps": {c: {} for c in caps}}}]

    def test_trusted_only_from_pm_and_only_for_that_agent(self):
        self.assertEqual(capabilities.caps_in(self.state(PM), AGENT), {"web"})
        self.assertEqual(capabilities.caps_in(self.state("@mallory:x"), AGENT), set())
        self.assertEqual(capabilities.caps_in(self.state(PM, agent=f"@betty-agent:{S}"), AGENT), set())

    def test_grant_and_revoke(self):
        self.assertIn("web", capabilities.record(set(), "web", ANNA, True)["caps"])
        self.assertEqual(capabilities.record({"web"}, "web", ANNA, False)["caps"], {})

    def test_phrases(self):
        self.assertEqual(intents.classify("let my agent search the web here"), intents.GRANT_WEB)
        self.assertEqual(intents.classify("stop letting my agent use the web"), intents.REVOKE_WEB)
        self.assertEqual(intents.classify("stop"), intents.OFF)

    def test_a_question_or_a_quote_is_not_a_grant(self):
        """GPT review 8: "should I let my agent search the web?" must not grant it."""
        for text in ("should I let my agent search the web?", '"let my agent search the web" said betty'):
            self.assertNotEqual(intents.classify(text), intents.GRANT_WEB, text)


class PmGrants(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp()) / "agents.json"
        path.write_text(json.dumps({"anna": {}, "betty": {}}))
        p = mock.patch.object(registry, "PATH", path)
        p.start()
        self.addCleanup(p.stop)
        self.lp = Loop.__new__(Loop)
        self.lp.grants_cache = {}
        self.said, self.written, self.planted = [], [], []

        async def say(room_id, body, *a, **k):
            self.said.append(body)

        async def get_state(room_id):
            return SimpleNamespace(events=self.planted)

        async def put_state(room_id, kind, content, state_key=""):
            self.written.append((state_key, content))
            return SimpleNamespace(event_id="$s")
        self.lp.say = say
        self.lp.client = SimpleNamespace(room_get_state=get_state, room_put_state=put_state)

    def test_an_owner_grants_for_their_own_agent_only(self):
        asyncio.run(self.lp.set_grant(SimpleNamespace(room_id="!r"), BETTY, "web", True))
        key, content = self.written[-1]
        self.assertEqual((key, list(content["caps"])), (f"agent:@betty-agent:{S}", ["web"]))   # never an "@" key
        self.assertIn("each web request is shown here", self.said[-1])

    def test_a_planted_grant_is_never_republished(self):
        """GPT review 8: @pm used to read the whole grants map and republish it under its own name."""
        self.planted = [{"type": capabilities.GRANTS_EVENT, "state_key": f"agent:@betty-agent:{S}", "sender": "@mallory:x",
                         "content": {"repo": github.REPO, "caps": {"web": {}, "everything": {}}}}]
        asyncio.run(self.lp.set_grant(SimpleNamespace(room_id="!r"), BETTY, "web", False))
        self.assertEqual(self.written[-1][1]["caps"], {})

    def test_remote_lookalikes_cant_grant(self):
        asyncio.run(self.lp.set_grant(SimpleNamespace(room_id="!r"), "@anna:evil.example", "web", True))
        self.assertEqual(self.written, [])


class WebTools(unittest.TestCase):
    def test_refused_without_the_owners_grant(self):
        with mock.patch.object(mcp_web, "owners_room", lambda: "!r"), \
                mock.patch.object(capabilities, "granted", lambda *a: set()), mock.patch.object(core, "token", lambda: "t"):
            self.assertIn("hasn't allowed web access", asyncio.run(mcp_web.fetch_page("https://example.com"))["error"])
            self.assertIn("hasn't allowed", asyncio.run(mcp_web.web_search("x"))["error"])

    def test_refused_when_the_room_is_ambiguous(self):
        with mock.patch.object(mcp_web, "owners_room", lambda: None):
            self.assertIn("can't tell which room", asyncio.run(mcp_web.fetch_page("https://example.com"))["error"])

    def test_never_this_machine_or_its_network(self):
        import safe_fetch
        for url in ("http://localhost:8648/_synapse/admin/v1/users", "http://127.0.0.1:8650/data", "http://2130706433/",
                    "http://[::ffff:127.0.0.1]/", "http://[::1]/", "http://192.168.1.1/", "http://224.0.0.1/",
                    "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"):
            with self.assertRaises(safe_fetch.Refused, msg=url):
                safe_fetch.fetch(url)

    def test_each_hop_connects_to_the_address_that_was_checked(self):
        """GPT review 8: the check and the connection resolved the name separately (DNS rebinding).
        Now _one_hop resolves once and connects to that exact IP; a redirect hop is resolved anew."""
        import safe_fetch
        connected = []

        class Conn:
            def __init__(self, host, port=None, timeout=None, **kw):
                connected.append(host)

            def request(self, *a, **k):
                pass

            def getresponse(self):
                return SimpleNamespace(status=302, getheader=lambda h: "http://rebind.example/x", read=lambda n: b"")

            def close(self):
                pass
        with mock.patch.object(safe_fetch, "resolve", side_effect=["93.184.216.34", safe_fetch.Refused("private")]), \
                mock.patch.object(safe_fetch.http.client, "HTTPConnection", Conn):
            with self.assertRaises(safe_fetch.Refused):
                safe_fetch.fetch("http://example.com/")
        self.assertEqual(connected, ["93.184.216.34"])           # the checked IP, never the name again
        self.assertIsNone(safe_fetch.public_ip("::ffff:10.0.0.1"))

    def test_page_text(self):
        self.assertEqual(mcp_web.page_text("<p>Hi <b>there</b></p><script>evil()</script>"), "Hi there")


class AskOwner(unittest.TestCase):
    def run_with(self, reactions, wait=0.05, owner=ANNA, action="write the plan into the team's Google Doc"):
        def api(method, path, body=None, token=None):
            if method == "PUT":
                return {"event_id": "$ask"}
            return {"chunk": [{"sender": s, "content": {"m.relates_to": {"key": k}}} for s, k in reactions]}

        async def no_wait(s):
            return None
        with mock.patch.object(core, "api", api), mock.patch.object(core, "token", lambda: "t"), \
                mock.patch.object(mcp_web, "owners_room", lambda: "!r"), mock.patch.object(core, "owner_id", lambda: owner), \
                mock.patch.object(mcp_web, "APPROVAL_WAIT_S", wait), mock.patch.object(mcp_web.asyncio, "sleep", no_wait):
            return asyncio.run(mcp_web.ask_owner(action))

    def test_the_owners_ok_approves_exactly_that_request(self):
        out = self.run_with([(ANNA, "✅")], wait=1)
        self.assertEqual((out["approved"], out["for"]), (True, "write the plan into the team's Google Doc"))

    def test_someone_elses_ok_doesnt(self):
        self.assertFalse(self.run_with([(BETTY, "✅")])["approved"])

    def test_a_denial_wins_and_silence_means_no(self):
        self.assertIn("said no", self.run_with([(ANNA, "✅"), (ANNA, "👎")], wait=1)["why"])
        self.assertIn("didn't answer", self.run_with([])["why"])

    def test_an_agent_cant_be_its_own_owner_or_hide_a_long_request(self):
        self.assertFalse(self.run_with([(core.USER_ID, "✅")], owner=core.USER_ID)["approved"])
        self.assertIn("under", self.run_with([(ANNA, "✅")], action="x" * 1200)["why"])


class BridgeWeb(unittest.TestCase):
    def test_web_tools_only_when_granted(self):
        self.assertNotIn("WebSearch", " ".join(agents._claude_argv(Path("/w"), None)))
        self.assertIn("WebSearch,WebFetch", " ".join(agents._claude_argv(Path("/w"), None, web=True)))
        with mock.patch.object(agents, "_codex_lockdown", lambda web=False: [f"web={web}"]):
            self.assertIn("web=True", agents._codex_argv(Path("/w"), None, web=True))


if __name__ == "__main__":
    unittest.main()
