"""GPT review of the federated panel sign-in (R15): each adopted point, as a regression test. DNS and
HTTP are stand-ins (tests/test_fed_openid.World); the deadline tests use the real clock with a short
deadline. Run: .venv/bin/python -m unittest tests.test_fed_openid_review"""
import asyncio
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import console
import fed_dns
import fed_https
import fed_openid
from tests.test_fed_openid import ALICE, PUBLIC, USERINFO, WELL_KNOWN, World, check

SHORT = 0.4


class Base(unittest.TestCase):
    def setUp(self):
        fed_https._cache.clear()
        fed_openid._tries.clear()
        fed_https._own[:] = [0.0, ()]
        self.addCleanup(fed_https._own.__setitem__, slice(None), [0.0, ()])


class EgressAndDeadlines(Base):
    def test_this_servers_own_public_address_and_listed_ranges_are_never_contacted(self):
        """"Public" alone still reached this host's own public interface (and a cloud's internal ranges)."""
        with World(addresses={"bridge.localhost": "45.33.32.156"}) as world:
            self.assertIsNone(check("evil.example")[0])          # evil.example is 45.33.32.156: ours
        self.assertEqual(world.connected, [])
        fed_https._own[:] = [0.0, ()]
        with mock.patch.dict(os.environ, {"HAGENCY_FED_DENY": "93.184.0.0/16, not-a-net"}), World() as world:
            self.assertIsNone(check("example.org")[0])
        self.assertEqual(world.connected, [])

    def test_a_peer_that_never_sends_its_headers_is_cut_at_the_deadline(self):
        aborted = threading.Event()

        class Stalls:
            sock = None

            def request(self, *a, **k):
                pass

            def getresponse(self):
                if not aborted.wait(5):
                    return None
                raise OSError("cut")

            def abort(self):
                aborted.set()

            def close(self):
                pass
        with World(), mock.patch.object(fed_https, "CONNECT", lambda *a: Stalls()), \
                mock.patch.object(fed_openid, "DEADLINE_S", SHORT):
            started = time.monotonic()
            self.assertIsNone(check("example.org:8448")[0])
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(aborted.is_set())

    def test_a_hanging_dns_lookup_gives_up_at_the_deadline(self):
        def hangs(*a, **k):
            time.sleep(3)
            raise OSError("late")
        with World(), mock.patch.object(fed_https.socket, "getaddrinfo", hangs), \
                mock.patch.object(fed_openid, "DEADLINE_S", SHORT):
            started = time.monotonic()
            self.assertIsNone(check("example.org:8448")[0])
        self.assertLess(time.monotonic() - started, 2)


class OddInput(Base):
    def test_names_with_a_newline_or_a_scope_are_refused(self):
        for name in ("matrix.org\n", "matrix.org:8448\n", "[fe80::1%eth0]", "[fe80::1%25eth0]:8448", "matrix.org\t"):
            self.assertIsNone(fed_https.parse_server_name(name), repr(name))
        with World() as world:
            self.assertIsNone(check("matrix.org\n")[0])
        self.assertEqual(world.connected, [])

    def test_odd_lengths_nested_json_and_bad_redirects_end_quietly(self):
        nested = b"[" * 5000 + b"]" * 5000
        for answers in ({(PUBLIC["matrix.org"], 8448, USERINFO): (200, {"Content-Length": "²"}, [b"{}"])},
                        {(PUBLIC["matrix.org"], 8448, USERINFO): (200, {}, [nested[:8000]])},
                        {(PUBLIC["matrix.org"], 443, WELL_KNOWN): (200, {}, [nested[:8000]])},
                        {(PUBLIC["matrix.org"], 443, WELL_KNOWN): (302, {"Location": "https://[bad/x"}, [b""])}):
            fed_https._cache.clear()
            with World(answers=answers):
                self.assertIsNone(check()[0])                     # never an exception (a 500)

    def test_our_own_branch_is_limited_too(self):
        for _ in range(fed_openid.PER_CLIENT_ANY):
            check("bridge.localhost", client="7.7.7.7")
        user, local = check("bridge.localhost", client="7.7.7.7")
        self.assertIsNone(user)
        local.assert_not_called()

    def test_the_route_takes_a_body_that_isnt_an_object(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        async def go():
            app = web.Application()
            app.add_routes([r for r in console.routes(SimpleNamespace(), lambda *a: "") if r.path == "/auth/openid"])
            async with TestClient(TestServer(app)) as client:
                r = await client.post("/auth/openid", json=["x"], headers={"Host": "localhost:8650"})
                return r.status, await r.json()
        self.assertEqual(asyncio.run(go()), (200, {"error": console.NOT_VERIFIED}))

    def test_expired_sessions_are_dropped(self):
        console._sessions.update({f"old{n}": (ALICE, 0, None) for n in range(console.MAX_SESSIONS + 1)})
        console.new_session(ALICE)
        self.assertFalse(any(k.startswith("old") for k in console._sessions))


class SrvChecks(unittest.TestCase):
    def answer(self, asked: str, owner: bytes = b"\xc0\x0c", target: bytes = b"\x03fed\x07example\x03org\x00", size=None):
        q = fed_dns.question(7, asked)
        rr = owner + bytes([0, 33, 0, 1, 0, 0, 0, 60, 0, size or 6 + len(target)]) + bytes([0, 10, 0, 5, 0x20, 0xfb]) + target
        data = bytearray(q + rr)
        data[2:4], data[6:8] = b"\x81\x80", b"\x00\x01"
        return bytes(data)

    def test_only_an_answer_to_our_question_counts(self):
        name = "_matrix-fed._tcp.example.org"
        self.assertEqual(fed_dns.parse(self.answer(name), 7, name), [(10, 5, 8443, "fed.example.org")])
        with self.assertRaises(fed_dns.Malformed):
            fed_dns.parse(self.answer("_matrix-fed._tcp.other.org"), 7, name)
        other_owner = b"\x05other\x03org\x00"
        self.assertEqual(fed_dns.parse(self.answer(name, owner=other_owner), 7, name), [])

    def test_a_target_running_past_its_record_or_with_a_dot_in_a_label_is_dropped(self):
        name = "_matrix-fed._tcp.example.org"
        long = b"\x03fed\x07example\x03org\x00"
        with self.assertRaises(fed_dns.Malformed):                  # the name can't end inside its record
            fed_dns.parse(self.answer(name, target=long, size=6 + 5), 7, name)
        with self.assertRaises(fed_dns.Malformed):
            fed_dns.parse(self.answer(name, target=b"\x07fed.evi\x03org\x00"), 7, name)

    def test_a_name_too_long_to_ask_about_is_no_record(self):
        with mock.patch.object(fed_dns, "ask", side_effect=AssertionError("asked")):
            self.assertEqual(fed_dns.lookup("_matrix-fed._tcp." + "a" * 250), [])
            self.assertEqual(fed_dns.lookup("_matrix-fed._tcp." + "a" * 64 + ".org"), [])


if __name__ == "__main__":
    unittest.main()
