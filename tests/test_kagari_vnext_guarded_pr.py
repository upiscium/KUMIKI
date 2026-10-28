"""#291 Draft PR continuation must use already existing #194 guarded facade.

Fake GitHub side effects only; no GitHub or real source writes. Main checks
the exact approved #191 Task before it hands control to the #194 facade,
which owns its own prerequisites, frozen write intent and separate approval.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests import test_task_collaboration_v4 as collaboration_fixture
from tools.kagari_vnext_main import (
    ApprovedTask, MainError, RepositoryMain)
from tools import kagari_vnext_main_service as service


class GuardedMainPRTests(unittest.TestCase):
    def setUp(self):
        self.fixture=collaboration_fixture.TaskCollaborationV4Test("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.approved={
            194:ApprovedTask(
                194,"refs/heads/issue-194",self.fixture.base_revision,
                self.fixture.publication.contract_id)
        }
        self.title="Task 194 requirement"
        self.body="Initial requirements for the task."
        self.main=RepositoryMain(
            self.fixture.store,
            approved=self.approved,
            read_issue=lambda n:{
                "repository":"acme/widgets","number":n,"state":"OPEN",
                "title":self.title,"body":self.body},
            runtime_home=self.fixture.temp_root/"other-host-state",
            port_for_issue=lambda _:47657,
            opencode_bin="opencode",
        )

    def guarded_pr(self, issue: int, facade: object):
        # Only the valid MainSupervisor path grants Main-owned continuation;
        # the #194 facade independently requires its own explicit Task grant.
        with patch.object(service,"_host_state_home",
                          return_value=self.fixture.temp_root):
            with service.MainSupervisor(self.main):
                return self.main.guarded_draft_pr(issue,facade)

    def test_direct_guarded_pr_requires_repo_main_owner_before_gh_effect(self):
        self.assertFalse(self.fixture.deny_execution)
        with self.assertRaises(MainError) as denied:
            self.main.guarded_draft_pr(194,self.fixture.facade)
        self.assertEqual(denied.exception.code,"MAIN_OWNER_REQUIRED")
        self.assertEqual(self.fixture.github.pulls,[])
        self.assertEqual(self.fixture.github.write_calls,[])
        self.assertEqual(self.fixture.intents,[])

    def test_second_clone_owner_does_not_authorize_direct_pr(self):
        other_root=self.fixture.temp_root/"other-host-clone-for-pr"
        subprocess.run(["git","init","-q",str(other_root)],check=True)
        other=SimpleNamespace(store=SimpleNamespace(
            root=other_root,repository="acme/widgets"),approved={})
        with patch.object(service,"_host_state_home",
                          return_value=self.fixture.temp_root):
            with service.MainSupervisor(other):
                with self.assertRaises(MainError) as denied:
                    self.main.guarded_draft_pr(194,self.fixture.facade)
                self.assertEqual(denied.exception.code,"MAIN_OWNER_REQUIRED")
            # After the other Main releases, exact owner can make the guarded
            # PR; no extra per-job Human Ask, and still no Source Publication.
            with service.MainSupervisor(self.main):
                receipt=self.main.guarded_draft_pr(194,self.fixture.facade)
        self.assertEqual(receipt.pull_request,91)
        self.assertEqual(len(self.fixture.github.pulls),1)

    def test_existing_collaboration_facade_retains_explicit_write_grant(self):
        self.fixture.deny_execution=True
        with self.assertRaises(MainError) as denied:
            self.guarded_pr(194,self.fixture.facade)
        self.assertEqual(denied.exception.code,"GUARDED_PR_PUBLICATION_REFUSED")
        self.assertEqual(self.fixture.github.write_calls,[])
        self.assertEqual(self.fixture.intents,[])
        self.fixture.deny_execution=False
        created=self.guarded_pr(194,self.fixture.facade)
        self.assertEqual(created.pull_request,91)
        self.assertEqual(len(self.fixture.github.pulls),1)
        self.assertTrue(self.fixture.authorization_requests)
        self.assertTrue(self.fixture.intents)
        before=len(self.fixture.github.write_calls)
        repeated=self.guarded_pr(194,self.fixture.facade)
        self.assertEqual(repeated.pull_request,91)
        self.assertEqual(before,len(self.fixture.github.write_calls))
        self.assertEqual(len(self.fixture.github.pulls),1)

    def test_foreign_facade_never_gets_pr_write(self):
        with self.assertRaises(MainError) as error:
            self.guarded_pr(194,object())
        self.assertEqual(error.exception.code,"GUARDED_PR_BINDING_MISMATCH")
        self.assertEqual(self.fixture.github.write_calls,[])

    def test_changed_issue_specification_blocks_before_pr_side_effect(self):
        self.body="Unsanctioned scope change"
        with self.assertRaises(MainError) as err:
            self.guarded_pr(194,self.fixture.facade)
        self.assertEqual(err.exception.code,"ISSUE_SNAPSHOT_DRIFT")
        self.assertEqual(self.fixture.github.write_calls,[])

    def test_unapproved_issue_never_reaches_guarded_pr_facade(self):
        with self.assertRaises(MainError) as err:
            self.guarded_pr(195,self.fixture.facade)
        self.assertEqual(err.exception.code,"ISSUE_NOT_APPROVED")
        self.assertEqual(self.fixture.github.write_calls,[])


if __name__=="__main__":
    unittest.main()
