"""Private R6B Phase B contracts, NOT a launcher or a dispatch capability.

Only the Main observation reads authority (#191 / live Issue / owner). Policy
and receipt functions are pure, synthetic self-consistency observations. There
is no CLI, transport injection, runtime-root access, receipt writer or retry.
Nothing here qualifies effective policy, even when every synthetic claim matches.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from . import kagari_vnext_claude_lane_b as lane
from . import kagari_vnext_claude_r6b_admission as phase_a
from .kagari_vnext_executor_selection import CLAUDE_CLI
from .kagari_vnext_main import MainError, RepositoryMain
from .kagari_vnext_main_service import MainSupervisor


_POLICY_VERSION = "r6b-phase-b-no-tools-v1"
_SYNTHETIC_SOURCE = "SYNTHETIC_TRUSTED_QUALIFIER"
# A closed list of required *claims*, not detection logic or an authority registry.
# A later trusted, exact-version qualifier must establish all of these together.
_REQUIRED_CHECKS = (
    ("binary_provenance", "MATCHED"),
    ("settings_precedence_and_all_sources", "RESOLVED"),
    ("managed_local_and_remote_settings", "RESOLVED"),
    ("personal_profile", "PINNED_NO_OVERRIDE"),
    ("environment", "CHECKED_NO_OVERRIDE"),
    ("hooks_all_lifecycle_events", "EMPTY_EFFECTIVE"),
    ("plugins", "EMPTY_EFFECTIVE"),
    ("mcp_servers_and_tools", "EMPTY_EFFECTIVE"),
    ("builtin_and_deferred_tools", "EMPTY_EFFECTIVE"),
    ("one_shot_no_resume", "MATCHED"),
    ("context_through_effect_boundary", "IMMUTABLE"),
)
_TASK_FIELDS = (
    "repository", "remote", "issue", "branch_ref", "base_revision", "contract_id",
    "project_root", "worktree", "git_common_dir", "issue_snapshot_sha256",
)


@dataclass(frozen=True, slots=True)
class _PolicyPins:
    """Expected host-selected digests, NOT self-authenticating evidence.

    Config context must cover executable/profile canonical identities. Settings
    must cover the complete resolved policy, including managed/remote sources;
    the environment digest covers the exact proposed child environment. This
    phase neither collects these facts nor accepts a live qualifier.
    """

    binary_sha256: str
    config_context_sha256: str
    effective_settings_sha256: str
    environment_sha256: str
    qualification_report_sha256: str


@dataclass(frozen=True, slots=True)
class _OperationIntent:
    """Frozen observation of one selected Task/diagnostic, never an approval."""

    operation: str
    policy_version: str
    executor: str
    repository: str
    remote: str
    issue: int
    branch_ref: str
    base_revision: str
    contract_id: str
    project_root: str = field(repr=False)
    worktree: str = field(repr=False)
    git_common_dir: str = field(repr=False)
    issue_snapshot_sha256: str
    session_id: str = field(repr=False)
    argv_sha256: str
    policy_pins: _PolicyPins
    policy_sha256: str


@dataclass(frozen=True, slots=True)
class _PolicyEvidence:
    source: str
    intent_sha256: str
    session_id: str
    policy_sha256: str
    pins: _PolicyPins
    cli_version: str
    profile: str
    environment_overrides: tuple[str, ...]
    checks: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _PendingReceipt:
    """Payload to be durably recorded BEFORE an effect in a later phase.

    Constructing this value establishes neither persistence nor permission.
    No content, raw settings/environment, credentials, usage or tokens belong here.
    """

    schema_version: int
    namespace: str
    task_sha256: str
    intent_sha256: str
    session_sha256: str
    operation: str
    policy_version: str
    policy_sha256: str
    state: str


@dataclass(frozen=True, slots=True)
class _ResultReceipt:
    schema_version: int
    pending_sha256: str
    status: str
    reason: str
    stage: str


def _digest(value: Any) -> str:
    return lane._sha256(lane._canonical_json(value))


def _valid_pins(value: object) -> bool:
    return type(value) is _PolicyPins and all(
        type(getattr(value, field.name)) is str
        and lane._CONTRACT_ID.fullmatch(getattr(value, field.name))
        for field in fields(_PolicyPins)
    )


def _policy_digest(session_id: str, pins: _PolicyPins) -> str:
    return _digest({
        "version": _POLICY_VERSION, "operation": lane.OPERATION,
        "cli_version": lane.EXPECTED_VERSION,
        "argv_sha256": _digest(lane._argv(session_id)),
        "prompt_sha256": lane._sha256(lane.FIXED_PROMPT.encode("ascii")),
        "pins": asdict(pins), "required_checks": _REQUIRED_CHECKS,
    })


def _valid_intent(value: object) -> bool:
    if type(value) is not _OperationIntent or not _valid_pins(value.policy_pins):
        return False
    if (type(value.issue) is not int
            or any(type(getattr(value, f.name)) is not str for f in fields(value)
                   if f.name not in {"issue", "policy_pins"})):
        return False
    paths = (value.project_root, value.worktree, value.git_common_dir)
    if any(not Path(p).is_absolute() or str(Path(p)) != p or ".." in Path(p).parts
           for p in paths):
        return False
    task = lane.LaneBTask(
        Path(value.project_root), Path(value.worktree), value.repository, value.issue,
        value.branch_ref, value.base_revision, value.contract_id, value.remote,
    )
    return bool(
        lane._validate_task_locator(task, value.session_id)
        and lane._CONTRACT_ID.fullmatch(value.issue_snapshot_sha256)
        and value.operation == lane.OPERATION and value.executor == CLAUDE_CLI
        and value.policy_version == _POLICY_VERSION
        and value.argv_sha256 == _digest(lane._argv(value.session_id))
        and value.policy_sha256 == _policy_digest(value.session_id, value.policy_pins)
    )


def _v2_selected(main: RepositoryMain) -> bool:
    return any((main.v2_readonly_for_issue, main.v2_worktree_for_issue,
                main.v2_session_for_issue, main.v2_pr_first_for_issue,
                main.v2_product_pr_for_issue))


def _observe_main_intent(
    main: RepositoryMain, task: lane.LaneBTask, session_id: str, *, pins: _PolicyPins,
) -> tuple[_OperationIntent | None, dict[str, Any]]:
    """Recheck existing authority under its live guard; return only an observation.

    Metadata/Issue adapters can perform I/O: this is private and non-wired, and
    offline tests replace only those adapters. Repeating this observation cannot
    authorize a retry. There is deliberately no independent attempt/approval DB.
    """
    if (type(main) is not RepositoryMain or type(task) is not lane.LaneBTask
            or type(session_id) is not str or not _valid_pins(pins)
            or not lane._validate_task_locator(task, session_id)):
        return None, lane._public_result("UNQUALIFIED", "R6B_INPUT_INVALID")
    # Do not retain mutable containers or aliases to the supplied dataclasses.
    frozen_task, frozen_pins = replace(task), replace(pins)
    try:
        with main._main_owner_guard:
            owner = main._main_owner
            if type(owner) is not MainSupervisor:
                return None, lane._public_result("UNQUALIFIED", "MAIN_OWNER_REQUIRED")
            owner._assert_owned(main)
            if _v2_selected(main) or owner.admission_only or owner.publication_only:
                return None, lane._public_result("UNQUALIFIED", "V2_MAIN_EXECUTION_NOT_QUALIFIED")
            store = main.store
            store_fields = (store.repository, store.remote, store.root)
            selected = main._choice_fields(main._selected_choice(task.issue))
            observed = phase_a._observe_main_owned_binding(main, frozen_task, session_id)
            if observed["stage"] != "TASK_BINDING_OBSERVED":
                return None, observed
            choice, snapshot = main._spec(frozen_task.issue)
            snapshot_digest = _digest(snapshot)
            identity = lane._task_identity(frozen_task)
            if identity is None:
                return None, lane._public_result("UNQUALIFIED", "R6B_TASK_BINDING_INVALID")
            intent = _OperationIntent(
                lane.OPERATION, _POLICY_VERSION, CLAUDE_CLI,
                frozen_task.repository, frozen_task.remote, frozen_task.issue,
                frozen_task.branch_ref, frozen_task.base_revision, identity.contract_id,
                str(identity.project), str(identity.worktree), str(identity.common_dir),
                snapshot_digest, session_id, _digest(lane._argv(session_id)), frozen_pins,
                _policy_digest(session_id, frozen_pins),
            )
            # External Issue/metadata readers are not authority to change the
            # selected Task, retire it, switch store/owner or weaken v2 exclusion.
            again, live_snapshot = main._spec(frozen_task.issue)
            if (main._choice_fields(choice) != selected
                    or main._choice_fields(again) != selected
                    or _digest(live_snapshot) != snapshot_digest
                    or lane._task_identity(frozen_task) != identity
                    or main._choice_fields(main._selected_choice(frozen_task.issue)) != selected
                    or main.store is not store
                    or (store.repository, store.remote, store.root) != store_fields
                    or task != frozen_task or pins != frozen_pins
                    or _v2_selected(main) or owner.admission_only or owner.publication_only
                    or not _valid_intent(intent)):
                return None, lane._public_result("UNKNOWN", "R6B_RECONCILIATION_REQUIRED")
            owner._assert_owned(main)
            return intent, lane._public_result(
                "UNQUALIFIED", "SOURCE_ONLY_NOT_QUALIFIED",
                stage="OPERATION_INTENT_OBSERVED", session_id=session_id,
            )
    except MainError as error:
        # Only closed, non-content diagnostics may escape this boundary.
        if error.code in {"TASK_RETIRED", "TASK_CONTRACT_CHANGED", "ISSUE_SNAPSHOT_DRIFT"}:
            return None, lane._public_result("UNQUALIFIED", error.code)
        return None, lane._public_result("UNKNOWN", "R6B_RECONCILIATION_REQUIRED")
    except Exception:
        return None, lane._public_result("UNKNOWN", "R6B_RECONCILIATION_REQUIRED")


def _observe_policy(intent: _OperationIntent, evidence: _PolicyEvidence) -> dict[str, Any]:
    """Pure closed-schema check; a matching synthetic report is NOT qualification."""
    if not _valid_intent(intent) or type(evidence) is not _PolicyEvidence:
        return lane._public_result("UNQUALIFIED", "POLICY_EVIDENCE_INVALID")
    scalar_names = ("source", "intent_sha256", "session_id", "policy_sha256",
                    "cli_version", "profile")
    if (any(type(getattr(evidence, name)) is not str for name in scalar_names)
            or not _valid_pins(evidence.pins)
            or type(evidence.environment_overrides) is not tuple
            or evidence.environment_overrides != ()
            or type(evidence.checks) is not tuple
            or any(type(pair) is not tuple or len(pair) != 2
                   or any(type(part) is not str for part in pair) for pair in evidence.checks)):
        return lane._public_result("UNQUALIFIED", "POLICY_EVIDENCE_INVALID")
    if (evidence.source != _SYNTHETIC_SOURCE or evidence.checks != _REQUIRED_CHECKS
            or evidence.intent_sha256 != _digest(asdict(intent))
            or evidence.session_id != intent.session_id
            or evidence.policy_sha256 != intent.policy_sha256
            or evidence.pins != intent.policy_pins
            or evidence.cli_version != lane.EXPECTED_VERSION or evidence.profile != "personal"):
        return lane._public_result("UNQUALIFIED", "POLICY_EVIDENCE_UNVERIFIED")
    return lane._public_result(
        "UNQUALIFIED", "SYNTHETIC_POLICY_MATCH_NOT_QUALIFICATION",
        stage="POLICY_EVIDENCE_OBSERVED", session_id=intent.session_id,
    )


def _pending_contract(intent: _OperationIntent) -> _PendingReceipt | None:
    """Pure payload + relative namespace; no root, file access, reservation or effect.

    Namespace keys only repository + issue: changing clone/common-dir, session,
    Contract, policy, remote or branch must not evade an earlier uncertain attempt.
    Exact Task paths/common-dir remain bound in the task and intent digests.
    """
    if not _valid_intent(intent):
        return None
    task_sha256 = _digest({name: getattr(intent, name) for name in _TASK_FIELDS})
    namespace = "r6b-phase-b-" + _digest({
        "repository": intent.repository, "issue": intent.issue,
    })
    return _PendingReceipt(
        1, namespace, task_sha256, _digest(asdict(intent)),
        _digest({"task_sha256": task_sha256, "session_id": intent.session_id,
                 "operation": intent.operation, "policy_sha256": intent.policy_sha256}),
        intent.operation, intent.policy_version, intent.policy_sha256, "PENDING_BEFORE_EFFECT",
    )


def _observe_receipts(
    intent: _OperationIntent, *, pending: tuple[_PendingReceipt, ...] | None,
    results: tuple[_ResultReceipt, ...] | None,
) -> dict[str, Any]:
    """Pure historical view of an unverified inventory, never start/status I/O.

    None means unavailable inventory, () only asserts absence: both are UNKNOWN.
    Multiple entries mean duplication, not 'pick latest'. Even a pair denies replay.
    This function cannot authenticate inventory completeness or durable storage.
    """
    unknown = lane._public_result("UNKNOWN", "RECEIPT_RECONCILIATION_REQUIRED")
    expected = _pending_contract(intent)
    if expected is None or type(pending) is not tuple or type(results) is not tuple:
        return unknown
    if len(pending) != 1 or len(results) != 1:
        return unknown
    first, last = pending[0], results[0]
    if (type(first) is not _PendingReceipt or type(last) is not _ResultReceipt
            or type(first.schema_version) is not int or type(last.schema_version) is not int
            or any(type(getattr(first, f.name)) is not str for f in fields(first)
                   if f.name != "schema_version")
            or any(type(getattr(last, f.name)) is not str for f in fields(last)
                   if f.name != "schema_version")
            or first != expected or last.schema_version != 1
            or last.pending_sha256 != _digest(asdict(first))
            or (last.status, last.reason, last.stage) != (
                "OBSERVED", "SYNTHETIC_TURN_RESULT", "TURN_RESULT_OBSERVED")):
        return unknown
    return lane._public_result(
        "UNQUALIFIED", "SYNTHETIC_RECEIPT_PAIR_NO_REPLAY",
        stage="RECEIPT_PAIR_OBSERVED", session_id=intent.session_id,
    )
