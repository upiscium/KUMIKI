"""#291 R5C1A: source-only, Host-granted Product branch + Draft PR publication.

Only an explicitly installed Main-owned Host may supply the narrowly typed
effects. No built-in GitHub token, Git push, PR creator, v1 MetadataStore
authority, Session/model/Ask path or runtime CLI is added. A lost/ambiguous
ACK is reconciled against the SAME durable #140 intent, never blindly retried.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import time

if __package__:
    from .kagari_vnext_main_v2_observation import (
        V2ReadOnlyMainHost, V2MainObservationRefused)
    from .kagari_vnext_task_binding import (
        ControlError, git, worktree_registered)
else:
    from kagari_vnext_main_v2_observation import (
        V2ReadOnlyMainHost, V2MainObservationRefused)
    from kagari_vnext_task_binding import ControlError, git, worktree_registered

from kagari_vnext_trusted_host import RecoveryJournal
from task_collaboration import RepositoryFacts

_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_KEY_DOMAIN = b"kagari-v2-product-pr-first-intent/v1\n"
_OPERATIONS = frozenset(("bootstrap", "push", "draft-pr"))
_MAX_PRS = 100


class ProductPrRefused(ValueError):
    """Effect denied or unqualified; never an implied Task/Session grant."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ProductPrUncertain(ProductPrRefused):
    """An exact effect may have happened; keep receipt and reconcile only."""


def need(ok: bool, code: str) -> None:
    if not ok:
        raise ProductPrRefused(code)


def _oid(value: object) -> bool:
    return type(value) is str and _SHA.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class ProductPull:
    number: int
    repository: str
    head_repository: str
    base_ref: str
    head_ref: str
    head_oid: str
    state: str
    draft: bool
    author_id: int
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class ProductPrFacts:
    """Host-authenticated complete branch PR inventory and exact remote ref.

    remote_state=ABSENT is a positive remote-ref absence proof, NOT an error;
    UNKNOWN observations are *not* represented and must throw.
    """
    repository: RepositoryFacts
    principal: int
    remote_state: str
    remote_head: str | None
    pulls: tuple[ProductPull, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class ProductRequest:
    repository_id: int
    repository: str
    issue: int
    branch_ref: str
    base_ref: str
    base_revision: str
    record_id: str
    contract_id: str
    archive_tip: str
    metadata_tip: str


@dataclass(frozen=True, slots=True)
class BootstrapCandidate:
    subject: str
    tree: str


@dataclass(frozen=True, slots=True)
class ProductEffect:
    operation: str
    request: ProductRequest
    expected_head: str
    subject: str
    expected_remote: str | None
    principal: int
    title: str
    body: str
    non_force: bool = True


def _key(effect: ProductEffect) -> str:
    request = effect.request
    # The key does NOT depend on a mutable remote HEAD, metadata tip,
    # candidate Commit, or PR ID. A crashed original effect must collide
    # with a subsequent proposal for this same first-lifecycle operation.
    value = [effect.operation, request.repository_id, request.issue,
             request.branch_ref, request.base_revision,
             request.record_id, request.contract_id]
    return hashlib.sha256(
        _KEY_DOMAIN + json.dumps(value, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _receipt(effect: ProductEffect) -> dict:
    req = effect.request
    return {
        "schema": 1, "operation": effect.operation,
        "repository_id": req.repository_id, "repository": req.repository,
        "issue": req.issue, "branch_ref": req.branch_ref,
        "base_ref": req.base_ref, "base_revision": req.base_revision,
        "record_id": req.record_id, "contract_id": req.contract_id,
        "archive_tip": req.archive_tip, "metadata_tip": req.metadata_tip,
        "expected_head": effect.expected_head,
        "subject": effect.subject, "expected_remote": effect.expected_remote,
        "principal": effect.principal, "title": effect.title, "body": effect.body,
        "non_force": True,
    }


@dataclass(frozen=True, slots=True)
class V2ProductPrFirstHost:
    """Only trusted Host supplies effects, authorization and #140 journal.

    A constructor may be called by anybody in Python; this object is NOT an
    authorization token. The Main owner and Host callbacks must prove the
    approved effect *again* inside their effect boundary.
    """

    readonly: V2ReadOnlyMainHost
    base_ref: str
    read_facts: Callable[[ProductRequest], ProductPrFacts]
    plan_bootstrap: Callable[[ProductRequest, str], BootstrapCandidate]
    bootstrap: Callable[[ProductEffect], object]
    push_branch: Callable[[ProductEffect], object]
    create_draft: Callable[[ProductEffect], object]
    authorize: Callable[[ProductEffect], bool]
    journal: RecoveryJournal

    def __post_init__(self) -> None:
        rd = self.readonly
        need(type(rd) is V2ReadOnlyMainHost
             and type(self.journal) is RecoveryJournal
             and type(self.base_ref) is str
             and self.base_ref.startswith("refs/heads/")
             and self.base_ref != rd.current_reader.registered.branch_ref
             and all(callable(fn) for fn in (
                 self.read_facts, self.plan_bootstrap, self.bootstrap,
                 self.push_branch, self.create_draft, self.authorize)),
             "R5C1A_HOST_INVALID")

    def publish(
        self, *, project: Path, repository: str, issue: int,
        branch_ref: str, base_revision: str, contract_id: str,
        read_issue: Callable[[int], dict],
        check_main_owner: Callable[[], None],
    ) -> dict:
        rd = self.readonly
        reg = rd.current_reader.registered
        selected = (repository, issue, branch_ref, base_revision, contract_id)
        need(selected == (reg.repository, reg.issue, reg.branch_ref,
                          reg.base_revision, reg.contract_id)
             and isinstance(project, Path) and callable(read_issue)
             and callable(check_main_owner),
             "R5C1A_TASK_BINDING_INVALID")
        target = project / ".worktrees" / str(issue)
        deadline = time.monotonic() + 90

        def authority() -> object:
            check_main_owner()
            try:
                rd.observe(project=project, repository=repository, issue=issue,
                           branch_ref=branch_ref, base_revision=base_revision,
                           contract_id=contract_id, read_issue=read_issue)
                current = rd._current()
            except V2MainObservationRefused as exc:
                raise ProductPrRefused(exc.code) from None
            check_main_owner()
            return current

        def local() -> tuple[str, str]:
            try:
                worktree_registered(project, target, branch_ref)
                head = git(target, "rev-parse", "HEAD")
                tree = git(target, "rev-parse", "HEAD^{tree}")
                dirty = git(target, "status", "--porcelain=v1", "-uall",
                            "--ignore-submodules=none")
                # A Task-specific commit must derive from approved base.
                git(target, "merge-base", "--is-ancestor", base_revision, head)
            except (ControlError, OSError, RuntimeError, ValueError):
                raise ProductPrRefused("R5C1A_PRODUCT_GIT_UNVERIFIED") from None
            need(_oid(head) and _oid(tree) and not dirty,
                 "R5C1A_PRODUCT_NOT_PRISTINE")
            return head, tree

        current = authority()
        req = ProductRequest(
            current.repository_id, repository, issue, branch_ref,
            self.base_ref, base_revision, current.record_id, contract_id,
            current.archive_tip, current.metadata_tip)
        approved = rd.current_reader.approved
        head, tree = local()

        def check(expected_head: str, expected_tree: str) -> None:
            need(time.monotonic() < deadline, "R5C1A_DEADLINE_EXCEEDED")
            need(authority() == current, "R5C1A_CURRENT_TASK_CHANGED")
            need(local() == (expected_head, expected_tree),
                 "R5C1A_PRODUCT_HEAD_CHANGED")

        def facts() -> ProductPrFacts:
            need(time.monotonic() < deadline, "R5C1A_DEADLINE_EXCEEDED")
            try:
                result = self.read_facts(req)
            except Exception:
                raise ProductPrRefused("R5C1A_REMOTE_FACTS_UNKNOWN") from None
            need(type(result) is ProductPrFacts and
                 type(result.repository) is RepositoryFacts and
                 (result.repository.repository, result.repository.repository_id,
                  result.repository.default_branch_ref) ==
                 (repository, current.repository_id, self.base_ref) and
                 type(result.principal) is int and 0 < result.principal < (1 << 63)
                 and result.complete is True and
                 type(result.pulls) is tuple and
                 len(result.pulls) <= _MAX_PRS and
                 all(type(p) is ProductPull for p in result.pulls) and
                 result.remote_state in ("ABSENT", "PRESENT") and
                 ((_oid(result.remote_head) and result.remote_state == "PRESENT")
                  or (result.remote_head is None and result.remote_state == "ABSENT")),
                 "R5C1A_REMOTE_FACTS_INVALID")
            numbers: set[int] = set()
            for pr in result.pulls:
                need(type(pr.number) is int and pr.number > 0 and
                     pr.number not in numbers and
                     type(pr.author_id) is int and pr.author_id > 0 and
                     type(pr.head_ref) is str and
                     type(pr.state) is str and
                     type(pr.title) is str and type(pr.body) is str,
                     "R5C1A_PR_INVENTORY_INVALID")
                numbers.add(pr.number)
            return result

        def task_pulls(observation: ProductPrFacts) -> tuple[ProductPull, ...]:
            return tuple(p for p in observation.pulls
                         if p.head_ref == branch_ref.removeprefix("refs/heads/"))

        def exact_pr(pr: ProductPull, subject: str, owner: int) -> None:
            need(pr.repository == repository and
                 pr.head_repository == repository and
                 pr.base_ref == self.base_ref.removeprefix("refs/heads/") and
                 pr.head_ref == branch_ref.removeprefix("refs/heads/") and
                 pr.head_oid == subject and pr.state == "open" and
                 pr.draft is True and pr.author_id == owner and
                 pr.title == approved.title and pr.body == approved.body,
                 "R5C1A_PR_CONFLICT")

        def intent(op: str, expected_head: str, subject: str,
                   expected_remote: str | None, owner: int) -> ProductEffect:
            need(op in _OPERATIONS and _oid(subject) and _oid(expected_head),
                 "R5C1A_EFFECT_IDENTITY_INVALID")
            return ProductEffect(
                op, req, expected_head, subject, expected_remote, owner,
                approved.title, approved.body)

        def saved(op: str, owner: int) -> dict | None:
            # Journal keys are from stable Task/Record/operation identity,
            # not from today's head. Do not inspect arbitrary persisted paths.
            dummy = intent(op, base_revision, base_revision, None, owner)
            try:
                data = self.journal.read(_key(dummy))
            except Exception:
                raise ProductPrRefused("R5C1A_INTENT_READ_UNAVAILABLE") from None
            need(data is None or type(data) is dict,
                 "R5C1A_INTENT_INVALID")
            return data

        def effect_once(
            proposal: ProductEffect, effect_callback: Callable,
            preflight: Callable[[], None],
            confirm: Callable[[], None],
        ) -> None:
            original = _receipt(proposal)
            key = _key(proposal)
            previous = saved(proposal.operation, proposal.principal)
            need(previous is None or previous == original,
                 "R5C1A_INTENT_CONFLICT")
            if previous is None:
                preflight()
                try:
                    permitted = self.authorize(proposal) is True
                except Exception:
                    raise ProductPrRefused("R5C1A_GRANT_UNKNOWN") from None
                need(permitted, "R5C1A_GRANT_REQUIRED")
                preflight()
                try:
                    self.journal.create(key, original)
                except Exception:
                    # A journal write can fail AFTER persistence.
                    raise ProductPrUncertain("R5C1A_INTENT_FSYNC_UNKNOWN") from None
                # Journal fsync may have blocked, and the Host/Issue/ref
                # grant may have expired or drifted. Recheck the SAME
                # approved operation after persistence, before any effect.
                try:
                    preflight()
                except Exception:
                    raise ProductPrUncertain("R5C1A_INTENT_EFFECT_BOUNDARY_UNKNOWN") from None
                if time.monotonic() >= deadline:
                    raise ProductPrUncertain("R5C1A_INTENT_DEADLINE_UNKNOWN")
                try:
                    effect_callback(proposal)
                except Exception:
                    pass  # lost ACK: never choose a different intent/candidate
            try:
                confirm()
            except Exception:
                raise ProductPrUncertain("R5C1A_EFFECT_RECONCILIATION_REQUIRED") from None

        # Source Task head can be the approved base (fresh R5A1 Worktree).
        # Bootstrap candidate is planned without ref mutation. Its OID is
        # journaled BEFORE applying a local empty commit/ref update.
        first = facts()
        owner = first.principal

        # Recovery is a *whole first-lifecycle stream*, not three unrelated
        # journals. Read every durable stage BEFORE planning any candidate,
        # granting a new effect or moving the Task branch. A pending push for
        # H1 may not be bypassed by resetting the Worktree to the base and
        # bootstrapping a contradictory H2 on retry.
        old_boot = saved("bootstrap", owner)
        old_push = saved("push", owner)
        old_draft = saved("draft-pr", owner)
        for stage, prior in (("bootstrap", old_boot),
                             ("push", old_push), ("draft-pr", old_draft)):
            if prior is None:
                continue
            prior_effect = intent(
                stage, prior.get("expected_head"), prior.get("subject"),
                prior.get("expected_remote"), owner)
            need(prior == _receipt(prior_effect),
                 "R5C1A_INTENT_CONFLICT")
        if old_boot is not None:
            need(old_boot["expected_head"] == base_revision
                 and old_boot["expected_remote"] is None,
                 "R5C1A_INTENT_CONFLICT")
            # Any following journal proves the bootstrap had completed
            # before the next stage. A reset back to base is not recovery.
            if old_push is not None or old_draft is not None:
                need(head == old_boot["subject"],
                     "R5C1A_INTENT_CONFLICT")
        if old_push is not None:
            need(old_push["subject"] != base_revision
                 and old_push["subject"] == head
                 and old_push["expected_head"] == head
                 and (old_boot is None
                      or old_push["subject"] == old_boot["subject"]),
                 "R5C1A_INTENT_CONFLICT")
        if old_draft is not None:
            need(old_draft["subject"] != base_revision
                 and old_draft["subject"] == head
                 and old_draft["expected_head"] == head
                 and old_draft["expected_remote"] == head
                 and (old_push is None
                      or old_draft["subject"] == old_push["subject"])
                 and (old_boot is None
                      or old_draft["subject"] == old_boot["subject"]),
                 "R5C1A_INTENT_CONFLICT")
        if head == base_revision or old_boot is not None:
            if old_boot is not None:
                candidate = old_boot.get("subject")
                need(_oid(candidate), "R5C1A_BOOTSTRAP_INTENT_INVALID")
                need(head in (base_revision, candidate),
                     "R5C1A_INTENT_CONFLICT")
                bootstrap_effect = intent(
                    "bootstrap", base_revision, candidate, None, owner)
                need(old_boot == _receipt(bootstrap_effect),
                     "R5C1A_INTENT_CONFLICT")
            else:
                need(head == base_revision, "R5C1A_BOOTSTRAP_NOT_REQUIRED")
                need(first.remote_state == "ABSENT" and
                     not task_pulls(first), "R5C1A_BOOTSTRAP_REMOTE_CONFLICT")
                check(head, tree)
                try:
                    candidate_obj = self.plan_bootstrap(req, tree)
                except Exception:
                    raise ProductPrRefused("R5C1A_BOOTSTRAP_PLAN_UNAVAILABLE") from None
                need(type(candidate_obj) is BootstrapCandidate and
                     _oid(candidate_obj.subject) and
                     candidate_obj.subject != base_revision and
                     candidate_obj.tree == tree,
                     "R5C1A_BOOTSTRAP_PLAN_INVALID")
                candidate = candidate_obj.subject
                bootstrap_effect = intent(
                    "bootstrap", base_revision, candidate, None, owner)
            # If head already equals candidate, effect_once only reconciles
            # an existing exact intent; if still base, do not blindly retry.
            def confirm_bootstrap() -> None:
                check(candidate, tree)
                need(git(target, "rev-list", "--parents", "-n", "1",
                         candidate).split() == [candidate, base_revision] and
                     git(target, "rev-parse", candidate + "^{tree}") == tree,
                     "R5C1A_BOOTSTRAP_HISTORY_INVALID")
                need(facts() == first, "R5C1A_BOOTSTRAP_REMOTE_CHANGED")
            effect_once(
                bootstrap_effect, self.bootstrap,
                lambda: (check(base_revision, tree),
                         need(facts() == first, "R5C1A_BOOTSTRAP_REMOTE_CHANGED")),
                confirm_bootstrap)
            head = candidate

        check(head, tree)
        initial = facts()
        need(initial.principal == owner, "R5C1A_PRINCIPAL_CHANGED")
        existing = task_pulls(initial)
        need(len(existing) <= 1, "R5C1A_PR_CONFLICT")
        if existing:
            exact_pr(existing[0], head, owner)
        # Ref is either positively absent (first non-force push) or
        # already exactly published. A mismatched remote ref is never reset.
        need(initial.remote_head is None or initial.remote_head == head,
             "R5C1A_REMOTE_REF_CONFLICT")
        # The whole-stream scan above already pinned every prior intent.
        # A new unexpected intent written after that scan must not be
        # interpreted as an approved continuation.
        need(saved("push", owner) == old_push,
             "R5C1A_INTENT_CONFLICT")
        if initial.remote_head != head or old_push is not None:
            proposal = (intent("push", head, head, initial.remote_head, owner)
                        if old_push is None
                        else intent("push", head, head,
                                    old_push.get("expected_remote"), owner))
            def confirm_push() -> None:
                check(head, tree)
                remote = facts()
                need(remote.remote_head == head and
                     (not task_pulls(remote) or
                      (len(task_pulls(remote)) == 1 and
                       task_pulls(remote) == task_pulls(initial))),
                     "R5C1A_PUSH_POSTCONDITION_INVALID")
            effect_once(
                proposal, self.push_branch,
                lambda: (check(head, tree),
                         need(facts() == initial, "R5C1A_PUSH_FACTS_DRIFT")),
                confirm_push)
        confirmed = facts()
        need(confirmed.remote_head == head,
             "R5C1A_REMOTE_BRANCH_UNCONFIRMED")
        before_pr = task_pulls(confirmed)
        need(len(before_pr) <= 1, "R5C1A_PR_CONFLICT")
        if before_pr:
            exact_pr(before_pr[0], head, owner)
        need(saved("draft-pr", owner) == old_draft,
             "R5C1A_INTENT_CONFLICT")
        if not before_pr or old_draft is not None:
            proposal = intent("draft-pr", head, head, head, owner)
            def confirm_draft() -> None:
                check(head, tree)
                completed = facts()
                need(completed.remote_head == head and
                     len(task_pulls(completed)) == 1,
                     "R5C1A_DRAFT_POSTCONDITION_INVALID")
                exact_pr(task_pulls(completed)[0], head, owner)
            effect_once(
                proposal, self.create_draft,
                lambda: (check(head, tree),
                         need(facts() == confirmed, "R5C1A_PR_FACTS_DRIFT")),
                confirm_draft)

        # Repeat ALL final authority and Git/remote/PR proofs. A positive
        # Draft PR is only the PR-first surface, not a v2 checkpoint/launch.
        final = facts()
        check(head, tree)
        need(final.remote_head == head and final.principal == owner
             and len(task_pulls(final)) == 1,
             "R5C1A_FINAL_PR_UNCONFIRMED")
        selected_pr = task_pulls(final)[0]
        exact_pr(selected_pr, head, owner)
        check(head, tree)
        try:
            rd._verify_lease(project)
        except V2MainObservationRefused:
            raise ProductPrUncertain("R5C1A_FINAL_ATTACHMENT_UNKNOWN") from None
        return {
            "status": "pr_first_draft_established",
            "metadata_version": 2, "repository": repository, "issue": issue,
            "branch_ref": branch_ref, "subject": head,
            "draft_pr": selected_pr.number, "pr_state": "OPEN_DRAFT",
            "checkpoint": "NOT_PUBLISHED", "kickoff": "NOT_SENT",
            "task_execution_authorized": False,
            "session_started": False, "prompt_sent": False,
            "metadata_write_authorized": False,
            "github_write_authorized": False,
        }
