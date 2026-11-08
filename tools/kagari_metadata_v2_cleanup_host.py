"""#337 P3C/B1: opt-in, source-only exact Issue metadata-ref cleanup host.

NO CLI/GitHub transport, production Host hookup, install or activation.
Only a trusted Host can supply the held lock, current terminal facts, fresh
Human cleanup decision and a one-shot exact-lease deletion capability.
Recovery is observation-only and NEVER invokes the deletion callback.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import time

import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_archive_host import (
    ArchiveOperation, ArchiveOutcome, TerminalFacts, _key as archive_key,
)
from kagari_metadata_v2_cleanup_history import (
    CleanupHistory, CleanupHistoryReader, CleanupHistoryRefused,
    _check_original_terminal_semantics,
)
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import ARCHIVE_REF, InvalidMetadata, Validation, issue_ref

_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DOMAIN = b"kagari-metadata-v2-issue-cleanup/v1\n"
_PURPOSE = "DELETE_ISSUE_METADATA_REF"


class CleanupRefused(ValueError):
    """No qualified new cleanup operation; no effect may be attempted."""


class CleanupUncertain(CleanupRefused):
    """A durable cleanup intent may exist; never blindly retry deletion."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise CleanupRefused(code)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _key(repository_id: int, issue: int, c: str, j: str) -> str:
    # The same C/J must share a single create-once intent even if a caller
    # proposes a different cleanup operation ID.
    return hashlib.sha256(_DOMAIN + _canonical(
        ["issue-cleanup", repository_id, issue, c, j])).hexdigest()


@dataclass(frozen=True, slots=True)
class CleanupOperation:
    cleanup_id: str
    archive_operation: ArchiveOperation
    archive_outcome: ArchiveOutcome

    def __post_init__(self) -> None:
        require(type(self.cleanup_id) is str
                and _HEX32.fullmatch(self.cleanup_id) is not None
                and type(self.archive_operation) is ArchiveOperation
                and type(self.archive_outcome) is ArchiveOutcome
                and self.archive_operation.retention_policy is None
                and self.archive_outcome.state in {"PUBLISHED", "RECOVERED"}
                and self.archive_outcome.issue_commit == self.archive_operation.expected_issue
                and self.archive_outcome.record_id == self.archive_operation.record_id,
                "cleanup_operation_invalid")


@dataclass(frozen=True, slots=True)
class CleanupDeleteRequest:
    """Fixed request; the trusted CLI adapter MUST use exact-old lease delete."""

    repository_id: int
    repository: str
    issue: int
    ref: str
    expected_old: str
    archived_j: str
    cleanup_id: str


@dataclass(frozen=True, slots=True)
class CleanupApproval:
    operation: CleanupOperation
    history: CleanupHistory
    terminal_facts: TerminalFacts
    delete_request: CleanupDeleteRequest


@dataclass(frozen=True, slots=True)
class CleanupHumanGrant:
    """Fresh Host-verified cleanup decision, not an Archive journal receipt."""

    purpose: str
    decision_id: str
    cleanup_id: str
    repository_id: int
    issue: int
    ref: str
    expected_old: str
    archived_j: str


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    state: str  # DELETED / STILL_C / MOVED / UNKNOWN
    issue: int
    ref: str
    expected_old: str
    archived_j: str


class CleanupQuiescenceLease:
    """Proof of the same live Host lifecycle lock, not a lock acquirer."""

    def __init__(self, verify: Callable[[CleanupOperation], bool]) -> None:
        require(callable(verify), "cleanup_lock_verifier_required")
        self._verify = verify

    def check(self, operation: CleanupOperation) -> None:
        try:
            owned = self._verify(operation)
        except Exception as error:
            raise CleanupRefused("cleanup_lock_unavailable") from error
        require(owned is True, "cleanup_lock_lost")


class CleanupHumanGrantLease:
    """Host-owned lock serializing cleanup grant revocation with ref effect.

    The trusted Host must acquire the SAME synchronization domain used for
    revoking a cleanup decision and keep it held through the deletion and
    exact readback. A True check without this exclusion is not sufficient.
    This class supplies NO new permission or lock implementation.
    """

    def __init__(self, verify: Callable[[CleanupApproval, CleanupHumanGrant], bool]) -> None:
        require(callable(verify), "cleanup_grant_lock_verifier_required")
        self._verify = verify

    def check(self, approval: CleanupApproval, grant: CleanupHumanGrant) -> None:
        try:
            live = self._verify(approval, grant)
        except Exception as error:
            raise CleanupRefused("cleanup_grant_lock_unavailable") from error
        require(live is True, "cleanup_grant_lock_lost_or_revoked")


class _NoSuppressExit:
    """Never let a Host context hide a body or nested __exit__ exception.

    In an ExitStack, an outer context can normally suppress the exception
    raised by an inner context's __exit__, even during a pending return.
    Host lifecycle/grant cleanup must not turn that unknown result into
    successful ref deletion. Leave actual release to the injected context.
    """

    def __init__(self, context: AbstractContextManager) -> None:
        self.context = context

    def __enter__(self):
        return self.context.__enter__()

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.context.__exit__(exc_type, exc_value, traceback)
        return False


class CleanupHost:
    """Host-bound one-shot delete admission and read-only intent recovery.

    The trusted caller must verify current Task/Session/Product facts, the
    ownership of the live quiescence context, and the *fresh* Human grant.
    The delete callable is NOT exposed to a Task or model and must implement
    one exact old-OID leased deletion with no force overwrite/retry.
    """

    def __init__(
        self,
        history: CleanupHistoryReader,
        *,
        lease: gate.AttachmentLease,
        journal: RecoveryJournal,
        hold_quiescence: Callable[[CleanupOperation], AbstractContextManager],
        hold_human_grant: Callable[[CleanupApproval], AbstractContextManager],
        read_terminal: Callable[[CleanupOperation, CleanupHistory], TerminalFacts],
        prior_attempts_cleared: Callable[[CleanupOperation], bool],
        authorize: Callable[[CleanupApproval], CleanupHumanGrant],
        delete: Callable[[CleanupDeleteRequest], bool],
    ) -> None:
        require(type(history) is CleanupHistoryReader
                and type(lease) is gate.AttachmentLease
                and type(journal) is RecoveryJournal
                and journal is history.journal
                and all(callable(fn) for fn in (
                    hold_quiescence, hold_human_grant, read_terminal,
                    prior_attempts_cleared,
                    authorize, delete)), "cleanup_host_capabilities_required")
        require(history.identity.attach_digest == lease._fingerprint
                and history.identity.git_object_format == "sha1",
                "cleanup_host_attachment_mismatch")
        self.history = history
        self.lease = lease
        self.journal = journal
        self.hold_quiescence = hold_quiescence
        self.hold_human_grant = hold_human_grant
        self.read_terminal = read_terminal
        self.prior_attempts_cleared = prior_attempts_cleared
        self.authorize = authorize
        self.delete = delete

    def _attach(self) -> None:
        try:
            self.lease.verify()
        except Exception as error:
            raise CleanupRefused("cleanup_attachment_invalid") from error
        require(self.lease._fingerprint == self.history.identity.attach_digest,
                "cleanup_attachment_changed")

    def _request(self, op: CleanupOperation) -> CleanupDeleteRequest:
        h = self.history
        return CleanupDeleteRequest(
            h.identity.repository_id, h.identity.repository, h.registered.issue,
            issue_ref(str(h.registered.issue)),
            op.archive_operation.expected_issue,
            op.archive_outcome.archive_commit, op.cleanup_id)

    def _base_record(self, op: CleanupOperation) -> dict:
        h = self.history
        req = self._request(op)
        return {
            "schema": 1, "operation": "cleanup-issue-ref", "version": 1,
            "branch_mode": "ordinary-git-branch",
            "repository_id": req.repository_id, "repository": req.repository,
            "genesis": h.repository.genesis,
            "attach_digest": h.identity.attach_digest,
            "issue": req.issue, "issue_ref": req.ref,
            "cleanup_id": req.cleanup_id,
            "archive_operation_id": op.archive_operation.operation_id,
            "archive_before": op.archive_operation.expected_archive,
            "issue_before": req.expected_old, "archive_candidate": req.archived_j,
            "record_id": h.registered.record_id,
            "contract_id": h.registered.contract_id,
            "branch_ref": h.registered.branch_ref,
            "base_revision": h.registered.base_revision,
        }

    def _saved(self, op: CleanupOperation, key: str) -> dict | None:
        try:
            saved = self.journal.read(key)
        except Exception as error:
            raise CleanupUncertain("cleanup_intent_unreadable") from error
        if saved is None:
            return None
        base = self._base_record(op)
        require(type(saved) is dict
                and saved.keys() == base.keys() | {"decision_id", "terminal_facts_digest"}
                and all(saved[k] == v and type(saved[k]) is type(v)
                        for k, v in base.items())
                and type(saved["decision_id"]) is str
                and _HEX64.fullmatch(saved["decision_id"]) is not None
                and type(saved["terminal_facts_digest"]) is str
                and _HEX64.fullmatch(saved["terminal_facts_digest"]) is not None,
                "cleanup_existing_intent_mismatch")
        return saved

    def _history(self, op: CleanupOperation) -> CleanupHistory:
        try:
            return self.history.inspect(op.archive_operation, op.archive_outcome)
        except Exception as error:
            raise CleanupRefused("cleanup_original_history_unavailable") from error

    def _prior(self, op: CleanupOperation) -> None:
        try:
            cleared = self.prior_attempts_cleared(op)
        except Exception as error:
            raise CleanupRefused("cleanup_prior_attempts_unknown") from error
        require(cleared is True, "cleanup_prior_attempts_unresolved")

    def _terminal(self, op: CleanupOperation, observed: CleanupHistory) -> TerminalFacts:
        try:
            facts = self.read_terminal(op, observed)
        except Exception as error:
            raise CleanupRefused("cleanup_current_terminal_unavailable") from error
        require(type(facts) is TerminalFacts, "cleanup_current_terminal_untyped")
        h = self.history
        require((facts.repository_id, facts.repository, facts.issue,
                 facts.record_id, facts.contract_id, facts.branch_ref,
                 facts.base_revision) ==
                (h.identity.repository_id, h.identity.repository, h.registered.issue,
                 h.registered.record_id, h.registered.contract_id,
                 h.registered.branch_ref, h.registered.base_revision),
                "cleanup_current_terminal_binding_changed")
        # The original journal is re-read only for its immutable route and
        # original Product PR identity; it is NEVER a substitute for current
        # facts or the fresh cleanup-specific Human grant below.
        original_key = archive_key(h.identity.repository_id, h.registered.issue,
                                   op.archive_operation.expected_archive,
                                   op.archive_operation.expected_issue)
        try:
            saved = h.journal.read(original_key)
            old = saved["terminal_facts"]
            require(type(old) is dict and old["route"] == facts.route,
                    "cleanup_current_terminal_route_mismatch")
            if facts.route == "MERGED_PR":
                require(all(getattr(facts, k) == old[k] for k in (
                    "pr_number", "pr_repository_id", "pr_repository",
                    "pr_issue", "pr_head_oid", "pr_merge_oid", "pr_base_ref")),
                    "cleanup_current_pr_identity_changed")
                disposition = None
            elif facts.route == "CANCELLED":
                disposition = {"kind": "cancelled"}
            elif facts.route == "SUPERSEDED":
                disposition = {"kind": "superseded", "replacement": {
                    "repository": old["replacement_repository"],
                    "task": old["replacement_task"]}}
            else:
                raise CleanupRefused("cleanup_current_terminal_route_invalid")
            require((facts.replacement_repository, facts.replacement_task)
                    == (old["replacement_repository"], old["replacement_task"]),
                    "cleanup_replacement_changed")
            _check_original_terminal_semantics(
                asdict(facts), disposition,
                repository_id=h.identity.repository_id,
                repository=h.identity.repository, issue=h.registered.issue)
        except CleanupRefused:
            raise
        except Exception as error:
            raise CleanupRefused("cleanup_current_terminal_unverifiable") from error
        return facts

    def _original_archive_decision_id(self, op: CleanupOperation) -> str:
        """Read the *original* J publication's decision, not current facts.

        The original Archive decision cannot be replayed as a cleanup grant
        even if a new current terminal observation has a different ID.
        CleanupHistoryReader independently validates the entire v3 journal
        and original Archive DAG before/after grant admission.
        """
        h = self.history
        key = archive_key(h.identity.repository_id, h.registered.issue,
                          op.archive_operation.expected_archive,
                          op.archive_operation.expected_issue)
        try:
            saved = h.journal.read(key)
            require(type(saved) is dict
                    and saved.get("operation") == "archive-issue"
                    and type(saved.get("version")) is int
                    and saved["version"] == 3
                    and saved.get("branch_mode") == "ordinary-git-branch"
                    and saved.get("operation_id") == op.archive_operation.operation_id
                    and saved.get("candidate") == op.archive_outcome.archive_commit,
                    "cleanup_original_archive_decision_unbound")
            original = saved["terminal_facts"]["human_decision_id"]
        except CleanupRefused:
            raise
        except Exception as error:
            raise CleanupRefused("cleanup_original_archive_decision_unavailable") from error
        require(type(original) is str and _HEX64.fullmatch(original) is not None,
                "cleanup_original_archive_decision_invalid")
        return original

    def _grant(self, approval: CleanupApproval) -> CleanupHumanGrant:
        try:
            grant = self.authorize(approval)
        except Exception as error:
            raise CleanupRefused("cleanup_human_grant_unavailable") from error
        r = approval.delete_request
        require(type(grant) is CleanupHumanGrant
                and grant.purpose == _PURPOSE
                and type(grant.decision_id) is str
                and _HEX64.fullmatch(grant.decision_id) is not None
                and (grant.cleanup_id, grant.repository_id, grant.issue,
                     grant.ref, grant.expected_old, grant.archived_j)
                == (r.cleanup_id, r.repository_id, r.issue,
                    r.ref, r.expected_old, r.archived_j)
                and grant.decision_id != approval.terminal_facts.human_decision_id
                and grant.decision_id != self._original_archive_decision_id(
                    approval.operation),
                "cleanup_fresh_human_grant_denied")
        return grant

    def _outcome(self, op: CleanupOperation, state: str) -> CleanupOutcome:
        r = self._request(op)
        return CleanupOutcome(state, r.issue, r.ref, r.expected_old, r.archived_j)

    def _moved_after_confirmed_archive(self, op: CleanupOperation) -> bool:
        """After B0 refuses a moved ref, independently recheck preserved J."""
        h = self.history
        v = Validation(h.identity.repository, h.repository.genesis, h.reader, h.limits)
        r = self._request(op)
        try:
            a = v.observe(h.observer, ARCHIVE_REF)
            v.archive(a)
            original = v.nodes.get(r.archived_j)
            need = (original is not None and original.role == "archive"
                    and original.parents == (
                        op.archive_operation.expected_archive,
                        op.archive_operation.expected_issue))
            moved = v.observe(h.observer, r.ref, absent=True)
            v.check()
            return bool(need and moved is not None and moved != r.expected_old
                        and v.observe(h.observer, ARCHIVE_REF) == a
                        and v.observe(h.observer, r.ref, absent=True) == moved)
        except (InvalidMetadata, ValueError, TypeError, KeyError):
            return False

    def _reconcile(self, op: CleanupOperation) -> CleanupOutcome:
        # Saved intent never calls current terminal, authorization or delete.
        try:
            h = self.history.inspect(op.archive_operation, op.archive_outcome)
            return self._outcome(op, "DELETED" if h.issue_ref_state == "ALREADY_ABSENT"
                                 else "STILL_C" if h.issue_ref_state == "PRESENT_EXACT"
                                 else "UNKNOWN")
        except CleanupHistoryRefused as error:
            if str(error) == "cleanup_issue_ref_moved" and self._moved_after_confirmed_archive(op):
                return self._outcome(op, "MOVED")
            return self._outcome(op, "UNKNOWN")
        except Exception:
            return self._outcome(op, "UNKNOWN")

    def cleanup(self, op: CleanupOperation) -> CleanupOutcome:
        require(type(op) is CleanupOperation, "cleanup_operation_required")
        self._attach()
        r = self._request(op)
        key = _key(r.repository_id, r.issue, r.expected_old, r.archived_j)
        saved = self._saved(op, key)
        if saved is not None:
            return self._reconcile(op)

        intent_attempted = False
        completed: CleanupOutcome | None = None
        deadline = time.monotonic() + self.history.limits.seconds
        try:
            with ExitStack() as stack:
                lock = stack.enter_context(_NoSuppressExit(self.hold_quiescence(op)))
                require(type(lock) is CleanupQuiescenceLease,
                        "cleanup_host_lock_required")
                lock.check(op)
                self._attach()
                before = self._history(op)
                require(before.issue_ref_state == "PRESENT_EXACT",
                        "cleanup_issue_not_present")
                self._prior(op)
                facts = self._terminal(op, before)
                approval = CleanupApproval(op, before, facts, r)
                # The Host must hold the same grant synchronization lock
                # used by Human revocation until delete and readback finish.
                grant_lock = stack.enter_context(
                    _NoSuppressExit(self.hold_human_grant(approval)))
                require(type(grant_lock) is CleanupHumanGrantLease,
                        "cleanup_human_grant_lock_required")
                grant = self._grant(approval)
                grant_lock.check(approval, grant)
                lock.check(op)
                self._attach()
                require(time.monotonic() < deadline
                        and self._history(op) == before
                        and self._terminal(op, before) == facts,
                        "cleanup_authority_changed_before_intent")
                self._prior(op)

                intent = self._base_record(op)
                intent.update(decision_id=grant.decision_id,
                              terminal_facts_digest=hashlib.sha256(
                                  _canonical(asdict(facts))).hexdigest())
                # From here, even a failed fsync or a suppressed context
                # exception requires recovery; no new delete attempt.
                intent_attempted = True
                self.journal.create(key, intent)
                lock.check(op)
                self._attach()
                require(time.monotonic() < deadline
                        and self._history(op) == before
                        and self._terminal(op, before) == facts,
                        "cleanup_authority_changed_after_intent")
                self._prior(op)
                grant_again = self._grant(approval)
                require(grant_again.decision_id == grant.decision_id,
                        "cleanup_human_decision_changed_at_effect")
                grant_lock.check(approval, grant_again)
                lock.check(op)
                self._attach()
                # Authorization itself may block or race a ref/Task change;
                # requalify immediately after the final grant and before effect.
                require(time.monotonic() < deadline
                        and self._history(op) == before
                        and self._terminal(op, before) == facts,
                        "cleanup_authority_changed_at_effect")
                self._prior(op)
                lock.check(op)
                grant_lock.check(approval, grant_again)
                self._attach()
                require(time.monotonic() < deadline,
                        "cleanup_deadline_expired_before_delete")
                try:
                    acknowledged = self.delete(r)
                except BaseException as error:
                    raise CleanupUncertain("cleanup_delete_ack_unknown") from error
                if acknowledged is not True:
                    raise CleanupUncertain("cleanup_delete_not_confirmed")
                after = self._history(op)
                require(after.issue_ref_state == "ALREADY_ABSENT",
                        "cleanup_delete_readback_not_absent")
                lock.check(op)
                grant_lock.check(approval, grant_again)
                self._attach()
                # Do not report DELETED until BOTH Host contexts have exited
                # without any suppressed/raised exit error.
                completed = self._outcome(op, "DELETED")
            require(type(completed) is CleanupOutcome,
                    "cleanup_context_suppressed")
            return completed
        except CleanupUncertain:
            raise
        except CleanupRefused as error:
            if intent_attempted:
                raise CleanupUncertain("cleanup_reconcile_after_intent") from error
            raise
        except BaseException as error:
            if intent_attempted:
                raise CleanupUncertain("cleanup_interrupted_after_intent") from error
            raise CleanupRefused("cleanup_host_unavailable_before_intent") from error
