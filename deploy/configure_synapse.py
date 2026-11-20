"""Adjust Synapse's generated homeserver.yaml for the public server (run once by bootstrap.sh).

  - Postgres instead of SQLite (the generated file's database block is replaced);
  - sign-up only with a registration token (an invite), never open;
  - the public URL, and federation over 443 (Caddy serves .well-known);
  - Synapse's own default rate limits (the local prototype relaxes them; the internet must not).
Reads the Postgres password from ../.env itself, so it never passes through a command line.
Usage: python3 configure_synapse.py synapse/homeserver.yaml
"""
import re
import sys
from pathlib import Path

ENV = Path(__file__).with_name(".env")


def env() -> dict:
    pairs = (line.split("=", 1) for line in ENV.read_text().splitlines() if "=" in line and not line.startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def configure(text: str, e: dict) -> str:
    database = ("database:\n  name: psycopg2\n  args:\n    user: synapse\n"
                f"    password: \"{e['POSTGRES_PASSWORD']}\"\n    database: synapse\n    host: postgres\n"
                "    cp_min: 5\n    cp_max: 10\n")
    text, replaced = re.subn(r"(?m)^database:\n(?:[ \t]+.*\n)+", database, text)
    if replaced != 1:
        raise SystemExit("✗ couldn't find the generated database block; check homeserver.yaml by hand")
    extra = {
        "public_baseurl": f"https://{e['MATRIX_DOMAIN']}/",
        "enable_registration": "true",
        "registration_requires_token": "true",       # admins hand out tokens: invite-only
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


if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.write_text(configure(path.read_text(), env()))
    path.chmod(0o600)            # it holds the database password and Synapse's own secrets
    print(f"✓ {path} configured for Postgres and invite-only sign-up")
