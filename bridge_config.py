"""Where this machine's Hagency processes connect, and the agent's password.

On the server everything comes from this folder: Synapse at http://127.0.0.1:8648 and the
passwords in the git-ignored .env. On a person's own computer, connect.py saves the credentials it
was given (homeserver, user_id, password, owner) in a 0600 file under ~/.hagency/ and runs the bridge
with HAGENCY_CREDENTIALS pointing at it; then the homeserver and server name come from that file.
HAGENCY_HOMESERVER / HAGENCY_SERVER_NAME override the defaults without a file.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent
DEFAULT_HOMESERVER = "http://127.0.0.1:8648"
DEFAULT_SERVER_NAME = "bridge.localhost"
CREDENTIAL_KEYS = ("homeserver", "user_id", "password", "owner")


def load_credentials(path: str | None) -> dict:
    """The connect.py credentials file, checked; {} when there is none."""
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(f"can't read the Hagency credentials file {path}: {e}") from e
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k] for k in CREDENTIAL_KEYS):
        raise SystemExit(f"the Hagency credentials file {path} is incomplete; run connect.py again with a new code")
    if data["user_id"] != f"@{data['owner']}-agent:{data['user_id'].partition(':')[2]}":
        raise SystemExit(f"the Hagency credentials file {path} doesn't belong to {data['owner']}'s agent")
    return data


CREDENTIALS = load_credentials(os.environ.get("HAGENCY_CREDENTIALS"))
HOMESERVER = CREDENTIALS.get("homeserver") or os.environ.get("HAGENCY_HOMESERVER") or DEFAULT_HOMESERVER
SERVER_NAME = (CREDENTIALS.get("user_id", "").partition(":")[2] or os.environ.get("HAGENCY_SERVER_NAME")
               or DEFAULT_SERVER_NAME)


def read_env() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def agent_password(owner: str) -> str:
    """The password of <owner>-agent: from the credentials file on a person's computer, else .env."""
    if CREDENTIALS:
        if CREDENTIALS["owner"] != owner:
            raise SystemExit(f"the credentials file is for {CREDENTIALS['owner']}'s agent, not {owner}'s")
        return CREDENTIALS["password"]
    return read_env()[f"PW_{owner.upper()}_AGENT"]
