"""Private, disposable Phase C storage experiment; NEVER an executor or grant.

No existing-root opener, production root discovery, result writer, process API,
transport, live policy collector or Main integration. A newly allocated fixture
is the only genesis witness; a directory supplied as allegedly empty is not one.
Linux local-filesystem semantics and cooperative same-user writers are assumed.
See docs/kagari-vnext-claude-r6b-phase-c.md for deliberately deferred gates.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import asdict, dataclass, field
import fcntl
import os
from pathlib import Path
import stat
import tempfile

from . import _kagari_vnext_claude_r6b_phase_b as phase_b
from . import kagari_vnext_claude_lane_b as lane


_LIMIT = 4096
_FILES = ("manifest.json", "pending.json", "reservation.json", "result.json")
_SURFACES = ("settings", "hooks", "mcp", "plugins", "tools", "managed_local", "managed_remote")
_ABSENT = phase_b._digest(None)
_EMPTY = phase_b._digest([])


@dataclass(frozen=True, slots=True)
class _Surface:
    name: str
    presence: str
    sha256: str


@dataclass(frozen=True, slots=True)
class _EffectivePolicyEvidence:
    base: phase_b._PolicyEvidence = field(repr=False)
    surfaces: tuple[_Surface, ...]


def _observe_effective_policy(intent, evidence) -> dict:
    """Only closed synthetic claims; presence/absence is not collected or trusted."""
    invalid = lane._public_result("UNQUALIFIED", "EFFECTIVE_POLICY_EVIDENCE_INVALID")
    if type(evidence) is not _EffectivePolicyEvidence:
        return invalid
    base = phase_b._observe_policy(intent, evidence.base)
    if base["stage"] != "POLICY_EVIDENCE_OBSERVED":
        return base
    surfaces = evidence.surfaces
    if (type(surfaces) is not tuple or len(surfaces) != len(_SURFACES)
            or any(type(s) is not _Surface for s in surfaces)):
        return invalid
    for name, surface in zip(_SURFACES, surfaces):
        if (any(type(v) is not str for v in (surface.name, surface.presence, surface.sha256))
                or surface.name != name or not lane._CONTRACT_ID.fullmatch(surface.sha256)):
            return invalid
        pair = surface.presence, surface.sha256
        if name == "settings":
            if pair != ("PRESENT", intent.policy_pins.effective_settings_sha256):
                return invalid
        elif name.startswith("managed_"):
            if pair != ("ABSENT", _ABSENT) and not (
                    surface.presence == "PRESENT" and surface.sha256 not in {_ABSENT, _EMPTY}):
                return invalid
        elif pair not in {("ABSENT", _ABSENT), ("PRESENT_EMPTY", _EMPTY)}:
            return invalid
    if phase_b._digest([asdict(s) for s in surfaces]) != intent.policy_pins.qualification_report_sha256:
        return invalid
    return lane._public_result("UNQUALIFIED", "SYNTHETIC_EFFECTIVE_POLICY_NOT_QUALIFICATION",
                               stage="SYNTHETIC_EFFECTIVE_POLICY_OBSERVED")


def _manifest(intent) -> bytes | None:
    """Pure bounded manifest, not an inventory-completeness assertion or admission."""
    pending = phase_b._pending_contract(intent)
    if pending is None:
        return None
    return lane._canonical_json({
        "schema_version": 1, "purpose": "DISPOSABLE_SYNTHETIC_ONLY",
        "namespace": pending.namespace, "pending_sha256": phase_b._digest(asdict(pending)),
        "files": _FILES,
    })


class _Problem(Exception):
    pass


def _need(condition):
    if not condition:
        raise _Problem


def _unknown() -> dict:
    return lane._public_result("UNKNOWN", "RECEIPT_RECONCILIATION_REQUIRED")


def _outside_git(path: Path) -> bool:
    # No Git command or config read. Conservative marker-only rejection includes
    # worktrees (.git file), ordinary repos, bare repos and separate admin dirs.
    # Exotic externally routed Git layouts are NOT qualified by this heuristic.
    for parent in (path, *path.parents):
        for name in (".git", "HEAD", "objects", "refs", "commondir"):
            try:
                (parent / name).lstat()
            except FileNotFoundError:
                continue
            return False
    return True


def _dir_id(info):
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
          and stat.S_IMODE(info.st_mode) == 0o700)
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid


def _private_root(path):
    _need(isinstance(path, Path) and path.is_absolute()
          and lane.preflight._no_symlink_components(path)
          and path.resolve(strict=True) == path and _outside_git(path))
    return _dir_id(path.lstat())


def _file_id(info):
    _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
          and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
          and 0 < info.st_size <= _LIMIT)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _names(fd, limit):
    names = []
    with os.scandir(fd) as entries:
        for entry in entries:
            _need(len(names) < limit and entry.name not in names)
            names.append(entry.name)
    return tuple(sorted(names))


def _inventory(fd):
    names = _names(fd, len(_FILES))
    _need(set(names) <= set(_FILES))
    return {name: _file_id(os.stat(name, dir_fd=fd, follow_symlinks=False)) for name in names}


def _read(fd, name, identity):
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                      dir_fd=fd)
    try:
        _need(_file_id(os.fstat(file_fd)) == identity)
        data = bytearray()
        while len(data) <= _LIMIT:
            chunk = os.read(file_fd, _LIMIT + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        _need(len(data) <= _LIMIT and _file_id(os.fstat(file_fd)) == identity
              and _file_id(os.stat(name, dir_fd=fd, follow_symlinks=False)) == identity)
        value = lane.preflight._loads_strict(bytes(data))
        _need(type(value) is dict and lane._canonical_json(value) == bytes(data))
        return value
    finally:
        os.close(file_fd)


def _exclusive(fd, name, data):
    _need(name in _FILES and type(data) is bytes and 0 < len(data) <= _LIMIT)
    file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                      0o600, dir_fd=fd)
    try:
        info = os.fstat(file_fd)
        _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
              and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
        view = memoryview(data)
        while view:
            count = os.write(file_fd, view)
            _need(0 < count <= len(view))
            view = view[count:]
        os.fsync(file_fd)
        _need(_file_id(os.fstat(file_fd)) == _file_id(
            os.stat(name, dir_fd=fd, follow_symlinks=False)))
    finally:
        os.close(file_fd)
    os.fsync(fd)


@dataclass(slots=True)
class _Fixture:
    """Ephemeral genesis witness, not serializable/reopenable host authority.

    Python construction/field tampering is not an adversarial sandbox boundary.
    No API accepts an existing root as fresh. Never route this into production.
    """

    root: Path = field(repr=False)
    intent: phase_b._OperationIntent = field(repr=False)
    root_id: tuple
    namespace_id: tuple
    _fresh: bool = True


def _open_directory(path, *, dir_fd=None):
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                   dir_fd=dir_fd)


@contextmanager
def _locked(fixture):
    _need(type(fixture) is _Fixture and _private_root(fixture.root) == fixture.root_id)
    pending = phase_b._pending_contract(fixture.intent)
    _need(pending is not None)
    with ExitStack() as stack:
        root_fd = _open_directory(fixture.root)
        stack.callback(os.close, root_fd)
        # A directory-inode lock avoids lock-file deletion/recreation. Each call
        # opens its own description, so threads also contend. No wait or retry.
        fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _need(_dir_id(os.fstat(root_fd)) == fixture.root_id
              and _names(root_fd, 1) == (pending.namespace,))
        fd = _open_directory(pending.namespace, dir_fd=root_fd)
        stack.callback(os.close, fd)
        _need(_dir_id(os.fstat(fd)) == fixture.namespace_id)
        yield fd
        _need(_private_root(fixture.root) == _dir_id(os.fstat(root_fd)) == fixture.root_id
              and _names(root_fd, 1) == (pending.namespace,)
              and _dir_id(os.stat(pending.namespace, dir_fd=root_fd, follow_symlinks=False))
              == _dir_id(os.fstat(fd)) == fixture.namespace_id)


_ERRORS = (_Problem, OSError, ValueError, TypeError, RecursionError,
           lane.preflight._DuplicateJSONKey)


def _new_disposable_fixture(scratch_parent, intent):
    """Allocate a NEW fixture only; failures retain every partial artifact.

    scratch_parent is not a receipt lookup/root-routing parameter. Every call
    creates an independent synthetic universe, never a production retry route.
    """
    try:
        manifest = _manifest(intent)
        _need(manifest is not None)
        parent_id = _private_root(scratch_parent)
        root = Path(tempfile.mkdtemp(prefix="r6b-phase-c-", dir=scratch_parent))
        root_id = _private_root(root)
        for raw in (intent.project_root, intent.worktree, intent.git_common_dir):
            path = Path(raw)
            _need(root != path and root not in path.parents and path not in root.parents)
        namespace = root / phase_b._pending_contract(intent).namespace
        namespace.mkdir(mode=0o700)
        fixture = _Fixture(root, intent, root_id, _dir_id(namespace.lstat()))
        with _locked(fixture) as fd:
            _need(_inventory(fd) == {})
            _exclusive(fd, "manifest.json", manifest)
            inventory = _inventory(fd)
            _need(tuple(inventory) == ("manifest.json",)
                  and lane._canonical_json(_read(fd, "manifest.json", inventory["manifest.json"]))
                  == manifest)
        with ExitStack() as stack:
            for path, identity in ((root, root_id), (scratch_parent, parent_id)):
                fd = _open_directory(path)
                stack.callback(os.close, fd)
                _need(_dir_id(os.fstat(fd)) == _private_root(path) == identity)
                os.fsync(fd)
        return fixture, lane._public_result("UNQUALIFIED", "DISPOSABLE_FIXTURE_ONLY")
    except _ERRORS:
        return None, _unknown()


def _expected(fixture, intent):
    _need(intent == fixture.intent and phase_b._valid_intent(intent))
    pending = phase_b._pending_contract(intent)
    reservation = {"schema_version": 1, "state": "RESERVED_NO_REPLAY",
                   "pending_sha256": phase_b._digest(asdict(pending))}
    return pending, {
        "manifest.json": _manifest(intent),
        "reservation.json": lane._canonical_json(reservation),
        "pending.json": lane._canonical_json(asdict(pending)),
    }


def _checked_inventory(fd, expected):
    inventory = _inventory(fd)
    _need(tuple(inventory) == tuple(sorted(expected)))
    for name, data in expected.items():
        _need(lane._canonical_json(_read(fd, name, inventory[name])) == data)
    _need(_inventory(fd) == inventory)
    return inventory


def _reserve_fixture(fixture, intent, evidence) -> dict:
    """Consume a fixture exactly once; fsync pending before reporting its state.

    No callback/effect is accepted. UNKNOWN never repairs, removes or replays.
    The durable reservation tombstone plus ephemeral consumed bit retain failure
    even before pending creation; losing the handle cannot authorize reopening.
    """
    try:
        with _locked(fixture) as fd:
            _pending, expected = _expected(fixture, intent)
            _need(fixture._fresh is True)
            fixture._fresh = False
            _need(_observe_effective_policy(intent, evidence)["stage"]
                  == "SYNTHETIC_EFFECTIVE_POLICY_OBSERVED")
            _checked_inventory(fd, {"manifest.json": expected["manifest.json"]})
            _exclusive(fd, "reservation.json", expected["reservation.json"])
            _exclusive(fd, "pending.json", expected["pending.json"])
            _checked_inventory(fd, expected)
        return lane._public_result("UNQUALIFIED", "FIXTURE_PENDING_NOT_DISPATCH",
                                   stage="FIXTURE_PENDING_DURABLE")
    except _ERRORS:
        return _unknown()


def _observe_fixture(fixture, intent) -> dict:
    """Complete historical inventory only. Even a matching pair forbids replay."""
    try:
        with _locked(fixture) as fd:
            pending, expected = _expected(fixture, intent)
            inventory = _inventory(fd)
            _need(set(inventory) == set(_FILES))
            result = phase_b._ResultReceipt(**_read(fd, "result.json", inventory["result.json"]))
            _checked_inventory(fd, {**expected, "result.json": lane._canonical_json(asdict(result))})
            _need(_inventory(fd) == inventory)
            observed = phase_b._observe_receipts(intent, pending=(pending,), results=(result,))
        # Keep even the native session UUID out of the storage diagnostic.
        return {**observed, "session_id": None}
    except _ERRORS:
        return _unknown()
