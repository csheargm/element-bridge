"""Unit tests for who the bridge answers. Run: .venv/bin/python -m unittest test_rules"""
import unittest
from types import SimpleNamespace

from bridge import Bridge

ANNA, BETTY, MALLORY = "@anna:bridge.localhost", "@betty:bridge.localhost", "@mallory:bridge.localhost"
AGENT, OTHER_AGENT = "@anna-agent:bridge.localhost", "@betty-agent:bridge.localhost"


def room(members, count=None):
    return SimpleNamespace(users={m: None for m in members}, member_count=count or len(members))


def msg(sender, body, mentions=None):
    content = {"body": body, **({"m.mentions": {"user_ids": mentions}} if mentions is not None else {})}
    return SimpleNamespace(sender=sender, body=body, source={"content": content})


class Rules(unittest.TestCase):
    def setUp(self):
        self.b = Bridge("anna", "codex")
        self.lounge = room([ANNA, BETTY, AGENT, OTHER_AGENT])

    def test_mention_pill_addresses_agent(self):
        self.assertTrue(self.b.is_addressed(self.lounge, msg(BETTY, "hey", [AGENT])))

    def test_plain_name_addresses_agent(self):
        self.assertTrue(self.b.is_addressed(self.lounge, msg(ANNA, "anna-agent: hi")))

    def test_other_agent_name_does_not_address(self):
        self.assertFalse(self.b.is_addressed(self.lounge, msg(ANNA, "betty-agent: hi", [OTHER_AGENT])))

    def test_dm_always_addresses(self):
        self.assertTrue(self.b.is_addressed(room([ANNA, AGENT]), msg(ANNA, "hello")))

    def test_non_member_refused(self):
        self.assertIsNotNone(self.b.should_ignore(self.lounge, msg(MALLORY, "anna-agent: rm -rf")))

    def test_other_agent_refused_loop_guard(self):
        self.assertIsNotNone(self.b.should_ignore(self.lounge, msg(OTHER_AGENT, "anna-agent: hi")))

    def test_own_message_refused(self):
        self.assertIsNotNone(self.b.should_ignore(self.lounge, msg(AGENT, "anna-agent")))

    def test_member_accepted(self):
        self.assertIsNone(self.b.should_ignore(self.lounge, msg(BETTY, "anna-agent: hi")))


if __name__ == "__main__":
    unittest.main()
