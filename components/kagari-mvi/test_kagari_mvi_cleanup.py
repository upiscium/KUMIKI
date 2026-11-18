"""Tests for Ready after a passing review and for `cleanup-once` (#432), with a fake gh/git runner."""
import json
import os
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
                ({"report": REPORT % ("old999", 0)}, "no passing runner test of this head")):
            with self.subTest(reason=reason):
                code, marks, comments, runner = self.review(**kwargs)
                self.assertEqual((code, marks), (0, []))
                self.assertIn("the PR stays Draft (", comments[0])
                self.assertIn(reason, comments[0])
                self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.REVIEWED])

    def test_a_failed_ready_mutation_blocks_at_stage_ready(self):
        still_draft = {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": True}}}}
        code, marks, comments, runner = self.review(ready=still_draft)
        self.assertEqual((code, len(marks)), (1, 1))
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        blocked = [body["body"] for body in runner.api_calls("POST", "repos/o/r/issues/7/comments")]
        self.assertIn("`ready`", blocked[0])


class CleanupTest(unittest.TestCase):
    def cleanup(self, pulls, ref=True, fail=None):
        runner = FakeRunner({
            ("GET", "repos/o/r/pulls?state=all&head=o%3Akagari%2Fissue-7&"): pulls.get(7, []),
            ("GET", "repos/o/r/pulls?state=all&head=o%3Akagari%2Fissue-8&"): pulls.get(8, []),
            ("GET", "repos/o/r/pulls?state=all&head=o%3Akagari%2Fissue-9&"): pulls.get(9, []),
            ("GET", "repos/o/r/git/matching-refs/heads/kagari/issue-7"): [{"ref": "x"}] if ref else [],
        }, fail=fail)
        runner.outputs["branch --list kagari/issue-7"] = "  kagari/issue-7\n"
        workdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(workdir, ignore_errors=True))
        wt = os.path.join(workdir, "wt", "o__r")
        for name in ("issue-7", "issue-7.verify", "issue-7.review", "issue-8", "issue-9.review", "notes"):
            os.makedirs(os.path.join(wt, name))
        os.makedirs(os.path.join(workdir, "repos", "o__r", ".git"))
        code = mvi.Worker("o/r", run=runner, log=lambda _: None).cleanup_once(workdir)
        return code, sorted(os.listdir(wt)), runner

    def test_only_merged_prs_lose_their_branches_and_directories(self):
        merged = [{"number": 70, "state": "closed", "merged_at": "2026-11-18T00:00:00Z"}]
        code, left, runner = self.cleanup({7: merged, 8: [{"number": 80, "state": "open", "merged_at": None}],
                                           9: [{"number": 90, "state": "closed", "merged_at": None}]})
        self.assertEqual(code, 0)
        self.assertEqual(left, ["issue-8", "issue-9.review", "notes"])
        joined = [" ".join(args) for args, _, _ in runner.calls]
        self.assertTrue(any(j.endswith("branch -D kagari/issue-7") for j in joined))
        self.assertFalse(any("issue-8" in j or "issue-9" in j for j in joined if "branch -D" in j))
        deleted = [args[6] for args, _, _ in runner.calls if args[:2] == ["gh", "api"] and args[3] == "DELETE"]
        self.assertEqual(deleted, ["repos/o/r/git/refs/heads/kagari/issue-7"])

    def test_a_merged_pr_with_a_new_open_one_is_kept(self):
        both = [{"number": 70, "state": "closed", "merged_at": "x"}, {"number": 71, "state": "open"}]
        code, left, _ = self.cleanup({7: both})
        self.assertEqual(code, 0)
        self.assertIn("issue-7", left)

    def test_remote_branch_already_deleted_by_github_is_not_deleted_again(self):
        code, left, runner = self.cleanup({7: [{"state": "closed", "merged_at": "x"}]}, ref=False)
        self.assertEqual(code, 0)
        self.assertNotIn("issue-7", left)
        self.assertEqual(runner.removed_labels(), [])  # no DELETE call at all

    def test_one_failure_is_reported_and_the_rest_continue(self):
        code, left, _ = self.cleanup({7: [{"state": "closed", "merged_at": "x"}]},
                                     fail={"branch -D": "error: cannot delete"})
        self.assertEqual(code, 1)
        self.assertIn("issue-8", left)


if __name__ == "__main__":
    unittest.main()
