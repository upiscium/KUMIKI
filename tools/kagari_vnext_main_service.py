#!/usr/bin/env python3
"""KAGARI #291: opt-in repository Main host runner.

One active local Main per ordinary Git repository, and an explicit finite
Issue selection. Durable Task truth remains #191/Git/GitHub; this module
owns no extra semantic/permission database or independent Ask broker.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import threading
from typing import TYPE_CHECKING, Any, Callable

if __package__:
    from .kagari_vnext_main import (
        ApprovedTask, InitialCollaboration, MainError, RepositoryMain, github_issue_reader)
else:
    from kagari_vnext_main import (
        ApprovedTask, InitialCollaboration, MainError, RepositoryMain, github_issue_reader)

if TYPE_CHECKING:
    from .kagari_vnext_main_v2_observation import V2ReadOnlyMainHost

try:
    from .kagari_vnext_executor_selection import (
        CLAUDE_CLI, OPENCODE, is_executor_name,
    )
except ImportError:
    from kagari_vnext_executor_selection import (
        CLAUDE_CLI, OPENCODE, is_executor_name,
    )

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
import sys
sys.path.insert(0, str(V4))
from metadata_ref import MetadataStore, MetadataRefError
from task_record import validate_branch_ref, TaskRecordError
from task_view import EvidenceRef
from issue_worktree import IssueWorktreeAllocator, IssueWorktreeError
from issue_worktree_cleanup import (
    CleanupReceipt, IssueWorktreeCleanup, github_pull_request_reader,
    stopped_session_from_runtime,
)
if __package__:
    from .kagari_vnext_progress import github_pr_reader
    from . import kagari_vnext_attachment_gate as attachment_gate
else:
    from kagari_vnext_progress import github_pr_reader
    import kagari_vnext_attachment_gate as attachment_gate


def _exact_dir(path: Path, name: str) -> Path:
    try:
        if (not path.is_absolute() or not path.is_dir()
                or path.is_symlink() or path.resolve(strict=True) != path):
            raise MainError(name)
    except (OSError, RuntimeError):
        raise MainError(name) from None
    return path


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _validate_selection(data: Any, *, legacy_python: bool = False) -> dict[str, Any]:
    """Validate the closed plan schema for both file and Python callers."""
    # Existing in-process hosts supplied a mapping rather than a serialized
    # plan and omitted its version tag. Preserve that API, but not for files;
    # all executor/task fields still pass the same closed-schema validation.
    if legacy_python and type(data) is dict and "schema_version" not in data:
        data = {"schema_version": 1, **data}
    required = {"schema_version", "project_root", "repository", "runtime_home", "tasks"}
    optional = {"remote", "agent", "model", "opencode_bin", "github_bin"}
    if (type(data) is not dict or data.keys() - required - optional
            or not required <= data.keys()
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1):
        raise MainError("MAIN_PLAN_INVALID")
    if (type(data["repository"]) is not str or not re.fullmatch(
            r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", data["repository"])):
        raise MainError("MAIN_PLAN_INVALID")
    for name in ("project_root", "runtime_home"):
        if type(data[name]) is not str or not Path(data[name]).is_absolute():
            raise MainError("MAIN_PLAN_INVALID")
    for name in ("remote", "agent", "opencode_bin", "github_bin"):
        if name in data and (type(data[name]) is not str or not data[name]):
            raise MainError("MAIN_PLAN_INVALID")
    model = data.get("model")
    if (model is not None and (
            type(model) is not dict
            or model.keys() != {"provider_id", "model_id"}
            or any(type(model[k]) is not str or not model[k]
                   for k in ("provider_id", "model_id")))):
        raise MainError("MAIN_PLAN_INVALID")
    tasks = data["tasks"]
    if type(tasks) is not list or not 1 <= len(tasks) <= 25:
        raise MainError("MAIN_PLAN_INVALID")
    seen_issues: set[int] = set()
    seen_ports: set[int] = set()
    for item in tasks:
        required_task = {
            "issue", "branch_ref", "base_revision", "contract_id"}
        if (type(item) is not dict or not required_task <= item.keys()
                or item.keys() - required_task - {"executor", "port", "pr_number",
                                                 "evidence", "cleanup"}):
            raise MainError("MAIN_PLAN_INVALID")
        executor = item.get("executor", OPENCODE)
        if not is_executor_name(executor):
            raise MainError("MAIN_PLAN_INVALID")
        number = item["issue"]
        if (type(number) is not int or not 1 <= number <= 10**9
                or number in seen_issues):
            raise MainError("MAIN_PLAN_INVALID")
        if executor == OPENCODE:
            port = item.get("port")
            if ("port" not in item or type(port) is not int
                    or not 1024 <= port <= 65535 or port in seen_ports):
                raise MainError("MAIN_PLAN_INVALID")
            seen_ports.add(port)
        elif "port" in item or "cleanup" in item:
            raise MainError("MAIN_PLAN_INVALID")
        if (type(item["base_revision"]) is not str or
                not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})",
                                 item["base_revision"])):
            raise MainError("MAIN_PLAN_INVALID")
        if (type(item["contract_id"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", item["contract_id"])):
            raise MainError("MAIN_PLAN_INVALID")
        try:
            validate_branch_ref(item["branch_ref"])
        except (TaskRecordError, TypeError):
            raise MainError("MAIN_PLAN_INVALID") from None
        if "pr_number" in item and (
                type(item["pr_number"]) is not int
                or item["pr_number"] < 1):
            raise MainError("MAIN_PLAN_INVALID")
        evidence = item.get("evidence", [])
        if type(evidence) is not list or len(evidence) > 64:
            raise MainError("MAIN_PLAN_INVALID")
        for evidence_ref in evidence:
            if (type(evidence_ref) is not dict
                    or evidence_ref.keys() != {"evidence_id", "subject"}
                    or type(evidence_ref["evidence_id"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}",
                                        evidence_ref["evidence_id"])
                    or type(evidence_ref["subject"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",
                                        evidence_ref["subject"])):
                raise MainError("MAIN_PLAN_INVALID")
        # Cleanup is an explicit Issue-specific lifecycle decision, not an
        # implicit consequence of observing any merged PR or idle Session.
        cleanup = item.get("cleanup")
        if cleanup is not None:
            if (type(cleanup) is not dict
                    or cleanup.keys() != {"pull_request", "base_ref"}
                    or type(cleanup["pull_request"]) is not int
                    or cleanup["pull_request"] < 1
                    or type(cleanup["base_ref"]) is not str
                    or cleanup["base_ref"] == item["branch_ref"]):
                raise MainError("MAIN_PLAN_INVALID")
            try:
                validate_branch_ref(cleanup["base_ref"])
            except TaskRecordError:
                raise MainError("MAIN_PLAN_INVALID") from None
            if (item.get("pr_number") is not None
                    and item["pr_number"] != cleanup["pull_request"]):
                raise MainError("MAIN_PLAN_INVALID")
        seen_issues.add(number)
    return data


def load_selection(path: Path) -> dict[str, Any]:
    """Trusted operator's explicit Issue selection, never an authority mint."""
    try:
        if (not path.is_absolute() or path.is_symlink()
                or not path.is_file() or path.stat().st_size > 64_000):
            raise ValueError("invalid plan file")
        data = json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise MainError("MAIN_PLAN_INVALID") from None
    return _validate_selection(data)


@dataclass(frozen=True)
class MainHostCapabilities:
    """In-process capabilities installed by the trusted execution host only.

    This is a typed handoff, NOT a new grant or an authorization registry.
    The provider must return the existing #194 facade bound to the requested
    registered Worktree, with its real TaskGit, GitHubTransport, prerequisites,
    authorization, WriteIntent/content policy and retry-safe #217 publisher.
    Main validates that binding and #194 alone owns collaboration effects.
    No plan field, environment variable or dynamically loaded source installs
    this object. The default packaged CLI deliberately has no provider.
    """

    read_issue: Callable[[int], dict]
    initial_collaboration_for_issue: Callable[[int, Path], InitialCollaboration]

    def __post_init__(self) -> None:
        if (not callable(self.read_issue)
                or not callable(self.initial_collaboration_for_issue)):
            raise MainError("MAIN_HOST_CAPABILITIES_INVALID")


@dataclass(frozen=True)
class MainV2ReadOnlyCapabilities:
    """Host-selected v2 read-only lane; never a new Main or Task grant."""

    read_issue: Callable[[int], dict]
    observers: dict[int, V2ReadOnlyMainHost]

    def __post_init__(self) -> None:
        # Same delayed optional import as Main, to avoid #337's bootstrap cycle.
        if __package__:
            from .kagari_vnext_main_v2_observation import V2ReadOnlyMainHost
        else:
            from kagari_vnext_main_v2_observation import V2ReadOnlyMainHost
        if (not callable(self.read_issue) or type(self.observers) is not dict
                or not self.observers
                or any(type(n) is not int
                       or type(observer) is not V2ReadOnlyMainHost
                       or observer.selection.issue != n
                       for n, observer in self.observers.items())):
            raise MainError("V2_MAIN_HOST_INVALID")


@dataclass(frozen=True)
class MainV2WorktreeCapabilities:
    """Host-only R5A1 capability: exact Issue-scoped local Git admission.

    No plan field or CLI action creates this object; a separate Worktree
    operation grant remains mandatory even if R5A0 observation was approved.
    """
    read_issue: Callable[[int], dict]
    admissions: dict[int, object]

    def __post_init__(self) -> None:
        if __package__:
            from .kagari_vnext_main_v2_worktree import V2MainWorktreeHost
        else:
            from kagari_vnext_main_v2_worktree import V2MainWorktreeHost
        if (not callable(self.read_issue) or type(self.admissions) is not dict
                or not self.admissions
                or any(type(issue) is not int
                       or type(host) is not V2MainWorktreeHost
                       or host.readonly.selection.issue != issue
                       for issue, host in self.admissions.items())):
            raise MainError("V2_MAIN_WORKTREE_HOST_INVALID")


@dataclass(frozen=True)
class MainV2SessionReadOnlyCapabilities:
    """Host-only R5B0 current-v2 Session/Ask observation; no reply/launch."""

    read_issue: Callable[[int], dict]
    sessions: dict[int, object]

    def __post_init__(self) -> None:
        if __package__:
            from .kagari_vnext_main_v2_session_observation import V2MainSessionReadOnlyHost
        else:
            from kagari_vnext_main_v2_session_observation import V2MainSessionReadOnlyHost
        if (not callable(self.read_issue) or type(self.sessions) is not dict
                or not self.sessions
                or any(type(issue) is not int
                       or type(host) is not V2MainSessionReadOnlyHost
                       or host.readonly.selection.issue != issue
                       for issue, host in self.sessions.items())):
            raise MainError("V2_SESSION_HOST_INVALID")


@dataclass(frozen=True)
class MainV2PrFirstReadOnlyCapabilities:
    """Host-only R5C0 PR-first proof, no GitHub effects/Task launch."""

    read_issue: Callable[[int], dict]
    proofs: dict[int, object]

    def __post_init__(self) -> None:
        if __package__:
            from .kagari_vnext_main_v2_pr_first_observation import V2PrFirstReadOnlyHost
        else:
            from kagari_vnext_main_v2_pr_first_observation import V2PrFirstReadOnlyHost
        if (not callable(self.read_issue) or type(self.proofs) is not dict
                or not self.proofs
                or any(type(issue) is not int
                       or type(host) is not V2PrFirstReadOnlyHost
                       or host.readonly.selection.issue != issue
                       for issue, host in self.proofs.items())):
            raise MainError("R5C0_HOST_INVALID")


@dataclass(frozen=True)
class MainV2ProductPrCapabilities:
    """Host-only R5C1A publication; no CLI/Session/Metadata writer."""

    read_issue: Callable[[int], dict]
    publications: dict[int, object]

    def __post_init__(self) -> None:
        if __package__:
            from .kagari_vnext_main_v2_pr_first_publication import V2ProductPrFirstHost
        else:
            from kagari_vnext_main_v2_pr_first_publication import V2ProductPrFirstHost
        if (not callable(self.read_issue) or type(self.publications) is not dict
                or not self.publications
                or any(type(issue) is not int
                       or type(host) is not V2ProductPrFirstHost
                       or host.readonly.selection.issue != issue
                       for issue, host in self.publications.items())):
            raise MainError("R5C1A_HOST_INVALID")


@dataclass(frozen=True)
class MainV2InitialCheckpointCapabilities:
    """Explicit R5C1B1 Host-only initial publisher, never a plan/CLI grant."""

    read_issue: Callable[[int], dict]
    publications: dict[int, object]

    def __post_init__(self) -> None:
        if __package__:
            from .kagari_vnext_main_v2_initial_checkpoint import V2InitialCheckpointHost
        else:
            from kagari_vnext_main_v2_initial_checkpoint import V2InitialCheckpointHost
        if (not callable(self.read_issue) or type(self.publications) is not dict
                or not self.publications
                or any(type(issue) is not int
                       or type(host) is not V2InitialCheckpointHost
                       or host.readonly.selection.issue != issue
                       for issue, host in self.publications.items())):
            raise MainError("R5C1B1_HOST_INVALID")


def build_main(
    plan: dict[str, Any], *,
    host: MainHostCapabilities | MainV2ReadOnlyCapabilities
          | MainV2WorktreeCapabilities | MainV2SessionReadOnlyCapabilities
          | MainV2PrFirstReadOnlyCapabilities
          | MainV2ProductPrCapabilities | MainV2InitialCheckpointCapabilities | None = None,
) -> RepositoryMain:
    plan = _validate_selection(plan, legacy_python=True)
    if host is not None and type(host) not in (
            MainHostCapabilities, MainV2ReadOnlyCapabilities,
            MainV2WorktreeCapabilities, MainV2SessionReadOnlyCapabilities,
            MainV2PrFirstReadOnlyCapabilities, MainV2ProductPrCapabilities,
            MainV2InitialCheckpointCapabilities):
        raise MainError("MAIN_HOST_CAPABILITIES_INVALID")
    _exact_dir(Path(plan["project_root"]), "PRIMARY_PROJECT_REQUIRED")
    try:
        store = MetadataStore(
            Path(plan["project_root"]), plan["repository"],
            plan.get("remote", "origin"))
    except (MetadataRefError, TypeError, ValueError):
        raise MainError("MAIN_PROJECT_INVALID") from None
    approved = {
        item["issue"]: ApprovedTask(
            item["issue"], item["branch_ref"], item["base_revision"],
            item["contract_id"], item.get("executor", OPENCODE))
        for item in plan["tasks"]
    }
    ports = {item["issue"]: item["port"] for item in plan["tasks"]
             if item.get("executor", OPENCODE) == OPENCODE}
    configured_model = plan.get("model")
    model = (
        (configured_model["provider_id"], configured_model["model_id"])
        if configured_model is not None else None
    )
    pr_for_issue = {
        item["issue"]: item["pr_number"]
        for item in plan["tasks"] if "pr_number" in item}
    evidence_for_issue = {
        item["issue"]: tuple(EvidenceRef(**v) for v in item["evidence"])
        for item in plan["tasks"] if item.get("evidence")}
    return RepositoryMain(
        store, approved=approved,
        read_issue=(host.read_issue if host is not None else github_issue_reader(
            plan["repository"], gh=plan.get("github_bin", "gh"))),
        runtime_home=Path(plan["runtime_home"]),
        port_for_issue=ports.__getitem__,
        opencode_bin=plan.get("opencode_bin", "opencode"),
        agent=plan.get("agent", "build"), model=model,
        pr_for_issue=pr_for_issue, evidence_for_issue=evidence_for_issue,
        read_pr=(github_pr_reader(
            plan["repository"], gh=plan.get("github_bin", "gh"))
            if pr_for_issue else None),
        initial_collaboration_for_issue=(
            host.initial_collaboration_for_issue
            if type(host) is MainHostCapabilities else None),
        v2_readonly_for_issue=(
            host.observers if type(host) is MainV2ReadOnlyCapabilities
            else {issue: admission.readonly
                  for issue, admission in host.admissions.items()}
            if type(host) is MainV2WorktreeCapabilities
            else {issue: session.readonly
                  for issue, session in host.sessions.items()}
            if type(host) is MainV2SessionReadOnlyCapabilities
            else {issue: proof.readonly
                  for issue, proof in host.proofs.items()}
            if type(host) is MainV2PrFirstReadOnlyCapabilities
            else {issue: pub.readonly
                  for issue, pub in host.publications.items()}
            if type(host) in (MainV2ProductPrCapabilities,
                             MainV2InitialCheckpointCapabilities) else None),
        v2_worktree_for_issue=(
            host.admissions if type(host) is MainV2WorktreeCapabilities else None),
        v2_session_for_issue=(
            host.sessions if type(host) is MainV2SessionReadOnlyCapabilities else None),
        v2_pr_first_for_issue=(
            host.proofs if type(host) is MainV2PrFirstReadOnlyCapabilities else None),
        v2_product_pr_for_issue=(
            host.publications if type(host) is MainV2ProductPrCapabilities else None),
        v2_initial_checkpoint_for_issue=(
            host.publications if type(host) is MainV2InitialCheckpointCapabilities else None),
    )


def build_cleanup_handlers(
    main: RepositoryMain, plan: dict[str, Any],
) -> dict[int, Callable[[], CleanupReceipt]]:
    """Explicit per-Issue retirements, never implicit from an open/merged PR.

    The authoritative #286 cleanup validates the exact #191 Contract, PR
    merge ancestry, registered Worktree, clean files and stopped Session.
    Main only routes independently authorized Issue selections to it.
    """
    if type(main) is not RepositoryMain or type(plan) is not dict:
        raise MainError("MAIN_CLEANUP_CONFIGURATION_INVALID")
    items = plan.get("tasks", [])
    if type(items) is list:
        for item in items:
            if type(item) is dict:
                issue = item.get("issue")
                if (type(issue) is int
                        and issue in main.v2_readonly_for_issue
                        and item.get("cleanup") is not None):
                    raise MainError("V2_MAIN_CLEANUP_NOT_QUALIFIED")
                executor_name = item.get("executor", OPENCODE)
                if (type(executor_name) is str and executor_name == CLAUDE_CLI
                        and "cleanup" in item):
                    raise MainError("MAIN_CLEANUP_CONFIGURATION_INVALID")
    plan = _validate_selection(plan, legacy_python=True)
    handlers: dict[int, Callable[[], CleanupReceipt]] = {}
    for item in plan.get("tasks", []):
        policy = item.get("cleanup")
        issue = item["issue"]
        if issue in main.v2_readonly_for_issue:
            if policy is not None:
                raise MainError("V2_MAIN_CLEANUP_NOT_QUALIFIED")
            continue
        choice = main.approved.get(issue)
        if choice is None:
            raise MainError("MAIN_CLEANUP_CONFIGURATION_INVALID")
        selected_executor = main._selected_executor(issue)
        if (selected_executor != item.get("executor", OPENCODE)
                or choice.branch_ref != item["branch_ref"]
                or choice.base_revision != item["base_revision"]
                or choice.contract_id != item["contract_id"]):
            raise MainError("MAIN_CLEANUP_CONFIGURATION_INVALID")
        if policy is None:
            continue
        if selected_executor == CLAUDE_CLI:
            raise MainError("MAIN_CLEANUP_CONFIGURATION_INVALID")
        backend = IssueWorktreeCleanup(
            IssueWorktreeAllocator(main.store),
            read_pull_request=github_pull_request_reader(
                main.store.repository, gh=plan.get("github_bin", "gh")),
            session_released=stopped_session_from_runtime(
                main._runtime(issue)),
        )

        def cleanup_one(*, selected=choice, selected_name=selected_executor,
                        executor=backend, request=policy) -> CleanupReceipt:
            # The returned callable can also be invoked directly by a trusted
            # host. It must not bypass the same-repository Main singleton.
            # Hold the guard across the *entire* potentially destructive #286
            # cleanup, not just its first preflight check.
            with main._main_owner_guard:
                owner = main._main_owner
                if owner is None:
                    raise MainError("MAIN_OWNER_REQUIRED")
                owner._assert_owned(main)
                current_name = main._selected_executor(selected.issue)
                if current_name != selected_name or current_name != OPENCODE:
                    raise MainError("MAIN_CLEANUP_CONFIGURATION_INVALID")
                return executor.cleanup(
                    task=str(selected.issue),
                    base_revision=selected.base_revision,
                    branch_ref=selected.branch_ref,
                    expected_contract_id=selected.contract_id,
                    pull_request=request["pull_request"],
                    base_ref=request["base_ref"])

        handlers[issue] = cleanup_one
    return handlers


def _host_state_home() -> Path:
    """User-owned, stable across checkouts and independent of HOME/XDG overrides.

    This host identity is cooperative one-host/UID isolation, not multi-host
    election or exclusion of a malicious process running as this same UID.
    """
    owner_home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    # Revalidate the passwd trust anchor and *every* namespace ancestor on
    # each call, not just during first-run bootstrap. Never derive this lease
    # from HOME, XDG_STATE_HOME, a checkout or Task configuration.
    _owned_directory(owner_home, private=False)
    home = owner_home / ".local" / "state"
    for ancestor in (home.parent, home):
        try:
            ancestor.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE") from None
        # Validate before creating the next component: an unsafe existing
        # parent must never receive a newly bootstrapped child.
        _owned_directory(ancestor, private=False)
    return home


def _owned_directory(path: Path, *, private: bool) -> None:
    """Fail closed on foreign, symlinked or permission-unsafe lock namespace."""
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE")
        st = path.lstat()
    except (OSError, RuntimeError):
        raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE") from None
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
            or st.st_mode & 0o022
            or (private and st.st_mode & 0o077)):
        raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE")


def _host_repo_lock(repository: str) -> Path:
    """One stable lock inode for each GitHub repository on the supported host.

    A per-checkout .git lock is insufficient: two different local clones of
    the same upstream would otherwise both start a Main. The trusted host
    namespace uses an owner-only directory, not an operator-selected path.
    """
    if (type(repository) is not str
            or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository)):
        raise MainError("MAIN_REPOSITORY_INVALID")
    home = _host_state_home()
    _owned_directory(home, private=False)
    for ancestor in (home / "kagari", home / "kagari" / "repository-main-locks"):
        try:
            ancestor.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE") from None
        _owned_directory(ancestor, private=True)
    key = hashlib.sha256(
        ("github.com/" + repository.lower()).encode("ascii")).hexdigest()
    return home / "kagari" / "repository-main-locks" / (key + ".lock")


def _verify_lock_file(path: Path, fd: int, error: str) -> None:
    """The visible empty lease must still be the exact open, safe inode."""
    current = path.lstat()
    original = os.fstat(fd)
    if ((current.st_dev, current.st_ino) != (original.st_dev, original.st_ino)
            or any(not stat.S_ISREG(st.st_mode)
                   or st.st_uid != os.geteuid() or st.st_nlink != 1
                   or st.st_mode & 0o177 or st.st_size != 0
                   for st in (current, original))):
        raise MainError(error)


class MainSupervisor:
    """One local active Main, with host-owned lock held across run cycles."""

    def __init__(
        self, main: RepositoryMain, *,
        cleanup_for_issue: dict[int, Callable[[], CleanupReceipt]] | None = None,
        observe_only: bool = False,
        attachment_lease: attachment_gate.AttachmentLease | None = None,
        admission_only: bool = False,
        publication_only: bool = False,
    ) -> None:
        requested = dict(cleanup_for_issue or {})
        if type(admission_only) is not bool:
            raise MainError("V2_MAIN_ADMISSION_CONFIGURATION_INVALID")
        if admission_only and (
                publication_only or observe_only or requested or type(main) is not RepositoryMain
                or not main.v2_worktree_for_issue or attachment_lease is None
                or any(host.readonly.lease is not attachment_lease
                       for host in main.v2_worktree_for_issue.values())):
            raise MainError("V2_MAIN_ADMISSION_CONFIGURATION_INVALID")
        if type(publication_only) is not bool:
            raise MainError("R5C1A_OWNER_CONFIGURATION_INVALID")
        publications = ((main.v2_product_pr_for_issue or main.v2_initial_checkpoint_for_issue)
                        if type(main) is RepositoryMain else {})
        if publication_only and (
                observe_only or admission_only or requested
                or type(main) is not RepositoryMain
                or not publications
                or attachment_lease is None
                or any(host.readonly.lease is not attachment_lease
                       for host in publications.values())):
            raise MainError("R5C1A_OWNER_CONFIGURATION_INVALID")
        if (type(observe_only) is not bool
                or any(n not in main.approved or not callable(f)
                       for n, f in requested.items())):
            raise MainError("MAIN_CLEANUP_CONFIGURATION_INVALID")
        if (attachment_lease is not None
                and (observe_only or not (
                    type(attachment_lease) is attachment_gate.AttachmentLease
                    or (admission_only and main.v2_worktree_for_issue
                        and attachment_lease is next(iter(
                            main.v2_worktree_for_issue.values())).readonly.lease)
                    or (publication_only and publications
                        and attachment_lease is next(iter(
                            publications.values())).readonly.lease)))):
            raise MainError("MAIN_ATTACHMENT_BINDING_INVALID")
        self._attachment_lease = attachment_lease
        self.main = main
        self.cleanup_for_issue = requested
        self.observe_only = observe_only
        self.admission_only = admission_only
        self.publication_only = publication_only
        self._entered = False
        self._file = None
        self._lock_path: Path | None = None
        self._owner_pid: int | None = None
        self._owner_repository: str | None = None
        # Prevent same-thread callback reentry from unlocking the canonical
        # repository flock during a #291 initial metadata/comment effect.
        self._initial_publish_active = 0
        self._initial_publish_issue: int | None = None
        self._initial_publish_host: object | None = None
        self._initial_publish_entered = False

    def _assert_owned(self, main: RepositoryMain) -> None:
        """Prove this exact Main still holds the canonical host flock.

        A bare Boolean/PID or a saved runtime JSON cannot grant dispatch.
        Check the original open file description, verified inode and live PID.
        Re-flocking the same descriptor succeeds only while ownership is
        available; a foreign lock or replaced path fails closed.
        Called under main._main_owner_guard for the entire dispatch/cycle.
        """
        if (type(main) is not RepositoryMain or self.main is not main
                or not self._entered or self.observe_only
                or main._main_owner is not self
                or self._file is None or self._file.closed
                or self._owner_pid != os.getpid()
                or self._owner_repository != main.store.repository
                or self._lock_path is None):
            raise MainError("MAIN_OWNER_REQUIRED")
        try:
            expected = _host_repo_lock(main.store.repository)
            if self._lock_path != expected:
                raise MainError("MAIN_OWNER_UNVERIFIED")
            _verify_lock_file(expected, self._file.fileno(), "MAIN_OWNER_UNVERIFIED")
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if _host_repo_lock(main.store.repository) != expected:
                raise MainError("MAIN_OWNER_UNVERIFIED")
            _verify_lock_file(expected, self._file.fileno(), "MAIN_OWNER_UNVERIFIED")
        except (OSError, RuntimeError):
            raise MainError("MAIN_OWNER_UNVERIFIED") from None
        self._verify_attachment()

    def _verify_attachment(self) -> None:
        """Never allow a bound host effect to outrun the pinned Attach receipt."""
        lease = self._attachment_lease
        if lease is None:
            return  # Existing independently authorized Main mode is unchanged.
        try:
            lease.verify(project=self.main.store.root)
        except (attachment_gate.GateBlocked,
                attachment_gate.attach.Blocked,
                attachment_gate.attach.host.HostBlocked,
                attachment_gate.attach.host.ignore.IgnoreBlocked,
                OSError, ValueError, RuntimeError):
            raise MainError("MAIN_ATTACHMENT_UNVERIFIED") from None

    def __enter__(self) -> MainSupervisor:
        if self._entered:
            raise MainError("MAIN_ALREADY_ACTIVE")
        if (not self.observe_only and not self.admission_only
                and not self.publication_only
                and type(self.main) is RepositoryMain
                and self.main.v2_readonly_for_issue):
            raise MainError("V2_MAIN_EXECUTION_NOT_QUALIFIED")
        self._verify_attachment()
        if self.observe_only:
            # Observation is a reader: no repository lock file, no exclusive
            # owner and no source/Task lifecycle side effect. Multiple
            # observers may coexist with a Main dispatch owner.
            self._entered = True
            return self
        project = _exact_dir(self.main.store.root, "PRIMARY_PROJECT_REQUIRED")
        _exact_dir(project / ".git", "PRIMARY_GIT_DIRECTORY_REQUIRED")
        path = _host_repo_lock(self.main.store.repository)
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError:
            raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE") from None
        self._file = os.fdopen(fd, "rb+")
        try:
            _verify_lock_file(path, fd, "MAIN_LOCK_NAMESPACE_UNSAFE")
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Do not publish ownership if the path, content or namespace
            # changed between validation/open and flock acquisition.
            if _host_repo_lock(self.main.store.repository) != path:
                raise MainError("MAIN_LOCK_NAMESPACE_UNSAFE")
            _verify_lock_file(path, fd, "MAIN_LOCK_NAMESPACE_UNSAFE")
            if type(self.main) is RepositoryMain:
                with self.main._main_owner_guard:
                    if self.main._main_owner is not None:
                        raise MainError("MAIN_ALREADY_ACTIVE")
                    self._owner_pid = os.getpid()
                    self._owner_repository = self.main.store.repository
                    self._lock_path = path
                    self._entered = True
                    self.main._main_owner = self
            else:
                # Minimal lock-only host fixture cannot dispatch a Task, but
                # it still owns an OS lock and must be fork-safe on release.
                self._entered = True
                self._owner_pid = os.getpid()
                self._owner_repository = self.main.store.repository
                self._lock_path = path
        except MainError:
            self._file.close()
            self._file = None
            raise
        except (OSError, BlockingIOError):
            self._file.close()
            self._file = None
            raise MainError("MAIN_ALREADY_ACTIVE") from None
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._owner_pid is not None and os.getpid() != self._owner_pid:
            # flock is bound to the *open file description*: a child after
            # fork() inherits it, so LOCK_UN here would unlock the parent's
            # active Main. Close ONLY this child's duplicate; never LOCK_UN.
            # Avoid the inherited RLock too: it may belong to a vanished
            # thread in a multi-threaded parent process.
            if self._file is not None:
                self._file.close()
                self._file = None
            if type(self.main) is RepositoryMain and self.main._main_owner is self:
                self.main._main_owner = None
            self._entered = False
            self._lock_path = None
            self._owner_pid = None
            self._owner_repository = None
            return

        def release() -> None:
            if type(self.main) is RepositoryMain and self.main._main_owner is self:
                self.main._main_owner = None
            self._entered = False
            self._lock_path = None
            self._owner_pid = None
            self._owner_repository = None
            if self._file is not None:
                try:
                    fcntl.flock(self._file, fcntl.LOCK_UN)
                finally:
                    self._file.close()
                    self._file = None
        if type(self.main) is RepositoryMain:
            with self.main._main_owner_guard:
                if self._initial_publish_active:
                    # The field may survive an asynchronous interrupt in the
                    # publishing frame's *own finally*. Reject a callback's
                    # reentrant exit while that frame is truly still on this
                    # thread's stack, but recover a stale flag after unwind.
                    frame = sys._getframe()
                    try:
                        while frame is not None:
                            if (frame.f_code is
                                    RepositoryMain.publish_v2_initial_checkpoint.__code__
                                    and frame.f_locals.get("self") is self.main):
                                raise MainError("R5C1B1_PUBLICATION_ACTIVE")
                            frame = frame.f_back
                    finally:
                        del frame
                    # No live publication frame remains on the releasing
                    # thread, and the RLock excludes any other effect here.
                    self._initial_publish_active = 0
                    self._initial_publish_entered = False
                    self._initial_publish_issue = None
                    self._initial_publish_host = None
                release()
        else:
            release()

    def admit_worktree(self, issue: int) -> dict[str, Any]:
        """No cycle/dispatch/Session: only a Main-owned #337 Worktree effect."""
        if not self.admission_only or not self._entered:
            raise MainError("V2_MAIN_ADMISSION_OWNER_REQUIRED")
        with self.main._main_owner_guard:
            self._assert_owned(self.main)
            return self.main.admit_v2_worktree(issue)

    def publish_product_pr_first(self, issue: int) -> dict[str, Any]:
        """Host-only R5C1A effect under the existing repository owner flock."""
        if not self.publication_only or not self._entered:
            raise MainError("R5C1A_OWNER_REQUIRED")
        with self.main._main_owner_guard:
            self._assert_owned(self.main)
            return self.main.publish_v2_product_pr_first(issue)

    def publish_initial_checkpoint(self, issue: int) -> dict[str, Any]:
        """Publication-only Python lane; no cycle, Task, Session or Ask effects."""
        if not self.publication_only or not self._entered:
            raise MainError("R5C1B1_OWNER_REQUIRED")
        with self.main._main_owner_guard:
            self._assert_owned(self.main)
            return self.main.publish_v2_initial_checkpoint(issue)

    def cycle(self, *, dispatch: bool = True) -> dict[str, Any]:
        if not self._entered:
            raise MainError("MAIN_NOT_ACTIVE")
        if (self.admission_only or self.publication_only) and dispatch:
            raise MainError("V2_MAIN_EXECUTION_NOT_QUALIFIED")
        if dispatch and self.observe_only:
            raise MainError("MAIN_OBSERVE_ONLY")
        if dispatch and type(self.main) is not RepositoryMain:
            raise MainError("MAIN_OWNER_REQUIRED")
        guard = self.main._main_owner_guard if dispatch else nullcontext()
        with guard:
            if dispatch:
                self._assert_owned(self.main)
            entries = []
            for issue in sorted(self.main.approved):
                if dispatch and issue in self.cleanup_for_issue:
                    try:
                        self.main._require_executor_effects(issue)
                        receipt = self.cleanup_for_issue[issue]()
                        if type(receipt) is not CleanupReceipt:
                            raise IssueWorktreeError("CLEANUP_RESULT_INVALID")
                        entries.append({
                            "issue": issue, "operation": "cleanup",
                            "status": "ok", "cleanup_state": receipt.state,
                            "pull_request": receipt.pull_request,
                            "worktree": receipt.worktree,
                        })
                    except (IssueWorktreeError, MainError) as err:
                        # A refused or uncertain cleanup must never terminate
                        # the repository Main or dispatch this Issue again.
                        entries.append({
                            "issue": issue, "operation": "cleanup",
                            "status": "blocked", "reason": err.code,
                        })
                    except Exception:
                        # A host PR reader/session checker may itself fail
                        # unexpectedly. Fail this Issue closed, continue others.
                        entries.append({
                            "issue": issue, "operation": "cleanup",
                            "status": "blocked",
                            "reason": "CLEANUP_HOST_UNAVAILABLE",
                        })
                    continue
                try:
                    if dispatch:
                        launched = self.main.dispatch(issue)
                        entries.append({
                            "issue": issue, "status": "ok",
                            "worktree_state": launched["worktree_state"],
                            "session_state": launched["session_state"],
                            "prompt_state": launched["prompt_state"],
                            "context": launched["context"],
                            "runtime": launched["runtime"],
                        })
                    else:
                        observed = self.main.observe(issue)
                        if observed.get("status") == "blocked":
                            entries.append({
                                "issue": issue, "status": "blocked",
                                "reason": observed["reason"],
                                "observation": observed})
                        else:
                            entries.append({
                                "issue": issue, "status": "ok",
                                "observation": observed})
                except MainError as err:
                    # One blocked Task does not expand authorization or silently
                    # suppress observations of other approved Tasks.
                    entries.append({
                        "issue": issue, "status": "blocked", "reason": err.code})
            return {
                "schema_version": 1,
                "repository": self.main.store.repository,
                "operation": "main-cycle",
                "mode": "dispatch" if dispatch else "observe",
                "tasks": entries,
            }


@dataclass(frozen=True)
class MainAttachmentConfig:
    """Explicit trusted host binding, not a Task grant or plan-file field."""

    state_home: Path
    package_root: Path
    profile: str = attachment_gate.attach.PROFILE

    def __post_init__(self) -> None:
        if (not isinstance(self.state_home, Path)
                or not isinstance(self.package_root, Path)
                or self.profile != attachment_gate.attach.PROFILE):
            raise MainError("MAIN_ATTACHMENT_BINDING_INVALID")


@contextmanager
def attached_supervisor(
    main: RepositoryMain, config: MainAttachmentConfig, *,
    cleanup_for_issue: dict[int, Callable[[], CleanupReceipt]] | None = None,
):
    """Hold Attach receipt + canonical Main lease through bounded host effects.

    The existing Task/Session/PR capabilities remain mandatory. This does
    NOT install a daemon, approve an Issue or persist an active-runtime record.
    """
    if type(main) is not RepositoryMain or type(config) is not MainAttachmentConfig:
        raise MainError("MAIN_ATTACHMENT_BINDING_INVALID")
    try:
        with attachment_gate.hold_verified(
            main.store.root, config.state_home, config.package_root,
            config.profile,
        ) as lease:
            with MainSupervisor(main, cleanup_for_issue=cleanup_for_issue,
                                attachment_lease=lease) as runner:
                runner._assert_owned(main)
                yield runner
    except (attachment_gate.GateBlocked,
            attachment_gate.attach.Blocked,
            attachment_gate.attach.host.HostBlocked,
            attachment_gate.attach.host.ignore.IgnoreBlocked,
            OSError, RuntimeError):
        raise MainError("MAIN_ATTACHMENT_UNVERIFIED") from None


def main(
    argv: list[str] | None = None, *,
    host: MainHostCapabilities | MainV2ReadOnlyCapabilities
          | MainV2WorktreeCapabilities | MainV2SessionReadOnlyCapabilities
          | MainV2PrFirstReadOnlyCapabilities
          | MainV2ProductPrCapabilities | MainV2InitialCheckpointCapabilities | None = None,
    attachment: MainAttachmentConfig | None = None,
) -> int:
    """Ordinary one-cycle/observe/follow runner, optionally called by a host.

    ``host`` is Python-only dependency injection; CLI/config inputs cannot
    select a provider or turn observed Issue/PR facts into authorization.
    """
    # Refuse even an injected R5C1B1 Host before loading a CLI plan or building
    # transports. Only build_main + MainSupervisor's Python lane can use it.
    if type(host) is MainV2InitialCheckpointCapabilities:
        print(json.dumps({"status": "error", "error_code": "R5C1B1_HOST_ONLY"}))
        return 2
    parser = argparse.ArgumentParser(description="KAGARI local repository Main runner")
    parser.add_argument("--config", type=Path, required=True,
                        help="Trusted, explicitly approved Issue list; not Task authority")
    parser.add_argument("--interval", type=int, default=30,
                        help="Polling interval (seconds) for --follow, default 30")
    parser.add_argument("--follow", action="store_true",
                        help="Remain the one active Main and inspect/continue allowed Tasks")
    parser.add_argument("operation", nargs="?", choices=("dispatch", "observe"),
                        default="dispatch")
    args = parser.parse_args(argv)
    try:
        plan = load_selection(args.config)
        controller = build_main(plan, host=host)
        if type(host) is MainV2WorktreeCapabilities:
            # R5A1 is an injected Python Host operation only; no CLI mode.
            raise MainError("V2_MAIN_WORKTREE_HOST_ONLY")
        if type(host) is MainV2ProductPrCapabilities:
            raise MainError("R5C1A_HOST_ONLY")
        if (type(host) in (MainV2ReadOnlyCapabilities,
                           MainV2SessionReadOnlyCapabilities,
                           MainV2PrFirstReadOnlyCapabilities)
                and args.operation != "observe"):
            raise MainError("V2_MAIN_EXECUTION_NOT_QUALIFIED")
        if args.follow and not 2 <= args.interval <= 3600:
            raise MainError("MAIN_INTERVAL_INVALID")
        if attachment is not None and type(attachment) is not MainAttachmentConfig:
            raise MainError("MAIN_ATTACHMENT_BINDING_INVALID")
        if attachment is not None and args.operation == "observe":
            raise MainError("MAIN_ATTACHMENT_OBSERVE_ONLY_UNSUPPORTED")
        cleanup = build_cleanup_handlers(controller, plan)
        if attachment is not None and args.operation == "dispatch":
            runner_context = attached_supervisor(
                controller, attachment, cleanup_for_issue=cleanup)
        else:
            # Observe never acquires an Attach/Main lease. The ordinary CLI
            # path does not infer a Host attachment from its plan file.
            runner_context = MainSupervisor(
                controller, cleanup_for_issue=cleanup,
                observe_only=args.operation == "observe")
        with runner_context as runner:
            def emit_cycle() -> None:
                result = runner.cycle(dispatch=args.operation == "dispatch")
                print(json.dumps(result, sort_keys=True, ensure_ascii=False),
                      flush=True)

            if not args.follow:
                emit_cycle()
                return 0

            stopped = threading.Event()
            previous = signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            try:
                while not stopped.is_set():
                    emit_cycle()
                    stopped.wait(args.interval)
            except KeyboardInterrupt:
                pass
            finally:
                signal.signal(signal.SIGTERM, previous)
            return 0
    except MainError as error:
        print(json.dumps({"status": "error", "error_code": error.code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
