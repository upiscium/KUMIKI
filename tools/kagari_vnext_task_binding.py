#!/usr/bin/env python3
"""Resolve a KAGARI Task/OpenCode runtime location from existing #191 facts.

Read-only identity bridge for Main #291. This NEVER grants Issue execution,
starts a model, registers a Worktree, or writes another Task authority store.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__:
    from .kagari_vnext_opencode_control import (
        TaskBinding, OpenCode, ControlError, status, reply)
else:
    from kagari_vnext_opencode_control import (
        TaskBinding, OpenCode, ControlError, status, reply)

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
sys.path.insert(0, str(V4))
from metadata_ref import MetadataStore, MetadataRefError  # noqa: E402
from task_record import TaskRecords, TaskRecordError  # noqa: E402


def git(root: Path, *argv: str) -> str:
    environ = {key: value for key, value in os.environ.items()
               if not key.startswith("GIT_")}
    environ.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_OPTIONAL_LOCKS": "0"})
    try:
        process = subprocess.run(
            ["git", "-C", str(root), "--no-optional-locks", *argv],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12,
            env=environ, check=False)
    except (OSError, subprocess.SubprocessError):
        raise ControlError("GIT_UNAVAILABLE", "Task Git observation unavailable") from None
    if process.returncode != 0 or len(process.stdout) > 128 * 1024:
        raise ControlError("GIT_MISMATCH", "Task Git observation did not verify") from None
    try:
        return process.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeError:
        raise ControlError("GIT_MISMATCH", "Task Git returned invalid text") from None


def exact_dir(path: Path, name: str) -> Path:
    if not path.is_absolute():
        raise ControlError("TASK_BINDING_INVALID", name + " path must be absolute")
    try:
        resolved = path.resolve(strict=True)
        if path != resolved or not path.is_dir() or path.is_symlink():
            raise ControlError("TASK_BINDING_INVALID", name + " path is not canonical")
    except OSError:
        raise ControlError("TASK_BINDING_INVALID", name + " path is unavailable") from None
    return resolved


def worktree_registered(project: Path, worktree: Path, branch_ref: str) -> None:
    if worktree == project or worktree.parent != project / ".worktrees":
        raise ControlError("WORKTREE_MISMATCH",
                           "Task Worktree must be a registered Issue worktree under .worktrees")
    if git(worktree, "rev-parse", "--show-toplevel") != str(worktree):
        raise ControlError("WORKTREE_MISMATCH", "Task is not rooted in the exact Worktree")
    if git(project, "rev-parse", "--show-toplevel") != str(project):
        raise ControlError("WORKTREE_MISMATCH", "Unexpected Project Git root")
    common = git(project, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common != git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"):
        raise ControlError("WORKTREE_MISMATCH", "Task is attached to a foreign Git repository")
    if git(worktree, "symbolic-ref", "--quiet", "HEAD") != branch_ref:
        raise ControlError("WORKTREE_MISMATCH", "Task checked-out branch differs from Record")
    branch_head = git(project, "rev-parse", "--verify", branch_ref)
    if git(worktree, "rev-parse", "HEAD") != branch_head:
        raise ControlError("WORKTREE_MISMATCH", "Registered branch and Task HEAD differ")
    listing = git(project, "worktree", "list", "--porcelain")
    registered = []
    for block in listing.split("\n\n"):
        if not block:
            continue
        entry = dict(item.split(" ", 1) for item in block.splitlines()
                     if " " in item)
        if entry.get("worktree") == str(worktree):
            registered.append(entry)
    if len(registered) != 1 or registered[0].get("branch") != branch_ref:
        raise ControlError("WORKTREE_MISMATCH", "Exact Git Worktree registration is absent")


def verify_task_record(args: argparse.Namespace) -> tuple[Path, str]:
    """Re-use the #191/Worktree authority check before root Session creation.

    This observes an already approved Task. It does not approve Issues or
    modify their Worktrees, so Main remains responsible for eligibility.
    """
    project = exact_dir(args.project_root, "Project")
    worktree = exact_dir(args.worktree, "Task Worktree")
    if type(args.issue) is not int or args.issue < 1:
        raise ControlError("TASK_BINDING_INVALID", "Issue must be positive")
    worktree_registered(project, worktree, args.branch_ref)
    try:
        store = MetadataStore(project, args.repository, args.remote)
        tip = store.fetch_tip()
    except (MetadataRefError, TypeError, ValueError):
        raise ControlError("TASK_RECORD_INVALID", "Cannot verify Task Record/Contract") from None
    if tip is None:
        raise ControlError("TASK_RECORD_ABSENT", "No canonical v4 metadata ref")
    try:
        records = TaskRecords(store, authorize=lambda _request: False)
        found = records.read(
            tip, task=str(args.issue), branch_ref=args.branch_ref,
            base_revision=args.base_revision)
    except (MetadataRefError, TaskRecordError, TypeError, ValueError):
        raise ControlError("TASK_RECORD_INVALID", "Cannot verify Task Record/Contract") from None
    if found is None:
        raise ControlError("TASK_RECORD_ABSENT", "Issue Task Record absent from metadata")
    _record_id, record, _contract = found
    if "disposition" in record["payload"]:
        raise ControlError("TASK_RETIRED", "Task has a Human terminal disposition")
    return worktree, record["payload"]["contract_id"]


def resolve(args: argparse.Namespace) -> TaskBinding:
    worktree, contract_id = verify_task_record(args)
    candidate = TaskBinding.from_mapping({
        "schema_version": 1,
        "repository": args.repository,
        "issue": args.issue,
        "worktree": str(worktree),
        "session_id": args.session_id,
        "server_url": args.server_url,
        "contract_sha256": contract_id,
        "contract_revision": "contract:" + contract_id[:16],
    })
    OpenCode(candidate).verify_session()
    return candidate


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="KAGARI Task-scoped Ask/status CLI")
    p.add_argument("--context", type=Path, help="KAGARI Main runtime locator (not authority)")
    p.add_argument("--pretty", action="store_true", help="Indent JSON for human inspection")
    p.add_argument("--project-root", type=Path)
    p.add_argument("--worktree", type=Path)
    p.add_argument("--repository")
    p.add_argument("--issue", type=int)
    p.add_argument("--base-revision")
    p.add_argument("--branch-ref")
    p.add_argument("--server-url")
    p.add_argument("--session-id")
    p.add_argument("--remote")
    p.add_argument("--expected-contract-id", help="Full contract ID observed with the pending Ask")
    p.add_argument("--request-id")
    p.add_argument("--kind", choices=("permission", "question"))
    p.add_argument("--decision", choices=("once", "reject"))
    p.add_argument("--answers-json")
    p.add_argument("operation", nargs="?", default="binding-resolve",
                   choices=("binding-resolve", "status", "asks", "ask-show", "ask-reply"))
    return p


def _task_locator(args: argparse.Namespace) -> None:
    """Human-friendly connection hints; never substitutes for #191 Task Record."""
    fields = ("project_root", "worktree", "repository", "issue",
              "base_revision", "branch_ref", "server_url", "session_id")
    if args.context is None:
        if any(getattr(args, field) is None for field in fields):
            raise ControlError("LOCATOR_REQUIRED",
                               "Provide --context or all Task location arguments")
        if args.remote is None:
            args.remote = "origin"
        return
    if any(getattr(args, field) is not None for field in (*fields, "remote")):
        raise ControlError("LOCATOR_CONFLICT",
                           "Do not mix --context with individual Task location arguments")
    try:
        data = args.context.read_bytes()
    except OSError:
        raise ControlError("LOCATOR_INVALID", "KAGARI runtime locator unavailable") from None
    if len(data) > 32_768:
        raise ControlError("LOCATOR_INVALID", "KAGARI runtime locator exceeds size limit")
    try:
        obj = json.loads(data)
    except (UnicodeError, ValueError):
        raise ControlError("LOCATOR_INVALID", "KAGARI runtime locator must be JSON") from None
    if (not isinstance(obj, dict) or type(obj.get("schema_version")) is not int
            or obj["schema_version"] != 1 or not set(fields) <= obj.keys()
            or obj.keys() - set(fields) - {"schema_version", "remote"}):
        raise ControlError("LOCATOR_INVALID", "Unsupported KAGARI runtime locator")
    if type(obj["issue"]) is not int or obj["issue"] < 1:
        raise ControlError("LOCATOR_INVALID", "Invalid KAGARI Issue number")
    for field in fields:
        if field == "issue":
            setattr(args, field, obj[field])
            continue
        if not isinstance(obj[field], str) or not obj[field]:
            raise ControlError("LOCATOR_INVALID", "Invalid KAGARI Task location field")
        setattr(args, field, Path(obj[field]) if field in ("project_root", "worktree")
                else obj[field])
    remote = obj.get("remote", "origin")
    if not isinstance(remote, str) or not remote:
        raise ControlError("LOCATOR_INVALID", "Invalid KAGARI Task metadata remote")
    args.remote = remote


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _task_locator(args)
        candidate = resolve(args)
        client = OpenCode(candidate)
        if args.operation == "binding-resolve":
            result = {"status": "ok", "operation": "binding-resolve",
                      "binding": {"schema_version": 1, **candidate.__dict__}}
        elif args.operation == "status":
            result = status(candidate, client)
        elif args.operation == "asks":
            result = {"status": "ok", "operation": "asks",
                      "task": candidate.subject(), "pending": client.pending()}
        elif args.operation == "ask-show":
            if not args.request_id:
                raise ControlError("INVALID_REPLY", "Ask ID is required")
            matches = [a for a in client.pending()
                       if a["request_id"] == args.request_id]
            if len(matches) != 1:
                raise ControlError("ASK_NOT_PENDING", "Exact Task Ask is not pending")
            result = {"status": "ok", "operation": "ask-show", "ask": matches[0]}
        else:
            if not args.request_id or not args.kind:
                raise ControlError("INVALID_REPLY", "Request ID and Ask kind are required")
            if not args.expected_contract_id:
                raise ControlError("REVISION_REQUIRED", "Current Task contract ID is required")
            if candidate.contract_sha256 != args.expected_contract_id:
                raise ControlError("STALE_TASK_CONTRACT",
                                   "Task contract changed since Ask was observed")
            result = reply(candidate, client, args)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False,
                         indent=2 if args.pretty else None))
        return 0
    except ControlError as error:
        print(json.dumps({"status": "error", "error_code": error.code,
                          "message": error.detail},
                         ensure_ascii=False, indent=2 if args.pretty else None))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
