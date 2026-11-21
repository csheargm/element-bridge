"""Codex with another model provider: how the ollama and remote kinds run (docs/adr/0008-agent-kinds.md).

Both are `codex exec` exactly as for the codex kind (the owner's config and powers, ADR 0006, or the
lockdown for locked turns), plus `-c` overrides that pick the provider and `-m <model>`:
  - ollama: Codex's built-in "ollama" provider (http://localhost:11434/v1, Responses API).
  - remote: our own provider entry with the person's base_url, wire_api "responses", and
    env_key HAGENCY_REMOTE_API_KEY. The key goes to Codex ONLY in that environment variable: never
    in argv, a log or a config file of Codex's; Codex's own shells get it blanked
    (shell_environment_policy.set), so a command the model runs can't print it.
The key lives in remote.env (mode 600) beside the agent's credentials.json, written by connect.py
from a getpass prompt. Model and base_url come from the credentials file (checked again here).
"""
import os
import tempfile
from pathlib import Path

import agent_kinds

KEY_ENV = "HAGENCY_REMOTE_API_KEY"
KEY_FILE = "remote.env"
PROVIDER_ID = "hagency_remote"               # a TOML bare key; Codex reserves openai, ollama, lmstudio...
OLLAMA_PROVIDER = "ollama"
MAX_KEY_CHARS = 4096


class ProviderError(RuntimeError):
    pass


def valid_key(key: object) -> bool:
    """A single line of printable characters (it goes into an env file and an HTTP header)."""
    return (isinstance(key, str) and 0 < len(key) <= MAX_KEY_CHARS and key.isprintable()
            and not any(c.isspace() for c in key))


def key_path(folder: Path) -> Path:
    return folder / KEY_FILE


def save_key(folder: Path, key: str) -> Path:
    """Written as 0600 (a new temp file: no symlink games) and swapped in whole."""
    if not valid_key(key):
        raise ProviderError("that API key has spaces or odd characters in it; paste it again")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".remote-")
    with os.fdopen(fd, "w") as f:
        f.write(f"{KEY_ENV}={key}\n")
    path = key_path(folder)
    os.replace(tmp, path)
    return path


def load_key(folder: Path) -> str | None:
    """The saved key, or None when there is none (or it isn't only the owner's: then it's not used)."""
    path = key_path(folder)
    try:
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise ProviderError(f"{path} can be read by others; run connect.py again to save the key afresh")
        text = path.read_text()
    except FileNotFoundError:
        return None
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == KEY_ENV and valid_key(value.strip()):
            return value.strip()
    return None


def _setting(creds: dict, key: str, check) -> str:
    value = creds.get(key)
    if not check(value):
        raise ProviderError(f"no usable {key} saved for this agent; run connect.py again")
    return value


def args(kind: str, creds: dict) -> list[str]:
    """The `codex exec` options that point it at this kind's provider and model (no secret)."""
    model = _setting(creds, "model", agent_kinds.valid_model)
    if kind == "ollama":
        return ["-c", f'model_provider="{OLLAMA_PROVIDER}"', "-m", model]
    if kind != "remote":
        raise ProviderError(f"{kind} isn't run through a Codex model provider")
    url = _setting(creds, "base_url", agent_kinds.valid_base_url)
    p = f"model_providers.{PROVIDER_ID}"
    return ["-c", f'model_provider="{PROVIDER_ID}"', "-c", f'{p}.name="Hagency remote"',
            "-c", f'{p}.base_url="{url}"', "-c", f'{p}.env_key="{KEY_ENV}"', "-c", f'{p}.wire_api="responses"',
            "-c", f'shell_environment_policy.set.{KEY_ENV}=""', "-m", model]


def env(kind: str, creds_path: str | None) -> dict:
    """What the Codex process needs in its environment for this kind: the key for remote."""
    if kind != "remote":
        return {}
    key = load_key(Path(creds_path).parent) if creds_path else None
    if not key:
        raise ProviderError("no API key saved for the remote service; run connect.py again to enter it")
    return {KEY_ENV: key}


def scrub(text: str, extra_env: dict) -> str:
    """An error text with the key (should a CLI ever echo it) cut out."""
    key = extra_env.get(KEY_ENV)
    return text.replace(key, "***") if key else text
