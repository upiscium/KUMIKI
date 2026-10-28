#!/usr/bin/env python3
"""Source-only, single-host Main ownership/eligibility preview; never an executor.

The host must pin one external state home across invocations and restarts. No
GitHub verification, Task/Worktree/Session calls, or Project writes exist here.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
from typing import Any

PROFILE = "linked-worktree-mvi-preview"
NAMESPACE = "kagari-vnext-main"
MAX_RECORD = 16384
MAX_ISSUES = 128
MAX_ISSUE = 2147483647
# Linux local filesystem magic numbers. Other filesystems fail closed. Overlay
# support assumes host-local backing storage, an operator deployment obligation.
LOCAL_FILESYSTEMS = {0xEF53, 0x58465342, 0x9123683E, 0x01021994, 0x794C7630}


class Blocked(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def need(ok: bool, code: str) -> None:
    if not ok:
        raise Blocked(code)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def issue_scope(values: list[int]) -> list[int]:
    need(0 < len(values) <= MAX_ISSUES and all(
        type(i) is int and 0 < i <= MAX_ISSUE for i in values), "INVALID_ISSUE_SCOPE")
    need(len(set(values)) == len(values), "DUPLICATE_ISSUE")
    return sorted(values)


def local_fs(fd: int) -> None:
    need(sys.platform == "linux", "LINUX_REQUIRED")
    # fstatfs writes a native struct statfs (<=256 bytes on supported Linux).
    buf = ctypes.create_string_buffer(256)
    call = ctypes.CDLL(None, use_errno=True).fstatfs
    call.argtypes = (ctypes.c_int, ctypes.c_void_p)
    call.restype = ctypes.c_int
    need(call(fd, buf) == 0, "FILESYSTEM_UNKNOWN")
    magic = ctypes.c_long.from_buffer(buf).value & 0xFFFFFFFF
    need(magic in LOCAL_FILESYSTEMS, "UNSUPPORTED_FILESYSTEM")


def directory(path: Path, *, private: bool = False) -> int:
    need(path.is_absolute() and path.resolve(strict=True) == path,
         "NONCANONICAL_DIRECTORY")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        need(info.st_uid == os.geteuid() and not info.st_mode & 0o022,
             "DIRECTORY_OWNER_OR_MODE_UNSAFE")
        if private:
            need(stat.S_IMODE(info.st_mode) == 0o700, "STATE_HOME_NOT_PRIVATE")
        local_fs(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def regular(fd: int, *, private: bool) -> os.stat_result:
    info = os.fstat(fd)
    need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
         and info.st_nlink == 1 and not info.st_mode & 0o022,
         "FILE_OWNER_MODE_OR_LINK_UNSAFE")
    if private:
        need(stat.S_IMODE(info.st_mode) == 0o600, "STATE_FILE_NOT_PRIVATE")
    return info


def same_path(path: Path, fd: int) -> None:
    need(path.resolve(strict=True) == path, "PATH_CHANGED")
    live, original = path.lstat(), os.fstat(fd)
    need((live.st_dev, live.st_ino) == (original.st_dev, original.st_ino),
         "PATH_CHANGED")


def git(root_fd: int, *args: str) -> bytes:
    # Never inherit cwd, GIT_*, HOME config, PATH entries, hooks, or fsmonitor.
    # NixOS may have no /usr/bin/git. Use only root-managed system
    # installations, never an arbitrary Git binary inherited from PATH.
    binary = next((p for p in ("/run/current-system/sw/bin/git",
                               "/usr/bin/git", "/bin/git")
                   if os.path.isfile(p) and os.access(p, os.X_OK)), None)
    need(binary is not None, "SYSTEM_GIT_REQUIRED")
    env = {"PATH": os.defpath, "HOME": "/nonexistent", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_COUNT": "0", "GIT_OPTIONAL_LOCKS": "0",
           "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        [binary, "--no-optional-locks", "-C", f"/proc/self/fd/{root_fd}",
         "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd="/", env=env, pass_fds=(root_fd,), capture_output=True,
        timeout=10, check=False)
    need(result.returncode == 0, "GIT_OBSERVATION_FAILED")
    return result.stdout


def project_identity(root: Path, root_fd: int, stack: ExitStack,
                     repository: str) -> dict[str, Any]:
    git_path = root / ".git"
    git_fd = directory(git_path)
    stack.callback(os.close, git_fd)
    # Primary repository only; no gitfile, linked Worktree, symlinked config,
    # alternate common-dir, or external repository-config includes.
    for name in ("HEAD", "config"):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=git_fd)
        try:
            need(regular(fd, private=False).st_size <= 1024 * 1024,
                 "GIT_METADATA_TOO_LARGE")
        finally:
            os.close(fd)
    for name in ("commondir", "config.worktree"):
        try:
            os.stat(name, dir_fd=git_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise Blocked("UNSUPPORTED_GIT_LAYOUT")
    config = git(root_fd, "config", "--local", "--no-includes", "--null", "--list")
    keys = [entry.split(b"\n", 1)[0].lower() for entry in config.split(b"\0") if entry]
    need(not any(k == b"include.path" or k.startswith(b"includeif.") for k in keys),
         "GIT_CONFIG_INCLUDES_UNSUPPORTED")
    observed = git(root_fd, "rev-parse", "--show-toplevel", "--absolute-git-dir",
                   "--path-format=absolute", "--git-common-dir", "--is-bare-repository")
    need(observed.decode("utf-8").splitlines() ==
         [str(root), str(git_path), str(git_path), "false"], "PRIMARY_PROJECT_REQUIRED")
    urls = git(root_fd, "config", "--local", "--no-includes", "--get-all",
               "remote.origin.url").decode("utf-8").splitlines()
    # This is a local locator consistency check, NOT a live GitHub assertion.
    allowed = {f"https://github.com/{repository}", f"https://github.com/{repository}.git",
               f"git@github.com:{repository}", f"git@github.com:{repository}.git",
               f"ssh://git@github.com/{repository}", f"ssh://git@github.com/{repository}.git"}
    need(len(urls) == 1 and urls[0] in allowed, "REPOSITORY_IDENTITY_MISMATCH")
    same_path(root, root_fd)
    same_path(git_path, git_fd)
    p, g = os.fstat(root_fd), os.fstat(git_fd)
    return {"project_root": str(root), "project_device": p.st_dev,
            "project_inode": p.st_ino, "common_git_dir": str(git_path),
            "common_git_device": g.st_dev, "common_git_inode": g.st_ino}


def project_key(identity: dict[str, Any]) -> str:
    # Deliberately excludes Issue scope, repository label, and state-home path.
    return digest(canonical([identity[k] for k in (
        "project_device", "project_inode", "common_git_device", "common_git_inode")]))


class Controller:
    """One synchronous owner. All descriptors/locks are process-local, CLOEXEC.

    Do not fork or pass this object between processes. The CLI never does so.
    """

    def __init__(self, project: Path, state_home: Path, repository: str,
                 issues: list[int], profile: str):
        need(profile == PROFILE, "EXPLICIT_PREVIEW_PROFILE_REQUIRED")
        need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}",
                          repository) is not None
             and repository.split("/")[1] not in (".", ".."), "INVALID_REPOSITORY")
        self.issues = issue_scope(issues)
        self.project, self.home = project, state_home
        self.repository = repository
        self.stack = ExitStack()
        self.namespace_fd: int | None = None
        self.lock_fd: int | None = None
        self.record: dict[str, Any] | None = None
        self.raw: bytes | None = None
        self.started = False
        self.busy = False

    def __enter__(self) -> Controller:
        try:
            self.root_fd = directory(self.project)
            self.stack.callback(os.close, self.root_fd)
            self.identity = project_identity(self.project, self.root_fd,
                                             self.stack, self.repository)
            self.home_fd = directory(self.home, private=True)
            self.stack.callback(os.close, self.home_fd)
            need(not self.home.is_relative_to(self.project)
                 and not self.project.is_relative_to(self.home), "STATE_MUST_BE_EXTERNAL")
            self.key = project_key(self.identity)
            self.binding = {"profile": PROFILE, "repository": self.repository,
                            "project": self.identity, "eligible_issues": self.issues,
                            "scope_sha256": digest(canonical(self.issues)),
                            "state_home": str(self.home)}
            # This second read-only lock protects against live split-brain even
            # if a caller erroneously selects a different state directory.
            try:
                fcntl.flock(self.root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.busy = True
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *_: Any) -> None:
        # An exception does NOT mark ACTIVE clean. Only release() can do that.
        self.stack.close()

    def _namespace(self, create: bool) -> bool:
        need(not self.busy, "MAIN_ALREADY_LIVE")
        same_path(self.home, self.home_fd)
        if create:
            try:
                os.mkdir(NAMESPACE, 0o700, dir_fd=self.home_fd)
                os.fsync(self.home_fd)
            except FileExistsError:
                pass
        try:
            fd = os.open(NAMESPACE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=self.home_fd)
        except FileNotFoundError:
            return False
        self.stack.callback(os.close, fd)
        self.namespace_fd = fd
        info = os.fstat(fd)
        need(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
             "NAMESPACE_UNSAFE")
        local_fs(fd)
        need(all(re.fullmatch(r"[0-9a-f]{64}\.(lock|json)", name)
                 for name in os.listdir(fd)), "UNKNOWN_STATE_PATH_REQUIRES_REVIEW")
        name = self.key + ".lock"
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            lock_fd = os.open(name, flags | (os.O_CREAT if create else 0),
                              0o600, dir_fd=fd)
        except FileNotFoundError:
            need(self.key + ".json" not in os.listdir(fd), "ORPHAN_RECORD_REQUIRES_REVIEW")
            return False
        self.stack.callback(os.close, lock_fd)
        self.lock_fd = lock_fd
        need(regular(lock_fd, private=True).st_size == 0, "LOCK_FILE_NOT_EMPTY")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Blocked("MAIN_ALREADY_LIVE") from None
        os.fsync(fd) if create else None
        self._read()
        return True

    def _paths(self) -> None:
        same_path(self.project, self.root_fd)
        p = self.identity
        live = (self.project / ".git").lstat()
        need((live.st_dev, live.st_ino) == (p["common_git_device"], p["common_git_inode"])
             and stat.S_ISDIR(live.st_mode), "PROJECT_IDENTITY_CHANGED")
        same_path(self.home, self.home_fd)
        assert self.namespace_fd is not None and self.lock_fd is not None
        same_path(self.home / NAMESPACE, self.namespace_fd)
        same_path(self.home / NAMESPACE / (self.key + ".lock"), self.lock_fd)
        regular(self.lock_fd, private=True)

    def _read(self) -> None:
        self._paths()
        try:
            fd = os.open(self.key + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=self.namespace_fd)
        except FileNotFoundError:
            return
        try:
            info = regular(fd, private=True)
            need(info.st_size <= MAX_RECORD, "OWNER_RECORD_TOO_LARGE")
            raw = os.read(fd, MAX_RECORD + 1)
            need(len(raw) == info.st_size, "OWNER_RECORD_CHANGED")
        finally:
            os.close(fd)
        self.raw = raw
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError):
            raise Blocked("AMBIGUOUS_OWNER_RECORD_REQUIRES_REVIEW") from None
        need(type(record) is dict and canonical(record) == raw,
             "AMBIGUOUS_OWNER_RECORD_REQUIRES_REVIEW")
        need(set(record) == {"schema", "binding", "state", "owner", "previous_record_sha256"}
             and type(record["schema"]) is int and record["schema"] == 1
             and record["state"] in ("ACTIVE", "CLEAN", "RECONCILED")
             and type(record["owner"]) is dict
             and set(record["owner"]) == {"pid", "token"}
             and type(record["owner"]["pid"]) is int and record["owner"]["pid"] > 0
             and type(record["owner"]["token"]) is str
             and re.fullmatch(r"[0-9a-f]{64}", record["owner"]["token"]) is not None
             and (record["previous_record_sha256"] is None or
                  (type(record["previous_record_sha256"]) is str and
                   re.fullmatch(r"[0-9a-f]{64}", record["previous_record_sha256"]) is not None)),
             "AMBIGUOUS_OWNER_RECORD_REQUIRES_REVIEW")
        need(record["binding"] == self.binding, "CONTEXT_OR_ISSUE_SCOPE_DRIFT")
        self.record = record

    def _write(self, record: dict[str, Any]) -> None:
        self._paths()
        data = canonical(record)
        need(len(data) <= MAX_RECORD, "OWNER_RECORD_TOO_LARGE")
        stage = self.key + ".stage-" + secrets.token_hex(16)
        fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.namespace_fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self._paths()
            os.replace(stage, self.key + ".json", src_dir_fd=self.namespace_fd,
                       dst_dir_fd=self.namespace_fd)
            os.fsync(self.namespace_fd)
        finally:
            # A crash leaving a stage is deliberately NOT auto-repaired later.
            try:
                os.unlink(stage, dir_fd=self.namespace_fd)
            except FileNotFoundError:
                pass
        self.record, self.raw = record, data

    def result(self, status: str, issue: int | None = None) -> dict[str, Any]:
        if issue is not None:
            need(type(issue) is int and issue in self.issues, "ISSUE_NOT_ELIGIBLE")
        return {"status": status, "profile": PROFILE, "project_key": self.key,
                "binding": self.binding, "issue": issue,
                "issue_eligibility": "ELIGIBLE_INPUT_ONLY" if issue is not None else "BOUNDED_INPUT_ONLY",
                "github_issue_verification": "NOT_VERIFIED",
                "execution_authority": "NOT_EXECUTION_AUTHORIZED",
                "task_execution_authorized": False, "model_or_session_started": False,
                "project_writes": False, "record": self.record,
                "record_sha256": digest(self.raw) if self.raw is not None else None,
                "pid_is_authority": False}

    def inspect(self, issue: int | None = None) -> dict[str, Any]:
        # Validate selection before touching host state, even for a busy owner.
        self.result("PLAN", issue)
        if self.busy:
            return self.result("MAIN_ALREADY_LIVE_RECORD_NOT_READ", issue)
        exists = self._namespace(False)
        state = self.record["state"] if exists and self.record else "ABSENT"
        status = "RECONCILIATION_REQUIRED" if state == "ACTIVE" else state
        return self.result(status, issue)

    def start(self, issue: int | None = None) -> dict[str, Any]:
        need(not self.started, "OWNER_ALREADY_STARTED")
        self.result("PLAN", issue)
        self._namespace(True)
        need(self.record is None or self.record["state"] != "ACTIVE",
             "RECONCILIATION_REQUIRED")
        record = {"schema": 1, "binding": self.binding, "state": "ACTIVE",
                  "owner": {"pid": os.getpid(), "token": secrets.token_hex(32)},
                  "previous_record_sha256": digest(self.raw) if self.raw else None}
        self._write(record)
        self.started = True
        return self.result("HOLDING", issue)

    def release(self) -> dict[str, Any]:
        need(self.started and self.record is not None, "NO_ACTIVE_OWNER")
        record = {**self.record, "state": "CLEAN",
                  "previous_record_sha256": digest(self.raw)}
        self._write(record)
        self.started = False
        # Kernel locks are released by __exit__, never by a PID-file deletion.
        return self.result("CLEAN_RELEASE")

    def reconcile(self, expected_digest: str) -> dict[str, Any]:
        need(re.fullmatch(r"[0-9a-f]{64}", expected_digest) is not None,
             "EXACT_RECORD_DIGEST_REQUIRED")
        need(self._namespace(False) and self.record is not None,
             "NO_OWNER_TO_RECONCILE")
        need(self.record["state"] == "ACTIVE", "OWNER_NOT_UNCLEAN")
        need(digest(self.raw) == expected_digest, "RECONCILE_DIGEST_MISMATCH")
        # Both nonblocking kernel locks are held. Never kill/check a saved PID,
        # grant work, change scope, remove a lock, or recreate an unknown record.
        self._write({**self.record, "state": "RECONCILED",
                     "previous_record_sha256": expected_digest})
        return self.result("RECONCILED_NO_EXECUTION")


def emit(value: dict[str, Any]) -> None:
    print(canonical(value).decode("ascii"), end="", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "doctor", "hold", "reconcile"))
    parser.add_argument("--profile", required=True, choices=(PROFILE,))
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--state-home", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--eligible-issues", required=True, nargs="+", type=int)
    parser.add_argument("--issue", type=int)
    parser.add_argument("--hold-seconds", type=float)
    parser.add_argument("--expected-record-sha256")
    args = parser.parse_args(argv)
    stop = threading.Event()
    previous = {}
    try:
        need((args.action == "hold") == (args.hold_seconds is not None),
             "HOLD_REQUIRES_EXPLICIT_DURATION")
        if args.hold_seconds is not None:
            need(0 < args.hold_seconds <= 300, "HOLD_DURATION_OUT_OF_BOUNDS")
        need((args.action == "reconcile") == (args.expected_record_sha256 is not None),
             "RECONCILE_REQUIRES_EXACT_RECORD_DIGEST")
        # Install graceful handlers before writing ACTIVE. SIGKILL/crash leaves
        # ACTIVE and must go through explicit reconciliation after lock release.
        if args.action == "hold":
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous[sig] = signal.signal(sig, lambda *_: stop.set())
        with Controller(args.project_root, args.state_home, args.repository,
                        args.eligible_issues, args.profile) as control:
            if args.action in ("plan", "doctor"):
                result = control.inspect(args.issue)
                emit(result)
                return 2 if result["status"] in (
                    "RECONCILIATION_REQUIRED", "MAIN_ALREADY_LIVE_RECORD_NOT_READ") else 0
            if args.action == "reconcile":
                control.result("PLAN", args.issue)
                emit(control.reconcile(args.expected_record_sha256))
            else:
                emit(control.start(args.issue))
                stop.wait(args.hold_seconds)
                emit(control.release())
        return 0
    except (Blocked, OSError, ValueError, subprocess.SubprocessError) as exc:
        emit({"status": "BLOCKED", "code": exc.code if isinstance(exc, Blocked)
              else "UNSAFE_OR_UNAVAILABLE_CONTEXT", "execution_authority": "NOT_EXECUTION_AUTHORIZED",
              "github_issue_verification": "NOT_VERIFIED", "task_execution_authorized": False,
              "model_or_session_started": False})
        return 2
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
