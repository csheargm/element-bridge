"""The Connect page: the one link @pm sends instead of a terminal line, and the Windows installer.

GET /connect/start#CODE shows the right one-liner for the visitor's computer (macOS, Windows or
Linux, detected in the browser, with tabs to switch) and a copy button. The code travels in the
URL fragment, which browsers never send to a server, so it never reaches this server's or the
proxy's logs; the page's own script only places it into the line shown, and the page's
Content-Security-Policy (no connect-src) means it couldn't send it anywhere if it tried.
GET /connect.ps1 serves the Windows installer, as /connect.sh (remote_agent.py) serves the other.
"""
from pathlib import Path

from aiohttp import web

import console
from remote_agent import CONSOLE_MARK         # the placeholder connect.sh uses, filled in the same way

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
WINDOWS_INSTALLER = ROOT / "connect.ps1"
PAGE_PATH = "/connect/start"
NO_STORE = console.NO_STORE
# Only our own script and style; no fetch, form, frame or beacon can leave the page.
PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'")
ASSETS = {f"{PAGE_PATH}.js": ("connect.js", "application/javascript"),
          f"{PAGE_PATH}.css": ("connect.css", "text/css")}


def page_link(code: str) -> str:
    """What @pm links to: the Connect page with this one-time code in the fragment."""
    return f"{console.public_url()}{PAGE_PATH}#{code}"


def _with_console(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace(CONSOLE_MARK, console.public_url())


def routes() -> list:
    async def page(request):
        # The request itself is never read: the code is in the fragment, which never arrives here.
        return web.Response(text=_with_console(STATIC / "connect.html"), content_type="text/html",
                            headers={**NO_STORE, "Content-Security-Policy": PAGE_CSP})

    async def asset(request):
        name, kind = ASSETS[request.path]
        return web.Response(text=(STATIC / name).read_text(encoding="utf-8"), content_type=kind, headers=NO_STORE)

    async def windows_installer(request):
        return web.Response(text=_with_console(WINDOWS_INSTALLER), content_type="text/plain", headers=NO_STORE)
    return [web.get(PAGE_PATH, page), *(web.get(p, asset) for p in ASSETS),
            web.get("/connect.ps1", windows_installer)]
