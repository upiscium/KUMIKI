"""KAGARI 0.1.0 composed local candidate: non-production, exact native Admin bridge.

No grants at install, no model without explicit scope and native Human Ask.
The script is installed in a pinned Nix output and accepts package identities only
from its immutable wrapper, never from user CLI options.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

VERSION = "0.1.0"
QUALIFICATION = "UNRELEASED_CANDIDATE"
NAME = "kagari-0.1.0-composed-unreleased-candidate"
SHARE = "share/kagari-v010-composed/receipt.json"
SCRIPT = "libexec/kagari-v010-composed/kagari_v010_composed_cli.py"
NATIVE_RECEIPT = "share/kagari-v010-native-generic/receipt.json"
NATIVE_ADMIN = "bin/kagari-v010-consent-admin-test-only"
OPERATIONS = (
    "inspect", "install", "repair", "uninstall",
    "show-agents", "show-permission",
    "approve-agents", "approve-permission", "deny-agents",
    "deny-permission", "apply-agents", "run-task",
)

class CandidateBlocked(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CandidateBlocked(reason)


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def regular(path: Path) -> bytes:
    require(path.is_file() and not path.is_symlink(), "missing or symlinked immutable package member")
    return path.read_bytes()


def verify(candidate: Path, native: Path) -> dict:
    require(candidate.is_absolute() and candidate.parent == Path("/nix/store")
            and re.fullmatch(r"[0-9a-df-np-sv-z]{32}-" + NAME, candidate.name) is not None,
            "expected exact composed Nix store output")
    require(native.is_absolute() and native.parent == Path("/nix/store")
            and native.name.endswith("-kagari-0.1.0-native-generic-test-only"),
            "expected exact native test-only Nix store output")
    script = candidate / SCRIPT
    require(script == Path(__file__) and script.resolve() == script,
            "candidate CLI must execute from its immutable package path")
    data = json.loads(regular(candidate / SHARE))
    for key, expected in {
        "version": VERSION,
        "qualification": QUALIFICATION,
        "released": False,
        "production_activation": False,
        "consumer_production_qualified": False,
        "generic_task_qualified": False,
        "source_publication_approved": False,
        "candidate_root": str(candidate),
        "native_root": str(native),
    }.items():
        require(type(data.get(key)) is type(expected) and data[key] == expected,
                "candidate receipt identity/qualification mismatch: " + key)
    require(data.get("cli_sha256") == sha(regular(script)),
            "candidate CLI SHA-256 mismatch")
    require(data.get("native_receipt_sha256") == sha(regular(native / NATIVE_RECEIPT)),
            "native package SHA-256 mismatch")
    native_data = json.loads(regular(native / NATIVE_RECEIPT))
    require(native_data.get("public_target_version") == VERSION
            and native_data.get("qualification") == "TEST_ONLY_UNQUALIFIED"
            and native_data.get("released") is False
            and native_data.get("production_activation") is False
            and native_data.get("human_ask_remote_user_identity_verified") is False,
            "native runtime is not the expected local test-only source")
    admin = native / NATIVE_ADMIN
    require(data.get("native_admin_sha256") == sha(regular(admin))
            and native_data.get("wrapper_sha256", {}).get(Path(NATIVE_ADMIN).name) == sha(regular(admin)),
            "native Admin wrapper is not receipt-bound")
    return data


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=("version", "status", "doctor", *OPERATIONS))
    p.add_argument("--test-only", action="store_true",
                   help="required for every Project-affecting local Admin operation")
    p.add_argument("--project-root", type=Path)
    p.add_argument("--runtime-root", type=Path)
    p.add_argument("--task-spec", type=Path)
    p.add_argument("--plan", type=Path)
    p.add_argument("--opencode", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--repository", default="fixture/kagari-native")
    p.add_argument("--task", default="271")
    p.add_argument("--timeout", type=int, default=150)
    return p


def execute(candidate: Path, native: Path, args: argparse.Namespace) -> int:
    receipt = verify(candidate, native)
    info = {
        "version": VERSION,
        "qualification": QUALIFICATION,
        "released": False,
        "production_activation": False,
        "consumer_production_qualified": False,
        "generic_task_qualified": False,
        "candidate_root": str(candidate),
        "native_root": str(native),
        "native_receipt_sha256": receipt["native_receipt_sha256"],
    }
    if args.operation in ("version", "status", "doctor"):
        require(not args.test_only and all(x is None for x in (
            args.project_root, args.runtime_root, args.task_spec, args.plan,
            args.opencode, args.output)),
            "metadata-only commands cannot silently inspect or mutate a Project")
    if args.operation == "version":
        print(json.dumps(info, sort_keys=True))
        return 0
    if args.operation in ("status", "doctor"):
        info.update(status="UNRELEASED_CANDIDATE",
                    supported_operations=list(OPERATIONS),
                    model_invoked=False,
                    project_permission_status="NOT_ASSESSED",
                    effective_project_authority=False)
        if args.operation == "doctor":
            info.update(host_platform=platform.system().lower(),
                        linux_profile_detected=sys.platform == "linux"
                        and Path("/proc/self/mountinfo").is_file(),
                        provider_checked=False)
        print(json.dumps(info, sort_keys=True))
        return 0

    require(args.test_only, "Project operations require explicit --test-only; production activation unavailable")
    require(args.project_root is not None and args.runtime_root is not None
            and args.project_root.is_absolute() and args.runtime_root.is_absolute(),
            "explicit absolute Project and external runtime roots required")
    if args.operation != "run-task":
        require(all(value is None for value in
                    (args.task_spec, args.plan, args.opencode, args.output)),
                "Task-only arguments are not valid for Admin lifecycle operations")
    else:
        require(args.task_spec is not None and args.opencode is not None and args.output is not None,
                "run-task requires exact TaskSpec, OpenCode executable and output")
    command = [
        str(native / NATIVE_ADMIN), args.operation,
        "--project-root", str(args.project_root),
        "--runtime-root", str(args.runtime_root),
        "--package-root", str(native),
        "--expected-receipt-sha256", receipt["native_receipt_sha256"],
        "--test-only",
    ]
    if args.operation == "run-task":
        command.extend(["--task-spec", str(args.task_spec),
                        "--opencode", str(args.opencode),
                        "--output", str(args.output),
                        "--repository", args.repository, "--task", args.task,
                        "--timeout", str(args.timeout)])
        if args.plan is not None:
            command.extend(["--plan", str(args.plan)])
    done = subprocess.run(command, shell=False, check=False)
    return done.returncode


def main(argv: list[str]) -> int:
    # Only the immutable Nix wrapper supplies candidate and native identities.
    try:
        require(len(argv) >= 3, "wrapper must supply candidate and native package identity")
        candidate, native = Path(argv[0]), Path(argv[1])
        args = parser().parse_args(argv[2:])
        return execute(candidate, native, args)
    except (CandidateBlocked, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "qualification": QUALIFICATION,
                          "production_activation": False,
                          "reason": str(exc)[:400]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
