#!/usr/bin/env python3
"""Explicit external host attachment preview for #256; NOT a v4 runtime install.

No package binary or Project script is executed. Install/repair reuse the host
prepare primitive. The sole attachment artifact is an atomic receipt containing
the pinned package descriptor (not a mutable launcher or a Project symlink).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Iterator

import kagari_vnext_host_prepare as host

PROFILE = host.PROFILE
STATE_DIR = "kagari-vnext-attach"
MAX_RECORD = 65536
MAX_BINARY = 16 * 1024 * 1024
REQUIRED_BINARIES = (
    "kagari-vnext-attach", "kagari-vnext-host-prepare",
    "kagari-vnext-worktree-install", "kagari-vnext-task", "kagari-vnext-session",
)
Blocked = host.HostBlocked
need = host.need


def stamp(info: os.stat_result) -> dict[str, int]:
    return {"device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid,
            "mode": stat.S_IMODE(info.st_mode), "links": info.st_nlink,
            "size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns}


def immutable_dir(path: Path) -> dict[str, int]:
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid())
         and not info.st_mode & 0o222, "PACKAGE_DIRECTORY_NOT_IMMUTABLE")
    return stamp(info)


def package_identity(package: Path) -> dict[str, Any]:
    need(package.is_absolute() and not package.is_symlink()
         and package.resolve(strict=True) == package, "PACKAGE_ROOT_NOT_CANONICAL")
    root = immutable_dir(package)
    binary_dir = immutable_dir(package / "bin")
    binaries = {}
    for name in REQUIRED_BINARIES:
        entry = package / "bin" / name
        link_info = entry.lstat()
        need((stat.S_ISREG(link_info.st_mode) or stat.S_ISLNK(link_info.st_mode))
             and link_info.st_uid in (0, os.geteuid()), "PACKAGE_ENTRY_UNSAFE")
        target = entry.resolve(strict=True)
        # symlinkJoin's executable links may resolve into other immutable Nix
        # outputs. Arbitrary external links are not an installation authority.
        boundary = package
        if not target.is_relative_to(package):
            parts = target.parts
            need(len(parts) >= 6 and parts[:3] == ("/", "nix", "store")
                 and re.fullmatch(r"[0-9a-z]{32}-.+", parts[3]) is not None,
                 "PACKAGE_BINARY_ESCAPES_PACKAGE")
            boundary = Path("/nix/store") / parts[3]
        parent = target.parent
        parents = {}
        while True:
            parents[str(parent)] = immutable_dir(parent)
            if parent == boundary:
                break
            need(parent != parent.parent and parent.is_relative_to(boundary),
                 "PACKAGE_BINARY_ESCAPES_PACKAGE")
            parent = parent.parent
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid in (0, os.geteuid())
                 and not before.st_mode & 0o222 and bool(before.st_mode & 0o111)
                 and before.st_size <= MAX_BINARY, "PACKAGE_BINARY_UNSAFE")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(MAX_BINARY + 1)
            need(len(data) == before.st_size
                 and stamp(before) == stamp(os.fstat(fd)), "PACKAGE_BINARY_CHANGED")
            need(stamp(entry.lstat()) == stamp(link_info)
                 and entry.resolve(strict=True) == target,
                 "PACKAGE_BINARY_CHANGED")
            binaries[name] = {"path": str(target), "entry": stamp(link_info),
                              "file": stamp(before), "parents": parents,
                              "sha256": hashlib.sha256(data).hexdigest()}
        finally:
            os.close(fd)
    need(root == immutable_dir(package)
         and binary_dir == immutable_dir(package / "bin"), "PACKAGE_CHANGED")
    return {"root": str(package), "directory": root,
            "bin_directory": binary_dir, "binaries": binaries}


def project_identity(project: Path) -> dict[str, Any]:
    before = (project / ".git").lstat()
    identity = host.project_identity(project)
    git = (project / ".git").lstat()
    need(stat.S_ISDIR(before.st_mode) and stat.S_ISDIR(git.st_mode)
         and (before.st_dev, before.st_ino) == (git.st_dev, git.st_ino),
         "PROJECT_GIT_IDENTITY_CHANGED")
    return {**identity, "git_device": git.st_dev, "git_inode": git.st_ino}


def state_name(identity: dict[str, Any]) -> str:
    # Key by path, but bind the inode INSIDE the record. Replacing a checkout
    # at the same path must conflict with, not silently orphan, its receipt.
    key = identity["project_root"].encode("utf-8")
    return "attach-" + hashlib.sha256(key).hexdigest() + ".json"


def expected_state(identity: dict[str, Any], home: Path,
                   package: dict[str, Any]) -> dict[str, Any]:
    info = home.lstat()
    sources = {}
    for module in (host, host.ignore, sys.modules[__name__]):
        source = Path(module.__file__)
        need(source.parent.resolve(strict=True) == Path(__file__).resolve().parent,
             "HOST_HELPER_NOT_SAME_PACKAGE")
        sources[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
    return {"schema": 1, "kind": "KAGARI_VNEXT_HOST_ATTACHMENT_PREVIEW",
            "profile": PROFILE, "project": identity,
            "state_home": {"path": str(home), "device": info.st_dev,
                           "inode": info.st_ino, "uid": info.st_uid},
            "package": package, "host_sources_sha256": sources,
            "authority": "HOST_ATTACHMENT_ONLY", "runtime_installed": False,
            "task_execution_authorized": False}


@contextmanager
def namespace(home: Path, *, create: bool) -> Iterator[int | None]:
    parent = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        pinned = os.fstat(parent)
        need(host.private_dir(home) == home
             and (pinned.st_dev, pinned.st_ino) ==
             (home.lstat().st_dev, home.lstat().st_ino), "STATE_HOME_CHANGED")
        if create:
            try:
                os.mkdir(STATE_DIR, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
        try:
            fd = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=parent)
        except FileNotFoundError:
            yield None
            return
        info = os.fstat(fd)
        need(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
             "ATTACH_NAMESPACE_UNSAFE")
        need(info.st_dev == pinned.st_dev and not os.path.ismount(home / STATE_DIR),
             "ATTACH_NAMESPACE_MOUNT_UNSAFE")
        yield fd
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def same_namespace(home: Path, fd: int) -> None:
    host.private_dir(home)
    path = home / STATE_DIR
    need(not path.is_symlink() and path.resolve(strict=True) == path,
         "ATTACH_NAMESPACE_CHANGED")
    live, opened = path.lstat(), os.fstat(fd)
    need((live.st_dev, live.st_ino) == (opened.st_dev, opened.st_ino)
         and live.st_uid == os.geteuid() and stat.S_IMODE(live.st_mode) == 0o700,
         "ATTACH_NAMESPACE_CHANGED")


def inventory(fd: int) -> None:
    for name in os.listdir(fd):
        need(re.fullmatch(r"attach-[0-9a-f]{64}\.json", name) is not None,
             "ATTACH_UNKNOWN_OR_INTERRUPTED_STATE")


@contextmanager
def attachment_lock(fd: int) -> Iterator[None]:
    # Linux flock on the namespace inode avoids creating a second attachment
    # artifact and keeps lock identity stable across receipt deletion/reinstall.
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


def prepare(identity: dict[str, Any], home: Path, action: str) -> dict[str, Any]:
    """Compose the existing host-prepare primitives with the OUTER inode pin.

    host.operate has no caller-supplied identity argument. Calling it after our
    validation would allow a replacement checkout to become its new authority.
    Use its unchanged receipt/preflight machinery, forwarding our original pin
    to the same ignore helper instead. No alternate ignore implementation.
    """
    project = Path(identity["project_root"])
    selected = {key: identity[key] for key in
                ("project_root", "project_device", "project_inode")}
    pinned = (identity["project_device"], identity["project_inode"])
    expected = host.expected_state(selected)
    write = action in ("install", "repair")
    name = host.state_name(selected)
    fd = host.open_state_dir(home, create=write)

    def preflight() -> dict[str, Any]:
        need(project_identity(project) == identity, "PROJECT_IDENTITY_CHANGED")
        report = host.ignore.operate(
            project, action if write else "doctor", expected_project_identity=pinned)
        need(project_identity(project) == identity, "PROJECT_IDENTITY_CHANGED")
        return report

    if fd is None:
        return host.result(action, "ABSENT", selected, home, preflight(), attached=False)
    try:
        with host.state_lock(fd):
            namespace_path = host.private_dir(home / host.STATE_DIR)
            live, opened = namespace_path.lstat(), os.fstat(fd)
            need((live.st_dev, live.st_ino) == (opened.st_dev, opened.st_ino),
                 "HOST_PREP_NAMESPACE_CHANGED")
            need(not any(entry.startswith("kagari-host-prep-stage-")
                         for entry in os.listdir(fd)), "HOST_STAGE_REQUIRES_REVIEW")
            # The older host reader opens before checking file type. Reject a
            # FIFO/device up front rather than potentially waiting on its open.
            try:
                record = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                record = None
            if record is not None:
                need(stat.S_ISREG(record.st_mode) and record.st_nlink == 1
                     and record.st_uid == os.geteuid()
                     and stat.S_IMODE(record.st_mode) == 0o600
                     and record.st_size <= host.MAX_RECORD, "HOST_STATE_FILE_UNSAFE")
            attached = host.stored(fd, name, expected)
            report = preflight()
            if write:
                need(report["git_ignore_effective"], "WORKTREE_IGNORE_NOT_EFFECTIVE")
                if not attached:
                    live = host.private_dir(namespace_path).lstat()
                    need((live.st_dev, live.st_ino) == (opened.st_dev, opened.st_ino),
                         "HOST_PREP_NAMESPACE_CHANGED")
                    host.publish(fd, name, expected, namespace_path)
                status = "READY" if attached else "PREPARED"
            else:
                status = ("ABSENT" if not attached else
                          "READY" if report["status"] == "READY" else "NEEDS_REPAIR")
            return host.result(action, status, selected, home, report,
                               attached=attached or write)
    finally:
        os.close(fd)


def stored(fd: int, name: str, expected: dict[str, Any]) -> os.stat_result | None:
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                          dir_fd=fd)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(file_fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
             and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
             and before.st_size <= MAX_RECORD, "ATTACH_RECEIPT_UNSAFE")
        with os.fdopen(file_fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_RECORD + 1)
        need(stamp(before) == stamp(os.fstat(file_fd)), "ATTACH_RECEIPT_CHANGED")
        # Exact canonical bytes reject duplicate keys, noncanonical JSON, extra
        # authority, changed package pins, and foreign Project/state identity.
        need(raw == host.canonical(expected), "ATTACH_RECEIPT_IDENTITY_CHANGED")
        live = os.stat(name, dir_fd=fd, follow_symlinks=False)
        need(stamp(live) == stamp(before), "ATTACH_RECEIPT_CHANGED")
        return before
    finally:
        os.close(file_fd)


def publish(fd: int, name: str, expected: dict[str, Any]) -> None:
    data = host.canonical(expected)
    need(len(data) <= MAX_RECORD, "ATTACH_RECEIPT_TOO_LARGE")
    stage = ".stage-" + secrets.token_hex(16)
    writer = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=fd)
    owned = os.fstat(writer)
    try:
        with os.fdopen(writer, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        try:
            os.link(stage, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError:
            raise Blocked("ATTACH_RECEIPT_APPEARED") from None
        os.fsync(fd)
    finally:
        # Only our O_EXCL staging inode, never a recursive cleanup or a glob.
        live = os.stat(stage, dir_fd=fd, follow_symlinks=False)
        need((live.st_dev, live.st_ino) == (owned.st_dev, owned.st_ino),
             "ATTACH_STAGE_CHANGED")
        os.unlink(stage, dir_fd=fd)
        os.fsync(fd)


def result(action: str, status: str, identity: dict[str, Any], home: Path,
           package: Path, attached: bool, preflight: dict[str, Any] | None) -> dict[str, Any]:
    return {"operation": action, "status": status, "profile": PROFILE,
            "project_root": identity["project_root"], "package_root": str(package),
            "attachment_receipt": str(home / STATE_DIR / state_name(identity)),
            "host_attached": attached, "preflight": preflight,
            "runtime_installed": False, "task_execution_authorized": False,
            "main_started": False, "model_or_session_started": False,
            "git_stage_or_commit_performed": False}


def operate(project: Path, state_home: Path, package_root: Path, action: str,
            profile: str) -> dict[str, Any]:
    need(profile == PROFILE, "EXPLICIT_LINKED_MVI_PROFILE_REQUIRED")
    need(action in ("plan", "doctor", "install", "repair", "uninstall"),
         "UNSUPPORTED_ACTION")
    identity = project_identity(project)
    home = host.private_dir(state_home)
    home_info = home.lstat()
    need(not home.is_relative_to(project) and not project.is_relative_to(home),
         "HOST_STATE_MUST_BE_OUTSIDE_PROJECT")
    package = package_identity(package_root)
    need(not package_root.is_relative_to(project)
         and not project.is_relative_to(package_root)
         and not package_root.is_relative_to(home)
         and not home.is_relative_to(package_root), "PACKAGE_MUST_BE_EXTERNAL")
    expected = expected_state(identity, home, package)
    need(expected["state_home"] == {"path": str(home), "device": home_info.st_dev,
                                   "inode": home_info.st_ino, "uid": home_info.st_uid},
         "STATE_HOME_CHANGED")
    name = state_name(identity)
    write = action in ("install", "repair")

    def revalidate() -> None:
        need(project_identity(project) == identity, "PROJECT_IDENTITY_CHANGED")
        need(package_identity(package_root) == package, "PACKAGE_IDENTITY_CHANGED")
        need(expected_state(identity, host.private_dir(home), package) == expected,
             "HOST_IDENTITY_CHANGED")

    with namespace(home, create=write) as fd:
        if fd is None:
            preflight = None if action == "uninstall" else prepare(identity, home, "doctor")
            revalidate()
            return result(action, "ABSENT", identity, home, package_root, False, preflight)
        with attachment_lock(fd):
            same_namespace(home, fd)
            inventory(fd)
            attached = stored(fd, name, expected)
            if action == "uninstall":
                if attached is not None:
                    revalidate()
                    same_namespace(home, fd)
                    checked = stored(fd, name, expected)
                    need(checked is not None and stamp(checked) == stamp(attached),
                         "ATTACH_RECEIPT_CHANGED")
                    os.unlink(name, dir_fd=fd)
                    os.fsync(fd)
                return result(action, "DETACHED" if attached is not None else "ABSENT",
                              identity, home, package_root, False, None)
            # Validate all attachment state BEFORE the helper can append owner
            # configuration. Its own receipt/source checks are also fail-closed.
            revalidate()
            preflight = prepare(identity, home, action if write else "doctor")
            revalidate()
            same_namespace(home, fd)
            if write:
                need(preflight["host_preflight_recorded"]
                     and preflight["ignore"]["git_ignore_effective"],
                     "HOST_PREFLIGHT_NOT_READY")
                if attached is None:
                    publish(fd, name, expected)
                else:
                    need(stored(fd, name, expected) is not None, "ATTACH_RECEIPT_DISAPPEARED")
                status = "ATTACHED" if attached is None else "READY"
                attached_now = True
            else:
                status = ("ABSENT" if attached is None else
                          "READY" if preflight["status"] == "READY" else "NEEDS_REPAIR")
                attached_now = attached is not None
            return result(action, status, identity, home, package_root, attached_now, preflight)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "doctor", "install", "repair", "uninstall"))
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--state-home", required=True, type=Path)
    parser.add_argument("--package-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = operate(args.project_root, args.state_home, args.package_root,
                         args.operation, args.profile)
    except (Blocked, host.ignore.IgnoreBlocked, OSError, ValueError, UnicodeError) as exc:
        print(json.dumps({"operation": args.operation, "status": "BLOCKED",
                          "code": getattr(exc, "code", "ATTACH_FS_ERROR"),
                          "reason": str(exc), "runtime_installed": False,
                          "task_execution_authorized": False}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
