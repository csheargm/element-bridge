"""@pm's front doors (the Lobby, Welcome) are never project rooms: no Hagency panel there, and one
added before is taken out at the next start (screen test: a newcomer saw the panel in the Lobby,
stuck at "Signing in…"). Run: .venv/bin/python -m unittest tests.test_front_doors"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nio import RoomGetStateEventError, RoomGetStateEventResponse, RoomPutStateResponse

import console
import panel
import project_rooms
import room_state
from status_board import Boards

PM, NEW = "@pm:bridge.localhost", "@finaltest2:bridge.localhost"


class Client:
    """Room state as the server keeps it: (room, type, key) → content."""
    def __init__(self, rooms):
        self.rooms, self.state, self.writes, self.unreadable = rooms, {}, [], False

    async def room_get_state_event(self, room_id, kind, key=""):
        if self.unreadable:
            return RoomGetStateEventError("busy", "M_UNKNOWN")
        if (room_id, kind, key) not in self.state:
            return RoomGetStateEventError("not found", "M_NOT_FOUND")
        return RoomGetStateEventResponse(self.state[(room_id, kind, key)], kind, key, room_id)

    async def room_put_state(self, room_id, kind, content, state_key=""):
        self.state[(room_id, kind, state_key)] = content
        self.writes.append((room_id, kind))
        return RoomPutStateResponse("$s", room_id)


def room(room_id, alias=None):
    levels = SimpleNamespace(get_user_level=lambda u: 100)
    return SimpleNamespace(room_id=room_id, name="x", canonical_alias=alias, power_levels=levels,
                           users={PM: 0, NEW: 0}, invited_users={})


class NoPanelAtTheFrontDoor(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(room_state, "repo_of", mock.AsyncMock(return_value=None))
        p.start()
        self.addCleanup(p.stop)
        self.client = Client({"!lobby": room("!lobby", project_rooms.LOBBY_ALIAS),
                              "!welcome": room("!welcome", project_rooms.WELCOME_ALIAS), "!p": room("!p")})
        self.b = Boards()
        self.b.client, self.b.state, self.b.open_issues = self.client, {}, {}
        self.b.board_file = Path(tempfile.mkdtemp()) / "boards.json"
        self.b.boards, self.b.user_id, self.b.dm_rooms = {}, PM, {}

    def widget(self, room_id):
        return self.client.state.get((room_id, panel.WIDGET_KIND, panel.WIDGET_ID))

    def test_never_added_in_the_lobby_or_welcome_but_in_a_project_room(self):
        asyncio.run(self.b.refresh_boards())
        self.assertIsNone(self.widget("!lobby"))
        self.assertIsNone(self.widget("!welcome"))
        self.assertEqual(self.widget("!p")["url"], panel.widget_url())

    def test_one_added_before_is_taken_out_once_and_other_widgets_stay(self):
        asyncio.run(panel.add_to_room(self.client, "!lobby"))           # as an older @pm did
        layout = self.client.state[("!lobby", panel.LAYOUT_KIND, "")]
        layout["widgets"]["other"] = {"container": "right"}
        self.b.boards["!lobby"] = {"panel": True}
        asyncio.run(self.b.refresh_boards())
        self.assertEqual(self.widget("!lobby"), {})                       # gone from Element
        self.assertEqual(self.client.state[("!lobby", panel.LAYOUT_KIND, "")]["widgets"], {"other": {"container": "right"}})
        self.assertNotIn("panel", self.b.boards["!lobby"])
        writes = len(self.client.writes)
        asyncio.run(self.b.refresh_boards())                              # checked once per start
        self.assertEqual(len(self.client.writes), writes)

    def test_an_unreadable_room_is_retried_at_the_next_poll(self):
        asyncio.run(panel.add_to_room(self.client, "!lobby"))
        self.client.unreadable = True
        asyncio.run(self.b.refresh_boards())
        self.assertIn("url", self.widget("!lobby"))
        self.client.unreadable = False
        asyncio.run(self.b.refresh_boards())
        self.assertEqual(self.widget("!lobby"), {})


class PanelOpenedInTheLobby(unittest.TestCase):
    def test_says_it_works_in_project_rooms_only(self):
        lobby = room("!lobby", project_rooms.LOBBY_ALIAS)
        loop = SimpleNamespace(client=SimpleNamespace(rooms={"!lobby": lobby}), is_person=lambda r, u: u in r.users)
        self.assertEqual(asyncio.run(console.view(loop, "!lobby", NEW)), {"error": project_rooms.NOT_A_PROJECT})


if __name__ == "__main__":
    unittest.main()
