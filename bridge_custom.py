"""Change your agent's own code from your private room with it (customize.py does the work).

  Betty (in her DM with betty-agent): "change your code so the transcript caption is shorter"
  betty-agent: works on Betty's private copy, then posts what changed with one-click buttons:
    👍 Use it myself      tests it, then Betty's agent runs her version (↩️ goes back)
    📤 Send as pull request   opens a PR to the official repo, tested by GitHub
    🗑️ Discard

Only the owner, only in a two-person room with their agent. Mixed into bridge.Bridge (client,
owner, owner_id, user_id, backend, send, tasks,
started_ms, sync_point).
"""
import asyncio
import logging
import re

from nio import MatrixRoom, ReactionEvent

import agents
import customize
import github
from redact import room_safe
from saved_dict import SavedDict

log = logging.getLogger("bridge")
CHANGE = re.compile(r"^\W*(please\s+|can you\s+|could you\s+)?(change|make|add|fix|update|remove|delete|"
                    r"customi[sz]e|improve|tweak|rename|let|teach)\b", re.I)
ABOUT_CODE = re.compile(r"\b(your (own )?(code|bridge|program)|the bridge|hagency( rooms)?|yourself)\b", re.I)
GO_BACK = customize.GO_BACK
USE, SEND, DROP, BACK = "👍", "📤", "🗑️", "↩️"
OFFER_KEYS = (USE, SEND, DROP)


def edit_prompt(request: str) -> str:
    return ("You are changing the code of Hagency Rooms, the program that connects you to this chat. This folder "
            "is your owner's private copy: the running version is not affected until they choose to use it.\n"
            f"Your owner asks: {request}\n\nRules: edit only files in this folder. Keep the change small and in the "
            "existing style. Add or update a unittest (test_*.py) for the new behaviour. Never weaken the owner-only "
            "check, the agent sandbox settings or secret handling unless the request explicitly asks for that, and "
            "never touch .env or credentials. Reply with 2-4 short plain lines saying what you changed.")


class BridgeCustom:
    def wants_change(self, room: MatrixRoom, event) -> bool:
        """The owner, in our private room, asking to change this program (not a question about it)."""
        private = room.joined_count == 2 and room.invited_count == 0
        text = event.body.strip()
        return (private and event.sender == self.owner_id and "?" not in text[-1:]
                and (bool(GO_BACK.search(text)) or (bool(CHANGE.match(text)) and bool(ABOUT_CODE.search(text)))))

    def offers(self) -> SavedDict:
        """Offer message → what it offers; on disk, so a click after a restart still works."""
        return SavedDict(customize.ROOT / "data" / f"custom-offers-{self.owner}.json")

    async def change_code(self, room: MatrixRoom, event) -> None:
        rid, text = room.room_id, event.body.strip()
        if GO_BACK.search(text):
            await self.go_back(rid)
            return
        if not hasattr(self, "custom_lock"):
            self.custom_lock = asyncio.Lock()
        if self.custom_lock.locked():
            await self.send(rid, "I'm still working on your last change; ask again when it's done.", reply_to=event)
            return
        async with self.custom_lock:
            await self.send(rid, "🛠 Working on your private copy of my code. The version you use now isn't touched.")
            try:
                work = await customize.prepare(self.owner)
                summary = await agents.run_turn(self.backend, work, edit_prompt(text), write=True, fresh=True)
                sha = await customize.commit(self.owner, text)
                files = await customize.changed_files(self.owner) if sha else []
            except (agents.AgentError, github.GitHubError, customize.CustomError, OSError) as e:
                log.error("customizing failed: %s", e)
                await self.send(rid, f"⚠️ I couldn't make that change ({room_safe(e)}).", reply_to=event)
                return
            if not sha:
                await self.send(rid, f"I didn't change anything:\n\n{summary}", reply_to=event)
                return
            await self.offer(rid, sha, text, summary, files)

    async def offer(self, rid: str, sha: str, request: str, summary: str, files: list[str]) -> None:
        shown = ", ".join(f"`{f}`" for f in files[:customize.MAX_DIFF_FILES]) + (" …" if len(files) > customize.MAX_DIFF_FILES else "")
        risky = customize.sensitive(files)
        body = (f"✏️ **Your version is ready** ({sha[:7]})\n\n{summary}\n\nFiles changed since the official version: {shown}\n\n"
                + (f"⚠️ **This touches the safety checks** ({', '.join(risky)}): the code that decides who I obey "
                   "and what I may do. Read the change before using it.\n\n" if risky else "")
                + f"{USE} **Use it myself**: I test it first, then switch to it.  "
                  f"{SEND} **Send as pull request** to the official project.  {DROP} **Discard**.")
        event_id = await self.send(rid, body)
        self.offers()[event_id] = {"sha": sha, "request": request, "summary": summary, "risky": risky}
        for key in OFFER_KEYS:
            await self.react(rid, event_id, key)

    async def react(self, rid: str, event_id: str, key: str) -> None:
        try:
            await self.client.room_send(rid, "m.reaction", {"m.relates_to": {
                "rel_type": "m.annotation", "event_id": event_id, "key": key}})
        except Exception as e:                 # noqa: BLE001 — a missing button: typing still works
            log.warning("couldn't add %s in %s: %s", key, rid, e)

    async def on_custom_reaction(self, room: MatrixRoom, event: ReactionEvent) -> None:
        from bridge import is_stale           # here: bridge.py imports this module
        if event.sender != self.owner_id or is_stale(event, self.started_ms) or not self.sync_point.first_time(event.event_id):
            return                            # my own buttons, anyone else's clicks, and replays do nothing
        offer = self.offers().get(event.reacts_to)
        if not offer:
            return
        job = asyncio.create_task(self.act_on(room.room_id, event.reacts_to, offer, event.key))
        self.tasks.add(job)
        job.add_done_callback(self.tasks.discard)

    async def act_on(self, rid: str, offer_id: str, offer: dict, key: str) -> None:
        try:
            if key == BACK and offer.get("back"):
                await self.go_back(rid)
            elif key == USE and "sha" in offer:
                await self.use(rid, offer)
            elif key == SEND and "sha" in offer:
                await self.send(rid, "📤 Opening a pull request…")
                url = await customize.send_pr(self.owner, offer["sha"], offer["request"], pr_body(self.owner, offer))
                await self.send(rid, f"📤 Pull request opened: {url}\nThe project's tests run on it automatically.")
            elif key == DROP and "sha" in offer:
                done = await customize.discard(self.owner, offer["sha"])
                self.offers().pop(offer_id, None)
                await self.send(rid, "🗑️ Discarded." if done else "That's not my newest change any more, so I left it.")
        except (github.GitHubError, customize.CustomError, OSError) as e:
            log.error("custom action %s failed: %s", key, e)
            await self.send(rid, f"⚠️ That didn't work ({room_safe(e)}).")

    async def use(self, rid: str, offer: dict) -> None:
        await self.send(rid, "🧪 Testing your version before switching (a minute or two)…")
        ok, result = await customize.use(self.owner, offer["sha"])
        if not ok:
            await self.send(rid, f"❌ Its tests fail ({result}), so I'm staying on the version you use now. "
                                 "Tell me what to fix.")
            return
        back = await self.send(rid, f"✅ Tests pass ({result}). Switching to your version {offer['sha'][:7]}: "
                                    f"I'll be back in a moment.\n\n{BACK} **Go back** to the official version any time "
                                    "(or tell @pm \"go back to the official bridge\").")
        self.offers()[back] = {"back": True}
        await self.react(rid, back, BACK)

    async def go_back(self, rid: str) -> None:
        changed = customize.go_back(self.owner)
        await self.send(rid, "↩️ Going back to the official version: I'll be back in a moment." if changed
                        else "I'm already running the official version.")


def pr_body(owner: str, offer: dict) -> str:
    risky = offer.get("risky") or []
    return (f"{offer['summary']}\n\nAsked for by {owner} in their private room and made by their agent with "
            "Hagency Rooms' customize flow.\n"
            + (f"\n⚠️ Touches safety-related files: {', '.join(risky)}. Please review those carefully.\n" if risky else ""))
