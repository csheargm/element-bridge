"""Unit tests: a listed or open project room's readable address (room_address.py), through the same
"make this room listed / private" path people use (pm_projects.set_visibility).
Run: .venv/bin/python -m unittest test_room_address"""
import asyncio
import json
import unittest
import urllib.parse
from types import SimpleNamespace
from unittest import mock

import room_address
from test_projects import ANNA, PM, S, Base, project


def run(coro):
    return asyncio.run(coro)


class Address(Base):
    def setUp(self):
        super().setUp()
        self.room = project("!a", "final project")
        self.lp.client.rooms = {"!a": self.room}
        self.members[("!a", ANNA)], self.mods["!a"] = "join", {ANNA}
        self.aliases, self.calls = {f"#lobby:{S}": "!l"}, []      # the server's directory: alias → room

        async def send(method, path, body=None, headers=None):
            self.calls.append((method, path))
            if "/directory/room/" in path:
                alias = urllib.parse.unquote(path.rsplit("/", 1)[1])
                if method == "PUT":
                    if alias in self.aliases:
                        return SimpleNamespace(status=409)
                    self.aliases[alias] = json.loads(body)["room_id"]
                elif self.aliases.pop(alias, None) is None:
                    return SimpleNamespace(status=404)
            return SimpleNamespace(status=200)
        self.lp.client.send = send
        self.private = mock.patch("project_rooms.repo_is_private", mock.AsyncMock(return_value=False))
        self.private.start()
        self.addCleanup(self.private.stop)

    def canonical(self):
        return self.state["!a"].get("m.room.canonical_alias", ({}, PM))[0].get("alias")

    def test_listing_gives_a_readable_address_and_private_takes_it_away(self):
        """Screen test: a listed room showed its raw room ID in the room directory."""
        answer = run(self.lp.set_visibility(self.room, ANNA, "listed"))
        self.assertIn(f"Its address is #final-project:{S}.", answer)
        self.assertEqual((self.canonical(), self.aliases[f"#final-project:{S}"]), (f"#final-project:{S}", "!a"))
        run(self.lp.set_visibility(self.room, ANNA, "open"))                               # open: the same address
        self.assertEqual(sum(m == "PUT" and "/directory/room/" in p for m, p in self.calls), 1)
        self.assertTrue(run(self.lp.set_visibility(self.room, ANNA, "private")).startswith("This room is now private"))
        self.assertNotIn(f"#final-project:{S}", self.aliases)
        self.assertIsNone(self.canonical())
        self.assertEqual(self.state["!a"][room_address.ADDRESS_EVENT][0], {})

    def test_a_taken_name_is_numbered_and_lobby_is_never_imitated(self):
        self.aliases[f"#final-project:{S}"] = "!other"
        self.assertIn(f"#final-project-2:{S}", run(self.lp.set_visibility(self.room, ANNA, "listed")))
        self.assertEqual(room_address.localpart("Lobby"), "lobby-project")
        self.assertEqual(room_address.localpart("!!!"), "project")

    def test_a_private_repos_name_is_never_in_the_address(self):
        self.room.display_name = "Secret One tracker"
        self.bind("!a", "a/secret-one")
        with mock.patch("project_rooms.repo_is_private", mock.AsyncMock(return_value=True)):
            answer = run(self.lp.set_visibility(self.room, ANNA, "listed"))
        self.assertIn(f"#project:{S}", answer)
        self.assertNotIn("secret", json.dumps(self.aliases))

    def test_an_address_its_owners_chose_is_left_alone(self):
        self.state["!a"] = {"m.room.canonical_alias": ({"alias": f"#theirs:{S}"}, ANNA),
                            room_address.ADDRESS_EVENT: ({"alias": f"#theirs:{S}"}, ANNA)}   # a record not mine
        self.aliases[f"#theirs:{S}"] = "!a"
        run(self.lp.set_visibility(self.room, ANNA, "listed"))
        run(self.lp.set_visibility(self.room, ANNA, "private"))
        self.assertEqual(self.aliases[f"#theirs:{S}"], "!a")
        self.assertEqual(self.canonical(), f"#theirs:{S}")
        self.assertFalse(any(m == "DELETE" for m, _ in self.calls))

    def test_a_failure_is_said_and_never_undoes_the_setting(self):
        async def refuse(method, path, body=None, headers=None):
            return SimpleNamespace(status=403 if "/directory/room/" in path else 200)
        self.lp.client.send = refuse
        answer = run(self.lp.set_visibility(self.room, ANNA, "listed"))
        self.assertTrue(answer.startswith("This room is now listed"), answer)
        self.assertIn("shows by its ID", answer)
        self.state["!a"][room_address.ADDRESS_EVENT] = ({"alias": f"#final-project:{S}"}, PM)
        self.assertIn("set it to private again to retry", run(self.lp.set_visibility(self.room, ANNA, "private")))
        self.assertEqual(self.state["!a"]["m.room.join_rules"][0], {"join_rule": "invite"})   # closed all the same


if __name__ == "__main__":
    unittest.main()
