"""Trusted-host #291 bridge: one explicitly approved GitHub Issue -> #191 TaskRecord.

An observed open Issue is NOT permission to create or execute a Task. The host
must supply an exact approved Issue snapshot and its own #191 AuthorityRequest
callback. All mutable metadata effects pass through existing TaskRecords CAS.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from pathlib import Path
import re
import sys

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
sys.path.insert(0, str(V4))
from metadata_ref import MetadataStore, MetadataRefError, MetadataConflictError
from task_record import (
    TaskRecords, TaskRecordError, AuthorityRequest,
    validate_branch_ref, encode_contract,
)

if __package__:
    from .kagari_vnext_main import MainError
else:
    from kagari_vnext_main import MainError


@dataclass(frozen=True)
class ApprovedIssueSnapshot:
    repository: str
    issue: int
    branch_ref: str
    base_revision: str
    title: str
    body: str


@dataclass(frozen=True)
class TaskBootstrapResult:
    state: str
    repository: str
    issue: int
    branch_ref: str
    base_revision: str
    contract_id: str
    record_id: str
    metadata_commit: str


def _validate_inputs(store: MetadataStore, approved: ApprovedIssueSnapshot) -> None:
    if not isinstance(store, MetadataStore) or type(approved) is not ApprovedIssueSnapshot:
        raise MainError("BOOTSTRAP_INVALID")
    if (type(approved.repository) is not str
            or approved.repository != store.repository
            or type(approved.issue) is not int
            or not 1 <= approved.issue <= 10**9):
        raise MainError("BOOTSTRAP_INVALID")
    try:
        validate_branch_ref(approved.branch_ref)
    except TaskRecordError:
        raise MainError("BOOTSTRAP_INVALID") from None
    if (not re.search(r"(^|[^0-9])" + str(approved.issue) + r"([^0-9]|$)",
                      approved.branch_ref)):
        raise MainError("BOOTSTRAP_INVALID")
    if (type(approved.base_revision) is not str
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",
                                approved.base_revision)):
        raise MainError("BOOTSTRAP_INVALID")
    if (type(approved.title) is not str or not approved.title.strip()
            or type(approved.body) is not str
            or len(approved.title.encode("utf-8")) > 64_000
            or len(approved.body.encode("utf-8")) > 160_000):
        raise MainError("BOOTSTRAP_INVALID")


def _confirm_issue(
    store: MetadataStore, approved: ApprovedIssueSnapshot,
    read_issue: Callable[[int], dict],
) -> None:
    try:
        actual = read_issue(approved.issue)
    except Exception:
        raise MainError("GITHUB_ISSUE_UNAVAILABLE") from None
    if (type(actual) is not dict
            or actual.get("repository") != store.repository
            or type(actual.get("number")) is not int
            or actual["number"] != approved.issue
            or actual.get("state") != "OPEN"
            or actual.get("title") != approved.title
            or actual.get("body") != approved.body):
        raise MainError("ISSUE_SNAPSHOT_DRIFT")


def bootstrap_approved_issue(
    store: MetadataStore,
    approved: ApprovedIssueSnapshot,
    *,
    read_issue: Callable[[int], dict],
    authorize_create: Callable[[AuthorityRequest], bool],
) -> TaskBootstrapResult:
    """Create or resume #191 Record, with an externally installed host grant.

    No second semantic authority exists. The callback is required and is
    invoked by #191 itself with the immutable proposed Contract Record.
    It must *not* be implemented by testing whether the Issue is simply open.
    A negative callback blocks creation; an identical existing Record is
    read-only and never replayed through another create/approval.
    """
    _validate_inputs(store, approved)
    if not callable(read_issue) or not callable(authorize_create):
        raise MainError("HOST_AUTHORITY_REQUIRED")
    _confirm_issue(store, approved, read_issue)
    try:
        store.validate_base_revision(approved.base_revision)
        tip = store.fetch_tip()
    except (MetadataRefError, ValueError):
        raise MainError("TASK_RECORD_INVALID") from None

    existing = None
    if tip is not None:
        try:
            existing = TaskRecords(store, authorize=lambda _req: False).read(
                tip, task=str(approved.issue),
                branch_ref=approved.branch_ref,
                base_revision=approved.base_revision)
        except (TaskRecordError, MetadataRefError, ValueError):
            raise MainError("TASK_RECORD_MISMATCH") from None
    if existing is not None:
        record_id, record, contract = existing
        if "disposition" in record["payload"]:
            raise MainError("TASK_RETIRED")
        if (contract["payload"]["title"] != approved.title
                or contract["payload"]["body"] != approved.body):
            raise MainError("TASK_CONTRACT_CHANGED")
        return TaskBootstrapResult(
            "RESUMED", approved.repository, approved.issue,
            approved.branch_ref, approved.base_revision,
            record["payload"]["contract_id"], record_id, tip)

    # Freeze expected bytes before the existing #191 host callback. The
    # callback must bind the exact branch/base/record/contract proposal.
    contract_id, _ = encode_contract(
        approved.repository, str(approved.issue), approved.base_revision,
        {"schema_version": 1, "title": approved.title, "body": approved.body})
    # Recheck the actual GitHub Issue immediately before writing.
    _confirm_issue(store, approved, read_issue)
    try:
        published = TaskRecords(store, authorize=authorize_create).create(
            task=str(approved.issue),
            branch_ref=approved.branch_ref, base_revision=approved.base_revision,
            title=approved.title, body=approved.body)
    except MetadataConflictError:
        # A competing approved Task publisher might have won the CAS. Do not
        # automatically retry writing; the caller may explicitly re-read.
        raise MainError("TASK_RECORD_CONFLICT") from None
    except (TaskRecordError, MetadataRefError, ValueError):
        raise MainError("TASK_RECORD_PUBLICATION_REFUSED") from None
    if published.contract_id != contract_id:
        raise MainError("TASK_RECORD_POSTCONDITION_MISMATCH")
    # Don't rely on a success ACK alone; ensure #191 sees the exact record
    # through its canonical metadata plane, without exposing extra authority.
    try:
        tip = store.fetch_tip()
        if tip is None:
            raise MainError("TASK_RECORD_POSTCONDITION_MISMATCH")
        found = TaskRecords(store, authorize=lambda _req: False).read(
            tip, task=str(approved.issue),
            branch_ref=approved.branch_ref,
            base_revision=approved.base_revision)
    except (TaskRecordError, MetadataRefError, ValueError):
        raise MainError("TASK_RECORD_POSTCONDITION_MISMATCH") from None
    if (found is None or found[0] != published.record_id
            or found[1]["payload"]["contract_id"] != published.contract_id):
        raise MainError("TASK_RECORD_POSTCONDITION_MISMATCH")
    return TaskBootstrapResult(
        "CREATED", approved.repository, approved.issue,
        approved.branch_ref, approved.base_revision,
        published.contract_id, published.record_id, tip)
