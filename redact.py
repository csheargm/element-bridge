"""Text that goes into a room must not carry local details: error messages from git, gh or the agent
CLIs often include absolute paths (user names, folder layout). Everything posted from an exception
or a tool's output goes through room_safe() first; the full text stays in the local log."""
import re
from pathlib import Path

_PATH_RE = re.compile(r"(?:/(?:Users|home|private|var|tmp|opt|Volumes)/|~/)[^\s'\"`)\]]*")
MAX_ROOM_ERROR = 300


def room_safe(text: object, limit: int = MAX_ROOM_ERROR) -> str:
    s = str(text).replace(str(Path.home()), "~")
    return _PATH_RE.sub("<local path>", s)[:limit]
