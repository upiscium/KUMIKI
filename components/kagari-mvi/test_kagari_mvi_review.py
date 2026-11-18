"""Tests for `review-once` with gh/git replaced by a fake runner."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
import kagari_mvi_exec as ex  # noqa: E402
from kagari_mvi_testkit import REVIEW_BODY, issue, stream, FakeRunner  # noqa: E402


class ReviewOnceTest(unittest.TestCase):
    def routes(self, comments=None, body=REVIEW_BODY):
        implemented = issue(7, [mvi.APPROVED, mvi.IMPLEMENTED])
        implemented["body"] = body
        return {
            ("GET", "repos/o/r/issues?"): [implemented, issue(8, [mvi.APPROVED, mvi.IMPLEMENTED,
                                                                 mvi.REVIEWED])],
            ("GET", "repos/o/r/pulls?"): [{"number": 70}],
            ("DIFF", "repos/o/r/pulls/70"): "diff --git a/greet.py b/greet.py\n+def greet(): ...\n",
            ("GET", "repos/o/r/pulls/70"): {"number": 70, "head": {"sha": "abc123"}},
            ("GET", "user"): {"login": "runner"},
            ("GET", "repos/o/r/issues/70/comments"): comments if comments is not None else [
                {"user": {"login": "runner"},
                 "body": mvi.TEST_REPORT_PREFIX + "\n- tested commit: `abc123`\n- exit code: 0"},
                {"user": {"login": "outsider"}, "body": mvi.TEST_REPORT_PREFIX + " FAKE PASS"}],
        }

    def run_review(self, runner):
        with tempfile.TemporaryDirectory() as workdir:
            return mvi.Worker("o/r", run=runner, log=lambda _: None).review_once(workdir)

    def run_tsumgi(self, runner):
        with tempfile.TemporaryDirectory() as workdir:
            return mvi.Worker("o/r", run=runner, log=lambda _: None, reviewer="tsumgi").review_once(workdir)

    def test_leftover_review_dir_is_reopened_as_agent_and_deleted(self):
        runner = FakeRunner(self.routes())
        with tempfile.TemporaryDirectory() as workdir:
            review_dir = os.path.join(workdir, "wt", "o__r", "issue-7.review")
            os.makedirs(os.path.join(review_dir, ".npm-cache"))
            mvi.Worker("o/r", run=runner, log=lambda _: None).review_once(workdir)
            self.assertFalse(os.path.exists(review_dir))
        joined = [" ".join(args) for args, _, _ in runner.calls]
        self.assertIn("sudo -n -u kagari-agent -H -- find %s -user kagari-agent -type d "
                      "-exec chmod u+rwx,g+rwx {} ;" % review_dir, joined)
        self.assertFalse(any("worktree remove" in j for j in joined))

    def test_leftover_review_dir_that_cannot_be_reopened_blocks(self):
        runner = FakeRunner(self.routes(), fail={"-exec chmod u+rwx,g+rwx": "find: 'x': Permission denied"})
        with tempfile.TemporaryDirectory() as workdir:
            os.makedirs(os.path.join(workdir, "wt", "o__r", "issue-7.review"))
            self.assertEqual(mvi.Worker("o/r", run=runner, log=lambda _: None).review_once(workdir), 1)
        self.assertIn(mvi.BLOCKED, runner.added_labels())
        self.assertNotIn(mvi.REVIEWED, runner.added_labels())
        body = runner.api_calls("POST", "repos/o/r/issues/7/comments")[0]["body"]
        self.assertIn("`checkout`", body)
        self.assertIn("Permission denied", body)
        self.assertFalse(any("stream-json" in args for args, _, _ in runner.calls))

    def test_tsumgi_review_posts_one_request_and_table(self):
        reply = json.dumps({"choices": [{"message": {
            "content": "<think>x</think>AC1: PASS — greet.py:1\nAC2: UNKNOWN — no test shown\n"
                       "TESTS: PASS — test_greet matches AC1"}}]})
        runner = FakeRunner(self.routes(), outputs={"chat/completions": reply})
        self.assertEqual(self.run_tsumgi(runner), 0)
        self.assertFalse(any("stream-json" in args for args, _, _ in runner.calls))
        curl = [(args, inp) for args, _, inp in runner.calls if args[0] == "curl"]
        self.assertEqual(len(curl), 1)
        self.assertEqual(curl[0][0][-1], ex.TSUMGI_URL + "/chat/completions")
        body = json.loads(curl[0][1])
        self.assertEqual(body["model"], ex.TSUMGI_MODEL)
        self.assertIn("You cannot read files.", body["messages"][0]["content"])
        self.assertIn("+def greet()", body["messages"][1]["content"])
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("TSUMGI `%s`" % ex.TSUMGI_MODEL, report)
        self.assertIn("| AC1 | greet() returns Hello | **PASS** | greet.py:1 |", report)
        self.assertIn("| TESTS | テストの目的（申告）と受入条件の対応 | **PASS** | test_greet matches AC1 |", report)
        self.assertIn("TESTS: PASS|FAIL|UNKNOWN", body["messages"][0]["content"])
        self.assertIn("reviewer session: ok", report)
        self.assertIn(mvi.REVIEWED, runner.added_labels())

    def test_tsumgi_large_pr_is_not_sent_and_unknown(self):
        routes = self.routes()
        routes[("DIFF", "repos/o/r/pulls/70")] = "+" + "x" * ex.TSUMGI_MAX_BYTES
        runner = FakeRunner(routes)
        self.assertEqual(self.run_tsumgi(runner), 0)
        self.assertFalse(any(args[0] == "curl" for args, _, _ in runner.calls))
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("too large for TSUMGI", report)
        self.assertIn("not PASS: 2 of 2", report)
        self.assertIn("reviewer session: ended with an error", report)

    def test_tsumgi_http_error_is_reported_not_blocked(self):
        runner = FakeRunner(self.routes(), fail={"chat/completions": '{"code":"context_length_exceeded"}'})
        self.assertEqual(self.run_tsumgi(runner), 0)
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("reviewer session: ended with an error", report)
        self.assertIn("not PASS: 2 of 2", report)
        self.assertIn("context_length_exceeded", report)  # TSUMGI's error body, not only curl's
        self.assertIn(mvi.REVIEWED, runner.added_labels())

    def test_tsumgi_cut_off_or_unclosed_reasoning_is_not_a_verdict(self):
        for choice in ({"finish_reason": "length", "message": {"content": "AC1: PASS — x"}},
                       {"finish_reason": "stop", "message": {"content": "<think>AC1: PASS — draft"}}):
            runner = FakeRunner(self.routes(), outputs={"chat/completions": json.dumps({"choices": [choice]})})
            self.assertEqual(self.run_tsumgi(runner), 0)
            report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
            self.assertIn("not PASS: 2 of 2", report)

    def test_review_posts_table_and_marks_reviewed(self):
        runner = FakeRunner(self.routes(), outputs={
            "stream-json": stream("AC1: PASS — greet.py:1\nAC2: FAIL — no test file")})
        self.assertEqual(self.run_review(runner), 0)
        claude = next(args for args, _, _ in runner.calls if "stream-json" in args)
        self.assertEqual(claude[claude.index("--tools") + 1], "Read,Grep,Glob")
        settings = json.loads(next(a for a in claude if a.startswith("--settings="))[11:])
        for tool in ("Bash", "Edit", "Write", "WebFetch"):
            self.assertIn(tool, settings["permissions"]["deny"])
        prompt = next(inp for args, _, inp in runner.calls if "stream-json" in args)
        self.assertIn("AC1: greet() returns Hello", prompt)
        self.assertIn("+def greet()", prompt)
        self.assertIn("exit code: 0", prompt)
        self.assertNotIn("FAKE PASS", prompt)  # only the runner's own test comment is used
        self.assertNotIn("nothing else", prompt)  # non-goals are not criteria
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("reviewed head: `abc123`", report)
        self.assertIn("| AC2 | tests added | **FAIL** | no test file |", report)
        self.assertIn("| TESTS | テストの目的（申告）と受入条件の対応 | **UNKNOWN** | (no TESTS verdict", report)
        self.assertIn("TESTS: PASS|FAIL|UNKNOWN", next(a for a in claude if "independent reviewer" in a))
        self.assertIn("not PASS: 1 of 2", report)
        self.assertIn("runner test result: for this head", report)
        self.assertNotIn("WARNING", prompt)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.REVIEWED])
        self.assertEqual(runner.removed_labels(), ["kagari%3Arunning"])
        add = next(args for args, _, _ in runner.calls if "--detach" in args)
        self.assertEqual(add[-1], "abc123")

    def test_missing_criteria_blocks(self):
        runner = FakeRunner(self.routes(body="no criteria here"))
        self.assertEqual(self.run_review(runner), 1)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        self.assertFalse(any("stream-json" in args for args, _, _ in runner.calls))

    def test_no_runner_test_comment_is_stated(self):
        runner = FakeRunner(self.routes(comments=[]), outputs={"stream-json": stream("AC1: UNKNOWN — x")})
        self.assertEqual(self.run_review(runner), 0)
        prompt = next(inp for args, _, inp in runner.calls if "stream-json" in args)
        self.assertIn("no runner test result", prompt)
        self.assertIn("WARNING", prompt)
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("missing or for another commit", report)

    def test_stale_test_result_and_errored_reviewer_are_reported(self):
        stale = [{"user": {"login": "runner"},
                  "body": mvi.TEST_REPORT_PREFIX + "\n- tested commit: `old999`\n- exit code: 0"}]
        runner = FakeRunner(self.routes(comments=stale),
                            outputs={"stream-json": stream("AC1: PASS — a", is_error=True)})
        self.assertEqual(self.run_review(runner), 0)
        prompt = next(inp for args, _, inp in runner.calls if "stream-json" in args)
        self.assertIn("WARNING", prompt)
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("missing or for another commit", report)
        self.assertIn("reviewer session: ended with an error", report)

    def test_nothing_to_review(self):
        runner = FakeRunner({("GET", "repos/o/r/issues?"): [issue(8, [mvi.APPROVED, mvi.IMPLEMENTED,
                                                                         mvi.REVIEWED])]})
        self.assertEqual(self.run_review(runner), 0)
        self.assertEqual(len(runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
