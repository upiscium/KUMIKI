#!/usr/bin/env python3
"""KAGARI 0.1.0 test-only Human Ask request, explicit reply, grant and revoke.

Two independent Ask kinds:
* AGENTS_SCOPED_EXCEPTION: narrowly append an exact, approved KAGARI-session
  addendum to AGENTS.md, retaining original bytes for exact undo.
* NATIVE_TASK_PERMISSION: consent to two KAGARI-only native subagent roles;
  no Project opencode.json rewrite or blanket task:allow.

This is a LOCAL INTERACTIVE TEST-ONLY approval boundary, not cryptographic
proof of the human's identity, a remote ChatGPT Ask, or production activation.
A model cannot turn a plan or CLI --yes argument into consent; the packaged
reply operation requires a foreground TTY and an exact typed request digest.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import kagari_v010_agents_compat as compat

VERSION = 1
KIND_AGENTS = "AGENTS_SCOPED_EXCEPTION"
KIND_PERMISSION = "NATIVE_TASK_PERMISSION"
LEAVES = ("kagari-v010-leaf-a", "kagari-v010-leaf-b")
MAX_RECORD = 1024 * 1024
FILE_MODE = 0o600
DIR_MODE = 0o700
MANAGED = frozenset({
    "request.json", "agents-preimage.bin", "decision.json",
    "activation.json", "revocation.json"
})


class AskBlocked(ValueError):
    """Unapproved, changed, inapplicable or unsafe Human Ask action."""


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise AskBlocked(reason)


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def verify_project(root: Path) -> Path:
    need(root.is_absolute() and root.resolve() == root
         and root.is_dir() and not root.is_symlink()
         and root.stat().st_uid == os.geteuid(),
         "canonical, ordinary euid-owned Project root required")
    dotgit = root / ".git"
    need(dotgit.is_dir() and not dotgit.is_symlink(), "real Git admin directory required")
    return root


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0")
    return env


def git_head(root: Path) -> str:
    env = _git_env()
    try:
        observed = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root),
             "-c", "core.fsmonitor=false", "rev-parse", "HEAD"],
            capture_output=True, check=True, timeout=10, env=env,
        ).stdout.decode().strip()
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise AskBlocked("Project HEAD is unavailable") from exc
    need(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", observed) is not None,
         "noncanonical Project HEAD")
    return observed


def head_in_approved_history(root: Path, approved_head: str) -> bool:
    """Allow ordinary subsequent Product commits; refuse unrelated histories."""
    current = git_head(root)
    if current == approved_head:
        return True
    try:
        checked = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root),
             "-c", "core.fsmonitor=false",
             "merge-base", "--is-ancestor", approved_head, current],
            env=_git_env(), capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AskBlocked("cannot verify continued Git ancestry") from exc
    need(checked.returncode in (0, 1), "Git history check failed")
    return checked.returncode == 0


def regular(path: Path, *, required: bool = True, max_size: int = MAX_RECORD,
            exact_mode: int | None = None,
            allow_root_owned: bool = False) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not required:
            return None
        raise AskBlocked("required file missing: " + path.name) from None
    permitted_owners = (os.geteuid(), 0) if allow_root_owned else (os.geteuid(),)
    need(stat.S_ISREG(info.st_mode) and info.st_nlink >= 1
         and info.st_uid in permitted_owners and info.st_size <= max_size,
         "unsafe file type/ownership/size: " + path.name)
    if not allow_root_owned:
        need(info.st_nlink == 1, "alias-linked Project/Ask input unsupported")
    if exact_mode is not None:
        need(stat.S_IMODE(info.st_mode) == exact_mode,
             "unexpected file mode: " + path.name)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AskBlocked("file unreadable: " + path.name) from exc
    need(len(data) <= max_size, "file read exceeds size bound: " + path.name)
    return data


def private_root(path: Path, *, create: bool = False) -> Path:
    need(path.is_absolute() and path.resolve() == path,
         "Ask state must have a canonical absolute location")
    if create:
        need(not path.exists() and not path.is_symlink(),
             "Ask state destination must be absent")
        parent = path.parent
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == DIR_MODE
             and not parent.is_symlink(),
             "Ask state parent must be private euid-owned 0700")
        path.mkdir(mode=DIR_MODE)
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
         and stat.S_IMODE(info.st_mode) == DIR_MODE
         and not path.is_symlink(), "unsafe Ask state directory")
    need({p.name for p in path.iterdir()} <= MANAGED,
         "unknown files present in Human Ask state")
    return path


def save_new(folder: Path, name: str, raw: bytes) -> None:
    need(name in MANAGED and len(raw) <= MAX_RECORD, "unsafe Ask record name/size")
    fd = os.open(folder / name,
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
    with os.fdopen(fd, "wb") as writer:
        os.fchmod(writer.fileno(), FILE_MODE)
        writer.write(raw)
        writer.flush()
        os.fsync(writer.fileno())


def load_json(folder: Path, name: str, *, required: bool = True) -> dict | None:
    data = regular(folder / name, required=required, exact_mode=FILE_MODE)
    if data is None:
        return None

    def no_duplicate(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, val in pairs:
            need(key not in result, "duplicate key in " + name)
            result[key] = val
        return result

    try:
        decoded = json.loads(data, object_pairs_hook=no_duplicate,
                             parse_constant=lambda _: need(False, "nonfinite Ask JSON"))
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, AskBlocked):
            raise
        raise AskBlocked("invalid Ask JSON: " + name) from exc
    need(type(decoded) is dict and canonical(decoded) == data,
         "Ask record is not a canonical JSON object: " + name)
    return decoded


def runtime_identity(source: Path) -> str:
    need(source.is_absolute() and source.resolve() == source
         and not source.is_symlink(), "runtime source must be canonical")
    data = regular(source, required=True, max_size=1024 * 1024,
                   allow_root_owned=True)
    assert data is not None
    return digest(data)


def agent_patch(before: bytes) -> bytes:
    source = compat.decode_text(before, "AGENTS.md")
    need(compat.BEGIN not in source and compat.END not in source,
         "existing KAGARI addendum requires manual review")
    suffix = "\n\n" if source and not source.endswith("\n") else "\n"
    return (source + suffix + compat.ADDENDUM).encode("utf-8")


def permission_policy(root: Path) -> tuple[str, str | None]:
    opencode = regular(root / "opencode.json", required=False, max_size=MAX_RECORD)
    if opencode is None:
        return "UNSPECIFIED", None
    try:
        obj = json.loads(opencode)
    except (UnicodeError, ValueError) as exc:
        raise AskBlocked("Project opencode.json is invalid") from exc
    need(type(obj) is dict and type(obj.get("permission", {})) is dict,
         "unsupported Project OpenCode permission structure")
    permission = obj.get("permission", {}).get("task")
    known_deny = permission == "deny" or (
        type(permission) is dict and permission.get("*") == "deny")
    state = "PROJECT_TASK_DENY" if known_deny else "PROJECT_TASK_POLICY_OTHER"
    return state, digest(opencode)


def make_request(project: Path, folder: Path, runtime_source: Path,
                 kind: str) -> dict:
    project = verify_project(project)
    need(kind in (KIND_AGENTS, KIND_PERMISSION), "unsupported Human Ask kind")
    need(not folder.is_relative_to(project) and not project.is_relative_to(folder),
         "Ask state and Project must be disjoint")
    diagnostic = compat.inspect(project)
    agents = regular(project / "AGENTS.md", required=False,
                     max_size=compat.MAX_INSTRUCTIONS)
    expected_config = digest(regular(project / ".git/config"))
    permission, cfg_sha = permission_policy(project)
    fingerprint = runtime_identity(runtime_source)
    base = {
        "schema_version": VERSION,
        "kind": kind,
        "profile": "LOCAL_INTERACTIVE_TEST_ONLY_UNQUALIFIED",
        "project_root": str(project),
        "project_dev": project.stat().st_dev,
        "project_ino": project.stat().st_ino,
        "project_head_at_ask": git_head(project),
        "git_config_sha256": expected_config,
        "AGENTS_before_sha256": digest(agents) if agents is not None else None,
        "AGENTS_before_mode": stat.S_IMODE((project / "AGENTS.md").stat().st_mode)
                               if agents is not None else None,
        "opencode_config_sha256": cfg_sha,
        "opencode_task_policy": permission,
        "runtime_source_sha256": fingerprint,
        "delegation_roles": list(LEAVES),
        "scope": "KAGARI_NATIVE_V4_TASK_SESSIONS_ONLY",
        "Git_revision_scope": "REQUEST_HEAD_OR_DESCENDANT_ONLY",
        "human_approval_required": True,
        "consent_origin": "LOCAL_FOREGROUND_TTY_NOT_CRYPTOGRAPHIC_IDENTITY",
        "production_qualified": False,
        "nonce": secrets.token_hex(16),
        "created_at": now(),
    }
    if kind == KIND_AGENTS:
        need(diagnostic["state"] == "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK",
             "only a confirmed AGENTS conflict qualifies for a fix Ask")
        need(agents is not None and diagnostic["ask_candidate"] is not None,
             "AGENTS exact source missing")
        preview = diagnostic["ask_candidate"]["proposed_file_change"]
        need(preview["state"] == "UNAPPROVED_EXACT_DIFF_PREVIEW",
             "unsupported AGENTS patch preview")
        proposed = agent_patch(agents)
        need(digest(proposed) == preview["proposed_sha256"],
             "AGENTS patch bytes do not match independent diagnostic")
        base["confirmed_conflict_ids"] = sorted(
            {v["id"] for v in diagnostic["confirmed_conflicts"]})
        base["conflicts"] = diagnostic["confirmed_conflicts"]
        base["patch_sha256"] = digest(proposed)
        base["unified_diff"] = preview["unified_diff"]
        base["effect"] = (
            "append the exact scoped KAGARI exception to Project AGENTS.md; "
            "ordinary AgentCore v3 sessions keep original rules"
        )
    else:
        need(permission == "PROJECT_TASK_DENY",
             "no install-time permission override Ask when Task is not denied")
        base["effect"] = (
            "within the KAGARI-managed primary, allow only the two installed "
            "KAGARI native child roles and deny the other Task targets; "
            "leave Project-wide OpenCode permissions unchanged"
        )
        base["default_task_permission"] = "deny"
        base["effective_task_allow"] = list(LEAVES)
        base["project_file_changes"] = []
    request_id = digest(canonical(base))[:24]
    base["request_id"] = request_id
    private_root(folder, create=True)
    if kind == KIND_AGENTS:
        save_new(folder, "agents-preimage.bin", agents)
    save_new(folder, "request.json", canonical(base))
    return base


def load_request(folder: Path) -> dict:
    folder = private_root(folder)
    request = load_json(folder, "request.json")
    assert request is not None
    need(type(request.get("request_id")) is str
         and re.fullmatch(r"[0-9a-f]{24}", request["request_id"]) is not None,
         "invalid request ID")
    without = {k: v for k, v in request.items() if k != "request_id"}
    need(digest(canonical(without))[:24] == request["request_id"],
         "Human Ask request ID does not bind exact content")
    need(request.get("schema_version") == VERSION
         and request.get("kind") in (KIND_AGENTS, KIND_PERMISSION)
         and request.get("profile") == "LOCAL_INTERACTIVE_TEST_ONLY_UNQUALIFIED"
         and request.get("delegation_roles") == list(LEAVES)
         and request.get("human_approval_required") is True
         and request.get("production_qualified") is False,
         "unsupported/expanded Human Ask scope")
    if request["kind"] == KIND_AGENTS:
        data = regular(folder / "agents-preimage.bin", max_size=compat.MAX_INSTRUCTIONS,
                       exact_mode=FILE_MODE)
        assert data is not None
        need(digest(data) == request["AGENTS_before_sha256"],
             "AGENTS preimage backup no longer matches request")
        need(digest(agent_patch(data)) == request["patch_sha256"],
             "Ask patch provenance mismatch")
    else:
        need(not (folder / "agents-preimage.bin").exists(),
             "unexpected AGENTS preimage in permission-only Ask")
    return request


def live_project(request: dict, *, exact_original_agents: bool = False,
                 allow_postimage: bool = False) -> Path:
    project = verify_project(Path(request["project_root"]))
    need(project.stat().st_dev == request["project_dev"]
         and project.stat().st_ino == request["project_ino"],
         "Project filesystem identity changed since Human Ask")
    config = regular(project / ".git/config")
    assert config is not None
    need(digest(config) == request["git_config_sha256"],
         "Project Git configuration changed since Human Ask")
    state, cfg_sha = permission_policy(project)
    need(state == request["opencode_task_policy"]
         and cfg_sha == request["opencode_config_sha256"],
         "Project OpenCode permission/configuration changed since Human Ask")
    if exact_original_agents or allow_postimage:
        path = project / "AGENTS.md"
        data = regular(path, max_size=compat.MAX_INSTRUCTIONS)
        assert data is not None
        before_sha = request["AGENTS_before_sha256"]
        permitted = {before_sha}
        if allow_postimage:
            permitted.add(request["patch_sha256"])
        need(digest(data) in permitted, "Project AGENTS.md changed since approved request")
        need(stat.S_IMODE(path.stat().st_mode) == request["AGENTS_before_mode"],
             "AGENTS file mode changed since approved request")
    return project


def validate_reply(folder: Path, request: dict) -> dict | None:
    reply = load_json(folder, "decision.json", required=False)
    if reply is None:
        return None
    need(reply.get("schema_version") == VERSION
         and reply.get("request_id") == request["request_id"]
         and reply.get("request_sha256") == digest(canonical(request))
         and reply.get("kind") == request["kind"]
         and reply.get("decision") in ("APPROVED", "DENIED")
         and reply.get("origin") == "LOCAL_FOREGROUND_TTY_NOT_CRYPTOGRAPHIC_IDENTITY",
         "Human Ask decision/provenance does not match request")
    return reply


def render_ask(request: dict) -> str:
    kind = request["kind"]
    first = ("KAGARI Human Ask — " + kind + "\n"
             + "Project: " + request["project_root"] + "\n"
             + "Ask ID: " + request["request_id"] + "\n"
             + "Runtime source SHA-256: " + request["runtime_source_sha256"] + "\n"
             + "Project AGENTS SHA-256: " + str(request["AGENTS_before_sha256"]) + "\n"
             + "Existing OpenCode Task policy: " + request["opencode_task_policy"] + "\n"
             + "Effect: " + request["effect"] + "\n"
             + "Refusal: no grant or Project mutation; conflicting KAGARI Task BLOCKED.\n"
             + "This is a test-only, local TTY Ask. It is NOT production user authentication.\n")
    if kind == KIND_AGENTS:
        return (first + "\nConflicting instructions:\n"
                + "\n".join(
                    str(item["line"]) + ": " + item["exact_instruction"]
                    for item in request["conflicts"])
                + "\n\nExact Project AGENTS.md patch:\n"
                + request["unified_diff"])
    return first + "\nOnly KAGARI-managed subagent roles: " + ", ".join(LEAVES)


def require_foreground_terminal() -> None:
    need(sys.stdin.isatty() and sys.stdout.isatty(),
         "Human Ask requires a foreground interactive TTY; no --yes bypass")
    # isatty alone is not evidence that this process owns the terminal.
    # /dev/tty must refer to an already-attached controlling terminal.
    ttyfd = None
    try:
        ttyfd = os.open("/dev/tty", os.O_RDONLY | os.O_NOCTTY)
        need(os.isatty(ttyfd), "missing controlling terminal")
        owner = os.tcgetpgrp(ttyfd)
        need(owner == os.getpgrp(),
             "Human Ask cannot confirm from a background process group")
    except (OSError, ValueError) as exc:
        raise AskBlocked("foreground controlling terminal unavailable") from exc
    finally:
        if ttyfd is not None:
            os.close(ttyfd)


def respond(folder: Path, decision: str) -> dict:
    request = load_request(folder)
    need(decision in ("approve", "deny"), "answer must be approve or deny")
    need(not (folder / "decision.json").exists(), "Ask was already answered")
    need(revoked(folder, request) is None,
         "Ask was retired/revoked; cannot record a subsequent answer")
    live_project(request, exact_original_agents=request["kind"] == KIND_AGENTS)
    need(git_head(Path(request["project_root"])) == request["project_head_at_ask"],
         "Project HEAD changed while Ask was pending; create a new request")
    require_foreground_terminal()
    print(render_ask(request), flush=True)
    expected = decision.upper() + " " + request["request_id"]
    print("\nEnter exactly " + expected + " to confirm this decision.", flush=True)
    actual = input("> ").strip()
    need(actual == expected, "Human confirmation phrase did not match; no decision")
    result = {
        "schema_version": VERSION, "request_id": request["request_id"],
        "request_sha256": digest(canonical(request)),
        "kind": request["kind"],
        "decision": "APPROVED" if decision == "approve" else "DENIED",
        "origin": "LOCAL_FOREGROUND_TTY_NOT_CRYPTOGRAPHIC_IDENTITY",
        "answered_at": now(),
    }
    save_new(folder, "decision.json", canonical(result))
    return result


def copy_exact(project: Path, payload: bytes, mode: int) -> None:
    """Replace only AGENTS.md after independently verified exact preimage."""
    need(len(payload) <= compat.MAX_INSTRUCTIONS, "AGENTS output too large")
    path = project / "AGENTS.md"
    need(path.is_file() and not path.is_symlink(), "AGENTS destination not ordinary")
    try:
        extra = os.listxattr(path, follow_symlinks=False)
    except OSError as exc:
        raise AskBlocked("cannot inspect Project AGENTS extended attributes") from exc
    need(not extra, "AGENTS with extended attributes/ACLs needs manual review")
    fd, temporary = tempfile.mkstemp(prefix=".kagari-scoped-", dir=str(project))
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        dirfd = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def activation(folder: Path, request: dict) -> dict | None:
    document = load_json(folder, "activation.json", required=False)
    if document is None:
        return None
    need(document.get("schema_version") == VERSION
         and document.get("request_sha256") == digest(canonical(request))
         and document.get("request_id") == request["request_id"]
         and document.get("before_sha256") == request["AGENTS_before_sha256"]
         and document.get("after_sha256") == request["patch_sha256"]
         and document.get("state") == "APPLIED_EXACT_ADDENDUM_TEST_ONLY",
         "Human Ask activation does not bind request/exact bytes")
    return document


def revoked(folder: Path, request: dict) -> dict | None:
    value = load_json(folder, "revocation.json", required=False)
    if value is not None:
        need(value.get("request_sha256") == digest(canonical(request))
             and value.get("request_id") == request["request_id"]
             and value.get("state") == "REVOKED",
             "invalid Ask revocation record")
    return value


def grant_status(folder: Path, project: Path, runtime_source: Path,
                 kind: str) -> dict:
    """Only this status grants Task dispatcher authority; never use ticket text."""
    request = load_request(folder)
    need(kind == request["kind"], "Human Ask kind does not match exact Task gate")
    need(str(verify_project(project)) == request["project_root"],
         "Task Project does not match approved Ask")
    need(runtime_identity(runtime_source) == request["runtime_source_sha256"],
         "active KAGARI runtime bytes differ from granted version")
    need(revoked(folder, request) is None, "Ask was revoked")
    reply = validate_reply(folder, request)
    need(reply is not None and reply["decision"] == "APPROVED",
         "required Human Ask approval is missing or denied")
    project = live_project(request, allow_postimage=request["kind"] == KIND_AGENTS)
    need(head_in_approved_history(project, request["project_head_at_ask"]),
         "Project Git HEAD is no longer descendant of approved request revision")
    if kind == KIND_AGENTS:
        active = activation(folder, request)
        need(active is not None, "approved AGENTS addendum not yet activated")
        data = regular(project / "AGENTS.md", max_size=compat.MAX_INSTRUCTIONS)
        assert data is not None
        need(digest(data) == request["patch_sha256"],
             "AGENTS grant is stale or user modified the approved addendum")
        diagnosis = compat.inspect(project)
        need(diagnosis["state"] == "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK"
             and not diagnosis["suspected_conflicts"]
             and sorted({c["id"] for c in diagnosis["confirmed_conflicts"]})
                == request["confirmed_conflict_ids"],
             "current AGENTS directives differ from approved known conflicts")
    else:
        need(request["opencode_task_policy"] == "PROJECT_TASK_DENY"
             and request["effective_task_allow"] == list(LEAVES)
             and request["default_task_permission"] == "deny",
             "permission manifest broadened from specific KAGARI leaves")
    return {
        "status": "ACTIVE_SCOPED_GRANT_TEST_ONLY",
        "request_id": request["request_id"],
        "kind": kind, "project_root": str(project),
        "runtime_source_sha256": request["runtime_source_sha256"],
        "request_sha256": digest(canonical(request)),
        "decision_sha256": digest(canonical(reply)),
        "granted_roles": list(LEAVES),
        "released": False,
        "production_qualified": False,
    }


def apply_agents(folder: Path, project: Path, runtime_source: Path) -> dict:
    request = load_request(folder)
    need(request["kind"] == KIND_AGENTS, "only AGENTS Ask has a Project patch")
    need(str(verify_project(project)) == request["project_root"],
         "wrong Project for approved AGENTS patch")
    need(runtime_identity(runtime_source) == request["runtime_source_sha256"],
         "approved runtime version changed")
    need(revoked(folder, request) is None, "Ask already revoked")
    reply = validate_reply(folder, request)
    need(reply is not None and reply["decision"] == "APPROVED",
         "no positive, exact Human approval for AGENTS patch")
    project = live_project(request, allow_postimage=True)
    # Never write the approved AGENTS addendum on a branch unrelated to
    # the revision that the human reviewed. Grant-status verification alone
    # is too late: the Project bytes would already have been changed.
    need(head_in_approved_history(project, request["project_head_at_ask"]),
         "Project Git HEAD is no longer descendant of approved request revision; "
         "refusing AGENTS mutation")
    backup = regular(folder / "agents-preimage.bin", exact_mode=FILE_MODE,
                     max_size=compat.MAX_INSTRUCTIONS)
    assert backup is not None
    after = agent_patch(backup)
    need(digest(after) == request["patch_sha256"], "patch drifted since approval")
    actual = regular(project / "AGENTS.md", max_size=compat.MAX_INSTRUCTIONS)
    assert actual is not None
    active = activation(folder, request)
    if active is None:
        need(actual in (backup, after), "AGENTS content modified; refuse patch")
        if actual == backup:
            copy_exact(project, after, request["AGENTS_before_mode"])
        save_new(folder, "activation.json", canonical({
            "schema_version": VERSION,
            "request_id": request["request_id"],
            "request_sha256": digest(canonical(request)),
            "before_sha256": request["AGENTS_before_sha256"],
            "after_sha256": request["patch_sha256"],
            "state": "APPLIED_EXACT_ADDENDUM_TEST_ONLY",
            "applied_at": now(),
        }))
    return grant_status(folder, project, runtime_source, KIND_AGENTS)


def revoke(folder: Path, project: Path, runtime_source: Path) -> dict:
    request = load_request(folder)
    need(str(verify_project(project)) == request["project_root"],
         "wrong Project for Ask revoke")
    need(runtime_identity(runtime_source) == request["runtime_source_sha256"],
         "runtime version mismatch at revoke")
    reply = validate_reply(folder, request)
    need(reply is not None and reply["decision"] == "APPROVED",
         "only an active approved Ask may be revoked")
    if revoked(folder, request) is not None:
        return {"status": "ALREADY_REVOKED", "request_id": request["request_id"]}
    project = live_project(request, allow_postimage=request["kind"] == KIND_AGENTS)
    need(head_in_approved_history(project, request["project_head_at_ask"]),
         "Project Git HEAD is no longer descendant of approved request revision; "
         "refusing owned content revocation")
    if request["kind"] == KIND_AGENTS:
        need(activation(folder, request) is not None,
             "AGENTS Ask is not applied; do not alter Project")
        current = regular(project / "AGENTS.md", max_size=compat.MAX_INSTRUCTIONS)
        assert current is not None
        need(digest(current) == request["patch_sha256"],
             "AGENTS modified by another owner; preserve and request review")
        before = regular(folder / "agents-preimage.bin",
                         max_size=compat.MAX_INSTRUCTIONS, exact_mode=FILE_MODE)
        assert before is not None
        copy_exact(project, before, request["AGENTS_before_mode"])
    save_new(folder, "revocation.json", canonical({
        "schema_version": VERSION, "request_id": request["request_id"],
        "request_sha256": digest(canonical(request)),
        "state": "REVOKED", "revoked_at": now(),
    }))
    return {"status": "REVOKED_TEST_ONLY", "request_id": request["request_id"],
            "AGENTS_restored": request["kind"] == KIND_AGENTS}


def retire(folder: Path, project: Path, runtime_source: Path) -> dict:
    """Uninstall-only: revoke KAGARI-owned pending/denied/unapplied requests.

    An already activated AGENTS grant is restored only through revoke(), with
    its exact postimage check. Unanswered or denied requests are never treated
    as approval; this records a permanent rejection of future activation.
    """
    request = load_request(folder)
    need(str(verify_project(project)) == request["project_root"],
         "wrong Project for Human Ask retirement")
    need(runtime_identity(runtime_source) == request["runtime_source_sha256"],
         "runtime identity changed before Ask retirement")
    if revoked(folder, request) is not None:
        return {"status": "ALREADY_REVOKED", "request_id": request["request_id"],
                "AGENTS_restored": False}
    observed = verify_project(project)
    need(observed.stat().st_dev == request["project_dev"]
         and observed.stat().st_ino == request["project_ino"],
         "Project ownership changed since Ask; cannot retire another Project")
    decision = validate_reply(folder, request)
    if request["kind"] == KIND_AGENTS:
        active = activation(folder, request)
        if active is not None:
            need(decision is not None and decision["decision"] == "APPROVED",
                 "AGENTS activation without approved request needs manual review")
            # Only an actually applied AGENTS addendum can rewrite Project
            # bytes. Exact postimage and Git ancestry must be rechecked.
            return revoke(folder, project, runtime_source)
    else:
        need(not (folder / "activation.json").exists(),
             "unexpected permission activation state")
    # Pending, denied, approved-but-unapplied AGENTS, and approved permission
    # grants can ALL be retired by writing only the external revocation record.
    # Drift of AGENTS/opencode/Git is not a reason to leave an unused Admin
    # attachment behind: no Project file is read or rewritten in this path.
    save_new(folder, "revocation.json", canonical({
        "schema_version": VERSION, "request_id": request["request_id"],
        "request_sha256": digest(canonical(request)),
        "state": "REVOKED", "revoked_at": now(),
    }))
    return {"status": "REVOKED_TEST_ONLY", "request_id": request["request_id"],
            "AGENTS_restored": False, "retired_without_active_grant": True}


def status(folder: Path, project: Path, runtime_source: Path) -> dict:
    request = load_request(folder)
    try:
        reply = validate_reply(folder, request)
        if revoked(folder, request):
            name = "REVOKED"
        elif reply is None:
            name = "PENDING_ASK"
        elif reply["decision"] == "DENIED":
            name = "DENIED"
        elif request["kind"] == KIND_AGENTS and activation(folder, request) is None:
            name = "APPROVED_NOT_APPLIED"
        else:
            grant_status(folder, project, runtime_source, request["kind"])
            name = "ACTIVE_SCOPED_GRANT_TEST_ONLY"
    except AskBlocked as exc:
        return {"status": "BLOCKED_UNVERIFIED_GRANT",
                "reason": str(exc), "request_id": request["request_id"]}
    return {"status": name, "request_id": request["request_id"],
            "kind": request["kind"], "project_root": request["project_root"],
            "runtime_sha256": request["runtime_source_sha256"],
            "effective_permission": name == "ACTIVE_SCOPED_GRANT_TEST_ONLY",
            "production_qualified": False}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=[
        "request-agents", "request-task-permission", "show", "approve", "deny",
        "apply", "revoke", "status"
    ])
    p.add_argument("--project-root", required=True, type=Path)
    p.add_argument("--state-root", required=True, type=Path)
    p.add_argument("--runtime-source", required=True, type=Path)
    args = p.parse_args()
    try:
        if args.operation in ("request-agents", "request-task-permission"):
            kind = KIND_AGENTS if args.operation == "request-agents" else KIND_PERMISSION
            requested = make_request(
                args.project_root, args.state_root, args.runtime_source, kind)
            answer = {"status": "PENDING_ASK", "request_id": requested["request_id"],
                      "kind": kind, "ask_text": render_ask(requested),
                      "approval_recorded": False, "project_mutated": False}
        elif args.operation == "show":
            requested = load_request(args.state_root)
            answer = {"status": "PENDING_OR_RECORDED_ASK",
                      "request_id": requested["request_id"],
                      "ask_text": render_ask(requested),
                      "current": status(args.state_root, args.project_root,
                                        args.runtime_source)}
        elif args.operation in ("approve", "deny"):
            answer = respond(args.state_root, args.operation)
        elif args.operation == "apply":
            answer = apply_agents(args.state_root, args.project_root,
                                  args.runtime_source)
        elif args.operation == "revoke":
            answer = revoke(args.state_root, args.project_root,
                            args.runtime_source)
        else:
            answer = status(args.state_root, args.project_root,
                            args.runtime_source)
    except (AskBlocked, compat.CompatibilityBlocked, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc),
                          "production_qualified": False}, ensure_ascii=False,
                         sort_keys=True))
        return 2
    print(json.dumps(answer, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
