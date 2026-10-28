#!/usr/bin/env python3
"""Opt-in KAGARI vNext host-prepare lifecycle (#304), NOT a full v4 installer.

Host-owned, externally packaged entrypoint. A successful prepare-install or
prepare-repair means the Issue-linked Worktree namespace is ready, not that a
KAGARI Main/Task/OpenCode runtime was installed or authorized. GA untouched.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Iterator

import kagari_vnext_worktree_ignore as ignore

PROFILE = "linked-worktree-mvi-preview"
KIND = "KAGARI_VNEXT_HOST_IGNORE_PREPARED"
STATE_DIR = "kagari-vnext-host-prep"
MAX_RECORD = 4096
SCHEMA = 1


class HostBlocked(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(detail or code)


def need(ok: bool, code: str) -> None:
    if not ok:
        raise HostBlocked(code)


def canonical(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def private_dir(path: Path) -> Path:
    need(path.is_absolute() and not path.is_symlink() and path.is_dir(),
         "STATE_HOME_INVALID")
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except OSError as exc:
        raise HostBlocked("STATE_HOME_UNAVAILABLE", str(exc)) from exc
    need(resolved == path and stat.S_ISDIR(info.st_mode)
         and info.st_uid == os.geteuid()
         and stat.S_IMODE(info.st_mode) == 0o700, "STATE_HOME_NOT_PRIVATE")
    return path


def source_sha() -> str:
    path = Path(ignore.__file__)
    need(path.parent.resolve(strict=True) ==
         Path(__file__).resolve(strict=True).parent,
         "HOST_HELPER_NOT_SAME_PACKAGE")
    data = path.read_bytes()
    need(len(data) <= 1024 * 1024, "SOURCE_TOO_LARGE")
    return hashlib.sha256(data).hexdigest()


def project_identity(project: Path) -> dict[str, Any]:
    project, _git_dir = ignore.primary_project(project)
    meta = project.stat()
    return {"project_root": str(project),
            "project_device": meta.st_dev, "project_inode": meta.st_ino}


def state_name(identity: dict[str, Any]) -> str:
    key = (identity["project_root"] + "\0"
           + str(identity["project_device"]) + "\0"
           + str(identity["project_inode"])).encode("utf-8")
    return "attach-" + hashlib.sha256(key).hexdigest()[:32] + ".json"


def expected_state(identity: dict[str, Any]) -> dict[str, Any]:
    return {**identity, "schema": SCHEMA, "kind": KIND,
            "profile": PROFILE, "helper_sha256": source_sha(),
            "authority": "PREFLIGHT_ONLY",
            "runtime_installed": False, "task_execution_authorized": False}


def open_state_dir(state_home: Path, *, create: bool) -> int | None:
    parent_fd = os.open(state_home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if create:
            try:
                os.mkdir(STATE_DIR, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
        try:
            fd = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=parent_fd)
        except FileNotFoundError:
            return None
    finally:
        os.close(parent_fd)
    info = os.fstat(fd)
    if not (stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700):
        os.close(fd)
        raise HostBlocked("HOST_STATE_NAMESPACE_UNSAFE")
    return fd


def stored(fd: int, name: str, expected: dict[str, Any]) -> bool:
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                          dir_fd=fd)
    except FileNotFoundError:
        return False
    try:
        info = os.fstat(file_fd)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
             and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o600
             and info.st_size <= MAX_RECORD, "HOST_STATE_FILE_UNSAFE")
        with os.fdopen(file_fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_RECORD + 1)
        need(len(raw) == info.st_size, "HOST_STATE_CHANGED")
        try:
            document = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise HostBlocked("HOST_STATE_INVALID") from exc
        need(type(document) is dict and raw == canonical(document),
             "HOST_STATE_NONCANONICAL")
        need(document == expected, "HOST_STATE_IDENTITY_CHANGED")
        return True
    finally:
        os.close(file_fd)


def publish(fd: int, name: str, expected: dict[str, Any], namespace: Path) -> None:
    data = canonical(expected)
    need(len(data) <= MAX_RECORD, "HOST_STATE_TOO_LARGE")
    temp_fd, temp = tempfile.mkstemp(prefix="kagari-host-prep-stage-", dir=namespace)
    try:
        with os.fdopen(temp_fd, "wb") as writer:
            writer.write(data)
            writer.flush()
            os.fchmod(writer.fileno(), 0o600)
            os.fsync(writer.fileno())
        try:
            os.link(temp, name, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError:
            raise HostBlocked("HOST_STATE_APPEARED") from None
        os.fsync(fd)
    finally:
        Path(temp).unlink(missing_ok=True)


@contextmanager
def state_lock(fd: int) -> Iterator[None]:
    lock_fd = os.open(".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                      0o600, dir_fd=fd)
    try:
        info = os.fstat(lock_fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
             "HOST_LOCK_UNSAFE")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def operate(project: Path, state_home: Path, action: str,
            profile: str = PROFILE) -> dict[str, Any]:
    need(profile == PROFILE, "EXPLICIT_LINKED_MVI_PROFILE_REQUIRED")
    need(action in ("plan", "doctor", "install", "repair"), "UNSUPPORTED_ACTION")
    identity = project_identity(project)
    home = private_dir(state_home)
    root = Path(identity["project_root"])
    need(not home.is_relative_to(root) and not root.is_relative_to(home),
         "HOST_STATE_MUST_BE_OUTSIDE_PROJECT")
    expected = expected_state(identity)
    pinned_project = (identity["project_device"], identity["project_inode"])
    name = state_name(identity)
    is_write = action in ("install", "repair")
    namespace_fd = open_state_dir(home, create=is_write)
    if namespace_fd is None:
        preflight = ignore.operate(
            root, "doctor", expected_project_identity=pinned_project)
        return result(action, "ABSENT" if not is_write else "BLOCKED",
                      identity, home, preflight, attached=False)
    try:
        with state_lock(namespace_fd):
            namespace = home / STATE_DIR
            need(not any(entry.startswith("kagari-host-prep-stage-")
                         for entry in os.listdir(namespace_fd)),
                 "HOST_STAGE_REQUIRES_REVIEW")
            attached = stored(namespace_fd, name, expected)
            if not is_write:
                preflight = ignore.operate(
                    root, "doctor", expected_project_identity=pinned_project)
                status = ("ABSENT" if not attached else
                          "READY" if preflight["status"] == "READY"
                          else "NEEDS_REPAIR")
                return result(action, status, identity, home, preflight,
                              attached=attached)
            # Host preflight, never an implicit Task or model approval.
            preflight = ignore.operate(
                root, action, expected_project_identity=pinned_project)
            need(preflight["git_ignore_effective"],
                 "WORKTREE_IGNORE_NOT_EFFECTIVE")
            need(project_identity(root) == identity,
                 "PROJECT_IDENTITY_CHANGED_AFTER_PREFLIGHT")
            if not attached:
                publish(namespace_fd, name, expected, namespace)
            return result(action,
                          "READY" if attached else "PREPARED",
                          identity, home, preflight, attached=True)
    finally:
        os.close(namespace_fd)


def result(action: str, status: str, identity: dict[str, Any],
           state_home: Path, preflight: dict[str, Any], *,
           attached: bool) -> dict[str, Any]:
    return {"operation": action, "status": status, "profile": PROFILE,
            "project_root": identity["project_root"],
            "host_record": str(state_home / STATE_DIR / state_name(identity)),
            "host_preflight_recorded": attached,
            "ignore": preflight,
            "runtime_installed": False,
            "task_execution_authorized": False,
            "model_or_session_started": False,
            "git_stage_or_commit_performed": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "doctor", "install", "repair"))
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--state-home", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = operate(args.project_root, args.state_home,
                         args.operation, args.profile)
    except (HostBlocked, ignore.IgnoreBlocked, OSError, ValueError, UnicodeError) as exc:
        print(json.dumps({"operation": args.operation, "status": "BLOCKED",
                          "code": getattr(exc, "code", "HOST_FS_ERROR"),
                          "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
