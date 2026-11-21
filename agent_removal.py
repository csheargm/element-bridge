"""Removing an agent for good, and forgetting agents whose accounts are gone (server side, blocking I/O).

remove(): a person's own remote agent (connect.py) has its Matrix account deactivated through the
admin API @pm already uses for Disconnect (local_signup.admin_call): signed out everywhere, out of
every room, never usable again. Then its password leaves .env and its agents.json entry goes, so its
slot is free. Deactivated account names can't be registered again, so a removed further agent's
label is kept ("removed") and a new agent needs a new name.

deactivated(): Synapse's own list of deactivated accounts; registry.prune() forgets only people whose
every account is on it. If the list can't be read in full, nothing is pruned.
"""
import logging
import urllib.parse

import local_signup
import registry
import setup_accounts
from bridge_config import SERVER_NAME
from remote_agent import WRITING, _signs_in
from setup_accounts import AccountError, load_env

log = logging.getLogger("loop")
PAGE = 500
MAX_PAGES = 40                       # 20 000 accounts: far beyond this server; a runaway list is "unreadable"


def remove(person: str, agent: str) -> bool:
    """Remove this remote agent of `person` (localpart or label). Their first agent goes only when it
    is their last one. True if its account was deactivated (False: it never existed, so only its
    entry went). Raises AccountError with a sentence for them."""
    with WRITING:
        person = registry.person_id(person)
        found = registry.find(person, agent)
        if found is None or found["agent"] != registry.REMOTE or not registry.owns(person, found["localpart"]):
            raise AccountError(f"{agent} isn't one of your agents on your own computer")
        others = [a["label"] for a in registry.agents_of(person) if not a["first"]]
        if found["first"] and others:
            raise AccountError(f"remove your other agents first ({', '.join(others)}); this one goes last")
        localpart, env = found["localpart"], load_env()
        key = setup_accounts.password_key(localpart)
        ours = found["ready"] or (key in env and _signs_in(localpart, env[key]))   # never close an account we don't hold
        if ours:
            _deactivate(f"@{localpart}:{SERVER_NAME}")
        registry.forget_agent(person, localpart)
        if key in env:
            setup_accounts.save_env({k: v for k, v in load_env().items() if k != key})   # a fresh read: others' lines stay
        return ours


def _deactivate(user_id: str) -> None:
    try:
        r = local_signup.admin_call("POST", f"/_synapse/admin/v1/deactivate/{urllib.parse.quote(user_id)}",
                                    {"erase": False})
    except (SystemExit, OSError, ValueError) as e:   # admin-bot can't sign in, Synapse away: as a refusal
        r = {"_status": 0, "errcode": str(e)[:80]}
    if "_status" in r:
        raise AccountError(f"couldn't close {user_id}'s account: {r.get('errcode')}")


def deactivated() -> set[str] | None:
    """Every deactivated account's full ID on this server, or None if Synapse's list can't be read
    in full (an error, a refusal or an odd answer: then nobody may be pruned)."""
    dead, start = set(), "0"
    for _ in range(MAX_PAGES):
        path = f"/_synapse/admin/v2/users?from={urllib.parse.quote(start)}&limit={PAGE}&guests=false&deactivated=true"
        try:
            r = local_signup.admin_call("GET", path, None)
        except (SystemExit, OSError, ValueError) as e:   # admin-bot can't sign in, Synapse away: nothing known
            log.warning("couldn't list deactivated accounts: %s", str(e)[:200])
            return None
        users = r.get("users")
        if "_status" in r or not isinstance(users, list):
            log.warning("couldn't list deactivated accounts: %s", r.get("errcode") or "an odd answer")
            return None
        dead |= {u["name"] for u in users if isinstance(u, dict) and u.get("deactivated") is True
                 and isinstance(u.get("name"), str)}
        start = r.get("next_token")
        if start is None:
            return dead
        start = str(start)
    return None
