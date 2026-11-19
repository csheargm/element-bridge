"""setup-bot: set a room up from chat instead of config files. Type `!help` in the room.

Everything it changes is the room's own Matrix state (room_state.py), so it is visible to
everyone in the room and survives restarts. Reading is open to members; changing needs a
room moderator. It holds no GitHub credentials: it only reads the loop repo.

Usage: python setup_bot.py
"""
import asyncio
import logging
import time

import markdown
from nio import (AsyncClient, LoginResponse, MatrixRoom, RoomGetEventResponse, RoomMessageText,
                 RoomPutStateResponse, RoomSendResponse)

import github
import lifecycle
import room_skills
import room_state
from bridge import HOMESERVER, SERVER_NAME, is_stale, read_env

SETUP_BOT = "setup-bot"          # Matrix localpart
HELP = f"""**setup-bot** — this room's settings, from chat. Anyone can look; moderators change.

- `!status` — loop level, repo, agents here, enabled skills, link to the live project status
- `!level off|assist|autonomous` — the GitHub loop for `{github.REPO}` (`!level` alone shows it)
- `!skills` — skills in the repo's `{room_skills.SKILL_DIR}/` folder and which are on here
- `!skill enable <name>` / `!skill disable <name>` — let this room's agents use a skill

A skill is a reviewed file `{room_skills.SKILL_DIR}/<name>.md` on the default branch. Agents propose new
ones as PRs (an issue labelled `{github.LABEL}` works). Enabling pins that exact version: if the file
changes, agents stop using it until a moderator enables it again. Skills are instructions only —
they never give an agent new tools, accounts or access to anyone's computer."""

log = logging.getLogger("setup-bot")


class SetupBot:
    def __init__(self) -> None:
        self.user_id = f"@{SETUP_BOT}:{SERVER_NAME}"
        self.client = AsyncClient(HOMESERVER, self.user_id, device_id="SETUP_BOT")
        self.started_ms = int(time.time() * 1000)
        self.sync_point = lifecycle.SyncPoint(self.client)
        self.busy: set[asyncio.Task] = set()
        self.commands = {"!help": self.cmd_help, "!status": self.cmd_status, "!level": self.cmd_level,
                         "!skills": self.cmd_skills, "!skill": self.cmd_skill}

    async def on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        if is_stale(event, self.started_ms) or not self.sync_point.first_time(event.event_id):
            return
        words = event.body.strip().split()
        handler = self.commands.get(words[0].lower()) if words else None
        if handler is None or event.sender == self.user_id or bot_like(event.sender):
            return
        if event.sender not in room.users or event.sender in room.invited_users:
            return
        try:
            reply = await handler(room, event.sender, words[1:])
        except (github.GitHubError, room_skills.SkillError) as e:
            reply = f"⚠️ couldn't read {github.REPO}: {str(e)[:200]}"
        await self.say(room.room_id, reply)

    async def say(self, room_id: str, text: str) -> None:
        content = {"msgtype": "m.notice", "body": text, "format": "org.matrix.custom.html",
                   "formatted_body": markdown.markdown(github.linkify(text))}
        resp = await self.client.room_send(room_id, "m.room.message", content)
        if not isinstance(resp, RoomSendResponse):
            log.error("could not post to %s: %s", room_id, resp)   # nothing else to tell: chat is the UI

    async def put(self, room_id: str, event_type: str, content: dict) -> str | None:
        resp = await self.client.room_put_state(room_id, event_type, content)
        return None if isinstance(resp, RoomPutStateResponse) else f"⚠️ the server refused the change: {resp}"

    async def moderator_only(self, room: MatrixRoom, sender: str) -> str | None:
        if await room_state.is_moderator(self.client, room, sender):
            return None
        return "only room moderators can change this"

    # --- commands: each returns the reply text ---------------------------------------
    async def cmd_help(self, room: MatrixRoom, sender: str, args: list[str]) -> str:
        return HELP

    async def cmd_status(self, room: MatrixRoom, sender: str, args: list[str]) -> str:
        agents = sorted(u.split(":")[0][1:] for u in room.users
                        if u.split(":")[0].endswith("-agent") and u not in room.invited_users)
        pinned = await room_state.skills(self.client, room.room_id)
        board = await self.board_link(room.room_id)
        return (f"loop: **{await room_state.level(self.client, room.room_id)}** for {github.REPO}\n\n"
                f"agents here: {', '.join(agents) or 'none'}\n\n"
                f"skills on: {', '.join(pinned) or 'none'}\n\n"
                + (f"live project status: [pinned board]({board})" if board else "no project status board yet"))

    async def board_link(self, room_id: str) -> str | None:
        """pm's pinned status board (status_board.py), as a link."""
        pins = (await room_state.read(self.client, room_id, "m.room.pinned_events")).get("pinned", [])
        for event_id in reversed(pins if isinstance(pins, list) else []):
            ev = await self.client.room_get_event(room_id, event_id)
            if isinstance(ev, RoomGetEventResponse) and ev.event.sender == f"@{github.PM_BOT}:{SERVER_NAME}":
                return f"https://matrix.to/#/{room_id}/{event_id}"
        return None

    async def cmd_level(self, room: MatrixRoom, sender: str, args: list[str]) -> str:
        now = await room_state.level(self.client, room.room_id)
        if not args:
            return f"loop level: {now} for {github.REPO}"
        refused = await self.moderator_only(room, sender)
        if refused:
            return refused
        if len(args) != 1 or args[0] not in github.LEVELS:
            return f"usage: !level {'|'.join(github.LEVELS)}  (now: {now})"
        failed = await self.put(room.room_id, github.LEVEL_EVENT, {"level": args[0], "repo": github.REPO})
        return failed or f"loop level: {args[0]} for {github.REPO}"

    async def cmd_skills(self, room: MatrixRoom, sender: str, args: list[str]) -> str:
        live = await room_skills.available(fresh=True)
        pinned = await room_state.skills(self.client, room.room_id)
        lines = [f"- `{n}` — {skill_status(n, live, pinned)}" for n in sorted({*live, *pinned})]
        return "skills:\n\n" + "\n".join(lines) if lines else (
            f"no skills yet: add `{room_skills.SKILL_DIR}/<name>.md` to {github.REPO} through a reviewed PR")

    async def cmd_skill(self, room: MatrixRoom, sender: str, args: list[str]) -> str:
        if len(args) != 2 or args[0] not in ("enable", "disable") or not room_skills.NAME_RE.match(args[1]):
            return "usage: !skill enable|disable <name>   (names: a-z, 0-9, -)"
        refused = await self.moderator_only(room, sender)
        if refused:
            return refused
        action, name = args
        pinned = await room_state.skills(self.client, room.room_id)
        if action == "disable":
            if name not in pinned:
                return f"`{name}` isn't on in this room"
            pinned.pop(name)
        else:
            sha = (await room_skills.available(fresh=True)).get(name)
            if sha is None:
                return f"there is no `{room_skills.SKILL_DIR}/{name}.md` on {github.REPO}'s default branch"
            if name not in pinned and len(pinned) >= room_skills.MAX_ENABLED:
                return f"a room can have at most {room_skills.MAX_ENABLED} skills on"
            await room_skills.text(sha)          # refuse a file that is too big or unreadable now
            pinned[name] = sha
        failed = await self.put(room.room_id, room_skills.SKILLS_EVENT, room_skills.state_content(pinned))
        if failed:
            return failed
        return (f"`{name}` on (version {pinned[name][:7]}) — agents here use it from their next turn"
                if action == "enable" else f"`{name}` off")

    async def run(self) -> None:
        resp = await self.client.login(read_env()["PW_SETUP_BOT"], device_name="setup-bot")
        if not isinstance(resp, LoginResponse):
            raise SystemExit(f"setup-bot login failed: {resp}")
        await lifecycle.first_sync(
            self.client, SETUP_BOT, self.sync_point,
            lambda: self.client.add_event_callback(self.on_message, RoomMessageText),
            lambda ms: setattr(self, "started_ms", ms))
        log.info("setup-bot online")
        await lifecycle.serve(self.client, SETUP_BOT, self.sync_point, self.busy)


def bot_like(user_id: str) -> bool:
    """Agents and bots never drive setup (no bot can change what another bot may do)."""
    local = user_id.split(":")[0]
    return local.endswith("-agent") or local[1:] in (github.PM_BOT, SETUP_BOT)


def skill_status(name: str, live: dict[str, str], pinned: dict[str, str]) -> str:
    if name not in pinned:
        return "off"
    if live.get(name) == pinned[name]:
        return f"**on** (version {pinned[name][:7]})"
    if name not in live:
        return "on, but the file is gone — agents don't use it"
    return "on, but the file changed since it was enabled — agents don't use it until a moderator enables it again"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [setup-bot] %(message)s")
    logging.getLogger("nio").setLevel(logging.WARNING)
    try:
        asyncio.run(SetupBot().run())
    except KeyboardInterrupt:
        pass  # Ctrl-C is the normal way to stop the bot


if __name__ == "__main__":
    main()
