"""Voice messages for this agent, heard by the agent itself (no @pm in between): transcribed on its
owner's computer (voice.py), answered like a typed question. A voice message can't carry an
@mention, so it is for this agent when it names it ("Betty's agent, …", "betty agent", "betty-agent"),
replies to one of its messages, or is in a two-person chat with it.

Also: when someone other than the owner asks, the agent says once why it won't answer, instead of
staying silent. Mixed into bridge.Bridge (client, owner, owner_id, user_id, send, should_ignore,
answer_locked, tasks, sync_point, started_ms).
"""
import asyncio
import logging
import re
import time
from types import SimpleNamespace

from nio import MatrixRoom, RoomMessageAudio

import voice

log = logging.getLogger("bridge")
REFUSAL_EVERY_S = 600                 # the "only my owner" note, at most once per person per room per 10 min
NOT_OWNER = "sender is not the agent's owner"


class BridgeVoice:
    def named_me(self, text: str) -> bool:
        owner = re.escape(self.owner)
        return re.search(rf"\b{owner}(?:'s|s)?[\s-]*agent\b", text, re.I) is not None

    async def on_voice(self, room: MatrixRoom, event: RoomMessageAudio) -> None:
        from bridge import is_stale           # here: bridge.py imports this module
        if is_stale(event, self.started_ms) or not self.sync_point.first_time(event.event_id):
            return
        if event.sender == self.user_id or event.sender.split(":")[0].endswith("-agent"):
            return                            # never other agents' audio (loop guard)
        if sum(1 for t in self.tasks if getattr(t, "voice", False)) >= voice.MAX_QUEUED:
            log.warning("voice queue full; skipped %s", event.event_id)   # quiet: it may not be for me
            return
        job = asyncio.create_task(self.hear(room, event))
        job.voice = True
        self.tasks.add(job)
        job.add_done_callback(self.tasks.discard)

    async def hear(self, room: MatrixRoom, event: RoomMessageAudio) -> None:
        sure = (room.joined_count == 2 and room.invited_count == 0) or (
            await voice.replied_to_sender(self.client, room.room_id, event) == self.user_id)
        try:
            text = await voice.listen(self.client, event)
        except (voice.VoiceError, OSError, RuntimeError, ValueError) as e:
            log.warning("voice message %s: %s", event.event_id, e)
            if sure:                          # clearly for me: say so; otherwise it may not be my business
                await self.send(room.room_id, f"🎙 I couldn't hear that ({e}). Please type it.", reply_to=event)
            return
        if not text or not (sure or self.named_me(text)):
            return
        asked = SimpleNamespace(sender=event.sender, body=text, event_id=event.event_id, source=event.source)
        reason = self.should_ignore(room, asked)
        if reason:
            await self.refuse(room, asked, reason)
            return
        name = room.user_name(event.sender) or event.sender
        job = asyncio.create_task(self.answer_locked(room, asked, f"{name} ({event.sender}) said by voice: {text}"))
        self.tasks.add(job)
        job.add_done_callback(self.tasks.discard)

    async def refuse(self, room: MatrixRoom, event, reason: str) -> None:
        """Why I won't answer, said once in a while to the person who asked (never silent)."""
        log.info("ignored message in %s: %s", room.room_id, reason)
        if not reason.startswith(NOT_OWNER):
            return                            # other reasons (loop guard, owner absent) stay quiet on purpose
        key = (room.room_id, event.sender)
        if time.time() - self.refused.get(key, 0) < REFUSAL_EVERY_S:
            return
        self.refused[key] = time.time()
        owner = room.user_name(self.owner_id) or self.owner.capitalize()
        await self.send(room.room_id, f"I only take requests from {owner}: I work with {owner}'s files.", reply_to=event)
