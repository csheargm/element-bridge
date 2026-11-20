"""The Hagency side panel: a small page @pm serves and adds to each project room as an Element
widget, shown next to the chat. It shows what the room is doing — the live Project status, what's
waiting for approval, the plan, the mode and whose agent is whose — refreshed every few seconds.

Read-only by design: people still change things by talking to @pm (permissions live there). It
listens on 127.0.0.1 only; a public deployment needs Element's OpenID hand-off to know the viewer
(docs/ROADMAP.md).
"""
import html
import logging
import os

import markdown
from aiohttp import web

import github
import ownership
import registry

PORT = int(os.environ.get("PANEL_PORT", "8650"))
URL = f"http://localhost:{PORT}/panel?room=$matrix_room_id"
WIDGET_ID = "hagency"
REFRESH_MS = 3000
ELEMENT_ORIGIN = os.environ.get("ELEMENT_ORIGIN", "http://localhost:8649")
HEADERS = {"Content-Security-Policy": f"frame-ancestors {ELEMENT_ORIGIN}; default-src 'self'; "
                                      "script-src 'unsafe-inline' 'self'; style-src 'unsafe-inline'",
           "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def local_host(request) -> bool:
    """Only requests addressed to this machine (guards against DNS-rebinding style access)."""
    return request.host.split(":")[0] in ("localhost", "127.0.0.1")
log = logging.getLogger("loop")

PAGE = """<!doctype html><meta charset="utf-8"><title>Hagency</title>
<style>body{font:14px system-ui,sans-serif;margin:12px;color:#222}h2{font-size:15px;margin:16px 0 6px}
.box{background:#f6f6f7;border-radius:8px;padding:8px 12px}.muted{color:#777}li{margin:2px 0}</style>
<div id="p">Loading…</div>
<script>
const room = new URLSearchParams(location.search).get("room");
async function load(){ try { const r = await fetch("data?room=" + encodeURIComponent(room));
  document.getElementById("p").innerHTML = (await r.json()).html; } catch(e) {} }
load(); setInterval(load, %d);
</script>""" % REFRESH_MS


def render(loop, room_id: str) -> str:
    """The panel's HTML for one room (all text escaped except @pm's own board markdown)."""
    room = loop.client.rooms.get(room_id)
    if room is None:
        return "<p class=muted>@pm isn't in this room.</p>"
    board = loop.boards.get(room_id, {}).get("text") or "No status yet."
    waiting = loop.pending.get(room_id)
    parts = [f"<h2>Project status</h2><div class=box>{markdown.markdown(github.linkify(board))}</div>"]
    if waiting:
        who = html.escape(str(waiting.get("by", "")).split(":")[0].lstrip("@"))
        what = {"plan": "a plan", "plan_request": "turning the discussion into work", "bring": "bringing an agent",
                "mode": f"switching to {html.escape(str(waiting.get('level')))}"}.get(waiting.get("kind"), "a change")
        parts.append(f"<h2>Waiting for approval</h2><p>{what} — {who or 'the owner'} reacts 👍 in the chat</p>")
    plan = loop.last_plans.get(room_id, {}).get("items", [])
    if plan:
        items = "".join(f"<li><b>{html.escape(i['title'])}</b></li>" for i in plan)
        parts.append(f"<h2>Current plan</h2><ol>{items}</ol>")
    agents = []
    grants = loop.grants_cache.get(room_id, {}) if hasattr(loop, "grants_cache") else {}
    for user in sorted(room.users):
        owner = ownership.owner_id(user)
        if owner:
            here = "" if ownership.joined(room, owner) else " <span class=muted>(owner not here: idle)</span>"
            follow = registry.follow(owner.split(":")[0].lstrip("@"))
            caps = ", ".join(sorted(grants.get(user, {}))) or "project only"
            agents.append(f"<li>{html.escape(user.split(':')[0][1:])} — "
                          f"{html.escape(owner.split(':')[0][1:])}'s agent, follows: {follow}, "
                          f"may use: {html.escape(caps)}{here}</li>")
    parts.append("<h2>Agents</h2><ul>" + ("".join(agents) or "<li class=muted>none</li>") + "</ul>")
    parts.append(f"<p class=muted>Repo: {html.escape(github.REPO)} · change anything by telling @pm in the chat</p>")
    return "".join(parts)


async def start(loop) -> web.AppRunner | None:
    """Serve the panel on 127.0.0.1:PORT; never fatal (the chat works without it)."""
    async def page(request):
        if not local_host(request):
            return web.Response(status=403)
        return web.Response(text=PAGE, content_type="text/html", headers=HEADERS)

    async def data(request):
        if not local_host(request):
            return web.Response(status=403)
        return web.json_response({"html": render(loop, request.query.get("room", ""))}, headers=HEADERS)
    app = web.Application()
    app.add_routes([web.get("/panel", page), web.get("/data", data)])
    runner = web.AppRunner(app, access_log=None)        # the widget polls every 3 s: don't flood the log
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", PORT).start()
    except OSError as e:
        log.warning("side panel not started (port %s): %s", PORT, e)
        return None
    return runner


async def add_to_room(client, room_id: str) -> bool:
    """Add the panel to the room as an Element widget, pinned at the top above the chat."""
    resp = await client.room_put_state(room_id, "im.vector.modular.widgets",
                                       {"type": "m.custom", "url": URL, "name": "Hagency",
                                        "id": WIDGET_ID, "data": {}}, state_key=WIDGET_ID)
    if not hasattr(resp, "event_id"):
        return False
    await client.room_put_state(room_id, "io.element.widgets.layout",       # pinned above the chat
                                {"widgets": {WIDGET_ID: {"container": "top", "index": 0, "width": 100, "height": 40}}})
    return True
