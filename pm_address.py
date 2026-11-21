"""Is a message for @pm? In a room: when it starts with "@pm" / "Product Manager" or mentions @pm.
In a private chat of mine with exactly one person (one I made, or one they opened with me), every
message is for me (seen live: "resend me the code" typed there got no answer). A project room never
counts, however few people are in it: there, plain "stop" stays someone thinking aloud (GPT review).
Mixed into pm_chat.Chat (user_id, dm_rooms).
"""
import re

from nio import RoomMessageText

from bridge import ROOT
from saved_dict import SavedDict

ADDRESS_RE = re.compile(r"^\s*@?(pm|product manager)\b[:,]?\s*(.*)$", re.I | re.S)


def without_quote(body: str) -> str:
    """A reply's body starts with the quoted message ("> <@someone> text", then a blank line): read
    only what the person wrote, so "ok" in a reply to any message is just "ok"."""
    lines = body.strip().splitlines()
    while lines and lines[0].startswith(">"):
        lines.pop(0)
    return "\n".join(lines).strip()


class Addressing:
    def direct_chats(self) -> dict:
        """room → person: direct chats a person opened with me themselves (pm_follow.py), on disk."""
        if "_direct_chats" not in self.__dict__:
            self._direct_chats = SavedDict(ROOT / "data" / "direct-chats.json")
        return self._direct_chats

    def is_private_chat(self, room) -> bool:
        """One of my private chats (mine, or one a person opened with me): never a project room."""
        from pm_settings import is_my_chat    # here: pm_settings imports pm_chat (through pm_projects)
        return room.room_id in self.direct_chats() or is_my_chat(room, self.user_id, getattr(self, "dm_rooms", {}))

    def is_one_to_one(self, room, person: str) -> bool:
        """A private chat of mine with exactly that person in it (nobody else joined or invited)."""
        return set(getattr(room, "users", None) or {}) == {self.user_id, person} and self.is_private_chat(room)

    def addressed_text(self, event: RoomMessageText, room=None) -> str | None:
        """The message without its "@pm" / "Product Manager" address, or None if not for me."""
        body = without_quote(event.body)
        m = ADDRESS_RE.match(body)
        if room is not None and self.is_one_to_one(room, event.sender):
            return m.group(2) if m else body
        mentions = event.source.get("content", {}).get("m.mentions", {})
        ids = mentions.get("user_ids") if isinstance(mentions, dict) else None
        if m:
            return m.group(2)
        return body if isinstance(ids, list) and self.user_id in ids else None
