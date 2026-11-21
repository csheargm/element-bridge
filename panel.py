"""The Hagency side panel and console: a page @pm serves and adds to each project room as an
Element widget (and at /console for phones and other clients). It shows the live Project status,
what's waiting, the plan and whose agent is whose, and — once it knows who you are (console.py) —
lets you change the room's mode, your agent's follow-me and web grant, and approve what you asked
for, through @pm's own checks. Refreshed every few seconds; listens on 127.0.0.1 only (PANEL_HOST: the public server's container).
"""
import html
import json
import logging
import os
from pathlib import Path

import markdown
from aiohttp import web

import console
from bridge_config import ELEMENT_ORIGIN
import remote_agent
import github
import ownership
import registry

PORT = console.PORT
HOST = os.environ.get("PANEL_HOST", "127.0.0.1")     # the public server's container: 0.0.0.0, behind Caddy
WIDGET_ID = "hagency"
AGENTS_JS = Path(__file__).resolve().parent / "static" / "panel_agents.js"
REFRESH_MS = 3000
HEADERS = {"Content-Security-Policy": f"frame-ancestors 'self' {ELEMENT_ORIGIN}; default-src 'self'; "
                                      "script-src 'unsafe-inline' 'self'; style-src 'unsafe-inline'",
           "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def widget_url() -> str:
    """The panel as Element loads it: this console's public URL ($matrix_room_id: Element fills it in)."""
    return f"{console.public_url()}/panel?room=$matrix_room_id"


def local_host(request) -> bool:
    """Only requests addressed to this machine or its public name (guards against DNS rebinding)."""
    return request.host.split(":")[0] in console.allowed_hosts()
log = logging.getLogger("loop")

PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hagency</title>
<style>
:root{--bg:#f7f7f8;--card:#fff;--ink:#17191c;--muted:#6b7078;--line:#e4e6ea;--accent:#0b6e4f;--accent-soft:#e3f2ec;
--warn:#8a5a00;--warn-soft:#fff4dc;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1d2024;--ink:#e8eaed;--muted:#9aa0a8;--line:#2c3036;
--accent:#3fb68b;--accent-soft:#17352b;--warn:#f0b54a;--warn-soft:#3a2f17}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);padding:16px}
.wrap{max-width:760px;margin:0 auto;display:grid;gap:12px}.stack{display:grid;gap:12px;min-width:0}
header{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;justify-content:space-between}
h1{font-size:18px;margin:0;font-weight:650}.sub{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px}
.chip{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;background:var(--card);
border:1px solid var(--line);font-size:12px;color:var(--muted)}.chip.code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.chip.role{background:var(--accent-soft);border-color:transparent;color:var(--accent);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card h2{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:0 0 10px;font-weight:650}
.card.attn{border-color:var(--warn);background:var(--warn-soft)}.card.attn h2{color:var(--warn)}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.set{display:flex;justify-content:space-between;align-items:center;gap:8px 12px;flex-wrap:wrap;padding:8px 0}
.set+.set{border-top:1px solid var(--line)}.hint{color:var(--muted);font-size:12.5px;margin-top:6px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--bg)}
.seg button{border:0;border-radius:0;margin:0;background:transparent;padding:5px 12px;color:var(--ink)}
.seg button+button{border-left:1px solid var(--line)}.seg button.on{background:var(--accent);color:#fff}
button{font:inherit;padding:5px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);cursor:pointer}
button:hover:not(:disabled){border-color:var(--accent)}button:disabled{opacity:.5;cursor:default}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.field{display:flex;gap:8px;flex-wrap:wrap}input{font:inherit;padding:5px 10px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--ink);flex:1;min-width:12em}
#msg:empty{display:none}#msg{padding:8px 12px;border-radius:8px;background:var(--accent-soft);color:var(--accent);white-space:pre-line}
.board{font-size:13.5px}.board p{margin:4px 0}.board ul{margin:4px 0;padding-left:18px}.board a{color:var(--accent)}
.agents{list-style:none;margin:0;padding:0}.agents li{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;padding:8px 0}
.agents li+li{border-top:1px solid var(--line)}.agents b{font-weight:600}.muted{color:var(--muted)}
footer{color:var(--muted);font-size:12px;text-align:center;padding:4px 0 8px}
</style>
<div class="wrap"><header><div><h1 id="title">Hagency</h1><div class="sub" id="sub"></div></div><div id="who"></div></header>
<div id="me" class="muted">Signing in…</div><div id="msg"></div><div id="controls" class="stack"></div><div id="p" class="stack"></div>
<footer>Change anything here or by telling @pm in the chat.</footer></div>
<script src="panel-agents.js"></script>
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
async function act(action, value, agent) { $("msg").textContent = "…";
  const r = await post("/console/action", {room, action, value, agent: agent || ""});
  $("msg").textContent = r.result === "ok" ? "Done." : (r.result || r.error); refresh(); }
const MODES = {off: "Paused: no new work is handed out and nothing is merged.",
  assist: "Agents build and review; a person merges each change.",
  autonomous: "Agents build, review and merge on their own."};
const cap = t => t.charAt(0).toUpperCase() + t.slice(1);
function buttons(name, values, current, enabled) {
  return `<span class="seg">` + values.map(v => `<button data-a="${esc(name)}" data-v="${esc(v)}" ${v === current ? 'class=on' : ''}
    ${enabled ? '' : 'disabled'}>${esc(cap(v))}</button>`).join("") + `</span>`; }
document.addEventListener("click", e => { const b = e.target.closest("button[data-a]");
  if (b) act(b.dataset.a, b.dataset.a === "repo" ? $("repo").value.trim() : b.dataset.a === "add" ? $("newagent").value.trim()
    : b.dataset.v, b.dataset.g); });
async function refresh() {
  if (!session) return;
  const r = await fetch("console/state?room=" + encodeURIComponent(room), {headers: {"Authorization": "Bearer " + session}});
  if (r.status === 401) { session = null; sessionStorage.removeItem("hagency-session"); return start(); }
  const s = await r.json(); if (s.error) { $("me").textContent = s.error; return; }
  if (document.activeElement && ["repo", "newagent"].includes(document.activeElement.id)) return;   // don't wipe what's being typed
  $("me").textContent = "";
  $("title").textContent = s.room;
  $("sub").innerHTML = s.repo ? `<span class="chip code">${esc(s.repo)}</span>` : `<span class="chip">Pick a project repo</span>`;
  $("who").innerHTML = `<span class="chip">${esc(s.user)}</span> <span class="chip role">${s.moderator ? "Owner / maintainer" : "Contributor"}</span>`;
  let h = "";
  if (s.waiting && s.waiting.mine) h += `<section class="card attn"><h2>Waiting for you</h2><div class="set"><div>${esc(cap(s.waiting.kind))}${
    s.waiting.items.length ? ": " + s.waiting.items.map(esc).join(" · ") : ""}</div>
    <button class="primary" data-a="approve" data-v="${esc(s.waiting.id)}">Approve 👍</button></div></section>`;
  h += `<div class="grid"><section class="card"><h2>Mode</h2>${buttons("mode", ["off", "assist", "autonomous"], s.mode, s.moderator)}
    <div class="hint">${esc(MODES[s.mode] || "")}${s.moderator ? "" : " Only the owner or a maintainer can change it."}</div></section>
    <section class="card"><h2>${s.repo ? "Project repo" : "Pick a project repo"}</h2>${s.moderator ? `<div class="field"><input id=repo placeholder="owner/name"
    value="${esc(s.repo || "")}"><button data-a="repo">Use for this room</button></div>` : `<div>${esc(s.repo || "None yet")}</div>`}
    ${s.repo ? '<div class="hint"><button data-a="check">Check readiness</button></div>' : '<div class="hint">One GitHub repo per room.</div>'}</section></div>`;
  h += agentCards(s);                        // panel_agents.js: "My agents"
  $("controls").innerHTML = h; $("p").innerHTML = s.html;      // s.html: server-escaped board (panel.render)
}
start(); setInterval(refresh, %d);
</script>""" % REFRESH_MS


def no_html(text: str) -> str:
    """Board text is markdown only: any raw HTML in it (from a stored board) shows as text."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def render(loop, room_id: str) -> str:
    """The panel's HTML for one room (all text escaped except @pm's own board markdown)."""
    room = loop.client.rooms.get(room_id)
    if room is None:
        return "<p class=muted>@pm isn't in this room.</p>"
    board = loop.boards.get(room_id, {}).get("text") or "No status yet."
    if board.startswith("📋"):                # its title line repeats the page header (repo, mode)
        board = board.split("\n", 1)[1].strip() if "\n" in board else "No status yet."
    waiting = loop.pending.get(room_id)
    parts = [f"<section class=card><h2>Project status</h2><div class=board>{markdown.markdown(github.linkify(no_html(board), loop.room_repos.get(room_id)))}</div></section>"]
    if waiting:
        who = html.escape(str(waiting.get("by", "")).split(":")[0].lstrip("@"))
        what = {"plan": "a plan", "plan_request": "turning the discussion into work", "bring": "bringing an agent",
                "mode": f"switching to {html.escape(str(waiting.get('level')))}"}.get(waiting.get("kind"), "a change")
        parts.append(f"<section class=card><h2>Waiting for approval</h2><div>{what} · {who or 'the owner'} clicks 👍 in the chat</div></section>")
    plan = loop.last_plans.get(room_id, {}).get("items", [])
    if plan:
        items = "".join(f"<li><b>{html.escape(i['title'])}</b></li>" for i in plan)
        parts.append(f"<section class=card><h2>Current plan</h2><ol>{items}</ol></section>")
    agents = []
    grants = loop.grants_cache.get(room_id, {}) if hasattr(loop, "grants_cache") else {}
    for user in sorted(room.users):
        owner = ownership.owner_id(user)
        if owner:
            here = "" if ownership.joined(room, owner) else " <span class=muted>(owner not here: idle)</span>"
            follow = registry.follow(owner, user.split(":")[0][1:])
            caps = ", ".join(sorted(grants.get(user, {}))) or "project only"
            agents.append(f"<li><div><b>{html.escape(user.split(':')[0][1:])}</b> <span class=muted>· "
                          f"{html.escape(owner.split(':')[0][1:].capitalize())}'s agent{here}</span></div>"
                          f"<div><span class=chip>comes along: {html.escape(follow)}</span> "
                          f"<span class=chip>uses: {html.escape(caps)}</span></div></li>")
    parts.append("<section class=card><h2>Agents in this room</h2><ul class=agents>"
                 + ("".join(agents) or "<li class=muted>None yet</li>") + "</ul></section>")
    return "".join(parts)


async def start(loop) -> web.AppRunner | None:
    """Serve the panel on 127.0.0.1:PORT; never fatal (the chat works without it)."""
    async def page(request):
        if not local_host(request):
            return web.Response(status=403)
        return web.Response(text=PAGE.replace("__ELEMENT_ORIGIN__", json.dumps(ELEMENT_ORIGIN)),
                            content_type="text/html", headers=HEADERS)

    async def agents_script(request):     # "My agents" (static/panel_agents.js)
        if not local_host(request):
            return web.Response(status=403)
        return web.Response(text=AGENTS_JS.read_text(encoding="utf-8"), content_type="application/javascript", headers=HEADERS)
    app = web.Application()
    app.add_routes([web.get("/panel", page), web.get("/console", page), web.get("/panel-agents.js", agents_script), *console.routes(loop, render),
                    *remote_agent.routes()])     # bring-your-own pairing (remote_agent.py)
    runner = web.AppRunner(app, access_log=None)        # the widget polls every 3 s: don't flood the log
    await runner.setup()
    try:
        await web.TCPSite(runner, HOST, PORT).start()
    except OSError as e:
        log.warning("side panel not started (port %s): %s", PORT, e)
        return None
    return runner


async def add_to_room(client, room_id: str) -> bool:
    """Add the panel to the room as an Element widget, pinned at the top above the chat."""
    resp = await client.room_put_state(room_id, "im.vector.modular.widgets",
                                       {"type": "m.custom", "url": widget_url(), "name": "Hagency",
                                        "id": WIDGET_ID, "data": {}}, state_key=WIDGET_ID)
    if not hasattr(resp, "event_id"):
        return False
    await client.room_put_state(room_id, "io.element.widgets.layout",       # pinned above the chat
                                {"widgets": {WIDGET_ID: {"container": "top", "index": 0, "width": 100, "height": 40}}})
    return True
