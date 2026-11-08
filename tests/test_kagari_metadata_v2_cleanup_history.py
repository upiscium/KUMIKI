"""#337 P3C/B0 source-only Archive-backed cleanup readback (local Git only).

Archive terminality is from the existing synthetic Host test fixture; this
new reader grants no deletion, Task approval, quiescence or runtime authority.
No GitHub service, GitHub ref delete, model or GA activation is exercised.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "components/agent-core-v4")]

from kagari_metadata_v2_archive_host import ArchiveOperation, ArchiveOutcome
from kagari_metadata_v2_bootstrap import BootstrapIdentity
from kagari_metadata_v2_cleanup_history import (
    CleanupHistoryReader, CleanupHistoryRefused,
)
from kagari_vnext_trusted_host import RecoveryJournal
import kagari_vnext_attachment_gate as gate
from metadata_v2 import ARCHIVE_REF, construct, issue_ref
from tests import test_kagari_metadata_v2_archive_host as archive_fixture
from tests.test_kagari_metadata_v2_archive_host import RID
from tests.test_metadata_v2 import REPOSITORY


class CleanupHistoryReaderTest(unittest.TestCase):
    def setUp(self):
        # Reuse the existing ArchiveHost, terminality, journal and attach
        # fixture; do not introduce a second Task authority model.
        self.case = archive_fixture.ArchiveHostTest("runTest")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.op = self.case.op(policy=None)
        self.result = self.case.archive(self.op)
        self.assertEqual("PUBLISHED", self.result.state)

    def reader(self, *, registered=None, approved=None, journal=None,
               observer=None, identity=None):
        case = self.case
        with gate.hold_verified(case.project, case.home, case.package) as lease:
            trusted_identity = BootstrapIdentity(
                RID, REPOSITORY, lease._fingerprint, "sha1")
        return CleanupHistoryReader(
            identity or trusted_identity, case.repository_identity,
            registered or case.registered, approved or case.approved,
            journal=journal or case.journal(),
            reader=case.fixture.reader, observer=observer or case.fixture.observe)

    def inspect(self, **reader_overrides):
        return self.reader(**reader_overrides).inspect(self.op, self.result)

    def test_original_archive_has_exact_present_issue_and_no_effect(self):
        before = self.case.observation(ARCHIVE_REF)
        original = self.case.observation(issue_ref("101"))
        outcome = self.inspect()
        self.assertEqual("PRESENT_EXACT", outcome.issue_ref_state)
        self.assertEqual(self.result.archive_commit, outcome.archive_candidate)
        self.assertEqual(original, outcome.issue_tip)
        self.assertEqual(before, outcome.archive_current_tip)
        self.assertEqual(self.case.registered.contract_id, outcome.contract_id)
        self.assertEqual(self.case.registered.record_id, outcome.record_id)
        self.assertEqual(before, self.case.observation(ARCHIVE_REF))
        self.assertEqual(original, self.case.observation(issue_ref("101")))

    def test_after_local_issue_delete_original_archive_history_remains(self):
        self.case.fixture.git("update-ref", "--no-deref", "-d",
                              issue_ref("101"), self.op.expected_issue)
        result = self.inspect()
        self.assertEqual("ALREADY_ABSENT", result.issue_ref_state)
        self.assertEqual(self.result.archive_commit, result.archive_candidate)
        self.assertIsNone(self.case.observation(issue_ref("101")))

    def test_after_second_issue_merge_reads_original_j_as_ancestor(self):
        second_approved, second_registered, second_tip = self.case.register(202)
        second_op = ArchiveOperation(
            "b" * 32, self.result.archive_commit, second_tip,
            second_registered.record_id, None)
        old_approved, old_registered = self.case.approved, self.case.registered
        try:
            self.case.approved, self.case.registered = (
                second_approved, second_registered)
            later = self.case.archive(second_op)
        finally:
            self.case.approved, self.case.registered = (
                old_approved, old_registered)
        original = self.inspect()
        self.assertEqual(self.result.archive_commit, original.archive_candidate)
        self.assertEqual(later.archive_commit, original.archive_current_tip)
        self.assertNotEqual(original.archive_candidate, original.archive_current_tip)
        # This is a disposable local ref-only removal, no external GitHub effect.
        self.case.fixture.git("update-ref", "--no-deref", "-d",
                              issue_ref("101"), self.op.expected_issue)
        after = self.inspect()
        self.assertEqual("ALREADY_ABSENT", after.issue_ref_state)
        self.assertEqual(later.archive_commit, after.archive_current_tip)

    def test_changed_current_issue_ref_refused_before_cleanup_authority(self):
        original = self.case.fixture.store().validation().issue(
            self.op.expected_issue, "101")
        candidate = construct(dict(original.snapshot.files),
                              (self.op.expected_issue,), nonce="cleanup-moved")
        self.case.fixture.install(candidate)
        self.case.fixture.set_ref(issue_ref("101"), candidate.commit,
                                  self.op.expected_issue)
        with self.assertRaisesRegex(CleanupHistoryRefused, "issue_ref_moved"):
            self.inspect()
        self.assertEqual(candidate.commit, self.case.observation(issue_ref("101")))

    def test_external_archive_rewind_refused_without_ruleset_assumption(self):
        self.case.fixture.set_ref(ARCHIVE_REF, self.case.fixture.genesis,
                                  self.result.archive_commit)
        with self.assertRaisesRegex(CleanupHistoryRefused,
                                    "candidate_not_original"):
            self.inspect()
        self.assertEqual(self.case.fixture.genesis,
                         self.case.observation(ARCHIVE_REF))

    def test_wrong_original_j_cannot_substitute_latest_or_foreign(self):
        bad_outcome = replace(self.result,
                              archive_commit=self.case.fixture.genesis)
        with self.assertRaisesRegex(CleanupHistoryRefused, "intent_mismatch"):
            self.reader().inspect(self.op, bad_outcome)
        self.assertEqual(self.result.archive_commit,
                         self.case.observation(ARCHIVE_REF))

    def test_missing_original_journal_refused(self):
        with tempfile.TemporaryDirectory(prefix="cleanup-journal-empty-") as temp:
            jdir = Path(temp)
            jdir.chmod(0o700)
            with self.assertRaisesRegex(CleanupHistoryRefused, "journal_invalid"):
                self.inspect(journal=RecoveryJournal(jdir))

    def test_legacy_ruleset_label_not_reinterpreted_as_ordinary(self):
        # Historical mode isn't silently adopted as a P3C ordinary-branch
        # cleanup grant, even if it has valid local Archive history.
        legacy = self.case.op(policy="legacy-fixture-only")
        with self.assertRaisesRegex(CleanupHistoryRefused, "operation_mismatch"):
            self.reader().inspect(legacy, self.result)

    def test_changed_original_contract_or_subject_refused(self):
        replacement_registered = replace(
            self.case.registered, base_revision="d" * 40)
        replacement_approved = replace(self.case.approved, base_revision="d" * 40)
        with self.assertRaisesRegex(CleanupHistoryRefused, "intent_mismatch"):
            self.inspect(registered=replacement_registered,
                         approved=replacement_approved)

    def test_archive_journal_malformed_fields_rejected_before_observation(self):
        # This is the original ArchiveHost protected, fsynced journal file.
        # Rewriting a synthetic fixture file probes each closed v3 schema
        # field; it does not mutate a real GitHub ref or Host grant.
        entries = tuple(self.case.jpath.iterdir())
        self.assertEqual(1, len(entries))
        path = entries[0]
        original = path.read_bytes()
        mutations = (
            ("schema_boolean", lambda d: d.__setitem__("schema", True)),
            ("bundle_negative", lambda d: d.__setitem__("bundle_object_count", -1)),
            ("bundle_bad_digest", lambda d: d.__setitem__("bundle_sha256", "not-a-digest")),
            ("facts_missing", lambda d: d["terminal_facts"].pop("session_stopped")),
            ("facts_extra", lambda d: d["terminal_facts"].__setitem__("unexpected", True)),
            ("facts_bad_bool", lambda d: d["terminal_facts"].__setitem__("session_stopped", "true")),
            ("facts_bad_pr", lambda d: d["terminal_facts"].__setitem__("pr_number", True)),
            ("facts_bad_route", lambda d: d["terminal_facts"].__setitem__("route", "RUNNING")),
            ("facts_bad_decision", lambda d: d["terminal_facts"].__setitem__("human_decision_id", "bad")),
        )
        try:
            for name, change in mutations:
                with self.subTest(name=name):
                    altered = json.loads(original)
                    change(altered)
                    encoded = json.dumps(
                        altered, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("ascii")
                    path.write_bytes(encoded)
                    with self.assertRaisesRegex(
                        CleanupHistoryRefused, "(journal_invalid|terminal_snapshot_invalid)"
                    ):
                        self.inspect()
        finally:
            path.write_bytes(original)
        self.assertEqual("PRESENT_EXACT", self.inspect().issue_ref_state)

    def test_ArchiveOutcome_does_not_grant_deletion(self):
        observation = self.inspect()
        self.assertFalse(hasattr(observation, "delete"))
        self.assertFalse(hasattr(self.reader(), "writer"))
        self.assertFalse(hasattr(self.reader(), "cleanup"))
        self.assertEqual("PRESENT_EXACT", observation.issue_ref_state)
        self.assertEqual(self.op.expected_issue,
                         self.case.observation(issue_ref("101")))


if __name__ == "__main__":
    unittest.main()
