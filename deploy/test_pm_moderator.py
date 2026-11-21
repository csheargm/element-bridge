"""Unit tests: the Synapse module that makes @pm a moderator in new rooms. Run from deploy/:
python3 -m unittest test_pm_moderator"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent / "synapse_modules"))
import hagency_pm_moderator as m  # noqa: E402

PM, ANNA, BOB = "@pm:s", "@anna:s", "@bob:s"


class WithPm(unittest.TestCase):
    def test_every_preset_a_person_makes_gets_pm_at_50(self):
        for preset in ("private_chat", "public_chat", None):
            config = {"preset": preset, "invite": [BOB]} if preset else {}
            self.assertTrue(m.with_pm(config, ANNA, PM))
            self.assertEqual(config["power_level_content_override"]["users"], {PM: 50})
        config = {"preset": "trusted_private_chat", "invite": [BOB]}          # bob is a creator: not in users
        self.assertTrue(m.with_pm(config, ANNA, PM))
        self.assertEqual(config["power_level_content_override"]["users"], {PM: 50})

    def test_a_co_creator_is_never_put_in_users(self):
        """GPT review 23: additional creators live in creation_content; Synapse refuses them in "users"."""
        config = {"creation_content": {"additional_creators": [BOB]}}
        self.assertTrue(m.with_pm(config, ANNA, PM))
        self.assertEqual(config["power_level_content_override"]["users"], {PM: 50})

    def test_the_creators_own_levels_are_kept(self):
        config = {"power_level_content_override": {"users": {BOB: 50}, "invite": 100}}
        m.with_pm(config, ANNA, PM)
        self.assertEqual(config["power_level_content_override"], {"users": {BOB: 50, PM: 50}, "invite": 100})
        config = {"power_level_content_override": {"users": {PM: 0}}}
        self.assertFalse(m.with_pm(config, ANNA, PM))                           # they chose: theirs

    def test_never_when_pm_is_a_creator_or_the_room_is_older(self):
        """Synapse refuses a creator in "users" (v12), and older versions would lose the creator's 100."""
        for config, creator in (({}, PM), ({"preset": "trusted_private_chat", "invite": [PM]}, ANNA),
                                ({"creation_content": {"additional_creators": [PM]}}, ANNA), ({"room_version": "10"}, ANNA),
                                ({"power_level_content_override": "x"}, ANNA), ({"power_level_content_override": None}, ANNA),
                                ({"initial_state": [{"type": "m.room.power_levels", "state_key": "", "content": {}}]}, ANNA),
                                ({"preset": "trusted_private_chat", "invite": 5}, PM),
                                ({"creation_content": {"additional_creators": [{"x": 1}]}, "room_version": "10"}, ANNA)):
            before = dict(config)
            self.assertFalse(m.with_pm(config, creator, PM))
            self.assertEqual(config, before)
        self.assertTrue(m.with_pm({"room_version": "10"}, ANNA, PM, default_version="10") is False)

    def test_the_module_registers_and_edits_the_request(self):
        seen = {}
        api = SimpleNamespace(register_third_party_rules_callbacks=lambda on_create_room: seen.update(cb=on_create_room))
        mod = m.PmModerator(m.PmModerator.parse_config({"pm": PM}), api)
        config = {"preset": "private_chat"}
        requester = SimpleNamespace(user=SimpleNamespace(to_string=lambda: ANNA))
        asyncio.run(seen["cb"](requester, config, False))
        self.assertEqual(config["power_level_content_override"]["users"], {PM: 50})
        self.assertIs(mod.pm, PM)
        with self.assertRaises(ValueError):
            m.PmModerator.parse_config({})


if __name__ == "__main__":
    unittest.main()
