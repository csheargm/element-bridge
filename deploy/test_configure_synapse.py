"""Unit tests for configure_synapse.py. Run from deploy/: python3 -m unittest test_configure_synapse"""
import unittest

from configure_synapse import configure

GENERATED = """server_name: "matrix.example.org"
database:
  name: sqlite3
  args:
    database: /data/homeserver.db
log_config: "/data/log.config"
registration_shared_secret: "keep-me"
enable_registration: false
"""
ENV = {"POSTGRES_PASSWORD": "abc123", "MATRIX_DOMAIN": "matrix.example.org"}


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
        self.assertIn("public_baseurl: https://matrix.example.org/", out)

    def test_refuses_an_unexpected_file(self):
        with self.assertRaises(SystemExit):
            configure("server_name: x\n", ENV)


if __name__ == "__main__":
    unittest.main()
