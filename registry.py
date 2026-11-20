"""Who brought which agent: agents.json (git-ignored), written by onboard.py, read by the supervisor
and setup. One line per person, e.g. {"anna": {"agent": "hermes", "dm": "!room:server"}}."""
import json
import os
import re
from pathlib import Path

PATH = Path(os.environ.get("HAGENCY_AGENTS", Path(__file__).parent / "agents.json"))
DEFAULT = {"anna": {}, "betty": {}}
NAME_RE = re.compile(r"^[a-z][a-z0-9]{1,23}$")   # a person who can have an agent here (anna, newbie1)
REMOTE = "remote"                    # their agent runs on their own computer (connect.py), not here   # before anyone has onboarded: start.sh's --agent applies


def load() -> dict:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return dict(DEFAULT)              # not onboarded yet (or a damaged file): the two demo people
    if not isinstance(data, dict) or not data:
        return dict(DEFAULT)
    # Keep only well-formed entries: a damaged line must never crash @pm or confuse ownership.
    return {p: (v if isinstance(v, dict) else {}) for p, v in data.items() if isinstance(p, str) and p}


def save(data: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, PATH)


# --- ownership: the admin created both accounts at onboarding, so this mapping is authoritative on
# this server. (Agents registered elsewhere, e.g. over federation, would need a two-sided claim:
# the owner names the agent AND the agent names the owner. Future work.)
FOLLOW = ("always", "ask", "never")


def agent_of(person: str) -> str:
    """The localpart of `person`'s agent (default <person>-agent)."""
    return load().get(person, {}).get("agent_user") or f"{person}-agent"


def owner_of(agent: str) -> str | None:
    """The person whose agent `agent` (a localpart) is, or None if it isn't a registered agent."""
    return next((p for p in load() if agent_of(p) == agent), None)


def follow(person: str) -> str:
    """Bring the person's agent along when they join a project room: always (default), ask, never."""
    value = load().get(person, {}).get("follow", "always")
    return value if value in FOLLOW else "always"


def set_follow(person: str, value: str) -> None:
    if value not in FOLLOW:
        raise ValueError(f"follow must be one of {FOLLOW}")
    data = load()
    data.setdefault(person, {})["follow"] = value
    save(data)
