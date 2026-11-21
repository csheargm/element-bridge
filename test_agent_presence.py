"""Unit tests: your agent runs in the background on your computer (agent_service.py, connect.py),
@pm tells you once when it is offline, and Disconnect signs it out (pm_presence.py, remote_agent.py).
Run: .venv/bin/python -m unittest test_agent_presence"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nio import PresenceGetResponse

import agent_service
import connect
import pm_presence
import registry
import remote_agent
import setup_accounts
from loop import Loop

S = "bridge.localhost"
NEW, PM, AGENT = f"@newbie1:{S}", f"@pm:{S}", "newbie1-agent"
CREDS = Path("/home/x/.hagency/credentials.json")


class Service(unittest.TestCase):
    def test_the_service_files_hold_no_secret_and_restart_only_after_a_crash(self):
        for text in (agent_service.plist(Path("/app"), NEW, "codex", AGENT, CREDS, Path("/logs")),
                     agent_service.unit(Path("/app"), NEW, "codex", AGENT, CREDS, Path("/logs"))):
            self.assertIn(str(CREDS), text)                    # the sign-in is read from the 0600 file
            self.assertNotIn("password", text.lower())
            self.assertIn("bridge.py", text)
            self.assertIn("/logs/newbie1-agent.log", text)
        plist = agent_service.plist(Path("/app"), "@a&b:x.org", "codex", AGENT, CREDS, Path("/logs"))
        self.assertIn("<key>RunAtLoad</key><true/>", plist)
        self.assertIn("<key>SuccessfulExit</key><false/>", plist)   # a clean exit (disconnected) stays stopped
        self.assertIn("@a&amp;b:x.org", plist)                         # escaped
        self.assertIn("Restart=on-failure", agent_service.unit(Path("/app"), NEW, "codex", AGENT, CREDS, Path("/l")))
        odd = agent_service.unit(Path("/app"), "@a%h$" + "{HOME}:x.org", "codex", AGENT, CREDS, Path("/l"))
        self.assertIn('"@a%%h$$' + '{HOME}:x.org"', odd)               # GPT review 23: systemd leaves it as sent

    def test_install_replaces_and_starts_stop_removes(self):
        home = Path(tempfile.mkdtemp())
        runs = []
        run = lambda args, **k: (runs.append(args), SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
        with mock.patch.object(agent_service, "kind", return_value="launchd"), \
                mock.patch.object(agent_service.Path, "home", return_value=home):
            how = agent_service.install(Path("/app"), NEW, "codex", AGENT, home / ".hagency" / "credentials.json", run=run)
            path = agent_service.service_file("launchd", AGENT, home)
            self.assertEqual(how, "launchd")
            self.assertTrue(path.exists())
            self.assertEqual([r[1] for r in runs], ["bootout", "bootstrap"])
            self.assertTrue(agent_service.stop(AGENT, run=run))
            self.assertFalse(path.exists())
            self.assertFalse(agent_service.stop(AGENT, run=run))

    def test_a_refused_start_is_an_error_not_silence(self):
        run = lambda args, **k: SimpleNamespace(returncode=5, stdout="", stderr="Bootstrap failed: 5")
        with mock.patch.object(agent_service, "kind", return_value="launchd"), \
                mock.patch.object(agent_service, "service_file", return_value=Path(tempfile.mkdtemp()) / "x.plist"):
            with self.assertRaisesRegex(RuntimeError, "Bootstrap failed"):
                agent_service.install(Path("/app"), NEW, "codex", AGENT, Path(tempfile.mkdtemp()) / "c.json", run=run)


class Connect(unittest.TestCase):
    def main(self, *argv, saved=None):
        home = Path(tempfile.mkdtemp())
        (home / "credentials.json").write_text(json.dumps(saved or {"homeserver": "https://m.org", "user_id": f"@{AGENT}:m.org",
                                                                    "password": "pw", "owner": "newbie1", "agent": "codex"}))
        with mock.patch.object(sys, "argv", ["connect.py", "--home", str(home), *argv]), \
                mock.patch.object(connect.shutil, "which", lambda b: True), \
                mock.patch.object(connect.os, "execve") as execve, mock.patch("builtins.print") as out:
            connect.main()
        return execve, " ".join(str(c.args[0]) for c in out.call_args_list)

    def test_it_goes_to_the_background_and_says_the_terminal_can_close(self):
        with mock.patch.object(agent_service, "install", return_value="launchd") as install:
            execve, said = self.main()
        execve.assert_not_called()
        self.assertEqual(install.call_args.args[1:4], ("newbie1", "codex", AGENT))
        self.assertIn("you can close this Terminal", said)

    def test_no_service_manager_or_foreground_runs_it_here(self):
        for result, argv in ((None, ()), ("launchd", ("--foreground",))):
            with mock.patch.object(agent_service, "install", return_value=result):
                execve, _ = self.main(*argv)
            execve.assert_called_once()
        with mock.patch.object(agent_service, "install", side_effect=RuntimeError("refused")):
            execve, _ = self.main()
        execve.assert_called_once()                            # said plainly, then here

    def test_stop(self):
        with mock.patch.object(agent_service, "stop", return_value=True) as stop:
            execve, said = self.main("--stop")
        stop.assert_called_once_with(AGENT)
        execve.assert_not_called()
        self.assertIn("removed its background service", said)


class Offline(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp()) / "agents.json"
        path.write_text(json.dumps({"newbie1": {"agent": "remote"}, "anna": {"agent": "hermes"}}))
        p = mock.patch.object(registry, "PATH", path)
        p.start()
        self.addCleanup(p.stop)
        self.lp = Loop.__new__(Loop)
        self.lp.user_id, self.lp._seen, self.said, self.buttons = PM, {}, [], []
        self.presence = "online"

        async def presence(user_id):
            return PresenceGetResponse(user_id, self.presence, None, None, None)

        async def say(room_id, body, *a, **k):
            self.said.append((room_id, body))
            return "$off"

        async def thumb(room_id, event_id, key="👍"):
            self.buttons.append(key)
        self.lp.client = mock.Mock(get_presence=mock.AsyncMock(side_effect=presence))
        self.lp.say, self.lp.offer_thumb = say, thumb
        self.lp.private_room = mock.AsyncMock(return_value="!dm")

    def at(self, t):
        self.lp._checked = 0
        asyncio.run(self.lp.check_agents(now=t))

    def test_told_once_after_five_minutes_away_with_a_button(self):
        self.at(1000)                                          # online
        self.presence = "offline"
        self.at(1060)
        self.assertEqual(self.said, [])                        # just left: maybe a restart
        self.at(1060 + pm_presence.OFFLINE_S)
        self.assertEqual(len(self.said), 1)
        self.assertIn("is offline", self.said[0][1])
        self.assertIn("run the install line again", self.said[0][1])
        self.assertEqual(self.buttons, ["🔁"])
        self.at(5000)
        self.assertEqual(len(self.said), 1)                    # once
        self.assertFalse(self.lp.agent_online(AGENT))
        self.lp.client.get_presence.assert_awaited_with(f"@{AGENT}:{S}")   # only remote agents are watched

    def test_never_connected_is_not_nagged_and_a_day_between_messages(self):
        self.presence = "offline"
        self.at(1000)
        self.at(1000 + 2 * pm_presence.OFFLINE_S)
        self.assertEqual(self.said, [])
        self.assertIsNone(self.lp.agent_online("nobody-agent"))
        self.presence = "online"
        self.at(3000)
        self.presence = "offline"
        self.at(3001)
        self.at(3001 + pm_presence.OFFLINE_S)
        self.presence = "online"
        self.at(4000)
        self.presence = "offline"
        self.at(4001)
        self.at(4001 + pm_presence.OFFLINE_S)                  # away again, same day: not again
        self.assertEqual(len(self.said), 1)
        self.at(4001 + pm_presence.ALERT_EVERY_S + 1)
        self.assertEqual(len(self.said), 2)

    def test_a_network_blip_never_stops_the_tick(self):
        self.lp.client.get_presence = mock.AsyncMock(side_effect=asyncio.TimeoutError())
        self.at(1000)
        self.assertEqual(self.said, [])

    def test_the_button_sends_a_fresh_line_only_for_the_owner(self):
        self.lp._seen = {AGENT: {"owner": NEW, "offer": "$off", "dm": "!dm"}}
        self.lp.choose = mock.AsyncMock()
        click = lambda who, key="🔁", room="!dm": asyncio.run(self.lp.on_offline_reaction(
            SimpleNamespace(room_id=room), SimpleNamespace(sender=who, key=key, reacts_to="$off")))
        self.assertFalse(click(f"@anna:{S}"))
        self.assertFalse(click(NEW, room="!elsewhere"))
        self.assertTrue(click(NEW))
        self.lp.choose.assert_awaited_once_with("!dm", NEW, "🔌")


class Disconnect(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp()) / "agents.json"
        path.write_text(json.dumps({"newbie1": {"agent": "remote"}}))
        mock.patch.object(registry, "PATH", path).start()
        self.env = {"PW_NEWBIE1_AGENT": "old"}
        mock.patch.object(remote_agent, "load_env", side_effect=lambda: dict(self.env)).start()
        self.saved = mock.patch.object(setup_accounts, "save_env", side_effect=lambda e: self.env.update(e)).start()
        remote_agent.CODES.codes = {}
        self.addCleanup(mock.patch.stopall)

    def test_a_new_password_everywhere_and_old_codes_die_first(self):
        code = remote_agent.CODES.issue(NEW)
        lp = Loop.__new__(Loop)
        lp.say = mock.AsyncMock()

        def reset(*a):
            self.assertEqual(remote_agent.CODES.codes, {})     # GPT review 23: revoked before the new password
            return {}
        with mock.patch.object(remote_agent.local_signup, "admin_call", side_effect=reset) as call:
            self.assertTrue(asyncio.run(lp.disconnect("!dm", NEW)))
        self.assertNotEqual(self.env["PW_NEWBIE1_AGENT"], "old")
        self.assertEqual(call.call_args.args[2]["new_password"], self.env["PW_NEWBIE1_AGENT"])
        self.assertTrue(call.call_args.args[2]["logout_devices"])
        self.assertIsNone(remote_agent.CODES.redeem(code))

    def test_if_synapse_refuses_or_cant_be_asked_the_old_password_stays(self):
        for answer in (mock.Mock(return_value={"_status": 403, "errcode": "M_FORBIDDEN"}),
                       mock.Mock(side_effect=SystemExit("login failed for admin-bot"))):
            with mock.patch.object(remote_agent.local_signup, "admin_call", answer):
                with self.assertRaises(setup_accounts.AccountError):
                    remote_agent.disconnect(NEW)
            self.assertEqual(self.env["PW_NEWBIE1_AGENT"], "old")

    def test_from_chat_only_for_their_own_remote_agent(self):
        lp = Loop.__new__(Loop)
        said = []

        async def say(room_id, body, *a, **k):
            said.append(body)
        lp.say = say
        with mock.patch.object(remote_agent, "disconnect") as d:
            self.assertFalse(asyncio.run(lp.disconnect("!dm", f"@anna:{S}")))
            asyncio.run(lp.disconnect("!dm", NEW))
        d.assert_called_once_with(NEW)
        self.assertIn("no agent connected", said[0])
        self.assertIn("Disconnected", said[1])
        self.assertTrue(pm_presence.DISCONNECT_RE.search("@pm disconnect my agent".removeprefix("@pm ")))


if __name__ == "__main__":
    unittest.main()
