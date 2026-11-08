"""#340 R6B D1B1 R3A synthetic-only negative-path binding contract.

All v2 dataclass values are deliberately caller fabricated. The R3A
assessment does not inspect Host, Main, Git, Journal, Task, CLI or live
filesystem state. Static tests read the R3A module's source file.
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

    def test_malformed_main_repository_name_is_refused(self):
        for bad in ("", "upiscium", "upiscium//KUMIKI", False, None):
            with self.subTest(name=bad):
                self.assert_non_grant(
                    self.evaluate(main_repository_name=bad),
                    "REFUSED", "main_repository_invalid")

    def test_mutated_malformed_v2_repository_names_refused_before_drift(self):
        for name, obj in (
                ("identity", self.identity),
                ("registered", self.registered),
                ("approved", self.approved),
                ("selection", self.selection)):
            with self.subTest(name=name):
                old = obj.repository
                try:
                    # Exact dataclass class, but invalid scalar field.
                    object.__setattr__(obj, "repository", "bad/")
                    self.assert_non_grant(
                        self.evaluate(), "REFUSED", "v2_fields_invalid")
                finally:
                    object.__setattr__(obj, "repository", old)

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

    def test_missing_one_current_cannot_hide_other_wrong_binding(self):
        for missing_side, present_side in (
                ("current_before", "current_after"),
                ("current_after", "current_before")):
            for field, value, reason in (
                    ("repository_id", self.REPO_ID + 1,
                     "current_identity_mismatch"),
                    ("issue", self.ISSUE + 1,
                     "current_identity_mismatch"),
                    ("record_id", "0" * 64,
                     "current_contract_or_record_mismatch"),
                    ("contract_id", "f" * 64,
                     "current_contract_or_record_mismatch"),
                    ("branch_ref", "refs/heads/task/340-other",
                     "current_contract_or_record_mismatch"),
                    ("base_revision", "0" * 40,
                     "current_contract_or_record_mismatch")):
                with self.subTest(missing=missing_side, field=field):
                    wrong = replace(self.current, **{field: value})
                    self.assert_non_grant(
                        self.evaluate(**{
                            missing_side: None,
                            present_side: wrong,
                        }),
                        "REFUSED", reason)

    def test_incomplete_one_current_cannot_hide_other_wrong_binding(self):
        incomplete = object.__new__(m.CurrentTask)
        for incomplete_side, present_side in (
                ("current_before", "current_after"),
                ("current_after", "current_before")):
            with self.subTest(incomplete=incomplete_side):
                wrong = replace(self.current, contract_id="f" * 64)
                self.assert_non_grant(
                    self.evaluate(**{
                        incomplete_side: incomplete,
                        present_side: wrong,
                    }),
                    "REFUSED", "current_contract_or_record_mismatch")
        # An incomplete CurrentTask without a wrong available counterpart
        # is still UNKNOWN, not a grant and not proof of missing history.
        self.assert_non_grant(
            self.evaluate(current_before=incomplete),
            "UNKNOWN", "untrusted_snapshot_invalid")

    def test_stale_current_task_observation_is_unknown(self):
        moved = replace(self.current, metadata_tip="3" * 40)
        self.assert_non_grant(
            self.evaluate(current_after=moved),
            "UNKNOWN", "current_task_drift")
        changed_archive = replace(self.current, archive_tip="4" * 40)
        self.assert_non_grant(
            self.evaluate(current_after=changed_archive),
            "UNKNOWN", "current_task_drift")

    def test_current_task_wrong_numeric_repository_or_issue_refuses(self):
        for changed in (
                replace(self.current, repository_id=self.REPO_ID + 1),
                replace(self.current, issue=self.ISSUE + 1)):
            with self.subTest(current=changed):
                self.assert_non_grant(
                    self.evaluate(current_before=changed, current_after=changed),
                    "REFUSED", "current_identity_mismatch")

    def test_one_sided_current_identity_mismatches_refuse_before_drift(self):
        for side in ("current_before", "current_after"):
            for field, value in (
                    ("repository_id", self.REPO_ID + 1),
                    ("issue", self.ISSUE + 1)):
                with self.subTest(side=side, field=field):
                    changed = replace(self.current, **{field: value})
                    self.assert_non_grant(
                        self.evaluate(**{side: changed}),
                        "REFUSED", "current_identity_mismatch")

    def test_one_sided_current_binding_mismatches_refuse_before_drift(self):
        for side in ("current_before", "current_after"):
            for field, value in (
                    ("record_id", "0" * 64),
                    ("contract_id", "f" * 64),
                    ("base_revision", "0" * 40),
                    ("branch_ref", "refs/heads/task/340-other")):
                with self.subTest(side=side, field=field):
                    changed = replace(self.current, **{field: value})
                    self.assert_non_grant(
                        self.evaluate(**{side: changed}),
                        "REFUSED", "current_contract_or_record_mismatch")

    def test_one_sided_repository_name_drift_remains_unknown(self):
        for side in ("current_before", "current_after"):
            with self.subTest(side=side):
                changed = replace(self.current, repository="upiscium/RENAMED")
                self.assert_non_grant(
                    self.evaluate(**{side: changed}),
                    "UNKNOWN", "repository_name_drift")

    def test_wrong_current_binding_overrides_tip_or_name_drift(self):
        changed = replace(self.current, issue=self.ISSUE + 1,
                          metadata_tip="3" * 40)
        self.assert_non_grant(
            self.evaluate(current_after=changed),
            "REFUSED", "current_identity_mismatch")
        changed = replace(self.current, contract_id="f" * 64,
                          metadata_tip="3" * 40)
        self.assert_non_grant(
            self.evaluate(current_after=changed),
            "REFUSED", "current_contract_or_record_mismatch")
        # A same-ID rename in one snapshot cannot hide a wrong binding
        # in the other snapshot.
        renamed = replace(self.current, repository="upiscium/RENAMED")
        wrong_record = replace(self.current, record_id="0" * 64)
        self.assert_non_grant(
            self.evaluate(current_before=renamed, current_after=wrong_record),
            "REFUSED", "current_contract_or_record_mismatch")

    def test_main_name_drift_does_not_hide_one_sided_current_mismatch(self):
        for side in ("current_before", "current_after"):
            for field, value, reason in (
                    ("repository_id", self.REPO_ID + 1,
                     "current_identity_mismatch"),
                    ("issue", self.ISSUE + 1,
                     "current_identity_mismatch"),
                    ("record_id", "0" * 64,
                     "current_contract_or_record_mismatch"),
                    ("contract_id", "f" * 64,
                     "current_contract_or_record_mismatch")):
                with self.subTest(side=side, field=field):
                    changed = replace(self.current, **{field: value})
                    self.assert_non_grant(
                        self.evaluate(
                            main_repository_name="upiscium/RENAMED",
                            **{side: changed}),
                        "REFUSED", reason)

    def test_v2_name_drift_does_not_hide_registered_or_current_mismatch(self):
        changed_identity = replace(
            self.identity, repository="upiscium/RENAMED")
        changed_registered = replace(
            self.registered, branch_ref="refs/heads/task/340-other")
        self.assert_non_grant(
            self.evaluate(identity=changed_identity,
                          registered=changed_registered),
            "REFUSED", "registered_task_mismatch")
        self.assert_non_grant(
            self.evaluate(
                identity=changed_identity,
                current_after=replace(self.current, record_id="0" * 64)),
            "REFUSED", "current_contract_or_record_mismatch")

    def test_main_name_drift_alone_still_freezes_without_permissions(self):
        self.assert_non_grant(
            self.evaluate(main_repository_name="upiscium/RENAMED"),
            "UNKNOWN", "repository_name_drift")

    def test_current_only_old_repository_name_freezes_same_numeric_id(self):
        new_name = "upiscium/RENAMED"
        result = self.evaluate(
            identity=replace(self.identity, repository=new_name),
            registered=replace(self.registered, repository=new_name),
            approved=replace(self.approved, repository=new_name),
            selection=replace(self.selection, repository=new_name),
            main_repository_name=new_name,
            # Both same-ID current snapshots retain the old repository name.
            current_before=self.current, current_after=self.current,
        )
        self.assert_non_grant(result, "UNKNOWN", "repository_name_drift")

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

    def test_mutated_v2_dataclass_fields_refused_without_equality_hooks(self):
        called = []

        class EqualityTrap:
            def __eq__(self, _other):
                called.append("unexpected equality")
                raise AssertionError("forged v2 field equality executed")
            def __ne__(self, _other):
                called.append("unexpected inequality")
                raise AssertionError("forged v2 field inequality executed")

        for name, obj, field, forged in (
                ("identity", self.identity, "genesis", EqualityTrap()),
                ("identity", self.identity, "git_object_format", EqualityTrap()),
                ("registered", self.registered, "record_id", EqualityTrap()),
                ("registered", self.registered, "contract_id", EqualityTrap()),
                ("registered", self.registered, "branch_ref", EqualityTrap()),
                ("registered", self.registered, "base_revision", EqualityTrap()),
                ("approved", self.approved, "branch_ref", EqualityTrap()),
                ("approved", self.approved, "base_revision", EqualityTrap()),
                ("approved", self.approved, "title", EqualityTrap()),
                ("approved", self.approved, "body", EqualityTrap()),
                ("selection", self.selection, "attach_digest", "bad"),
                ("selection", self.selection, "attach_digest", EqualityTrap()),
        ):
            with self.subTest(name=name, field=field, forged=type(forged).__name__):
                old = getattr(obj, field)
                try:
                    # Bypass frozen dataclass constructor to simulate a forged
                    # exact-class in-process Python object, NOT a Host record.
                    object.__setattr__(obj, field, forged)
                    self.assert_non_grant(
                        self.evaluate(), "REFUSED", "v2_fields_invalid")
                finally:
                    object.__setattr__(obj, field, old)
        self.assertEqual(called, [])

    def test_mutated_approved_branch_missing_issue_is_refused(self):
        wrong_branch = "refs/heads/task/999-r3a"
        old_reg = self.registered.branch_ref
        old_approved = self.approved.branch_ref
        try:
            # Both canonical v2 classes were initially constructed validly.
            # Mutate them and the current snapshot to agree on a branch
            # that ApprovedIssue.__post_init__ would reject.
            object.__setattr__(self.registered, "branch_ref", wrong_branch)
            object.__setattr__(self.approved, "branch_ref", wrong_branch)
            current = replace(self.current, branch_ref=wrong_branch)
            self.assert_non_grant(
                self.evaluate(current_before=current, current_after=current),
                "REFUSED", "v2_fields_invalid")
        finally:
            object.__setattr__(self.registered, "branch_ref", old_reg)
            object.__setattr__(self.approved, "branch_ref", old_approved)

    def test_intermediate_dotlock_branch_is_refused_using_canonical_parser(self):
        wrong_branch = "refs/heads/task.lock/340-r3a"
        old_reg = self.registered.branch_ref
        old_approved = self.approved.branch_ref
        try:
            object.__setattr__(self.registered, "branch_ref", wrong_branch)
            object.__setattr__(self.approved, "branch_ref", wrong_branch)
            current = replace(self.current, branch_ref=wrong_branch)
            self.assert_non_grant(
                self.evaluate(current_before=current, current_after=current),
                "REFUSED", "v2_fields_invalid")
        finally:
            object.__setattr__(self.registered, "branch_ref", old_reg)
            object.__setattr__(self.approved, "branch_ref", old_approved)

    def test_malformed_unicode_in_approved_title_is_refused(self):
        old_title = self.approved.title
        try:
            object.__setattr__(self.approved, "title", "\ud800")
            self.assert_non_grant(
                self.evaluate(), "REFUSED", "v2_fields_invalid")
        finally:
            object.__setattr__(self.approved, "title", old_title)

    def test_r2_nonstring_dict_key_rejected_before_equality(self):
        called = []

        class KeyTrap:
            def __hash__(self):
                return hash("status")
            def __eq__(self, other):
                called.append("custom key equality called")
                return other == "status"

        forged = {k: v for k, v in self.r2_report.items() if k != "status"}
        forged[KeyTrap()] = "OBSERVED_BLOCKED"
        called.clear()
        self.assert_non_grant(
            self.evaluate(r2_report=forged), "REFUSED", "r2_report_invalid")
        self.assertEqual(called, [])

    def test_path_subclass_cannot_override_lexical_validity(self):
        called = []

        class ForgedPath(type(self.project)):
            def is_absolute(self):
                called.append("is_absolute")
                return True
            @property
            def parts(self):
                called.append("parts")
                return ("fictional",)
            def __truediv__(self, other):
                called.append("truediv")
                return self
            def __eq__(self, other):
                called.append("eq")
                return True
            __hash__ = type(self.project).__hash__

        bad_project = ForgedPath("relative/../forged")
        bad_worktree = ForgedPath("elsewhere")
        for change in (
                {"project_root": bad_project, "worktree_path": bad_worktree},
                {"worktree_path": bad_worktree},
                {"project_root": bad_project},
        ):
            with self.subTest(change=tuple(change)):
                self.assert_non_grant(
                    self.evaluate(**change), "REFUSED", "worktree_claim_invalid")
        self.assertEqual(called, [])

    def test_bool_numeric_field_and_str_subclass_are_refused(self):
        old_id = self.registered.repository_id
        try:
            object.__setattr__(self.registered, "repository_id", True)
            self.assert_non_grant(
                self.evaluate(), "REFUSED", "repository_numeric_invalid")
        finally:
            object.__setattr__(self.registered, "repository_id", old_id)

        class StrangeStr(str):
            pass

        old_digest = self.selection.attach_digest
        try:
            object.__setattr__(self.selection, "attach_digest", StrangeStr("e" * 64))
            self.assert_non_grant(
                self.evaluate(), "REFUSED", "v2_fields_invalid")
        finally:
            object.__setattr__(self.selection, "attach_digest", old_digest)

    def test_canonical_invalid_branch_characters_are_refused(self):
        for branch in ("refs/heads/task/340^invalid",
                       "refs/heads/task/340..bad",
                       "refs/heads/task/340@{1}"):
            with self.subTest(branch=branch):
                original = self.registered.branch_ref
                try:
                    object.__setattr__(self.registered, "branch_ref", branch)
                    self.assert_non_grant(
                        self.evaluate(), "REFUSED", "v2_fields_invalid")
                finally:
                    object.__setattr__(self.registered, "branch_ref", original)

    def test_mismatched_r2_status_and_reason_are_refused(self):
        for status, reason in (
                ("UNKNOWN", "existing_reservation_observed_only"),
                ("OBSERVED_BLOCKED", "host_journal_missing"),
                ("REFUSED", "history_absence_unproven")):
            with self.subTest(status=status, reason=reason):
                self.assert_non_grant(
                    self.evaluate(r2_report={
                        **self.r2_report, "status": status, "reason": reason,
                    }), "REFUSED", "r2_report_invalid")

    def test_uninitialized_exact_v2_dataclasses_are_refused(self):
        for name, cls in (
            ("identity", m.RepositoryIdentity),
            ("registered", m.RegisteredIssue),
            ("approved", m.ApprovedIssue),
            ("selection", m.V2Selection),
        ):
            with self.subTest(name=name):
                # Deliberately bypass __init__ and __post_init__. A frozen
                # class identity does not guarantee its slots were filled.
                broken = object.__new__(cls)
                self.assert_non_grant(
                    self.evaluate(**{name: broken}),
                    "REFUSED", "v2_fields_invalid")

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
                elif isinstance(node.func, ast.Name):
                    # Supplement the module's no-effect boundary check:
                    # an unqualified built-in open() is also forbidden.
                    self.assertNotIn(
                        node.func.id,
                        {"open", "eval", "exec", "__import__", "compile"},
                    )
        self.assertTrue(imported <= {
            "__future__", "pathlib", "re",
            "kagari_vnext_main_v2_observation",
            "kagari_metadata_v2_task_admission", "task_record", "<relative>",
        })
        for disallowed in (
                "observe", "_assert_owned", "mkdir", "open", "create",
                "read", "read_bytes", "write_bytes", "resolve", "stat",
                "spawn", "Popen", "run", "system", "unlink", "replace"):
            self.assertNotIn(disallowed, calls)


if __name__ == "__main__":
    unittest.main()
