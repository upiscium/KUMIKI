#!/usr/bin/env python3
"""Unreleased native v4 MODULE bundle builder; not a KAGARI runtime launcher."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import time
from pathlib import Path

SOURCE = "components/agent-core-v4"
PREFIX = "lib/kagari_v4"
MODULES = frozenset(
    (
        "cleanup_resources.py",
        "collaboration_git.py",
        "evidence.py",
        "integration_git.py",
        "metadata_codec.py",
        "metadata_ref.py",
        "operation_prerequisites.py",
        "task_cleanup.py",
        "task_collaboration.py",
        "task_integration.py",
        "task_record.py",
        "task_view.py",
    )
)
REVISION = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.ASCII)
DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", re.ASCII)
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024


class BundleError(ValueError):
    pass


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise BundleError(reason)


def serialize(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _real_directory(path: Path) -> Path:
    need(path.is_absolute(), "path must be absolute")
    need(path.is_dir() and not path.is_symlink(), "directory unavailable")
    need(path.resolve(strict=True) == path, "directory path must be canonical")
    return path


def read_regular(
    path: Path, max_bytes: int, *, expected_size: int | None = None
) -> bytes:
    """No-follow bounded file read; rejects FIFOs and file replacement races."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as reader:
            info = os.fstat(reader.fileno())
            need(
                stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o444,
                "unsafe file type or mode",
            )
            need(info.st_size <= max_bytes, "file exceeds declared maximum")
            if expected_size is not None:
                need(info.st_size == expected_size, "file length mismatch")
            data = reader.read(max_bytes + 1)
            need(len(data) == info.st_size, "file changed size during read")
            after = path.lstat()
            need(
                (after.st_dev, after.st_ino) == (info.st_dev, info.st_ino),
                "file changed during inspection",
            )
            return data
    except OSError as exc:
        raise BundleError("file unavailable or unsafe") from exc


def _git(repo: Path, *arguments: str, limit: int = MAX_TOTAL) -> bytes:
    """Bound Git stdout while streaming; never honor Git replacement objects."""
    need(0 <= limit <= MAX_TOTAL, "invalid Git output byte budget")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_PAGER": "cat",
        }
    )
    try:
        proc = subprocess.Popen(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                *arguments,
            ],
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise BundleError("Git object inspection unavailable") from exc

    deadline = time.monotonic() + 20
    chunks: list[bytes] = []
    total = 0
    try:
        need(proc.stdout is not None, "Git stdout is unavailable")
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                need(remaining > 0, "Git object inspection timed out")
                need(selector.select(remaining), "Git object inspection timed out")
                data = os.read(proc.stdout.fileno(), min(65536, limit - total + 1))
                if not data:
                    break
                total += len(data)
                need(total <= limit, "Git output exceeds byte budget")
                chunks.append(data)
        proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        need(proc.returncode == 0, "Git object inspection failed")
        return b"".join(chunks)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BundleError("Git object inspection unavailable or timed out") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()


def source_repository(repo: Path) -> str:
    """Observe one local GitHub origin as an unverified source hint."""
    raw = _git(
        repo,
        "config",
        "--local",
        "--no-includes",
        "--get-all",
        "remote.origin.url",
        limit=1024,
    )
    try:
        values = [v for v in raw.decode("utf-8", "strict").splitlines() if v]
    except UnicodeError as exc:
        raise BundleError("source origin is not valid UTF-8") from exc
    need(len(values) == 1, "source origin must be exactly one canonical URL")
    origin = values[0]
    if origin.startswith("https://github.com/"):
        identity = origin.removeprefix("https://github.com/")
    elif origin.startswith("git@github.com:"):
        identity = origin.removeprefix("git@github.com:")
    else:
        raise BundleError("source Git origin must be canonical GitHub HTTPS/SSH")
    identity = identity.removesuffix(".git")
    need(
        REPOSITORY.fullmatch(identity) is not None,
        "invalid canonical GitHub source repository identity",
    )
    need(
        identity == "upiscium/Templates",
        "N1 source origin must use exact upiscium/Templates spelling",
    )
    return identity


def exact_modules(repo: Path, revision: str) -> dict[str, bytes]:
    repo = _real_directory(repo)
    need(
        isinstance(revision, str) and REVISION.fullmatch(revision) is not None,
        "expected full lowercase source commit",
    )
    resolved = _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}", limit=128)
    need(
        resolved.strip().decode("ascii") == revision,
        "requested revision is not an exact Git commit",
    )
    tree = _git(
        repo, "ls-tree", "-r", "-z", "--full-tree", revision, "--", SOURCE, limit=8192
    )
    result: dict[str, bytes] = {}
    total = 0
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        try:
            header, filename = entry.split(b"\t", 1)
            mode, kind, object_id = header.decode("ascii").split(" ")
            path = filename.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise BundleError("invalid Git tree entry") from exc
        need(path.startswith(SOURCE + "/"), "unexpected source path")
        name = path.removeprefix(SOURCE + "/")
        need(
            mode == "100644" and kind == "blob" and name in MODULES,
            "unexpected native v4 module or Git mode",
        )
        need(name not in result, "duplicate Git module")
        size_output = _git(repo, "cat-file", "-s", object_id, limit=64).strip()
        need(
            re.fullmatch(rb"(0|[1-9][0-9]*)", size_output) is not None,
            "invalid Git blob size",
        )
        size = int(size_output)
        need(
            size <= MAX_FILE and total + size <= MAX_TOTAL,
            "source code exceeded byte budget before materialization",
        )
        data = _git(repo, "cat-file", "blob", object_id, limit=size)
        need(len(data) == size, "Git blob size changed during inspection")
        total += size
        result[name] = data
    need(set(result) == MODULES, "native v4 module inventory incomplete")
    return dict(sorted(result.items()))


def manifest(revision: str, repository: str, modules: dict[str, bytes]) -> dict:
    return {
        "schema_version": 1,
        "kind": "kagari-v4-native-module-bundle",
        "qualification": "SOURCE_ONLY_UNQUALIFIED",
        "target_version": "1.0.0",
        "source_revision": revision,
        "source_repository_hint": repository,
        "files": [
            {
                "path": f"{PREFIX}/{name}",
                "mode": 0o444,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in sorted(modules.items())
        ],
    }


def reject_git_project_output(parent: Path) -> None:
    """Never create source-only scratch output inside any Git Project."""
    for ancestor in (parent, *parent.parents):
        marker = ancestor / ".git"
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BundleError("cannot establish destination Git ownership") from exc
        raise BundleError("bundle destination lies inside another Git Project")


def build(repo: Path, revision: str, destination: Path) -> dict:
    """Create a new external source-only bundle. Never replace existing data."""
    repo = _real_directory(repo)
    need(destination.is_absolute(), "output path must be absolute")
    _real_directory(destination.parent)
    reject_git_project_output(destination.parent)
    need(
        repo != destination
        and repo not in destination.parents
        and destination not in repo.parents,
        "bundle destination overlaps source checkout",
    )
    need(
        not destination.exists() and not destination.is_symlink(),
        "bundle destination already exists",
    )
    repository = source_repository(repo)
    modules = exact_modules(repo, revision)
    encoded = serialize(manifest(revision, repository, modules))
    need(len(encoded) < 64 * 1024, "manifest too large")
    try:
        destination.mkdir(mode=0o700)
        path = destination / PREFIX
        path.mkdir(parents=True, mode=0o700)
        for name, data in modules.items():
            file_path = path / name
            with file_path.open("xb") as writer:
                writer.write(data)
                writer.flush()
                os.fsync(writer.fileno())
            file_path.chmod(0o444)
        with (destination / "manifest.json").open("xb") as writer:
            writer.write(encoded)
            writer.flush()
            os.fsync(writer.fileno())
        (destination / "manifest.json").chmod(0o444)
    except OSError as exc:
        raise BundleError(
            "build interrupted; partial output retained, not adopted"
        ) from exc
    return {
        "status": "BUILT_SOURCE_ONLY_UNQUALIFIED",
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "source_revision": revision,
        "source_repository_hint": repository,
        "module_count": len(modules),
        "approved": False,
        "can_install": False,
        "can_execute": False,
    }


def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        need(key not in result, "duplicate manifest JSON key")
        result[key] = value
    return result


def inspect(
    bundle: Path,
    expected_digest: str,
    expected_revision: str,
    *,
    repo: Path | None = None,
) -> dict:
    """Validate manifest/file inventory, never load or execute packaged code."""
    bundle = _real_directory(bundle)
    need(
        isinstance(expected_digest, str)
        and DIGEST.fullmatch(expected_digest) is not None,
        "expected digest must be lowercase SHA256",
    )
    need(
        isinstance(expected_revision, str)
        and REVISION.fullmatch(expected_revision) is not None,
        "expected source revision must be a full commit",
    )
    file = bundle / "manifest.json"
    content = read_regular(file, 64 * 1024)
    for directory in (bundle / "lib", bundle / PREFIX):
        try:
            info = directory.lstat()
        except OSError as exc:
            raise BundleError("native bundle directory missing") from exc
        need(stat.S_ISDIR(info.st_mode), "unsafe native module parent directory")
    need(
        hashlib.sha256(content).hexdigest() == expected_digest,
        "manifest digest mismatch",
    )
    try:
        value = json.loads(content, object_pairs_hook=no_duplicate_keys)
    except BundleError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BundleError("invalid JSON manifest") from exc
    need(content == serialize(value), "noncanonical manifest")
    need(
        type(value) is dict
        and set(value)
        == {
            "schema_version",
            "kind",
            "qualification",
            "target_version",
            "source_revision",
            "source_repository_hint",
            "files",
        },
        "unknown or missing manifest keys",
    )
    need(
        type(value["schema_version"]) is int and value["schema_version"] == 1,
        "wrong schema",
    )
    need(
        value["kind"] == "kagari-v4-native-module-bundle"
        and value["qualification"] == "SOURCE_ONLY_UNQUALIFIED"
        and value["target_version"] == "1.0.0"
        and value["source_revision"] == expected_revision
        and isinstance(value["source_repository_hint"], str)
        and REPOSITORY.fullmatch(value["source_repository_hint"]) is not None,
        "invalid bundle identity",
    )
    files = value["files"]
    need(type(files) is list and len(files) == len(MODULES), "wrong module count")
    expected_paths = {f"{PREFIX}/{name}" for name in MODULES}
    discovered: dict[str, bytes] = {}
    total = 0
    for item in files:
        need(
            type(item) is dict and set(item) == {"path", "mode", "size", "sha256"},
            "invalid module manifest entry",
        )
        relative = item["path"]
        need(
            type(relative) is str
            and relative in expected_paths
            and relative not in discovered,
            "unexpected/duplicate module path",
        )
        need(
            type(item["mode"]) is int
            and item["mode"] == 0o444
            and type(item["size"]) is int
            and 0 <= item["size"] <= MAX_FILE
            and type(item["sha256"]) is str
            and DIGEST.fullmatch(item["sha256"]) is not None,
            "invalid module metadata",
        )
        path = bundle / relative
        data = read_regular(path, MAX_FILE, expected_size=item["size"])
        total += len(data)
        need(
            total <= MAX_TOTAL
            and len(data) == item["size"]
            and hashlib.sha256(data).hexdigest() == item["sha256"],
            "module digest or length mismatch",
        )
        discovered[relative] = data
    need(set(discovered) == expected_paths, "missing expected native module")
    need(
        [entry["path"] for entry in files] == sorted(expected_paths),
        "manifest entries not in canonical order",
    )

    actual_dirs: set[str] = set()
    actual_files: set[str] = set()

    def on_walk_error(exc: OSError) -> None:
        raise BundleError("cannot fully enumerate bundle") from exc

    for current, directories, names in os.walk(
        bundle, followlinks=False, onerror=on_walk_error
    ):
        for name in directories:
            child = Path(current) / name
            need(
                child.is_dir() and not child.is_symlink(),
                "symlink or unusual bundle directory",
            )
            actual_dirs.add(child.relative_to(bundle).as_posix())
        for name in names:
            child = Path(current) / name
            need(
                child.is_file() and not child.is_symlink(),
                "symlink or special bundle file",
            )
            actual_files.add(child.relative_to(bundle).as_posix())
    need(
        actual_dirs == {"lib", PREFIX}
        and actual_files == expected_paths | {"manifest.json"},
        "undeclared file or directory",
    )

    source_matched = False
    if repo is not None:
        need(
            source_repository(repo) == value["source_repository_hint"],
            "bundle Git origin differs from recorded source hint",
        )
        objects = exact_modules(repo, expected_revision)
        need(
            all(
                discovered[f"{PREFIX}/{name}"] == data for name, data in objects.items()
            ),
            "bundle differs from exact Git commit",
        )
        source_matched = True
    return {
        "status": "BYTE_VALIDATED_SOURCE_ONLY_UNQUALIFIED",
        "manifest_sha256": expected_digest,
        "source_revision": expected_revision,
        "module_count": len(MODULES),
        "git_source_matched": source_matched,
        "approved": False,
        "can_install": False,
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("build")
    make.add_argument("--repo", required=True, type=Path)
    make.add_argument("--revision", required=True)
    make.add_argument("--output", required=True, type=Path)
    check = sub.add_parser("check")
    check.add_argument("--bundle", required=True, type=Path)
    check.add_argument("--digest", required=True)
    check.add_argument("--revision", required=True)
    check.add_argument("--repo", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build(args.repo, args.revision, args.output)
        else:
            result = inspect(args.bundle, args.digest, args.revision, repo=args.repo)
        print(json.dumps(result, sort_keys=True))
        return 0  # CLI success does NOT grant release/execute authority.
    except (BundleError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": str(exc),
                    "approved": False,
                    "can_execute": False,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
