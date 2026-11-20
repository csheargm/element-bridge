"""Adjust Synapse's generated homeserver.yaml for the public server (bootstrap.sh runs it every time;
running it again changes nothing):
  - Postgres instead of SQLite (the database block is replaced);
  - sign-up only with a registration token (an invite), no guests, no URL previews;
  - every new account joins the private Welcome room, created by @pm, where @pm greets them
    (pm_welcome.py; @pm must exist before the first sign-up, or Synapse can't create the room);
  - the public URL; Synapse's own default rate limits (the local prototype relaxes them);
  - Oracle's free limits: uploads up to 20 MB, local media kept 90 days and remote media 30 days.
It refuses a config whose server_name differs from .env (a server's name can never change). The
new file is written next to the old one and swapped in, so an interruption never leaves half a
config; synapse/.hagency-configured marks success, and bootstrap won't start Synapse without it.
Usage: python3 configure_synapse.py ../synapse/homeserver.yaml
"""
import json
import re
import sys
from pathlib import Path

import envfile
import hostfiles

MARKER = ".hagency-configured"
WELCOME_LOCALPART = "welcome"                 # #welcome:<server>; pm_welcome.py looks for this room
PM_LOCALPART = "pm"


def welcome_settings(server_name: str) -> dict:
    """New accounts are auto-joined to a private Welcome room that @pm creates on first use."""
    return {
        "auto_join_rooms": json.dumps([f"#{WELCOME_LOCALPART}:{server_name}"]),
        "autocreate_auto_join_rooms": "true",
        "auto_join_mxid_localpart": PM_LOCALPART,    # @pm creates it and owns it
        "autocreate_auto_join_room_preset": "private_chat",   # invite-only: Synapse invites each new account
        "auto_join_rooms_for_guests": "false",
    }


def set_keys(text: str, values: dict) -> str:
    """Set top-level `key: value` lines (replace, or append once): running again changes nothing."""
    for key, value in values.items():
        if re.search(rf"(?m)^{key}:", text):
            text = re.sub(rf"(?m)^{key}:.*$", lambda _m: f"{key}: {value}", text)
        else:
            text += ("" if text.endswith("\n") else "\n") + f"{key}: {value}\n"
    return text


def configure(text: str, e: dict) -> str:
    found = re.search(r'(?m)^server_name:\s*"?([^"\n]+)"?\s*$', text)
    if not found or found.group(1).strip() != e["SERVER_NAME"]:
        raise SystemExit(f"✗ homeserver.yaml is for {found and found.group(1)!r}, .env says {e['SERVER_NAME']!r}; "
                         "a server's name can't change (start over with an empty synapse/ to use a new one)")
    database = ("database:\n  name: psycopg2\n  args:\n    user: synapse\n"
                f"    password: {json.dumps(e['POSTGRES_PASSWORD'])}\n    database: synapse\n    host: postgres\n"
                "    cp_min: 5\n    cp_max: 10\n")
    text, replaced = re.subn(r"(?m)^database:\n(?:[ \t]+.*\n)+", database, text)
    if replaced != 1:
        raise SystemExit("✗ couldn't find the database block; check homeserver.yaml by hand")
    extra = {
        "public_baseurl": json.dumps(f"https://{e['MATRIX_DOMAIN']}/"),
        "enable_registration": "true",
        "registration_requires_token": "true",       # an admin hands out tokens: invite-only
        "allow_guest_access": "false",
        "max_upload_size": "20M",                    # with media_retention: the disk and traffic stay free-tier
        "media_retention": "{local_media_lifetime: 90d, remote_media_lifetime: 30d}",
        "url_preview_enabled": "false",              # previews would fetch arbitrary URLs from the server
        **welcome_settings(e["SERVER_NAME"]),
    }
    return set_keys(text, extra)


def install(path: Path, e: dict) -> bool:
    """Configure in place (atomically, never through a link Synapse's container could plant);
    True if the file changed, so bootstrap restarts Synapse to load it."""
    old = path.read_text()
    new = configure(old, e)
    if new != old:
        hostfiles.replace(path, new, 0o600, (path.stat().st_uid, path.stat().st_gid))   # Synapse's container reads it as its own user (991)
    hostfiles.replace(path.parent / MARKER, "ok\n", 0o644)
    return new != old


if __name__ == "__main__":
    values = envfile.read()
    if envfile.validate(values):
        raise SystemExit("✗ run `python3 envfile.py check` first")
    changed = install(Path(sys.argv[1]), values)
    print(f"✓ homeserver.yaml {'configured' if changed else 'unchanged'}: Postgres, invite-only sign-up, Welcome room")
