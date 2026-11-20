"""@pm's planning step: "@pm let's build it" → the requester's own agent drafts issues from the
room's discussion → @pm shows the plan → the owner or a maintainer confirms with ✅ → the same agent
opens the issues under its own GitHub identity → the loop picks them up.

@pm has no AI and no GitHub write access of its own: the requester's agent does the thinking and
the writing, so planning runs on the requester's model and account. Mixed into loop.Loop.
"""
import asyncio
import time
import urllib.parse
import uuid

from nio import MatrixRoom

import github
import planning
from setup_accounts import api

CONFIRM_S = 600
HISTORY = 200                     # room events read for the discussion


class Planner:
    plans: dict                   # room → {"task_id", "agent", "by"} while an agent drafts or files
    last_plans: dict              # room → the plan last shown (items, agent, by), kept on disk

    def own_agent(self, room: MatrixRoom, person: str) -> str | None:
        """The requester's own agent in this room (<person>-agent): planning runs on their model."""
        want = f"@{person.split(':')[0].lstrip('@')}-agent:{person.split(':', 1)[1]}"
        return want if want in self.agents_in(room) else None

    async def start_plan(self, room: MatrixRoom, sender: str) -> None:
        rid = room.room_id
        agent = self.own_agent(room, sender)
        if agent is None:
            await self.say(rid, f"I plan with your own agent, and {sender.split(':')[0]}-agent isn't in this "
                                "room. Invite it, then ask me again.")
            return
        discussion = await asyncio.to_thread(self.discussion, rid)
        issues = await github.run_json(["issue", "list", "-R", github.REPO, "--state", "open", "--label",
                                        github.LABEL, "--json", "title", "--limit", "100"])
        task_id = uuid.uuid4().hex
        self.plans[rid] = {"task_id": task_id, "agent": agent, "by": sender}
        await self.say(rid, f"{agent}: plan the work from this room's discussion", agent,
                       {"kind": "plan", "task_id": task_id, "discussion": discussion,
                        "existing": [i["title"] for i in issues]})

    def discussion(self, room_id: str) -> str:
        """What people (and their agents' chat replies) said, oldest first; no bot or task traffic."""
        path = f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/messages?dir=b&limit={HISTORY}"
        events = api("GET", path, token=self.client.access_token).get("chunk", [])
        lines = []
        for ev in reversed(events):
            content = ev.get("content", {})
            body = content.get("body", "")
            if (ev.get("type") != "m.room.message" or ev.get("sender") == self.user_id or not body
                    or github.TASK_KEY in content or github.RESULT_KEY in content or "m.new_content" in content
                    or body.startswith("!")):
                continue
            lines.append(f"{ev['sender'].split(':')[0].lstrip('@')}: {body}")
        return "\n".join(lines)[-planning.MAX_DISCUSSION:]

    async def on_plan_result(self, room: MatrixRoom, sender: str, result: dict) -> None:
        rid, plan = room.room_id, self.plans.get(room.room_id)
        if not plan or result.get("task_id") != plan["task_id"] or sender != plan["agent"]:
            return                                    # stale, or not the agent we asked
        self.plans.pop(rid)
        if result.get("error"):
            await self.say(rid, f"⚠️ {result['kind'].replace('_', ' ')} didn't work: {str(result['error'])[:200]}")
        elif result["kind"] == "plan":
            await self.propose(rid, plan, result.get("items"))
        else:
            self.last_plans.pop(rid, None)                    # opened: it's issues now, not a plan
            numbers = ", ".join(f"#{n}" for n in result.get("numbers", []))
            nxt = ("The agents start on them now." if await self.level(rid) != "off" else
                   'Say "take it all the way" or "let us approve each change" to start building.')
            await self.say(rid, f"Opened {numbers}. {nxt}")

    async def propose(self, rid: str, plan: dict, items: object) -> None:
        try:
            if not isinstance(items, list) or len(items) > planning.MAX_ITEMS:
                raise planning.PlanError("not a list of items")
            items = [planning.clean_item(x) for x in items]       # re-checked: the agent isn't trusted
        except planning.PlanError as e:
            await self.say(rid, f"⚠️ the plan came back malformed ({e}); ask me again")
            return
        if not items and self.last_plans.get(rid, {}).get("items"):
            await self.say(rid, "I found nothing to change, so the plan stays as it was:")
            await self.show_plan(rid)                         # never lose a plan to an empty revision
            return
        if not items:
            await self.say(rid, "I don't see anything new agreed in the discussion yet.")
            return
        self.last_plans[rid] = {"items": items, "agent": plan["agent"], "by": plan["by"]}
        await self.show_plan(rid)

    async def show_plan(self, rid: str) -> None:
        """Post the room's current plan with its ✅ (also for "@pm show the plan")."""
        last = self.last_plans.get(rid)
        if not last:
            await self.say(rid, "There's no plan yet. Say \"let's build it\" and I'll draft one from the discussion.")
            return
        items = last["items"]
        event_id = await self.say(rid, f"Here's the plan from your discussion:\n\n{planning.show(items)}\n\n"
                                       f"✅ to open these {len(items)} issue(s), or tell me what to change "
                                       "and ask me to plan again.")
        self.pending[rid] = {"kind": "plan", "event_id": event_id, "until": time.time() + CONFIRM_S,
                             "items": items, "agent": last["agent"], "by": last["by"]}

    async def file_plan(self, room: MatrixRoom, pending: dict, approver: str) -> None:
        task_id = uuid.uuid4().hex
        self.plans[room.room_id] = {"task_id": task_id, "agent": pending["agent"], "by": approver}
        await self.say(room.room_id, f"{pending['agent']}: open the approved issues", pending["agent"],
                       {"kind": "file_issues", "task_id": task_id, "items": pending["items"],
                        "approved_by": approver.split(":")[0].lstrip("@")})
