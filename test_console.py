"""Unit tests: the Hagency console (settings in the side panel / a page). Run: .venv/bin/python -m unittest test_console"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import console
import registry
import room_state

S = "bridge.localhost"
ANNA, BETTY, MALLORY = f"@anna:{S}", f"@betty:{S}", f"@mallory:{S}"


class Auth(unittest.TestCase):
    def test_a_link_works_once_expires_and_is_for_one_room(self):
        code = console.new_link(ANNA, "!r")
        self.assertEqual(console.redeem(code), (ANNA, "!r"))
        self.assertIsNone(console.redeem(code))                       # single use
        old = console.new_link(ANNA, "!r")
        console._links[old] = (ANNA, time.time() - 1, "!r")
        self.assertIsNone(console.redeem(old))

    def test_a_link_session_works_only_in_its_room(self):
        token = console.new_session(ANNA, "!r")
        req = SimpleNamespace(headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(console.session_of(req), (ANNA, "!r"))
        self.assertIsNone(console.session_of(SimpleNamespace(headers={"Authorization": token})))   # Bearer required

    def test_only_json_from_our_own_origin(self):
        ok = SimpleNamespace(host="localhost:8650", content_type="application/json", headers={"Origin": "http://localhost:8650"})
        self.assertTrue(console.allowed(ok, True))
        for bad in (SimpleNamespace(host="evil.example", content_type="application/json", headers={}),
                    SimpleNamespace(host="localhost:8650", content_type="text/plain", headers={}),
                    SimpleNamespace(host="localhost:8650", content_type="application/json", headers={"Origin": "https://evil.example"})):
            self.assertFalse(console.allowed(bad, True))

    def test_openid_tokens_are_verified_with_the_homeserver_and_only_local_users_count(self):
        def opener(sub):
            class R:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return json.dumps({"sub": sub}).encode()
            return lambda url, timeout=None: R()
        def build(sub, fail=False):
            def opener(*handlers):
                return SimpleNamespace(open=(lambda url, timeout=None: (_ for _ in ()).throw(OSError("401"))) if fail
                                       else (lambda url, timeout=None: Resp(sub)))
            return opener

        class Resp:
            def __init__(self, sub):
                self.sub = sub

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"sub": self.sub} if self.sub != "notadict" else ["x"]).encode()
        for sub, want in ((ANNA, ANNA), ("@anna:evil.example", None), ("@anna:evil.bridge.localhost", None),
                          ("notadict", None)):
            with mock.patch.object(console.urllib.request, "build_opener", build(sub)):
                self.assertEqual(console.openid_user("tok"), want, sub)
        with mock.patch.object(console.urllib.request, "build_opener", build(None, fail=True)):
            self.assertIsNone(console.openid_user("bogus"))

    def test_sessions(self):
        token = console.new_session(ANNA)
        self.assertEqual(console.user_of(SimpleNamespace(headers={"Authorization": f"Bearer {token}"})), ANNA)
        self.assertIsNone(console.user_of(SimpleNamespace(headers={"Authorization": "Bearer nope"})))
        self.assertIsNone(console.user_of(SimpleNamespace(headers={})))


class Actions(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp()) / "agents.json"
        path.write_text(json.dumps({"anna": {}, "betty": {}}))
        p = mock.patch.object(registry, "PATH", path)
        p.start()
        self.addCleanup(p.stop)
        room = SimpleNamespace(users={ANNA: 0, BETTY: 0}, invited_users={}, display_name="Hello")
        self.calls = []

        async def change_level(rid, level):
            self.calls.append(("mode", level))

        async def set_follow(r, user, value):
            self.calls.append(("follow", user, value))

        async def set_grant(r, user, cap, on):
            self.calls.append(("web", user, on))

        async def say(*a, **k):
            pass

        async def confirm(r, user):
            return False
        self.loop = SimpleNamespace(client=SimpleNamespace(rooms={"!r": room}), change_level=change_level, say=say,
                                    is_person=lambda r, u: u in r.users and not u.split(":")[0].endswith("-agent"),
                                    set_follow=set_follow, set_grant=set_grant, confirm=confirm, pending={},
                                    grants_cache={}, level=lambda rid: asyncio.sleep(0, "off"))
        p2 = mock.patch.object(room_state, "is_moderator", lambda c, r, u: asyncio.sleep(0, u == ANNA))
        p2.start()
        self.addCleanup(p2.stop)

    def act(self, user, action, value):
        return asyncio.run(console.act(self.loop, "!r", user, action, value))

    def test_owner_changes_the_mode_contributor_cant(self):
        self.assertEqual(self.act(ANNA, "mode", "autonomous"), "ok")
        self.assertIn("only the room owner", self.act(BETTY, "mode", "autonomous"))
        self.assertEqual(self.act(BETTY, "mode", "off"), "ok")         # anyone may stop
        self.assertEqual(self.calls, [("mode", "autonomous"), ("mode", "off")])

    def test_everyone_sets_their_own_agent(self):
        self.assertEqual(self.act(BETTY, "follow", "never"), "ok")
        self.assertEqual(self.act(BETTY, "web", "on"), "ok")
        self.assertEqual(self.calls, [("follow", BETTY, "never"), ("web", BETTY, True)])

    def test_people_outside_the_room_and_odd_values_do_nothing(self):
        self.assertIn("not in that room", self.act(MALLORY, "mode", "off"))
        self.assertEqual(self.act(ANNA, "mode", "turbo"), "unknown action")
        self.assertEqual(self.calls, [])

    def test_approve_only_what_you_saw(self):
        self.loop.pending["!r"] = {"kind": "plan", "event_id": "$now", "by": ANNA}
        self.assertIn("no longer what's waiting", self.act(ANNA, "approve", "$earlier"))

    def test_agents_cant_use_the_console(self):
        self.loop.client.rooms["!r"].users[f"@anna-agent:{S}"] = 0
        self.assertIn("not in that room", self.act(f"@anna-agent:{S}", "mode", "off"))

    def test_view_shows_my_role_and_my_agent(self):
        v = asyncio.run(console.view(self.loop, "!r", BETTY))
        self.assertEqual((v["user"], v["moderator"], v["agent"], v["follow"], v["web"]),
                         ("betty", False, "betty-agent", "always", False))


if __name__ == "__main__":
    unittest.main()
