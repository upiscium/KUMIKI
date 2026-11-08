"""#337 P3C/B1: disposable bare-Git exact-lease cleanup Host tests.

No authenticated GitHub, live KUMIKI Task, production branch, deployment or
model; only a local temporary bare repository and a stub trusted Host.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import json
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "components/agent-core-v4")]

import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_archive_host import CurrentTerminalRecord
from kagari_metadata_v2_cleanup_host import (
    CleanupHost, CleanupOperation, CleanupQuiescenceLease, CleanupHumanGrantLease,
    CleanupHumanGrant, CleanupRefused, CleanupUncertain, _key,
)
from metadata_v2 import ARCHIVE_REF, issue_ref
from tests import test_kagari_metadata_v2_cleanup_history as history_fixture


class CleanupHostTest(unittest.TestCase):
    def setUp(self):
        self.source = history_fixture.CleanupHistoryReaderTest("runTest")
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.case = self.source.case
        self.op = CleanupOperation("b" * 32, self.source.op, self.source.result)
        self.lock_valid = True
        self.granted = True
        self.prior_clear = True
        self.current_valid = True
        self.effect = "delete"
        self.delete_calls = []
        self.grant_calls = 0
        self.holds = 0
        self.grant_holds = 0
        self.grant_mutex = threading.Lock()
        self.journal_before_effect = []

    @contextmanager
    def hold(self, op):
        self.holds += 1
        try:
            yield CleanupQuiescenceLease(
                lambda candidate: self.lock_valid and candidate == op and self.holds == 1)
        finally:
            self.holds -= 1

    @contextmanager
    def hold_grant(self, approval):
        # Synthetic synchronization domain shared with Human revocation.
        with self.grant_mutex:
            self.grant_holds += 1
            try:
                yield CleanupHumanGrantLease(
                    lambda checked_approval, grant: self.granted
                    and self.grant_holds == 1 and checked_approval == approval
                    and grant.decision_id == "f" * 64)
            finally:
                self.grant_holds -= 1

    def terminal(self, op, history):
        facts = self.case.terminal(
            op.archive_operation,
            CurrentTerminalRecord(
                op.archive_operation.expected_archive,
                op.archive_operation.expected_issue,
                self.case.registered.record_id,
                self.case.registered.contract_id,
                None))
        if not self.current_valid:
            return replace(facts, session_stopped=False)
        return facts

    def authorize(self, approval):
        self.grant_calls += 1
        if not self.granted:
            return None
        req = approval.delete_request
        return CleanupHumanGrant(
            "DELETE_ISSUE_METADATA_REF", "f" * 64,
            req.cleanup_id, req.repository_id, req.issue, req.ref,
            req.expected_old, req.archived_j)

    def delete(self, req):
        self.assertEqual(1, self.holds)
        self.assertEqual(1, self.grant_holds)  # held across effect
        self.assertFalse(self.grant_mutex.acquire(blocking=False))
        self.delete_calls.append(req)
        self.assertEqual(issue_ref("101"), req.ref)
        self.assertEqual(self.op.archive_operation.expected_issue, req.expected_old)
        self.assertEqual(self.op.archive_outcome.archive_commit, req.archived_j)
        key = _key(req.repository_id, req.issue, req.expected_old, req.archived_j)
        from kagari_vnext_trusted_host import RecoveryJournal
        intent = RecoveryJournal(self.case.jpath).read(key)
        self.assertEqual("cleanup-issue-ref", intent["operation"])
        self.assertEqual(req.expected_old, intent["issue_before"])
        self.assertEqual("f" * 64, intent["decision_id"])
        self.journal_before_effect.append(intent)
        if self.effect == "no-effect-error":
            raise TimeoutError("unknown ACK before effect")
        if self.effect == "positive-without-effect":
            return True
        if self.effect == "moved":
            self.case.fixture.set_ref(req.ref, self.case.fixture.genesis, req.expected_old)
            raise TimeoutError("unknown ACK after concurrent move")
        self.case.fixture.git("update-ref", "--no-deref", "-d", req.ref, req.expected_old)
        if self.effect == "delete-then-error":
            raise TimeoutError("lost ACK after effect")
        return True

    # Reuse the original Archive journal directory, with a distinct key
    # and operation namespace for the separate cleanup intent.
    def run_cleanup(self, *, observer=None, authorize=None, delete=None,
                    terminal=None, hold=None, grant_hold=None, op=None):
        selected = op or self.op
        history = self.source.reader(observer=observer)
        with gate.hold_verified(self.case.project, self.case.home, self.case.package) as lease:
            host = CleanupHost(
                history, lease=lease, journal=history.journal,
                hold_quiescence=hold or self.hold,
                hold_human_grant=grant_hold or self.hold_grant,
                read_terminal=terminal or self.terminal,
                prior_attempts_cleared=lambda _op: self.prior_clear,
                authorize=authorize or self.authorize,
                delete=delete or self.delete)
            return host.cleanup(selected)

    def local_intent(self, op=None):
        from kagari_vnext_trusted_host import RecoveryJournal
        selected = op or self.op
        key = _key(self.case.repository_identity.repository_id,
                   self.case.registered.issue,
                   selected.archive_operation.expected_issue,
                   selected.archive_outcome.archive_commit)
        return RecoveryJournal(self.case.jpath).read(key)

    def test_exact_delete_fsynced_intent_and_repeat_readonly(self):
        result = self.run_cleanup()
        self.assertEqual("DELETED", result.state)
        self.assertIsNone(self.case.observation(issue_ref("101")))
        self.assertEqual(1, len(self.delete_calls))
        self.assertEqual("cleanup-issue-ref", self.local_intent()["operation"])
        self.assertEqual("DELETED", self.run_cleanup().state)
        self.assertEqual(1, len(self.delete_calls))
        self.assertEqual(2, self.grant_calls)

    def test_deleted_outside_without_intent_cannot_claim_own_cleanup(self):
        self.case.fixture.git("update-ref", "--no-deref", "-d",
                              issue_ref("101"), self.op.archive_operation.expected_issue)
        with self.assertRaisesRegex(CleanupRefused, "not_present"):
            self.run_cleanup()
        self.assertIsNone(self.local_intent())
        self.assertEqual([], self.delete_calls)

    def test_denied_current_terminality_does_not_write_intent(self):
        self.current_valid = False
        with self.assertRaisesRegex(CleanupRefused, "current_terminal"):
            self.run_cleanup()
        self.assertIsNone(self.local_intent())
        self.assertEqual([], self.delete_calls)

    def test_missing_fresh_cleanup_grant_does_not_write_intent(self):
        self.granted = False
        with self.assertRaisesRegex(CleanupRefused, "fresh_human_grant"):
            self.run_cleanup()
        self.assertIsNone(self.local_intent())
        self.assertEqual([], self.delete_calls)

    def test_lost_quiescence_or_unresolved_prior_attempt_blocks(self):
        self.lock_valid = False
        with self.assertRaisesRegex(CleanupRefused, "lock_lost"):
            self.run_cleanup()
        self.lock_valid = True
        self.prior_clear = False
        with self.assertRaisesRegex(CleanupRefused, "prior_attempts_unresolved"):
            self.run_cleanup()
        self.assertIsNone(self.local_intent())
        self.assertEqual([], self.delete_calls)

    def test_lost_ack_after_actual_delete_reconciles_without_repeat(self):
        self.effect = "delete-then-error"
        with self.assertRaisesRegex(CleanupUncertain, "delete_ack_unknown"):
            self.run_cleanup()
        self.assertEqual("DELETED", self.run_cleanup().state)
        self.assertEqual(1, len(self.delete_calls))

    def test_unknown_ack_before_delete_never_retries(self):
        self.effect = "no-effect-error"
        with self.assertRaises(CleanupUncertain):
            self.run_cleanup()
        self.assertEqual("STILL_C", self.run_cleanup().state)
        self.assertEqual(1, len(self.delete_calls))
        self.assertEqual(self.op.archive_operation.expected_issue,
                         self.case.observation(issue_ref("101")))

    def test_moved_ref_after_unknown_ack_is_not_deleted(self):
        self.effect = "moved"
        with self.assertRaises(CleanupUncertain):
            self.run_cleanup()
        self.assertEqual("MOVED", self.run_cleanup().state)
        self.assertEqual(self.case.fixture.genesis,
                         self.case.observation(issue_ref("101")))
        self.assertEqual(1, len(self.delete_calls))

    def test_lost_archive_after_unknown_ack_classifies_unknown(self):
        self.effect = "no-effect-error"
        with self.assertRaises(CleanupUncertain):
            self.run_cleanup()
        self.case.fixture.set_ref(
            ARCHIVE_REF, self.case.fixture.genesis,
            self.op.archive_outcome.archive_commit)
        self.assertEqual("UNKNOWN", self.run_cleanup().state)
        self.assertEqual(1, len(self.delete_calls))

    def test_positive_ack_without_delete_is_uncertain_then_still_c(self):
        self.effect = "positive-without-effect"
        with self.assertRaisesRegex(CleanupUncertain, "reconcile_after_intent"):
            self.run_cleanup()
        self.assertEqual("STILL_C", self.run_cleanup().state)
        self.assertEqual(1, len(self.delete_calls))

    def test_new_cleanup_id_cannot_bypass_unresolved_intent(self):
        self.effect = "no-effect-error"
        with self.assertRaises(CleanupUncertain):
            self.run_cleanup()
        other = replace(self.op, cleanup_id="c" * 32)
        with self.assertRaisesRegex(CleanupRefused, "existing_intent_mismatch"):
            self.run_cleanup(op=other)
        self.assertEqual(1, len(self.delete_calls))

    def test_revoked_grant_during_first_authorize_blocks_before_intent(self):
        def revoke_after_first(approval):
            grant = self.authorize(approval)
            self.granted = False
            return grant
        with self.assertRaisesRegex(CleanupRefused, "grant_lock_lost_or_revoked"):
            self.run_cleanup(authorize=revoke_after_first)
        self.assertIsNone(self.local_intent())
        self.assertEqual([], self.delete_calls)

    def test_ref_moves_during_grant_before_intent_refuses(self):
        def malicious_race(approval):
            self.case.fixture.set_ref(
                approval.delete_request.ref,
                self.case.fixture.genesis,
                approval.delete_request.expected_old)
            return self.authorize(approval)
        with self.assertRaisesRegex(CleanupRefused, "original_history_unavailable"):
            self.run_cleanup(authorize=malicious_race)
        self.assertIsNone(self.local_intent())
        self.assertEqual([], self.delete_calls)

    def test_poisoned_saved_intent_is_never_replaced(self):
        self.effect = "no-effect-error"
        with self.assertRaises(CleanupUncertain):
            self.run_cleanup()
        key = _key(self.case.repository_identity.repository_id,
                   self.case.registered.issue, self.op.archive_operation.expected_issue,
                   self.op.archive_outcome.archive_commit)
        saved = self.local_intent()
        saved["issue_before"] = "0" * 40
        path = self.case.jpath / key
        path.write_text(json.dumps(saved, sort_keys=True, separators=(",", ":")))
        with self.assertRaisesRegex(CleanupRefused, "existing_intent_mismatch"):
            self.run_cleanup()
        self.assertEqual(1, len(self.delete_calls))


    def test_second_grant_moves_ref_after_intent_and_blocks_effect(self):
        def grant_race(approval):
            result = self.authorize(approval)
            if self.grant_calls == 2:
                self.case.fixture.set_ref(
                    approval.delete_request.ref, self.case.fixture.genesis,
                    approval.delete_request.expected_old)
            return result
        with self.assertRaisesRegex(CleanupUncertain, "reconcile_after_intent"):
            self.run_cleanup(authorize=grant_race)
        self.assertIsNotNone(self.local_intent())
        self.assertEqual([], self.delete_calls)
        self.assertEqual("MOVED", self.run_cleanup().state)

    def test_second_grant_changes_live_terminality_and_blocks_effect(self):
        def grant_race(approval):
            result = self.authorize(approval)
            if self.grant_calls == 2:
                self.current_valid = False
            return result
        with self.assertRaisesRegex(CleanupUncertain, "reconcile_after_intent"):
            self.run_cleanup(authorize=grant_race)
        self.assertIsNotNone(self.local_intent())
        self.assertEqual([], self.delete_calls)
        self.assertEqual("STILL_C", self.run_cleanup().state)

    def test_revocation_during_final_grant_blocks_delete_with_intent(self):
        def revoke_after_grant(approval):
            result = self.authorize(approval)
            if self.grant_calls == 2:
                self.granted = False
            return result
        with self.assertRaisesRegex(CleanupUncertain, "reconcile_after_intent"):
            self.run_cleanup(authorize=revoke_after_grant)
        self.assertEqual([], self.delete_calls)
        self.assertEqual(0, self.grant_holds)
        self.assertIsNotNone(self.local_intent())
        self.assertEqual("STILL_C", self.run_cleanup().state)

    def test_untrusted_grant_lease_type_refused_before_intent(self):
        @contextmanager
        def no_grant_mutex(approval):
            yield object()
        with self.assertRaisesRegex(CleanupRefused, "human_grant_lock_required"):
            self.run_cleanup(grant_hold=no_grant_mutex)
        self.assertEqual([], self.delete_calls)
        self.assertIsNone(self.local_intent())


    def test_original_archive_human_decision_cannot_be_reused_as_cleanup(self):
        # Regression: original J decision A, current decision B, cleanup
        # presents A. Previously only B was rejected, so A was admitted.
        original = self.case.human_decision_id
        self.assertEqual("e" * 64, original)
        current = "d" * 64
        self.assertNotEqual(original, current)

        def current_terminal(op, history):
            return replace(self.terminal(op, history), human_decision_id=current)

        def replay_archive_grant(approval):
            return replace(self.authorize(approval), decision_id=original)

        @contextmanager
        def compatible_grant_lock(approval):
            with self.grant_mutex:
                self.grant_holds += 1
                try:
                    yield CleanupHumanGrantLease(
                        lambda actual, grant: self.grant_holds == 1
                        and actual == approval and grant.decision_id == original)
                finally:
                    self.grant_holds -= 1

        with self.assertRaisesRegex(CleanupRefused, "fresh_human_grant_denied"):
            self.run_cleanup(terminal=current_terminal,
                             authorize=replay_archive_grant,
                             grant_hold=compatible_grant_lock)
        self.assertIsNone(self.local_intent())
        self.assertEqual([], self.delete_calls)

    def test_distinct_original_current_and_fresh_cleanup_decisions_pass(self):
        original = self.case.human_decision_id
        current = "d" * 64
        fresh = "f" * 64
        self.assertEqual(3, len({original, current, fresh}))

        def current_terminal(op, history):
            return replace(self.terminal(op, history), human_decision_id=current)

        self.assertEqual("DELETED",
                         self.run_cleanup(terminal=current_terminal).state)
        self.assertEqual(1, len(self.delete_calls))
        self.assertEqual(fresh, self.local_intent()["decision_id"])

    def test_grant_exit_error_suppressed_by_outer_context_is_uncertain(self):
        # Regression: an ExitStack pending return once allowed outer __exit__
        # to swallow inner __exit__ failure and falsely return DELETED.
        @contextmanager
        def grant_exit_raises(approval):
            with self.grant_mutex:
                self.grant_holds += 1
                try:
                    yield CleanupHumanGrantLease(
                        lambda actual, grant: actual == approval
                        and self.granted and self.grant_holds == 1
                        and grant.decision_id == "f" * 64)
                finally:
                    self.grant_holds -= 1
            raise OSError("synthetic grant unlock failure")

        @contextmanager
        def outer_suppresses_grant_error(op):
            self.holds += 1
            try:
                try:
                    yield CleanupQuiescenceLease(
                        lambda actual: self.lock_valid
                        and actual == op and self.holds == 1)
                except OSError:
                    # A hostile/misbehaving outer Host context swallows an
                    # inner lock release failure, just like ExitStack can.
                    pass
            finally:
                self.holds -= 1

        with self.assertRaisesRegex(CleanupUncertain,
                                    "cleanup_interrupted_after_intent"):
            self.run_cleanup(hold=outer_suppresses_grant_error,
                             grant_hold=grant_exit_raises)
        self.assertEqual(1, len(self.delete_calls))
        self.assertIsNotNone(self.local_intent())
        self.assertEqual(0, self.holds)
        self.assertEqual(0, self.grant_holds)
        self.assertIsNone(self.case.observation(issue_ref("101")))
        # Journal-based recovery observes success but cannot execute again.
        self.assertEqual("DELETED", self.run_cleanup().state)
        self.assertEqual(1, len(self.delete_calls))

    def test_lifecycle_context_cannot_suppress_pre_intent_refusal(self):
        @contextmanager
        def suppressing_hold(op):
            self.holds += 1
            try:
                try:
                    yield CleanupQuiescenceLease(
                        lambda actual: self.lock_valid
                        and actual == op and self.holds == 1)
                except CleanupRefused:
                    pass
            finally:
                self.holds -= 1

        self.granted = False
        with self.assertRaisesRegex(CleanupRefused, "fresh_human_grant_denied"):
            self.run_cleanup(hold=suppressing_hold)
        self.assertIsNone(self.local_intent())
        self.assertEqual([], self.delete_calls)

if __name__ == "__main__":
    unittest.main()
