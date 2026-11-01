"""#337 Phase 2A: explicit read-only Metadata v2 Host binding (source only).

The trusted Host supplies repository identity, Issue/Contract registration,
bounded object reads, and exact-direct ref observations. This adapter does
not authenticate those callbacks, acquire credentials, run Git, or write refs.
It does NOT change any v1 MetadataStore consumer or install a v2 profile.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re
import sys
from pathlib import Path

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
sys.path.insert(0, str(V4))
import metadata_codec as codec
from metadata_v2 import (
    ARCHIVE_REF, Limits, MetadataV2, ObjectReader, Observer, issue_ref, oid,
)
from task_record import validate_branch_ref

VERSION = 2
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SUBJECT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.ASCII)
_MAX_ID = (1 << 63) - 1


class MetadataV2HostRefused(ValueError):
    """Invalid binding, ref history or exact checkpoint; never a write permit."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise MetadataV2HostRefused(reason)


def _positive(value: object) -> bool:
    return type(value) is int and 1 <= value <= _MAX_ID


def _digest(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _subject(value: object) -> bool:
    return type(value) is str and _SUBJECT.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    """Host-attested GitHub numeric repository ID + canonical name and genesis.

    An integer/name provided by arbitrary untrusted JSON is NOT authenticated
    by this type. Only the trusted Host may establish the identity and callbacks.
    """

    repository_id: int
    repository: str
    genesis: str
    git_object_format: str

    def __post_init__(self) -> None:
        _require(_positive(self.repository_id), "repository_id_invalid")
        _require(type(self.repository) is str
                 and len(self.repository) <= codec.MAX_REPOSITORY_LENGTH
                 and _REPOSITORY.fullmatch(self.repository) is not None,
                 "repository_name_invalid")
        _require(type(self.git_object_format) is str
                 and self.git_object_format == "sha1", "git_object_format_unsupported")
        try:
            oid(self.genesis)
        except (TypeError, ValueError):
            raise MetadataV2HostRefused("genesis_oid_invalid") from None


@dataclass(frozen=True, slots=True)
class RegisteredIssue:
    """A trusted #286/#191 exact binding; no Worktree or GitHub lookup here."""

    repository_id: int
    repository: str
    issue: int
    branch_ref: str
    base_revision: str
    record_id: str
    contract_id: str

    def __post_init__(self) -> None:
        _require(_positive(self.repository_id) and _positive(self.issue),
                 "registered_identity_invalid")
        _require(type(self.repository) is str and _REPOSITORY.fullmatch(self.repository)
                 is not None and len(self.repository) <= codec.MAX_REPOSITORY_LENGTH,
                 "registered_repository_invalid")
        try:
            validate_branch_ref(self.branch_ref)
        except ValueError:
            raise MetadataV2HostRefused("registered_branch_invalid") from None
        _require(_subject(self.base_revision) and _digest(self.record_id)
                 and _digest(self.contract_id), "registered_record_contract_invalid")


@dataclass(frozen=True, slots=True)
class ExactLocator:
    """Versioned checkpoint locator; never interprets latest ref as a commit."""

    version: int
    repository_id: int
    repository: str
    issue: int
    metadata_commit: str
    kind: str
    object_id: str
    subject: str

    def __post_init__(self) -> None:
        _require(type(self.version) is int and self.version == VERSION,
                 "metadata_locator_version_unsupported")
        _require(_positive(self.repository_id) and _positive(self.issue),
                 "metadata_locator_identity_invalid")
        _require(type(self.repository) is str and _REPOSITORY.fullmatch(self.repository)
                 is not None and len(self.repository) <= codec.MAX_REPOSITORY_LENGTH,
                 "metadata_locator_repository_invalid")
        try:
            oid(self.metadata_commit)
            codec.object_path(self.kind, self.object_id)
        except (ValueError, TypeError):
            raise MetadataV2HostRefused("metadata_locator_object_invalid") from None
        _require(_subject(self.subject), "metadata_locator_subject_invalid")

    @classmethod
    def from_mapping(cls, value: object) -> ExactLocator:
        """Parse a closed explicit locator, rejecting absent/extra fields."""
        fields = frozenset(cls.__dataclass_fields__)
        _require(type(value) is dict and value.keys() == fields,
                 "metadata_locator_fields_invalid")
        return cls(**value)


class ReadOnlyMetadataV2Host:
    """Host-side v2 reader; no credentials, writer callbacks or side effects.

    The Host must bind *both* callbacks to the same authenticated repository,
    enforce their byte/time limits, and prove direct-ref identity. This Python
    class cannot prove a GitHub Ruleset or an atomic remote observation.
    """

    def __init__(
        self, identity: RepositoryIdentity, registered: RegisteredIssue,
        *, reader: ObjectReader, observer: Observer, limits: Limits = Limits(),
    ) -> None:
        _require(type(identity) is RepositoryIdentity
                 and type(registered) is RegisteredIssue
                 and type(limits) is Limits, "metadata_host_binding_type_invalid")
        _require((identity.repository_id, identity.repository)
                 == (registered.repository_id, registered.repository),
                 "metadata_host_repository_mismatch")
        _require(callable(reader) and callable(observer),
                 "metadata_host_read_capabilities_required")
        self._identity = identity
        self._registered = registered
        # No issue_writer; no archive writer, no install/ref mutation API.
        self._store = MetadataV2(identity.repository, identity.genesis,
                                 reader, observer, limits=limits)
        self._observer = observer

    def read(self, locator: ExactLocator) -> dict[str, Any]:
        """Resolve one exact historical object and verify same-commit authority.

        Both current refs are only reachability roots. The returned envelope is
        pinned to locator.metadata_commit and cannot inherit newer records.
        """
        _require(type(locator) is ExactLocator, "metadata_locator_type_invalid")
        registered = self._registered
        _require((locator.repository_id, locator.repository, locator.issue)
                 == (self._identity.repository_id, self._identity.repository,
                     registered.issue), "metadata_locator_identity_mismatch")
        task = str(registered.issue)
        path = codec.object_path(locator.kind, locator.object_id)
        validator = self._store.validation()
        try:
            aggregate = validator.observe(self._observer, ARCHIVE_REF)
            validator.archive(aggregate)
            active = validator.observe(self._observer, issue_ref(task), absent=True)
            if active is not None:
                validator.issue(active, task)
            visited: set[str] = set()
            pending = [aggregate] + ([active] if active else [])
            while pending:
                validator.check()
                commit = pending.pop()
                if commit in visited:
                    continue
                visited.add(commit)
                pending.extend(validator.nodes[commit].parents)
            _require(locator.metadata_commit in visited,
                     "metadata_locator_commit_unreachable")
            snapshot = validator.nodes[locator.metadata_commit].snapshot
            _require(snapshot.pointers.get(task) == registered.record_id,
                     "metadata_record_checkpoint_mismatch")
            record = snapshot.objects.get(registered.record_id)
            contract = snapshot.objects.get(registered.contract_id)
            _require(record is not None and contract is not None,
                     "metadata_record_contract_missing")
            _require((record["kind"], record["repository"], record["task"],
                      record["subject"]) ==
                     ("task-record", registered.repository, task,
                      registered.base_revision), "metadata_record_identity_mismatch")
            _require((contract["kind"], contract["repository"], contract["task"],
                      contract["subject"]) ==
                     ("contract", registered.repository, task,
                      registered.base_revision), "metadata_contract_identity_mismatch")
            _require(record["payload"]["branch_ref"] == registered.branch_ref
                     and record["payload"]["contract_id"] == registered.contract_id,
                     "metadata_record_contract_binding_mismatch")
            data = snapshot.files.get(path)
            _require(data is not None, "metadata_locator_object_missing")
            result = codec.decode_object(
                data, expected_id=locator.object_id,
                expected_repository=self._identity.repository,
                expected_task=task, expected_subject=locator.subject,
                expected_kind=locator.kind,
            )
            validator.check()
            _require(validator.observe(self._observer, ARCHIVE_REF) == aggregate
                     and validator.observe(self._observer, issue_ref(task), absent=True)
                     == active, "metadata_refs_changed_during_read")
            return result
        except MetadataV2HostRefused:
            raise
        except (ValueError, TypeError, KeyError, IndexError) as error:
            raise MetadataV2HostRefused("metadata_exact_read_refused") from error
