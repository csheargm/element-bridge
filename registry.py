"""Who brought which agent: agents.json (git-ignored), written by onboard.py, read by the supervisor
and setup. One line per person, e.g. {"anna": {"agent": "hermes", "dm": "!room:server"}}."""
import json
import os
from pathlib import Path

PATH = Path(os.environ.get("HAGENCY_AGENTS", Path(__file__).parent / "agents.json"))
DEFAULT = {"anna": {}, "betty": {}}   # before anyone has onboarded: start.sh's --agent applies


def load() -> dict:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return dict(DEFAULT)              # not onboarded yet (or a damaged file): the two demo people
    return data if isinstance(data, dict) and data else dict(DEFAULT)


def save(data: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, PATH)
