"""Tests for `work-once` (#535): run-once then review-once, repeated, with a direct GET."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
from kagari_mvi_batch import work_once  # noqa: E402
from test_kagari_mvi_review import ReviewOnceTest  # noqa: E402
from kagari_mvi_testkit import REVIEW_BODY, issue, FakeRunner  # noqa: E402


class StubWorker:
    """run_once takes the next number from `approved`; `outcomes` maps a number to a code."""

    def __init__(self, approved, run_codes=None, review_codes=None):
        self.approved, self.calls, self.selected, self.implemented = list(approved), [], None, None
        self.done = set()
        self.run_codes, self.review_codes = run_codes or {}, review_codes or {}

    def log(self, _):
        pass

    def run_once(self, allowed, workdir):
        self.selected = self.implemented = None
        if not self.approved:
            self.calls.append("run none")
            return 0
        number = self.selected = self.approved.pop(0)
        code = self.run_codes.get(number, 0)
        self.calls.append("run %d" % number)
        if number in self.done:  # like the real run_once: the direct GET shows it is no longer a candidate
            return 0
        self.done.add(number)
        if code == 0:
            self.implemented = number
        return code

    def review_once(self, workdir, also=None):
        self.calls.append("review %s" % also)
        return self.review_codes.get(also, 0)


class WorkOnceTest(unittest.TestCase):
    def work(self, worker, maximum=3):
        return work_once(worker, ["a"], "/w", maximum), worker.calls

    @staticmethod
    def gets(runner):  # direct GETs of Issue 7 (not the list, not #70's comments)
        return sum(1 for args, _, _ in runner.calls if args[:2] == ["gh", "api"] and args[6] == "repos/o/r/issues/7")

    def test_review_reads_the_issue_run_once_just_implemented_although_the_search_misses_it(self):
        routes = ReviewOnceTest().routes()
        routes[("GET", "repos/o/r/issues?")] = []  # the label search lags: nothing is found
        routes[("GET", "repos/o/r/issues/7")] = issue(7, [mvi.APPROVED, mvi.IMPLEMENTED])
        routes[("GET", "repos/o/r/issues/7")]["body"] = REVIEW_BODY
        runner = FakeRunner(routes)
        with tempfile.TemporaryDirectory() as workdir:
            code = mvi.Worker("o/r", run=runner, log=lambda _: None).review_once(workdir, also=7)
        self.assertEqual(code, 0)
        self.assertIn(mvi.REVIEWED, runner.added_labels())
        self.assertEqual(self.gets(runner), 1)

    def test_a_hinted_issue_that_is_also_in_the_search_is_not_fetched_again(self):
        routes = ReviewOnceTest().routes()
        runner = FakeRunner(routes)
        with tempfile.TemporaryDirectory() as workdir:
            mvi.Worker("o/r", run=runner, log=lambda _: None).review_once(workdir, also=7)
        self.assertEqual(self.gets(runner), 0)

    def test_two_approved_issues_are_processed_in_order_in_one_run(self):
        code, calls = self.work(StubWorker([1, 2]))
        self.assertEqual((code, calls[:4]), (0, ["run 1", "review 1", "run 2", "review 2"]))
        self.assertEqual(calls[4:], ["run none", "review None"])

    def test_the_limit_leaves_the_rest_for_the_next_run(self):
        worker = StubWorker([1, 2, 3, 4])
        code, calls = self.work(worker, 3)
        self.assertEqual((code, calls), (0, ["run 1", "review 1", "run 2", "review 2", "run 3", "review 3"]))
        self.assertEqual(worker.approved, [4])

    def test_no_approved_issue_runs_each_once_and_stops(self):
        self.assertEqual(self.work(StubWorker([])), (0, ["run none", "review None"]))

    def test_blocked_stops_the_loop_after_the_review(self):
        code, calls = self.work(StubWorker([1, 2], run_codes={1: 1}))
        self.assertEqual((code, calls), (1, ["run 1", "review None"]))

    def test_usage_limit_stops_the_loop_and_is_not_a_failure(self):
        code, calls = self.work(StubWorker([1, 2], run_codes={1: 3}))
        self.assertEqual((code, calls), (3, ["run 1", "review None"]))
        code, calls = self.work(StubWorker([1, 2], review_codes={1: 3}))
        self.assertEqual((code, calls), (3, ["run 1", "review 1"]))

    def test_the_same_issue_is_not_handled_twice_in_one_run(self):
        worker = StubWorker([5, 5, 6])
        code, calls = self.work(worker)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["run 5", "review 5", "run 5"])  # the 2nd run_once starts nothing
        self.assertEqual(worker.done, {5})
        self.assertEqual(worker.approved, [6])


if __name__ == "__main__":
    unittest.main()
