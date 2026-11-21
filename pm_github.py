"""Connect GitHub, as @pm does it (ADR 0007): "Connect GitHub" in the Hagency panel, or "connect my
github" in @pm's private chat. @pm starts GitHub's device flow (github_link.py), sends the code only
to that person (their private chat with @pm, and their own view of the panel), waits for them to
approve on github.com, and then knows their GitHub login. "Disconnect GitHub" forgets it.

One sign-in per person at a time (asking again shows the same code), a few per person per hour,
and a cap for the whole server: GitHub limits how many device codes an App may ask for.
Mixed into loop.Loop (say, private_room, in_private, addressed_text: pm_settings.py, pm_agents.py).
"""
import asyncio
import logging
import re
import time

from nio import MatrixRoom, RoomMessageText

import github
import github_link
from pm_address import without_quote
from redact import room_safe

log = logging.getLogger("loop")
_GITHUB = r"\s+(my\s+)?github(\s+account)?(\s+please)?\W*$"
CONNECT_RE = re.compile(r"^\W*(please\W+)?(connect|link|sign in with|verify)" + _GITHUB, re.I)
DISCONNECT_RE = re.compile(r"^\W*(please\W+)?(disconnect|unlink|forget)" + _GITHUB, re.I)
STARTS_PER_HOUR = 5               # one person: new codes asked for per hour
SERVER_STARTS_PER_HOUR = 40       # everyone together (GitHub rate-limits the App's device codes)
HOUR_S = 3600
CODE = ("To connect your GitHub account, open {url} and enter the code **{code}** (it works for {minutes} "
        "minutes). GitHub asks you to authorize **Hagency Rooms**: I read only your GitHub username, then "
        "throw the sign-in away. Enter this code only if you asked for it yourself.")
OFF = "This server doesn't use Connect GitHub: your agent's own GitHub login counts here."


def _recent(times: list, now: float) -> list:
    return [t for t in times if now - t < HOUR_S]


class GitHubLink:
    def _github_starts(self) -> dict:
        """Who asked for a code when (in memory): person → times, and "" → everyone's."""
        return self.__dict__.setdefault("_github_start_times", {})

    def _github_limited(self, person: str) -> str | None:
        starts, now = self._github_starts(), time.time()
        mine, everyone = _recent(starts.get(person, []), now), _recent(starts.get("", []), now)
        if len(mine) >= STARTS_PER_HOUR:
            return "You've asked for several GitHub codes this hour; please try again later."
        if len(everyone) >= SERVER_STARTS_PER_HOUR:
            return "Many people are connecting GitHub right now; please try again in a few minutes."
        starts[person], starts[""] = [*mine, now], [*everyone, now]
        return None

    async def github_action(self, person: str, value: str, dm: str | None = None) -> str:
        """The panel's (and the chat's) Connect / Disconnect GitHub. Returns what the person is told."""
        if value == "connect":
            return await self.connect_github(person, dm)
        if value == "disconnect":
            return self.disconnect_github(person)
        return "unknown action"

    async def connect_github(self, person: str, dm: str | None = None) -> str:
        return (await self.start_github(person, dm))[0]

    async def start_github(self, person: str, dm: str | None = None) -> tuple[str, bool | None]:
        """(what to tell them, whether the code reached their private chat; None: no code given)."""
        if not github_link.configured():
            return OFF, None
        flow = github_link.FLOWS.get(person)
        if flow is None or flow["expires_at"] <= time.time():
            flow = await self.new_github_flow(person)
            if isinstance(flow, str):
                return flow, None
        minutes = max(1, round((flow["expires_at"] - time.time()) / 60))
        text = CODE.format(url=flow["url"], code=flow["user_code"], minutes=minutes)
        return text, await self.tell_github(person, text, dm)

    async def new_github_flow(self, person: str) -> dict | str:
        """A new device code for `person` and its background wait; or why not (a str to tell them)."""
        starting = self.__dict__.setdefault("_github_starting", set())
        if person in starting:                        # two clicks at once: still one sign-in
            return "One moment: I'm asking GitHub for your code."
        limited = self._github_limited(person)
        if limited:
            return limited
        starting.add(person)
        try:
            flow = await github_link.start()
        except github.GitHubError as e:              # its text never carries a code or a token
            return f"I couldn't start the GitHub sign-in ({room_safe(e)}); please try again later."
        finally:
            starting.discard(person)
        old = github_link.FLOWS.get(person)
        if old and old.get("task"):
            old["task"].cancel()                      # one sign-in per person: an expired one ends
        github_link.FLOWS[person] = flow
        flow["task"] = asyncio.create_task(self.finish_github(person, flow))
        return flow

    def disconnect_github(self, person: str) -> str:
        flow = github_link.FLOWS.pop(person, None)
        if flow and flow.get("task"):
            flow["task"].cancel()
        if github_link.unlink(person):
            return "Disconnected: I no longer know a GitHub account for you."
        return "Cancelled the GitHub sign-in." if flow else "No GitHub account was connected."

    async def finish_github(self, person: str, flow: dict) -> None:
        """Waits in the background for the person to approve; tells them how it ended, privately."""
        try:
            login, gid = await github_link.wait_for_login(flow)
        except github_link.LinkFailed as e:
            said = str(e)
        except github.GitHubError as e:
            said = f"The GitHub sign-in failed ({room_safe(e)}). Click Connect GitHub to try again."
        else:
            github_link.save_link(person, login, gid)
            said = f"✓ Connected: your GitHub account is **@{login}**."
        finally:
            if github_link.FLOWS.get(person) is flow:
                github_link.FLOWS.pop(person, None)
        await self.tell_github(person, said)

    async def tell_github(self, person: str, text: str, dm: str | None = None) -> bool:
        """To that person only: the chat they asked in (private), else my private chat with them."""
        try:
            room = dm or await self.private_room(person)
            if room is not None:
                await self.say(room, text)
                return True
        except RuntimeError as e:     # Matrix refused (SendError): the panel still shows it to them
            log.warning("couldn't tell %s about GitHub: %s", person, room_safe(e))
        return False

    async def on_github_text(self, room: MatrixRoom, event: RoomMessageText) -> bool:
        """True if the message was "connect my github" / "disconnect my github" (answered here)."""
        private = self.in_private(room, event.sender)
        text = without_quote(event.body) if private else self.addressed_text(event, room)
        if text is None or not (CONNECT_RE.search(text) or DISCONNECT_RE.search(text)):
            return False
        if DISCONNECT_RE.search(text):
            await self.say(room.room_id, self.disconnect_github(event.sender))
            return True
        answer, sent = await self.start_github(event.sender, room.room_id if private else None)
        if sent is None:              # no code given (off, a limit, GitHub refused): safe to say here
            await self.say(room.room_id, answer)
        elif not private:             # the code itself never goes to a project room
            await self.say(room.room_id, "I sent you the GitHub code in our private chat." if sent else
                           "I couldn't open a private chat with you: click Connect GitHub in the Hagency panel.")
        return True
