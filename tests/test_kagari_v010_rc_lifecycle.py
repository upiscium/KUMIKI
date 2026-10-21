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
        raw=os.environ.get("KAGARI_V010_RC_PACKAGE")
        if raw is None:
            raise RuntimeError("exact KAGARI_V010_RC_PACKAGE must be built by canonical CI")
        cls.root=Path(raw)
        cls.cli=cls.root/"bin/kagari-v010-rc"
        cls.receipt=json.loads(
            (cls.root/"share/kagari-v010-rc/receipt.json").read_bytes())
        assert cls.cli.is_file()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="kagari-v010-rc-tests-")
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
        self.assertEqual(d["version"],"0.1.0-rc.1")
        self.assertEqual(d["profile"],"RC_MANAGED_ISOLATED_WORKUNIT")
        self.assertEqual(d["qualification"],"RC_RESTRICTED_PROFILE")
        self.assertEqual(d["publication_attestation"],"EXTERNAL_UNVERIFIED")
        self.assertEqual(d["release_channel"],"prerelease")
        self.assertIs(d["production_activation"],False)
        self.assertNotIn("released",d)
        self.assertEqual(self.receipt["qualification"],"RC_RESTRICTED_PROFILE")
        self.assertNotIn("released",self.receipt)
        self.assertFalse(self.receipt["direct_live_Project_Task_supported"])
        self.assertIs(self.receipt["managed_isolated_Work_Unit_supported"],True)
        self.assertEqual(self.receipt["cli_sha256"],hashlib.sha256(
            (self.root/"libexec/kagari-v010-rc/kagari_v010_rc_lifecycle.py")
            .read_bytes()).hexdigest())
        self.assertEqual(self.receipt["native_component_qualification"],
                         "TEST_ONLY_UNQUALIFIED")

    def test_install_repair_uninstall_reinstall_preserves_user_files(self):
        code,absent=self.run_cli("inspect")
        self.assertEqual(code,0,absent)
        self.assertEqual(absent["status"],"ABSENT")
        code,installed=self.run_cli("install")
        self.assertEqual(code,0,installed)
        self.assertEqual(installed["status"],"INSTALLED_RC_CANDIDATE")
        first=installed["installed_nonce"]
        self.unchanged()
        owner=Path(installed["attachment_root"])
        self.assertTrue((owner/"package").is_symlink())
        (owner/"package").unlink()
        code,missing=self.run_cli("inspect")
        self.assertEqual(missing["status"],"MISSING_OWNED_PACKAGE_LINK")
        code,repaired=self.run_cli("repair")
        self.assertEqual(code,0,repaired)
        self.assertEqual(repaired["status"],"INSTALLED_RC_CANDIDATE")
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

    def test_managed_workunit_requires_separate_ask_and_no_original_changes(self):
        self.assertEqual(self.run_cli("install")[0],0)
        code,pending=self.run_cli("prepare-task",*self.task_options())
        self.assertEqual(code,0,pending)
        self.assertEqual(pending["status"],
                         "RC_WORKUNIT_AWAITING_EXACT_HUMAN_APPROVAL")
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
        self.assertEqual(view["status"],"RC_JOB_AWAITING_NATIVE_ASKS")
        self.assertFalse(view["Task_approved"])
        code,blocked=self.run_cli("run-task","--job-id",jobid)
        self.assertEqual(code,2)
        self.assertIn("required exact file missing",blocked["reason"])
        self.assertFalse((self.state/"kagari-v010-rc"/"history"/jobid/"evidence").exists())
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")
        self.unchanged()

    def test_approval_requires_actual_foreground_tty_never_auto_grants(self):
        self.assertEqual(self.run_cli("install")[0],0)
        _,pending=self.run_cli("prepare-task",*self.task_options())
        jobid=pending["job_id"]
        code,blocked=self.run_cli("approve-task","--job-id",jobid)
        self.assertEqual(code,2,blocked)
        self.assertIn("foreground interactive TTY",blocked["reason"])
        job=Path(pending["rc_task_request_id"])
        self.assertFalse((self.state/"kagari-v010-rc"/"history"/jobid/
                          "task-approval.json").exists())
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")
        self.unchanged()

    def test_prepared_Job_cannot_outlive_install_nonce(self):
        self.assertEqual(self.run_cli("install")[0],0)
        code,pending=self.run_cli("prepare-task",*self.task_options())
        self.assertEqual(code,0)
        jobid=pending["job_id"]
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")
        self.assertEqual(self.run_cli("install")[1]["status"],"INSTALLED_RC_CANDIDATE")
        code,blocked=self.run_cli("run-task","--job-id",jobid)
        self.assertEqual(code,2)
        self.assertIn("rc_install_nonce",blocked["reason"])
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


    def test_tampered_RC_owner_is_not_silently_repaired_or_deleted(self):
        code,installed=self.run_cli("install")
        self.assertEqual(code,0,installed)
        owner=Path(installed["attachment_root"])/"owner.json"
        before=owner.read_bytes()
        changed=json.loads(before)
        changed["rc_receipt_sha256"]="0"*64
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
        job=self.state/"kagari-v010-rc"/"history"/jobid
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

    def test_original_task_input_edit_refused_before_WorkUnit_preparation(self):
        self.assertEqual(self.run_cli("install")[0],0)
        changed=json.loads(self.task.read_bytes())
        changed["base_revision"]="a"*40
        self.task.write_text(json.dumps(changed)+"\n")
        code,status=self.run_cli("prepare-task",*self.task_options())
        self.assertEqual(code,2,status)
        self.assertIn("baseline",status["reason"])
        self.assertFalse((self.state/"kagari-v010-rc"/"history").exists())
        self.assertEqual(self.run_cli("uninstall")[1]["status"],"ABSENT")
        self.unchanged()

if __name__=="__main__":
    unittest.main()
