"""#337 Phase 2D1: opt-in versioned #194 PR checkpoint and exact history reader.

No v1 codec/reader changes, no GitHub client, no credentials, no installed
capability, no metadata ref mutation, no Task/model activation. All GitHub and
Git callbacks must be supplied by a trusted Host and bounded at their source.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ContextManager, Protocol

from kagari_metadata_v2_host_binding import (
    ExactLocator, ReadOnlyMetadataV2Host, RegisteredIssue, RepositoryIdentity,
)
from metadata_v2 import Limits, ObjectReader, Observer
from task_record import validate_branch_ref

VERSION = 2
MARKER = "kagari-pr-checkpoint/v2"
_DOMAIN = b"kagari-pr-checkpoint/v2\n"
_MARKER = re.compile(r"<!-- kagari-pr-checkpoint/v2 sha256=([0-9a-f]{64}) -->\Z", re.ASCII)
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_BODY = 16 * 1024
_MAX_REPORT = 4096
_MAX_COMMENTS = 500
_MAX_ID = (1 << 63) - 1


class CheckpointRefused(ValueError):
    """Invalid or unverified checkpoint; never fall back to a later commit."""


class CheckpointUncertain(CheckpointRefused):
    """Comment write may have happened; recover by exact read, never repost."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CheckpointRefused(code)


def positive(value: object) -> bool:
    return type(value) is int and 0 < value <= _MAX_ID


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CheckpointRefused("checkpoint_duplicate_json_key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class V2PRCheckpoint:
    """Immutable closed PR capsule. The comment ID is *external*, not invented.

    Historical record and contract IDs are pinned to this original commit.
    A later current Task pointer is neither substituted nor authoritative.
    """

    version: int
    repository_id: int
    repository: str
    issue: int
    pr_number: int
    principal: int
    branch_ref: str
    base_ref: str
    subject: str
    metadata_commit: str
    record_id: str
    contract_id: str
    snapshot_id: str
    boundary: str
    key: str
    report: str

    def __post_init__(self) -> None:
        require(type(self.version) is int and self.version == VERSION,
                "checkpoint_version_unsupported")
        require(all(positive(v) for v in (self.repository_id, self.issue,
                                          self.pr_number, self.principal)),
                "checkpoint_identity_invalid")
        try:
            validate_branch_ref(self.branch_ref)
            validate_branch_ref(self.base_ref)
            ExactLocator(VERSION, self.repository_id, self.repository, self.issue,
                         self.metadata_commit, "task-view-snapshot",
                         self.snapshot_id, self.subject)
        except (ValueError, TypeError) as error:
            raise CheckpointRefused("checkpoint_locator_invalid") from error
        require(type(self.record_id) is str and _SHA256.fullmatch(self.record_id)
                is not None and type(self.contract_id) is str
                and _SHA256.fullmatch(self.contract_id) is not None,
                "checkpoint_record_contract_invalid")
        require(self.boundary in ("initial", "turn-end", "explicit-handoff")
                and type(self.boundary) is str, "checkpoint_boundary_invalid")
        require(type(self.key) is str and _KEY.fullmatch(self.key) is not None
                and (self.key == "initial") == (self.boundary == "initial"),
                "checkpoint_key_invalid")
        require(type(self.report) is str and bool(self.report)
                and len(self.report.encode("utf-8")) <= _MAX_REPORT
                and not any(ord(c) < 32 and c not in "\n\t" or ord(c) == 127
                            for c in self.report) and "\r" not in self.report
                and not self.report.endswith("\n"), "checkpoint_report_invalid")

    @classmethod
    def from_mapping(cls, value: object) -> V2PRCheckpoint:
        require(type(value) is dict
                and value.keys() == frozenset(cls.__dataclass_fields__),
                "checkpoint_schema_invalid")
        return cls(**value)

    def snapshot_locator(self) -> ExactLocator:
        return ExactLocator(VERSION, self.repository_id, self.repository,
                            self.issue, self.metadata_commit,
                            "task-view-snapshot", self.snapshot_id, self.subject)


def render_checkpoint(value: V2PRCheckpoint) -> str:
    """Deterministic v2-only #194 body; does not publish a comment."""
    require(type(value) is V2PRCheckpoint, "checkpoint_type_invalid")
    data = _canonical(dataclasses.asdict(value))
    digest = hashlib.sha256(_DOMAIN + data).hexdigest()
    capsule = base64.b64encode(data).decode("ascii")
    body = (f"<!-- {MARKER} sha256={digest} -->\n"
            f"Task checkpoint ({value.boundary}, v2):\n{value.report}\n"
            f"Capsule: {capsule}")
    require(len(body.encode("utf-8")) <= _MAX_BODY, "checkpoint_body_limit")
    return body


def decode_checkpoint(body: str) -> V2PRCheckpoint:
    """Reject v1, duplicate/noncanonical JSON, extra fields and body edits."""
    require(type(body) is str and len(body.encode("utf-8")) <= _MAX_BODY,
            "checkpoint_body_invalid")
    lines = body.split("\n")
    require(len(lines) >= 4, "checkpoint_comment_invalid")
    match = _MARKER.fullmatch(lines[0])
    require(match is not None and lines[-1].startswith("Capsule: "),
            "checkpoint_marker_invalid")
    encoded = lines[-1][len("Capsule: "):]
    require(len(encoded) <= _MAX_BODY, "checkpoint_capsule_limit")
    try:
        data = base64.b64decode(encoded, validate=True)
        require(base64.b64encode(data).decode("ascii") == encoded,
                "checkpoint_capsule_noncanonical")
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        require(_canonical(payload) == data, "checkpoint_json_noncanonical")
        value = V2PRCheckpoint.from_mapping(payload)
        require(lines[1] == f"Task checkpoint ({value.boundary}, v2):"
                and "\n".join(lines[2:-1]) == value.report
                and render_checkpoint(value) == body
                and hashlib.sha256(_DOMAIN + data).hexdigest() == match.group(1),
                "checkpoint_digest_or_display_mismatch")
        return value
    except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError) as error:
        if isinstance(error, CheckpointRefused):
            raise
        raise CheckpointRefused("checkpoint_capsule_invalid") from error


@dataclass(frozen=True, slots=True)
class CommentFacts:
    """Host-authenticated, exact, *PR-conversation* comment identity."""
    repository_id: int
    repository: str
    pr_number: int
    comment_id: int
    author_id: int
    body: str
    kind: str = "pr"


@dataclass(frozen=True, slots=True)
class ConfirmedCheckpoint:
    comment_id: int
    checkpoint: V2PRCheckpoint
    snapshot: dict[str, Any]


CommentReader = Callable[[int, int, float], CommentFacts]


class ExactCheckpointReader:
    """Read ONLY a requested historical comment ID and original Git objects.

    The same Host must pin authenticated repository identity and registered
    Issue to its bounded object, direct-ref and PR-comment readers. A comment
    body is data, not evidence of who posted it or whether the PR exists.
    """

    def __init__(
        self, identity: RepositoryIdentity, registered: RegisteredIssue,
        *, base_ref: str, principal: int, reader: ObjectReader, observer: Observer,
        comment_reader: CommentReader, limits: Limits = Limits(),
    ) -> None:
        require(type(identity) is RepositoryIdentity
                and type(registered) is RegisteredIssue
                and identity.repository_id == registered.repository_id
                and identity.repository == registered.repository,
                "checkpoint_host_identity_invalid")
        try:
            validate_branch_ref(base_ref)
        except (TypeError, ValueError) as error:
            raise CheckpointRefused("checkpoint_base_ref_invalid") from error
        require(callable(comment_reader), "checkpoint_comment_reader_required")
        require(positive(principal), "checkpoint_host_principal_invalid")
        self.principal = principal
        self.identity = identity
        self.registered = registered
        self.base_ref = base_ref
        self._comment_reader = comment_reader
        self._metadata = ReadOnlyMetadataV2Host(
            identity, registered, reader=reader, observer=observer, limits=limits)

    def _comment(self, pr_number: int, comment_id: int, deadline: float) -> CommentFacts:
        require(positive(pr_number) and positive(comment_id),
                "checkpoint_comment_identity_invalid")
        require(time.monotonic() < deadline, "checkpoint_deadline_exceeded")
        try:
            comment = self._comment_reader(pr_number, comment_id, deadline)
        except Exception as error:
            raise CheckpointRefused("checkpoint_comment_read_unavailable") from error
        require(time.monotonic() < deadline, "checkpoint_deadline_exceeded")
        require(type(comment) is CommentFacts
                and comment.repository_id == self.identity.repository_id
                and comment.repository == self.identity.repository
                and comment.pr_number == pr_number
                and comment.comment_id == comment_id
                and positive(comment.author_id) and comment.kind == "pr"
                and type(comment.body) is str
                and len(comment.body.encode("utf-8")) <= _MAX_BODY,
                "checkpoint_comment_provenance_invalid")
        return comment

    def _match(self, value: V2PRCheckpoint, pr_number: int) -> None:
        registered = self.registered
        require((value.repository_id, value.repository, value.issue,
                 value.branch_ref, value.base_ref, value.pr_number,
                 value.record_id, value.contract_id, value.principal)
                == (registered.repository_id, registered.repository,
                    registered.issue, registered.branch_ref, self.base_ref,
                    pr_number, registered.record_id, registered.contract_id,
                    self.principal),
                "checkpoint_registration_mismatch")

    def verify_metadata(self, value: V2PRCheckpoint) -> dict[str, Any]:
        """Verify Record/Contract/Evidence/Snapshot graph at the exact OID.

        The Metadata v2 validator independently checks the complete bounded
        archive DAG, original Issue ancestry, hashes, and snapshot graph.
        """
        require(type(value) is V2PRCheckpoint, "checkpoint_type_invalid")
        self._match(value, value.pr_number)
        def locator(kind: str, content_id: str, subject: str) -> ExactLocator:
            return ExactLocator(VERSION, value.repository_id, value.repository,
                                value.issue, value.metadata_commit, kind,
                                content_id, subject)
        try:
            record = self._metadata.read(locator(
                "task-record", value.record_id, self.registered.base_revision))
            contract = self._metadata.read(locator(
                "contract", value.contract_id, self.registered.base_revision))
            snapshot = self._metadata.read(value.snapshot_locator())
            v = snapshot["payload"]
            view = v["view"]
            authority = view["authority"]
            git = view["git"]
            github = view["github"]
            pull = github["pull_request"]
            expected_boundary = ("explicit-handoff" if value.boundary == "initial"
                                 else value.boundary)
            require(
                record["payload"]["contract_id"] == value.contract_id
                and record["payload"]["branch_ref"] == value.branch_ref
                and contract["subject"] == self.registered.base_revision
                and v["boundary"] == expected_boundary
                and view["repository"] == value.repository
                and view["task"] == str(value.issue)
                and view["subject"] == value.subject
                and view["branch_ref"] == value.branch_ref
                and authority["state"] == "observed"
                and authority["record_id"] == value.record_id
                and authority["contract_id"] == value.contract_id
                and authority["base_revision"] == self.registered.base_revision
                and authority["branch_ref"] == value.branch_ref
                and git["head"] == value.subject
                and git["branch_ref"] == value.branch_ref
                and git["branch_matches_task"] is True
                and git["remote_head"]["state"] == "observed"
                and git["remote_head"]["head_oid"] == value.subject
                and github["state"] == "observed"
                and pull["number"] == value.pr_number
                and pull["state"] == "open" and pull["draft"] is True
                and pull["base_repository"] == value.repository
                and pull["head_repository"] == value.repository
                and pull["base_ref"] == value.base_ref.removeprefix("refs/heads/")
                and pull["head_ref"] == value.branch_ref.removeprefix("refs/heads/")
                and pull["head_oid"] == value.subject
                and (view["previous_checkpoint"]["state"] == "absent"
                     if value.boundary == "initial"
                     else view["previous_checkpoint"]["state"] == "observed"),
                "checkpoint_snapshot_binding_mismatch",
            )
        except CheckpointRefused:
            raise
        except (ValueError, TypeError, IndexError, KeyError, AttributeError) as error:
            raise CheckpointRefused("checkpoint_metadata_unavailable") from error
        return v

    def read(self, pr_number: int, comment_id: int) -> ConfirmedCheckpoint:
        """Never choose a 'latest' comment, Task pointer or archive tip."""
        deadline = time.monotonic() + 40
        before = self._comment(pr_number, comment_id, deadline)
        value = decode_checkpoint(before.body)
        require(value.principal == before.author_id,
                "checkpoint_comment_author_mismatch")
        self._match(value, pr_number)
        snapshot = self.verify_metadata(value)
        after = self._comment(pr_number, comment_id, deadline)
        require(before == after, "checkpoint_comment_changed")
        return ConfirmedCheckpoint(comment_id, value, snapshot)


@dataclass(frozen=True, slots=True)
class PullFacts:
    """Host-authenticated effect-time PR, not comment-body claims."""
    repository_id: int
    repository: str
    pr_number: int
    base_ref: str
    head_ref: str
    head_oid: str
    state: str
    draft: bool
    principal: int


PullReader = Callable[[int, float], PullFacts]
CommentInventory = Callable[[int, float], tuple[CommentFacts, ...]]
PostComment = Callable[[int, str, float], object]
Authorization = Callable[[V2PRCheckpoint, bytes, float], bool]
# Host attests that every durable intent in this stream is either this key or
# positively reconciled; holds one exclusive stream lease until publish ends.
StreamLease = Callable[[V2PRCheckpoint, float], ContextManager[bool]]


class CreateOnceJournal(Protocol):
    """Implement with a trusted #140 fsync create-once journal."""
    def read(self, key: str) -> dict[str, Any] | None: ...
    def create(self, key: str, record: dict[str, Any]) -> None: ...


def _journal_key(value: V2PRCheckpoint) -> str:
    fields = (value.repository_id, value.issue, value.pr_number,
              value.principal, value.key)
    return hashlib.sha256(b"kagari-pr-checkpoint-intent/v2\n"
                          + _canonical(fields)).hexdigest()


class SourceOnlyCheckpointPublisher:
    """Opt-in #194 Host adapter, NOT an installed live GitHub publisher.

    The Host supplies fully authenticated, bounded/complete PR-comment views,
    exact comment reads, effect-time PR facts, Human authorization and durable
    #140 intent. No post is attempted after an unconfirmed prior intent.
    It cannot guarantee GitHub's cross-writer comment uniqueness/atomic CAS.
    """

    def __init__(
        self, exact_reader: ExactCheckpointReader, *,
        pull_reader: PullReader, inventory: CommentInventory,
        authorize: Authorization, post_comment: PostComment,
        journal: CreateOnceJournal, stream_lease: StreamLease,
    ) -> None:
        require(type(exact_reader) is ExactCheckpointReader
                and all(callable(fn) for fn in
                        (pull_reader, inventory, authorize, post_comment,
                         stream_lease))
                and callable(getattr(journal, "read", None))
                and callable(getattr(journal, "create", None)),
                "checkpoint_publisher_capabilities_required")
        self._exact = exact_reader
        self._pull = pull_reader
        self._inventory = inventory
        self._authorize = authorize
        self._post = post_comment
        self._journal = journal
        self._stream_lease = stream_lease

    def _pull_facts(self, value: V2PRCheckpoint, deadline: float) -> PullFacts:
        require(time.monotonic() < deadline, "checkpoint_deadline_exceeded")
        try:
            fact = self._pull(value.pr_number, deadline)
        except Exception as error:
            raise CheckpointRefused("checkpoint_pr_observation_unavailable") from error
        require(time.monotonic() < deadline, "checkpoint_deadline_exceeded")
        require(type(fact) is PullFacts
                and (fact.repository_id, fact.repository, fact.pr_number,
                     fact.base_ref, fact.head_ref, fact.head_oid,
                     fact.state, fact.draft, fact.principal)
                == (value.repository_id, value.repository, value.pr_number,
                    value.base_ref, value.branch_ref, value.subject,
                    "open", True, value.principal),
                "checkpoint_pr_not_exact_open_draft")
        return fact

    def _comments(self, value: V2PRCheckpoint,
                  deadline: float) -> tuple[CommentFacts, ...]:
        require(time.monotonic() < deadline, "checkpoint_deadline_exceeded")
        try:
            comments = self._inventory(value.pr_number, deadline)
        except Exception as error:
            raise CheckpointRefused("checkpoint_comment_inventory_unavailable") from error
        require(time.monotonic() < deadline, "checkpoint_deadline_exceeded")
        require(type(comments) is tuple and len(comments) <= _MAX_COMMENTS,
                "checkpoint_comment_inventory_limit")
        total = 0
        previous_id = 0
        for comment in comments:
            require(type(comment) is CommentFacts
                    and (comment.repository_id, comment.repository,
                         comment.pr_number, comment.kind)
                    == (value.repository_id, value.repository, value.pr_number, "pr")
                    and positive(comment.comment_id)
                    and comment.comment_id > previous_id
                    and positive(comment.author_id)
                    and type(comment.body) is str,
                    "checkpoint_comment_inventory_identity_invalid")
            total += len(comment.body.encode("utf-8"))
            require(total <= 8 * 1024 * 1024,
                    "checkpoint_comment_inventory_byte_limit")
            previous_id = comment.comment_id
        return comments

    def _owned(self, value: V2PRCheckpoint,
               comments: tuple[CommentFacts, ...]) -> dict[str, CommentFacts]:
        checkpoints: dict[str, CommentFacts] = {}
        for comment in comments:
            if comment.author_id != value.principal or MARKER not in comment.body:
                continue
            parsed = decode_checkpoint(comment.body)
            require((parsed.repository_id, parsed.repository, parsed.issue,
                     parsed.pr_number, parsed.principal, parsed.branch_ref,
                     parsed.base_ref)
                    == (value.repository_id, value.repository, value.issue,
                        value.pr_number, value.principal, value.branch_ref,
                        value.base_ref), "checkpoint_comment_namespace_conflict")
            require(parsed.key not in checkpoints, "checkpoint_key_duplicate")
            checkpoints[parsed.key] = comment
        return checkpoints

    def _confirmed(
        self, value: V2PRCheckpoint, candidate: CommentFacts,
        expected_body: str,
    ) -> ConfirmedCheckpoint:
        require(candidate.body == expected_body, "checkpoint_key_conflict")
        confirmed = self._exact.read(value.pr_number, candidate.comment_id)
        require(confirmed.checkpoint == value,
                "checkpoint_readback_mismatch")
        return confirmed

    def publish(
        self, value: V2PRCheckpoint, *,
        previous_comment_id: int | None = None,
    ) -> ConfirmedCheckpoint:
        """A serialized Host stream lease is required for every publication.

        The lease checks all durable intents across checkpoint keys, not only
        the matching key, and remains exclusive until readback/uncertainty.
        """
        require(type(value) is V2PRCheckpoint, "checkpoint_type_invalid")
        self._exact._match(value, value.pr_number)
        deadline = time.monotonic() + 40
        require(time.monotonic() < deadline, "checkpoint_deadline_exceeded")
        try:
            lease = self._stream_lease(value, deadline)
            with lease as cleared:
                require(cleared is True, "checkpoint_stream_pending")
                return self._publish_locked(value, previous_comment_id, deadline)
        except CheckpointRefused:
            raise
        except Exception as error:
            # The lease may fail after an effect. No automatic repost is safe.
            raise CheckpointUncertain("checkpoint_stream_lease_uncertain") from error

    def _publish_locked(
        self, value: V2PRCheckpoint,
        previous_comment_id: int | None, deadline: float,
    ) -> ConfirmedCheckpoint:
        body = render_checkpoint(value)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        journal_key = _journal_key(value)
        expected_intent = {
            "schema": 2, "repository_id": value.repository_id,
            "issue": value.issue, "pr_number": value.pr_number,
            "principal": value.principal, "key": value.key,
            "metadata_commit": value.metadata_commit,
            "snapshot_id": value.snapshot_id, "body_sha256": digest,
            "previous_comment_id": previous_comment_id,
        }
        require((previous_comment_id is None) if value.boundary == "initial"
                else positive(previous_comment_id),
                "checkpoint_previous_id_required")
        try:
            saved = self._journal.read(journal_key)
        except Exception as error:
            raise CheckpointRefused("checkpoint_intent_read_unavailable") from error
        require(saved is None or saved == expected_intent,
                "checkpoint_intent_conflict")
        pull_before = self._pull_facts(value, deadline)
        comments_before = self._comments(value, deadline)
        owned = self._owned(value, comments_before)

        # Both reconciliation and fresh publication must prove the immutable
        # original metadata and caller-specified predecessor relation.
        snapshot = self._exact.verify_metadata(value)
        previous = snapshot["view"]["previous_checkpoint"]
        if value.boundary == "initial":
            require(previous == {"state": "absent"},
                    "checkpoint_initial_boundary_mismatch")
        else:
            initial = owned.get("initial")
            require(initial is not None, "checkpoint_initial_required")
            initial_verified = self._exact.read(value.pr_number, initial.comment_id)
            require(initial_verified.checkpoint.boundary == "initial",
                    "checkpoint_initial_history_invalid")
            require(previous["state"] == "observed"
                    and previous["checkpoint_id"] == str(previous_comment_id),
                    "checkpoint_previous_binding_mismatch")
            expected = next((c for c in comments_before
                             if c.comment_id == previous_comment_id), None)
            require(expected is not None and expected.author_id == value.principal,
                    "checkpoint_invalid_previous")
            previous_verified = self._exact.read(value.pr_number,
                                                 previous_comment_id)
            require(previous_verified.checkpoint.key in owned
                    and owned[previous_verified.checkpoint.key].comment_id
                    == previous_comment_id
                    and (previous_verified.checkpoint.subject,
                         previous_verified.checkpoint.metadata_commit,
                         previous_verified.checkpoint.snapshot_id)
                    == (previous["subject"], previous["metadata_commit"],
                        previous["snapshot_id"]),
                    "checkpoint_previous_graph_mismatch")

        if value.key in owned:
            result = self._confirmed(value, owned[value.key], body)
            require(self._comments(value, deadline) == comments_before,
                    "checkpoint_inventory_changed")
            require(self._pull_facts(value, deadline) == pull_before,
                    "checkpoint_pr_changed_during_readback")
            return result
        if saved is not None:
            raise CheckpointUncertain("checkpoint_previous_intent_unconfirmed")

        if value.boundary == "initial":
            require(not owned, "checkpoint_initial_not_first")
        else:
            # Only new effects require the latest predecessor. Historical
            # idempotent readbacks above may name a non-latest valid comment.
            require(max(c.comment_id for c in owned.values()) == previous_comment_id,
                    "checkpoint_previous_not_current")

        try:
            approved = self._authorize(value, body.encode("utf-8"), deadline)
        except Exception as error:
            raise CheckpointRefused("checkpoint_grant_unavailable") from error
        require(approved is True, "checkpoint_human_grant_required")
        require(self._pull_facts(value, deadline) == pull_before
                and self._comments(value, deadline) == comments_before,
                "checkpoint_pre_effect_changed")

        # Must be fsynced before a possible external post; stream lease guards
        # all earlier keys' pending effects for the entire critical section.
        try:
            self._journal.create(journal_key, expected_intent)
        except Exception as error:
            raise CheckpointUncertain("checkpoint_intent_fsync_uncertain") from error

        try:
            require(self._pull_facts(value, deadline) == pull_before
                    and self._comments(value, deadline) == comments_before,
                    "checkpoint_post_intent_changed")
        except CheckpointRefused as error:
            raise CheckpointUncertain("checkpoint_intent_requires_recovery") from error

        try:
            self._post(value.pr_number, body, deadline)
        except Exception:
            pass  # lost ACK: one original-identity readback, never repost
        try:
            require(self._pull_facts(value, deadline) == pull_before,
                    "checkpoint_pr_changed_after_effect")
            after = self._comments(value, deadline)
            selected = self._owned(value, after).get(value.key)
            require(selected is not None, "checkpoint_post_not_observed")
            result = self._confirmed(value, selected, body)
            require(self._comments(value, deadline) == after,
                    "checkpoint_inventory_changed")
            require(self._pull_facts(value, deadline) == pull_before,
                    "checkpoint_pr_changed_during_readback")
            return result
        except Exception as error:
            raise CheckpointUncertain("checkpoint_post_outcome_uncertain") from error
