"""Unit tests: @pm welcomes a newcomer and offers them an agent. Run: .venv/bin/python -m unittest test_welcome"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pm_welcome
import registry
import remote_agent
import room_state
from loop import Loop
from nio import RoomResolveAliasError, RoomResolveAliasResponse

S = "bridge.localhost"
NEW, ANNA, PM = f"@newbie1:{S}", f"@anna:{S}", f"@pm:{S}"
WELCOME_ROOM = SimpleNamespace(room_id="!welcome", canonical_alias=pm_welcome.WELCOME_ALIAS, users={})
DM = SimpleNamespace(room_id="!dm", canonical_alias=None, users={})


def member(who, membership="join", prev=None):
    return SimpleNamespace(state_key=who, membership=membership, prev_membership=prev, server_timestamp=10**13,
                           event_id="$m")


def text(sender, body):
    return SimpleNamespace(sender=sender, body=body, source={"content": {"body": body}})


def reaction(sender, key, to="$offer"):
    return SimpleNamespace(sender=sender, key=key, reacts_to=to, server_timestamp=10**13)


class Base(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp()) / "agents.json"
        path.write_text(json.dumps({"anna": {"agent": "hermes"}}))
        p = mock.patch.object(registry, "PATH", path)
        p.start()
        self.addCleanup(p.stop)
        self.lp = Loop.__new__(Loop)
        self.lp.user_id, self.lp.started_ms, self.lp.busy, self.lp._welcomes = PM, 0, set(), {}
        self.said, self.buttons = [], []

        async def say(room_id, body, *a, **k):
            self.said.append((room_id, body))
            return "$offer" if body.startswith("Welcome") else f"$m{len(self.said)}"

        async def thumb(room_id, event_id, key="👍"):
            self.buttons.append((room_id, event_id, key))
        self.lp.say, self.lp.offer_thumb = say, thumb
        self.lp.private_room = mock.AsyncMock(return_value="!dm")

    def run_(self, coro):
        return asyncio.run(coro)


class Greeting(Base):
    def test_a_newcomer_joining_welcome_gets_one_private_offer_with_two_buttons(self):
        self.run_(self.lp.on_welcome_member(WELCOME_ROOM, member(NEW)))
        self.lp.private_room.assert_awaited_once_with(NEW)
        self.assertEqual(self.said[0][0], "!dm")
        self.assertIn("Get your agent", self.said[0][1])
        self.assertEqual([b[2] for b in self.buttons], ["🏠", "🔌"])
        self.assertEqual(self.lp._welcomes[NEW], {"dm": "!dm", "offer": "$offer", "done": []})

    def test_only_once(self):
        self.run_(self.lp.on_welcome_member(WELCOME_ROOM, member(NEW)))
        self.run_(self.lp.on_welcome_member(WELCOME_ROOM, member(NEW, prev="leave")))   # left and came back
        self.assertEqual(len(self.said), 1)

    def test_agents_bots_people_with_agents_and_other_rooms_are_not_greeted(self):
        for room, who in ((WELCOME_ROOM, f"@newbie1-agent:{S}"), (WELCOME_ROOM, f"@admin-bot:{S}"),
                          (WELCOME_ROOM, ANNA), (WELCOME_ROOM, PM), (WELCOME_ROOM, "@newbie1:elsewhere.org"),
                          (DM, NEW)):
            self.run_(self.lp.on_welcome_member(room, member(who)))
        self.run_(self.lp.on_welcome_member(WELCOME_ROOM, member(NEW, prev="join")))   # a profile change
        self.run_(self.lp.on_welcome_member(WELCOME_ROOM, member(NEW, membership="invite")))
        self.assertEqual(self.said, [])

    def test_welcome_room_is_created_by_pm_when_missing(self):
        self.lp.client = mock.Mock(room_resolve_alias=mock.AsyncMock(return_value=RoomResolveAliasError("not found")),
                                   room_create=mock.AsyncMock(return_value=SimpleNamespace(room_id="!w")))
        self.run_(self.lp.ensure_welcome_room())
        kw = self.lp.client.room_create.call_args.kwargs
        self.assertEqual((kw["alias"], kw["preset"].value), ("welcome", "private_chat"))
        self.lp.client.room_resolve_alias = mock.AsyncMock(return_value=RoomResolveAliasResponse("#welcome:x", "!w", []))
        self.lp.client.room_create.reset_mock()
        self.lp.client.rooms = {}
        self.run_(self.lp.ensure_welcome_room())
        self.lp.client.room_create.assert_not_called()

    def test_people_who_joined_while_i_was_down_are_welcomed_at_start(self):
        """GPT review 21: a join during a restart or cold start is never lost."""
        welcome = SimpleNamespace(users={NEW: None, ANNA: None, PM: None, f"@x:{S}": None}, invited_users={f"@x:{S}": None})
        self.lp.client = mock.Mock(rooms={"!w": welcome},
                                   room_resolve_alias=mock.AsyncMock(return_value=RoomResolveAliasResponse("#w", "!w", [])))
        self.run_(self.lp.ensure_welcome_room())
        self.lp.private_room.assert_awaited_once_with(NEW)      # not anna (has an agent), not an invitee
        self.run_(self.lp.ensure_welcome_room())
        self.assertEqual(len(self.said), 1)                      # once

    def test_a_failed_greeting_is_not_remembered(self):
        self.lp.private_room = mock.AsyncMock(return_value=None)
        self.run_(self.lp.on_welcome_member(WELCOME_ROOM, member(NEW)))
        self.assertNotIn(NEW, self.lp._welcomes)                 # retried later

    def test_agents_never_follow_into_the_welcome_room(self):
        self.lp.client = mock.Mock(room_invite=mock.AsyncMock())
        self.run_(self.lp.bring_agent(SimpleNamespace(**{**vars(WELCOME_ROOM), "users": {ANNA: None}}), ANNA))
        self.lp.client.room_invite.assert_not_called()


class Buttons(Base):
    def setUp(self):
        super().setUp()
        self.run_(self.lp.on_welcome_member(WELCOME_ROOM, member(NEW)))
        self.said.clear()
        self.provision = mock.patch.object(remote_agent, "provision", return_value="newbie1-agent").start()
        self.addCleanup(mock.patch.stopall)
        remote_agent.CODES.codes = {}
        self.members = [PM, NEW]
        state = lambda room_id: SimpleNamespace(events=[{"type": "m.room.member", "state_key": u,
                                                          "content": {"membership": "join"}} for u in self.members])
        self.lp.client = mock.Mock(room_get_state=mock.AsyncMock(side_effect=state))

    def click(self, key, who=NEW, room=DM, to="$offer"):
        async def go():
            handled = await self.lp.on_welcome_reaction(room, reaction(who, key, to))
            await asyncio.gather(*self.lp.busy)
            return handled
        return self.run_(go())

    def test_house_agent_is_coming_and_offers_bring_your_own(self):
        self.assertTrue(self.click("🏠"))
        self.assertIn("comes with the public server", self.said[0][1])
        self.assertIn("bring my own", self.said[0][1])

    def test_bring_your_own_creates_the_agent_and_sends_one_line_with_a_code(self):
        self.assertTrue(self.click("🔌"))
        self.provision.assert_called_once_with("newbie1")
        room_id, body = self.said[0]
        self.assertEqual(room_id, "!dm")
        self.assertRegex(body, r"curl -fsSL http://localhost:8650/connect\.sh \| sh -s -- [A-Z2-9]{5}-[A-Z2-9]{5}")
        self.assertEqual(len(remote_agent.CODES.codes), 1)

    def test_each_button_counts_once(self):
        self.click("🔌")
        self.click("🔌")
        self.assertEqual(self.provision.call_count, 1)

    def test_only_that_person_on_that_message_in_that_chat(self):
        self.assertFalse(self.click("🔌", who=ANNA))
        self.assertFalse(self.click("🔌", room=WELCOME_ROOM))
        self.assertFalse(self.click("🔌", to="$other"))
        self.assertTrue(self.click("👍"))             # my message, but not a button: nothing happens
        self.assertEqual(self.said, [])

    def test_typing_works_too(self):
        self.run_(self.lp.on_welcome_text(DM, text(NEW, "house agent please")))
        self.assertIn("public server", self.said[-1][1])

        async def bring():
            self.assertTrue(await self.lp.on_welcome_text(DM, text(NEW, "I'll bring my own")))
            await asyncio.gather(*self.lp.busy)
        self.run_(bring())
        self.provision.assert_called_once_with("newbie1")

    def test_one_set_up_at_a_time_per_person(self):
        async def twice():
            await self.lp.on_welcome_text(DM, text(NEW, "bring my own"))
            await self.lp.on_welcome_text(DM, text(NEW, "bring my own"))
            await asyncio.gather(*self.lp.busy)
        self.run_(twice())
        self.assertEqual(self.provision.call_count, 1)

    def test_no_is_not_a_choice(self):
        self.assertFalse(self.run_(self.lp.on_welcome_text(DM, text(NEW, "I don't want to bring my own"))))
        self.provision.assert_not_called()

    def test_the_code_goes_to_a_chat_that_is_still_private(self):
        self.lp.private_room = mock.AsyncMock(return_value="!fresh")   # the old one gained a member
        self.click("🔌")
        self.assertEqual(self.said[0][0], "!fresh")

    def test_no_code_if_someone_else_is_in_the_chat_right_now(self):
        """GPT review 21 re-check: asked of the server at send time, not my cached view."""
        self.members.append(f"@mallory:{S}")
        self.click("🔌")
        self.assertNotIn("connect.sh", self.said[0][1])
        self.assertIn("couldn't set up your agent", self.said[0][1])
        self.assertEqual(remote_agent.CODES.codes, {})

    def test_a_failure_is_said_plainly(self):
        self.provision.side_effect = remote_agent.AccountError("couldn't create @newbie1-agent: M_FORBIDDEN")
        self.click("🔌")
        self.assertIn("couldn't set up your agent", self.said[0][1])


class Asking(Base):
    def test_i_want_an_agent_anywhere_opens_the_offer_privately(self):
        room = SimpleNamespace(room_id="!project", canonical_alias=None)
        self.assertTrue(self.run_(self.lp.on_welcome_text(room, text(NEW, "@pm I want an agent"))))
        self.assertIn("Get your agent", self.said[0][1])
        self.assertEqual(self.said[1], ("!project", "Sent you a private message, newbie1."))

    def test_someone_with_an_agent_is_told(self):
        room = SimpleNamespace(room_id="!project", canonical_alias=None)
        self.run_(self.lp.on_welcome_text(room, text(ANNA, "@pm can I get an agent?")))
        self.assertEqual(self.said, [("!project", "You already have an agent here: anna-agent.")])

    def test_a_same_named_person_from_another_server_gets_nothing(self):
        """GPT review 21: @newbie1:evil.org must never get a code for our newbie1's agent."""
        registry.PATH.write_text(json.dumps({"newbie1": {"agent": "remote"}}))
        room = SimpleNamespace(room_id="!project", canonical_alias=None)
        self.run_(self.lp.on_welcome_text(room, text("@newbie1:evil.org", "@pm I want an agent")))
        self.lp.private_room.assert_not_called()
        self.assertIn("only for people with an account on this server", self.said[0][1])
        self.run_(self.lp.choose("!dm", "@newbie1:evil.org", "🔌"))
        self.assertEqual(self.lp.busy, set())

    def test_project_requests_that_mention_an_agent_go_on_to_the_usual_chat(self):
        room = SimpleNamespace(room_id="!project", canonical_alias=None)
        for said in ("@pm I need an agent to review #7", "@pm can I get an agent's opinion on the plan?"):
            self.assertFalse(self.run_(self.lp.on_welcome_text(room, text(ANNA, said))), said)
        self.assertTrue(self.run_(self.lp.on_welcome_text(room, text(NEW, "@pm please, I'd like an agent!"))))

    def test_other_messages_go_on_to_the_usual_chat(self):
        room = SimpleNamespace(room_id="!project", canonical_alias=None)
        self.assertFalse(self.run_(self.lp.on_welcome_text(room, text(NEW, "@pm where are we?"))))
        self.assertFalse(self.run_(self.lp.on_welcome_text(room, text(NEW, "bring my own"))))   # not to me, not our chat


class TickWhileJoining(unittest.TestCase):
    def test_a_room_joined_during_a_tick_doesnt_crash_it(self):
        """Regression: welcoming someone opens a DM mid-tick (dictionary changed size during iteration)."""
        lp = Loop.__new__(Loop)
        lp.client = SimpleNamespace(rooms={"!a": None})
        lp.room_repos, lp.open_issues, lp.state = {}, {}, {}

        async def repo_of(client, room_id):
            client.rooms["!new"] = None
            return None
        with mock.patch.object(room_state, "repo_of", repo_of):
            asyncio.run(lp.tick())


if __name__ == "__main__":
    unittest.main()
