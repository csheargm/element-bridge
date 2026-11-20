"""Unit tests: the live Project status board. Run: .venv/bin/python -m unittest test_status_board"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nio import RoomGetStateEventResponse, RoomPutStateResponse, RoomSendResponse

import status_board
from status_board import Boards, render
from types import SimpleNamespace

NOW = 1_000_000.0
A = "@anna-hermes-agent:bridge.localhost"


class Render(unittest.TestCase):
    def test_shows_step_agent_and_time(self):
        text = render({"210": {"room": "!r", "stage": "building", "agent": A, "since": NOW - 180,
                               "title": "docs: add HERMES.md"}}, "autonomous", [], NOW)
        self.assertIn("loop: **autonomous**", text)
        self.assertIn("#210 docs: add HERMES.md — building · anna-hermes-agent · 3 min", text)

    def test_queue_done_and_stopped(self):
        items = {"200": {"stage": "done", "ended": NOW - 60, "title": "a"},
                 "201": {"stage": "done", "pr": 301, "ended": NOW - 60, "title": "b"},
                 "96": {"stage": "stopped", "why": "PR #97 has merge conflicts", "ended": NOW - 60},
                 "50": {"stage": "done", "pr": 51, "ended": NOW - 2 * status_board.RECENT_S}}
        text = render(items, "assist", [{"number": 211, "title": "docs: add BETTY.md"}], NOW)
        self.assertIn("- #211 docs: add BETTY.md", text)
        self.assertIn("#200 a (closed: already done)", text)
        self.assertIn("#201 b → PR #301 merged", text)
        self.assertIn("#96 — PR #97 has merge conflicts", text)
        self.assertNotIn("#50", text)                       # older than a day
        self.assertIn("nothing in progress", text)

    def test_never_shows_paths_or_tasks(self):
        text = render({"7": {"stage": "building", "agent": A, "since": NOW, "body": "/Users/x/secret",
                             "task": {"kind": "build"}}}, "autonomous", [], NOW)
        self.assertNotIn("/Users", text)


class FakeClient:
    def __init__(self):
        self.rooms = {"!r": None}
        self.sent, self.pins = [], None

    async def room_send(self, room_id, kind, content):
        self.sent.append(content)
        return RoomSendResponse(f"$e{len(self.sent)}", room_id)

    async def room_get_state_event(self, room_id, kind, key):
        return RoomGetStateEventResponse({"pinned": ["$other"]}, kind, key, room_id)

    async def room_put_state(self, room_id, kind, content):
        if kind == "m.room.pinned_events":
            self.pins = content["pinned"]
        else:
            self.status = content
        return RoomPutStateResponse("$p", room_id)


class Board(unittest.TestCase):
    def setUp(self):
        self.b = Boards()
        self.b.client, self.b.state, self.b.open_issues = FakeClient(), {}, {}
        self.b.board_file = Path(tempfile.mkdtemp()) / "boards.json"
        self.b.boards = self.b.load_boards()

        async def level(room_id):
            return "autonomous"
        self.b.level = level

    def refresh(self):
        asyncio.run(self.b.refresh_boards())

    def test_first_refresh_posts_and_pins_keeping_other_pins(self):
        self.refresh()
        self.assertEqual(len(self.b.client.sent), 1)
        self.assertEqual(self.b.client.pins, ["$other", "$e1"])

    def test_unchanged_text_is_not_reposted(self):
        self.refresh()
        self.refresh()
        self.assertEqual(len(self.b.client.sent), 1)

    def test_change_edits_the_same_message(self):
        self.refresh()
        self.b.state["7"] = {"room": "!r", "stage": "building", "agent": A, "since": 0, "title": "x"}
        self.refresh()
        edit = self.b.client.sent[-1]
        self.assertEqual(edit["m.relates_to"], {"rel_type": "m.replace", "event_id": "$e1"})
        self.assertIn("#7 x — building", edit["m.new_content"]["body"])
        self.assertEqual(self.b.boards["!r"]["event_id"], "$e1")

    def test_pin_is_retried_until_allowed(self):
        """Seen live: the board was posted before @pm was made a moderator, and never pinned."""
        refused = {"yes": True}

        async def put_state(room_id, kind, content):
            if refused["yes"]:
                return SimpleNamespace(message="M_FORBIDDEN")
            if kind == "m.room.pinned_events":
                self.b.client.pins = content["pinned"]
            return RoomPutStateResponse("$p", room_id)
        self.b.client.room_put_state = put_state
        self.refresh()
        self.assertIsNone(self.b.client.pins)
        refused["yes"] = False                               # anna makes @pm a moderator
        self.refresh()                                       # same text: no repost, but the pin is retried
        self.assertEqual(self.b.client.pins, ["$other", "$e1"])
        self.assertEqual(len(self.b.client.sent), 1)

    def test_status_is_published_for_agents_to_read(self):
        """the owner: agents answered from their own memory ('awaiting room approval') after the project was done."""
        import github
        self.refresh()
        self.assertEqual(self.b.client.status["repo"], github.REPO)
        self.assertIn("Project status", self.b.client.status["text"])

    def test_board_survives_a_restart(self):
        self.refresh()
        fresh = Boards()
        fresh.board_file = self.b.board_file
        self.assertEqual(fresh.load_boards()["!r"]["event_id"], "$e1")


if __name__ == "__main__":
    unittest.main()
