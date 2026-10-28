"""Measured actual local subprocess Evidence bound to canonical #191/#217 Task.

All product Git, metadata CAS, and command execution remain inside
disposable fixtures. No real GitHub or KAGARI repository writes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_kagari_vnext_main as fixtures
from tools.kagari_vnext_main import MainError
from tools.kagari_vnext_test_evidence import TaskTestEvidenceRunner
from tools import kagari_vnext_test_evidence as runner_module
from tools.kagari_vnext_task_binding import ControlError
from issue_worktree import IssueWorktreeAllocator
from evidence import Evidence
from task_view import EvidenceRef


class TaskTestEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.MainOrchestratorTest("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.main=self.fixture.main
        self.run_id="smoke01"
        self.runner=TaskTestEvidenceRunner(self.main)
        allocation=IssueWorktreeAllocator(self.fixture.store).ensure(
            task="291",branch_ref=self.fixture.branch_ref,
            base_revision=self.fixture.base,
            expected_contract_id=self.fixture.pub.contract_id)
        self.worktree=Path(allocation.worktree)
        self.count_file=self.fixture.fixture.temp_root/"command-count.txt"

    def argv(self, *, exit_code: int = 0):
        script=(
            "from pathlib import Path; import sys; "
            f"p=Path({str(self.count_file)!r}); "
            "p.write_text(p.read_text()+'x' if p.exists() else 'x'); "
            "print('Task test executed'); "
            f"sys.exit({exit_code})"
        )
        return (sys.executable,"-B","-c",script)

    def test_successful_real_command_persists_immutable_pass_and_idempotent_resume(self):
        argv=self.argv()
        created=self.runner.run(291,run_id=self.run_id,argv=argv)
        self.assertEqual(created.state,"CREATED")
        self.assertEqual(created.result,"PASS")
        self.assertEqual(created.exit_code,0)
        self.assertEqual(created.subject,self.fixture.base)
        self.assertEqual(self.count_file.read_text(),"x")
        record=Evidence(self.fixture.store).read(
            created.metadata_commit, created.evidence_id,
            task="291",subject=self.fixture.base)
        payload=record["payload"]["payload"]
        self.assertEqual(payload["result"],"PASS")
        self.assertEqual(payload["argv_sha256"],hashlib.sha256(
            json.dumps({"argv":argv,"timeout":120},separators=(",",":"),
                       ensure_ascii=False).encode()).hexdigest())
        self.assertEqual(payload["stdout_sha256"],hashlib.sha256(
            b"Task test executed\n").hexdigest())
        resumed=self.runner.run(291,run_id=self.run_id,argv=argv)
        self.assertEqual(resumed.state,"RESUMED")
        self.assertEqual(created.evidence_id,resumed.evidence_id)
        self.assertEqual(self.count_file.read_text(),"x")
        observed=self.main._live_view(self.main.approved[291],self.worktree)
        self.assertEqual(observed["evidence"],[])
        selected=type(self.main)(
            self.fixture.store,approved=self.main.approved,
            read_issue=lambda n:self.fixture.facts[n].copy(),
            runtime_home=self.main.runtime_home,
            port_for_issue=self.fixture.ports.__getitem__,
            opencode_bin=str(self.fixture.bin),
            evidence_for_issue={291:(EvidenceRef(created.evidence_id,
                                                 created.subject),)})
        view=selected.observe(291)["task_view"]
        self.assertEqual(view["evidence"],[{
            "evidence_id":created.evidence_id,
            "subject":created.subject,
            "matches_current_head":True}])

    def test_failed_test_records_fail_not_pass(self):
        receipt=self.runner.run(
            291,run_id="failure1",argv=self.argv(exit_code=3))
        self.assertEqual(receipt.state,"CREATED")
        self.assertEqual(receipt.result,"FAIL")
        self.assertEqual(receipt.exit_code,3)
        evidence=Evidence(self.fixture.store).read(
            receipt.metadata_commit,receipt.evidence_id,
            task="291",subject=self.fixture.base)
        self.assertEqual(evidence["payload"]["payload"]["result"],"FAIL")

    def test_unauthorized_task_does_not_create_test_runtime(self):
        with self.assertRaises(MainError) as exc:
            self.runner.run(292,run_id="notapproved",argv=self.argv())
        self.assertEqual(exc.exception.code,"ISSUE_NOT_APPROVED")
        self.assertFalse(self.count_file.exists())

    def test_same_run_id_different_command_cannot_be_reused(self):
        self.runner.run(291,run_id="repeat01",argv=self.argv())
        with self.assertRaises(MainError) as err:
            self.runner.run(291,run_id="repeat01",argv=self.argv(exit_code=1))
        self.assertEqual(err.exception.code,"TEST_RUN_RECONCILIATION_REQUIRED")
        self.assertEqual(self.count_file.read_text(),"x")

    def test_lost_metadata_ack_positive_readback_recovers_without_rerunning(self):
        original=self.fixture.store.publish

        def accepted_but_ack_lost(*args, **kwargs):
            original(*args,**kwargs)
            from metadata_ref import MetadataRefError
            raise MetadataRefError("synthetic CAS ACK loss")
        with patch.object(self.fixture.store,"publish",
                          side_effect=accepted_but_ack_lost):
            with self.assertRaises(MainError) as err:
                self.runner.run(291,run_id="ackloss",argv=self.argv())
        self.assertEqual(err.exception.code,"TEST_EVIDENCE_OUTCOME_UNCERTAIN")
        self.assertTrue((self.main._runtime(291)/
                         "test-ackloss.candidate.json").exists())
        recovered=self.runner.run(291,run_id="ackloss",argv=self.argv())
        self.assertEqual(recovered.state,"RECOVERED")
        self.assertEqual(recovered.result,"PASS")
        self.assertEqual(self.count_file.read_text(),"x")

    def test_started_marker_without_evidence_prevents_implicit_reexecution(self):
        runid="unknownrun"
        argv=self.argv()
        import hashlib, json
        digest=hashlib.sha256(json.dumps(
            {"argv":argv,"timeout":120},
            separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
        intent={
            "schema_version":1,"repository":"acme/widgets",
            "issue":291,"run_id":runid,
            "contract_id":self.fixture.pub.contract_id,
            "subject":self.fixture.base,"argv_sha256":digest}
        runtime=self.main._runtime(291)
        runtime.mkdir(parents=True)
        (runtime/"test-unknownrun.started.json").write_text(
            json.dumps(intent))
        with self.assertRaises(MainError) as err:
            self.runner.run(291,run_id=runid,argv=argv)
        self.assertEqual(err.exception.code,"TEST_EXECUTION_OUTCOME_UNCERTAIN")
        self.assertFalse(self.count_file.exists())

    def test_preexisting_uncommitted_changes_are_not_evidence_for_head(self):
        (self.worktree/"untracked-secret.txt").write_text("uncommitted work")
        with self.assertRaises(MainError) as err:
            self.runner.run(291,run_id="dirtybefore",argv=self.argv())
        self.assertEqual(err.exception.code,"TEST_SOURCE_DIRTY")
        self.assertFalse(self.count_file.exists())
        self.assertFalse((self.main._runtime(291)/
                          "test-dirtybefore.started.json").exists())

    def test_command_leaving_dirty_worktree_must_not_publish_pass(self):
        script="from pathlib import Path;Path('test-side-effect.txt').write_text('x')"
        with self.assertRaises(MainError) as err:
            self.runner.run(291,run_id="dirtyafter",
                            argv=(sys.executable,"-B","-c",script))
        self.assertEqual(err.exception.code,"TEST_SOURCE_DIRTY_AFTER_RUN")
        self.assertTrue((self.main._runtime(291)/
                         "test-dirtyafter.started.json").exists())
        self.assertFalse((self.main._runtime(291)/
                          "test-dirtyafter.candidate.json").exists())

    def test_registration_lost_during_test_blocks_evidence_publication(self):
        original_tip=self.fixture.store.fetch_tip()
        with patch.object(
            runner_module,"worktree_registered",
            side_effect=[
                None, ControlError("WORKTREE_MISMATCH",
                                   "registered Worktree changed during test"),
            ]):
            with self.assertRaises(MainError) as err:
                self.runner.run(291,run_id="registrydrift",argv=self.argv())
        self.assertEqual(err.exception.code,"TEST_WORKTREE_REGISTRATION_CHANGED")
        self.assertEqual(self.count_file.read_text(),"x")
        self.assertEqual(self.fixture.store.fetch_tip(),original_tip)
        self.assertTrue((self.main._runtime(291)/
                         "test-registrydrift.started.json").exists())
        self.assertFalse((self.main._runtime(291)/
                          "test-registrydrift.candidate.json").exists())

    def test_test_that_commits_new_head_is_not_recorded_as_pass(self):
        script=(
            "from pathlib import Path;import subprocess;"
            "Path('test-output.txt').write_text('content');"
            "subprocess.run(['git','add','test-output.txt'],check=True);"
            "subprocess.run(['git','-c','user.name=Test',"
            "'-c','user.email=test@example.invalid','commit','-q','-m',"
            "'test modifies HEAD'],check=True)"
        )
        with self.assertRaises(MainError) as err:
            self.runner.run(
                291,run_id="changeshead",argv=(sys.executable,"-B","-c",script))
        self.assertEqual(err.exception.code,"TEST_SUBJECT_CHANGED")
        self.assertTrue((self.main._runtime(291)/
                         "test-changeshead.started.json").exists())
        self.assertFalse((self.main._runtime(291)/
                          "test-changeshead.confirmed.json").exists())

    def test_wrong_contract_after_bootstrap_refuses_execution(self):
        self.fixture.writer.reauthorize(
            task="291",branch_ref=self.fixture.branch_ref,
            base_revision=self.fixture.base,
            expected_record_id=self.fixture.pub.record_id,
            title="Changed accepted task",body="New issue specification")
        with self.assertRaises(MainError) as err:
            self.runner.run(291,run_id="stalecontract",argv=self.argv())
        self.assertEqual(err.exception.code,"TASK_CONTRACT_CHANGED")
        self.assertFalse(self.count_file.exists())


if __name__=="__main__":
    unittest.main()
