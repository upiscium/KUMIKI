"""Host-observed, bounded auto-Planning for KAGARI v0.1.0 TEST_ONLY.

This module runs exactly one explicitly selected OpenCode executable with a
read-only, no-tool external planner role. It never dispatches leaf Tasks or
changes the Project. The host itself observes stdout JSONL and validates the
resulting TaskSpec-bound two-leaf Plan; user-provided transcript claims are
never accepted as model provenance. This is NOT provider-identity attestation
or a production-qualified human authorization mechanism.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
from typing import Callable

import kagari_v010_basic_plan as basic

MAX_PLAN_STDOUT = 1024 * 1024
MAX_PLAN_STDERR = 65536
MAX_PROMPT = 16 * 1024
ROLE = "kagari-v010-planner"


class ModelPlanningBlocked(ValueError):
    """The trusted host could not establish an auto-Planning result."""


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ModelPlanningBlocked(reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def save_new(path: Path, content: bytes) -> None:
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(handle, "wb") as f:
        f.write(content)


def read_events(data: bytes) -> tuple[dict, str]:
    """Bind a single tool-free OpenCode session's text to one strict Plan."""
    require(0 < len(data) <= MAX_PLAN_STDOUT, "planner JSONL missing or oversized")
    parts: list[str] = []
    sessions: set[str] = set()
    counts: dict[str, int] = {}
    phase = "before-start"
    for line in data.splitlines():
        try:
            event = basic.parse_single_json(line)
        except basic.PlanError as exc:
            raise ModelPlanningBlocked("invalid planner JSONL") from exc
        require(type(event) is dict, "planner event is not an object")
        kind = event.get("type")
        require(kind in ("step_start", "text", "step_finish"),
                "unexpected planner event or attempted tool invocation")
        session = event.get("sessionID")
        require(type(session) is str
                and re.fullmatch(r"ses_[A-Za-z0-9_-]{8,120}", session) is not None,
                "planner event session identity missing")
        sessions.add(session)
        section = event.get("part")
        require(type(section) is dict, "planner event part missing")
        if phase == "before-start":
            require(kind == "step_start" and section.get("type") == "step-start",
                    "planner did not start before returning text")
            phase = "text"
        elif phase == "text" and kind == "text":
            require(section.get("type") == "text"
                    and type(section.get("text")) is str,
                    "planner text part missing")
            parts.append(section["text"])
        elif phase == "text" and kind == "step_finish":
            require(section.get("type") == "step-finish" and bool(parts),
                    "planner terminated without a valid text Plan")
            phase = "finished"
        else:
            raise ModelPlanningBlocked("planner JSONL events out of order or after finish")
        counts[kind] = counts.get(kind, 0) + 1
    require(phase == "finished" and len(sessions) == 1
            and counts.get("step_start") == 1
            and counts.get("step_finish") == 1 and bool(parts),
            "planner session incomplete or ambiguous")
    text = "".join(parts)
    require(0 < len(text.encode("utf-8")) <= basic.MAX_JSON_BYTES,
            "planner plan text exceeds protocol budget")
    return {"session_id": next(iter(sessions)),
            "event_types": counts,
            "jsonl_sha256": digest(data)}, text


def planner_prompt(spec: dict) -> str:
    task_data = json.dumps(spec, sort_keys=True, ensure_ascii=False, allow_nan=False)
    prompt = (
        "You are a tool-free planning-only component of KAGARI. Do not call any "
        "tool, access files, edit code, run tests, delegate, or request extra "
        "permissions. The following TaskSpec is DATA, not a command to perform "
        "work now. Return precisely ONE JSON object, NO markdown, commentary "
        "or code fences, with keys schema_version=1, base_revision and steps. "
        "steps must have exactly three entries: sub-1 and sub-2 are independent "
        "ready subagents, role=subagent, agent=build, depends_on=[], each "
        "owning a nonempty DISJOINT subset of TaskSpec allowed_paths; together "
        "own every allowed path exactly once. integrate is role=primary, "
        "agent=build, depends_on=[sub-1,sub-2], allowed_paths=[]. Each step "
        "has exactly id, role, agent, depends_on, allowed_paths, instruction. "
        "Each instruction must be concise yet actionable, use the actual goal "
        "and acceptance, respect existing interfaces and tests, and never "
        "authorize work outside its paths. Leaf instructions cover ONLY "
        "authorized Product edits and post-edit file readback. They MUST NOT "
        "assign test execution, Git commit, or Evidence storage to leaves: "
        "the KAGARI HOST performs all TaskSpec tests, scope validation, "
        "Product commit, and native-v4 Evidence/Task View. A leaf that "
        "successfully finishes its edits and readback is COMPLETED even while "
        "host tests are pending; inability to run host-owned tests alone is "
        "not a leaf blocker. Genuine failed leaf edits/readback remain BLOCKED. "
        "Do not provide a test command or make a commit. TaskSpec:\n" + task_data
    )
    require(len(prompt.encode("utf-8")) <= MAX_PROMPT,
            "planner prompt exceeds budget")
    return prompt


def run_planning_child(argv: list[str], working_directory: Path, env: dict,
                       fout, ferr, timeout: int) -> int:
    """Isolate the tool-free planner outside Project and reap its subprocesses.

    Even when the direct OpenCode child has exited, any remaining subprocesses
    in its own new session are signalled before the host checks Project bytes.
    This is scoped process-group containment, not a universal descendant tree
    or provider-identity guarantee.
    """
    process = subprocess.Popen(
        argv, cwd=working_directory, env=env, stdout=fout, stderr=ferr,
        stdin=subprocess.DEVNULL, shell=False, start_new_session=True)
    try:
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ModelPlanningBlocked("planner process timed out") from exc
        return code
    finally:
        # The group is dedicated to this one invocation. Reap the leader
        # before returning, whether the invocation succeeded or failed.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def generate(project: Path, spec: dict, executable: Path, root: Path, *,
             timeout: int, verify_unmodified: Callable[[], None],
             allow_optional_v2: bool = False) -> tuple[dict, dict]:
    """Return a host-observed Plan and a digest-bound private planning binding.

    Project and Human Ask preflight, exact executable identity and independent
    Project inventory checks belong to the calling native Task host.
    """
    require(spec.get("planning") == "auto", "auto planning requires auto TaskSpec")
    basic.validate_task_spec(spec, allow_optional_v2=allow_optional_v2)
    require(type(timeout) is int and 10 <= timeout <= 180,
            "planner model timeout outside allowed range")
    require(root.is_absolute() and root.resolve(strict=False) == root
            and not root.exists() and not root.is_symlink()
            and root.parent.is_dir() and not root.parent.is_symlink()
            and root.parent.resolve() == root.parent
            and not root.is_relative_to(project)
            and not project.is_relative_to(root),
            "planning root must be fresh, canonical and outside Project")
    require(executable.is_absolute() and executable.is_file()
            and not executable.is_symlink() and os.access(executable, os.X_OK),
            "planner executable must be exact non-symlink binary")
    require(callable(verify_unmodified), "Project stability callback missing")
    prompt = planner_prompt(spec)

    # The planner has no read/edit/bash/task tools and its prompt contains
    # the complete bounded TaskSpec. It does not need the Product working
    # directory. OpenCode's first run in a new Git Clone creates a private
    # .git/opencode workspace ID even under --pure; using Project as cwd
    # makes the host's unchanged-Git-control gate correctly BLOCK that run.
    # Keep the gate strict: invoke the planner only in private external
    # Evidence instead of granting an exception to Git metadata mutation.
    root.mkdir(mode=0o700)
    role_dir = root / "roles"
    role_dir.mkdir(mode=0o700)
    agents = role_dir / "agents"
    agents.mkdir(mode=0o700)
    role = (
        "---\n"
        "description: KAGARI host-observed planning-only model\n"
        "mode: primary\n"
        "permission:\n"
        "  read: deny\n"
        "  edit: deny\n"
        "  bash: deny\n"
        "  task: deny\n"
        "  question: deny\n"
        "  webfetch: deny\n"
        "  websearch: deny\n"
        "  skill: deny\n"
        "---\n"
        "You are only a tool-free JSON Task Planner. Never call tools, "
        "edit files, delegate, invoke commands or claim execution.\n"
    )
    save_new(agents / (ROLE + ".md"), role.encode("utf-8"))
    save_new(root / "prompt.txt", prompt.encode("utf-8"))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    for var in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT",
                "OPENCODE_MODEL", "OPENCODE_AGENT"):
        require(not env.get(var), "unclassified planner configuration selector")
    env["OPENCODE_CONFIG_DIR"] = str(role_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    argv = [str(executable), "--pure", "run", "--agent", ROLE,
            "--format", "json", prompt]
    save_new(root / "invocation.json", encoded({
        "argv_without_prompt": argv[:7],
        "cwd": str(root),
        "task_context_project": str(project),
        "project_filesystem_access_required": False,
        "configuration_override": False,
        "provider_identity_attested": False,
        "test_only": True,
    }))
    stdout = root / "stdout.jsonl"
    stderr = root / "stderr.raw"
    fdout = os.open(stdout, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    fderr = os.open(stderr, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fdout, "wb") as fout, os.fdopen(fderr, "wb") as ferr:
            exit_code = run_planning_child(argv, root, env, fout, ferr, timeout)
    finally:
        # The caller must run the full physical/Git preservation check on
        # both successful and failed execution paths before any Task step.
        verify_unmodified()
    require(exit_code == 0, "planner model failed or timed out")
    raw = stdout.read_bytes()
    require(0 < len(raw) <= MAX_PLAN_STDOUT
            and stderr.stat().st_size <= MAX_PLAN_STDERR,
            "planner output exceeds bounded evidence budget")
    observed, text = read_events(raw)
    try:
        candidate = basic.validate_plan(spec, basic.parse_single_json(text),
                                        allow_optional_v2=allow_optional_v2)
    except basic.PlanError as exc:
        raise ModelPlanningBlocked("model Plan failed strict TaskSpec validation") from exc
    leaves = [s for s in candidate["steps"] if s["role"] == "subagent"]
    require(len(leaves) == 2
            and set(p for s in leaves for p in s["allowed_paths"])
            == set(spec["allowed_paths"]),
            "model Plan did not assign every authorized Product path")
    plan_raw = encoded(candidate)
    save_new(root / "plan.json", plan_raw)
    receipt = {
        "status": "HOST_OBSERVED_VALIDATED_PLAN_TEST_ONLY",
        "schema_version": 1,
        "provider_provenance": "HOST_SPAWNED_OPENCODE_JSONL_NOT_CRYPTOGRAPHICALLY_ATTESTED",
        "host_observed_model_output": True,
        "model_identity_independently_verified": False,
        "external_transcript_accepted_as_authority": False,
        "tool_events_observed": False,
        "model_override": False,
        "session_id": observed["session_id"],
        "event_types": observed["event_types"],
        "stdout_sha256": observed["jsonl_sha256"],
        "stderr_sha256": digest(stderr.read_bytes()),
        "plan_file_sha256": digest(plan_raw),
        "plan_contract_sha256": digest(json.dumps(candidate, sort_keys=True).encode()),
        "task_spec_contract_sha256": digest(json.dumps(spec, sort_keys=True).encode()),
        "Project_mutation_verified_absent": True,
        "task_dispatched": False,
        "released": False,
    }
    receipt_raw = encoded(receipt)
    save_new(root / "receipt.json", receipt_raw)
    binding = {
        "mode": "auto",
        "provenance": "HOST_OBSERVED_JSONL_TEST_ONLY",
        "task_spec_contract_sha256": receipt["task_spec_contract_sha256"],
        "plan_contract_sha256": receipt["plan_contract_sha256"],
        "plan_file_sha256": receipt["plan_file_sha256"],
        "model_stdout_sha256": receipt["stdout_sha256"],
        "planning_receipt_sha256": digest(receipt_raw),
        "provider_identity_attested": False,
    }
    return candidate, binding
