"""Host-owned, opt-in Issue Worktree cleanup after proven PR merge (#286).

Never force-remove, prune, kill Sessions, rewrite the base branch, or delete
unpreserved Product work. Only exact full-commit merges are automatically
provable; squash/rebase and stale local merge bases require Human reconciliation.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
from dataclasses import dataclass
from typing import Callable

from issue_worktree import (
    IssueWorktreeAllocator, IssueWorktreeError, _git, _worktree_inventory,
)

_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_TASK = re.compile(r"[1-9][0-9]{0,127}\Z")


@dataclass(frozen=True)
class PullRequestRequest:
    repository: str
    issue: int
    number: int
    branch_ref: str


@dataclass(frozen=True)
class MergedPullRequest:
    repository: str
    number: int
    state: str              # MERGED
    base_ref: str           # refs/heads/main
    head_ref: str           # exact Task branch
    head_oid: str           # PR source at merge
    merge_oid: str          # actual GitHub merge commit


@dataclass(frozen=True)
class SessionReleaseRequest:
    repository: str
    issue: int
    contract_id: str
    worktree: str
    branch_ref: str
    head_oid: str


@dataclass(frozen=True)
class CleanupReceipt:
    state: str              # CLEANED / ALREADY_CLEANED
    repository: str
    issue: int
    branch_ref: str
    head_oid: str
    merge_oid: str
    pull_request: int
    worktree: str


def github_pull_request_reader(
    repository: str, *, gh: str = "gh",
) -> Callable[[PullRequestRequest], MergedPullRequest]:
    """Bounded read-only GitHub PR facts for the requested exact repository."""
    if (type(repository) is not str
            or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository)
            or type(gh) is not str or not gh):
        raise IssueWorktreeError("PR_READER_INVALID")

    def read(request: PullRequestRequest) -> MergedPullRequest:
        if (type(request) is not PullRequestRequest
                or request.repository != repository
                or type(request.number) is not int or request.number <= 0):
            raise IssueWorktreeError("PR_REQUEST_INVALID")
        args = [
            gh, "pr", "view", str(request.number), "--repo", repository,
            "--json", ("number,state,mergedAt,baseRefName,headRefName,"
                       "headRefOid,headRepository,isCrossRepository,mergeCommit"),
        ]
        try:
            p = subprocess.run(
                args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=15, check=False,
                env={**os.environ, "GH_PROMPT_DISABLED": "1",
                     "GIT_TERMINAL_PROMPT": "0"})
            if p.returncode or len(p.stdout.encode()) > 64 * 1024:
                raise IssueWorktreeError("PR_FACTS_UNAVAILABLE")
            data = json.loads(p.stdout)
        except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
            raise IssueWorktreeError("PR_FACTS_UNAVAILABLE") from None
        if (type(data) is not dict
                or type(data.get("number")) is not int
                or data["number"] != request.number
                or data.get("isCrossRepository") is not False
                or type(data.get("headRepository")) is not dict
                or data["headRepository"].get("nameWithOwner") != repository):
            raise IssueWorktreeError("PR_IDENTITY_MISMATCH")
        merge = data.get("mergeCommit")
        if (data.get("state") != "MERGED"
                or type(data.get("mergedAt")) is not str
                or not data["mergedAt"]
                or type(merge) is not dict
                or type(merge.get("oid")) is not str
                or not _OID.fullmatch(merge["oid"])):
            raise IssueWorktreeError("PR_NOT_PROVEN_MERGED")
        base, head, head_oid = (
            data.get("baseRefName"), data.get("headRefName"),
            data.get("headRefOid"))
        if (type(base) is not str or not base
                or type(head) is not str
                or "refs/heads/" + head != request.branch_ref
                or type(head_oid) is not str or not _OID.fullmatch(head_oid)):
            raise IssueWorktreeError("PR_IDENTITY_MISMATCH")
        return MergedPullRequest(
            repository, request.number, "MERGED",
            "refs/heads/" + base, "refs/heads/" + head,
            head_oid, merge["oid"])
    return read


def _read_intent(path: Path) -> dict:
    try:
        st = path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or st.st_size > 8192):
            raise IssueWorktreeError("CLEANUP_INTENT_INVALID")
        data = json.loads(path.read_text("utf-8"))
        if type(data) is not dict:
            raise IssueWorktreeError("CLEANUP_INTENT_INVALID")
        return data
    except (OSError, UnicodeError, ValueError):
        raise IssueWorktreeError("CLEANUP_INTENT_INVALID") from None


def _write_intent_once(path: Path, data: dict) -> None:
    fd = os.open(
        path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
        | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write((json.dumps(data, sort_keys=True, separators=(",", ":"))
                      + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())


def _branch_oid(project: Path, branch_ref: str) -> str | None:
    if (_git(project, "show-ref", "--verify", "--quiet", branch_ref,
             accept_absent=True) is None):
        return None
    return _git(project, "rev-parse", "--verify", branch_ref + "^{commit}")


class IssueWorktreeCleanup:
    def __init__(
        self, allocator: IssueWorktreeAllocator, *,
        read_pull_request: Callable[[PullRequestRequest], MergedPullRequest],
        session_released: Callable[[SessionReleaseRequest], bool],
    ) -> None:
        if (type(allocator) is not IssueWorktreeAllocator
                or not callable(read_pull_request)
                or not callable(session_released)):
            raise IssueWorktreeError("CLEANUP_HOST_CAPABILITIES_REQUIRED")
        self.allocator = allocator
        self.read_pull_request = read_pull_request
        self.session_released = session_released

    def _merged(self, request: PullRequestRequest, base_ref: str) -> MergedPullRequest:
        try:
            facts = self.read_pull_request(request)
        except Exception:
            raise IssueWorktreeError("PR_FACTS_UNAVAILABLE") from None
        if (type(facts) is not MergedPullRequest
                or facts.repository != request.repository
                or type(facts.number) is not int or facts.number != request.number
                or facts.state != "MERGED"
                or facts.head_ref != request.branch_ref
                or facts.base_ref != base_ref
                or type(facts.head_oid) is not str
                or not _OID.fullmatch(facts.head_oid)
                or type(facts.merge_oid) is not str
                or not _OID.fullmatch(facts.merge_oid)):
            raise IssueWorktreeError("PR_NOT_EXACT_MERGED_TASK")
        return facts

    def _merge_proof(self, facts: MergedPullRequest,
                     task_head: str, base_ref: str) -> None:
        project = self.allocator.project
        if facts.head_oid != task_head:
            raise IssueWorktreeError("PR_HEAD_MISMATCH")
        if _git(project, "symbolic-ref", "--quiet", "HEAD") != base_ref:
            raise IssueWorktreeError("BASE_BRANCH_NOT_CHECKED_OUT")
        if _git(project, "cat-file", "-t", facts.merge_oid) != "commit":
            raise IssueWorktreeError("MERGE_COMMIT_NOT_LOCAL")
        if (_git(project, "merge-base", "--is-ancestor",
                 task_head, facts.merge_oid, accept_absent=True) is None):
            raise IssueWorktreeError("TASK_HISTORY_NOT_IN_MERGE")
        if (_git(project, "merge-base", "--is-ancestor",
                 facts.merge_oid, base_ref, accept_absent=True) is None):
            raise IssueWorktreeError("MERGED_HISTORY_NOT_IN_BASE")

    def _stopped(self, request: SessionReleaseRequest) -> None:
        # The authorized #163/Main host must positively confirm the exact
        # live Task session/process is no longer running. No PID-file guesses.
        try:
            stopped = self.session_released(request)
        except Exception:
            raise IssueWorktreeError("SESSION_RELEASE_UNVERIFIED") from None
        if stopped is not True:
            raise IssueWorktreeError("SESSION_RELEASE_UNVERIFIED")

    def cleanup(
        self, *, task: str, base_revision: str, branch_ref: str,
        expected_contract_id: str, pull_request: int,
        base_ref: str = "refs/heads/main",
    ) -> CleanupReceipt:
        from task_record import TaskRecordError, validate_branch_ref
        if (type(task) is not str or not _TASK.fullmatch(task)
                or type(pull_request) is not int or pull_request <= 0
                or type(expected_contract_id) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", expected_contract_id)
                or type(base_revision) is not str
                or not _OID.fullmatch(base_revision)):
            raise IssueWorktreeError("CLEANUP_REQUEST_INVALID")
        try:
            validate_branch_ref(branch_ref)
            validate_branch_ref(base_ref)
        except TaskRecordError:
            raise IssueWorktreeError("CLEANUP_REQUEST_INVALID") from None
        if (base_ref == branch_ref
                or not re.search(r"(^|[^0-9])" + re.escape(task)
                                 + r"([^0-9]|$)", branch_ref)):
            raise IssueWorktreeError("CLEANUP_REQUEST_INVALID")

        owner = self.allocator
        target = owner.project / ".worktrees" / task
        if (target.parent.is_symlink() or
                (target.parent.exists() and not target.parent.is_dir())):
            raise IssueWorktreeError("WORKTREE_NAMESPACE_INVALID")
        request = PullRequestRequest(
            owner.store.repository, int(task), pull_request, branch_ref)
        lockhome = owner.common_dir / "kagari-issue-worktree-locks"
        lockhome.mkdir(mode=0o700, exist_ok=True)
        lockflags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(lockhome / (task + ".lock"), lockflags, 0o600),
                       "rb+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                owner._authority(
                    task, base_revision, branch_ref, expected_contract_id)
                inventory = _worktree_inventory(owner.project)
                entries = [e for e in inventory
                           if e.get("worktree") == str(target)]
                others = [e for e in inventory
                          if e.get("branch") == branch_ref]
                branch_oid = _branch_oid(owner.project, branch_ref)
                journals = owner.common_dir / "kagari-issue-worktree-cleanups"
                marker = journals / (task + ".json")
                previous = (_read_intent(marker)
                            if marker.exists() or marker.is_symlink()
                            else None)
                if previous is None:
                    if (len(entries) != 1 or len(others) != 1
                            or target.is_symlink()):
                        raise IssueWorktreeError("WORKTREE_NOT_REGISTERED_FOR_CLEANUP")
                    branch_oid = owner._registered(target, branch_ref)
                else:
                    if (previous.get("schema_version") != 1
                            or previous.get("repository") != request.repository
                            or previous.get("task") != task
                            or previous.get("branch_ref") != branch_ref
                            or previous.get("base_ref") != base_ref
                            or previous.get("contract_id") != expected_contract_id
                            or previous.get("pull_request") != pull_request
                            or previous.get("worktree") != str(target)):
                        raise IssueWorktreeError("CLEANUP_INTENT_MISMATCH")
                    if (len(entries) > 1 or len(others) > 1
                            or target.is_symlink()):
                        raise IssueWorktreeError("CLEANUP_AMBIGUOUS_TARGET")
                    if (branch_oid is not None and
                            branch_oid != previous.get("head_oid")):
                        raise IssueWorktreeError("CLEANUP_BRANCH_CHANGED")
                    if target.exists():
                        if len(entries) != 1 or branch_oid is None:
                            raise IssueWorktreeError("CLEANUP_AMBIGUOUS_TARGET")
                        branch_oid = owner._registered(target, branch_ref)
                    else:
                        if entries or others:
                            raise IssueWorktreeError("CLEANUP_AMBIGUOUS_TARGET")
                        branch_oid = previous.get("head_oid")
                if type(branch_oid) is not str or not _OID.fullmatch(branch_oid):
                    raise IssueWorktreeError("CLEANUP_HEAD_INVALID")
                facts = self._merged(request, base_ref)
                self._merge_proof(facts, branch_oid, base_ref)
                session = SessionReleaseRequest(
                    request.repository, int(task), expected_contract_id,
                    str(target), branch_ref, branch_oid)
                self._stopped(session)
                intent = {
                    "schema_version": 1, "repository": request.repository,
                    "task": task, "branch_ref": branch_ref,
                    "base_ref": base_ref, "contract_id": expected_contract_id,
                    "pull_request": pull_request, "head_oid": branch_oid,
                    "merge_oid": facts.merge_oid, "worktree": str(target),
                }
                if previous is not None and previous != intent:
                    raise IssueWorktreeError("CLEANUP_INTENT_MISMATCH")
                first = previous is None
                if target.exists():
                    if _git(target, "status", "--porcelain=v1", "-z",
                            "--untracked-files=all", "--ignored=matching"):
                        raise IssueWorktreeError("WORKTREE_HAS_UNPRESERVED_DATA")
                    if first:
                        if journals.is_symlink():
                            raise IssueWorktreeError("CLEANUP_INTENT_INVALID")
                        journals.mkdir(mode=0o700, exist_ok=True)
                        _write_intent_once(marker, intent)
                    owner._authority(
                        task, base_revision, branch_ref, expected_contract_id)
                    self._stopped(session)
                    if owner._registered(target, branch_ref) != branch_oid:
                        raise IssueWorktreeError("WORKTREE_CHANGED_BEFORE_CLEANUP")
                    if _git(target, "status", "--porcelain=v1", "-z",
                            "--untracked-files=all", "--ignored=matching"):
                        raise IssueWorktreeError("WORKTREE_HAS_UNPRESERVED_DATA")
                    try:
                        _git(owner.project, "-c", "core.hooksPath=/dev/null",
                             "worktree", "remove", "--", str(target))
                    except IssueWorktreeError:
                        raise IssueWorktreeError(
                            "WORKTREE_REMOVE_OUTCOME_UNCERTAIN") from None
                if target.exists() or target.is_symlink():
                    raise IssueWorktreeError("WORKTREE_REMOVE_OUTCOME_UNCERTAIN")
                inventory = _worktree_inventory(owner.project)
                if any(e.get("worktree") == str(target)
                       or e.get("branch") == branch_ref for e in inventory):
                    raise IssueWorktreeError("WORKTREE_REMOVE_OUTCOME_UNCERTAIN")
                current = _branch_oid(owner.project, branch_ref)
                if current is not None:
                    if current != branch_oid:
                        raise IssueWorktreeError("CLEANUP_BRANCH_CHANGED")
                    self._merge_proof(facts, current, base_ref)
                    try:
                        _git(owner.project, "-c", "core.hooksPath=/dev/null",
                             "branch", "-d", "--",
                             branch_ref.removeprefix("refs/heads/"))
                    except IssueWorktreeError:
                        raise IssueWorktreeError(
                            "BRANCH_REMOVE_OUTCOME_UNCERTAIN") from None
                if (_branch_oid(owner.project, branch_ref) is not None
                        or target.exists() or target.is_symlink()):
                    raise IssueWorktreeError("CLEANUP_POSTCONDITION_FAILED")
                return CleanupReceipt(
                    "CLEANED" if first else "ALREADY_CLEANED",
                    request.repository, int(task), branch_ref,
                    branch_oid, facts.merge_oid, pull_request, str(target))
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)



def stopped_session_from_runtime(
    runtime_dir: Path,
) -> Callable[[SessionReleaseRequest], bool]:
    """Read-only #163 session ownership check, conservative on uncertainty.

    A supported #163 root Session writes both context.json and process.json
    into its exact runtime directory. A live PID or loopback listener blocks
    release. Missing or malformed evidence is UNKNOWN, never 'not running'.
    This checks supported same-host processes, not multi-host cluster owners.
    """
    if not isinstance(runtime_dir, Path) or not runtime_dir.is_absolute():
        raise IssueWorktreeError("SESSION_RUNTIME_INVALID")

    def stopped(request: SessionReleaseRequest) -> bool:
        if type(request) is not SessionReleaseRequest:
            return False
        if (runtime_dir.is_symlink() or not runtime_dir.is_dir()
                or runtime_dir.resolve(strict=True) != runtime_dir):
            return False
        def local_json(name: str) -> dict | None:
            file = runtime_dir / name
            try:
                info = file.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_size > 8192):
                    return None
                data = json.loads(file.read_text("utf-8"))
                return data if type(data) is dict else None
            except (OSError, UnicodeError, ValueError):
                return None

        context = local_json("context.json")
        process = local_json("process.json")
        if context is None or process is None:
            return False
        pid = process.get("pid")
        port = process.get("port")
        if (context.get("repository") != request.repository
                or context.get("issue") != request.issue
                or context.get("worktree") != request.worktree
                or context.get("branch_ref") != request.branch_ref
                or process.get("worktree") != request.worktree
                or type(pid) is not int or pid <= 1
                or type(port) is not int or not 1024 <= port <= 65535
                or context.get("server_url") != f"http://127.0.0.1:{port}"
                or type(context.get("session_id")) is not str
                or not re.fullmatch(r"ses_[A-Za-z0-9_-]{8,120}",
                                    context["session_id"])):
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass                  # exact recorded process is gone
        except (OSError, PermissionError):
            return False
        else:
            return False         # process still running or an unknown PID reuse
        # A restarted server at the same endpoint must not be treated as a
        # stopped Task merely because its original PID exited.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(0.3)
                return client.connect_ex(("127.0.0.1", port)) == errno.ECONNREFUSED
        except OSError:
            return False
    return stopped
