"""The Hagency side panel and console: a page @pm serves and adds to each project room as an
Element widget (and at /console for phones and other clients). It shows the live Project status,
what's waiting, the plan and whose agent is whose, and — once it knows who you are (console.py) —
lets you change the room's mode, your agent's follow-me and web grant, and approve what you asked
for, through @pm's own checks. Refreshed every few seconds; listens on 127.0.0.1 only.
"""
import html
import json
import logging
import os

import markdown
from aiohttp import web

import console
import github
import ownership
import registry

PORT = int(os.environ.get("PANEL_PORT", "8650"))
URL = f"http://localhost:{PORT}/panel?room=$matrix_room_id"
WIDGET_ID = "hagency"
REFRESH_MS = 3000
ELEMENT_ORIGIN = os.environ.get("ELEMENT_ORIGIN", "http://localhost:8649")
HEADERS = {"Content-Security-Policy": f"frame-ancestors 'self' {ELEMENT_ORIGIN}; default-src 'self'; "
                                      "script-src 'unsafe-inline' 'self'; style-src 'unsafe-inline'",
           "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def local_host(request) -> bool:
    """Only requests addressed to this machine (guards against DNS-rebinding style access)."""
    return request.host.split(":")[0] in ("localhost", "127.0.0.1")
log = logging.getLogger("loop")

PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hagency</title>
<style>body{font:14px system-ui,sans-serif;margin:12px;color:#222}h2{font-size:15px;margin:16px 0 6px}
.box{background:#f6f6f7;border-radius:8px;padding:8px 12px}.muted{color:#777}li{margin:2px 0}
button{font:inherit;padding:4px 10px;margin:2px 4px 2px 0;border:1px solid #ccc;border-radius:6px;background:#fff;cursor:pointer}
button.on{background:#0b6e4f;color:#fff;border-color:#0b6e4f}button:disabled{opacity:.45;cursor:default}
.row{margin:6px 0}#msg{color:#0b6e4f}#me{color:#444;margin-bottom:6px}#me b{font-size:16px;color:#111}</style>
<div id="me" class="muted">Signing in…</div><div id="msg"></div><div id="controls"></div><div id="p"></div>
<script>
const q = new URLSearchParams(location.search), room = q.get("room"), wid = q.get("widgetId");
const ELEMENT = __ELEMENT_ORIGIN__;
const embedded = window.parent !== window && !!wid;
let session = sessionStorage.getItem("hagency-session");
// A login code arrives after "#" (never sent to the server): take it, then wipe it at once.
const linkCode = new URLSearchParams(location.hash.slice(1)).get("login");
if (location.hash) history.replaceState(null, "", location.pathname + location.search);
const $ = id => document.getElementById(id);
const esc = t => String(t).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function post(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json",
    "Authorization": "Bearer " + (session || "")}, body: JSON.stringify(body)}); return r.json(); }
function signedIn(s) { session = s; sessionStorage.setItem("hagency-session", s); refresh(); }
// Inside Element only: the Matrix widget API, talking to Element's exact origin and our parent frame.
let openidRequest = null, gotCredentials = false;
if (embedded) window.addEventListener("message", ev => {
  const m = ev.data || {};
  if (ev.origin !== ELEMENT || ev.source !== window.parent || m.widgetId !== wid) return;
  if (m.api === "toWidget") {
    const ack = {api: "toWidget", widgetId: wid, requestId: m.requestId, action: m.action, data: {},
                 response: m.action === "capabilities" ? {capabilities: []} : {}};   // never echo credentials
    window.parent.postMessage(ack, ELEMENT);
    if (m.action === "capabilities" && !openidRequest) setTimeout(askOpenId, 300);
    if (m.action === "openid_credentials" && openidRequest && m.data && m.data.state === "allowed")
      gotToken(m.data.access_token);
  } else if (m.api === "fromWidget" && m.requestId === openidRequest && m.response) {
    if (m.response.state === "allowed") gotToken(m.response.access_token);
    else if (m.response.state === "blocked") $("me").textContent = "Element didn't share who you are.";
  }
});
function askOpenId() { openidRequest = "openid-" + crypto.randomUUID();
  window.parent.postMessage({api: "fromWidget", widgetId: wid, requestId: openidRequest, action: "get_openid", data: {}}, ELEMENT); }
async function gotToken(t) { if (gotCredentials) return; gotCredentials = true;
  const r = await post("/auth/openid", {access_token: t}); r.session ? signedIn(r.session) : ($("me").textContent = r.error); }
async function start() {
  if (linkCode) { const r = await post("/auth/link", {code: linkCode});
    return r.session ? signedIn(r.session) : ($("me").textContent = r.error); }
  if (session) return refresh();
  if (!embedded) $("me").innerHTML = 'Say <b>@pm send me my console link</b> in the room to sign in here.';
}
async function act(action, value) { $("msg").textContent = "…";
  const r = await post("/console/action", {room, action, value});
  $("msg").textContent = r.result === "ok" ? "Done." : (r.result || r.error); refresh(); }
function buttons(name, values, current, enabled) {
  return values.map(v => `<button data-a="${esc(name)}" data-v="${esc(v)}" ${v === current ? 'class=on' : ''}
    ${enabled ? '' : 'disabled'}>${esc(v)}</button>`).join(""); }
document.addEventListener("click", e => { const b = e.target.closest("button[data-a]"); if (b) act(b.dataset.a, b.dataset.v); });
async function refresh() {
  if (!session) return;
  const r = await fetch("console/state?room=" + encodeURIComponent(room), {headers: {"Authorization": "Bearer " + session}});
  if (r.status === 401) { session = null; sessionStorage.removeItem("hagency-session"); return start(); }
  const s = await r.json(); if (s.error) { $("me").textContent = s.error; return; }
  $("me").innerHTML = `<b>${esc(s.room)}</b> · ${esc(s.repo)}<br>Signed in as ${esc(s.user)} · ${s.moderator ? "owner/maintainer" : "contributor"}`;
  let h = `<h2>Mode</h2><div class=row>${buttons("mode", ["off", "assist", "autonomous"], s.mode, s.moderator)}</div>`;
  if (s.waiting && s.waiting.mine) h += `<h2>Waiting for you</h2><div class=row>${esc(s.waiting.kind)}${s.waiting.items.length ?
    ": " + s.waiting.items.map(esc).join(" · ") : ""} <button data-a="approve" data-v="${esc(s.waiting.id)}">Approve 👍</button></div>`;
  if (s.agent) h += `<h2>My agent (${esc(s.agent)})</h2>
    <div class=row>Comes along when I join a project room: ${buttons("follow", ["always", "ask", "never"], s.follow, true)}</div>
    <div class=row>Web research in this room: ${buttons("web", ["on", "off"], s.web ? "on" : "off", true)}
    <span class=muted>(each request is shown in the chat)</span></div>`;
  $("controls").innerHTML = h; $("p").innerHTML = s.html;      // s.html: server-escaped board (panel.render)
}
start(); setInterval(refresh, %d);
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
        return web.Response(text=PAGE.replace("__ELEMENT_ORIGIN__", json.dumps(ELEMENT_ORIGIN)),
                            content_type="text/html", headers=HEADERS)
    app = web.Application()
    app.add_routes([web.get("/panel", page), web.get("/console", page), *console.routes(loop, render)])
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
