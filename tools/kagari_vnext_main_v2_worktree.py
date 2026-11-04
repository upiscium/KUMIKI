"""#291 R5A1: bounded Main-owned v2 Worktree CREATE/RESUME (source-only).

Only a trusted, explicitly admitted Host with a *held* #256 AttachmentLease
and the canonical MainSupervisor owner may invoke this local Git operation.
Never call #337 admit_worktree() under this lease: it would acquire it twice.
No Session, model, GitHub PR, v2 Issue writer, installer or public CLI.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kagari_metadata_v2_task_admission import (
    AdmissionRefused, V2AdmissionRequest, V2WorktreeAllocator,
)
if __package__:
    from .kagari_vnext_main_v2_observation import (
        V2ReadOnlyMainHost, V2MainObservationRefused)
else:
    from kagari_vnext_main_v2_observation import (
        V2ReadOnlyMainHost, V2MainObservationRefused)
from metadata_ref import MetadataStore, MetadataRefError
from issue_worktree import IssueWorktreeError


class V2MainWorktreeRefused(ValueError):
    """Bounded refusal; a post-effect error may require manual reconciliation."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(value: bool, code: str) -> None:
    if not value:
        raise V2MainWorktreeRefused(code)


@dataclass(frozen=True, slots=True)
class V2MainWorktreeHost:
    """Distinct Host-owned operation capability; a read grant is insufficient.

    All current-v2 metadata observations come from the same #337 reader used
    by R5A0. Worktree authorization is supplied separately and must be
    operation-specific and repeated inside #337's existing Issue lock.
    """

    readonly: V2ReadOnlyMainHost
    authorize_worktree: Callable[[V2AdmissionRequest], bool]

    def __post_init__(self) -> None:
        require(type(self.readonly) is V2ReadOnlyMainHost
                and callable(self.authorize_worktree),
                "V2_MAIN_WORKTREE_HOST_REQUIRED")

    def admit(
        self, *, store: MetadataStore, issue: int,
        repository: str, branch_ref: str, base_revision: str,
        contract_id: str, read_issue: Callable[[int], dict],
        check_main_owner: Callable[[], None],
    ) -> dict:
        """One authorized local Worktree effect; caller holds Main + Attach.

        Missing Host approval and stale requirements refuse before even
        creating #286's per-Issue lock. Once Git starts, never auto-rollback.
        """
        readonly = self.readonly
        selection = readonly.selection
        authority = readonly.current_reader
        registered = authority.registered
        approved = authority.approved
        lease = readonly.lease
        require(type(store) is MetadataStore and callable(read_issue)
                and callable(check_main_owner) and type(issue) is int
                and (repository, issue, branch_ref, base_revision, contract_id)
                == (registered.repository, registered.issue,
                    registered.branch_ref, registered.base_revision,
                    registered.contract_id)
                and (selection.repository, selection.issue,
                     selection.repository_id)
                == (registered.repository, registered.issue,
                    authority.identity.repository_id)
                and (approved.repository, approved.issue,
                     approved.repository_id, approved.branch_ref,
                     approved.base_revision)
                == (repository, issue, selection.repository_id,
                    branch_ref, base_revision)
                and store.repository == repository,
                "V2_MAIN_WORKTREE_BINDING_MISMATCH")

        def exact_issue() -> None:
            try:
                facts = read_issue(issue)
            except Exception:
                raise V2MainWorktreeRefused("V2_MAIN_ISSUE_UNAVAILABLE") from None
            require(type(facts) is dict and type(facts.get("number")) is int
                    and facts["number"] == issue
                    and facts.get("repository") == repository
                    and facts.get("state") == "OPEN"
                    and facts.get("title") == approved.title
                    and facts.get("body") == approved.body,
                    "V2_MAIN_ISSUE_SNAPSHOT_DRIFT")

        def check_context() -> None:
            check_main_owner()
            try:
                readonly._verify_lease(store.root)
            except V2MainObservationRefused:
                raise V2MainWorktreeRefused("V2_MAIN_ATTACHMENT_UNAVAILABLE") from None
            exact_issue()

        # The granted operation is precisely #337's Worktree admission, not
        # the R5A0 read-only observation or a generic Task/model permission.
        def valid_request(req: V2AdmissionRequest) -> None:
            require(type(req) is V2AdmissionRequest
                    and req.operation == "v2-worktree-admission"
                    and req.selection == selection
                    and req.record_id == registered.record_id
                    and req.metadata_tip == first.metadata_tip
                    and req.contract_id == contract_id
                    and req.branch_ref == branch_ref
                    and req.base_revision == base_revision,
                    "V2_MAIN_WORKTREE_REQUEST_MISMATCH")

        def checked_legacy(req: V2AdmissionRequest) -> bool:
            valid_request(req)
            check_context()
            try:
                cleared = readonly.legacy_issue_absent(req) is True
            except Exception:
                raise V2MainWorktreeRefused("V2_MAIN_HOST_LEGACY_UNAVAILABLE") from None
            check_context()
            return cleared

        def checked_grant(req: V2AdmissionRequest) -> bool:
            valid_request(req)
            check_context()
            try:
                granted = self.authorize_worktree(req) is True
            except Exception:
                raise V2MainWorktreeRefused("V2_MAIN_HOST_GRANT_UNAVAILABLE") from None
            check_context()
            return granted

        allocation_completed = False
        try:
            check_context()
            first = readonly._current()
            require((first.repository_id, first.repository, first.issue,
                     first.branch_ref, first.base_revision, first.contract_id,
                     first.record_id)
                    == (selection.repository_id, repository, issue,
                        branch_ref, base_revision, contract_id,
                        registered.record_id),
                    "V2_MAIN_WORKTREE_CURRENT_MISMATCH")
            req = V2AdmissionRequest(
                "v2-worktree-admission", selection, first.metadata_tip,
                first.record_id, first.contract_id, branch_ref, base_revision)
            cleared = checked_legacy(req)
            granted = checked_grant(req) if cleared else False
            require(cleared, "V2_MAIN_EXISTING_V1_ASSIGNMENT")
            require(granted, "V2_MAIN_WORKTREE_GRANT_REQUIRED")
            check_context()
            require(readonly._current() == first,
                    "V2_MAIN_WORKTREE_CURRENT_CHANGED")
            # #337's allocator supplies the existing #286 per-Issue flock,
            # double current/ref/grant check, Git register-or-resume behavior,
            # and conservative partial-effect retention. No nested Attach.
            allocation = V2WorktreeAllocator(
                store, authority, lease, selection,
                authorize=checked_grant,
                legacy_issue_absent=checked_legacy,
            ).ensure(
                task=str(issue), branch_ref=branch_ref,
                base_revision=base_revision,
                expected_contract_id=contract_id,
            )
            allocation_completed = True
            check_context()
            require(readonly._current() == first,
                    "V2_MAIN_WORKTREE_CURRENT_CHANGED")
        except V2MainWorktreeRefused:
            if allocation_completed:
                # Git CREATE/RESUME is already positively recorded. Never
                # turn a post-effect drift into an ordinary pre-effect denial;
                # preserve the Worktree for explicit operator reconciliation.
                raise V2MainWorktreeRefused(
                    "V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED") from None
            raise
        except (AdmissionRefused, IssueWorktreeError, MetadataRefError,
                V2MainObservationRefused, OSError, RuntimeError, ValueError):
            # If CREATE happened but a post-effect check refused, retain all
            # Product Git data; the Host reconciles it explicitly.
            raise V2MainWorktreeRefused(
                "V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED") from None
        return {
            "status": "worktree_admitted",
            "metadata_version": 2, "repository": repository, "issue": issue,
            "contract_id": contract_id, "worktree_state": allocation.status,
            "worktree": allocation.worktree, "branch_ref": allocation.branch_ref,
            "base_revision": allocation.base_revision,
            "runtime": "NOT_STARTED", "kickoff": "NOT_SENT",
            "task_execution_authorized": False,
            "session_started": False, "pr_authorized": False,
            "github_write_authorized": False,
        }
