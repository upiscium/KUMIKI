"""#337 Phase 2C1: opt-in, source-only v2 Issue metadata publisher.

The trusted Host holds a #256 AttachmentLease, supplies authenticated
repository/Issue identity and explicit operation authority, and implements
exact direct-ref, expected-old non-force publication. No transport, daemon,
Task/Session/model, archive writer or deletion capability lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from collections.abc import Callable
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "components/agent-core-v4"))

import metadata_codec as codec
import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_bootstrap import ApprovedIssue, BootstrapIdentity
from kagari_metadata_v2_host_binding import RegisteredIssue, RepositoryIdentity
from kagari_metadata_v2_task_admission import CurrentTaskReader
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import (
    ARCHIVE_REF, Bundle, InvalidMetadata, Limits, MetadataV2, ObjectReader,
    Observer, UncertainPublication, Update, Validation, Writer,
    issue_ref, json_container_markers, oid,
)
from task_record import decode_record, TaskRecordError


_DOMAIN = b"kagari-metadata-v2-issue-write/v1\n"
_NONCE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_MAX_REQUEST = 4096
_ALLOWED = frozenset(("evidence", "task-view-snapshot", "task-record"))


class IssueWriteRefused(ValueError):
    """Invalid, unauthorized, conflicting or foreign source-only operation."""


class IssueWriteUncertain(IssueWriteRefused):
    """Attempted ref effect may have happened; never issue blind retries."""


def need(ok: bool, reason: str) -> None:
    if not ok:
        raise IssueWriteRefused(reason)


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _key(repository_id: int, issue: int, expected_issue: str) -> str:
    # A create-once intent belongs to the original expected Issue HEAD,
    # independent of supplied operation ID. Same-head competing proposals
    # MUST collide, rather than create a second random candidate.
    return hashlib.sha256(_DOMAIN + _json(
        ["issue-head", repository_id, issue, expected_issue])).hexdigest()


@dataclass(frozen=True, slots=True)
class IssueWrite:
    """Exact one-shot operation chosen by trusted Host, not by a Task leaf."""

    operation_id: str
    expected_archive: str
    expected_issue: str
    objects: tuple[bytes, ...]
    next_record_id: str | None = None

    def __post_init__(self) -> None:
        need(type(self.operation_id) is str and _NONCE.fullmatch(self.operation_id)
             is not None, "issue_write_operation_id_invalid")
        try:
            oid(self.expected_archive)
            oid(self.expected_issue)
            if self.next_record_id is not None:
                codec.object_path("task-record", self.next_record_id)
        except (InvalidMetadata, ValueError, TypeError):
            raise IssueWriteRefused("issue_write_oid_invalid") from None
        need(type(self.objects) is tuple and
             1 <= len(self.objects) <= _MAX_REQUEST and
             all(type(obj) is bytes for obj in self.objects),
             "issue_write_objects_invalid")


@dataclass(frozen=True, slots=True)
class IssueWriteApproval:
    """Exact proposed Commit/objects, ref CAS and approved Issue authority."""

    identity: BootstrapIdentity
    approved: ApprovedIssue
    registered: RegisteredIssue
    operation_id: str
    expected_archive: str
    expected_issue: str
    candidate: str
    object_ids: tuple[str, ...]
    next_record_id: str | None


@dataclass(frozen=True, slots=True)
class IssueWriteResult:
    state: str  # PUBLISHED / RECOVERED, never assumed from ACK alone
    issue_commit: str
    previous_issue_commit: str
    record_id: str


class IssuePublisher:
    """A narrowly scoped Host lane: one Issue, one pinned first-lifecycle Record.

    Journal candidate/attempt is fsync'ed BEFORE calling the Host's writer.
    Host must persist bundle objects, atomically compare *both* direct refs,
    prove the Issue grant at effect boundary, and non-force update ONLY that
    Issue metadata ref. A named Python callback is NOT server-side CAS.
    """

    def __init__(
        self, identity: BootstrapIdentity, repository_identity: RepositoryIdentity,
        registered: RegisteredIssue, approved: ApprovedIssue, *, lease: gate.AttachmentLease,
        reader: ObjectReader, observer: Observer, journal: RecoveryJournal,
        authorize: Callable[[IssueWriteApproval], bool] | None,
        writer: Writer | None, limits: Limits = Limits(),
    ) -> None:
        need(type(identity) is BootstrapIdentity
             and type(repository_identity) is RepositoryIdentity
             and type(registered) is RegisteredIssue
             and type(approved) is ApprovedIssue
             and type(lease) is gate.AttachmentLease
             and type(journal) is RecoveryJournal
             and type(limits) is Limits
             and callable(reader) and callable(observer)
             and callable(authorize) and callable(writer),
             "issue_write_host_capabilities_required")
        need((identity.repository_id, identity.repository)
             == (repository_identity.repository_id, repository_identity.repository)
             == (registered.repository_id, registered.repository)
             == (approved.repository_id, approved.repository)
             and registered.issue == approved.issue
             and registered.base_revision == approved.base_revision
             and registered.branch_ref == approved.branch_ref
             and identity.attach_digest == lease._fingerprint
             and identity.git_object_format == repository_identity.git_object_format,
             "issue_write_identity_mismatch")
        self.identity, self.registered, self.approved = identity, registered, approved
        self.repository_identity = repository_identity
        self.lease, self.reader, self.observer = lease, reader, observer
        self.journal, self.authorize, self.writer, self.limits = (
            journal, authorize, writer, limits)
        # RepositoryIdentity pins the original #140 B1 orphan genesis; it is
        # supplied and authenticated by the trusted Host, never derived from
        # a Product HEAD, current Archive HEAD or an Issue payload.
        self.current = CurrentTaskReader(
            repository_identity, registered, approved,
            reader=reader, observer=observer, limits=limits,
        )

    def _attach(self) -> None:
        try:
            self.lease.verify()
        except Exception as error:
            raise IssueWriteRefused("issue_write_attachment_invalid") from error
        need(self.lease._fingerprint == self.identity.attach_digest,
             "issue_write_attachment_mismatch")

    def _decode_objects(self, op: IssueWrite, deadline: float) -> tuple[str, ...]:
        # Match the existing Phase 1 pre-decode structural limits; canonical
        # JSON can contain hundreds of thousands of tiny containers under the
        # byte cap. Reject over-budget input *before* json.loads allocates it.
        need(sum(len(item) for item in op.objects) <= self.limits.bytes,
             "issue_write_payload_too_large")
        ids: list[str] = []
        used_containers = 0
        record_bytes = None
        record_count = 0
        for data in op.objects:
            need(time.monotonic() < deadline,
                 "issue_write_predecode_deadline_expired")
            need(len(data) <= codec.MAX_OBJECT_BYTES, "issue_write_object_too_large")
            try:
                used_containers += json_container_markers(data)
            except InvalidMetadata as error:
                raise IssueWriteRefused("issue_write_container_limit") from error
            need(used_containers <= self.limits.containers,
                 "issue_write_cumulative_container_limit")
            need(time.monotonic() < deadline,
                 "issue_write_predecode_deadline_expired")
            try:
                decoded = codec.decode_object(
                    data, expected_repository=self.identity.repository,
                    expected_task=str(self.registered.issue))
            except (ValueError, TypeError) as error:
                raise IssueWriteRefused("issue_write_object_invalid") from error
            need(time.monotonic() < deadline,
                 "issue_write_predecode_deadline_expired")
            kind = decoded["kind"]
            need(kind in _ALLOWED, "issue_write_kind_unsupported")
            if kind == "task-record":
                record_count += 1
                record_bytes = data
            ids.append(hashlib.sha256(b"agentcore-metadata-object/v1\n" + data).hexdigest())
        need(len(ids) == len(set(ids)), "issue_write_duplicate_object")
        need((op.next_record_id is None and record_count == 0)
             or (op.next_record_id is not None and record_count == 1),
             "issue_write_record_update_required")
        if op.next_record_id is not None:
            need(op.next_record_id in ids and
                 op.next_record_id != self.registered.record_id,
                 "issue_write_new_record_id_mismatch")
            try:
                record = decode_record(
                    record_bytes, object_id=op.next_record_id,
                    repository=self.registered.repository,
                    task=str(self.registered.issue),
                    base_revision=self.registered.base_revision,
                    branch_ref=self.registered.branch_ref)
            except (ValueError, TypeError, TaskRecordError) as error:
                raise IssueWriteRefused("issue_write_new_record_invalid") from error
            need(record["payload"]["contract_id"] == self.registered.contract_id
                 and "disposition" in record["payload"],
                 "issue_write_only_terminal_record_transition_supported")
        return tuple(ids)

    def _receipt(self, op: IssueWrite, object_ids: tuple[str, ...], candidate: str) -> dict:
        return {
            "schema": 1, "operation": "issue-write", "version": 2,
            "repository_id": self.identity.repository_id,
            "repository": self.identity.repository,
            "genesis": self.current.identity.genesis,
            "attach_digest": self.identity.attach_digest,
            "issue": self.registered.issue,
            "operation_id": op.operation_id,
            "branch_ref": self.registered.branch_ref,
            "base_revision": self.registered.base_revision,
            "contract_id": self.registered.contract_id,
            "record_id": self.registered.record_id,
            "expected_archive": op.expected_archive,
            "expected_issue": op.expected_issue,
            "candidate": candidate,
            "object_ids": list(object_ids),
            "objects_sha256": [hashlib.sha256(item).hexdigest() for item in op.objects],
            "next_record_id": op.next_record_id,
        }

    def _checked_receipt(self, op: IssueWrite, ids: tuple[str, ...], key: str) -> dict | None:
        try:
            raw = self.journal.read(key)
        except Exception as error:
            # Partial, foreign, corrupt or symlinked Host-owned records never
            # authorize another candidate or a new mutation attempt.
            raise IssueWriteUncertain("issue_write_receipt_unverifiable") from error
        if raw is None:
            return None
        need(type(raw) is dict and raw.keys() ==
             self._receipt(op, ids, "0" * 40).keys(),
             "issue_write_receipt_schema_invalid")
        try:
            candidate = oid(raw["candidate"])
        except (InvalidMetadata, TypeError, ValueError):
            raise IssueWriteRefused("issue_write_receipt_candidate_invalid") from None
        need(raw == self._receipt(op, ids, candidate),
             "issue_write_receipt_identity_mismatch")
        return raw

    def _confirm(self, op: IssueWrite, ids: tuple[str, ...],
                 candidate: str, *, state: str) -> IssueWriteResult:
        """Host-backed proof; no cached candidate overlay and no writer."""
        store = MetadataV2(self.identity.repository, self.current.identity.genesis,
                           self.reader, self.observer, limits=self.limits)
        validator = store.validation()
        task = str(self.registered.issue)
        try:
            archive_tip = validator.observe(self.observer, ARCHIVE_REF)
            archive = validator.archive(archive_tip)
            need(task not in archive.snapshot.tasks,
                 "issue_write_already_archived")
            need(op.expected_archive in validator.nodes
                 and validator.nodes[op.expected_archive].role in ("archive", "genesis"),
                 "issue_write_original_archive_unreachable")
            tip = validator.observe(self.observer, issue_ref(task), absent=True)
            if tip != candidate:
                raise IssueWriteUncertain("issue_write_candidate_not_current")
            current = validator.issue(tip, task)
            prior = validator.issue(op.expected_issue, task)
            need(current.parents == (op.expected_issue,),
                 "issue_write_noninitial_update_parent_invalid")
            need(prior.snapshot.pointers.get(task) == self.registered.record_id,
                 "issue_write_original_record_mismatch")
            expected_record = (op.next_record_id if op.next_record_id is not None
                               else self.registered.record_id)
            need(current.snapshot.pointers.get(task) == expected_record,
                 "issue_write_current_pointer_mismatch")
            # Positive readback must prove the *entire exact proposed tree*,
            # not merely that requested objects happen to be present in a
            # foreign or expanded candidate that a faulty Host wrote.
            exact_files = dict(prior.snapshot.files)
            for data, object_id in zip(op.objects, ids, strict=True):
                env = codec.decode_object(data, expected_repository=self.identity.repository,
                                          expected_task=task)
                path = codec.object_path(env["kind"], object_id)
                need(path not in exact_files or exact_files[path] == data,
                     "issue_write_original_object_conflict")
                exact_files[path] = data
            if op.next_record_id is not None:
                exact_files[f"tasks/{task}/record"] = (op.next_record_id + "\n").encode("ascii")
            need(dict(current.snapshot.files) == exact_files,
                 "issue_write_candidate_tree_not_exact")
            validator.check()
            need(validator.observe(self.observer, ARCHIVE_REF) == archive_tip
                 and validator.observe(self.observer, issue_ref(task), absent=True) == tip,
                 "issue_write_ref_drift_during_confirm")
        except IssueWriteUncertain:
            raise
        except (IssueWriteRefused, InvalidMetadata, ValueError, TypeError, KeyError) as error:
            raise IssueWriteUncertain("issue_write_confirmation_unknown") from error
        return IssueWriteResult(state, candidate, op.expected_issue, expected_record)

    def publish(self, op: IssueWrite) -> IssueWriteResult:
        need(type(op) is IssueWrite, "issue_write_operation_required")
        self._attach()
        admission_deadline = time.monotonic() + self.limits.seconds
        object_ids = self._decode_objects(op, admission_deadline)
        key = _key(self.identity.repository_id, self.registered.issue,
                   op.expected_issue)
        receipt = self._checked_receipt(op, object_ids, key)
        if receipt is not None:
            # Do not invoke MetadataV2.publish_issue or a new candidate nonce.
            # A recovered receipt may name a retired Task; historical positive
            # ref evidence is sufficient to confirm the old effect, not renew
            # permission to write another update.
            try:
                self._attach()
                return self._confirm(op, object_ids, receipt["candidate"], state="RECOVERED")
            except IssueWriteRefused as error:
                raise IssueWriteUncertain("issue_write_reconciliation_required") from error

        try:
            current = self.current.observe()
        except Exception as error:
            raise IssueWriteRefused("issue_write_current_authority_unavailable") from error
        need(current.archive_tip == op.expected_archive
             and current.metadata_tip == op.expected_issue
             and current.record_id == self.registered.record_id,
             "issue_write_expected_tips_changed")

        def wrapped(update: Update, bundle: Bundle, deadline: float) -> bool:
            need(update.repository == self.identity.repository
                 and update.genesis == self.current.identity.genesis
                 and update.task == str(self.registered.issue)
                 and update.ref == issue_ref(str(self.registered.issue))
                 and update.retention_policy is None
                 and update.expected_archive == op.expected_archive
                 and update.expected_issue == op.expected_issue
                 and bundle.commit == update.candidate,
                 "issue_write_effect_identity_mismatch")
            self._attach()
            observed = self.current.observe()
            need(observed == current, "issue_write_current_changed_at_effect")
            approval = IssueWriteApproval(
                self.identity, self.approved, self.registered, op.operation_id,
                update.expected_archive, update.expected_issue, bundle.commit,
                object_ids, op.next_record_id)
            try:
                grant = self.authorize(approval) is True
            except Exception as error:
                raise IssueWriteRefused("issue_write_authorization_unavailable") from error
            need(grant, "issue_write_authorization_denied")
            self._attach()
            need(self.current.observe() == current,
                 "issue_write_authority_changed_during_grant")
            # CurrentTaskReader has its own per-read deadline; neither its
            # callback nor Human approval extends the original publication
            # budget. An expired grant never reaches an external effect.
            need(time.monotonic() < deadline,
                 "issue_write_effect_deadline_expired")
            # Durable original-candidate intent: no transport/remote effect
            # before this exact create-once fsync record. A competing operation
            # rooted at the same Issue tip cannot allocate a second journal.
            self.journal.create(key, self._receipt(op, object_ids, bundle.commit))
            # Fsync may itself block. Check the *same* original deadline again
            # immediately before handing objects/ref capability to the Host.
            if time.monotonic() >= deadline:
                raise IssueWriteUncertain("issue_write_deadline_after_intent")
            return self.writer(update, bundle, deadline)

        store = MetadataV2(
            self.identity.repository, self.current.identity.genesis,
            self.reader, self.observer, issue_writer=wrapped, limits=self.limits)
        try:
            candidate = store.publish_issue(
                str(self.registered.issue), op.objects,
                record_update=(self.registered.record_id, op.next_record_id)
                if op.next_record_id is not None else None)
        except UncertainPublication as error:
            # The wrapper cannot call an external writer until after journal
            # persistence. Without a journal, a denial or refusal made zero
            # mutation attempts; with a journal the ACK must be reconciled.
            try:
                receipt = self._checked_receipt(op, object_ids, key)
            except Exception as lookup_error:
                raise IssueWriteUncertain("issue_write_receipt_unverifiable") from lookup_error
            if receipt is None:
                raise IssueWriteRefused("issue_write_refused_before_effect") from error
            raise IssueWriteUncertain("issue_write_ack_unknown") from error
        except (InvalidMetadata, ValueError, TypeError) as error:
            raise IssueWriteRefused("issue_write_core_refused") from error
        try:
            self._attach()
            return self._confirm(op, object_ids, candidate, state="PUBLISHED")
        except IssueWriteRefused as error:
            raise IssueWriteUncertain("issue_write_postack_uncertain") from error
