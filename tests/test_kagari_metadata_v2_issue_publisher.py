"""#337 Phase 2C1: real disposable bare-Git v2 Issue writer + #140 journal.

All callbacks and refs are synthetic. No real GitHub, remote Ruleset, Product
worktree effects, Task/Session/model, production Attach, or source publishing.
"""
from __future__ import annotations

import dataclasses
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "components/agent-core-v4"))

import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_bootstrap import BootstrapIdentity, ApprovedIssue
from kagari_metadata_v2_host_binding import RepositoryIdentity, RegisteredIssue
import kagari_metadata_v2_issue_publisher as publisher_module
from kagari_metadata_v2_issue_publisher import (
    IssuePublisher, IssueWrite, IssueWriteRefused, IssueWriteUncertain, _key,
)
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import ARCHIVE_REF, Limits, RefState, issue_ref
from metadata_v2_archive import archive_issue
from task_record import encode_contract, encode_record
from task_view import encode_snapshot
from tests.test_metadata_v2 import Fixture, REPOSITORY
from tests import test_kagari_vnext_attach as attach_fixture
import metadata_codec as codec

REPO_ID = 424242


class IssueWriterTest(unittest.TestCase):
    def setUp(self):
        attached = attach_fixture.AttachTests("runTest")
        attached.setUp()
        self.addCleanup(attached.doCleanups)
        self.attached = attached
        self.project, self.home, self.package = (
            attached.project, attached.home, attached.package)
        self.assertEqual("ATTACHED", attached.operate("install")["status"])
        self.base = attached.head
        self.temp = tempfile.TemporaryDirectory(prefix="337-phase2c1-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = Fixture(self.root)
        self.jpath = self.root / "journal"
        self.jpath.mkdir(mode=0o700)
        self.issue = ApprovedIssue(
            REPO_ID, REPOSITORY, 101, "refs/heads/task-101", self.base,
            "Issue 101", "approved immutable scope")
        self.contract_id, contract = encode_contract(
            REPOSITORY, "101", self.base,
            {"schema_version": 1, "title": self.issue.title, "body": self.issue.body})
        self.record_id, record = encode_record(
            REPOSITORY, "101", self.base,
            {"schema_version": 1, "branch_ref": self.issue.branch_ref,
             "contract_id": self.contract_id})
        self.first = self.fixture.store().publish_issue(
            "101", [contract, record], record_update=(None, self.record_id))
        self.registered = RegisteredIssue(
            REPO_ID, REPOSITORY, 101, self.issue.branch_ref, self.base,
            self.record_id, self.contract_id)
        self.repo_identity = RepositoryIdentity(
            REPO_ID, REPOSITORY, self.fixture.genesis, "sha1")
        self.writes, self.grants = [], []
        self.allowed = True

    def op(self, *objects, expected=None, archive=None,
           operation_id="1" * 32, next_record_id=None):
        return IssueWrite(operation_id,
                          archive or self.observe(ARCHIVE_REF),
                          expected or self.observe(issue_ref("101")),
                          tuple(objects), next_record_id)

    def observe(self, ref):
        return self.fixture.observe(ref, time.monotonic() + 2).commit

    def journal(self):
        return RecoveryJournal(self.jpath)

    def _grant(self, request):
        self.grants.append(request)
        return self.allowed

    def _writer(self, request, bundle, deadline):
        self.writes.append(request)
        key = _key(REPO_ID, int(request.task), request.expected_issue)
        saved = self.journal().read(key)
        self.assertEqual(saved["candidate"], bundle.commit)
        self.assertEqual(saved["expected_archive"], request.expected_archive)
        self.assertEqual(saved["expected_issue"], request.expected_issue)
        self.assertEqual(saved["issue"], int(request.task))
        self.assertEqual(saved["schema"], 1)
        return self.fixture.writer(request, bundle, deadline)

    def publish(self, operation, *, writer=None, grant=None,
                approved=None, registered=None, identity=None,
                repo_identity=None, reader=None, observer=None, journal=None,
                limits=None):
        with gate.hold_verified(self.project, self.home, self.package) as lease:
            identity = identity or BootstrapIdentity(
                REPO_ID, REPOSITORY, lease._fingerprint, "sha1")
            publisher = IssuePublisher(
                identity, repo_identity or self.repo_identity,
                registered or self.registered, approved or self.issue,
                lease=lease, reader=reader or self.fixture.reader,
                observer=observer or self.fixture.observe,
                journal=journal or self.journal(),
                authorize=grant or self._grant, writer=writer or self._writer,
                limits=limits or Limits())
            return publisher.publish(operation)

    def evidence(self, *, task="101", repository=REPOSITORY, subject=None):
        payload = {
            "schema_version": 1, "kind": "structured",
            "producer": {"name": "fixture"},
            "created_at": "2026-11-03T00:00:00Z",
            "payload": {"assertion": "exact Git metadata"},
        }
        return codec.encode_object("evidence", repository, task, subject or self.base, payload)

    def snapshot(self, parent, *, subject=None):
        subject = subject or self.base
        branch = self.issue.branch_ref
        view = {
            "schema_version": 1, "repository": REPOSITORY, "task": "101",
            "subject": subject, "branch_ref": branch,
            "git": {
                "head": subject, "tree": "b" * 40, "branch_ref": branch,
                "branch_matches_task": True,
                "status": {key: 0 for key in (
                    "index_changes", "worktree_changes", "untracked",
                    "conflicts", "submodule_changes")},
                "worktrees": [], "default_branch": {"state": "not_requested"},
                "remote_head": {"state": "not_requested"},
                "submodule_worktrees": "not_inspected", "staged_gitlinks": 0,
            },
            "authority": {
                "state": "observed", "metadata_commit": parent,
                "record_id": self.record_id, "contract_id": self.contract_id,
                "base_revision": self.base, "branch_ref": branch,
                "disposition": None,
            },
            "evidence": [], "github": {"state": "not_requested"},
            "previous_checkpoint": {"state": "not_requested"},
        }
        return encode_snapshot(REPOSITORY, "101", subject, "turn-end", view)

    def test_evidence_append_then_snapshot_exact_ancestor_and_new_operation(self):
        evidence_id, evidence = self.evidence()
        first_op = self.op(evidence)
        published = self.publish(first_op)
        self.assertEqual("PUBLISHED", published.state)
        self.assertEqual(self.first, published.previous_issue_commit)
        self.assertEqual(self.record_id, published.record_id)
        self.assertEqual("evidence", self.fixture.store(writer=False).read_object(
            published.issue_commit, "evidence", evidence_id,
            task="101", subject=self.base)["kind"])
        snapshot_id, snapshot = self.snapshot(published.issue_commit)
        result = self.publish(self.op(snapshot, operation_id="2" * 32))
        self.assertEqual("PUBLISHED", result.state)
        self.assertEqual(published.issue_commit, result.previous_issue_commit)
        read = self.fixture.store(writer=False).read_object(
            result.issue_commit, "task-view-snapshot", snapshot_id,
            task="101", subject=self.base)
        self.assertEqual(published.issue_commit,
                         read["payload"]["view"]["authority"]["metadata_commit"])
        self.assertEqual(2, len(self.writes))
        self.assertEqual(2, len(list(self.jpath.iterdir())))

    def test_same_request_retry_returns_original_candidate_without_second_write(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        first = self.publish(op)
        second = self.publish(op)
        self.assertEqual(("PUBLISHED", "RECOVERED"), (first.state, second.state))
        self.assertEqual(first.issue_commit, second.issue_commit)
        self.assertEqual(1, len(self.writes))
        self.assertEqual(1, len(self.grants))

    def test_different_operation_same_old_head_is_blocked_without_new_commit(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        self.publish(op)
        with self.assertRaisesRegex(IssueWriteRefused, "receipt_identity_mismatch"):
            self.publish(dataclasses.replace(op, operation_id="2" * 32))
        with self.assertRaisesRegex(IssueWriteRefused, "receipt_identity_mismatch"):
            self.publish(self.op(self.evidence(subject="b" * 40)[1],
                                 expected=op.expected_issue, operation_id="3" * 32))
        self.assertEqual(1, len(self.writes))

    def test_lost_ack_after_effect_reconciles_without_blind_replay(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        def applied_lost(update, bundle, deadline):
            self._writer(update, bundle, deadline)
            raise TimeoutError("ack lost after commit")
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op, writer=applied_lost)
        self.assertEqual(1, len(self.writes))
        result = self.publish(op)
        self.assertEqual("RECOVERED", result.state)
        self.assertEqual(self.observe(issue_ref("101")), result.issue_commit)
        self.assertEqual(1, len(self.writes))

    def test_lost_ack_before_effect_persists_intent_and_never_retries(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        calls = []
        def before(update, bundle, deadline):
            calls.append(update)
            raise TimeoutError("no remote effect")
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op, writer=before)
        self.assertEqual(self.first, self.observe(issue_ref("101")))
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op, writer=before)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(list(self.jpath.iterdir())))

    def test_writer_returns_false_also_does_not_blindly_retry(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        calls = []
        def reject(update, bundle, deadline):
            calls.append(update)
            return False
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op, writer=reject)
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op, writer=reject)
        self.assertEqual(1, len(calls))
        self.assertEqual(self.first, self.observe(issue_ref("101")))

    def test_partial_or_corrupt_host_receipt_preserves_unknown_no_replay(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        effects = []
        def no_ack(update, bundle, deadline):
            effects.append(update)
            raise TimeoutError("attempt may be incomplete")
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op, writer=no_ack)
        record_path = self.jpath / _key(REPO_ID, 101, op.expected_issue)
        self.assertTrue(record_path.is_file())
        record_path.write_bytes(b"partial invalid canonical receipt")
        with self.assertRaisesRegex(IssueWriteUncertain, "receipt_unverifiable"):
            self.publish(op, writer=no_ack)
        self.assertEqual(1, len(effects))
        self.assertEqual(self.first, self.observe(issue_ref("101")))

    def test_two_distinct_issue_writers_are_isolated_by_task_and_journal_key(self):
        other = ApprovedIssue(
            REPO_ID, REPOSITORY, 202, "refs/heads/task-202", self.base,
            "Issue 202", "other immutable requirements")
        cid, contract = encode_contract(REPOSITORY, "202", self.base,
            {"schema_version": 1, "title": other.title, "body": other.body})
        rid, record = encode_record(REPOSITORY, "202", self.base,
            {"schema_version": 1, "branch_ref": other.branch_ref,
             "contract_id": cid})
        other_first = self.fixture.store().publish_issue(
            "202", [contract, record], record_update=(None, rid))
        other_registered = RegisteredIssue(
            REPO_ID, REPOSITORY, 202, other.branch_ref, self.base, rid, cid)
        _, first_evidence = self.evidence()
        _, other_evidence = self.evidence(task="202")
        first_result = self.publish(self.op(first_evidence))
        second = self.publish(
            IssueWrite("2" * 32, self.observe(ARCHIVE_REF), other_first,
                       (other_evidence,)),
            approved=other, registered=other_registered)
        self.assertEqual("PUBLISHED", second.state)
        self.assertNotEqual(first_result.issue_commit, second.issue_commit)
        self.assertEqual(first_result.issue_commit, self.observe(issue_ref("101")))
        self.assertEqual(second.issue_commit, self.observe(issue_ref("202")))
        self.assertEqual(2, len(self.writes))
        self.assertEqual(2, len(list(self.jpath.iterdir())))

    def test_expired_grant_cannot_invoke_external_writer(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        def slow_authorize(_request):
            time.sleep(1.7)
            return True
        with self.assertRaisesRegex(IssueWriteRefused, "before_effect"):
            self.publish(op, grant=slow_authorize, limits=Limits(seconds=1.5))
        self.assertEqual([], self.writes)
        self.assertFalse(list(self.jpath.iterdir()))
        self.assertEqual(self.first, self.observe(issue_ref("101")))

    def test_deadline_expires_during_journal_fsync_no_ref_effect_or_replay(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        journal = self.journal()
        original_create = journal.create
        def slow_durable_create(key, record):
            original_create(key, record)
            time.sleep(1.7)
        journal.create = slow_durable_create
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op, journal=journal, limits=Limits(seconds=1.5))
        self.assertEqual([], self.writes)
        self.assertEqual(self.first, self.observe(issue_ref("101")))
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op)
        self.assertEqual([], self.writes)

    def test_two_simultaneous_same_head_publishers_cannot_both_write(self):
        _, first_data = self.evidence()
        _, second_data = self.evidence(subject="b" * 40)
        op_one = self.op(first_data, operation_id="3" * 32)
        op_two = self.op(second_data, operation_id="4" * 32)
        barrier = threading.Barrier(2)
        def grant(request):
            barrier.wait(timeout=8)
            return True
        with gate.hold_verified(self.project, self.home, self.package) as lease:
            identity = BootstrapIdentity(
                REPO_ID, REPOSITORY, lease._fingerprint, "sha1")
            def instantiate():
                return IssuePublisher(
                    identity, self.repo_identity, self.registered, self.issue,
                    lease=lease, reader=self.fixture.reader,
                    observer=self.fixture.observe, journal=self.journal(),
                    authorize=grant, writer=self._writer)
            one, two = instantiate(), instantiate()
            with ThreadPoolExecutor(max_workers=2) as pool:
                attempts = (pool.submit(one.publish, op_one),
                            pool.submit(two.publish, op_two))
                values, failures = [], []
                for attempt in attempts:
                    try:
                        values.append(attempt.result(timeout=25))
                    except (IssueWriteRefused, IssueWriteUncertain) as error:
                        failures.append(error)
        self.assertEqual(1, len(values))
        self.assertEqual(1, len(failures))
        self.assertEqual(1, len(self.writes))
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        self.assertEqual(values[0].issue_commit, self.observe(issue_ref("101")))

    def test_positive_ack_but_corrupted_candidate_readback_is_uncertain(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        invalid = set()
        def reader(key, maximum, deadline):
            kind, raw = self.fixture.reader(key, maximum, deadline)
            return (kind, raw + b"tampered") if key in invalid else (kind, raw)
        def ack_and_break_reader(update, bundle, deadline):
            self._writer(update, bundle, deadline)
            invalid.add(bundle.commit)
            return True
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op, writer=ack_and_break_reader, reader=reader)
        self.assertEqual(1, len(self.writes))
        self.assertEqual("RECOVERED", self.publish(op).state)
        self.assertEqual(1, len(self.writes))

    def test_authority_denied_or_missing_does_not_persist_or_publish(self):
        self.allowed = False
        _, evidence = self.evidence()
        with self.assertRaisesRegex(IssueWriteRefused, "before_effect"):
            self.publish(self.op(evidence))
        self.assertFalse(list(self.jpath.iterdir()))
        self.assertEqual([], self.writes)
        self.assertEqual(1, len(self.grants))

    def test_contract_and_record_disposition_update_exact_expected_prior(self):
        prior = self.first
        next_id, next_record = encode_record(
            REPOSITORY, "101", self.base,
            {"schema_version": 1, "branch_ref": self.issue.branch_ref,
             "contract_id": self.contract_id, "disposition": {"kind": "cancelled"}})
        op = self.op(next_record, next_record_id=next_id)
        first = self.publish(op)
        self.assertEqual("PUBLISHED", first.state)
        self.assertEqual(next_id, first.record_id)
        self.assertEqual((prior,), self.fixture.store().validation().issue(
            first.issue_commit, "101").parents)
        self.assertEqual("cancelled", self.fixture.store(writer=False).read_object(
            first.issue_commit, "task-record", next_id, task="101",
            subject=self.base)["payload"]["disposition"]["kind"])
        self.assertEqual("RECOVERED", self.publish(op).state)
        self.assertEqual(1, len(self.writes))
        with self.assertRaises(IssueWriteRefused):
            self.publish(self.op(self.evidence()[1], operation_id="3" * 32))

    def test_unapproved_reauthorization_without_disposition_refuses(self):
        next_id, other = encode_record(
            REPOSITORY, "101", self.base,
            {"schema_version": 1, "branch_ref": self.issue.branch_ref,
             "contract_id": self.contract_id})
        with self.assertRaises(IssueWriteRefused):
            self.publish(self.op(other, next_record_id=next_id))
        self.assertEqual([], self.writes)

    def test_record_new_contract_base_or_branch_refuses(self):
        next_id, other = encode_record(
            REPOSITORY, "101", self.base,
            {"schema_version": 1, "branch_ref": self.issue.branch_ref,
             "contract_id": "f" * 64, "disposition": {"kind": "cancelled"}})
        with self.assertRaises(IssueWriteRefused):
            self.publish(self.op(other, next_record_id=next_id))
        self.assertEqual([], self.writes)

    def test_per_object_container_limit_refuses_before_json_decode(self):
        payload = {"schema_version": 1, "kind": "structured",
                   "producer": {"name": "fixture"},
                   "created_at": "2026-11-03T00:00:00Z",
                   "payload": {"items": [{} for _ in range(8_300)]}}
        _, raw = codec.encode_object("evidence", REPOSITORY, "101", self.base, payload)
        self.assertLess(len(raw), codec.MAX_OBJECT_BYTES)
        with patch.object(codec, "decode_object",
                          side_effect=AssertionError("decoder must not run")) as dec:
            with self.assertRaisesRegex(IssueWriteRefused, "container_limit"):
                self.publish(self.op(raw))
        self.assertEqual(0, dec.call_count)
        self.assertFalse(list(self.jpath.iterdir()))
        self.assertEqual([], self.writes)
        self.assertEqual([], self.grants)

    def test_cumulative_container_budget_refuses_before_excess_decode(self):
        objects = []
        for i in range(8):
            payload = {"schema_version": 1, "kind": "structured",
                       "producer": {"name": "fixture"},
                       "created_at": "2026-11-03T00:00:00Z",
                       "payload": {"items": [{} for _ in range(4_500)],
                                   "nonce": i}}
            _, raw = codec.encode_object(
                "evidence", REPOSITORY, "101", f"{i + 1:040x}", payload)
            objects.append(raw)
        original = codec.decode_object
        with patch.object(codec, "decode_object", wraps=original) as dec:
            with self.assertRaisesRegex(IssueWriteRefused, "cumulative_container"):
                self.publish(self.op(*objects))
        self.assertLess(dec.call_count, len(objects))
        self.assertGreater(dec.call_count, 0)
        self.assertFalse(list(self.jpath.iterdir()))
        self.assertEqual([], self.writes)
        self.assertEqual([], self.grants)

    def test_slow_container_scan_refuses_before_json_decode(self):
        _, raw = self.evidence()
        original = publisher_module.json_container_markers
        def slow(data):
            time.sleep(0.22)
            return original(data)
        with patch.object(publisher_module, "json_container_markers", side_effect=slow):
            with patch.object(codec, "decode_object",
                              side_effect=AssertionError("decoder must not run")) as dec:
                with self.assertRaisesRegex(IssueWriteRefused, "predecode_deadline"):
                    self.publish(self.op(raw), limits=Limits(seconds=0.12))
        self.assertEqual(0, dec.call_count)
        self.assertFalse(list(self.jpath.iterdir()))
        self.assertEqual([], self.writes)

    def test_wrong_issue_repository_kind_and_duplicate_objects_refuse(self):
        _, evidence = self.evidence()
        _, foreign_task = self.evidence(task="202")
        _, foreign_repo = self.evidence(repository="acme/foreign")
        _, contract = encode_contract(REPOSITORY, "101", self.base,
            {"schema_version": 1, "title": self.issue.title, "body": self.issue.body})
        for items in ((foreign_task,), (foreign_repo,), (contract,), (evidence,evidence)):
            with self.subTest(items=items), self.assertRaises(IssueWriteRefused):
                self.publish(self.op(*items))
        self.assertEqual([], self.writes)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_missing_foreign_or_symbolic_current_ref_refuse(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        ref = issue_ref("101")
        self.fixture.git("update-ref", "--no-deref", "-d", ref, self.first)
        with self.assertRaises(IssueWriteRefused):
            self.publish(op)
        self.fixture.set_ref(ref, self.first)
        def symbolic(name, deadline):
            state = self.fixture.observe(name, deadline)
            return RefState(ref, state.commit, False) if name == ref else state
        with self.assertRaises(IssueWriteRefused):
            self.publish(op, observer=symbolic)
        self.assertEqual([], self.writes)

    def test_attached_source_identity_drift_and_missing_receipt_refuse(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        self.attached.operate("uninstall")
        with self.assertRaises(Exception):
            self.publish(op)
        self.attached.operate("install")
        with gate.hold_verified(self.project, self.home, self.package) as lease:
            foreign = BootstrapIdentity(REPO_ID+1, REPOSITORY, lease._fingerprint)
        with self.assertRaises(IssueWriteRefused):
            self.publish(op, identity=foreign)
        self.assertEqual([], self.writes)

    def test_changed_archive_before_effect_refuses_without_journal(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        _, contract = encode_contract(REPOSITORY, "202", self.base,
            {"schema_version": 1, "title": "Task 202", "body": "second"})
        cid = codec.encode_object("contract", REPOSITORY,"202",self.base,
             {"schema_version": 1, "title": "Task 202", "body": "second"})[0]
        rid, record = encode_record(REPOSITORY, "202",self.base,
            {"schema_version": 1, "branch_ref":"refs/heads/task-202", "contract_id":cid})
        self.fixture.store().publish_issue("202",[contract,record],record_update=(None,rid))
        archive_issue(self.fixture.store(),"202",retention_policy="fixture-only",
                      verify_terminal=lambda *_: True,
                      archive_writer=self.fixture.writer)
        with self.assertRaisesRegex(IssueWriteRefused, "expected_tips_changed"):
            self.publish(op)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_archive_advances_other_issue_after_effect_recover_original_candidate(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        def archived_other(update,bundle,deadline):
            self._writer(update,bundle,deadline)
            cid, contract = encode_contract(REPOSITORY,"202",self.base,
                {"schema_version":1,"title":"Second","body":"other"})
            rid, record = encode_record(REPOSITORY,"202",self.base,
                {"schema_version":1,"branch_ref":"refs/heads/task-202","contract_id":cid})
            self.fixture.store().publish_issue("202",[contract,record],record_update=(None,rid))
            archive_issue(self.fixture.store(),"202",retention_policy="fixture-only",
                          verify_terminal=lambda *_: True,archive_writer=self.fixture.writer)
            return True
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op, writer=archived_other)
        # Archive advanced due to another Issue only. The previously written
        # Issue candidate remains current and can be positively reconciled.
        self.assertNotEqual(op.expected_archive, self.observe(ARCHIVE_REF))
        self.assertEqual("RECOVERED",self.publish(op).state)
        self.assertEqual(1,len(self.writes))

    def test_same_issue_ref_advanced_after_unknown_ack_blocks_without_replay(self):
        _, evidence = self.evidence()
        op = self.op(evidence)
        def apply_then_advance(update,bundle,deadline):
            self._writer(update,bundle,deadline)
            self.fixture.store().publish_issue("101",[self.evidence(subject="c"*40)[1]])
            return True
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op, writer=apply_then_advance)
        with self.assertRaises(IssueWriteUncertain):
            self.publish(op)
        self.assertEqual(1, len(self.writes))

    def test_product_head_index_dirty_files_and_v1_ref_remain_intact(self):
        self.fixture.set_ref("refs/agentcore/metadata", self.fixture.genesis)
        (self.project / "README.md").write_text("stage me\n")
        self.attached.git("add","README.md")
        (self.project / "README.md").write_text("dirty user note\n")
        (self.project / "untracked-user.txt").write_text("retain\n")
        status_before = self.attached.git("status","--porcelain=v1","-uall")
        index = (self.project / ".git/index").read_bytes()
        head = self.attached.git("rev-parse","HEAD")
        v1 = self.observe("refs/agentcore/metadata")
        _, evidence = self.evidence()
        result = self.publish(self.op(evidence))
        self.assertEqual("PUBLISHED",result.state)
        self.assertEqual(head,self.attached.git("rev-parse","HEAD"))
        self.assertEqual(index,(self.project / ".git/index").read_bytes())
        self.assertEqual(status_before,self.attached.git("status","--porcelain=v1","-uall"))
        self.assertEqual(v1,self.observe("refs/agentcore/metadata"))
        self.assertEqual("dirty user note\n",(self.project / "README.md").read_text())
        self.assertEqual("retain\n",(self.project / "untracked-user.txt").read_text())


if __name__ == "__main__":
    unittest.main()
