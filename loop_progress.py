"""loop-bot's polling half: pick up the next issue and move each item on once GitHub's state allows.

Mixed into loop.Loop (which supplies client, state, save, say, dispatch, level, agents_in).
"""
import os
import time

from nio import MatrixRoom

import agents
import github

# Stages in which an item is still in flight (anything else is done/stopped).
ACTIVE = ("building", "reviewing", "approved", "ready", "merging", "merge_sent",
          "verifying", "confirmed_done", "close_ready", "closing", "close_sent")
CLOSE_STAGES = ("confirmed_done", "close_ready", "close_sent")
AGENT_STAGES = ("building", "reviewing", "verifying", "merging", "closing")   # waiting on an agent
GITHUB_STAGES = ("approved", "merge_sent", "close_sent")                    # waiting on GitHub
# ("ready" / "close_ready" wait on a person in Assist: no timeout.)
# A step's answer is overdue once the agent's own turn limit plus its gh/git calls have surely passed.
STEP_TIMEOUT_S = float(os.environ.get("LOOP_STEP_TIMEOUT_S", agents.TURN_TIMEOUT_S + 600))
GITHUB_WAIT_S = float(os.environ.get("LOOP_GITHUB_WAIT_S", 900))
MAX_SENDS = 2                    # first send + one resend, then a person takes over


class Progress:
    # --- polling: pick up issues, check GitHub's merge verdict -------------------------
    async def tick(self) -> None:
        for room_id, room in self.client.rooms.items():
            lvl = await self.level(room_id)
            if lvl == "off":
                continue
            active = [(k, v) for k, v in self.state.items() if v["room"] == room_id and v["stage"] in ACTIVE]
            for key, item in active:
                if not await self.watchdog(room_id, key, item):
                    await self.advance(room_id, key, item, lvl)
            if not active:
                await self.claim_next(room, lvl)

    async def watchdog(self, room_id: str, key: str, item: dict) -> bool:
        """Never wait forever: resend a step with no answer once, and stop items GitHub keeps blocked.
        Returns True if it acted on the item this tick."""
        now = time.time()
        if item.get("seen_stage") != item["stage"]:
            item.update(seen_stage=item["stage"], since=now)
            self.save()
        if item["stage"] in AGENT_STAGES and now - (item.get("sent_at") or item["since"]) > STEP_TIMEOUT_S:
            who = str(item.get("agent", "the agent")).split(":")[0].lstrip("@")
            if item.get("sends", 1) >= MAX_SENDS or "task" not in item:
                return await self.stop(room_id, key, item, f"no answer from {who} on #{key}")
            sends = item["sends"] + 1
            await self.say(room_id, f"no answer from {who} on #{key} yet; asking again")
            await self.dispatch(room_id, item, item["agent"], item["body"], item["task"])
            item["sends"] = sends
            self.save()
            return True
        if item["stage"] in GITHUB_STAGES and now - item["since"] > GITHUB_WAIT_S:
            return await self.stop(room_id, key, item, f"GitHub still blocks #{key} after {GITHUB_WAIT_S / 60:.0f} min")
        return False

    async def stop(self, room_id: str, key: str, item: dict, why: str) -> bool:
        item["stage"] = "stopped"
        self.save()
        await self.say(room_id, f"{why}; loop stopped on #{key}, a person takes over")
        return True

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
        if item["stage"] in CLOSE_STAGES:
            await self.advance_close(room_id, key, item, lvl)
            return
        if item["stage"] not in ("approved", "ready", "merge_sent"):
            return                                   # waiting on an agent's result message
        ok, info = await github.merge_ready(item["pr"])
        if info.get("state") == "MERGED":
            by = "the loop" if item["stage"] == "merge_sent" else "a person"
            item["stage"] = "done"
            await self.say(room_id, f"PR #{item['pr']} merged by {by}; #{key} done")
        elif item["stage"] == "merge_sent":
            pass                                     # merge requested; wait for GitHub to show MERGED
        elif item["stage"] == "approved" and info.get("mergeable") == "CONFLICTING":
            await self.stop(room_id, key, item, f"PR #{item['pr']} has merge conflicts")   # don't block the queue
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

    async def advance_close(self, room_id: str, key: str, item: dict, lvl: str) -> None:
        """An already-done issue, confirmed by a second agent: Autonomous closes it, Assist asks a person."""
        issue = await github.run_json(["issue", "view", key, "-R", github.REPO, "--json", "state"])
        if issue.get("state") == "CLOSED":
            by = "the loop" if item["stage"] == "close_sent" else "a person"
            item["stage"] = "done"
            await self.say(room_id, f"#{key} closed by {by} (already done)")
        elif item["stage"] == "confirmed_done" and lvl == "autonomous":
            item["stage"] = "closing"
            await self.dispatch(room_id, item, item["builder"], f"{item['builder']}: close #{key} (already done)",
                                {"kind": "close", "issue": int(key), "verifier": item["reviewer"]})
        elif item["stage"] == "confirmed_done":
            item["stage"] = "close_ready"
            await self.say(room_id, f"#{key} is already done (confirmed by {item['reviewer'].split(':')[0][1:]}) — a person closes")
        self.save()
