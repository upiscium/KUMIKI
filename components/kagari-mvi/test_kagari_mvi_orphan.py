"""Tests for the orphaned remote branch check in prepare_worktree (#551)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
from kagari_mvi_orphan import orphan_branch  # noqa: E402
from kagari_mvi_testkit import FakeRunner  # noqa: E402
from test_kagari_mvi_run import RunOnceTest  # noqa: E402

REF = "for-each-ref"
SUBJECT = "kagari-mvi: implement #7"


def outputs(subjects=None):
    if subjects is None:
        return {}
    return {REF: "refs/remotes/origin/kagari/issue-7\n", "log --format": "\n".join(subjects)}


class OrphanBranchTest(unittest.TestCase):
    def classify(self, subjects):
        return orphan_branch(FakeRunner({}, outputs=outputs(subjects)), "c", 7, "main")

    def test_classification(self):
        self.assertEqual(self.classify(None), "none")
        self.assertEqual(self.classify([SUBJECT]), "runner")
        self.assertEqual(self.classify(["other"]), "unknown")
        self.assertEqual(self.classify([SUBJECT, SUBJECT]), "unknown")
        self.assertEqual(self.classify(["human", SUBJECT]), "unknown")


class PrepareWorktreeOrphanTest(unittest.TestCase):
    def prepare(self, runner):
        with tempfile.TemporaryDirectory() as tmp:
            worker = mvi.Worker("o/r", run=runner, log=lambda _: None)
            return worker.prepare_worktree(tmp, 7, "main")

    def deletes(self, runner):
        return [a for a, _, _ in runner.calls if "--delete" in a]

    def test_no_remote_branch_deletes_nothing(self):
        runner = FakeRunner({})
        self.prepare(runner)
        self.assertEqual(self.deletes(runner), [])

    def test_runner_branch_is_deleted_once_and_work_continues(self):
        runner = FakeRunner({}, outputs=outputs([SUBJECT]))
        self.prepare(runner)
        self.assertEqual(len(self.deletes(runner)), 1)
        self.assertEqual(self.deletes(runner)[0][-4:], ["push", "origin", "--delete", "kagari/issue-7"])
        self.assertTrue(any("worktree" in a and "add" in a for a, _, _ in runner.calls))

    def test_unknown_branch_blocks_without_deleting(self):
        for subjects in (["other"], [SUBJECT, SUBJECT]):
            runner = FakeRunner({}, outputs=outputs(subjects))
            with self.assertRaises(mvi.Blocked) as caught:
                self.prepare(runner)
            self.assertIn("did not make", str(caught.exception))
            self.assertEqual(self.deletes(runner), [])
            self.assertFalse(any("add" in a and "worktree" in a for a, _, _ in runner.calls))

    def test_failed_delete_propagates_and_stops(self):
        runner = FakeRunner({}, outputs=outputs([SUBJECT]), fail={"--delete": "GitHub error"})
        with self.assertRaises(mvi.CommandError):
            self.prepare(runner)
        self.assertFalse(any("add" in a and "worktree" in a for a, _, _ in runner.calls))


class RunOnceOrphanTest(RunOnceTest):
    def test_unknown_branch_blocks_the_run(self):
        runner = FakeRunner(self.routes(), outputs=outputs(["other"]))
        self.assertEqual(self.run_worker(runner), 1)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])

    def test_existing_pr_skips_the_orphan_check(self):
        runner = FakeRunner(self.routes({"number": 70, "html_url": "x", "head": {"sha": "s"}}),
                            outputs=outputs([SUBJECT]))
        self.run_worker(runner)
        self.assertFalse(any(REF in " ".join(a) or "--delete" in a for a, _, _ in runner.calls))


for _name in [n for n in dir(RunOnceTest) if n.startswith("test_")]:
    setattr(RunOnceOrphanTest, _name, None)  # only run the tests defined above
del RunOnceTest

if __name__ == "__main__":
    unittest.main()
