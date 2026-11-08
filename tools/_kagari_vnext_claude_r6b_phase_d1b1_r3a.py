"""#340 D1B1 R3A: pure, non-authoritative v2 Task + R2 observations.

SOURCE-ONLY / SYNTHETIC INPUTS / NOT WIRED / NEVER EXECUTES.
This validates consistency of EXISTING Metadata v2 data types and one
caller-provided R2 diagnostic. It does not fetch current metadata, inspect a
Host, verify ownership, inspect a Worktree, read a Journal, or grant a Task.
Even an exact match is a *claim*, not provenance or authorization.
"""
from __future__ import annotations

from pathlib import Path
import re

# Import the canonical top-level v2 class identities via the existing Main
# observation module; do not load a second copy under the 'tools.' prefix.
# Importing existing source modules is not a Main/Task activation.
from .kagari_vnext_main_v2_observation import CurrentTask, V2Selection, V2_PROFILE
from kagari_metadata_v2_task_admission import (
    ApprovedIssue, RegisteredIssue, RepositoryIdentity,
)
from . import _kagari_vnext_claude_r6b_phase_d1b1_r1 as r1
from . import _kagari_vnext_claude_r6b_phase_d1b1_r2 as r2

SCOPE = "R6B_D1B1_R3A_UNTRUSTED_V2_R2_NEGATIVE_PATH"
_STAGE = "OBSERVATION_ONLY"
_R2_REASONS = {
    "REFUSED": frozenset((
        "repository_issue_invalid", "owner_home_invalid",
        "owner_home_unsafe", "host_journal_unsafe", "root_replaced",
    )),
    "UNKNOWN": frozenset((
        "owner_home_missing", "host_journal_missing",
        "history_absence_unproven", "journal_read_or_history_uncertain",
        "unverified_host_state",
    )),
    "OBSERVED_BLOCKED": frozenset(("existing_reservation_observed_only",)),
}
_NON_GRANTS = (
    "dispatch_allowed", "replay_allowed", "task_completed",
    "commit_allowed", "authenticated_absence",
)
_R2_KEYS = frozenset((
    "status", "reason", "qualification", "stage", "evidence",
    "dispatch_allowed", "replay_allowed", "task_completed",
    "commit_allowed", "authenticated_absence", "effective_policy",
))
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}\Z", re.ASCII)


def _report(status: str, reason: str) -> dict[str, str | bool]:
    return {
        "status": status, "reason": reason, "qualification": "UNQUALIFIED",
        "stage": _STAGE, "evidence": SCOPE,
        "dispatch_allowed": False, "replay_allowed": False,
        "task_completed": False, "commit_allowed": False,
        "authenticated_absence": False, "effective_policy": "UNVERIFIED",
    }


def _valid_current(value: object) -> bool:
    if type(value) is not CurrentTask:
        return False
    return (type(value.repository_id) is int and 1 <= value.repository_id <= r1.REPO_MAX
            and type(value.issue) is int and 1 <= value.issue <= r1.ISSUE_MAX
            and type(value.repository) is str and bool(value.repository)
            and all(type(getattr(value, field)) is str
                    and _OID.fullmatch(getattr(value, field)) is not None
                    for field in ("metadata_tip", "archive_tip", "base_revision"))
            and all(type(getattr(value, field)) is str
                    and re.fullmatch(r"[0-9a-f]{64}", getattr(value, field))
                    is not None for field in ("record_id", "contract_id"))
            and type(value.branch_ref) is str and value.branch_ref.startswith("refs/heads/"))


def _valid_path(value: object) -> bool:
    return isinstance(value, Path) and value.is_absolute() and ".." not in value.parts


def _valid_r2_report(value: object) -> bool:
    if type(value) is not dict or value.keys() != _R2_KEYS:
        return False
    status = value.get("status")
    if type(status) is not str or status not in _R2_REASONS:
        return False
    return (
        type(value.get("reason")) is str
        and value["reason"] in _R2_REASONS[status]
        and all(type(value[field]) is str for field in (
            "qualification", "stage", "evidence", "effective_policy"))
        and value["qualification"] == "UNQUALIFIED"
        and value["stage"] == "READ_ONLY"
        and value["evidence"] == r2.EVIDENCE
        and value["effective_policy"] == "UNVERIFIED"
        and all(value[field] is False for field in _NON_GRANTS)
    )


def assess_r3a_unqualified(
    *,
    identity: object,
    registered: object,
    approved: object,
    selection: object,
    current_before: object,
    current_after: object,
    main_repository_name: object,
    main_owner_claim: object,
    attachment_claim: object,
    legacy_collision_claim: object,
    project_root: object,
    worktree_path: object,
    r2_report: object,
) -> dict[str, str | bool]:
    """Compare *caller-provided* existing v2 shapes; NEVER grant permission.

    The inputs are not verified Host observations. In particular, a claimed
    Main owner, Attachment or CurrentTaskReader result may be fabricated.
    R3A deliberately calls no CurrentTaskReader.observe(), MainSupervisor
    lock, Worktree allocator, R2 filesystem observer or CLI.
    """
    try:
        if not (type(identity) is RepositoryIdentity
                and type(registered) is RegisteredIssue
                and type(approved) is ApprovedIssue
                and type(selection) is V2Selection):
            return _report("REFUSED", "v2_shape_invalid")
        if type(selection.profile) is not str or selection.profile != V2_PROFILE:
            return _report("REFUSED", "v2_profile_invalid")
        try:
            r1.reservation_key(identity.repository_id, registered.issue)
        except ValueError:
            return _report("REFUSED", "repository_numeric_invalid")
        if any(type(x) is not int or not 1 <= x <= r1.REPO_MAX for x in (
                identity.repository_id, registered.repository_id,
                approved.repository_id, selection.repository_id)):
            return _report("REFUSED", "repository_numeric_invalid")
        if len({identity.repository_id, registered.repository_id,
                approved.repository_id, selection.repository_id}) != 1:
            return _report("REFUSED", "repository_numeric_mismatch")
        if type(main_repository_name) is not str or not main_repository_name:
            return _report("REFUSED", "main_repository_invalid")
        names = (identity.repository, registered.repository,
                 approved.repository, selection.repository,
                 main_repository_name)
        if not all(type(name) is str for name in names):
            return _report("REFUSED", "repository_name_invalid")
        if len(set(names)) != 1:
            # Numeric IDs agree, so a textual alias/rename may be in flight.
            # The name-keyed Main flock could split: freeze, never re-key.
            return _report("UNKNOWN", "repository_name_drift")
        if not all(type(x) is int and 1 <= x <= r1.ISSUE_MAX
                   for x in (registered.issue, approved.issue, selection.issue)):
            return _report("REFUSED", "issue_invalid")
        if (registered.issue != selection.issue or approved.issue != selection.issue
                or registered.branch_ref != approved.branch_ref
                or registered.base_revision != approved.base_revision):
            return _report("REFUSED", "registered_task_mismatch")
        if type(current_before) is not CurrentTask or type(current_after) is not CurrentTask:
            return _report("UNKNOWN", "current_task_missing")
        if not _valid_current(current_before) or not _valid_current(current_after):
            return _report("REFUSED", "current_task_invalid")
        if current_before != current_after:
            return _report("UNKNOWN", "current_task_drift")
        if (current_before.repository_id, current_before.repository,
                current_before.issue) != (
                identity.repository_id, identity.repository, selection.issue):
            return _report("REFUSED", "current_identity_mismatch")
        if (current_before.record_id, current_before.contract_id,
                current_before.branch_ref, current_before.base_revision) != (
                registered.record_id, registered.contract_id,
                registered.branch_ref, registered.base_revision):
            return _report("REFUSED", "current_contract_or_record_mismatch")
        if type(main_owner_claim) is not str or main_owner_claim != "HELD_CLAIMED":
            return _report("REFUSED", "main_owner_unverified")
        if type(attachment_claim) is not str or attachment_claim != "HELD_CLAIMED":
            return _report("REFUSED", "attachment_unverified")
        if type(legacy_collision_claim) is not str:
            return _report("UNKNOWN", "legacy_assignment_unverified")
        if legacy_collision_claim == "PRESENT":
            return _report("REFUSED", "legacy_assignment_collision")
        if legacy_collision_claim != "CLEAR_CLAIMED":
            return _report("UNKNOWN", "legacy_assignment_unverified")
        if not _valid_path(project_root) or not _valid_path(worktree_path):
            return _report("REFUSED", "worktree_claim_invalid")
        if worktree_path != project_root / ".worktrees" / str(selection.issue):
            return _report("REFUSED", "worktree_claim_mismatch")
        # Purely lexical equality is NOT inode, symlink, Git common-dir or
        # registered Worktree evidence. Future Host must verify separately.
        if r2_report is None:
            return _report("UNKNOWN", "journal_observation_missing")
        if not _valid_r2_report(r2_report):
            return _report("REFUSED", "r2_report_invalid")
        if r2_report["status"] == "REFUSED":
            return _report("REFUSED", "r2_observation_refused")
        if r2_report["status"] == "UNKNOWN":
            return _report("UNKNOWN", "r2_history_or_root_unverified")
        return _report("OBSERVED_BLOCKED", "r2_reservation_claim_blocks_only")
    except Exception:
        # A malformed slots dataclass or adversarial in-process object can
        # never manufacture a permit or leak a supplied path/identity.
        return _report("UNKNOWN", "untrusted_snapshot_invalid")
