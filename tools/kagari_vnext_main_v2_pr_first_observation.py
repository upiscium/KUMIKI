"""#291 R5C0/R5C1B0: Host-only canonical v2 PR-first read-only proof.

Never creates Product commits/PR/comments/Metadata, runs a Task/Session/model,
or consumes #194's v1 TaskCollaboration for v2 authority. Host supplies
authenticated, bounded and COMPLETE task-branch PR facts and comment history.
A positive proof is an observation, NOT a Task launch grant.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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

# Reuse the one canonical #337 codec/identity types, not a second "v2" parser.
# These are top-level import identities shared by #337 and #291's Host.
from kagari_metadata_v2_pr_checkpoint import (
    V2PRCheckpoint, CommentFacts as V2CheckpointCommentFacts,
    ConfirmedCheckpoint, ExactCheckpointReader, CheckpointRefused,
    decode_checkpoint)
from task_collaboration import RepositoryFacts, PullFacts

_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_MAX_COMMENT_BYTES = 8 * 1024 * 1024
# Only an actual beginning-of-body checkpoint opener is eligible. Ordinary
# discussion mentioning a format string is NOT a checkpoint candidate.
_RESERVED_CHECKPOINT_OPENERS = (
    "<!-- kagari-pr-checkpoint", "<!-- kagari-v2-initial-checkpoint",
    "<!-- agentcore-task-checkpoint",
)


class V2PrFirstRefused(ValueError):
    """No Task execution may follow a missing/ambiguous v2 PR-first proof."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def need(ok: bool, code: str) -> None:
    if not ok:
        raise V2PrFirstRefused(code)


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
    comments: tuple[V2CheckpointCommentFacts, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class V2PrFirstReadOnlyHost:
    readonly: V2ReadOnlyMainHost
    checkpoint: ExactCheckpointReader
    expected_base_ref: str
    read_facts: Callable[[PrFirstRequest], PrFirstFacts]

    def __post_init__(self) -> None:
        rd = self.readonly
        need(type(rd) is V2ReadOnlyMainHost
             and type(self.checkpoint) is ExactCheckpointReader
             and type(self.expected_base_ref) is str
             and self.expected_base_ref.startswith("refs/heads/")
             and self.expected_base_ref != rd.current_reader.registered.branch_ref
             and callable(self.read_facts), "R5C0_HOST_INVALID")
        identity = rd.current_reader.identity
        registered = rd.current_reader.registered
        need(self.checkpoint.identity == identity
             and self.checkpoint.registered == registered
             and self.checkpoint.base_ref == self.expected_base_ref,
             "R5C1B0_CHECKPOINT_HOST_MISMATCH")

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
        # Validate *every* authenticated inventory entry, before selecting an
        # author-owned initial checkpoint. An unknown/truncated inventory, a
        # forged repo ID or duplicate/non-monotonic comment ID is not authority.
        def validated_comments(response: PrFirstFacts) -> None:
            total = 0
            previous = 0
            for item in response.comments:
                need(type(item) is V2CheckpointCommentFacts
                     and type(item.repository_id) is int
                     and type(item.pr_number) is int
                     and type(item.comment_id) is int
                     and type(item.author_id) is int
                     and type(item.repository) is str
                     and type(item.kind) is str
                     and type(item.body) is str
                     and (item.repository_id, item.repository,
                          item.pr_number, item.kind)
                     == (first.repository_id, repository, pr.number, "pr")
                     and 0 < item.comment_id <= (1 << 63) - 1
                     and 0 < item.author_id <= (1 << 63) - 1
                     and item.comment_id > previous,
                     "R5C1B0_COMMENT_INVENTORY_INVALID")
                try:
                    count = len(item.body.encode("utf-8", "strict"))
                except UnicodeError:
                    raise V2PrFirstRefused(
                        "R5C1B0_COMMENT_INVENTORY_INVALID") from None
                total += count
                # #337 bounds the complete PR inventory at its Host source,
                # not all unrelated human comments to the 16 KiB capsule
                # limit. The canonical decoder separately enforces that
                # smaller bound on actual owned checkpoint comments.
                need(total <= _MAX_COMMENT_BYTES,
                     "R5C1B0_COMMENT_INVENTORY_INVALID")
                previous = item.comment_id

        need(all(type(p) is PullFacts for p in before.pulls),
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
        need(self.checkpoint.principal == before.principal,
             "R5C1B0_PRINCIPAL_MISMATCH")
        validated_comments(before)

        # The original initial capsule is the only acceptable PR-first proof.
        # Do not silently adopt #194 v1 or R5C0's provisional v1-labelled
        # format. Non-checkpoint discussion comments are harmless, including
        # other authors' comments; a conflicting owned checkpoint is not.
        owned: list[tuple[V2CheckpointCommentFacts, V2PRCheckpoint]] = []
        for item in before.comments:
            if item.author_id != before.principal:
                continue
            if not item.body.startswith(_RESERVED_CHECKPOINT_OPENERS):
                continue
            need(not item.body.startswith((
                     "<!-- kagari-v2-initial-checkpoint",
                     "<!-- agentcore-task-checkpoint")),
                 "R5C1B0_OBSOLETE_CHECKPOINT")
            try:
                capsule = decode_checkpoint(item.body)
            except CheckpointRefused:
                raise V2PrFirstRefused("R5C1B0_CANONICAL_INVALID") from None
            need(capsule.boundary == "initial" and capsule.key == "initial",
                 "R5C1B0_NONINITIAL_CHECKPOINT")
            owned.append((item, capsule))
        need(len(owned) == 1, "R5C0_INITIAL_CHECKPOINT_MISSING_OR_DUPLICATE")
        comment, capsule = owned[0]
        need((capsule.repository_id, capsule.repository, capsule.issue,
              capsule.branch_ref, capsule.base_ref, capsule.pr_number,
              capsule.principal, capsule.subject, capsule.record_id,
              capsule.contract_id)
             == (first.repository_id, repository, issue, branch_ref,
                 self.expected_base_ref, pr.number, before.principal,
                 subject, first.record_id, contract_id),
             "R5C1B0_CHECKPOINT_BINDING_MISMATCH")
        # #337's canonical reader obtains the same exact ID independently
        # from the trusted Host and verifies original Record/Contract/Snapshot
        # at the *original* metadata commit, including snapshot PR facts and
        # an unchanged comment-body read before/after Git object traversal.
        try:
            confirmed = self.checkpoint.read(pr.number, comment.comment_id)
        except CheckpointRefused:
            raise V2PrFirstRefused("R5C1B0_CHECKPOINT_UNVERIFIED") from None
        except Exception:
            raise V2PrFirstRefused("R5C1B0_CHECKPOINT_UNAVAILABLE") from None
        need(type(confirmed) is ConfirmedCheckpoint
             and confirmed.comment_id == comment.comment_id
             and confirmed.checkpoint == capsule
             and type(confirmed.snapshot) is dict,
             "R5C1B0_COMMENT_READBACK_MISMATCH")
        # An initial Snapshot is immutable historical data; it is not itself
        # a fresh Task grant or proof the current Issue remains admitted.
        view = confirmed.snapshot.get("view")
        need(type(view) is dict
             and type(view.get("authority")) is dict
             and view["authority"].get("disposition") is None,
             "R5C1B0_SNAPSHOT_TERMINAL")

        after = facts()
        need(all(type(p) is PullFacts for p in after.pulls),
             "R5C0_PR_FACTS_MISMATCH")
        validated_comments(after)
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
                "metadata_commit": capsule.metadata_commit,
                "snapshot_id": capsule.snapshot_id,
                "checkpoint_schema": "kagari-pr-checkpoint/v2",
            },
            "task_execution_authorized": False,
            "worktree_mutation_authorized": False,
            "github_write_authorized": False,
            "session_started": False, "prompt_sent": False,
        }
