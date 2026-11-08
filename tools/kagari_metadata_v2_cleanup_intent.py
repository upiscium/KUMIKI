"""#337 P3C/B1-A: Host-owned source-only Issue cleanup admission and intent.

No Git transport, ref deletion, effect callback, retry, activation or deploy.
Only records a create-once intent using the existing #140 RecoveryJournal.
The next B1-B phase must separately implement one exact leased Git effect and
unknown-ACK reconciliation. A saved intent is NEVER a reusable delete grant.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Callable

import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_archive_host import (
    ArchiveOperation, ArchiveOutcome, ArchiveQuiescenceLease,
    CurrentTerminalRecord, TerminalFacts,
)
from kagari_metadata_v2_cleanup_history import (
    CleanupHistory, CleanupHistoryReader, CleanupHistoryRefused,
    _check_original_terminal_semantics,
)
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import MetadataV2


class CleanupIntentRefused(ValueError):
    """No authority to prepare a new cleanup operation."""


class CleanupIntentUncertain(CleanupIntentRefused):
    """Intent may be durable: inspect the original key, never retry blindly."""


def require(value: bool, reason: str) -> None:
    if not value:
        raise CleanupIntentRefused(reason)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _intent_key(repository_id: int, issue: int, original_c: str) -> str:
    # Same Issue and immutable original C must collide across a changed J or
    # operation nonce. Never reset the key to manufacture fresh authorization.
    return hashlib.sha256(
        b"kagari-v2-cleanup-intent/v1\n"
        + _canonical(["cleanup", repository_id, issue, original_c])
    ).hexdigest()


def _reservation_key(repository_id: int, issue: int, original_c: str) -> str:
    return hashlib.sha256(
        b"kagari-v2-cleanup-reservation/v1\n"
        + _canonical(["reservation", repository_id, issue, original_c])
    ).hexdigest()


def _validate_current_fact_types(facts: dict) -> None:
    """Reject typed dataclass fields containing malformed runtime values.

    A TerminalFacts instance is not self-authenticating, and dataclass type
    hints are not enforced at runtime. Only the trusted Host callback issues
    the facts; the Host must also provide a complete closed-shaped payload.
    """
    required_bools = (
        "product_history_preserved", "product_ancestry_proven",
        "no_unpublished_product_work", "session_stopped",
        "metadata_writers_quiescent",
    )
    required_strings = (
        "repository", "record_id", "contract_id", "branch_ref",
        "base_revision", "route", "human_decision_id", "product_head_oid",
        "product_base_oid", "product_base_ref",
    )
    optional_positive_ints = ("pr_number", "pr_repository_id", "pr_issue")
    optional_strings = (
        "pr_state", "pr_head_oid", "pr_merge_oid", "pr_base_ref",
        "pr_repository", "replacement_repository", "replacement_task",
    )
    require(facts.keys() == TerminalFacts.__dataclass_fields__.keys()
            and type(facts["repository_id"]) is int
            and type(facts["issue"]) is int
            and all(type(facts[field]) is bool for field in required_bools)
            and all(type(facts[field]) is str for field in required_strings)
            and type(facts["human_decision_id"]) is str
            and len(facts["human_decision_id"]) == 64
            and all(c in "0123456789abcdef" for c in facts["human_decision_id"])
            and all(facts[field] is None
                    or (type(facts[field]) is int and facts[field] > 0)
                    for field in optional_positive_ints)
            and all(facts[field] is None or type(facts[field]) is str
                    for field in optional_strings),
            "cleanup_current_terminal_types_invalid")


@dataclass(frozen=True, slots=True)
class CleanupAdmission:
    """Exact fresh Host attestation request; not a transferable credential."""

    history: CleanupHistory
    original_archive_operation: ArchiveOperation
    archived_outcome: ArchiveOutcome
    current_terminal: TerminalFacts


@dataclass(frozen=True, slots=True)
class CleanupIntentState:
    repository_id: int
    issue: int
    ref: str
    expected_old: str
    archived_j: str
    key: str
    state: str  # PREPARED_NO_EFFECT / RESERVED_ONLY_UNRESOLVED / PRESENT_UNRESOLVED / ABSENT_ARCHIVED_UNATTRIBUTED


class CleanupIntentHost:
    """P3C/B1-A: existing Host leases, archive proof and durable journal only."""

    def __init__(
        self, history: CleanupHistoryReader,
        *, lease: gate.AttachmentLease,
        journal: RecoveryJournal,
        hold_quiescence: Callable[[ArchiveOperation], AbstractContextManager],
        read_terminal: Callable[[ArchiveOperation, CurrentTerminalRecord], TerminalFacts],
        prior_attempts_cleared: Callable[[ArchiveOperation], bool],
        authorize: Callable[[CleanupAdmission], bool],
    ) -> None:
        require(type(history) is CleanupHistoryReader
                and type(lease) is gate.AttachmentLease
                and type(journal) is RecoveryJournal
                and all(callable(c) for c in (
                    hold_quiescence, read_terminal, prior_attempts_cleared,
                    authorize)), "cleanup_host_capabilities_required")
        require(history.identity.attach_digest == lease._fingerprint
                and history.journal.directory == journal.directory,
                "cleanup_host_identity_or_journal_mismatch")
        self.history = history
        self.lease = lease
        self.journal = journal
        self.hold_quiescence = hold_quiescence
        self.read_terminal = read_terminal
        self.prior_attempts_cleared = prior_attempts_cleared
        self.authorize = authorize

    def _attach(self) -> None:
        try:
            self.lease.verify()
        except Exception as error:
            raise CleanupIntentRefused("cleanup_attachment_unavailable") from error
        require(self.lease._fingerprint == self.history.identity.attach_digest,
                "cleanup_attach_identity_changed")

    def _read_history(self, op: ArchiveOperation,
                      outcome: ArchiveOutcome) -> CleanupHistory:
        try:
            return self.history.inspect(op, outcome)
        except CleanupHistoryRefused as error:
            raise CleanupIntentRefused("cleanup_original_history_unavailable") from error

    def _fresh_facts(self, op: ArchiveOperation, hist: CleanupHistory) -> TerminalFacts:
        r = self.history.registered
        current = CurrentTerminalRecord(
            hist.archive_current_tip, hist.issue_tip, r.record_id, r.contract_id, None)
        try:
            record = MetadataV2(
                hist.repository, self.history.repository.genesis,
                self.history.reader, self.history.observer,
                limits=self.history.limits
            ).read_object(hist.issue_tip, "task-record", r.record_id,
                          task=str(r.issue), subject=r.base_revision)
            disposition = record["payload"].get("disposition")
            current = CurrentTerminalRecord(
                hist.archive_current_tip, hist.issue_tip,
                r.record_id, r.contract_id, disposition)
            facts = self.read_terminal(op, current)
            require(type(facts) is TerminalFacts,
                    "cleanup_current_terminal_untyped")
            require((facts.repository_id, facts.repository, facts.issue,
                     facts.record_id, facts.contract_id, facts.branch_ref,
                     facts.base_revision)
                    == (hist.repository_id, hist.repository, hist.issue,
                        hist.record_id, hist.contract_id, r.branch_ref,
                        hist.base_revision), "cleanup_current_terminal_identity_mismatch")
            # Only trusted fresh Host callback may supply facts. Explicitly
            # check all runtime dataclass field types BEFORE creating a durable
            # record, even on CANCELLED/SUPERSEDED where some booleans are not
            # semantically required to be True.
            current_payload = asdict(facts)
            _validate_current_fact_types(current_payload)
            _check_original_terminal_semantics(
                current_payload, disposition, repository_id=hist.repository_id,
                repository=hist.repository, issue=hist.issue)
        except CleanupIntentRefused:
            raise
        except Exception as error:
            raise CleanupIntentRefused("cleanup_current_terminal_unavailable") from error
        return facts

    def _prior_clear(self, op: ArchiveOperation) -> None:
        try:
            permitted = self.prior_attempts_cleared(op)
        except Exception as error:
            raise CleanupIntentRefused("cleanup_prior_attempts_unknown") from error
        require(permitted is True, "cleanup_prior_attempts_unresolved")

    def _reservation_record(self, op: ArchiveOperation,
                            outcome: ArchiveOutcome, hist: CleanupHistory) -> dict:
        """Stable original-C reservation, written before an intent attempt."""
        return {
            "schema": 1,
            "operation": "cleanup-intent-reservation",
            "version": 1,
            "repository_id": hist.repository_id,
            "repository": hist.repository,
            "issue": hist.issue,
            "attach_digest": self.history.identity.attach_digest,
            "archive_before": op.expected_archive,
            "original_issue_C": hist.issue_tip,
            "archived_J": outcome.archive_commit,
            "issue_ref": hist.issue_ref,
            "record_id": hist.record_id,
            "contract_id": hist.contract_id,
            "base_revision": hist.base_revision,
            "state": "RESERVED_NO_REMOTE_EFFECT",
        }

    def _reservation_saved(self, op: ArchiveOperation,
                           outcome: ArchiveOutcome, hist: CleanupHistory) -> dict | None:
        key = _reservation_key(hist.repository_id, hist.issue, hist.issue_tip)
        try:
            value = self.journal.read(key)
        except Exception as error:
            raise CleanupIntentUncertain("cleanup_reservation_unreadable") from error
        if value is None:
            return None
        expected = self._reservation_record(op, outcome, hist)
        require(type(value) is dict and value.keys() == expected.keys()
                and all(value[k] == item for k, item in expected.items())
                and all(type(value[k]) is int
                        for k in ("schema", "version", "repository_id", "issue")),
                "cleanup_reservation_identity_or_schema_mismatch")
        return value

    def _saved(self, op: ArchiveOperation, outcome: ArchiveOutcome,
               hist: CleanupHistory, key: str) -> dict | None:
        try:
            saved = self.journal.read(key)
        except Exception as error:
            raise CleanupIntentUncertain("cleanup_intent_unreadable") from error
        if saved is None:
            return None
        expected = {
            "schema": 1, "operation": "cleanup-issue-intent",
            "version": 1, "mode": "ordinary-git-branch",
            "repository_id": hist.repository_id,
            "repository": hist.repository,
            "issue": hist.issue,
            "attach_digest": self.history.identity.attach_digest,
            "genesis": self.history.repository.genesis,
            "archive_operation_id": op.operation_id,
            "archive_before": op.expected_archive,
            "issue_before": hist.issue_tip,
            "archive_j": outcome.archive_commit,
            "issue_ref": hist.issue_ref,
            "record_id": hist.record_id,
            "contract_id": hist.contract_id,
            "base_revision": hist.base_revision,
            "effect": "NONE_PREPARED_ONLY",
        }
        require(type(saved) is dict
                and saved.keys() == expected.keys() | {
                    "terminal_facts_digest", "human_decision_id"}
                and type(saved["schema"]) is int
                and type(saved["version"]) is int
                and type(saved["repository_id"]) is int
                and type(saved["issue"]) is int
                and all(saved.get(field) == value for field, value in expected.items())
                and type(saved.get("terminal_facts_digest")) is str
                and len(saved["terminal_facts_digest"]) == 64
                and all(c in "0123456789abcdef" for c in saved["terminal_facts_digest"])
                and type(saved.get("human_decision_id")) is str
                and len(saved["human_decision_id"]) == 64
                and all(c in "0123456789abcdef" for c in saved["human_decision_id"]),
                "cleanup_intent_identity_or_schema_mismatch")
        return saved

    def read_saved(self, op: ArchiveOperation,
                   outcome: ArchiveOutcome) -> CleanupIntentState:
        """Read-only restart reconciliation; NEVER retries or authorizes delete."""
        self._attach()
        hist = self._read_history(op, outcome)
        key = _intent_key(hist.repository_id, hist.issue, hist.issue_tip)
        reservation = self._reservation_saved(op, outcome, hist)
        need = self._saved(op, outcome, hist, key)
        require(reservation is not None or need is None,
                "cleanup_intent_missing_reservation")
        require(reservation is not None, "cleanup_intent_absent")
        state = ("RESERVED_ONLY_UNRESOLVED" if need is None
                 else "PRESENT_UNRESOLVED" if hist.issue_ref_state == "PRESENT_EXACT"
                 else "ABSENT_ARCHIVED_UNATTRIBUTED")
        return CleanupIntentState(
            hist.repository_id, hist.issue, hist.issue_ref, hist.issue_tip,
            outcome.archive_commit, key, state)

    def prepare(self, op: ArchiveOperation, outcome: ArchiveOutcome) -> CleanupIntentState:
        """Durably record an exact potential cleanup, with NO Git ref effect.

        Any later invocation with the same original C must use read_saved()
        and separate recovery. Neither outcome means 'safe to delete now'.
        """
        require(type(op) is ArchiveOperation and type(outcome) is ArchiveOutcome
                and op.retention_policy is None,
                "cleanup_operation_requires_ordinary_archive")
        self._attach()
        try:
            hist = self._read_history(op, outcome)
            key = _intent_key(hist.repository_id, hist.issue, hist.issue_tip)
            require(self._reservation_saved(op, outcome, hist) is None
                    and self._saved(op, outcome, hist, key) is None,
                    "cleanup_existing_intent_requires_reconciliation")
            require(hist.issue_ref_state == "PRESENT_EXACT",
                    "cleanup_issue_ref_not_active")
        except CleanupIntentRefused:
            raise
        entered_intent_phase = False
        try:
            with self.hold_quiescence(op) as lock:
                require(type(lock) is ArchiveQuiescenceLease,
                        "cleanup_quiescence_lease_required")
                lock.check(op)
                self._attach()
                hist = self._read_history(op, outcome)
                require(hist.issue_ref_state == "PRESENT_EXACT",
                        "cleanup_issue_ref_not_active")
                key = _intent_key(hist.repository_id, hist.issue, hist.issue_tip)
                require(self._reservation_saved(op, outcome, hist) is None
                        and self._saved(op, outcome, hist, key) is None,
                        "cleanup_existing_intent_requires_reconciliation")
                self._prior_clear(op)
                facts = self._fresh_facts(op, hist)
                grant = CleanupAdmission(hist, op, outcome, facts)
                try:
                    accepted = self.authorize(grant)
                except Exception as error:
                    raise CleanupIntentRefused("cleanup_human_grant_unavailable") from error
                require(accepted is True, "cleanup_human_grant_denied")

                # Repeat live Host and archived-history checks under the SAME
                # held lock after granting, before recording any intent.
                lock.check(op)
                self._attach()
                self._prior_clear(op)
                require(self._read_history(op, outcome) == hist
                        and self._fresh_facts(op, hist) == facts,
                        "cleanup_authority_changed_before_intent")
                record = {
                    "schema": 1, "operation": "cleanup-issue-intent",
                    "version": 1, "mode": "ordinary-git-branch",
                    "repository_id": hist.repository_id,
                    "repository": hist.repository,
                    "issue": hist.issue,
                    "attach_digest": self.history.identity.attach_digest,
                    "genesis": self.history.repository.genesis,
                    "archive_operation_id": op.operation_id,
                    "archive_before": op.expected_archive,
                    "issue_before": hist.issue_tip,
                    "archive_j": outcome.archive_commit,
                    "issue_ref": hist.issue_ref,
                    "record_id": hist.record_id,
                    "contract_id": hist.contract_id,
                    "base_revision": hist.base_revision,
                    "terminal_facts_digest": _digest(asdict(facts)),
                    "human_decision_id": facts.human_decision_id,
                    "effect": "NONE_PREPARED_ONLY",
                }
                # Durable fixed-C reservation MUST be successfully fsynced
                # before we even attempt the full intent. An invisible
                # reservation after uncertain creation is safe to re-create
                # only because no full intent or remote Git effect was tried.
                # If reservation exists, any later prepare ALWAYS refuses,
                # including when a full intent is absent after lost ACK.
                reservation_key = _reservation_key(
                    hist.repository_id, hist.issue, hist.issue_tip)
                reservation_record = self._reservation_record(op, outcome, hist)
                entered_intent_phase = True
                self.journal.create(reservation_key, reservation_record)
                require(self._reservation_saved(op, outcome, hist)
                        == reservation_record,
                        "cleanup_reservation_readback_mismatch")
                lock.check(op)
                self._attach()
                require(self._read_history(op, outcome) == hist
                        and self._fresh_facts(op, hist) == facts,
                        "cleanup_authority_changed_after_reservation")
                # The only effect here is local journal create-once+fsync.
                # B1-B remote deletion is NOT part of this module.
                self.journal.create(key, record)
                require(self._saved(op, outcome, hist, key) == record,
                        "cleanup_intent_postwrite_readback_mismatch")
                lock.check(op)
                self._attach()
                require(self._read_history(op, outcome) == hist,
                        "cleanup_refs_moved_during_intent")
                return CleanupIntentState(
                    hist.repository_id, hist.issue, hist.issue_ref,
                    hist.issue_tip, outcome.archive_commit,
                    key, "PREPARED_NO_EFFECT")
            # If a malformed context suppresses the body exception, do not
            # return a misleading success or issue a second receipt.
            if entered_intent_phase:
                raise CleanupIntentUncertain("cleanup_context_suppressed_after_intent")
            raise CleanupIntentRefused("cleanup_context_suppressed_before_intent")
        except CleanupIntentUncertain:
            raise
        except Exception as error:
            if entered_intent_phase:
                raise CleanupIntentUncertain(
                    "cleanup_intent_or_context_unknown_no_retry") from error
            if isinstance(error, CleanupIntentRefused):
                raise
            raise CleanupIntentRefused("cleanup_host_quiescence_unavailable") from error
        except BaseException as error:
            if entered_intent_phase:
                raise CleanupIntentUncertain(
                    "cleanup_intent_interrupted_no_retry") from error
            raise
