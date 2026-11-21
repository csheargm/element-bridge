"""Unit tests: the Synapse module that opens a person's private chats from @pm and their own agent
already joined. Run from deploy/: python3 -m unittest test_auto_accept"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent / "synapse_modules"))
import hagency_auto_accept as m  # noqa: E402

S = "s.org"
PM, ANNA, BOB = f"@pm:{S}", f"@anna:{S}", f"@bob:{S}"
ANNA_AGENT, BOB_AGENT = f"@anna-agent:{S}", f"@bob-agent:{S}"


def invite(sender: str, invitee: str, is_direct=True, membership="invite", room="!r:s.org") -> dict:
    content = {"membership": membership, **({"is_direct": is_direct} if is_direct is not None else {})}
    return {"type": "m.room.member", "state_key": invitee, "sender": sender, "content": content, "room_id": room}


class ShouldAccept(unittest.TestCase):
    def test_pm_and_the_persons_own_agent(self):
        self.assertTrue(m.should_accept(invite(PM, ANNA), S, PM))
        self.assertTrue(m.should_accept(invite(ANNA_AGENT, ANNA), S, PM))

    def test_never_someone_elses_agent_or_another_person(self):
        self.assertFalse(m.should_accept(invite(BOB_AGENT, ANNA), S, PM))
        self.assertFalse(m.should_accept(invite(BOB, ANNA), S, PM))
        self.assertFalse(m.should_accept(invite("@pm:evil.org", ANNA), S, PM))       # same name, another server
        self.assertFalse(m.should_accept(invite("@anna-agent:evil.org", ANNA), S, PM))

    def test_agents_bots_and_pm_keep_deciding_for_themselves(self):
        """bridge.py joins only its owner's or @pm's rooms; pm_follow.py ignores agents and bots."""
        for invitee in (ANNA_AGENT, f"@admin-bot:{S}", PM):
            for sender in (PM, ANNA, BOB):
                self.assertFalse(m.should_accept(invite(sender, invitee), S, PM), (sender, invitee))

    def test_only_direct_invites_to_people_on_this_server(self):
        self.assertFalse(m.should_accept(invite(PM, ANNA, is_direct=False), S, PM))
        self.assertFalse(m.should_accept(invite(PM, ANNA, is_direct=None), S, PM))
        self.assertFalse(m.should_accept(invite(PM, ANNA, is_direct="true"), S, PM))   # only a real true
        self.assertFalse(m.should_accept(invite(PM, "@anna:other.org"), S, PM))
        self.assertFalse(m.should_accept(invite(PM, ANNA, membership="join"), S, PM))
        self.assertFalse(m.should_accept({**invite(PM, ANNA), "type": "m.room.message"}, S, PM))
        for broken in ({**invite(PM, ANNA), "content": "x"}, {"type": "m.room.member"},
                       {**invite(PM, ANNA), "state_key": 5}, {**invite(PM, f"@:{S}")}):
            self.assertFalse(m.should_accept(broken, S, PM), broken)

    def test_direct_map_lists_the_room_once_and_keeps_the_rest(self):
        out = m.with_direct({BOB: ("!a:s",), "junk": 5}, PM, "!r:s")
        self.assertEqual(out, {BOB: ["!a:s"], PM: ["!r:s"]})
        self.assertEqual(m.with_direct(out, PM, "!r:s"), out)
        self.assertEqual(m.with_direct(None, PM, "!r:s"), {PM: ["!r:s"]})


class FakeApi:
    def __init__(self, user=None, fail=0):
        self.user = user if user is not None else SimpleNamespace(is_deactivated=False, suspended=False, locked=False)
        self.fail, self.joins, self.data, self.slept, self.cb = fail, [], {}, [], None
        self.account_data_manager = SimpleNamespace(get_global=self.get_global, put_global=self.put_global)

    def register_third_party_rules_callbacks(self, on_new_event):
        self.cb = on_new_event

    async def get_userinfo_by_id(self, user_id):
        return self.user or None

    def run_as_background_process(self, desc, func, *args):
        asyncio.get_running_loop().create_task(func(*args))

    async def sleep(self, seconds):
        self.slept.append(seconds)

    async def update_room_membership(self, **kw):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("busy")
        self.joins.append(kw)

    async def get_global(self, user_id, kind):
        return self.data.get((user_id, kind))

    async def put_global(self, user_id, kind, value):
        self.data[(user_id, kind)] = value


class Event(SimpleNamespace):
    def get_dict(self):
        return self.raw


def event(d: dict) -> Event:
    return Event(raw=d, type=d.get("type"), state_key=d.get("state_key"), sender=d.get("sender"), room_id=d.get("room_id"))


def deliver(api: FakeApi, d: dict) -> None:
    async def go():
        await api.cb(event(d))
        await asyncio.gather(*(asyncio.all_tasks() - {asyncio.current_task()}))   # the background join
    asyncio.run(go())


class Module(unittest.TestCase):
    def make(self, **kw) -> FakeApi:
        api = FakeApi(**kw)
        m.AutoAccept(m.AutoAccept.parse_config({"pm": PM, "server": S}), api)
        return api

    def test_pms_chat_is_joined_and_marked_direct(self):
        api = self.make()
        deliver(api, invite(PM, ANNA))
        self.assertEqual(api.joins, [{"sender": ANNA, "target": ANNA, "room_id": "!r:s.org", "new_membership": "join"}])
        self.assertEqual(api.data[(ANNA, "m.direct")], {PM: ["!r:s.org"]})

    def test_anything_else_is_left_as_an_invite(self):
        api = self.make()
        deliver(api, invite(BOB, ANNA_AGENT))
        self.assertEqual((api.joins, api.data), ([], {}))

    def test_never_for_an_account_that_cant_use_it(self):
        for user in (False, SimpleNamespace(is_deactivated=True, suspended=False, locked=False),
                     SimpleNamespace(is_deactivated=False, suspended=True, locked=False),
                     SimpleNamespace(is_deactivated=False, suspended=False, locked=True)):
            api = self.make(user=user)
            deliver(api, invite(PM, ANNA))
            self.assertEqual(api.joins, [], user)

    def test_a_failed_join_is_retried_then_left_for_the_person(self):
        api = self.make(fail=1)
        deliver(api, invite(ANNA_AGENT, ANNA))
        self.assertEqual(len(api.joins), 1)
        api = self.make(fail=m.JOIN_TRIES)
        with self.assertLogs(m.log, "WARNING") as logs:
            deliver(api, invite(PM, ANNA))
        self.assertEqual(api.joins, [])
        self.assertIn("couldn't auto-join", logs.output[0])           # in Synapse's log, not silent

    def test_config_must_name_pm_on_this_server(self):
        for bad in ({}, {"pm": PM}, {"pm": "pm", "server": S}, {"pm": "@pm:other.org", "server": S}, None):
            with self.assertRaises(ValueError):
                m.AutoAccept.parse_config(bad)


if __name__ == "__main__":
    unittest.main()
