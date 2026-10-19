"""v0.1 M1 disposable real-model acceptance; not a production release launcher.

Explicit --execute-model is required. Uses configured OpenCode build model,
existing v4 Contract/Evidence/View APIs, and an isolated disposable Git product.
No source publication, live consumer mutation, recovery expansion or fake PR.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from kagari_v4_opencode_namespace_probe import snapshot, tool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/agent-core-v4"))
from evidence import Evidence, encode_evidence
from metadata_ref import METADATA_REF, MetadataStore
from task_record import AuthorityRequest, TaskRecords, encode_contract, encode_record
from task_view import EvidenceRef, TaskViewSnapshots

REPO = "fixture/kagari-v010"
TASK = "271"
BRANCH = "refs/heads/task/v010-m1"
PRODUCTS = {"src/labels.py", "src/instruction_receipt.json"}
TITLE = "KAGARI 0.1.0 real model label normalization"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_argv() -> list[str]:
    return [
        sys.executable,
        "-I",
        "-B",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-v",
    ]


def exact_bash_output(part: dict, expected_argv: list[str]) -> str | None:
    state = part.get("state", {})
    if (
        part.get("tool") != "bash"
        or state.get("status") != "completed"
        or state.get("metadata", {}).get("exit") != 0
    ):
        return None
    try:
        actual = shlex.split(state.get("input", {}).get("command", ""))
    except (ValueError, TypeError):
        return None
    if actual != expected_argv:
        return None
    value = state.get("output")
    return value if isinstance(value, str) else None


def successful_model_tests(parts: list[dict]) -> bool:
    """Require exactly the agreed test argv, not a command printing test text."""
    for part in parts:
        output = exact_bash_output(part, test_argv())
        if output is not None and "Ran 9 tests" in output and "\nOK" in output:
            return True
    return False


def observed_git_state(parts: list[dict]) -> dict:
    heads, stages = [], []
    for part in parts:
        head = exact_bash_output(part, ["git", "rev-parse", "HEAD"])
        stage = exact_bash_output(part, ["git", "diff", "--cached", "--name-only"])
        if head is not None:
            heads.append(head.strip())
        if stage is not None:
            stages.append(stage.splitlines())
    return {
        "queried_head": heads[-1] if heads else None,
        "queried_staged_paths": stages[-1] if stages else None,
    }


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


MOUNT = r"""set -eu
project=$1;scratch=$2;mount=$3;opencode=$4;python=$5
"$mount" --bind "$project/src" "$scratch/product-alias"
"$mount" --bind "$project" "$project"
"$mount" -o remount,bind,ro "$project"
"$mount" --bind "$scratch/product-alias" "$project/src"
"$mount" -o remount,bind,rw "$project/src"
"$mount" -t overlay overlay -o "lowerdir=$project/.opencode,upperdir=$scratch/upper,workdir=$scratch/work,userxattr" "$project/.opencode"
readlink /proc/self/ns/mnt > "$scratch/namespace"
cd "$project"
exec "$python" -I -B "$scratch/model_exec.py" "$opencode" "$scratch/argv.json"
"""

TESTS = r"""import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from labels import normalize_label
class LabelsTest(unittest.TestCase):
    def test_spaces(self):self.assertEqual(normalize_label("  Hello \t World  "),"hello-world")
    def test_nfkc(self):self.assertEqual(normalize_label("ＡＢＣ"),"abc")
    def test_unicode(self):self.assertEqual(normalize_label("日本　語"),"日本-語")
    def test_casefold(self):self.assertEqual(normalize_label("Straße"),"strasse")
    def test_empty(self):
        with self.assertRaises(ValueError):normalize_label("")
    def test_whitespace(self):
        with self.assertRaises(ValueError):normalize_label(" \n\t")
    def test_none(self):
        with self.assertRaises(TypeError):normalize_label(None)
    def test_number(self):
        with self.assertRaises(TypeError):normalize_label(12)
    def test_idempotent(self):self.assertEqual(normalize_label("already-normal"),"already-normal")
if __name__=="__main__":unittest.main()
"""


class DisposableTask:
    def __init__(self, root: Path, output: Path):
        self.root, self.output = root, output
        self.project, self.remote, self.scratch = (
            root / "project",
            root / "metadata.git",
            root / "scratch",
        )
        self.project.mkdir()
        self.scratch.mkdir()
        for name in ("upper", "work", "product-alias", "external", "cache"):
            (self.scratch / name).mkdir()
        # Do not silently erase an operator's config/model selection. This
        # first support profile requires the normal configured global profile.
        selection_keys = (
            "OPENCODE_CONFIG",
            "OPENCODE_CONFIG_DIR",
            "OPENCODE_CONFIG_CONTENT",
            "OPENCODE_MODEL",
        )
        require(
            not any(os.environ.get(k) for k in selection_keys),
            "custom OPENCODE model/config selector requires explicit integration, not fallback",
        )
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.expected_model: str | None = None
        self.git_env = {
            **self.env,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
        self.git("init", "-q", "-b", "main")
        self.git("init", "--bare", "-q", str(self.remote))
        hook = self.remote / "hooks/reference-transaction"
        hook.write_text(
            "#!" + tool("sh") + '\n[ "$1" = prepared ] || exit 0\n'
            'while read -r old new ref; do\n if [ "$ref" = refs/agentcore/metadata ] && '
            + tool("git")
            + " symbolic-ref -q refs/agentcore/metadata >/dev/null 2>&1; then exit 1; fi\ndone\n"
        )
        hook.chmod(0o755)
        self.git("config", "user.name", "KAGARI Disposable Acceptance")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("remote", "add", "origin", str(self.remote))
        self.root_nonce, self.relative_nonce = (
            secrets.token_hex(12),
            secrets.token_hex(12),
        )
        for name in ("src", "tests", ".opencode/agents"):
            (self.project / name).mkdir(parents=True)
        (self.project / "src/labels.py").write_text(
            'def normalize_label(value: str) -> str:\n    raise NotImplementedError("implement the Project specification")\n'
        )
        (self.project / "tests/test_labels.py").write_text(TESTS)
        (self.project / "README.md").write_text(
            "Original unrelated product documentation\n"
        )
        (self.project / "AGENTS.md").write_text(
            "# Project-owned instructions\nImplement normalize_label by Unicode NFKC normalization, casefold, "
            "and whitespace collapse with hyphens. Only src/labels.py and src/instruction_receipt.json may change. "
            "Do not change tests, settings, Git state or instruction files.\n"
            "Write src/instruction_receipt.json with root_nonce="
            + self.root_nonce
            + ", relative_nonce from PROJECT_RULES.md, and cwd equal to your actual Project working directory. "
            "Do not copy or quote tokens to other locations.\nRun the following test command by itself, not chained with other commands: "
            + sys.executable
            + " -I -B -m unittest discover -s tests -v\n"
        )
        (self.project / "PROJECT_RULES.md").write_text(
            "# Additional Project instructions loaded by opencode.json\n"
            "Raise TypeError on non-str input and ValueError on empty/whitespace-only normalized output.\n"
            "The instruction_receipt.json relative_nonce must equal "
            + self.relative_nonce
            + ".\n"
        )
        (self.project / "opencode.json").write_text(
            json.dumps({"instructions": ["PROJECT_RULES.md"]}) + "\n"
        )
        (self.project / ".opencode/agents/project-notes.md").write_text(
            "---\ndescription: Project-owned role definition\nmode: primary\n---\nDo not modify this Project role.\n"
        )
        self.git("add", "--", ".")
        self.git("commit", "-q", "-m", "Disposable M1 product baseline")
        self.base = self.git("rev-parse", "HEAD")
        self.git("switch", "-q", "-c", "task/v010-m1")
        (self.project / "README.md").write_text(
            "Preserve this preexisting user-staged edit\n"
        )
        self.git("add", "README.md")
        self.user_stage = self.git("ls-files", "--stage", "--", "README.md")
        self.index = (self.project / ".git/index").read_bytes()
        self.before = snapshot(self.project)
        permissions = {
            "read": "allow",
            "task": "deny",
            "question": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "glob": "allow",
            "grep": "allow",
            "list": "allow",
            "edit": {
                "*": "deny",
                "src/*": "allow",
                str(self.project / "src/*"): "allow",
            },
            "bash": {
                "*": "deny",
                "python*": "allow",
                sys.executable + "*": "allow",
                "pwd": "allow",
                "git status*": "allow",
                "git rev-parse*": "allow",
                "git diff*": "allow",
            },
        }
        save(
            self.scratch / "external/opencode.json",
            {"$schema": "https://opencode.ai/config.json", "permission": permissions},
        )
        (self.scratch / "model_exec.py").write_text(
            "import json,os,sys\na=json.load(open(sys.argv[2]));os.execv(sys.argv[1],[sys.argv[1]]+a)\n"
        )
        self.allowed: set[AuthorityRequest] = set()
        self.store = self.new_store()
        self.records = TaskRecords(
            self.store, authorize=lambda req: req in self.allowed
        )

    def git(
        self,
        *args: str,
        at: Path | None = None,
        hooks: bool = False,
        check: bool = True,
    ) -> str:
        where = at or self.project
        require(where.is_relative_to(self.root), "disposable Git destination escaped")
        p = subprocess.run(
            [
                tool("git"),
                "-C",
                str(where),
                "-c",
                "core.hooksPath="
                + (str(self.remote / "hooks") if hooks else os.devnull),
                "-c",
                "commit.gpgSign=false",
                "-c",
                "core.fsmonitor=false",
                *args,
            ],
            env=self.git_env,
            capture_output=True,
            text=True,
            timeout=20,
            check=check,
        )
        return p.stdout.strip()

    def cas(self, candidate: str, expected: str | None) -> bool:
        require(
            self.git("show", "-s", "--format=%P", candidate) == (expected or ""),
            "metadata parent changed",
        )
        if self.git("symbolic-ref", "-q", METADATA_REF, at=self.remote, check=False):
            return False
        self.git(
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            str(self.project),
            candidate,
            at=self.remote,
        )
        try:
            self.git(
                "update-ref",
                "--no-deref",
                METADATA_REF,
                candidate,
                expected or "0" * len(candidate),
                at=self.remote,
                hooks=True,
            )
        except subprocess.CalledProcessError:
            return False
        return True

    def new_store(self) -> MetadataStore:
        return MetadataStore(
            self.project, REPO, str(self.remote), direct_ref_cas=self.cas
        )

    def create_contract(self) -> object:
        body = json.dumps(
            {
                "kind": "kagari-v010/model-task-acceptance",
                "allowed_paths": sorted(PRODUCTS),
                "test_command": sys.executable
                + " -I -B -m unittest discover -s tests -v",
                "project_instructions": {
                    name: digest((self.project / name).read_bytes())
                    for name in ("AGENTS.md", "PROJECT_RULES.md", "opencode.json")
                },
                "qualification": "DISPOSABLE_REAL_MODEL_NOT_RELEASE",
            },
            sort_keys=True,
        )
        cid, _ = encode_contract(
            REPO, TASK, self.base, {"schema_version": 1, "title": TITLE, "body": body}
        )
        rid, _ = encode_record(
            REPO,
            TASK,
            self.base,
            {"schema_version": 1, "branch_ref": BRANCH, "contract_id": cid},
        )
        self.allowed.add(
            AuthorityRequest("create", REPO, TASK, BRANCH, self.base, None, rid, cid)
        )
        receipt = self.records.create(
            task=TASK,
            branch_ref=BRANCH,
            base_revision=self.base,
            title=TITLE,
            body=body,
        )
        require(
            receipt.record_id == rid and receipt.contract_id == cid,
            "native Contract mismatch",
        )
        self.before = snapshot(self.project)
        self.index = (self.project / ".git/index").read_bytes()
        return receipt

    def model(
        self, executable: Path, prompt: str, *, session: str | None = None
    ) -> dict:
        args = ["--pure", "run", "--agent", "build", "--format", "json"]
        if session:
            args += ["--session", session]
        args += [prompt]
        save(self.scratch / "argv.json", args)
        configured = subprocess.run(
            [str(executable), "--pure", "debug", "agent", "build"],
            env=self.env,
            cwd="/",
            capture_output=True,
            timeout=30,
            check=True,
        )
        configured_model = json.loads(configured.stdout).get("model")
        require(
            isinstance(configured_model, dict)
            and isinstance(configured_model.get("providerID"), str)
            and isinstance(configured_model.get("modelID"), str),
            "configured build model identity unavailable",
        )
        expected_model = (
            configured_model["providerID"] + "/" + configured_model["modelID"]
        )
        if self.expected_model is not None:
            require(
                self.expected_model == expected_model,
                "configured model changed between turns",
            )
        self.expected_model = expected_model
        command = [
            tool("unshare"),
            "--user",
            "--map-root-user",
            "--mount",
            "--pid",
            "--fork",
            "--mount-proc",
            "--kill-child",
            "--propagation",
            "private",
            tool("sh"),
            "-c",
            MOUNT,
            "kagari-v010-real-model",
            str(self.project),
            str(self.scratch),
            tool("mount"),
            str(executable),
            sys.executable,
        ]
        env = {
            **self.env,
            "OPENCODE_CONFIG_DIR": str(self.scratch / "external"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        label = "resume" if session else "model"
        out, err = self.output / (label + ".jsonl"), self.output / (label + ".stderr")
        with out.open("wb") as stdout, err.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                env=env,
                cwd="/",
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            try:
                rc = proc.wait(timeout=180)
            except subprocess.TimeoutExpired:
                rc = 124
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
        out.chmod(0o600)
        err.chmod(0o600)
        require(
            out.stat().st_size <= 8 * 1024 * 1024,
            "model response exceeded evidence bound",
        )
        events = []
        for line in out.read_text(errors="replace").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                events.append(value)
        sessions = {v.get("sessionID") for v in events if v.get("sessionID")}
        tool_calls = [
            v.get("part", {}).get("tool") for v in events if v.get("type") == "tool_use"
        ]
        result = {
            "return_code": rc,
            "session_ids": sorted(sessions),
            "event_types": sorted({v.get("type", "unknown") for v in events}),
            "tool_calls": tool_calls,
            "transcript_sha256": digest(out.read_bytes()),
            "transcript": str(out),
            "stderr": str(err),
            "model_override": False,
        }
        completed_tools = [
            v.get("part", {})
            for v in events
            if v.get("type") == "tool_use"
            and v.get("part", {}).get("state", {}).get("status") == "completed"
        ]
        result["completed_tools"] = [v.get("tool") for v in completed_tools]
        result["model_attempted_product_tests"] = any(
            v.get("tool") == "bash"
            and "unittest" in str(v.get("state", {}).get("input", {}))
            for v in completed_tools
        )
        result["model_ran_product_tests"] = successful_model_tests(completed_tools)
        result["final_text"] = "\n".join(
            v.get("part", {}).get("text", "") for v in events if v.get("type") == "text"
        )
        if sessions:
            export = subprocess.run(
                [str(executable), "--pure", "export", min(sessions)],
                env=self.env,
                capture_output=True,
                timeout=30,
                check=True,
            )
            exported = json.loads(export.stdout)
            (self.output / (label + "-session.json")).write_bytes(export.stdout)
            (self.output / (label + "-session.json")).chmod(0o600)
            result["observed_models"] = sorted(
                {
                    str(message.get("info", {}).get("providerID"))
                    + "/"
                    + str(message.get("info", {}).get("modelID"))
                    for message in exported.get("messages", [])
                    if message.get("info", {}).get("role") == "assistant"
                }
            )
        result["expected_configured_model"] = expected_model
        result["configured_model_matched"] = result.get("observed_models") == [
            expected_model
        ]
        result.update(observed_git_state(completed_tools))
        save(self.output / (label + "-summary.json"), result)
        require(
            result["configured_model_matched"],
            "actual session model differs from configured build model",
        )
        return result

    def revalidate(self, receipt: object) -> None:
        require(
            self.git("rev-parse", "HEAD") == self.base,
            "product HEAD changed before commit",
        )
        require(
            (self.project / ".git/index").read_bytes() == self.index,
            "user index changed before commit",
        )
        current = self.records.read(
            self.store.fetch_tip(),
            task=TASK,
            base_revision=self.base,
            branch_ref=BRANCH,
        )
        require(
            current is not None and current[0] == receipt.record_id,
            "Task Record changed",
        )
        require(
            current[1]["payload"]["contract_id"] == receipt.contract_id,
            "Contract changed",
        )
        require("disposition" not in current[1]["payload"], "Task cancelled")

    def finish(self, receipt: object, model: dict) -> dict:
        after = snapshot(self.project)
        changed = {
            p
            for p in set(self.before) | set(after)
            if self.before.get(p) != after.get(p)
        }
        require(
            changed == PRODUCTS,
            "model edited outside Contract or omitted required output: "
            + repr(changed),
        )
        self.revalidate(receipt)
        functions = [
            node.name
            for node in ast.parse((self.project / "src/labels.py").read_text()).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        require(
            functions.count("normalize_label") == 1,
            "duplicate function definitions are not accepted",
        )
        proof = json.loads((self.project / "src/instruction_receipt.json").read_bytes())
        require(
            proof.get("root_nonce") == self.root_nonce,
            "root AGENTS instruction not demonstrated",
        )
        require(
            proof.get("relative_nonce") == self.relative_nonce,
            "relative Project rule not demonstrated",
        )
        require(proof.get("cwd") == str(self.project), "model Project cwd incorrect")
        test = subprocess.run(
            test_argv(),
            cwd=self.project,
            env={**self.env, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            check=False,
            timeout=30,
        )
        (self.output / "product-tests.log").write_bytes(test.stdout + test.stderr)
        require(
            test.returncode == 0 and b"Ran 9 tests" in test.stderr,
            "model product tests failed",
        )
        self.revalidate(receipt)
        self.git("add", "--", "src/instruction_receipt.json")
        self.git(
            "commit",
            "--only",
            "-q",
            "-m",
            "Implement model-generated label normalization",
            "--",
            *sorted(PRODUCTS),
        )
        commit = self.git("rev-parse", "HEAD")
        require(self.git("rev-parse", "HEAD^") == self.base, "product parent changed")
        require(
            set(
                self.git(
                    "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
                ).splitlines()
            )
            == PRODUCTS,
            "commit scope mismatch",
        )
        require(
            self.git("ls-files", "--stage", "--", "README.md") == self.user_stage,
            "unrelated user staged changes lost",
        )
        require(
            self.git("diff", "--cached", "--name-only") == "README.md",
            "user index not preserved",
        )
        eid, data = encode_evidence(
            REPO,
            TASK,
            commit,
            {
                "schema_version": 1,
                "kind": "kagari-v010/real-model-task",
                "producer": {"name": "OpenCode", "session_ids": model["session_ids"]},
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "payload": {
                    "contract_id": receipt.contract_id,
                    "record_id": receipt.record_id,
                    "product_commit": commit,
                    "test_count": 9,
                    "tests_pass": True,
                    "root_and_relative_instruction_behavior_verified": True,
                    "transcript_sha256": model["transcript_sha256"],
                    "released_runtime": False,
                },
            },
        )
        metadata = self.store.publish([data])
        Evidence(self.store).read(metadata, eid, task=TASK, subject=commit)
        authorization = lambda req: (
            req.task == TASK
            and req.repository == REPO
            and req.subject == commit
            and req.record_id == receipt.record_id
            and req.contract_id == receipt.contract_id
        )
        view = TaskViewSnapshots(self.store, authorize=authorization).capture(
            task=TASK,
            branch_ref=BRANCH,
            base_revision=self.base,
            boundary="explicit-handoff",
            selected_evidence=(EvidenceRef(eid, commit),),
            default_branch_ref="refs/heads/main",
        )
        fresh = self.new_store()
        persisted = TaskViewSnapshots(fresh, authorize=lambda _req: False).read(
            view.metadata_commit, view.snapshot_id, task=TASK, subject=commit
        )
        require(
            persisted["view"]["authority"]["contract_id"] == receipt.contract_id,
            "Task View binding mismatch",
        )
        for name in PRODUCTS:
            dest = self.output / Path(name).name
            shutil.copy2(self.project / name, dest)
            dest.chmod(0o600)
        return {
            "status": "REAL_MODEL_LOCAL_ROUNDTRIP_PASS",
            "product_commit": commit,
            "base": self.base,
            "record_id": receipt.record_id,
            "contract_id": receipt.contract_id,
            "evidence_id": eid,
            "metadata_commit": view.metadata_commit,
            "snapshot_id": view.snapshot_id,
            "selected_product_tests": 9,
            "instruction_behavior_verified": True,
            "unrelated_staged_readme_preserved": True,
            "task_view_fresh_client_readback": True,
            "model": model,
            "live_pr_tested": False,
            "release_ready": False,
        }


def execute(executable: Path, output: Path) -> dict:
    require(
        executable.is_absolute() and executable.is_file(), "OpenCode executable missing"
    )
    require(not output.exists(), "do not overwrite existing model acceptance evidence")
    output.mkdir(parents=True, mode=0o700)
    report: dict = {"status": "NOT_STARTED", "release_ready": False}
    try:
        with tempfile.TemporaryDirectory(prefix="kagari-v010-m1-") as tmp:
            root = Path(tmp)
            task = DisposableTask(root, output)
            receipt = task.create_contract()
            save(output / "contract-binding.json", asdict(receipt))
            prompt = (
                "Implement normalize_label in src/labels.py according to this Project's AGENTS.md, "
                "PROJECT_RULES.md and tests/test_labels.py. Produce src/instruction_receipt.json as the "
                "Project instructions require. Only those two product files may change. Run the existing "
                "unit tests. Do not stage, commit, push or alter settings. Complete this small acceptance "
                "task in this primary agent, without delegating it. Finish with a brief test result."
            )
            save(
                output / "requested-task.json",
                {
                    "prompt": prompt,
                    "project": str(task.project),
                    "root_nonce_hash": digest(task.root_nonce.encode()),
                    "relative_nonce_hash": digest(task.relative_nonce.encode()),
                },
            )
            print(
                "REAL_MODEL_STARTED configured_OpenCode_build_no_override", flush=True
            )
            model = task.model(executable, prompt)
            print("REAL_MODEL_FINISHED", json.dumps(model), flush=True)
            require(
                model["return_code"] == 0 and model["session_ids"],
                "actual model did not return a recorded session",
            )
            require(
                model["model_ran_product_tests"],
                "model did not execute the required product tests",
            )
            report = task.finish(receipt, model)
            report["model_primary_source_sha256"] = digest(Path(__file__).read_bytes())
            save(output / "report.json", report)
            print(
                "NATIVE_RECORD_EVIDENCE_TASKVIEW_SAVED",
                report["product_commit"],
                flush=True,
            )
            # A second actual model process resumes the same session from disk.
            before_resume = snapshot(task.project)
            resumed = task.model(
                executable,
                "Read the completed product and tests. Execute exactly these TWO commands SEPARATELY: "
                "(1) git rev-parse HEAD (2) git diff --cached --name-only. Do not chain commands. "
                "Report the full commit ID and which paths remain staged. Do not edit files, commit, or delegate.",
                session=model["session_ids"][0],
            )
            require(
                snapshot(task.project) == before_resume,
                "resume changed product/Git bytes",
            )
            report["model_resume"] = resumed
            report["model_session_resumed"] = (
                resumed["return_code"] == 0
                and resumed["session_ids"] == model["session_ids"]
                and resumed["configured_model_matched"]
                and resumed["queried_head"] == report["product_commit"]
                and resumed["queried_staged_paths"] == ["README.md"]
            )
            require(
                report["model_session_resumed"],
                "resumed model did not verify exact current commit and user stage",
            )
            state_path = task.scratch / "durable-readback.json"
            state = {"project": str(task.project), "remote": str(task.remote), **report}
            save(state_path, state)
            readback = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(Path(__file__).resolve()),
                    "--readback",
                    str(state_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            observed = json.loads(readback.stdout)
            require(
                observed["status"] == "FRESH_PROCESS_NATIVE_READBACK_PASS",
                "durable readback failed",
            )
            report["fresh_process_native_readback"] = observed
            with tarfile.open(
                output / "disposable-git-evidence.tar.gz", "w:gz"
            ) as archive:
                archive.add(task.project, arcname="project", recursive=True)
                archive.add(task.remote, arcname="metadata.git", recursive=True)
            (output / "disposable-git-evidence.tar.gz").chmod(0o600)
            report["fixture_archive_sha256"] = digest(
                (output / "disposable-git-evidence.tar.gz").read_bytes()
            )
        report["disposable_project_cleaned"] = not root.exists()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        report.update(status="BLOCKED", reason=str(exc)[:600])
        report["disposable_project_cleaned"] = (
            not Path(tmp).exists() if "tmp" in locals() else True
        )
    save(output / "report.json", report)
    return report


def readback(path: Path) -> dict:
    state = json.loads(path.read_bytes())
    store = MetadataStore(Path(state["project"]), REPO, state["remote"])
    records = TaskRecords(store, authorize=lambda _request: False)
    record = records.read(
        state["metadata_commit"],
        task=TASK,
        base_revision=state["base"],
        branch_ref=BRANCH,
    )
    require(
        record is not None and record[0] == state["record_id"],
        "fresh process Task Record differs",
    )
    evidence = Evidence(store).read(
        state["metadata_commit"],
        state["evidence_id"],
        task=TASK,
        subject=state["product_commit"],
    )
    require(
        evidence["payload"]["payload"]["contract_id"] == state["contract_id"],
        "fresh process Evidence binding differs",
    )
    view = TaskViewSnapshots(store, authorize=lambda _req: False).read(
        state["metadata_commit"],
        state["snapshot_id"],
        task=TASK,
        subject=state["product_commit"],
    )
    require(
        view["view"]["authority"]["record_id"] == state["record_id"],
        "fresh process snapshot differs",
    )
    return {
        "status": "FRESH_PROCESS_NATIVE_READBACK_PASS",
        "record_id": state["record_id"],
        "contract_id": state["contract_id"],
        "evidence_id": state["evidence_id"],
        "snapshot_id": state["snapshot_id"],
        "product_commit": state["product_commit"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-model", action="store_true")
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--readback", type=Path)
    args = parser.parse_args()
    if args.readback:
        print(json.dumps(readback(args.readback), sort_keys=True))
        return 0
    if not args.execute_model or args.executable is None or args.output is None:
        parser.error("actual configured model invocation requires --execute-model")
    result = execute(args.executable, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result["status"] == "REAL_MODEL_LOCAL_ROUNDTRIP_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
