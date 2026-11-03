"""#337 Phase 2B2: source-only v2 current-Task/Worktree Host admission.

No CLI, install, deployment, Task/model/Session launch or GitHub transport.
Reuse the existing #256 verified attachment lease and #286 Git allocator.
The Host must install an independent explicit v2 grant and prove legacy
same-Issue assignment absence; a past v2 checkpoint is never current authority.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import sys

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
sys.path.insert(0, str(V4))

import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_bootstrap import ApprovedIssue, BootstrapIdentity
from kagari_metadata_v2_host_binding import RegisteredIssue, RepositoryIdentity
from issue_worktree import Allocation, IssueWorktreeAllocator, IssueWorktreeError
from metadata_ref import MetadataStore, MetadataRefError
from metadata_codec import object_path
from metadata_v2 import ARCHIVE_REF, InvalidMetadata, Limits, ObjectReader, Observer, Validation, issue_ref
from task_record import decode_contract, decode_record, TaskRecordError


V2_PROFILE = "metadata-v2-linked-worktree"


class AdmissionRefused(ValueError):
    """No v2 Task or Worktree may be admitted from this observation."""


def need(ok: bool, code: str) -> None:
    if not ok:
        raise AdmissionRefused(code)


@dataclass(frozen=True, slots=True)
class V2Selection:
    """Separately opted-in Host lane. Never derived from a v1 plan/profile."""

    profile: str
    repository_id: int
    repository: str
    issue: int
    attach_digest: str

    def __post_init__(self) -> None:
        need(type(self.profile) is str and self.profile == V2_PROFILE,
             "V2_PROFILE_EXPLICIT_OPT_IN_REQUIRED")
        need(type(self.repository_id) is int and self.repository_id > 0
             and type(self.issue) is int and self.issue > 0
             and type(self.repository) is str and bool(self.repository),
             "V2_SELECTION_INVALID")
        need(type(self.attach_digest) is str and len(self.attach_digest) == 64
             and all(c in "0123456789abcdef" for c in self.attach_digest),
             "V2_ATTACHMENT_DIGEST_INVALID")


@dataclass(frozen=True, slots=True)
class CurrentTask:
    """Immutable observation, not transferable authorization or a future lease."""

    repository_id: int
    repository: str
    issue: int
    metadata_tip: str
    archive_tip: str
    record_id: str
    contract_id: str
    base_revision: str
    branch_ref: str


@dataclass(frozen=True, slots=True)
class V2AdmissionRequest:
    """Host-owned, one-operation approval and legacy assignment check."""

    operation: str
    selection: V2Selection
    metadata_tip: str
    record_id: str
    contract_id: str
    branch_ref: str
    base_revision: str


class CurrentTaskReader:
    """Validate *current* Issue metadata tip; never use an old ExactLocator.

    Trusted Host supplies exact direct ref observer, bounded Git object reader,
    verified repository/Task identity and current Issue requirement facts.
    """

    def __init__(
        self, identity: RepositoryIdentity, registered: RegisteredIssue,
        approved: ApprovedIssue, *, reader: ObjectReader, observer: Observer,
        limits: Limits = Limits(),
    ) -> None:
        need(type(identity) is RepositoryIdentity
             and type(registered) is RegisteredIssue
             and type(approved) is ApprovedIssue
             and type(limits) is Limits and callable(reader)
             and callable(observer), "V2_CURRENT_HOST_CAPABILITIES_INVALID")
        need((identity.repository_id, identity.repository)
             == (registered.repository_id, registered.repository)
             == (approved.repository_id, approved.repository)
             and registered.issue == approved.issue
             and registered.branch_ref == approved.branch_ref
             and registered.base_revision == approved.base_revision,
             "V2_CURRENT_ISSUE_BINDING_INVALID")
        self.identity = identity
        self.registered = registered
        self.approved = approved
        self.reader = reader
        self.observer = observer
        self.limits = limits

    def observe(self) -> CurrentTask:
        registered = self.registered
        task = str(registered.issue)
        v = Validation(self.identity.repository, self.identity.genesis,
                       self.reader, self.limits)
        try:
            archive_tip = v.observe(self.observer, ARCHIVE_REF)
            archive = v.archive(archive_tip)
            need(task not in archive.snapshot.tasks,
                 "V2_CURRENT_ISSUE_ALREADY_ARCHIVED")
            tip = v.observe(self.observer, issue_ref(task), absent=True)
            need(tip is not None, "V2_CURRENT_ISSUE_REF_ABSENT")
            current = v.issue(tip, task)
            record_id = current.snapshot.pointers.get(task)
            need(record_id == registered.record_id,
                 "V2_CURRENT_RECORD_CHANGED")
            record = current.snapshot.objects.get(registered.record_id)
            contract = current.snapshot.objects.get(registered.contract_id)
            need(record is not None and contract is not None,
                 "V2_CURRENT_RECORD_OR_CONTRACT_MISSING")
            record_bytes = current.snapshot.files.get(
                object_path("task-record", registered.record_id))
            contract_bytes = current.snapshot.files.get(
                object_path("contract", registered.contract_id))
            need(record_bytes is not None and contract_bytes is not None,
                 "V2_CURRENT_RECORD_OR_CONTRACT_MISSING")
            checked_record = decode_record(
                record_bytes, object_id=registered.record_id,
                repository=registered.repository, task=task,
                base_revision=registered.base_revision,
                branch_ref=registered.branch_ref)
            checked_contract = decode_contract(
                contract_bytes, object_id=registered.contract_id,
                repository=registered.repository, task=task,
                base_revision=registered.base_revision)
            need("disposition" not in checked_record["payload"],
                 "V2_CURRENT_TASK_RETIRED")
            need(checked_record["payload"]["contract_id"] == registered.contract_id
                 and checked_contract["payload"]["title"] == self.approved.title
                 and checked_contract["payload"]["body"] == self.approved.body,
                 "V2_CURRENT_CONTRACT_CHANGED")
            v.check()
            need(v.observe(self.observer, ARCHIVE_REF) == archive_tip
                 and v.observe(self.observer, issue_ref(task), absent=True) == tip,
                 "V2_CURRENT_REFS_MOVED")
        except AdmissionRefused:
            raise
        except (InvalidMetadata, TaskRecordError, ValueError, TypeError, KeyError) as exc:
            raise AdmissionRefused("V2_CURRENT_METADATA_UNAVAILABLE") from exc
        return CurrentTask(
            self.identity.repository_id, self.identity.repository,
            registered.issue, tip, archive_tip, record_id,
            registered.contract_id, registered.base_revision,
            registered.branch_ref)


class V2WorktreeAllocator(IssueWorktreeAllocator):
    """Reuse #286 Git mutation/lock/registration unchanged; override authority.

    MetadataStore is constructed *only* as a v1-shaped Project path/repository
    container for the inherited allocator. No v1 TaskRecords or v1 source ref
    is used as a v2 Task grant. v1 collision absence is a separate mandatory
    trusted Host observation.
    """

    def __init__(
        self, project_store: MetadataStore, authority: CurrentTaskReader,
        lease: gate.AttachmentLease, selection: V2Selection, *,
        authorize: Callable[[V2AdmissionRequest], bool],
        legacy_issue_absent: Callable[[V2AdmissionRequest], bool],
    ) -> None:
        need(type(project_store) is MetadataStore
             and type(authority) is CurrentTaskReader
             and type(lease) is gate.AttachmentLease
             and type(selection) is V2Selection
             and callable(authorize) and callable(legacy_issue_absent),
             "V2_WORKTREE_HOST_CAPABILITIES_REQUIRED")
        need(project_store.repository == authority.identity.repository
             and (selection.repository_id, selection.repository, selection.issue)
             == (authority.identity.repository_id, authority.identity.repository,
                 authority.registered.issue)
             and selection.attach_digest == lease._fingerprint,
             "V2_WORKTREE_SELECTION_INVALID")
        super().__init__(project_store)
        self._v2_authority = authority
        self._lease = lease
        self._selection = selection
        self._authorize = authorize
        self._legacy_issue_absent = legacy_issue_absent

    def _authority(self, task: str, base_revision: str, branch_ref: str,
                   expected_contract_id: str) -> str:
        registered = self._v2_authority.registered
        need((task, base_revision, branch_ref, expected_contract_id)
             == (str(registered.issue), registered.base_revision,
                 registered.branch_ref, registered.contract_id),
             "V2_WORKTREE_TASK_SELECTION_MISMATCH")
        # This override is called inside #286's existing per-Issue flock both
        # before and after Git worktree CREATE/RESUME.
        self._lease.verify(project=self.project)
        need(self._lease._fingerprint == self._selection.attach_digest,
             "V2_WORKTREE_ATTACHMENT_CHANGED")
        current = self._v2_authority.observe()
        request = V2AdmissionRequest(
            "v2-worktree-admission", self._selection, current.metadata_tip,
            current.record_id, current.contract_id, branch_ref, base_revision)
        try:
            clear = self._legacy_issue_absent(request) is True
            granted = self._authorize(request) is True if clear else False
        except Exception as exc:
            raise AdmissionRefused("V2_HOST_ADMISSION_UNAVAILABLE") from exc
        need(clear, "V2_EXISTING_V1_ISSUE_ASSIGNMENT")
        need(granted, "V2_HUMAN_OPERATION_GRANT_REQUIRED")
        # A Host callback may block or observe drift. It must not promote an
        # authority observation taken before it to an effect-time permit.
        self._lease.verify(project=self.project)
        need(self._v2_authority.observe() == current,
             "V2_CURRENT_CHANGED_DURING_APPROVAL")
        return expected_contract_id


def admit_worktree(
    *, project: Path, state_home: Path, package_root: Path,
    identity: RepositoryIdentity, registered: RegisteredIssue,
    approved: ApprovedIssue, selection: V2Selection,
    reader: ObjectReader, observer: Observer,
    authorize: Callable[[V2AdmissionRequest], bool],
    legacy_issue_absent: Callable[[V2AdmissionRequest], bool],
    remote: str = "origin", limits: Limits = Limits(),
) -> Allocation:
    """Source-only v2 Host call: approved Product Worktree CREATE/RESUME only.

    The ordinary v1 Runtime CLI, Main and Task Session are not used.
    The caller MUST be an installed trusted Host with a separate v2 permit.
    """
    need(type(selection) is V2Selection
         and type(identity) is RepositoryIdentity
         and type(registered) is RegisteredIssue
         and type(approved) is ApprovedIssue
         and isinstance(project, Path) and isinstance(state_home, Path)
         and isinstance(package_root, Path)
         and type(remote) is str and bool(remote)
         and callable(authorize) and callable(legacy_issue_absent),
         "V2_ADMISSION_INPUT_INVALID")
    need(selection.profile == V2_PROFILE
         and (selection.repository_id, selection.repository, selection.issue)
         == (identity.repository_id, identity.repository, registered.issue)
         and approved.issue == selection.issue
         and approved.repository == selection.repository
         and approved.repository_id == selection.repository_id,
         "V2_EXPLICIT_SELECTION_REQUIRED")
    # The existing verified receipt is pinned throughout Worktree admission.
    # No second lock, Install receipt, Task SoT or uncontrolled Git mutator.
    try:
        with gate.hold_verified(project, state_home, package_root) as lease:
            lease.verify(project=project)
            need(selection.attach_digest == lease._fingerprint,
                 "V2_INSTALL_RECEIPT_MISMATCH")
            authority = CurrentTaskReader(
                identity, registered, approved,
                reader=reader, observer=observer, limits=limits)
            # Initial refusal is read-only; no #286 lock/worktree directory
            # need be created for an unapproved or retired Task.
            now = authority.observe()
            request = V2AdmissionRequest(
                "v2-worktree-admission", selection, now.metadata_tip,
                now.record_id, now.contract_id,
                registered.branch_ref, registered.base_revision)
            try:
                legacy_clear = legacy_issue_absent(request) is True
                grant = authorize(request) is True if legacy_clear else False
            except Exception as error:
                raise AdmissionRefused("V2_HOST_ADMISSION_UNAVAILABLE") from error
            need(legacy_clear, "V2_EXISTING_V1_ISSUE_ASSIGNMENT")
            need(grant, "V2_HUMAN_OPERATION_GRANT_REQUIRED")
            context = MetadataStore(project, identity.repository, remote)
            allocator = V2WorktreeAllocator(
                context, authority, lease, selection, authorize=authorize,
                legacy_issue_absent=legacy_issue_absent)
            allocation = allocator.ensure(
                task=str(registered.issue), branch_ref=registered.branch_ref,
                base_revision=registered.base_revision,
                expected_contract_id=registered.contract_id)
            lease.verify(project=project)
            authority.observe()
            return allocation
    except AdmissionRefused:
        raise
    except (gate.GateBlocked, gate.attach.Blocked,
            gate.attach.host.HostBlocked,
            gate.attach.host.ignore.IgnoreBlocked,
            IssueWorktreeError, MetadataRefError,
            OSError, RuntimeError) as error:
        raise AdmissionRefused("V2_ADMISSION_CONTEXT_UNAVAILABLE") from error
