"""Adjust Synapse's generated homeserver.yaml for the public server (bootstrap.sh runs it every time;
running it again changes nothing):
  - Postgres instead of SQLite (the database block is replaced);
  - sign-up only with a registration token (an invite), no guests, no URL previews;
  - the public URL; Synapse's own default rate limits (the local prototype relaxes them).
It refuses a config whose server_name differs from .env (a server's name can never change). The
new file is written next to the old one and swapped in, so an interruption never leaves half a
config; synapse/.hagency-configured marks success, and bootstrap won't start Synapse without it.
Usage: python3 configure_synapse.py ../synapse/homeserver.yaml
"""
import json
import os
import re
import sys
from pathlib import Path

import envfile

MARKER = ".hagency-configured"


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
        "max_upload_size": "20M",
        "url_preview_enabled": "false",              # previews would fetch arbitrary URLs from the server
    }
    for key, value in extra.items():
        if re.search(rf"(?m)^{key}:", text):
            text = re.sub(rf"(?m)^{key}:.*$", f"{key}: {value}", text)
        else:
            text += f"{key}: {value}\n"
    return text


def install(path: Path, e: dict) -> None:
    new = configure(path.read_text(), e)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(new)
    st = path.stat()
    os.chown(tmp, st.st_uid, st.st_gid)      # Synapse's container reads it as its own user (991)
    os.replace(tmp, path)                    # atomic: the old file or the new one, never half of one
    (path.parent / MARKER).write_text("ok\n")


if __name__ == "__main__":
    values = envfile.read()
    if envfile.validate(values):
        raise SystemExit("✗ run `python3 envfile.py check` first")
    install(Path(sys.argv[1]), values)
    print("✓ homeserver.yaml configured: Postgres, invite-only sign-up")
