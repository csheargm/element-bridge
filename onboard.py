"""Bring a person and their own agent into Hagency — one command per person:

  python onboard.py anna --agent hermes      # anna brings her Hermes
  python onboard.py betty --agent claude     # betty brings Claude Code (or: codex)

It creates the Matrix accounts <person> and <person>-agent (generated passwords go to the
git-ignored .env and are never printed), opens a private DM between the person and their agent,
records the choice in agents.json, and for Hermes sets up the room profile "<person>room"
(hermes_profile.py). It also checks that the agent has its own GitHub identity in .gh/<person>-agent/.
Run it again any time: it only fills in what's missing. Then ./start.sh runs everyone's agent.
"""
import argparse
import re
import secrets
import sys
import urllib.parse

import agents
import github
import hermes_profile
import registry
from setup_accounts import ROOT, SERVER_NAME, api, load_env, login, register, save_env

NAME_RE = re.compile(r"^[a-z][a-z0-9]{1,23}$")


def ensure_user(env: dict, user: str, shared_secret: str) -> str:
    key = "PW_" + user.upper().replace("-", "_")
    if key not in env:
        env[key] = secrets.token_urlsafe(18)
        save_env(env)                          # saved first, so a failed register can be retried
        r = register(user, env[key], shared_secret)
        if "_status" in r:
            raise SystemExit(f"couldn't create @{user}: {r.get('errcode')} {r.get('error')}")
        print(f"  created @{user}:{SERVER_NAME}")
    return env[key]


def ensure_dm(person: str, person_pw: str, agent_pw: str, known: str | None) -> str:
    """A private room for the person and their agent, marked as a DM for both (Element, Hermes)."""
    me, bot = login(person, person_pw), login(f"{person}-agent", agent_pw)
    me_id, bot_id = f"@{person}:{SERVER_NAME}", f"@{person}-agent:{SERVER_NAME}"
    room = known
    if not room:
        room = api("POST", "/_matrix/client/v3/createRoom", {"is_direct": True, "preset": "trusted_private_chat",
                                                            "invite": [bot_id]}, token=me)["room_id"]
    api("POST", f"/_matrix/client/v3/join/{urllib.parse.quote(room)}", {}, token=bot)
    for token, user_id, other in ((me, me_id, bot_id), (bot, bot_id, me_id)):
        path = f"/_matrix/client/v3/user/{urllib.parse.quote(user_id)}/account_data/m.direct"
        direct = api("GET", path, token=token)
        direct = {k: v for k, v in direct.items() if not k.startswith("_") and k != "errcode" and k != "error"}
        if room not in direct.get(other, []):
            direct[other] = [*direct.get(other, []), room]
            api("PUT", path, direct, token=token)
    return room


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("person", help="the person's Matrix name, e.g. anna")
    ap.add_argument("--agent", required=True, choices=["hermes", *sorted(agents.BACKENDS)])
    args = ap.parse_args()
    if not NAME_RE.match(args.person):
        raise SystemExit("use a short lowercase name, letters and digits (e.g. anna)")
    yaml = (ROOT / "data" / "homeserver.yaml").read_text()
    shared_secret = re.search(r'^registration_shared_secret: "?([^"\n]+)"?', yaml, re.M).group(1)
    env = load_env()
    print(f"{args.person}:")
    person_pw = ensure_user(env, args.person, shared_secret)
    agent_pw = ensure_user(env, f"{args.person}-agent", shared_secret)
    people = registry.load()
    info = {**people.get(args.person, {}), "agent": args.agent}
    info["dm"] = ensure_dm(args.person, person_pw, agent_pw, info.get("dm"))
    people[args.person] = info
    registry.save(people)
    if args.agent == "hermes":
        hermes_profile.configure(f"{args.person}room", owner=args.person, agent=f"{args.person}-agent",
                                 password=agent_pw, home_room=info["dm"])
        print(f"  Hermes profile '{args.person}room' ready (only {args.person} and @pm can direct it)")
    print(f"  agent: {args.agent}; private DM with {args.person}-agent ready")
    if github.identity_dir(f"{args.person}-agent") is None:
        print(f"  ⚠ {args.person}-agent has no GitHub identity yet: see docs/GITHUB_SETUP.md, step 1, "
              f"with the folder .gh/{args.person}-agent", file=sys.stderr)
    print(f"  sign in to Element as {args.person} (password: PW_{args.person.upper()} in .env)")


if __name__ == "__main__":
    main()
