"""Host-owned, opt-in Issue-lifetime Git linked Worktree allocator (#286).

Existing #191 Task Record/immutable Contract is the only Task authority.
This module never creates a Task Record, assigns Issues, starts models, or
changes the shipped independent-clone backend. The trusted host supplies a
MetadataStore and an already selected Issue. Errors retain partial Git state
for the host to inspect rather than deleting it on speculative retry.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess
from dataclasses import dataclass
from typing import Any

from metadata_ref import MetadataRefError, MetadataStore
from task_record import TaskRecordError, TaskRecords, validate_branch_ref


class IssueWorktreeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Allocation:
    status: str                    # CREATED or RESUMED
    repository: str
    task: str
    contract_id: str
    branch_ref: str
    worktree: str
    head: str
    base_revision: str


def _git(root: Path, *args: str, accept_absent: bool = False) -> str | None:
    """Fixed Git arguments only; never interpret caller data as shell."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    try:
        p = subprocess.run(
            ["git", "-C", str(root), "--no-optional-locks", *args],
            capture_output=True, env=env, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        raise IssueWorktreeError("GIT_UNAVAILABLE") from None
    if p.returncode == 1 and accept_absent:
        return None
    if p.returncode or len(p.stdout) > 512 * 1024:
        raise IssueWorktreeError("GIT_OBSERVATION_FAILED")
    try:
        return p.stdout.decode("utf-8", "strict").strip()
    except UnicodeError:
        raise IssueWorktreeError("GIT_OUTPUT_INVALID") from None


def _worktree_inventory(project: Path) -> list[dict[str, str]]:
    """Parse exact native Git registrations, not directory-name guesses."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    p = subprocess.run(
        ["git", "-C", str(project), "--no-optional-locks",
         "worktree", "list", "--porcelain", "-z"],
        capture_output=True, env=env, timeout=15, check=False)
    if p.returncode or len(p.stdout) > 512 * 1024:
        raise IssueWorktreeError("WORKTREE_INVENTORY_UNAVAILABLE")
    output: list[dict[str, str]] = []
    try:
        for block in p.stdout.strip(b"\0").split(b"\0\0"):
            entry = {}
            for part in block.split(b"\0"):
                if not part:
                    continue
                key, sep, value = part.partition(b" ")
                entry[key.decode("ascii")] = value.decode("utf-8", "strict") if sep else ""
            if entry:
                output.append(entry)
    except UnicodeError:
        raise IssueWorktreeError("WORKTREE_INVENTORY_INVALID") from None
    return output


def _canonical_dir(path: Path, code: str) -> Path:
    if (not path.is_absolute() or path.is_symlink()
            or not path.is_dir() or path.resolve(strict=True) != path):
        raise IssueWorktreeError(code)
    return path


def _read_regular(path: Path) -> str:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
            raise IssueWorktreeError("GIT_POINTER_INVALID")
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise IssueWorktreeError("GIT_POINTER_INVALID") from None


class IssueWorktreeAllocator:
    def __init__(self, store: MetadataStore):
        if not isinstance(store, MetadataStore):
            raise IssueWorktreeError("TASK_STORE_REQUIRED")
        self.store = store
        self.project = _canonical_dir(store.root, "PROJECT_ROOT_INVALID")
        dotgit = self.project / ".git"
        if dotgit.is_symlink() or not dotgit.is_dir():
            raise IssueWorktreeError("PRIMARY_GIT_DIRECTORY_REQUIRED")
        if _git(self.project, "rev-parse", "--show-toplevel") != str(self.project):
            raise IssueWorktreeError("PROJECT_ROOT_MISMATCH")
        self.common_dir = _canonical_dir(dotgit, "PRIMARY_GIT_DIRECTORY_REQUIRED")

    def _authority(self, task: str, base_revision: str, branch_ref: str,
                   expected_contract_id: str) -> str:
        try:
            tip = self.store.fetch_tip()
        except (MetadataRefError, TypeError, ValueError):
            raise IssueWorktreeError("TASK_RECORD_INVALID") from None
        if tip is None:
            raise IssueWorktreeError("TASK_RECORD_ABSENT")
        try:
            read = TaskRecords(self.store, authorize=lambda _req: False).read(
                tip, task=task, base_revision=base_revision,
                branch_ref=branch_ref)
        except (TaskRecordError, MetadataRefError, TypeError, ValueError):
            raise IssueWorktreeError("TASK_RECORD_INVALID") from None
        if read is None:
            raise IssueWorktreeError("TASK_RECORD_ABSENT")
        _rid, record, _contract = read
        if "disposition" in record["payload"]:
            raise IssueWorktreeError("TASK_RETIRED")
        if record["payload"]["contract_id"] != expected_contract_id:
            raise IssueWorktreeError("TASK_CONTRACT_CHANGED")
        return expected_contract_id

    def _registered(self, target: Path, branch_ref: str) -> str:
        _canonical_dir(target, "WORKTREE_PATH_INVALID")
        inventory = _worktree_inventory(self.project)
        matches = [e for e in inventory if e.get("worktree") == str(target)]
        if len(matches) != 1 or matches[0].get("branch") != branch_ref:
            raise IssueWorktreeError("WORKTREE_REGISTRATION_MISMATCH")
        if sum(e.get("branch") == branch_ref for e in inventory) != 1:
            raise IssueWorktreeError("DUPLICATE_BRANCH_REGISTRATION")
        if "locked" in matches[0] or "prunable" in matches[0]:
            raise IssueWorktreeError("WORKTREE_UNAVAILABLE")
        if _git(target, "rev-parse", "--show-toplevel") != str(target):
            raise IssueWorktreeError("WORKTREE_ROOT_MISMATCH")
        if _git(target, "rev-parse", "--path-format=absolute",
                "--git-common-dir") != str(self.common_dir):
            raise IssueWorktreeError("FOREIGN_WORKTREE")
        if _git(target, "symbolic-ref", "--quiet", "HEAD") != branch_ref:
            raise IssueWorktreeError("WORKTREE_BRANCH_MISMATCH")
        head = _git(target, "rev-parse", "HEAD")
        if head != matches[0].get("HEAD", matches[0].get("head")):
            # Native porcelain uses lowercase 'head' in Git 2.55.
            raise IssueWorktreeError("WORKTREE_HEAD_MISMATCH")
        if head != _git(self.project, "rev-parse", "--verify", branch_ref):
            raise IssueWorktreeError("WORKTREE_HEAD_MISMATCH")
        # Git pointer must reciprocally bind the worktree to the real common
        # gitdir, not merely contain a user-chosen similar branch and path.
        gitfile = _read_regular(target / ".git")
        if not gitfile.startswith("gitdir: "):
            raise IssueWorktreeError("GIT_POINTER_INVALID")
        gitdir_text = gitfile[len("gitdir: "):]
        admin = Path(gitdir_text)
        if not admin.is_absolute():
            admin = target / admin
        try:
            admin = admin.resolve(strict=True)
        except (OSError, RuntimeError):
            raise IssueWorktreeError("GIT_POINTER_INVALID") from None
        if admin.parent != self.common_dir / "worktrees" or not admin.is_dir():
            raise IssueWorktreeError("GIT_POINTER_INVALID")
        backlink = _read_regular(admin / "gitdir")
        back = Path(backlink)
        if not back.is_absolute():
            back = admin / back
        try:
            resolved_backlink = back.resolve(strict=True)
        except (OSError, RuntimeError):
            raise IssueWorktreeError("GIT_POINTER_INVALID") from None
        if resolved_backlink != target / ".git":
            raise IssueWorktreeError("GIT_POINTER_INVALID")
        return head

    def ensure(self, *, task: str, base_revision: str, branch_ref: str,
               expected_contract_id: str) -> Allocation:
        if not isinstance(task, str) or not re.fullmatch(r"[1-9][0-9]{0,127}", task):
            raise IssueWorktreeError("TASK_ID_INVALID")
        try:
            validate_branch_ref(branch_ref)
        except TaskRecordError:
            raise IssueWorktreeError("TASK_BRANCH_INVALID") from None
        if not re.search(r"(^|[^0-9])" + re.escape(task) + r"([^0-9]|$)", branch_ref):
            raise IssueWorktreeError("BRANCH_ISSUE_ID_MISMATCH")
        if not isinstance(expected_contract_id, str) or not re.fullmatch(
                r"[0-9a-f]{64}", expected_contract_id):
            raise IssueWorktreeError("CONTRACT_ID_INVALID")
        namespace = self.project / ".worktrees"
        if namespace.is_symlink() or (namespace.exists() and not namespace.is_dir()):
            raise IssueWorktreeError("WORKTREE_NAMESPACE_INVALID")
        target = namespace / task

        # Non-effectful namespace check before the Task lock. Authoritative
        # metadata reads occur under the lock so concurrent callers cannot
        # disturb each other's bounded Git FETCH_HEAD observations.
        # Probe the directory itself. A narrower file-only ignore rule must
        # not pass while the rest of a linked Worktree remains untracked.
        if _git(self.project, "check-ignore", "--quiet", "--no-index",
                "--", ".worktrees/", accept_absent=True) is None:
            raise IssueWorktreeError("MANAGED_WORKTREE_NOT_IGNORED")
        # Same-Task local process serialization. No additional Task/permission
        # database; the lock file only controls concurrent Git mutations.
        lockhome = self.common_dir / "kagari-issue-worktree-locks"
        lockhome.mkdir(mode=0o700, exist_ok=True)
        lock = lockhome / (task + ".lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(lock, flags, 0o600), "rb+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                self._authority(task, base_revision, branch_ref, expected_contract_id)
                # A cleaned or partially cleaned Issue must never be silently
                # recreated by a stale Main dispatch after Merge.
                cleanup_marker = (self.common_dir /
                                  "kagari-issue-worktree-cleanups" /
                                  (task + ".json"))
                if cleanup_marker.exists() or cleanup_marker.is_symlink():
                    raise IssueWorktreeError("WORKTREE_CLEANED_OR_CLEANUP_PENDING")
                inventory = _worktree_inventory(self.project)
                matches = [e for e in inventory if e.get("worktree") == str(target)]
                if matches:
                    head = self._registered(target, branch_ref)
                    state = "RESUMED"
                else:
                    if target.exists() or target.is_symlink():
                        raise IssueWorktreeError("UNREGISTERED_TARGET_PRESENT")
                    if any(e.get("branch") == branch_ref for e in inventory):
                        raise IssueWorktreeError("BRANCH_ALREADY_IN_WORKTREE")
                    # A previously created but unregistered branch may contain
                    # unique data. Do not reset/delete/adopt it.
                    if _git(self.project, "show-ref", "--verify", "--quiet",
                            branch_ref, accept_absent=True) is not None:
                        raise IssueWorktreeError("ORPHAN_BRANCH_REQUIRES_RECONCILIATION")
                    if _git(self.project, "cat-file", "-t", base_revision) != "commit":
                        raise IssueWorktreeError("BASE_NOT_COMMIT")
                    if not namespace.exists():
                        namespace.mkdir(mode=0o700, exist_ok=True)
                    # Git creates the branch and the linked Worktree together.
                    # On failure retain whatever Git left for investigation.
                    try:
                        _git(self.project, "-c", "core.hooksPath=/dev/null",
                             "worktree", "add", "--no-track", "-b",
                             branch_ref[len("refs/heads/"):], str(target), base_revision)
                        head = self._registered(target, branch_ref)
                    except IssueWorktreeError:
                        raise IssueWorktreeError("WORKTREE_CREATION_AMBIGUOUS") from None
                    state = "CREATED"
                # Recheck authority after possible Git change/metadata race.
                self._authority(task, base_revision, branch_ref, expected_contract_id)
                return Allocation(state, self.store.repository, task,
                                  expected_contract_id, branch_ref, str(target),
                                  head, base_revision)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
