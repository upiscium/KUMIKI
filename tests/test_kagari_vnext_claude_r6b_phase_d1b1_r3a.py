"""#340 R6B D1B1 R3A synthetic-only negative-path binding contract.

All v2 dataclass values are deliberately caller fabricated. No Host, Main,
Git, Journal, Task, process, CLI, network or filesystem state is inspected.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest

from tools import _kagari_vnext_claude_r6b_phase_d1b1_r2 as r2
from tools import _kagari_vnext_claude_r6b_phase_d1b1_r3a as m


class R3AUnqualifiedBindingTests(unittest.TestCase):
    REPO_ID = 1183254623
    ISSUE = 340
    REPOSITORY = "upiscium/KUMIKI"

    def setUp(self):
        self.identity = m.RepositoryIdentity(
            self.REPO_ID, self.REPOSITORY, "a" * 40, "sha1")
        self.registered = m.RegisteredIssue(
            self.REPO_ID, self.REPOSITORY, self.ISSUE,
            "refs/heads/task/340-r3a", "b" * 40, "c" * 64, "d" * 64)
        self.approved = m.ApprovedIssue(
            self.REPO_ID, self.REPOSITORY, self.ISSUE,
            "refs/heads/task/340-r3a", "b" * 40, "synthetic", "no effects")
        self.selection = m.V2Selection(
            m.V2_PROFILE, self.REPO_ID, self.REPOSITORY, self.ISSUE, "e" * 64)
        self.current = m.CurrentTask(
            self.REPO_ID, self.REPOSITORY, self.ISSUE, "1" * 40,
            "2" * 40, "c" * 64, "d" * 64, "b" * 40,
            "refs/heads/task/340-r3a")
        self.project = Path("/synthetic/r3a-project")
        self.worktree = self.project / ".worktrees" / str(self.ISSUE)
        self.r2_report = r2._result(
            "OBSERVED_BLOCKED", "existing_reservation_observed_only")

    def evaluate(self, **updates):
        params = dict(
            identity=self.identity, registered=self.registered,
            approved=self.approved, selection=self.selection,
            current_before=self.current, current_after=self.current,
            main_repository_name=self.REPOSITORY,
            main_owner_claim="HELD_CLAIMED",
            attachment_claim="HELD_CLAIMED",
            legacy_collision_claim="CLEAR_CLAIMED",
            project_root=self.project, worktree_path=self.worktree,
            r2_report=self.r2_report,
        )
        params.update(updates)
        return m.assess_r3a_unqualified(**params)

    def assert_non_grant(self, value, status, reason):
        self.assertEqual(value, dict(
            status=status, reason=reason, qualification="UNQUALIFIED",
            stage="OBSERVATION_ONLY", evidence=m.SCOPE,
            dispatch_allowed=False, replay_allowed=False,
            task_completed=False, commit_allowed=False,
            authenticated_absence=False, effective_policy="UNVERIFIED",
        ))
        for field in ("dispatch_allowed", "replay_allowed", "task_completed",
                      "commit_allowed", "authenticated_absence"):
            self.assertIs(value[field], False)

    def test_matching_v2_and_retained_r2_record_still_denies_all(self):
        self.assert_non_grant(
            self.evaluate(), "OBSERVED_BLOCKED", "r2_reservation_claim_blocks_only")

    def test_matching_v2_is_not_an_authenticated_host_binding(self):
        result = self.evaluate()
        self.assertNotIn("READY", result.values())
        self.assertNotIn("QUALIFIED", result.values())
        self.assertNotIn("PERMITTED", result.values())

    def test_bad_v2_shape_is_refused(self):
        for key in ("identity", "registered", "approved", "selection"):
            with self.subTest(key=key):
                self.assert_non_grant(
                    self.evaluate(**{key: {"untrusted": True}}),
                    "REFUSED", "v2_shape_invalid")

    def test_numeric_repository_identity_mismatch_is_refused(self):
        for key, obj in (
                ("identity", self.identity),
                ("registered", self.registered),
                ("approved", self.approved),
                ("selection", self.selection)):
            with self.subTest(key=key):
                changed = replace(obj, repository_id=self.REPO_ID + 1)
                self.assert_non_grant(
                    self.evaluate(**{key: changed}),
                    "REFUSED", "repository_numeric_mismatch")

    def test_out_of_range_numeric_id_fails_before_any_host_io(self):
        bad = replace(self.identity, repository_id=10**12 + 1)
        self.assert_non_grant(self.evaluate(identity=bad),
                              "REFUSED", "repository_numeric_invalid")

    def test_repository_name_drift_freezes_same_id_without_rekey(self):
        for key, obj in (("identity", self.identity), ("registered", self.registered),
                         ("approved", self.approved), ("selection", self.selection)):
            with self.subTest(key=key):
                self.assert_non_grant(
                    self.evaluate(**{key: replace(obj, repository="upiscium/NEW")}),
                    "UNKNOWN", "repository_name_drift")
        self.assert_non_grant(
            self.evaluate(main_repository_name="upiscium/RENAMED"),
            "UNKNOWN", "repository_name_drift")

    def test_main_owner_missing_or_stale_always_refuses(self):
        for claim in ("MISSING", "STALE", "UNVERIFIED", False, None):
            with self.subTest(claim=claim):
                self.assert_non_grant(
                    self.evaluate(main_owner_claim=claim),
                    "REFUSED", "main_owner_unverified")

    def test_claimed_main_owner_never_converts_to_permit(self):
        self.assertIs(self.evaluate(main_owner_claim="HELD_CLAIMED")
                      ["dispatch_allowed"], False)

    def test_attachment_missing_or_changed_refuses(self):
        for claim in ("MISSING", "CHANGED", None, True):
            with self.subTest(claim=claim):
                self.assert_non_grant(
                    self.evaluate(attachment_claim=claim),
                    "REFUSED", "attachment_unverified")

    def test_v1_assignment_collision_refuses(self):
        self.assert_non_grant(
            self.evaluate(legacy_collision_claim="PRESENT"),
            "REFUSED", "legacy_assignment_collision")

    def test_unconfirmed_legacy_clearance_is_unknown(self):
        self.assert_non_grant(
            self.evaluate(legacy_collision_claim="UNKNOWN"),
            "UNKNOWN", "legacy_assignment_unverified")

    def test_wrong_issue_selection_is_refused(self):
        self.assert_non_grant(
            self.evaluate(selection=replace(self.selection, issue=self.ISSUE + 1)),
            "REFUSED", "registered_task_mismatch")

    def test_wrong_branch_and_base_are_refused(self):
        for changed in (
                replace(self.registered, branch_ref="refs/heads/task/340-other"),
                replace(self.registered, base_revision="f" * 40)):
            with self.subTest(registered=changed):
                self.assert_non_grant(
                    self.evaluate(registered=changed),
                    "REFUSED", "registered_task_mismatch")

    def test_current_task_missing_is_unknown(self):
        for key in ("current_before", "current_after"):
            with self.subTest(key=key):
                self.assert_non_grant(
                    self.evaluate(**{key: None}),
                    "UNKNOWN", "current_task_missing")

    def test_stale_current_task_observation_is_unknown(self):
        moved = replace(self.current, metadata_tip="3" * 40)
        self.assert_non_grant(
            self.evaluate(current_after=moved),
            "UNKNOWN", "current_task_drift")
        changed_archive = replace(self.current, archive_tip="4" * 40)
        self.assert_non_grant(
            self.evaluate(current_after=changed_archive),
            "UNKNOWN", "current_task_drift")

    def test_current_task_wrong_repository_or_issue_refuses(self):
        for changed in (
                replace(self.current, repository_id=self.REPO_ID + 1),
                replace(self.current, issue=self.ISSUE + 1),
                replace(self.current, repository="upiscium/OTHER")):
            with self.subTest(current=changed):
                self.assert_non_grant(
                    self.evaluate(current_before=changed, current_after=changed),
                    "REFUSED", "current_identity_mismatch")

    def test_wrong_current_contract_or_record_refuses(self):
        for changed in (
                replace(self.current, record_id="0" * 64),
                replace(self.current, contract_id="f" * 64),
                replace(self.current, branch_ref="refs/heads/task/340-wrong"),
                replace(self.current, base_revision="0" * 40)):
            with self.subTest(current=changed):
                self.assert_non_grant(
                    self.evaluate(current_before=changed, current_after=changed),
                    "REFUSED", "current_contract_or_record_mismatch")

    def test_invalid_current_digest_does_not_grant(self):
        broken = replace(self.current, contract_id="bad")
        self.assert_non_grant(
            self.evaluate(current_before=broken, current_after=broken),
            "REFUSED", "current_task_invalid")

    def test_mismatched_worktree_is_refused_without_fs_lookup(self):
        other = self.project / ".worktrees" / "999"
        self.assert_non_grant(
            self.evaluate(worktree_path=other),
            "REFUSED", "worktree_claim_mismatch")

    def test_non_absolute_or_parent_traversal_path_refused(self):
        for path in (Path("local"), Path("/synthetic/../other"), None):
            with self.subTest(path=path):
                self.assert_non_grant(
                    self.evaluate(worktree_path=path),
                    "REFUSED", "worktree_claim_invalid")

    def test_missing_r2_observation_is_unknown(self):
        self.assert_non_grant(
            self.evaluate(r2_report=None),
            "UNKNOWN", "journal_observation_missing")

    def test_r2_unknown_history_stays_unknown(self):
        for reason in ("host_journal_missing", "history_absence_unproven",
                       "journal_read_or_history_uncertain"):
            with self.subTest(reason=reason):
                v = r2._result("UNKNOWN", reason)
                self.assert_non_grant(
                    self.evaluate(r2_report=v),
                    "UNKNOWN", "r2_history_or_root_unverified")

    def test_r2_refused_root_stays_refused(self):
        self.assert_non_grant(
            self.evaluate(r2_report=r2._result("REFUSED", "host_journal_unsafe")),
            "REFUSED", "r2_observation_refused")

    def test_forged_r2_grant_rejected(self):
        for field in ("dispatch_allowed", "replay_allowed", "task_completed",
                      "commit_allowed", "authenticated_absence"):
            with self.subTest(field=field):
                forged = {**self.r2_report, field: True}
                self.assert_non_grant(
                    self.evaluate(r2_report=forged),
                    "REFUSED", "r2_report_invalid")

    def test_malformed_r2_shapes_are_refused(self):
        for report in ({"status": "OBSERVED_BLOCKED"}, 3,
                       {**self.r2_report, "evidence": "FAKE"},
                       {**self.r2_report, "extra": "grant"},
                       {**self.r2_report, "status": "READY"},
                       {**self.r2_report, "reason": "successful_launch"}):
            with self.subTest(report=report):
                self.assert_non_grant(
                    self.evaluate(r2_report=report),
                    "REFUSED", "r2_report_invalid")

    def test_r2_custom_equality_objects_are_rejected_without_comparison(self):
        called = []

        class EqualityTrap:
            def __eq__(self, _other):
                called.append("called")
                raise AssertionError("must not compare untrusted objects")

        for field in ("qualification", "stage", "evidence", "effective_policy"):
            with self.subTest(field=field):
                fake = {**self.r2_report, field: EqualityTrap()}
                self.assert_non_grant(
                    self.evaluate(r2_report=fake),
                    "REFUSED", "r2_report_invalid")
        self.assertEqual(called, [])

    def test_claimed_owner_equality_objects_are_not_evaluated(self):
        called = []

        class EqualityTrap:
            def __eq__(self, _other):
                called.append("called")
                raise AssertionError("must not compare untrusted owner")

        self.assert_non_grant(
            self.evaluate(main_owner_claim=EqualityTrap()),
            "REFUSED", "main_owner_unverified")
        self.assert_non_grant(
            self.evaluate(attachment_claim=EqualityTrap()),
            "REFUSED", "attachment_unverified")
        self.assertEqual(called, [])

    def test_malformed_uninitialized_current_is_unknown(self):
        broken = object.__new__(m.CurrentTask)
        self.assert_non_grant(
            self.evaluate(current_before=broken, current_after=broken),
            "UNKNOWN", "untrusted_snapshot_invalid")

    def test_output_does_not_echo_claims_or_expose_host_paths(self):
        result = self.evaluate(main_repository_name=self.REPOSITORY)
        values = tuple(str(v) for v in result.values())
        for secret in (self.REPOSITORY, str(self.project), "a" * 40):
            self.assertFalse(any(secret in v for v in values))

    def test_source_has_no_direct_io_or_executor_wiring(self):
        source = Path(m.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(name.name for name in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "<relative>")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    calls.append(node.func.attr)
        self.assertTrue(imported <= {
            "__future__", "pathlib", "re",
            "kagari_vnext_main_v2_observation",
            "kagari_metadata_v2_task_admission", "<relative>",
        })
        for disallowed in (
                "observe", "_assert_owned", "mkdir", "open", "create",
                "read", "read_bytes", "write_bytes", "resolve", "stat",
                "spawn", "Popen", "run", "system", "unlink", "replace"):
            self.assertNotIn(disallowed, calls)


if __name__ == "__main__":
    unittest.main()
