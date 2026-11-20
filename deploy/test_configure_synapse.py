"""Unit tests for configure_synapse.py. Run from deploy/: python3 -m unittest test_configure_synapse"""
import unittest

import envfile
from configure_synapse import configure, set_keys, welcome_settings

GENERATED = """server_name: "matrix.example.org"
database:
  name: sqlite3
  args:
    database: /data/homeserver.db
log_config: "/data/log.config"
registration_shared_secret: "keep-me"
enable_registration: false
"""
ENV = {"POSTGRES_PASSWORD": "ab" * 32, "MATRIX_DOMAIN": "matrix.example.org", "SERVER_NAME": "matrix.example.org"}


class Configure(unittest.TestCase):
    def test_postgres_replaces_sqlite_and_nothing_else_is_lost(self):
        out = configure(GENERATED, ENV)
        self.assertIn("name: psycopg2", out)
        self.assertNotIn("sqlite3", out)
        self.assertIn('registration_shared_secret: "keep-me"', out)      # Synapse's own secret kept
        self.assertIn('log_config: "/data/log.config"', out)

    def test_invite_only_and_no_duplicate_keys(self):
        out = configure(GENERATED, ENV)
        self.assertIn("registration_requires_token: true", out)
        self.assertEqual(out.count("enable_registration:"), 1)            # replaced, not appended twice
        self.assertIn("url_preview_enabled: false", out)
        self.assertIn('public_baseurl: "https://matrix.example.org/"', out)

    def test_new_accounts_join_the_private_welcome_room_pm_owns(self):
        out = configure(GENERATED, ENV)
        self.assertIn('auto_join_rooms: ["#welcome:matrix.example.org"]', out)
        self.assertIn("autocreate_auto_join_rooms: true", out)
        self.assertIn("auto_join_mxid_localpart: pm", out)
        self.assertIn("autocreate_auto_join_room_preset: private_chat", out)
        self.assertIn("auto_join_rooms_for_guests: false", out)
        self.assertEqual(out.count("\nauto_join_rooms:"), 1)

    def test_media_stays_within_the_free_disk(self):
        out = configure(GENERATED, ENV)
        self.assertIn("max_upload_size: 20M", out)
        self.assertIn("media_retention: {local_media_lifetime: 90d, remote_media_lifetime: 30d}", out)
        self.assertEqual(out.count("media_retention:"), 1)

    def test_pm_can_sign_in_often_but_accounts_keep_synapses_limits(self):
        out = configure(GENERATED, ENV)
        self.assertIn("rc_login: {address: {per_second: 0.02, burst_count: 20}}", out)
        self.assertNotIn("account:", out.split("rc_login:")[1].splitlines()[0])

    def test_set_keys_replaces_in_place_and_appends_once(self):
        text = "a: 1\nb: 2"                                   # no final newline: the new key gets its own line
        out = set_keys(text, {"a": "9", "c": "3"})
        self.assertEqual(out, "a: 9\nb: 2\nc: 3\n")
        self.assertEqual(set_keys(out, {"a": "9", "c": "3"}), out)
        self.assertEqual(welcome_settings("x.org")["auto_join_rooms"], '["#welcome:x.org"]')

    def test_refuses_an_unexpected_file(self):
        with self.assertRaises(SystemExit):
            configure("server_name: x\n", ENV)

    def test_running_again_changes_nothing(self):
        once = configure(GENERATED, ENV)
        self.assertEqual(configure(once, ENV), once)

    def test_a_server_name_never_changes(self):
        """GPT review 12: a mismatched .env must fail, not reconfigure an existing server."""
        with self.assertRaises(SystemExit):
            configure(GENERATED, {**ENV, "SERVER_NAME": "hagencylabs.xyz"})


class EnvFile(unittest.TestCase):
    """GPT review 12: .env is data, validated, never sourced by a shell."""
    GOOD = {"ROOMS_DOMAIN": "rooms.example.org", "MATRIX_DOMAIN": "matrix.example.org", "ADMIN_DOMAIN": "admin.example.org", "PM_UID": "1001",
            "SERVER_NAME": "matrix.example.org", "POSTGRES_PASSWORD": "ab" * 32}

    def test_good_values_pass(self):
        self.assertEqual(envfile.validate(self.GOOD), [])

    def test_shell_and_latest_are_refused(self):
        for key, bad in (("ROOMS_DOMAIN", "$(id).example.org"), ("POSTGRES_PASSWORD", 'x" ; drop'),
                         ("OPERATOR", "anna;id"), ("PM_UID", "0"), ("PM_UID", "1001;id"), ("OPERATOR", "@anna:x.org"), ("MATRIX_DOMAIN", "")):
            self.assertTrue(envfile.validate({**self.GOOD, key: bad}), (key, bad))

    def test_the_operator_is_optional(self):
        self.assertEqual(envfile.validate({**self.GOOD, "OPERATOR": ""}), [])
        self.assertEqual(envfile.validate({**self.GOOD, "OPERATOR": "anna"}), [])


if __name__ == "__main__":
    unittest.main()
