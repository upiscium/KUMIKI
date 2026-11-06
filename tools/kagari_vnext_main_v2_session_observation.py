"""#291 R5B0 — opt-in Metadata v2 Session/Ask observation; no effects.

The trusted Host binds #337's CURRENT Record through the R5A0 observer and
returns an exact existing-Session locator (or positively observed absence).
No v1 TaskRecords/fetch_tip, Session start, Ask reply, GitHub mutation, model,
runtime package installation or unowned inference can occur in this module.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .kagari_vnext_main_v2_observation import (
        V2ReadOnlyMainHost, V2MainObservationRefused)
    from .kagari_vnext_task_binding import worktree_registered
    from .kagari_vnext_opencode_control import (
        TaskBinding, OpenCode, ControlError, status as opencode_status)
else:
    from kagari_vnext_main_v2_observation import (
        V2ReadOnlyMainHost, V2MainObservationRefused)
    from kagari_vnext_task_binding import worktree_registered
    from kagari_vnext_opencode_control import (
        TaskBinding, OpenCode, ControlError, status as opencode_status)


class V2SessionObservationRefused(ValueError):
    """Fail closed; never report a false empty Ask queue or Task completion."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(ok: bool, code: str) -> None:
    if not ok:
        raise V2SessionObservationRefused(code)


@dataclass(frozen=True, slots=True)
class V2SessionObservationRequest:
    """Exact current v2 authority observed by the trusted read-only Host."""

    operation: str
    repository_id: int
    repository: str
    issue: int
    metadata_tip: str
    archive_tip: str
    record_id: str
    contract_id: str
    branch_ref: str
    base_revision: str


@dataclass(frozen=True, slots=True)
class V2SessionLocator:
    """Host observation of one exact Task Session, not a start/reply permit.

    ABSENT means positive Host evidence of no registered root Session.
    UNKNOWN means the Host cannot establish absence or presence. Missing
    callbacks or a raw None result do not establish ABSENT.
    """

    state: str
    repository: str
    issue: int
    contract_id: str
    worktree: str | None = None
    session_id: str | None = None
    server_url: str | None = None

    def __post_init__(self) -> None:
        require(type(self.state) is str
                and self.state in ("ABSENT", "PRESENT", "UNKNOWN")
                and type(self.repository) is str and bool(self.repository)
                and type(self.issue) is int and self.issue > 0
                and type(self.contract_id) is str
                and len(self.contract_id) == 64
                and all(c in "0123456789abcdef" for c in self.contract_id),
                "V2_SESSION_LOCATOR_INVALID")
        attributes = (self.worktree, self.session_id, self.server_url)
        if self.state != "PRESENT":
            require(all(x is None for x in attributes),
                    "V2_SESSION_LOCATOR_INVALID")
        else:
            require(all(type(x) is str and bool(x) for x in attributes)
                    and Path(self.worktree).is_absolute(),
                    "V2_SESSION_LOCATOR_INVALID")


@dataclass(frozen=True, slots=True)
class V2MainSessionReadOnlyHost:
    """Explicit all-Issue R5B0 Host capability with a held R5A0 lease."""

    readonly: V2ReadOnlyMainHost
    read_locator: Callable[[V2SessionObservationRequest], V2SessionLocator]

    def __post_init__(self) -> None:
        require(type(self.readonly) is V2ReadOnlyMainHost
                and callable(self.read_locator),
                "V2_SESSION_HOST_INVALID")

    def observe(
        self, *, project: Path, repository: str, issue: int,
        branch_ref: str, base_revision: str, contract_id: str,
        read_issue: Callable[[int], dict],
    ) -> dict:
        """Read existing root Session and native pending Ask, never mutate.

        Revalidate v2 current-Task authority on both sides of Host and
        upstream reads. A positive R5A0 status remains BLOCKED_LIVE; it
        never becomes a Task dispatch or PR collaboration authorization.
        """
        rd = self.readonly
        current = rd.current_reader
        registered = current.registered
        selection = rd.selection
        require(isinstance(project, Path) and type(issue) is int
                and (repository, issue, branch_ref, base_revision, contract_id)
                == (registered.repository, registered.issue,
                    registered.branch_ref, registered.base_revision,
                    registered.contract_id)
                and (selection.repository, selection.issue,
                     selection.repository_id)
                == (repository, issue, current.identity.repository_id),
                "V2_SESSION_TASK_BINDING_MISMATCH")

        def verified_readonly() -> dict:
            try:
                return rd.observe(
                    project=project, repository=repository, issue=issue,
                    branch_ref=branch_ref, base_revision=base_revision,
                    contract_id=contract_id, read_issue=read_issue)
            except V2MainObservationRefused as exc:
                raise V2SessionObservationRefused(exc.code) from None

        def current_now():
            try:
                return rd._current()
            except V2MainObservationRefused as exc:
                raise V2SessionObservationRefused(exc.code) from None

        before = verified_readonly()
        first = current_now()
        request = V2SessionObservationRequest(
            "v2-session-read-only-observation", selection.repository_id,
            repository, issue, first.metadata_tip, first.archive_tip,
            first.record_id, contract_id, branch_ref, base_revision)
        try:
            locator = self.read_locator(request)
        except Exception:
            raise V2SessionObservationRefused("V2_SESSION_LOCATOR_UNAVAILABLE") from None
        require(type(locator) is V2SessionLocator
                and (locator.repository, locator.issue, locator.contract_id)
                == (repository, issue, contract_id),
                "V2_SESSION_LOCATOR_MISMATCH")

        if locator.state == "ABSENT":
            session = {
                "state": "NOT_STARTED", "reason": "HOST_CONFIRMED_ABSENT",
                "session_id": None, "pending_asks": [],
                "pending_count": 0,
            }
            runtime = "NOT_STARTED"
            kickoff = "NOT_SENT"
        elif locator.state == "UNKNOWN":
            session = {
                "state": "UNKNOWN", "reason": "HOST_SESSION_UNCERTAIN",
                "session_id": None, "pending_asks": None,
                "pending_count": None,
            }
            runtime = "UNKNOWN"
            kickoff = "UNKNOWN"
        else:
            expected_worktree = project / ".worktrees" / str(issue)
            require(locator.worktree == str(expected_worktree),
                    "V2_SESSION_WORKTREE_MISMATCH")
            try:
                # This is the Git-only #292 registration check, NOT its
                # v1 verify_task_record()/resolve() metadata validator.
                worktree_registered(project, expected_worktree, branch_ref)
            except (ControlError, OSError, RuntimeError, ValueError):
                raise V2SessionObservationRefused(
                    "V2_SESSION_WORKTREE_UNVERIFIED") from None
            try:
                binding = TaskBinding.from_mapping({
                    "schema_version": 1, "repository": repository,
                    "issue": issue, "worktree": locator.worktree,
                    "session_id": locator.session_id,
                    "server_url": locator.server_url,
                    "contract_sha256": contract_id,
                    "contract_revision": "contract:" + contract_id[:16],
                })
                client = OpenCode(binding)
                before_pending = client.pending()
                observed = opencode_status(binding, client)
                after_pending = client.pending()
                require(type(observed) is dict
                        and observed.get("status") == "ok"
                        and observed.get("task") == binding.subject()
                        and type(observed.get("pending_asks")) is int
                        and observed["pending_asks"] == len(before_pending)
                        and before_pending == after_pending
                        and observed.get("session_state")
                        in ("busy", "retry", "idle", "UNKNOWN"),
                        "V2_SESSION_UPSTREAM_CHANGED")
                # Recheck native Git worktree registration after remote GETs.
                worktree_registered(project, expected_worktree, branch_ref)
            except V2SessionObservationRefused:
                raise
            except (ControlError, OSError, RuntimeError, ValueError):
                raise V2SessionObservationRefused(
                    "V2_SESSION_UPSTREAM_UNAVAILABLE") from None

            native_state = observed["session_state"]
            has_pending = bool(after_pending)
            state = ("PENDING_ASK" if has_pending else
                     "RUNNING" if native_state in ("busy", "retry") else
                     "UNKNOWN")
            session = {
                "state": state,
                "reason": ("ACTUAL_PENDING_ASK" if has_pending else
                           "UPSTREAM_RUNNING" if state == "RUNNING" else
                           "IDLE_NOT_TASK_COMPLETION" if native_state == "idle"
                           else "UPSTREAM_STATE_UNKNOWN"),
                "session_id": locator.session_id,
                "worktree": locator.worktree,
                "task": binding.subject(),
                "session_state": native_state,
                "pending_asks": after_pending,
                "pending_count": len(after_pending),
            }
            runtime = "EXISTING_SESSION_OBSERVED"
            kickoff = "UNKNOWN"

        # A trusted Host locator is a scoped observation, never a durable
        # permit. Changed Issue/ref/Contract/lease after upstream reads blocks.
        after = verified_readonly()
        require(first == current_now()
                and before["approved_task"] == after["approved_task"]
                and before["current_record"] == after["current_record"],
                "V2_SESSION_CURRENT_CHANGED")
        # Current Record/Git registration cannot establish root Session
        # stability during native GETs. Re-read the exact Host locator.
        try:
            latest_locator = self.read_locator(request)
        except Exception:
            raise V2SessionObservationRefused(
                "V2_SESSION_LOCATOR_UNAVAILABLE") from None
        require(type(latest_locator) is V2SessionLocator
                and latest_locator == locator,
                "V2_SESSION_LOCATOR_CHANGED")
        # The second Host callback may have raced a legitimate current Task
        # update. Check the authoritative Record/Issue again after it returns.
        latest = verified_readonly()
        require(first == current_now()
                and after["approved_task"] == latest["approved_task"]
                and after["current_record"] == latest["current_record"],
                "V2_SESSION_CURRENT_CHANGED")
        after = latest
        # The final R5A0 Issue callback/current read runs AFTER its last
        # lease check. Attachment can drift during those otherwise unchanged
        # observations; end this read-only operation under the same live lease.
        try:
            rd._verify_lease(project)
        except V2MainObservationRefused as exc:
            raise V2SessionObservationRefused(exc.code) from None
        return {
            **after, "runtime": runtime, "kickoff": kickoff,
            "session_observation": session,
            # Observation does not authorize any new implementation Turn.
            "task_execution_authorized": False,
            "worktree_mutation_authorized": False,
            "github_write_authorized": False,
        }
