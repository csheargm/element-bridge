"""A Hermes profile for working in Hagency rooms: a separate profile with no personal skills or
memories, that obeys only its owner and @pm, posts only final replies, has no file, terminal or
web tools, and reaches repos only through the hagency MCP server (contained edits, own GitHub
identity). Used by onboard.py and test_hermes_e2e.py.

Only the model's API key is copied from the default profile, read from file to file and never
printed; nothing else personal (other keys, skills, memories) comes along.
"""
import os
import re
import subprocess
from pathlib import Path

import github
from bridge import ROOT, SERVER_NAME

HERMES_HOME = Path.home() / ".hermes"
PY = str(ROOT / ".venv" / "bin" / "python")
PROVIDER_KEYS = {"ollama-cloud": "OLLAMA_API_KEY", "openrouter": "OPENROUTER_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                 "openai": "OPENAI_API_KEY", "nous": "NOUS_API_KEY", "gemini": "GEMINI_API_KEY"}
MATRIX_KEYS = ("MATRIX_HOMESERVER", "MATRIX_USER_ID", "MATRIX_PASSWORD", "MATRIX_DEVICE_ID", "MATRIX_ALLOWED_USERS",
               "MATRIX_ALLOWED_ROOMS", "MATRIX_HOME_ROOM", "MATRIX_E2EE_MODE", "MATRIX_AUTO_THREAD",
               "MATRIX_SESSION_SCOPE", "MATRIX_REQUIRE_MENTION")
ROOM_RULES = ("\n\nIn Hagency rooms: before answering any question about the project (status, what's done or "
              "planned, what happened, what the app does), call the hagency tool project_status and, when the "
              "question is about the code or content, read_project_file. They show the live state and the whole "
              "room's conversation; your own memory of earlier turns may be out of date. "
              "When @pm gives you a task, ALWAYS call the hagency tool next_task first, even "
              "if the issue looks familiar from earlier in the conversation: each task is new work and the tools, "
              "not your memory, say what to do. Then do exactly what each answer's `next` field says. Edit repo "
              "files only with the hagency read_file/write_files tools. Other people read the room: never post "
              "file paths, folder names, tool calls or raw JSON there; reply with one short line about the outcome. "
              "Your owner may allow web research per room: then use the hagency web_search and fetch_page tools. "
              "Before doing anything outside the project that isn't a read (posting elsewhere, writing a shared "
              "document, contacting anyone), call the hagency tool ask_owner and go ahead only if it returns "
              "approved. "
              "When your owner asks you to change your own code (how you or Hagency behave), call the hagency "
              "tool start_code_change and follow its `next` fields; the change goes to their private copy and they "
              "choose with buttons in your private room. If they ask to go back to the official version, call "
              "go_back_to_official.\n")


def profile_dir(name: str) -> Path:
    return HERMES_HOME / "profiles" / name


def ensure_profile(name: str, owner: str) -> Path:
    if not profile_dir(name).exists():
        subprocess.run(["hermes", "profile", "create", name, "--no-skills", "--no-alias", "--description",
                        f"{owner}'s agent for Hagency rooms (no personal data)"], check=True, capture_output=True)
    return profile_dir(name)


def default_model() -> tuple[dict, str | None]:
    """The default profile's model settings, and the env var holding its API key."""
    text = (HERMES_HOME / "config.yaml").read_text()
    block = re.search(r"^model:\n((?:  .*\n)+)", text, re.M)
    model = dict(re.findall(r"^  (\w+): (.+)$", block.group(1), re.M)) if block else {}
    return model, PROVIDER_KEYS.get(model.get("provider", ""))


def repo_env() -> dict:
    """The repos this run works on, for the agent's MCP server (only checked owner/name values reach
    the YAML)."""
    shared_ok = {"HAGENCY_CUSTOM_SHARED_OK": "1"} if os.environ.get("HAGENCY_CUSTOM_SHARED_OK") == "1" else {}
    return {"LOOP_REPO": github.REPO,           # (and the operator's "Use it myself" opt-in, customize.py)
            "HAGENCY_REPOS": " ".join(r for r in sorted(github.ALLOWED_REPOS) if github.REPO_RE.match(r)),
            **shared_ok}


def set_repos(name: str) -> None:
    """Point the profile's hagency MCP server at this run's repos (Hermes gives MCP servers only the
    env in its config, so LOOP_REPO / HAGENCY_REPOS set for start.sh never reach them otherwise)."""
    cfg = profile_dir(name) / "config.yaml"
    text = cfg.read_text()
    for key, value in repo_env().items():
        if re.search(rf"^      {key}: .*$", text, re.M):
            text = re.sub(rf"^      {key}: .*$", f"      {key}: '{value}'", text, flags=re.M)
        else:
            text = re.sub(r"^(      HAGENCY_AGENT: .*)$", rf"\1\n      {key}: '{value}'", text, count=1, flags=re.M)
    cfg.write_text(text)
    soul = profile_dir(name) / "SOUL.md"             # the room rules too: new tools need their rule
    if soul.exists():
        soul.write_text(soul.read_text().split("\n\nIn Hagency rooms:")[0] + ROOM_RULES)


def set_code_dir(name: str, code: Path) -> None:
    """Run the profile's hagency MCP server from `code`: the official folder, or its owner's own
    version (customize.py). HAGENCY_HOME keeps it finding the official folder's state either way."""
    cfg = profile_dir(name) / "config.yaml"
    text = re.sub(r"^    args: \[.*hagency_mcp\.py\]$", f"    args: [{code / 'hagency_mcp.py'}]", cfg.read_text(), flags=re.M)
    if not re.search(r"^      HAGENCY_HOME: .*$", text, re.M):
        text = re.sub(r"^(      HAGENCY_AGENT: .*)$", rf"\1\n      HAGENCY_HOME: '{ROOT}'", text, count=1, flags=re.M)
    cfg.write_text(text)


def profile_model(profile: Path) -> tuple[dict, str | None]:
    """The model this profile already uses (e.g. chosen with `hermes -p <profile> model`), kept on
    re-runs; a new profile starts with the default profile's model."""
    existing = profile / "config.yaml"
    if existing.exists():
        block = re.search(r"^model:\n((?:  .*\n)+)", existing.read_text(), re.M)
        if block:
            model = dict(re.findall(r"^  (\w+): (.+)$", block.group(1), re.M))
            if model.get("provider"):
                return model, PROVIDER_KEYS.get(model["provider"])   # OAuth providers need no key copy
    return default_model()


def write_env(profile: Path, values: dict, key_name: str | None) -> None:
    path = profile / ".env"
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [ln for ln in lines if ln.split("=", 1)[0] not in (*MATRIX_KEYS, key_name)]
    if key_name:
        lines += [ln for ln in (HERMES_HOME / ".env").read_text().splitlines() if ln.startswith(key_name + "=")]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join([*lines, *(f"{k}={v}" for k, v in values.items())]) + "\n")


def configure(name: str, *, owner: str, agent: str, password: str, home_room: str | None,
              mcp_env: dict | None = None) -> Path:
    """Write the profile's config.yaml, .env (Matrix + model key) and room rules in SOUL.md."""
    profile = ensure_profile(name, owner)
    model, key_name = profile_model(profile)
    env = {"HAGENCY_AGENT": agent, **repo_env(), "PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
           **(mcp_env or {})}
    env_lines = "\n".join(f"      {k}: '{v}'" for k, v in env.items())
    model_lines = "\n".join(f"  {k}: {v}" for k, v in model.items())
    (profile / "config.yaml").write_text(f"""model:
{model_lines}
toolsets: [hermes-cli]
platform_toolsets:
  matrix: [todo, memory, skills]      # no file/terminal/web: repo edits go through hagency's contained tools
approvals:
  mode: manual
display:                              # a shared room sees final replies only: no tool calls, paths or drafts
  tool_progress: "off"
  interim_assistant_messages: false
  memory_notifications: "off"         # Hermes still learns; it just doesn't announce it in the room
matrix:
  require_mention: true
  gateway_restart_notification: false # restarts (e.g. after a code update) aren't announced in every room
  reply_to_mode: "off"                # answers don't quote the question; threads are kept (docs/hermes-matrix-reply-mode.patch)
onboarding:                           # no "shall I build your profile?" offers in a shared room
  profile_build: "off"
mcp_servers:
  hagency:
    command: {PY}
    args: [{ROOT / 'hagency_mcp.py'}]
    timeout: 300
    env:
{env_lines}
""")
    values = {"MATRIX_HOMESERVER": "http://127.0.0.1:8648", "MATRIX_USER_ID": f"@{agent}:{SERVER_NAME}",
              "MATRIX_PASSWORD": password, "MATRIX_DEVICE_ID": f"HERMES_{name.upper()}",
              "MATRIX_ALLOWED_USERS": f"@{owner}:{SERVER_NAME},@pm:{SERVER_NAME}",   # its owner, and @pm's tasks
              "MATRIX_E2EE_MODE": "off", "MATRIX_AUTO_THREAD": "false", "MATRIX_SESSION_SCOPE": "room",
              "MATRIX_REQUIRE_MENTION": "true", **({"MATRIX_HOME_ROOM": home_room} if home_room else {})}
    write_env(profile, values, key_name)
    soul = profile / "SOUL.md"
    soul.write_text(soul.read_text().split("\n\nIn Hagency rooms:")[0] + ROOM_RULES)
    return profile
