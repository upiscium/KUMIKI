#!/usr/bin/env python3
"""KAGARI #291: opt-in repository Main host runner.

One active local Main per ordinary Git repository, and an explicit finite
Issue selection. Durable Task truth remains #191/Git/GitHub; this module
owns no extra semantic/permission database or independent Ask broker.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import threading
from typing import Any, Callable

if __package__:
    from .kagari_vnext_main import (
        ApprovedTask, MainError, RepositoryMain, github_issue_reader)
else:
    from kagari_vnext_main import (
        ApprovedTask, MainError, RepositoryMain, github_issue_reader)

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
import sys
sys.path.insert(0, str(V4))
from metadata_ref import MetadataStore, MetadataRefError
from task_record import validate_branch_ref, TaskRecordError
from task_view import EvidenceRef
from issue_worktree import IssueWorktreeAllocator, IssueWorktreeError
from issue_worktree_cleanup import (
    CleanupReceipt, IssueWorktreeCleanup, github_pull_request_reader,
    stopped_session_from_runtime,
)
if __package__:
    from .kagari_vnext_progress import github_pr_reader
else:
    from kagari_vnext_progress import github_pr_reader


def _exact_dir(path: Path, name: str) -> Path:
    try:
        if (not path.is_absolute() or not path.is_dir()
                or path.is_symlink() or path.resolve(strict=True) != path):
            raise MainError(name)
    except (OSError, RuntimeError):
        raise MainError(name) from None
    return path


def load_selection(path: Path) -> dict[str, Any]:
    """Trusted operator's explicit Issue selection, never an authority mint."""
    try:
        if (not path.is_absolute() or path.is_symlink()
                or not path.is_file() or path.stat().st_size > 64_000):
            raise ValueError("invalid plan file")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise MainError("MAIN_PLAN_INVALID") from None
    required = {"schema_version", "project_root", "repository", "runtime_home", "tasks"}
    optional = {"remote", "agent", "model", "opencode_bin", "github_bin"}
    if (type(data) is not dict or data.keys() - required - optional
            or not required <= data.keys()
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1):
        raise MainError("MAIN_PLAN_INVALID")
    if (type(data["repository"]) is not str or not re.fullmatch(
            r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", data["repository"])):
        raise MainError("MAIN_PLAN_INVALID")
    for name in ("project_root", "runtime_home"):
        if type(data[name]) is not str or not Path(data[name]).is_absolute():
            raise MainError("MAIN_PLAN_INVALID")
    for name in ("remote", "agent", "opencode_bin", "github_bin"):
        if name in data and (type(data[name]) is not str or not data[name]):
            raise MainError("MAIN_PLAN_INVALID")
    model = data.get("model")
    if (model is not None and (
            type(model) is not dict
            or model.keys() != {"provider_id", "model_id"}
            or any(type(model[k]) is not str or not model[k]
                   for k in ("provider_id", "model_id")))):
        raise MainError("MAIN_PLAN_INVALID")
    tasks = data["tasks"]
    if type(tasks) is not list or not 1 <= len(tasks) <= 25:
        raise MainError("MAIN_PLAN_INVALID")
    seen_issues: set[int] = set()
    seen_ports: set[int] = set()
    for item in tasks:
        required_task = {
            "issue", "branch_ref", "base_revision", "contract_id", "port"}
        if (type(item) is not dict or not required_task <= item.keys()
                or item.keys() - required_task - {"pr_number", "evidence",
                                                 "cleanup"}):
            raise MainError("MAIN_PLAN_INVALID")
        number = item["issue"]
        port = item["port"]
        if (type(number) is not int or not 1 <= number <= 10**9
                or number in seen_issues
                or type(port) is not int or not 1024 <= port <= 65535
                or port in seen_ports):
            raise MainError("MAIN_PLAN_INVALID")
        if (type(item["base_revision"]) is not str or
                not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})",
                                 item["base_revision"])):
            raise MainError("MAIN_PLAN_INVALID")
        if (type(item["contract_id"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", item["contract_id"])):
            raise MainError("MAIN_PLAN_INVALID")
        try:
            validate_branch_ref(item["branch_ref"])
        except (TaskRecordError, TypeError):
            raise MainError("MAIN_PLAN_INVALID") from None
        if "pr_number" in item and (
                type(item["pr_number"]) is not int
                or item["pr_number"] < 1):
            raise MainError("MAIN_PLAN_INVALID")
        evidence = item.get("evidence", [])
        if type(evidence) is not list or len(evidence) > 64:
            raise MainError("MAIN_PLAN_INVALID")
        for evidence_ref in evidence:
            if (type(evidence_ref) is not dict
                    or evidence_ref.keys() != {"evidence_id", "subject"}
                    or type(evidence_ref["evidence_id"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}",
                                        evidence_ref["evidence_id"])
                    or type(evidence_ref["subject"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",
                                        evidence_ref["subject"])):
                raise MainError("MAIN_PLAN_INVALID")
        # Cleanup is an explicit Issue-specific lifecycle decision, not an
        # implicit consequence of observing any merged PR or idle Session.
        cleanup = item.get("cleanup")
        if cleanup is not None:
            if (type(cleanup) is not dict
                    or cleanup.keys() != {"pull_request", "base_ref"}
                    or type(cleanup["pull_request"]) is not int
                    or cleanup["pull_request"] < 1
                    or type(cleanup["base_ref"]) is not str
                    or cleanup["base_ref"] == item["branch_ref"]):
                raise MainError("MAIN_PLAN_INVALID")
            try:
                validate_branch_ref(cleanup["base_ref"])
            except TaskRecordError:
                raise MainError("MAIN_PLAN_INVALID") from None
            if (item.get("pr_number") is not None
                    and item["pr_number"] != cleanup["pull_request"]):
                raise MainError("MAIN_PLAN_INVALID")
        seen_issues.add(number)
        seen_ports.add(port)
    return data


def build_main(plan: dict[str, Any]) -> RepositoryMain:
    _exact_dir(Path(plan["project_root"]), "PRIMARY_PROJECT_REQUIRED")
    try:
        store = MetadataStore(
            Path(plan["project_root"]), plan["repository"],
            plan.get("remote", "origin"))
    except (MetadataRefError, TypeError, ValueError):
        raise MainError("MAIN_PROJECT_INVALID") from None
    approved = {
        item["issue"]: ApprovedTask(
            item["issue"], item["branch_ref"], item["base_revision"],
            item["contract_id"]) for item in plan["tasks"]
    }
    ports = {item["issue"]: item["port"] for item in plan["tasks"]}
    configured_model = plan.get("model")
    model = (
        (configured_model["provider_id"], configured_model["model_id"])
        if configured_model is not None else None
    )
    pr_for_issue = {
        item["issue"]: item["pr_number"]
        for item in plan["tasks"] if "pr_number" in item}
    evidence_for_issue = {
        item["issue"]: tuple(EvidenceRef(**v) for v in item["evidence"])
        for item in plan["tasks"] if item.get("evidence")}
    return RepositoryMain(
        store, approved=approved,
        read_issue=github_issue_reader(
            plan["repository"], gh=plan.get("github_bin", "gh")),
        runtime_home=Path(plan["runtime_home"]),
        port_for_issue=ports.__getitem__,
        opencode_bin=plan.get("opencode_bin", "opencode"),
        agent=plan.get("agent", "build"), model=model,
        pr_for_issue=pr_for_issue, evidence_for_issue=evidence_for_issue,
        read_pr=(github_pr_reader(
            plan["repository"], gh=plan.get("github_bin", "gh"))
            if pr_for_issue else None),
    )


def build_cleanup_handlers(
    main: RepositoryMain, plan: dict[str, Any],
) -> dict[int, Callable[[], CleanupReceipt]]:
    """Explicit per-Issue retirements, never implicit from an open/merged PR.

    The authoritative #286 cleanup validates the exact #191 Contract, PR
    merge ancestry, registered Worktree, clean files and stopped Session.
    Main only routes independently authorized Issue selections to it.
    """
    if type(main) is not RepositoryMain or type(plan) is not dict:
        raise MainError("MAIN_CLEANUP_CONFIGURATION_INVALID")
    handlers: dict[int, Callable[[], CleanupReceipt]] = {}
    for item in plan.get("tasks", []):
        policy = item.get("cleanup")
        if policy is None:
            continue
        issue = item["issue"]
        choice = main.approved.get(issue)
        if (choice is None or choice.branch_ref != item["branch_ref"]
                or choice.base_revision != item["base_revision"]
                or choice.contract_id != item["contract_id"]):
            raise MainError("MAIN_CLEANUP_CONFIGURATION_INVALID")
        backend = IssueWorktreeCleanup(
            IssueWorktreeAllocator(main.store),
            read_pull_request=github_pull_request_reader(
                main.store.repository, gh=plan.get("github_bin", "gh")),
            session_released=stopped_session_from_runtime(
                main._runtime(issue)),
        )

        def cleanup_one(*, selected=choice, executor=backend,
                        request=policy) -> CleanupReceipt:
            # The returned callable can also be invoked directly by a trusted
            # host. It must not bypass the same-repository Main singleton.
            # Hold the guard across the *entire* potentially destructive #286
            # cleanup, not just its first preflight check.
            with main._main_owner_guard:
                owner = main._main_owner
                if owner is None:
                    raise MainError("MAIN_OWNER_REQUIRED")
                owner._assert_owned(main)
                return executor.cleanup(
                    task=str(selected.issue),
                    base_revision=selected.base_revision,
                    branch_ref=selected.branch_ref,
                    expected_contract_id=selected.contract_id,
                    pull_request=request["pull_request"],
                    base_ref=request["base_ref"])

        handlers[issue] = cleanup_one
    return handlers


def _host_state_home() -> Path:
    """User-owned, stable across checkouts and independent of HOME/XDG overrides.

    This host identity is cooperative one-host/UID isolation, not multi-host
    election or exclusion of a malicious process running as this same UID.
    """
    return Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".local" / "state"


def _owned_directory(path: Path, *, private: bool) -> None:
    """Fail closed on foreign, symlinked or permission-unsafe lock namespace."""
    try:
        st = path.lstat()
    except OSError:
        raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE") from None
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
            or st.st_mode & 0o022
            or (private and st.st_mode & 0o077)):
        raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE")


def _host_repo_lock(repository: str) -> Path:
    """One stable lock inode for each GitHub repository on the supported host.

    A per-checkout .git lock is insufficient: two different local clones of
    the same upstream would otherwise both start a Main. The trusted host
    namespace uses an owner-only directory, not an operator-selected path.
    """
    if (type(repository) is not str
            or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository)):
        raise MainError("MAIN_REPOSITORY_INVALID")
    home = _host_state_home()
    if not home.is_absolute() or home.is_symlink():
        raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE")
    if not home.is_dir():
        # A fresh Linux user (including an ephemeral GitHub CI runner) may
        # have no ~/.local/state yet. Bootstrap ONLY the OS passwd-derived
        # user namespace; never allow HOME, XDG, Project config or a patched
        # arbitrary path to select a host repository lock namespace.
        owner_home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        if (home != owner_home / ".local" / "state"
                or not owner_home.is_absolute()
                or owner_home.is_symlink()
                or not owner_home.is_dir()
                or owner_home.resolve(strict=True) != owner_home):
            raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE")
        _owned_directory(owner_home, private=False)
        for ancestor in (home.parent, home):
            try:
                ancestor.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError:
                raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE") from None
            if (ancestor.is_symlink()
                    or ancestor.resolve(strict=True) != ancestor):
                raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE")
            _owned_directory(ancestor, private=False)
    if (not home.is_dir() or home.resolve(strict=True) != home):
        raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE")
    _owned_directory(home, private=False)
    for ancestor in (home / "kagari", home / "kagari" / "repository-main-locks"):
        try:
            ancestor.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE") from None
        _owned_directory(ancestor, private=True)
    key = hashlib.sha256(
        ("github.com/" + repository.lower()).encode("ascii")).hexdigest()
    return home / "kagari" / "repository-main-locks" / (key + ".lock")


class MainSupervisor:
    """One local active Main, with host-owned lock held across run cycles."""

    def __init__(
        self, main: RepositoryMain, *,
        cleanup_for_issue: dict[int, Callable[[], CleanupReceipt]] | None = None,
        observe_only: bool = False,
    ) -> None:
        requested = dict(cleanup_for_issue or {})
        if (type(observe_only) is not bool
                or any(n not in main.approved or not callable(f)
                       for n, f in requested.items())):
            raise MainError("MAIN_CLEANUP_CONFIGURATION_INVALID")
        self.main = main
        self.cleanup_for_issue = requested
        self.observe_only = observe_only
        self._entered = False
        self._file = None
        self._lock_path: Path | None = None
        self._owner_pid: int | None = None
        self._owner_repository: str | None = None

    def _assert_owned(self, main: RepositoryMain) -> None:
        """Prove this exact Main still holds the canonical host flock.

        A bare Boolean/PID or a saved runtime JSON cannot grant dispatch.
        Check the original open file description, verified inode and live PID.
        Re-flocking the same descriptor succeeds only while ownership is
        available; a foreign lock or replaced path fails closed.
        Called under main._main_owner_guard for the entire dispatch/cycle.
        """
        if (type(main) is not RepositoryMain or self.main is not main
                or not self._entered or self.observe_only
                or main._main_owner is not self
                or self._file is None or self._file.closed
                or self._owner_pid != os.getpid()
                or self._owner_repository != main.store.repository
                or self._lock_path is None):
            raise MainError("MAIN_OWNER_REQUIRED")
        try:
            expected = _host_repo_lock(main.store.repository)
            if self._lock_path != expected:
                raise MainError("MAIN_OWNER_UNVERIFIED")
            current = self._lock_path.lstat()
            original = os.fstat(self._file.fileno())
            if (not stat.S_ISREG(current.st_mode)
                    or not stat.S_ISREG(original.st_mode)
                    or current.st_ino != original.st_ino
                    or current.st_dev != original.st_dev
                    or current.st_uid != os.geteuid()
                    or current.st_nlink != 1
                    or current.st_mode & 0o177):
                raise MainError("MAIN_OWNER_UNVERIFIED")
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, RuntimeError):
            raise MainError("MAIN_OWNER_UNVERIFIED") from None

    def __enter__(self) -> MainSupervisor:
        if self._entered:
            raise MainError("MAIN_ALREADY_ACTIVE")
        if self.observe_only:
            # Observation is a reader: no repository lock file, no exclusive
            # owner and no source/Task lifecycle side effect. Multiple
            # observers may coexist with a Main dispatch owner.
            self._entered = True
            return self
        project = _exact_dir(self.main.store.root, "PRIMARY_PROJECT_REQUIRED")
        _exact_dir(project / ".git", "PRIMARY_GIT_DIRECTORY_REQUIRED")
        path = _host_repo_lock(self.main.store.repository)
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError:
            raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE") from None
        self._file = os.fdopen(fd, "rb+")
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                    or st.st_nlink != 1 or st.st_mode & 0o177):
                raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE")
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if type(self.main) is RepositoryMain:
                with self.main._main_owner_guard:
                    if self.main._main_owner is not None:
                        raise MainError("MAIN_ALREADY_ACTIVE")
                    self._owner_pid = os.getpid()
                    self._owner_repository = self.main.store.repository
                    self._lock_path = path
                    self._entered = True
                    self.main._main_owner = self
            else:
                # Minimal lock-only host fixture cannot dispatch a Task, but
                # it still owns an OS lock and must be fork-safe on release.
                self._entered = True
                self._owner_pid = os.getpid()
                self._owner_repository = self.main.store.repository
                self._lock_path = path
        except MainError:
            self._file.close()
            self._file = None
            raise
        except (OSError, BlockingIOError):
            self._file.close()
            self._file = None
            raise MainError("MAIN_ALREADY_ACTIVE") from None
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._owner_pid is not None and os.getpid() != self._owner_pid:
            # flock is bound to the *open file description*: a child after
            # fork() inherits it, so LOCK_UN here would unlock the parent's
            # active Main. Close ONLY this child's duplicate; never LOCK_UN.
            # Avoid the inherited RLock too: it may belong to a vanished
            # thread in a multi-threaded parent process.
            if self._file is not None:
                self._file.close()
                self._file = None
            if type(self.main) is RepositoryMain and self.main._main_owner is self:
                self.main._main_owner = None
            self._entered = False
            self._lock_path = None
            self._owner_pid = None
            self._owner_repository = None
            return

        def release() -> None:
            if type(self.main) is RepositoryMain and self.main._main_owner is self:
                self.main._main_owner = None
            self._entered = False
            self._lock_path = None
            self._owner_pid = None
            self._owner_repository = None
            if self._file is not None:
                try:
                    fcntl.flock(self._file, fcntl.LOCK_UN)
                finally:
                    self._file.close()
                    self._file = None
        if type(self.main) is RepositoryMain:
            with self.main._main_owner_guard:
                release()
        else:
            release()

    def cycle(self, *, dispatch: bool = True) -> dict[str, Any]:
        if not self._entered:
            raise MainError("MAIN_NOT_ACTIVE")
        if dispatch and self.observe_only:
            raise MainError("MAIN_OBSERVE_ONLY")
        if dispatch and type(self.main) is not RepositoryMain:
            raise MainError("MAIN_OWNER_REQUIRED")
        guard = self.main._main_owner_guard if dispatch else nullcontext()
        with guard:
            if dispatch:
                self._assert_owned(self.main)
            entries = []
            for issue in sorted(self.main.approved):
                if dispatch and issue in self.cleanup_for_issue:
                    try:
                        receipt = self.cleanup_for_issue[issue]()
                        if type(receipt) is not CleanupReceipt:
                            raise IssueWorktreeError("CLEANUP_RESULT_INVALID")
                        entries.append({
                            "issue": issue, "operation": "cleanup",
                            "status": "ok", "cleanup_state": receipt.state,
                            "pull_request": receipt.pull_request,
                            "worktree": receipt.worktree,
                        })
                    except (IssueWorktreeError, MainError) as err:
                        # A refused or uncertain cleanup must never terminate
                        # the repository Main or dispatch this Issue again.
                        entries.append({
                            "issue": issue, "operation": "cleanup",
                            "status": "blocked", "reason": err.code,
                        })
                    except Exception:
                        # A host PR reader/session checker may itself fail
                        # unexpectedly. Fail this Issue closed, continue others.
                        entries.append({
                            "issue": issue, "operation": "cleanup",
                            "status": "blocked",
                            "reason": "CLEANUP_HOST_UNAVAILABLE",
                        })
                    continue
                try:
                    if dispatch:
                        launched = self.main.dispatch(issue)
                        entries.append({
                            "issue": issue, "status": "ok",
                            "worktree_state": launched["worktree_state"],
                            "session_state": launched["session_state"],
                            "prompt_state": launched["prompt_state"],
                            "context": launched["context"],
                            "runtime": launched["runtime"],
                        })
                    else:
                        entries.append({
                            "issue": issue, "status": "ok",
                            "observation": self.main.observe(issue)})
                except MainError as err:
                    # One blocked Task does not expand authorization or silently
                    # suppress observations of other approved Tasks.
                    entries.append({
                        "issue": issue, "status": "blocked", "reason": err.code})
            return {
                "schema_version": 1,
                "repository": self.main.store.repository,
                "operation": "main-cycle",
                "mode": "dispatch" if dispatch else "observe",
                "tasks": entries,
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KAGARI local repository Main runner")
    parser.add_argument("--config", type=Path, required=True,
                        help="Trusted, explicitly approved Issue list; not Task authority")
    parser.add_argument("--interval", type=int, default=30,
                        help="Polling interval (seconds) for --follow, default 30")
    parser.add_argument("--follow", action="store_true",
                        help="Remain the one active Main and inspect/continue allowed Tasks")
    parser.add_argument("operation", nargs="?", choices=("dispatch", "observe"),
                        default="dispatch")
    args = parser.parse_args(argv)
    try:
        plan = load_selection(args.config)
        host = build_main(plan)
        if args.follow and not 2 <= args.interval <= 3600:
            raise MainError("MAIN_INTERVAL_INVALID")
        with MainSupervisor(
            host, cleanup_for_issue=build_cleanup_handlers(host, plan),
            observe_only=args.operation == "observe",
        ) as runner:
            def emit_cycle() -> None:
                result = runner.cycle(dispatch=args.operation == "dispatch")
                print(json.dumps(result, sort_keys=True, ensure_ascii=False),
                      flush=True)

            if not args.follow:
                emit_cycle()
                return 0

            stopped = threading.Event()
            previous = signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            try:
                while not stopped.is_set():
                    emit_cycle()
                    stopped.wait(args.interval)
            except KeyboardInterrupt:
                pass
            finally:
                signal.signal(signal.SIGTERM, previous)
            return 0
    except MainError as error:
        print(json.dumps({"status": "error", "error_code": error.code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
