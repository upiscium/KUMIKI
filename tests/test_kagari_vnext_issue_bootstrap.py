"""Explicitly approved Issue -> existing #191 TaskRecord bootstrap fixture tests.

No real GitHub effect. A fixture host must supply true authorization for the
one exact #191 create request; Open Issue existence never grants authority.
"""
from __future__ import annotations

from dataclasses import replace
import unittest
from tests import test_metadata_ref as fixtures

from tools.kagari_vnext_issue_bootstrap import (
    ApprovedIssueSnapshot, bootstrap_approved_issue)
from tools.kagari_vnext_main import MainError
from task_record import TaskRecords


class TaskBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.MetadataRefTest("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store
        self.root=self.fixture.product
        self.base=fixtures.git("rev-parse","HEAD",cwd=self.root).decode().strip()
        self.approved=ApprovedIssueSnapshot(
            repository="acme/widgets", issue=291,
            branch_ref="refs/heads/issue-291",
            base_revision=self.base,
            title="Approved task", body="Exact approved requirements.")
        self.facts={
            "repository":"acme/widgets","number":291,"state":"OPEN",
            "title":self.approved.title,"body":self.approved.body}
        self.requests=[]
        self.authorize=lambda r: self.capture_authorization(r)

    def capture_authorization(self, request):
        self.requests.append(request)
        return (request.operation=="create"
                and request.repository=="acme/widgets"
                and request.task=="291"
                and request.branch_ref=="refs/heads/issue-291"
                and request.base_revision==self.base)

    def run_bootstrap(self, **overrides):
        return bootstrap_approved_issue(
            self.store,overrides.get("approved",self.approved),
            read_issue=overrides.get("read_issue",lambda n:self.facts.copy()),
            authorize_create=overrides.get("authorize_create",self.authorize))

    def reject(self, code, **opts):
        with self.assertRaises(MainError) as caught:
            self.run_bootstrap(**opts)
        self.assertEqual(caught.exception.code,code)
        return caught.exception

    def test_first_exact_approved_issue_records_one_immutable_snapshot(self):
        result=self.run_bootstrap()
        self.assertEqual(result.state,"CREATED")
        self.assertEqual(result.repository,"acme/widgets")
        self.assertEqual(result.issue,291)
        self.assertEqual(len(self.requests),1)
        self.assertEqual(self.requests[0].contract_id,result.contract_id)
        resolved=TaskRecords(self.store,authorize=lambda _:False).read(
            result.metadata_commit,task="291",branch_ref=self.approved.branch_ref,
            base_revision=self.base)
        self.assertEqual(resolved[0],result.record_id)
        self.assertEqual(resolved[2]["payload"]["title"],self.approved.title)
        self.assertEqual(resolved[2]["payload"]["body"],self.approved.body)
        self.assertFalse((self.root/".worktrees").exists())

    def test_exact_retry_reuses_record_without_new_host_authorization(self):
        first=self.run_bootstrap()
        resumed=self.run_bootstrap(authorize_create=lambda _:False)
        self.assertEqual(resumed.state,"RESUMED")
        self.assertEqual(first.record_id,resumed.record_id)
        self.assertEqual(first.contract_id,resumed.contract_id)
        self.assertEqual(first.metadata_commit,resumed.metadata_commit)
        self.assertEqual(len(self.requests),1)

    def test_host_denial_refuses_even_for_open_issue(self):
        self.reject("TASK_RECORD_PUBLICATION_REFUSED",
                    authorize_create=lambda request:False)
        self.assertIsNone(self.store.fetch_tip())
        self.assertFalse((self.root/".worktrees").exists())

    def test_closed_issue_or_changed_requirements_refused_before_effects(self):
        for change in ({"state":"CLOSED"},{"body":"Other requirements"},
                       {"repository":"other/repo"},{"number":292},
                       {"title":"Other title"}):
            with self.subTest(change=change):
                self.reject("ISSUE_SNAPSHOT_DRIFT",read_issue=lambda _:self.facts|change)
                self.assertIsNone(self.store.fetch_tip())

    def test_existing_record_with_new_body_refuses_instead_of_overwriting(self):
        first=self.run_bootstrap()
        different=replace(self.approved,body="Later requirements")
        self.facts["body"]=different.body
        self.reject("TASK_CONTRACT_CHANGED",approved=different)
        self.assertEqual(self.store.fetch_tip(),first.metadata_commit)
        self.assertEqual(len(self.requests),1)

    def test_missing_host_capability_is_not_inferred_from_issue_visibility(self):
        self.reject("HOST_AUTHORITY_REQUIRED",authorize_create=None)
        self.assertIsNone(self.store.fetch_tip())

    def test_cross_repository_branch_and_base_mismatch_refused(self):
        for wrong in (
            replace(self.approved,repository="foreign/repo"),
            replace(self.approved,branch_ref="refs/heads/issue-999"),
            replace(self.approved,base_revision="a"*40),
        ):
            with self.subTest(wrong=wrong):
                self.reject("BOOTSTRAP_INVALID" if wrong.base_revision==self.base
                            else "TASK_RECORD_INVALID",approved=wrong)

    def test_reauthorized_contract_is_not_implicitly_adopted(self):
        created=self.run_bootstrap()
        TaskRecords(self.store,authorize=lambda _:True).reauthorize(
            task="291",branch_ref=self.approved.branch_ref,
            base_revision=self.base,expected_record_id=created.record_id,
            title="Retitled task",body="Later authorized requirements.")
        self.reject("TASK_CONTRACT_CHANGED")
        self.assertEqual(len(self.requests),1)

    def test_disposed_task_cannot_be_rebootstrapped(self):
        created=self.run_bootstrap()
        TaskRecords(self.store,authorize=lambda _:True).set_disposition(
            task="291",branch_ref=self.approved.branch_ref,
            base_revision=self.base,expected_record_id=created.record_id,
            disposition={"kind":"cancelled"})
        self.reject("TASK_RETIRED")


if __name__=="__main__":
    unittest.main()
