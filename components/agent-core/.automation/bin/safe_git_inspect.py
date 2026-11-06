#!/usr/bin/env python3
"""Issue #364 Phase A: *unactivated* Task-bound Git plumbing inspection prototype.

This is NOT a replacement for the publication/whitespace gate. The host-native
wrapper, not a model-produced shell command, must supply the current Task root.
Only fixed read operations with known non-executing behaviour are supported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time

MAX_STDOUT = 4 * 1024 * 1024
MAX_JSON = 1536
MAX_STATE_BYTES = 64 * 1024
TIMEOUT = 8
TOTAL_BUDGET = 8.5  # Less than the host tool's outer 24-second deadline.
_TERMINATION_REQUESTED = False
TASK_RE = re.compile(r"[1-9][0-9]{0,8}\Z")
GIT_OID = {"sha1": 40, "sha256": 64}
BRANCH = re.compile(r"(?:task|fix)/([1-9][0-9]*)-[a-zA-Z0-9][a-zA-Z0-9_.-]*\Z")
# Detect all branch spellings assigned to the same Task before enforcing the
# narrower current-branch syntax. Git permits additional slashes in branch names.
TASK_BRANCH_PREFIX = re.compile(r"(?:task|fix)/([1-9][0-9]*)(?=[-/])")
ORIGIN = re.compile(
    r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+)/"
    r"([A-Za-z0-9_.-]+?)(?:\.git)?\Z", re.IGNORECASE
)


class InspectionError(Exception):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _trusted_git(path: str) -> str:
    if not os.path.isabs(path):
        raise InspectionError("Git executable must be an absolute path")
    resolved = os.path.realpath(path)
    if not (
        resolved.startswith("/nix/store/") or
        resolved.startswith("/usr/bin/") or
        resolved.startswith("/bin/")
    ):
        raise InspectionError("Git executable outside approved system prefixes")
    try:
        info = os.stat(resolved)
    except OSError as exc:
        raise InspectionError("Trusted Git executable is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise InspectionError("Trusted Git executable has unsafe metadata")
    if not os.access(resolved, os.X_OK):
        raise InspectionError("Trusted Git executable is not executable")
    return resolved


def _git_env(binary: str) -> dict[str, str]:
    """No caller GIT_*, credential, user shell, or local Nix-profile executable."""
    path = os.path.dirname(binary)
    return {
        "PATH": path + ":/usr/bin:/bin",
        "HOME": "/nonexistent",
        "XDG_CONFIG_HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_ALLOW_PROTOCOL": "",  # Allow no network or local Git transports.
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TRACE": "0",
        "LC_ALL": "C",
    }


def _check_termination() -> None:
    if _TERMINATION_REQUESTED:
        raise InspectionError("Guarded Git inspection was interrupted")


def _git(root: Path, binary: str, *argv: str, deadline: float | None = None) -> bytes:
    """Fixed Git invocations only; no caller-provided argv or shell."""
    _check_termination()
    allowed = {
        ("rev-parse", "--show-toplevel"),
        ("rev-parse", "--verify", "HEAD"),
        ("rev-parse", "--show-object-format"),
        ("symbolic-ref", "--quiet", "--short", "HEAD"),
        ("worktree", "list", "--porcelain", "-z"),
        ("remote", "get-url", "origin"),
        ("ls-files", "--stage", "-z"),
    }
    if argv not in allowed:
        raise InspectionError("Git operation is not part of Phase A allowlist")
    # Read each pipe incrementally. subprocess.run(capture_output=True) would
    # buffer unbounded attacker-influenced Index output before checking its size.
    process = None
    output = bytearray()
    errors = bytearray()
    command_deadline = min(time.monotonic() + TIMEOUT, deadline or float("inf"))
    completed = False
    try:
        process = subprocess.Popen(
            [binary, "-c", "core.fsmonitor=false", *argv],
            cwd=root, env=_git_env(binary), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, output)
            selector.register(process.stderr, selectors.EVENT_READ, errors)
            while selector.get_map():
                _check_termination()
                remaining = command_deadline - time.monotonic()
                if remaining <= 0:
                    raise InspectionError("Guarded Git inspection timed out")
                # The SIGTERM handler only records cancellation so it cannot
                # interrupt process-group cleanup. Poll at a bounded interval.
                events = selector.select(min(remaining, 0.2))
                if not events:
                    continue
                _check_termination()
                for key, _ in events:
                    raw = os.read(key.fileobj.fileno(), 8192)
                    if not raw:
                        selector.unregister(key.fileobj)
                        continue
                    target = key.data
                    limit = MAX_STDOUT if target is output else 2048
                    if len(target) + len(raw) > limit:
                        raise InspectionError("Guarded Git inspection exceeded its output budget")
                    target.extend(raw)
        _check_termination()
        remaining = command_deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise InspectionError("Guarded Git inspection returned a nonzero exit")
        _check_termination()
        completed = True
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InspectionError("Guarded Git inspection failed") from exc
    finally:
        if process is not None:
            # Kill the entire process group if an error, timeout or signal
            # interrupted collection. A child may survive after Git itself exits.
            # Do NOT call poll(): it reaps an exited group leader, making its
            # numeric PGID unsafe to signal while pipe-holding descendants live.
            # A child whose returncode is still None has not been reaped by
            # this single-threaded owner, even when it exited at kernel level.
            # Signal its original process group before our wait() releases PID
            # custody. Once wait() sets returncode, never signal the old PGID.
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
    return bytes(output)


def _text(data: bytes, label: str, *, line: bool = False) -> str:
    try:
        value = data.decode("utf-8")
    except UnicodeError as exc:
        raise InspectionError(f"Invalid UTF-8 in {label}") from exc
    # NUL-delimited worktree records may legally contain trailing spaces or
    # newlines; only newline-delimited Git output sheds its protocol terminator.
    if line:
        if not value.endswith("\n"):
            raise InspectionError(f"Missing Git output line terminator in {label}")
        return value[:-1]
    return value


def _registered(root: Path, binary: str, task: str, oid_length: int,
                deadline: float) -> tuple[str, str]:
    raw = _git(root, binary, "worktree", "list", "--porcelain", "-z", deadline=deadline)
    branches: list[tuple[str, str, str]] = []
    for entry in raw.split(b"\0\0"):
        if not entry:
            continue
        fields = entry.strip(b"\0").split(b"\0")
        values: dict[bytes, bytes] = {}
        for field in fields:
            key, separator, value = field.partition(b" ")
            if key not in {b"worktree", b"HEAD", b"branch", b"detached", b"bare", b"locked", b"prunable"}:
                raise InspectionError("Unknown registered-worktree field")
            if key in values:
                raise InspectionError("Duplicated registered-worktree field")
            values[key] = value
        if b"worktree" not in values:
            raise InspectionError("Incomplete registered worktree")
        # A bare common repository appears in the worktree inventory without
        # a HEAD record; it cannot itself be a registered linked Task.
        if b"bare" in values:
            if values[b"bare"] or b"branch" in values:
                raise InspectionError("Malformed bare worktree inventory")
            continue
        if b"HEAD" not in values:
            raise InspectionError("Incomplete registered worktree")
        path = _text(values[b"worktree"], "worktree")
        head = _text(values[b"HEAD"], "HEAD")
        branch = _text(values.get(b"branch", b""), "branch")
        if not re.fullmatch(r"[0-9a-f]{" + str(oid_length) + r"}", head):
            raise InspectionError("Invalid registered HEAD")
        branches.append((path, head, branch))

    candidates = [
        (path, head, branch)
        for path, head, branch in branches
        if branch.startswith("refs/heads/") and
        (m := TASK_BRANCH_PREFIX.match(branch.removeprefix("refs/heads/"))) and m.group(1) == task
    ]
    if len(candidates) != 1 or candidates[0][0] != str(root):
        raise InspectionError("Expected one uniquely registered current Task worktree")
    current_branch = candidates[0][2].removeprefix("refs/heads/")
    if BRANCH.fullmatch(current_branch) is None:
        raise InspectionError("Current Task branch uses unsupported syntax")
    return candidates[0][1], current_branch


def _stable_stat(info: os.stat_result) -> tuple[int, ...]:
    # Access time is omitted: an ordinary read may legitimately update atime.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _task_marker(root: Path, task: str, branch: str) -> str:
    state = root / ".task-state"
    path = state / "task.md"
    try:
        state_info = state.lstat()
        if (not stat.S_ISDIR(state_info.st_mode) or stat.S_ISLNK(state_info.st_mode)
            or state_info.st_uid != os.geteuid() or state_info.st_mode & 0o022):
            raise InspectionError("Task State directory has unsafe metadata")
        if not hasattr(os, "O_NOFOLLOW"):
            raise InspectionError("Task inspection requires O_NOFOLLOW")
        handle = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            before = os.fstat(handle)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != os.geteuid() or before.st_mode & 0o022 or
                before.st_size > MAX_STATE_BYTES):
                raise InspectionError("Unsafe Task identity marker")
            data = os.read(handle, MAX_STATE_BYTES + 1)
            after = os.fstat(handle)
            if _stable_stat(before) != _stable_stat(after) or len(data) > MAX_STATE_BYTES:
                raise InspectionError("Task identity marker changed during read")
            current = path.lstat()
            if _stable_stat(before) != _stable_stat(current) or (
                _stable_stat(state_info) != _stable_stat(state.lstat())
            ):
                raise InspectionError("Task State marker or directory was replaced")
        finally:
            os.close(handle)
    except OSError as exc:
        raise InspectionError("Task State identity marker is unavailable") from exc
    content = _text(data, "Task State")
    expected = {"Task ID": task, "Branch": branch, "Worktree": str(root)}
    for key, value in expected.items():
        candidates = [line for line in content.splitlines()
                      if line.startswith("- " + key + ":")]
        if candidates != ["- " + key + ": " + value]:
            raise InspectionError("Task State identity is missing, duplicated or contradictory")
    return _digest(data)


def inspect(root: Path, task: str, operation: str, git_binary: str) -> dict[str, object]:
    if not isinstance(task, str) or not TASK_RE.fullmatch(task):
        raise InspectionError("Expected exact positive numeric Task ID")
    if operation not in {"identity", "index-meta"}:
        raise InspectionError("Phase A supports identity or index-meta only")
    if not root.is_absolute() or str(root) != os.path.realpath(root):
        raise InspectionError("Task root must be an exact canonical path")
    if Path.cwd().resolve() != root:
        raise InspectionError("Git inspection must run from its registered Task root")
    binary = _trusted_git(git_binary)
    deadline = time.monotonic() + TOTAL_BUDGET

    def observe() -> tuple[str, str, str, str, str, str]:
        top = _text(_git(root, binary, "rev-parse", "--show-toplevel",
                         deadline=deadline), "Git root", line=True)
        if top != str(root):
            raise InspectionError("Git root does not match current Task Worktree")
        kind = _text(_git(root, binary, "rev-parse", "--show-object-format",
                          deadline=deadline), "object format", line=True)
        if kind not in GIT_OID:
            raise InspectionError("Unsupported Git object format")
        head = _text(_git(root, binary, "rev-parse", "--verify", "HEAD",
                          deadline=deadline), "HEAD", line=True)
        if not re.fullmatch(r"[0-9a-f]{" + str(GIT_OID[kind]) + r"}", head):
            raise InspectionError("Invalid immutable HEAD identity")
        registered_head, branch = _registered(root, binary, task, GIT_OID[kind], deadline)
        if registered_head != head or branch != _text(
            _git(root, binary, "symbolic-ref", "--quiet", "--short", "HEAD",
                 deadline=deadline), "current branch", line=True
        ):
            raise InspectionError("Task Worktree registration changed")
        marker_sha = _task_marker(root, task, branch)
        origin = _text(_git(root, binary, "remote", "get-url", "origin",
                            deadline=deadline), "origin", line=True)
        match = ORIGIN.fullmatch(origin)
        if match is None:
            raise InspectionError("Canonical GitHub origin is unavailable")
        repository = match.group(1).lower() + "/" + match.group(2).lower()
        return (kind, head, branch, repository, marker_sha, top)

    kind, head, branch, repository, marker_sha, _ = first = observe()
    common: dict[str, object] = {
        "schema_version": 1,
        "phase": "A",
        "qualification": "PROTOTYPE_ONLY",
        "task": task,
        "operation": operation,
        "repository": repository,
        "branch": branch,
        "head": head,
        "object_format": kind,
        "worktree_sha256": _digest(str(root).encode()),
    }
    if operation == "index-meta":
        raw = _git(root, binary, "ls-files", "--stage", "-z", deadline=deadline)
        count = 0
        if raw and not raw.endswith(b"\0"):
            raise InspectionError("Index listing is not properly terminated")
        for record in raw.split(b"\0"):
            if not record:
                continue
            metadata, separator, filename = record.partition(b"\t")
            fields = metadata.split(b" ")
            if separator != b"\t" or len(fields) != 3 or not filename:
                raise InspectionError("Malformed stage-0 index record")
            mode, oid, stage = fields
            if mode not in {b"100644", b"100755"} or stage != b"0":
                raise InspectionError("Non-regular or unmerged index entry is unsupported in Phase A")
            if not re.fullmatch(rb"[0-9a-f]{" + str(GIT_OID[kind]).encode() + rb"}", oid):
                raise InspectionError("Invalid index object ID")
            count += 1
            if count > 10000:
                raise InspectionError("Index entry count exceeds Phase A budget")
        # Refuse an index changed between the first read and the final
        # identity check; this is still not an atomic cross-process snapshot.
        again = _git(root, binary, "ls-files", "--stage", "-z", deadline=deadline)
        if raw != again:
            raise InspectionError("Index changed during Phase A inspection")
        common.update(index_entries=count, index_listing_sha256=_digest(raw))
    if first != observe():
        raise InspectionError("Task identity changed during Phase A inspection")
    return common


def _on_termination(_signal: int, _frame: object) -> None:
    # Never raise asynchronously while Popen owns a child or while killpg /
    # wait / pipe close must run. _git checks this flag at bounded safe points.
    global _TERMINATION_REQUESTED
    _TERMINATION_REQUESTED = True


def main(argv: list[str] | None = None) -> int:
    global _TERMINATION_REQUESTED
    _TERMINATION_REQUESTED = False
    signal.signal(signal.SIGTERM, _on_termination)
    parser = argparse.ArgumentParser(description="Phase A guarded Git inspection prototype")
    parser.add_argument("task")
    parser.add_argument("operation", choices=("identity", "index-meta"))
    args = parser.parse_args(argv)
    try:
        root_text = os.environ.get("KAGARI_GIT_INSPECT_ROOT", "")
        if not root_text:
            raise InspectionError("Trusted Task root handoff is absent")
        root = Path(root_text)
        provided_git = os.environ.get("KAGARI_GIT_INSPECT_BINARY")
        if provided_git is None:
            raise InspectionError("Trusted Git executable handoff is absent")
        result = inspect(root, args.task, args.operation, provided_git)
        _check_termination()
        encoded = (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(encoded) > MAX_JSON:
            raise InspectionError("Bounded response would be oversized")
        sys.stdout.buffer.write(encoded)
        # The Native Tool consumes stdout only for a successful exit code.
        # If cancellation arrives during write/flush, return nonzero so the
        # partial or complete bytes cannot be mistaken for an accepted result.
        sys.stdout.buffer.flush()
        _check_termination()
    except InspectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
