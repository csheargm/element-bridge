"""ADR 0006: the owner's agent runs their own CLI as they configured it; everything else stays locked.
Run: .venv/bin/python -m unittest test_owner_powers"""
import asyncio
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import agents
import bridge
import workdirs

CHAT = "--dangerously-skip-permissions; rm -rf ~ $(whoami)"     # chat text that must never reach argv
LOCKDOWN_CLAUDE = ("--tools", "--strict-mcp-config", "--setting-sources", "--permission-mode")
OWNER, MEMBER = "@anna:bridge.localhost", "@betty:bridge.localhost"


def codex_home(toml=""):
    d = Path(tempfile.mkdtemp())
    (d / "config.toml").write_text(toml)
    return mock.patch.dict("os.environ", {"CODEX_HOME": str(d)})


class OwnerArgv(unittest.TestCase):
    def test_claude_owner_turn_adds_no_lockdown(self):
        argv = agents._claude_argv(Path("/w"), None, owner=True)
        self.assertEqual(argv, ["claude", "-p", "--output-format", "json"])
        for flag in LOCKDOWN_CLAUDE:
            self.assertNotIn(flag, argv)

    def test_claude_owner_turn_resumes_its_session(self):
        self.assertEqual(agents._claude_argv(Path("/w"), "abcdef12-x", owner=True)[-2:], ["--resume", "abcdef12-x"])

    def test_codex_owner_turn_uses_the_owners_config_toml(self):
        with codex_home('[mcp_servers.node_repl]\ncommand="x"\n'):
            for sid in (None, "abcdef12-x"):
                argv = agents._codex_argv(Path("/w"), sid, owner=True)
                self.assertNotIn("-c", argv)          # no sandbox/approval/features/web/MCP overrides
                self.assertEqual(argv[-1], "-")       # prompt on stdin
            self.assertEqual(agents._codex_argv(Path("/w"), None, owner=True)[-3:], ["-C", "/w", "-"])

    def test_codex_owner_turn_does_not_need_switchable_mcp_names(self):
        with codex_home('[mcp_servers."a b"]\ncommand="x"\n'):     # locked turns refuse this name
            self.assertNotIn("-c", agents._codex_argv(Path("/w"), None, owner=True))

    def test_locked_turns_are_unchanged(self):
        claude = agents._claude_argv(Path("/w"), None)
        self.assertEqual(claude[claude.index("--tools") + 1], "Read,Grep,Glob")
        self.assertEqual(claude[claude.index("--permission-mode") + 1], "default")
        build = agents._claude_argv(Path("/w"), None, write=True)
        self.assertEqual(build[build.index("--tools") + 1], "Read,Grep,Glob,Edit,Write")
        with codex_home():
            self.assertIn('sandbox_mode="read-only"', agents._codex_argv(Path("/w"), None))
            self.assertIn('sandbox_mode="workspace-write"', agents._codex_argv(Path("/w"), None, write=True))
            self.assertIn('approval_policy="never"', agents._codex_argv(Path("/w"), None))


def fake_cli(stdout: bytes, seen: dict):
    async def fake_exec(*argv, **kw):
        seen.update(argv=argv, cwd=kw.get("cwd"))
        proc = mock.Mock(returncode=0)

        async def communicate(data):
            seen["stdin"] = data
            return stdout, b""
        proc.communicate = communicate
        return proc
    return mock.patch("asyncio.create_subprocess_exec", fake_exec)


class OwnerTurn(unittest.TestCase):
    def run_claude(self, payload: dict, owner=True):
        seen, d = {}, Path(tempfile.mkdtemp())
        with fake_cli(json.dumps(payload).encode(), seen):
            text = asyncio.run(agents.run_turn("claude", d, CHAT, owner=owner))
        return text, seen, d

    def test_prompt_on_stdin_never_in_argv_and_cwd_is_the_work_folder(self):
        text, seen, d = self.run_claude({"result": "hi", "session_id": "abcdef12-1"})
        self.assertEqual(text, "hi")
        self.assertEqual(seen["stdin"], CHAT.encode())
        self.assertFalse([a for a in seen["argv"] if CHAT in a or "rm -rf" in a])
        self.assertEqual(seen["cwd"], d)

    def test_denied_tools_are_said_plainly_by_name_only(self):
        denials = [{"tool_name": "WebSearch", "tool_input": {"query": "ghp_secretsecret"}},
                   {"tool_name": "Bash", "tool_input": {"command": "cat ~/.ssh/id_rsa"}},
                   {"tool_name": "WebSearch"}, {"tool_name": "bad name;rm"}, "junk"]
        text, _, _ = self.run_claude({"result": "I couldn't search.", "session_id": "abcdef12-1",
                                      "permission_denials": denials})
        self.assertTrue(text.startswith("I couldn't search."))
        self.assertIn("Your Claude Code permission settings didn't allow: WebSearch, Bash (", text)
        self.assertIn('"permissions": {"allow": [...]}', text)
        for leaked in ("ghp_", "id_rsa", "bad name"):
            self.assertNotIn(leaked, text)

    def test_only_denials_is_a_reply_not_an_empty_failure(self):
        text, _, _ = self.run_claude({"result": "", "permission_denials": [{"tool_name": "WebFetch"}]})
        self.assertIn("WebFetch", text)

    def test_empty_reply_is_still_a_failure(self):
        with self.assertRaises(agents.AgentError):
            self.run_claude({"result": "  "})


class BridgeAnswer(unittest.TestCase):
    """Only the owner's own words get the owner-configured CLI; replies are scrubbed of secrets."""
    def answer(self, sender, reply="ok"):
        b = bridge.Bridge("anna", "claude", allow_members=True)
        b.client.access_token = "syt_bridge_own_token_value"
        calls, sent = [], []

        async def run_turn(backend, workdir, prompt, **kw):
            calls.append(SimpleNamespace(workdir=workdir, prompt=prompt, **kw))
            return reply

        async def send(room_id, body, reply_to=None, **kw):
            sent.append(body)
        b.send, b.keep_typing = send, mock.AsyncMock()
        b.client.room_typing = mock.AsyncMock()
        work = Path(tempfile.mkdtemp())
        with mock.patch.object(bridge.agents, "run_turn", run_turn), \
                mock.patch.object(bridge.room_context, "snapshot", lambda *a: {"status": "", "conversation": ""}), \
                mock.patch.object(workdirs, "WORK_HOME", work):
            room = SimpleNamespace(room_id="!r:x", display_name="Lounge")
            asyncio.run(b.answer(room, SimpleNamespace(sender=sender), f"x ({sender}): hi"))
        return calls[0], sent[0], work

    def test_owner_message_runs_the_owners_cli_outside_the_app(self):
        call, _, work = self.answer(OWNER)
        self.assertTrue(call.owner)
        self.assertEqual(call.workdir.parent, work / "anna")       # ~/.hagency/work/<owner>/<room>
        self.assertNotIn(Path(bridge.ROOT).resolve(), call.workdir.resolve().parents)

    def test_a_members_message_under_allow_members_stays_locked(self):
        call, _, _ = self.answer(MEMBER)
        self.assertFalse(call.owner)

    def test_reply_never_carries_secrets(self):
        _, sent, _ = self.answer(OWNER, reply="token syt_bridge_own_token_value key "
                                              "sk-ant-api03-abcdefghijklmnopqrstuv gh ghp_abcdefgh123")
        for secret in ("syt_bridge_own_token_value", "sk-ant-api03", "ghp_abcdefgh123"):
            self.assertNotIn(secret, sent)
        self.assertEqual(sent.count("<redacted>"), 3)


class SecretSafe(unittest.TestCase):
    def test_a_whole_private_key_is_cut_not_just_its_header(self):
        from redact import secret_safe
        pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----"
        self.assertEqual(secret_safe(f"here: {pem} done"), "here: <redacted> done")
        self.assertEqual(secret_safe("plain words, sk-short, AKIA"), "plain words, sk-short, AKIA")


class LockedCallers(unittest.TestCase):
    def test_task_brain_and_code_change_turns_never_ask_for_owner_powers(self):
        """Loop builds/reviews/plans (tasks.py), @pm's brain and code changes run on untrusted text."""
        for name in ("tasks.py", "pm_brain.py", "bridge_custom.py"):
            src = (Path(__file__).parent / name).read_text()
            self.assertIn("run_turn(", src, name)
            self.assertNotIn("owner=", src, name)

    def test_task_folders_are_unchanged(self):
        self.assertIn('ROOT / "rooms" / self.owner / "work"', inspect.getsource(bridge.Bridge.run_task))


if __name__ == "__main__":
    unittest.main()
