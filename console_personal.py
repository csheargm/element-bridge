"""My Hagency: the Hagency panel at the top of a person's private chat with @pm (Product Manager), with
only their personal cards: My agents (add with the AI picker, rename, remove, disconnect, comes along),
and Connect GitHub. No room cards there, and only that person can use it: the chat is @pm's own with
them (pm_settings.private_room: only @pm invites; nobody else is in it), checked on every view and
action. The same widget URL as in project rooms, so the same pre-approval (deploy/element_home.py).
"""
ACTIONS = ("follow", "add", "github", "rename", "remove", "disconnect", "goback")   # console.act's personal ones
ROOM_ONLY = "This is your own Hagency panel; a project room's settings are in that room's panel."
NOT_YOURS = "This is someone else's chat with Product Manager."
TITLE = "My Hagency"


def is_personal_chat(loop, room) -> bool:
    """@pm's own private chat with one person (made by private_room, never one they opened), just the two."""
    import pm_settings                        # here: pm_settings imports console, which imports this module
    rid = getattr(room, "room_id", None)
    person = next((p for p, r in (getattr(loop, "dm_rooms", None) or {}).items() if r == rid), None) if rid else None
    return (person is not None and pm_settings.is_safe_chat(room)
            and set(room.users) <= {loop.user_id, person})


def owner_of(loop, room) -> str | None:
    """The person whose My Hagency this room is, or None (not a personal chat)."""
    if not is_personal_chat(loop, room):
        return None
    return next(p for p, r in loop.dm_rooms.items() if r == room.room_id)


def refusal(loop, room, user: str, action: str) -> str | None:
    """Why `user` may not do `action` here (a personal chat), or None (go ahead, or not a personal chat)."""
    owner = owner_of(loop, room)
    if owner is None:
        return None
    if owner != user:
        return NOT_YOURS
    return None if action in ACTIONS else ROOM_ONLY


def view(loop, room, user: str, mine: list, can_add: bool, extra: dict) -> dict:
    """The personal cards' state only (console.view gives `mine`, `can_add` and the shared fields)."""
    if owner_of(loop, room) != user:
        return {"error": NOT_YOURS}
    return {"personal": True, "room": TITLE, "user": user.split(":")[0].lstrip("@"), "agents": mine,
            "can_add": can_add, "pm_chat": room.room_id, "repo": None, "moderator": False, "waiting": None,
            "html": "", **extra}
