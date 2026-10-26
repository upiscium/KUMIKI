#!/usr/bin/env python3
"""KAGARI #163: one Issue-bound OpenCode root Session, native Ask capable.

This is the minimal, opt-in Task runtime handoff for KAGARI Main #291. The
existing #191 Task Record and Git worktree registry determine the eligible
Task; this host-only helper starts or reattaches to an *already approved* Task.
The locator written here is a connection hint consumed by #292's CLI, not
Task authority, an Issue scheduler, or durable product progress.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import URLError, HTTPError

if __package__:
    from .kagari_vnext_task_binding import (
        verify_task_record, resolve, ControlError)
    from .kagari_vnext_opencode_control import TaskBinding, OpenCode
else:
    from kagari_vnext_task_binding import (
        verify_task_record, resolve, ControlError)
    from kagari_vnext_opencode_control import TaskBinding, OpenCode

SUPPORTED_OPENCODE = "1.18.35"
# Only keep Popen handles alive in a long-running caller; this is not a
# runtime authority or a second Session registry.
_DETACHED_CHILDREN: dict[int, subprocess.Popen] = {}
SESSION_PATTERN = re.compile(r"ses_[A-Za-z0-9_-]{8,120}\Z")


def _save(path: Path, payload: dict[str, object], *, exclusive: bool = False) -> None:
    data = (json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n").encode()
    flags = os.O_CREAT | os.O_WRONLY | (os.O_EXCL if exclusive else os.O_TRUNC)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _read_json(path: Path) -> dict[str, object]:
    try:
        if path.is_symlink() or path.stat().st_size > 32_768:
            raise ValueError("invalid runtime record")
        obj = json.loads(path.read_text(encoding="utf-8"))
        if type(obj) is not dict:
            raise ValueError("invalid runtime record")
        return obj
    except (OSError, UnicodeError, ValueError):
        raise ControlError("SESSION_RECONCILIATION_REQUIRED",
                           "Invalid runtime locator; preserve and reconcile it") from None


def _existing(args: argparse.Namespace, path: Path, contract_id: str) -> dict:
    obj = _read_json(path)
    expected = {
        "schema_version": 1, "project_root": str(args.project_root),
        "worktree": str(args.worktree), "repository": args.repository,
        "issue": args.issue, "base_revision": args.base_revision,
        "branch_ref": args.branch_ref, "remote": args.remote,
        "server_url": "http://127.0.0.1:" + str(args.port),
    }
    if any(obj.get(key) != value for key, value in expected.items()):
        raise ControlError("SESSION_RECONCILIATION_REQUIRED",
                           "Runtime locator belongs to a different Issue or endpoint")
    binding = resolve(argparse.Namespace(**{
        "project_root": args.project_root, "worktree": args.worktree,
        "repository": args.repository, "issue": args.issue,
        "base_revision": args.base_revision, "branch_ref": args.branch_ref,
        "remote": args.remote, "server_url": obj["server_url"],
        "session_id": obj.get("session_id"),
    }))
    if binding.contract_sha256 != contract_id:
        raise ControlError("SESSION_RECONCILIATION_REQUIRED",
                           "Task Contract changed; no implicit Session rebind")
    return {
        "status": "ok", "operation": "session-start", "outcome": "RESUMED",
        "context": str(path), "task": binding.subject(),
        "server_url": binding.server_url,
    }


def _request(url: str, *, payload: object = None) -> object:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if password:
        import base64
        name = os.environ.get("OPENCODE_SERVER_USERNAME") or "opencode"
        headers["Authorization"] = "Basic " + base64.b64encode(
            (name + ":" + password).encode()).decode()
    req = Request(url, method="POST" if payload is not None else "GET",
                  data=json.dumps(payload).encode() if payload is not None else None,
                  headers=headers)
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=5) as response:
            raw = response.read(512_001)
            if len(raw) > 512_000:
                raise ControlError("OPENCODE_INVALID", "Unexpected OpenCode response")
            return json.loads(raw) if raw else None
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError):
        raise ControlError("OPENCODE_UNAVAILABLE",
                           "OpenCode Session API request failed") from None


def _opencode_binary(executable: str) -> str:
    binary = shutil.which(executable)
    if not binary:
        raise ControlError("OPENCODE_UNAVAILABLE", "OpenCode executable unavailable")
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, check=False, timeout=8)
    except (OSError, subprocess.SubprocessError):
        raise ControlError("OPENCODE_UNAVAILABLE", "Cannot verify OpenCode version") from None
    if proc.returncode or proc.stdout.strip() != SUPPORTED_OPENCODE:
        raise ControlError("OPENCODE_UNSUPPORTED", "Only verified OpenCode 1.18.35 is supported")
    return binary


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def start(args: argparse.Namespace) -> dict:
    # No process or runtime-file effects before the registered Task and frozen
    # Contract are observed. KAGARI Main remains responsible for *which* IDs
    # a Human authorized to run, and #286 for creating the Worktree.
    worktree, contract_id = verify_task_record(args)
    if contract_id != args.expected_contract_id:
        raise ControlError("STALE_TASK_CONTRACT",
                           "Current immutable Issue Contract is not the requested one")
    if not isinstance(args.port, int) or not 1024 <= args.port <= 65535:
        raise ControlError("SESSION_INVALID", "Specify a loopback port between 1024 and 65535")
    root = args.runtime_dir
    if not root.is_absolute() or root.is_symlink():
        raise ControlError("SESSION_INVALID", "Runtime directory must be absolute and not a symlink")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.resolve(strict=True) != root:
        raise ControlError("SESSION_INVALID", "Runtime directory is not canonical")
    context = root / "context.json"
    pending = root / "launch.pending.json"
    lock = root / ".lock"
    with open(lock, "a+b") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            # Verify again inside the lock in case a Task was reauthorized
            # during preflight. Same root + Task may be retried idempotently.
            _, current = verify_task_record(args)
            if current != contract_id:
                raise ControlError("STALE_TASK_CONTRACT", "Issue Contract changed before launch")
            if context.exists():
                return _existing(args, context, current)
            if pending.exists():
                raise ControlError("SESSION_RECONCILIATION_REQUIRED",
                                   "Earlier start is uncertain; do not launch a duplicate")
            if not _port_available(args.port):
                raise ControlError("OPENCODE_PORT_OCCUPIED",
                                   "Chosen loopback port is already in use")
            binary = _opencode_binary(args.opencode_bin)
            url = "http://127.0.0.1:" + str(args.port)
            # This is operational crash-window evidence, never a permission
            # database. Unknown/partial launch must be reconciled explicitly.
            _save(pending, {"schema_version": 1, "repository": args.repository,
                            "issue": args.issue, "worktree": str(worktree),
                            "contract_id": current, "server_url": url}, exclusive=True)
            log = open(root / "opencode-serve.log", "ab", buffering=0)
            try:
                proc = subprocess.Popen(
                    [binary, "serve", "--pure", "--hostname", "127.0.0.1",
                     "--port", str(args.port)],
                    cwd=worktree, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError:
                raise ControlError("OPENCODE_UNAVAILABLE", "Cannot start OpenCode") from None
            finally:
                log.close()
            for pid, prior in list(_DETACHED_CHILDREN.items()):
                if prior.poll() is not None:
                    _DETACHED_CHILDREN.pop(pid, None)
            _DETACHED_CHILDREN[proc.pid] = proc
            _save(root / "process.json", {"pid": proc.pid, "port": args.port,
                                          "worktree": str(worktree)})
            deadline = time.monotonic() + 15
            healthy = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise ControlError("OPENCODE_START_FAILED",
                                       "OpenCode process exited during startup")
                try:
                    health = _request(url + "/global/health")
                    if (isinstance(health, dict) and health.get("healthy") is True
                            and health.get("version") == SUPPORTED_OPENCODE):
                        healthy = True
                        break
                except ControlError:
                    pass
                time.sleep(0.15)
            if not healthy:
                raise ControlError("OPENCODE_START_FAILED",
                                   "OpenCode did not become healthy; reconcile process")
            session = _request(url + "/session?" + urlencode(
                {"directory": str(worktree)}), payload={
                    "title": f"KAGARI Task #{args.issue} (Issue-root)",
                    "permission": [
                        {"permission": "bash", "pattern": "*", "action": "ask"},
                        {"permission": "edit", "pattern": "*", "action": "ask"},
                    ],
                })
            if (not isinstance(session, dict)
                    or not isinstance(session.get("id"), str)
                    or not SESSION_PATTERN.fullmatch(session["id"])
                    or session.get("directory") != str(worktree)
                    or session.get("parentID") is not None):
                raise ControlError("SESSION_MISMATCH",
                                   "OpenCode did not create exact Issue-root Session")
            data = {
                "schema_version": 1, "project_root": str(args.project_root),
                "worktree": str(worktree), "repository": args.repository,
                "issue": args.issue, "base_revision": args.base_revision,
                "branch_ref": args.branch_ref, "remote": args.remote,
                "server_url": url, "session_id": session["id"],
            }
            # Consume the exact runtime locator through the already merged
            # #292 public CLI validation before advertising success.
            binding = TaskBinding.from_mapping({
                "schema_version": 1, "repository": args.repository,
                "issue": args.issue, "worktree": str(worktree),
                "server_url": url, "session_id": session["id"],
                "contract_sha256": current, "contract_revision": "contract:" + current[:16],
            })
            OpenCode(binding).verify_session()
            _save(context, data, exclusive=True)
            pending.unlink()
            return {
                "status": "ok", "operation": "session-start", "outcome": "CREATED",
                "context": str(context), "task": binding.subject(),
                "server_url": url,
            }
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="KAGARI #163 Issue-root OpenCode Session handoff")
    p.add_argument("--project-root", type=Path, required=True)
    p.add_argument("--worktree", type=Path, required=True)
    p.add_argument("--repository", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--base-revision", required=True)
    p.add_argument("--branch-ref", required=True)
    p.add_argument("--expected-contract-id", required=True)
    p.add_argument("--runtime-dir", type=Path, required=True)
    p.add_argument("--remote", default="origin")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--opencode-bin", default="opencode")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = start(args)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except ControlError as error:
        print(json.dumps({"status": "error", "error_code": error.code,
                          "message": error.detail}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
