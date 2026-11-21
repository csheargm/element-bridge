// "My agents" in the Hagency panel and console (panel.py serves this as /panel-agents.js).
// A person may have several agents (ADR 0005): each has its own Online/Offline, Disconnect,
// "comes along" setting and, in this room, its own web-research grant. Every value shown comes from
// /console/state and is escaped here; every change goes through @pm's own checks (console.act).
function agentSeg(name, agent, values, current) {
  return `<span class="seg">` + values.map(v => `<button data-a="${esc(name)}" data-v="${esc(v)}" data-g="${esc(agent)}"
    ${v === current ? 'class=on' : ''}>${esc(cap(v))}</button>`).join("") + `</span>`;
}
function agentStatus(a) {
  if (!a.remote) return `<span class="chip">Runs on this server</span>`;
  const word = a.online === true ? "Online" : a.online === false ? "Offline" : "Not seen yet";
  return `<span class="chip">${word}</span> <button data-a="disconnect" data-v="" data-g="${esc(a.name)}">Disconnect</button>`;
}
function agentRow(a, version) {
  return `<li><div><b>${esc(a.label)}</b> <span class="muted">· ${esc(a.name)}</span>${a.here ? ' <span class="chip">in this room</span>' : ""}
    ${a.remote && a.online === false ? '<div class="hint">Run the connect line again on its computer; @pm sends its Connect page with a fresh code (🔁).</div>' : ""}</div>
    <div>${agentStatus(a)}</div></li>
    <li><div class="hint">Comes along when I join a project room${a.first ? "" : " (or invite it yourself)"}</div>${agentSeg("follow", a.name, ["always", "ask", "never"], a.follow)}</li>
    <li><div class="hint">Web research in this room · each search is listed under your question</div>${agentSeg("web", a.name, ["on", "off"], a.web ? "on" : "off")}</li>
    ${a.first && version !== undefined ? `<li><div class="hint">Code it runs: ${version ? `your own version (${esc(version)})` : "the official version"}</div>${
      version ? '<button data-a="goback" data-v="">Go back to official</button>' : ""}</li>` : ""}`;
}
function agentCards(s) {
  if (!s.agents || !s.agents.length) return "";
  const add = s.can_add ? `<div class="set"><div class="field"><input id="newagent" maxlength="9" placeholder="name, e.g. codex">
    <button data-a="add" data-v="">Add an agent</button></div><div class="hint">@pm sends its Connect page in your private chat.</div></div>` : "";
  return `<section class="card"><h2>My agents · ${s.agents.length}</h2><div class="hint">The same in every room; web research is per room.</div>
    <ul class="agents">${s.agents.map(a => agentRow(a, s.version)).join("")}</ul>${add}</section>`;
}
