"""hagency MCP tools that reach beyond the project, each behind its owner's say-so:
  - web_search / fetch_page: only where the owner granted "web" in the room (capabilities.py), and
    never this machine or its network (no localhost, private, link-local or reserved addresses,
    re-checked on every redirect) — so a planted URL can't turn the agent against Synapse, the panel
    or anything else on the LAN;
  - ask_owner: before any action outside the project that isn't a read (posting elsewhere, writing a
    shared document, contacting someone), the agent asks in the room and goes ahead only on its
    owner's own ✅ (any other reaction, or silence, means no).
"""
import html
import ipaddress
import re
import socket
import time
import urllib.parse
import urllib.request

import capabilities
import intents
import mcp_core as core
from mcp_context import owners_room
from mcp_core import mcp

MAX_PAGE_BYTES = 1_000_000
MAX_TEXT = 20_000
FETCH_TIMEOUT_S = 15
APPROVAL_WAIT_S = 240               # under the MCP call timeout (300 s in the profile)
NO_REACTIONS = ("❌", "👎", "🚫", "✋")
USER_AGENT = "Mozilla/5.0 (compatible; hagency-agent)"


def public_url(url: str) -> bool:
    """http(s) to a host whose every address is public (not this machine or its network)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError:
        return False
    return all(ipaddress.ip_address(info[4][0].split("%")[0]).is_global for info in infos)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_url(newurl):
            raise urllib.error.URLError(f"redirect to a non-public address refused: {newurl[:80]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str) -> str:
    if not public_url(url):
        raise ValueError("only public http(s) addresses (never this machine or its network)")
    opener = urllib.request.build_opener(_CheckedRedirects)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=FETCH_TIMEOUT_S) as r:
        return r.read(MAX_PAGE_BYTES).decode(r.headers.get_content_charset() or "utf-8", errors="replace")


def page_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", raw))).strip()


def web_allowed() -> str | None:
    """Why the web isn't allowed right now, or None if the owner granted it in their room."""
    room = owners_room()
    if room is None:
        return "no recent message from your owner to you in a room"
    if "web" not in capabilities.granted(core.token(), room, core.USER_ID):
        return 'your owner hasn\'t allowed web access in this room (they can say "@pm let my agent search the web here")'
    return None


@mcp.tool()
def web_search(query: str) -> dict:
    """Search the web (only where your owner allowed it). Returns titles, links and snippets."""
    refused = web_allowed()
    if refused:
        return {"error": refused}
    try:
        raw = fetch("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query[:300]))
    except (OSError, ValueError) as e:
        return {"error": f"search failed: {str(e)[:120]}"}
    results = []
    for href, title in re.findall(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', raw)[:8]:
        target = urllib.parse.parse_qs(urllib.parse.urlsplit(html.unescape(href)).query).get("uddg", [href])[0]
        results.append({"title": page_text(title), "url": target})
    return {"query": query, "results": results}


@mcp.tool()
def fetch_page(url: str) -> dict:
    """Read a public web page as text (only where your owner allowed web access)."""
    refused = web_allowed()
    if refused:
        return {"error": refused}
    try:
        text = page_text(fetch(url))
    except (OSError, ValueError) as e:
        return {"error": f"couldn't read that page: {str(e)[:160]}"}
    return {"url": url, "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}


@mcp.tool()
def ask_owner(action: str) -> dict:
    """Before anything outside the project that isn't a read, ask your owner. Returns approved
    true only if THEY react with an OK emoji within a few minutes; otherwise don't do it."""
    room = owners_room()
    owner = core.owner_id()
    if room is None or owner is None:
        return {"approved": False, "why": "no room with your owner to ask in"}
    who = owner.split(":")[0].lstrip("@")
    content = {"msgtype": "m.text", "m.mentions": {"user_ids": [owner]},
               "body": f"{who}, may I: {' '.join(action.split())[:400]}? React ✅ to allow (anything else, "
                       f"or no answer in {APPROVAL_WAIT_S // 60} min, means no)."}
    sent = core.api("PUT", core.room_path(room, f"/send/m.room.message/a{time.time_ns()}"), content, token=core.token())
    event_id = sent.get("event_id")
    if not event_id:
        return {"approved": False, "why": "couldn't ask in the room"}
    deadline = time.time() + APPROVAL_WAIT_S
    while time.time() < deadline:
        time.sleep(3)
        path = f"/_matrix/client/v1/rooms/{urllib.parse.quote(room)}/relations/{urllib.parse.quote(event_id)}/m.annotation"
        for ev in core.api("GET", path, token=core.token()).get("chunk", []):
            key = ev.get("content", {}).get("m.relates_to", {}).get("key", "")
            if ev.get("sender") == owner and intents.is_confirm_reaction(key):
                return {"approved": True}
            if ev.get("sender") == owner and key in NO_REACTIONS:
                return {"approved": False, "why": f"{who} said no"}
    return {"approved": False, "why": f"{who} didn't answer in time; don't do it"}
