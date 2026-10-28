"""PR and explicitly selected immutable Evidence facts through #144 Task View.

No GitHub writes, PR creation, Source Publication bypass or arbitrary evidence
discovery. The main read adapter is tested against a fake gh response here.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from tests import test_kagari_vnext_main as main_fixture
from tools.kagari_vnext_main import MainError
from tools.kagari_vnext_progress import github_pr_reader
from issue_worktree import IssueWorktreeAllocator
from task_view import EvidenceRef, GitHubPullRequestRequest, GitHubObservation
from evidence import encode_evidence


class PRReadAdapterTests(unittest.TestCase):
    def setUp(self):
        self.request = GitHubPullRequestRequest(
            "acme/widgets", "291", 93, "refs/heads/issue-291")
        self.data = {
            "number": 93,
            "state": "OPEN",
            "isDraft": True,
            "baseRefName": "main",
            "headRefName": "issue-291",
            "headRefOid": "a"*40,
            "headRepository": {"nameWithOwner": "acme/widgets"},
            "isCrossRepository": False,
        }

    def fetch(self, *, value=None):
        p=subprocess.CompletedProcess(
            ["gh"],0,stdout=json.dumps(self.data if value is None else value),
            stderr="")
        with patch("tools.kagari_vnext_progress.subprocess.run",return_value=p) as call:
            observed=github_pr_reader("acme/widgets")(self.request)
        self.assertEqual(
            call.call_args.args[0],
            ["gh","pr","view","93","--repo","acme/widgets",
             "--json",("number,state,isDraft,baseRefName,headRefName,"
                       "headRefOid,headRepository,isCrossRepository")])
        self.assertEqual(call.call_args.kwargs["stdin"],subprocess.DEVNULL)
        return observed

    def test_exact_selected_pr_binds_current_task_and_open_state(self):
        result=self.fetch()
        self.assertIsInstance(result,GitHubObservation)
        self.assertEqual(result.pull_request.number,93)
        self.assertEqual(result.pull_request.base_repository,"acme/widgets")
        self.assertEqual(result.pull_request.head_repository,"acme/widgets")
        self.assertEqual(result.pull_request.head_ref,"issue-291")
        self.assertEqual(result.pull_request.state,"open")

    def test_fork_or_foreign_task_branch_cannot_be_relabelled(self):
        for key,value in (
            ("headRefName","issue-292"),
            ("number",94),
            ("isCrossRepository",True),
            ("headRepository",{"nameWithOwner":"attacker/widgets"}),
            ("headRefOid","not-a-git-id"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(MainError) as err:
                    self.fetch(value=self.data|{key:value})
                self.assertEqual(err.exception.code,"GITHUB_PR_IDENTITY_MISMATCH")

    def test_unknown_repo_not_a_pr_capability(self):
        with self.assertRaises(MainError):
            github_pr_reader("bad/repo name")


class TaskProgressObservationTests(unittest.TestCase):
    def setUp(self):
        self.inner=main_fixture.MainOrchestratorTest("runTest")
        self.inner.setUp()
        self.addCleanup(self.inner.doCleanups)

    def test_selected_pr_and_existing_evidence_observed_through_task_view(self):
        root=self.inner.project
        result=IssueWorktreeAllocator(self.inner.store).ensure(
            task="291",branch_ref=self.inner.branch_ref,
            base_revision=self.inner.base,
            expected_contract_id=self.inner.pub.contract_id)
        self.assertEqual(result.status,"CREATED")
        evidence_id,data=encode_evidence(
            "acme/widgets","291",self.inner.base,{
                "schema_version":1,"kind":"fixture/test-result-v1",
                "producer":{"name":"controlled-local-fixture"},
                "created_at":"2026-10-25T00:00:00Z",
                "payload":{"result":"PASS"},
            })
        self.inner.store.publish([data])
        def read_pr(request):
            from task_view import (
                GitHubObservation, GitHubPullRequestFacts)
            return GitHubObservation(GitHubPullRequestFacts(
                base_repository="acme/widgets",
                head_repository="acme/widgets",
                number=request.number, state="open", draft=True,
                base_ref="main",head_ref="issue-291",
                head_oid=self.inner.base),None)
        main=self.inner.main
        selected=type(main)(
            self.inner.store,approved=self.inner.approved,
            read_issue=lambda n:self.inner.facts[n].copy(),
            runtime_home=self.inner.runtime,
            port_for_issue=self.inner.ports.__getitem__,
            opencode_bin=str(self.inner.bin),
            pr_for_issue={291:93},
            evidence_for_issue={291:(EvidenceRef(evidence_id,self.inner.base),)},
            read_pr=read_pr)
        observation=selected.observe(291)
        self.assertEqual(observation["runtime"],"NOT_STARTED")
        view=observation["task_view"]
        self.assertTrue(view["git"]["branch_matches_task"])
        self.assertEqual(view["github"]["state"],"observed")
        self.assertEqual(view["github"]["pull_request"]["number"],93)
        self.assertEqual(view["github"]["pull_request"]["head_ref"],"issue-291")
        self.assertEqual(view["evidence"],[{
            "evidence_id":evidence_id, "subject":self.inner.base,
            "matches_current_head":True,
        }])
        self.assertFalse(self.inner.runtime.exists())

    def test_unavailable_selected_pr_does_not_fabricate_readiness(self):
        expected=self.inner.main.approved
        def failed_reader(_request):
            raise ConnectionError("not connected")
        selected=type(self.inner.main)(
            self.inner.store,approved=expected,
            read_issue=lambda n:self.inner.facts[n].copy(),
            runtime_home=self.inner.runtime,
            port_for_issue=self.inner.ports.__getitem__,
            opencode_bin=str(self.inner.bin),
            pr_for_issue={291:93},
            read_pr=failed_reader)
        observation=selected.observe(291)
        self.assertEqual(observation["task_view"]["github"]["state"],"unavailable")
        self.assertEqual(observation["task_view"]["github"]["error_code"],
                         "callback_failed")
        self.assertFalse((self.inner.project/".worktrees").exists())

    def test_missing_selected_evidence_does_not_claim_pass(self):
        selected=type(self.inner.main)(
            self.inner.store,approved=self.inner.approved,
            read_issue=lambda n:self.inner.facts[n].copy(),
            runtime_home=self.inner.runtime,
            port_for_issue=self.inner.ports.__getitem__,
            opencode_bin=str(self.inner.bin),
            evidence_for_issue={291:(EvidenceRef("f"*64,self.inner.base),)},
        )
        with self.assertRaises(MainError) as error:
            selected.observe(291)
        self.assertEqual(error.exception.code,"TASK_VIEW_UNAVAILABLE")


if __name__=="__main__":
    unittest.main()
