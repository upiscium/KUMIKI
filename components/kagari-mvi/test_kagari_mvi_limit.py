"""Tests for the usage-limit (HTTP 429) deferral (#481), with a fake gh/git runner."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
import kagari_mvi_exec as ex  # noqa: E402
from kagari_mvi_testkit import REVIEW_BODY, FakeRunner, edited, issue, labeled, stream  # noqa: E402

RESETS = 1794989400  # 2026-11-18 08:10 UTC, as Claude Code reports it
WHEN = "2026-11-18 08:10 UTC"


def limit_stream(resets=RESETS):
    """What `claude -p` printed on 2026-11-18 when the five-hour limit was hit."""
    error = {"type": "assistant", "error": "rate_limit", "api_error": "usage_limit_reached",
             "api_error_status": 429,
             "api_error_params": {"rate_limit_info": {"status": "rejected", "resetsAt": resets,
                                                      "rateLimitType": "five_hour"}}}
    result = {"type": "result", "is_error": True, "api_error": "usage_limit_reached",
              "api_error_status": 429, "result": "You've hit your 5-hour limit · resets 8:10am (UTC)",
              "permission_denials": []}
    return "\n".join(json.dumps(event) for event in (error, result))


class OutcomeTest(unittest.TestCase):
    def test_a_usage_limit_is_told_apart_from_auth_and_other_errors(self):
        limited = ex.agent_outcome(limit_stream(), True)
        self.assertEqual((limited["limited"], limited["auth"], limited["resets"]), (True, False, WHEN))
        no_time = ex.agent_outcome(limit_stream(resets=None), True)
        self.assertIn("5-hour limit", no_time["resets"])  # falls back to Claude's own words
        for text, is_error in (("Not logged in · Please run /login", True), ("boom", True),
                               ("status: COMPLETED", False)):
            self.assertFalse(ex.agent_outcome(stream(text, is_error=is_error), False)["limited"], text)
        # A limit seen earlier, then another failure at the end: the end decides, so it still blocks.
        retried = limit_stream().split("\n")[0] + "\n" + stream("ran out of turns", is_error=True)
        self.assertFalse(ex.agent_outcome(retried, False)["limited"])
        self.assertFalse(ex.agent_outcome(limit_stream().split("\n")[0], True)["limited"])  # no result at all
        # The words alone are not enough: only the API's own error counts ...
        self.assertFalse(ex.agent_outcome(stream("hit the 5-hour usage limit", is_error=True), False)["limited"])
        # ... and only in a run that actually errored.
        quiet = json.dumps({"type": "result", "is_error": False, "api_error": "usage_limit_reached",
                            "result": "status: COMPLETED"})
        self.assertFalse(ex.agent_outcome(quiet, False)["limited"])


class RunOnceLimitTest(unittest.TestCase):
    def routes(self, comments=()):
        return {
            ("GET", "repos/o/r/issues?"): [issue(7, [mvi.APPROVED])],
            ("GET", "repos/o/r/issues/7/timeline"): [labeled("upiscium")],
            ("GET", "repos/o/r/issues/7/comments"): list(comments),
            ("GET", "repos/o/r/pulls?"): [],
            ("GET", "repos/o/r/labels"): [],
            ("POST", "repos/o/r/pulls"): {"number": 70, "html_url": "https://x/pull/70"},
            ("GET", "repos/o/r"): {"default_branch": "main"},
            ("POST", "graphql"): edited(None),
        }

    def run_once(self, output, comments=()):
        runner = FakeRunner(self.routes(comments))
        runner.outputs["stream-json"] = output
        with tempfile.TemporaryDirectory() as workdir:
            code = mvi.Worker("o/r", run=runner, log=lambda _: None).run_once(["upiscium"], workdir)
        posted = [body["body"] for body in runner.api_calls("POST", "repos/o/r/issues/7/comments")]
        return code, runner, posted

    def test_a_usage_limit_puts_the_issue_back_instead_of_blocking_it(self):
        code, runner, posted = self.run_once(limit_stream())
        self.assertEqual(code, 3)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING])  # no kagari:blocked
        self.assertEqual(runner.removed_labels(), ["kagari%3Arunning"])
        self.assertEqual(len(posted), 1)
        self.assertIn(WHEN, posted[0])
        self.assertIn("not blocked", posted[0])
        self.assertEqual(runner.api_calls("POST", "repos/o/r/pulls"), [])
        self.assertFalse(any("push" in args for args, _, _ in runner.calls))

    def test_the_same_limit_is_commented_only_once(self):
        _, _, posted = self.run_once(limit_stream())
        code, runner, again = self.run_once(limit_stream(), comments=[{"body": posted[0]}])
        self.assertEqual((code, again), (3, []))  # still put back, but no second comment
        self.assertEqual(runner.removed_labels(), ["kagari%3Arunning"])
        _, _, later = self.run_once(limit_stream(resets=RESETS + 18000), comments=[{"body": posted[0]}])
        self.assertEqual(len(later), 1)  # a new reset time is news

    def test_a_failing_comment_still_releases_the_lock(self):
        runner = FakeRunner(self.routes(), fail={"issues/7/comments": "HTTP 502"})
        runner.outputs["stream-json"] = limit_stream()
        with tempfile.TemporaryDirectory() as workdir:
            code = mvi.Worker("o/r", run=runner, log=lambda _: None).run_once(["upiscium"], workdir)
        self.assertEqual((code, runner.removed_labels()), (3, ["kagari%3Arunning"]))

    def test_an_auth_error_still_blocks(self):
        code, runner, posted = self.run_once(stream("Not logged in · Please run /login", is_error=True))
        self.assertEqual(code, 1)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        self.assertIn("再ログインが必要", posted[0])


class ReviewOnceLimitTest(unittest.TestCase):
    def test_a_usage_limit_leaves_the_issue_implemented_and_unreviewed(self):
        implemented = issue(7, [mvi.APPROVED, mvi.IMPLEMENTED])
        implemented["body"] = REVIEW_BODY
        runner = FakeRunner({
            ("GET", "repos/o/r/issues?"): [implemented],
            ("GET", "repos/o/r/issues/7/comments"): [],
            ("GET", "repos/o/r/pulls?"): [{"number": 70}],
            ("DIFF", "repos/o/r/pulls/70"): "+def greet(): ...\n",
            ("GET", "repos/o/r/pulls/70"): {"number": 70, "node_id": "n", "head": {"sha": "abc123"}},
            ("GET", "user"): {"login": "runner"},
            ("GET", "repos/o/r/issues/70/comments"): [{
                "user": {"login": "runner"},
                "body": mvi.TEST_REPORT_PREFIX + "\n- tested commit: `abc123`\n- test command: `t`\n- exit code: 0\n"}],
        })
        runner.outputs["stream-json"] = limit_stream()
        with tempfile.TemporaryDirectory() as workdir:
            code = mvi.Worker("o/r", run=runner, log=lambda _: None).review_once(workdir)
        self.assertEqual(code, 3)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING])  # neither reviewed nor blocked
        self.assertEqual(runner.removed_labels(), ["kagari%3Arunning"])
        self.assertEqual(runner.api_calls("POST", "repos/o/r/issues/70/comments"), [])  # no verdict table
        self.assertEqual(len(runner.api_calls("POST", "repos/o/r/issues/7/comments")), 1)


if __name__ == "__main__":
    unittest.main()
