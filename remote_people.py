"""People from other Matrix servers (e.g. @alice:matrix.org) can get an agent here too.

They never registered on this server, so @pm can't put them in Welcome: they DM @pm, or say
"@pm I want an agent" in a room @pm is in, and get the same offer (pm_welcome.py). Their agent
account is made here, named from their full Matrix ID (identity.py), and obeys only that full ID.

Limits, since anyone on any server can ask:
  - HAGENCY_ALLOW_REMOTE=0 turns it off (default on);
  - one agent per full Matrix ID (the name is derived from it, registry.py records it);
  - at most HAGENCY_REMOTE_PER_HOUR new ones per hour, for everyone together (default 10);
  - @pm joins at most DM_JOINS_PER_HOUR private chats people from other servers open with it.
"""
import os
import time

from setup_accounts import AccountError

HOUR_S = 3600
DM_JOINS_PER_HOUR = 30
FULL = "too many people from other servers asked for an agent in the last hour; please try again later"
OFF = "this server isn't making agents for people from other servers right now"


def allowed() -> bool:
    return os.environ.get("HAGENCY_ALLOW_REMOTE", "1").strip().lower() not in ("0", "off", "false", "no")


def per_hour() -> int:
    try:
        return max(0, int(os.environ.get("HAGENCY_REMOTE_PER_HOUR", "10")))
    except ValueError:                        # a typo in the setting: the default, never unlimited
        return 10


def now() -> float:
    return time.time()


def check_new(people: dict, at: float | None = None) -> None:
    """Raise AccountError unless one more person from another server may get an agent now.
    `people` is registry.load(): each remote agent records when it was "created"."""
    if not allowed():
        raise AccountError(OFF)
    at = now() if at is None else at
    recent = [v for v in people.values() if isinstance(v.get("created"), (int, float)) and v["created"] > at - HOUR_S]
    if len(recent) >= per_hour():
        raise AccountError(FULL)


class DmJoins:
    """How many private chats from other servers @pm joined in the last hour (in memory)."""
    def __init__(self, limit: int = DM_JOINS_PER_HOUR) -> None:
        self.limit, self.times = limit, []

    def take(self, at: float | None = None) -> bool:
        at = now() if at is None else at
        self.times = [t for t in self.times if t > at - HOUR_S]
        if len(self.times) >= self.limit:
            return False
        self.times.append(at)
        return True
