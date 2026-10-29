"""Offline TaskSpec v1 validation and read-only ordinary Git Project inspection.

No model, test command, task dispatch, or Git mutation is performed here.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath


class TaskSpecError(ValueError):
    """Unsupported or unsafe TaskSpec / Project baseline."""


FIELDS = {"schema_version", "goal", "base_revision", "allowed_paths", "tests",
          "acceptance", "subagent_count", "planning"}
OPTIONAL_READS = "optional_exploration_paths"
PROTECTED = {".git", ".opencode", "AGENTS.md", "opencode.json", "opencode.jsonc",
             "flake.nix", "flake.lock", "Justfile", "justfile", "just"}
SHELLS = {"sh", "bash", "dash", "zsh", "fish", "csh", "tcsh", "cmd", "cmd.exe",
          "powershell", "pwsh", "env", "sudo"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TaskSpecError(message)


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON field: " + key)
        result[key] = value
    return result


def _path(value: object, root: Path, *, strict_v2: bool = False) -> str:
    # Preserve accepted legacy v1 lexical paths; v2 alone adds a bounded,
    # explicit optional-read contract and strict byte/DEL constraints.
    _require(isinstance(value, str) and bool(value) and "\\" not in value
             and not any(ord(char) < 32 for char in value),
             "invalid allowed path")
    if strict_v2:
        try:
            encoded = value.encode("utf-8", "strict")
        except UnicodeError:
            raise TaskSpecError("invalid UTF-8 v2 allowed path") from None
        _require(0 < len(encoded) <= 240 and chr(127) not in value,
                 "invalid v2 allowed path")
    parts = value.split("/")
    _require(all(part not in ("", ".", "..") for part in parts)
             and not value.startswith("/") and ":" not in parts[0]
             and not any(part in PROTECTED for part in parts), "unsafe allowed path: " + value)
    _require(PurePosixPath(value).as_posix() == value, "noncanonical allowed path")
    current = root
    for part in parts:
        current = current / part
        _require(not current.is_symlink(), "symlink in allowed path: " + value)
        if current.is_file():
            info = current.stat()
            _require(not stat.S_ISREG(info.st_mode) or info.st_nlink == 1,
                     "hard-linked product path is not supported: " + value)
    return value


def _root(project_root: Path) -> Path:
    root = Path(project_root)
    _require(root.is_absolute() and root.is_dir() and not root.is_symlink()
             and root.resolve() == root, "Project must be a real absolute directory")
    _require((root / ".git").is_dir() and not (root / ".git").is_symlink(),
             "ordinary Git Project with real .git directory required")
    return root


def load_task_spec(path: Path, project_root: Path, *,
                   allow_optional_v2: bool = False) -> dict:
    """Parse exact TaskSpec v1, or explicit native-only v2, without side effects.

    Raises TaskSpecError on invalid input; does not invoke Git or execute tests.
    """
    root = _root(project_root)
    source = Path(path)
    _require(source.is_file() and not source.is_symlink(), "TaskSpec must be a regular file")
    try:
        spec = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_object,
                          parse_constant=lambda _: (_require(False, "nonfinite JSON number")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TaskSpecError("invalid UTF-8 JSON TaskSpec") from exc
    _require(type(allow_optional_v2) is bool, "optional v2 enablement must be explicit")
    _require(type(spec) is dict and type(spec.get("schema_version")) is int
             and ((spec["schema_version"] == 1 and set(spec) == FIELDS)
                  or (allow_optional_v2 and spec["schema_version"] == 2
                      and set(spec) == FIELDS | {OPTIONAL_READS})),
             "TaskSpec must use exact v1 or approved native-only v2 optional-read schema")
    for key in ("goal", "acceptance"):
        _require(type(spec[key]) is str and bool(spec[key].strip()), key + " must be nonempty text")
    _require(type(spec["base_revision"]) is str
             and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", spec["base_revision"]) is not None,
             "base_revision must be a full lowercase Git object ID")
    paths = spec["allowed_paths"]
    _require(type(paths) is list and bool(paths), "allowed_paths must be nonempty")
    v2 = spec["schema_version"] == 2
    normalized = [_path(value, root, strict_v2=v2) for value in paths]
    _require(len(set(normalized)) == len(normalized), "duplicate allowed path")
    declared = spec.get(OPTIONAL_READS, [])
    _require(type(declared) is list and len(declared) <= 32,
             "optional exploratory paths must be a bounded list")
    optional = [_path(path, root, strict_v2=True) for path in declared]
    _require(len(set(optional)) == len(optional),
             "duplicate optional exploratory path")
    _require(not any(a == b or a.startswith(b + "/") or b.startswith(a + "/")
                     for a in optional for b in normalized),
             "optional inspection may not overlap required Product scope")
    tests = spec["tests"]
    _require(type(tests) is list, "tests must be an array")
    for test in tests:
        _require(type(test) is dict and set(test) == {"argv", "cwd"} and test["cwd"] == ".",
                 "test must contain only argv and cwd='.'")
        argv = test["argv"]
        _require(type(argv) is list and bool(argv) and all(type(arg) is str and bool(arg)
                 and not any(ord(c) < 32 for c in arg) for arg in argv),
                 "test argv must be nonempty literal strings")
        _require(Path(argv[0]).name.lower() not in SHELLS
                 and not any(re.search(r"[;&|`$<>\n\r]", arg) for arg in argv)
                 and not any(arg in ("-c", "--command", "-e", "--eval") for arg in argv),
                 "shell commands / evaluated code are not test argv")
    _require(type(spec["subagent_count"]) is int and spec["subagent_count"] >= 2,
             "subagent_count must be integer >= 2")
    _require(spec["planning"] in ("auto", "specified") and type(spec["planning"]) is str,
             "planning must be auto or specified")
    return {**spec, "allowed_paths": normalized,
            **({OPTIONAL_READS: optional} if OPTIONAL_READS in spec else {}),
            "tests": [{"argv": list(test["argv"]), "cwd": "."} for test in tests]}


def inspect_project(project: Path, spec: dict) -> dict:
    """Report matching baseline, staged, unstaged and untracked paths without writes.

    Requires a spec returned by load_task_spec for this Project. Raises
    TaskSpecError on Git errors, mismatched baseline, or nonordinary worktree.
    """
    root = _root(project)
    _require(type(spec) is dict and type(spec.get("base_revision")) is str
             and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", spec["base_revision"]) is not None,
             "invalid inspected baseline")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")

    def git(*args: str) -> bytes:
        try:
            return subprocess.run(["git", "--no-optional-locks", "-C", str(root),
                                   "-c", "core.fsmonitor=false", *args], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=20, check=True).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise TaskSpecError("Git read-only preflight failed: " + " ".join(args)) from exc

    _require(Path(os.fsdecode(git("rev-parse", "--show-toplevel")).strip()) == root,
             "Project is not the Git worktree root")
    head = git("rev-parse", "--verify", "HEAD").decode("ascii").strip()
    _require(head == spec["base_revision"], "Project HEAD differs from TaskSpec baseline")

    def paths(*args: str) -> list[str]:
        return sorted(os.fsdecode(p) for p in git(*args).split(b"\0") if p)

    # Separate snapshots: an index-only edit is staged, a worktree-only edit is
    # unstaged, and a new file is untracked (not silently treated as dirty).
    staged = paths("diff", "--cached", "--name-only", "-z", "--no-ext-diff", "HEAD")
    dirty = paths("diff", "--name-only", "-z", "--no-ext-diff")
    untracked = paths("ls-files", "--others", "--exclude-standard", "-z")
    return {"baseline": head, "staged_paths": staged, "dirty_paths": dirty,
            "untracked_paths": untracked}
