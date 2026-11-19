"""Unit tests for agent CLI argv/state handling. Run: .venv/bin/python -m unittest test_agents"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agents


class Argv(unittest.TestCase):
    def test_codex_is_read_only_and_reads_prompt_from_stdin(self):
        argv = agents._codex_argv(Path("/w"), None)
        self.assertIn('sandbox_mode="read-only"', argv)
        self.assertEqual(argv[-1], "-")

    def test_claude_limits_tools_and_mcp(self):
        argv = agents._claude_argv(Path("/w"), None)
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Grep,Glob")
        self.assertIn("--strict-mcp-config", argv)

    def test_malformed_saved_session_id_is_not_passed_to_cli(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".bridge-session.json").write_text(json.dumps({"codex": "--dangerously-x"}))
            seen = {}

            async def fake_exec(*argv, **kw):
                seen["argv"] = argv
                proc = mock.Mock(returncode=0)
                out = b'{"type":"thread.started","thread_id":"0123abcd-0000"}\n' \
                      b'{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}\n'
                proc.communicate = mock.AsyncMock(return_value=(out, b""))
                return proc

            with mock.patch("asyncio.create_subprocess_exec", fake_exec):
                self.assertEqual(asyncio.run(agents.run_turn("codex", Path(d), "x")), "hi")
            self.assertNotIn("--dangerously-x", seen["argv"])
            self.assertNotIn("resume", seen["argv"])
            saved = json.loads((Path(d) / ".bridge-session.json").read_text())
            self.assertEqual(saved["codex"], "0123abcd-0000")


if __name__ == "__main__":
    unittest.main()
