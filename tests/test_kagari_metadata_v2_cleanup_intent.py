"""#337 P3C/B1-A — local synthetic Host admission and durable intent only.

No real GitHub API, Git push, deletion, model/Task execution, or activation.
Reuses existing ArchiveHost attached, authorized, and journaled fixtures.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "components/agent-core-v4")]

import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_archive_host import ArchiveQuiescenceLease
from kagari_metadata_v2_bootstrap import BootstrapIdentity
from kagari_metadata_v2_cleanup_history import CleanupHistoryReader
from kagari_metadata_v2_cleanup_intent import (
    CleanupIntentHost, CleanupIntentRefused, CleanupIntentUncertain,
    CleanupAdmission, _intent_key,
)
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import ARCHIVE_REF, issue_ref, construct
from tests import test_kagari_metadata_v2_archive_host as archive_fixture
from tests.test_kagari_metadata_v2_archive_host import RID
from tests.test_metadata_v2 import REPOSITORY


class CleanupIntentHostTest(unittest.TestCase):
    def setUp(self):
        self.case = archive_fixture.ArchiveHostTest("runTest")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.op = self.case.op(policy=None)
        self.archived = self.case.archive(self.op)
        self.assertEqual("PUBLISHED", self.archived.state)
        self.archive_j = self.case.observation(ARCHIVE_REF)
        self.issue_c = self.case.observation(issue_ref("101"))
        self.grants_at_archive = len(self.case.grants)
        self.writes_at_archive = len(self.case.writes)
        self.reads_at_archive = self.case.terminal_reads
        self.assertEqual(1, len(list(self.case.jpath.iterdir())))

    @contextmanager
    def host(self, *, authorize=None, terminal=None,
             prior=None, journal=None, hold=None):
        c = self.case
        with gate.hold_verified(c.project, c.home, c.package) as lease:
            identity = BootstrapIdentity(
                RID, REPOSITORY, lease._fingerprint, "sha1")
            archive_journal = c.journal()
            history = CleanupHistoryReader(
                identity, c.repository_identity, c.registered, c.approved,
                journal=archive_journal,
                reader=c.fixture.reader, observer=c.fixture.observe)
            host = CleanupIntentHost(
                history, lease=lease, journal=journal or c.journal(),
                hold_quiescence=hold or c.hold,
                read_terminal=terminal or c.terminal,
                prior_attempts_cleared=prior or (lambda _op: c.prior_clear),
                authorize=authorize or c.authorize)
            yield host

    def prepare(self, **kwargs):
        with self.host(**kwargs) as host:
            return host.prepare(self.op, self.archived)

    def read_saved(self, **kwargs):
        with self.host(**kwargs) as host:
            return host.read_saved(self.op, self.archived)

    def receipts(self):
        return tuple(self.case.jpath.iterdir())

    def test_prepare_is_durable_and_no_git_delete_or_new_writer(self):
        saved = self.prepare()
        self.assertEqual("PREPARED_NO_EFFECT", saved.state)
        self.assertEqual(_intent_key(RID, 101, self.issue_c), saved.key)
        self.assertEqual(3, len(self.receipts()))
        self.assertEqual(self.archive_j, self.case.observation(ARCHIVE_REF))
        self.assertEqual(self.issue_c, self.case.observation(issue_ref("101")))
        self.assertEqual(self.writes_at_archive, len(self.case.writes))
        self.assertEqual(self.grants_at_archive + 1, len(self.case.grants))
        self.assertIsInstance(self.case.grants[-1], CleanupAdmission)
        intent = self.case.journal().read(saved.key)
        self.assertEqual("cleanup-issue-intent", intent["operation"])
        self.assertEqual("NONE_PREPARED_ONLY", intent["effect"])
        self.assertEqual(self.archived.archive_commit, intent["archive_j"])
        self.assertEqual(self.issue_c, intent["issue_before"])

    def test_second_call_never_creates_new_grant_or_intent(self):
        self.prepare()
        grants = len(self.case.grants)
        with self.assertRaisesRegex(
                CleanupIntentRefused, "existing_intent_requires_reconciliation"):
            self.prepare()
        self.assertEqual(grants, len(self.case.grants))
        self.assertEqual(3, len(self.receipts()))

    def test_restart_readback_original_C_present_unresolved(self):
        self.prepare()
        state = self.read_saved()
        self.assertEqual("PRESENT_UNRESOLVED", state.state)
        self.assertEqual(self.issue_c, state.expected_old)
        self.assertEqual(self.writes_at_archive, len(self.case.writes))

    def test_restart_after_local_ref_absent_not_attributed_to_our_host(self):
        self.prepare()
        self.case.fixture.git(
            "update-ref", "--no-deref", "-d",
            issue_ref("101"), self.issue_c)
        state = self.read_saved()
        self.assertEqual("ABSENT_ARCHIVED_UNATTRIBUTED", state.state)
        self.assertEqual(self.archive_j, self.case.observation(ARCHIVE_REF))
        self.assertEqual(self.writes_at_archive, len(self.case.writes))

    def test_ref_absent_before_first_intent_refuses(self):
        self.case.fixture.git(
            "update-ref", "--no-deref", "-d",
            issue_ref("101"), self.issue_c)
        with self.assertRaisesRegex(CleanupIntentRefused, "not_active"):
            self.prepare()
        self.assertEqual(1, len(self.receipts()))

    def test_stopped_session_currently_false_refuses_without_intent(self):
        self.case.session_stopped = False
        with self.assertRaisesRegex(CleanupIntentRefused, "current_terminal_unavailable"):
            self.prepare()
        self.assertEqual(1, len(self.receipts()))
        self.assertEqual(self.writes_at_archive, len(self.case.writes))

    def test_writer_quiescence_currently_false_refuses_without_intent(self):
        self.case.writers_quiescent = False
        with self.assertRaisesRegex(CleanupIntentRefused, "current_terminal_unavailable"):
            self.prepare()
        self.assertEqual(1, len(self.receipts()))

    def test_denied_fresh_Human_cleanup_approval_refuses_without_intent(self):
        self.case.granted = False
        with self.assertRaisesRegex(CleanupIntentRefused, "human_grant_denied"):
            self.prepare()
        self.assertEqual(1, len(self.receipts()))

    def test_prior_attempts_unresolved_refuses(self):
        self.case.prior_clear = False
        with self.assertRaisesRegex(CleanupIntentRefused, "prior_attempts_unresolved"):
            self.prepare()
        self.assertEqual(1, len(self.receipts()))

    def test_lost_quiescence_refuses(self):
        self.case.lock_valid = False
        with self.assertRaisesRegex(CleanupIntentRefused, "host_quiescence_unavailable"):
            self.prepare()
        self.assertEqual(1, len(self.receipts()))

    def test_issue_ref_moves_during_Human_approval_refuses(self):
        source = self.case.fixture.store().validation().issue(self.issue_c, "101")
        candidate = construct(dict(source.snapshot.files),
                              (self.issue_c,), nonce="b1-issue-ref-moved")
        self.case.fixture.install(candidate)
        def mutate_then_grant(_request):
            self.case.fixture.set_ref(
                issue_ref("101"), candidate.commit, self.issue_c)
            return True
        with self.assertRaisesRegex(CleanupIntentRefused, "original_history_unavailable"):
            self.prepare(authorize=mutate_then_grant)
        self.assertEqual(1, len(self.receipts()))
        self.assertEqual(candidate.commit,
                         self.case.observation(issue_ref("101")))

    def test_archive_rewinds_between_grant_and_intent_refused(self):
        def rewind_then_grant(_request):
            self.case.fixture.set_ref(
                ARCHIVE_REF, self.case.fixture.genesis, self.archive_j)
            return True
        with self.assertRaisesRegex(CleanupIntentRefused, "original_history_unavailable"):
            self.prepare(authorize=rewind_then_grant)
        self.assertEqual(1, len(self.receipts()))

    def test_create_may_succeed_then_ack_lost_requires_read_only_recovery(self):
        initial = RecoveryJournal.create
        def write_then_lose_ack(journal, key, record):
            initial(journal, key, record)
            if record["operation"] == "cleanup-issue-intent":
                raise TimeoutError("synthetic lost intent fsync acknowledgment")
        with patch.object(RecoveryJournal, "create", write_then_lose_ack):
            with self.assertRaises(CleanupIntentUncertain):
                self.prepare()
        self.assertEqual(3, len(self.receipts()))
        status = self.read_saved()
        self.assertEqual("PRESENT_UNRESOLVED", status.state)
        self.assertEqual(self.writes_at_archive, len(self.case.writes))

    def test_intent_unknown_without_visible_entry_never_regrants(self):
        # A durable reservation exists before any full intent-create call.
        # Even with the latter's ACK lost and NO readable final file, a new
        # Host cannot generate another Human grant or attempt another intent.
        initial = RecoveryJournal.create
        def intent_ambiguous_without_entry(journal, key, record):
            if record["operation"] == "cleanup-issue-intent":
                raise TimeoutError("simulated unknown intent write, no file")
            return initial(journal, key, record)
        with patch.object(RecoveryJournal, "create", intent_ambiguous_without_entry):
            with self.assertRaises(CleanupIntentUncertain):
                self.prepare()
        self.assertEqual(2, len(self.receipts()))  # Archive + reservation only
        state = self.read_saved()
        self.assertEqual("RESERVED_ONLY_UNRESOLVED", state.state)
        grants = len(self.case.grants)
        with self.assertRaisesRegex(
                CleanupIntentRefused, "existing_intent_requires_reconciliation"):
            self.prepare()
        self.assertEqual(grants, len(self.case.grants))
        self.assertEqual(2, len(self.receipts()))
        self.assertEqual(self.writes_at_archive, len(self.case.writes))

    def test_reservation_ack_unknown_stops_without_attempting_intent(self):
        initial = RecoveryJournal.create
        def reserve_then_lose_ack(journal, key, record):
            initial(journal, key, record)
            if record["operation"] == "cleanup-intent-reservation":
                raise TimeoutError("simulated lost reservation ACK after fsync")
        with patch.object(RecoveryJournal, "create", reserve_then_lose_ack):
            with self.assertRaises(CleanupIntentUncertain):
                self.prepare()
        self.assertEqual(2, len(self.receipts()))
        self.assertEqual("RESERVED_ONLY_UNRESOLVED", self.read_saved().state)
        with self.assertRaisesRegex(
                CleanupIntentRefused, "existing_intent_requires_reconciliation"):
            self.prepare()

    def test_current_terminal_human_decision_bad_type_refused_before_journal(self):
        def malformed_decision(op, current):
            return replace(self.case.terminal(op, current),
                           human_decision_id=True)
        with self.assertRaisesRegex(
                CleanupIntentRefused, "current_terminal_types_invalid"):
            self.prepare(terminal=malformed_decision)
        self.assertEqual(1, len(self.receipts()))
        self.assertEqual(self.writes_at_archive, len(self.case.writes))

    def test_corrupt_reservation_record_fails_closed(self):
        state = self.prepare()
        from kagari_metadata_v2_cleanup_intent import _reservation_key
        key = _reservation_key(RID, 101, self.issue_c)
        path = self.case.jpath / key
        original = path.read_bytes()
        try:
            value = json.loads(original)
            value["schema"] = True
            path.write_bytes(json.dumps(
                value, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True).encode("ascii"))
            with self.assertRaisesRegex(
                    CleanupIntentRefused, "reservation_identity_or_schema_mismatch"):
                self.read_saved()
        finally:
            path.write_bytes(original)
        self.assertEqual(state.key, self.read_saved().key)

    def test_saved_intent_corruption_fails_closed(self):
        status = self.prepare()
        path = self.case.jpath / status.key
        old = path.read_bytes()
        try:
            for corrupt in (
                ("schema", True),
                ("version", True),
                ("issue_ref", "refs/heads/kagari/metadata"),
                ("issue_before", "f" * 40),
                ("effect", "DELETE_COMMITTED"),
                ("human_decision_id", True),
                ("terminal_facts_digest", "not-a-digest"),
            ):
                with self.subTest(field=corrupt[0]):
                    obj = json.loads(old)
                    obj[corrupt[0]] = corrupt[1]
                    path.write_bytes(json.dumps(
                        obj,sort_keys=True,separators=(",",":"),
                        ensure_ascii=True).encode("ascii"))
                    with self.assertRaises(CleanupIntentRefused):
                        self.read_saved()
        finally:
            path.write_bytes(old)
        self.assertEqual("PRESENT_UNRESOLVED", self.read_saved().state)

    def test_wrong_archive_j_cannot_adopt_prepared_intent(self):
        self.prepare()
        changed = replace(self.archived, archive_commit=self.case.fixture.genesis)
        with self.host() as host:
            with self.assertRaises(CleanupIntentRefused):
                host.read_saved(self.op, changed)
        self.assertEqual(3, len(self.receipts()))

    def test_read_only_intent_host_has_no_delete_or_effect_capability(self):
        with self.host() as host:
            for name in ("delete", "push", "writer", "execute", "activate"):
                self.assertFalse(hasattr(host, name))
        self.assertEqual(1, len(self.receipts()))

    def test_archived_j_can_be_ancestor_after_other_issue_append(self):
        approved, reg, c2 = self.case.register(202)
        op2 = replace(self.op, operation_id="b" * 32,
                      expected_archive=self.archive_j, expected_issue=c2,
                      record_id=reg.record_id)
        first_approved, first_registered = self.case.approved, self.case.registered
        try:
            self.case.approved, self.case.registered = approved, reg
            second = self.case.archive(op2)
        finally:
            self.case.approved, self.case.registered = first_approved, first_registered
        status = self.prepare()
        self.assertEqual("PREPARED_NO_EFFECT", status.state)
        self.assertNotEqual(second.archive_commit, self.archive_j)
        self.assertEqual(second.archive_commit, self.case.observation(ARCHIVE_REF))


if __name__ == "__main__":
    unittest.main()
