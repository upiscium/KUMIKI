"""#337 Phase 2B1: source-only opt-in Metadata v2 Host bootstrap.

This is not a Git or GitHub transport. The trusted Host provides authentic
repository/Attach/Issue grants, exact direct-ref observations, bounded object
reads, externally serialized create-if-absent ref effects and a private
create-once recovery journal. No existing v1 runtime is changed.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
sys.path.insert(0, str(V4))

from metadata_v2 import (
    ARCHIVE_REF, Bundle, InvalidMetadata, Limits, MetadataV2, ObjectReader,
    Observer, UncertainPublication, Update, Validation, construct, issue_ref, oid,
)
from task_record import encode_contract, encode_record, validate_branch_ref
from kagari_vnext_trusted_host import RecoveryJournal

SCHEMA = 1
_VERSION = 2
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SUBJECT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)
_NONCE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_MAX_ID = (1 << 63) - 1
_DOMAIN = b"kagari-metadata-v2-bootstrap/v1\n"


class BootstrapBlocked(ValueError):
    """Denied, invalid or foreign state. No mutation may be inferred."""


class BootstrapUncertain(BootstrapBlocked):
    """An effect may have happened; preserve intent, never retry blindly."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise BootstrapBlocked(reason)


def _positive(value: object) -> bool:
    return type(value) is int and 1 <= value <= _MAX_ID


def _digest(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _subject(value: object) -> bool:
    return type(value) is str and _SUBJECT.fullmatch(value) is not None


def _key(*parts: object) -> str:
    data = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(_DOMAIN + data).hexdigest()


def _archive_record(identity: BootstrapIdentity, nonce: str, candidate: str) -> dict:
    return {
        "schema": SCHEMA, "operation": "genesis-create", "version": _VERSION,
        "repository_id": identity.repository_id, "repository": identity.repository,
        "attach_digest": identity.attach_digest, "nonce": nonce, "genesis": candidate,
    }


@dataclass(frozen=True, slots=True)
class BootstrapIdentity:
    """Attested by the external Host, not by accepting caller-supplied JSON."""

    repository_id: int
    repository: str
    attach_digest: str
    git_object_format: str = "sha1"

    def __post_init__(self) -> None:
        require(_positive(self.repository_id), "repository_id_invalid")
        require(type(self.repository) is str and len(self.repository) <= 512
                and _REPO.fullmatch(self.repository) is not None,
                "repository_name_invalid")
        require(_digest(self.attach_digest), "attach_receipt_digest_invalid")
        require(type(self.git_object_format) is str and
                self.git_object_format == "sha1", "git_format_unsupported")


@dataclass(frozen=True, slots=True)
class ApprovedIssue:
    """Exact approved Issue/Task snapshot; its origin must be checked by Host."""

    repository_id: int
    repository: str
    issue: int
    branch_ref: str
    base_revision: str
    title: str
    body: str

    def __post_init__(self) -> None:
        require(_positive(self.repository_id) and type(self.issue) is int
                and 1 <= self.issue <= 10**9, "issue_identity_invalid")
        require(type(self.repository) is str and len(self.repository) <= 512
                and _REPO.fullmatch(self.repository) is not None,
                "issue_repository_invalid")
        try:
            validate_branch_ref(self.branch_ref)
        except ValueError:
            raise BootstrapBlocked("issue_branch_invalid") from None
        require(re.search(r"(^|[^0-9])" + str(self.issue) + r"([^0-9]|$)",
                          self.branch_ref) is not None, "issue_branch_number_mismatch")
        require(_subject(self.base_revision), "issue_base_revision_invalid")
        require(type(self.title) is str and bool(self.title.strip())
                and type(self.body) is str,
                "issue_requirements_invalid")
        try:
            title_bytes = self.title.encode("utf-8", errors="strict")
            body_bytes = self.body.encode("utf-8", errors="strict")
        except UnicodeError:
            raise BootstrapBlocked("issue_requirements_unicode_invalid") from None
        require(len(title_bytes) <= 64_000 and len(body_bytes) <= 160_000,
                "issue_requirements_size_invalid")


@dataclass(frozen=True, slots=True)
class ArchiveCreate:
    """Host-only, expected-absent archive effect boundary."""

    identity: BootstrapIdentity
    ref: str
    expected_old: None
    candidate: str
    intent_key: str


@dataclass(frozen=True, slots=True)
class IssueCreate:
    """Host-only expected-absent first lifecycle with exact Task authority."""

    identity: BootstrapIdentity
    approved: ApprovedIssue
    genesis: str
    archive_tip: str
    record_id: str
    contract_id: str


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    state: str
    genesis: str | None
    archive_tip: str | None


@dataclass(frozen=True, slots=True)
class IssueResult:
    state: str
    genesis: str
    issue_commit: str
    record_id: str
    contract_id: str


class Bootstrap:
    """One v2-only Host lane. Doctor never authorizes or writes.

    Effect callbacks are supplied by a trusted Host; this class cannot
    authenticate repository IDs or enforce remote multi-ref CAS/Rulesets.
    The #140 journal records the original candidate BEFORE a Host effect.
    """

    def __init__(
        self, identity: BootstrapIdentity, *, reader: ObjectReader,
        observer: Observer, journal: RecoveryJournal,
        authorize_install: Callable[[ArchiveCreate], bool] | None = None,
        create_archive: Callable[[ArchiveCreate, Bundle, float], bool] | None = None,
        authorize_issue: Callable[[IssueCreate], bool] | None = None,
        create_issue: Callable[[Update, Bundle, float], bool] | None = None,
        limits: Limits = Limits(),
    ) -> None:
        require(type(identity) is BootstrapIdentity and
                type(journal) is RecoveryJournal and type(limits) is Limits and
                callable(reader) and callable(observer),
                "trusted_host_bootstrap_capabilities_required")
        self.identity = identity
        self.reader = reader
        self.observer = observer
        self.journal = journal
        self.authorize_install = authorize_install
        self.create_archive = create_archive
        self.authorize_issue = authorize_issue
        self.create_issue = create_issue
        self.limits = limits

    def _archive_key(self) -> str:
        # Repository ID intentionally keys any prior owner/name/Attach state;
        # identity drift must collide and block, not allocate a new receipt.
        return _key("archive", self.identity.repository_id)

    def _issue_key(self, issue: int) -> str:
        return _key("issue", self.identity.repository_id, issue)

    def _read_archive_record(self) -> tuple[dict, Bundle] | None:
        record = self.journal.read(self._archive_key())
        if record is None:
            return None
        require(type(record) is dict and record.keys() ==
                _archive_record(self.identity, "0" * 32, "0" * 40).keys(),
                "archive_receipt_schema_invalid")
        require(type(record["nonce"]) is str and _NONCE.fullmatch(record["nonce"])
                is not None, "archive_receipt_nonce_invalid")
        try:
            candidate = oid(record["genesis"])
            bundle = construct({}, (), nonce=record["nonce"])
        except (InvalidMetadata, ValueError, TypeError):
            raise BootstrapBlocked("archive_receipt_genesis_invalid") from None
        require(record == _archive_record(self.identity, record["nonce"], candidate)
                and bundle.commit == candidate, "archive_receipt_identity_mismatch")
        return record, bundle

    def _observe(self, validator: Validation, ref: str, *, absent: bool = False) -> str | None:
        try:
            return validator.observe(self.observer, ref, absent=absent)
        except InvalidMetadata:
            raise BootstrapBlocked("exact_direct_ref_required") from None

    def doctor(self) -> ArchiveResult:
        """No writes. Foreign archive, uncertain intent and malformed refs block."""
        receipt = self._read_archive_record()
        if receipt is None:
            probe = Validation(self.identity.repository, "0" * 40,
                               self.reader, self.limits)
            tip = self._observe(probe, ARCHIVE_REF, absent=True)
            require(tip is None, "unowned_archive_ref_present")
            return ArchiveResult("ABSENT", None, None)
        record, _bundle = receipt
        genesis = record["genesis"]
        validator = Validation(self.identity.repository, genesis, self.reader,
                               self.limits)
        observed = self._observe(validator, ARCHIVE_REF, absent=True)
        if observed is None:
            raise BootstrapUncertain("archive_intent_exists_but_ref_absent")
        try:
            validator.archive(observed)
            validator.check()
        except InvalidMetadata:
            raise BootstrapBlocked("archive_history_invalid") from None
        require(self._observe(validator, ARCHIVE_REF) == observed,
                "archive_changed_during_observation")
        return ArchiveResult("READY", genesis, observed)

    def install(self) -> ArchiveResult:
        """Explicit opt-in effect; never creates an archive on Doctor/preview."""
        require(callable(self.authorize_install) and callable(self.create_archive),
                "archive_install_capabilities_absent")
        prior = self._read_archive_record()
        if prior is not None:
            return self.doctor()  # No second candidate, no implicit retry.
        probe = Validation(self.identity.repository, "0" * 40,
                           self.reader, self.limits)
        require(self._observe(probe, ARCHIVE_REF, absent=True) is None,
                "unowned_archive_ref_present")
        nonce = secrets.token_hex(16)
        bundle = construct({}, (), nonce=nonce)
        intent = ArchiveCreate(self.identity, ARCHIVE_REF, None,
                               bundle.commit, self._archive_key())
        try:
            allowed = self.authorize_install(intent) is True
        except Exception:
            raise BootstrapBlocked("archive_install_authority_unavailable") from None
        require(allowed, "archive_install_authority_denied")
        try:
            probe.check()
        except InvalidMetadata:
            raise BootstrapBlocked("archive_install_preflight_expired") from None
        # Exclusive, fsync'ed create-once receipt BEFORE an external effect.
        # A second contender loses and cannot overwrite the original nonce.
        self.journal.create(intent.intent_key, _archive_record(
            self.identity, nonce, bundle.commit))
        try:
            ack = self.create_archive(intent, bundle, probe.deadline)
        except Exception as error:
            raise BootstrapUncertain("archive_creation_ack_unknown") from error
        if ack is not True:
            raise BootstrapUncertain("archive_creation_ack_unknown")
        # The Host effect deadline is shared with the original preflight;
        # a slow/late ACK must not be converted to a positive result.
        try:
            probe.check()
        except InvalidMetadata as error:
            raise BootstrapUncertain("archive_creation_deadline_unknown") from error
        try:
            result = self.doctor()
        except BootstrapBlocked as error:
            raise BootstrapUncertain("archive_creation_readback_uncertain") from error
        require(result.genesis == bundle.commit, "archive_readback_genesis_mismatch")
        return ArchiveResult("CREATED", result.genesis, result.archive_tip)

    def _issue_record(self, request: IssueCreate, candidate: str) -> dict:
        return {
            "schema": SCHEMA, "operation": "issue-create", "version": _VERSION,
            "repository_id": self.identity.repository_id,
            "repository": self.identity.repository,
            "attach_digest": self.identity.attach_digest,
            "issue": request.approved.issue, "branch_ref": request.approved.branch_ref,
            "base_revision": request.approved.base_revision,
            "record_id": request.record_id, "contract_id": request.contract_id,
            "genesis": request.genesis, "archive_tip": request.archive_tip,
            "candidate": candidate,
        }

    def _read_issue_record(self, request: IssueCreate) -> dict | None:
        prior = self.journal.read(self._issue_key(request.approved.issue))
        if prior is None:
            return None
        require(type(prior) is dict and prior.keys() ==
                self._issue_record(request, "0" * 40).keys(),
                "issue_receipt_schema_invalid")
        try:
            oid(prior["candidate"])
        except (InvalidMetadata, TypeError):
            raise BootstrapBlocked("issue_receipt_candidate_invalid") from None
        # The archive may legitimately advance for *other* Issues after
        # this first-lifecycle registration. The originally observed archive
        # tip remains pinned in the create-once receipt and must be an
        # ancestor of the newly observed validated archive, not equal latest.
        try:
            original_archive = oid(prior["archive_tip"])
        except (InvalidMetadata, TypeError):
            raise BootstrapBlocked("issue_receipt_archive_tip_invalid") from None
        expected = self._issue_record(request, prior["candidate"])
        expected["archive_tip"] = original_archive
        require(prior == expected, "issue_receipt_identity_mismatch")
        return prior

    def _check_issue(self, request: IssueCreate, candidate: str,
                     *, original_archive_tip: str | None = None) -> IssueResult:
        """Host-backed, pinned readback; no reliance on a success ACK."""
        store = MetadataV2(self.identity.repository, request.genesis,
                           self.reader, self.observer, limits=self.limits)
        v = store.validation()
        try:
            archive = v.observe(self.observer, ARCHIVE_REF)
            aggregate = v.archive(archive)
            if original_archive_tip is not None:
                require(original_archive_tip in v.nodes and
                        v.nodes[original_archive_tip].role in {"genesis", "archive"},
                        "issue_receipt_archive_ancestry_mismatch")
            if str(request.approved.issue) in aggregate.snapshot.tasks:
                raise BootstrapBlocked("issue_already_archived")
            observed = v.observe(self.observer,
                                 issue_ref(str(request.approved.issue)), absent=True)
            if observed != candidate:
                raise BootstrapUncertain("issue_ref_absent_or_changed")
            node = v.issue(observed, str(request.approved.issue))
            require(node.parents == (request.genesis,),
                    "initial_issue_parent_not_genesis")
            task = str(request.approved.issue)
            require(node.snapshot.pointers.get(task) == request.record_id,
                    "issue_record_pointer_mismatch")
            record = node.snapshot.objects.get(request.record_id)
            contract = node.snapshot.objects.get(request.contract_id)
            require(record is not None and contract is not None,
                    "issue_record_contract_missing")
            require((record["kind"], record["repository"], record["task"],
                     record["subject"]) ==
                    ("task-record", self.identity.repository, task,
                     request.approved.base_revision), "issue_record_subject_mismatch")
            require((contract["kind"], contract["repository"], contract["task"],
                     contract["subject"]) ==
                    ("contract", self.identity.repository, task,
                     request.approved.base_revision), "issue_contract_subject_mismatch")
            require(record["payload"]["branch_ref"] == request.approved.branch_ref
                    and record["payload"]["contract_id"] == request.contract_id,
                    "issue_record_contract_link_mismatch")
            require(contract["payload"]["title"] == request.approved.title
                    and contract["payload"]["body"] == request.approved.body,
                    "issue_contract_bytes_changed")
            v.check()
            require(v.observe(self.observer, ARCHIVE_REF) == archive and
                    v.observe(self.observer, issue_ref(task), absent=True) == observed,
                    "issue_ref_changed_during_readback")
        except BootstrapBlocked:
            raise
        except (InvalidMetadata, KeyError, TypeError, ValueError) as error:
            raise BootstrapBlocked("issue_history_readback_invalid") from error
        return IssueResult("RESUMED", request.genesis, candidate,
                           request.record_id, request.contract_id)

    def register(self, approved: ApprovedIssue) -> IssueResult:
        """One authorized first-lifecycle Task Record + Contract publication.

        This DOES NOT create or attach a Product Worktree. Existing v1
        IssueWorktreeAllocator and Main do not consume this v2 object.
        """
        require(type(approved) is ApprovedIssue and
                (approved.repository_id, approved.repository) ==
                (self.identity.repository_id, self.identity.repository),
                "issue_repository_identity_mismatch")
        archive = self.doctor()
        require(archive.state == "READY" and archive.genesis is not None and
                archive.archive_tip is not None, "archive_install_required")
        require(callable(self.authorize_issue) and callable(self.create_issue),
                "issue_registration_capabilities_absent")
        task = str(approved.issue)
        contract_id, contract = encode_contract(self.identity.repository, task,
            approved.base_revision,
            {"schema_version": 1, "title": approved.title, "body": approved.body})
        record_id, record = encode_record(self.identity.repository, task,
            approved.base_revision,
            {"schema_version": 1, "branch_ref": approved.branch_ref,
             "contract_id": contract_id})
        request = IssueCreate(self.identity, approved, archive.genesis,
                              archive.archive_tip, record_id, contract_id)
        existing = self._read_issue_record(request)
        if existing is not None:
            # Exact readback only; unknown state NEVER causes replay.
            return self._check_issue(
                request, existing["candidate"],
                original_archive_tip=existing["archive_tip"])
        store = MetadataV2(self.identity.repository, archive.genesis,
                           self.reader, self.observer, limits=self.limits)
        v = store.validation()
        archive_tip = v.observe(self.observer, ARCHIVE_REF)
        v.archive(archive_tip)
        require(archive_tip == archive.archive_tip,
                "archive_changed_during_registration_preflight")
        require(v.observe(self.observer, issue_ref(task), absent=True) is None,
                "unowned_or_existing_issue_ref")
        try:
            authorized = self.authorize_issue(request) is True
        except Exception:
            raise BootstrapBlocked("issue_registration_authority_unavailable") from None
        require(authorized, "issue_registration_authority_denied")

        def publish(update: Update, bundle: Bundle, deadline: float) -> bool:
            require(update.repository == self.identity.repository and
                    update.genesis == archive.genesis and update.task == task and
                    update.ref == issue_ref(task) and
                    update.expected_archive == archive.archive_tip and
                    update.expected_issue is None and
                    update.candidate == bundle.commit,
                    "issue_create_intent_changed")
            self.journal.create(self._issue_key(approved.issue),
                                self._issue_record(request, bundle.commit))
            return self.create_issue(update, bundle, deadline)

        # The trusted, journal-first callback is supplied only for this
        # explicitly approved first-lifecycle publication. Preliminary reads
        # above used a separate non-writing MetadataV2 instance.
        publisher = MetadataV2(self.identity.repository, archive.genesis,
                               self.reader, self.observer, issue_writer=publish,
                               limits=self.limits)
        try:
            candidate = publisher.publish_issue(
                task, (contract, record), record_update=(None, record_id))
        except UncertainPublication as error:
            raise BootstrapUncertain("issue_creation_ack_unknown") from error
        except InvalidMetadata as error:
            raise BootstrapBlocked("issue_creation_refused") from error
        try:
            completed = self._check_issue(request, candidate)
        except BootstrapBlocked as error:
            raise BootstrapUncertain("issue_creation_readback_uncertain") from error
        return IssueResult("CREATED", completed.genesis, completed.issue_commit,
                           completed.record_id, completed.contract_id)
