"""Which AI a person's further agent runs: @pm's side of the picker in "Add an agent" (the panel and the
chat). @pm only records the choice and hands it to that agent's computer with its sign-in
(remote_agent.credentials_for: agent_kind, model, base_url, as ADR 0008's connect info); connect.py
there runs it. No kind: as before (connect.py picks the CLI it finds). A remote model's API key is
never asked for, typed or stored here: the person enters it on their own computer during Connect.
TODO(ADR 0008): once the computer side's agent_kinds.py is in, check with its MODEL_RE and base_url
rules instead of the copies below (one place, R3); the values here follow them exactly.
"""
import json
import re
import urllib.parse

# kind → what people see (ADR 0008's KINDS). "remote": an OpenAI-compatible service (OpenRouter, Ollama
# Cloud, …); not agents.json's "agent": "remote" (runs on their computer), so it is kept in agent_kind.
KINDS = {"codex": "Codex", "claude": "Claude Code", "hermes": "Hermes (macOS, Linux)", "ollama": "Local model (Ollama)",
         "remote": "Remote model (OpenAI-compatible)"}
FIELDS = ("agent_kind", "model", "base_url")      # the names connect.py reads (its ADR)
SAID = {"codex": "codex", "claude": "claude", "claude code": "claude", "hermes": "hermes", "ollama": "ollama",
        "a local model": "ollama", "local model": "ollama", "local": "ollama", "remote": "remote",
        "a remote model": "remote", "remote model": "remote", "openai": "remote"}
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}")   # ADR 0008 (and never "..")
URL_MAX = 300
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")   # plain http only to their own computer (ADR 0008)
KEY_RE = re.compile(r"\b(sk|rk|pk|gsk|xai|hf|or|nvapi)[-_][A-Za-z0-9_-]{12,}|\bbearer\s+\S{12,}", re.I)
NO_KEYS = ("That looks like an API key: never type one here, in any chat. You enter it on your own computer "
           "when you connect the agent, and it stays there. (If it was a real key, revoke it with its provider.)")
HOW = ("Say which AI it runs, for example \"add another agent called pi using claude\", \"... using ollama "
       "llama3.2\" or \"... using remote https://openrouter.ai/api/v1 openai/gpt-4o\" (codex, claude, hermes, "
       "ollama [model], remote <base URL> <model>).")


LONG_RUN = 20                                 # a key-like piece: this long, letters and digits mixed,
LONGER_RUN = 32                               # or this long of either (no model name has such a word)


def looks_like_key(text: str) -> bool:
    """A key someone pasted: a known key prefix, or a long piece of mixed letters and digits (model names and
    URL paths are short words: llama3.2, openai/gpt-4o, api/v1). GPT review: plain keys had no prefix."""
    pieces = re.split(r"[/:._@+\-\s?=&]+", str(text))
    return bool(KEY_RE.search(text)) or any(len(p) >= LONGER_RUN or (len(p) >= LONG_RUN and re.search(r"[A-Za-z]", p)
                                                                      and re.search(r"\d", p)) for p in pieces)


def clean_url(text: str) -> str | None:
    """An https endpoint, or http on their own computer (no user, query, fragment, quotes, spaces or
    backslashes), or None."""
    url = str(text).strip().rstrip("/")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    try:
        parts.port                            # a malformed port: refused here as on their computer (ADR 0008)
    except ValueError:
        return None
    local = parts.scheme == "http" and parts.hostname in LOCAL_HOSTS
    if ((parts.scheme != "https" and not local) or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or len(url) > URL_MAX or re.search(r"[\s\\\"'<>`]", url)):
        return None
    return url


def choice(kind: str, model: str = "", base_url: str = "") -> dict | str:
    """The recorded choice ({agent_kind, model?, base_url?}), or why not (a sentence)."""
    if kind not in KINDS:
        return HOW
    if model and (not MODEL_RE.fullmatch(model) or ".." in model):
        return "A model's name is letters, digits and . : / @ + - only, for example llama3.2 or openai/gpt-4o."
    if kind == "remote":
        url = clean_url(base_url)
        if not url or not model:
            return "A remote model needs its base URL (https) and the model's name, for example https://openrouter.ai/api/v1 openai/gpt-4o."
        return {"agent_kind": kind, "model": model, "base_url": url}
    if base_url:
        return f"Only a remote model takes a base URL; {KINDS[kind]} runs on your computer."
    if model and kind != "ollama":
        return f"{KINDS[kind]} uses its own model settings on your computer; only Ollama and remote models take a model name here."
    return {"agent_kind": kind, **({"model": model} if model else {})}


def from_words(text: str) -> dict | str:
    """"claude", "ollama llama3.2", "remote https://… openai/gpt-4o" → the choice, or why not."""
    if looks_like_key(text):
        return NO_KEYS
    words = text.strip().rstrip(".!").split()
    for size in (3, 2, 1):                   # the longest name first: "a local model", "claude code"
        said = " ".join(words[:size]).lower()
        if said in SAID:
            rest = words[size:]
            kind = SAID[said]
            if kind == "remote":
                return choice(kind, rest[1] if len(rest) > 1 else "", rest[0] if rest else "") if len(rest) <= 2 else HOW
            return choice(kind, rest[0] if rest else "") if len(rest) <= 1 else HOW
    return HOW


def from_panel(value: str) -> tuple[str, dict | str | None]:
    """The panel's Add an agent: a plain name (no kind), or JSON {name, agent_kind, model, base_url}."""
    if not value.lstrip().startswith("{"):
        return value.strip(), None
    try:
        data = json.loads(value)
    except ValueError:
        return "", HOW
    if not isinstance(data, dict):
        return "", HOW
    text = {k: data.get(k) if isinstance(data.get(k), str) else "" for k in ("name", *FIELDS)}
    if set(data) - set(text) or any(looks_like_key(v) for v in text.values()):   # an api_key field included
        return text["name"].strip(), NO_KEYS
    if not text["agent_kind"]:
        return text["name"].strip(), None
    return text["name"].strip(), choice(text["agent_kind"], text["model"].strip(), text["base_url"].strip())


def describe(agent: dict) -> str:
    """What My agents shows: "Claude Code", "Local model (Ollama): llama3.2", "Remote: openai/gpt-4o at openrouter.ai"."""
    kind = agent.get("agent_kind")
    if kind not in KINDS:
        return ""
    if kind == "remote":
        host = urllib.parse.urlsplit(agent.get("base_url") or "").hostname or "?"
        return f"Remote: {agent.get('model') or '?'} at {host}"
    return KINDS[kind] + (f": {agent['model']}" if agent.get("model") else "")
