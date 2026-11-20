"""Clear ~/hagency/pm for a new release (deploy.sh runs it on the VM, with @pm stopped, just before
the commit's files are unpacked there).

  git ls-tree -r -z --name-only SHA | python3 release.py ~/hagency/pm

Everything that is neither one of the commit's files nor @pm's state goes: files the commit dropped,
anything planted next to the code, the virtualenv (rebuilt from requirements.txt on start), caches.
So each release starts from exactly the commit plus STATE.
"""
import shutil
import sys
from pathlib import Path

STATE = {".env", "agents.json", "data", "custom", ".gh", "rooms", "logs"}   # @pm's, kept across releases
STATE_PREFIXES = ("loop-state",)                                             # loop-state*.json


def is_state(name: str) -> bool:
    return name in STATE or name.startswith(STATE_PREFIXES)


def stale(root: Path, tracked: set[str]) -> list[Path]:
    """Paths under root to remove: not tracked, not state, and not a folder holding tracked files."""
    folders = {str(Path(t).parent) for t in tracked} | {"."}
    folders |= {str(p) for t in tracked for p in Path(t).parents}
    out = []

    def walk(folder: Path) -> None:
        for path in sorted(folder.iterdir()):
            rel = str(path.relative_to(root))
            if folder == root and is_state(path.name):
                continue
            if path.is_dir() and not path.is_symlink() and rel in folders:
                walk(path)
            elif rel not in tracked or path.is_symlink() or path.is_dir():
                out.append(path)
    walk(root)
    return out


def clear(root: Path, tracked: set[str]) -> int:
    paths = stale(root, tracked)
    for path in paths:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    return len(paths)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    names = {n for n in sys.stdin.read().split("\0") if n}
    if "loop.py" not in names:
        raise SystemExit("✗ that isn't Hagency Rooms' file list; nothing removed")
    print(f"cleared {clear(Path(sys.argv[1]), names)} paths from the previous release")
