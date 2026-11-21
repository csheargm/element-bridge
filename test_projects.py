"""Unit tests: project rooms — one room per repo, visibility, "make me a project room", "find a
project" (pm_projects.py, project_rooms.py, room_state.py). Run: .venv/bin/python -m unittest test_projects"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nio import RoomGetStateResponse, RoomPutStateResponse

import github
import pm_projects
import project_rooms
import registry
import room_state
from loop import Loop

S = "bridge.localhost"
PM, ANNA, BETTY = f"@pm:{S}", f"@anna:{S}", f"@betty:{S}"
REPO = "your-name/hagency-hello"


def project(rid="!a", name="Alpha", *members, mods=(ANNA,)):
    users = {m: None for m in (PM, ANNA, BETTY, *members)}
    return SimpleNamespace(room_id=rid, display_name=name, users=users, invited_users={}, canonical_alias=None,
                           power_levels=SimpleNamespace(get_user_level=lambda u: 100 if u in mods else 0))


def run(coro):
    return asyncio.run(coro)


class Base(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp()) / "agents.json"
        path.write_text(json.dumps({"anna": {"agent": "hermes"}}))
        for p in (mock.patch.object(registry, "PATH", path),
                  mock.patch.object(github, "ALLOWED_REPOS", frozenset({REPO, "a/public-one", "a/secret-one"}))):
            p.start()
            self.addCleanup(p.stop)
        self.lp = Loop.__new__(Loop)
        self.lp.user_id, self.lp.busy, self.lp.dm_rooms, self.lp.room_repos = PM, set(), {}, {}
        self.lp.started_ms = 0
        self.lp._join_requests, self.lp._lobby_greeted = {}, {}
        self.said, self.buttons, self.state = [], [], {}         # state: room → {event type: (content, sender)}
        self.members = {}                                         # (room, user) → membership, as the server says
        self.lp.lock = asyncio.Lock()

        async def say(room_id, body, *a, **k):
            self.said.append((room_id, body))
            return f"$e{len(self.said)}"

        async def thumb(room_id, event_id, key="👍"):
            self.buttons.append((room_id, event_id, key))

        async def get_state(rid):
            return RoomGetStateResponse([{"type": t, "state_key": "", "sender": who, "content": c}
                                         for t, (c, who) in self.state.get(rid, {}).items()], rid)

        async def get_state_event(rid, kind, key):
            if kind == "m.room.member":
                return SimpleNamespace(content={"membership": self.members.get((rid, key))})
            return SimpleNamespace(content=self.state.get(rid, {}).get(kind, ({}, PM))[0])

        async def put_state(rid, kind, content, state_key=""):
            self.state.setdefault(rid, {})[kind] = (content, PM)
            return RoomPutStateResponse("$s", rid)
        self.lp.say, self.lp.offer_thumb = say, thumb
        self.lp.client = mock.Mock(user_id=PM, rooms={}, access_token="tok", room_get_state=get_state,
                                   room_put_state=put_state, room_get_state_event=get_state_event)
        self.lp.client.send = mock.AsyncMock(return_value=SimpleNamespace(status=200, release=lambda: None))
        self.lp.private_room = mock.AsyncMock(side_effect=lambda who: f"!dm-{who}")
        self.lp.kick = lambda: None

    def bind(self, rid, repo=REPO, runs_in=None):
        self.state.setdefault(rid, {})[github.LEVEL_EVENT] = (room_state.content("off", repo, runs_in), PM)
        self.lp.room_repos[rid] = repo


class RoomPerRepo(Base):
    def setUp(self):
        super().setUp()
        self.a, self.b = project("!a", "Alpha"), project("!b", "Beta")
        self.lp.client.rooms = {"!a": self.a, "!b": self.b}
        self.bind("!a")
        for target, value in (("pm_settings.may_write", mock.AsyncMock(return_value=True)),
                              ("github_app.installed", mock.AsyncMock(return_value=True)),
                              ("project_rooms.repo_is_private", mock.AsyncMock(return_value=True))):
            q = mock.patch(target, value)
            q.start()
            self.addCleanup(q.stop)
        self.lp.lock, self.lp.install_watches, self.lp.state = asyncio.Lock(), {}, {}
        self.lp.pending, self.lp.plans, self.lp.last_plans = {}, {}, {}

    def test_a_second_room_on_the_same_repo_gets_two_choices(self):
        answer = run(self.lp.set_repo(self.b, ANNA, REPO))
        self.assertIn("already the project of another room", answer)
        self.assertNotIn("Alpha", answer)                                   # people here may not know that room
        self.assertEqual([k for r, _, k in self.buttons], ["🙋", "➕"])
        self.assertNotIn(github.LEVEL_EVENT, self.state.get("!b", {}))      # nothing bound yet

    def test_a_separate_room_discusses_but_never_runs_the_loop(self):
        run(self.lp.set_repo(self.b, ANNA, REPO))
        offer = self.buttons[0][1]
        click = SimpleNamespace(sender=ANNA, key="➕", reacts_to=offer)
        self.assertTrue(run(self.lp.on_conflict_reaction(self.b, click)))
        content = self.state["!b"][github.LEVEL_EVENT][0]
        self.assertEqual((room_state.runs_in(content), room_state.level_of({**content, "level": "autonomous"})), ("!a", "off"))
        self.assertIn("build loop runs in the room that already works on it", self.said[-1][1])
        refused = run(self.lp.change_level("!b", "autonomous"))           # assist/autonomous refused there
        self.assertEqual(refused, pm_projects.RUN_ELSEWHERE)
        self.assertIsNone(run(self.lp.change_level("!b", "off")))
        self.assertEqual(room_state.runs_in(self.state["!b"][github.LEVEL_EVENT][0]), "!a")   # kept on a stop

    def test_only_a_moderator_chooses_and_ask_to_join_goes_to_the_other_rooms_owners(self):
        run(self.lp.set_repo(self.b, ANNA, REPO))
        offer = self.buttons[0][1]
        self.assertTrue(run(self.lp.on_conflict_reaction(self.b, SimpleNamespace(sender=BETTY, key="➕", reacts_to=offer))))
        self.assertNotIn(github.LEVEL_EVENT, self.state.get("!b", {}))      # betty isn't a moderator: no effect
        self.a.users.pop(ANNA)
        self.a.power_levels = SimpleNamespace(get_user_level=lambda u: 100 if u == BETTY else 0)
        run(self.lp.on_conflict_reaction(self.b, SimpleNamespace(sender=ANNA, key="🙋", reacts_to=offer)))
        asks = [(r, b) for r, b in self.said if r == f"!dm-{BETTY}"]
        self.assertEqual(len(asks), 1)
        self.assertIn(f"anna ({ANNA}) asks to join **Alpha**", asks[0][1])

    def test_two_rooms_choosing_at_once_never_both_run_the_repo(self):
        """GPT review: the check and the bind are one step under the lock."""
        self.state.pop("!a")
        self.lp.room_repos.clear()
        async def both():
            return await asyncio.gather(self.lp.set_repo(self.a, ANNA, REPO), self.lp.set_repo(self.b, ANNA, REPO))
        answers = run(both())
        self.assertEqual(sum("now works on" in a for a in answers), 1)
        self.assertEqual(sum("another room" in a for a in answers), 1)

    def test_right_after_a_start_bound_rooms_are_still_found(self):
        self.lp.room_repos.clear()                                        # the poll hasn't run yet
        self.assertIn("another room", run(self.lp.set_repo(self.b, ANNA, REPO)))

    def test_an_old_question_is_dropped_once_the_room_binds(self):
        run(self.lp.set_repo(self.b, ANNA, REPO))
        offer = self.buttons[0][1]
        self.state.pop("!a")
        self.lp.room_repos.pop("!a")
        run(self.lp.set_repo(self.b, ANNA, REPO))                         # !a let go: !b now runs it
        self.assertFalse(run(self.lp.on_conflict_reaction(self.b, SimpleNamespace(sender=ANNA, key="➕", reacts_to=offer))))

    def test_a_discussion_room_is_never_the_repos_primary(self):
        self.bind("!b", runs_in="!a")
        self.assertEqual(run(self.lp.primary_room_for(REPO, exclude="!a")), None)
        self.assertEqual(run(self.lp.primary_room_for(REPO.upper())), "!a")


class Visibility(Base):
    def setUp(self):
        super().setUp()
        self.room = project("!a", "Alpha")
        self.lp.client.rooms = {"!a": self.room}
        self.private = mock.patch("project_rooms.repo_is_private", mock.AsyncMock(return_value=False))
        self.private.start()
        self.addCleanup(self.private.stop)

    def test_each_visibility_sets_its_join_rule_and_directory_entry(self):
        for value, rule, listed in (("listed", "knock", "public"), ("open", "public", "public"), ("private", "invite", "private")):
            answer = run(self.lp.set_visibility(self.room, ANNA, value))
            self.assertTrue(answer.startswith(f"This room is now {value}"), answer)
            self.assertEqual(self.state["!a"]["m.room.join_rules"][0], {"join_rule": rule})
            self.assertEqual(json.loads(self.lp.client.send.call_args.args[2]), {"visibility": listed})
            self.assertEqual(run(project_rooms.visibility(self.lp.client, "!a")), value)

    def test_only_moderators_and_never_open_on_a_private_repo(self):
        self.assertIn("Only the room owner", run(self.lp.set_visibility(self.room, BETTY, "open")))
        self.bind("!a")
        with mock.patch("project_rooms.repo_is_private", mock.AsyncMock(return_value=True)):
            self.assertEqual(run(self.lp.set_visibility(self.room, ANNA, "open")), project_rooms.NOT_OPEN)
            self.assertTrue(run(self.lp.set_visibility(self.room, ANNA, "listed")).startswith("This room is now listed"))
        self.assertNotIn("public", json.dumps(self.state["!a"]["m.room.join_rules"][0]))

    def test_an_open_room_is_closed_before_a_private_repo_is_bound(self):
        """GPT review: by its real join rule (not my record), and no bind if closing fails."""
        self.state["!a"] = {"m.room.join_rules": ({"join_rule": "public"}, ANNA)}   # set by hand in Element
        with mock.patch("project_rooms.repo_is_private", mock.AsyncMock(return_value=True)):
            refused, note = run(self.lp.close_for_private("!a", REPO))
            self.assertIsNone(refused)
            self.assertIn("now listed instead of open", note)
            self.assertEqual(self.state["!a"]["m.room.join_rules"][0], {"join_rule": "knock"})
            self.state["!a"]["m.room.join_rules"] = ({"join_rule": "public"}, ANNA)
            self.lp.client.send = mock.AsyncMock(return_value=SimpleNamespace(status=500, release=lambda: None))
            refused, _ = run(self.lp.close_for_private("!a", REPO))
        self.assertIn("I haven't set the repo", refused)

    def test_someone_elses_record_is_ignored_and_front_doors_have_no_setting(self):
        self.state["!a"] = {project_rooms.VISIBILITY_EVENT: ({"visibility": "open"}, ANNA)}
        self.assertEqual(run(project_rooms.visibility(self.lp.client, "!a")), "private")
        lobby = project("!l", "Lobby")
        lobby.canonical_alias = project_rooms.LOBBY_ALIAS
        self.assertEqual(run(self.lp.set_visibility(lobby, ANNA, "private")), "Only project rooms have this setting.")

    def test_a_failure_at_any_step_is_said_and_the_record_is_written_last(self):
        self.lp.client.send = mock.AsyncMock(return_value=SimpleNamespace(status=403, release=lambda: None))
        self.assertIn("couldn't update the room directory", run(self.lp.set_visibility(self.room, ANNA, "listed")))
        self.assertNotIn(project_rooms.VISIBILITY_EVENT, self.state["!a"])

    def test_unreadable_repos_count_as_private(self):
        self.private.stop()
        with mock.patch("github.run_json", mock.AsyncMock(side_effect=github.GitHubError("no"))):
            self.assertTrue(run(project_rooms.repo_is_private(REPO)))
        with mock.patch("github.run_json", mock.AsyncMock(return_value={"private": False})):
            self.assertFalse(run(project_rooms.repo_is_private(REPO)))
        self.private.start()


class MakeAndFind(Base):
    def test_make_me_a_project_room(self):
        self.lp.client.room_create = mock.AsyncMock(return_value=SimpleNamespace(room_id="!new"))
        origin = project("!o", "Lobby")
        self.lp.addressed_text = lambda event: event.body
        with mock.patch("panel.add_to_room", mock.AsyncMock(return_value=True)) as panel:
            ok = run(self.lp.on_project_text(origin, SimpleNamespace(sender=ANNA, body="make me a project room called Rocket")))
        self.assertTrue(ok)
        kw = self.lp.client.room_create.call_args.kwargs
        self.assertEqual((kw["name"], kw["invite"], kw["power_level_override"]), ("Rocket", [ANNA], {"users": {ANNA: 100}}))
        panel.assert_awaited_once()
        self.assertIn("pick its GitHub repo", self.said[0][1])                # the next step, in the room
        self.assertIn("accept its invite", self.said[1][1])                   # and where the room is
        for _ in range(3):
            run(self.lp.make_project_room(origin, ANNA, ""))
        self.assertIn("enough new rooms", self.said[-1][1])                   # a few an hour

    def test_find_lists_only_listed_and_open_rooms_and_never_a_private_repos_name(self):
        rooms = {"!pub": project("!pub", "Pub"), "!sec": project("!sec", "Sec"), "!hid": project("!hid", "Hidden")}
        self.lp.client.rooms = rooms
        self.bind("!pub", "a/public-one")
        self.bind("!sec", "a/secret-one")
        self.state["!pub"][project_rooms.VISIBILITY_EVENT] = ({"visibility": "open"}, PM)
        self.state["!sec"][project_rooms.VISIBILITY_EVENT] = ({"visibility": "listed"}, PM)
        with mock.patch("project_rooms.repo_is_private", lambda repo: asyncio.sleep(0, repo == "a/secret-one")):
            text = run(self.lp.find_projects())
        self.assertIn("**Pub** · a/public-one", text)
        self.assertIn("**Sec**", text)
        self.assertNotIn("secret-one", text)
        self.assertNotIn("Hidden", text)
        self.assertIn("matrix.to/#/%21sec", text)


class Panel(Visibility):
    def test_the_panel_switch_goes_through_the_same_checks_and_says_it_in_the_room(self):
        import console
        self.lp.is_person = lambda room, user: True
        self.assertTrue(run(console.act(self.lp, "!a", ANNA, "visibility", "listed")).startswith("This room is now listed"))
        self.assertEqual(self.said[-1][0], "!a")
        self.assertIn("Only the room owner", run(console.act(self.lp, "!a", BETTY, "visibility", "open")))

    def test_the_panel_shows_where_a_discussion_rooms_repo_is_run(self):
        import console
        self.lp.client.rooms["!b"] = project("!b", "Beta")
        self.bind("!b", runs_in="!a")
        shown = run(console.runs_in(self.lp, "!b", ANNA))
        self.assertEqual(shown["name"], "Alpha")
        self.assertTrue(shown["link"].endswith("/#/room/%21a"))
        self.assertEqual(run(console.runs_in(self.lp, "!b", "@carl:x")), {"name": "another room", "link": ""})
        self.assertIsNone(run(console.runs_in(self.lp, "!a", ANNA)))

    def test_room_names_are_shown_as_text_never_as_links_or_instructions(self):
        self.assertEqual(project_rooms.plain("[Approve](http://x)\n**now**"), "\\[Approve\\]\\(http://x\\) \\*\\*now\\*\\*")


if __name__ == "__main__":
    unittest.main()
