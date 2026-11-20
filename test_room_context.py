"""Unit tests: what agents know before answering. Run: .venv/bin/python -m unittest test_room_context"""
import asyncio
import base64
import os
import unittest
from unittest import mock

os.environ.setdefault("HAGENCY_AGENT", "anna-agent")
import github  # noqa: E402
import mcp_context  # noqa: E402
import room_context  # noqa: E402


def fake_api(events, status=None):
    def api(method, path, body=None, token=None):
        if github.STATUS_EVENT in path:
            return status or {"_status": 404}
        return {"chunk": events}
    return mock.patch.object(room_context, "api", api)


def msg(sender, body, **extra):
    return {"type": "m.room.message", "sender": f"@{sender}:x", "content": {"body": body, **extra}}


class Context(unittest.TestCase):
    def test_conversation_is_everyones_oldest_first_without_board_edits(self):
        events = [msg("pm", "🎉 All done: #1 merged"), msg("pm", "* 📋 Project status …", **{"m.new_content": {}}),
                  msg("betty", "can we add Swahili?"), msg("anna", "hello")]          # newest first, as Matrix returns
        with fake_api(events):
            text = room_context.conversation("t", "!r")
        self.assertEqual(text.splitlines(), ["anna: hello", "betty: can we add Swahili?", "pm: 🎉 All done: #1 merged"])

    def test_status_only_for_this_repo(self):
        with fake_api([], {"repo": "someone/else", "text": "x"}):
            self.assertEqual(room_context.status("t", "!r"), "")
        with fake_api([], {"repo": github.REPO, "text": "Done: #1"}):
            self.assertEqual(room_context.status("t", "!r"), "Done: #1")

    def test_the_prompt_block_says_to_trust_the_live_status(self):
        block = room_context.describe({"status": "Done: #1, #2", "conversation": "anna: hi"})
        self.assertIn("trust it over your memory", block)
        self.assertIn("Done: #1, #2", block)


class ProjectFiles(unittest.TestCase):
    def test_paths_outside_the_repo_are_refused(self):
        for bad in ("../secrets", "/etc/passwd"):
            self.assertIn("error", asyncio.run(mcp_context.read_project_file(bad)), bad)

    def test_reads_a_file_and_lists_a_folder(self):
        async def run_json(args, agent=None):
            if args[1].endswith("/contents"):
                return [{"name": "index.html", "type": "file"}, {"name": "docs", "type": "dir"}]
            return {"content": base64.b64encode(b"<h1>Hello</h1>").decode()}
        with mock.patch.object(github, "run_json", run_json):
            self.assertEqual(asyncio.run(mcp_context.read_project_file(""))["entries"], ["index.html", "docs/"])
            self.assertEqual(asyncio.run(mcp_context.read_project_file("index.html"))["content"], "<h1>Hello</h1>")


if __name__ == "__main__":
    unittest.main()
