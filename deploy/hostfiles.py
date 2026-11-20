"""Files bootstrap.sh writes on the VM host, each atomically and without following anything a
container could have planted (a new mkstemp file in a directory only root writes, then rename).

  sudo python3 hostfiles.py secret synapse/homeserver.yaml pm-secret UID   # @pm's copy of the secret
  python3 hostfiles.py public                                               # Element's and Synapse Admin's configs
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import envfile

SECRET_NAME = "registration_shared_secret"
SECRET_RE = re.compile(r'(?m)^registration_shared_secret: "?([^"\n]+)"?')   # Synapse's own key


def replace(path: Path, text: str, mode: int, owner: tuple[int, int] | None = None) -> None:
    """Write `text` to a fresh temporary file next to `path` (O_EXCL: never an existing file or a
    link), set its mode and owner, then rename it over `path`."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            os.fchmod(f.fileno(), mode)           # on the open file, not its name: nothing to swap
            if owner is not None:
                os.fchown(f.fileno(), *owner)
        os.replace(tmp, path)                     # at worst moves a swapped-in link, never writes through it
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_regular(path: Path) -> str:
    """A container-writable folder's file, read as root: only a real file, never a link out of it."""
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"✗ {path} isn't a regular file; check it by hand")
    return path.read_text()


def copy_secret(homeserver_yaml: Path, folder: Path, uid: int) -> None:
    """Only the registration secret, for @pm (uid), read-only; the folder stays root's (0755)."""
    found = SECRET_RE.search(read_regular(homeserver_yaml))
    if not found:
        raise SystemExit("✗ homeserver.yaml has no registration_shared_secret")
    folder.mkdir(mode=0o755, exist_ok=True)
    if folder.is_symlink() or folder.stat().st_uid != os.getuid():
        raise SystemExit(f"✗ {folder} must be a real folder owned by root")
    os.chmod(folder, 0o755)
    replace(folder / SECRET_NAME, found.group(1), 0o400, (uid, uid))


def public_configs(values: dict, here: Path) -> None:
    """Element points at this homeserver; Synapse Admin may only talk to the admin host. Both are
    public by design (0644: the containers read them as their own users)."""
    element = {"default_server_config": {"m.homeserver": {"base_url": f"https://{values['MATRIX_DOMAIN']}",
                                                          "server_name": values["SERVER_NAME"]}},
               "disable_custom_urls": False, "disable_guests": True, "brand": "Hagency Rooms"}
    admin = {"restrictBaseUrl": f"https://{values['ADMIN_DOMAIN']}"}
    replace(here / "element-config.json", json.dumps(element, indent=2), 0o644)
    replace(here / "synapse-admin-config.json", json.dumps(admin, indent=2), 0o644)


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 4 and args[0] == "secret":
        copy_secret(Path(args[1]), Path(args[2]), int(args[3]))
    elif args == ["public"]:
        values = envfile.read()
        if envfile.validate(values):
            raise SystemExit("✗ run `python3 envfile.py check` first")
        public_configs(values, envfile.HERE)
    else:
        raise SystemExit(__doc__)
