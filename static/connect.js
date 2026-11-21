/* The Connect page (connect_page.py): the right one-liner for this computer, with @pm's code in it.
   The code comes from the URL fragment (or the box) and only goes into the line shown: this script
   makes no request, and the page's Content-Security-Policy forbids any. */
"use strict";

const CODE_RE = /^[A-Z0-9-]{4,32}$/;          // the shape pairing.py codes have; nothing else gets in the line

const STEPS = {
  mac: { open: "Open Terminal: press ⌘ Space, type Terminal, press Return.", enter: "Return", copyKey: "\u2318C",
    needs: "You need Codex or Claude Code on this Mac, plus git and Python 3.11 or newer." },
  windows: { open: "Open PowerShell: press the Windows key, type PowerShell, press Enter (no admin needed).",
    enter: "Enter", copyKey: "Ctrl+C",
    needs: "You need Codex or Claude Code on this PC, plus Python 3.11 or newer (winget install Python.Python.3.12). Git is optional." },
  linux: { open: "Open a terminal.", enter: "Enter", copyKey: "Ctrl+C",
    needs: "You need Codex or Claude Code on this computer, plus git and Python 3.11 or newer." },
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

/** The line to paste. `consoleUrl` is the server's own address (checked by console.py's URL_RE). */
function commandFor(os, consoleUrl, code) {
  const shown = code || "YOUR-CODE";
  if (os === "windows") return `& ([scriptblock]::Create((irm ${consoleUrl}/connect.ps1))) -Code ${shown}`;
  return `curl -fsSL ${consoleUrl}/connect.sh | sh -s -- ${shown}`;
}

function copyText(text, button, key) {
  selectCommand();                            // selected first: Ctrl/Cmd-C works even if the clipboard says no
  const done = () => { button.textContent = "Copied"; setTimeout(() => { button.textContent = "Copy"; }, 2000); };
  const manual = () => { button.textContent = `Press ${key}`; };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, manual);
  } else {
    manual();
  }
}

function selectCommand() {
  const range = document.createRange();
  range.selectNodeContents(document.getElementById("command"));
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

function start() {
  const consoleUrl = document.querySelector('meta[name="hagency-console"]').content;
  const input = document.getElementById("code");
  const copy = document.getElementById("copy");
  let code = cleanCode(location.hash.slice(1));
  const detected = detectOS(navigator);
  let os = detected === "other" ? "mac" : detected;
  document.getElementById("other-device").hidden = detected !== "other";
  document.getElementById("code-box").hidden = Boolean(code);

  function show() {
    for (const tab of document.querySelectorAll("[role=tab]")) {
      tab.setAttribute("aria-selected", String(tab.dataset.os === os));
      tab.tabIndex = tab.dataset.os === os ? 0 : -1;
    }
    document.getElementById("panel").setAttribute("aria-labelledby", `tab-${os}`);
    document.getElementById("step-open").textContent = STEPS[os].open;
    document.getElementById("enter-key").textContent = STEPS[os].enter;
    document.getElementById("needs").textContent = STEPS[os].needs;
    document.getElementById("command").textContent = commandFor(os, consoleUrl, code);
    copy.disabled = !code;
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
    code = cleanCode(location.hash.slice(1));
    document.getElementById("code-box").hidden = Boolean(code);
    show();
  });
  input.addEventListener("input", () => { code = cleanCode(input.value); show(); });
  copy.addEventListener("click", () => copyText(document.getElementById("command").textContent, copy, STEPS[os].copyKey));
  show();
}

if (typeof module === "object" && module.exports) {
  module.exports = { detectOS, cleanCode, commandFor, CODE_RE };   // test_connect_page.py runs these under node
} else {
  document.addEventListener("DOMContentLoaded", start);
}
