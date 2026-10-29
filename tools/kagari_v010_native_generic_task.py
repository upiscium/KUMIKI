"""KAGARI 0.1.0 local *test-only* generic native multi-agent Task slice.

TaskSpec + validated plan -> one OpenCode primary / two actual concurrent native
Task leaves -> child session attribution -> bounded tests -> host-only product
commit -> native v4 Contract/Record/Evidence/Task View + durable readback.

Only for independently disposable Git consumers with explicit execution consent.
Not a production runtime, universal OS sandbox, source publication or release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tarfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import kagari_v010_basic_plan as planning
import kagari_v010_auto_planning as model_planner
import kagari_v010_general_task_spec as task_spec
import kagari_v010_agents_compat as agents_compat
import kagari_v010_human_ask as human_ask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/agent-core-v4"))
from metadata_ref import METADATA_REF, MetadataStore
from task_record import AuthorityRequest, TaskRecords, encode_contract, encode_record
from evidence import Evidence, encode_evidence
from task_view import EvidenceRef, TaskViewSnapshots

MAX_STDOUT = 8 * 1024 * 1024
MAX_EXPORT = 3 * 1024 * 1024
LEAVES = (("sub-1", "kagari-v010-leaf-a"), ("sub-2", "kagari-v010-leaf-b"))
PRIMARY = "kagari-v010-primary"


class NativeBlocked(RuntimeError):
    """The explicitly authorized local Task did not qualify."""


class AgentsCompatibilityAsk(NativeBlocked):
    """Exact, unapproved Project instruction conflict; never auto-grant."""

    def __init__(self, diagnostic: dict, detail: str | None = None):
        self.diagnostic = diagnostic
        labels = sorted({item["id"] for item in diagnostic["confirmed_conflicts"]})
        explanation = (
            "AGENTS_COMPAT_ASK_REQUIRED: " + ", ".join(labels)
            + "; inspect exact proposed AGENTS.md diff and obtain separate"
              " Human approval before any KAGARI Task execution."
        )
        if detail:
            explanation += " Existing grant invalid: " + detail[:180]
        super().__init__(explanation)


def need(ok: Any, message: str) -> None:
    if not ok:
        raise NativeBlocked(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def save(path: Path, data: Any) -> None:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)


def git_env() -> dict[str, str]:
    result = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                  GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0")
    return result


def git(root: Path, *args: str, check: bool = True, max_seconds: int = 25) -> bytes:
    cmd = ["git", "--no-optional-locks", "-C", str(root), "-c",
           "core.fsmonitor=false", *args]
    try:
        p = subprocess.run(cmd, env=git_env(), capture_output=True,
                           check=check, timeout=max_seconds)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeBlocked("Git precondition or operation failed: " + args[0]) from exc
    return p.stdout.strip()


def git_lines(root: Path, *args: str) -> list[str]:
    try:
        return sorted(os.fsdecode(v) for v in git(root, *args).split(b"\0") if v)
    except UnicodeError as exc:
        raise NativeBlocked("invalid Git path encoding") from exc


def inventory(project: Path) -> dict[str, tuple]:
    """Full Project physical inventory, including gitignored files and modes."""
    found: dict[str, tuple] = {}
    for parent, dirs, files in os.walk(project, followlinks=False):
        prefix = Path(parent)
        if prefix == project:
            need(".git" in dirs and (project / ".git").is_dir()
                 and not (project / ".git").is_symlink(),
                 "ordinary Project with real Git metadata required")
            dirs.remove(".git")
        for name in list(dirs) + list(files):
            path = prefix / name
            relative = path.relative_to(project).as_posix()
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                # Record links exactly, never traverse them.
                found[relative] = ("link", os.readlink(path), mode)
            elif stat.S_ISDIR(info.st_mode):
                found[relative] = ("dir", mode)
            elif stat.S_ISREG(info.st_mode):
                found[relative] = ("file", sha(path.read_bytes()), mode, info.st_nlink)
            else:
                raise NativeBlocked("special Project file refused: " + relative)
    return found


def git_control_inventory(project: Path) -> dict[str, tuple]:
    """Fingerprint local Git control data without reading immutable objects.

    Auto Planning runs read-only but may load Project instructions through
    OpenCode. Track changes to refs, hooks, logs, info, config and other
    control files, including files that do not affect git status. The object
    database is not authority without a changed ref/config; Git HEAD/index
    and refs are checked separately. Conservatively BLOCK special paths.
    """
    root = project / ".git"
    need(root.is_dir() and not root.is_symlink(),
         "Git control inventory requires an ordinary Git directory")
    observed: dict[str, tuple] = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        ancestor = Path(parent)
        if ancestor == root and "objects" in directories:
            directories.remove("objects")
        for name in sorted(directories + files):
            path = ancestor / name
            rel = path.relative_to(root).as_posix()
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode):
                observed[rel] = ("dir", mode)
            elif stat.S_ISREG(info.st_mode):
                need(info.st_size <= 4 * 1024 * 1024,
                     "oversized Git control file: " + rel)
                observed[rel] = ("file", sha(path.read_bytes()), mode, info.st_nlink)
            else:
                raise NativeBlocked("unsafe Git control entry: " + rel)
    alternates = root / "objects/info/alternates"
    if alternates.exists() or alternates.is_symlink():
        info = alternates.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_size <= 1024 * 1024,
             "unsupported Git object alternate authority")
        observed["objects/info/alternates"] = (
            "file", sha(alternates.read_bytes()), stat.S_IMODE(info.st_mode))
    return observed


def guard_existing(repo: Path, evidence_dir: Path) -> None:
    need(repo.is_absolute() and repo.is_dir() and repo.resolve() == repo
         and not repo.is_symlink() and repo.stat().st_uid == os.geteuid(),
         "an absolute euid-owned ordinary disposable Project is required")
    need((repo / ".git").is_dir() and not (repo / ".git").is_symlink(),
         "detached or linked Git worktree unsupported in this acceptance")
    need(evidence_dir.is_absolute() and evidence_dir.parent.is_dir()
         and evidence_dir.parent.resolve() == evidence_dir.parent
         and evidence_dir.parent.stat().st_uid == os.geteuid()
         and stat.S_IMODE(evidence_dir.parent.stat().st_mode) == 0o700
         and not evidence_dir.exists() and not evidence_dir.is_symlink(),
         "evidence output must be fresh under private euid-owned 0700 root")
    need(not repo.is_relative_to(evidence_dir)
         and not evidence_dir.is_relative_to(repo),
         "Task runtime and Project paths must be disjoint")
    need(Path(os.fsdecode(git(repo, "rev-parse", "--show-toplevel")).strip()) == repo,
         "Project must be a canonical ordinary Git top-level")


def verify_plan(spec: dict, plan: dict, *,
                host_observed_auto: bool = False) -> None:
    planning.validate_plan(spec, plan,
                           allow_optional_v2=spec.get("schema_version") == 2)
    need(spec["subagent_count"] == 2 and (
         spec["planning"] == "specified"
         or (spec["planning"] == "auto" and host_observed_auto)),
         "automatic Planning requires a host-observed, validated model Plan")
    leaves = {x["id"]: x for x in plan["steps"]}
    need(set(leaves["sub-1"]["allowed_paths"] + leaves["sub-2"]["allowed_paths"])
         == set(spec["allowed_paths"]),
         "each TaskSpec Product must have exactly one leaf owner")
    need(bool(spec["tests"]), "at least one explicitly authorized test is required")


def _role_file(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(content.encode("utf-8"))


def write_roles(project: Path, spec: dict, plan: dict, roles: Path) -> dict:
    """External OpenCode role templates; never rewrite Project AGENTS/config."""
    roles.mkdir(mode=0o700)
    agents = roles / "agents"
    agents.mkdir(mode=0o700)
    build = """---
description: KAGARI local bounded primary coordinator for two native leaves
mode: primary
permission:
  read: allow
  edit: deny
  bash: deny
  task:
    "*": deny
    kagari-v010-leaf-a: allow
    kagari-v010-leaf-b: allow
  question: deny
  webfetch: deny
  websearch: deny
  skill: deny
---
You are KAGARI's read/delegate-only primary in an authorized disposable Project.
Dispatch EXACTLY TWO native Task tool calls as ONE PARALLEL TOOL-CALL BATCH
in your FIRST action; do not call read, glob, grep, or other tools beforehand.
All required Product paths, task instructions and Project-owned instructions
are provided verbatim in the user prompt. Do NOT execute only A then B.
Both native task calls must be sent together in one assistant response, with
no intermediate wait. If impossible, report BLOCKED without executing either.
Never edit directly, run bash, commit, push, or publish. Report real evidence.
"""
    _role_file(agents / (PRIMARY + ".md"), build)
    step = {item["id"]: item for item in plan["steps"]}
    for ident, role in LEAVES:
        leaf = step[ident]
        # OpenCode's explicit path-pattern allowlist is narrower than Project
        # permission. All actual changes get independent host path verification.
        lines = ["---", "description: KAGARI local bounded native Work Unit " + ident,
                 "mode: subagent", "permission:", "  read: allow", "  edit:",
                 '    "*": deny']
        for relative in leaf["allowed_paths"]:
            lines += ["    " + json.dumps(relative) + ": allow",
                      "    " + json.dumps(str(project / relative)) + ": allow"]
        lines += ["  bash: deny", "  task: deny", "  question: deny",
                  "  external_directory: deny", "  webfetch: deny",
                  "  websearch: deny", "  skill: deny", "---",
                  "You are a real KAGARI non-interactive native OpenCode subagent.",
                  "You are limited to these exact product files: " +
                  json.dumps(leaf["allowed_paths"], ensure_ascii=False) + ".",
                  "Follow Project AGENTS.md and its normal instruction inheritance.",
                  "Do not edit Project settings, Git metadata, unrelated files,",
                  "README, run a shell, delegate again, stage, commit, push or ask.",
                  "Leaf Work Unit completion is NOT Project acceptance: the",
                  "KAGARI HOST, not this leaf, runs ALL declared TaskSpec tests,",
                  "revalidates Project scope, commits only approved Product paths,",
                  "and persists/verifies native-v4 Evidence and Task View.",
                  "Do not execute or request tests; your shell remains denied.",
                  "Apply ONLY the assigned Product edits, then use read to inspect",
                  "every changed Product file after its LAST edit.",
                  "If all assigned edits and final reads succeeded, start your",
                  "final reply EXACTLY with status: COMPLETED even though host",
                  "tests are still pending. Explicitly report",
                  "tests: NOT_RUN_BY_LEAF (HOST_OWNED); do not claim they PASS.",
                  "If any required edit or final Product readback failed,",
                  "start exactly with status: BLOCKED and report the cause.",
                  "Only failed inspections of explicitly HOST-approved",
                  "optional_exploration_paths can be diagnostic when all",
                  "assigned edits and final Product readbacks succeeded.",
                  "HOST-approved optional exploratory paths: " +
                  json.dumps(spec.get("optional_exploration_paths", []),
                             ensure_ascii=False) + ".",
                  "Any failed inspection NOT on that immutable whitelist is",
                  "BLOCKED, including missing necessary inputs or readbacks.",
                  "Report a whitelisted failed optional inspection explicitly.",
                  "Out-of-Project reads and denied tools remain blockers.",
                  "Inability to run HOST-owned tests is NOT a leaf blocker.",
                  "Never conceal real leaf failures or claim unrun checks PASS.",
                  "Assigned work: " + leaf["instruction"]]
        _role_file(agents / (role + ".md"), "\n".join(lines) + "\n")
    return {name: sha((agents / (name + ".md")).read_bytes())
            for name in [PRIMARY] + [x[1] for x in LEAVES]}


def prompt_primary(project: Path, spec: dict, plan: dict) -> str:
    by_id = {s["id"]: s for s in plan["steps"]}
    payload = {
        "goal": spec["goal"], "acceptance": spec["acceptance"],
        "plan_base_revision": plan["base_revision"],
        "host_optional_exploration_paths": spec.get("optional_exploration_paths", []),
        "instructions": [
            {"native_role": role, "step": ident,
             "authorized_paths": by_id[ident]["allowed_paths"],
             "instruction": by_id[ident]["instruction"]}
            for ident, role in LEAVES
        ]}
    project_guidance = ((project / "AGENTS.md").read_text()
                        if (project / "AGENTS.md").is_file() else
                        "No Project-owned root AGENTS.md")
    need(len(project_guidance.encode("utf-8")) <= 20000,
         "Project AGENTS.md exceeds bounded inline prompt")
    return (
        "CRITICAL: Your FIRST action MUST be ONE parallel batch of two native "
        "Task tool calls, subagent_type kagari-v010-leaf-a AND "
        "subagent_type kagari-v010-leaf-b. Do not invoke read/glob first, "
        "and do not submit either Task alone. The host will fail the Task "
        "if the two calls are sequential. Work independently in disjoint paths.\n"
        "Project AGENTS.md (source preserved, included so no preliminary read is needed):\n"
        + project_guidance + "\n"
        "This is an authorized TEST-ONLY KAGARI Task in a disposable Project.\n"
        "Do not change Project configuration. The following accepted bounded plan is data "
        "approved by the host. Do not broaden it or invent new Work Units.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True) +
        "\nInvoke the native OpenCode task tool for EXACTLY kagari-v010-leaf-a "
        "and kagari-v010-leaf-b in ONE assistant tool-call batch; no other task "
        "tools. Each child must get only its own scope and the relevant Project "
        "instructions. Do not wait for A before invoking B. Do not implement "
        "yourself. Each leaf succeeds on its own bounded code edit and final "
        "Product readback, NOT on running TaskSpec tests: only the KAGARI HOST "
        "runs tests, revalidates scope, commits, and writes v4 Evidence. "
        "Explicitly instruct both leaves to report status: COMPLETED with "
        "tests: NOT_RUN_BY_LEAF (HOST_OWNED) when their assigned edits and reads "
        "are complete, or status: BLOCKED if a required edit/readback, "
        "prohibited operation, out-of-Project attempt, or necessary input fails. "
        "Only an explicitly TaskSpec-whitelisted optional in-Project "
        "inspection error is diagnostic after final Product readbacks. "
        "Unclassified read failures are BLOCKED; the model must not "
        "invent optional paths or excuse a missing necessary input. "
        "Do NOT ask leaves to run tests and do NOT invent test PASS. "
        "Every child must report its actual status and file operations. "
        "Do not stage, commit, run tests, use bash, push, or publish. "
        "If child work fails, report the blocker; never pretend success."
    )


class NativeMetadata:
    def __init__(self, project: Path, output: Path, repo: str, task: str,
                 branch: str, baseline: str):
        self.project, self.output = project, output
        self.remote = output / "metadata.git"
        self.repository, self.task, self.branch, self.baseline = repo, task, branch, baseline
        self.remote.mkdir(mode=0o700)
        git(self.remote, "init", "-q", "--bare")
        self.store = MetadataStore(project, repo, str(self.remote), direct_ref_cas=self.cas)
        self.allowed: set[AuthorityRequest] = set()
        self.records = TaskRecords(self.store, authorize=lambda req: req in self.allowed)

    def cas(self, candidate: str, old: str | None) -> bool:
        parents = git(self.project, "show", "-s", "--format=%P", candidate).decode().strip()
        need(parents == (old or ""), "native metadata CAS parent mismatch")
        git(self.remote, "fetch", "--no-tags", "--no-write-fetch-head",
            str(self.project), candidate)
        try:
            git(self.remote, "update-ref", "--no-deref", METADATA_REF,
                candidate, old or "0" * len(candidate))
        except NativeBlocked:
            return False
        return True

    def create(self, spec: dict, plan: dict, source_config_sha: dict,
               human_grants: list[dict] | None = None,
               legacy_task_deny_opt_in: bool = False,
               planning_binding: dict | None = None) -> dict:
        contract = {
            "kind": "kagari-v010/test-only-native-parallel-task",
            "goal": spec["goal"], "acceptance": spec["acceptance"],
            "allowed_paths": sorted(spec["allowed_paths"]),
            "test_argv": spec["tests"],
            "plan_sha256": sha(json.dumps(plan, sort_keys=True).encode()),
            "task_spec_sha256": sha(json.dumps(spec, sort_keys=True).encode()),
            "project_instructions": source_config_sha,
            "human_ask_grants": human_grants or [],
            "legacy_task_deny_operator_flag_test_only": bool(legacy_task_deny_opt_in),
            "runtime_qualification": "DISPOSABLE_NOT_PRODUCTION"}
        if planning_binding is not None:
            contract["planning_binding"] = planning_binding
        title = "KAGARI bounded native two-subagent Task (test only)"
        body = json.dumps(contract, sort_keys=True, ensure_ascii=False)
        cid, _ = encode_contract(self.repository, self.task, self.baseline,
                                 {"schema_version": 1, "title": title, "body": body})
        rid, _ = encode_record(self.repository, self.task, self.baseline,
                               {"schema_version": 1, "branch_ref": self.branch, "contract_id": cid})
        self.allowed.add(AuthorityRequest("create", self.repository, self.task,
                                          self.branch, self.baseline, None, rid, cid))
        receipt = self.records.create(task=self.task, branch_ref=self.branch,
                                      base_revision=self.baseline, title=title, body=body)
        need(receipt.record_id == rid and receipt.contract_id == cid,
             "native Task Contract is not exactly bound")
        save(self.output / "contract-binding.json", asdict(receipt))
        return {"record_id": rid, "contract_id": cid}

    def revalidate(self, binding: dict) -> None:
        latest = self.store.fetch_tip()
        current = self.records.read(latest, task=self.task,
                                    base_revision=self.baseline, branch_ref=self.branch)
        need(current is not None and current[0] == binding["record_id"]
             and current[1]["payload"]["contract_id"] == binding["contract_id"]
             and "disposition" not in current[1]["payload"],
             "native Contract changed or Task cancelled")


    def finish(self, binding: dict, product_commit: str, model: dict,
               test_results: list[dict], scope: set[str],
               human_grants: list[dict] | None = None,
               legacy_task_deny_opt_in: bool = False,
               planning_binding: dict | None = None) -> dict:
        self.revalidate(binding)
        eid, data = encode_evidence(
            self.repository, self.task, product_commit,
            {"schema_version": 1,
             "kind": "kagari-v010/generic-native-two-subagent",
             "producer": {"name": "OpenCode",
                          "parent_session_id": model["parent_session"],
                          "child_session_ids": [s["child_session"] for s in model["children"]]},
             "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "payload": {"contract_id": binding["contract_id"],
                         "record_id": binding["record_id"],
                         "product_commit": product_commit,
                         "product_paths": sorted(scope),
                         "tests_pass": True, "test_count": len(test_results),
                         "test_receipts_sha256": sha(json.dumps(
                             test_results, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")),
                         "parallel_overlap_ms": model["overlap_ms"],
                         "parent_transcript_sha256": model["stdout_sha256"],
                         "child_session_export_sha256": model["export_sha256"],
                         "human_ask_grants": human_grants or [],
                         "planning_binding": planning_binding,
                         "legacy_task_deny_operator_flag_test_only":
                             bool(legacy_task_deny_opt_in),
                         "released_runtime": False}})
        commit = self.store.publish([data])
        Evidence(self.store).read(commit, eid, task=self.task, subject=product_commit)
        auth = lambda req: (req.repository == self.repository
                           and req.task == self.task
                           and req.subject == product_commit
                           and req.record_id == binding["record_id"]
                           and req.contract_id == binding["contract_id"])
        view = TaskViewSnapshots(self.store, authorize=auth).capture(
            task=self.task, branch_ref=self.branch, base_revision=self.baseline,
            boundary="explicit-handoff",
            selected_evidence=(EvidenceRef(eid, product_commit),),
            default_branch_ref="refs/heads/main")
        record = {"repository": self.repository, "task": self.task,
                  "branch_ref": self.branch, "project": str(self.project),
                  "remote": str(self.remote), "base": self.baseline,
                  "record_id": binding["record_id"], "contract_id": binding["contract_id"],
                  "evidence_id": eid, "metadata_commit": view.metadata_commit,
                  "snapshot_id": view.snapshot_id, "product_commit": product_commit,
                  "human_ask_grants": human_grants or [],
                  "planning_binding": planning_binding,
                  "legacy_task_deny_operator_flag_test_only":
                      bool(legacy_task_deny_opt_in)}
        save(self.output / "native-readback-input.json", record)
        need(readback(record)["status"] == "FRESH_PROCESS_NATIVE_READBACK_PASS",
             "native v4 metadata readback failed")
        return record


def readback(record: dict) -> dict:
    root = Path(record["project"])
    store = MetadataStore(root, record["repository"], record["remote"])
    found = TaskRecords(store, authorize=lambda _: False).read(
        record["metadata_commit"], task=record["task"],
        base_revision=record["base"], branch_ref=record["branch_ref"])
    need(found is not None and found[0] == record["record_id"]
         and found[1]["payload"]["contract_id"] == record["contract_id"],
         "native Task Record/Contract readback mismatch")
    ev = Evidence(store).read(record["metadata_commit"], record["evidence_id"],
                              task=record["task"], subject=record["product_commit"])
    need(ev["payload"]["payload"]["contract_id"] == record["contract_id"],
         "native Evidence Contract binding mismatch")
    need(ev["payload"]["payload"].get("human_ask_grants", [])
         == record.get("human_ask_grants", []),
         "Human Ask grant identities differ from persisted native Evidence")
    need(ev["payload"]["payload"].get(
         "legacy_task_deny_operator_flag_test_only", False)
         is record.get("legacy_task_deny_operator_flag_test_only", False),
         "legacy test-only Task permission bypass not bound to native Evidence")
    expected_receipts = ev["payload"]["payload"].get("test_receipts_sha256")
    need(type(expected_receipts) is str and re.fullmatch(r"[0-9a-f]{64}", expected_receipts),
         "native Evidence lacks test receipt digest")
    receipts_path = Path(record["remote"]).parent / "test-receipts.json"
    need(receipts_path.is_file() and not receipts_path.is_symlink(),
         "native test receipt file missing")
    raw_receipts = receipts_path.read_bytes()
    need(0 < len(raw_receipts) <= MAX_STDOUT, "native test receipt file unbounded")
    try:
        receipts = json.loads(raw_receipts)
    except ValueError as exc:
        raise NativeBlocked("native test receipt file invalid") from exc
    need(type(receipts) is list and len(receipts) == ev["payload"]["payload"].get("test_count")
         and bool(receipts) and sha(json.dumps(
         receipts, ensure_ascii=False, sort_keys=True,
         separators=(",", ":")).encode("utf-8")) == expected_receipts,
         "native Evidence test receipt digest mismatch")
    evidence_root = receipts_path.parent

    def retained_sha(path: Path, expected: str, label: str) -> None:
        need(type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
             "native " + label + " hash malformed")
        need(path.is_file() and not path.is_symlink(),
             "native " + label + " retained artifact missing")
        info = path.stat()
        need(info.st_size <= MAX_STDOUT,
             "native " + label + " retained artifact oversized")
        need(sha(path.read_bytes()) == expected,
             "native " + label + " retained artifact SHA mismatch")

    for index, entry in enumerate(receipts):
        need(type(entry) is dict and type(entry.get("argv")) is list
             and entry.get("returncode") == 0,
             "native test receipt invalid or not passing")
        retained_sha(evidence_root / ("test-" + str(index) + ".log"),
                     entry.get("log_sha256"), "test log " + str(index))

    native_payload = ev["payload"]["payload"]
    planning_binding = record.get("planning_binding")
    need(native_payload.get("planning_binding") == planning_binding,
         "native Planning binding not preserved in Evidence")
    if planning_binding is not None:
        need(type(planning_binding) is dict
             and planning_binding.get("mode") == "auto"
             and planning_binding.get("provenance") == "HOST_OBSERVED_JSONL_TEST_ONLY"
             and planning_binding.get("provider_identity_attested") is False,
             "unsupported or unauthenticated Planning mode")
        planning_dir = evidence_root / "planning"
        retained_sha(planning_dir / "receipt.json",
                     planning_binding.get("planning_receipt_sha256"),
                     "auto Planning receipt")
        retained_sha(planning_dir / "plan.json",
                     planning_binding.get("plan_file_sha256"),
                     "model-produced Plan")
        retained_sha(planning_dir / "stdout.jsonl",
                     planning_binding.get("model_stdout_sha256"),
                     "model Planning JSONL")
        receipt = planning.parse_single_json((planning_dir / "receipt.json").read_bytes())
        plan = planning.parse_single_json((planning_dir / "plan.json").read_bytes())
        requested = planning.parse_single_json(
            (evidence_root / "requested-task.json").read_bytes())
        need(type(receipt) is dict and type(plan) is dict
             and type(requested) is dict
             and receipt.get("status") == "HOST_OBSERVED_VALIDATED_PLAN_TEST_ONLY"
             and receipt.get("host_observed_model_output") is True
             and receipt.get("model_identity_independently_verified") is False
             and receipt.get("tool_events_observed") is False
             and requested.get("plan") == plan
             and type(requested.get("spec")) is dict
             and requested["spec"].get("planning") == "auto",
             "retained Planning provenance is incomplete")
        need(receipt.get("stdout_sha256")
             == planning_binding["model_stdout_sha256"]
             and receipt.get("plan_file_sha256")
             == planning_binding["plan_file_sha256"]
             and receipt.get("plan_contract_sha256")
             == planning_binding["plan_contract_sha256"]
             == sha(json.dumps(plan, sort_keys=True).encode())
             and receipt.get("task_spec_contract_sha256")
             == planning_binding["task_spec_contract_sha256"]
             == sha(json.dumps(requested["spec"], sort_keys=True).encode()),
             "model Planning receipt/Contract digests diverged")
        planning.validate_plan(requested["spec"], plan,
                               allow_optional_v2=requested["spec"].get("schema_version") == 2)
        try:
            observed_planner, planner_text = model_planner.read_events(
                (planning_dir / "stdout.jsonl").read_bytes())
            extracted_plan = planning.validate_plan(
                requested["spec"], planning.parse_single_json(planner_text),
                allow_optional_v2=requested["spec"].get("schema_version") == 2)
        except (model_planner.ModelPlanningBlocked, planning.PlanError) as exc:
            raise NativeBlocked("native Planning stdout is not a validated model Plan") from exc
        need(extracted_plan == plan
             and observed_planner["session_id"] == receipt.get("session_id")
             and observed_planner["jsonl_sha256"] == receipt["stdout_sha256"]
             and observed_planner["event_types"] == receipt.get("event_types"),
             "native Planning model JSONL and retained Plan differ")
    retained_sha(evidence_root / "model.jsonl",
                 native_payload.get("parent_transcript_sha256"), "model transcript")
    exports = native_payload.get("child_session_export_sha256")
    need(type(exports) is dict and set(exports) == {"primary", "sub-1", "sub-2"},
         "native child/parent export digest set missing")
    for name, filename in (("primary", "parent.json"),
                           ("sub-1", "sub-1.json"), ("sub-2", "sub-2.json")):
        retained_sha(evidence_root / "native-session-exports" / filename,
                     exports[name], "session export " + name)
    view = TaskViewSnapshots(store, authorize=lambda _: False).read(
        record["metadata_commit"], record["snapshot_id"],
        task=record["task"], subject=record["product_commit"])
    need(view["view"]["authority"]["record_id"] == record["record_id"],
         "native Task View binding mismatch")
    return {"status": "FRESH_PROCESS_NATIVE_READBACK_PASS",
            "product_commit": record["product_commit"],
            "record_id": record["record_id"], "contract_id": record["contract_id"],
            "evidence_id": record["evidence_id"], "snapshot_id": record["snapshot_id"]}


def _model_events(path: Path) -> tuple[str, list[dict]]:
    data = path.read_bytes()
    need(0 < len(data) <= MAX_STDOUT, "model JSONL empty or oversized")
    events = []
    for raw in data.splitlines():
        try:
            item = json.loads(raw)
        except ValueError as exc:
            raise NativeBlocked("invalid OpenCode JSONL event") from exc
        need(type(item) is dict, "non-object OpenCode JSONL event")
        events.append(item)
    need(not any(item.get("type") == "error" for item in events),
         "OpenCode provider error event observed")
    return sha(data), events


def _export_session(executable: Path, cwd: Path, env: dict,
                    session_id: str, dest: Path) -> dict:
    need(type(session_id) is str and session_id.startswith("ses_"),
         "invalid native session ID")
    # Some OpenCode/Bun builds return exit 0 but truncate redirected pipe
    # stdout at exactly 192 KiB while their file-backed export is complete.
    # Capture straight into this exclusive, private Evidence file instead of
    # accepting partial JSON from subprocess.PIPE as a successful export.
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        p = subprocess.run([str(executable), "--pure", "export", session_id],
                           env=env, cwd=cwd, stdout=f,
                           stderr=subprocess.DEVNULL, timeout=35)
    observed = dest.stat()
    need(p.returncode == 0 and stat.S_ISREG(observed.st_mode)
         and 0 < observed.st_size <= MAX_EXPORT,
         "native OpenCode session export unavailable or exceeded size bound")
    raw = dest.read_bytes()
    need(len(raw) == observed.st_size and len(raw) <= MAX_EXPORT,
         "native OpenCode session export length changed")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise NativeBlocked("invalid native OpenCode session export") from exc
    need(type(data) is dict and type(data.get("info")) is dict
         and type(data.get("messages")) is list,
         "incomplete native session export")
    return data


def _leaf_status_lines(text: str) -> list[str]:
    """Collect exact leaf terminal statuses without promoting ambiguous output."""
    if type(text) is not str:
        return []
    return re.findall(
        r"(?m)^status: (COMPLETED|BLOCKED|NEEDS_APPROVAL|NEEDS_DECISION)\.?\r?$",
        text)


def _inspection_path(tool: str, entry: dict, project: Path) -> Path:
    """Validate a native read-only tool's filesystem scope before accepting it.

    A successful or failed inspection must not launder an out-of-Project
    access into optional diagnostic evidence. Unspecified search paths use
    the already verified project-rooted child session.
    """
    root = project.resolve(strict=True)
    if tool == "read":
        raw = entry.get("filePath")
        need(type(raw) is str and Path(raw).is_absolute(),
             "native child read path not absolute")
        target = Path(raw)
    else:
        raw = entry.get("path")
        need(raw is None or type(raw) is str,
             "native child search path malformed")
        need(raw is None or bool(raw.strip()),
             "native child search path empty")
        if raw is None:
            target = root
        else:
            candidate = Path(raw)
            need("~" not in candidate.parts,
                 "native child search home expansion forbidden")
            target = candidate if candidate.is_absolute() else root / candidate
        if tool == "glob":
            pattern = entry.get("pattern")
            need(type(pattern) is str and bool(pattern),
                 "native glob pattern malformed")
            need(not pattern.startswith("/") and
                 ".." not in pattern.split("/") and
                 "~" not in pattern.split("/"),
                 "native glob pattern escaped Project")
        elif tool == "grep":
            include = entry.get("include")
            if include is not None:
                need(type(include) is str and bool(include)
                     and not include.startswith("/")
                     and ".." not in include.split("/")
                     and "~" not in include.split("/"),
                     "native grep include escaped Project")
    resolved = target.resolve(strict=False)
    need(resolved.is_relative_to(root),
         "native child inspection escaped Project")
    return resolved


def _child_tools(export: dict, role: str, parent_session: str,
                 model: dict, project: Path, paths: set[str],
                 *, optional_inspections: set[str] | None = None) -> None:
    info = export["info"]
    need(info.get("parentID") == parent_session and info.get("agent") == role
         and info.get("directory") == str(project),
         "child-session native attribution mismatch")
    emodel = info.get("model", {})
    need(type(emodel) is dict and emodel.get("id") == model.get("modelID")
         and emodel.get("providerID") == model.get("providerID"),
         "native child model identity mismatch")
    actual_edits, actual_reads = set(), set()
    # Require inspection *after* each Product's most recent modification.
    # A read before edit does not establish the resulting Product bytes.
    last_write: dict[str, int] = {}
    last_read: dict[str, int] = {}
    status_reports: list[str] = []
    step_index = 0
    for msg in export["messages"]:
        if type(msg) is not dict or msg.get("info", {}).get("role") != "assistant":
            continue
        for part in msg.get("parts", []):
            if type(part) is not dict:
                continue
            step_index += 1
            if part.get("type") == "text":
                text = part.get("text", "")
                status_reports.extend(_leaf_status_lines(text))
            if part.get("type") != "tool":
                continue
            tool = part.get("tool")
            state = part.get("state")
            need(type(state) is dict, "native child tool lacks state")
            entry = state.get("input")
            need(type(entry) is dict, "child tool lacks exact input")
            if state.get("status") != "completed":
                # A failed read-only inspection (e.g. read on a directory) is
                # not a failed product write. Refuse failed edits, delegations
                # or reads outside the delegated Project instead.
                need(state.get("status") == "error"
                     and tool in ("read", "glob", "grep", "list"),
                     "incomplete native child write or prohibited tool")
                observed = _inspection_path(tool, entry, project)
                relative = observed.relative_to(project.resolve(strict=True)).as_posix()
                need(relative in (optional_inspections or set())
                     and not any(relative == product
                                 or relative.startswith(product + "/")
                                 or product.startswith(relative + "/")
                                 for product in paths),
                     "failed native inspection is not host-approved optional")
                # OpenCode 1.18.35 reports a missing-file read as a native
                # tool error: "File not found: /exact/requested/path".
                # Status "error" by itself also covers permission denial,
                # transport failures and unrelated problems. Never treat
                # those as optional simply because the path was approved.
                requested = (entry.get("filePath") if tool == "read"
                             else entry.get("path"))
                need(type(requested) is str
                     and state.get("error") == "File not found: " + requested,
                     "native inspection error was not verified file-not-found")
                continue
            if tool == "apply_patch":
                patch = entry.get("patchText")
                need(type(patch) is str, "native patch text absent")
                touched = [line.split(": ", 1)[1] for line in patch.splitlines()
                           if line.startswith(("*** Add File: ", "*** Update File: ",
                                               "*** Delete File: "))]
                need(touched, "native patch has no product file")
                for name in touched:
                    target = Path(name)
                    need(target.is_absolute() and target.is_relative_to(project),
                         "native patch product escaped Project")
                    relative = target.relative_to(project).as_posix()
                    need(relative in paths, "native child edited outside own scope")
                    actual_edits.add(relative)
                    last_write[relative] = step_index
            elif tool == "edit":
                target = entry.get("filePath")
                need(type(target) is str and Path(target).is_absolute()
                     and Path(target).is_relative_to(project),
                     "native edit product escaped Project")
                relative = Path(target).relative_to(project).as_posix()
                need(relative in paths, "native edit outside assigned scope")
                actual_edits.add(relative)
                last_write[relative] = step_index
            elif tool == "read":
                target = _inspection_path(tool, entry, project)
                relative = target.relative_to(project.resolve(strict=True)).as_posix()
                actual_reads.add(relative)
                last_read[relative] = step_index
            elif tool in ("glob", "grep", "list"):
                _inspection_path(tool, entry, project)
                continue
            else:
                raise NativeBlocked("unapproved native leaf tool: " + str(tool))
    need(status_reports == ["COMPLETED"] and actual_edits == paths
         and actual_edits <= actual_reads,
         "native leaf write/read/COMPLETED not attributable to assigned paths")
    need(all(last_read.get(relative, -1) > last_write.get(relative, -1)
             for relative in paths),
         "native child did not read each Product after its final write")


def verify_delegation(executable: Path, project: Path, output: Path,
                      env: dict, plan: dict, *,
                      optional_inspections: set[str] | None = None) -> dict:
    transcript_sha, events = _model_events(output / "model.jsonl")
    calls = []
    for event in events:
        if event.get("type") != "tool_use":
            continue
        part = event.get("part")
        if type(part) is not dict or part.get("tool") != "task":
            continue
        state = part.get("state", {})
        need(type(state) is dict and state.get("status") == "completed",
             "native Task call did not complete")
        meta, inp, time = state.get("metadata"), state.get("input"), state.get("time")
        need(type(meta) is dict and type(inp) is dict and type(time) is dict,
             "native Task child metadata missing")
        start, end = time.get("start"), time.get("end")
        need(type(start) is int and type(end) is int and 0 < start < end,
             "invalid native leaf execution clock")
        child, parent = meta.get("sessionId"), meta.get("parentSessionId")
        need(type(child) is str and child.startswith("ses_") and type(parent) is str
             and parent.startswith("ses_")
             and parent == part.get("sessionID") == event.get("sessionID"),
             "native Task child-parent binding mismatch")
        need(type(meta.get("model")) is dict and type(meta["model"].get("modelID")) is str
             and type(meta["model"].get("providerID")) is str,
             "actual native leaf model identity missing")
        # OpenCode leaf status must be unmistakably COMPLETED. A single
        # terminal full stop is a presentation variant, not a different state.
        need(_leaf_status_lines(state.get("output")) == ["COMPLETED"],
             "native leaf did not report unambiguous COMPLETED")
        calls.append({"role": inp.get("subagent_type"),
                      "call_id": part.get("callID"), "child_session": child,
                      "parent_session": parent, "start_ms": start,
                      "end_ms": end, "model": meta["model"]})
    roles = {leaf for _, leaf in LEAVES}
    need(len(calls) == 2 and {c["role"] for c in calls} == roles
         and len({c["child_session"] for c in calls}) == 2
         and len({c["parent_session"] for c in calls}) == 1
         and len({c["call_id"] for c in calls}) == 2,
         "exactly two distinct, same-parent native Task leaves required")
    overlap = min(x["end_ms"] for x in calls) - max(x["start_ms"] for x in calls)
    need(overlap > 0, "native leaves did not execute concurrently")
    parent = calls[0]["parent_session"]
    exports_dir = output / "native-session-exports"
    exports_dir.mkdir(mode=0o700)
    result_sha = {}
    by_id = {s["id"]: s for s in plan["steps"]}
    for ident, role in LEAVES:
        found = [x for x in calls if x["role"] == role]
        need(len(found) == 1, "native leaf missing")
        call = found[0]
        dest = exports_dir / (ident + ".json")
        exported = _export_session(executable, project, env, call["child_session"], dest)
        need(exported["info"].get("id") == call["child_session"], "native child export session mismatched")
        _child_tools(exported, role, parent, call["model"], project,
                     set(by_id[ident]["allowed_paths"]),
                     optional_inspections=optional_inspections)
        result_sha[ident] = sha(dest.read_bytes())
    dest = exports_dir / "parent.json"
    pdoc = _export_session(executable, project, env, parent, dest)
    need(pdoc["info"].get("id") == parent and pdoc["info"].get("agent") == PRIMARY
         and pdoc["info"].get("directory") == str(project),
         "native parent export identity mismatched")
    target_ids = {call["call_id"] for call in calls}
    first_assistant = next(
        (msg for msg in pdoc["messages"]
         if type(msg) is dict and type(msg.get("info")) is dict
         and msg["info"].get("role") == "assistant"),
        None)
    need(first_assistant is not None, "native primary assistant message absent")
    first_tools = [part for part in first_assistant.get("parts", [])
                   if type(part) is dict and part.get("type") == "tool"]
    need(len(first_tools) == 2
         and all(part.get("tool") == "task" for part in first_tools)
         and {part.get("callID") for part in first_tools} == target_ids,
         "native two Tasks were not the first primary tool-call batch")
    result_sha["primary"] = sha(dest.read_bytes())
    return {"parent_session": parent,
            "children": sorted(calls, key=lambda x: x["role"]),
            "overlap_ms": overlap, "stdout_sha256": transcript_sha,
            "export_sha256": result_sha, "native_tool_calls": 2,
            "actual_child_writes_verified": True}


def spawn_model(executable: Path, project: Path, output: Path,
                env: dict, prompt: str, timeout: int) -> None:
    argv = [str(executable), "--pure", "run", "--agent", PRIMARY, "--format", "json",
            prompt]
    save(output / "model-invocation.json",
         {"argv_without_prompt": argv[:-1], "prompt_sha256": sha(prompt.encode()),
          "project": str(project), "model_override": False,
          "role_config_outside_project": env["OPENCODE_CONFIG_DIR"]})
    with (output / "model.jsonl").open("xb") as stdout, (output / "model.stderr").open("xb") as stderr:
        os.fchmod(stdout.fileno(), 0o600)
        os.fchmod(stderr.fileno(), 0o600)
        proc = subprocess.Popen(argv, cwd=project, env=env,
                                stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise NativeBlocked("OpenCode native multi-agent Task timed out") from exc
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    need(rc == 0, "configured OpenCode primary exited unsuccessfully")


def _scope_delta(before: dict, after: dict, allowed: set[str]) -> set[str]:
    all_changes = {x for x in before.keys() | after.keys()
                   if before.get(x) != after.get(x)}
    # New parent directories of allowlisted files are structural, not Product.
    parents = set()
    for path in allowed:
        parent = Path(path).parent
        while parent.as_posix() not in (".", "/"):
            parents.add(parent.as_posix())
            parent = parent.parent
    structural = {x for x in all_changes
                  if x in parents and x not in before
                  and after.get(x, (None,))[0] == "dir"}
    changes = all_changes - structural
    need(changes == allowed, "model physical change set is outside or missing Contract: "
         + repr(sorted(changes ^ allowed)[:20]))
    for name in allowed:
        entry = after.get(name)
        need(entry is not None and entry[0] == "file" and entry[-1] == 1,
             "native Product must be an ordinary non-hardlinked file")
    return changes


def verify_model_git_diff(project: Path, staged: list[str],
                          unstaged: list[str], tracked_allowed: set[str]) -> None:
    """Allow new tracked Product diffs but never modified preexisting dirt.

    Physical pre/post inventory independently enforces the exact Product
    allowlist. New untracked Product files are not emitted by Git diff until
    the host stages and commits them, so they must not be expected here.
    """
    observed_stage = git_lines(project, "diff", "--cached", "--name-only", "-z")
    observed_worktree = set(git_lines(project, "diff", "--name-only", "-z"))
    expected_worktree = set(unstaged) | tracked_allowed
    need(observed_stage == staged and observed_worktree == expected_worktree,
         "Model changed staged or existing dirty state")


def verify_committed_products(project: Path, commit: str, tested: dict,
                              allowed: set[str]) -> None:
    """Bind success Evidence to actual committed Git blob bytes and modes.

    Project .gitattributes, clean filters, Git autocrlf or encoding transforms
    can alter committed blobs even when the worktree stays unchanged.
    """
    for relative in sorted(allowed):
        expected = tested.get(relative)
        need(expected is not None and expected[0] == "file",
             "committed Product lacks verified worktree source")
        try:
            raw = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(project),
                 "show", commit + ":" + relative],
                env=git_env(), check=True, capture_output=True,
                timeout=20).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise NativeBlocked("committed product Git blob unavailable") from exc
        need(sha(raw) == expected[1],
             "committed Git blob does not equal independently tested Product: " + relative)
        manifest = git(project, "ls-tree", "-z", commit, "--", relative)
        need(b"\t" in manifest and manifest.count(b"\0") == 1,
             "committed Product tree mode unavailable")
        signature, name = manifest.split(b"\t", 1)
        oid = name.split(b"\0", 1)[0]
        need(oid == relative.encode("utf-8"), "committed path identity differs")
        expect_mode = b"100755" if expected[2] & 0o111 else b"100644"
        need(signature.startswith(expect_mode + b" blob "),
             "committed Git blob mode differs from tested Product: " + relative)


def run_tests(project: Path, spec: dict, output: Path, env: dict) -> list[dict]:
    results = []
    for index, test in enumerate(spec["tests"]):
        # No shell or arbitrary command string. Execution still requires the
        # operator's separate explicit consent on a disposable Project.
        try:
            p = subprocess.run(test["argv"], cwd=project / test["cwd"],
                               env={**env, "PYTHONDONTWRITEBYTECODE": "1"},
                               shell=False, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise NativeBlocked("declared Task test could not complete") from exc
        log = output / ("test-" + str(index) + ".log")
        log.write_bytes(p.stdout + p.stderr)
        log.chmod(0o600)
        need(p.returncode == 0, "declared Task tests failed: " + str(index))
        results.append({"argv": test["argv"], "returncode": p.returncode,
                        "log_sha256": sha(log.read_bytes())})
    return results



def execute(project: Path, spec_path: Path, plan_path: Path | None, executable: Path,
            output: Path, *, repository: str, task: str, consent: bool,
            disposable: bool, timeout: int = 150,
            allow_project_task_deny: bool = False,
            agents_ask_root: Path | None = None,
            permission_ask_root: Path | None = None) -> dict:
    """Test-only generic Task path; separate installation and Task results."""
    need(consent and disposable,
         "explicit --execute-model --run-tests --disposable-project authorization required")
    need(type(timeout) is int and 10 <= timeout <= 240, "invalid model timeout")
    guard_existing(project, output)
    need(executable.is_absolute() and executable.is_file() and not executable.is_symlink()
         and os.access(executable, os.X_OK), "absolute executable required")
    need(type(repository) is str and re.fullmatch(
         r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", repository),
         "explicit canonical metadata repository identity required")
    need(type(task) is str and re.fullmatch(r"[1-9][0-9]*", task),
         "positive numeric Task identity required")
    # TaskSpec v2 is test-only native-leaf opt-in, never GA/RC admission.
    spec = task_spec.load_task_spec(
        spec_path, project, allow_optional_v2=True)
    plan: dict | None = None
    if spec["planning"] == "specified":
        need(plan_path is not None, "specified Planning requires an explicit Plan")
        try:
            plan = planning.validate_plan(
                spec, planning.parse_single_json(plan_path.read_bytes()),
                allow_optional_v2=spec.get("schema_version") == 2)
        except (OSError, ValueError) as exc:
            raise NativeBlocked("invalid bounded two-leaf Task plan") from exc
        verify_plan(spec, plan)
    else:
        need(plan_path is None,
             "auto Planning must be generated by the host, not a supplied Plan")
    # An unapproved preview or a legacy test-only flag is NOT a Human Ask
    # approval. An AGENTS grant must have a matching active exact-diff receipt.
    runtime_source = Path(__file__).resolve()
    grant_bindings: list[tuple[Path, str, dict]] = []

    def verified_grant(state_root: Path, kind: str) -> dict:
        try:
            receipt = human_ask.grant_status(
                state_root, project, runtime_source, kind)
        except (human_ask.AskBlocked, OSError) as exc:
            raise NativeBlocked("HUMAN_ASK_GRANT_BLOCKED: " + str(exc)) from exc
        grant_bindings.append((state_root, kind, receipt))
        return receipt

    def recheck_human_grants() -> None:
        for state_root, kind, original in grant_bindings:
            try:
                current = human_ask.grant_status(
                    state_root, project, runtime_source, kind)
            except (human_ask.AskBlocked, OSError) as exc:
                raise NativeBlocked(
                    "HUMAN_ASK_REVALIDATION_BLOCKED: " + str(exc)) from exc
            need(current == original,
                 "Human Ask grant identity changed during this Task")

    # AGENTS compatibility is independent of the Task-tool grant. Detect
    # conflicts BEFORE model, metadata or Product execution.
    try:
        agents_diagnostic = agents_compat.inspect(project)
    except agents_compat.CompatibilityBlocked as exc:
        raise NativeBlocked("AGENTS_COMPAT_INSPECTION_BLOCKED: " + str(exc)) from exc
    if agents_diagnostic["state"] == "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK":
        if agents_ask_root is None:
            raise AgentsCompatibilityAsk(agents_diagnostic)
        try:
            verified_grant(agents_ask_root, human_ask.KIND_AGENTS)
        except NativeBlocked as exc:
            raise AgentsCompatibilityAsk(agents_diagnostic, str(exc)) from exc
    else:
        need(agents_ask_root is None,
             "AGENTS Ask supplied without exact known active conflict")
    need(agents_diagnostic["state"] in (
             "NO_EXACT_KNOWN_CONFLICT",
             "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK"),
         "AGENTS_COMPAT_MANUAL_REVIEW_REQUIRED: " + agents_diagnostic["state"])
    state = task_spec.inspect_project(project, spec)
    need(state["baseline"] == spec["base_revision"],
         "preflight baseline differs from TaskSpec")
    ref = git(project, "symbolic-ref", "-q", "HEAD").decode()
    need(ref.startswith("refs/heads/"), "local Task branch required")
    # This is an explicit disposable external test; do not infer that a regular
    # user's Project task:deny grants production KAGARI dispatch authority.
    for var in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT",
                "OPENCODE_MODEL", "OPENCODE_AGENT"):
        need(not os.environ.get(var), "unclassified OpenCode config/model selector: " + var)
    # An existing Project task:deny is an explicit Project policy, not an
    # invitation to silently override it using more specific external Roles.
    # Only an additional, separate TEST_ONLY operator authorization permits
    # dispatch in a disposable copy; the Project config itself remains intact.
    policy = "UNSPECIFIED"
    cfg_file = project / "opencode.json"
    if cfg_file.exists():
        need(cfg_file.is_file() and not cfg_file.is_symlink(),
             "unsupported Project OpenCode config file")
        try:
            configured = json.loads(cfg_file.read_bytes())
        except (UnicodeError, ValueError) as exc:
            raise NativeBlocked("Project OpenCode JSON config unparseable") from exc
        need(type(configured) is dict, "Project OpenCode config must be an object")
        permissions = configured.get("permission", {})
        need(type(permissions) is dict, "unknown Project permission type")
        declared = permissions.get("task")
        if declared == "deny" or (type(declared) is dict
                                  and declared.get("*") == "deny"):
            policy = "PROJECT_TASK_DENY"
            if permission_ask_root is not None:
                verified_grant(permission_ask_root, human_ask.KIND_PERMISSION)
            else:
                need(allow_project_task_deny,
                     "Project task:deny requires exact installed Human Ask grant "
                     "or explicit legacy --allow-project-task-deny-test-only")
        elif declared is not None:
            policy = "PROJECT_TASK_POLICY_CONFIGURED"
    need(not (permission_ask_root is not None and allow_project_task_deny),
         "ambiguous permission authority: choose Human Ask or legacy test-only flag")
    if policy != "PROJECT_TASK_DENY":
        need(permission_ask_root is None,
             "permission Ask supplied for Project without task:deny")
    human_grants = sorted((
        {
            "kind": kind, "request_id": approval["request_id"],
            "request_sha256": approval["request_sha256"],
            "decision_sha256": approval["decision_sha256"],
            "runtime_source_sha256": approval["runtime_source_sha256"],
            "grant_scope": "KAGARI_NATIVE_V4_TASK_SESSIONS_ONLY",
            "human_authentication": "LOCAL_TTY_TEST_ONLY_NOT_REMOTE_VERIFIED",
        }
        for _, kind, approval in grant_bindings
    ), key=lambda entry: entry["kind"])
    recheck_human_grants()
    before = inventory(project)
    git_controls_before = git_control_inventory(project) if plan is None else None
    git_cfg = sha((project / ".git/config").read_bytes())
    git_cfg_mode = stat.S_IMODE((project / ".git/config").stat().st_mode)
    head = git(project, "rev-parse", "HEAD").decode()
    index = (project / ".git/index").read_bytes()
    staged = git_lines(project, "diff", "--cached", "--name-only", "-z")
    original_staged_entries = {
        name: git(project, "ls-files", "--stage", "--", name) for name in staged}
    unstaged = git_lines(project, "diff", "--name-only", "-z")
    # Only baseline-tracked Product files appear in git diff before add.
    # Newly created allowed files are untracked until host-only commit; their
    # presence and exact scope are instead governed by physical inventory.
    tracked_allowed = set(git_lines(
        project, "ls-files", "--cached", "-z", "--",
        *sorted(spec["allowed_paths"])))
    refs_before = git(project, "show-ref")
    instruction_paths = ("AGENTS.md", "opencode.json", "PROJECT_RULES.md")
    source_cfg = {k: sha((project / k).read_bytes()) for k in instruction_paths
                  if (project / k).is_file() and not (project / k).is_symlink()}
    output.mkdir(mode=0o700)
    current: dict = {"status": "BLOCKED", "stage": "planning" if plan is None else "preflight",
                     "qualification": "TEST_ONLY_UNQUALIFIED",
                     "release_ready": False, "installation_assessed": False,
                     "generic_task_qualified": False,
                     "scope": sorted(spec["allowed_paths"]),
                     "source_head": head,
                     "project_task_policy": policy,
                     "project_task_deny_override_test_only": bool(allow_project_task_deny),
                     "human_ask_grants": human_grants}
    try:
        planning_binding: dict | None = None
        if plan is None:
            def verify_planning_read_only() -> None:
                need(inventory(project) == before
                     and git_control_inventory(project) == git_controls_before
                     and git(project, "rev-parse", "HEAD").decode() == head
                     and git(project, "symbolic-ref", "-q", "HEAD").decode() == ref
                     and (project / ".git/index").read_bytes() == index
                     and sha((project / ".git/config").read_bytes()) == git_cfg
                     and git(project, "show-ref") == refs_before
                     and git_lines(project, "diff", "--cached",
                                   "--name-only", "-z") == staged
                     and git_lines(project, "diff", "--name-only", "-z") == unstaged
                     and stat.S_IMODE((project / ".git/config").stat().st_mode)
                     == git_cfg_mode,
                     "auto Planning changed Project source, Git state or staged work")
                recheck_human_grants()

            native_v2_kwargs = ({"allow_optional_v2": True}
                                if spec.get("schema_version") == 2 else {})
            plan, planning_binding = model_planner.generate(
                project, spec, executable, output / "planning",
                timeout=min(timeout, 120),
                verify_unmodified=verify_planning_read_only,
                **native_v2_kwargs)
            verify_plan(spec, plan, host_observed_auto=True)
            verify_planning_read_only()
            current["planning"] = planning_binding
        need(plan is not None, "Planning must yield an exact TaskSpec-bound Plan")
        save(output / "requested-task.json",
             {"spec": spec, "plan": plan, "repo": repository, "task": task,
              "source_preflight": state, "before_config_sha256": git_cfg,
              "before_index_sha256": sha(index),
              "human_ask_grants": human_grants,
              "planning_binding": planning_binding})
        role_shas = write_roles(project, spec, plan, output / "roles")
        current["role_sha256"] = role_shas
        native = NativeMetadata(project, output, repository, task, ref, head)
        binding = native.create(
            spec, plan, source_cfg, human_grants,
            legacy_task_deny_opt_in=allow_project_task_deny,
            planning_binding=planning_binding)
        current.update({"stage": "model", "contract_id": binding["contract_id"],
                        "record_id": binding["record_id"]})
        current["installation_assessed"] = False
        # External role definitions do not overwrite Project-local files.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["OPENCODE_CONFIG_DIR"] = str(output / "roles")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        prompt = prompt_primary(project, spec, plan)
        (output / "native-prompt.txt").write_text(prompt)
        (output / "native-prompt.txt").chmod(0o600)
        recheck_human_grants()
        print("NATIVE_GENERIC_TASK_MODEL_STARTED", json.dumps({
            "project": str(project), "scope": sorted(spec["allowed_paths"]),
            "configured_model_override": False, "subagents": 2
        }), flush=True)
        spawn_model(executable, project, output, env, prompt, timeout)
        model = verify_delegation(
            executable, project, output, env, plan,
            optional_inspections=set(spec.get("optional_exploration_paths", [])))
        recheck_human_grants()
        need(git(project, "rev-parse", "HEAD").decode() == head
             and git(project, "symbolic-ref", "-q", "HEAD").decode() == ref
             and (project / ".git/index").read_bytes() == index
             and sha((project / ".git/config").read_bytes()) == git_cfg
             and git(project, "show-ref") == refs_before,
             "Model changed Git HEAD/index/config/refs outside Contract")
        need(stat.S_IMODE((project / ".git/config").stat().st_mode) == git_cfg_mode,
             "Model changed Project Git configuration mode")
        allowed = set(spec["allowed_paths"])
        after_model = inventory(project)
        _scope_delta(before, after_model, allowed)
        # Preserve every baseline dirty and staged path while allowing only
        # baseline-tracked Product paths to appear as new worktree diffs.
        # Newly created allowed Product paths are untracked until host commit.
        verify_model_git_diff(project, staged, unstaged, tracked_allowed)
        for name, observed_hash in source_cfg.items():
            need(sha((project / name).read_bytes()) == observed_hash,
                 "Model modified Project-owned configuration/instructions")
        native.revalidate(binding)
        current["stage"] = "tests"
        test_results = run_tests(project, spec, output, env)
        need(inventory(project) == after_model and
             git(project, "rev-parse", "HEAD").decode() == head
             and (project / ".git/index").read_bytes() == index
             and sha((project / ".git/config").read_bytes()) == git_cfg
             and stat.S_IMODE((project / ".git/config").stat().st_mode) == git_cfg_mode
             and git(project, "show-ref") == refs_before,
             "Declared tests modified Project, index, HEAD, Git config or refs")
        native.revalidate(binding)
        recheck_human_grants()
        current["stage"] = "product-commit"
        # The host, not the model or leaves, owns the product commit. Disable
        # arbitrary Project hooks and GPG signers for this exact local operation.
        git(project, "add", "--", *sorted(allowed))
        need(set(git_lines(project, "diff", "--cached", "--name-only", "-z"))
             == set(staged) | allowed, "unexpected staged additions before commit")
        git(project, "-c", "core.hooksPath=" + os.devnull,
            "-c", "commit.gpgSign=false", "-c", "user.name=KAGARI Test Host",
            "-c", "user.email=kagari-test@example.invalid",
            "commit", "--only", "-q", "-m", "KAGARI native parallel Task test-only",
            "--", *sorted(allowed))
        product_commit = git(project, "rev-parse", "HEAD").decode()
        need(git(project, "rev-parse", "HEAD^").decode() == head
             and set(git_lines(project, "diff-tree", "--no-commit-id",
                               "--name-only", "-r", "-z", "HEAD")) == allowed,
             "host product commit parent or scope mismatch")
        need(git_lines(project, "diff", "--cached", "--name-only", "-z") == staged
             and git_lines(project, "diff", "--name-only", "-z") == unstaged
             and all(git(project, "ls-files", "--stage", "--", name) == entry
                     for name, entry in original_staged_entries.items()),
             "host product commit lost unrelated staged content, mode or dirty state")
        verify_committed_products(project, product_commit, after_model, allowed)
        after_commit = inventory(project)
        need(after_commit == after_model and
             sha((project / ".git/config").read_bytes()) == git_cfg
             and stat.S_IMODE((project / ".git/config").stat().st_mode) == git_cfg_mode,
             "host commit modified verified Project product/config bytes or modes")
        current["stage"] = "native-v4-evidence"
        recheck_human_grants()
        save(output / "model-evidence.json", model)
        save(output / "test-receipts.json", test_results)
        native_record = native.finish(
            binding, product_commit, model, test_results, allowed, human_grants,
            legacy_task_deny_opt_in=allow_project_task_deny,
            planning_binding=planning_binding)
        current.update({
            "status": "REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS",
            "stage": "complete", "product_commit": product_commit,
            "model": model, "tests": test_results,
            "planning": planning_binding,
            "native": native_record,
            "generic_task_qualified": False, "release_ready": False,
            "installation_assessed": False})
        # Standalone fresh Python process exercises the actual readback entrypoint.
        try:
            observed = subprocess.run(
                [sys.executable, "-B", str(Path(__file__).resolve()),
                 "--readback", str(output / "native-readback-input.json")],
                capture_output=True, text=True, timeout=35, check=True)
            readback_status = json.loads(observed.stdout)
            need(readback_status["status"] == "FRESH_PROCESS_NATIVE_READBACK_PASS",
                 "new-process native v4 readback failed")
            current["fresh_process_readback"] = readback_status
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            raise NativeBlocked("fresh Python process native readback failed") from exc
    except (NativeBlocked, OSError, ValueError, subprocess.SubprocessError) as exc:
        current.update(status="BLOCKED", reason=str(exc)[:500])
    save(output / "report.json", current)
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--task-spec", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--opencode", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repository", default="fixture/kagari-native")
    parser.add_argument("--task", default="271")
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--execute-model", action="store_true")
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--disposable-project", action="store_true")
    parser.add_argument("--allow-project-task-deny-test-only", action="store_true")
    parser.add_argument("--agents-ask-root", type=Path)
    parser.add_argument("--permission-ask-root", type=Path)
    parser.add_argument("--readback", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.readback is not None:
        print(json.dumps(readback(json.loads(args.readback.read_bytes())),
                         sort_keys=True))
        return 0
    if any(v is None for v in (args.project_root, args.task_spec,
                               args.opencode, args.output)):
        parser.error("absolute --project-root --task-spec --opencode --output required; --plan required only for specified Planning")
    try:
        result = execute(args.project_root, args.task_spec, args.plan,
                         args.opencode, args.output, repository=args.repository,
                         task=args.task,
                         consent=args.execute_model and args.run_tests,
                         disposable=args.disposable_project, timeout=args.timeout,
                         allow_project_task_deny=args.allow_project_task_deny_test_only,
                         agents_ask_root=args.agents_ask_root,
                         permission_ask_root=args.permission_ask_root)
    except AgentsCompatibilityAsk as exc:
        result = {
            "status": "BLOCKED",
            "gate": "AGENTS_COMPAT_ASK_REQUIRED",
            "reason": str(exc)[:500],
            "ask_candidate": exc.diagnostic["ask_candidate"],
            "qualification": "TEST_ONLY_UNQUALIFIED",
            "release_ready": False,
            "AGENTS_modified": False,
        }
    except (NativeBlocked, OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {"status": "BLOCKED", "reason": str(exc)[:500],
                  "qualification": "TEST_ONLY_UNQUALIFIED", "release_ready": False}
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("status") == "REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
