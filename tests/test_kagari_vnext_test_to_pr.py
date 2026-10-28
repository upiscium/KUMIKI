"""One frozen Task -> measured Evidence -> #194 guarded Draft PR, local only.

Combines the existing #194 FakeGitHub/authorized collaboration fixture with
real #191 metadata, a real linked Git Worktree, and actual test subprocesses.
No production PR or remote GitHub write is performed.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests import test_kagari_vnext_guarded_pr as prior
from tests import test_metadata_ref as git_fixture
from tools.kagari_vnext_main import MainError
from tools import kagari_vnext_main_service as service
from tools.kagari_vnext_test_evidence import TaskTestEvidenceRunner
from metadata_ref import MetadataStore


class BoundedTestToDraftPRTests(unittest.TestCase):
    def setUp(self):
        self.base=prior.GuardedMainPRTests("runTest")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.main=self.base.main
        self.facade=self.base.fixture.facade
        self.github=self.base.fixture.github
        project=self.base.fixture.product
        # The #194 fixture has the Task branch as the primary checkout.
        # Move primary to a disjoint main branch, then create a *real* Issue
        # linked Worktree that can be independently measured by #144.
        git_fixture.git("switch","-q","-c","main",cwd=project)
        (project/".gitignore").write_text("/.worktrees/\n")
        git_fixture.git("add",".gitignore",cwd=project)
        git_fixture.git("commit","-q","-m","ignore Issue Worktree",cwd=project)
        self.worktree=project/".worktrees"/"194"
        self.worktree.parent.mkdir()
        git_fixture.git("worktree","add","-q",str(self.worktree),
                        "issue-194",cwd=project)
        self.runner=TaskTestEvidenceRunner(self.main)
        self.count=self.base.fixture.temp_root/"test-commands-count.txt"

    def guarded_after_pass(self, issue: int, *, run_id: str, collaboration: object):
        # Task-local tests can run without Main, but resulting PR writes may
        # run only under a valid canonical-repository Main owner.
        with patch.object(service, "_host_state_home",
                          return_value=self.base.fixture.temp_root):
            with service.MainSupervisor(self.main):
                return self.runner.guarded_draft_after_pass(
                    issue, run_id=run_id, collaboration=collaboration)

    def test_pass_evidence_cannot_issue_direct_ownerless_pr(self):
        evidence = self.runner.run(194, run_id="ownerless1", argv=self.command())
        self.assertEqual(evidence.result, "PASS")
        with self.assertRaises(MainError) as error:
            self.runner.guarded_draft_after_pass(
                194, run_id="ownerless1", collaboration=self.facade)
        self.assertEqual(error.exception.code, "MAIN_OWNER_REQUIRED")
        self.assertEqual(self.github.pulls, [])
        self.assertEqual(self.github.write_calls, [])
        receipt = self.guarded_after_pass(
            194, run_id="ownerless1", collaboration=self.facade)
        self.assertEqual(receipt.pull_request, 91)
        self.assertEqual(len(self.github.pulls), 1)

    def command(self, *, exit_code=0):
        program=(
            "from pathlib import Path; import sys; "
            f"p=Path({str(self.count)!r});"
            "p.write_text(p.read_text()+'x' if p.exists() else 'x');"
            "print('validated Task requirements');"
            f"sys.exit({exit_code})"
        )
        return (sys.executable,"-B","-c",program)

    def test_pass_then_separate_pr_write_approval_then_exact_adoption(self):
        evidence=self.runner.run(194,run_id="checked01",argv=self.command())
        self.assertEqual(evidence.result,"PASS")
        self.assertEqual(self.count.read_text(),"x")
        self.base.fixture.deny_execution=True
        with self.assertRaises(MainError) as blocked:
            self.guarded_after_pass(
                194,run_id="checked01",collaboration=self.facade)
        self.assertEqual(blocked.exception.code,"GUARDED_PR_PUBLICATION_REFUSED")
        self.assertEqual(self.github.write_calls,[])
        self.base.fixture.deny_execution=False
        new=self.guarded_after_pass(
            194,run_id="checked01",collaboration=self.facade)
        self.assertEqual(new.pull_request,91)
        self.assertEqual(len(self.github.pulls),1)
        writes=len(self.github.write_calls)
        repeated=self.guarded_after_pass(
            194,run_id="checked01",collaboration=self.facade)
        self.assertEqual(repeated.pull_request,91)
        self.assertEqual(writes,len(self.github.write_calls))
        self.assertEqual(self.count.read_text(),"x")
        self.assertTrue(self.base.fixture.authorization_requests)
        self.assertTrue(self.base.fixture.intents)

    def test_worktree_scoped_existing_collaboration_facade_is_accepted(self):
        receipt=self.runner.run(
            194,run_id="scoped1",argv=self.command())
        self.assertEqual(receipt.result,"PASS")
        scoped_store=MetadataStore(
            self.worktree, "acme/widgets", self.base.fixture.store.remote)
        fake_git=prior.collaboration_fixture.FakeTaskGit(
            scoped_store,self.base.fixture.base_revision)
        upstream=self.base.fixture

        def authorize(request):
            upstream.authorization_requests.append(request)
            return not upstream.deny_execution

        def record_intent(intent):
            upstream.intents.append(intent)
            return True

        def content_policy(request):
            upstream.content_requests.append(request)
            return not upstream.deny_content

        scoped_facade=prior.collaboration_fixture.collaboration.TaskCollaboration(
            scoped_store,fake_git,upstream.github,
            prerequisites_by_operation=upstream.engines,
            authorize=authorize,record_intent=record_intent,
            content_policy=content_policy)
        created=self.guarded_after_pass(
            194,run_id="scoped1",collaboration=scoped_facade)
        self.assertEqual(created.pull_request,91)
        self.assertEqual(len(self.github.pulls),1)
        self.assertEqual(self.count.read_text(),"x")
        repeated=self.guarded_after_pass(
            194,run_id="scoped1",collaboration=scoped_facade)
        self.assertEqual(repeated.pull_request,91)
        self.assertEqual(len(self.github.pulls),1)

    def test_failing_test_cannot_create_draft(self):
        failed=self.runner.run(
            194,run_id="failing1",argv=self.command(exit_code=2))
        self.assertEqual(failed.result,"FAIL")
        with self.assertRaises(MainError) as error:
            self.guarded_after_pass(
                194,run_id="failing1",collaboration=self.facade)
        self.assertEqual(error.exception.code,"TEST_RESULT_NOT_PASS")
        self.assertEqual(self.github.write_calls,[])

    def test_absent_confirmed_evidence_blocks_pr(self):
        with self.assertRaises(MainError) as error:
            self.guarded_after_pass(
                194,run_id="missing1",collaboration=self.facade)
        self.assertEqual(error.exception.code,"TEST_EVIDENCE_NOT_CONFIRMED")
        self.assertEqual(self.github.write_calls,[])

    def test_head_advanced_after_pass_requires_new_test_before_pr(self):
        first=self.runner.run(194,run_id="oldhead1",argv=self.command())
        (self.worktree/"new-code.txt").write_text("new candidate source")
        git_fixture.git("add","new-code.txt",cwd=self.worktree)
        git_fixture.git("commit","-q","-m","advance Issue Task",cwd=self.worktree)
        with self.assertRaises(MainError) as error:
            self.guarded_after_pass(
                194,run_id="oldhead1",collaboration=self.facade)
        self.assertEqual(error.exception.code,"TEST_SUBJECT_CHANGED")
        self.assertEqual(self.github.write_calls,[])


if __name__=="__main__":
    unittest.main()
