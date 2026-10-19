#!/usr/bin/env python3
"""KAGARI v0.1.0 TEST_ONLY: attach native runtime + Human Ask lifecycle.

This is a disposable local, external-root Admin bridge. It does not use
Project .automation/Just/flake, upgrade legacy v3, or grant Task permissions
without separate foreground Human Ask decisions. No production activation,
source publication, remote Push, or model invocation occurs at install.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import kagari_v010_agents_compat as compat
import kagari_v010_human_ask as ask

VERSION = 1
NAME = "kagari-v010"
PROFILE = "TEST_ONLY_UNQUALIFIED"
STORE_PATTERN = re.compile(
    r"[0-9a-z]{32}-kagari-0\.1\.0-native-generic-test-only\Z")
ASK_PATTERN = re.compile(r"asks-[0-9a-f]{24}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
INSTALLED_TOOLS = frozenset({
    "kagari_v010_native_generic_task.py",
    "kagari_v010_basic_plan.py",
    "kagari_v010_auto_planning.py",
    "kagari_v010_general_task_spec.py",
    "kagari_v010_native_subagent_proof.py",
    "kagari_v010_agents_compat.py",
    "kagari_v010_human_ask.py",
    "kagari_v010_consent_admin.py",
})
MAIN = "libexec/kagari-v010-native-generic/tools/kagari_v010_native_generic_task.py"
ASK_COMMAND = "bin/kagari-v010-human-ask-test-only"
TASK_COMMAND = "bin/kagari-v010-native-generic-test-only"
PERMISSION = "task-permission"
AGENTS = "agents"


class AdminBlocked(ValueError):
    """No authority to mutate or interpret incomplete Admin/Ask state."""


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise AdminBlocked(reason)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode() + b"\n"


def exact_regular(path: Path, *, mode: int, max_size: int = 1024 * 1024) -> bytes:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise AdminBlocked("required installed file missing: " + str(path)) from None
    # Nix-store files may be hardlinked through store deduplication. The
    # operator-pinned SHA and store immutability, not st_nlink=1, bind content.
    need(stat.S_ISREG(info.st_mode) and info.st_nlink >= 1
         and stat.S_IMODE(info.st_mode) == mode and info.st_size <= max_size,
         "file type/link/mode/bound mismatch: " + str(path))
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AdminBlocked("unreadable installed file: " + str(path)) from exc
    need(len(data) <= max_size, "installed file exceeded bound: " + str(path))
    return data


def root_dir(path: Path, *, name: str | None = None) -> Path:
    need(path.is_absolute() and path.resolve() == path
         and path.is_dir() and not path.is_symlink(),
         "directory must be existing canonical absolute path")
    if name is not None:
        need(path.name == name, "unexpected directory basename")
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
         and stat.S_IMODE(info.st_mode) == 0o700
         and not os.path.ismount(path), "directory must be private euid-owned 0700")
    return path


def roots(project: Path, runtime: Path) -> tuple[Path, Path]:
    try:
        project = ask.verify_project(project)
    except ask.AskBlocked as exc:
        raise AdminBlocked(str(exc)) from exc
    root_dir(runtime, name="runtime")
    parent = root_dir(runtime.parent)
    need(parent.parent == Path(tempfile.gettempdir()).resolve()
         and parent.name.startswith("kagari-v010-consent-install-"),
         "Admin runtime must reside in separate disposable private directory")
    need(not project.is_relative_to(parent) and not parent.is_relative_to(project),
         "Project and runtime ownership roots must be disjoint")
    # Never mistake a normal Project subdirectory for a Git repository.
    git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    git_env.update(GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_NOSYSTEM="1", GIT_OPTIONAL_LOCKS="0")
    try:
        observed = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(project),
             "-c", "core.fsmonitor=false", "rev-parse", "--show-toplevel"],
            env=git_env, capture_output=True, check=True, timeout=10,
        ).stdout.decode().strip()
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise AdminBlocked("Git root cannot be validated") from exc
    need(observed == str(project), "Project must be the exact Git toplevel")
    return project, runtime


def verify_package(package: Path, expected_receipt_sha256: str) -> dict:
    """Conservative external *operator-pinned* test-only Nix payload check."""
    need(type(expected_receipt_sha256) is str
         and SHA256.fullmatch(expected_receipt_sha256) is not None,
         "operator must supply independently reviewed receipt SHA-256")
    need(package.is_absolute()
         and package.parent == Path("/nix/store")
         and STORE_PATTERN.fullmatch(package.name) is not None
         and package.is_dir() and not package.is_symlink()
         and stat.S_IMODE(package.stat().st_mode) == 0o555,
         "native test-only package must be exact immutable Nix output")
    raw = exact_regular(package / "share/kagari-v010-native-generic/receipt.json",
                        mode=0o444, max_size=64 * 1024)
    need(sha(raw) == expected_receipt_sha256,
         "independent native package receipt SHA-256 mismatch")
    try:
        receipt = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise AdminBlocked("native package receipt invalid") from exc
    need(type(receipt) is dict
         and receipt.get("public_target_version") == "0.1.0"
         and receipt.get("qualification") == PROFILE
         and receipt.get("released") is False
         and receipt.get("production_activation") is False
         and receipt.get("generic_production_task_qualified") is False
         and receipt.get("human_ask_production_qualified") is False
         and receipt.get("human_ask_remote_user_identity_verified") is False,
         "native package claim/version invalid")
    claimed = receipt.get("tool_sha256")
    need(type(claimed) is dict and set(claimed) == INSTALLED_TOOLS,
         "unexpected native package tool closure")
    for name, expected in claimed.items():
        need(type(expected) is str and SHA256.fullmatch(expected) is not None,
             "native package tool digest malformed")
        source = package / "libexec/kagari-v010-native-generic/tools" / name
        need(sha(exact_regular(source, mode=0o444)) == expected,
             "native installed tool differs from pinned receipt: " + name)
    n1 = exact_regular(package / "share/kagari-v010-native-generic/n1-manifest.json",
                       mode=0o444, max_size=64 * 1024)
    need(sha(n1) == receipt.get("n1_manifest_sha256"),
         "historical N1 manifest digest mismatch")
    try:
        manifest = json.loads(n1)
    except (ValueError, UnicodeError) as exc:
        raise AdminBlocked("historical N1 manifest malformed") from exc
    items = manifest.get("files") if type(manifest) is dict else None
    need(type(items) is list and len(items) == 12
         and manifest.get("target_version") == "1.0.0"
         and manifest.get("qualification") == "SOURCE_ONLY_UNQUALIFIED",
         "unrecognized N1 module contract")
    modules = receipt.get("v4_module_sha256")
    need(type(modules) is dict and len(modules) == 12,
         "unbound v4 module inventory")
    discovered = set()
    for item in items:
        need(type(item) is dict and type(item.get("path")) is str
             and type(item.get("sha256")) is str
             and SHA256.fullmatch(item["sha256"]) is not None,
             "invalid expected native v4 module")
        name = Path(item["path"]).name
        need(name not in discovered and name.endswith(".py")
             and modules.get(name) == item["sha256"],
             "native v4 module receipt differs from N1 declaration")
        source = (package / "libexec/kagari-v010-native-generic/"
                  "components/agent-core-v4" / name)
        need(sha(exact_regular(source, mode=0o444)) == item["sha256"],
             "native v4 module bytes do not match pinned N1 manifest")
        discovered.add(name)
    need(set(modules) == discovered, "unknown native v4 module identity")
    required_wrappers = {
        "kagari-v010-native-generic-test-only",
        "kagari-v010-native-subagent-proof-test-only",
        "kagari-v010-agents-compat-test-only",
        "kagari-v010-human-ask-test-only",
        "kagari-v010-consent-admin-test-only",
    }
    wrappers = receipt.get("wrapper_sha256")
    need(type(wrappers) is dict and set(wrappers) == required_wrappers,
         "missing exact binary entrypoint checksums")
    for name, checksum in wrappers.items():
        need(type(checksum) is str and SHA256.fullmatch(checksum) is not None,
             "invalid native wrapper hash")
        binary = exact_regular(package / "bin" / name, mode=0o555, max_size=65536)
        need(sha(binary) == checksum, "native package executable not pinned: " + name)
    return {
        "package_root": str(package),
        "receipt_sha256": expected_receipt_sha256,
        "runtime_source_sha256": claimed["kagari_v010_native_generic_task.py"],
        "public_target_version": "0.1.0",
        "qualification": PROFILE,
    }


def save_owner(path: Path, raw: bytes) -> None:
    need(len(raw) <= 65536, "owner record exceeds bounded size")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def owner_record(project: Path, identity: dict, ask_parent: Path,
                 required_kinds: tuple[str, ...], identifiers: dict[str, str]) -> dict:
    info = project.stat()
    return {
        "schema_version": VERSION,
        "kind": "KAGARI_LOCAL_CONSENT_INSTALL",
        "project_root": str(project), "project_dev": info.st_dev, "project_ino": info.st_ino,
        **identity,
        "ask_parent": str(ask_parent),
        "required_asks": list(required_kinds),
        "request_ids": identifiers,
        "released": False,
        "production_activation": False,
    }


def needed_asks(project: Path) -> tuple[str, ...]:
    diagnostic = compat.inspect(project)
    perm, _ = ask.permission_policy(project)
    needs = []
    if diagnostic["state"] == "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK":
        needs.append(ask.KIND_AGENTS)
    if perm == "PROJECT_TASK_DENY":
        needs.append(ask.KIND_PERMISSION)
    return tuple(needs)


def ask_dir(owner: dict) -> Path:
    path = Path(owner["ask_parent"])
    need(path.parent.name.startswith("kagari-v010-consent-install-")
         and ASK_PATTERN.fullmatch(path.name) is not None,
         "invalid owner Ask state location")
    root_dir(path)
    return path


def validate_owner(project: Path, runtime: Path, identity: dict) -> tuple[dict | None, str]:
    entries = {p.name for p in runtime.iterdir()}
    if not entries:
        return None, "ABSENT"
    need(entries == {NAME}, "unknown runtime root entries")
    owned = root_dir(runtime / NAME)
    entries = {p.name for p in owned.iterdir()}
    need(entries in ({"owner.json"}, {"owner.json", "package"}),
         "unknown KAGARI-managed runtime entries")
    raw = exact_regular(owned / "owner.json", mode=0o600, max_size=65536)
    try:
        owner = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise AdminBlocked("installed owner record malformed") from exc
    need(type(owner) is dict and canonical(owner) == raw,
         "owner record is not canonical")
    here = project.stat()
    need(owner.get("schema_version") == VERSION
         and type(owner["schema_version"]) is int
         and owner.get("kind") == "KAGARI_LOCAL_CONSENT_INSTALL"
         and owner.get("project_root") == str(project)
         and owner.get("project_dev") == here.st_dev
         and owner.get("project_ino") == here.st_ino
         and owner.get("released") is False
         and owner.get("production_activation") is False,
         "owner Project/qualification changed")
    for label, value in identity.items():
        need(owner.get(label) == value, "installed package identity changed: " + label)
    parent = ask_dir(owner)
    need(parent.parent == runtime.parent, "Ask owner directory is outside runtime generation")
    needed = owner.get("required_asks")
    ids = owner.get("request_ids")
    need(type(needed) is list and set(needed) <= {ask.KIND_AGENTS, ask.KIND_PERMISSION}
         and len(needed) == len(set(needed))
         and type(ids) is dict and set(ids) == set(needed),
         "unknown requested-Ask inventory in owner")
    need({x.name for x in parent.iterdir()}
         == {AGENTS if x == ask.KIND_AGENTS else PERMISSION for x in needed},
         "unrecognized Ask generation entries")
    for kind in needed:
        folder = parent / (AGENTS if kind == ask.KIND_AGENTS else PERMISSION)
        request = ask.load_request(folder)
        need(request["kind"] == kind
             and request["request_id"] == ids[kind]
             and request["project_root"] == str(project)
             and request["runtime_source_sha256"] == identity["runtime_source_sha256"],
             "Ask record does not belong to installed version/Project")
    if "package" not in entries:
        return owner, "MISSING_OWNED_LINK"
    link = owned / "package"
    need(link.is_symlink() and os.readlink(link) == identity["package_root"],
         "installed Nix package link changed or unknown")
    return owner, "INSTALLED_TEST_ONLY"


def request_status(owner: dict, project: Path, runtime_source: Path) -> dict:
    parent = ask_dir(owner)
    result = {}
    for kind in owner["required_asks"]:
        folder = parent / (AGENTS if kind == ask.KIND_AGENTS else PERMISSION)
        result[kind] = ask.status(folder, project, runtime_source)
    return result


def summary(project: Path, runtime: Path, package: Path, identity: dict) -> dict:
    owner, state = validate_owner(project, runtime, identity)
    diagnostic = compat.inspect(project)
    if owner is None:
        needs = needed_asks(project)
        return {
            "status": "ABSENT", "version": "0.1.0", "qualification": PROFILE,
            "project_mutations": [], "required_asks_if_installed": list(needs),
            "agents_compatibility": diagnostic["state"],
            "ready_for_model_task": False,
            "production_activation": False, **identity,
        }
    grants = request_status(owner, project, package / MAIN)
    current_needed = set(needed_asks(project))
    scope_unchanged = current_needed == set(owner["required_asks"])
    valid = all(v["status"] == "ACTIVE_SCOPED_GRANT_TEST_ONLY"
                for v in grants.values())
    return {
        "status": state, "version": "0.1.0", "qualification": PROFILE,
        "project_mutations": [],
        "required_asks": owner["required_asks"],
        "ask_state_root": owner["ask_parent"],
        "ask_status": grants,
        "agents_compatibility": diagnostic["state"],
        "Ask_scope_matches_current_Project": scope_unchanged,
        "ready_for_model_task": bool(state == "INSTALLED_TEST_ONLY"
                                     and scope_unchanged and valid
                                     and diagnostic["state"] not in (
                                         "REVIEW_REQUIRED_UNCONFIRMED_CONFLICT",
                                         "REVIEW_REQUIRED_UNKNOWN_INSTRUCTIONS")),
        "production_activation": False, **identity,
    }


def install(project: Path, runtime: Path, package: Path, identity: dict) -> dict:
    owner, state = validate_owner(project, runtime, identity)
    if owner is not None:
        need(state == "INSTALLED_TEST_ONLY",
             "partial attached package requires explicit repair")
        return summary(project, runtime, package, identity)
    kinds = needed_asks(project)
    # Requests are created in one private external generation, never in a
    # Project tree or Admin-owned package directory.
    parent = runtime.parent / ("asks-" + secrets.token_hex(12))
    need(not parent.exists() and not parent.is_symlink(),
         "new Ask generation path unexpectedly exists")
    parent.mkdir(mode=0o700)
    ids: dict[str, str] = {}
    source = package / MAIN
    for kind in kinds:
        child = parent / (AGENTS if kind == ask.KIND_AGENTS else PERMISSION)
        request = ask.make_request(project, child, source, kind)
        ids[kind] = request["request_id"]
    expected = owner_record(project, identity, parent, kinds, ids)
    # The attachment itself is non-executing; pending Ask is not authority.
    owned = runtime / NAME
    owned.mkdir(mode=0o700)
    save_owner(owned / "owner.json", canonical(expected))
    (owned / "package").symlink_to(package, target_is_directory=True)
    return summary(project, runtime, package, identity)


def repair(project: Path, runtime: Path, package: Path, identity: dict) -> dict:
    owner, state = validate_owner(project, runtime, identity)
    need(owner is not None, "cannot repair an absent attachment")
    if state == "MISSING_OWNED_LINK":
        (runtime / NAME / "package").symlink_to(package, target_is_directory=True)
    return summary(project, runtime, package, identity)


def uninstall(project: Path, runtime: Path, package: Path, identity: dict) -> dict:
    owner, state = validate_owner(project, runtime, identity)
    if owner is None:
        return {"status": "ABSENT", "revoked_asks": [], "production_activation": False}
    parent = ask_dir(owner)
    source = package / MAIN
    states = request_status(owner, project, source)
    # Only an actually applied AGENTS addendum may require a Project rewrite.
    # Pending, denied, approved-but-unapplied, and permission-only grants can
    # be retired without demanding frozen Project HEAD/AGENTS/opencode bytes.
    # This makes uninstall possible after ordinary unrelated Project edits.
    if ask.KIND_AGENTS in owner["required_asks"]:
        agents_folder = parent / AGENTS
        agents_request = ask.load_request(agents_folder)
        if ask.activation(agents_folder, agents_request) is not None:
            # An already revoked AGENTS Ask has restored the exact preimage
            # through ask.revoke(). Its retained activation.json is historical
            # evidence, not a currently active grant or content to restore.
            # ask.retire() will verify revocation and avoid Project mutation.
            if ask.revoked(agents_folder, agents_request) is None:
                need(states[ask.KIND_AGENTS]["status"] ==
                     "ACTIVE_SCOPED_GRANT_TEST_ONLY",
                     "approved AGENTS content changed; preserve Project and install")
                ask.grant_status(agents_folder, project, source, ask.KIND_AGENTS)
    # Stop delegation first, then restore exact AGENTS bytes when owned.
    ordering = [ask.KIND_PERMISSION, ask.KIND_AGENTS]
    revoked = []
    for kind in ordering:
        if kind not in owner["required_asks"]:
            continue
        folder = parent / (AGENTS if kind == ask.KIND_AGENTS else PERMISSION)
        response = ask.retire(folder, project, source)
        revoked.append({"kind": kind, **response})
    link = runtime / NAME / "package"
    if state == "INSTALLED_TEST_ONLY":
        link.unlink()
    (runtime / NAME / "owner.json").unlink()
    (runtime / NAME).rmdir()
    # Retired audit receipts are kept private, never silently destroyed.
    return {"status": "ABSENT", "revoked_asks": revoked,
            "ask_audit_root_retained": str(parent),
            "project_mutations": ["AGENTS.md (exact approved preimage restored)"]
                                 if any(x.get("AGENTS_restored") for x in revoked)
                                 else [],
            "production_activation": False}


def owner_ask_root(owner: dict, kind: str) -> Path:
    need(kind in owner["required_asks"], "Ask not required for this Project")
    return ask_dir(owner) / (AGENTS if kind == ask.KIND_AGENTS else PERMISSION)


def decision(project: Path, runtime: Path, package: Path, identity: dict,
             kind: str, operation: str) -> dict:
    owner, state = validate_owner(project, runtime, identity)
    need(owner is not None and state == "INSTALLED_TEST_ONLY",
         "must install exact runtime before Human Ask decision")
    folder = owner_ask_root(owner, kind)
    if operation == "approve":
        return ask.respond(folder, "approve")
    if operation == "deny":
        return ask.respond(folder, "deny")
    if operation == "apply":
        need(kind == ask.KIND_AGENTS, "only AGENTS Ask can modify AGENTS.md")
        return ask.apply_agents(folder, project, package / MAIN)
    if operation == "show":
        req = ask.load_request(folder)
        return {"status": "ASK_REVIEW", "kind": kind, "request_id": req["request_id"],
                "ask_text": ask.render_ask(req),
                "current": ask.status(folder, project, package / MAIN)}
    raise AdminBlocked("unknown Human Ask decision")


def task_arguments(spec: Path, plan: Path | None, opencode: Path, output: Path,
                   repository: str, task: str, timeout: int) -> list[str]:
    need(all(v.is_absolute() for v in (spec, opencode, output))
         and (plan is None or plan.is_absolute()),
         "Task inputs must be absolute paths")
    need(type(timeout) is int and 10 <= timeout <= 240, "invalid model timeout")
    need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*",
                      repository) is not None,
         "Task repository identity required")
    need(re.fullmatch(r"[1-9][0-9]*", task) is not None, "positive Task ID required")
    result = ["--execute-model", "--run-tests", "--disposable-project",
              "--task-spec", str(spec), "--opencode", str(opencode),
              "--output", str(output), "--repository", repository,
              "--task", task, "--timeout", str(timeout)]
    if plan is not None:
        result.extend(["--plan", str(plan)])
    return result


def invoke_native(executable: Path, project: Path, args: list[str],
                  timeout: int) -> subprocess.CompletedProcess[str]:
    """Run the exact native CLI with a complete bounded multi-phase budget.

    Auto Planning, native primary, three bounded session exports, up to 16
    declared tests and metadata readback are distinct phases. Do not kill
    an otherwise valid Task after the old one-model timeout + 70 seconds.
    The dedicated process group is signalled before any timeout is reported.
    """
    # The TaskSpec validator permits at most 16 tests, each <=60 seconds.
    # This remains a TEST_ONLY upper bound, not a production SLA.
    total = timeout + 120 + 3 * 35 + 16 * 60 + 120
    argv = [str(executable), *args]
    proc = subprocess.Popen(
        argv, cwd=project, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, stdin=subprocess.DEVNULL,
        shell=False, start_new_session=True)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=total)
        except subprocess.TimeoutExpired as exc:
            raise AdminBlocked("native Task exceeded bounded end-to-end runtime") from exc
        need(len(stdout.encode("utf-8")) <= 1024 * 1024
             and len(stderr.encode("utf-8")) <= 1024 * 1024,
             "native Task emitted excessive diagnostic output")
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Reap the direct leader. Descendants in this separate process group
        # were signalled before the Admin boundary can return.
        proc.wait(timeout=5)


def run_task(project: Path, runtime: Path, package: Path, identity: dict,
             *, spec: Path, plan: Path | None, opencode: Path, output: Path,
             repository: str, task: str, timeout: int) -> dict:
    owner, state = validate_owner(project, runtime, identity)
    need(owner is not None and state == "INSTALLED_TEST_ONLY",
         "verified installed KAGARI native runtime required")
    diagnostic = compat.inspect(project)
    need(diagnostic["state"] not in (
         "REVIEW_REQUIRED_UNCONFIRMED_CONFLICT",
         "REVIEW_REQUIRED_UNKNOWN_INSTRUCTIONS"),
         "AGENTS compatibility manual review required")
    folder = ask_dir(owner)
    for kind in owner["required_asks"]:
        target = folder / (AGENTS if kind == ask.KIND_AGENTS else PERMISSION)
        ask.grant_status(target, project, package / MAIN, kind)
    # Recompute requirements to avoid using prior consent in an expanded policy.
    need(set(owner["required_asks"]) == set(needed_asks(project)),
         "Project instruction/permission policy changed after Install")
    args = task_arguments(spec, plan, opencode, output, repository, task, timeout)
    # A valid Task output may not share Admin-owned / Ask-owned storage.
    # Otherwise a Task's output directory could make inspect/uninstall refuse
    # an unexpectedly contaminated runtime inventory after a successful run.
    resolved_output = output.resolve(strict=False)
    need(not resolved_output.is_relative_to(runtime.parent)
         and not resolved_output.is_relative_to(project)
         and not resolved_output.is_relative_to(package),
         "Task output cannot be inside Project, installed runtime or Ask state")
    args = ["--project-root", str(project), *args]
    if ask.KIND_AGENTS in owner["required_asks"]:
        args.extend(["--agents-ask-root", str(folder / AGENTS)])
    if ask.KIND_PERMISSION in owner["required_asks"]:
        args.extend(["--permission-ask-root", str(folder / PERMISSION)])
    # No legacy --allow-project-task-deny-test-only shortcut.
    proc = invoke_native(package / TASK_COMMAND, project, args, timeout)
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise AdminBlocked("native Task emitted no readable final receipt") from exc
    need(type(result) is dict, "native Task result must be an object")
    need(proc.returncode == 0 or
         result.get("status") != "REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS",
         "native Task claims PASS despite unsuccessful executable")
    return {
        "status": result.get("status", "BLOCKED"),
        "native_task_exit": proc.returncode,
        "qualification": PROFILE, "production_activation": False,
        "native_task_result": result,
        "ask_runtime_attached": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=(
        "inspect", "install", "repair", "uninstall",
        "show-agents", "show-permission",
        "approve-agents", "deny-agents", "apply-agents",
        "approve-permission", "deny-permission", "run-task"))
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--expected-receipt-sha256", required=True)
    parser.add_argument("--test-only", action="store_true")
    parser.add_argument("--task-spec", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--opencode", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repository", default="fixture/kagari-native")
    parser.add_argument("--task", default="271")
    parser.add_argument("--timeout", type=int, default=150)
    args = parser.parse_args(argv)
    try:
        need(args.test_only, "--test-only is required")
        project, runtime = roots(args.project_root, args.runtime_root)
        package = args.package_root
        identity = verify_package(package, args.expected_receipt_sha256)
        if args.operation == "inspect":
            result = summary(project, runtime, package, identity)
        elif args.operation == "install":
            result = install(project, runtime, package, identity)
        elif args.operation == "repair":
            result = repair(project, runtime, package, identity)
        elif args.operation == "uninstall":
            result = uninstall(project, runtime, package, identity)
        elif args.operation == "run-task":
            need(all(x is not None for x in
                     (args.task_spec, args.opencode, args.output)),
                 "explicit TaskSpec/OpenCode/output required; Plan optional only in auto mode")
            result = run_task(
                project, runtime, package, identity,
                spec=args.task_spec, plan=args.plan, opencode=args.opencode,
                output=args.output, repository=args.repository, task=args.task,
                timeout=args.timeout)
        else:
            kind = ask.KIND_AGENTS if args.operation.endswith("agents") else ask.KIND_PERMISSION
            action = args.operation.split("-", 1)[0]
            result = decision(project, runtime, package, identity, kind, action)
    except (AdminBlocked, ask.AskBlocked, compat.CompatibilityBlocked,
            OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        result = {"status": "BLOCKED", "reason": str(exc)[:500],
                  "qualification": PROFILE, "production_activation": False}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if (result["status"] not in ("BLOCKED",)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
