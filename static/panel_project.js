// "Who can join" in the Hagency panel and console (panel.py serves this as /panel-project.js), and
// where this room's repo is run from when it only discusses it (one project room per repo:
// pm_projects.py). Values come from /console/state and are escaped here; changes go through @pm's
// own checks (console.act → set_visibility: owners and maintainers only, never "open" on a private repo).
// Each help text starts with its button's own label (a screen test saw "Private" selected over "Unlisted: ...").
const VISIBILITY = {private: "Private: not listed anywhere. People join when invited, or by asking @pm with the exact repo name.",
  listed: "Listed: shown in Find a project and the room directory. People ask to join, and an owner lets them in.",
  open: "Open: listed, and anyone can join right away."};
function projectCard(s) {
  let h = "";
  if (s.runs_in) h += `<section class="card attn"><h2>Run from another room</h2><div>This repo is run from
    ${s.runs_in.link ? `<a href="${esc(s.runs_in.link)}" target="_blank" rel="noopener">${esc(s.runs_in.name)}</a>`
      : esc(s.runs_in.name)}: this room discusses it, and its mode stays off.</div></section>`;
  const vis = s.visibility || "private";
  h += `<section class="card"><h2>Who can join</h2>${buttons("visibility", ["private", "listed", "open"], vis, s.moderator)}
    <div class="hint">${esc(VISIBILITY[vis] || "")}${s.moderator ? "" : " Only the owner or a maintainer can change it."}</div></section>`;
  return h;
}
