"""loop-bot: the autonomous GitHub loop, coordinated in a Matrix room.

Watches open `agent-ok` issues on github.REPO and, per room level (state event
org.elementbridge.loop, set with `!level off|assist|autonomous` by a room moderator):
  assign a builder agent → it claims the issue and opens a PR → ask a DIFFERENT person's agent
  to review → when GitHub itself reports the PR mergeable+approved: Assist posts
  "ready — a person merges", Autonomous asks the builder agent to merge.
loop-bot holds no GitHub credentials: it only reads (ambient gh) and talks to agents.

Usage: python loop.py
"""
import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

from nio import (AsyncClient, LoginResponse, MatrixRoom, RoomGetStateEventResponse, RoomGetStateResponse,
                 RoomMessageText)

import github
from bridge import HOMESERVER, ROOT, SERVER_NAME, is_stale, read_env

POLL_S = float(os.environ.get("LOOP_POLL_S", "30"))
MODERATOR_LEVEL = 50
STATE_FILE = Path(os.environ.get("LOOP_STATE", ROOT / "loop-state.json"))   # git-ignored; per-issue progress
ACTIVE = ("building", "reviewing", "approved", "ready", "merging", "merge_sent")
EXPECTED_STAGE = {"build": "building", "review": "reviewing", "merge": "merging"}

log = logging.getLogger("loop")


class Loop:
    def __init__(self) -> None:
        self.user_id = f"@{github.LOOP_BOT}:{SERVER_NAME}"
        self.client = AsyncClient(HOMESERVER, self.user_id, device_id="LOOP_BOT")
        self.state: dict = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
        self.started_ms = int(time.time() * 1000)

    def save(self) -> None:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        os.replace(tmp, STATE_FILE)

    async def dispatch(self, room_id: str, item: dict, agent: str, body: str, task: dict) -> None:
        """Send a task to one agent; only a result echoing this task_id can advance the item."""
        item["task_id"] = uuid.uuid4().hex
        self.save()
        await self.say(room_id, body, agent, {**task, "task_id": item["task_id"]})

    async def say(self, room_id: str, body: str, mention: str | None = None, task: dict | None = None) -> None:
        content = {"msgtype": "m.notice" if task is None else "m.text", "body": body}
        if mention:
            content["m.mentions"] = {"user_ids": [mention]}
        if task is not None:
            content[github.TASK_KEY] = {**task, "agent": mention, "repo": github.REPO}
        await self.client.room_send(room_id, "m.room.message", content)

    async def level(self, room_id: str) -> str:
        r = await self.client.room_get_state_event(room_id, github.LEVEL_EVENT, "")
        if isinstance(r, RoomGetStateEventResponse) and r.content.get("repo") == github.REPO:
            lvl = r.content.get("level")
            return lvl if lvl in github.LEVELS else "off"
        return "off"

    def agents_in(self, room: MatrixRoom) -> list[str]:
        return sorted(u for u in room.users if u.split(":")[0].endswith("-agent") and u not in room.invited_users)

    # --- chat: level command and agent results -----------------------------------------
    async def on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        if is_stale(event, self.started_ms):
            return
        body = event.body.strip()
        if body.startswith("!level"):
            await self.set_level(room, event.sender, body.split()[1:])
            return
        result = event.source.get("content", {}).get(github.RESULT_KEY)
        if isinstance(result, dict) and event.sender in self.agents_in(room):
            try:
                await self.on_result(room, event.sender, result)
            except (KeyError, TypeError, ValueError) as e:
                # A malformed result from a room member must not kill the sync loop; the item
                # stays at its stage and a person can see the log line and take over.
                log.error("bad result from %s: %s", event.sender, e)

    async def set_level(self, room: MatrixRoom, sender: str, args: list[str]) -> None:
        if not await self.is_moderator(room, sender):
            await self.say(room.room_id, "only room moderators can change the loop level")
            return
        if len(args) != 1 or args[0] not in github.LEVELS:
            await self.say(room.room_id, f"usage: !level {'|'.join(github.LEVELS)}  (now: {await self.level(room.room_id)})")
            return
        await self.client.room_put_state(room.room_id, github.LEVEL_EVENT, {"level": args[0], "repo": github.REPO})
        await self.say(room.room_id, f"loop level: {args[0]} for {github.REPO}")

    async def is_moderator(self, room: MatrixRoom, sender: str) -> bool:
        if room.power_levels.get_user_level(sender) >= MODERATOR_LEVEL:
            return True
        # Room v12+: creators outrank everyone but are NOT listed in power_levels.users, and
        # matrix-nio doesn't model that, so read the create event ourselves.
        state = await self.client.room_get_state(room.room_id)
        if not isinstance(state, RoomGetStateResponse):
            return False
        for ev in state.events:
            if ev.get("type") == "m.room.create":
                content = ev.get("content", {})
                version = str(content.get("room_version", "1"))
                creators = {ev.get("sender"), *content.get("additional_creators", [])}
                return version.isdigit() and int(version) >= 12 and sender in creators
        return False

    async def on_result(self, room: MatrixRoom, sender: str, result: dict) -> None:
        kind, key = result.get("kind"), str(result.get("issue"))
        item = self.state.get(key)
        if not isinstance(kind, str) or not item or item["room"] != room.room_id:
            return
        if item["stage"] != EXPECTED_STAGE.get(kind) or result.get("task_id") != item.get("task_id"):
            return   # late/duplicate result for a step that's no longer current
        if sender != item.get("reviewer" if result.get("kind") == "review" else "builder"):
            return   # only the agent the step was given to can report on it
        if result.get("error"):
            item["stage"] = "stopped"
            await self.say(room.room_id, f"loop stopped on #{key}; a person takes over")
        elif kind == "build":
            item.update(stage="reviewing", pr=int(result["pr"]))
            if not await self.pr_is_builders(item, key):
                item["stage"] = "stopped"
                await self.say(room.room_id, f"PR #{item['pr']} isn't {item['builder']}'s branch for #{key}; stopping")
                self.save()
                return
            others = [a for a in self.agents_in(room) if a != item["builder"]]
            if not others:
                item["stage"] = "stopped"
                await self.say(room.room_id, f"no second agent to review PR #{item['pr']}; a person reviews")
            else:
                item["reviewer"] = others[int(key) % len(others)]
                await self.dispatch(room.room_id, item, item["reviewer"],
                                    f"{item['reviewer']}: review PR #{item['pr']} against #{key}",
                                    {"kind": "review", "issue": int(key), "pr": item["pr"]})
                await self.say(room.room_id, f"review requested from {item['reviewer'].split(':')[0][1:]}")
        elif kind == "review" and int(result["pr"]) == item["pr"]:
            item.update(stage="approved" if result.get("verdict") == "approve" else "stopped", sha=str(result["sha"]))
        elif kind == "merge":
            item["stage"] = "merge_sent"    # done only once GitHub itself reports MERGED
        self.save()

    async def pr_is_builders(self, item: dict, key: str) -> bool:
        """The builder reports a PR number; check it is really its own branch for this issue."""
        info = await github.run_json(["pr", "view", str(item["pr"]), "-R", github.REPO, "--json",
                                      "headRefName,isCrossRepository,state"])
        branch = f"agent/{item['builder'].split(':')[0][1:]}/issue-{key}"
        return info.get("headRefName") == branch and not info.get("isCrossRepository") and info.get("state") == "OPEN"

    # --- polling: pick up issues, check GitHub's merge verdict -------------------------
    async def tick(self) -> None:
        for room_id, room in self.client.rooms.items():
            lvl = await self.level(room_id)
            if lvl == "off":
                continue
            active = [(k, v) for k, v in self.state.items() if v["room"] == room_id and v["stage"] in ACTIVE]
            for key, item in active:
                await self.advance(room_id, key, item, lvl)
            if not active:
                await self.claim_next(room, lvl)

    async def claim_next(self, room: MatrixRoom, lvl: str) -> None:
        issues = await github.run_json(["issue", "list", "-R", github.REPO, "--label", github.LABEL,
                                        "--state", "open", "--json", "number,title", "--limit", "50"])
        fresh = sorted((i for i in issues if str(i["number"]) not in self.state), key=lambda i: i["number"])
        agents = self.agents_in(room)
        if not fresh or not agents:
            return
        issue = fresh[0]
        n = issue["number"]
        builder = agents[n % len(agents)]
        item = self.state[str(n)] = {"room": room.room_id, "stage": "building", "builder": builder}
        await self.dispatch(room.room_id, item, builder, f"{builder}: build #{n} {issue['title']} ({lvl})",
                            {"kind": "build", "issue": n})

    async def advance(self, room_id: str, key: str, item: dict, lvl: str) -> None:
        if item["stage"] not in ("approved", "ready", "merge_sent"):
            return                                   # waiting on an agent's result message
        ok, info = await github.merge_ready(item["pr"])
        if info.get("state") == "MERGED":
            by = "the loop" if item["stage"] == "merge_sent" else "a person"
            item["stage"] = "done"
            await self.say(room_id, f"PR #{item['pr']} merged by {by}; #{key} done")
        elif item["stage"] == "merge_sent":
            pass                                     # merge requested; wait for GitHub to show MERGED
        elif ok and lvl == "autonomous":
            item["stage"] = "merging"
            await self.dispatch(room_id, item, item["builder"], f"{item['builder']}: merge PR #{item['pr']}",
                                {"kind": "merge", "issue": int(key), "pr": item["pr"], "sha": item["sha"]})
        elif ok and item["stage"] == "approved":
            item["stage"] = "ready"
            await self.say(room_id, f"PR #{item['pr']} ready — a person merges")
        elif not ok and item["stage"] == "approved" and info.get("reviewDecision") != "APPROVED":
            item["stage"] = "stopped"   # an agent approved but GitHub doesn't count it: protection missing?
            await self.say(room_id, f"PR #{item['pr']}: GitHub reports {info.get('reviewDecision') or 'no required review'}"
                           " — check the repo's branch rules; a person merges")
        self.save()

    async def run(self) -> None:
        resp = await self.client.login(read_env()["PW_LOOP_BOT"], device_name="loop-bot")
        if not isinstance(resp, LoginResponse):
            raise SystemExit(f"loop-bot login failed: {resp}")
        await self.client.sync(timeout=0, full_state=True)       # skip history
        self.client.add_event_callback(self.on_message, RoomMessageText)
        log.info("loop-bot online for %s (poll %ss)", github.REPO, POLL_S)
        sync = asyncio.create_task(self.client.sync_forever(timeout=30_000))
        while not sync.done():
            try:
                await self.tick()
            except github.GitHubError as e:
                log.error("tick failed: %s", e)   # transient gh/network errors: retry next tick
            await asyncio.sleep(POLL_S)
        await sync


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [loop-bot] %(message)s")
    logging.getLogger("nio").setLevel(logging.WARNING)
    try:
        asyncio.run(Loop().run())
    except KeyboardInterrupt:
        pass  # Ctrl-C is the normal way to stop the loop


if __name__ == "__main__":
    main()
