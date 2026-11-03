#!/usr/bin/env python3
"""#256 R3: opt-in native-v4 Host Runtime install and bounded handoff.

Reuses the already-installed vNext Attach receipt, Host Prepare and package.
No new persistence, lease, Task authority, implicit model launch, or recovery.
A real Main dispatch requires a separately supplied #291 trusted Host object.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Attach imports Host Prepare and ignore by sibling module name. The immutable
# package carries all three sources and the existing Main/Session executables.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kagari_vnext_attach as attach
if __package__:
    from . import kagari_vnext_main_service as main_service
else:
    import kagari_vnext_main_service as main_service


PROFILE = "native-v4-linked-worktree"
OPERATIONS = ("plan", "doctor", "install", "repair", "uninstall")


class RuntimeBlocked(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(ok: bool, code: str) -> None:
    if not ok:
        raise RuntimeBlocked(code)


def executable(package_root: Path) -> str:
    """Inspect the actual packaged Main executable, not a configured script.

    Existing Attach owns package identity checks and the installed receipt;
    this is only the missing native Main binary completeness check.
    """
    binary = package_root / "bin" / "kagari-vnext-main"
    require(binary.is_file(), "RUNTIME_MAIN_UNAVAILABLE")
    attach.package_identity(package_root, include_main=True)
    return str(binary)


def operate(project: Path, state_home: Path, package_root: Path,
            operation: str, profile: str = PROFILE) -> dict:
    require(profile == PROFILE, "RUNTIME_PROFILE_UNSUPPORTED")
    require(operation in OPERATIONS, "RUNTIME_OPERATION_UNSUPPORTED")
    # Uninstall still works when only the extra Main executable is missing:
    # the existing Attach owns its precise receipt/uninstall eligibility.
    main_binary = executable(package_root) if operation != "uninstall" else None
    result = attach.operate(project, state_home, package_root, operation,
                            attach.PROFILE)
    installed = (operation != "uninstall"
                 and result["host_attached"]
                 and result["status"] in ("ATTACHED", "READY"))
    return {
        **result,
        "profile": PROFILE,
        "attachment_profile": attach.PROFILE,
        "runtime_package_installed": installed,
        "runtime_installed": installed,
        "runtime_binary": main_binary if installed else None,
        "runtime_running": "NOT_OBSERVED",  # No Main liveness probe here.
        "main_started": False,
        "model_or_session_started": False,
        "task_execution_authorized": False,
        "new_runtime_state_created": False,
    }


def launch_with_host(
    project: Path, state_home: Path, package_root: Path, plan: Path, *,
    host: main_service.MainHostCapabilities,
    follow: bool = False,
    interval: int = 30,
) -> int:
    """Launch the already-existing Main inside this caller's trusted Host.

    No CLI flag or plan file creates MainHostCapabilities. Main itself enforces
    the bounded Issue/Contract/Worktree and original #194 effect authority.
    Ownership is delegated to PR #330 attached_supervisor, never a new lease.
    """
    require(type(host) is main_service.MainHostCapabilities,
            "RUNTIME_TRUSTED_HOST_REQUIRED")
    require(type(follow) is bool and type(interval) is int
            and 2 <= interval <= 3600, "RUNTIME_INTERVAL_INVALID")
    plan_data = main_service.load_selection(plan)
    require(plan_data["project_root"] == str(project),
            "RUNTIME_PROJECT_PLAN_MISMATCH")
    status = operate(project, state_home, package_root, "doctor")
    require(status["runtime_installed"] and status["status"] == "READY",
            "RUNTIME_NOT_INSTALLED")
    argv = ["--config", str(plan), "--interval", str(interval)]
    if follow:
        argv.append("--follow")
    argv.append("dispatch")
    return main_service.main(
        argv, host=host,
        attachment=main_service.MainAttachmentConfig(
            state_home=state_home, package_root=package_root),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--state-home", required=True, type=Path)
    parser.add_argument("--package-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        status = operate(args.project_root, args.state_home, args.package_root,
                         args.operation, args.profile)
    except (RuntimeBlocked, attach.Blocked,
            attach.host.ignore.IgnoreBlocked, main_service.MainError,
            OSError, ValueError, UnicodeError, RuntimeError) as exc:
        print(json.dumps({
            "operation": args.operation, "status": "BLOCKED",
            "code": getattr(exc, "code", "RUNTIME_CONTEXT_UNAVAILABLE"),
            "runtime_installed": False,
            "task_execution_authorized": False,
            "main_started": False,
        }, sort_keys=True), flush=True)
        return 2
    print(json.dumps(status, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
