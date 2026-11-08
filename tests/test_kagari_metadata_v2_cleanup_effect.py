"""#337 P3C/B1-B: disposable local bare Git exact-OID delete tests only.

No GitHub credentials, requests, remote push, real Task, model, deployment,
activation, or metadata refs outside the temporary synthetic fixture.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "components/agent-core-v4")]

from kagari_metadata_v2_cleanup_effect import (
    CleanupEffectHost, CleanupEffectRefused, CleanupEffectUncertain,
    EffectApproval, ExactDeleteRequest, _effect_key, _confirmation_key,
)
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import ARCHIVE_REF, construct, issue_ref
from tests import test_kagari_metadata_v2_cleanup_intent as b1a_fixture


class CleanupEffectHostTest(unittest.TestCase):
    def setUp(self):
        self.b1a = b1a_fixture.CleanupIntentHostTest("runTest")
        self.b1a.setUp()
        self.addCleanup(self.b1a.doCleanups)
        self.case = self.b1a.case
        self.b1a.prepare()
        self.ref = issue_ref("101")
        self.original_c = self.b1a.issue_c
        self.archive_j = self.b1a.archive_j
        self.initial_writers = len(self.case.writes)
        self.calls = []
        self.approvals = []
        self.marker_key = _effect_key(
            424242, 101, self.original_c)
        self.confirm_key = _confirmation_key(
            424242, 101, self.original_c)
        self.assertEqual(3, len(list(self.case.jpath.iterdir())))

    def delete_local_exact(self, request):
        """Atomic exact-old-C delete in a TEMPORARY local bare Git fixture."""
        self.calls.append(request)
        self.assertIsInstance(request, ExactDeleteRequest)
        self.assertEqual(self.ref, request.ref)
        self.assertEqual(self.original_c, request.expected_old)
        self.assertIs(request.require_direct, True)
        self.assertEqual(self.archive_j, request.archive_j)
        self.assertEqual("refs/heads/kagari-metadata/101", request.ref)
        self.case.fixture.git(
            "update-ref", "--no-deref", "-d",
            request.ref, request.expected_old)
        return True

    def authorize(self, approval):
        self.approvals.append(approval)
        self.assertIsInstance(approval, EffectApproval)
        return self.case.granted

    @contextmanager
    def host(self, *, deleter=None, grant=None, terminal=None,
             hold=None, prior=None):
        with self.b1a.host(terminal=terminal, hold=hold, prior=prior) as intent:
            yield CleanupEffectHost(
                intent, authorize_effect=grant or self.authorize,
                delete_exact=deleter or self.delete_local_exact)

    def execute(self, **kwargs):
        with self.host(**kwargs) as host:
            return host.execute_once(self.b1a.op, self.b1a.archived)

    def inspect(self, **kwargs):
        with self.host(**kwargs) as host:
            return host.inspect(self.b1a.op, self.b1a.archived)

    def receipt_count(self):
        return len(list(self.case.jpath.iterdir()))

    def test_one_exact_local_CAS_delete_and_durable_positive_readback(self):
        result = self.execute()
        self.assertEqual("ACKED_ABSENT_ARCHIVED", result.state)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(self.approvals))
        self.assertEqual(5, self.receipt_count()) # Archive, reserve, intent, marker, confirmation
        self.assertEqual(self.initial_writers, len(self.case.writes))
        self.assertIsNone(self.case.fixture.observe(
            self.ref, __import__("time").monotonic()+3).commit)
        self.assertEqual(self.archive_j, self.case.observation(ARCHIVE_REF))
        self.assertEqual("ACKED_ABSENT_ARCHIVED", self.inspect().state)
        marker = self.case.journal().read(self.marker_key)
        self.assertEqual("MAY_HAVE_EXECUTED", marker["state"])
        self.assertEqual("ACKED_EXACT_AND_REF_ABSENT_ARCHIVED",
                         self.case.journal().read(self.confirm_key)["state"])

    def test_second_effect_refused_even_if_Host_facts_and_grant_valid(self):
        self.execute()
        count = self.receipt_count()
        with self.assertRaisesRegex(
                CleanupEffectRefused, "existing_attempt_no_retry"):
            self.execute()
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(self.approvals))
        self.assertEqual(count, self.receipt_count())

    def test_intent_alone_never_authorizes_delete(self):
        self.assertEqual("INTENT_ONLY_REF_PRESENT", self.inspect().state)
        self.assertFalse((self.case.jpath / self.marker_key).exists())
        self.assertEqual(0, len(self.calls))

    def test_effect_Human_denial_no_marker(self):
        self.case.granted = False
        with self.assertRaisesRegex(CleanupEffectRefused, "fresh_Human_denied"):
            self.execute()
        self.assertEqual(3, self.receipt_count())
        self.assertEqual(0, len(self.calls))

    def test_session_running_or_writers_active_refused_before_marker(self):
        for field in ("session_stopped", "writers_quiescent"):
            with self.subTest(field=field):
                setattr(self.case, field, False)
                with self.assertRaises(CleanupEffectRefused):
                    self.execute()
                self.assertEqual(3, self.receipt_count())
                setattr(self.case, field, True)
        self.assertEqual(0, len(self.calls))

    def test_prior_attempt_unclear_refused_before_marker(self):
        self.case.prior_clear = False
        with self.assertRaises(CleanupEffectRefused):
            self.execute()
        self.assertEqual(3, self.receipt_count())

    def test_lost_quiescence_refused_before_marker(self):
        self.case.lock_valid = False
        with self.assertRaises(CleanupEffectRefused):
            self.execute()
        self.assertEqual(3, self.receipt_count())

    def test_issue_ref_already_absent_without_marker_is_unattributed(self):
        self.case.fixture.git(
            "update-ref", "--no-deref", "-d", self.ref, self.original_c)
        self.assertEqual("INTENT_ONLY_REF_ABSENT_UNATTRIBUTED",
                         self.inspect().state)
        with self.assertRaisesRegex(CleanupEffectRefused, "ref_not_exact_present"):
            self.execute()
        self.assertEqual(3, self.receipt_count())
        self.assertEqual(0, len(self.calls))

    def test_changed_current_issue_ref_refuses_before_marker(self):
        original = self.case.fixture.store().validation().issue(
            self.original_c, "101")
        moved = construct(dict(original.snapshot.files),
                          (self.original_c,), nonce="b1b-foreign-issue-advance")
        self.case.fixture.install(moved)
        self.case.fixture.set_ref(self.ref, moved.commit, self.original_c)
        with self.assertRaises(CleanupEffectRefused):
            self.execute()
        self.assertEqual(0, len(self.calls))
        self.assertEqual(3, self.receipt_count())

    def test_unknown_ack_after_local_CAS_preserves_marker_no_retry(self):
        def apply_then_lost_ack(req):
            self.delete_local_exact(req)
            raise TimeoutError("synthetic Git transport lost ACK")
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(deleter=apply_then_lost_ack)
        self.assertEqual(4, self.receipt_count()) # no confirmation
        self.assertEqual("ATTEMPT_UNCERTAIN_REF_ABSENT_UNATTRIBUTED",
                         self.inspect().state)
        with self.assertRaisesRegex(
                CleanupEffectRefused, "existing_attempt_no_retry"):
            self.execute()
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(self.approvals))

    def test_unknown_ack_without_effect_preserves_marker_no_retry(self):
        def fail_before_effect(_request):
            self.calls.append(_request)
            raise TimeoutError("synthetic no response before delete")
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(deleter=fail_before_effect)
        self.assertEqual(self.original_c, self.case.observation(self.ref))
        self.assertEqual("ATTEMPT_UNCERTAIN_REF_PRESENT", self.inspect().state)
        with self.assertRaises(CleanupEffectRefused):
            self.execute()
        self.assertEqual(1, len(self.calls))
        self.assertEqual(4, self.receipt_count())

    def test_marker_create_then_ACK_unknown_must_never_call_deleter(self):
        original = RecoveryJournal.create
        def write_marker_then_lose_ack(journal, key, value):
            original(journal, key, value)
            if value["operation"] == "cleanup-issue-effect-attempt":
                raise TimeoutError("synthetic marker ACK lost")
        with patch.object(RecoveryJournal, "create", write_marker_then_lose_ack):
            with self.assertRaises(CleanupEffectUncertain):
                self.execute()
        self.assertEqual(4, self.receipt_count())
        self.assertEqual(0, len(self.calls))
        self.assertEqual("ATTEMPT_UNCERTAIN_REF_PRESENT", self.inspect().state)
        with self.assertRaises(CleanupEffectRefused):
            self.execute()
        self.assertEqual(0, len(self.calls))

    def test_marker_write_not_visible_no_remote_effect(self):
        def fail_before_marker(journal, key, value):
            if value["operation"] == "cleanup-issue-effect-attempt":
                raise TimeoutError("synthetic before marker file creation")
            raise AssertionError("unexpected operation")
        with patch.object(RecoveryJournal, "create", fail_before_marker):
            with self.assertRaises(CleanupEffectUncertain):
                self.execute()
        self.assertEqual(3, self.receipt_count())
        self.assertEqual("INTENT_ONLY_REF_PRESENT", self.inspect().state)
        self.assertEqual(0, len(self.calls))

    def test_prior_clearance_revoked_during_marker_fsync_stops_deleter(self):
        original = RecoveryJournal.create
        def marker_then_revoke_prior(journal, key, value):
            result = original(journal, key, value)
            if value["operation"] == "cleanup-issue-effect-attempt":
                self.case.prior_clear = False
            return result
        with patch.object(RecoveryJournal, "create", marker_then_revoke_prior):
            with self.assertRaises(CleanupEffectUncertain):
                self.execute()
        self.assertEqual(4, self.receipt_count())  # attempt marker retained
        self.assertEqual(0, len(self.calls))        # NO Git delete callback
        self.assertEqual(self.original_c, self.case.observation(self.ref))
        self.assertEqual("ATTEMPT_UNCERTAIN_REF_PRESENT", self.inspect().state)
        with self.assertRaises(CleanupEffectRefused):
            self.execute()
        self.assertEqual(0, len(self.calls))

    def test_session_resume_after_delete_prevents_positive_confirmation(self):
        def exact_delete_then_resume(request):
            self.delete_local_exact(request)
            self.case.session_stopped = False
            return True
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(deleter=exact_delete_then_resume)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(4, self.receipt_count())
        self.assertFalse((self.case.jpath / self.confirm_key).exists())
        self.assertEqual("ATTEMPT_UNCERTAIN_REF_ABSENT_UNATTRIBUTED",
                         self.inspect().state)
        with self.assertRaises(CleanupEffectRefused):
            self.execute()

    def test_writer_wakes_during_confirmation_create_reports_uncertain(self):
        original = RecoveryJournal.create
        def confirm_then_start_writer(journal, key, value):
            result = original(journal, key, value)
            if value["operation"] == "cleanup-issue-effect-confirmation":
                self.case.writers_quiescent = False
            return result
        with patch.object(RecoveryJournal, "create", confirm_then_start_writer):
            with self.assertRaises(CleanupEffectUncertain):
                self.execute()
        self.assertEqual(1, len(self.calls))
        self.assertEqual(5, self.receipt_count())
        # The durable confirmation reflects ACK+Archive+absent C at the time
        # of the write, but effect execution cannot now claim live authority.
        self.assertEqual("ACKED_ABSENT_ARCHIVED", self.inspect().state)

    def test_prior_clearance_lapses_during_final_predelete_fact_read(self):
        reads = 0
        def terminal(op, current):
            nonlocal reads
            reads += 1
            facts = self.case.terminal(op, current)
            # 1=pre-grant, 2=pre-marker, 3=post-marker, before
            # final effect-time prior clearance/lease fence.
            if reads == 3:
                self.case.prior_clear = False
            return facts
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(terminal=terminal)
        self.assertEqual(0, len(self.calls))
        self.assertEqual(4, self.receipt_count())
        self.assertEqual("ATTEMPT_UNCERTAIN_REF_PRESENT", self.inspect().state)

    def test_prior_clearance_lapses_during_preconfirmation_fact_read(self):
        reads = 0
        def terminal(op, current):
            nonlocal reads
            reads += 1
            facts = self.case.terminal(op, current)
            # 4=post-delete, 5=before confirmation journal effect.
            if reads == 5:
                self.case.prior_clear = False
            return facts
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(terminal=terminal)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(4, self.receipt_count())
        self.assertFalse((self.case.jpath/self.confirm_key).exists())
        self.assertEqual("ATTEMPT_UNCERTAIN_REF_ABSENT_UNATTRIBUTED",
                         self.inspect().state)

    def test_final_terminal_read_loses_lease_before_return(self):
        reads = 0
        def terminal(op, current):
            nonlocal reads
            reads += 1
            facts = self.case.terminal(op, current)
            # 6th (last) facts read after durable confirmation.
            if reads == 6:
                self.case.lock_valid = False
            return facts
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(terminal=terminal)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(5, self.receipt_count())
        # Historical ACK and archived J are still attested by the durable
        # receipt. Live Host permission has just lapsed, so no success return.
        self.assertEqual("ACKED_ABSENT_ARCHIVED", self.inspect().state)

    def test_positive_ACK_but_ref_still_present_remains_uncertain(self):
        def misreported_ACK(req):
            self.calls.append(req)
            return True
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(deleter=misreported_ACK)
        self.assertEqual("ATTEMPT_UNCERTAIN_REF_PRESENT", self.inspect().state)
        self.assertEqual(4, self.receipt_count())

    def test_wrong_old_oid_rejects_local_CAS_even_after_marker(self):
        original = self.case.fixture.store().validation().issue(
            self.original_c, "101")
        moved = construct(dict(original.snapshot.files),
                          (self.original_c,), nonce="b1b-race-during-transport")
        self.case.fixture.install(moved)
        def move_then_CAS(req):
            self.case.fixture.set_ref(self.ref, moved.commit, req.expected_old)
            self.delete_local_exact(req)
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(deleter=move_then_CAS)
        self.assertEqual(moved.commit, self.case.observation(self.ref))
        self.assertEqual(1, len(self.calls))
        with self.assertRaises(CleanupEffectRefused):
            self.inspect()
        self.assertEqual(4, self.receipt_count())

    def test_archive_rewind_after_exact_delete_stops_confirmation(self):
        def delete_then_rewind(req):
            self.delete_local_exact(req)
            self.case.fixture.set_ref(
                ARCHIVE_REF, self.case.fixture.genesis, self.archive_j)
            return True
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(deleter=delete_then_rewind)
        self.assertEqual(4, self.receipt_count())
        with self.assertRaises(CleanupEffectRefused):
            self.inspect()

    def test_corrupted_attempt_journal_fails_closed(self):
        def fail_delete(_req):
            raise TimeoutError()
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(deleter=fail_delete)
        p = self.case.jpath / self.marker_key
        old = p.read_bytes()
        try:
            for field, value in (
                ("ref", "refs/heads/kagari/metadata"),
                ("expected_old", "f" * 40),
                ("state", "NOT_ATTEMPTED"),
                ("require_direct", False),
                ("human_decision_id", True),
                ("current_terminal_digest", "bad"),
            ):
                with self.subTest(field=field):
                    data=json.loads(old)
                    data[field]=value
                    p.write_bytes(json.dumps(
                        data,sort_keys=True,separators=(",",":"),
                        ensure_ascii=True).encode("ascii"))
                    with self.assertRaises(CleanupEffectRefused):
                        self.inspect()
        finally:
            p.write_bytes(old)
        self.assertEqual("ATTEMPT_UNCERTAIN_REF_PRESENT", self.inspect().state)

    def test_corrupt_confirmation_fails_closed(self):
        self.execute()
        p=self.case.jpath/self.confirm_key
        old=p.read_bytes()
        try:
            val=json.loads(old)
            val["attempt_digest"]="f"*64
            p.write_bytes(json.dumps(
                val,sort_keys=True,separators=(",",":"),
                ensure_ascii=True).encode("ascii"))
            with self.assertRaises(CleanupEffectRefused):
                self.inspect()
        finally:
            p.write_bytes(old)
        self.assertEqual("ACKED_ABSENT_ARCHIVED", self.inspect().state)

    def test_effect_interruption_is_uncertain_and_marker_stays(self):
        def interrupted(req):
            self.calls.append(req)
            raise KeyboardInterrupt()
        with self.assertRaises(CleanupEffectUncertain):
            self.execute(deleter=interrupted)
        self.assertEqual(4, self.receipt_count())
        self.assertEqual("ATTEMPT_UNCERTAIN_REF_PRESENT", self.inspect().state)

    def test_no_prepared_B1A_intent_denies_any_effect(self):
        for path in list(self.case.jpath.iterdir()):
            # Synthetic only: preserve existing immutable Archive journal,
            # remove test-created B1A local files, never production metadata.
            if path.name not in {self.marker_key, self.confirm_key}:
                record=self.case.journal().read(path.name)
                if record["operation"].startswith("cleanup-"):
                    path.unlink()
        with self.assertRaisesRegex(
                CleanupEffectRefused, "requires_durable_intent"):
            self.execute()
        self.assertEqual(0, len(self.calls))


if __name__ == "__main__":
    unittest.main()
