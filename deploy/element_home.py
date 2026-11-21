"""Element Web's config and Hagency's home page for it (hostfiles.py public writes both on the VM).

Element's own first screen ("Send a Direct Message / Explore Public Rooms / Create a Group Chat")
gives a newcomer no next step. Instead, embedded_pages.home_url shows element_page/home.html:
three numbered steps whose links open things inside Element (the chat with @pm, the new-room
dialog). Caddy serves the folder at /hagency/ (Caddyfile, docker-compose.yml), on Element's own
name, because Element fetches the page and inlines it (no iframe). The login and sign-up pages
get Hagency's logo and background. Everything is served from this server: no outside assets.
"""
import html
import json
from pathlib import Path

from configure_synapse import PM_LOCALPART

SOURCE = Path(__file__).resolve().parent / "element_page"   # the page's template and images, in git
OUT = "element-home"                          # generated next to bootstrap.sh; Caddy mounts it read-only
PREFIX = "/hagency"                           # where Caddy serves OUT's three files (Caddyfile: @hagency)
PM_PLACEHOLDER = "__PM_ID__"
STATIC = ("logo.svg", "background.svg")
BRAND = "Hagency Rooms"


def pm_id(server_name: str) -> str:
    return f"@{PM_LOCALPART}:{server_name}"


def element_config(values: dict) -> dict:
    """Element points at this homeserver, shows Hagency's home page and branding."""
    rooms = f"https://{values['ROOMS_DOMAIN']}{PREFIX}"
    return {"default_server_config": {"m.homeserver": {"base_url": f"https://{values['MATRIX_DOMAIN']}",
                                                       "server_name": values["SERVER_NAME"]}},
            "disable_custom_urls": False, "disable_guests": True, "brand": BRAND,
            "embedded_pages": {"home_url": f"{rooms}/home.html"},
            "branding": {"auth_header_logo_url": f"{rooms}/logo.svg", "welcome_background_url": f"{rooms}/background.svg"}}


def home_page(server_name: str) -> str:
    """home.html with @pm's ID filled in (escaped, though envfile.py only lets a domain through)."""
    template = (SOURCE / "home.html").read_text()
    if PM_PLACEHOLDER not in template:
        raise SystemExit(f"✗ {SOURCE / 'home.html'} has no {PM_PLACEHOLDER}: the 'Get your agent' link would be broken")
    return template.replace(PM_PLACEHOLDER, html.escape(pm_id(server_name), quote=True))


def files(values: dict) -> dict[str, str]:
    """{file name in OUT: its text}: the filled-in page and the images."""
    out = {"home.html": home_page(values["SERVER_NAME"])}
    out.update({name: (SOURCE / name).read_text() for name in STATIC})
    return out


def config_text(values: dict) -> str:
    return json.dumps(element_config(values), indent=2)
