"""Synthetic, unqualified Claude Lane B fixture seam.

``dispatch`` is deliberately inert: it never reads a Task, touches a receipt,
calls authorization, or starts a process.  The private ``_exercise_fixture``
exists only for offline tests that inject both an authorization callback and a
fixture transport.  It is not connected to Main and is not a production model
transport.  Its best possible result is a sanitized synthetic observation,
never Task completion, model acceptance, or Ask state. Receipt path checks assume
trusted local same-user storage; repeated identity checks narrow races but do not
claim resistance to a malicious same-user process.
"""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from types import SimpleNamespace
from typing import Any, Callable, Mapping
import uuid

if __package__:
    from . import kagari_vnext_claude_preflight as preflight
    from . import kagari_vnext_task_binding as task_binding
else:  # pragma: no cover - supports direct script-style test imports
    import kagari_vnext_claude_preflight as preflight
    import kagari_vnext_task_binding as task_binding


QUALIFICATION = "UNQUALIFIED"
EFFECTIVE_POLICY = "UNVERIFIED"
DISPATCH_ALLOWED = False
TASK_COMPLETED = False
ASK_CAPABILITY = "UNQUALIFIED"

OPERATION = "claude-lane-b-no-tools-diagnostic"
FIXED_PROMPT = (
    "Reply with exactly the ASCII string KUMIKI_LANE_B_DIAGNOSTIC_OK and "
    "nothing else. Do not call tools."
)
EXPECTED_RESPONSE = "KUMIKI_LANE_B_DIAGNOSTIC_OK"
EXPECTED_VERSION = "2.1.293"
SUPPORTED_PERSONAL_PLANS = frozenset(("pro", "max", "ultra"))
MAX_CAPTURE = 64 * 1024
MAX_LINE = 8 * 1024
MAX_EVENTS = 128
MAX_JSON_DEPTH = 32
RECEIPT_SCHEMA = 1
DEFAULT_TIMEOUT = 5.0
DEFAULT_LOCK_TIMEOUT = 0.5

_FULL_HELP_FLAGS = (
    "--print",
    "--output-format",
    "--verbose",
    "--session-id",
    "--permission-mode",
    "--setting-sources",
    "--tools",
    "--disallowedTools",
    "--strict-mcp-config",
    "--mcp-config",
)
_HEX_40_OR_64 = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_CONTRACT_ID = re.compile(r"[0-9a-f]{64}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_OBSERVATION_REASONS = frozenset((
    "SYNTHETIC_TURN_RESULT", "TOOL_POLICY_UNSUPPORTED", "RATE_LIMITED", "MALFORMED_STREAM",
    "OUTPUT_LIMIT", "PROCESS_TIMEOUT", "PROCESS_IO_ERROR", "PROCESS_NONZERO",
    "TRANSPORT_FAILURE", "SCHEMA_UNSUPPORTED", "SESSION_MISMATCH", "WORKTREE_MISMATCH",
    "RESPONSE_MISMATCH", "RESULT_MISMATCH",
))
_OBSERVATION_STAGES = frozenset(("UNKNOWN", "SESSION_OBSERVED", "RESPONSE_OBSERVED", "TURN_RESULT_OBSERVED"))


@dataclass(frozen=True)
class LaneBTask:
    """Caller-supplied locator; #191 metadata remains the identity authority."""

    project_root: Path
    worktree: Path
    repository: str
    issue: int
    branch_ref: str
    base_revision: str
    expected_contract_id: str
    remote: str = "origin"


@dataclass(frozen=True)
class LaneBGrantSubject:
    """Immutable, exact subject presented to the private fixture grant hook."""

    operation: str
    repository: str
    remote: str
    issue: int
    branch_ref: str
    base_revision: str
    contract_id: str
    project_root: str
    worktree_cwd: str
    git_common_dir: str
    binary_path: str
    candidate_binary_version: str
    config_context_sha256: str
    session_id: str
    prompt_sha256: str
    argv: tuple[str, ...]
    argv_sha256: str
    policy_sha256: str


@dataclass(frozen=True)
class FixtureInvocation:
    """Immutable call description for an explicitly injected fake transport."""

    executable: Path
    argv: tuple[str, ...]
    cwd: Path
    environment: tuple[tuple[str, str], ...]
    timeout: float
    output_limit: int = MAX_CAPTURE


@dataclass(frozen=True)
class _TaskIdentity:
    project: Path
    worktree: Path
    common_dir: Path
    contract_id: str


@dataclass(frozen=True)
class _ConfigContext:
    config_path: Path
    executable: Path
    executable_signature: tuple[int, ...]
    profile_dir: Path
    plan: str
    context_sha256: str


def _public_result(
    status: str,
    reason: str,
    *,
    stage: str = "NOT_OBSERVED",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return only fixed, sanitized status fields; never paths or raw output."""
    return {
        "qualification": QUALIFICATION,
        "dispatch_allowed": DISPATCH_ALLOWED,
        "task_completed": TASK_COMPLETED,
        "ask_capability": ASK_CAPABILITY,
        "effective_policy": EFFECTIVE_POLICY,
        "status": status,
        "reason": reason,
        "stage": stage,
        "session_id": session_id,
    }


def dispatch(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Inert candidate API: always unqualified and always before side effects."""
    return _public_result("UNQUALIFIED", "CANDIDATE_ONLY")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _valid_session_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def _validate_task_locator(task: LaneBTask, session_id: str) -> bool:
    if not isinstance(task, LaneBTask) or not _valid_session_id(session_id):
        return False
    if (type(task.issue) is not int or task.issue < 1
            or not isinstance(task.repository, str) or not _REPOSITORY.fullmatch(task.repository)
            or not isinstance(task.remote, str) or not _REMOTE.fullmatch(task.remote)
            or not isinstance(task.branch_ref, str) or not task.branch_ref.startswith("refs/heads/")
            or not isinstance(task.base_revision, str) or not _HEX_40_OR_64.fullmatch(task.base_revision)
            or not isinstance(task.expected_contract_id, str)
            or not _CONTRACT_ID.fullmatch(task.expected_contract_id)):
        return False
    try:
        return (
            isinstance(task.project_root, Path) and task.project_root.is_absolute()
            and isinstance(task.worktree, Path) and task.worktree.is_absolute()
            and not any(part in task.branch_ref for part in ("..", "//", "\\", "@{"))
            and not task.branch_ref.endswith(("/", ".")) and ".lock/" not in task.branch_ref
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        return False


def _task_identity(task: LaneBTask) -> _TaskIdentity | None:
    """Revalidate #191 record, Worktree registration, branch and common-dir."""
    try:
        project = task_binding.exact_dir(task.project_root, "Project")
        worktree = task_binding.exact_dir(task.worktree, "Task Worktree")
        task_binding.git(project, "check-ref-format", task.branch_ref)
        args = SimpleNamespace(project_root=project, worktree=worktree,
                               repository=task.repository, issue=task.issue,
                               branch_ref=task.branch_ref, base_revision=task.base_revision,
                               remote=task.remote)
        verified_worktree, contract_id = task_binding.verify_task_record(args)
        if verified_worktree != worktree or contract_id != task.expected_contract_id:
            return None
        common = Path(task_binding.git(project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        if not common.is_absolute() or common.resolve(strict=True) != common or not common.is_dir():
            return None
        if task_binding.git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir") != str(common):
            return None
        return _TaskIdentity(project, worktree, common, contract_id)
    except Exception:
        return None


def _config_context(
    config_path: Path,
    identity: _TaskIdentity,
    managed_settings_root: Path,
) -> _ConfigContext | None:
    """Read the explicit personal-only config and bounded settings snapshot."""
    try:
        if not config_path.is_absolute() or not managed_settings_root.is_absolute(): return None
        canonical_config = preflight._canonical_config_path(config_path)
        document, status_code = preflight._read_config(canonical_config)
        if document is None or status_code: return None
        profiles = document.get("_validated_profiles")
        if not isinstance(profiles, dict) or set(profiles) != {"personal"}: return None
        profile_dir, plan = profiles["personal"]
        if plan not in SUPPORTED_PERSONAL_PLANS: return None
        raw_bin = document.get("claude_bin")
        if not isinstance(raw_bin, str) or not os.path.isabs(raw_bin): return None
        executable, executable_status = preflight._executable_path(raw_bin)
        if (executable is None or executable_status or str(executable) != raw_bin
                or not preflight._no_symlink_components(Path(raw_bin))): return None
        config_signature = preflight._config_file_signature(canonical_config)
        executable_signature = preflight._executable_identity(raw_bin, executable)
        profile_identity = preflight._profile_directory_identity(profile_dir)
        if config_signature is None or executable_signature is None or profile_identity is None: return None
        config_bytes = preflight._read_regular_file(
            canonical_config, preflight.CONFIG_LIMIT, strict_ancestors=True
        )
        worktree_status, worktree_snapshot = preflight._inspect_settings(
            profile_dir, identity.worktree, managed_settings_root
        )
        project_status, project_snapshot = preflight._inspect_settings(
            profile_dir, identity.project, managed_settings_root
        )
        if worktree_status or project_status: return None
        settings_snapshot = tuple(
            sorted(("project:" + path, signature) for path, signature in project_snapshot)
            + sorted(("worktree:" + path, signature) for path, signature in worktree_snapshot)
        )
        context_fields = {
            "config_sha256": _sha256(config_bytes),
            "config_signature": config_signature,
            "executable": str(executable),
            "executable_signature": executable_signature,
            "profile_dir": str(profile_dir),
            "profile_identity": profile_identity,
            "plan": plan,
            "settings_snapshot": settings_snapshot,
        }
        context_hash = _sha256(_canonical_json(context_fields))
        return _ConfigContext(
            canonical_config, executable, executable_signature, profile_dir, plan, context_hash
        )
    except Exception:
        return None


def _same_config_context(
    expected: _ConfigContext,
    config_path: Path,
    identity: _TaskIdentity,
    managed_settings_root: Path,
) -> bool:
    return _config_context(config_path, identity, managed_settings_root) == expected


def _argv(session_id: str) -> tuple[str, ...]:
    return (
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--session-id",
        session_id,
        "--permission-mode",
        "default",
        "--setting-sources",
        "user",
        "--tools",
        "",
        "--disallowedTools",
        "mcp__*",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        FIXED_PROMPT,
    )


def _grant_subject(
    task: LaneBTask,
    identity: _TaskIdentity,
    context: _ConfigContext,
    session_id: str,
) -> LaneBGrantSubject:
    args = _argv(session_id)
    policy = {
        "operation": OPERATION,
        "argv": args,
        "expected_version": EXPECTED_VERSION,
        "effective_policy": EFFECTIVE_POLICY,
    }
    return LaneBGrantSubject(
        OPERATION,
        task.repository,
        task.remote,
        task.issue,
        task.branch_ref,
        task.base_revision,
        identity.contract_id,
        str(identity.project),
        str(identity.worktree),
        str(identity.common_dir),
        str(context.executable),
        EXPECTED_VERSION,
        context.context_sha256,
        session_id,
        _sha256(FIXED_PROMPT.encode("ascii")),
        args,
        _sha256(_canonical_json(args)),
        _sha256(_canonical_json(policy)),
    )


def _runtime_root(value: Path, identity: _TaskIdentity) -> Path | None:
    """Require an existing canonical, same-user 0700 cache outside both trees."""
    try:
        if not isinstance(value, Path) or not value.is_absolute() or not preflight._no_symlink_components(value):
            return None
        canonical = value.resolve(strict=True)
        info = value.lstat()
        if (canonical != value or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700): return None
        roots = (identity.project, identity.worktree, identity.common_dir)
        if any(canonical == root or canonical in root.parents or root in canonical.parents for root in roots):
            return None
        return canonical
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def _namespace_name(task: LaneBTask, identity: _TaskIdentity) -> str:
    key = f"{task.repository}\0{identity.common_dir}\0{task.issue}".encode("utf-8")
    return "lane-b-" + _sha256(key)


def _check_private_dir(path: Path, *, owner: int | None = None) -> bool:
    try:
        info = path.lstat()
        return (stat.S_ISDIR(info.st_mode) and info.st_uid == (os.geteuid() if owner is None else owner)
                and stat.S_IMODE(info.st_mode) == 0o700)
    except OSError:
        return False


def _check_private_file(path: Path, *, owner: int | None = None) -> bool:
    try:
        info = path.lstat()
        return (stat.S_ISREG(info.st_mode) and info.st_uid == (os.geteuid() if owner is None else owner)
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
    except OSError:
        return False


class _Lock:
    def __init__(self, fd: int, root: Path, namespace: Path, created: bool,
                 root_id: tuple[int, ...], namespace_id: tuple[int, ...],
                 lock_id: tuple[int, ...]) -> None:
        self.fd = fd
        self.root = root
        self.namespace = namespace
        self.created = created
        self.root_id = root_id
        self.namespace_id = namespace_id
        self.lock_id = lock_id

    @staticmethod
    def _dir_id(path: Path) -> tuple[int, ...]:
        info = path.lstat()
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid

    @staticmethod
    def _file_id(info: os.stat_result) -> tuple[int, ...]:
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink

    def valid(self) -> bool:
        try:
            lock_path = self.namespace / "lock"
            if (self.namespace.parent != self.root or not preflight._no_symlink_components(self.root)
                    or self.root.resolve(strict=True) != self.root
                    or not _check_private_dir(self.root) or not _check_private_dir(self.namespace)):
                return False
            root_id = self._dir_id(self.root)
            namespace_id = self._dir_id(self.namespace)
            path_info = lock_path.lstat()
            fd_info = os.fstat(self.fd)
            return (root_id == self.root_id and namespace_id == self.namespace_id
                    and self._file_id(path_info) == self.lock_id
                    and self._file_id(fd_info) == self.lock_id
                    and stat.S_ISREG(fd_info.st_mode) and fd_info.st_uid == os.geteuid()
                    and stat.S_IMODE(fd_info.st_mode) == 0o600 and fd_info.st_nlink == 1)
        except (OSError, RuntimeError, ValueError):
            return False

    def close(self) -> None:
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def _open_lock(root: Path, namespace: Path, timeout: float, *, create: bool) -> tuple[_Lock | None, str]:
    lock_path = namespace / "lock"
    if (namespace.parent != root or not preflight._no_symlink_components(root)
            or not _check_private_dir(root) or not _check_private_dir(namespace)):
        return None, "INVALID"
    flags = os.O_RDWR
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    flags |= (getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
              | getattr(os, "O_NONBLOCK", 0))
    created = False
    try:
        fd = os.open(lock_path, flags, 0o600)
        created = create
    except FileExistsError:
        if not create:
            return None, "EXISTS"
        try:
            fd = os.open(
                lock_path,
                os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
        except OSError:
            return None, "INVALID"
    except FileNotFoundError:
        return None, "MISSING"
    except OSError:
        return None, "INVALID"
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                root_id = _Lock._dir_id(root)
                namespace_id = _Lock._dir_id(namespace)
                path_info = lock_path.lstat()
                fd_info = os.fstat(fd)
                lock_id = _Lock._file_id(fd_info)
                if (not stat.S_ISDIR(root_id[2]) or root_id[3] != os.geteuid()
                        or stat.S_IMODE(root_id[2]) != 0o700
                        or not stat.S_ISDIR(namespace_id[2]) or namespace_id[3] != os.geteuid()
                        or stat.S_IMODE(namespace_id[2]) != 0o700
                        or not stat.S_ISREG(fd_info.st_mode) or fd_info.st_uid != os.geteuid()
                        or stat.S_IMODE(fd_info.st_mode) != 0o600 or fd_info.st_nlink != 1
                        or _Lock._file_id(path_info) != lock_id):
                    os.close(fd)
                    return None, "INVALID"
                lock = _Lock(fd, root, namespace, created, root_id, namespace_id, lock_id)
                if not lock.valid():
                    lock.close()
                    return None, "INVALID"
                return lock, ""
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return None, "LOCK_TIMEOUT"
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    except OSError:
        os.close(fd)
        return None, "INVALID"


def _create_namespace(root: Path, namespace: Path) -> tuple[bool, bool]:
    created = False
    try:
        namespace.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    except OSError:
        return False, False
    if not _check_private_dir(root) or not _check_private_dir(namespace) or namespace.parent != root:
        return False, False
    if created:
        try:
            fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try: os.fsync(fd)
            finally: os.close(fd)
        except OSError:
            return False, False
    return True, created


class _ReceiptProblem(Exception):
    pass


def _read_receipt(path: Path) -> dict[str, Any]:
    if not _check_private_file(path): raise _ReceiptProblem
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise _ReceiptProblem from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size > 16 * 1024): raise _ReceiptProblem
        chunks: list[bytes] = []
        total = 0
        while total <= 16 * 1024:
            chunk = os.read(fd, min(4096, 16 * 1024 + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            chunks.append(chunk)
        if total > 16 * 1024: raise _ReceiptProblem
        value = preflight._loads_strict(b"".join(chunks))
        if _depth(value) > MAX_JSON_DEPTH:
            raise _ReceiptProblem
    except (OSError, ValueError, UnicodeError, RecursionError, preflight._DuplicateJSONKey) as exc:
        raise _ReceiptProblem from exc
    finally:
        os.close(fd)
    if not isinstance(value, dict): raise _ReceiptProblem
    return value


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> bool:
    data = _canonical_json(value)
    if len(data) > 16 * 1024: return False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        return False
    try:
        if not _check_private_file(path): return False
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if count <= 0: return False
            view = view[count:]
        os.fsync(fd)
    except OSError:
        return False
    finally:
        os.close(fd)
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        return False
    return _check_private_file(path)


def _task_binding(identity: _TaskIdentity, task: LaneBTask) -> dict[str, Any]:
    return {"repository": task.repository, "remote": task.remote, "issue": task.issue, "branch_ref": task.branch_ref,
            "base_revision": task.base_revision, "contract_id": identity.contract_id,
            "project_root": str(identity.project), "worktree": str(identity.worktree),
            "git_common_dir": str(identity.common_dir)}


def _receipt_subject(
    task: LaneBTask,
    identity: _TaskIdentity,
    context: _ConfigContext,
    session_id: str,
    version: str,
) -> dict[str, Any]:
    args = _argv(session_id)
    return {"schema_version": RECEIPT_SCHEMA, "task": _task_binding(identity, task),
            "session_id": session_id, "binary_path": str(context.executable),
            "binary_signature": list(context.executable_signature), "binary_version": version,
            "config_context_sha256": context.context_sha256, "credential_profile": "personal",
            "personal_plan": context.plan, "prompt_sha256": _sha256(FIXED_PROMPT.encode("ascii")),
            "argv_sha256": _sha256(_canonical_json(args)), "policy_sha256": _policy_digest(session_id)}


def _policy_digest(session_id: str) -> str:
    return _sha256(_canonical_json({
        "operation": OPERATION, "argv": _argv(session_id),
        "expected_version": EXPECTED_VERSION, "effective_policy": EFFECTIVE_POLICY,
    }))


def _valid_receipt_subject(subject: Any, task: LaneBTask, identity: _TaskIdentity,
                           session_id: str) -> bool:
    fields = {"schema_version", "task", "session_id", "binary_path", "binary_signature",
              "binary_version", "config_context_sha256", "credential_profile", "personal_plan",
              "prompt_sha256", "argv_sha256", "policy_sha256"}
    if not isinstance(subject, dict) or set(subject) != fields:
        return False
    signature = subject.get("binary_signature")
    binary_path = subject.get("binary_path")
    return (
        type(subject.get("schema_version")) is int and subject["schema_version"] == RECEIPT_SCHEMA
        and subject.get("task") == _task_binding(identity, task)
        and subject.get("session_id") == session_id
        and isinstance(binary_path, str) and Path(binary_path).is_absolute()
        and str(Path(binary_path)) == binary_path
        and isinstance(signature, list) and len(signature) == 6
        and all(type(part) is int for part in signature)
        and subject.get("binary_version") == EXPECTED_VERSION
        and isinstance(subject.get("config_context_sha256"), str)
        and bool(_CONTRACT_ID.fullmatch(subject["config_context_sha256"]))
        and subject.get("credential_profile") == "personal"
        and isinstance(subject.get("personal_plan"), str)
        and subject.get("personal_plan") in SUPPORTED_PERSONAL_PLANS
        and subject.get("prompt_sha256") == _sha256(FIXED_PROMPT.encode("ascii"))
        and subject.get("argv_sha256") == _sha256(_canonical_json(_argv(session_id)))
        and subject.get("policy_sha256") == _policy_digest(session_id)
    )


def _matching_receipt_subject(
    pending: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    return (set(pending) == {"schema_version", "kind", "subject"}
            and type(pending.get("schema_version")) is int
            and pending.get("kind") == "pending" and pending.get("subject") == expected)


def _receipt_observation(
    namespace: Path, expected: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    pending_path = namespace / "pending.json"
    result_path = namespace / "result.json"
    try:
        pending_exists = pending_path.lstat() is not None
    except FileNotFoundError:
        pending_exists = False
    except OSError:
        return None, "RECONCILIATION_REQUIRED"
    try:
        result_exists = result_path.lstat() is not None
    except FileNotFoundError:
        result_exists = False
    except OSError:
        return None, "RECONCILIATION_REQUIRED"
    if not pending_exists and not result_exists: return None, ""
    if not pending_exists or not result_exists: return None, "RECONCILIATION_REQUIRED"
    try:
        pending = _read_receipt(pending_path)
        result = _read_receipt(result_path)
    except _ReceiptProblem:
        return None, "RECONCILIATION_REQUIRED"
    if (
        type(pending.get("schema_version")) is not int
        or pending.get("schema_version") != RECEIPT_SCHEMA
        or not _matching_receipt_subject(pending, expected)
        or set(result) != {"schema_version", "kind", "pending_sha256", "observation"}
        or type(result.get("schema_version")) is not int
        or result.get("schema_version") != RECEIPT_SCHEMA
        or result.get("kind") != "result"
        or result.get("pending_sha256") != _sha256(_canonical_json(pending))
    ):
        return None, "RECONCILIATION_REQUIRED"
    observation = result.get("observation")
    if not _valid_observation(observation): return None, "RECONCILIATION_REQUIRED"
    return observation, "MATCHED_RESULT"


def _valid_observation(value: Any) -> bool:
    if (not isinstance(value, dict) or set(value) != {"status", "reason", "stage"}
            or not all(isinstance(value[key], str) for key in ("status", "reason", "stage"))
            or value["status"] not in {"OBSERVED", "BLOCKED", "UNKNOWN"}
            or value["reason"] not in _OBSERVATION_REASONS or value["stage"] not in _OBSERVATION_STAGES):
        return False
    if value["status"] == "OBSERVED":
        return value["reason"] == "SYNTHETIC_TURN_RESULT" and value["stage"] == "TURN_RESULT_OBSERVED"
    if value["stage"] == "TURN_RESULT_OBSERVED":
        return False
    if value["status"] == "BLOCKED":
        return value["reason"] == "TOOL_POLICY_UNSUPPORTED" and value["stage"] in {
            "SESSION_OBSERVED", "RESPONSE_OBSERVED"
        }
    return value["reason"] not in {"SYNTHETIC_TURN_RESULT", "TOOL_POLICY_UNSUPPORTED"}


def _write_result(
    namespace: Path,
    pending: Mapping[str, Any],
    observation: Mapping[str, str],
) -> bool:
    return _write_exclusive(namespace / "result.json", {
        "schema_version": RECEIPT_SCHEMA, "kind": "result",
        "pending_sha256": _sha256(_canonical_json(pending)), "observation": dict(observation),
    })


def _observation_result(
    observation: Mapping[str, str], session_id: str
) -> dict[str, Any]:
    status = observation["status"]
    return _public_result(
        status,
        observation["reason"],
        stage=observation["stage"],
        session_id=session_id,
    )


def observe(
    task: LaneBTask,
    *,
    runtime_root: Path,
    session_id: str,
) -> dict[str, Any]:
    """Read-only historical self-consistency view, not an authenticated ledger.

    It does not revalidate current CLI/auth state or prove current account state.
    """
    safe_session = session_id if _valid_session_id(session_id) else None
    if not _validate_task_locator(task, session_id):
        return _public_result("UNQUALIFIED", "INVALID_LOCATOR", session_id=safe_session)
    identity = _task_identity(task)
    if identity is None: return _public_result("UNQUALIFIED", "TASK_IDENTITY_MISMATCH", session_id=session_id)
    root = _runtime_root(runtime_root, identity)
    if root is None: return _public_result("UNQUALIFIED", "RUNTIME_ROOT_INVALID", session_id=session_id)
    namespace = root / _namespace_name(task, identity)
    if not _check_private_dir(namespace) or not _check_private_file(namespace / "lock"):
        return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
    lock, error = _open_lock(root, namespace, DEFAULT_LOCK_TIMEOUT, create=False)
    if lock is None: return _public_result("UNKNOWN", "LOCK_TIMEOUT" if error == "LOCK_TIMEOUT" else "RECONCILIATION_REQUIRED", session_id=session_id)
    try:
        if (not lock.valid() or _task_identity(task) != identity):
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        try:
            pending = _read_receipt(namespace / "pending.json")
        except _ReceiptProblem: return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        subject = pending.get("subject")
        if not _valid_receipt_subject(subject, task, identity, session_id):
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        observation, status = _receipt_observation(namespace, subject)
        if status != "MATCHED_RESULT" or observation is None: return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        if not _storage_valid(runtime_root, identity, root, namespace, lock, task):
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        return _observation_result(observation, session_id)
    finally:
        lock.close()


def _depth(value: Any, current: int = 0) -> int:
    if current > MAX_JSON_DEPTH:
        return current
    if isinstance(value, dict):
        return max([current, *(_depth(k, current + 1) for k in value.keys()), *(_depth(v, current + 1) for v in value.values())])
    if isinstance(value, list):
        return max([current, *(_depth(item, current + 1) for item in value)])
    return current


def _parse_events(stdout: bytes) -> tuple[list[dict[str, Any]] | None, str]:
    if not isinstance(stdout, bytes) or len(stdout) > MAX_CAPTURE:
        return None, "OUTPUT_LIMIT"
    if not stdout:
        return [], ""
    parts = stdout.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()
    failure = "OUTPUT_LIMIT" if len(parts) > MAX_EVENTS else ""
    parts = parts[:MAX_EVENTS]
    events: list[dict[str, Any]] = []
    for line in parts:
        if line.endswith(b"\r"): line = line[:-1]
        if not line or len(line) > MAX_LINE: return events, "OUTPUT_LIMIT"
        try:
            value = preflight._loads_strict(line)
        except (ValueError, UnicodeError, RecursionError, preflight._DuplicateJSONKey):
            return events, "MALFORMED_STREAM"
        if not isinstance(value, dict) or _depth(value) > MAX_JSON_DEPTH: return events, "MALFORMED_STREAM"
        events.append(value)
    return events, failure


def _has_tool_content(value: Any) -> bool:
    if isinstance(value, dict):
        if any(value.get(key) is not None for key in (
            "deferred_tool_use", "tool_use", "server_tool_use", "tool_result", "tool_call"
        )):
            return True
        if any(key in value and value[key] != [] for key in ("tools", "mcp_servers", "plugins")):
            return True
        content_type = value.get("type")
        if isinstance(content_type, str) and content_type in {
            "tool_use",
            "server_tool_use",
            "tool_result",
            "permission_denial",
            "tool_call",
        }:
            return True
        if value.get("stop_reason") == "tool_deferred":
            return True
        return any(_has_tool_content(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_tool_content(child) for child in value)
    return False


def _session_matches(event: Mapping[str, Any], session_id: str) -> bool:
    return event.get("session_id") == session_id


def _contains_rate_limit_error(value: Any) -> bool:
    if isinstance(value, dict):
        error_type = value.get("type")
        if isinstance(error_type, str) and error_type in {"rate_limit", "rate_limit_error"}:
            return True
        return any(_contains_rate_limit_error(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_rate_limit_error(child) for child in value)
    return False


def parse_stream(
    stdout: bytes,
    stderr: bytes,
    *,
    requested_session_id: str,
    expected_cwd: Path,
    returncode: int,
    timed_out: bool = False,
    overflow: bool = False,
    io_error: bool = False,
) -> dict[str, str]:
    """Parse a bounded synthetic NDJSON candidate; never return model content."""
    safe = lambda status, reason, stage: {"status": status, "reason": reason, "stage": stage}
    if (not _valid_session_id(requested_session_id) or not isinstance(expected_cwd, Path)
            or not expected_cwd.is_absolute() or type(returncode) is not int
            or any(type(flag) is not bool for flag in (timed_out, overflow, io_error))):
        return safe("UNKNOWN", "SCHEMA_UNSUPPORTED", "UNKNOWN")
    if not isinstance(stdout, bytes) or not isinstance(stderr, bytes) or len(stdout) > MAX_CAPTURE:
        return safe("UNKNOWN", "OUTPUT_LIMIT", "UNKNOWN")
    if len(stdout) + len(stderr) > MAX_CAPTURE:
        overflow = True
    events, failure = _parse_events(stdout)
    if events is None: return safe("UNKNOWN", failure, "UNKNOWN")
    if not events:
        if timed_out: return safe("UNKNOWN", "PROCESS_TIMEOUT", "UNKNOWN")
        if overflow: return safe("UNKNOWN", "OUTPUT_LIMIT", "UNKNOWN")
        if io_error: return safe("UNKNOWN", "PROCESS_IO_ERROR", "UNKNOWN")
        return safe("UNKNOWN", failure or "MALFORMED_STREAM", "UNKNOWN")
    first = events[0]
    if first.get("type") != "system" or first.get("subtype") != "init":
        return safe("UNKNOWN", "MALFORMED_STREAM", "UNKNOWN")
    if not _session_matches(first, requested_session_id):
        return safe("UNKNOWN", "SESSION_MISMATCH", "UNKNOWN")
    if first.get("cwd") != str(expected_cwd): return safe("UNKNOWN", "WORKTREE_MISMATCH", "UNKNOWN")
    stage = "SESSION_OBSERVED"
    if any("session_id" in event and not _session_matches(event, requested_session_id) for event in events[1:]):
        return safe("UNKNOWN", "SESSION_MISMATCH", stage)
    if any(_has_tool_content(event) for event in events):
        return safe("BLOCKED", "TOOL_POLICY_UNSUPPORTED", stage)
    if any(event.get("type") == "system" and event.get("subtype") == "init" for event in events[1:]):
        return safe("UNKNOWN", "MALFORMED_STREAM", stage)
    if first.get("version") not in (None, EXPECTED_VERSION) or first.get("permissionMode") not in (None, "default"):
        return safe("UNKNOWN", "SCHEMA_UNSUPPORTED", stage)
    if first.get("tools") != [] or first.get("mcp_servers") != [] or first.get("plugins", []) != []:
        return safe("BLOCKED", "TOOL_POLICY_UNSUPPORTED", stage)
    if len(events) < 2:
        if timed_out: return safe("UNKNOWN", "PROCESS_TIMEOUT", stage)
        if overflow: return safe("UNKNOWN", "OUTPUT_LIMIT", stage)
        if io_error: return safe("UNKNOWN", "PROCESS_IO_ERROR", stage)
        return safe("UNKNOWN", failure or "MALFORMED_STREAM", stage)
    assistant = events[1]
    if assistant.get("type") != "assistant" or not _session_matches(assistant, requested_session_id):
        return safe("UNKNOWN", "SESSION_MISMATCH" if assistant.get("type") == "assistant" else "MALFORMED_STREAM", stage)
    if assistant.get("parent_tool_use_id") is not None:
        return safe("BLOCKED", "TOOL_POLICY_UNSUPPORTED", stage)
    message = assistant.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return safe("UNKNOWN", "SCHEMA_UNSUPPORTED", stage)
    content = message.get("content")
    if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
            or content[0].get("type") != "text" or content[0].get("text") != EXPECTED_RESPONSE):
        return safe("UNKNOWN", "RESPONSE_MISMATCH", stage)
    stage = "RESPONSE_OBSERVED"
    stop_reasons = (("stop_reason" in assistant, assistant.get("stop_reason")),
                    ("stop_reason" in message, message.get("stop_reason")))
    if any(present and value == "tool_deferred" for present, value in stop_reasons):
        return safe("BLOCKED", "TOOL_POLICY_UNSUPPORTED", stage)
    if any(present and value != "end_turn" for present, value in stop_reasons):
        return safe("UNKNOWN", "SCHEMA_UNSUPPORTED", stage)
    if timed_out: return safe("UNKNOWN", "PROCESS_TIMEOUT", stage)
    if overflow: return safe("UNKNOWN", "OUTPUT_LIMIT", stage)
    if io_error: return safe("UNKNOWN", "PROCESS_IO_ERROR", stage)
    if failure: return safe("UNKNOWN", failure, stage)
    if any(_session_matches(event, requested_session_id) and _contains_rate_limit_error(event)
           for event in events[2:]):
        return safe("UNKNOWN", "RATE_LIMITED", stage)
    if len(events) != 3: return safe("UNKNOWN", "MALFORMED_STREAM", stage)
    terminal = events[2]
    if terminal.get("type") != "result" or not _session_matches(terminal, requested_session_id):
        return safe("UNKNOWN", "SESSION_MISMATCH" if terminal.get("type") == "result" else "MALFORMED_STREAM", stage)
    if _contains_rate_limit_error(terminal.get("errors")) or _contains_rate_limit_error(terminal.get("error")):
        return safe("UNKNOWN", "RATE_LIMITED", stage)
    errors = terminal.get("errors", [])
    if (not isinstance(errors, list) or errors or "error" in terminal
            or "api_error_status" in terminal):
        return safe("UNKNOWN", "RESULT_MISMATCH", stage)
    if _has_tool_content(terminal): return safe("BLOCKED", "TOOL_POLICY_UNSUPPORTED", stage)
    denials = terminal.get("permission_denials")
    if isinstance(denials, list) and denials: return safe("BLOCKED", "TOOL_POLICY_UNSUPPORTED", stage)
    if (terminal.get("subtype") != "success" or terminal.get("is_error") is not False
            or ("terminal_reason" in terminal and terminal.get("terminal_reason") != "success")
            or ("stop_reason" in terminal and terminal.get("stop_reason") != "end_turn")
            or terminal.get("result") != EXPECTED_RESPONSE or denials != []
            or type(terminal.get("num_turns")) is not int or terminal.get("num_turns") != 1):
        return safe("UNKNOWN", "RESULT_MISMATCH", stage)
    if returncode != 0: return safe("UNKNOWN", "PROCESS_NONZERO", stage)
    return safe("OBSERVED", "SYNTHETIC_TURN_RESULT", "TURN_RESULT_OBSERVED")


def _validate_transport(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    timed_out = raw.get("timed_out", False)
    overflow = raw.get("overflow", False)
    io_error = raw.get("io_error", False)
    if any(type(flag) is not bool for flag in (timed_out, overflow, io_error)):
        return None
    stdout = raw.get("stdout", b"")
    stderr = raw.get("stderr", b"")
    if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
        return None
    if len(stdout) + len(stderr) > MAX_CAPTURE:
        return {"stdout": stdout[:MAX_CAPTURE], "stderr": b"", "returncode": -1,
                "timed_out": timed_out, "overflow": True, "io_error": io_error}
    code = raw.get("returncode")
    if timed_out or overflow or io_error:
        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": -1,
            "timed_out": timed_out,
            "overflow": overflow,
            "io_error": io_error,
        }
    if type(code) is not int:
        return None
    return {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": code,
        "timed_out": False,
        "overflow": False,
        "io_error": False,
    }


def _full_help_supported(context: _ConfigContext, identity: _TaskIdentity, env: Mapping[str, str], timeout: float) -> bool:
    run = preflight._run_bounded(
        context.executable,
        ("--help",),
        cwd=identity.worktree,
        env=preflight._child_environment(env, context.profile_dir),
        timeout=timeout,
    )
    if (
        run is None
        or run.get("timed_out")
        or run.get("overflow")
        or run.get("io_error")
        or run.get("returncode") != 0
    ):
        return False
    output = run.get("stdout", b"") + run.get("stderr", b"")
    try:
        text = output.decode("utf-8", errors="strict")
    except (AttributeError, UnicodeError):
        return False
    return all(
        re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", text)
        is not None
        for flag in _FULL_HELP_FLAGS
    )


def _auth_probe(
    context: _ConfigContext,
    identity: _TaskIdentity,
    environ: Mapping[str, str],
    managed_settings_root: Path,
    timeout: float,
) -> bool:
    result = preflight.run_preflight(
        context.config_path,
        identity.worktree,
        profile="personal",
        probe_auth=True,
        environ=environ,
        managed_settings_root=managed_settings_root,
        timeout=timeout,
    )
    return (
        result.get("status") == "AUTH_STATUS_MATCHED"
        and result.get("version") == EXPECTED_VERSION
        and set(result.get("capabilities", ())) == set(preflight.EXPECTED_CAPABILITIES)
    )


def _fixed_observation(status: str, reason: str, stage: str = "UNKNOWN") -> dict[str, str]:
    return {"status": status, "reason": reason, "stage": stage}


def _authorize_exact(
    authorize: Callable[[LaneBGrantSubject], Any], subject: LaneBGrantSubject
) -> bool:
    supplied = LaneBGrantSubject(**dict(subject.__dict__))
    try:
        result = authorize(supplied)
    except Exception:
        return False
    return result is True and supplied == subject


def _context_valid(
    task: LaneBTask,
    identity: _TaskIdentity,
    context: _ConfigContext,
    config_path: Path,
    managed_settings_root: Path,
) -> bool:
    return (_task_identity(task) == identity
            and _same_config_context(context, config_path, identity, managed_settings_root))


def _storage_valid(
    runtime_root: Path,
    identity: _TaskIdentity,
    root: Path,
    namespace: Path,
    lock: _Lock,
    task: LaneBTask,
) -> bool:
    return (_runtime_root(runtime_root, identity) == root
            and namespace == root / _namespace_name(task, identity)
            and lock.root == root and lock.namespace == namespace and lock.valid())


def _exercise_fixture(
    task: LaneBTask,
    *,
    config_path: Path,
    runtime_root: Path,
    session_id: str,
    authorize: Callable[[LaneBGrantSubject], Any],
    fixture_transport: Callable[[FixtureInvocation], Mapping[str, Any]],
    environ: Mapping[str, str],
    managed_settings_root: Path,
    timeout: float = DEFAULT_TIMEOUT,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> dict[str, Any]:
    """Private offline seam; requires injected grant, fake CLI config and transport."""
    safe_session = session_id if _valid_session_id(session_id) else None
    if not _validate_task_locator(task, session_id):
        return _public_result("UNQUALIFIED", "INVALID_LOCATOR", session_id=safe_session)
    if (not callable(authorize) or not callable(fixture_transport)
            or not isinstance(config_path, Path) or not isinstance(runtime_root, Path)
            or not isinstance(managed_settings_root, Path) or not isinstance(environ, Mapping)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in environ.items())
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30
            or type(lock_timeout) not in (int, float) or not math.isfinite(lock_timeout)
            or not 0 <= lock_timeout <= 5):
        return _public_result("UNQUALIFIED", "FIXTURE_INPUT_INVALID", session_id=safe_session)
    supplied_environment = dict(environ)
    if preflight._environment_has_override(supplied_environment):
        return _public_result("UNQUALIFIED", "PERSONAL_PROFILE_REQUIRED", session_id=session_id)
    identity = _task_identity(task)
    if identity is None: return _public_result("UNQUALIFIED", "TASK_IDENTITY_MISMATCH", session_id=session_id)
    root = _runtime_root(runtime_root, identity)
    if root is None: return _public_result("UNQUALIFIED", "RUNTIME_ROOT_INVALID", session_id=session_id)
    context = _config_context(config_path, identity, managed_settings_root)
    if context is None: return _public_result("UNQUALIFIED", "PERSONAL_PROFILE_REQUIRED", session_id=session_id)
    subject = _grant_subject(task, identity, context, session_id)
    namespace = root / _namespace_name(task, identity)
    lock: _Lock | None = None
    try:
        if namespace.exists():
            if not _check_private_dir(namespace) or not _check_private_file(namespace / "lock"):
                return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
            lock, error = _open_lock(root, namespace, lock_timeout, create=False)
        else:
            # Even cache allocation is an effect. A rejected initial grant must
            # not leave a namespace/lock that poisons the next authorized try.
            root_id = _Lock._dir_id(root)
            first_grant = _authorize_exact(authorize, subject)
            if not first_grant:
                return _public_result("UNQUALIFIED", "AUTHORIZATION_DENIED", session_id=session_id)
            if not _context_valid(task, identity, context, config_path, managed_settings_root):
                return _public_result("UNQUALIFIED", "CONTEXT_DRIFT", session_id=session_id)
            if (_runtime_root(runtime_root, identity) != root
                    or _Lock._dir_id(root) != root_id):
                return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
            made, created_namespace = _create_namespace(root, namespace)
            if not made: return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
            if not created_namespace:
                if not _check_private_file(namespace / "lock"):
                    return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
                lock, error = _open_lock(root, namespace, lock_timeout, create=False)
            else:
                lock, error = _open_lock(root, namespace, lock_timeout, create=True)
        if lock is None:
            reason = "LOCK_TIMEOUT" if error == "LOCK_TIMEOUT" else "RECONCILIATION_REQUIRED"
            return _public_result("UNKNOWN", reason, session_id=session_id)
        if not _storage_valid(runtime_root, identity, root, namespace, lock, task):
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        if not _context_valid(task, identity, context, config_path, managed_settings_root):
            return _public_result("UNQUALIFIED", "CONTEXT_DRIFT", session_id=session_id)
        expected_receipt = _receipt_subject(task, identity, context, session_id, EXPECTED_VERSION)
        prior, receipt_state = _receipt_observation(namespace, expected_receipt)
        if receipt_state == "MATCHED_RESULT" and prior is not None:
            if not _storage_valid(runtime_root, identity, root, namespace, lock, task):
                return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
            return _observation_result(prior, session_id)
        if receipt_state or not lock.created:
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)

        context_ok = _context_valid(task, identity, context, config_path, managed_settings_root)
        storage_ok = _storage_valid(runtime_root, identity, root, namespace, lock, task)
        if not context_ok: return _public_result("UNQUALIFIED", "CONTEXT_DRIFT", session_id=session_id)
        if not storage_ok: return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)

        if not _auth_probe(context, identity, supplied_environment, managed_settings_root, timeout):
            return _public_result("UNQUALIFIED", "AUTH_NOT_MATCHED", session_id=session_id)
        if not _full_help_supported(context, identity, supplied_environment, timeout):
            return _public_result("UNQUALIFIED", "CLI_FLAGS_UNVERIFIED", session_id=session_id)
        if not _context_valid(task, identity, context, config_path, managed_settings_root):
            return _public_result("UNQUALIFIED", "CONTEXT_DRIFT", session_id=session_id)
        if not _storage_valid(runtime_root, identity, root, namespace, lock, task):
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)

        second_grant = _authorize_exact(authorize, subject)
        context_ok = _context_valid(task, identity, context, config_path, managed_settings_root)
        storage_ok = _storage_valid(runtime_root, identity, root, namespace, lock, task)
        if not second_grant: return _public_result("UNQUALIFIED", "AUTHORIZATION_DENIED", session_id=session_id)
        if not context_ok: return _public_result("UNQUALIFIED", "CONTEXT_DRIFT", session_id=session_id)
        if not storage_ok: return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)

        pending = {"schema_version": RECEIPT_SCHEMA, "kind": "pending",
                   "subject": _receipt_subject(task, identity, context, session_id, EXPECTED_VERSION)}
        if not _context_valid(task, identity, context, config_path, managed_settings_root):
            return _public_result("UNQUALIFIED", "CONTEXT_DRIFT", session_id=session_id)
        if not _storage_valid(runtime_root, identity, root, namespace, lock, task):
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        if not _write_exclusive(namespace / "pending.json", pending): return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)

        third_grant = _authorize_exact(authorize, subject)
        context_ok = _context_valid(task, identity, context, config_path, managed_settings_root)
        storage_ok = _storage_valid(runtime_root, identity, root, namespace, lock, task)
        if not third_grant: return _public_result("UNQUALIFIED", "AUTHORIZATION_DENIED", session_id=session_id)
        if not context_ok: return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        if not storage_ok: return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)

        invocation = FixtureInvocation(context.executable, subject.argv, identity.worktree,
            tuple(sorted(preflight._child_environment(supplied_environment, context.profile_dir).items())), timeout)
        if not _context_valid(task, identity, context, config_path, managed_settings_root):
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        if not _storage_valid(runtime_root, identity, root, namespace, lock, task):
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        try: transport = _validate_transport(fixture_transport(invocation))
        except Exception: transport = None
        observation = (_fixed_observation("UNKNOWN", "TRANSPORT_FAILURE") if transport is None else
            parse_stream(transport["stdout"], transport["stderr"], requested_session_id=session_id,
                         expected_cwd=identity.worktree, returncode=transport["returncode"],
                         timed_out=transport["timed_out"], overflow=transport["overflow"],
                         io_error=transport["io_error"]))
        context_ok = _context_valid(task, identity, context, config_path, managed_settings_root)
        storage_ok = _storage_valid(runtime_root, identity, root, namespace, lock, task)
        if not storage_ok:
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        if not context_ok:
            observation = _fixed_observation("UNKNOWN", "WORKTREE_MISMATCH")
        if not _storage_valid(runtime_root, identity, root, namespace, lock, task):
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        if not _write_result(namespace, pending, observation): return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        if not _storage_valid(runtime_root, identity, root, namespace, lock, task):
            return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
        return _observation_result(observation, session_id)
    except Exception:
        return _public_result("UNKNOWN", "RECONCILIATION_REQUIRED", session_id=session_id)
    finally:
        if lock is not None: lock.close()
