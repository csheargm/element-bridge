"""Unit tests: one-time pairing codes and the bring-your-own server side.
Run: .venv/bin/python -m unittest test_pairing"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import pairing
import registry
import remote_agent
import setup_accounts


def tmp() -> Path:
    return Path(tempfile.mkdtemp())


class Codes(unittest.TestCase):
    def setUp(self):
        self.path = tmp() / "pairing.json"
        self.codes = pairing.Codes(self.path)

    def test_a_code_works_once(self):
        code = self.codes.issue("newbie1")
        self.assertRegex(code, r"^[A-Z2-9]{5}-[A-Z2-9]{5}$")
        self.assertEqual(self.codes.redeem(code), "newbie1")
        self.assertIsNone(self.codes.redeem(code))

    def test_typed_loosely_still_works(self):
        code = self.codes.issue("newbie1")
        self.assertEqual(self.codes.redeem(" " + code.lower().replace("-", "") + " "), "newbie1")

    def test_expired_codes_and_wrong_codes_fail(self):
        code = self.codes.issue("newbie1", now=1000)
        self.assertIsNone(self.codes.redeem("ABCDE-FGHJK"))
        self.assertIsNone(self.codes.redeem(code, now=1000 + pairing.CODE_S + 1))
        self.assertIsNone(self.codes.redeem(code, now=1001))          # an expired try used it up
        for bad in (None, "", 5, "x" * 100):
            self.assertIsNone(self.codes.redeem(bad))

    def test_only_a_hash_is_kept_on_disk(self):
        code = self.codes.issue("newbie1")
        on_disk = self.path.read_text()
        self.assertNotIn(code, on_disk)
        self.assertNotIn(pairing.normalize(code), on_disk)
        self.assertIn(pairing.digest(code), json.loads(on_disk))
        self.assertEqual(pairing.Codes(self.path).redeem(code), "newbie1")   # survives a restart

    def test_a_new_code_replaces_the_old_one(self):
        old = self.codes.issue("newbie1")
        other = self.codes.issue("newbie2")
        new = self.codes.issue("newbie1")
        self.assertIsNone(self.codes.redeem(old))
        self.assertEqual((self.codes.redeem(new), self.codes.redeem(other)), ("newbie1", "newbie2"))


class Limits(unittest.TestCase):
    def test_wrong_codes_are_rate_limited_per_client_then_released(self):
        lim = pairing.Limiter()
        for _ in range(pairing.FAILS_PER_CLIENT):
            self.assertFalse(lim.blocked("1.2.3.4", now=100))
            lim.failed("1.2.3.4", now=100)
        self.assertTrue(lim.blocked("1.2.3.4", now=101))
        self.assertFalse(lim.blocked("5.6.7.8", now=101))
        self.assertFalse(lim.blocked("1.2.3.4", now=100 + pairing.FAIL_WINDOW_S + 1))

    def test_and_in_total(self):
        lim = pairing.Limiter()
        for i in range(pairing.FAILS_TOTAL):
            lim.failed(f"10.0.0.{i}", now=100)
        self.assertTrue(lim.blocked("10.9.9.9", now=101))


class Server(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reg = tmp() / "agents.json"
        reg.write_text(json.dumps({"newbie1": {"agent": "remote"}, "anna": {"agent": "hermes"}}))
        for target, name, value in ((registry, "PATH", reg), (remote_agent, "CODES", pairing.Codes(tmp() / "p.json")),
                                    (remote_agent, "LIMIT", pairing.Limiter()),
                                    (remote_agent, "load_env", lambda: {"PW_NEWBIE1_AGENT": "s3cret", "PW_ANNA_AGENT": "a"})):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        app = web.Application()
        app.add_routes(remote_agent.routes())
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def post(self, code):
        r = await self.client.post("/connect", json={"code": code})
        return r.status, await r.json(), r.headers

    async def test_the_code_gives_the_sign_in_exactly_once(self):
        code = remote_agent.CODES.issue("newbie1")
        status, body, headers = await self.post(code)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"homeserver": "http://localhost:8648", "user_id": "@newbie1-agent:bridge.localhost",
                                "password": "s3cret", "owner": "newbie1"})
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, body, _ = await self.post(code)
        self.assertEqual(status, 400)
        self.assertNotIn("password", body)

    async def test_only_remote_agents_are_handed_out(self):
        status, _, _ = await self.post(remote_agent.CODES.issue("anna"))     # her agent runs on the server
        self.assertEqual(status, 400)

    async def test_too_many_wrong_codes_are_refused_even_with_a_right_one(self):
        for _ in range(pairing.FAILS_PER_CLIENT):
            await self.post("WRONG-CODE0")
        status, body, _ = await self.post(remote_agent.CODES.issue("newbie1"))
        self.assertEqual(status, 429)
        self.assertNotIn("password", body)

    async def test_not_json_is_a_wrong_code(self):
        r = await self.client.post("/connect", data="code=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 400)

    async def test_installer_names_this_console(self):
        r = await self.client.get("/connect.sh")
        script = await r.text()
        self.assertIn('CONSOLE="${HAGENCY_CONSOLE:-http://localhost:8650}"', script)
        self.assertNotIn(remote_agent.CONSOLE_MARK, script)


class Provision(unittest.TestCase):
    def setUp(self):
        self.reg = tmp() / "agents.json"
        self.reg.write_text(json.dumps({"anna": {"agent": "hermes"}}))
        mock.patch.object(registry, "PATH", self.reg).start()
        self.ensure = mock.patch.object(setup_accounts, "ensure_user", return_value=("pw", True)).start()
        mock.patch.object(setup_accounts, "shared_secret", return_value="secret").start()
        mock.patch.object(remote_agent, "load_env", return_value={}).start()
        self.dm = mock.patch.object(remote_agent, "open_dm", return_value="!dm").start()
        self.addCleanup(mock.patch.stopall)

    def test_creates_the_agent_records_it_remote_and_opens_its_chat(self):
        self.assertEqual(remote_agent.provision("newbie1"), "newbie1-agent")
        self.assertEqual(self.ensure.call_args.args[1:], ("newbie1-agent", "secret"))
        self.dm.assert_called_once_with("newbie1-agent", "pw", "newbie1")
        self.assertEqual(registry.load()["newbie1"], {"agent": "remote", "dm": "!dm"})
        self.assertEqual(registry.owner_of("newbie1-agent"), "newbie1")
        remote_agent.provision("newbie1")                    # again: same chat, nothing new
        self.dm.assert_called_once()

    def test_refuses_people_with_a_server_agent_and_odd_names(self):
        for name in ("anna", "John.Doe", "x"):
            with self.assertRaises(setup_accounts.AccountError):
                remote_agent.provision(name)
        self.ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
