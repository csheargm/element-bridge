"""The deploy settings in .env, read as data (never sourced by a shell) and validated, so the shell,
Docker Compose and configure_synapse.py all see the same plain values.

  python3 envfile.py check            # validate .env (creating it, mode 600, from .env.example)
  python3 envfile.py get NAME         # print one validated, non-secret value
Secrets are filled here, never on a command line, and never printed.
"""
import os
import re
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV, EXAMPLE = HERE / ".env", HERE / ".env.example"
DOMAIN = r"(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
RULES = {
    "ROOMS_DOMAIN": DOMAIN, "MATRIX_DOMAIN": DOMAIN, "SERVER_NAME": DOMAIN, "ADMIN_DOMAIN": DOMAIN,
    "POSTGRES_PASSWORD": r"[0-9a-f]{64}",
    "OPERATOR": r"[a-z0-9._=-]{1,64}",       # the account guard.py warns (a localpart)
}
SECRETS = {"POSTGRES_PASSWORD"}
OPTIONAL = {"OPERATOR"}                      # empty until the operator has an account


def read() -> dict:
    values = {}
    for line in ENV.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, sep, value = line.partition("=")
            if not sep:
                raise SystemExit(f"✗ .env: not NAME=value: {key.strip()[:40]}")
            values[key.strip()] = value.strip()
    return values


def ensure() -> dict:
    """Create .env (mode 600 from the start) and fill empty secrets; refuse a readable .env."""
    if not ENV.exists():
        fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(EXAMPLE.read_text())
    if ENV.stat().st_mode & 0o077:
        raise SystemExit("✗ .env is readable by others: run `chmod 600 .env`")
    values = read()
    for line in EXAMPLE.read_text().splitlines():  # settings added since this .env was made: their defaults
        key, sep, default = line.partition("=")
        if sep and not line.startswith("#") and key.strip() not in values:
            values[key.strip()] = default.strip()
    for key in SECRETS:
        if not values.get(key):
            values[key] = secrets.token_hex(32)
    lines = [ln for ln in EXAMPLE.read_text().splitlines() if ln.startswith("#") or "=" not in ln]
    body = "\n".join(lines) + "\n" + "".join(f"{k}={v}\n" for k, v in values.items())
    tmp = ENV.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    os.replace(tmp, ENV)
    return values


def validate(values: dict) -> list[str]:
    problems = [f"{k} is missing" for k in RULES if not values.get(k) and k not in OPTIONAL]
    problems += [f"{k} doesn't look right" for k, rx in RULES.items()
                 if values.get(k) and not re.fullmatch(rx, values[k])]
    return problems


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["check"]:
        problems = validate(ensure())
        print("\n".join(f"✗ {p} (edit ~/hagency/deploy/.env)" for p in problems) or "✓ .env ok")
        sys.exit(1 if problems else 0)
    if cmd == ["get"] and len(sys.argv) == 3 and sys.argv[2] in RULES and sys.argv[2] not in SECRETS:
        values = read()
        if validate(values):
            raise SystemExit("✗ run `python3 envfile.py check` first")
        print(values.get(sys.argv[2], ""))
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
