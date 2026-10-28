#!/usr/bin/env python3
"""KAGARI #291 — single-repository Main, one approved Issue vertical slice.

Run a Task already authorized/frozen by #191 and explicitly selected by the
trusted Main host. Reuse #286 Worktree, #163 root Session and #292 CLI; neither
arbitrary GitHub Issue visibility nor Issue title alone grants execution.
Source-only MVI: no project-wide Issue auto discovery, release or Merge.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
import re
import subprocess
import threading
from typing import Callable
from urllib.parse import quote

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
import sys
sys.path.insert(0, str(V4))
from metadata_ref import MetadataStore, MetadataRefError
from task_record import TaskRecords, TaskRecordError
from issue_worktree import IssueWorktreeAllocator, IssueWorktreeError
from task_view import (
    observe_live_task, TaskViewError, EvidenceRef,
    GitHubPullRequestRequest,
)
from task_collaboration import (
    TaskCollaboration, TaskCollaborationError, CollaborationReceipt,
)
try:
    from .kagari_vnext_main_observation import isolated_metadata_observation
except ImportError:
    from kagari_vnext_main_observation import isolated_metadata_observation
if __package__:
    from .kagari_vnext_session import start as start_root_session, _request as opencode_request
    from .kagari_vnext_task_binding import (
        ControlError, resolve, status as task_status, worktree_registered)
    from .kagari_vnext_opencode_control import OpenCode, TaskBinding
else:
    from kagari_vnext_session import start as start_root_session, _request as opencode_request
    from kagari_vnext_task_binding import (
        ControlError, resolve, status as task_status, worktree_registered)
    from kagari_vnext_opencode_control import OpenCode, TaskBinding


DISPATCH_LOCK_TIMEOUT_SECONDS = 8.0


class MainError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class InitialCollaboration:
    """Trusted host-supplied #194 capability, not an approval flag.

    The facade must itself have installed exact operation prerequisites,
    guarded Git/GitHub transports, authorization and a durable #217 metadata
    publisher. The config/Issue/agent cannot construct a source grant.
    """
    facade: TaskCollaboration
    metadata_provider: Callable[[str, str, str, int, str], object]
    report: str


@dataclass(frozen=True)
class ApprovedTask:
    """Exact externally approved Issue / frozen Task Record inputs.

    Approval is *provided* by the trusted host. Constructing an arbitrary
    ApprovedTask inside an agent is not a grant. The actual Issue is re-read.
    """
    issue: int
    branch_ref: str
    base_revision: str
    contract_id: str


def _json_write_once(path: Path, obj: dict) -> None:
    data = (json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _read_receipt(path: Path) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > 32_768:
            raise ValueError("invalid receipt")
        obj = json.loads(path.read_text("utf-8"))
        if type(obj) is not dict:
            raise ValueError("invalid receipt")
        return obj
    except (OSError, UnicodeError, ValueError):
        raise MainError("MAIN_RECONCILIATION_REQUIRED") from None


def github_issue_reader(repository: str, *, gh: str = "gh") -> Callable[[int], dict]:
    """Real read-only GitHub Issue SoT adapter. Issue visibility is NOT approval."""
    if (type(repository) is not str
            or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository)):
        raise MainError("GITHUB_REPOSITORY_INVALID")

    def read(number: int) -> dict:
        if type(number) is not int or number < 1:
            raise MainError("GITHUB_ISSUE_INVALID")
        try:
            result = subprocess.run(
                [gh, "issue", "view", str(number), "--repo", repository,
                 "--json", "number,title,body,state"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, timeout=15,
                env={**os.environ, "GH_PROMPT_DISABLED": "1",
                     "GIT_TERMINAL_PROMPT": "0"}, check=False)
            if result.returncode or len(result.stdout) > 180_000:
                raise MainError("GITHUB_ISSUE_UNAVAILABLE")
            value = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            raise MainError("GITHUB_ISSUE_UNAVAILABLE") from None
        if not isinstance(value, dict):
            raise MainError("GITHUB_ISSUE_UNAVAILABLE")
        return {"repository": repository, **value}

    return read


class RepositoryMain:
    def __init__(
        self,
        store: MetadataStore,
        *,
        approved: dict[int, ApprovedTask],
        read_issue: Callable[[int], dict],
        runtime_home: Path,
        port_for_issue: Callable[[int], int],
        opencode_bin: str = "opencode",
        agent: str = "build",
        model: tuple[str, str] | None = None,
        pr_for_issue: dict[int, int] | None = None,
        evidence_for_issue: dict[int, tuple[EvidenceRef, ...]] | None = None,
        read_pr: Callable | None = None,
        initial_collaboration_for_issue: Callable[[int, Path], InitialCollaboration] | None = None,
    ) -> None:
        if not isinstance(store, MetadataStore) or not callable(read_issue):
            raise MainError("MAIN_CONFIGURATION_INVALID")
        self.store = store
        self.approved = approved.copy()
        self.read_issue = read_issue
        self.runtime_home = runtime_home
        self.port_for_issue = port_for_issue
        self.opencode_bin = opencode_bin
        self.agent = agent
        self.model = model
        self.pr_for_issue = dict(pr_for_issue or {})
        self.evidence_for_issue = dict(evidence_for_issue or {})
        self.read_pr = read_pr
        # Dispatch authority is a live MainSupervisor owner of the canonical
        # repository lock, never an Issue ID, process PID or plan-file flag.
        # The reentrant guard also prevents owner release during a model
        # kickoff; it is NOT a second durable Task or approval database.
        self._main_owner_guard = threading.RLock()
        self._main_owner = None
        if (initial_collaboration_for_issue is not None
                and not callable(initial_collaboration_for_issue)):
            raise MainError("MAIN_INITIAL_COLLABORATION_INVALID")
        self.initial_collaboration_for_issue = initial_collaboration_for_issue
        if (any(n not in self.approved or type(v) is not int or v < 1
                for n, v in self.pr_for_issue.items())
                or any(n not in self.approved or type(v) is not tuple
                       or any(type(e) is not EvidenceRef for e in v)
                       for n, v in self.evidence_for_issue.items())
                or (bool(self.pr_for_issue) != bool(self.read_pr))
                or (self.read_pr is not None and not callable(self.read_pr))):
            raise MainError("MAIN_OBSERVATION_INVALID")
        if not self.approved or any(
            type(n) is not int or type(p) is not ApprovedTask or p.issue != n
            for n, p in self.approved.items()
        ):
            raise MainError("ELIGIBLE_ISSUES_REQUIRED")

    def _spec(
        self, issue: int, *, metadata_store: MetadataStore | None = None,
    ) -> tuple[ApprovedTask, dict]:
        if type(issue) is not int or issue not in self.approved:
            raise MainError("ISSUE_NOT_APPROVED")
        choice = self.approved[issue]
        store = metadata_store if metadata_store is not None else self.store
        try:
            tip = store.fetch_tip()
            if tip is None:
                raise MainError("TASK_RECORD_ABSENT")
            entry = TaskRecords(store, authorize=lambda _req: False).read(
                tip, task=str(issue), branch_ref=choice.branch_ref,
                base_revision=choice.base_revision)
        except (MetadataRefError, TaskRecordError, TypeError, ValueError):
            raise MainError("TASK_RECORD_INVALID") from None
        if entry is None:
            raise MainError("TASK_RECORD_ABSENT")
        _, record, contract = entry
        if "disposition" in record["payload"]:
            raise MainError("TASK_RETIRED")
        if record["payload"]["contract_id"] != choice.contract_id:
            raise MainError("TASK_CONTRACT_CHANGED")
        snapshot = contract["payload"]
        try:
            facts = self.read_issue(issue)
        except Exception:
            raise MainError("GITHUB_ISSUE_UNAVAILABLE") from None
        if (type(facts) is not dict or type(facts.get("number")) is not int
                or facts["number"] != issue
                or facts.get("repository") != self.store.repository
                or facts.get("state") != "OPEN"
                or facts.get("title") != snapshot["title"]
                or facts.get("body") != snapshot["body"]):
            raise MainError("ISSUE_SNAPSHOT_DRIFT")
        return choice, snapshot

    def _runtime(self, issue: int) -> Path:
        if not self.runtime_home.is_absolute() or self.runtime_home.is_symlink():
            raise MainError("MAIN_RUNTIME_INVALID")
        if self.runtime_home.exists() and self.runtime_home.resolve(strict=True) != self.runtime_home:
            raise MainError("MAIN_RUNTIME_INVALID")
        return self.runtime_home / ("issue-" + str(issue))

    def _session_args(self, choice: ApprovedTask, worktree: Path) -> argparse.Namespace:
        return argparse.Namespace(
            project_root=self.store.root, repository=self.store.repository,
            issue=choice.issue, branch_ref=choice.branch_ref,
            base_revision=choice.base_revision, expected_contract_id=choice.contract_id,
            worktree=worktree, runtime_dir=self._runtime(choice.issue),
            port=self.port_for_issue(choice.issue), remote=self.store.remote,
            opencode_bin=self.opencode_bin)

    def _payload(self, issue: int, snapshot: dict, choice: ApprovedTask) -> dict:
        # Exact immutable requirements are forwarded, not redesigned by Main.
        text = (
            f"You are the Issue-root KAGARI Task Orchestrator for "
            f"{self.store.repository}#{issue}.\n"
            f"The immutable approved Task Contract is {choice.contract_id}.\n"
            f"Issue title:\n{snapshot['title']}\n"
            f"Approved Issue body:\n{snapshot['body']}\n\n"
            "Work only within this Task's approved Project/Issue Worktree and "
            "source collaboration capabilities. Plan and implement the approved "
            "requirements, validate and report factual Evidence. Execute normal "
            "in-scope tools directly; OpenCode will surface its native Permission "
            "when a tool requires explicit approval. Do not ask a separate Question "
            "before routine tool calls or at Task start. Use Question only for "
            "unresolved product or architecture decisions requiring Human judgment. "
            "Do not launch other Issues, decide new product requirements, auto-Merge "
            "or auto-Release. Report missing authority without inventing it."
        )
        if len(text.encode("utf-8")) > 100_000:
            raise MainError("ISSUE_PROMPT_TOO_LARGE")
        payload: dict = {
            "agent": self.agent,
            "parts": [{"type": "text", "text": text}],
        }
        if self.model is not None:
            payload["model"] = {
                "providerID": self.model[0], "modelID": self.model[1]}
        return payload

    @staticmethod
    def _recover_kickoff(
        binding: object, receipt: dict, payload: dict,
        pending: Path, accepted: Path,
    ) -> str:
        """Resolve a lost prompt HTTP acknowledgement only with positive
        upstream user-message evidence. Never resend on uncertainty.
        """
        if _read_receipt(pending) != receipt:
            raise MainError("PROMPT_RECONCILIATION_REQUIRED")
        url = (binding.server_url + "/session/" + binding.session_id
               + "/message?directory=" + quote(binding.worktree, safe=""))
        try:
            messages = opencode_request(url)
        except ControlError:
            raise MainError("PROMPT_OUTCOME_UNCERTAIN") from None
        if type(messages) is not list or len(messages) > 1000:
            raise MainError("PROMPT_OUTCOME_UNCERTAIN")
        expected_parts = payload["parts"]

        def same_prompt_parts(value: object) -> bool:
            # OpenCode stores native part IDs and other observation fields
            # alongside the original type/text, even for accepted prompts.
            if type(value) is not list or len(value) != len(expected_parts):
                return False
            return all(
                type(observed) is dict
                and observed.get("type") == expected["type"]
                and observed.get("text") == expected["text"]
                for observed, expected in zip(value, expected_parts)
            )

        matches = [
            message for message in messages
            if type(message) is dict
            and type(message.get("info")) is dict
            and message["info"].get("role") == "user"
            and same_prompt_parts(message.get("parts"))
        ]
        if len(matches) != 1:
            raise MainError("PROMPT_OUTCOME_UNCERTAIN")
        os.replace(pending, accepted)
        return "RECOVERED_SUBMITTED"

    def _establish_initial_collaboration(
        self, choice: ApprovedTask, worktree: Path,
    ) -> CollaborationReceipt:
        """Establish and re-observe #194's PR-first launch boundary.

        This is a *host-installed* capability. Missing transport, missing
        authority and partial/uncertain GitHub writes refuse the first model
        turn. Merely setting a PR number or holding a JSON plan is never a
        positive initial checkpoint.
        """
        with self._main_owner_guard:
            owner = self._main_owner
            if owner is None:
                raise MainError("MAIN_OWNER_REQUIRED")
            owner._assert_owned(self)
            provider = self.initial_collaboration_for_issue
            if provider is None:
                raise MainError("MAIN_INITIAL_COLLABORATION_REQUIRED")
            try:
                installed = provider(choice.issue, worktree)
            except Exception:
                raise MainError("MAIN_INITIAL_COLLABORATION_UNAVAILABLE") from None
            if (type(installed) is not InitialCollaboration
                    or type(installed.facade) is not TaskCollaboration
                    or not callable(installed.metadata_provider)
                    or type(installed.report) is not str
                    or not installed.report.strip()
                    or len(installed.report.encode("utf-8")) > 16_384):
                raise MainError("MAIN_INITIAL_COLLABORATION_INVALID")
            facade = installed.facade
            bound_store = facade._store
            if (type(bound_store) is not MetadataStore
                    or bound_store.root != worktree
                    or bound_store.repository != self.store.repository
                    or bound_store.remote != self.store.remote
                    or facade.repository != self.store.repository
                    or facade.task != str(choice.issue)
                    or facade.branch_ref != choice.branch_ref
                    or facade._base_revision != choice.base_revision):
                raise MainError("MAIN_INITIAL_COLLABORATION_MISMATCH")
            try:
                worktree_registered(self.store.root, worktree, choice.branch_ref)
                receipt = facade.establish(installed.report, installed.metadata_provider)
                if type(receipt) is not CollaborationReceipt:
                    raise MainError("MAIN_INITIAL_COLLABORATION_UNCONFIRMED")
                if (receipt.repository != self.store.repository
                        or receipt.task != str(choice.issue)
                        or receipt.branch_ref != choice.branch_ref
                        or receipt.contract_id != choice.contract_id
                        or type(receipt.pull_request) is not int
                        or receipt.pull_request <= 0
                        or type(receipt.comment_id) is not int
                        or receipt.comment_id <= 0
                        or type(receipt.subject) is not str
                        or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", receipt.subject)
                        or type(receipt.metadata_commit) is not str
                        or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", receipt.metadata_commit)
                        or type(receipt.snapshot_id) is not str
                        or not re.fullmatch(r"[a-f0-9]{64}", receipt.snapshot_id)
                        or type(receipt.record_id) is not str
                        or not re.fullmatch(r"[a-f0-9]{64}", receipt.record_id)):
                    raise MainError("MAIN_INITIAL_COLLABORATION_UNCONFIRMED")
                # Confirm native Git facts and the *actual* durable PR checkpoint,
                # not merely a successful create/POST acknowledgement.
                view = self._live_view(choice, worktree)
                if (view["subject"] != receipt.subject
                        or view["git"]["branch_matches_task"] is not True):
                    raise MainError("MAIN_INITIAL_COLLABORATION_SUBJECT_CHANGED")
                facts = facade.github_observation(GitHubPullRequestRequest(
                    self.store.repository, str(choice.issue),
                    receipt.pull_request, choice.branch_ref))
                checkpoint = facts.previous_checkpoint
                pr = facts.pull_request
                if (checkpoint is None
                        or checkpoint.checkpoint_id != str(receipt.comment_id)
                        or checkpoint.subject != receipt.subject
                        or checkpoint.metadata_commit != receipt.metadata_commit
                        or checkpoint.snapshot_id != receipt.snapshot_id
                        or pr.number != receipt.pull_request
                        or pr.head_repository != self.store.repository
                        or pr.base_repository != self.store.repository
                        or pr.head_ref != choice.branch_ref.removeprefix("refs/heads/")
                        or pr.head_oid != receipt.subject
                        or pr.state != "open" or pr.draft is not True):
                    raise MainError("MAIN_INITIAL_COLLABORATION_UNCONFIRMED")
            except (TaskCollaborationError, ControlError, TaskViewError,
                    MetadataRefError, KeyError, ValueError):
                raise MainError("MAIN_INITIAL_COLLABORATION_REFUSED") from None
            return receipt

    @staticmethod
    def _collaboration_receipt(choice: ApprovedTask, receipt: CollaborationReceipt) -> dict:
        return {
            "schema_version": 1, "repository": receipt.repository,
            "issue": choice.issue, "branch_ref": receipt.branch_ref,
            "contract_id": choice.contract_id, "subject": receipt.subject,
            "pull_request": receipt.pull_request,
            "checkpoint_id": receipt.comment_id,
            "metadata_commit": receipt.metadata_commit,
            "record_id": receipt.record_id, "snapshot_id": receipt.snapshot_id,
        }

    @staticmethod
    def _validate_collaboration_marker(choice: ApprovedTask, marker: dict,
                                       repository: str) -> None:
        expected = {"schema_version": 1, "repository": repository,
                    "issue": choice.issue, "branch_ref": choice.branch_ref,
                    "contract_id": choice.contract_id}
        if (any(marker.get(k) != v for k, v in expected.items())
                or type(marker.get("subject")) is not str
                or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", marker["subject"])
                or type(marker.get("pull_request")) is not int
                or marker["pull_request"] <= 0
                or type(marker.get("checkpoint_id")) is not int
                or marker["checkpoint_id"] <= 0
                or type(marker.get("metadata_commit")) is not str
                or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", marker["metadata_commit"])
                or type(marker.get("snapshot_id")) is not str
                or not re.fullmatch(r"[a-f0-9]{64}", marker["snapshot_id"])
                or type(marker.get("record_id")) is not str
                or not re.fullmatch(r"[a-f0-9]{64}", marker["record_id"])):
            raise MainError("MAIN_INITIAL_COLLABORATION_RECONCILIATION_REQUIRED")

    def dispatch(self, issue: int) -> dict:
        """Run only while this Main has a verified live repository owner.

        A trusted host calling RepositoryMain directly without MainSupervisor
        must not bypass the same-repository singleton across distinct clones.
        Holding the guard across the full effect prevents supervisor shutdown
        from releasing the OS flock between ownership validation and kickoff.
        """
        if type(issue) is not int or issue not in self.approved:
            raise MainError("ISSUE_NOT_APPROVED")
        with self._main_owner_guard:
            owner = self._main_owner
            if owner is None:
                raise MainError("MAIN_OWNER_REQUIRED")
            owner._assert_owned(self)
            # The canonical MainSupervisor owner is held for this entire call.
            # The local Git flock serializes Task handoff within this Project.
            # It is not a second semantic Task or permission database.
            # Do not read mutable remote metadata before taking the repository
            # lock: concurrent Git fetches can share FETCH_HEAD in one Git common dir.
            if type(issue) is not int or issue not in self.approved:
                raise MainError("ISSUE_NOT_APPROVED")
            common = Path(os.path.realpath(self.store.root / ".git"))
            if not common.is_dir() or common.parent != self.store.root:
                raise MainError("PRIMARY_PROJECT_REQUIRED")
            lock = common / "kagari-main-issue-dispatch.lock"
            fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "rb+") as handle:
                # Normal retries wait briefly for the first handoff, but a
                # stalled/unknown owner cannot block Main shutdown forever.
                deadline = time.monotonic() + DISPATCH_LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise MainError("MAIN_DISPATCH_BUSY") from None
                        time.sleep(0.05)
                try:
                    # Source/eligibility can drift even while waiting on local host.
                    choice, snapshot = self._spec(issue)
                    runtime = self._runtime(issue)
                    collab_marker = runtime / "initial-collaboration.json"
                    # New Tasks must have the trusted #194 collaboration capability.
                    # A previous prompt may resume with its already pinned marker,
                    # but a plan file or an open Issue is not a grant.
                    if (not collab_marker.exists()
                            and self.initial_collaboration_for_issue is None):
                        raise MainError("MAIN_INITIAL_COLLABORATION_REQUIRED")
                    allocation = IssueWorktreeAllocator(self.store).ensure(
                        task=str(issue), branch_ref=choice.branch_ref,
                        base_revision=choice.base_revision,
                        expected_contract_id=choice.contract_id)
                    worktree = Path(allocation.worktree)
                    pending = runtime / "prompt.pending.json"
                    accepted = runtime / "prompt.sent.json"
                    if collab_marker.exists():
                        prepared = _read_receipt(collab_marker)
                        self._validate_collaboration_marker(
                            choice, prepared, self.store.repository)
                        if not pending.exists() and not accepted.exists():
                            # Crash between PR establishment and model kickoff:
                            # positively re-read the #194 checkpoint before trying
                            # the original Turn, rather than trusting a local file.
                            verified = self._establish_initial_collaboration(choice, worktree)
                            if self._collaboration_receipt(choice, verified) != prepared:
                                raise MainError("MAIN_INITIAL_COLLABORATION_RECONCILIATION_REQUIRED")
                    else:
                        if pending.exists() or accepted.exists():
                            raise MainError("MAIN_INITIAL_COLLABORATION_RECONCILIATION_REQUIRED")
                        verified = self._establish_initial_collaboration(choice, worktree)
                        prepared = self._collaboration_receipt(choice, verified)
                        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
                        _json_write_once(collab_marker, prepared)
                    # Fresh kickoff must still target the exact checkpointed Git
                    # subject. An already sent Turn may have moved Task HEAD.
                    if not pending.exists() and not accepted.exists():
                        current = self._live_view(choice, worktree)
                        if current["subject"] != prepared["subject"]:
                            raise MainError("MAIN_INITIAL_COLLABORATION_SUBJECT_CHANGED")
                    # A live Issue may have changed while PR metadata was published.
                    latest_choice, latest_snapshot = self._spec(issue)
                    if latest_choice != choice or latest_snapshot != snapshot:
                        raise MainError("ISSUE_SNAPSHOT_DRIFT")
                    started = start_root_session(
                        self._session_args(choice, Path(allocation.worktree)))
                    context = Path(started["context"])
                    with context.open(encoding="utf-8") as stream:
                        location = json.load(stream)
                    current_binding = resolve(argparse.Namespace(
                        project_root=self.store.root,
                        worktree=Path(allocation.worktree),
                        repository=self.store.repository, issue=issue,
                        base_revision=choice.base_revision,
                        branch_ref=choice.branch_ref, remote=self.store.remote,
                        server_url=location["server_url"],
                        session_id=location["session_id"]))
                    if current_binding.contract_sha256 != choice.contract_id:
                        raise MainError("TASK_CONTRACT_CHANGED")
                    # Re-read the external Issue and Task authority immediately
                    # before the non-idempotent model kickoff.
                    current_choice, current_snapshot = self._spec(issue)
                    if current_choice != choice or current_snapshot != snapshot:
                        raise MainError("ISSUE_SNAPSHOT_DRIFT")
                    payload = self._payload(issue, snapshot, choice)
                    encoded = json.dumps(payload, sort_keys=True).encode()
                    fingerprint = hashlib.sha256(encoded).hexdigest()
                    receipt = {
                        "schema_version": 1, "issue": issue,
                        "repository": self.store.repository,
                        "contract_id": choice.contract_id,
                        "session_id": current_binding.session_id,
                        "prompt_sha256": fingerprint,
                    }
                    runtime.mkdir(parents=True, exist_ok=True)
                    uncertain = pending
                    if uncertain.exists():
                        if accepted.exists():
                            raise MainError("PROMPT_RECONCILIATION_REQUIRED")
                        dispatch_state = self._recover_kickoff(
                            current_binding, receipt, payload, uncertain, accepted)
                    elif accepted.exists():
                        if _read_receipt(accepted) != receipt:
                            raise MainError("PROMPT_RECONCILIATION_REQUIRED")
                        dispatch_state = "ALREADY_DISPATCHED"
                    else:
                        # Persist uncertainty *before* the non-idempotent native POST.
                        # A lost HTTP acknowledgement never causes a duplicate turn.
                        _json_write_once(uncertain, receipt)
                        url = current_binding.server_url + (
                            "/session/" + current_binding.session_id
                            + "/prompt_async?directory="
                            + quote(current_binding.worktree, safe=""))
                        opencode_request(url, payload=payload)
                        os.replace(uncertain, accepted)
                        dispatch_state = "SUBMITTED"
                    state = task_status(current_binding, OpenCode(current_binding))
                    return {
                        "status": "ok", "operation": "main-dispatch",
                        "repository": self.store.repository, "issue": issue,
                        "contract_id": choice.contract_id,
                        "worktree": allocation.worktree,
                        "worktree_state": allocation.status,
                        "session_state": started["outcome"],
                        "prompt_state": dispatch_state,
                        "context": str(context), "runtime": state,
                    }
                except (IssueWorktreeError, ControlError) as exc:
                    raise MainError(getattr(exc, "code", "TASK_LAUNCH_FAILED")) from None
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _live_view(
        self, choice: ApprovedTask, worktree: Path | None = None,
        *, metadata_reader: Callable[[Path], MetadataStore] | None = None,
    ) -> dict:
        """Observe #144 Task View from the actual Task Worktree when known."""
        target = worktree or self.store.root / ".worktrees" / str(choice.issue)
        store = (self.store if metadata_reader is None
                 else metadata_reader(self.store.root))
        if target.exists():
            try:
                worktree_registered(self.store.root, target, choice.branch_ref)
                store = (
                    MetadataStore(target, self.store.repository, self.store.remote)
                    if metadata_reader is None else metadata_reader(target))
            except (ControlError, MetadataRefError):
                raise MainError("TASK_WORKTREE_MISMATCH") from None
        try:
            number = self.pr_for_issue.get(choice.issue)
            return observe_live_task(
                store, task=str(choice.issue),
                branch_ref=choice.branch_ref,
                base_revision=choice.base_revision,
                selected_evidence=self.evidence_for_issue.get(
                    choice.issue, ()),
                pr_number=number,
                github_reader=self.read_pr if number is not None else None)
        except TaskViewError:
            raise MainError("TASK_VIEW_UNAVAILABLE") from None

    def guarded_draft_pr(self, issue: int, collaboration: object):
        """#194 PR continuation needs BOTH Task grants and a live Main owner.

        No direct trusted-host PR side effect may bypass the canonical
        repository singleton. This is not source publication or Merge.
        """
        if type(issue) is not int or issue not in self.approved:
            raise MainError("ISSUE_NOT_APPROVED")
        with self._main_owner_guard:
            owner = self._main_owner
            if owner is None:
                raise MainError("MAIN_OWNER_REQUIRED")
            owner._assert_owned(self)
            choice, _snapshot = self._spec(issue)
            from task_collaboration import TaskCollaboration, TaskCollaborationError
            if (type(collaboration) is not TaskCollaboration
                    or collaboration.repository != self.store.repository
                    or collaboration.task != str(issue)
                    or collaboration.branch_ref != choice.branch_ref
                    or collaboration._base_revision != choice.base_revision):
                raise MainError("GUARDED_PR_BINDING_MISMATCH")
            associated_store = collaboration._store
            if associated_store is not self.store:
                target = self.store.root / ".worktrees" / str(issue)
                if (type(associated_store) is not MetadataStore
                        or associated_store.root != target
                        or associated_store.repository != self.store.repository
                        or associated_store.remote != self.store.remote):
                    raise MainError("GUARDED_PR_BINDING_MISMATCH")
                try:
                    worktree_registered(self.store.root, target, choice.branch_ref)
                except ControlError:
                    raise MainError("GUARDED_PR_BINDING_MISMATCH") from None
            try:
                return collaboration.ensure_draft_pr()
            except TaskCollaborationError:
                raise MainError("GUARDED_PR_PUBLICATION_REFUSED") from None

    def observe(self, issue: int) -> dict:
        """Factually observe Task without writing FETCH_HEAD into Product Git.

        #217 metadata fetch/history validation runs against a disposable Git
        object/ref environment; #144 Git HEAD/status still reads the genuine
        Project or exact Issue Worktree. No Main lock, Task or PR effect.
        """
        try:
            with isolated_metadata_observation(self.store) as reader:
                return self._observe_isolated(issue, reader)
        except MetadataRefError:
            raise MainError("TASK_VIEW_UNAVAILABLE") from None
        except ControlError:
            # An unavailable/changed upstream root Session is one Issue's
            # observation failure; it must not terminate Main's entire
            # multi-Issue observation cycle.
            raise MainError("SESSION_OBSERVATION_UNAVAILABLE") from None

    def _observe_isolated(
        self, issue: int, reader: Callable[[Path], MetadataStore],
    ) -> dict:
        choice, snapshot = self._spec(
            issue, metadata_store=reader(self.store.root))
        runtime = self._runtime(issue)
        context = runtime / "context.json"
        approved_task = {
            "repository": self.store.repository, "issue": issue,
            "contract_id": choice.contract_id,
            "title": snapshot["title"],
            "base_revision": choice.base_revision,
            "branch_ref": choice.branch_ref,
        }
        if not context.exists():
            # Before Session creation, show factual Git/Contract state. A
            # missing Worktree or an unconfirmed Task never becomes complete.
            view = self._live_view(choice, metadata_reader=reader)
            return {
                "status": "ok", "issue": issue, "repository": self.store.repository,
                "approved_task": approved_task, "task_view": view,
                "runtime": "NOT_STARTED", "kickoff": "NOT_SENT",
            }
        data = _read_receipt(context)
        expected = {
            "schema_version": 1,
            "project_root": str(self.store.root),
            "repository": self.store.repository,
            "issue": issue,
            "base_revision": choice.base_revision,
            "branch_ref": choice.branch_ref,
            "remote": self.store.remote,
        }
        if (any(data.get(k) != v for k, v in expected.items())
                or type(data.get("worktree")) is not str
                or type(data.get("server_url")) is not str
                or type(data.get("session_id")) is not str):
            raise MainError("MAIN_RUNTIME_BINDING_MISMATCH")
        worktree = Path(data["worktree"])
        if (not worktree.is_absolute()
                or worktree != self.store.root / ".worktrees" / str(issue)):
            raise MainError("MAIN_RUNTIME_BINDING_MISMATCH")
        try:
            worktree_registered(self.store.root, worktree, choice.branch_ref)
            # The #191 snapshot above already authenticates the Contract.
            # Reusing #292's binding and native root Session verifier avoids
            # its separate, mutating metadata fetch in an observer process.
            binding = TaskBinding.from_mapping({
                "schema_version": 1,
                "repository": self.store.repository, "issue": issue,
                "worktree": str(worktree),
                "server_url": data["server_url"], "session_id": data["session_id"],
                "contract_sha256": choice.contract_id,
                "contract_revision": "contract:" + choice.contract_id[:16],
            })
        except ControlError:
            raise MainError("MAIN_RUNTIME_BINDING_MISMATCH") from None
        view = self._live_view(choice, worktree, metadata_reader=reader)
        client = OpenCode(binding)
        return {
            "status": "ok", "issue": issue, "repository": self.store.repository,
            "approved_task": approved_task, "task_view": view,
            "runtime": task_status(binding, client),
            "pending": client.pending(),
            "kickoff": ("UNCERTAIN" if (runtime / "prompt.pending.json").exists()
                        else "SENT" if (runtime / "prompt.sent.json").exists()
                        else "NOT_SENT"),
        }
