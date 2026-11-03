"""#291 R5A0: opt-in, non-executing Metadata v2 Main observation.

Requires #337 Phase 2B2's *current* Issue Task reader and an already held
#256 AttachmentLease. No Worktree allocator, Main owner, Session, publisher,
network, or model is created by this module. Host callbacks are trusted
read-only observations/grants, not self-authenticating permissions.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# #337 v2 sources use the canonical top-level module names to share exact
# class identity with their own readers and Host Attach fixtures. Importing
# them again as tools.* creates different nominal dataclass types.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_task_admission import (
    AdmissionRefused, CurrentTask, CurrentTaskReader,
    V2AdmissionRequest, V2Selection, V2_PROFILE,
)


class V2MainObservationRefused(ValueError):
    """Refuse all effects; expose only bounded reason codes."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise V2MainObservationRefused(reason)


@dataclass(frozen=True, slots=True)
class V2ReadOnlyMainHost:
    """Exact Host-installed, Issue-scoped R5A0 observation capability.

    This class does not grant or expose the R5A1 Worktree mutation API.
    Only use within the #256 hold_verified() context that created the lease.
    The trusted Host must bind the callbacks to an authenticated repository.
    """

    selection: V2Selection
    current_reader: CurrentTaskReader
    lease: gate.AttachmentLease
    legacy_issue_absent: Callable[[V2AdmissionRequest], bool]
    authorize_observation: Callable[[V2AdmissionRequest], bool]

    def __post_init__(self) -> None:
        require(type(self.selection) is V2Selection
                and self.selection.profile == V2_PROFILE
                and type(self.current_reader) is CurrentTaskReader
                and type(self.lease) is gate.AttachmentLease
                and callable(self.legacy_issue_absent)
                and callable(self.authorize_observation),
                "V2_MAIN_HOST_INVALID")
        registered = self.current_reader.registered
        identity = self.current_reader.identity
        require((self.selection.repository_id, self.selection.repository,
                 self.selection.issue)
                == (identity.repository_id, identity.repository,
                    registered.issue)
                and self.selection.attach_digest == self.lease._fingerprint,
                "V2_MAIN_HOST_BINDING_MISMATCH")

    def _verify_lease(self, project: Path) -> None:
        try:
            self.lease.verify(project=project)
        except (gate.GateBlocked, gate.attach.Blocked,
                gate.attach.host.HostBlocked,
                gate.attach.host.ignore.IgnoreBlocked,
                OSError, RuntimeError, ValueError):
            raise V2MainObservationRefused("V2_MAIN_ATTACHMENT_UNAVAILABLE") from None
        require(self.selection.attach_digest == self.lease._fingerprint,
                "V2_MAIN_ATTACHMENT_CHANGED")

    def _current(self) -> CurrentTask:
        try:
            observed = self.current_reader.observe()
        except (AdmissionRefused, OSError, RuntimeError, ValueError):
            raise V2MainObservationRefused("V2_MAIN_CURRENT_TASK_UNAVAILABLE") from None
        require(type(observed) is CurrentTask,
                "V2_MAIN_CURRENT_TASK_UNAVAILABLE")
        return observed

    def observe(
        self, *, project: Path, repository: str, issue: int,
        branch_ref: str, base_revision: str, contract_id: str,
        read_issue: Callable[[int], dict],
    ) -> dict:
        """Re-read the exact v2 current record and live Issue, never act.

        Even a fully verified current Task returns BLOCKED_LIVE: R5A0 is not
        session/model/PR/cleanup admission. No implicit v1 fallback.
        """
        registered = self.current_reader.registered
        approved = self.current_reader.approved
        choice = (repository, issue, branch_ref, base_revision, contract_id)
        require(type(issue) is int and
                choice == (registered.repository, registered.issue,
                           registered.branch_ref, registered.base_revision,
                           registered.contract_id)
                and repository == self.selection.repository
                and issue == self.selection.issue
                and approved.repository_id == self.selection.repository_id
                and (approved.repository, approved.issue,
                     approved.branch_ref, approved.base_revision)
                == (repository, issue, branch_ref, base_revision),
                "V2_MAIN_TASK_BINDING_MISMATCH")

        def exact_issue() -> dict:
            try:
                facts = read_issue(issue)
            except Exception:
                raise V2MainObservationRefused("V2_MAIN_ISSUE_UNAVAILABLE") from None
            require(type(facts) is dict
                    and type(facts.get("number")) is int
                    and facts["number"] == issue
                    and facts.get("repository") == repository
                    and facts.get("state") == "OPEN"
                    and facts.get("title") == approved.title
                    and facts.get("body") == approved.body,
                    "V2_MAIN_ISSUE_SNAPSHOT_DRIFT")
            return facts

        self._verify_lease(project)
        exact_issue()
        first = self._current()
        require((first.repository_id, first.repository, first.issue,
                 first.branch_ref, first.base_revision, first.contract_id)
                == (self.selection.repository_id, repository, issue,
                    branch_ref, base_revision, contract_id),
                "V2_MAIN_CURRENT_BINDING_MISMATCH")
        request = V2AdmissionRequest(
            "v2-main-read-only-observation", self.selection,
            first.metadata_tip, first.record_id, first.contract_id,
            first.branch_ref, first.base_revision)
        # These callbacks are supplied by the trusted Host. Neither the
        # selection nor a fresh remote read grants any Task mutation.
        try:
            clear = self.legacy_issue_absent(request) is True
            approved_read = self.authorize_observation(request) is True if clear else False
        except Exception:
            raise V2MainObservationRefused("V2_MAIN_HOST_OBSERVATION_UNAVAILABLE") from None
        require(clear, "V2_MAIN_EXISTING_V1_ASSIGNMENT")
        require(approved_read, "V2_MAIN_OBSERVATION_GRANT_REQUIRED")
        self._verify_lease(project)
        exact_issue()
        require(self._current() == first,
                "V2_MAIN_CURRENT_CHANGED_DURING_OBSERVATION")
        return {
            "status": "blocked", "reason": "V2_MAIN_BLOCKED_LIVE",
            "metadata_version": 2, "repository": repository, "issue": issue,
            "approved_task": {
                "repository": repository, "issue": issue,
                "contract_id": contract_id, "title": approved.title,
                "base_revision": base_revision, "branch_ref": branch_ref,
            },
            "current_record": {
                "record_id": first.record_id,
                "contract_id": first.contract_id,
                "metadata_tip": first.metadata_tip,
                "archive_tip": first.archive_tip,
            },
            "runtime": "NOT_STARTED", "kickoff": "NOT_SENT",
            "worktree_state": "NOT_OBSERVED",
            "task_execution_authorized": False,
            "worktree_mutation_authorized": False,
            "github_write_authorized": False,
        }
