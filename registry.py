"""Who brought which agent: agents.json (git-ignored), written by onboard.py and remote_agent.py,
read by the supervisor and setup. One line per person, keyed by their FULL Matrix ID, e.g.
{"@anna:this.server": {"agent": "hermes", "dm": "!room:server"}, "@alice:matrix.org": {"agent": "remote"}}.
Older files keyed by bare names ("anna") mean people on this server: load() reads them as full IDs."""
import json
import os
import threading
from pathlib import Path

import bridge_config
import identity
from bridge_config import SERVER_NAME

PATH = Path(os.environ.get("HAGENCY_AGENTS", Path(__file__).parent / "agents.json"))
DEFAULT = {"anna": {}, "betty": {}}
NAME_RE = identity.NAME_RE          # a person here who can have an agent (anna, newbie1)
REMOTE = "remote"                    # their agent runs on their own computer (connect.py), not here


def load() -> dict:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        data = DEFAULT                    # not onboarded yet (or a damaged file): the two demo people
    if not isinstance(data, dict) or not data:
        data = DEFAULT
    # Keep only well-formed entries: a damaged line must never crash @pm or confuse ownership. An old
    # bare key and its full ID together: the full ID's entry wins.
    people = {}
    for p, v in sorted(data.items(), key=lambda kv: str(kv[0]).startswith("@")):
        if isinstance(p, str) and p and (p.startswith("@") or ":" not in p):
            people[person_id(p)] = dict(v) if isinstance(v, dict) else {}
    return people


def person_id(who: str) -> str:
    """A full Matrix ID; a bare name is a person on this server."""
    return identity.person_id(who, SERVER_NAME)


def is_local(person: str) -> bool:
    return identity.is_local(person_id(person), SERVER_NAME)


def local_name(person: str) -> str | None:
    """The bare name of a person on this server (what the supervisor and Hermes run by), else None."""
    return identity.split(person_id(person))[0] if is_local(person) else None


def save(data: dict) -> None:
    tmp = PATH.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")   # @pm and onboard.py both write
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, PATH)


# --- ownership: this server created every agent account (onboarding, or @pm for "bring your own",
# also for people from other servers), so this mapping is authoritative here. (Agent accounts on
# other servers would need a two-sided claim: the owner names the agent AND the agent names the
# owner. Future work.)
FOLLOW = ("always", "ask", "never")


def agent_of(person: str) -> str:
    """The localpart of `person`'s agent on this server (identity.agent_localpart unless recorded)."""
    person = person_id(person)
    return load().get(person, {}).get("agent_user") or identity.agent_localpart(person, SERVER_NAME)


def owner_of(agent: str) -> str | None:
    """The full ID of the person whose agent `agent` (a localpart here) is, or None if it isn't a
    registered agent. Compare it with a full ID: never a bare name taken from another server."""
    people = load()
    found = next((p for p, v in people.items() if (v.get("agent_user") or identity.agent_localpart(p, SERVER_NAME))
                  == agent), None)
    mine = bridge_config.CREDENTIALS          # a person's own computer: no agents.json, just connect.py's file
    if found is None and mine and mine["user_id"] == f"@{agent}:{SERVER_NAME}":
        return person_id(mine["owner"])
    return found


def owns(person: str, agent: str) -> bool:
    """`person` (a full ID, or a bare name here) is the registered owner of agent localpart `agent`."""
    return owner_of(agent) == person_id(person)


def follow(person: str) -> str:
    """Bring the person's agent along when they join a project room: always (default), ask, never."""
    value = load().get(person_id(person), {}).get("follow", "always")
    return value if value in FOLLOW else "always"


def set_follow(person: str, value: str) -> None:
    if value not in FOLLOW:
        raise ValueError(f"follow must be one of {FOLLOW}")
    data = load()
    data.setdefault(person_id(person), {})["follow"] = value
    save(data)
