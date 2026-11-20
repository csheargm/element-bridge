"""Voice messages: @pm transcribes them on this computer (the audio never leaves it), shows the
room what it heard, then handles the words exactly like a typed message.

A voice message can't carry an @mention, so it counts as addressed to @pm when the words say
"PM" / "Product Manager", when it approves the question @pm is waiting on, or in a two-person chat.
Mixed into loop.Loop (client, user_id, say, pending, busy, on_human, is_person).
"""
import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from nio import MatrixRoom, RoomMessageAudio

import intents
from bridge import is_stale

log = logging.getLogger("loop")
STT = os.environ.get("HAGENCY_STT") or shutil.which("mlx_whisper")   # local speech-to-text CLI
STT_MODEL = os.environ.get("HAGENCY_STT_MODEL", "mlx-community/whisper-large-v3-turbo")
MAX_BYTES = 10_000_000               # about 10 minutes of Element's Opus voice
MAX_MS = 5 * 60 * 1000
STT_TIMEOUT_S = 180
NAMED_PM = re.compile(r"\b(p\.?\s?m\.?|product manager)(?=\W|$)", re.I)


class VoiceError(Exception):
    """Why a voice message couldn't be turned into words (said in the room)."""


async def transcribe(audio: bytes) -> str:
    """Speech to text with the local model, offline. Raises VoiceError."""
    if not STT:
        raise VoiceError("speech-to-text isn't installed here (mlx_whisper)")
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "voice.ogg"
        src.write_bytes(audio)
        proc = await asyncio.create_subprocess_exec(
            STT, str(src), "--model", STT_MODEL, "--output-format", "txt", "--output-dir", d,
            "--output-name", "words", "--verbose", "False", stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, env={**os.environ, "HF_HUB_OFFLINE": "1"})   # never fetches
        try:
            _, err = await asyncio.wait_for(proc.communicate(), STT_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            raise VoiceError("transcribing took too long") from None
        words = Path(d) / "words.txt"
        if proc.returncode != 0 or not words.exists():
            log.warning("speech-to-text failed (%s): %s", proc.returncode, err.decode(errors="replace")[-300:])
            raise VoiceError("speech-to-text failed")
        return " ".join(words.read_text().split())


class Voice:
    async def on_voice(self, room: MatrixRoom, event: RoomMessageAudio) -> None:
        """Transcribing takes seconds: do it beside the sync loop, like any slow decision."""
        if (is_stale(event, self.started_ms) or not self.is_person(room, event.sender)
                or not self.sync_point.first_time(event.event_id)):
            return
        job = asyncio.create_task(self.hear(room, event))
        self.busy.add(job)
        job.add_done_callback(self.busy.discard)

    async def hear(self, room: MatrixRoom, event: RoomMessageAudio) -> None:
        content = event.source.get("content", {})
        duration = (content.get("info") or {}).get("duration") or 0
        who = event.sender.split(":")[0].lstrip("@")
        try:
            if not isinstance(duration, int) or duration > MAX_MS:
                raise VoiceError("it's longer than 5 minutes")
            resp = await self.client.download(mxc=event.url)
            body = getattr(resp, "body", None)
            if not isinstance(body, bytes) or len(body) > MAX_BYTES:
                raise VoiceError("I couldn't download it" if not isinstance(body, bytes) else "it's too large")
            text = await transcribe(body)
        except VoiceError as e:               # said in the room: the person knows to type it instead
            await self.say(room.room_id, f"🎙 I couldn't understand {who}'s voice message ({e}). Please type it.")
            return
        if not text:
            await self.say(room.room_id, f"🎙 I heard no words in {who}'s voice message.")
            return
        await self.say(room.room_id, f"🎙 {who} said: “{text}”", plain=True)   # what I understood, for everyone
        await self.on_human(room, self.as_text(room, event, text))

    def as_text(self, room: MatrixRoom, event: RoomMessageAudio, text: str) -> SimpleNamespace:
        """The voice message as the typed message it stands for (same sender, same reply)."""
        content = event.source.get("content", {})
        for_me = (NAMED_PM.search(text) is not None or len(room.users) == 2
                  or (room.room_id in self.pending and intents.is_approval(text)))   # "ok, go ahead" to my question
        body = NAMED_PM.sub("", text, count=1).lstrip(" ,.:;!-") if NAMED_PM.match(text.strip()) else text
        source = {"content": {"body": body, "m.mentions": {"user_ids": [self.user_id] if for_me else []},
                              **({"m.relates_to": content["m.relates_to"]} if "m.relates_to" in content else {})}}
        return SimpleNamespace(sender=event.sender, body=body, source=source, event_id=event.event_id)
