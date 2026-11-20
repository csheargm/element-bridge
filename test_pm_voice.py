"""Voice messages to @pm: transcribed locally, shown, then handled as typed text.
Run: .venv/bin/python -m unittest test_pm_voice"""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import loop
import pm_voice

PM, ANNA, BETTY = "@pm:x", "@anna:x", "@betty:x"


class Voice(unittest.TestCase):
    """the owner: "the voice msg should be transcribed to text and then sent to pm"."""
    def setUp(self):
        self.lp = loop.Loop.__new__(loop.Loop)
        self.lp.user_id, self.lp.pending, self.lp.busy = PM, {}, set()
        self.lp.client = SimpleNamespace(download=mock.AsyncMock(return_value=SimpleNamespace(body=b"OggS...")))
        self.said, self.heard, self.replies = [], [], []

        async def say(room_id, body, mention=None, task=None, **kw):
            self.said.append(body)
            self.replies.append(kw.get("reply_to"))
            return "$e"

        async def on_human(room, event):
            self.heard.append((event.body, event.source["content"]["m.mentions"]["user_ids"]))
        self.lp.say, self.lp.on_human = say, on_human
        self.room = SimpleNamespace(room_id="!r", users={PM: 0, ANNA: 0, BETTY: 0})

    def voice(self, words, duration=4000):
        ev = SimpleNamespace(sender=ANNA, url="mxc://x/abc", event_id="$v",
                             source={"content": {"msgtype": "m.audio", "info": {"duration": duration}}})
        with mock.patch.object(pm_voice, "transcribe", mock.AsyncMock(return_value=words)):
            asyncio.run(self.lp.hear(self.room, ev))

    def test_saying_pm_addresses_it_and_the_room_sees_the_words(self):
        self.voice("Product manager, let's build it.")
        self.assertIn("📝 Transcript: “Product manager, let's build it.”", self.said)
        self.assertEqual(self.heard, [("let's build it.", [PM])])
        self.assertEqual(self.replies, ["$v"])            # attached to the voice message, as its caption

    def test_ok_go_ahead_to_a_waiting_question_needs_no_name(self):
        self.lp.pending["!r"] = {"kind": "plan", "event_id": "$p", "until": time.time() + 60, "by": ANNA}
        self.voice("Okay, go ahead.")
        self.assertEqual(self.heard, [("Okay, go ahead.", [PM])])

    def test_people_talking_is_shown_but_not_a_command(self):
        self.voice("Let's meet tomorrow about the toggle.")
        self.assertEqual(self.heard, [("Let's meet tomorrow about the toggle.", [])])

    def test_too_long_is_refused_without_downloading(self):
        self.voice("x", duration=10 * 60 * 1000)
        self.lp.client.download.assert_not_called()
        self.assertIn("No transcript (it's longer than 5 minutes)", self.said[-1])
        self.assertEqual(self.heard, [])

    def test_no_speech_to_text_says_so(self):
        with mock.patch.object(pm_voice, "STT", None):
            with self.assertRaises(pm_voice.VoiceError):
                asyncio.run(pm_voice.transcribe(b"x"))


if __name__ == "__main__":
    unittest.main()
