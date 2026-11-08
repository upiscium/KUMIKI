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
from task_record import TaskRecordError, validate_branch_ref
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
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REPO = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z",
    re.ASCII,
)
_CONCRETE_PATH_TYPE = type(Path())  # PosixPath on the supported Linux Host


def _repo(value: object) -> bool:
    return (type(value) is str and len(value) <= 512
            and _REPO.fullmatch(value) is not None)


def _oid(value: object) -> bool:
    return type(value) is str and _OID.fullmatch(value) is not None


def _sha(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _branch(value: object) -> bool:
    # Reuse the existing Metadata v2 Task branch grammar, including .lock
    # on intermediate components, instead of duplicating a partial parser.
    # Strict primitive type first: no caller-defined string operations.
    if type(value) is not str or len(value) > 512:
        return False
    try:
        return validate_branch_ref(value) == value
    except (TaskRecordError, ValueError):
        return False


def _bounded_utf8(value: object, maximum: int, *, required: bool = False) -> bool:
    if type(value) is not str or (required and not value.strip()):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum
    except UnicodeError:
        return False


def _complete_v2_fields(identity: RepositoryIdentity,
                        registered: RegisteredIssue,
                        approved: ApprovedIssue,
                        selection: V2Selection) -> bool:
    """Reject uninitialized exact-class dataclasses before scalar lookup.

    Existing canonical classes use frozen slots, but object.__new__ can
    produce an exact-class instance with missing required fields.
    """
    for obj, names in (
        (identity, ("repository_id", "repository", "genesis", "git_object_format")),
        (registered, ("repository_id", "repository", "issue", "branch_ref",
                      "base_revision", "record_id", "contract_id")),
        (approved, ("repository_id", "repository", "issue", "branch_ref",
                    "base_revision", "title", "body")),
        (selection, ("profile", "repository_id", "repository", "issue",
                     "attach_digest")),
    ):
        try:
            for name in names:
                object.__getattribute__(obj, name)
        except AttributeError:
            return False
    return True


def _valid_v2_fields(identity: RepositoryIdentity,
                     registered: RegisteredIssue,
                     approved: ApprovedIssue,
                     selection: V2Selection) -> bool:
    """Revalidate every constructor-field shape before ANY v2 comparison.

    Frozen/slotted dataclasses can be modified using object.__setattr__;
    type(instance) alone does not ensure __post_init__ ran or still holds.
    Checking exact primitive types first excludes custom __eq__ callbacks.
    This proves no origin, Main owner, history or execution permission.
    """
    return (
        _repo(identity.repository) and _oid(identity.genesis)
        and type(identity.git_object_format) is str
        and identity.git_object_format == "sha1"
        and _repo(registered.repository)
        and _branch(registered.branch_ref)
        and _oid(registered.base_revision)
        and _sha(registered.record_id) and _sha(registered.contract_id)
        and _repo(approved.repository)
        and _branch(approved.branch_ref)
        # ApprovedIssue's constructor requires the Issue number as a
        # separate numeric token in the branch name, not a substring.
        and re.search(r"(^|[^0-9])" + str(approved.issue) + r"([^0-9]|$)",
                      approved.branch_ref) is not None
        and _oid(approved.base_revision)
        and _bounded_utf8(approved.title, 64000, required=True)
        and _bounded_utf8(approved.body, 160000)
        and type(selection.profile) is str
        and selection.profile == V2_PROFILE
        and _repo(selection.repository)
        and _sha(selection.attach_digest)
    )


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
            and _repo(value.repository)
            and all(_oid(getattr(value, field))
                    for field in ("metadata_tip", "archive_tip", "base_revision"))
            and all(_sha(getattr(value, field))
                    for field in ("record_id", "contract_id"))
            and _branch(value.branch_ref))


def _valid_path(value: object) -> bool:
    # No subclass hooks: is_absolute, parts, joining and equality must not
    # dispatch user-defined code. This is still lexical, not inode proof.
    return (type(value) is _CONCRETE_PATH_TYPE and value.is_absolute()
            and ".." not in value.parts)


def _valid_r2_report(value: object) -> bool:
    if type(value) is not dict:
        return False
    # Check key types BEFORE dict_keys equality or string lookups. An
    # arbitrary key with __hash__/__eq__ could otherwise impersonate 'status'.
    if any(type(key) is not str for key in value):
        return False
    if value.keys() != _R2_KEYS:
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
        if not _complete_v2_fields(identity, registered, approved, selection):
            return _report("REFUSED", "v2_fields_invalid")
        if type(selection.profile) is not str or selection.profile != V2_PROFILE:
            return _report("REFUSED", "v2_profile_invalid")
        # No digest/key derivation is necessary in a negative-path check.
        # Validate exact primitive numeric ranges BEFORE any comparison.
        if any(type(x) is not int or not 1 <= x <= r1.REPO_MAX for x in (
                identity.repository_id, registered.repository_id,
                approved.repository_id, selection.repository_id)):
            return _report("REFUSED", "repository_numeric_invalid")
        if not all(type(x) is int and 1 <= x <= min(r1.ISSUE_MAX, 10**9)
                   for x in (registered.issue, approved.issue, selection.issue)):
            return _report("REFUSED", "issue_invalid")
        # Frozen dataclasses may have been forged/mutated post-construction.
        # Reject invalid names and all other fields BEFORE name drift checks.
        if not _valid_v2_fields(identity, registered, approved, selection):
            return _report("REFUSED", "v2_fields_invalid")
        if len({identity.repository_id, registered.repository_id,
                approved.repository_id, selection.repository_id}) != 1:
            return _report("REFUSED", "repository_numeric_mismatch")
        if not _repo(main_repository_name):
            return _report("REFUSED", "main_repository_invalid")
        names = (identity.repository, registered.repository,
                 approved.repository, selection.repository,
                 main_repository_name)
        if len(set(names)) != 1:
            # All names are now well-formed, so a same-ID alias/rename
            # may be in flight. Freeze admission; never re-key.
            return _report("UNKNOWN", "repository_name_drift")
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
        if (current_before.repository_id, current_before.issue) != (
                identity.repository_id, selection.issue):
            return _report("REFUSED", "current_identity_mismatch")
        # A same-numeric-ID current record with a different repository name
        # is an alias/rename reconciliation freeze, not a new identity.
        if current_before.repository != identity.repository:
            return _report("UNKNOWN", "repository_name_drift")
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
