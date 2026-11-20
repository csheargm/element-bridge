"""hagency MCP tools for knowing the project (any time, not only during a task): the live status
@pm publishes, the room's recent conversation, and the project's files on the default branch.
Read-only; files are fetched as this agent's own GitHub identity. See room_context.py."""
import base64
import binascii
from pathlib import PurePosixPath

import github
import mcp_core as core
import room_context
from mcp_core import AGENT, mcp

MAX_FILE_CHARS = 60_000


@mcp.tool()
def project_status() -> dict:
    """The live status of each project room you're in (what's done, in progress, next, stopped) and
    the room's recent conversation. Check this before answering questions about the project:
    it is more current than your memory."""
    rooms = []
    for room in core.rooms():
        snap = room_context.snapshot(core.token(), room)
        if snap["status"]:                       # a project room (@pm publishes a status there)
            rooms.append({"room": room, **snap})
    return {"repo": github.REPO, "rooms": rooms or "no project room with a published status"}


@mcp.tool()
async def read_project_file(path: str = "") -> dict:
    """Read a file (or list a folder, path "" for the top) on the project's default branch."""
    rel = PurePosixPath(path.strip("/"))
    if rel.is_absolute() or ".." in rel.parts:
        return {"error": "use a path inside the repo, e.g. README.md or docs/"}
    try:
        data = await github.run_json(["api", f"repos/{github.REPO}/contents/{rel}".rstrip("/.")], AGENT)
    except github.GitHubError as e:
        return {"error": f"not found or not readable: {str(e)[:120]}"}
    if isinstance(data, list):
        return {"folder": str(rel) or "/", "entries": [f"{e.get('name')}{'/' if e.get('type') == 'dir' else ''}"
                                                        for e in data]}
    try:
        text = base64.b64decode(str(data.get("content", ""))).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return {"error": "couldn't decode that file"}
    return {"path": str(rel), "content": text[:MAX_FILE_CHARS], "truncated": len(text) > MAX_FILE_CHARS}
