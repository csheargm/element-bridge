// "My agents" in the Hagency panel and console (panel.py serves this as /panel-agents.js).
// A person may have several agents (ADR 0005): each has its own Online/Offline, Disconnect, Remove and
// "comes along" setting. Every value shown comes from /console/state and is escaped here;
// every change goes through @pm's own checks (console.act).
function agentSeg(name, agent, values, current) {
  return `<span class="seg">` + values.map(v => `<button data-a="${esc(name)}" data-v="${esc(v)}" data-g="${esc(agent)}"
    ${v === current ? 'class=on' : ''}>${esc(cap(v))}</button>`).join("") + `</span>`;
}
function agentStatus(a) {
  if (!a.remote) return `<span class="chip">Runs on this server</span>`;
  const word = a.online === true ? "Online" : a.online === false ? "Offline" : "Not seen yet";
  return `<span class="chip">${word}</span> <button data-a="disconnect" data-v="" data-g="${esc(a.name)}">Disconnect</button>
    ${removeButton(a)}`;
}
// Remove is for good, so it asks twice in the page (Element's widget frame may block confirm()).
let removeArmed = {agent: "", until: 0};
function removeButton(a) {
  const armed = removeArmed.agent === a.name && Date.now() < removeArmed.until;
  return `<button data-a="remove" data-v="${armed ? "sure" : ""}" data-g="${esc(a.name)}">${
    armed ? "Click again to remove it for good" : "Remove"}</button>`;
}
function confirmed(b) {                // panel.py's click handler: false = this click only armed Remove
  if (b.dataset.a !== "remove" || b.dataset.v === "sure") return true;
  removeArmed = {agent: b.dataset.g, until: Date.now() + 8000};
  b.dataset.v = "sure";
  b.textContent = "Click again to remove it for good";
  return false;
}
function agentRow(a, version) {
  return `<li><div><b>${esc(a.label)}</b> <span class="muted">· ${esc(a.name)}</span>${a.here ? ' <span class="chip">in this room</span>' : ""}
    ${a.remote && a.online === false ? '<div class="hint">Run the connect line again on its computer; @pm sends its Connect page with a fresh code (🔁).</div>' : ""}</div>
    <div>${agentStatus(a)}</div></li>
    <li><div class="hint">Comes along when I join a project room${a.first ? "" : " (or invite it yourself)"}</div>${agentSeg("follow", a.name, ["always", "ask", "never"], a.follow)}</li>
    ${a.first && version !== undefined ? `<li><div class="hint">Code it runs: ${version ? `your own version (${esc(version)})` : "the official version"}</div>${
      version ? '<button data-a="goback" data-v="">Go back to official</button>' : ""}</li>` : ""}`;
}
function agentCards(s) {
  if (!s.agents || !s.agents.length) return "";
  const add = s.can_add ? `<div class="set"><div class="field"><input id="newagent" maxlength="9" placeholder="name, e.g. codex">
    <button data-a="add" data-v="">Add an agent</button></div><div class="hint">@pm sends its Connect page in your private chat.</div></div>` : "";
  return `<section class="card"><h2>My agents · ${s.agents.length}</h2><div class="hint">The same in every room.</div>
    <ul class="agents">${s.agents.map(a => agentRow(a, s.version)).join("")}</ul>${add}</section>`;
}
