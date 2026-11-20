"""How @pm answers people. They talk in plain words ("@pm take it all the way", "@pm stop",
"@pm where are we?"); intents.py turns that into one of a few fixed intents, and this code
decides what is allowed:
  - stop: anyone in the room, at once, no confirmation (the safe direction is always open);
  - assist: the room owner or a maintainer;
  - autonomous: the owner or a maintainer, at once (they asked in plain words; "stop" undoes it);
  - turning the discussion into issues: the plan needs a ✅ from whoever asked for it.
`!help`, `!status`, `!level …` still work as shortcuts for people who like commands.
Mixed into loop.Loop (client, user_id, say, level, boards, agents_in, start_plan, file_plan).
"""
import asyncio
import re
import time
import urllib.parse

from nio import MatrixRoom, ReactionEvent, RoomMessageText, RoomPreset

import github
import console
import intents
import panel
import logging

import pm_brain
from saved_dict import SavedDict
import room_state

log = logging.getLogger("loop")
CONFIRM_S = 300
FOLLOW_SETTINGS = {intents.FOLLOW_ALWAYS: "always", intents.FOLLOW_ASK: "ask", intents.FOLLOW_NEVER: "never"}
NOTE_WORDS = 6               # a longer message to @pm that isn't a command is a requirement, not noise
LEVEL_WORDS = {"off": "stopped: no new work is handed out and nothing is merged",
               "assist": "assist: agents build and review; a person merges each change",
               "autonomous": "autonomous: agents build, review and merge on their own"}
HELP = ("Talk to me in plain words, for example:\n"
        "- **@pm take it all the way**: agents build, review and merge on their own (owner or maintainer)\n"
        "- **@pm let us approve each change**: agents stop at each change for a person to merge\n"
        "- **@pm stop**: no new work and no merges, right away (anyone)\n"
        "- **@pm where are we?**: what's happening now\n"
        "- **@pm let's build it**: your agent turns this discussion into issues; 👍 to open them\n"
        "- **@pm show the plan**: the plan waiting for your 👍\n"
        "- **@pm send me my console link**: settings in a page (also the Hagency panel in this room)\n"
        "- **@pm let my agent search the web here**: your own agent, this room only (\"stop letting…\" undoes it)\n"
        f"I work on `{github.REPO}`; the pinned **Project status** shows progress live.")


def replied_to(event: RoomMessageText) -> str | None:
    rel = event.source.get("content", {}).get("m.relates_to", {})
    reply = rel.get("m.in_reply_to", {}) if isinstance(rel, dict) else {}
    return reply.get("event_id") if isinstance(reply, dict) else None


def without_quote(body: str) -> str:
    """A reply's body starts with the quoted message ("> <@someone> text", then a blank line): read
    only what the person wrote, so "ok" in a reply to any message is just "ok"."""
    lines = body.strip().splitlines()
    while lines and lines[0].startswith(">"):
        lines.pop(0)
    return "\n".join(lines).strip()


class Chat:
    generation: dict                     # room → bumped by "stop" (cancels in-flight decisions)
    pending: dict           # room → the autonomous switch waiting for a ✅

    def addressed_text(self, event: RoomMessageText) -> str | None:
        """The message without its "@pm" / "Product Manager" address, or None if not for me."""
        mentions = event.source.get("content", {}).get("m.mentions", {})
        ids = mentions.get("user_ids") if isinstance(mentions, dict) else None
        mentioned = isinstance(ids, list) and self.user_id in ids
        m = re.match(r"^\s*@?(pm|product manager)\b[:,]?\s*(.*)$", without_quote(event.body), re.I | re.S)
        if m:
            return m.group(2)
        return without_quote(event.body) if mentioned else None

    async def on_human(self, room: MatrixRoom, event: RoomMessageText) -> None:
        body, sender = without_quote(event.body), event.sender
        if body.startswith("!"):
            await self.command(room, sender, body.split())
            return
        text = self.addressed_text(event)
        reply_to = replied_to(event)
        if intents.is_yes(body) or (text is not None and intents.is_yes(text)):
            waiting = self.pending.get(room.room_id, {}).get("event_id")
            if (reply_to in (None, waiting)) and await self.confirm(room, sender):
                return                # "yes", "@pm ok", or ok in a reply to the question: confirms it
            if text is not None:      # an OK meant for me, but nothing (or something else) is waiting
                await self.answer_stale_ok(room.room_id, reply_to, sender)
                return
        if text is None:
            return
        intent = intents.classify(text)
        if intent == intents.OFF or pm_brain.BRAIN == "off":
            await self.by_rules(room, sender, text, intent)    # "stop" never waits for a model
            return
        # The model can take seconds: decide in the background so the sync loop (and a "stop"
        # right behind this message) is never held up; a stop meanwhile cancels the decision.
        gen = self.generation.get(room.room_id, 0)
        job = asyncio.create_task(self.decide(room, sender, text, intent, reply_to, gen))
        self.busy.add(job)
        job.add_done_callback(self.busy.discard)

    async def by_rules(self, room: MatrixRoom, sender: str, text: str, intent: str) -> None:
        """The phrase rules (intents.py): the fast path for "stop", and the fallback for the model."""
        if intent == intents.UNKNOWN and await self.revise_plan(room, sender):
            return
        if intent == intents.UNKNOWN and len(text.split()) >= NOTE_WORDS:
            await self.say(room.room_id, "Noted. When you're ready, say \"let's build it\" and I'll turn the "
                                         "discussion into work.")    # it is part of the discussion that gets planned
            return
        await self.act(room, sender, intent)

    async def revise_plan(self, room: MatrixRoom, sender: str) -> bool:
        """A plan waits for ✅ and the owner/maintainer says what to change: plan again with it."""
        p = self.pending.get(room.room_id)
        if not p or p.get("kind") != "plan" or not await room_state.is_moderator(self.client, room, sender):
            return False
        self.pending.pop(room.room_id)
        await self.say(room.room_id, "Updating the plan with that…")
        await self.start_plan(room, sender)
        return True

    async def on_reaction(self, room: MatrixRoom, event: ReactionEvent) -> None:
        wanted = self.pending.get(room.room_id, {}).get("event_id")
        if intents.is_confirm_reaction(event.key) and event.reacts_to == wanted:
            await self.confirm(room, event.sender)

    async def act(self, room: MatrixRoom, sender: str, intent: str) -> None:
        rid = room.room_id
        if intent == intents.OFF:                                 # never gated: stopping is always safe
            self.generation[rid] = self.generation.get(rid, 0) + 1    # drop decisions still being thought about
            self.pending.pop(rid, None)
            await self.say(rid, await self.change_level(rid, "off") or
                           "Stopped. No new work is handed out and nothing is merged; steps already "
                           "running finish. Tell me when to continue.")
        elif intent in (intents.ASSIST, intents.AUTONOMOUS) and not await room_state.is_moderator(self.client, room, sender):
            await self.say(rid, "Only the room owner or a maintainer can change how I run this project.")
        elif intent == intents.ASSIST:
            await self.say(rid, await self.change_level(rid, "assist") or
                           "OK: agents build and review, and each change waits for a person to merge it.")
        elif intent == intents.AUTONOMOUS:   # an explicit phrase from the owner/maintainer: no extra ✅
            await self.say(rid, await self.change_level(rid, "autonomous") or
                           "Running on my own now: agents build, review and merge without asking. "
                           "Say \"stop\" anytime.")
        elif intent in (intents.GRANT_WEB, intents.REVOKE_WEB):   # the sender's own agent, this room only
            await self.set_grant(room, sender, "web", intent == intents.GRANT_WEB)
        elif intent == intents.CONSOLE:
            await self.send_console_link(room, sender)
        elif intent in FOLLOW_SETTINGS:      # the sender's own setting only (pm_follow.py)
            await self.set_follow(room, sender, FOLLOW_SETTINGS[intent])
        elif intent == intents.SHOW_PLAN:
            await self.show_plan(rid)
        elif intent == intents.STATUS:
            await self.say(rid, await self.status_text(rid))
        elif intent == intents.PLAN and not await room_state.is_moderator(self.client, room, sender):
            who = sender.split(":")[0].lstrip("@")         # a contributor asks; the owner decides
            event_id = await self.say(rid, f"{who} would like to turn this discussion into work. Room owner or "
                                           "a maintainer: react 👍 and I'll plan it with your agent.")
            self.pending[rid] = {"kind": "plan_request", "event_id": event_id, "until": time.time() + CONFIRM_S}
        elif intent == intents.PLAN:
            await self.start_plan(room, sender)
        elif intent == intents.HELP:
            await self.say(rid, HELP)
        elif intent == intents.ABOUT:
            await self.say(rid, "I'm the Product Manager: a coordinator, with no AI model of my own. I turn your "
                                "discussion into work and keep it moving; the thinking and building are done by "
                                "your own agents, on their owners' models and accounts.")
        else:
            await self.say(rid, "I didn't catch that. You can say: **show the plan** · **let's build it** · "
                                "**let us approve each change** · **take it all the way** · **stop** · "
                                "**where are we?**")

    async def confirm(self, room: MatrixRoom, sender: str) -> bool:
        """Apply the pending autonomous switch if a moderator confirms in time."""
        p = self.pending.get(room.room_id)
        if not p or time.time() > p["until"]:
            return False
        if p.get("kind") != "plan_request" and sender != p.get("by"):
            return False              # only the person who asked confirms (a stray "yes" from anyone else doesn't)
        if p.get("kind") != "bring" and not await room_state.is_moderator(self.client, room, sender):
            return False              # authority re-checked now (bringing your OWN agent needs none)

        self.pending.pop(room.room_id)
        self.note_outcome(p.get("event_id"), "approved")
        if p.get("kind") == "plan_request":
            await self.start_plan(room, sender)                 # planned with the confirming owner's agent
        elif p.get("kind") == "plan":
            await self.file_plan(room, p, sender)               # pm_plan.py
        elif p.get("kind") == "bring":                          # pm_follow.py: "bring your agent? ✅"
            await self.invite_agent(room.room_id, p["agent"], "Your agent is on its way in.")
        else:                                 # a mode switch asked about with ✅ (ask_mode)
            level = p.get("level", "autonomous")
            await self.say(room.room_id, await self.change_level(room.room_id, level) or
                           f"OK — {LEVEL_WORDS[level]}. Say \"stop\" anytime.")
        return True

    def outcomes(self) -> dict:
        """What became of each question I asked (event id → "approved — opened #7", ...), on disk."""
        if "_outcomes" not in self.__dict__:
            path = getattr(self, "board_file", None)
            self._outcomes = SavedDict(path.with_name(path.stem + "-outcomes.json")) if path else {}
        return self._outcomes

    def note_outcome(self, event_id: str | None, what: str) -> None:
        if event_id:
            self.outcomes()[event_id] = what

    async def answer_stale_ok(self, room_id: str, reply_to: str | None, sender: str = "") -> None:
        """An OK for something already settled (or for nothing): say what happened, never "didn't catch"."""
        done = self.outcomes().get(reply_to) if reply_to else None
        waiting = self.pending.get(room_id, {})
        if done:
            first = f"That one is already taken care of: {done}."
        elif waiting.get("by") and waiting["by"] != sender:
            first = f"That's waiting for {waiting['by'].split(':')[0].lstrip('@')} to approve."
        else:
            first = "Nothing is waiting for your OK right now."
        await self.say(room_id, f"{first} {await self.status_text(room_id)}")

    async def send_console_link(self, room: MatrixRoom, sender: str) -> None:
        """A one-time sign-in link to the console for this room, sent privately (it's a credential)."""
        dm = await self.private_room(sender)
        if dm is None:
            await self.say(room.room_id, "I couldn't open a private chat with you; the console is also in this "
                                         "room's Hagency panel (room info → Extensions).")
            return
        code = console.new_link(sender, room.room_id)               # the session it gives is for this room only
        url = f"http://localhost:{panel.PORT}/console?room={urllib.parse.quote(room.room_id)}#login={code}"
        await self.say(dm, f"Your Hagency console for **{room.display_name}** (works once, for 10 minutes): {url}")
        await self.say(room.room_id, f"Sent you a private console link, {sender.split(':')[0].lstrip('@')}.")

    async def private_room(self, user: str) -> str | None:
        known = self.dm_rooms.get(user)
        room = self.client.rooms.get(known) if known else None
        if room is not None and set(room.users) <= {self.user_id, user}:
            return known                      # still just the two of us (joined or invited)
        resp = await self.client.room_create(is_direct=True, invite=[user], preset=RoomPreset.private_chat,
                                             name="Hagency (private)")
        if not hasattr(resp, "room_id"):
            log.warning("couldn't open a DM with %s: %s", user, resp)
            return None
        self.dm_rooms[user] = resp.room_id
        return resp.room_id

    async def change_level(self, room_id: str, level: str) -> str | None:
        """Write the room's loop level; returns an error line, or None when done. Any explicit mode
        change cancels a confirmation still waiting (it was asked for under the old mode)."""
        if self.pending.get(room_id, {}).get("kind", "autonomous") == "autonomous":
            self.pending.pop(room_id, None)
        if level == "off" and not await room_state.bound(self.client, room_id):
            return None                       # stopping never binds a room to the repo (anyone may stop)
        return await self.write_level(room_id, level)

    async def write_level(self, room_id: str, level: str) -> str | None:
        """Write the level, which also binds the room to the repo: callers have checked a moderator asked."""
        resp = await self.client.room_put_state(room_id, github.LEVEL_EVENT, {"level": level, "repo": github.REPO})
        if not hasattr(resp, "event_id"):
            return f"⚠️ I couldn't change the mode: {resp}"
        self.kick()                           # start (or stop) now, not at the next poll
        return None

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
