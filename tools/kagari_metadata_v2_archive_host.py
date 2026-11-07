"""#337 Phase 2C2: source-only Host terminality and durable Archive coordinator.

No GitHub transport, actual Task/Session/Main owner, deletion, install, release,
model dispatch, Product Git mutation, or v1 metadata fallback. The trusted
Host supplies a held quiescence context plus authenticated terminal facts,
operation grants, bounded direct-ref reader and a genuine non-force CAS writer.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "components/agent-core-v4"))

import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_bootstrap import ApprovedIssue, BootstrapIdentity
from kagari_metadata_v2_host_binding import RegisteredIssue, RepositoryIdentity
from kagari_vnext_trusted_host import RecoveryJournal
import metadata_codec as codec
from metadata_v2 import (
    ARCHIVE_REF, Bundle, InvalidMetadata, Limits, MetadataV2, ObjectReader,
    Observer, Update, UncertainPublication, Validation, Writer, issue_ref, oid,
    union,
)
from metadata_v2_archive import archive_issue
from task_record import decode_contract, decode_record, TaskRecordError


_DOMAIN = b"kagari-metadata-v2-archive-attempt/v1\n"
_HEX32 = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REF_MAIN = "refs/heads/main"


class ArchiveRefused(ValueError):
    """Unqualified current Task/terminality or stale/foreign Host capability."""


class ArchiveUncertain(ArchiveRefused):
    """An Archive ref effect may have happened; do not construct another J."""


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ArchiveRefused(reason)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _key(repository_id: int, issue: int, archive_oid: str, issue_oid: str) -> str:
    # Same A,C candidates must collide despite different operation IDs/nonces.
    # A later independent A' operation has a different receipt key, but must
    # independently prove the prior A,C attempt was not ambiguous.
    return hashlib.sha256(_DOMAIN + _canonical(
        ["archive", repository_id, issue, archive_oid, issue_oid])).hexdigest()


@dataclass(frozen=True, slots=True)
class ArchiveOperation:
    """One exact explicitly authorized first-lifecycle Archive attempt."""

    operation_id: str
    expected_archive: str
    expected_issue: str
    record_id: str
    # Legacy v2 journal label. None is a new ordinary-branch v3 operation.
    # This is never proof of external GitHub Ruleset enforcement.
    retention_policy: str | None = None

    def __post_init__(self) -> None:
        require(type(self.operation_id) is str and _HEX32.fullmatch(self.operation_id)
                is not None, "archive_operation_id_invalid")
        try:
            oid(self.expected_archive)
            oid(self.expected_issue)
        except (InvalidMetadata, ValueError, TypeError):
            raise ArchiveRefused("archive_expected_oid_invalid") from None
        require(type(self.record_id) is str and _HEX64.fullmatch(self.record_id)
                is not None, "archive_record_id_invalid")
        if self.retention_policy is not None:
            require(type(self.retention_policy) is str
                    and 1 <= len(self.retention_policy) <= 512
                    and bool(self.retention_policy.strip()),
                    "archive_legacy_policy_identifier_invalid")


@dataclass(frozen=True, slots=True)
class TerminalFacts:
    """Facts attested by the *trusted Host*, NOT self-authenticating authority.

    The Host must collect independently authenticated Product/GitHub/Session
    observations and prove all unique Product work preserved. A bool supplied
    by a Task or metadata JSON must never instantiate this authority.
    """

    repository_id: int
    repository: str
    issue: int
    record_id: str
    contract_id: str
    branch_ref: str
    base_revision: str
    route: str                       # MERGED_PR / CANCELLED / SUPERSEDED
    human_decision_id: str           # SHA-256 of independent Human decision
    product_head_oid: str
    product_base_oid: str
    product_base_ref: str
    product_history_preserved: bool
    product_ancestry_proven: bool
    no_unpublished_product_work: bool
    session_stopped: bool
    metadata_writers_quiescent: bool
    pr_number: int | None = None
    pr_state: str | None = None
    pr_head_oid: str | None = None
    pr_merge_oid: str | None = None
    pr_base_ref: str | None = None
    pr_repository_id: int | None = None
    pr_repository: str | None = None
    pr_issue: int | None = None
    replacement_repository: str | None = None
    replacement_task: str | None = None


@dataclass(frozen=True, slots=True)
class ArchiveApproval:
    """Exact operation/terminal proof and Git candidate passed to Human Host."""

    identity: BootstrapIdentity
    registered: RegisteredIssue
    approved: ApprovedIssue
    operation: ArchiveOperation
    facts: TerminalFacts
    candidate: str


@dataclass(frozen=True, slots=True)
class ArchiveOutcome:
    state: str  # PUBLISHED / RECOVERED
    archive_commit: str
    issue_commit: str
    record_id: str


@dataclass(frozen=True, slots=True)
class CurrentTerminalRecord:
    archive_tip: str
    issue_tip: str
    record_id: str
    contract_id: str
    disposition: dict | None


class ArchiveQuiescenceLease:
    """A Host-held exclusive lifecycle lock, not a synthetic Main owner.

    This object does not acquire the lock. The Host owns the live scoped
    context manager, must keep it held across archive()+readback and make
    verify() return True ONLY if the original live lock is still owned.
    """

    def __init__(self, verify: Callable[[ArchiveOperation], bool]) -> None:
        require(callable(verify), "archive_quiescence_verifier_required")
        self._verify = verify

    def check(self, operation: ArchiveOperation) -> None:
        try:
            actual = self._verify(operation)
        except Exception as error:
            raise ArchiveRefused("archive_quiescence_unknown") from error
        require(actual is True, "archive_quiescence_unverified")


class ArchiveHost:
    """Opt-in, one-Issue source-only integration with existing v2 Archive core.

    Fresh publication must hold positive v2 lifecycle/quiescence authority
    across the whole operation. A saved attempt is read-back-only and cannot
    resume a ref effect, even if the Host now has an independent grant.
    """

    def __init__(
        self, identity: BootstrapIdentity,
        repository_identity: RepositoryIdentity,
        registered: RegisteredIssue,
        approved: ApprovedIssue,
        *, lease: gate.AttachmentLease,
        reader: ObjectReader,
        observer: Observer,
        journal: RecoveryJournal,
        hold_quiescence: Callable[[ArchiveOperation], AbstractContextManager],
        read_terminal: Callable[[ArchiveOperation, CurrentTerminalRecord], TerminalFacts],
        prior_attempts_cleared: Callable[[ArchiveOperation], bool],
        authorize: Callable[[ArchiveApproval], bool],
        writer: Writer,
        limits: Limits = Limits(),
    ) -> None:
        require(type(identity) is BootstrapIdentity
                and type(repository_identity) is RepositoryIdentity
                and type(registered) is RegisteredIssue
                and type(approved) is ApprovedIssue
                and type(lease) is gate.AttachmentLease
                and type(journal) is RecoveryJournal
                and type(limits) is Limits
                and all(callable(v) for v in
                        (reader, observer, hold_quiescence, read_terminal,
                         prior_attempts_cleared, authorize, writer)),
                "archive_host_capabilities_required")
        require((identity.repository_id, identity.repository)
                == (repository_identity.repository_id, repository_identity.repository)
                == (registered.repository_id, registered.repository)
                == (approved.repository_id, approved.repository)
                and registered.issue == approved.issue
                and registered.base_revision == approved.base_revision
                and registered.branch_ref == approved.branch_ref
                and identity.git_object_format == repository_identity.git_object_format
                and identity.attach_digest == lease._fingerprint,
                "archive_host_identity_mismatch")
        self.identity = identity
        self.repository_identity = repository_identity
        self.registered = registered
        self.approved = approved
        self.lease = lease
        self.reader = reader
        self.observer = observer
        self.journal = journal
        self.hold_quiescence = hold_quiescence
        self.read_terminal = read_terminal
        self.prior_attempts_cleared = prior_attempts_cleared
        self.authorize = authorize
        self.writer = writer
        self.limits = limits

    def _attach(self) -> None:
        try:
            self.lease.verify()
        except Exception as error:
            raise ArchiveRefused("archive_attachment_invalid") from error
        require(self.lease._fingerprint == self.identity.attach_digest,
                "archive_attachment_identity_changed")

    def _current(self, operation: ArchiveOperation) -> CurrentTerminalRecord:
        """Current HEAD authority, including canonical cancelled/superseded.

        Unlike Phase 2B2's active-only CurrentTaskReader, terminality must
        distinguish canonical retired records without treating them as a
        new Task or permitting Task dispatch.
        """
        task = str(self.registered.issue)
        v = Validation(self.identity.repository, self.repository_identity.genesis,
                       self.reader, self.limits)
        try:
            a = v.observe(self.observer, ARCHIVE_REF)
            archive = v.archive(a)
            require(task not in archive.snapshot.tasks,
                    "archive_issue_already_in_aggregate")
            c = v.observe(self.observer, issue_ref(task), absent=True)
            require(c is not None, "archive_issue_ref_absent")
            issue = v.issue(c, task)
            rid = issue.snapshot.pointers.get(task)
            require(rid == operation.record_id, "archive_current_record_changed")
            contract = issue.snapshot.objects.get(self.registered.contract_id)
            record = issue.snapshot.objects.get(rid)
            require(record is not None and contract is not None,
                    "archive_current_record_contract_missing")
            record_bytes = issue.snapshot.files.get(codec.object_path("task-record", rid))
            contract_bytes = issue.snapshot.files.get(
                codec.object_path("contract", self.registered.contract_id))
            require(record_bytes is not None and contract_bytes is not None,
                    "archive_current_record_contract_missing")
            validated_record = decode_record(
                record_bytes, object_id=rid, repository=self.identity.repository,
                task=task, base_revision=self.registered.base_revision,
                branch_ref=self.registered.branch_ref)
            validated_contract = decode_contract(
                contract_bytes, object_id=self.registered.contract_id,
                repository=self.identity.repository, task=task,
                base_revision=self.registered.base_revision)
            require(validated_record["payload"]["contract_id"] == self.registered.contract_id
                    and validated_contract["payload"]["title"] == self.approved.title
                    and validated_contract["payload"]["body"] == self.approved.body,
                    "archive_contract_or_requirements_changed")
            require(a == operation.expected_archive and c == operation.expected_issue,
                    "archive_expected_heads_changed")
            v.check()
            require(v.observe(self.observer, ARCHIVE_REF) == a
                    and v.observe(self.observer, issue_ref(task), absent=True) == c,
                    "archive_refs_moved_during_authority")
            disposition = validated_record["payload"].get("disposition")
        except ArchiveRefused:
            raise
        except (InvalidMetadata, TaskRecordError, ValueError, TypeError, KeyError) as error:
            raise ArchiveRefused("archive_current_metadata_unavailable") from error
        return CurrentTerminalRecord(a, c, rid, self.registered.contract_id,
                                     disposition)

    def _facts(self, op: ArchiveOperation,
               current: CurrentTerminalRecord) -> TerminalFacts:
        try:
            f = self.read_terminal(op, current)
        except Exception as error:
            raise ArchiveRefused("archive_terminal_observation_unavailable") from error
        need = require
        need(type(f) is TerminalFacts, "archive_terminal_facts_untyped")
        expected = (self.identity.repository_id, self.identity.repository,
                    self.registered.issue, current.record_id, current.contract_id,
                    self.registered.branch_ref, self.registered.base_revision)
        need((f.repository_id, f.repository, f.issue, f.record_id, f.contract_id,
              f.branch_ref, f.base_revision) == expected,
             "archive_terminal_binding_mismatch")
        need(type(f.human_decision_id) is str
             and _HEX64.fullmatch(f.human_decision_id) is not None,
             "archive_human_decision_unknown")
        try:
            oid(f.product_head_oid)
            oid(f.product_base_oid)
        except (InvalidMetadata, ValueError, TypeError):
            raise ArchiveRefused("archive_product_oids_invalid") from None
        need(f.product_base_ref == _REF_MAIN
             and type(f.product_history_preserved) is bool
             and type(f.product_ancestry_proven) is bool
             and type(f.no_unpublished_product_work) is bool
             and type(f.session_stopped) is bool
             and type(f.metadata_writers_quiescent) is bool
             and f.product_history_preserved is True
             and f.no_unpublished_product_work is True
             and f.session_stopped is True
             and f.metadata_writers_quiescent is True,
             "archive_product_or_session_not_quiescent")
        if current.disposition is None:
            need(f.route == "MERGED_PR" and f.product_ancestry_proven is True
                 and type(f.pr_number) is int and f.pr_number > 0
                 and f.pr_state == "MERGED"
                 and f.pr_repository_id == self.identity.repository_id
                 and f.pr_repository == self.identity.repository
                 and f.pr_issue == self.registered.issue
                 and f.pr_head_oid == f.product_head_oid
                 and f.pr_base_ref == f.product_base_ref
                 and type(f.pr_merge_oid) is str
                 and f.replacement_repository is None and f.replacement_task is None,
                 "archive_product_pr_not_exact_merged")
            try:
                oid(f.pr_merge_oid)
            except (InvalidMetadata, ValueError, TypeError):
                raise ArchiveRefused("archive_product_merge_oid_invalid") from None
        else:
            disposition = current.disposition
            kind = disposition["kind"]
            need(f.route == kind.upper()
                 and f.pr_number is None and f.pr_state is None
                 and f.pr_head_oid is None and f.pr_merge_oid is None
                 and f.pr_base_ref is None and f.pr_repository_id is None
                 and f.pr_repository is None and f.pr_issue is None,
                 "archive_human_disposition_not_exact")
            if kind == "superseded":
                replacement = disposition["replacement"]
                need((f.replacement_repository, f.replacement_task) ==
                     (replacement["repository"], replacement["task"]),
                     "archive_supersession_replacement_mismatch")
            else:
                need(f.replacement_repository is None
                     and f.replacement_task is None,
                     "archive_cancellation_must_not_replace")
        return f

    def _prior_cleared(self, op: ArchiveOperation) -> None:
        """Trusted Host MUST reconcile all earlier attempts for this Issue.

        In particular, moving from A to A' after a lost/stale ACK is not a
        new grant unless the original J was positively proved never
        published. The callback examines durable cross-attempt Host intent,
        not this one-key local journal alone. Missing evidence blocks.
        """
        try:
            cleared = self.prior_attempts_cleared(op)
        except Exception as error:
            raise ArchiveRefused("archive_prior_attempts_unknown") from error
        require(cleared is True, "archive_prior_attempts_unresolved")

    def _record(self, op: ArchiveOperation, fact: TerminalFacts, candidate: str,
                bundle: Bundle) -> dict:
        # Keep #140's private 16-KiB journal bounded even when the archive
        # has thousands of immutable objects. Candidate Git bytes are stored
        # by the Host's authenticated object store, never by the journal.
        inventory = sorted(
            (object_id, kind, hashlib.sha256(raw).hexdigest())
            for object_id, (kind, raw) in bundle.objects.items())
        ordinary = op.retention_policy is None
        record = {
            "schema": 1, "operation": "archive-issue",
            "version": 3 if ordinary else 2,
            "repository_id": self.identity.repository_id,
            "repository": self.identity.repository,
            "genesis": self.repository_identity.genesis,
            "attach_digest": self.identity.attach_digest,
            "issue": self.registered.issue,
            "operation_id": op.operation_id,
            "archive_before": op.expected_archive,
            "issue_before": op.expected_issue,
            "record_id": op.record_id,
            "contract_id": self.registered.contract_id,
            "branch_ref": self.registered.branch_ref,
            "base_revision": self.registered.base_revision,
            "candidate": candidate,
            "terminal_facts": asdict(fact),
            "bundle_object_count": len(inventory),
            "bundle_sha256": hashlib.sha256(_canonical(inventory)).hexdigest(),
        }
        if ordinary:
            record["branch_mode"] = "ordinary-git-branch"
        else:
            # Retain the exact v2 schema for unknown-ACK recovery.
            record["policy"] = op.retention_policy
        return record

    def _saved(self, op: ArchiveOperation, key: str) -> dict | None:
        try:
            saved = self.journal.read(key)
        except Exception as error:
            raise ArchiveUncertain("archive_journal_unreadable") from error
        if saved is None:
            return None
        # The journal key is A/C-derived for both modes. An unresolved
        # legacy intent must not be reinterpreted in ordinary mode, nor
        # overwritten by a different record for the same key.
        ordinary = op.retention_policy is None
        common_fields = {
            "schema", "operation", "version", "repository_id", "repository",
            "genesis", "attach_digest", "issue", "operation_id",
            "archive_before", "issue_before", "record_id", "contract_id",
            "branch_ref", "base_revision", "candidate",
            "terminal_facts", "bundle_object_count", "bundle_sha256",
        }
        mode_field = "branch_mode" if ordinary else "policy"
        require(type(saved) is dict
                and saved.keys() == common_fields | {mode_field},
                "archive_journal_schema_or_mode_mismatch")
        require(type(saved["schema"]) is int
                and type(saved["version"]) is int
                and type(saved["repository_id"]) is int
                and type(saved["issue"]) is int
                and type(saved[mode_field]) is str,
                "archive_journal_scalar_types_invalid")
        require((saved["schema"], saved["operation"], saved["version"],
                 saved["repository_id"], saved["repository"],
                 saved["genesis"], saved["attach_digest"], saved["issue"],
                 saved["operation_id"], saved["archive_before"],
                 saved["issue_before"], saved["record_id"],
                 saved["contract_id"], saved["branch_ref"],
                 saved["base_revision"]) ==
                (1, "archive-issue", 3 if ordinary else 2,
                 self.identity.repository_id, self.identity.repository,
                 self.repository_identity.genesis, self.identity.attach_digest,
                 self.registered.issue, op.operation_id, op.expected_archive,
                 op.expected_issue, op.record_id, self.registered.contract_id,
                 self.registered.branch_ref, self.registered.base_revision),
                "archive_journal_identity_mismatch")
        require(saved[mode_field] ==
                ("ordinary-git-branch" if ordinary else op.retention_policy),
                "archive_journal_mode_or_policy_mismatch")
        try:
            oid(saved["candidate"])
        except (InvalidMetadata, TypeError, ValueError):
            raise ArchiveUncertain("archive_saved_candidate_invalid") from None
        require(type(saved["terminal_facts"]) is dict
                and set(saved["terminal_facts"]) == set(TerminalFacts.__dataclass_fields__)
                and type(saved["bundle_object_count"]) is int
                and 1 <= saved["bundle_object_count"] <= self.limits.entries
                and type(saved["bundle_sha256"]) is str
                and _HEX64.fullmatch(saved["bundle_sha256"]) is not None,
                "archive_saved_terminal_or_objects_invalid")
        return saved

    def _confirm(self, op: ArchiveOperation, candidate: str, *, state: str,
                 facts: TerminalFacts | None = None) -> ArchiveOutcome:
        """Only Host-backed original Git DAG, NEVER candidate overlay.

        A later compatible Archive merge may make J an ancestor. It is never
        evidence authorizing deletion, resumption of active Task or any ref
        effect, and it must still preserve the exact active Issue C.
        """
        task = str(self.registered.issue)
        v = Validation(self.identity.repository, self.repository_identity.genesis,
                       self.reader, self.limits)
        try:
            archive_tip = v.observe(self.observer, ARCHIVE_REF)
            archive = v.archive(archive_tip)
            require(candidate in v.nodes
                    and v.nodes[candidate].role == "archive",
                    "archive_candidate_not_reachable")
            j = v.nodes[candidate]
            require(j.parents == (op.expected_archive, op.expected_issue),
                    "archive_candidate_parents_changed")
            prior_a = v.nodes[op.expected_archive]
            prior_c = v.nodes[op.expected_issue]
            require(prior_a.role in {"archive", "genesis"}
                    and prior_c.role == "issue" and prior_c.task == task
                    and task not in prior_a.snapshot.tasks,
                    "archive_candidate_provenance_invalid")
            expected = union(prior_a.snapshot, prior_c.snapshot)
            require(dict(j.snapshot.files) == expected,
                    "archive_candidate_tree_changed")
            require(prior_c.snapshot.pointers.get(task) == op.record_id
                    and j.snapshot.pointers.get(task) == op.record_id,
                    "archive_candidate_record_changed")
            # A saved attempt is not a source of fresh Task authority. Even
            # historical success must be bound to the original approved
            # Contract/branch/base and canonical current Record bytes.
            record_bytes = prior_c.snapshot.files.get(
                codec.object_path("task-record", op.record_id))
            contract_bytes = prior_c.snapshot.files.get(
                codec.object_path("contract", self.registered.contract_id))
            require(record_bytes is not None and contract_bytes is not None,
                    "archive_saved_record_contract_missing")
            saved_record = decode_record(
                record_bytes, object_id=op.record_id,
                repository=self.identity.repository, task=task,
                base_revision=self.registered.base_revision,
                branch_ref=self.registered.branch_ref)
            saved_contract = decode_contract(
                contract_bytes, object_id=self.registered.contract_id,
                repository=self.identity.repository, task=task,
                base_revision=self.registered.base_revision)
            require(saved_record["payload"]["contract_id"] == self.registered.contract_id
                    and saved_contract["payload"]["title"] == self.approved.title
                    and saved_contract["payload"]["body"] == self.approved.body,
                    "archive_saved_contract_identity_mismatch")
            issue_tip = v.observe(self.observer, issue_ref(task), absent=True)
            require(issue_tip == op.expected_issue,
                    "archive_source_issue_changed_or_deleted")
            v.check()
            require(v.observe(self.observer, ARCHIVE_REF) == archive_tip
                    and v.observe(self.observer, issue_ref(task), absent=True) == issue_tip,
                    "archive_refs_moved_during_confirm")
        except ArchiveUncertain:
            raise
        except (ArchiveRefused, InvalidMetadata, TaskRecordError,
                ValueError, KeyError, TypeError) as error:
            raise ArchiveUncertain("archive_original_candidate_not_confirmed") from error
        return ArchiveOutcome(state, candidate, op.expected_issue, op.record_id)

    def archive(self, op: ArchiveOperation) -> ArchiveOutcome:
        require(type(op) is ArchiveOperation,
                "archive_operation_required")
        self._attach()
        task = str(self.registered.issue)
        key = _key(self.identity.repository_id, self.registered.issue,
                   op.expected_archive, op.expected_issue)
        saved = self._saved(op, key)
        if saved is not None:
            # After unknown ACK only ORIGINAL candidate and Host-backed truth.
            # No new Merge nonce, authorize(), terminal callback or writer.
            self._attach()
            return self._confirm(op, saved["candidate"], state="RECOVERED")

        # Once journal creation may have started, even failure while releasing
        # a quiescence context is UNKNOWN, not a safe ordinary refusal.
        # An ill-behaved __exit__ may suppress an exception entirely; detect
        # fallthrough after the with statement instead of returning None.
        effect_phase_entered = False
        try:
            held = self.hold_quiescence(op)
            with held as lock:
                require(type(lock) is ArchiveQuiescenceLease,
                        "archive_quiescence_lease_required")
                lock.check(op)
                first = self._current(op)
                self._prior_cleared(op)
                facts = self._facts(op, first)
                self._attach()
                lock.check(op)

                def terminal(update: Update, deadline: float) -> bool:
                    require(time.monotonic() < deadline,
                            "archive_deadline_expired_before_grant")
                    require(update.repository == self.identity.repository
                            and update.genesis == self.repository_identity.genesis
                            and update.task == task
                            and update.ref == ARCHIVE_REF
                            and update.expected_archive == op.expected_archive
                            and update.expected_issue == op.expected_issue
                            and update.retention_policy == op.retention_policy,
                            "archive_candidate_binding_mismatch")
                    lock.check(op)
                    self._attach()
                    require(self._current(op) == first
                            and self._facts(op, first) == facts,
                            "archive_terminal_facts_changed")
                    approval = ArchiveApproval(self.identity, self.registered,
                                               self.approved, op, facts, update.candidate)
                    try:
                        permitted = self.authorize(approval)
                    except Exception as error:
                        raise ArchiveRefused("archive_human_grant_unavailable") from error
                    require(permitted is True, "archive_human_grant_denied")
                    lock.check(op)
                    self._attach()
                    require(self._current(op) == first
                            and self._facts(op, first) == facts,
                            "archive_terminal_changed_during_grant")
                    require(time.monotonic() < deadline,
                            "archive_deadline_expired_after_grant")
                    return True

                def write(update: Update, bundle: Bundle, deadline: float) -> bool:
                    nonlocal effect_phase_entered
                    # Hold exact same Host lease, current record, Product state
                    # and terminal/Human facts immediately before Archive CAS.
                    require(update.repository == self.identity.repository
                            and update.genesis == self.repository_identity.genesis
                            and update.task == task
                            and update.ref == ARCHIVE_REF
                            and update.expected_archive == op.expected_archive
                            and update.expected_issue == op.expected_issue
                            and update.candidate == bundle.commit
                            and update.retention_policy == op.retention_policy,
                            "archive_writer_identity_mismatch")
                    lock.check(op)
                    self._attach()
                    self._prior_cleared(op)
                    require(self._current(op) == first
                            and self._facts(op, first) == facts,
                            "archive_terminal_changed_at_effect")
                    approval = ArchiveApproval(self.identity, self.registered,
                                               self.approved, op, facts, bundle.commit)
                    try:
                        permitted = self.authorize(approval)
                    except Exception as error:
                        raise ArchiveRefused("archive_human_grant_unavailable") from error
                    require(permitted is True, "archive_human_grant_denied")
                    require(time.monotonic() < deadline,
                            "archive_deadline_expired_before_intent")
                    # Journal create-once / fsync MUST precede external writer.
                    # Set uncertainty before attempting journal persistence:
                    # the call could have durably written then lost its ACK.
                    effect_phase_entered = True
                    self.journal.create(key, self._record(op, facts, bundle.commit, bundle))
                    if time.monotonic() >= deadline:
                        raise ArchiveUncertain("archive_deadline_after_intent")
                    lock.check(op)
                    self._attach()
                    require(self._current(op) == first
                            and self._facts(op, first) == facts,
                            "archive_terminal_changed_after_intent")
                    if time.monotonic() >= deadline:
                        raise ArchiveUncertain("archive_deadline_after_intent")
                    return self.writer(update, bundle, deadline)

                store = MetadataV2(self.identity.repository,
                                   self.repository_identity.genesis,
                                   self.reader, self.observer, limits=self.limits)
                try:
                    candidate = archive_issue(
                        store, task, retention_policy=op.retention_policy,
                        verify_terminal=terminal, archive_writer=write)
                except UncertainPublication as error:
                    try:
                        journaled = self._saved(op, key)
                    except Exception as cause:
                        raise ArchiveUncertain("archive_journal_reconciliation_required") from cause
                    if journaled is None:
                        raise ArchiveRefused("archive_refused_before_effect") from error
                    raise ArchiveUncertain("archive_ack_unknown") from error
                except InvalidMetadata as error:
                    raise ArchiveRefused("archive_core_refused") from error
                # An effect may already have happened. Refuse to report a
                # definitive failure if the Host lost its lock/grant or exact
                # ref readback after a positive writer ACK.
                try:
                    lock.check(op)
                    self._attach()
                    require(self._facts(op, first) == facts,
                            "archive_terminal_changed_after_write")
                    result = self._confirm(
                        op, candidate, state="PUBLISHED", facts=facts)
                    # Readback can itself run arbitrarily long. The original
                    # live quiescence lease and Attach must still be held
                    # after it finishes, not merely when readback starts.
                    lock.check(op)
                    self._attach()
                    require(self._facts(op, first) == facts,
                            "archive_terminal_changed_during_readback")
                    return result
                except (ArchiveRefused, ArchiveUncertain) as error:
                    raise ArchiveUncertain("archive_post_effect_confirmation_unknown") from error
            # Only reachable if the Host context suppressed an exception in
            # the body. Never return None as a fake successful Archive.
            if effect_phase_entered:
                raise ArchiveUncertain("archive_context_suppressed_after_intent")
            raise ArchiveRefused("archive_context_suppressed_before_intent")
        except ArchiveUncertain:
            raise
        except ArchiveRefused as error:
            if effect_phase_entered:
                raise ArchiveUncertain("archive_context_refusal_after_intent") from error
            raise
        except Exception as error:
            if effect_phase_entered:
                raise ArchiveUncertain("archive_context_exit_unknown_after_intent") from error
            # Missing/unavailable Host context/lock is never a permit.
            raise ArchiveRefused("archive_host_quiescence_unavailable") from error
