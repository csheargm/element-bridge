"""The room's live "Project status": one message pm pins in each room and keeps editing.

It shows the loop's steps (which issue, which step, which agent, how long), what's queued, what
finished in the last day and what stopped and needs a person. It never shows what an agent does
inside a step (no paths, no tool calls). pm re-renders after every transition and poll and
edits the message only when the text changed, so idle rooms aren't flooded with edits.

Mixed into loop.Loop (which supplies client, state, level, open_issues and STATE_FILE's folder).
"""
import html
import json
import logging
import os
import time
from pathlib import Path

import markdown
from nio import RoomGetStateEventResponse, RoomSendResponse

import capabilities
import github
from loop_progress import num
import panel
import room_state

log = logging.getLogger("loop")
RECENT_S = 24 * 3600            # finished items stay on the board this long
MAX_QUEUED = 5
STEP_WORDS = {"building": "building", "reviewing": "reviewing", "fixing": "fixing review feedback", "approved": "approved, waiting for GitHub",
              "ready": "ready — a person merges", "merging": "merging", "merge_sent": "merging",
              "verifying": "checking it's already done", "confirmed_done": "confirmed already done",
              "close_ready": "already done — a person closes", "closing": "closing", "close_sent": "closing"}


def safe(text: object) -> str:
    """Issue titles and reasons come from issue authors and agents: never let them become markup
    (the board is rendered as HTML in Element and in the side panel)."""
    return html.escape(" ".join(str(text).split()), quote=False).replace("[", "&#91;").replace("*", "&#42;")


def ago(seconds: float) -> str:
    m = int(seconds // 60)
    return "just now" if m < 1 else f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


def who(user_id: object) -> str:
    return str(user_id).split(":")[0].lstrip("@") if user_id else "—"


def render(items: dict, level: str, queued: list[dict], now: float, repo: str = "") -> str:
    """The board's markdown for one room (items: this room's loop-state entries by issue number)."""
    def title(key: str, item: dict) -> str:
        return f"#{num(key)} {safe(item.get('title', ''))}".rstrip()

    lines = [f"📋 **Project status** — {repo or 'no repo yet'} · loop: **{level}**", ""]
    active = [(k, v) for k, v in items.items() if v["stage"] in STEP_WORDS]
    lines += ["**Now**", ""] + ([f"- {title(k, v)} — {STEP_WORDS[v['stage']]} · {who(v.get('agent') or v.get('builder'))}"
                             f" · {ago(now - v.get('since', now))}" for k, v in sorted(active, key=lambda kv: num(kv[0]))]
                            or ["- nothing in progress"])
    if queued:
        lines += ["", "**Next**", ""] + [f"- #{int(i['number'])} {safe(i['title'])}" for i in queued[:MAX_QUEUED]]
    recent = [(k, v) for k, v in items.items() if v["stage"] in ("done", "stopped")
              and now - v.get("ended", 0) < RECENT_S]
    done = [f"- {title(k, v)}" + (f" → PR #{v['pr']} merged" if v.get("pr") else " (closed: already done)")
            for k, v in sorted(recent, key=lambda kv: num(kv[0])) if v["stage"] == "done"]
    stopped = [f"- {title(k, v)} — {safe(v.get('why') or 'stopped')}"
               for k, v in sorted(recent, key=lambda kv: num(kv[0])) if v["stage"] == "stopped"]
    if done:
        lines += ["", "**Done (last 24 h)**", "", *done]
    if stopped:
        lines += ["", "**Needs a person**", "", *stopped]
    return "\n".join(lines)


class Boards:
    """Post, edit and pin the board. Board event ids persist next to the loop state file."""
    board_file: Path

    def load_boards(self) -> dict:
        try:
            boards = json.loads(self.board_file.read_text())
        except (OSError, ValueError):
            return {}                         # no board yet (or a damaged file): post a new one
        for board in boards.values():         # re-check real pins (and re-publish) after a restart instead
            board["pinned"] = False           # of trusting the file (both are no-ops when already done)
            board["published"] = False
        return boards

    def save_boards(self) -> None:
        tmp = self.board_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.boards))
        os.replace(tmp, self.board_file)

    async def refresh_boards(self) -> None:
        now = time.time()
        for room_id in list(self.client.rooms):
            repo = await room_state.repo_of(self.client, room_id)
            if repo is None:
                continue                      # not a project room (no repo yet)
            state = await self.client.room_get_state(room_id)
            events = getattr(state, "events", []) or []
            agents_with = {capabilities.agent_from_key(ev.get("state_key", "")) for ev in events
                           if ev.get("type") == capabilities.GRANTS_EVENT}
            self.grants_cache[room_id] = {a: {c: {} for c in capabilities.caps_in(events, a)} for a in agents_with}
            items = {k: v for k, v in self.state.items() if v.get("room") == room_id}
            text = render(items, await self.level(room_id), self.open_issues.get(room_id, []), now, repo)
            board = self.boards.get(room_id, {})
            if board.get("text") == text:
                if board.get("event_id") and not board.get("pinned"):   # e.g. posted before @pm was a moderator
                    board["pinned"] = await self.pin(room_id, board["event_id"], None)
                    self.save_boards()
                if not board.get("published"):
                    board["published"] = await self.publish(room_id, text, repo)
                    self.save_boards()
                if not board.get("panel"):           # the side panel widget, once per room
                    board["panel"] = await panel.add_to_room(self.client, room_id)
                    self.save_boards()
                continue
            event_id = await self.show(room_id, board.get("event_id"), text, repo)
            if event_id:
                pinned = board.get("pinned", False) if event_id == board.get("event_id") else False
                if not pinned:
                    pinned = await self.pin(room_id, event_id, board.get("event_id"))
                self.boards[room_id] = {"event_id": event_id, "text": text, "pinned": pinned,
                                        "published": await self.publish(room_id, text, repo),
                                        **{k: board[k] for k in ("announced", "panel") if k in board}}
                self.save_boards()

    async def publish(self, room_id: str, text: str, repo: str) -> bool:
        """The same status as room state, so every agent in the room can read it (room_context.py)."""
        resp = await self.client.room_put_state(room_id, github.STATUS_EVENT,
                                                {"repo": repo, "text": text, "ts": int(time.time())})
        return hasattr(resp, "event_id")

    async def show(self, room_id: str, event_id: str | None, text: str, repo: str) -> str | None:
        body = {"msgtype": "m.notice", "body": text, "format": "org.matrix.custom.html",
                "formatted_body": markdown.markdown(github.linkify(text, repo))}
        if event_id:                          # edit in place: Element shows the change live
            content = {**body, "body": "* " + text, "m.new_content": body,
                       "m.relates_to": {"rel_type": "m.replace", "event_id": event_id}}
            resp = await self.client.room_send(room_id, "m.room.message", content)
            if isinstance(resp, RoomSendResponse):
                return event_id
            log.warning("couldn't edit the status board in %s (%s); posting a new one", room_id, resp)
        resp = await self.client.room_send(room_id, "m.room.message", body)
        if not isinstance(resp, RoomSendResponse):
            log.error("couldn't post the status board in %s: %s", room_id, resp)   # retried next poll
            return None
        return resp.event_id

    async def pin(self, room_id: str, event_id: str, old: str | None) -> bool:
        """Pin the board (replacing an older board pin); False if not allowed yet, retried later."""
        r = await self.client.room_get_state_event(room_id, "m.room.pinned_events", "")
        pinned = r.content.get("pinned", []) if isinstance(r, RoomGetStateEventResponse) else []
        if event_id in pinned and old not in pinned:
            return True
        pinned = [e for e in pinned if e != old] + ([] if event_id in pinned else [event_id])
        resp = await self.client.room_put_state(room_id, "m.room.pinned_events", {"pinned": pinned})
        if not hasattr(resp, "event_id"):
            # The board still works unpinned; it is pinned once someone makes @pm a moderator.
            log.warning("couldn't pin the status board in %s yet: %s", room_id, resp)
            return False
        return True
