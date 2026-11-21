"""Is a person on this server one of its admins (e.g. @owner)? Admins run the server; @pm doesn't
welcome them or offer them an agent in Welcome (they can still say "@pm I want an agent").

Asked of Synapse's admin API (as admin-bot, local_signup.admin_call) once per person and
remembered in data/admins.json: each question costs admin-bot a sign-in.
"""
import logging
import urllib.parse

import local_signup
from bridge_config import ROOT
from saved_dict import SavedDict

log = logging.getLogger("loop")
_known: dict = {}


def known() -> dict:
    if "file" not in _known:
        _known["file"] = SavedDict(ROOT / "data" / "admins.json")
    return _known["file"]


def is_admin(user_id: str) -> bool:
    """Blocking. False when Synapse can't say (a greeting too many beats a newcomer left unwelcomed)."""
    if user_id in known():
        return known()[user_id]
    try:
        info = local_signup.admin_call("GET", f"/_synapse/admin/v2/users/{urllib.parse.quote(user_id)}", None)
    except (SystemExit, OSError, ValueError, AttributeError) as e:   # admin-bot can't sign in, Synapse away: ask again later
        log.warning("couldn't ask whether %s is an admin: %s", user_id, e)
        return False
    if "_status" in info:                     # an error answer: not remembered either
        return False
    known()[user_id] = info.get("admin") is True
    return known()[user_id]
