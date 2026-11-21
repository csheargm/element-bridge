"""Unit tests: asking to join a project room, the Lobby's one-line greeting, and a chat someone opens
with @pm themselves (pm_joining.py, pm_lobby.py, pm_follow.py). Run: .venv/bin/python -m unittest test_joining"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from nio import RoomInviteError, RoomInviteResponse, RoomKickResponse, RoomResolveAliasError, RoomResolveAliasResponse

import pm_joining
import pm_lobby
import pm_welcome
import project_rooms
from test_projects import ANNA, BETTY, PM, REPO, S, Base, project

NEW = f"@newbie1:{S}"


def run(coro):
    return asyncio.run(coro)


def click(sender, key, to):
    return SimpleNamespace(sender=sender, key=key, reacts_to=to, server_timestamp=10**13)


def member(who, membership, prev=None):
    return SimpleNamespace(state_key=who, membership=membership, prev_membership=prev, server_timestamp=10**13, event_id="$m")


class AskToJoin(Base):
    def setUp(self):
        super().setUp()
        self.room = project("!a", "Alpha", mods=(ANNA, BETTY))
        self.lp.client.rooms = {"!a": self.room}
        self.lp.client.room_invite = mock.AsyncMock(return_value=RoomInviteResponse())
        self.lp.client.room_kick = mock.AsyncMock(return_value=RoomKickResponse())
        self.members.update({("!a", ANNA): "join", ("!a", BETTY): "join"})

    def asks(self):
        return [(r, b) for r, b in self.said if "asks to join" in b]

    def test_every_moderator_is_asked_with_two_buttons_and_only_theirs_counts(self):
        self.assertTrue(run(self.lp.ask_to_join("!a", NEW)))
        self.assertEqual([r for r, _ in self.asks()], [f"!dm-{ANNA}", f"!dm-{BETTY}"])
        self.assertIn(f"newbie1 ({NEW}) asks to join **Alpha**", self.asks()[0][1])     # the full ID, never just a name
        self.assertEqual([k for _, _, k in self.buttons], ["👍", "👎", "👍", "👎"])
        anna_q = next(e for e, r in self.lp._join_requests.items() if r["moderator"] == ANNA)
        dm = SimpleNamespace(room_id=f"!dm-{ANNA}")
        self.assertTrue(run(self.lp.on_join_reaction(dm, click(NEW, "👍", anna_q))))      # the asker can't let themself in
        self.lp.client.room_invite.assert_not_called()
        self.assertTrue(run(self.lp.on_join_reaction(dm, click(ANNA, "👍", anna_q))))
        self.lp.client.room_invite.assert_awaited_once_with("!a", NEW)
        self.assertEqual(self.lp._join_requests, {})                                       # settled for every moderator

    def test_a_moderator_who_lost_the_role_cant_let_anyone_in(self):
        run(self.lp.ask_to_join("!a", NEW))
        q = next(e for e, r in self.lp._join_requests.items() if r["moderator"] == BETTY)
        self.room.power_levels = SimpleNamespace(get_user_level=lambda u: 100 if u == ANNA else 0)
        with mock.patch("room_state.is_moderator", lambda c, r, u: asyncio.sleep(0, u == ANNA)):
            run(self.lp.on_join_reaction(SimpleNamespace(room_id=f"!dm-{BETTY}"), click(BETTY, "👍", q)))
        self.lp.client.room_invite.assert_not_called()
        self.assertIn("no longer an owner", self.said[-1][1])

    def test_a_declined_knock_is_turned_down(self):
        self.members[("!a", NEW)] = "knock"
        run(self.lp.on_welcome_member(self.room, member(NEW, "knock", "leave")))          # Element's "Ask to join"
        q, req = next(iter(self.lp._join_requests.items()))
        self.assertEqual(req["knock"], "$m")
        run(self.lp.on_join_reaction(SimpleNamespace(room_id=req["dm"]), click(req["moderator"], "👎", q)))
        self.lp.client.room_kick.assert_awaited_once()
        self.lp.client.room_invite.assert_not_called()

    def test_a_withdrawn_knock_or_someone_already_in_is_never_acted_on(self):
        """GPT review: a stale 👍 must not invite, a stale 👎 must not kick."""
        self.members[("!a", NEW)] = "knock"
        run(self.lp.on_knock(self.room, NEW, "$k"))
        q, req = next(iter(self.lp._join_requests.items()))
        self.members[("!a", NEW)] = "leave"                                               # they withdrew
        run(self.lp.on_join_reaction(SimpleNamespace(room_id=req["dm"]), click(req["moderator"], "👎", q)))
        self.lp.client.room_kick.assert_not_called()
        self.assertIn("no longer stands", self.said[-1][1])

    def test_a_moderator_who_left_the_room_cant_decide(self):
        run(self.lp.ask_to_join("!a", NEW))
        q = next(e for e, r in self.lp._join_requests.items() if r["moderator"] == ANNA)
        self.members[("!a", ANNA)] = "leave"                                              # power kept, membership gone
        run(self.lp.on_join_reaction(SimpleNamespace(room_id=f"!dm-{ANNA}"), click(ANNA, "👍", q)))
        self.lp.client.room_invite.assert_not_called()

    def test_a_failed_invite_leaves_the_request_open(self):
        run(self.lp.ask_to_join("!a", NEW))
        self.lp.client.room_invite = mock.AsyncMock(return_value=RoomInviteError("busy"))
        q = next(e for e, r in self.lp._join_requests.items() if r["moderator"] == ANNA)
        run(self.lp.on_join_reaction(SimpleNamespace(room_id=f"!dm-{ANNA}"), click(ANNA, "👍", q)))
        self.assertIn("didn't work", self.said[-1][1])
        self.assertEqual(len(self.lp._join_requests), 2)                                    # another click can still do it

    def test_asking_is_rate_limited(self):
        self.assertTrue(run(self.lp.ask_to_join("!a", NEW)))
        self.lp._join_requests.clear()                                                     # settled: still a day apart
        self.assertFalse(run(self.lp.ask_to_join("!a", NEW)))                              # same room: once a day
        rooms = {f"!r{i}": project(f"!r{i}", f"R{i}") for i in range(pm_joining.ASKS_PER_HOUR + 1)}
        self.lp.client.rooms.update(rooms)
        results = [run(self.lp.ask_to_join(rid, f"@carl:{S}")) for rid in rooms]
        self.assertEqual(results, [True] * pm_joining.ASKS_PER_HOUR + [False])         # a few an hour, all rooms

    def test_members_are_never_asked_about(self):
        self.assertFalse(run(self.lp.ask_to_join("!a", BETTY)))
        self.assertEqual(self.said, [])

    def test_asking_by_repo_name_says_the_same_whether_or_not_a_room_exists(self):
        self.lp.addressed_text = lambda event: event.body
        lobby = project("!l", "Lobby")
        self.bind("!a")
        async def both():
            await self.lp.on_project_text(lobby, SimpleNamespace(sender=NEW, body=f"ask to join {REPO}"))
            self.assertEqual(self.said, [("!l", pm_joining.ASKED)])                        # answered before any lookup
            await self.lp.on_project_text(lobby, SimpleNamespace(sender=NEW, body="ask to join someone/elses-private"))
            await asyncio.gather(*self.lp.busy)
        run(both())
        replies = [b for r, b in self.said if r == "!l"]
        self.assertEqual(replies, [pm_joining.ASKED, pm_joining.ASKED])
        self.assertEqual(len(self.asks()), 2)                                              # anna and betty, for Alpha only


class LobbyGreeting(Base):
    def setUp(self):
        super().setUp()
        self.lp.offer_again = mock.AsyncMock()
        a = mock.patch.object(pm_lobby.server_admins, "is_admin", lambda who: False)
        a.start()
        self.addCleanup(a.stop)
        self.lobby = project("!l", "Lobby")
        self.lobby.canonical_alias = project_rooms.LOBBY_ALIAS

    def test_one_line_once_ever_with_a_button_only_they_can_use(self):
        run(self.lp.on_welcome_member(self.lobby, member(NEW, "join", "leave")))
        run(self.lp.on_welcome_member(self.lobby, member(NEW, "join", "leave")))
        self.assertEqual(len(self.said), 1)
        self.assertIn(NEW, self.said[0][1])
        self.assertIn("Click 👍 below to get your agent", self.said[0][1])
        self.assertEqual(self.buttons, [("!l", "$e1", "👍")])
        self.assertTrue(run(self.lp.on_lobby_reaction(self.lobby, click(NEW, "👍", "$e1"))))
        self.assertFalse(run(self.lp.on_lobby_reaction(self.lobby, click(BETTY, "👍", "$e1"))))
        run(self.lp.on_lobby_reaction(self.lobby, click(NEW, "👍", "$e1")))
        self.lp.offer_again.assert_awaited_once_with(self.lobby, NEW)                       # once

    def test_a_failed_offer_doesnt_use_up_the_button(self):
        run(self.lp.on_welcome_member(self.lobby, member(NEW, "join", "leave")))
        self.lp.offer_again = mock.AsyncMock(side_effect=[RuntimeError("M_LIMIT_EXCEEDED"), None])
        run(self.lp.on_lobby_reaction(self.lobby, click(NEW, "👍", "$e1")))
        run(self.lp.on_lobby_reaction(self.lobby, click(NEW, "👍", "$e1")))
        self.assertEqual(self.lp.offer_again.await_count, 2)
        self.assertTrue(self.lp._lobby_greeted[NEW]["done"])

    def test_people_with_agents_agents_and_my_private_chats_get_nothing(self):
        dm = project("!dm", "Product Manager")
        dm.users = {PM: None, NEW: None}
        self.lp.dm_rooms = {NEW: "!dm"}
        for room, who in ((self.lobby, ANNA), (self.lobby, f"@newbie1-agent:{S}"), (self.lobby, f"@x-bot:{S}"), (dm, NEW)):
            run(self.lp.on_welcome_member(room, member(who, "join", "invite")))
        self.assertEqual(self.said, [])

    def test_the_lobby_is_created_public_and_listed_and_welcome_points_at_my_chat(self):
        self.lp.client.room_resolve_alias = mock.AsyncMock(return_value=RoomResolveAliasError("not found"))
        self.lp.client.room_create = mock.AsyncMock(return_value=SimpleNamespace(room_id="!l"))
        run(self.lp.ensure_lobby())
        kw = self.lp.client.room_create.call_args.kwargs
        self.assertEqual((kw["alias"], kw["preset"].value, kw["visibility"].value), ("lobby", "public_chat", "public"))
        self.lp.client.room_resolve_alias = mock.AsyncMock(return_value=RoomResolveAliasResponse("#lobby:x", "!l", []))
        self.lp.client.room_create.reset_mock()
        run(self.lp.ensure_lobby())
        self.lp.client.room_create.assert_not_called()
        self.lp.client.rooms = {"!w": SimpleNamespace(topic="Everyone starts here.")}
        run(self.lp.ensure_welcome_topic("!w"))
        self.assertEqual(self.state["!w"]["m.room.topic"][0], {"topic": pm_lobby.WELCOME_TOPIC})

    def test_the_pointer_names_the_next_step_for_each_server(self):
        self.assertIn("open on the left as **Product Manager**", pm_welcome.pointer(NEW))
        self.assertIn("accept it under Invites", pm_welcome.pointer("@newbie1:matrix.org"))
        for text in (pm_welcome.pointer(NEW), pm_lobby.GREET, pm_welcome.WELCOME):
            self.assertRegex(text, r"[Cc]lick")                                            # every message: the next step


class TheirOwnChatWithMe(Base):
    def test_a_newcomer_opening_a_chat_gets_the_offer_not_a_moderator_request(self):
        self.lp.client.join = mock.AsyncMock()
        self.lp.client.room_get_state_event = mock.AsyncMock()
        self.lp.only_us = mock.AsyncMock(return_value=True)
        self.lp.offer_again = mock.AsyncMock()
        room = project("!theirs", "")
        invite = SimpleNamespace(sender=NEW, state_key=PM, membership="invite", content={"is_direct": True})
        run(self.lp.on_invite(room, invite))
        self.lp.offer_again.assert_awaited_once_with(room, NEW)
        self.lp.client.room_get_state_event.assert_not_called()                             # no "make me a moderator"
        invite.sender = ANNA                                                                # has an agent
        run(self.lp.on_invite(room, invite))
        self.assertIn("@pm help", self.said[-1][1])


if __name__ == "__main__":
    unittest.main()
