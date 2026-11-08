"""#337 P3C/B0: source-only readback of archived Issue cleanup provenance.

This module has NO deletion/writer callback and grants NO cleanup authority.
It compares the original #140 Archive journal with Host-backed immutable
Metadata history. Current Task terminality, quiescence, approval, ref lease
and durable delete/recovery effects remain separate trusted Host boundaries.
"""
from __future__ import annotations


from dataclasses import dataclass
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "components/agent-core-v4"))

from kagari_metadata_v2_archive_host import ArchiveOperation, ArchiveOutcome, TerminalFacts, _key
from kagari_metadata_v2_bootstrap import ApprovedIssue, BootstrapIdentity
from kagari_metadata_v2_host_binding import RegisteredIssue, RepositoryIdentity
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import (
    ARCHIVE_REF, InvalidMetadata, Limits, ObjectReader, Observer,
    Validation, issue_ref, union,
)
from task_record import TaskRecordError, decode_contract, decode_record
import metadata_codec as codec


_DIGEST64 = re.compile(r"[0-9a-f]{64}\Z")
_OID40 = re.compile(r"[0-9a-f]{40}\Z")


class CleanupHistoryRefused(ValueError):
    """Missing or ambiguous source-owned Archive/Issue cleanup history."""


def need(ok: bool, detail: str) -> None:
    if not ok:
        raise CleanupHistoryRefused(detail)


def _check_original_terminal_semantics(facts: dict, disposition: dict | None,
                                       *, repository_id: int, repository: str,
                                       issue: int) -> None:
    """Validate *historical* ArchiveHost admission shape, never current grant.

    Facts are journal provenance and cannot prove present Task terminality.
    They must nonetheless be a state ArchiveHost._facts could have accepted
    at the original J publication, consistent with C's immutable disposition.
    """
    need(facts["product_base_ref"] == "refs/heads/main"
         and _OID40.fullmatch(facts["product_head_oid"]) is not None
         and _OID40.fullmatch(facts["product_base_oid"]) is not None
         and facts["product_history_preserved"] is True
         and facts["no_unpublished_product_work"] is True
         and facts["session_stopped"] is True
         and facts["metadata_writers_quiescent"] is True,
         "cleanup_archive_terminal_semantics_invalid")
    if disposition is None:
        need(facts["route"] == "MERGED_PR"
             and facts["product_ancestry_proven"] is True
             and type(facts["pr_number"]) is int and facts["pr_number"] > 0
             and facts["pr_state"] == "MERGED"
             and type(facts["pr_repository_id"]) is int
             and facts["pr_repository_id"] == repository_id
             and facts["pr_repository"] == repository
             and type(facts["pr_issue"]) is int and facts["pr_issue"] == issue
             and facts["pr_head_oid"] == facts["product_head_oid"]
             and facts["pr_base_ref"] == facts["product_base_ref"]
             and type(facts["pr_merge_oid"]) is str
             and _OID40.fullmatch(facts["pr_merge_oid"]) is not None
             and facts["replacement_repository"] is None
             and facts["replacement_task"] is None,
             "cleanup_archive_terminal_semantics_invalid")
    else:
        kind = disposition["kind"]  # Already validated by decode_record.
        need(facts["route"] == kind.upper()
             and all(facts[field] is None for field in (
                 "pr_number", "pr_state", "pr_head_oid", "pr_merge_oid",
                 "pr_base_ref", "pr_repository_id", "pr_repository",
                 "pr_issue")),
             "cleanup_archive_terminal_semantics_invalid")
        if kind == "superseded":
            replacement = disposition["replacement"]
            need((facts["replacement_repository"], facts["replacement_task"])
                 == (replacement["repository"], replacement["task"]),
                 "cleanup_archive_terminal_semantics_invalid")
        else:
            need(kind == "cancelled"
                 and facts["replacement_repository"] is None
                 and facts["replacement_task"] is None,
                 "cleanup_archive_terminal_semantics_invalid")


@dataclass(frozen=True, slots=True)
class CleanupHistory:
    """Observation only; this is NOT a delete permit or live Task authority."""

    repository_id: int
    repository: str
    issue: int
    issue_ref: str
    issue_tip: str
    archive_candidate: str
    archive_current_tip: str
    record_id: str
    contract_id: str
    base_revision: str
    issue_ref_state: str  # PRESENT_EXACT / ALREADY_ABSENT


class CleanupHistoryReader:
    """Read-only bridge from original ArchiveHost intent to P3C operations.

    Trusted Host must bind these types, reader, observer and journal to the
    same authenticated repository. No callback in this module can mutate
    refs or infer current terminality from old durable Archive facts.
    """

    def __init__(
        self, identity: BootstrapIdentity, repository: RepositoryIdentity,
        registered: RegisteredIssue, approved: ApprovedIssue,
        *, journal: RecoveryJournal, reader: ObjectReader,
        observer: Observer, limits: Limits = Limits(),
    ) -> None:
        need(type(identity) is BootstrapIdentity
             and type(repository) is RepositoryIdentity
             and type(registered) is RegisteredIssue
             and type(approved) is ApprovedIssue
             and type(journal) is RecoveryJournal
             and type(limits) is Limits
             and callable(reader) and callable(observer),
             "cleanup_history_host_capabilities_required")
        need((identity.repository_id, identity.repository)
             == (repository.repository_id, repository.repository)
             == (registered.repository_id, registered.repository)
             == (approved.repository_id, approved.repository)
             and identity.git_object_format == repository.git_object_format == "sha1"
             and registered.issue == approved.issue
             and registered.branch_ref == approved.branch_ref
             and registered.base_revision == approved.base_revision,
             "cleanup_history_identity_mismatch")
        self.identity = identity
        self.repository = repository
        self.registered = registered
        self.approved = approved
        self.journal = journal
        self.reader = reader
        self.observer = observer
        self.limits = limits

    def inspect(self, operation: ArchiveOperation, outcome: ArchiveOutcome) -> CleanupHistory:
        """Qualify only *history*, including an already-absent Issue ref.

        Saved Archive J is pinned to the original A/C journal; the current
        Archive may be J or a later valid two-parent append containing J.
        Never substitute the latest Archive tip as the original J.
        """
        need(type(operation) is ArchiveOperation and type(outcome) is ArchiveOutcome
             and operation.retention_policy is None
             and outcome.state in {"PUBLISHED", "RECOVERED"}
             and outcome.issue_commit == operation.expected_issue
             and outcome.record_id == operation.record_id
             and operation.record_id == self.registered.record_id,
             "cleanup_history_archive_operation_mismatch")
        key = _key(self.identity.repository_id, self.registered.issue,
                   operation.expected_archive, operation.expected_issue)
        try:
            saved = self.journal.read(key)
        except Exception as error:
            raise CleanupHistoryRefused("cleanup_archive_journal_unavailable") from error
        fields = {
            "schema", "operation", "version", "repository_id", "repository",
            "genesis", "attach_digest", "issue", "operation_id",
            "archive_before", "issue_before", "record_id", "contract_id",
            "branch_ref", "base_revision", "candidate", "terminal_facts",
            "bundle_object_count", "bundle_sha256", "branch_mode",
        }
        need(type(saved) is dict and saved.keys() == fields
             and type(saved["schema"]) is int
             and type(saved["repository_id"]) is int
             and type(saved["issue"]) is int
             and type(saved["version"]) is int
             and type(saved["bundle_object_count"]) is int
             and 1 <= saved["bundle_object_count"] <= self.limits.entries
             and type(saved["bundle_sha256"]) is str
             and _DIGEST64.fullmatch(saved["bundle_sha256"]) is not None
             and type(saved["terminal_facts"]) is dict,
             "cleanup_archive_journal_invalid")
        need((saved["schema"], saved["operation"], saved["version"],
              saved["branch_mode"], saved["repository_id"], saved["repository"],
              saved["genesis"], saved["attach_digest"], saved["issue"],
              saved["operation_id"], saved["archive_before"], saved["issue_before"],
              saved["record_id"], saved["contract_id"], saved["branch_ref"],
              saved["base_revision"], saved["candidate"])
             == (1, "archive-issue", 3, "ordinary-git-branch",
                 self.identity.repository_id, self.identity.repository,
                 self.repository.genesis, self.identity.attach_digest,
                 self.registered.issue, operation.operation_id,
                 operation.expected_archive, operation.expected_issue,
                 self.registered.record_id, self.registered.contract_id,
                 self.registered.branch_ref, self.registered.base_revision,
                 outcome.archive_commit),
             "cleanup_original_archive_intent_mismatch")
        saved_facts = saved["terminal_facts"]
        # These bytes are provenance only, never a renewed Human grant. Still,
        # reject a partial or malformed historical TerminalFacts envelope.
        need(saved_facts.keys() == TerminalFacts.__dataclass_fields__.keys()
             and type(saved_facts["repository_id"]) is int
             and type(saved_facts["issue"]) is int
             and all(type(saved_facts[k]) is bool for k in (
                 "product_history_preserved", "product_ancestry_proven",
                 "no_unpublished_product_work", "session_stopped",
                 "metadata_writers_quiescent"))
             and all(type(saved_facts[k]) is str for k in (
                 "repository", "record_id", "contract_id", "branch_ref",
                 "base_revision", "route", "human_decision_id",
                 "product_head_oid", "product_base_oid", "product_base_ref"))
             and saved_facts["route"] in {"MERGED_PR", "CANCELLED", "SUPERSEDED"}
             and _DIGEST64.fullmatch(saved_facts["human_decision_id"]) is not None
             and all(v is None or (type(v) is int and v > 0)
                     for v in (saved_facts[k] for k in (
                         "pr_number", "pr_repository_id", "pr_issue")))
             and all(saved_facts[k] is None or type(saved_facts[k]) is str
                     for k in ("pr_state", "pr_head_oid", "pr_merge_oid",
                               "pr_base_ref", "pr_repository",
                               "replacement_repository", "replacement_task")),
             "cleanup_archive_terminal_snapshot_invalid")
        need((saved_facts.get("repository_id"), saved_facts.get("repository"),
              saved_facts.get("issue"), saved_facts.get("record_id"),
              saved_facts.get("contract_id"), saved_facts.get("branch_ref"),
              saved_facts.get("base_revision"))
             == (self.identity.repository_id, self.identity.repository,
                 self.registered.issue, self.registered.record_id,
                 self.registered.contract_id, self.registered.branch_ref,
                 self.registered.base_revision),
             "cleanup_archive_terminal_snapshot_not_original")
        task = str(self.registered.issue)
        ref = issue_ref(task)
        try:
            validator = Validation(self.identity.repository, self.repository.genesis,
                                   self.reader, self.limits)
            archive_tip = validator.observe(self.observer, ARCHIVE_REF)
            validator.archive(archive_tip)
            original = validator.nodes.get(outcome.archive_commit)
            source = validator.nodes.get(operation.expected_issue)
            prior = validator.nodes.get(operation.expected_archive)
            need(original is not None and original.role == "archive"
                 and original.parents == (operation.expected_archive,
                                          operation.expected_issue)
                 and prior is not None and prior.role in {"archive", "genesis"}
                 and source is not None and source.role == "issue"
                 and source.task == task
                 and task not in prior.snapshot.tasks,
                 "cleanup_archive_candidate_not_original")
            need(dict(original.snapshot.files) == union(prior.snapshot, source.snapshot)
                 and source.snapshot.pointers.get(task) == self.registered.record_id
                 and original.snapshot.pointers.get(task) == self.registered.record_id,
                 "cleanup_history_original_tree_or_pointer_changed")
            record_raw = source.snapshot.files.get(
                codec.object_path("task-record", self.registered.record_id))
            contract_raw = source.snapshot.files.get(
                codec.object_path("contract", self.registered.contract_id))
            need(record_raw is not None and contract_raw is not None,
                 "cleanup_original_record_contract_missing")
            record = decode_record(
                record_raw, object_id=self.registered.record_id,
                repository=self.identity.repository, task=task,
                base_revision=self.registered.base_revision,
                branch_ref=self.registered.branch_ref)
            contract = decode_contract(
                contract_raw, object_id=self.registered.contract_id,
                repository=self.identity.repository, task=task,
                base_revision=self.registered.base_revision)
            need(record["payload"]["contract_id"] == self.registered.contract_id
                 and contract["payload"]["title"] == self.approved.title
                 and contract["payload"]["body"] == self.approved.body,
                 "cleanup_original_requirements_changed")
            # Reconcile saved terminal facts with the original, immutable
            # Record disposition. Neither is current authorization to delete.
            _check_original_terminal_semantics(
                saved_facts, record["payload"].get("disposition"),
                repository_id=self.identity.repository_id,
                repository=self.identity.repository, issue=self.registered.issue)
            current = validator.observe(self.observer, ref, absent=True)
            need(current in {None, operation.expected_issue},
                 "cleanup_issue_ref_moved")
            validator.check()
            need(validator.observe(self.observer, ARCHIVE_REF) == archive_tip
                 and validator.observe(self.observer, ref, absent=True) == current,
                 "cleanup_refs_changed_during_read")
        except CleanupHistoryRefused:
            raise
        except (InvalidMetadata, TaskRecordError, TypeError, ValueError, KeyError) as error:
            raise CleanupHistoryRefused("cleanup_original_archive_history_unavailable") from error
        return CleanupHistory(
            self.identity.repository_id, self.identity.repository,
            self.registered.issue, ref, operation.expected_issue,
            outcome.archive_commit, archive_tip, self.registered.record_id,
            self.registered.contract_id, self.registered.base_revision,
            "PRESENT_EXACT" if current is not None else "ALREADY_ABSENT")
