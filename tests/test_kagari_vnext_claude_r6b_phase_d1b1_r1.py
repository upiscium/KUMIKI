"""Pure, isolated #340 D1B1 R1 synthetic Host-binding refusal tests.

No Host files, credentials, Git mutation, CLI, process or network effects.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest

from tools import _kagari_vnext_claude_r6b_phase_d1b1_r1 as m

EXPECTED_KEY_1_23 = "aa2647fe3149f893fbdb79f69da741d6d5d82b017f8a9d94548c2ded8fb91125"
EXPECTED_ROOT_1_23 = "ceb181a843e9934fcb57908b832382937e549f87c807624315308b8ce14130c8"
EXPECTED_KEY_12_3 = "4608be10c94a3aa9aeaa23053e391af9689c3eeda5b9a5cf1bea5c679e31a3c2"
EXPECTED_ROOT_12_3 = "8291ffac23c550353f7e0230be3f5ecbabb3811a21fd125d07285905e4e77e39"


class R1SyntheticHostBindingTests(unittest.TestCase):
    def setUp(self):
        self.binding = m.SyntheticBinding(1183254623, "upiscium/KUMIKI", 340,
                                          "a" * 64, "b" * 64)
        self.selected = m.SyntheticMainObservation(1183254623, "upiscium/KUMIKI", 340,
                                                   "a" * 64, "b" * 64)
        self.journal = m.SyntheticJournalObservation(
            "PRESENT", m.root_component(1183254623, 340), "RESERVED")

    def check_no_grant(self, result, status, reason):
        self.assertEqual(result, {
            "status": status, "reason": reason, "qualification": "UNQUALIFIED",
            "evidence": m.SCOPE, "stage": "OBSERVATION_ONLY",
            "dispatch_allowed": False, "task_completed": False,
            "replay_allowed": False, "commit_allowed": False,
            "authenticated_absence": False, "effective_policy": "UNVERIFIED",
        })
        for field in ("dispatch_allowed", "replay_allowed", "commit_allowed",
                      "task_completed", "authenticated_absence"):
            self.assertIs(result[field], False)

    def observe(self, binding=None, selected=None, journal=None):
        return m.observe_synthetic_host_binding(
            self.binding if binding is None else binding,
            self.selected if selected is None else selected,
            self.journal if journal is None else journal,
        )

    def test_literal_golden_vectors_and_numeric_pair_separation(self):
        self.assertEqual(m.reservation_key(1, 23), EXPECTED_KEY_1_23)
        self.assertEqual(m.root_component(1, 23), EXPECTED_ROOT_1_23)
        self.assertEqual(m.reservation_key(12, 3), EXPECTED_KEY_12_3)
        self.assertEqual(m.root_component(12, 3), EXPECTED_ROOT_12_3)
        self.assertNotEqual(EXPECTED_KEY_1_23, EXPECTED_KEY_12_3)
        self.assertNotEqual(EXPECTED_ROOT_1_23, EXPECTED_ROOT_12_3)
        self.assertEqual(len(m.reservation_key(1, 23)), 64)

    def test_canonical_key_not_affected_by_names_clones_or_uuids(self):
        # Public derivation intentionally exposes only (numeric ID, Issue).
        before = m.reservation_key(self.binding.repository_id, self.binding.issue)
        renamed = replace(self.binding, repository_name="upiscium/RENAMED")
        self.assertEqual(before, m.reservation_key(renamed.repository_id, renamed.issue))
        self.assertEqual(m.root_component(self.binding.repository_id, self.binding.issue),
                         m.root_component(renamed.repository_id, renamed.issue))
        with self.assertRaises(TypeError):
            m.reservation_key(self.binding.repository_id, self.binding.issue,
                              "another-uuid")

    def test_exact_positive_integer_limits_reject_bool_and_out_of_range(self):
        for repo, issue in ((True, 1), (1, False), (0, 1), (1, 0), (-1, 1),
                            (1, -1), (10**12 + 1, 1), (1, 2**31),
                            (1.0, 2), ("1", 2), (1, "2"), (None, 2)):
            with self.subTest(repo=repo, issue=issue):
                with self.assertRaises(ValueError):
                    m.reservation_key(repo, issue)
                with self.assertRaises(ValueError):
                    m.root_component(repo, issue)
        self.assertEqual(len(m.reservation_key(10**12, 2**31 - 1)), 64)

    def test_retained_reservation_is_blocking_observation_only(self):
        self.check_no_grant(self.observe(), "OBSERVED_BLOCKED",
                            "retained_attempt_observed_only")

    def test_result_claim_does_not_mean_completion(self):
        value = self.observe(journal=replace(self.journal, record_state="RESULT"))
        self.check_no_grant(value, "OBSERVED_BLOCKED", "retained_attempt_observed_only")

    def test_empty_record_is_not_authenticated_absence_or_genesis(self):
        self.check_no_grant(
            self.observe(journal=replace(self.journal, record_state="ABSENT")),
            "UNKNOWN", "record_absence_not_authenticated")

    def test_absent_snapshot_is_unknown(self):
        value = m.observe_synthetic_host_binding(self.binding, self.selected, None)
        self.check_no_grant(value, "UNKNOWN", "journal_observation_missing")

    def test_absent_root_is_not_genesis(self):
        self.check_no_grant(
            self.observe(journal=replace(self.journal, root_state="MISSING",
                                         directory_digest=None, record_state="ABSENT")),
            "UNKNOWN", "root_missing_not_genesis")

    def test_non_present_root_with_directory_digest_refused(self):
        for root_state in ("MISSING", "MOVED", "REPLACED", "MULTIPLE", "UNSAFE"):
            for record_state in ("ABSENT", "RESERVED", "RESULT", "PARTIAL",
                                 "DIVERGENT", "ACK_LOST"):
                with self.subTest(root_state=root_state, record_state=record_state):
                    journal = replace(self.journal, root_state=root_state,
                                      record_state=record_state)
                    self.check_no_grant(self.observe(journal=journal), "REFUSED",
                                        "journal_observation_invalid")

    def test_non_present_root_with_retained_record_refused(self):
        for root_state in ("MISSING", "MOVED", "REPLACED", "MULTIPLE", "UNSAFE"):
            for record_state in ("RESERVED", "RESULT"):
                with self.subTest(root_state=root_state, record_state=record_state):
                    journal = replace(self.journal, root_state=root_state,
                                      directory_digest=None, record_state=record_state)
                    self.check_no_grant(self.observe(journal=journal), "REFUSED",
                                        "journal_observation_invalid")

    def test_present_root_without_directory_digest_refused(self):
        for record_state in ("ABSENT", "RESERVED", "RESULT", "PARTIAL",
                             "DIVERGENT", "ACK_LOST"):
            with self.subTest(record_state=record_state):
                journal = replace(self.journal, directory_digest=None,
                                  record_state=record_state)
                self.check_no_grant(self.observe(journal=journal), "REFUSED",
                                    "journal_observation_invalid")

    def test_moved_replaced_multiple_or_unsafe_root_refused(self):
        for state in ("MOVED", "REPLACED", "MULTIPLE", "UNSAFE"):
            with self.subTest(state=state):
                self.check_no_grant(
                    self.observe(journal=replace(self.journal, root_state=state,
                                                 directory_digest=None,
                                                 record_state="ABSENT")),
                    "REFUSED", "root_unqualified")

    def test_mismatched_and_foreign_root_component_refused(self):
        self.check_no_grant(
            self.observe(journal=replace(self.journal, directory_digest="f" * 64)),
            "REFUSED", "root_component_mismatch")

    def test_partial_conflict_and_ack_loss_retained_unknown(self):
        for state in ("PARTIAL", "DIVERGENT", "ACK_LOST"):
            with self.subTest(state=state):
                self.check_no_grant(
                    self.observe(journal=replace(self.journal, record_state=state)),
                    "UNKNOWN", "reconciliation_required")

    def test_main_owner_claim_missing_and_stale_refused(self):
        for owner in ("MISSING", "STALE"):
            self.check_no_grant(
                self.observe(selected=replace(self.selected, owner_claim=owner)),
                "REFUSED", "main_owner_not_proven")

    def test_wrong_repository_issue_contract_or_task_refused(self):
        alternatives = (
            (replace(self.binding, repository_id=2), self.selected),
            (replace(self.binding, issue=341), self.selected),
            (replace(self.binding, contract_digest="c" * 64), self.selected),
            (replace(self.binding, task_digest="d" * 64), self.selected),
        )
        for b, s in alternatives:
            with self.subTest(binding=b):
                self.check_no_grant(self.observe(binding=b, selected=s), "REFUSED",
                                    "task_identity_mismatch")

    def test_name_mismatch_can_be_observed_but_not_verified_as_rename(self):
        alias = replace(self.binding, repository_name="upiscium/Renamed")
        self.check_no_grant(self.observe(binding=alias), "REFUSED",
                            "repository_name_mismatch_unqualified")
        self.assertEqual(m.reservation_key(alias.repository_id, alias.issue),
                         m.reservation_key(self.binding.repository_id, self.binding.issue))

    def test_profile_is_checked_not_used_as_rekey_input(self):
        wrong = replace(self.binding, profile="r6b-d1b1-r2")
        self.check_no_grant(self.observe(binding=wrong), "REFUSED", "binding_invalid")
        self.assertEqual(m.reservation_key(wrong.repository_id, wrong.issue),
                         m.reservation_key(self.binding.repository_id, self.binding.issue))

    def test_input_shapes_reject_dict_subclass_ambiguous_and_invalid_names(self):
        class Derived(m.SyntheticBinding):
            pass
        for val in ({"repository_id": 1}, Derived(*(
                self.binding.repository_id, self.binding.repository_name,
                self.binding.issue, self.binding.contract_digest, self.binding.task_digest)),
                    replace(self.binding, issue=True),
                    replace(self.binding, repository_name="../Other"),
                    replace(self.binding, repository_name="a/.."),
                    replace(self.binding, contract_digest="x" * 64)):
            self.check_no_grant(self.observe(binding=val), "REFUSED", "binding_invalid")

    def test_invalid_selected_task_shape_is_refused(self):
        for val in ({"owner_claim": "HELD"},
                    replace(self.selected, owner_claim=True),
                    replace(self.selected, repository_name="bad/path/extra"),
                    replace(self.selected, issue=0),
                    replace(self.selected, task_digest="invalid")):
            self.check_no_grant(self.observe(selected=val), "REFUSED",
                                "main_selection_invalid")

    def test_invalid_snapshot_shapes_refused(self):
        for val in ({"root_state": "PRESENT"},
                    replace(self.journal, root_state="UNKNOWN"),
                    replace(self.journal, record_state="DONE"),
                    replace(self.journal, directory_digest=True)):
            self.check_no_grant(self.observe(journal=val), "REFUSED",
                                "journal_observation_invalid")

    def test_uninitialized_exact_type_fails_closed_without_exception(self):
        instance = object.__new__(m.SyntheticBinding)
        value = m.observe_synthetic_host_binding(instance, self.selected, self.journal)
        self.check_no_grant(value, "UNKNOWN", "malformed_untrusted_witness")

    def test_synthetic_module_has_no_effect_or_host_imports(self):
        source_path = Path(m.__file__)
        source = source_path.read_text(encoding="utf-8")
        parsed = ast.parse(source)
        imports = []
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        self.assertEqual(sorted(imports),
                         ["__future__", "dataclasses", "hashlib", "json", "re"])
        forbidden = ("_host_state_home(", "RecoveryJournal(", ".create(",
                     "O_CREAT", "subprocess.", "socket.", "os.", "Path(",
                     "kagari_vnext_main", "kagari_vnext_claude_transport")
        for text in forbidden:
            self.assertNotIn(text, source)
        # This boundary guard is static inspection only; it proves no runtime
        # Main integration or external effect qualification.


if __name__ == "__main__":
    unittest.main()
