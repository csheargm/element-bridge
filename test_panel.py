"""Unit tests: the side panel. Run: .venv/bin/python -m unittest test_panel"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import panel
import registry

S = "bridge.localhost"


class Render(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp()) / "agents.json"
        path.write_text(json.dumps({"anna": {}, "betty": {"follow": "ask"}}))
        p = mock.patch.object(registry, "PATH", path)
        p.start()
        self.addCleanup(p.stop)
        room = SimpleNamespace(users={f"@anna:{S}": 0, f"@anna-agent:{S}": 0, f"@betty-agent:{S}": 0}, invited_users={})
        self.loop = SimpleNamespace(client=SimpleNamespace(rooms={"!r": room}),
                                    boards={"!r": {"text": "📋 **Project status** — loop: **assist**"}},
                                    pending={"!r": {"kind": "plan", "by": f"@anna:{S}", "until": time.time() + 60}},
                                    last_plans={"!r": {"items": [{"title": "<script>alert(1)</script>", "criteria": ["x"]}]}},
                                    grants_cache={"!r": {f"@anna-agent:{S}": {"web": {"by": f"@anna:{S}"}}}})

    def test_shows_status_waiting_plan_and_whose_agent_is_whose(self):
        page = panel.render(self.loop, "!r")
        self.assertIn("<strong>Project status</strong>", page)
        self.assertIn("a plan — anna reacts 👍", page)
        self.assertIn("anna-agent — anna's agent, follows: always, may use: web", page)
        self.assertIn("betty-agent — betty's agent, follows: ask, may use: project only <span class=muted>(owner not here: idle)", page)

    def test_everything_people_wrote_is_escaped(self):
        self.assertNotIn("<script>", panel.render(self.loop, "!r"))
        self.assertIn("&lt;script&gt;", panel.render(self.loop, "!r"))

    def test_unknown_room(self):
        self.assertIn("isn't in this room", panel.render(self.loop, "!other"))


class PageScript(unittest.TestCase):
    """Seen live: an apostrophe in a button label ("it\\'s") lost its escape in the Python string and
    broke the whole console's script. Unit tests never ran the JavaScript; node checks it here."""
    def test_the_page_script_parses(self):
        import re
        import shutil
        import subprocess
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not installed")
        page = panel.PAGE.replace("__ELEMENT_ORIGIN__", json.dumps(panel.ELEMENT_ORIGIN))
        script = re.search(r"<script>(.*)</script>", page, re.S).group(1)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
        done = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[-500:])


class Widget(unittest.TestCase):
    def test_pinned_at_the_top_where_people_see_it(self):
        import asyncio
        from nio import RoomPutStateResponse
        calls = []

        async def put(room_id, kind, content, state_key=""):
            calls.append((kind, content, state_key))
            return RoomPutStateResponse("$e", room_id)
        self.assertTrue(asyncio.run(panel.add_to_room(SimpleNamespace(room_put_state=put), "!r")))
        (kind, content, key), (layout_kind, layout, _) = calls
        self.assertEqual((kind, key, content["url"]), ("im.vector.modular.widgets", "hagency", panel.URL))
        self.assertEqual(layout["widgets"]["hagency"]["container"], "top")


if __name__ == "__main__":
    unittest.main()
