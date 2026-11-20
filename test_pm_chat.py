"""Unit tests: talking to @pm in plain words. Run: .venv/bin/python -m unittest test_pm_chat"""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from nio import RoomGetStateResponse, RoomPutStateResponse

import github
import intents
import room_state
from loop import Loop

ANNA, BETTY = "@anna:bridge.localhost", "@betty:bridge.localhost"     # anna owns the room
PM = f"@{github.PM_BOT}:bridge.localhost"


class Intents(unittest.TestCase):
    def test_plain_words(self):
        cases = {"do this project all the way to the end": intents.AUTONOMOUS,
                 "just get it done": intents.AUTONOMOUS, "get all done": intents.AUTONOMOUS,
                 "build it but let us approve each change": intents.ASSIST,
                 "check with us first": intents.ASSIST,
                 "stop": intents.OFF, "turn it all off": intents.OFF, "kill all the AI": intents.OFF,
                 "pause everything": intents.OFF,
                 "where are we?": intents.STATUS, "what's left": intents.STATUS,
                 "let's build it": intents.PLAN, "that's great! let's do it!": intents.PLAN,
                 "go for it": intents.PLAN, "can you reiterate teh plan": intents.SHOW_PLAN,
                 "show me the plan": intents.SHOW_PLAN, "let me know your plan": intents.SHOW_PLAN,
                 "what's your model?": intents.ABOUT, "help": intents.HELP, "hello there": intents.UNKNOWN}
        for text, want in cases.items():
            self.assertEqual(intents.classify(text), want, text)

    def test_mixed_signals_take_the_safer_meaning(self):
        self.assertEqual(intents.classify("just get it done but check with us before merging"), intents.ASSIST)
        self.assertEqual(intents.classify("take it all the way, actually no, stop"), intents.OFF)

    def test_any_ok_emoji_confirms(self):
        """Seen live: anna reacted 👌, which wasn't accepted."""
        for key in ("✅", "👌", "👍🏽", "✔️", "☑️", "🆗"):
            self.assertTrue(intents.is_confirm_reaction(key), key)
        for key in ("❤️", "👎", "😂"):
            self.assertFalse(intents.is_confirm_reaction(key), key)

    def test_yes(self):
        for t in ("yes", "Yes!", "✅", "ok", "do it"):
            self.assertTrue(intents.is_yes(t), t)
        self.assertFalse(intents.is_yes("yes but only the readme"))


class Talk(unittest.TestCase):
    def setUp(self):
        self.lp = Loop.__new__(Loop)
        self.lp.user_id, self.lp.pending, self.lp.boards = PM, {}, {}
        self.said, self.levels = [], []

        async def say(room_id, body, mention=None, task=None):
            self.said.append(body)
            return f"$m{len(self.said)}"

        async def put_state(room_id, kind, content):
            self.levels.append(content["level"])
            return RoomPutStateResponse("$s", room_id)
        create = RoomGetStateResponse([{"type": "m.room.create", "sender": ANNA, "content": {"room_version": "12"}}], "!r")
        self.lp.say = say
        self.lp.client = SimpleNamespace(room_put_state=put_state, room_get_state=mock.AsyncMock(return_value=create))
        self.room = SimpleNamespace(room_id="!r", users={ANNA: None, BETTY: None}, invited_users={},
                                    power_levels=SimpleNamespace(get_user_level=lambda u: 0))

    def tell(self, sender, body, mention=True):
        content = {"body": body, "m.mentions": {"user_ids": [PM]} if mention else {}}
        ev = SimpleNamespace(sender=sender, body=body, source={"content": content})
        asyncio.run(self.lp.on_human(self.room, ev))

    def react(self, sender, key, to):
        asyncio.run(self.lp.on_reaction(self.room, SimpleNamespace(sender=sender, key=key, reacts_to=to)))

    def test_anyone_can_stop_at_once(self):
        self.tell(BETTY, "@pm stop everything")
        self.assertEqual(self.levels, ["off"])
        self.assertIn("Stopped", self.said[-1])

    def test_autonomous_waits_for_the_owners_check(self):
        self.tell(ANNA, "@pm take it all the way")
        self.assertEqual(self.levels, [])
        self.assertIn("✅ to confirm", self.said[-1])
        self.react(BETTY, "✅", "$m1")                       # not a maintainer: ignored
        self.assertEqual(self.levels, [])
        self.react(ANNA, "✅", "$m1")
        self.assertEqual(self.levels, ["autonomous"])

    def test_yes_in_text_confirms_too_but_not_after_it_expires(self):
        self.tell(ANNA, "pm: just get it done")
        self.lp.pending["!r"]["until"] = time.time() - 1
        self.tell(ANNA, "yes", mention=False)
        self.assertEqual(self.levels, [])
        self.tell(ANNA, "pm: just get it done")
        self.tell(ANNA, "yes", mention=False)
        self.assertEqual(self.levels, ["autonomous"])

    def test_only_the_asker_confirms(self):
        """GPT review: any maintainer's unrelated "yes" used to confirm."""
        self.room.power_levels = SimpleNamespace(get_user_level=lambda u: 50)    # betty is a maintainer too
        self.tell(ANNA, "@pm take it all the way")
        self.tell(BETTY, "yes", mention=False)
        self.react(BETTY, "✅", "$m1")
        self.assertEqual(self.levels, [])
        self.react(ANNA, "✅", "$m1")
        self.assertEqual(self.levels, ["autonomous"])

    def test_a_mode_change_cancels_the_pending_switch(self):
        self.tell(ANNA, "@pm take it all the way")
        self.tell(ANNA, "!level assist", mention=False)
        self.react(ANNA, "✅", "$m1")
        self.assertEqual(self.levels, ["assist"])

    def test_malformed_mentions_are_ignored(self):
        ev = SimpleNamespace(sender=ANNA, body="stop", source={"content": {"m.mentions": {"user_ids": 7}}})
        asyncio.run(self.lp.on_human(self.room, ev))
        self.assertEqual(self.levels, [])

    def test_stop_cancels_a_pending_switch(self):
        self.tell(ANNA, "@pm take it all the way")
        self.tell(BETTY, "@pm wait, stop")
        self.react(ANNA, "✅", "$m1")
        self.assertEqual(self.levels, ["off"])

    def test_contributors_cannot_change_the_mode(self):
        self.tell(BETTY, "@pm take it all the way")
        self.tell(BETTY, "@pm let us approve each change")
        self.assertEqual(self.levels, [])
        self.assertIn("Only the room owner or a maintainer", self.said[-1])

    def test_a_contributor_can_ask_the_owner_to_start(self):
        """Seen in the first live demo: betty said "@pm let's do it!"."""
        started = []

        async def start_plan(room, sender):
            started.append(sender)
        self.lp.start_plan = start_plan
        self.tell(BETTY, "@pm that's great! let's do it!")
        self.assertIn("betty would like to turn this discussion into work", self.said[-1])
        self.react(BETTY, "✅", "$m1")                      # betty can't approve her own request
        self.assertEqual(started, [])
        self.react(ANNA, "✅", "$m1")
        self.assertEqual(started, [ANNA])                   # planned with the owner's agent

    def test_assist_needs_no_check(self):
        self.tell(ANNA, "@pm let us approve each change")
        self.assertEqual(self.levels, ["assist"])

    def test_messages_not_for_pm_are_ignored(self):
        self.tell(ANNA, "betty, should we stop for lunch?", mention=False)
        self.assertEqual((self.levels, self.said), ([], []))

    def test_feedback_on_a_waiting_plan_revises_it(self):
        """Seen live: anna told @pm what to change while the plan waited, and got "didn't catch that"."""
        started = []

        async def start_plan(room, sender):
            started.append(sender)
        self.lp.start_plan = start_plan
        self.lp.pending["!r"] = {"kind": "plan", "event_id": "$p", "until": time.time() + 60, "by": ANNA}
        self.tell(ANNA, "@pm the languages should all be on one page, sized by population")
        self.assertEqual(started, [ANNA])
        self.assertIn("Updating the plan", self.said[-1])
        self.assertNotIn("!r", self.lp.pending)

    def test_a_longer_note_without_a_plan_is_acknowledged(self):
        self.tell(BETTY, "@pm the greeting colours should match each country's flag")
        self.assertIn("Noted", self.said[-1])

    def test_unknown_gets_a_hint(self):
        self.tell(ANNA, "@pm what's the weather")
        self.assertIn("show the plan", self.said[-1])

    def test_level_shortcut_still_works(self):
        self.tell(ANNA, "!level autonomous", mention=False)
        self.assertEqual(self.levels, ["autonomous"])

    def test_agents_are_not_people(self):
        self.assertFalse(self.lp.is_person(self.room, "@anna-agent:bridge.localhost"))
        self.assertTrue(self.lp.is_person(self.room, ANNA))


class Moderator(unittest.TestCase):
    def check(self, sender, version, pl=0):
        state = RoomGetStateResponse([{"type": "m.room.create", "sender": ANNA, "state_key": "",
                                       "content": {"room_version": version}}], "!r")
        client = SimpleNamespace(room_get_state=mock.AsyncMock(return_value=state))
        r = SimpleNamespace(room_id="!r", power_levels=SimpleNamespace(get_user_level=lambda u: pl))
        return asyncio.run(room_state.is_moderator(client, r, sender))

    def test_v12_creator_is_moderator_without_pl_entry(self):
        self.assertTrue(self.check(ANNA, "12"))

    def test_non_creator_without_pl_is_not(self):
        self.assertFalse(self.check(BETTY, "12"))

    def test_old_room_creator_needs_pl(self):
        self.assertFalse(self.check(ANNA, "10"))

    def test_pl_50_is_moderator(self):
        self.assertTrue(self.check(BETTY, "12", pl=50))


if __name__ == "__main__":
    unittest.main()
