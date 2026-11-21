// "GitHub" in the Hagency panel and console (panel.py serves this as /panel-github.js; ADR 0007).
// Connect GitHub proves which GitHub account is yours (GitHub's device flow): @pm shows a code here
// and in your private chat, you enter it on github.com, and the card then says "GitHub: @login ✓".
// s.github comes from /console/state for the signed-in person only; null: this server doesn't use it.
function githubCard(s) {
  const g = s.github;
  if (!g) return "";
  let body = "";
  if (g.login) body += `<div class="set"><div>GitHub: <b>@${esc(g.login)}</b> ✓</div>
    <button data-a="github" data-v="disconnect">Disconnect GitHub</button></div>`;
  if (g.code) body += `<div class="set"><div>Open <a href="${esc(g.url)}" target="_blank" rel="noopener noreferrer">${esc(g.url)}</a>
    and enter <span class="chip code">${esc(g.code)}</span></div>${g.login ? "" : '<button data-a="github" data-v="disconnect">Cancel</button>'}</div>
    <div class="hint">Waiting for you to approve on GitHub… Enter the code only if you asked for it here.</div>`;
  else if (!g.login) body += `<div class="set"><div>Show @pm which GitHub account is yours.</div>
    <button class="primary" data-a="github" data-v="connect">Connect GitHub</button></div>`;
  return `<section class="card"><h2>GitHub</h2>${body}<div class="hint">Choosing a room's repo needs write access to it
    on GitHub. @pm reads only your username.</div></section>`;
}
