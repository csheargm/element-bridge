/* The Connect page (connect_page.py): numbered steps for this computer, one "Copy command" button
   for the exact line with @pm's code in it, the chosen kind's checklist, and a live status. The
   code comes from the URL fragment (or the box). It goes into the line shown and, in the body of a
   POST to this server's /connect/status, nowhere else: the page's Content-Security-Policy allows
   requests only to this origin. */
"use strict";

const CODE_RE = /^[A-Z0-9-]{4,32}$/;          // the shape pairing.py codes have; nothing else gets in the line
const KIND_RE = /^[a-z]{2,16}$/;
const FULL_CODE_RE = /^[A-Z0-9]{10}$/;               // a whole pairing.py code (dashes aside): only that is asked about
const STATUS_PATH = "/connect/status";
const POLL_MS = 3000, SLOW_POLL_MS = 10000, GIVE_UP_MS = 35 * 60 * 1000;

const STEPS = {
  mac: { title: "Open Terminal", enter: "Return", copyKey: "⌘C",
    how: "Hold the ⌘ Command key and press the Space bar. Type Terminal, then press Return. A window opens where you can type.",
    pasteTitle: "Paste it into Terminal and press Return",
    paste: "Click inside the Terminal window, press ⌘V to paste the command, then press Return.",
    basics: ["git and Python 3.11 or newer (macOS offers to install git the first time; Python from python.org)"] },
  windows: { title: "Open PowerShell", enter: "Enter", copyKey: "Ctrl+C",
    how: "Click Start (or press the Windows key), type PowerShell, then click Windows PowerShell. You don't need to run it as administrator.",
    pasteTitle: "Paste it into PowerShell and press Enter",
    paste: "Click inside the PowerShell window, press Ctrl+V (or right-click) to paste the command, then press Enter.",
    basics: ["Python 3.11 or newer (in PowerShell: winget install Python.Python.3.12). Git is optional."] },
  linux: { title: "Open a terminal", enter: "Enter", copyKey: "Ctrl+C",
    how: "Press Ctrl+Alt+T, or open your apps menu and start Terminal.",
    pasteTitle: "Paste it into the terminal and press Enter",
    paste: "Click inside the terminal window, press Ctrl+Shift+V to paste the command, then press Enter.",
    basics: ["git and Python 3.11 or newer"] },
};
const LEGACY = ["codex", "claude"];           // no kind named: Codex or Claude Code, as before

const STATUS = {
  waiting: "Waiting for your computer… This page updates by itself once the command has run.",
  connected: "✓ Connected! Your agent is starting on your computer. When the window says it runs in the "
    + "background, you can close the window and this page.",
  expired: "This code has expired or was already used. Ask @pm for a new one:",
};

/** "mac", "windows", "linux", or "other" (phones, tablets, Chromebooks, unknown). */
function detectOS(nav) {
  const data = (nav && nav.userAgentData) || null;
  const platform = String((data && data.platform) || (nav && nav.platform) || "");
  const ua = String((nav && nav.userAgent) || "");
  const all = platform + " " + ua;
  if ((data && data.mobile) || /android|iphone|ipad|ipod|cros|chrome os/i.test(all)) return "other";
  if (/mac/i.test(all) && nav && nav.maxTouchPoints > 1) return "other";   // an iPad asking for the desktop site
  if (/^win/i.test(platform) || /windows/i.test(ua)) return "windows";
  if (/mac/i.test(all)) return "mac";
  if (/linux|x11|bsd/i.test(all)) return "linux";
  return "other";
}

/** The code as pairing.py reads it, or "" when it isn't one. */
function cleanCode(raw) {
  let text = String(raw || "");
  try { text = decodeURIComponent(text); } catch (e) { return ""; }   // a broken fragment: ask for the code instead
  const code = text.replace(/^#/, "").trim().toUpperCase();
  return CODE_RE.test(code) ? code : "";
}

/** The code to follow on the status, or "" while it's still being typed (no wasted wrong guesses). */
function pollable(code) {
  return FULL_CODE_RE.test(String(code || "").replace(/-/g, "")) ? code : "";
}

/** {code, kind} from "#CODE" or "#CODE&kind=ollama" (connect_page.page_link); a kind not in `kinds` is dropped. */
function parseFragment(hash, kinds) {
  const [first, ...rest] = String(hash || "").replace(/^#/, "").split("&");
  let kind = "";
  for (const part of rest) {
    const [key, value] = part.split("=");
    if (key === "kind" && KIND_RE.test(value || "") && (kinds || []).some((k) => k.kind === value)) kind = value;
  }
  return { code: cleanCode(first), kind };
}

/** The exact line to paste: no prompt sign, no newline. `consoleUrl` is checked by console.py's URL_RE. */
function commandFor(os, consoleUrl, code, kind) {
  const shown = code || "YOUR-CODE";
  const named = kind && KIND_RE.test(kind) ? kind : "";
  if (os === "windows") {
    return `& ([scriptblock]::Create((irm -UseBasicParsing ${consoleUrl}/connect.ps1))) -Code ${shown}` + (named ? ` -Agent ${named}` : "");
  }
  return `curl -fsSL ${consoleUrl}/connect.sh | sh -s -- ${shown}` + (named ? ` --agent ${named}` : "");
}

/** What "Before you start" lists for this computer and kind (agent_kinds.kinds()). */
function needsFor(os, kind, kinds) {
  const chosen = (kinds || []).filter((k) => (kind ? k.kind === kind : LEGACY.includes(k.kind)));
  const agent = kind ? (chosen[0] ? chosen[0].needs : []) : ["One of: " + chosen.map((k) => k.needs[0]).join("; or ")];
  return [...agent, ...STEPS[os].basics];
}

/** Can this kind run on this computer? (Hermes: not on Windows.) */
function runsOn(os, kind, kinds) {
  const k = (kinds || []).find((x) => x.kind === kind);
  return !(os === "windows" && k && !k.windows);
}

function copyText(text, button, key) {
  selectCommand();                            // selected first: Ctrl/Cmd-C works even if the clipboard says no
  const done = () => { button.textContent = "Copied ✓"; setTimeout(() => { button.textContent = "Copy command"; }, 2500); };
  const manual = () => {                     // the older way, on the selection; else the person presses the keys
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (e) { copied = false; }
    if (copied) done(); else button.textContent = `Press ${key} to copy`;
  };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, manual);
  else manual();
}

function selectCommand() {
  const range = document.createRange();
  range.selectNodeContents(document.getElementById("command"));
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

/** Ask this server whether the code was used; the code goes in the body only. */
async function askStatus(code) {
  const r = await fetch(STATUS_PATH, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }), credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer" });
  if (!r.ok) return "";
  const data = await r.json();
  return Object.prototype.hasOwnProperty.call(STATUS, data.state) ? data.state : "";
}

function showStatus(state, pmLink) {
  const box = document.getElementById("status");
  box.hidden = !state;
  box.className = `status ${state}`;
  box.textContent = STATUS[state] || "";
  if (state === "expired") {
    const a = document.createElement("a");
    a.href = pmLink;
    a.rel = "noopener noreferrer";
    a.textContent = "open your chat with @pm";
    box.append(" ", a, ".");
  }
}

/** Follow one code's status until it's connected or expired (or GIVE_UP_MS); a new code starts over. */
function makePoller(show, ask = askStatus, later = setTimeout, now = Date.now) {
  let current = "", timer = null, started = 0;
  async function tick(code) {
    if (code !== current || now() - started > GIVE_UP_MS) return;
    let state = "";
    try { state = await ask(code); } catch (e) { state = ""; }   // offline for a moment: ask again, slower
    if (code !== current) return;                              // the code changed meanwhile
    show(state || "waiting");
    if (state !== "connected" && state !== "expired") timer = later(() => tick(code), state ? POLL_MS : SLOW_POLL_MS);
  }
  return function follow(code) {
    if (code === current) return;
    current = code;
    started = now();
    clearTimeout(timer);
    show(code ? "waiting" : "");
    if (code) tick(code);
  };
}

function start() {
  const consoleUrl = document.querySelector('meta[name="hagency-console"]').content;
  const pmLink = document.querySelector('meta[name="hagency-pm"]').content;
  let kinds = [];
  try { kinds = JSON.parse(document.getElementById("kinds").textContent); } catch (e) { kinds = []; }   // no list: the plain line still works
  const input = document.getElementById("code");
  const copy = document.getElementById("copy");
  let { code, kind } = parseFragment(location.hash, kinds);
  const detected = detectOS(navigator);
  let os = detected === "other" ? "mac" : detected;
  const follow = makePoller((state) => showStatus(state, pmLink));
  document.getElementById("other-device").hidden = detected !== "other";
  document.getElementById("code-box").hidden = Boolean(code);

  function show() {
    const steps = STEPS[os];
    for (const tab of document.querySelectorAll("[role=tab]")) {
      tab.setAttribute("aria-selected", String(tab.dataset.os === os));
      tab.tabIndex = tab.dataset.os === os ? 0 : -1;
    }
    for (const pic of document.querySelectorAll("svg.picture")) pic.classList.toggle("shown", pic.dataset.os === os);
    document.getElementById("panel").setAttribute("aria-labelledby", `tab-${os}`);
    document.getElementById("open-title").textContent = steps.title;
    document.getElementById("open-how").textContent = steps.how;
    document.getElementById("paste-title").textContent = steps.pasteTitle;
    document.getElementById("paste-how").textContent = steps.paste;
    for (const el of document.querySelectorAll(".enter-key")) el.textContent = steps.enter;
    const list = document.getElementById("needs-list");
    list.replaceChildren(...needsFor(os, kind, kinds).map((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      return li;
    }));
    const fits = runsOn(os, kind, kinds);
    const note = document.getElementById("no-windows");
    note.hidden = fits;
    note.textContent = fits ? "" : "This agent needs a Mac or a Linux computer: open this page there.";
    document.getElementById("command").textContent = commandFor(os, consoleUrl, code, fits ? kind : "");
    copy.disabled = !code || !fits;
  }

  const tabs = Array.from(document.querySelectorAll("[role=tab]"));
  for (const tab of tabs) {
    tab.addEventListener("click", () => { os = tab.dataset.os; show(); });
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const next = tabs[(tabs.indexOf(tab) + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      os = next.dataset.os;
      show();
      next.focus();
    });
  }
  window.addEventListener("hashchange", () => {   // a new link opened in this same tab
    ({ code, kind } = parseFragment(location.hash, kinds));
    input.value = code;
    document.getElementById("code-box").hidden = Boolean(code);
    show();
    follow(pollable(code));
  });
  input.addEventListener("input", () => { code = cleanCode(input.value); show(); follow(pollable(code)); });
  copy.addEventListener("click", () => copyText(document.getElementById("command").textContent, copy, STEPS[os].copyKey));
  show();
  follow(pollable(code));
}

if (typeof module === "object" && module.exports) {   // test_connect_page.py runs these under node
  module.exports = { detectOS, cleanCode, parseFragment, commandFor, needsFor, runsOn, makePoller, pollable, STEPS, STATUS, CODE_RE };
} else {
  document.addEventListener("DOMContentLoaded", start);
}
