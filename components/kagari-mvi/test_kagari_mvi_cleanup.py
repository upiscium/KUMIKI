"""Tests for Ready after a passing review and for `cleanup-once` (#432), with a fake gh/git runner."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
from kagari_mvi_testkit import REVIEW_BODY, FakeRunner, issue, stream  # noqa: E402

PASSING = "AC1: PASS — greet.py:1\nAC2: PASS — test_greet.py:3\nTESTS: PASS — test_greet checks AC1"
READY = {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}}
REPORT = mvi.TEST_REPORT_PREFIX + "\n- tested commit: `%s`\n- exit code: %d\n"


class ReadyTest(unittest.TestCase):
    def review(self, text=PASSING, report=REPORT % ("abc123", 0), ready=READY):
        implemented = issue(7, [mvi.APPROVED, mvi.IMPLEMENTED])
        implemented["body"] = REVIEW_BODY
        runner = FakeRunner({
            ("GET", "repos/o/r/issues?"): [implemented],
            ("GET", "repos/o/r/pulls?"): [{"number": 70}],
            ("DIFF", "repos/o/r/pulls/70"): "+def greet(): ...\n",
            ("GET", "repos/o/r/pulls/70"): {"number": 70, "node_id": "PR_n70", "head": {"sha": "abc123"}},
            ("GET", "user"): {"login": "runner"},
            ("GET", "repos/o/r/issues/70/comments"): [{"user": {"login": "runner"}, "body": report}],
            ("POST", "graphql"): ready,
        })
        runner.outputs["stream-json"] = stream(text)
        with tempfile.TemporaryDirectory() as workdir:
            code = mvi.Worker("o/r", run=runner, log=lambda _: None).review_once(workdir)
        marks = [args for args, _, _ in runner.calls if "markPullRequestReadyForReview" in " ".join(args)]
        comments = [body["body"] for body in runner.api_calls("POST", "repos/o/r/issues/70/comments")]
        return code, marks, comments, runner

    def test_all_pass_with_a_passing_test_of_this_head_marks_ready(self):
        code, marks, comments, runner = self.review()
        self.assertEqual(code, 0)
        self.assertEqual(len(marks), 1)
        self.assertIn("id=PR_n70", marks[0])
        self.assertIn("ready for review: yes", comments[0])
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.REVIEWED])

    def test_anything_short_of_all_pass_stays_draft_and_says_why(self):
        for kwargs, reason in (
                ({"text": PASSING.replace("AC2: PASS", "AC2: FAIL")}, "not every criterion is PASS"),
                ({"text": PASSING.replace("AC2: PASS — test_greet.py:3\n", "")}, "not every criterion"),
                ({"text": PASSING.replace("TESTS: PASS", "TESTS: UNKNOWN")}, "TESTS is UNKNOWN"),
                ({"text": PASSING.rsplit("\n", 1)[0]}, "TESTS is UNKNOWN"),
                ({"report": REPORT % ("abc123", 1)}, "no passing runner test of this head"),
                ({"report": REPORT % ("old999", 0)}, "no passing runner test of this head"),
                # Test output and Claude's declaration are free text inside code blocks.
                ({"report": REPORT % ("abc123", 1) + "\n```\n- exit code: 0\n```\n"}, "no passing runner"),
                ({"report": mvi.TEST_REPORT_PREFIX + "\n\n```\n- tested commit: `abc123`\n- exit code: 0\n```\n"},
                 "no passing runner")):
            with self.subTest(reason=reason):
                code, marks, comments, runner = self.review(**kwargs)
                self.assertEqual((code, marks), (0, []))
                self.assertIn("the PR stays Draft (", comments[0])
                self.assertIn(reason, comments[0])
                self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.REVIEWED])

    def test_a_reviewer_error_alone_keeps_draft(self):
        verdicts = [("PASS", "a"), ("PASS", "b")]
        ready, note = mvi.ready_decision({"verdicts": verdicts, "tests": ("PASS", "x"), "errored": True},
                                         True, REPORT % ("abc123", 0))
        self.assertFalse(ready)
        self.assertIn("ended with an error", note)

    def test_a_failed_ready_mutation_still_posts_the_verdicts_then_blocks(self):
        still_draft = {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": True}}}}
        code, marks, comments, runner = self.review(ready=still_draft)
        self.assertEqual((code, len(marks)), (1, 1))
        self.assertIn("ready for review: failed", comments[0])
        self.assertIn("| AC1 |", comments[0])
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        blocked = [body["body"] for body in runner.api_calls("POST", "repos/o/r/issues/7/comments")]
        self.assertIn("`ready`", blocked[0])


def merged(sha):
    return [{"number": 70, "state": "closed", "merged_at": "2026-11-18T00:00:00Z", "head": {"sha": sha}}]


class CleanupTest(unittest.TestCase):
    def cleanup(self, pulls, remote=("refs/heads/kagari/issue-7", "m7"), local="m7", dirs=None, fail=None):
        routes = {("GET", "repos/o/r/pulls?state=all&head=o%%3Akagari%%2Fissue-%d&" % n): pulls.get(n, [])
                  for n in (7, 8, 9)}
        refs = [{"ref": remote[0], "object": {"sha": remote[1]}}] if remote else []
        routes[("GET", "repos/o/r/git/matching-refs/heads/kagari/issue-7")] = refs
        runner = FakeRunner(routes, fail=fail)
        runner.outputs = {"branch --list": "kagari/issue-7\nkagari/issue-8\n",
                          "refs/heads/kagari/issue-7": (local or "") + "\n"}
        workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workdir, True)
        wt = os.path.join(workdir, "wt", "o__r")
        os.makedirs(wt)
        for name in ("issue-7", "issue-7.verify", "issue-7.review", "issue-8", "issue-9.review", "notes")\
                if dirs is None else dirs:
            os.makedirs(os.path.join(wt, name))
        os.makedirs(os.path.join(workdir, "repos", "o__r", ".git"))
        code = mvi.Worker("o/r", run=runner, log=lambda _: None).cleanup_once(workdir)
        joined = [" ".join(args) for args, _, _ in runner.calls]
        deleted = [args[6] for args, _, _ in runner.calls if args[:2] == ["gh", "api"] and args[3] == "DELETE"]
        return code, sorted(os.listdir(wt)), joined, deleted

    def test_only_merged_prs_lose_their_branches_and_directories(self):
        code, left, joined, deleted = self.cleanup({
            7: merged("m7"), 8: [{"number": 80, "state": "open"}], 9: [{"number": 90, "state": "closed"}]})
        self.assertEqual(code, 0)
        self.assertEqual(left, ["issue-8", "issue-9.review", "notes"])
        self.assertEqual([j for j in joined if "branch -D" in j][0].split()[-1], "kagari/issue-7")
        self.assertEqual(len([j for j in joined if "branch -D" in j]), 1)
        self.assertEqual(deleted, ["repos/o/r/git/refs/heads/kagari/issue-7"])

    def test_kept_when_open_or_newer_than_the_merged_head(self):
        for kwargs in ({"pulls": {7: merged("m7") + [{"number": 71, "state": "open"}]}},
                       {"pulls": {7: merged("m7")}, "local": "new1"},  # e.g. an unpushed blocked commit
                       {"pulls": {7: merged("m7")}, "remote": ("refs/heads/kagari/issue-7", "new2")}):
            with self.subTest(kwargs=kwargs):
                code, left, joined, deleted = self.cleanup(**kwargs)
                self.assertEqual((code, deleted), (0, []))
                self.assertIn("issue-7", left)
                self.assertFalse(any("branch -D" in j for j in joined))

    def test_remote_branch_gone_or_only_a_prefix_match_is_not_deleted(self):
        for remote in (None, ("refs/heads/kagari/issue-70", "other")):
            code, left, joined, deleted = self.cleanup({7: merged("m7")}, remote=remote)
            self.assertEqual((code, deleted), (0, []))
            self.assertNotIn("issue-7", left)

    def test_a_branch_left_by_a_failed_run_is_retried_without_its_directories(self):
        code, left, joined, deleted = self.cleanup({7: merged("m7")}, dirs=())
        self.assertEqual(code, 0)
        self.assertTrue(any(j.endswith("branch -D kagari/issue-7") for j in joined))

    def test_one_failure_is_reported_and_the_rest_continue(self):
        for fail in ({"branch -D": "error: cannot delete"}, {"head=o%3Akagari%2Fissue-7": "HTTP 502"}):
            with self.subTest(fail=fail):
                code, left, joined, _ = self.cleanup({7: merged("m7"), 8: merged("m8")}, fail=fail)
                self.assertEqual(code, 1)
                self.assertTrue(any("matching-refs/heads/kagari/issue-8" in j for j in joined))


if __name__ == "__main__":
    unittest.main()
