"""Unit tests for github_setup.evaluate. Run: .venv/bin/python -m unittest test_github_setup"""
import unittest

from github_setup import evaluate

GATED = [{"type": "pull_request", "parameters": {"required_approving_review_count": 1}}]
OK = {"anna-agent": {"login": "a", "push": True}, "betty-agent": {"login": "b", "push": True}}


class Evaluate(unittest.TestCase):
    def test_ready(self):
        self.assertEqual(evaluate(OK, GATED), [])

    def test_missing_ruleset(self):
        self.assertIn("approving review", evaluate(OK, [])[0])

    def test_zero_approvals_is_not_a_gate(self):
        rules = [{"type": "pull_request", "parameters": {"required_approving_review_count": 0}}]
        self.assertEqual(len(evaluate(OK, rules)), 1)

    def test_not_signed_in(self):
        p = evaluate({**OK, "betty-agent": {"error": "no identity"}}, GATED)
        self.assertIn("betty-agent: not signed in", p[0])

    def test_no_write(self):
        p = evaluate({**OK, "betty-agent": {"login": "b", "push": False}}, GATED)
        self.assertIn("collaborator", p[0])

    def test_shared_account(self):
        p = evaluate({"anna-agent": {"login": "a", "push": True}, "betty-agent": {"login": "a", "push": True}}, GATED)
        self.assertIn("share a GitHub account", p[0])


if __name__ == "__main__":
    unittest.main()
