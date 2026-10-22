#!/usr/bin/env python3
"""Preserve an approved KAGARI Task's Product commit as durable external Evidence.

GA qualification candidate only. Does NOT execute a model, approve a Task,
change an attached Project or Work Unit, apply a patch, commit or Push.
Works with a completed, verified RC-managed Work Unit; separately supports
byte-verified readback AFTER that disposable Work Unit has been removed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import time
from typing import Any

PATCH_LIMIT = 16 * 1024 * 1024
SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}\Z")
ARTIFACT = "product-commit.patch"
RECEIPT = "product-artifact.json"


class Blocked(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise Blocked(reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def owned_file(path: Path, *, limit: int = 1024 * 1024) -> bytes:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and info.st_size <= limit and info.st_nlink == 1,
            "untrusted, oversized or aliased Evidence file")
    raw = path.read_bytes()
    require(len(raw) <= limit, "Evidence read exceeds limit")
    return raw


def document(path: Path) -> dict[str, Any]:
    value = json.loads(owned_file(path))
    require(type(value) is dict, "Evidence JSON must be an object")
    return value


def private_dir(path: Path) -> None:
    require(path.is_absolute() and not path.is_symlink()
            and path.resolve(strict=True) == path
            and path.is_dir()
            and path.stat().st_uid == os.geteuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o700,
            "Job folder must be canonical, private and current-user-owned")


def git(root: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0")
    cp = subprocess.run(["git", "--no-optional-locks", "-C", str(root),
                         "-c", "core.fsmonitor=false", *args],
                        capture_output=True, timeout=30, env=env)
    require(cp.returncode == 0, "Git read failed: " + str(args[:2]))
    return cp.stdout.strip()


def bounded_git_patch(root: Path, commit: str) -> bytes:
    """Read a commit patch with a hard stdout budget; no arbitrary temp writes."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0")
    cmd = ["git", "--no-optional-locks", "-C", str(root),
           "-c", "core.fsmonitor=false",
           "-c", "core.hooksPath=/dev/null", "format-patch",
           "--no-ext-diff", "--no-textconv",
           "--stdout", "--binary", "--full-index", "--no-stat", "-1", commit]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env)
    chunks: list[bytes] = []
    size = 0
    deadline = time.monotonic() + 45
    try:
        assert proc.stdout is not None
        while True:
            left = deadline - time.monotonic()
            require(left > 0, "Git Product patch generation timed out")
            readable, _, _ = select.select([proc.stdout], [], [], min(left, 1))
            if not readable:
                continue
            part = os.read(proc.stdout.fileno(), 65536)
            if not part:
                break
            size += len(part)
            require(size <= PATCH_LIMIT, "Product patch exceeds 16 MiB export budget")
            chunks.append(part)
        require(proc.wait(timeout=max(1, deadline - time.monotonic())) == 0,
                "Git format-patch failed")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        if proc.stdout:
            proc.stdout.close()
    patch = b"".join(chunks)
    require(patch.startswith(b"From ") and b"\nSubject: " in patch
            and b"\ndiff --git " in patch, "Product patch is missing Git headers")
    return patch


def save_new(file: Path, payload: bytes) -> None:
    require(not file.exists() and not file.is_symlink(),
            "Existing Product archive bytes cannot be overwritten")
    descriptor = os.open(file, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as sink:
        os.fchmod(sink.fileno(), 0o600)
        sink.write(payload)
        sink.flush()
        os.fsync(sink.fileno())


def verify(jobdir: Path, *, result_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read back archived Product bytes without accessing disposable Work Unit."""
    private_dir(jobdir)
    archive = document(jobdir / RECEIPT)
    result = document(jobdir / "result.json") if result_override is None else result_override
    record = document(jobdir / "job.json")
    evidence = owned_file(jobdir / "evidence" / "report.json")
    product = owned_file(jobdir / ARTIFACT, limit=PATCH_LIMIT)
    require(archive.get("schema_version") == 1
            and archive.get("kind") == "KAGARI_DURABLE_PRODUCT_PATCH_V1"
            and archive.get("qualification") == "ARTIFACT_ONLY_NOT_GA",
            "Unknown archive receipt")
    require(type(archive.get("patch_size")) is int
            and 0 < archive["patch_size"] <= PATCH_LIMIT
            and archive["patch_size"] == len(product)
            and archive.get("patch_sha256") == digest(product),
            "Archived Product bytes changed or are unbounded")
    require(result.get("status") == "GA_MANAGED_WORKUNIT_TASK_PASS"
            and result.get("Product_commit") == archive.get("product_commit")
            and archive.get("job_id") == record.get("job_id") == jobdir.name
            and archive.get("source_head") == record.get("Project_head")
            and archive.get("ga_receipt_sha256") == record.get("ga_receipt_sha256")
            and archive.get("native_evidence_sha256") == digest(evidence),
            "Product archive identity or Evidence linkage does not match")
    require(archive.get("allowed_product_paths") == sorted(record["allowed_paths"]),
            "Archived Product allowlist differs from approved Task")
    return {"status": "DURABLE_PRODUCT_ARTIFACT_VERIFIED",
            "job_id":jobdir.name, "product_commit":archive["product_commit"],
            "patch_sha256":archive["patch_sha256"],
            "patch_size":archive["patch_size"],
            "workunit_required_for_readback":False,
            "source_project_mutated":False}


def archive(jobdir: Path, *, result_override: dict[str, Any] | None = None) -> dict[str, Any]:
    private_dir(jobdir)
    require(jobdir.name.startswith("job-") and jobdir.parent.name == "history",
            "Not a KAGARI owned history Job")
    private_dir(jobdir.parent)
    require(not (jobdir / ARTIFACT).exists()
            and not (jobdir / RECEIPT).exists()
            and not (jobdir / ARTIFACT).is_symlink()
            and not (jobdir / RECEIPT).is_symlink(),
            "Product archive already exists or has unexpected state")
    record = document(jobdir / "job.json")
    result = document(jobdir / "result.json") if result_override is None else result_override
    native_raw = owned_file(jobdir / "evidence" / "report.json")
    native = json.loads(native_raw)
    require(record.get("kind") == "KAGARI_GA_WORKUNIT_JOB"
            and record.get("job_id") == jobdir.name
            and type(record.get("allowed_paths")) is list
            and bool(record["allowed_paths"]),
            "Unsupported job schema or Product scope")
    commit = result.get("Product_commit")
    base = record.get("Project_head")
    require(type(commit) is str and SHA_RE.fullmatch(commit) is not None
            and type(base) is str and SHA_RE.fullmatch(base) is not None,
            "Expected full Product commit/parent hashes")
    require(result.get("status") == "GA_MANAGED_WORKUNIT_TASK_PASS"
            and result.get("Native_exit") == 0
            and result.get("Native_status") == "REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS"
            and result.get("native_v4_Evidence_readback", {}).get("status")
                == "FRESH_PROCESS_NATIVE_READBACK_PASS"
            and result["native_v4_Evidence_readback"].get("product_commit") == commit
            and native.get("product_commit") == commit
            and native.get("status") == "REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS",
            "Task completion and persisted Native Evidence are not qualified")
    unit = Path(record["workunit_clone"])
    require(unit.is_absolute() and unit.resolve(strict=True) == unit
            and unit.is_dir() and not unit.is_symlink()
            and (unit / ".git").is_dir() and not (unit / ".git").is_symlink()
            and (unit / ".git").stat().st_uid == os.geteuid(),
            "Verified ordinary private Work Unit required for archive")
    require(git(unit, "remote") == b"", "Product Work Unit must have no Git remotes")
    require(git(unit, "rev-parse", "HEAD").decode() == commit
            and git(unit, "rev-parse", "HEAD^").decode() == base,
            "Product HEAD/parent differs from accepted Task")
    paths = git(unit, "diff-tree", "--no-commit-id", "--name-only",
                "-r", commit).decode().splitlines()
    allowed = record["allowed_paths"]
    require(all(type(x) is str and x and x != "AGENTS.md" for x in allowed)
            and len(allowed) == len(set(allowed)), "Malformed Product allowlist")
    require(bool(paths) and set(paths).issubset(set(allowed))
            and "AGENTS.md" not in paths, "Committed Product paths outside Contract")
    patch = bounded_git_patch(unit, commit)
    # Only private persistent Job state is written; never the consumer.
    save_new(jobdir / ARTIFACT, patch)
    receipt = {
        "schema_version": 1,
        "kind": "KAGARI_DURABLE_PRODUCT_PATCH_V1",
        "qualification": "ARTIFACT_ONLY_NOT_GA",
        "job_id": jobdir.name,
        "source_head": base,
        "product_commit": commit,
        "allowed_product_paths": sorted(allowed),
        "actual_product_paths": sorted(paths),
        "ga_receipt_sha256": record["ga_receipt_sha256"],
        "native_evidence_sha256": digest(native_raw),
        "patch_sha256": digest(patch),
        "patch_size": len(patch),
        "restore_policy": "HUMAN_CONTROLLED_OFFLINE_GIT_AM_ONLY",
        "source_project_mutated": False,
        "source_remote_push": False,
    }
    save_new(jobdir / RECEIPT, canonical(receipt))
    return verify(jobdir, result_override=result_override)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("archive", "verify"))
    parser.add_argument("--jobdir", required=True, type=Path)
    args = parser.parse_args()
    try:
        answer = archive(args.jobdir) if args.operation == "archive" else verify(args.jobdir)
        print(json.dumps(answer, sort_keys=True))
        return 0
    except (Blocked, ValueError, KeyError, IndexError, OSError,
            subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "BLOCKED", "reason":str(error)[:450],
                          "GA_qualified":False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
