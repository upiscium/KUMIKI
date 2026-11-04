"""#337 Phase 2C2: disposable native Git + synthetic Host archive tests.

No real GitHub, production Task/Main/Session, model, external metadata refs,
Nix Install, deletion authority, Release or Source Guard publication.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import tempfile
import sys
import threading
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "components/agent-core-v4")]

import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_archive_host import (
    ArchiveHost, ArchiveOperation, ArchiveQuiescenceLease,
    ArchiveRefused, ArchiveUncertain, TerminalFacts, _key,
)
from kagari_metadata_v2_bootstrap import ApprovedIssue, BootstrapIdentity
from kagari_metadata_v2_host_binding import RepositoryIdentity, RegisteredIssue
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import ARCHIVE_REF, RefState, UncertainPublication, issue_ref
from metadata_v2_archive import archive_issue
from task_record import encode_contract, encode_record
from tests.test_metadata_v2 import Fixture, REPOSITORY
from tests import test_kagari_vnext_attach as attach_fixture

RID = 424242


class ArchiveHostTest(unittest.TestCase):
    def setUp(self):
        attached = attach_fixture.AttachTests("runTest")
        attached.setUp()
        self.addCleanup(attached.doCleanups)
        self.attached = attached
        self.project, self.home, self.package = (
            attached.project, attached.home, attached.package)
        self.assertEqual("ATTACHED", attached.operate("install")["status"])
        self.base = attached.head
        tmp = tempfile.TemporaryDirectory(prefix="337-v2-archive-host-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.fixture = Fixture(self.root)
        self.jpath = self.root / "journal"
        self.jpath.mkdir(mode=0o700)
        self.repository_identity = RepositoryIdentity(
            RID, REPOSITORY, self.fixture.genesis, "sha1")
        self.approved, self.registered, self.first = self.register(101)
        self.pr_state = "MERGED"
        self.human_decision_id = "e" * 64
        self.session_stopped = True
        self.writers_quiescent = True
        self.product_preserved = True
        self.no_unpublished_work = True
        self.product_ancestry_proven = True
        self.lock_valid = True
        self.prior_clear = True
        self.granted = True
        self.terminal_reads = 0
        self.holds = 0
        self.grants = []
        self.writes = []
        self.saved_before_writer = []
        self.route = None

    def register(self, n, *, disposition=None):
        issue = ApprovedIssue(
            RID, REPOSITORY, n, f"refs/heads/task-{n}", self.base,
            f"Issue {n}", f"approved requirements {n}")
        cid, contract = encode_contract(
            REPOSITORY, str(n), self.base,
            {"schema_version": 1, "title": issue.title, "body": issue.body})
        payload = {"schema_version": 1, "branch_ref": issue.branch_ref,
                   "contract_id": cid}
        if disposition is not None:
            payload["disposition"] = disposition
        rid, record = encode_record(REPOSITORY, str(n), self.base, payload)
        commit = self.fixture.store().publish_issue(
            str(n), [contract, record], record_update=(None, rid))
        binding = RegisteredIssue(
            RID, REPOSITORY, n, issue.branch_ref, self.base, rid, cid)
        return issue, binding, commit

    def observation(self, name):
        return self.fixture.observe(name, time.monotonic() + 3).commit

    def journal(self):
        return RecoveryJournal(self.jpath)

    def op(self, *, archive=None, issue=None, record_id=None,
           operation_id="a" * 32, policy="fixture-retention-only"):
        return ArchiveOperation(
            operation_id, archive or self.observation(ARCHIVE_REF),
            issue or self.observation(issue_ref(str(self.approved.issue))),
            record_id or self.registered.record_id, policy)

    @contextmanager
    def hold(self, op):
        self.holds += 1
        try:
            yield ArchiveQuiescenceLease(lambda attempt: self.lock_valid and attempt == op)
        finally:
            self.holds -= 1

    def terminal(self, op, current):
        self.terminal_reads += 1
        selected = self.approved
        registered = self.registered
        disposition = current.disposition
        route = self.route or (
            disposition["kind"].upper() if disposition else "MERGED_PR")
        pr = route == "MERGED_PR"
        return TerminalFacts(
            RID, REPOSITORY, selected.issue, current.record_id,
            registered.contract_id, selected.branch_ref, self.base,
            route, self.human_decision_id, self.base, self.base,
            "refs/heads/main", self.product_preserved,
            self.product_ancestry_proven if pr else False,
            self.no_unpublished_work, self.session_stopped,
            self.writers_quiescent,
            pr_number=23 if pr else None,
            pr_state=self.pr_state if pr else None,
            pr_head_oid=self.base if pr else None,
            pr_merge_oid=self.base if pr else None,
            pr_base_ref="refs/heads/main" if pr else None,
            pr_repository_id=RID if pr else None,
            pr_repository=REPOSITORY if pr else None,
            pr_issue=selected.issue if pr else None,
            replacement_repository=(disposition["replacement"]["repository"]
                                    if disposition and route == "SUPERSEDED" else None),
            replacement_task=(disposition["replacement"]["task"]
                              if disposition and route == "SUPERSEDED" else None),
        )

    def authorize(self, request):
        self.grants.append(request)
        return self.granted

    def write(self, request, bundle, deadline):
        self.writes.append(request)
        saved = self.journal().read(_key(RID, self.approved.issue,
                                         request.expected_archive,
                                         request.expected_issue))
        self.saved_before_writer.append(saved)
        self.assertEqual(bundle.commit, saved["candidate"])
        self.assertEqual(request.expected_archive, saved["archive_before"])
        self.assertEqual(request.expected_issue, saved["issue_before"])
        self.assertEqual("archive-issue", saved["operation"])
        self.assertEqual(2, len(self._bundle_parents(bundle.commit, bundle)))
        return self.fixture.writer(request, bundle, deadline)

    def _bundle_parents(self, commit, bundle):
        raw = bundle.objects[commit][1]
        return [line for line in raw.split(b"\n\n", 1)[0].split(b"\n")
                if line.startswith(b"parent ")]

    def archive(self, request, *, approved=None, registered=None,
                writer=None, terminal=None, hold=None, authorize=None,
                reader=None, observer=None, journal=None, identity=None,
                repo_identity=None, limits=None):
        with gate.hold_verified(self.project, self.home, self.package) as lease:
            selected_identity = identity or BootstrapIdentity(
                RID, REPOSITORY, lease._fingerprint, "sha1")
            host = ArchiveHost(
                selected_identity, repo_identity or self.repository_identity,
                registered or self.registered, approved or self.approved,
                lease=lease, reader=reader or self.fixture.reader,
                observer=observer or self.fixture.observe,
                journal=journal or self.journal(),
                hold_quiescence=hold or self.hold,
                read_terminal=terminal or self.terminal,
                prior_attempts_cleared=lambda _request: self.prior_clear,
                authorize=authorize or self.authorize,
                writer=writer or self.write,
                **({"limits": limits} if limits is not None else {}),
            )
            return host.archive(request)

    def test_merged_pr_produces_exact_two_parent_j_and_no_source_delete(self):
        op = self.op()
        result = self.archive(op)
        self.assertEqual("PUBLISHED", result.state)
        self.assertEqual(op.expected_issue, result.issue_commit)
        self.assertEqual(self.observation(ARCHIVE_REF), result.archive_commit)
        self.assertEqual(op.expected_issue, self.observation(issue_ref("101")))
        v = self.fixture.store().validation()
        node = v.archive(result.archive_commit)
        self.assertEqual((op.expected_archive, op.expected_issue), node.parents)
        self.assertEqual(self.registered.record_id,
                         node.snapshot.pointers.get("101"))
        self.assertEqual(1, len(self.writes))
        self.assertEqual(2, len(self.grants))
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        self.assertEqual(0, self.holds)

    def test_two_separate_issues_archive_and_exact_old_oid_fresh_clone(self):
        first_request = self.op()
        first = self.archive(first_request)
        second_approved, second_reg, second_tip = self.register(202)
        second_request = ArchiveOperation(
            "b" * 32, first.archive_commit, second_tip,
            second_reg.record_id, "fixture-retention-only")
        old_approved, old_registered = self.approved, self.registered
        try:
            self.approved, self.registered = second_approved, second_reg
            second = self.archive(second_request)
        finally:
            self.approved, self.registered = old_approved, old_registered
        self.assertEqual("PUBLISHED", second.state)
        check = self.fixture.store().validation()
        last = check.archive(second.archive_commit)
        self.assertEqual((first.archive_commit, second_tip), last.parents)
        self.assertEqual({"101", "202"}, set(last.snapshot.pointers))
        self.assertIn(first.archive_commit, check.nodes)
        self.assertIn(first_request.expected_issue, check.nodes)
        self.assertEqual(first_request.expected_issue,
                         self.observation(issue_ref("101")))
        clone = self.root / "fresh.git"
        self.fixture.git("clone", "--bare", "--no-hardlinks",
                         str(self.fixture.bare), str(clone), bare=False)
        prior = self.fixture.bare
        try:
            self.fixture.bare = clone
            from metadata_v2 import MetadataV2
            restored = MetadataV2(REPOSITORY, self.fixture.genesis,
                                  self.fixture.reader, self.fixture.observe)
            record = restored.read_object(
                first_request.expected_issue, "task-record",
                self.registered.record_id, task="101", subject=self.base)
            self.assertEqual(self.registered.contract_id,
                             record["payload"]["contract_id"])
        finally:
            self.fixture.bare = prior

    def _retire(self, disposition):
        next_id, data = encode_record(
            REPOSITORY, "101", self.base,
            {"schema_version": 1,
             "branch_ref": self.approved.branch_ref,
             "contract_id": self.registered.contract_id,
             "disposition": disposition})
        previous = self.observation(issue_ref("101"))
        current = self.fixture.store().publish_issue(
            "101", [data], record_update=(self.registered.record_id, next_id))
        self.assertNotEqual(previous, current)
        return next_id

    def test_cancelled_and_superseded_with_human_and_product_proof(self):
        for disposition in (
            {"kind": "cancelled"},
            {"kind": "superseded",
             "replacement": {"repository": REPOSITORY, "task": "202"}},
        ):
            with self.subTest(disposition=disposition):
                # Separate full disposable remote for each path.
                t = ArchiveHostTest("runTest")
                t.setUp()
                try:
                    newer = t._retire(disposition)
                    outcome = t.archive(t.op(record_id=newer))
                    self.assertEqual("PUBLISHED", outcome.state)
                    self.assertEqual(newer, outcome.record_id)
                    self.assertEqual(disposition, t.fixture.store().read_object(
                        outcome.issue_commit, "task-record", newer,
                        task="101", subject=t.base)["payload"]["disposition"])
                finally:
                    t.doCleanups()

    def test_cancel_disposition_alone_does_not_admit_archive(self):
        new_record = self._retire({"kind": "cancelled"})
        self.session_stopped = False
        with self.assertRaises(ArchiveRefused):
            self.archive(self.op(record_id=new_record))
        self.assertEqual([], self.writes)
        self.assertEqual(0, len(list(self.jpath.iterdir())))

    def test_closed_unmerged_pr_refuses_without_journal_or_writer(self):
        self.pr_state = "CLOSED"
        with self.assertRaises(ArchiveRefused):
            self.archive(self.op())
        self.assertEqual([], self.writes)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_foreign_product_pr_repository_or_issue_refuses_before_effect(self):
        for change in ({"pr_repository_id": RID + 1},
                       {"pr_repository": "foreign/repo"},
                       {"pr_issue": 202},
                       {"pr_head_oid": "f" * 40},
                       {"pr_base_ref": "refs/heads/other"}):
            with self.subTest(change=change):
                def foreign(op, current):
                    return replace(self.terminal(op, current), **change)
                with self.assertRaises(ArchiveRefused):
                    self.archive(self.op(), terminal=foreign)
                self.assertFalse(list(self.jpath.iterdir()))
                self.assertEqual([], self.writes)

    def test_denied_human_grant_refuses_before_intent(self):
        self.granted = False
        with self.assertRaises(ArchiveRefused):
            self.archive(self.op())
        self.assertEqual([], self.writes)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_unknown_session_writer_or_unique_product_work_refuses(self):
        for field in ("session_stopped", "writers_quiescent",
                      "no_unpublished_work", "product_preserved",
                      "product_ancestry_proven"):
            with self.subTest(field=field):
                old = getattr(self, field)
                setattr(self, field, False)
                try:
                    with self.assertRaises(ArchiveRefused):
                        self.archive(self.op())
                finally:
                    setattr(self, field, old)
                self.assertEqual([], self.writes)
                self.assertFalse(list(self.jpath.iterdir()))

    def test_lost_ack_after_archive_effect_recovers_original_j(self):
        op = self.op()
        def applied_lost(update, bundle, deadline):
            self.write(update, bundle, deadline)
            raise TimeoutError("network Ack lost")
        with self.assertRaises(ArchiveUncertain):
            self.archive(op, writer=applied_lost)
        self.assertEqual(1, len(self.writes))
        winner = self.observation(ARCHIVE_REF)
        recovered = self.archive(op)
        self.assertEqual("RECOVERED", recovered.state)
        self.assertEqual(winner, recovered.archive_commit)
        self.assertEqual(1, len(self.writes))
        self.assertEqual(op.expected_issue, self.observation(issue_ref("101")))

    def test_lost_ack_before_effect_never_retries_writer(self):
        op = self.op()
        calls = []
        def timeout(update, bundle, deadline):
            calls.append(update)
            raise TimeoutError("effect unknown")
        with self.assertRaises(ArchiveUncertain):
            self.archive(op, writer=timeout)
        self.assertEqual(self.fixture.genesis, self.observation(ARCHIVE_REF))
        with self.assertRaises(ArchiveUncertain):
            self.archive(op, writer=timeout)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(list(self.jpath.iterdir())))

    def test_false_ack_preserves_original_and_never_blind_retries(self):
        op = self.op()
        calls = []
        def refused(update, bundle, deadline):
            calls.append(update)
            return False
        with self.assertRaises(ArchiveUncertain):
            self.archive(op, writer=refused)
        with self.assertRaises(ArchiveUncertain):
            self.archive(op, writer=refused)
        self.assertEqual(1, len(calls))

    def test_same_a_c_competing_operation_ids_collide(self):
        op = self.op()
        self.archive(op)
        other = replace(op, operation_id="b" * 32)
        with self.assertRaises(ArchiveRefused):
            self.archive(other)
        self.assertEqual(1, len(self.writes))

    def test_concurrent_same_a_c_host_writers_cannot_both_publish(self):
        op = self.op()
        other = replace(op, operation_id="c" * 32)
        barrier = threading.Barrier(2)

        def concurrent_grant(approval):
            barrier.wait(timeout=8)
            return True

        with gate.hold_verified(self.project, self.home, self.package) as lease:
            identity = BootstrapIdentity(RID, REPOSITORY, lease._fingerprint)
            def make_host():
                return ArchiveHost(
                    identity, self.repository_identity, self.registered, self.approved,
                    lease=lease, reader=self.fixture.reader,
                    observer=self.fixture.observe, journal=self.journal(),
                    hold_quiescence=self.hold, read_terminal=self.terminal,
                    prior_attempts_cleared=lambda _request: self.prior_clear,
                    authorize=concurrent_grant, writer=self.write)
            one = make_host()
            two = make_host()
            with ThreadPoolExecutor(max_workers=2) as pool:
                attempts = (pool.submit(one.archive, op),
                            pool.submit(two.archive, other))
                success, rejected = [], []
                for future in attempts:
                    try:
                        success.append(future.result(timeout=25))
                    except (ArchiveRefused, ArchiveUncertain) as error:
                        rejected.append(error)
        self.assertEqual(1, len(success))
        self.assertEqual(1, len(rejected))
        self.assertEqual(1, len(self.writes))
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        self.assertEqual(success[0].archive_commit, self.observation(ARCHIVE_REF))

    def test_stale_archive_cas_race_refuses_and_retains_issue_ref(self):
        op = self.op()
        second_approved, second_reg, _ = self.register(202)
        def race(update, bundle, deadline):
            self.writes.append(update)
            archive_issue(self.fixture.store(), "202",
                          retention_policy="synthetic",
                          verify_terminal=lambda *_: True,
                          archive_writer=self.fixture.writer)
            return self.fixture.writer(update, bundle, deadline)
        with self.assertRaises(ArchiveUncertain):
            self.archive(op, writer=race)
        self.assertEqual(op.expected_issue, self.observation(issue_ref("101")))
        self.assertNotEqual(op.expected_archive, self.observation(ARCHIVE_REF))
        self.assertEqual(1, len(self.writes))
        with self.assertRaises(ArchiveUncertain):
            self.archive(op)
        self.assertEqual(1, len(self.writes))
        # A separately approved new A' attempt is possible only after the
        # trusted Host proves the old candidate was not published.
        current_archive = self.observation(ARCHIVE_REF)
        self.assertNotEqual(current_archive, op.expected_archive)
        fresh = replace(op, expected_archive=current_archive, operation_id="c" * 32)
        # Unknown old ACK must not grant a fresh attempt merely because
        # A' is visible. The trusted Host must positively reconcile the
        # durable original J as known NOT published before a fresh grant.
        self.prior_clear = False
        with self.assertRaises(ArchiveRefused):
            self.archive(fresh)
        self.assertEqual(1, len(self.writes))
        self.prior_clear = True
        later = self.archive(fresh)
        self.assertEqual("PUBLISHED", later.state)
        self.assertEqual((current_archive, op.expected_issue),
                         self.fixture.store().validation().archive(
                             later.archive_commit).parents)

    def test_j_reconciled_as_valid_ancestor_after_other_issue_archive(self):
        op = self.op()
        winner = self.archive(op)
        _, _, other_tip = self.register(202)
        archive_issue(self.fixture.store(), "202",
                      retention_policy="fixture-only",
                      verify_terminal=lambda *_: True,
                      archive_writer=self.fixture.writer)
        self.assertNotEqual(winner.archive_commit, self.observation(ARCHIVE_REF))
        self.assertEqual("RECOVERED", self.archive(op).state)
        self.assertEqual(1, len(self.writes))

    def test_source_issue_changes_after_j_before_replay_stops(self):
        from metadata_v2 import construct
        op = self.op()
        self.archive(op)
        obj_id, extra = self.evidence_fixture()
        import metadata_codec as codec
        v = self.fixture.store().validation()
        old = v.issue(op.expected_issue, "101")
        files = dict(old.snapshot.files)
        files[codec.object_path("evidence", obj_id)] = extra
        moved = construct(files, (op.expected_issue,), nonce="foreign-ref-drift")
        self.fixture.install(moved)
        self.fixture.set_ref(issue_ref("101"), moved.commit, op.expected_issue)
        with self.assertRaises(ArchiveUncertain):
            self.archive(op)
        self.assertEqual(1, len(self.writes))

    def evidence_fixture(self):
        import metadata_codec as codec
        payload = {"schema_version": 1, "kind": "structured",
                   "producer": {"name": "fixture"},
                   "created_at": "2026-11-04T00:00:00Z",
                   "payload": {"note": "after archive"}}
        return codec.encode_object("evidence", REPOSITORY, "101", self.base, payload)

    def test_corrupt_journal_and_missing_source_never_write_twice(self):
        op = self.op()
        self.archive(op)
        key = _key(RID, 101, op.expected_archive, op.expected_issue)
        (self.jpath / key).write_bytes(b"truncated invalid intent")
        with self.assertRaises((ArchiveRefused, ArchiveUncertain)):
            self.archive(op)
        self.assertEqual(1, len(self.writes))

    def test_expired_or_foreign_host_identity_lease_refuses(self):
        op = self.op()
        self.lock_valid = False
        with self.assertRaises(ArchiveRefused):
            self.archive(op)
        self.lock_valid = True
        with gate.hold_verified(self.project, self.home, self.package) as lease:
            wrong = BootstrapIdentity(RID + 1, REPOSITORY, lease._fingerprint)
        with self.assertRaises(ArchiveRefused):
            self.archive(op, identity=wrong)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_symbolic_ref_no_effect_and_wrong_record(self):
        op = self.op()
        wrong = replace(op, record_id="f" * 64)
        with self.assertRaises(ArchiveRefused):
            self.archive(wrong)
        ref = issue_ref("101")
        def symbolic(name, deadline):
            state = self.fixture.observe(name, deadline)
            if name == ref:
                return RefState(ref, state.commit, False)
            return state
        with self.assertRaises(ArchiveRefused):
            self.archive(op, observer=symbolic)
        self.assertEqual([], self.writes)

    def test_quiescence_lost_during_human_grant_refuses_before_journal(self):
        op = self.op()
        def revoke(_request):
            self.lock_valid = False
            return True
        with self.assertRaises(ArchiveRefused):
            self.archive(op, authorize=revoke)
        self.assertEqual([], self.writes)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_terminal_facts_change_during_grant_refuses_no_effect(self):
        op = self.op()
        def change_facts(_request):
            self.session_stopped = False
            return True
        with self.assertRaises(ArchiveRefused):
            self.archive(op, authorize=change_facts)
        self.assertEqual([], self.writes)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_deadline_expires_in_human_authorization_without_ref_effect(self):
        from metadata_v2 import Limits
        op = self.op()
        def slow(_request):
            time.sleep(1.7)
            return True
        with self.assertRaises(ArchiveRefused):
            self.archive(op, authorize=slow, limits=Limits(seconds=1.5))
        self.assertEqual([], self.writes)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_deadline_expires_during_durable_journal_no_effect_or_retry(self):
        from metadata_v2 import Limits
        op = self.op()
        journal = self.journal()
        original = journal.create
        def slow_create(key, record):
            original(key, record)
            time.sleep(1.7)
        journal.create = slow_create
        with self.assertRaises(ArchiveUncertain):
            self.archive(op, journal=journal, limits=Limits(seconds=1.5))
        self.assertEqual([], self.writes)
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        with self.assertRaises(ArchiveUncertain):
            self.archive(op)
        self.assertEqual([], self.writes)

    def test_quiescence_lost_during_final_readback_is_uncertain_not_published(self):
        op = self.op()
        original = ArchiveHost._confirm
        def revoke_after_readback(host, attempt, candidate, *, state, facts=None):
            receipt = original(host, attempt, candidate, state=state, facts=facts)
            self.lock_valid = False
            return receipt
        with patch.object(ArchiveHost, "_confirm", revoke_after_readback):
            with self.assertRaisesRegex(ArchiveUncertain, "post_effect"):
                self.archive(op)
        self.assertEqual(1, len(self.writes))
        self.lock_valid = True
        result = self.archive(op)
        self.assertEqual("RECOVERED", result.state)
        self.assertEqual(1, len(self.writes))

    def test_post_ack_terminal_fact_change_is_uncertain_then_readback_recovers(self):
        op = self.op()
        def changed_after_write(update, bundle, deadline):
            self.write(update, bundle, deadline)
            self.session_stopped = False
            return True
        with self.assertRaises(ArchiveUncertain):
            self.archive(op, writer=changed_after_write)
        self.assertEqual(1, len(self.writes))
        recovered = self.archive(op)
        self.assertEqual("RECOVERED", recovered.state)
        self.assertEqual(1, len(self.writes))

    def test_wrong_original_record_after_disposition_update_refuses(self):
        original_op = self.op()
        self._retire({"kind": "cancelled"})
        with self.assertRaises(ArchiveRefused):
            self.archive(original_op)
        self.assertEqual([], self.writes)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_current_issue_head_advanced_before_archive_refuses(self):
        original_op = self.op()
        _, extra = self.evidence_fixture()
        self.fixture.store().publish_issue("101", [extra])
        with self.assertRaises(ArchiveRefused):
            self.archive(original_op)
        self.assertEqual([], self.writes)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_writer_with_wrong_identity_ref_is_not_promoted(self):
        op = self.op()
        def bad_writer(update, bundle, deadline):
            self.writes.append(update)
            return False
        with self.assertRaises(ArchiveUncertain):
            self.archive(op, writer=bad_writer)
        self.assertEqual(self.fixture.genesis, self.observation(ARCHIVE_REF))
        self.assertEqual(op.expected_issue, self.observation(issue_ref("101")))
        self.assertEqual(1, len(self.writes))

    def test_product_head_index_untracked_and_v1_ref_unmodified(self):
        self.fixture.set_ref("refs/agentcore/metadata", self.fixture.genesis)
        (self.project / "README.md").write_text("staged\n")
        self.attached.git("add", "README.md")
        (self.project / "README.md").write_text("dirty-owner-data\n")
        (self.project / "owner.txt").write_text("keep\n")
        before = (self.attached.git("rev-parse", "HEAD"),
                  self.attached.git("status", "--porcelain=v1", "-uall"),
                  (self.project / ".git/index").read_bytes())
        v1 = self.observation("refs/agentcore/metadata")
        self.archive(self.op())
        self.assertEqual(before[0], self.attached.git("rev-parse", "HEAD"))
        self.assertEqual(before[1], self.attached.git("status", "--porcelain=v1", "-uall"))
        self.assertEqual(before[2], (self.project / ".git/index").read_bytes())
        self.assertEqual("dirty-owner-data\n",
                         (self.project / "README.md").read_text())
        self.assertEqual("keep\n", (self.project / "owner.txt").read_text())
        self.assertEqual(v1, self.observation("refs/agentcore/metadata"))


if __name__ == "__main__":
    unittest.main()
