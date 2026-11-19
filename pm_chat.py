"""How @pm answers people. They talk in plain words ("@pm take it all the way", "@pm stop",
"@pm where are we?"); intents.py turns that into one of a few fixed intents, and this code
decides what is allowed:
  - stop: anyone in the room, at once, no confirmation (the safe direction is always open);
  - assist: the room owner or a maintainer;
  - autonomous: the owner or a maintainer, and only after a ✅ (reaction or "yes") within 5 min.
`!help`, `!status`, `!level …` still work as shortcuts for people who like commands.
Mixed into loop.Loop (client, user_id, say, level, boards, agents_in, start_plan, file_plan).
"""
import re
import time

from nio import MatrixRoom, ReactionEvent, RoomMessageText

import github
import intents
import room_state

CONFIRM_S = 300
LEVEL_WORDS = {"off": "stopped: no new work is handed out and nothing is merged",
               "assist": "assist: agents build and review; a person merges each change",
               "autonomous": "autonomous: agents build, review and merge on their own"}
HELP = ("Talk to me in plain words, for example:\n"
        "- **@pm take it all the way**: agents build, review and merge on their own (owner or maintainer, with ✅)\n"
        "- **@pm let us approve each change**: agents stop at each change for a person to merge\n"
        "- **@pm stop**: no new work and no merges, right away (anyone)\n"
        "- **@pm where are we?**: what's happening now\n"
        "- **@pm let's build it**: your agent turns this discussion into issues; ✅ to open them\n"
        f"I work on `{github.REPO}`; the pinned **Project status** shows progress live.")


class Chat:
    pending: dict           # room → the autonomous switch waiting for a ✅

    def addressed_text(self, event: RoomMessageText) -> str | None:
        """The message without its "@pm" / "Product Manager" address, or None if not for me."""
        mentions = event.source.get("content", {}).get("m.mentions", {})
        ids = mentions.get("user_ids") if isinstance(mentions, dict) else None
        mentioned = isinstance(ids, list) and self.user_id in ids
        m = re.match(r"^\s*@?(pm|product manager)\b[:,]?\s*(.*)$", event.body, re.I | re.S)
        if m:
            return m.group(2)
        return event.body if mentioned else None

    async def on_human(self, room: MatrixRoom, event: RoomMessageText) -> None:
        body, sender = event.body.strip(), event.sender
        if body.startswith("!"):
            await self.command(room, sender, body.split())
            return
        if intents.is_yes(body) and await self.confirm(room, sender):
            return
        text = self.addressed_text(event)
        if text is not None:
            await self.act(room, sender, intents.classify(text))

    async def on_reaction(self, room: MatrixRoom, event: ReactionEvent) -> None:
        wanted = self.pending.get(room.room_id, {}).get("event_id")
        if event.key in intents.CONFIRM_REACTIONS and event.reacts_to == wanted:
            await self.confirm(room, event.sender)

    async def act(self, room: MatrixRoom, sender: str, intent: str) -> None:
        rid = room.room_id
        if intent == intents.OFF:                                 # never gated: stopping is always safe
            self.pending.pop(rid, None)
            await self.say(rid, await self.change_level(rid, "off") or
                           "Stopped. No new work is handed out and nothing is merged; steps already "
                           "running finish. Tell me when to continue.")
        elif intent in (intents.ASSIST, intents.AUTONOMOUS) and not await room_state.is_moderator(self.client, room, sender):
            await self.say(rid, "Only the room owner or a maintainer can change how I run this project.")
        elif intent == intents.ASSIST:
            await self.say(rid, await self.change_level(rid, "assist") or
                           "OK: agents build and review, and each change waits for a person to merge it.")
        elif intent == intents.AUTONOMOUS:
            event_id = await self.say(rid, "Got it: I'll run this to the end on my own. Agents build, review "
                                           "and merge without asking. Say \"stop\" anytime. ✅ to confirm?")
            self.pending[rid] = {"event_id": event_id, "until": time.time() + CONFIRM_S, "by": sender}
        elif intent == intents.STATUS:
            await self.say(rid, await self.status_text(rid))
        elif intent == intents.PLAN and not await room_state.is_moderator(self.client, room, sender):
            await self.say(rid, "Only the room owner or a maintainer can turn the discussion into work.")
        elif intent == intents.PLAN:
            await self.start_plan(room, sender)
        elif intent == intents.HELP:
            await self.say(rid, HELP)
        else:
            await self.say(rid, "I didn't catch that. Say \"help\" to see what I can do.")

    async def confirm(self, room: MatrixRoom, sender: str) -> bool:
        """Apply the pending autonomous switch if a moderator confirms in time."""
        p = self.pending.get(room.room_id)
        if not p or time.time() > p["until"] or sender != p.get("by"):
            return False              # only the person who asked confirms (a stray "yes" from anyone else doesn't)
        if not await room_state.is_moderator(self.client, room, sender):
            return False              # authority re-checked now: they may have been demoted since

        self.pending.pop(room.room_id)
        if p.get("kind") == "plan":
            await self.file_plan(room, p, sender)               # pm_plan.py
        else:
            await self.say(room.room_id, await self.change_level(room.room_id, "autonomous") or
                           "Running on my own now. Say \"stop\" anytime.")
        return True

    async def change_level(self, room_id: str, level: str) -> str | None:
        """Write the room's loop level; returns an error line, or None when done. Any explicit mode
        change cancels a confirmation still waiting (it was asked for under the old mode)."""
        if self.pending.get(room_id, {}).get("kind", "autonomous") == "autonomous":
            self.pending.pop(room_id, None)
        resp = await self.client.room_put_state(room_id, github.LEVEL_EVENT, {"level": level, "repo": github.REPO})
        return None if hasattr(resp, "event_id") else f"⚠️ I couldn't change the mode: {resp}"

    async def status_text(self, room_id: str) -> str:
        agents = ", ".join(a.split(":")[0][1:] for a in self.agents_in(self.client.rooms[room_id])) or "none"
        board = self.boards.get(room_id, {}).get("event_id")
        link = f" Live progress: [Project status](https://matrix.to/#/{room_id}/{board})." if board else ""
        return f"Mode: {LEVEL_WORDS[await self.level(room_id)]}. Agents here: {agents}.{link}"

    async def command(self, room: MatrixRoom, sender: str, words: list[str]) -> None:
        cmd, args = words[0].lower(), words[1:]
        if cmd == "!help":
            await self.say(room.room_id, HELP)
        elif cmd == "!status":
            await self.say(room.room_id, await self.status_text(room.room_id))
        elif cmd == "!level" and not args:
            await self.say(room.room_id, f"loop level: {await self.level(room.room_id)} for {github.REPO}")
        elif cmd == "!level":
            # The expert shortcut: an explicit level needs no ✅, but still needs a moderator.
            if args[0] not in github.LEVELS or len(args) != 1:
                await self.say(room.room_id, f"usage: !level {'|'.join(github.LEVELS)}")
            elif args[0] != "off" and not await room_state.is_moderator(self.client, room, sender):
                await self.say(room.room_id, "only room moderators can change the loop level")
            else:
                await self.say(room.room_id, await self.change_level(room.room_id, args[0])
                               or f"loop level: {args[0]} for {github.REPO}")
