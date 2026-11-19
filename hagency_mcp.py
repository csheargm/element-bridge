"""hagency MCP server: lets any MCP-capable agent (Hermes, Claude Code, Codex...) take part in a
Hagency room's GitHub loop without a bridge. The agent does the thinking and the file edits;
this server does the rest, the same way the Codex/Claude bridge does (tasks.py):
  - reads the task pm addressed to this agent (only pm, only this agent);
  - clones/commits/pushes/opens PRs/reviews/merges with the agent's OWN GitHub identity
    (.gh/<agent>/), so the agent itself never holds a GitHub token or runs git;
  - posts the result event pm waits for, as the agent's Matrix account.

Run by the agent as a stdio MCP server, e.g. in a Hermes profile's config.yaml:
  mcp_servers: {hagency: {command: .../.venv/bin/python, args: [.../hagency_mcp.py],
                env: {HAGENCY_AGENT: anna-hermes-agent, HAGENCY_ROOM: "!room:server"}}}
"""
from mcp_build import (finish_build, get_review, merge_pr, read_file, start_build,
                       submit_review, write_files)
from mcp_core import mcp, next_task, report_failure
from mcp_plan import get_plan_request, open_issues, submit_plan

# Importing the tool modules registers their tools on `mcp`; re-exported for tests and tooling.
__all__ = ["finish_build", "get_plan_request", "get_review", "mcp", "merge_pr", "next_task", "open_issues",
           "read_file", "report_failure", "start_build", "submit_plan", "submit_review", "write_files"]

if __name__ == "__main__":
    mcp.run()
