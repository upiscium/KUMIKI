"""r7: initial #194 collaboration is required before first model Turn.

The existing #194 suite tests real establish/Push/PR/Evidence semantics. These
scoped Main fixture tests exercise the binding, no-grant and retry boundary
using the real #191/#286/#163 code and a controlled #194 transport double.
"""
from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_kagari_vnext_main as fixtures
from tests import test_metadata_ref as git_fixture
from tools.kagari_vnext_main import (
    ApprovedTask, InitialCollaboration, MainError, RepositoryMain)
from metadata_ref import MetadataStore
from task_collaboration import (
    TaskCollaboration, TaskCollaborationError, CollaborationReceipt)
from task_view import (
    GitHubObservation, GitHubPullRequestFacts, PreviousCheckpoint)


class MainPRFirstTests(unittest.TestCase):
    def setUp(self):
        self.source=fixtures.MainOrchestratorTest("runTest")
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.source.initial_fixture_patch.stop()
        self.callbacks=[]
        self.fault=None
        self.refuse_checkpoint=False
        self.changing_issue=False
        self.model=self.new_main()

    def new_main(self, callback=True) -> RepositoryMain:
        return RepositoryMain(
            self.source.store,approved=self.source.approved,
            read_issue=lambda n:self.source.facts[n].copy(),
            runtime_home=self.source.runtime,
            port_for_issue=lambda n:self.source.ports[n],
            opencode_bin=str(self.source.bin),
            initial_collaboration_for_issue=self.install if callback else None)

    def install(self, issue: int, worktree: Path) -> InitialCollaboration:
        self.callbacks.append((issue,worktree))
        fake=object.__new__(TaskCollaboration)
        fake._store=MetadataStore(worktree,self.source.store.repository,self.source.store.remote)
        fake._repository=self.source.store.repository
        fake._task=str(issue)
        fake._branch_ref=self.source.branch_ref
        fake._base_revision=self.source.base
        self.current_facade=fake
        return InitialCollaboration(
            fake, lambda *_: self.fail("metadata publisher is #194 facade-owned"),
            "Initial exact scoped Issue checkpoint.")

    def evidence(self, facade: TaskCollaboration, *args):
        if self.fault is not None:
            raise TaskCollaborationError(self.fault)
        if self.changing_issue:
            self.source.facts[291]["body"]="unapproved change during PR publication"
        subject=git_fixture.git("rev-parse","HEAD",
                               cwd=self.source.project/".worktrees"/"291").decode().strip()
        receipt=CollaborationReceipt(
            repository="acme/widgets",task="291",
            branch_ref=self.source.branch_ref,subject=subject,
            pull_request=91,comment_id=201,
            metadata_commit=self.source.pub.metadata_commit,
            record_id=self.source.pub.record_id,
            contract_id=self.source.pub.contract_id,snapshot_id="f"*64)
        self.receipt=receipt
        return receipt

    def observation(self, facade: TaskCollaboration, _request):
        r=self.receipt
        checkpoint=None if self.refuse_checkpoint else PreviousCheckpoint(
            str(r.comment_id),r.subject,r.metadata_commit,r.snapshot_id)
        return GitHubObservation(
            GitHubPullRequestFacts(
                "acme/widgets","acme/widgets",r.pull_request,
                "open",True,"main","issue-291",r.subject),
            checkpoint)

    def execute(self, main: RepositoryMain | None = None):
        from tools import kagari_vnext_main_service as service
        with patch.object(TaskCollaboration,"establish",side_effect=lambda *a:self.evidence(None,*a)), \
             patch.object(TaskCollaboration,"github_observation",side_effect=lambda request:self.observation(None,request)), \
             patch.object(service, "_host_state_home",
                          return_value=self.source.fixture.temp_root):
            owner=main or self.model
            with service.MainSupervisor(owner):
                return owner.dispatch(291)

    def test_internal_collaboration_establish_without_main_owner_cannot_write_pr(self):
        # A trusted integration must not bypass singleton ownership by
        # invoking the otherwise-internal PR-first establishment directly.
        choice=self.source.approved[291]
        wt=self.source.project / ".worktrees" / "291"
        with self.assertRaises(MainError) as denied:
            self.model._establish_initial_collaboration(choice,wt)
        self.assertEqual(denied.exception.code,"MAIN_OWNER_REQUIRED")
        self.assertEqual(self.callbacks,[])
        self.assertFalse(wt.exists())
        self.assertFalse(self.source.runtime.exists())
        self.assertEqual(self.source.prompts(),[])

    def test_no_installed_collaboration_denies_without_worktree_or_model(self):
        without=self.new_main(callback=False)
        with self.assertRaises(MainError) as err:
            self.execute(without)
        self.assertEqual(err.exception.code,"MAIN_INITIAL_COLLABORATION_REQUIRED")
        self.assertFalse((self.source.project/".worktrees").exists())
        self.assertEqual(self.source.prompts(),[])

    def test_checked_initial_checkpoint_allows_one_turn_not_second(self):
        first=self.execute()
        self.assertEqual(first["prompt_state"],"SUBMITTED")
        self.assertEqual(len(self.source.prompts()),1)
        marker=self.source.runtime/"issue-291"/"initial-collaboration.json"
        self.assertTrue(marker.exists())
        again=self.execute()
        self.assertEqual(again["prompt_state"],"ALREADY_DISPATCHED")
        self.assertEqual(len(self.callbacks),1)
        self.assertEqual(len(self.source.prompts()),1)

    def test_pr_checkpoint_refusal_cannot_start_model(self):
        self.refuse_checkpoint=True
        with self.assertRaises(MainError) as err:
            self.execute()
        self.assertEqual(err.exception.code,"MAIN_INITIAL_COLLABORATION_REFUSED")
        self.assertEqual(self.source.prompts(),[])
        self.assertFalse((self.source.runtime/"issue-291"/"prompt.pending.json").exists())

    def test_guarded_collaboration_denial_then_authorized_retry(self):
        self.fault="execution_authorization_denied"
        with self.assertRaises(MainError):
            self.execute()
        self.assertEqual(self.source.prompts(),[])
        self.fault=None
        actual=self.execute()
        self.assertEqual(actual["prompt_state"],"SUBMITTED")
        self.assertEqual(len(self.source.prompts()),1)

    def test_changed_issue_during_pr_establishment_is_refused(self):
        self.changing_issue=True
        with self.assertRaises(MainError) as err:
            self.execute()
        self.assertEqual(err.exception.code,"ISSUE_SNAPSHOT_DRIFT")
        self.assertEqual(self.source.prompts(),[])

    def test_host_mismatched_facade_is_not_permission(self):
        def wrong(_issue, worktree):
            value=self.install(291,worktree)
            value.facade._branch_ref="refs/heads/issue-292"
            return value
        broken=self.new_main(callback=False)
        broken.initial_collaboration_for_issue=wrong
        with self.assertRaises(MainError) as err:
            self.execute(broken)
        self.assertEqual(err.exception.code,"MAIN_INITIAL_COLLABORATION_MISMATCH")
        self.assertEqual(self.source.prompts(),[])

    def test_uncertain_checkpoint_marker_refuses_new_model_turn(self):
        self.execute()
        marker=self.source.runtime/"issue-291"/"initial-collaboration.json"
        data=marker.read_text(encoding="utf-8")
        marker.write_text(data.replace('"contract_id":', '"contract_broken":'),encoding="utf-8")
        with self.assertRaises(MainError) as err:
            self.execute()
        self.assertEqual(err.exception.code,
                         "MAIN_INITIAL_COLLABORATION_RECONCILIATION_REQUIRED")
        self.assertEqual(len(self.source.prompts()),1)


if __name__=="__main__":
    unittest.main()
