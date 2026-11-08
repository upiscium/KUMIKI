"""#337 P3C/B1-B: source-only Host-scoped one-shot cleanup effect contract.

This module is NOT installed or wired to GitHub. A trusted Host supplies an
exact-old-OID direct-ref compare-and-delete callback. It can be exercised
ONLY against disposable local Git fixtures without a separate live gate.
An fsynced, create-once attempt is recorded BEFORE invoking that callback.
An uncertain effect is reconciled by observations; it is NEVER retried here.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
import hashlib
import re
import time
from typing import Callable

from kagari_metadata_v2_archive_host import (
    ArchiveOperation, ArchiveOutcome, ArchiveQuiescenceLease, TerminalFacts,
)
from kagari_metadata_v2_cleanup_history import CleanupHistory
from kagari_metadata_v2_cleanup_intent import (
    CleanupIntentHost, CleanupIntentRefused, CleanupIntentUncertain,
    _canonical, _digest, _intent_key,
)

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class CleanupEffectRefused(ValueError):
    """No current authority or invalid exact history; no effect grant."""


class CleanupEffectUncertain(CleanupEffectRefused):
    """The fixed-C effect may have occurred; READ ONLY, never retry."""


def need(ok: bool, why: str) -> None:
    if not ok:
        raise CleanupEffectRefused(why)


def _effect_key(repository_id: int, issue: int, original_c: str) -> str:
    return hashlib.sha256(b"kagari-v2-cleanup-effect/v1\n" + _canonical(
        ["effect", repository_id, issue, original_c])).hexdigest()


def _confirmation_key(repository_id: int, issue: int, original_c: str) -> str:
    return hashlib.sha256(b"kagari-v2-cleanup-confirmation/v1\n" + _canonical(
        ["confirmation", repository_id, issue, original_c])).hexdigest()


@dataclass(frozen=True, slots=True)
class ExactDeleteRequest:
    """One exact existing Issue ref C; trusted Host must enforce atomic CAS.

    The callback MUST reject symbolic refs and fail if the current ref is not
    direct or its exact old commit differs from expected_old. Blind delete or
    overwriting any other ref is never an allowed implementation.
    """
    repository_id: int
    repository: str
    issue: int
    ref: str
    expected_old: str
    archive_j: str
    archive_current_tip: str
    intent_key: str
    attempt_key: str
    require_direct: bool
    deadline: float


@dataclass(frozen=True, slots=True)
class EffectApproval:
    """Fresh Human grant bound to original Archive and held Host state."""
    history: CleanupHistory
    operation: ArchiveOperation
    outcome: ArchiveOutcome
    current_facts: TerminalFacts
    intent_digest: str
    request: ExactDeleteRequest


@dataclass(frozen=True, slots=True)
class EffectObservation:
    """Informational state only, never a transferable deletion permission."""
    repository_id: int
    issue: int
    ref: str
    expected_old: str
    archive_j: str
    attempt_key: str
    state: str


class CleanupEffectHost:
    """B1-B opt-in source-only Host integration; one external callback max.

    The actual Git deletion transport is NOT implemented here. A fake
    callback in the synthetic tests uses local Git update-ref --no-deref
    -d <ref> <exact-old-C>, without any GitHub I/O.
    """

    def __init__(
        self,
        intent_host: CleanupIntentHost,
        *,
        authorize_effect: Callable[[EffectApproval], bool],
        delete_exact: Callable[[ExactDeleteRequest], bool],
    ) -> None:
        need(type(intent_host) is CleanupIntentHost
             and callable(authorize_effect) and callable(delete_exact),
             "cleanup_effect_host_capabilities_required")
        self.intent_host = intent_host
        self.authorize_effect = authorize_effect
        self.delete_exact = delete_exact

    def _history(self, op: ArchiveOperation,
                 outcome: ArchiveOutcome) -> CleanupHistory:
        self.intent_host._attach()
        try:
            return self.intent_host._read_history(op, outcome)
        except CleanupIntentRefused as error:
            raise CleanupEffectRefused("cleanup_effect_original_history_invalid") from error

    def _intent(self, op: ArchiveOperation, outcome: ArchiveOutcome,
                hist: CleanupHistory) -> dict:
        try:
            reservation = self.intent_host._reservation_saved(op, outcome, hist)
            key = _intent_key(hist.repository_id, hist.issue, hist.issue_tip)
            saved = self.intent_host._saved(op, outcome, hist, key)
        except CleanupIntentUncertain as error:
            raise CleanupEffectUncertain("cleanup_effect_intent_read_unknown") from error
        except CleanupIntentRefused as error:
            raise CleanupEffectRefused("cleanup_effect_intent_invalid") from error
        need(reservation is not None and saved is not None,
             "cleanup_effect_requires_durable_intent_and_reservation")
        return saved

    def _attempt_record(
        self, op: ArchiveOperation, outcome: ArchiveOutcome,
        hist: CleanupHistory, intent: dict,
        current_facts: TerminalFacts,
    ) -> dict:
        return {
            "schema": 1, "version": 1,
            "operation": "cleanup-issue-effect-attempt",
            "mode": "ordinary-git-branch-exact-old-oid",
            "repository_id": hist.repository_id,
            "repository": hist.repository, "issue": hist.issue,
            "attach_digest": self.intent_host.history.identity.attach_digest,
            "genesis": self.intent_host.history.repository.genesis,
            "archive_operation_id": op.operation_id,
            "archive_before": op.expected_archive,
            "issue_before": hist.issue_tip,
            "archive_j": outcome.archive_commit,
            "ref": hist.issue_ref,
            "expected_old": hist.issue_tip,
            "record_id": hist.record_id,
            "contract_id": hist.contract_id,
            "base_revision": hist.base_revision,
            "intent_key": _intent_key(hist.repository_id, hist.issue, hist.issue_tip),
            "intent_digest": _digest(intent),
            "current_terminal_digest": _digest(asdict(current_facts)),
            "human_decision_id": current_facts.human_decision_id,
            "require_direct": True,
            "state": "MAY_HAVE_EXECUTED",
        }

    def _saved_attempt(
        self, op: ArchiveOperation, outcome: ArchiveOutcome,
        hist: CleanupHistory, intent: dict,
    ) -> dict | None:
        key = _effect_key(hist.repository_id, hist.issue, hist.issue_tip)
        try:
            raw = self.intent_host.journal.read(key)
        except Exception as error:
            raise CleanupEffectUncertain("cleanup_effect_attempt_read_unknown") from error
        if raw is None:
            return None
        fixed = {
            "schema": 1, "version": 1,
            "operation": "cleanup-issue-effect-attempt",
            "mode": "ordinary-git-branch-exact-old-oid",
            "repository_id": hist.repository_id,
            "repository": hist.repository, "issue": hist.issue,
            "attach_digest": self.intent_host.history.identity.attach_digest,
            "genesis": self.intent_host.history.repository.genesis,
            "archive_operation_id": op.operation_id,
            "archive_before": op.expected_archive,
            "issue_before": hist.issue_tip,
            "archive_j": outcome.archive_commit,
            "ref": hist.issue_ref, "expected_old": hist.issue_tip,
            "record_id": hist.record_id, "contract_id": hist.contract_id,
            "base_revision": hist.base_revision,
            "intent_key": _intent_key(hist.repository_id, hist.issue, hist.issue_tip),
            "intent_digest": _digest(intent), "require_direct": True,
            "state": "MAY_HAVE_EXECUTED",
        }
        need(type(raw) is dict
             and raw.keys() == fixed.keys() | {
                 "current_terminal_digest", "human_decision_id"}
             and type(raw["schema"]) is int and type(raw["version"]) is int
             and type(raw["repository_id"]) is int and type(raw["issue"]) is int
             and type(raw["require_direct"]) is bool
             and all(raw.get(k) == v for k, v in fixed.items())
             and all(type(raw.get(k)) is str
                     and _HEX64.fullmatch(raw[k]) is not None
                     for k in ("current_terminal_digest", "human_decision_id")),
             "cleanup_effect_saved_attempt_invalid")
        return raw

    def _confirmation_record(self, hist: CleanupHistory, attempt: dict) -> dict:
        return {
            "schema": 1, "version": 1,
            "operation": "cleanup-issue-effect-confirmation",
            "repository_id": hist.repository_id,
            "repository": hist.repository,
            "issue": hist.issue,
            "ref": hist.issue_ref,
            "expected_old": hist.issue_tip,
            "archive_j": hist.archive_candidate,
            "attempt_digest": _digest(attempt),
            "state": "ACKED_EXACT_AND_REF_ABSENT_ARCHIVED",
        }

    def _saved_confirmation(self, hist: CleanupHistory,
                            attempt: dict | None) -> dict | None:
        key = _confirmation_key(hist.repository_id, hist.issue, hist.issue_tip)
        try:
            value = self.intent_host.journal.read(key)
        except Exception as error:
            raise CleanupEffectUncertain("cleanup_effect_confirmation_read_unknown") from error
        if value is None:
            return None
        need(attempt is not None, "cleanup_effect_confirmation_missing_attempt")
        expected = self._confirmation_record(hist, attempt)
        need(type(value) is dict and value.keys() == expected.keys()
             and all(type(value[k]) is int
                     for k in ("schema", "version", "repository_id", "issue"))
             and value == expected, "cleanup_effect_confirmation_invalid")
        return value

    def inspect(self, op: ArchiveOperation,
                outcome: ArchiveOutcome) -> EffectObservation:
        """Read-only recovery; NO further effect, authorization or retries."""
        need(type(op) is ArchiveOperation and type(outcome) is ArchiveOutcome
             and op.retention_policy is None,
             "cleanup_effect_operation_invalid")
        hist = self._history(op, outcome)
        intent = self._intent(op, outcome, hist)
        attempt = self._saved_attempt(op, outcome, hist, intent)
        confirmation = self._saved_confirmation(hist, attempt)
        if confirmation is not None:
            need(hist.issue_ref_state == "ALREADY_ABSENT",
                 "cleanup_effect_confirmed_ref_reappeared")
            state = "ACKED_ABSENT_ARCHIVED"
        elif attempt is None:
            state = ("INTENT_ONLY_REF_PRESENT" if hist.issue_ref_state == "PRESENT_EXACT"
                     else "INTENT_ONLY_REF_ABSENT_UNATTRIBUTED")
        else:
            state = ("ATTEMPT_UNCERTAIN_REF_PRESENT"
                     if hist.issue_ref_state == "PRESENT_EXACT"
                     else "ATTEMPT_UNCERTAIN_REF_ABSENT_UNATTRIBUTED")
        return EffectObservation(
            hist.repository_id, hist.issue, hist.issue_ref, hist.issue_tip,
            outcome.archive_commit,
            _effect_key(hist.repository_id, hist.issue, hist.issue_tip), state)

    def execute_once(self, op: ArchiveOperation,
                     outcome: ArchiveOutcome) -> EffectObservation:
        """One Host callback maximum. Persistent attempt forbids any replay."""
        need(type(op) is ArchiveOperation and type(outcome) is ArchiveOutcome
             and op.retention_policy is None,
             "cleanup_effect_operation_invalid")
        hist = self._history(op, outcome)
        intent = self._intent(op, outcome, hist)
        need(self._saved_attempt(op, outcome, hist, intent) is None,
             "cleanup_effect_existing_attempt_no_retry")
        need(hist.issue_ref_state == "PRESENT_EXACT",
             "cleanup_effect_ref_not_exact_present")
        entered_attempt = False
        deadline = time.monotonic() + self.intent_host.history.limits.seconds
        try:
            with self.intent_host.hold_quiescence(op) as lock:
                need(type(lock) is ArchiveQuiescenceLease,
                     "cleanup_effect_quiescence_lease_required")
                lock.check(op)
                self.intent_host._attach()
                hist = self._history(op, outcome)
                need(hist.issue_ref_state == "PRESENT_EXACT",
                     "cleanup_effect_ref_not_exact_present")
                intent = self._intent(op, outcome, hist)
                need(self._saved_attempt(op, outcome, hist, intent) is None,
                     "cleanup_effect_existing_attempt_no_retry")
                self.intent_host._prior_clear(op)
                facts = self.intent_host._fresh_facts(op, hist)
                req = ExactDeleteRequest(
                    hist.repository_id, hist.repository, hist.issue,
                    hist.issue_ref, hist.issue_tip, outcome.archive_commit,
                    hist.archive_current_tip,
                    _intent_key(hist.repository_id, hist.issue, hist.issue_tip),
                    _effect_key(hist.repository_id, hist.issue, hist.issue_tip),
                    True, deadline)
                approval = EffectApproval(hist, op, outcome, facts,
                                          _digest(intent), req)
                try:
                    authorized = self.authorize_effect(approval)
                except Exception as error:
                    raise CleanupEffectRefused(
                        "cleanup_effect_fresh_Human_authorization_unknown") from error
                need(authorized is True, "cleanup_effect_fresh_Human_denied")
                lock.check(op)
                self.intent_host._attach()
                self.intent_host._prior_clear(op)
                need(self._history(op, outcome) == hist
                     and self.intent_host._fresh_facts(op, hist) == facts
                     and self._intent(op, outcome, hist) == intent
                     and self._saved_attempt(op, outcome, hist, intent) is None
                     and time.monotonic() < deadline,
                     "cleanup_effect_admission_changed_before_marker")

                # This fsynced marker is BEFORE the external delete callback.
                # If marker creation/ACK is unknown, NO callback can run.
                entered_attempt = True
                marker = self._attempt_record(op, outcome, hist, intent, facts)
                self.intent_host.journal.create(req.attempt_key, marker)
                need(self._saved_attempt(op, outcome, hist, intent) == marker,
                     "cleanup_effect_marker_readback_invalid")
                lock.check(op)
                self.intent_host._attach()
                # Prior-attempt clearance can change during marker fsync.
                # Requalify it after the marker as well as before it.
                self.intent_host._prior_clear(op)
                need(self._history(op, outcome) == hist
                     and self.intent_host._fresh_facts(op, hist) == facts
                     and self._intent(op, outcome, hist) == intent
                     and time.monotonic() < deadline,
                     "cleanup_effect_admission_changed_after_marker")
                # Final effect boundary: no authority read may occur after
                # the final prior-clearance and live lease checks. A callback
                # may change clearance during the preceding fresh-facts read.
                self.intent_host._attach()
                self.intent_host._prior_clear(op)
                lock.check(op)
                need(time.monotonic() < deadline,
                     "cleanup_effect_deadline_at_delete_boundary")
                # Trusted Host must perform atomic direct-ref compare-delete,
                # not a blind GitHub REST delete or unrestricted ref push.
                result = self.delete_exact(req)
                need(result is True, "cleanup_effect_delete_ack_unconfirmed")
                lock.check(op)
                self.intent_host._attach()
                self.intent_host._prior_clear(op)
                observed = self._history(op, outcome)
                need(observed.issue_ref_state == "ALREADY_ABSENT"
                     and observed.archive_current_tip == hist.archive_current_tip
                     and self.intent_host._fresh_facts(op, observed) == facts,
                     "cleanup_effect_post_delete_authority_unconfirmed")
                self.intent_host._prior_clear(op)
                lock.check(op)
                self.intent_host._attach()
                self.intent_host._prior_clear(op)
                need(self._history(op, outcome) == observed
                     and self.intent_host._fresh_facts(op, observed) == facts,
                     "cleanup_effect_before_confirmation_authority_changed")
                # Immediately before durable confirmation (no more history
                # reads between the final prior-clear / held-lease checks).
                self.intent_host._attach()
                self.intent_host._prior_clear(op)
                lock.check(op)
                conf_key = _confirmation_key(hist.repository_id, hist.issue,
                                             hist.issue_tip)
                conf = self._confirmation_record(observed, marker)
                self.intent_host.journal.create(conf_key, conf)
                need(self._saved_confirmation(observed, marker) == conf,
                     "cleanup_effect_confirmation_readback_invalid")
                lock.check(op)
                self.intent_host._attach()
                self.intent_host._prior_clear(op)
                verified = self._history(op, outcome)
                need(verified.issue_ref_state == "ALREADY_ABSENT"
                     and verified.archive_current_tip == observed.archive_current_tip
                     and self.intent_host._fresh_facts(op, verified) == facts,
                     "cleanup_effect_post_confirmation_authority_changed")
                # A final callback may have invalidated both prior-clearance
                # and the live quiescence lease during the LAST facts read.
                self.intent_host._attach()
                self.intent_host._prior_clear(op)
                lock.check(op)
                return EffectObservation(
                    hist.repository_id, hist.issue, hist.issue_ref,
                    hist.issue_tip, outcome.archive_commit,
                    req.attempt_key, "ACKED_ABSENT_ARCHIVED")
            if entered_attempt:
                raise CleanupEffectUncertain(
                    "cleanup_effect_context_suppressed_after_attempt")
            raise CleanupEffectRefused(
                "cleanup_effect_context_suppressed_before_attempt")
        except CleanupEffectUncertain:
            raise
        except Exception as error:
            if entered_attempt:
                raise CleanupEffectUncertain(
                    "cleanup_effect_attempt_unknown_no_retry") from error
            if isinstance(error, CleanupEffectRefused):
                raise
            raise CleanupEffectRefused(
                "cleanup_effect_Host_lease_or_admission_unavailable") from error
        except BaseException as error:
            if entered_attempt:
                raise CleanupEffectUncertain(
                    "cleanup_effect_interrupted_after_marker_no_retry") from error
            raise
