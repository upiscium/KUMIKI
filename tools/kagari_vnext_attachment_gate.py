#!/usr/bin/env python3
"""#256: host-only, nonexecuting attachment receipt guard.

Hold the existing Attach namespace flock across a trusted host's bounded
operation. No Task, Session, Main, Model, or executable is started or approved.
The separate canonical repository MainSupervisor lease is still mandatory.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

import kagari_vnext_attach as attach


class GateBlocked(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(value: bool, code: str) -> None:
    if not value:
        raise GateBlocked(code)


@contextmanager
def hold(project: Path, state_home: Path, package_root: Path,
         profile: str = attach.PROFILE) -> Iterator[dict[str, Any]]:
    """Pin an already-installed preview receipt while a trusted host checks it.

    Only the trusted host may wrap a separately authorized operation in this
    context. Merely entering does not authorize dispatch, a Main, or a Session.
    Uninstall/Repair using the existing Attach namespace waits until exit.
    The guard itself never creates an attachment, modifies a Project, or
    reconciles a crashed Main/Session.
    """
    require(profile == attach.PROFILE, "GATE_PROFILE_UNSUPPORTED")
    identity = attach.project_identity(project)
    home = attach.host.private_dir(state_home)
    require(not home.is_relative_to(project)
            and not project.is_relative_to(home),
            "GATE_STATE_HOME_NOT_EXTERNAL")
    package = attach.package_identity(package_root)
    require(not package_root.is_relative_to(project)
            and not project.is_relative_to(package_root)
            and not package_root.is_relative_to(home)
            and not home.is_relative_to(package_root),
            "GATE_PACKAGE_NOT_EXTERNAL")
    expected = attach.expected_state(identity, home, package)
    name = attach.state_name(identity)

    with attach.namespace(home, create=False) as namespace:
        require(namespace is not None, "GATE_ATTACHMENT_REQUIRED")
        try:
            fcntl.flock(namespace, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GateBlocked("GATE_ATTACHMENT_BUSY") from None
        try:
            def verify() -> dict[str, int]:
                attach.same_namespace(home, namespace)
                attach.inventory(namespace)
                require(attach.project_identity(project) == identity,
                        "GATE_PROJECT_CHANGED")
                require(attach.package_identity(package_root) == package,
                        "GATE_PACKAGE_CHANGED")
                require(attach.expected_state(
                    identity, attach.host.private_dir(home), package) == expected,
                    "GATE_HOST_SOURCE_CHANGED")
                receipt = attach.stored(namespace, name, expected)
                require(receipt is not None, "GATE_ATTACHMENT_REQUIRED")
                # The host-owned #301 preflight must still be effective, not
                # merely recorded as a once-valid stale install receipt.
                try:
                    prepared = attach.host.operate(project, home, "doctor", profile)
                except (attach.host.HostBlocked,
                        attach.host.ignore.IgnoreBlocked):
                    raise GateBlocked("GATE_WORKTREE_PREFLIGHT_NOT_READY") from None
                require(prepared["status"] == "READY"
                        and prepared["host_preflight_recorded"] is True
                        and prepared["ignore"]["git_ignore_effective"] is True,
                        "GATE_WORKTREE_PREFLIGHT_NOT_READY")
                return attach.stamp(receipt)

            before = verify()
            result = {
                "schema": 1, "status": "ATTACHMENT_READY_ONLY",
                "authority": "PRECONDITION_NOT_EXECUTION_AUTHORITY",
                "profile": profile, "project": identity,
                "package_root": str(package_root),
                "attachment_receipt_sha256": hashlib.sha256(
                    attach.host.canonical(expected)).hexdigest(),
                "runtime_installed": False, "main_started": False,
                "task_execution_authorized": False,
                "model_or_session_started": False,
            }
            try:
                yield result
            finally:
                require(verify() == before, "GATE_ATTACHMENT_CHANGED_DURING_USE")
        finally:
            fcntl.flock(namespace, fcntl.LOCK_UN)


def doctor(project: Path, state_home: Path, package_root: Path,
           profile: str = attach.PROFILE) -> dict[str, Any]:
    with hold(project, state_home, package_root, profile) as report:
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("doctor",))
    parser.add_argument("--profile", required=True, choices=(attach.PROFILE,))
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--state-home", required=True, type=Path)
    parser.add_argument("--package-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = doctor(args.project_root, args.state_home,
                        args.package_root, args.profile)
    except (GateBlocked, attach.Blocked, attach.host.ignore.IgnoreBlocked,
            OSError, ValueError, UnicodeError, RuntimeError) as error:
        print(json.dumps({"status": "BLOCKED",
                          "code": getattr(error, "code", "GATE_CONTEXT_UNAVAILABLE"),
                          "runtime_installed": False,
                          "task_execution_authorized": False},
                         sort_keys=True), flush=True)
        return 2
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
