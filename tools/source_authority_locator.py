#!/usr/bin/env python3
"""Reference implementation of a host-installed KUMIKI source-authority locator.

This checked-out file is NOT an authority. The operator must independently
install it into a fixed Nix-store output and substitute HOST_DESCRIPTOR_PATH.
The descriptor cannot be selected by a source Task or an environment variable.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


HOST_DESCRIPTOR_PATH = "@HOST_DESCRIPTOR_PATH@"
REPOSITORY = "upiscium/Templates"
REPOSITORY_ID = 1183254623
SCHEMA_VERSION = 2
DEFAULT_STORE = Path("/nix/store")
POLICY_RELATIVE = Path("lib/templates-source/source_collaboration.py")
LAUNCHER_RELATIVE = Path("bin/templates-source")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
OUTPUT_RE = re.compile(r"[0-9a-z]{32}-templates-source-collaboration\Z")
LOCATOR_OUTPUT_RE = re.compile(r"[0-9a-z]{32}-kumiki-source-authority-locator\Z")
LOCATOR_RELATIVE = Path("bin/kumiki-source")
MAX_LOCATOR = 65536
BASE_RE = re.compile(r'^TRUSTED_SOURCE_BASE = "([0-9a-f]{40})"$',
                     flags=re.MULTILINE)
MAX_DESCRIPTOR = 8192
MAX_POLICY = 4 * 1024 * 1024
MAX_LAUNCHER = 65536


class LocatorError(RuntimeError):
    """Fail-closed selection error; never use mutable source as fallback."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise LocatorError(reason)


def canonical_absolute(path: Path) -> bool:
    value = str(path)
    return (
        path.is_absolute()
        and value == os.path.normpath(value)
        and all(part not in {"", ".", ".."} for part in path.parts[1:])
    )


def check_directories(path: Path, *, owner_uid: int, secure_root: Path) -> None:
    """Refuse link/writable ancestors down to a fixed trusted root.

    In production secure_root is '/'. Test callers may inject a self-owned,
    private temporary trust root; this injection is never exposed in the CLI.
    """
    require(canonical_absolute(path) and canonical_absolute(secure_root),
            "noncanonical authority path")
    require(path == secure_root or path.is_relative_to(secure_root),
            "authority path outside trusted root")
    directories = [secure_root]
    current = secure_root
    for part in path.relative_to(secure_root).parts:
        current = current / part
        directories.append(current)
    for directory in directories:
        try:
            info = os.lstat(directory)
        except OSError as exc:
            raise LocatorError("trusted directory unavailable") from exc
        # Nix commonly uses root:nixbld 1775 for /nix/store. Its sticky
        # bit protects the root-owned, nonwritable output entry, so do not
        # mistake this standard store layout for Task-writable authority.
        writes = info.st_mode & 0o022
        standard_nix_store = (
            directory == DEFAULT_STORE
            and writes == 0o020
            and bool(info.st_mode & stat.S_ISVTX)
        )
        require(stat.S_ISDIR(info.st_mode)
                and info.st_uid == owner_uid
                and (writes == 0 or standard_nix_store),
                "untrusted authority directory owner, type or mode")


def protected_bytes(path: Path, *, max_size: int, owner_uid: int,
                    secure_root: Path) -> bytes:
    require(canonical_absolute(path), "noncanonical authority file")
    check_directories(path.parent, owner_uid=owner_uid, secure_root=secure_root)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise LocatorError("authority file unavailable or symlink") from exc
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode)
                and before.st_uid == owner_uid
                and before.st_mode & 0o022 == 0,
                "untrusted authority file owner, type or mode")
        require(before.st_size <= max_size, "authority file too large")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(max_size + 1)
        require(len(data) <= max_size, "authority file too large")
        after = os.stat(path, follow_symlinks=False)
        require((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
                "authority file changed during verification")
        return data
    except OSError as exc:
        raise LocatorError("authority file verification failed") from exc
    finally:
        os.close(fd)


def strict_json_object(data: bytes) -> dict[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            require(key not in result, "duplicate descriptor key")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs,
                           parse_constant=lambda _: require(False, "invalid JSON constant"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise LocatorError("invalid descriptor JSON") from exc
    require(isinstance(value, dict), "descriptor must be an object")
    return value


def resolve_authority(descriptor: Path, *,
                      store_root: Path = DEFAULT_STORE,
                      secure_root: Path = Path("/"),
                      owner_uid: int = 0,
                      locator_path: Path | None = None) -> tuple[Path, str]:
    """Verify only host-owned static data and an immutable Nix-store output.

    The injected roots/uid exist solely for synthetic, isolated unit tests.
    They are not exposed by the installed command line.
    """
    fields = strict_json_object(
        protected_bytes(descriptor, max_size=MAX_DESCRIPTOR,
                        owner_uid=owner_uid, secure_root=secure_root))
    expected = {
        "schema_version", "repository", "repository_id", "revision",
        "output_path", "launcher_sha256", "policy_sha256",
        "locator_path", "locator_sha256",
    }
    require(set(fields) == expected, "unknown or missing descriptor fields")
    require(type(fields["schema_version"]) is int
            and fields["schema_version"] == SCHEMA_VERSION,
            "unsupported descriptor schema")
    require(fields["repository"] == REPOSITORY
            and type(fields["repository_id"]) is int
            and fields["repository_id"] == REPOSITORY_ID,
            "repository identity mismatch")
    revision = fields["revision"]
    require(isinstance(revision, str)
            and REVISION_RE.fullmatch(revision) is not None
            and revision != "0" * 40, "invalid approved source revision")
    raw_output = fields["output_path"]
    require(type(raw_output) is str
            and raw_output.startswith("/")
            and raw_output == os.path.normpath(raw_output)
            and canonical_absolute(Path(raw_output)),
            "invalid immutable output path")
    output = Path(raw_output)
    require(output.parent == store_root
            and OUTPUT_RE.fullmatch(output.name) is not None,
            "authority output is not the pinned Nix-store package")
    launcher = output / LAUNCHER_RELATIVE
    policy = output / POLICY_RELATIVE
    for key in ("launcher_sha256", "policy_sha256", "locator_sha256"):
        require(type(fields[key]) is str
                and SHA256_RE.fullmatch(fields[key]) is not None,
                "invalid authority content digest")
    approved_locator_raw = fields["locator_path"]
    require(type(approved_locator_raw) is str
            and approved_locator_raw.startswith("/")
            and approved_locator_raw == os.path.normpath(approved_locator_raw),
            "invalid host-approved locator path")
    approved_locator = Path(approved_locator_raw)
    locator_output = approved_locator.parent.parent
    require(locator_output.parent == store_root
            and LOCATOR_OUTPUT_RE.fullmatch(locator_output.name) is not None
            and approved_locator == locator_output / LOCATOR_RELATIVE,
            "host-approved locator must be an exact Nix-store executable")
    running_locator = Path(__file__).absolute() if locator_path is None else locator_path
    require(running_locator == approved_locator,
            "running locator does not match host-approved executable")
    locator_data = protected_bytes(approved_locator, max_size=MAX_LOCATOR,
                                   owner_uid=owner_uid, secure_root=secure_root)
    require(hashlib.sha256(locator_data).hexdigest() == fields["locator_sha256"],
            "host-approved locator digest mismatch")
    require(os.stat(approved_locator, follow_symlinks=False).st_mode & 0o111 != 0,
            "host-approved locator is not executable")
    launcher_data = protected_bytes(launcher, max_size=MAX_LAUNCHER,
                                    owner_uid=owner_uid, secure_root=secure_root)
    policy_data = protected_bytes(policy, max_size=MAX_POLICY,
                                  owner_uid=owner_uid, secure_root=secure_root)
    require(hashlib.sha256(launcher_data).hexdigest() == fields["launcher_sha256"]
            and hashlib.sha256(policy_data).hexdigest() == fields["policy_sha256"],
            "pinned source executor digest mismatch")
    try:
        source = policy_data.decode("utf-8")
    except UnicodeError as exc:
        raise LocatorError("invalid pinned source policy encoding") from exc
    bases = BASE_RE.findall(source)
    require(bases == [revision], "pinned source revision does not match policy")
    require(str(policy).encode() in launcher_data,
            "pinned launcher does not reference verified policy")
    require(launcher_data.startswith(b"#!/"),
            "pinned launcher is not an executable script")
    try:
        mode = os.stat(launcher, follow_symlinks=False).st_mode
    except OSError as exc:
        raise LocatorError("pinned launcher unavailable") from exc
    require(mode & 0o111 != 0, "pinned launcher not executable")
    return launcher, revision


def validate_args(argv: list[str]) -> None:
    require(len(argv) >= 4 and argv[0] == "--worktree"
            and Path(argv[1]).is_absolute()
            and argv[2] in {"publication-check", "commit", "push",
                            "pr-create", "checkpoint"},
            "expected --worktree <absolute-path> <guarded-operation> ...")


def _installed_descriptor() -> Path:
    # Checked-out source / PATH lookups are never a trust selector.
    entrypoint = Path(__file__).absolute()
    require(entrypoint.is_relative_to(DEFAULT_STORE)
            and not HOST_DESCRIPTOR_PATH.startswith("@"),
            "source-tree locator is not installed authority")
    descriptor = Path(HOST_DESCRIPTOR_PATH)
    require(canonical_absolute(descriptor),
            "installed host descriptor path is not fixed and absolute")
    return descriptor


def main(argv: list[str]) -> int:
    if argv in (["--help"], ["-h"]):
        print("Host-installed KUMIKI authority locator\n"
              "  --check\n"
              "  --worktree <absolute-path> <publication-check|commit|push|pr-create|checkpoint> ...")
        return 0
    try:
        check_only = argv == ["--check"]
        if not check_only:
            validate_args(argv)
        descriptor = _installed_descriptor()
        launcher, revision = resolve_authority(descriptor)
        if check_only:
            print(json.dumps({"status": "READY", "revision": revision,
                              "launcher": str(launcher)}, sort_keys=True))
            return 0
        try:
            os.execv(str(launcher), [str(launcher), *argv])
        except OSError as exc:
            raise LocatorError("pinned source executor could not start") from exc
        raise LocatorError("source executor did not start")
    except LocatorError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)},
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
