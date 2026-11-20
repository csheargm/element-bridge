"""Who a person is: their full Matrix ID (@anna:this.server, @alice:matrix.org), wherever ownership
is decided. A bare name ("anna") means a person on this server: agents.json's old keys, onboard.py.

Every agent account lives on THIS server. Its localpart comes from its person's full ID:
  - a person here whose name is plain lowercase letters and digits: <name>-agent (anna-agent);
  - anyone else (another server, or an unusual name here): a readable prefix plus a hash of the full
    ID, e.g. @alice:matrix.org → alice-at-matrix-org-3f2a9c01d4-agent. The plain names have no
    "-", so the two kinds never meet; the hash keeps @alice:matrix.org and @alice:matrix-org apart.
Pure functions (no project imports): bridge_config.py uses them on people's own computers too.
"""
import hashlib
import re

NAME_RE = re.compile(r"^[a-z][a-z0-9]{1,23}$")   # a person here whose agent is simply <name>-agent
AGENT_SUFFIX = "-agent"
USER_RE = re.compile(r"^@[^:\s]{1,255}:[A-Za-z0-9.-]{1,255}(:\d{1,5})?$")   # a full Matrix user ID
PREFIX_LOCAL, PREFIX_SERVER, HASH_CHARS = 20, 30, 10


def person_id(who: str, home: str) -> str:
    """`who` as a full Matrix ID; a bare name is a person on `home` (this server)."""
    return who if who.startswith("@") else f"@{who}:{home}"


def split(user_id: str) -> tuple[str, str]:
    local, _, server = user_id.lstrip("@").partition(":")
    return local, server


def is_local(user_id: str, home: str) -> bool:
    return split(user_id)[1] == home


def _clean(text: str, limit: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "x"


def agent_localpart(person: str, home: str) -> str:
    """The localpart of `person`'s agent account on `home` (deterministic, see the module docstring)."""
    full = person_id(person, home)
    local, server = split(full)
    if server == home and NAME_RE.match(local):
        return f"{local}{AGENT_SUFFIX}"
    tag = hashlib.sha256(full.encode()).hexdigest()[:HASH_CHARS]
    where = f"-at-{_clean(server, PREFIX_SERVER)}" if server != home else ""
    return f"{_clean(local, PREFIX_LOCAL)}{where}-{tag}{AGENT_SUFFIX}"


def handle(person: str, home: str) -> str:
    """A safe file and folder name for `person` (their agent's localpart without -agent)."""
    return agent_localpart(person, home)[:-len(AGENT_SUFFIX)]

