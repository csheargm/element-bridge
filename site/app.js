// hagencylabs.xyz: the live room in the hero, the Get started tabs, and copy buttons. No dependencies.
const PEOPLE = {
  anna: { label: "Anna", color: "#7c8cff" }, "anna-agent": { label: "Anna's agent", color: "#a9b4ff" },
  betty: { label: "Betty", color: "#2fd6b5" }, "betty-agent": { label: "Betty's agent", color: "#8cf0dc" },
  pm: { label: "@pm", color: "#ffb86b" },
};
// A real flow from the prototype: discuss → plan → 👍 → build → review by the OTHER person's agent → merge.
const SCRIPT = [
  ["anna", "partner org", "We need a Swahili greeting on the hello page, and a dark mode toggle."],
  ["betty", "Acme Labs", "Agreed. Keep the toggle in the header. <b>@pm let's build it</b>"],
  ["pm", "", "Here's the plan from your discussion:<br>1. Add a Swahili greeting<br>2. Dark mode toggle in the header<br>React 👍 to open these 2 issues.", "👍 betty"],
  ["pm", "", "Opened <code>#12</code>, <code>#13</code>. Mode: <b>autonomous</b>. Starting #12 with betty-agent."],
  ["betty-agent", "Claude Code · Betty's keys", "PR <code>#14</code> opened for #12."],
  ["anna-agent", "Hermes · Anna's keys", "Reviewed PR #14 against the issue: <b>approved</b>."],
  ["pm", "", "GitHub says #14 is approved and mergeable → merged. #13 is next.",
   null, '<div class="board"><span class="ok">●</span> Project status · #12 done · #13 building · 0 waiting on you</div>'],
  ["anna", "partner org", "@anna-agent what changed on the page so far?"],
  ["anna-agent", "Hermes · Anna's keys", "The hello page now greets in Swahili (“Habari!”), merged in #14. Dark mode is in progress in #13."],
];

function message([who, where, html, reaction, extra]) {
  const p = PEOPLE[who];
  const el = document.createElement("div");
  el.className = "msg";
  el.innerHTML = `<div class="av" style="background:${p.color}">${p.label[0] === "@" ? "pm" : p.label[0]}</div>
    <div><div class="who">${p.label}${where ? `<span>${where}</span>` : ""}</div>
    <div class="body">${html}</div>${reaction ? `<span class="react">${reaction}</span>` : ""}${extra || ""}</div>`;
  return el;   // every string above is our own fixed copy, never visitor input
}

function playRoom() {
  const feed = document.getElementById("feed");
  if (!feed) return;
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let i = 0;
  const next = () => {
    if (i === SCRIPT.length) { i = 0; feed.replaceChildren(); }
    feed.appendChild(message(SCRIPT[i++]));
    while (feed.children.length > 6) feed.firstChild.remove();
    if (!still) setTimeout(next, i === SCRIPT.length ? 5200 : 2300);
  };
  if (still) { SCRIPT.slice(-6).forEach(m => feed.appendChild(message(m))); return; }
  next();
}

function tabs() {
  const buttons = document.querySelectorAll(".tab");
  buttons.forEach(b => b.addEventListener("click", () => {
    buttons.forEach(x => x.setAttribute("aria-selected", String(x === b)));
    document.querySelectorAll("[data-panel]").forEach(p => { p.hidden = p.dataset.panel !== b.dataset.tab; });
  }));
}

function copyButtons() {
  document.querySelectorAll(".copy").forEach(b => b.addEventListener("click", async () => {
    const text = b.parentElement.querySelector("code").textContent;
    try {
      await navigator.clipboard.writeText(text);
      b.textContent = "Copied";
    } catch {
      b.textContent = "Select & copy";   // clipboard blocked (old browser or no permission): say so
    }
    setTimeout(() => { b.textContent = "Copy"; }, 1800);
  }));
}

document.getElementById("year").textContent = new Date().getFullYear();
playRoom(); tabs(); copyButtons();
