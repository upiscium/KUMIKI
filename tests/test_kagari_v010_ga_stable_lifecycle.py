"""KAGARI RC.1 non-invasive existing Git Project + managed Work Unit tests.

All effects are disposable fixture-only. No actual LLM, Human approval,
Project source Push/PR, or production release occurs in this suite.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


class ReleaseCandidateLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw=os.environ.get("KAGARI_V010_GA_STABLE_PACKAGE")
        if raw is None:
            raise RuntimeError("exact KAGARI_V010_GA_STABLE_PACKAGE must be built by canonical CI")
        cls.root=Path(raw)
        cls.cli=cls.root/"bin/kagari-v010-ga"
        cls.receipt=json.loads(
            (cls.root/"share/kagari-v010-ga/receipt.json").read_bytes())
        assert cls.cli.is_file()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="kagari-v010-ga-tests-")
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.project=self.folder/"source-project"
        self.project.mkdir()
        (self.project/"docs").mkdir()
        (self.project/"tests").mkdir()
        (self.project/"docs/a.md").write_text("A before\n")
        (self.project/"docs/b.md").write_text("B before\n")
        (self.project/"tests/test_product.py").write_text(
            "import unittest\n"
            "class Product(unittest.TestCase):\n"
            "  def test_a(self): self.assertTrue(True)\n"
            "  def test_b(self): self.assertTrue(True)\n")
        (self.project/"README.md").write_text("Original\n")
        (self.project/"AGENTS.md").write_text("# Original user rules\n")
        (self.project/"opencode.json").write_text('{"permission":{"task":"deny"}}\n')
        (self.project/"flake.nix").write_text('intentionally invalid user flake {')
        (self.project/"Justfile").write_text("myrecipe:\n    false\n")
        self.git("init","-q","-b","main")
        self.git("add","-A")
        self.git("-c","user.name=Fixture","-c",
                 "user.email=fixture@example.invalid","commit","-qm","baseline")
        self.head=self.git("rev-parse","HEAD").decode().strip()
        (self.project/"README.md").write_text("Unrelated user staged\n")
        self.git("add","README.md")
        self.index=(self.project/".git/index").read_bytes()
        self.before={
            name:(self.project/name).read_bytes() for name
            in ("AGENTS.md","opencode.json","flake.nix","Justfile","docs/a.md",
                "docs/b.md")
        }
        self.state=self.folder/"persistent-external-state"
        self.state.mkdir(mode=0o700)
        self.task=self.folder/"task-spec.json"
        spec={
            "schema_version":1,"goal":"Change A/B independently as authorized",
            "base_revision":self.head,
            "allowed_paths":["docs/a.md","docs/b.md"],
            "tests":[{"argv":[sys.executable,"-B","-m","unittest",
                                "discover","-s","tests","-q"],"cwd":"."}],
            "acceptance":"Both independent edits and host tests pass",
            "subagent_count":2,"planning":"auto"}
        self.task.write_text(json.dumps(spec)+"\n")
        self.program=Path(sys.executable).resolve()

    def git(self,*args):
        return subprocess.run(["git","-C",str(self.project),*args],
                              check=True,capture_output=True,timeout=20).stdout

    def run_cli(self,operation:str,*options):
        argv=[str(self.cli),operation,"--project-root",str(self.project),
              "--state-home",str(self.state),*map(str,options)]
        p=subprocess.run(argv,capture_output=True,text=True,timeout=90)
        try:
            body=json.loads(p.stdout)
        except ValueError:
            self.fail("invalid RC response: "+repr((p.returncode,p.stdout,p.stderr)))
        return p.returncode,body

    def task_options(self):
        return ("--task-spec",self.task,"--opencode",self.program,
                "--repository","fixture/rc-consumer","--task","27")

    def unchanged(self):
        self.assertEqual(self.git("rev-parse","HEAD").decode().strip(),self.head)
        self.assertEqual((self.project/".git/index").read_bytes(),self.index)
        for name,before in self.before.items():
            self.assertEqual((self.project/name).read_bytes(),before)

    def test_immutable_rc_identity_separates_external_release_and_runtime(self):
        p=subprocess.run([str(self.cli),"version"],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        d=json.loads(p.stdout)
        self.assertEqual(d["version"],"0.1.0")
        self.assertEqual(d["profile"],"GA_BOUNDED_ISOLATED_WORKUNIT")
        self.assertEqual(d["qualification"],"GA_BOUNDED_PROFILE_QUALIFIED")
        self.assertEqual(d["publication_attestation"],"EXTERNAL_UNVERIFIED")
        self.assertEqual(d["release_channel"],"stable")
        self.assertIs(d["production_activation"],False)
        self.assertNotIn("released",d)
        self.assertEqual(self.receipt["qualification"],"GA_BOUNDED_PROFILE_QUALIFIED")
        self.assertNotIn("released",self.receipt)
        self.assertFalse(self.receipt["direct_live_Project_Task_supported"])
        self.assertIs(self.receipt["managed_isolated_Work_Unit_supported"],True)
        self.assertEqual(self.receipt["cli_sha256"],hashlib.sha256(
            (self.root/"libexec/kagari-v010-ga/kagari_v010_ga_stable_lifecycle.py")
            .read_bytes()).hexdigest())
        self.assertEqual(self.receipt["native_component_qualification"],
                         "TEST_ONLY_UNQUALIFIED")

    def test_bounded_GA_qualification_evidence_is_source_bound(self):
        f=self.root/"share/kagari-v010-ga/bounded-qualification.json"
        report=json.loads(f.read_bytes())
        self.assertEqual(report["qualification"],"GA_BOUNDED_PROFILE_QUALIFIED")
        self.assertEqual(report["native_component_qualification"],"TEST_ONLY_UNQUALIFIED")
        self.assertIs(report["production_activation"],False)
        self.assertEqual(self.receipt["bounded_qualification_sha256"],
                         hashlib.sha256(f.read_bytes()).hexdigest())
        self.assertEqual(self.receipt["bounded_profile_id"],report["scope_id"])

    def test_install_repair_uninstall_reinstall_preserves_user_files(self):
        code,absent=self.run_cli("inspect")
        self.assertEqual(code,0,absent)
        self.assertEqual(absent["status"],"ABSENT")
        code,installed=self.run_cli("install")
        self.assertEqual(code,0,installed)
        self.assertEqual(installed["status"],"INSTALLED_GA_CANDIDATE")
        first=installed["installed_nonce"]
        self.unchanged()
        owner=Path(installed["attachment_root"])
        self.assertTrue((owner/"package").is_symlink())
        (owner/"package").unlink()
        code,missing=self.run_cli("inspect")
        self.assertEqual(missing["status"],"MISSING_OWNED_PACKAGE_LINK")
        code,repaired=self.run_cli("repair")
        self.assertEqual(code,0,repaired)
        self.assertEqual(repaired["status"],"INSTALLED_GA_CANDIDATE")
        self.assertEqual(repaired["installed_nonce"],first)
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")
        code,second=self.run_cli("install")
        self.assertEqual(code,0,second)
        self.assertNotEqual(second["installed_nonce"],first)
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")
        self.unchanged()

    def test_refuse_unknown_owner_redirects_and_project_state_overlap(self):
        code,installed=self.run_cli("install")
        self.assertEqual(code,0,installed)
        owner=Path(installed["attachment_root"])
        link=owner/"package"
        link.unlink()
        link.symlink_to(self.project, target_is_directory=True)
        code,fail=self.run_cli("uninstall")
        self.assertEqual(code,2)
        self.assertEqual(fail["status"],"BLOCKED")
        self.assertEqual(link.readlink(),self.project)
        link.unlink()
        (owner/"stranger-file").write_text("Not a RC-owned file")
        code,fail=self.run_cli("repair")
        self.assertEqual(code,2)
        self.assertTrue((owner/"stranger-file").exists())
        nested=self.project/"invalid-state"
        nested.mkdir(mode=0o700)
        argv=[str(self.cli),"install","--project-root",str(self.project),
              "--state-home",str(nested)]
        p=subprocess.run(argv,capture_output=True,text=True)
        self.assertEqual(p.returncode,2)
        self.assertEqual(json.loads(p.stdout)["status"],"BLOCKED")
        self.unchanged()

    def test_historic_show_after_original_head_advances_is_read_only(self):
        self.assertEqual(self.run_cli("install")[0], 0)
        code, prepared = self.run_cli("prepare-task", *self.task_options())
        self.assertEqual(code, 0, prepared)
        job_id = prepared["job_id"]
        job = self.state / "kagari-v010-ga-stable" / "history" / job_id
        before = {(job / name).read_bytes() for name in ("job.json", "task-request.json")}
        code, before_view = self.run_cli("history-show-task", "--job-id", job_id)
        self.assertEqual(code, 0, before_view)
        self.assertEqual(before_view["status"], "GA_JOB_NOT_DISPATCHED_HISTORIC")
        self.assertIs(before_view["task_execution_authorized"], False)
        self.assertFalse(before_view["run_dispatched"])
        self.assertEqual(before_view["source_head"], self.head)

        # Advance only the disposable original Project; old Task authority
        # must not be revived just because the history can now be read.
        self.git("-c", "user.name=Fixture", "-c",
                 "user.email=fixture@example.invalid",
                 "commit", "-qm", "advance disposable original")
        current = self.git("rev-parse", "HEAD").decode().strip()
        self.assertNotEqual(current, self.head)
        code, old = self.run_cli("show-task", "--job-id", job_id)
        self.assertEqual(code, 2, old)
        self.assertIn("Project_head", old["reason"])
        code, historic = self.run_cli("history-show-task", "--job-id", job_id)
        self.assertEqual(code, 0, historic)
        self.assertEqual(historic["current_project_head"], current)
        self.assertEqual(historic["source_head"], self.head)
        self.assertFalse(historic["task_execution_authorized"])
        self.assertEqual(before,
                         {(job / name).read_bytes() for name in ("job.json", "task-request.json")})

    def test_historic_show_does_not_require_active_installation(self):
        self.assertEqual(self.run_cli("install")[0], 0)
        code, prepared = self.run_cli("prepare-task", *self.task_options())
        self.assertEqual(code, 0, prepared)
        job_id = prepared["job_id"]
        self.assertEqual(self.run_cli("uninstall")[1]["status"], "ABSENT")
        code, historical = self.run_cli("history-show-task", "--job-id", job_id)
        self.assertEqual(code, 0, historical)
        self.assertEqual(historical["status"], "GA_JOB_NOT_DISPATCHED_HISTORIC")
        self.assertFalse(historical["task_execution_authorized"])
        self.unchanged()

    def test_historic_show_rejects_missing_approval_and_tampered_authority(self):
        self.assertEqual(self.run_cli("install")[0], 0)
        code, prepared = self.run_cli("prepare-task", *self.task_options())
        self.assertEqual(code, 0, prepared)
        job_id = prepared["job_id"]
        folder = self.state / "kagari-v010-ga-stable" / "history" / job_id
        record = json.loads((folder / "job.json").read_bytes())
        request = json.loads((folder / "task-request.json").read_bytes())
        # Synthetic dispatch marker cannot confer original missing grant.
        (folder / "run-dispatch.json").write_text(json.dumps({
            "schema_version": 1, "kind": "KAGARI_GA_ONE_SHOT_DISPATCH",
            "Task_request_id": request["request_id"],
            "TaskSpec_sha256": record["task_spec_sha256"],
            "Project_head": record["Project_head"],
            "native_receipt_sha256": record["native_receipt_sha256"]}))
        code, refused = self.run_cli("history-show-task", "--job-id", job_id)
        self.assertEqual(code, 2, refused)
        self.assertIn("required exact file missing", refused["reason"])
        (folder / "run-dispatch.json").unlink()
        record["ga_receipt_sha256"] = "0" * 64
        (folder / "job.json").write_text(json.dumps(
            record, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False) + "\n")
        code, refused = self.run_cli("history-show-task", "--job-id", job_id)
        self.assertEqual(code, 2, refused)
        self.assertIn("historic immutable Task binding mismatch", refused["reason"])
        self.assertFalse((folder / "result.json").exists())

    def _historic_dispatched_fixture(self):
        code, installed = self.run_cli("install")
        self.assertEqual(code, 0, installed)
        code, prepared = self.run_cli("prepare-task", *self.task_options())
        self.assertEqual(code, 0, prepared)
        job_id = prepared["job_id"]
        job = self.state / "kagari-v010-ga-stable" / "history" / job_id
        request = json.loads((job / "task-request.json").read_bytes())
        record = json.loads((job / "job.json").read_bytes())

        def frozen(name, document):
            payload = (json.dumps(document, sort_keys=True, ensure_ascii=False,
                                  separators=(",", ":")) + "\n").encode()
            dest = job / name
            self.assertFalse(dest.exists())
            dest.write_bytes(payload)
            dest.chmod(0o600)
            return payload

        approval = {
            "schema_version": 1, "kind": request["kind"],
            "request_id": request["request_id"],
            "request_sha256": hashlib.sha256(
                (json.dumps(request, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")) + "\n").encode()
            ).hexdigest(),
            "decision": "APPROVED",
            "origin": "LOCAL_FOREGROUND_TTY_NOT_REMOTE_VERIFIED",
        }
        frozen_approval = frozen("task-approval.json", approval)
        frozen("run-dispatch.json", {
            "schema_version": 1,
            "kind": "KAGARI_GA_ONE_SHOT_DISPATCH",
            "Task_request_id": request["request_id"],
            "Task_approval_sha256": hashlib.sha256(frozen_approval).hexdigest(),
            "TaskSpec_sha256": record["task_spec_sha256"],
            "Project_head": record["Project_head"],
            "native_receipt_sha256": record["native_receipt_sha256"],
        })
        return job_id, job, record, frozen

    def test_historic_dispatched_without_result_stays_unavailable(self):
        job_id, job, record, frozen = self._historic_dispatched_fixture()
        code, view = self.run_cli("history-show-task", "--job-id", job_id)
        self.assertEqual(code, 0, view)
        self.assertEqual(view["status"], "GA_JOB_RESULT_UNAVAILABLE")
        self.assertTrue(view["run_dispatched"])
        self.assertTrue(view["Task_approved"])
        self.assertFalse(view["task_execution_authorized"])
        self.assertIsNone(view["Product_commit"])
        self.assertFalse((job / "result.json").exists())
        code, refusal = self.run_cli("run-task", "--job-id", job_id)
        self.assertEqual(code, 2)
        self.assertFalse((job / "result.json").exists())
        self.unchanged()

    def test_historic_dispatched_terminal_blocked_remains_blocked(self):
        job_id, job, record, frozen = self._historic_dispatched_fixture()
        frozen("result.json", {
            "schema_version": 1, "kind": "KAGARI_GA_MANAGED_WORKUNIT_TASK_RESULT",
            "job_id": job_id, "original_Project_root": record["Project_root"],
            "native_runtime_receipt_sha256": record["native_receipt_sha256"],
            "status": "BLOCKED", "reason": "synthetic native leaf failed required read",
        })
        code, view = self.run_cli("history-show-task", "--job-id", job_id)
        self.assertEqual(code, 0, view)
        self.assertEqual(view["status"], "GA_JOB_BLOCKED")
        self.assertEqual(view["result_status"], "BLOCKED")
        self.assertIn("required read", view["result_reason"])
        self.assertIsNone(view["Product_commit"])
        self.assertFalse(view["task_execution_authorized"])
        self.unchanged()

    def test_historic_verified_terminal_success_uses_real_archive(self):
        job_id, job, record, frozen = self._historic_dispatched_fixture()
        clone = Path(record["workunit_clone"])
        (clone / "docs/a.md").write_text("A after\n")
        (clone / "docs/b.md").write_text("B after\n")
        subprocess.run(["git", "-C", str(clone), "add", "--", "docs/a.md", "docs/b.md"],
                       capture_output=True, check=True, timeout=20)
        subprocess.run(["git", "-C", str(clone), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid",
                        "-c", "core.hooksPath=/dev/null",
                        "commit", "-qm", "disposable authentic Product commit"],
                       capture_output=True, check=True, timeout=20)
        product = subprocess.check_output(
            ["git", "-C", str(clone), "rev-parse", "HEAD"], timeout=20).decode().strip()
        (job / "evidence").mkdir(mode=0o700)
        ids = {
            "contract_id": "a" * 64,
            "record_id": "b" * 64,
            "evidence_id": "c" * 64,
            "snapshot_id": "d" * 64,
        }
        native_readback = {
            **ids, "status": "FRESH_PROCESS_NATIVE_READBACK_PASS",
            "product_commit": product,
        }
        frozen("evidence/report.json", {
            "status": "REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS",
            "product_commit": product,
            "contract_id": ids["contract_id"],
            "record_id": ids["record_id"],
            "native": {"evidence_id": ids["evidence_id"],
                       "snapshot_id": ids["snapshot_id"]},
            "fresh_process_readback": native_readback,
        })
        outcome = {
            "schema_version": 1, "kind": "KAGARI_GA_MANAGED_WORKUNIT_TASK_RESULT",
            "job_id": job_id, "original_Project_root": record["Project_root"],
            "native_runtime_receipt_sha256": record["native_receipt_sha256"],
            "status": "GA_MANAGED_WORKUNIT_TASK_PASS",
            "Native_exit": 0, "Native_status": "REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS",
            "Product_commit": product,
            "native_v4_Evidence_readback": native_readback,
        }

        # The real archive function binds the actual binary Git patch, not a
        # mocked archive.verify return value.
        import importlib.util
        path = Path(__file__).resolve().parents[1] / (
            "tools/kagari_v010_ga_product_archive.py")
        module_spec = importlib.util.spec_from_file_location(
            "kagari_v010_ga_product_archive", path)
        artifact = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(artifact)
        self.assertEqual(
            artifact.archive(job, result_override=outcome)["product_commit"], product)
        frozen_result = frozen("result.json", outcome)
        before_sha = hashlib.sha256(frozen_result).hexdigest()
        code, view = self.run_cli("history-show-task", "--job-id", job_id)
        self.assertEqual(code, 0, view)
        self.assertEqual(view["status"], "GA_JOB_COMPLETED")
        self.assertEqual(view["Product_commit"], product)
        self.assertEqual(view["result_status"], "GA_MANAGED_WORKUNIT_TASK_PASS")
        self.assertEqual(view["result_sha256"], before_sha)
        self.assertFalse(view["task_execution_authorized"])
        self.assertTrue(view["historical_read_only"])
        self.assertEqual((job / "result.json").read_bytes(), frozen_result)
        self.unchanged()

        # A forged success is never accepted as historical fact.
        bad = {**outcome, "Product_commit": "f" * 40}
        (job / "result.json").write_bytes(
            (json.dumps(bad, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")) + "\n").encode())
        code, failure = self.run_cli("history-show-task", "--job-id", job_id)
        self.assertEqual(code, 2, failure)
        self.assertEqual(failure["status"], "BLOCKED")
        self.assertIn("archive verification failed", failure["reason"])
        (job / "result.json").write_bytes(frozen_result)
        # A forged Native readback field in a canonical successful result
        # must not be displayed as verified after the genuine archive succeeds.
        forged = json.loads(frozen_result)
        forged["native_v4_Evidence_readback"] = {
            **native_readback, "product_commit": "e" * 40}
        (job / "result.json").write_bytes(
            (json.dumps(forged, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")) + "\n").encode())
        code, failure = self.run_cli("history-show-task", "--job-id", job_id)
        self.assertEqual(code, 2, failure)
        self.assertEqual(failure["status"], "BLOCKED")
        self.assertIn("historic Native Evidence readback mismatch", failure["reason"])
        self.unchanged()

    def test_managed_workunit_requires_separate_ask_and_no_original_changes(self):
        self.assertEqual(self.run_cli("install")[0],0)
        code,pending=self.run_cli("prepare-task",*self.task_options())
        self.assertEqual(code,0,pending)
        self.assertEqual(pending["status"],
                         "GA_WORKUNIT_AWAITING_EXACT_HUMAN_APPROVAL")
        self.assertEqual(pending["source_head"],self.head)
        self.assertFalse(pending["model_invoked"])
        self.assertFalse(pending["source_remote_push_authorized"])
        jobid=pending["job_id"]
        clone=Path(pending["managed_Work_Unit"])
        self.assertTrue(clone.is_dir())
        self.assertEqual(subprocess.check_output(
            ["git","-C",str(clone),"remote"]).strip(),b"")
        self.assertEqual(subprocess.check_output(
            ["git","-C",str(clone),"rev-parse","--is-shallow-repository"]).strip(),b"false")
        self.assertEqual(subprocess.check_output(
            ["git","-C",str(clone),"rev-parse","HEAD"]).strip().decode(),self.head)
        self.unchanged()
        code,view=self.run_cli("show-task","--job-id",jobid)
        self.assertEqual(code,0,view)
        self.assertEqual(view["status"],"GA_JOB_AWAITING_NATIVE_ASKS")
        self.assertFalse(view["Task_approved"])
        code,blocked=self.run_cli("run-task","--job-id",jobid)
        self.assertEqual(code,2)
        self.assertIn("required exact file missing",blocked["reason"])
        self.assertFalse((self.state/"kagari-v010-ga-stable"/"history"/jobid/"evidence").exists())
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")
        self.unchanged()

    def test_approval_requires_actual_foreground_tty_never_auto_grants(self):
        self.assertEqual(self.run_cli("install")[0],0)
        _,pending=self.run_cli("prepare-task",*self.task_options())
        jobid=pending["job_id"]
        code,blocked=self.run_cli("approve-task","--job-id",jobid)
        self.assertEqual(code,2,blocked)
        self.assertIn("foreground interactive TTY",blocked["reason"])
        job=Path(pending["ga_task_request_id"])
        self.assertFalse((self.state/"kagari-v010-ga-stable"/"history"/jobid/
                          "task-approval.json").exists())
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")
        self.unchanged()

    def test_prepared_Job_cannot_outlive_install_nonce(self):
        self.assertEqual(self.run_cli("install")[0],0)
        code,pending=self.run_cli("prepare-task",*self.task_options())
        self.assertEqual(code,0)
        jobid=pending["job_id"]
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")
        self.assertEqual(self.run_cli("install")[1]["status"],"INSTALLED_GA_CANDIDATE")
        code,blocked=self.run_cli("run-task","--job-id",jobid)
        self.assertEqual(code,2)
        self.assertIn("ga_install_nonce",blocked["reason"])
        code,completed=self.run_cli("uninstall")
        self.assertEqual(code,0,completed)
        self.assertEqual(completed["status"],"ABSENT")
        self.assertIn(jobid,completed["historical_jobs_preserved"])
        self.unchanged()

    def test_uninstall_after_original_Project_head_advances(self):
        self.assertEqual(self.run_cli("install")[0],0)
        code,pending=self.run_cli("prepare-task",*self.task_options())
        self.assertEqual(code,0)
        (self.project/"docs/readme.md").write_text("unrelated new Product")
        self.git("add","docs/readme.md")
        self.git("-c","user.name=Fixture","-c","user.email=fixture@example.invalid",
                 "commit","--only","-qm","separate human Product work","--","docs/readme.md")
        code,detached=self.run_cli("uninstall")
        self.assertEqual(code,0,detached)
        self.assertEqual(detached["status"],"ABSENT")
        self.assertEqual((self.project/"AGENTS.md").read_bytes(),self.before["AGENTS.md"])
        self.assertTrue((self.project/"docs/readme.md").exists())
        self.assertEqual(self.git("diff","--cached","--name-only").strip(),b"README.md")


    def test_tampered_GA_owner_is_not_silently_repaired_or_deleted(self):
        code,installed=self.run_cli("install")
        self.assertEqual(code,0,installed)
        owner=Path(installed["attachment_root"])/"owner.json"
        before=owner.read_bytes()
        changed=json.loads(before)
        changed["ga_receipt_sha256"]="0"*64
        owner.write_text(json.dumps(changed,sort_keys=True,separators=(",",":"))+"\n")
        for action in ("inspect","repair","uninstall"):
            with self.subTest(action=action):
                rc,status=self.run_cli(action)
                self.assertEqual(rc,2,status)
                self.assertEqual(status["status"],"BLOCKED")
        self.assertTrue(owner.exists())
        self.unchanged()
        # The tester owns this disposable fixture and restores precisely the
        # locally retained bytes, never trusts tampered receipt as authority.
        owner.write_bytes(before)
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")

    def test_tampered_prepared_TaskSpec_never_dispatches_model(self):
        self.assertEqual(self.run_cli("install")[0],0)
        code,pending=self.run_cli("prepare-task",*self.task_options())
        self.assertEqual(code,0,pending)
        jobid=pending["job_id"]
        job=self.state/"kagari-v010-ga-stable"/"history"/jobid
        frozen=job/"task-spec.json"
        original=frozen.read_bytes()
        spec=json.loads(original)
        spec["goal"]="Unapproved replacement of originally consented Task"
        frozen.write_text(json.dumps(spec,sort_keys=True,separators=(",",":"))+"\n")
        code,status=self.run_cli("show-task","--job-id",jobid)
        self.assertEqual(code,2,status)
        self.assertIn("TaskSpec snapshot was altered",status["reason"])
        code,status=self.run_cli("run-task","--job-id",jobid)
        self.assertEqual(code,2,status)
        self.assertFalse((job/"run-dispatch.json").exists())
        self.assertFalse((job/"task-approval.json").exists())
        self.unchanged()
        frozen.write_bytes(original)
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")

    def test_immutable_ga_runner_verifies_archive_before_persisting_success(self):
        # Guard the exact packaged Task PASS order, not a test-local copy.
        installed=self.root/"libexec/kagari-v010-ga/kagari_v010_ga_stable_lifecycle.py"
        source=installed.read_text()
        import_name="import kagari_v010_ga_product_archive as product_archive"
        begin=source.index("    if success:\n        "+import_name)
        end=source.index("    return doc",begin)
        path=source[begin:end]
        self.assertIn('product_archive.archive(jobdir,result_override=doc)',path)
        self.assertIn('product_archive.verify(jobdir,result_override=doc)',path)
        self.assertIn('save_new(jobdir/"result.json",canonical(doc))',path)
        self.assertLess(path.index("product_archive.verify(jobdir,result_override=doc)"),
                        path.index('save_new(jobdir/"result.json",canonical(doc))'))
        self.assertNotIn('product_archive.verify(jobdir)\n    return doc',source)

    def test_original_task_input_edit_refused_before_WorkUnit_preparation(self):
        self.assertEqual(self.run_cli("install")[0],0)
        changed=json.loads(self.task.read_bytes())
        changed["base_revision"]="a"*40
        self.task.write_text(json.dumps(changed)+"\n")
        code,status=self.run_cli("prepare-task",*self.task_options())
        self.assertEqual(code,2,status)
        self.assertIn("baseline",status["reason"])
        self.assertFalse((self.state/"kagari-v010-ga-stable"/"history").exists())
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")
        self.unchanged()

if __name__=="__main__":
    unittest.main()
