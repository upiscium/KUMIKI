"""M2 positive slice: run the M1 native Task against an already existing disposable Project.

The fixture owner creates and removes the TemporaryDirectory. This module never
creates or resets its Project; it fails closed before preparing external runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict
from pathlib import Path

import kagari_v010_model_task as m1


def no_mounts_below(path: Path) -> None:
    """Reject bind mounts too (st_dev alone cannot distinguish them). Linux-only."""
    with open("/proc/self/mountinfo", encoding="utf-8") as mounts:
        for line in mounts:
            fields = line.split()
            m1.require(len(fields) >= 5, "invalid Linux mountinfo")
            mount = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4])
            point = Path(mount)
            m1.require(point != path and not point.is_relative_to(path), "mount inside disposable area")


def secure_temp_dir(path: Path) -> None:
    mode = path.lstat()
    m1.require(stat.S_ISDIR(mode.st_mode) and mode.st_uid == os.geteuid()
               and stat.S_IMODE(mode.st_mode) == 0o700 and path.resolve() == path,
               "disposable directory must be owned, real and mode 0700")


def fixture_root(root: Path) -> None:
    host_temp = Path(tempfile.gettempdir()).resolve()
    m1.require(root.is_absolute() and root.parent == host_temp
               and host_temp.is_dir() and host_temp.resolve() == host_temp
               and root.name.startswith("kagari-v010-existing-"), "not an explicit host-temp fixture root")
    secure_temp_dir(root)
    no_mounts_below(root)
    for name in ("metadata.git", "scratch"):
        try:
            (root / name).lstat()
        except FileNotFoundError:
            pass
        else:
            m1.require(False, "preexisting runner sibling: " + name)
    m1.require({entry.name for entry in root.iterdir()} == {"project"}, "unknown fixture root contents")


def validate_output(output: Path, root: Path) -> None:
    host_temp = Path(tempfile.gettempdir()).resolve()
    m1.require(output.is_absolute() and output.parent.parent == host_temp
               and output.parent != root and output.resolve() == output
               and not output.exists() and not output.is_symlink(),
               "output must be new real external path under a separate host-temp directory")
    secure_temp_dir(output.parent)
    no_mounts_below(output.parent)


def preflight(project: Path) -> dict:
    m1.require(project.is_absolute() and project.name == "project", "absolute project/ required")
    root = project.parent
    fixture_root(root)
    m1.require(project.is_dir() and not project.is_symlink() and project.resolve() == project, "unsafe Project")
    m1.require(project.stat().st_uid == os.geteuid(), "Project not owned by current user")
    m1.require(root.stat().st_dev == project.stat().st_dev, "Project mount not supported")
    # No symlink anywhere in the worktree, including Git management paths. No
    # mounted subdirectories: the namespace policy relies on a plain worktree.
    for base, dirs, files in os.walk(project, followlinks=False):
        for name in dirs + files:
            path = Path(base) / name
            mode = path.lstat().st_mode
            m1.require(not stat.S_ISLNK(mode) and (stat.S_ISDIR(mode) or stat.S_ISREG(mode)), "symlink or special worktree entry")
            m1.require(path.stat().st_dev == root.stat().st_dev, "mounted worktree entry")
    required = {"AGENTS.md", "PROJECT_RULES.md", "opencode.json", "README.md", "src/labels.py", "tests/test_labels.py", ".opencode/agents/project-notes.md"}
    expected_paths = required | {"src", "tests", ".opencode", ".opencode/agents"}
    observed_paths = {
        relative
        for path in project.rglob("*")
        if (relative := path.relative_to(project).as_posix()) != ".git"
        and not relative.startswith(".git/")
    }
    m1.require(observed_paths == expected_paths, "unexpected Project paths including ignored instructions")
    for name in required:
        m1.require((project / name).is_file(), "missing fixture contract: " + name)
    m1.require((project / "tests/test_labels.py").read_text() == m1.TESTS, "unsupported product tests")
    m1.require((project / "src/labels.py").read_text() == 'def normalize_label(value: str) -> str:\n    raise NotImplementedError("implement the Project specification")\n', "unsupported source baseline")
    config = json.loads((project / "opencode.json").read_text())
    m1.require(config == {"instructions": ["PROJECT_RULES.md"]}, "unsupported Project settings")
    agents = (project / "AGENTS.md").read_text()
    rules = (project / "PROJECT_RULES.md").read_text()
    root_match = re.search(r"root_nonce=([0-9a-f]{24}), relative_nonce from PROJECT_RULES.md", agents)
    relative_match = re.search(r"instruction_receipt.json relative_nonce must equal ([0-9a-f]{24})\.", rules)
    m1.require(bool(root_match and relative_match), "missing Project instruction nonce contract")
    expected_agents = (
        "# Project-owned instructions\nImplement normalize_label by Unicode NFKC normalization, casefold, "
        "and whitespace collapse with hyphens. Only src/labels.py and src/instruction_receipt.json may change. "
        "Do not change tests, settings, Git state or instruction files.\n"
        "Write src/instruction_receipt.json with root_nonce=" + root_match.group(1)
        + ", relative_nonce from PROJECT_RULES.md, and cwd equal to your actual Project working directory. "
        "Do not copy or quote tokens to other locations.\nRun the following test command by itself, not chained with other commands: "
        + sys.executable + " -I -B -m unittest discover -s tests -v\n"
    )
    expected_rules = (
        "# Additional Project instructions loaded by opencode.json\n"
        "Raise TypeError on non-str input and ValueError on empty/whitespace-only normalized output.\n"
        "The instruction_receipt.json relative_nonce must equal " + relative_match.group(1) + ".\n"
    )
    m1.require((project / "AGENTS.md").read_bytes() == expected_agents.encode()
               and (project / "PROJECT_RULES.md").read_bytes() == expected_rules.encode(),
               "unsupported Project instructions")
    m1.require((project / ".opencode/agents/project-notes.md").read_text() == "---\ndescription: Project-owned role definition\nmode: primary\n---\nDo not modify this Project role.\n", "unsupported Project agent settings")
    m1.require((project / "README.md").read_text() == "Preserve this preexisting user-staged edit\n", "missing staged README fixture")
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1"}
    env = {k: v for k, v in env.items() if not k.startswith("GIT_") or k in {"GIT_OPTIONAL_LOCKS", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_NO_REPLACE_OBJECTS"}}

    def git(*args: str) -> str:
        return subprocess.run([m1.tool("git"), "-C", str(project), "-c", "core.fsmonitor=false", *args], env=env, text=True, capture_output=True, timeout=20, check=True).stdout.strip()

    m1.require((project / ".git").is_dir(), "ordinary Git worktree required")
    m1.require(git("symbolic-ref", "HEAD") == "refs/heads/task/v010-m1", "unexpected branch")
    head = git("rev-parse", "HEAD")
    m1.require(git("rev-parse", "main") == head, "fixture baseline HEAD differs")
    m1.require(git("status", "--porcelain=v1", "--untracked-files=all") == "M  README.md", "unexpected dirty product")
    m1.require(git("diff", "--cached", "--name-only") == "README.md", "unexpected staged paths")
    m1.require(git("diff", "--name-only") == "", "unstaged product changes")
    m1.require(set(git("ls-files").splitlines()) == required, "unsupported tracked inventory")
    m1.require(git("show", "HEAD:README.md") == "Original unrelated product documentation", "unsupported README baseline")
    m1.require(git("config", "--local", "--get", "user.email") == "fixture@example.invalid", "unsupported Git identity")
    return {"root": root, "base": head, "index": (project / ".git/index").read_bytes(), "user_stage": git("ls-files", "--stage", "--", "README.md"), "before": m1.snapshot(project), "root_nonce": root_match.group(1), "relative_nonce": relative_match.group(1)}


class ExistingTask(m1.DisposableTask):
    def __init__(self, project: Path, output: Path, state: dict):
        # Intentionally do not call DisposableTask.__init__: that constructor
        # mkdirs/writes a new Project. Reuse its native model/contract/finish.
        m1.require(project == state["root"] / "project", "Project root changed")
        fixture_root(state["root"])
        self.project, self.root, self.output = project, state["root"], output
        self.remote, self.scratch = self.root / "metadata.git", self.root / "scratch"
        self.base, self.index, self.user_stage, self.before = (state[k] for k in ("base", "index", "user_stage", "before"))
        self.root_nonce, self.relative_nonce = state["root_nonce"], state["relative_nonce"]
        selectors = ("OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT", "OPENCODE_MODEL")
        m1.require(not any(os.environ.get(k) for k in selectors), "custom OPENCODE model/config selector requires explicit integration, not fallback")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.expected_model = None
        self.git_env = {**self.env, "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1"}
        self.assert_entry(state)
        self.scratch.mkdir()
        for name in ("upper", "work", "product-alias", "external", "cache"):
            (self.scratch / name).mkdir()
        self.git("init", "--bare", "-q", str(self.remote))
        hook = self.remote / "hooks/reference-transaction"
        hook.write_text("#!" + m1.tool("sh") + '\n[ "$1" = prepared ] || exit 0\n' + 'while read -r old new ref; do\n if [ "$ref" = refs/agentcore/metadata ] && ' + m1.tool("git") + ' symbolic-ref -q refs/agentcore/metadata >/dev/null 2>&1; then exit 1; fi\ndone\n')
        hook.chmod(0o755)
        permissions = {"read": "allow", "task": "deny", "question": "deny", "webfetch": "deny", "websearch": "deny", "glob": "allow", "grep": "allow", "list": "allow", "edit": {"*": "deny", "src/*": "allow", str(project / "src/*"): "allow"}, "bash": {"*": "deny", "python*": "allow", sys.executable + "*": "allow", "pwd": "allow", "git status*": "allow", "git rev-parse*": "allow", "git diff*": "allow"}}
        m1.save(self.scratch / "external/opencode.json", {"$schema": "https://opencode.ai/config.json", "permission": permissions})
        (self.scratch / "model_exec.py").write_text("import json,os,sys\na=json.load(open(sys.argv[2]));os.execv(sys.argv[1],[sys.argv[1]]+a)\n")
        self.allowed = set()
        self.store = self.new_store()
        self.records = m1.TaskRecords(self.store, authorize=lambda req: req in self.allowed)

    def assert_entry(self, state: dict) -> None:
        m1.require(self.git("rev-parse", "HEAD") == state["base"] and (self.project / ".git/index").read_bytes() == state["index"] and m1.snapshot(self.project) == state["before"], "Project changed after preflight")


def execute(project: Path, executable: Path, output: Path) -> dict:
    state = preflight(project)  # all validation before any runner-created artifact
    m1.require(executable.is_absolute() and executable.is_file(), "OpenCode executable missing")
    validate_output(output, state["root"])
    fixture_root(state["root"])
    output.mkdir(mode=0o700)
    report: dict = {"status": "NOT_STARTED", "release_ready": False}
    try:
        task = ExistingTask(project, output, state)
        task.assert_entry(state)
        receipt = task.create_contract()
        m1.save(output / "contract-binding.json", asdict(receipt))
        prompt = ("Implement normalize_label in src/labels.py according to this Project's AGENTS.md, PROJECT_RULES.md and tests/test_labels.py. Produce src/instruction_receipt.json as the Project instructions require. Only those two product files may change. Run the existing unit tests. Do not stage, commit, push or alter settings. Complete this small acceptance task in this primary agent, without delegating it. Finish with a brief test result.")
        m1.save(output / "requested-task.json", {"prompt": prompt, "project": str(project), "root_nonce_hash": m1.digest(state["root_nonce"].encode()), "relative_nonce_hash": m1.digest(state["relative_nonce"].encode())})
        print("REAL_MODEL_STARTED configured_OpenCode_build_no_override", flush=True)
        model = task.model(executable, prompt)
        m1.require(model["return_code"] == 0 and model["session_ids"] and model["model_ran_product_tests"], "model session or exact product test failed")
        report = task.finish(receipt, model)
        report["preexisting_project"] = True
        report["model_primary_source_sha256"] = m1.digest(Path(__file__).read_bytes())
        m1.save(output / "report.json", report)
        before_resume = m1.snapshot(project)
        resumed = task.model(executable, "Read the completed product and tests. Execute exactly these TWO commands SEPARATELY: (1) git rev-parse HEAD (2) git diff --cached --name-only. Do not chain commands. Report the full commit ID and which paths remain staged. Do not edit files, commit, or delegate.", session=model["session_ids"][0])
        m1.require(m1.snapshot(project) == before_resume, "resume changed product/Git bytes")
        report["model_resume"] = resumed
        report["model_session_resumed"] = (resumed["return_code"] == 0 and resumed["session_ids"] == model["session_ids"] and resumed["configured_model_matched"] and resumed["queried_head"] == report["product_commit"] and resumed["queried_staged_paths"] == ["README.md"])
        m1.require(report["model_session_resumed"], "resumed model did not verify exact commit and user stage")
        readback_state = task.scratch / "durable-readback.json"
        m1.save(readback_state, {"project": str(project), "remote": str(task.remote), **report})
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--readback", str(readback_state)], capture_output=True, text=True, timeout=30, check=True)
        observed = json.loads(result.stdout)
        m1.require(observed["status"] == "FRESH_PROCESS_NATIVE_READBACK_PASS", "durable readback failed")
        report["fresh_process_native_readback"] = observed
        with tarfile.open(output / "disposable-git-evidence.tar.gz", "w:gz") as archive:
            archive.add(project, arcname="project", recursive=True)
            archive.add(task.remote, arcname="metadata.git", recursive=True)
        (output / "disposable-git-evidence.tar.gz").chmod(0o600)
        report["fixture_archive_sha256"] = m1.digest((output / "disposable-git-evidence.tar.gz").read_bytes())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        report.update(status="BLOCKED", reason=str(exc)[:600])
    m1.save(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute-model", action="store_true")
    parser.add_argument("--readback", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.readback:
        print(json.dumps(m1.readback(args.readback), sort_keys=True))
        return 0
    if not args.execute_model or not all((args.project_root, args.executable, args.output)):
        parser.error("explicit --execute-model, --project-root ABS, --executable ABS and --output ABS required")
    try:
        result = execute(args.project_root, args.executable, args.output)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)[:600]}))
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "REAL_MODEL_LOCAL_ROUNDTRIP_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
