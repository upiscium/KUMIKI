#!/usr/bin/env python3
"""Opt-in KAGARI vNext linked-Worktree install/repair prerequisite (#301).

This is a host-owned Project configuration step, NOT the v0.1.0 GA installer,
Issue allocator, Task authority, or permission escalation. It never runs the
target Project's scripts or touches its Git index, refs, or existing Worktrees.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any

RULE = b"/.worktrees/"
PATH = ".gitignore"
MAX_BYTES = 1024 * 1024


class IgnoreBlocked(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(detail or code)


def demand(condition: bool, code: str) -> None:
    if not condition:
        raise IgnoreBlocked(code)


def git(project: Path, *args: str, allow_failure: bool = False) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_COUNT="0", GIT_OPTIONAL_LOCKS="0",
               GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "--no-optional-locks",
             "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IgnoreBlocked("GIT_UNAVAILABLE", str(exc)) from exc
    if not allow_failure and result.returncode:
        raise IgnoreBlocked("GIT_OBSERVATION_FAILED")
    return result


def primary_project(project: Path) -> tuple[Path, Path]:
    demand(project.is_absolute() and not project.is_symlink(),
           "PROJECT_ROOT_NOT_CANONICAL")
    try:
        actual = project.resolve(strict=True)
        p = project.lstat()
    except OSError as exc:
        raise IgnoreBlocked("PROJECT_ROOT_UNAVAILABLE", str(exc)) from exc
    demand(actual == project and stat.S_ISDIR(p.st_mode) and p.st_uid == os.geteuid(),
           "PROJECT_ROOT_NOT_CANONICAL")
    git_dir = project / ".git"
    try:
        d = git_dir.lstat()
    except OSError as exc:
        raise IgnoreBlocked("PRIMARY_GIT_DIRECTORY_REQUIRED", str(exc)) from exc
    demand(stat.S_ISDIR(d.st_mode) and d.st_uid == os.geteuid(),
           "PRIMARY_GIT_DIRECTORY_REQUIRED")
    result = git(project, "rev-parse", "--show-toplevel", "--path-format=absolute",
                 "--git-common-dir")
    values = result.stdout.decode("utf-8", "strict").splitlines()
    demand(len(values) == 2 and values == [str(project), str(git_dir)],
           "PRIMARY_GIT_PROJECT_REQUIRED")
    return project, git_dir


def lines(data: bytes) -> list[bytes]:
    return [line.removesuffix(b"\r") for line in data.split(b"\n")]


def contains_rule(data: bytes) -> bool:
    return RULE in lines(data)


def newline(data: bytes) -> bytes:
    # Retain existing bytes verbatim; new bytes follow the prevailing style.
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n")
    return b"\r\n" if crlf and crlf == lf else b"\n"


def append_rule(data: bytes | None) -> bytes:
    if data is None:
        return RULE + b"\n"
    if contains_rule(data):
        return data
    eol = newline(data)
    prefix = b"" if not data or data.endswith(b"\n") else eol
    return data + prefix + RULE + eol


def snapshot(dir_fd: int) -> tuple[bytes, os.stat_result] | None:
    try:
        metadata = os.stat(PATH, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    demand(stat.S_ISREG(metadata.st_mode), "GITIGNORE_NOT_REGULAR")
    demand(metadata.st_nlink == 1, "GITIGNORE_HAS_HARDLINKS")
    demand(metadata.st_uid == os.geteuid(), "GITIGNORE_FOREIGN_OWNER")
    demand(bool(metadata.st_mode & stat.S_IRUSR), "GITIGNORE_NOT_READABLE")
    demand(metadata.st_size <= MAX_BYTES, "GITIGNORE_TOO_LARGE")
    fd = os.open(PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    try:
        live = os.fstat(fd)
        demand((live.st_dev, live.st_ino) == (metadata.st_dev, metadata.st_ino)
               and stat.S_ISREG(live.st_mode) and live.st_nlink == 1,
               "GITIGNORE_CHANGED_DURING_READ")
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(MAX_BYTES + 1)
        demand(len(data) <= MAX_BYTES, "GITIGNORE_TOO_LARGE")
        after = os.fstat(fd)
        demand(unchanged_stat(live, after), "GITIGNORE_CHANGED_DURING_READ")
        return data, after
    finally:
        os.close(fd)


def unchanged_stat(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino, a.st_uid, a.st_gid, a.st_mode, a.st_nlink,
            a.st_size, a.st_mtime_ns, a.st_ctime_ns) == (
            b.st_dev, b.st_ino, b.st_uid, b.st_gid, b.st_mode, b.st_nlink,
            b.st_size, b.st_mtime_ns, b.st_ctime_ns)


def effective_ignore(project: Path) -> bool:
    check = git(project, "check-ignore", "--quiet", "--no-index",
                "--", ".worktrees/", allow_failure=True)
    demand(check.returncode in (0, 1), "GIT_IGNORE_CHECK_FAILED")
    return check.returncode == 0


def conflicting_negation(data: bytes) -> bool:
    """Ask Git to evaluate the existing negations against a synthetic ignore.

    With a known initial ignore rule, a matching negation removes it. This
    catches Git wildcard/anchoring semantics without parsing them ourselves.
    """
    candidates = [line for line in lines(data) if line.startswith(b"!")]
    if not candidates:
        return False
    with tempfile.TemporaryDirectory(prefix="kagari-ignore-preflight-") as scratch:
        folder = Path(scratch)
        git(folder, "init", "-q")
        (folder / PATH).write_bytes(RULE + b"\n" + b"\n".join(candidates) + b"\n")
        return not effective_ignore(folder)


def namespace_safe(project: Path, root_fd: int) -> None:
    try:
        info = os.stat(".worktrees", dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        info = None
    if info is not None:
        demand(stat.S_ISDIR(info.st_mode), "WORKTREE_NAMESPACE_NOT_DIRECTORY")
    tracked = git(project, "ls-files", "-z", "--", ".worktrees").stdout
    demand(not tracked, "TRACKED_WORKTREE_NAMESPACE")
    if info is None:
        return
    inventory = git(project, "worktree", "list", "--porcelain").stdout.decode("utf-8", "strict")
    registrations: dict[str, dict[str, str]] = {}
    for block in inventory.split("\n\n"):
        record = dict(line.partition(" ")[::2] for line in block.splitlines() if line)
        location = record.get("worktree")
        if location:
            demand(location not in registrations, "AMBIGUOUS_WORKTREE_REGISTRATION")
            registrations[location] = record
    for entry in (project / ".worktrees").iterdir():
        demand(not entry.is_symlink() and entry.is_dir()
               and str(entry) in registrations,
               "UNKNOWN_WORKTREE_NAMESPACE_CONTENT")
        record = registrations[str(entry)]
        demand("locked" not in record and "prunable" not in record
               and record.get("branch", "").startswith("refs/heads/"),
               "STALE_WORKTREE_REGISTRATION")
        dotgit = entry / ".git"
        try:
            metadata = dotgit.lstat()
            demand(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
                   and metadata.st_size <= 4096, "STALE_WORKTREE_REGISTRATION")
            marker = dotgit.read_text(encoding="utf-8").strip()
            demand(marker.startswith("gitdir: "), "STALE_WORKTREE_REGISTRATION")
            admin = Path(marker[8:])
            if not admin.is_absolute():
                admin = entry / admin
            admin = admin.resolve(strict=True)
            demand(admin.parent == project / ".git/worktrees" and admin.is_dir(),
                   "STALE_WORKTREE_REGISTRATION")
            backlink = (admin / "gitdir").read_text(encoding="utf-8").strip()
            back = Path(backlink)
            if not back.is_absolute():
                back = admin / back
            demand(back.resolve(strict=True) == dotgit.resolve(strict=True),
                   "STALE_WORKTREE_REGISTRATION")
            actual = git(entry, "rev-parse", "--show-toplevel",
                         "--path-format=absolute", "--git-common-dir").stdout.decode().splitlines()
            demand(actual == [str(entry), str(project / ".git")],
                   "STALE_WORKTREE_REGISTRATION")
            branch = git(entry, "symbolic-ref", "--quiet", "HEAD").stdout.decode().strip()
            head = git(entry, "rev-parse", "HEAD").stdout.decode().strip()
            demand(branch == record["branch"]
                   and head == record.get("HEAD", record.get("head"))
                   and git(project, "rev-parse", "--verify",
                           branch).stdout.decode().strip() == head,
                   "STALE_WORKTREE_REGISTRATION")
        except (OSError, UnicodeError, IgnoreBlocked) as exc:
            raise IgnoreBlocked("STALE_WORKTREE_REGISTRATION", str(exc)) from exc


def committed_rule(project: Path) -> bool:
    result = git(project, "show", "HEAD:.gitignore", allow_failure=True)
    if result.returncode or not contains_rule(result.stdout):
        return False
    # A literal line is insufficient if a later committed !pattern undoes it.
    with tempfile.TemporaryDirectory(prefix="kagari-ignore-committed-") as scratch:
        frozen = Path(scratch)
        git(frozen, "init", "-q")
        (frozen / PATH).write_bytes(result.stdout)
        return effective_ignore(frozen)


def rename_exchange(src_fd: int, src: str, dst_fd: int, dst: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    operation = getattr(libc, "renameat2", None)
    demand(operation is not None, "ATOMIC_EXCHANGE_UNAVAILABLE")
    operation.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                          ctypes.c_char_p, ctypes.c_uint)
    operation.restype = ctypes.c_int
    if operation(src_fd, os.fsencode(src), dst_fd, os.fsencode(dst), 2) != 0:
        err = ctypes.get_errno()
        raise IgnoreBlocked("ATOMIC_EXCHANGE_FAILED", os.strerror(err))


def same_root(project: Path, fd: int) -> None:
    try:
        live = os.stat(project, follow_symlinks=False)
    except OSError:
        raise IgnoreBlocked("PROJECT_ROOT_CHANGED") from None
    anchored = os.fstat(fd)
    demand((live.st_dev, live.st_ino) == (anchored.st_dev, anchored.st_ino),
           "PROJECT_ROOT_CHANGED")


def publish(project: Path, root_fd: int, git_dir: Path, old: tuple[bytes, os.stat_result] | None,
            candidate: bytes) -> str | None:
    demand(len(candidate) <= MAX_BYTES, "GITIGNORE_TOO_LARGE")
    original_mode = stat.S_IMODE(old[1].st_mode) if old else 0o644
    if old:
        demand(bool(old[1].st_mode & stat.S_IWUSR), "GITIGNORE_NOT_WRITABLE")
    # Open the Git-admin anchor FIRST. If staging fails (including EMFILE),
    # close it without leaving any privately staged user bytes behind.
    git_fd = os.open(git_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd, scratch = tempfile.mkstemp(prefix="kagari-gitignore-stage-", dir=git_dir)
    except BaseException:
        os.close(git_fd)
        raise
    temp = Path(scratch)
    new_inode: int | None = None
    exchanged = False
    created = False
    retain_stage = False
    preserved: str | None = None
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(candidate)
            f.flush()
            if old and os.fstat(f.fileno()).st_gid != old[1].st_gid:
                os.fchown(f.fileno(), os.geteuid(), old[1].st_gid)
            os.fchmod(f.fileno(), original_mode)
            os.fsync(f.fileno())
            new_inode = os.fstat(f.fileno()).st_ino
        same_root(project, root_fd)
        current = snapshot(root_fd)
        demand((old is None and current is None) or
               (old is not None and current is not None
                and current[0] == old[0] and unchanged_stat(current[1], old[1])),
               "GITIGNORE_CHANGED_BEFORE_PUBLICATION")
        if old is None:
            try:
                os.link(temp, PATH, dst_dir_fd=root_fd, follow_symlinks=False)
            except FileExistsError:
                raise IgnoreBlocked("GITIGNORE_APPEARED_BEFORE_PUBLICATION") from None
            created = True
        else:
            rename_exchange(git_fd, temp.name, root_fd, PATH)
            exchanged = True
            displaced = snapshot_stage(git_fd, temp.name)
            if displaced[0] != old[0] or not exchanged_original_stat(displaced[1], old[1]):
                rollback_exchange(root_fd, git_fd, temp.name, new_inode)
                exchanged = False
                raise IgnoreBlocked("GITIGNORE_CHANGED_AT_PUBLICATION")
        same_root(project, root_fd)
        demand(effective_ignore(project), "GITIGNORE_RULE_NOT_EFFECTIVE")
        if exchanged:
            # Catch a late write through an fd opened before RENAME_EXCHANGE.
            # Never discard the displaced inode: even a post-check writer may
            # still hold it open, so it must remain recoverable in Git-private.
            displaced = snapshot_stage(git_fd, temp.name)
            if displaced[0] != old[0] or not exchanged_original_stat(displaced[1], old[1]):
                rollback_exchange(root_fd, git_fd, temp.name, new_inode)
                exchanged = False
                raise IgnoreBlocked("GITIGNORE_CONCURRENT_WRITER_DETECTED")
            archive = git_dir / temp.name.replace(
                "kagari-gitignore-stage-", "kagari-gitignore-preserved-", 1)
            os.link(temp.name, archive.name, src_dir_fd=git_fd,
                    dst_dir_fd=git_fd, follow_symlinks=False)
            os.unlink(temp.name, dir_fd=git_fd)
            preserved = str(archive)
            exchanged = False
        return preserved
    except BaseException as exc:
        if exchanged:
            try:
                rollback_exchange(root_fd, git_fd, temp.name, new_inode)
                exchanged = False
            except Exception:
                retain_stage = True
        if retain_stage:
            raise IgnoreBlocked("GITIGNORE_RECOVERY_REQUIRES_REVIEW",
                                "preserved original bytes in Git-private stage") from exc
        if created:
            # There is no safe pathname-only unlink CAS. Another process may
            # have replaced the file even after our final inode check.
            # Preserve whichever .gitignore now exists for Human inspection.
            raise IgnoreBlocked("GITIGNORE_CREATION_REQUIRES_REVIEW",
                                "created rule may be present; inspect Project") from exc
        raise
    finally:
        try:
            if not retain_stage:
                try:
                    os.unlink(temp.name, dir_fd=git_fd)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise IgnoreBlocked("GITIGNORE_STAGE_CLEANUP_FAILED", str(exc)) from exc
        finally:
            os.close(git_fd)


def exchanged_original_stat(a: os.stat_result, b: os.stat_result) -> bool:
    # rename(2) updates ctime of the displaced original inode.
    return (a.st_dev, a.st_ino, a.st_uid, a.st_gid, a.st_mode, a.st_nlink,
            a.st_size, a.st_mtime_ns) == (
            b.st_dev, b.st_ino, b.st_uid, b.st_gid, b.st_mode, b.st_nlink,
            b.st_size, b.st_mtime_ns)


def snapshot_stage(git_fd: int, name: str) -> tuple[bytes, os.stat_result]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=git_fd)
    try:
        m = os.fstat(fd)
        demand(stat.S_ISREG(m.st_mode) and m.st_size <= MAX_BYTES,
               "GITIGNORE_STAGE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_BYTES + 1)
        return data, m
    finally:
        os.close(fd)


def rollback_exchange(root_fd: int, git_fd: int, temp_name: str,
                      new_inode: int | None) -> None:
    current = os.stat(PATH, dir_fd=root_fd, follow_symlinks=False)
    demand(current.st_ino == new_inode, "GITIGNORE_ROLLBACK_AMBIGUOUS")
    rename_exchange(git_fd, temp_name, root_fd, PATH)


def operate(project: Path, operation: str) -> dict[str, Any]:
    demand(operation in ("plan", "doctor", "install", "repair"), "UNSUPPORTED_OPERATION")
    project, git_dir = primary_project(project)
    root_fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        same_root(project, root_fd)
        admin_fd = os.open(git_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            lock_fd = os.open("kagari-worktree-ignore.lock",
                              os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
                              dir_fd=admin_fd)
        finally:
            os.close(admin_fd)
        try:
            info = os.fstat(lock_fd)
            demand(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                   and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
                   "GIT_PRIVATE_LOCK_INVALID")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            demand(not any(name.startswith("kagari-gitignore-stage-")
                           for name in os.listdir(git_dir)),
                   "UNRESOLVED_GITIGNORE_STAGE")
            namespace_safe(project, root_fd)
            before = snapshot(root_fd)
            original = before[0] if before else None
            demand(original is None or b"\x00" not in original,
                   "GITIGNORE_INVALID_CONTENT")
            if original and conflicting_negation(original):
                raise IgnoreBlocked("GITIGNORE_NEGATION_CONFLICT")
            ignored = effective_ignore(project)
            exact = original is not None and contains_rule(original)
            if exact and not ignored:
                raise IgnoreBlocked("GITIGNORE_RULE_NOT_EFFECTIVE")
            needs_change = not exact
            if operation in ("plan", "doctor"):
                return report(operation, "NEEDS_UPDATE" if needs_change else "READY",
                              project, changed=False, needs_commit=not committed_rule(project),
                              effective=ignored)
            archived_preimage = None
            if needs_change:
                archived_preimage = publish(
                    project, root_fd, git_dir, before, append_rule(original))
            demand(effective_ignore(project), "GITIGNORE_RULE_NOT_EFFECTIVE")
            return report(operation, "UPDATED" if needs_change else "UNCHANGED",
                          project, changed=needs_change, needs_commit=not committed_rule(project),
                          effective=True, preserved_preimage=archived_preimage)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
    finally:
        os.close(root_fd)


def report(operation: str, status: str, project: Path, *, changed: bool,
           needs_commit: bool, effective: bool,
           preserved_preimage: str | None = None) -> dict[str, Any]:
    return {"operation": operation, "status": status, "project_root": str(project),
            "path": PATH, "rule": RULE.decode(), "changed_paths": [PATH] if changed else [],
            "git_ignore_effective": effective, "needs_commit_for_new_clones": needs_commit,
            "git_index_or_history_modified": False,
            "preserved_preimage": preserved_preimage, "owner": "Project"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "doctor", "install", "repair"))
    parser.add_argument("--project-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        response = operate(args.project_root, args.operation)
    except (IgnoreBlocked, OSError, UnicodeError) as exc:
        print(json.dumps({"operation": args.operation, "status": "BLOCKED",
                          "code": getattr(exc, "code", "FILESYSTEM_ERROR"),
                          "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(response, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
