"""#291 R5C0: Host-only v2 PR-first + initial checkpoint read-only proof.

Never creates Product commits/PR/comments/Metadata, runs a Task/Session/model,
or consumes #194's v1 TaskCollaboration for v2 authority. Host supplies
authenticated, bounded and COMPLETE task-branch PR facts and comment history.
A positive proof is an observation, NOT a Task launch grant.
"""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

if __package__:
    from .kagari_vnext_main_v2_observation import (
        V2ReadOnlyMainHost, V2MainObservationRefused)
    from .kagari_vnext_task_binding import (
        worktree_registered, git, ControlError)
else:
    from kagari_vnext_main_v2_observation import (
        V2ReadOnlyMainHost, V2MainObservationRefused)
    from kagari_vnext_task_binding import (
        worktree_registered, git, ControlError)

# #337 and #194 use canonical top-level Host types. Do not import them again
# as tools.* and accidentally create distinct nominal dataclass identities.
from kagari_metadata_v2_host_binding import (
    ExactLocator, ReadOnlyMetadataV2Host, MetadataV2HostRefused, VERSION)
from task_collaboration import RepositoryFacts, PullFacts, CommentFacts
from task_view import decode_snapshot, TaskViewError
from metadata_codec import encode_object


_MARKER = re.compile(
    r"<!-- kagari-v2-initial-checkpoint/v1 sha256=([0-9a-f]{64}) -->\Z")
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA64 = re.compile(r"[0-9a-f]{64}\Z")
_DOMAIN = b"kagari-v2-initial-checkpoint/v1\n"
_MAX_BODY = 16_384
_FIELDS = {
    "schema_version", "metadata_version", "repository_id", "repository",
    "issue", "branch_ref", "base_ref", "pr_number", "principal", "subject",
    "metadata_commit", "record_id", "contract_id", "snapshot_id",
    "boundary", "key", "report",
}


class V2PrFirstRefused(ValueError):
    """No Task execution may follow a missing/ambiguous v2 PR-first proof."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def need(ok: bool, code: str) -> None:
    if not ok:
        raise V2PrFirstRefused(code)


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _no_dupes(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for name, item in pairs:
        if name in value:
            raise V2PrFirstRefused("R5C0_CAPSULE_DUPLICATE_KEY")
        value[name] = item
    return value


def decode_initial_capsule(body: str) -> dict:
    """Parse a version-2 checkpoint with closed canonical authenticated bytes.

    The comment author's identity is validated independently against the
    trusted Host principal; this digest is *integrity*, not authentication.
    """
    need(type(body) is str, "R5C0_CAPSULE_INVALID")
    try:
        encoded_body = body.encode("utf-8", errors="strict")
    except UnicodeError:
        raise V2PrFirstRefused("R5C0_CAPSULE_INVALID") from None
    need(len(encoded_body) <= _MAX_BODY, "R5C0_CAPSULE_INVALID")
    lines = body.split("\n")
    need(len(lines) == 3 and _MARKER.fullmatch(lines[0]) is not None
         and lines[1] == "Task checkpoint (initial):"
         and lines[2].startswith("Capsule: "),
         "R5C0_CAPSULE_INVALID")
    digest = _MARKER.fullmatch(lines[0]).group(1)
    encoded = lines[2][len("Capsule: "):]
    try:
        raw = base64.b64decode(encoded, validate=True)
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_no_dupes)
    except V2PrFirstRefused:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise V2PrFirstRefused("R5C0_CAPSULE_INVALID") from None
    try:
        canonical = _canonical(value)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise V2PrFirstRefused("R5C0_CAPSULE_INVALID") from None
    need(base64.b64encode(raw).decode("ascii") == encoded
         and type(value) is dict and value.keys() == _FIELDS
         and canonical == raw
         and hashlib.sha256(_DOMAIN + raw).hexdigest() == digest,
         "R5C0_CAPSULE_INVALID")
    for field in ("repository", "branch_ref", "base_ref", "subject",
                  "metadata_commit", "record_id", "contract_id", "snapshot_id",
                  "boundary", "key", "report"):
        need(type(value[field]) is str, "R5C0_CAPSULE_INVALID")
    try:
        report_bytes = value["report"].encode("utf-8", errors="strict")
    except UnicodeError:
        raise V2PrFirstRefused("R5C0_CAPSULE_INVALID") from None
    need(type(value["schema_version"]) is int and value["schema_version"] == 1
         and type(value["metadata_version"]) is int and value["metadata_version"] == VERSION
         and all(type(value[k]) is int and value[k] > 0 for k in
                 ("repository_id", "issue", "pr_number", "principal"))
         and bool(_SHA40.fullmatch(value["subject"]))
         and bool(_SHA40.fullmatch(value["metadata_commit"]))
         and all(_SHA64.fullmatch(value[k]) is not None
                 for k in ("record_id", "contract_id", "snapshot_id"))
         and value["boundary"] == "initial" and value["key"] == "initial"
         and 0 < len(report_bytes) <= 4096,
         "R5C0_CAPSULE_INVALID")
    return value


@dataclass(frozen=True, slots=True)
class PrFirstRequest:
    operation: str
    repository_id: int
    repository: str
    issue: int
    branch_ref: str
    base_ref: str
    base_revision: str
    record_id: str
    contract_id: str
    metadata_tip: str
    archive_tip: str
    subject: str


@dataclass(frozen=True, slots=True)
class PrFirstFacts:
    """Trusted Host-read immutable authenticated PR/remote/comment snapshot.

    `complete` must mean all matching Task-branch PRs and all comments
    for its candidate PR are represented, bounded and independently read.
    Typed facts and a Boolean cannot authenticate a malicious Host.
    """

    repository: RepositoryFacts
    principal: int
    remote_head: str | None
    pulls: tuple[PullFacts, ...]
    comments: tuple[CommentFacts, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class V2PrFirstReadOnlyHost:
    readonly: V2ReadOnlyMainHost
    history: ReadOnlyMetadataV2Host
    expected_base_ref: str
    read_facts: Callable[[PrFirstRequest], PrFirstFacts]

    def __post_init__(self) -> None:
        rd = self.readonly
        need(type(rd) is V2ReadOnlyMainHost
             and type(self.history) is ReadOnlyMetadataV2Host
             and type(self.expected_base_ref) is str
             and self.expected_base_ref.startswith("refs/heads/")
             and self.expected_base_ref != rd.current_reader.registered.branch_ref
             and callable(self.read_facts), "R5C0_HOST_INVALID")
        identity = rd.current_reader.identity
        registered = rd.current_reader.registered
        need(self.history._identity == identity
             and self.history._registered == registered,
             "R5C0_HISTORY_HOST_MISMATCH")

    def observe(
        self, *, project: Path, repository: str, issue: int,
        branch_ref: str, base_revision: str, contract_id: str,
        read_issue: Callable[[int], dict],
    ) -> dict:
        rd = self.readonly
        registered = rd.current_reader.registered
        need((repository, issue, branch_ref, base_revision, contract_id) ==
             (registered.repository, registered.issue, registered.branch_ref,
              registered.base_revision, registered.contract_id)
             and callable(read_issue), "R5C0_TASK_BINDING_MISMATCH")

        def authoritative() -> dict:
            try:
                return rd.observe(
                    project=project, repository=repository, issue=issue,
                    branch_ref=branch_ref, base_revision=base_revision,
                    contract_id=contract_id, read_issue=read_issue)
            except V2MainObservationRefused as exc:
                raise V2PrFirstRefused(exc.code) from None

        def current():
            try:
                return rd._current()
            except V2MainObservationRefused as exc:
                raise V2PrFirstRefused(exc.code) from None

        def git_subject() -> str:
            target = project / ".worktrees" / str(issue)
            try:
                worktree_registered(project, target, branch_ref)
                subject = git(target, "rev-parse", "HEAD")
                # Git's default respects submodule.<path>.ignore=all;
                # explicitly include modified/untracked nested submodules.
                dirty = git(target, "status", "--porcelain=v1", "-uall",
                            "--ignore-submodules=none")
            except (ControlError, OSError, RuntimeError, ValueError):
                raise V2PrFirstRefused("R5C0_WORKTREE_UNVERIFIED") from None
            need(bool(_SHA40.fullmatch(subject)) and not dirty,
                 "R5C0_PRODUCT_NOT_PRISTINE")
            return subject

        first_authority = authoritative()
        first = current()
        subject = git_subject()
        request = PrFirstRequest(
            "v2-pr-first-read-only-proof", first.repository_id,
            repository, issue, branch_ref, self.expected_base_ref,
            base_revision, first.record_id, contract_id,
            first.metadata_tip, first.archive_tip, subject)

        def facts() -> PrFirstFacts:
            try:
                response = self.read_facts(request)
            except Exception:
                raise V2PrFirstRefused("R5C0_PR_FACTS_UNAVAILABLE") from None
            need(type(response) is PrFirstFacts
                 and type(response.repository) is RepositoryFacts
                 and response.repository.repository == repository
                 and response.repository.repository_id == first.repository_id
                 and response.repository.default_branch_ref == self.expected_base_ref
                 and type(response.principal) is int and response.principal > 0
                 and response.complete is True
                 and type(response.remote_head) is str
                 and response.remote_head == subject
                 and type(response.pulls) is tuple and len(response.pulls) <= 100
                 and type(response.comments) is tuple and len(response.comments) <= 100,
                 "R5C0_PR_FACTS_MISMATCH")
            return response

        before = facts()
        # The read_facts callback must exhaustively enumerate matching PRs
        # for this exact head branch. Duplicate, moved or foreign candidates
        # are never silently selected by "first match".
        need(all(type(p) is PullFacts for p in before.pulls)
             and all(type(c) is CommentFacts and type(c.body) is str
                     for c in before.comments),
             "R5C0_PR_FACTS_MISMATCH")
        relevant = [p for p in before.pulls if
                    p.head_ref == branch_ref[len("refs/heads/"):]]
        need(len(relevant) == 1, "R5C0_PR_ABSENT_OR_AMBIGUOUS")
        pr = relevant[0]
        need(type(pr.number) is int and pr.number > 0
             and pr.base_repository == repository
             and pr.head_repository == repository
             and pr.base_ref == self.expected_base_ref[len("refs/heads/"):]
             and pr.head_ref == branch_ref[len("refs/heads/"):]
             and pr.head_oid == subject and pr.state == "open"
             and pr.draft is True,
             "R5C0_PR_IDENTITY_MISMATCH")

        # Closed canonical capsule. v1-only checkpoint comments cannot be
        # promoted to v2 just because they mention a v2 commit.
        owned = [c for c in before.comments if
                 c.author_id == before.principal
                 and ("kagari-v2-initial-checkpoint/" in c.body
                      or "agentcore-task-checkpoint/" in c.body)]
        need(len(owned) == 1, "R5C0_INITIAL_CHECKPOINT_MISSING_OR_DUPLICATE")
        comment = owned[0]
        need(comment.repository == repository and comment.kind == "pr"
             and comment.number == pr.number and type(comment.comment_id) is int
             and comment.comment_id > 0,
             "R5C0_CHECKPOINT_COMMENT_MISMATCH")
        capsule = decode_initial_capsule(comment.body)
        need((capsule["repository_id"], capsule["repository"], capsule["issue"],
              capsule["branch_ref"], capsule["base_ref"], capsule["pr_number"],
              capsule["principal"], capsule["subject"], capsule["record_id"],
              capsule["contract_id"]) ==
             (first.repository_id, repository, issue, branch_ref,
              self.expected_base_ref, pr.number, before.principal,
              subject, first.record_id, contract_id),
             "R5C0_CHECKPOINT_BINDING_MISMATCH")

        locator = ExactLocator(
            VERSION, first.repository_id, repository, issue,
            capsule["metadata_commit"], "task-view-snapshot",
            capsule["snapshot_id"], subject)
        try:
            raw = self.history.read(locator)
            # Validate closed #144 snapshot schema, not just a stored object
            # matching the content digest/metadata tree path.
            oid, snapshot_bytes = encode_object(
                raw["kind"], raw["repository"], raw["task"],
                raw["subject"], raw["payload"])
            need(oid == capsule["snapshot_id"],
                 "R5C0_SNAPSHOT_BINDING_MISMATCH")
            snapshot = decode_snapshot(
                snapshot_bytes, snapshot_id=oid, repository=repository,
                task=str(issue), subject=subject)
        except V2PrFirstRefused:
            raise
        except (MetadataV2HostRefused, TaskViewError, ValueError, TypeError, KeyError):
            raise V2PrFirstRefused("R5C0_METADATA_HISTORY_UNAVAILABLE") from None
        view = snapshot["view"]
        authority = view["authority"]
        git_view = view["git"]
        github_view = view["github"]
        need(snapshot["boundary"] == "explicit-handoff"
             and view["branch_ref"] == branch_ref
             and view["previous_checkpoint"] == {"state": "absent"}
             and authority["record_id"] == first.record_id
             and authority["contract_id"] == contract_id
             and authority["base_revision"] == base_revision
             and authority["branch_ref"] == branch_ref
             and authority["disposition"] is None
             and git_view["head"] == subject
             and git_view["branch_ref"] == branch_ref
             and git_view["branch_matches_task"] is True
             and github_view["state"] == "observed"
             and github_view["pull_request"] == {
                 "base_repository": repository, "head_repository": repository,
                 "number": pr.number, "state": "open", "draft": True,
                 "base_ref": pr.base_ref, "head_ref": pr.head_ref,
                 "head_oid": subject,
             }, "R5C0_SNAPSHOT_BINDING_MISMATCH")

        after = facts()
        current_after = current()
        authoritative_after = authoritative()
        need(before == after and first == current_after
             and first_authority["approved_task"] ==
             authoritative_after["approved_task"]
             and first_authority["current_record"] ==
             authoritative_after["current_record"]
             and subject == git_subject(),
             "R5C0_OBSERVATION_CHANGED")
        try:
            rd._verify_lease(project)
        except V2MainObservationRefused as exc:
            raise V2PrFirstRefused(exc.code) from None

        return {
            **authoritative_after,
            "pr_first": {
                "proof": "PROVED", "repository_id": first.repository_id,
                "repository": repository, "issue": issue,
                "contract_id": contract_id, "record_id": first.record_id,
                "branch_ref": branch_ref, "subject": subject,
                "pull_request": pr.number, "comment_id": comment.comment_id,
                "metadata_commit": locator.metadata_commit,
                "snapshot_id": locator.object_id,
            },
            "task_execution_authorized": False,
            "worktree_mutation_authorized": False,
            "github_write_authorized": False,
            "session_started": False, "prompt_sent": False,
        }
