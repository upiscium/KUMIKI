"""Offline verifier for a bounded, real OpenCode native Task delegation trace.

Validates two native child sessions, their common parent and overlapping execution,
product bytes, Project instruction inheritance and preservation of unrelated
Git state. It does not authenticate caller-supplied JSONL or ship a Task runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path


class ProofBlocked(ValueError):
    """A bounded Task-delegation acceptance gate did not pass."""


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ProofBlocked(reason)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(project: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    try:
        return subprocess.run(
            ["git", "--no-optional-locks", "-C", str(project),
             "-c", "core.fsmonitor=false", *args],
            env=env, capture_output=True, check=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProofBlocked("read-only Git inspection failed") from exc


def _role_path(name: str) -> str:
    require(type(name) is str and re.fullmatch(r"[a-z0-9_-]+(?:/[a-z0-9_.-]+)*", name)
            and not any(part in (".", "..", ".git", ".opencode") for part in name.split("/")),
            "invalid product path")
    return name


def _load_trace(events_file: Path) -> tuple[bytes, list[dict]]:
    require(events_file.is_absolute() and events_file.is_file()
            and not events_file.is_symlink(), "absolute regular JSONL required")
    data = events_file.read_bytes()
    require(0 < len(data) <= 8 * 1024 * 1024, "bounded JSONL size required")
    events: list[dict] = []
    for line in data.splitlines():
        try:
            obj = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise ProofBlocked("malformed JSONL event") from exc
        require(type(obj) is dict, "non-object JSONL event")
        events.append(obj)
    return data, events


def _verify_child_exports(project: Path, trace_dir: Path, calls: list[dict]) -> dict:
    """Cross-check native session exports, including actual child edit tools."""
    export_dir = trace_dir / "native-session-exports"
    require(export_dir.is_dir() and not export_dir.is_symlink(),
            "native child session exports missing")
    received = {}
    for label in ("a", "b"):
        source = export_dir / (label + "-session.json")
        require(source.is_file() and not source.is_symlink(),
                "native child session export missing")
        raw = source.read_bytes()
        require(0 < len(raw) <= 2 * 1024 * 1024,
                "native child export out of bounds")
        try:
            doc = json.loads(raw)
        except ValueError as exc:
            raise ProofBlocked("invalid native child session export") from exc
        role = "kagari-v010-leaf-" + label
        matching = [c for c in calls if c["role"] == role]
        require(len(matching) == 1, "native child Role not found")
        call = matching[0]
        info = doc.get("info", {}) if type(doc) is dict else {}
        require(type(info) is dict
                and info.get("id") == call["child_session"]
                and info.get("parentID") == call["parent_session"]
                and info.get("agent") == role
                and info.get("directory") == str(project),
                "native child session provenance mismatch")
        model = info.get("model", {})
        require(type(model) is dict
                and model.get("id") == call["model"]["modelID"]
                and model.get("providerID") == call["model"]["providerID"],
                "native child model provenance mismatch")
        messages = doc.get("messages")
        require(type(messages) is list, "native child messages missing")
        edits, reads, completed = [], [], False
        for message in messages:
            if type(message) is not dict or message.get("info", {}).get("role") != "assistant":
                continue
            for part in message.get("parts", []):
                if type(part) is not dict:
                    continue
                if part.get("type") == "text" and type(part.get("text")) is str:
                    if part["text"].startswith("status: COMPLETED"):
                        completed = True
                if part.get("type") != "tool":
                    continue
                tool = part.get("tool")
                state = part.get("state")
                require(type(state) is dict and state.get("status") == "completed",
                        "native child tool did not complete")
                payload = state.get("input")
                require(type(payload) is dict, "native child tool input missing")
                if tool == "apply_patch":
                    patch = payload.get("patchText")
                    require(type(patch) is str, "native child patch text missing")
                    touched = [
                        line.split(": ", 1)[1] for line in patch.splitlines()
                        if line.startswith(("*** Add File: ", "*** Update File: ",
                                            "*** Delete File: "))
                    ]
                    require(touched == [str(project / ("docs/worker-" + label + ".md"))],
                            "native child edited an unauthorized file")
                    require("kagari_worker=" + label.upper() in patch,
                            "native child edit lacks assigned marker")
                    edits.append(touched[0])
                elif tool == "read":
                    reads.append(payload.get("filePath"))
                else:
                    raise ProofBlocked("unapproved native child tool used: " + str(tool))
        expected_path = str(project / ("docs/worker-" + label + ".md"))
        require(edits == [expected_path] and expected_path in reads and completed,
                "native child physical write/read/completion not attributable")
        received[role] = digest(raw)

    source = export_dir / "parent-session.json"
    require(source.is_file() and not source.is_symlink(), "native parent export missing")
    raw = source.read_bytes()
    require(0 < len(raw) <= 2 * 1024 * 1024, "native parent export out of bounds")
    try:
        parent = json.loads(raw)
    except ValueError as exc:
        raise ProofBlocked("invalid native parent session export") from exc
    info = parent.get("info", {}) if type(parent) is dict else {}
    require(type(info) is dict and info.get("id") == calls[0]["parent_session"]
            and info.get("agent") == "kagari-v010-primary"
            and info.get("directory") == str(project),
            "native parent session provenance mismatch")
    messages = parent.get("messages", [])
    grouped = [
        {part.get("callID") for part in message.get("parts", [])
         if type(part) is dict and part.get("type") == "tool" and part.get("tool") == "task"}
        for message in messages
        if type(message) is dict and type(message.get("info")) is dict
        and message["info"].get("role") == "assistant"
    ]
    require(any(batch == {call["call_id"] for call in calls} for batch in grouped),
            "two native Task calls were not issued in one parent tool batch")
    received["kagari-v010-primary"] = digest(raw)
    return received


def verify(project: Path, setup_path: Path, events_file: Path) -> dict:
    """Attest observable native event semantics; independent provider attestation
    and v0.1.0 generic Project qualification are separate required gates.
    """
    require(setup_path.is_absolute() and setup_path.is_file() and not setup_path.is_symlink(),
            "absolute pre-run operator setup required")
    setup_raw = setup_path.read_bytes()
    setup = json.loads(setup_raw)
    require(type(setup) is dict, "invalid setup")
    require(type(setup.get("project")) is str
            and type(setup.get("head")) is str
            and type(setup.get("index_sha256")) is str
            and type(setup.get("project_instruction_token")) is str
            and type(setup.get("allowed")) is list,
            "incomplete pre-run setup")
    require(project.is_absolute() and project.is_dir() and not project.is_symlink()
            and project.resolve() == project and project.stat().st_uid == os.geteuid()
            and str(project) == setup["project"], "Project root does not match setup")
    require((project / ".git").is_dir() and not (project / ".git").is_symlink(),
            "ordinary Git Project required")
    require(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", setup["head"]) is not None,
            "invalid base revision")
    require(re.fullmatch(r"[0-9a-f]{64}", setup["index_sha256"]) is not None,
            "invalid index fingerprint")
    allowed = [_role_path(x) for x in setup["allowed"]]
    require(allowed == ["docs/worker-a.md", "docs/worker-b.md"],
            "this bounded acceptance requires exactly two separated products")
    token = setup["project_instruction_token"]
    require(type(token) is str and re.fullmatch(r"[0-9a-f]{16,64}", token) is not None,
            "invalid Project instruction token")

    raw, events = _load_trace(events_file)
    calls = []
    for event in events:
        if event.get("type") != "tool_use":
            continue
        part = event.get("part")
        if type(part) is not dict or part.get("tool") != "task":
            continue
        state = part.get("state")
        require(type(state) is dict and state.get("status") == "completed",
                "native child Task must complete")
        entry = state.get("input")
        meta = state.get("metadata")
        clocks = state.get("time")
        require(type(entry) is dict and type(meta) is dict and type(clocks) is dict,
                "incomplete native child metadata")
        ident = entry.get("subagent_type")
        require(ident in ("kagari-v010-leaf-a", "kagari-v010-leaf-b")
                and type(ident) is str, "unexpected delegated Role")
        child, parent = meta.get("sessionId"), meta.get("parentSessionId")
        require(all(type(x) is str and x.startswith("ses_") for x in (child, parent)),
                "missing native parent-child session binding")
        require(parent == event.get("sessionID") == part.get("sessionID"),
                "native Task event parent-session mismatch")
        start, end = clocks.get("start"), clocks.get("end")
        require(type(start) is int and type(end) is int and 0 < start < end,
                "invalid native child execution interval")
        output = state.get("output")
        require(type(output) is str
                and '<task id="' + child + '" state="completed">' in output
                and re.search(r"(?:^|\n)status: COMPLETED(?:\n|$)", output) is not None,
                "child output lacks completed native result")
        model = meta.get("model")
        require(type(model) is dict and type(model.get("providerID")) is str
                and type(model.get("modelID")) is str, "native child model identity absent")
        calls.append({"role": ident, "child_session": child, "parent_session": parent,
                      "call_id": part.get("callID"), "start_ms": start, "end_ms": end,
                      "model": model})
    require(len(calls) == 2 and {x["role"] for x in calls}
            == {"kagari-v010-leaf-a", "kagari-v010-leaf-b"},
            "exactly two distinct native child roles required")
    require(len({x["child_session"] for x in calls}) == 2
            and len({x["call_id"] for x in calls}) == 2
            and len({x["parent_session"] for x in calls}) == 1,
            "distinct children must have one real parent session")
    overlap = min(x["end_ms"] for x in calls) - max(x["start_ms"] for x in calls)
    require(overlap > 0, "no overlapping live native subagent intervals")
    export_hashes = _verify_child_exports(project, events_file.parent, calls)

    require(git(project, "rev-parse", "HEAD").decode().strip() == setup["head"],
            "Git HEAD changed")
    require(digest((project / ".git/index").read_bytes()) == setup["index_sha256"],
            "pre-existing staged index changed")
    require(git(project, "diff", "--cached", "--name-only", "-z").split(b"\0")
            == [b"README.md", b""], "unrelated staged README not preserved")
    require((project / "AGENTS.md").read_bytes()
            == git(project, "show", "HEAD:AGENTS.md"),
            "Project-owned AGENTS.md modified")
    require(git(project, "diff", "--name-only", "-z") == b"",
            "unrelated unstaged tracked files changed")
    require((project / "README.md").read_bytes()
            == git(project, "show", ":README.md"),
            "staged README working copy modified")
    # The original R1 operator fixture captured HEAD/index but not Git config
    # SHA. Use its exact known git-init profile, not an inferred baseline.
    # A non-fixture project must supply separate trusted pre-run config evidence.
    standard_git_config = (
        b"[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n"
        b"\tbare = false\n\tlogallrefupdates = true\n"
    )
    require((project / ".git/config").read_bytes() == standard_git_config,
            "Git configuration deviated from pinned test-only git-init profile")
    for name in (".git/config", ".git/index", "AGENTS.md", "README.md"):
        info = (project / name).lstat()
        require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o644,
                "protected Project file mode or file type changed: " + name)
    # Include ignored files, directories, and symlinks: git ls-files --others
    # alone does NOT detect changes beneath an ignored directory.
    actual_files, actual_directories = set(), set()
    for current, directories, files in os.walk(project, followlinks=False):
        parent = Path(current)
        if parent == project:
            require(".git" in directories, "Git directory missing")
            directories.remove(".git")
        for name in directories:
            directory = parent / name
            require(directory.is_dir() and not directory.is_symlink(),
                    "unexpected Project directory link")
            actual_directories.add(directory.relative_to(project).as_posix())
        for name in files:
            actual_files.add((parent / name).relative_to(project).as_posix())
    require(actual_directories == {"docs"}
            and actual_files == {"AGENTS.md", "README.md", *allowed},
            "unexpected Project file or ignored-path modification")

    extras = [x.decode("utf-8", "strict") for x in
              git(project, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if x]
    require(set(extras) == set(allowed), "unrelated untracked product paths detected")
    instructions = (project / "AGENTS.md").read_text()
    require("inheritance_token=" + token in instructions,
            "Project instruction token missing")
    product_hashes = {}
    for label in ("a", "b"):
        rel = "docs/worker-" + label + ".md"
        target = project / rel
        info = target.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                "product is not an ordinary unaliased regular file")
        payload = target.read_bytes()
        require(("kagari_worker=" + label.upper()).encode() in payload
                and ("inheritance_token=" + token).encode() in payload,
                "child Product or Project instruction inheritance mismatch")
        product_hashes[rel] = digest(payload)
    return {
        "status": "REAL_NATIVE_TWO_SUBAGENTS_PARALLEL_PASS",
        "qualification": "LOCAL_DISPOSABLE_SUBAGENT_PATH_ONLY",
        "native_task_calls": 2,
        "common_parent_session": calls[0]["parent_session"],
        "overlap_ms": overlap,
        "children": sorted(calls, key=lambda x: x["role"]),
        "model_override_in_jsonl": "NOT_ATTESTED_BY_TOOL_EVENTS",
        "git_head_and_staged_index_preserved": True,
        "project_instructions_preserved_and_inherited": True,
        "product_sha256": product_hashes,
        "events_sha256": digest(raw),
        "native_session_exports_sha256": export_hashes,
        "git_config_test_fixture_profile_verified": True,
        "all_project_files_including_ignored_checked": True,
        "operator_setup_sha256": digest(setup_raw),
        "generic_task_qualified": False,
        "v010_release_qualified": False,
        "remote_publication": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--setup", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.project, args.setup, args.events)
    except (ProofBlocked, OSError, ValueError, KeyError, TypeError) as exc:
        result = {"status": "BLOCKED", "reason": str(exc)[:400],
                  "generic_task_qualified": False, "v010_release_qualified": False}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "REAL_NATIVE_TWO_SUBAGENTS_PARALLEL_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
