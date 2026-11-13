"""#291 R5C1B1: opt-in Main-owned canonical v2 initial publication only.

No transport, codec, Task launch or CLI. The independently authenticated Host
adapters retain their own grants/CAS/stream lease. This coordinator adds one
#140 create-once ORIGINAL PLAN, not another authority store. Recovery never
captures today's view or substitutes today's Issue tip for the original one.
"""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

if __package__:
    from .kagari_vnext_main_v2_pr_first_observation import (
        V2PrFirstReadOnlyHost, PrFirstRequest, PrFirstFacts)
    from .kagari_vnext_main_v2_pr_first_publication import (
        ProductRequest, ProductPrFacts, ProductPull)
    from .kagari_vnext_task_binding import git, worktree_registered
else:
    from kagari_vnext_main_v2_pr_first_observation import (
        V2PrFirstReadOnlyHost, PrFirstRequest, PrFirstFacts)
    from kagari_vnext_main_v2_pr_first_publication import (
        ProductRequest, ProductPrFacts, ProductPull)
    from kagari_vnext_task_binding import git, worktree_registered

# #337 nominal identities MUST remain top-level, never tools.* aliases.
from kagari_metadata_v2_issue_publisher import (
    IssuePublisher, IssueWrite, IssueWriteResult, _key as issue_write_key)
from kagari_metadata_v2_pr_checkpoint import (
    V2PRCheckpoint, ExactCheckpointReader, SourceOnlyCheckpointPublisher,
    CommentFacts, ConfirmedCheckpoint, PullFacts as CheckpointPull,
    render_checkpoint, positive)
from kagari_metadata_v2_task_admission import CurrentTask
from kagari_vnext_trusted_host import RecoveryJournal
from kagari_vnext_main_v2_provenance import (
    D2P0HostGateway, D2P0Rejected, OriginalOperation,
    check_claim, check_ack)
from task_collaboration import RepositoryFacts, PullFacts
from task_view import encode_snapshot, decode_snapshot

_OPENERS = ("<!-- kagari-pr-checkpoint", "<!-- kagari-v2-initial-checkpoint",
            "<!-- agentcore-task-checkpoint")
_FLAGS = ("task_execution_authorized", "worktree_mutation_authorized",
          "github_write_authorized", "metadata_write_authorized",
          "model_turn_authorized", "session_authorized", "ask_authorized",
          "session_started", "prompt_sent")


class InitialCheckpointRefused(ValueError):
    """UNCHANGED: this invocation has not entered a possible effect boundary."""
    state = "UNCHANGED"

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class InitialCheckpointUncertain(InitialCheckpointRefused):
    """Keep ALL journals; no automatic repair, fresh candidate or repost."""
    state = "UNCERTAIN"


def need(ok: bool, code: str) -> None:
    if not ok:
        raise InitialCheckpointRefused(code)


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _key(repository_id: int, issue: int) -> str:
    # Deliberately independent of PR, actor, Record, tip and operation ID.
    # A conflicting first-lifecycle proposal must collide with the old plan.
    return hashlib.sha256(b"kagari-v2-main-initial-plan/v1\n" +
                          _json([repository_id, issue])).hexdigest()


def _local(project: Path, issue: int, branch: str, base: str) -> dict:
    target = project / ".worktrees" / str(issue)
    need(target.resolve(strict=True) == target and not target.is_symlink(),
         "R5C1B1_WORKTREE_INVALID")
    worktree_registered(project, target, branch)
    head = git(target, "rev-parse", "HEAD")
    tree = git(target, "rev-parse", "HEAD^{tree}")
    need(all(re.fullmatch(r"[0-9a-f]{40}", v) for v in (head, tree))
         and head != base
         and not git(target, "status", "--porcelain=v1", "-uall",
                     "--ignore-submodules=none"), "R5C1B1_PRODUCT_NOT_PRISTINE")
    git(target, "merge-base", "--is-ancestor", base, head)
    gitdir = Path(git(target, "rev-parse", "--absolute-git-dir"))
    need(gitdir.resolve(strict=True) == gitdir, "R5C1B1_WORKTREE_INVALID")
    identities = []
    for path, directory in ((target, True), (target / ".git", False), (gitdir, True)):
        info = path.lstat()
        need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)),
             "R5C1B1_WORKTREE_INVALID")
        identities.append([info.st_dev, info.st_ino])
    return {"project": str(project), "worktree": str(target), "git_dir": str(gitdir),
            "identities": identities, "head": head, "tree": tree}


@dataclass(frozen=True, slots=True)
class V2InitialCheckpointHost:
    """Trusted Python installation only; construction itself grants nothing.

    read_product is the R5C1A authenticated branch/PR reader (including author,
    title and body). proof.read_facts and checkpoint's inventory/pull readers
    are independently bound authenticated observations, not caller PR claims.
    read_view is used ONLY for a new plan. Host callbacks must bound their I/O;
    synchronous Python cannot preempt a blocked callback. No permission is
    inferred from the plan, a complete=True flag or an empty inventory.
    """
    proof: V2PrFirstReadOnlyHost
    metadata: IssuePublisher
    checkpoint: SourceOnlyCheckpointPublisher
    read_product: Callable[[ProductRequest], ProductPrFacts]
    read_view: Callable[[PrFirstRequest], dict]
    journal: RecoveryJournal
    operation_id: str
    report: str
    provenance: D2P0HostGateway | None = None

    @property
    def readonly(self):
        return self.proof.readonly

    def __post_init__(self) -> None:
        need(type(self.proof) is V2PrFirstReadOnlyHost
             and type(self.metadata) is IssuePublisher
             and type(self.checkpoint) is SourceOnlyCheckpointPublisher
             and type(self.journal) is RecoveryJournal
             and type(self.metadata.journal) is RecoveryJournal
             and type(self.checkpoint._journal) is RecoveryJournal
             and callable(self.read_product) and callable(self.read_view)
             and type(self.operation_id) is str
             and re.fullmatch(r"[0-9a-f]{32}", self.operation_id) is not None,
             "R5C1B1_HOST_INVALID")
        rd, meta, exact = self.readonly, self.metadata, self.proof.checkpoint
        cur = rd.current_reader
        need(self.checkpoint._exact is exact and meta.lease is rd.lease
             and meta.repository_identity == cur.identity
             and meta.registered == cur.registered and meta.approved == cur.approved
             and meta.current.identity == cur.identity
             and meta.current.registered == cur.registered
             and meta.current.approved == cur.approved
             and meta.identity.attach_digest == rd.selection.attach_digest,
             "R5C1B1_HOST_BINDING_MISMATCH")

    def publish(self, *, project: Path, repository: str, issue: int,
                branch_ref: str, base_revision: str, contract_id: str,
                read_issue: Callable[[int], dict], main_owner: object) -> dict:
        # A caller-supplied no-op owner callback is not publication authority.
        # Load lazily to avoid #337 bootstrap -> trusted Host -> Main cycles.
        if __package__:
            from .kagari_vnext_main_service import MainSupervisor, RepositoryMain, OPENCODE
        else:
            from kagari_vnext_main_service import MainSupervisor, RepositoryMain, OPENCODE
        need(type(main_owner) is MainSupervisor, "R5C1B1_OWNER_REQUIRED")
        main = main_owner.main
        need(type(main) is RepositoryMain, "R5C1B1_OWNER_REQUIRED")
        with main._main_owner_guard:
            choice = main._selected_choice(issue)

            def check_owner():
                need(main_owner.publication_only is True
                     and main_owner._attachment_lease is self.readonly.lease
                     and main.v2_initial_checkpoint_for_issue.get(issue) is self
                     and main.v2_readonly_for_issue.get(issue) is self.readonly
                     and main.read_issue is read_issue
                     and (main.store.root, main.store.repository) == (project, repository)
                     and main._selected_choice(issue) == choice
                     and (choice.branch_ref, choice.base_revision, choice.contract_id,
                          choice.executor) == (branch_ref, base_revision, contract_id, OPENCODE),
                     "R5C1B1_OWNER_BINDING_MISMATCH")
                main_owner._assert_owned(main)

            return self._publish_owned(
                project=project, repository=repository, issue=issue,
                branch_ref=branch_ref, base_revision=base_revision, contract_id=contract_id,
                read_issue=read_issue, check_main_owner=check_owner,
                main_owner=main_owner)

    def _publish_owned(self, *, project: Path, repository: str, issue: int,
                       branch_ref: str, base_revision: str, contract_id: str,
                       read_issue: Callable[[int], dict],
                       check_main_owner: Callable[[], None],
                       main_owner: object) -> dict:
        """Called under MainSupervisor's canonical singleton for the entire lane.

        Typed UNCHANGED/UNCERTAIN failures never remove/rewrite a journal.
        Even success is V2_MAIN_BLOCKED_LIVE, NOT permission to run a Task.
        """
        # Do not let a direct call to this helper bypass the real owner.
        if __package__:
            from .kagari_vnext_main_service import MainSupervisor, RepositoryMain, OPENCODE
        else:
            from kagari_vnext_main_service import MainSupervisor, RepositoryMain, OPENCODE
        need(type(main_owner) is MainSupervisor and
             type(main_owner.main) is RepositoryMain,
             "R5C1B1_OWNER_REQUIRED")
        main = main_owner.main
        with main._main_owner_guard:
            need(main._main_owner is main_owner and main_owner._entered and
                 main_owner.publication_only is True and
                 main_owner._attachment_lease is self.readonly.lease and
                 main_owner._initial_publish_active == 1 and
                 main_owner._initial_publish_issue == issue and
                 main_owner._initial_publish_host is self and
                 main.v2_initial_checkpoint_for_issue.get(issue) is self and
                 main.v2_readonly_for_issue.get(issue) is self.readonly and
                 main.read_issue is read_issue and
                 (main.store.root, main.store.repository) == (project, repository),
                 "R5C1B1_OWNER_REQUIRED")
            selected = main._selected_choice(issue)
            need((selected.branch_ref, selected.base_revision,
                  selected.contract_id, selected.executor) ==
                 (branch_ref, base_revision, contract_id, OPENCODE),
                 "R5C1B1_SELECTED_CHOICE_MISMATCH")
            main_owner._assert_owned(main)
            need(main_owner._initial_publish_entered is False,
                 "R5C1B1_REENTRANT_PUBLICATION")
            # The outer Main route owns cleanup in its finally. Keeping this
            # flag until outer exit blocks nested same-Issue helper calls.
            main_owner._initial_publish_entered = True
        # D2-P0 default is deliberately non-effectful: the source-only
        # caller cannot manufacture a credential-bearing Host trust root.
        # This opt-in gateway must be separately qualified before activation.
        pending = True  # journal not yet read: no claim of UNCHANGED
        try:
            if type(self.provenance) is not D2P0HostGateway:
                raise InitialCheckpointUncertain(
                    "R5C1B1_D2_PROVENANCE_HOST_REQUIRED")
            gateway = self.provenance
            self.__post_init__()
            rd = self.readonly
            cur = rd.current_reader
            reg = cur.registered
            need(type(issue) is int and isinstance(project, Path)
                 and (repository, issue, branch_ref, base_revision, contract_id) ==
                 (reg.repository, reg.issue, reg.branch_ref, reg.base_revision, reg.contract_id)
                 and callable(read_issue) and callable(check_main_owner),
                 "R5C1B1_TASK_BINDING_MISMATCH")
            deadline = time.monotonic() + 90
            args = dict(project=project, repository=repository, issue=issue,
                        branch_ref=branch_ref, base_revision=base_revision,
                        contract_id=contract_id, read_issue=read_issue)

            def tick():
                need(time.monotonic() < deadline, "R5C1B1_DEADLINE_EXCEEDED")
                with main._main_owner_guard:
                    selected = main._selected_choice(issue)
                    need(main._main_owner is main_owner and
                         main_owner._initial_publish_active == 1 and
                         main_owner._initial_publish_issue == issue and
                         main_owner._initial_publish_host is self and
                         (selected.branch_ref, selected.base_revision,
                          selected.contract_id, selected.executor) ==
                         (branch_ref, base_revision, contract_id, OPENCODE),
                         "R5C1B1_OWNER_REQUIRED")
                    main_owner._assert_owned(main)
                check_main_owner()
                rd._verify_lease(project)

            tick()
            key = _key(reg.repository_id, issue)
            # A failed/partial read is uncertainty, never proof of absence.
            try:
                saved = self.journal.read(key)
            except Exception:
                raise InitialCheckpointUncertain("R5C1B1_PLAN_UNREADABLE") from None
            pending = saved is not None
            recovering = saved is not None
            tick()
            rd.observe(**args)
            original_binding = {
                "schema": 1, "operation": "v2-main-initial-checkpoint",
                "identity": asdict(cur.identity), "registered": asdict(reg),
                "approved": asdict(cur.approved), "selection": asdict(rd.selection),
                "operation_id": self.operation_id, "report": self.report,
            }
            if saved is not None:
                need(type(saved) is dict and saved.keys() == original_binding.keys() | {
                    "current", "local", "product", "facts", "snapshot_id", "snapshot_b64"}
                     and _json({k: saved[k] for k in original_binding}) == _json(original_binding),
                     "R5C1B1_PLAN_CONFLICT")
                original = CurrentTask(**saved["current"])
                local = saved["local"]
            else:
                original = rd._current()
                local = _local(project, issue, branch_ref, base_revision)
            need(type(original) is CurrentTask and
                 (original.repository_id, original.repository, original.issue,
                  original.record_id, original.contract_id, original.branch_ref,
                  original.base_revision) ==
                 (reg.repository_id, repository, issue, reg.record_id,
                  contract_id, branch_ref, base_revision), "R5C1B1_PLAN_CONFLICT")
            subject = local["head"]
            req = PrFirstRequest(
                "v2-main-initial-checkpoint", reg.repository_id, repository, issue,
                branch_ref, self.proof.expected_base_ref, base_revision,
                reg.record_id, contract_id, original.metadata_tip,
                original.archive_tip, subject)
            product_req = ProductRequest(
                req.repository_id, repository, issue, branch_ref, req.base_ref,
                base_revision, reg.record_id, contract_id,
                original.archive_tip, original.metadata_tip)
            # Capture installed capabilities, never mutate shared publisher objects.
            meta, cp, exact = self.metadata, self.checkpoint, self.proof.checkpoint
            pull_read, inventory = cp._pull, cp._inventory
            expected_tips = {original.metadata_tip}
            desired = None
            if saved is not None:
                raw = base64.b64decode(saved["snapshot_b64"], validate=True)
                snapshot_id = saved["snapshot_id"]
                op = IssueWrite(self.operation_id, original.archive_tip,
                                original.metadata_tip, (raw,))
                # Reuse #337's exact receipt validator, not a second receipt codec.
                receipt = meta._checked_receipt(
                    op, (snapshot_id,), issue_write_key(reg.repository_id, issue,
                                                       original.metadata_tip))
                if receipt is None:
                    raise InitialCheckpointUncertain("R5C1B1_METADATA_RECEIPT_MISSING")
                expected_tips.add(receipt["candidate"])

            def anchor():
                tick()
                try:
                    intact = self.journal.read(key) == saved
                except Exception:
                    raise InitialCheckpointUncertain("R5C1B1_PLAN_UNREADABLE") from None
                if not intact:
                    raise InitialCheckpointUncertain("R5C1B1_PLAN_CHANGED")
                self.__post_init__()
                live_issue = read_issue(issue)
                need(type(live_issue) is dict and type(live_issue.get("number")) is int
                     and (live_issue.get("repository"), live_issue["number"],
                          live_issue.get("state"), live_issue.get("title"), live_issue.get("body")) ==
                     (repository, issue, "OPEN", cur.approved.title, cur.approved.body),
                     "R5C1B1_ISSUE_CHANGED")
                current = rd._current()
                need(current.metadata_tip in expected_tips
                     and replace(current, metadata_tip=original.metadata_tip) == original,
                     "R5C1B1_CURRENT_CHANGED")
                need(_local(project, issue, branch_ref, base_revision) == local,
                     "R5C1B1_WORKTREE_CHANGED")
                tick()

            def comments(items, pr, *, absent=False):
                need(type(items) is tuple and len(items) <= 100,
                     "R5C1B1_INVENTORY_INVALID")
                total, last = 0, 0
                owned = []
                for item in items:
                    need(type(item) is CommentFacts and all(positive(n) for n in (
                        item.repository_id, item.pr_number, item.comment_id, item.author_id))
                         and (item.repository_id, item.repository, item.pr_number, item.kind) ==
                         (reg.repository_id, repository, pr.number, "pr")
                         and item.comment_id > last and type(item.body) is str,
                         "R5C1B1_INVENTORY_INVALID")
                    total += len(item.body.encode("utf-8", "strict"))
                    need(total <= 8 * 1024 * 1024, "R5C1B1_INVENTORY_LIMIT")
                    last = item.comment_id
                    if item.body.startswith(_OPENERS):
                        owned.append(item)
                # Conservative initial-only MVI: even a foreign reserved opener
                # blocks a new effect. Ordinary human discussion is not authority.
                need(len(owned) <= 1 and (not owned or (not absent and desired is not None
                     and owned[0].author_id == exact.principal
                     and owned[0].body == render_checkpoint(desired))),
                     "R5C1B1_INITIAL_CONFLICT")
                return tuple(item for item in items if item not in owned)

            def facts(*, absent=False, cutoff=None):
                cutoff = min(deadline, deadline if cutoff is None else cutoff)
                need(time.monotonic() < cutoff, "R5C1B1_DEADLINE_EXCEEDED")
                anchor()
                product = self.read_product(product_req)
                anchor()
                need(type(product) is ProductPrFacts
                     and type(product.repository) is RepositoryFacts
                     and product.repository == RepositoryFacts(repository, req.base_ref, reg.repository_id)
                     and positive(product.principal) and product.principal == exact.principal
                     and product.remote_state == "PRESENT" and product.remote_head == subject
                     and product.complete is True and type(product.pulls) is tuple
                     and len(product.pulls) == 1 and type(product.pulls[0]) is ProductPull,
                     "R5C1B1_PRODUCT_FACTS_INVALID")
                pr = product.pulls[0]
                need(positive(pr.number) and positive(pr.author_id)
                     and pr.author_id == product.principal
                     and (pr.repository, pr.head_repository, pr.base_ref, pr.head_ref,
                          pr.head_oid, pr.state, pr.draft, pr.title, pr.body) ==
                     (repository, repository, req.base_ref.removeprefix("refs/heads/"),
                      branch_ref.removeprefix("refs/heads/"), subject, "open", True,
                      cur.approved.title, cur.approved.body) and pr.draft is True,
                     "R5C1B1_PR_CHANGED")
                observed = self.proof.read_facts(req)
                anchor()
                projection = PullFacts(pr.number, pr.repository, pr.head_repository,
                                       pr.base_ref, pr.head_ref, pr.head_oid, pr.state,
                                       pr.draft, pr.title, pr.body)
                need(type(observed) is PrFirstFacts
                     and type(observed.repository) is RepositoryFacts
                     and observed.repository == product.repository
                     and positive(observed.principal) and observed.principal == product.principal
                     and observed.remote_head == subject and observed.complete is True
                     and type(observed.pulls) is tuple and len(observed.pulls) == 1
                     and type(observed.pulls[0]) is PullFacts
                     and _json(asdict(observed.pulls[0])) == _json(asdict(projection)),
                     "R5C1B1_FACTS_DISAGREE")
                ordinary = comments(observed.comments, pr, absent=absent)
                independent_pull = pull_read(pr.number, cutoff)
                anchor()
                need(type(independent_pull) is CheckpointPull
                     and _json(asdict(independent_pull)) == _json(asdict(CheckpointPull(
                         reg.repository_id, repository, pr.number, req.base_ref,
                         branch_ref, subject, "open", True, exact.principal))),
                     "R5C1B1_FACTS_DISAGREE")
                independent_comments = inventory(pr.number, cutoff)
                anchor()
                comments(independent_comments, pr, absent=absent)
                need(independent_comments == observed.comments, "R5C1B1_FACTS_DISAGREE")
                if saved is not None:
                    old_facts = {**asdict(observed), "comments": [asdict(c) for c in ordinary]}
                    need(_json(asdict(product)) == _json(saved["product"])
                         and _json(old_facts) == _json(saved["facts"]),
                         "R5C1B1_ORIGINAL_FACTS_CHANGED")
                need(time.monotonic() < cutoff, "R5C1B1_DEADLINE_EXCEEDED")
                return product, observed

            def capsule(candidate, sid, pr_number):
                return V2PRCheckpoint(
                    2, reg.repository_id, repository, issue, pr_number, exact.principal,
                    branch_ref, req.base_ref, subject, candidate, reg.record_id,
                    contract_id, sid, "initial", "initial", self.report)

            if saved is not None:
                desired = capsule(receipt["candidate"], snapshot_id,
                                  saved["product"]["pulls"][0]["number"])
            product, observed = facts(absent=saved is None)
            if saved is None:
                view = self.read_view(req)
                anchor()
                # Do NOT call TaskViewSnapshots.capture(MetadataStore) for v2.
                snapshot_id, raw = encode_snapshot(
                    repository, str(issue), subject, boundary="explicit-handoff", view=view)
            snapshot = decode_snapshot(raw, snapshot_id=snapshot_id, repository=repository,
                                       task=str(issue), subject=subject)
            view = snapshot["view"]
            pr = product.pulls[0]
            target_b64 = base64.b64encode(os.fsencode(local["worktree"])).decode("ascii")
            target_entries = [w for w in view["git"]["worktrees"] if w["path_b64"] == target_b64]
            need(snapshot["boundary"] == "explicit-handoff"
                 and view["branch_ref"] == branch_ref
                 and view["authority"] == {
                     "state": "observed", "metadata_commit": original.metadata_tip,
                     "record_id": reg.record_id, "contract_id": contract_id,
                     "base_revision": base_revision, "branch_ref": branch_ref, "disposition": None}
                 and view["previous_checkpoint"] == {"state": "absent"}
                 and view["git"]["tree"] == local["tree"]
                 and view["git"]["branch_matches_task"] is True
                 and not any(view["git"]["status"].values())
                 and view["git"]["staged_gitlinks"] == 0
                 and view["git"]["remote_head"] == {"state": "observed", "head_oid": subject}
                 and target_entries == [{"path_b64": target_b64, "head": subject,
                                         "branch_ref": branch_ref}]
                 and view["github"] == {"state": "observed", "pull_request": {
                     "base_repository": repository, "head_repository": repository,
                     "number": pr.number, "state": "open", "draft": True,
                     "base_ref": pr.base_ref, "head_ref": pr.head_ref, "head_oid": subject}},
                 "R5C1B1_SNAPSHOT_PROJECTION_INVALID")
            need(view["evidence"] == [] and view["git"]["default_branch"] == {"state": "not_requested"},
                 "R5C1B1_OPTIONAL_VIEW_DEFERRED")
            # Validate immutable capsule parameters before ANY metadata effect.
            render_checkpoint(capsule(original.metadata_tip, snapshot_id, pr.number))
            if saved is None:
                proposal = {**original_binding, "current": asdict(original), "local": local,
                            "product": asdict(product), "facts": asdict(observed),
                            "snapshot_id": snapshot_id,
                            "snapshot_b64": base64.b64encode(raw).decode("ascii")}
                need(len(_json(proposal)) <= 16384, "R5C1B1_PLAN_SIZE_DEFERRED")
                proposal = json.loads(_json(proposal))  # detach/normalize journal JSON arrays
                need(facts(absent=True) == (product, observed), "R5C1B1_PREFLIGHT_CHANGED")
            # All Host verification below is source-only contract checking:
            # only a real credential-bearing Host may assert these witnesses.
            # Do not issue any new Metadata write, Host claim or checkpoint
            # POST if its authorization/stream ownership is not positive.
            scope = OriginalOperation.from_plan(
                repository_id=reg.repository_id, repository=repository,
                issue=issue, operation_id=self.operation_id,
                contract_id=contract_id, pr_number=pr.number,
                principal=exact.principal, subject=subject,
                snapshot_id=snapshot_id,
                plan=saved if recovering else proposal)
            try:
                entry_claim = check_claim(scope, gateway.assert_entry(scope))
            except D2P0Rejected as error:
                raise InitialCheckpointUncertain(error.code) from error
            if saved is None:
                pending = True  # create may fail AFTER fsync
                self.journal.create(key, proposal)
                saved = proposal
                need(self.journal.read(key) == proposal, "R5C1B1_PLAN_READBACK_FAILED")
            try:
                held_claim = check_claim(
                    scope, gateway.establish_claim(scope, recovering))
                if held_claim != entry_claim:
                    raise D2P0Rejected("D2P0_HOST_EPOCH_CHANGED")
            except D2P0Rejected as error:
                raise InitialCheckpointUncertain(error.code) from error
            facts()
            op = IssueWrite(self.operation_id, original.archive_tip, original.metadata_tip, (raw,))

            def budget(fn):
                # #337 owns bounded object/ref graph validation. These callbacks
                # are read-only; full cross-system checks surround each facade
                # and every grant/effect, without recursively reading the graph.
                def bounded(*values):
                    cutoff = min(deadline, values[-1])
                    need(time.monotonic() < cutoff, "R5C1B1_DEADLINE_EXCEEDED")
                    result = fn(*values[:-1], cutoff)
                    need(time.monotonic() < cutoff, "R5C1B1_DEADLINE_EXCEEDED")
                    return result
                return bounded

            def guarded(fn, *, absent=False):
                def call(*values):
                    cutoff = (min(deadline, values[-1])
                              if values and type(values[-1]) in (int, float) else deadline)
                    rd.observe(**args)  # independent read/legacy grant is still live
                    facts(absent=absent, cutoff=cutoff)
                    try:
                        return fn(*values)
                    finally:
                        facts(cutoff=cutoff)
                return call

            def checked_read(fn):
                def call(*values):
                    try:
                        return fn(*values)
                    finally:
                        # #337 compares PR/comment reads before and after its
                        # traversals. Recheck current Task/Issue/local ownership
                        # after each callback without recursively reading it.
                        anchor()
                return call

            def metadata_write(update, bundle, cutoff):
                need(not recovering, "R5C1B1_RECOVERY_ONLY")
                rd.observe(**args)
                facts(absent=True, cutoff=cutoff)
                need(update.expected_archive == original.archive_tip
                     and update.expected_issue == original.metadata_tip
                     and self.journal.read(key) == saved,
                     "R5C1B1_EFFECT_PLAN_CHANGED")
                tick()
                need(time.monotonic() < min(cutoff, deadline), "R5C1B1_DEADLINE_EXCEEDED")
                # Only this canonical candidate may be observed across the
                # effect boundary. The facade must STILL positively confirm it.
                expected_tips.add(bundle.commit)
                try:
                    return meta.writer(update, bundle, min(cutoff, deadline))
                finally:
                    facts(cutoff=cutoff)

            def metadata_grant(approval):
                need(not recovering, "R5C1B1_RECOVERY_ONLY")
                return guarded(meta.authorize, absent=True)(approval)

            bounded_meta = IssuePublisher(
                meta.identity, meta.repository_identity, reg, cur.approved,
                lease=rd.lease, reader=budget(meta.reader), observer=budget(meta.observer),
                journal=meta.journal, authorize=metadata_grant,
                writer=metadata_write, limits=meta.limits)
            if recovering:
                need(meta._checked_receipt(op, (snapshot_id,), issue_write_key(
                    reg.repository_id, issue, original.metadata_tip)) == receipt,
                     "R5C1B1_METADATA_RECEIPT_CHANGED")
            result = bounded_meta.publish(op)
            need(type(result) is IssueWriteResult
                 and result.state in ("PUBLISHED", "RECOVERED")
                 and result.previous_issue_commit == original.metadata_tip
                 and result.record_id == reg.record_id, "R5C1B1_METADATA_UNCONFIRMED")
            expected_tips = {result.issue_commit}
            desired = capsule(result.issue_commit, snapshot_id, pr.number)
            facts()
            bounded_exact = ExactCheckpointReader(
                exact.identity, exact.registered, base_ref=exact.base_ref,
                principal=exact.principal, reader=budget(exact._object_reader),
                observer=budget(exact._observer),
                comment_reader=budget(checked_read(exact._comment_reader)), limits=exact._limits)
            # Canonical #337 alone owns stream intent, body rendering, exact
            # readback and lost-ACK handling. No custom comment retry loop.
            def d2_host_post(number, body, cutoff):
                need(number == desired.pr_number and
                     body == render_checkpoint(desired),
                     "R5C1B1_D2_POST_BODY_MISMATCH")
                # Only the Host-controlled credential-bearing egress may
                # perform this effect; never directly expose cp._post here.
                return gateway.post(scope, desired, number, body, cutoff)

            bounded_checkpoint = SourceOnlyCheckpointPublisher(
                bounded_exact, pull_reader=budget(checked_read(pull_read)),
                inventory=budget(checked_read(inventory)),
                authorize=budget(guarded(cp._authorize, absent=True)),
                post_comment=budget(guarded(d2_host_post, absent=True)),
                journal=cp._journal,
                stream_lease=lambda value, cutoff: cp._stream_lease(value, min(cutoff, deadline)))
            confirmed = bounded_checkpoint.publish(desired, previous_comment_id=None)
            need(type(confirmed) is ConfirmedCheckpoint and confirmed.checkpoint == desired
                 and confirmed.snapshot == snapshot, "R5C1B1_CHECKPOINT_UNCONFIRMED")
            # Generic #337 can adopt an identical comment without ever
            # issuing POST. It also discards POST's return value. Therefore
            # read exact durable Host ACK independently for BOTH branches.
            try:
                check_ack(scope, held_claim, desired, confirmed.comment_id,
                          gateway.read_ack(scope, desired, confirmed.comment_id))
            except D2P0Rejected as error:
                raise InitialCheckpointUncertain(error.code) from error
            facts()
            final = self.proof.observe(**args)
            facts()
            need(final.get("status") == "blocked" and final.get("reason") == "V2_MAIN_BLOCKED_LIVE"
                 and final.get("runtime") == "NOT_STARTED" and final.get("kickoff") == "NOT_SENT"
                 and all(final.get(f) is False for f in (
                     "task_execution_authorized", "worktree_mutation_authorized",
                     "github_write_authorized", "session_started", "prompt_sent"))
                 and all(flag not in final or final[flag] is False for flag in _FLAGS)
                 and final["pr_first"]["proof"] == "PROVED"
                 and (final["pr_first"]["metadata_commit"], final["pr_first"]["snapshot_id"],
                      final["pr_first"]["comment_id"], final["pr_first"]["pull_request"]) ==
                 (result.issue_commit, snapshot_id, confirmed.comment_id, pr.number),
                 "R5C1B1_FINAL_PROOF_INVALID")
            tick()
            return {**final, "initial_checkpoint": "CONFIRMED",
                    **{flag: False for flag in _FLAGS}}
        except InitialCheckpointUncertain:
            raise
        except Exception as error:
            code = (error.code if isinstance(error, InitialCheckpointRefused)
                    else "R5C1B1_HOST_OR_CANONICAL_REFUSAL")
            failure = InitialCheckpointUncertain if pending else InitialCheckpointRefused
            raise failure(code) from error
        except BaseException:
            if pending:
                raise InitialCheckpointUncertain("R5C1B1_INTERRUPTED") from None
            raise
