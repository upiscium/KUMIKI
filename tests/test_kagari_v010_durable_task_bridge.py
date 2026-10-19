"""#275 durable Admin -> native Task: disposable-only Human Ask gate.

No actual model invocation, no Human approval, source publication, or Product
changes. Real configured-model acceptance needs independent explicit consent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class DurableDisposableTaskBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw=os.environ.get("KAGARI_V010_COMPOSED_PACKAGE")
        if not raw:
            raise RuntimeError("exact composed package output required")
        cls.package=Path(raw)
        cls.cli=cls.package/"bin/kagari-v010-durable-preview"
        cls.receipt=json.loads((cls.package/"share/kagari-v010-composed/receipt.json").read_bytes())
        cls.assertions={
            "qualification": "UNRELEASED_CANDIDATE",
            "production_activation": False,
            "consumer_production_qualified": False,
        }

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="kagari-v010-durable-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.other=tempfile.TemporaryDirectory(prefix="kagari-v010-durable-state-")
        self.addCleanup(self.other.cleanup)
        self.output_root=tempfile.TemporaryDirectory(prefix="kagari-v010-durable-evidence-")
        self.addCleanup(self.output_root.cleanup)
        self.project=Path(self.temp.name)/"project"
        self.state=Path(self.other.name)
        self.output=Path(self.output_root.name)/"evidence"
        self.project.mkdir()
        (self.project/"AGENTS.md").write_text("# User-owned Project instructions\n")
        (self.project/"opencode.json").write_text('{"permission":{"task":"deny"}}\n')
        (self.project/"docs").mkdir()
        (self.project/"docs/a.md").write_text("original A\n")
        (self.project/"docs/b.md").write_text("original B\n")
        (self.project/"README.md").write_text("user Product readme\n")
        self.git("init","-q","-b","main")
        self.git("add","-A")
        self.git("-c","user.name=Fixture","-c",
                 "user.email=fixture@example.invalid","commit","-qm","baseline")
        self.before_head=self.git("rev-parse","HEAD")
        self.before={key:(self.project/key).read_bytes() for key
                     in ("AGENTS.md","opencode.json","docs/a.md","docs/b.md","README.md")}
        self.task=Path(self.output_root.name)/"task.json"
        self.task.write_text(json.dumps({
            "schema_version":1,"goal":"Modify A and B while preserving user data",
            "base_revision":self.before_head,
            "allowed_paths":["docs/a.md","docs/b.md"],
            "tests":[],"acceptance":"Both files reflect two bounded edits",
            "subagent_count":2,"planning":"auto"},sort_keys=True)+"\n")

    def git(self,*argv):
        return subprocess.run(["git","-C",str(self.project),*argv],
                              check=True,capture_output=True,text=True,timeout=15).stdout.strip()

    def command(self,op,*opts):
        p=subprocess.run([
            str(self.cli),op,"--project-root",str(self.project),
            "--state-home",str(self.state),*map(str,opts)],
            capture_output=True,text=True,timeout=22)
        try:
            return p.returncode,json.loads(p.stdout)
        except ValueError:
            self.fail("CLI output was not JSON: "+repr((p.returncode,p.stdout,p.stderr)))

    def unchanged(self):
        self.assertEqual(self.git("rev-parse","HEAD"),self.before_head)
        for name,payload in self.before.items():
            self.assertEqual((self.project/name).read_bytes(),payload)
        self.assertFalse(self.output.exists())

    def test_only_exact_disposable_clone_can_request_model_task(self):
        status,installed=self.command("install")
        self.assertEqual(status,0,installed)
        self.assertEqual(installed["status"],"INSTALLED_TEST_ONLY")
        self.assertIn("NATIVE_TASK_PERMISSION",installed["required_asks"])
        no_explicit,status=self.command("run-task")
        self.assertEqual(no_explicit,2)
        self.assertIn("not qualified",status["reason"])
        partial,status=self.command("run-task","--disposable-task-test-only")
        self.assertEqual(partial,2)
        self.assertIn("requires absolute TaskSpec",status["reason"])
        code,blocked=self.command(
            "run-task","--disposable-task-test-only",
            "--task-spec",self.task,
            "--opencode","/usr/bin/false",
            "--output",self.output,
            "--repository","fixture/bridge","--task","275")
        self.assertEqual(code,2,blocked)
        self.assertEqual(blocked["status"],"BLOCKED")
        self.assertFalse(self.output.exists())
        self.unchanged()
        self.assertEqual(self.command("uninstall")[0],0)
        self.unchanged()

    def test_foreground_approval_still_required_no_auto_grant(self):
        code,installed=self.command("install")
        self.assertEqual(code,0,installed)
        self.assertIn("NATIVE_TASK_PERMISSION",installed["required_asks"])
        state=Path(installed["ask_state_root"])
        review=self.command("show-permission")
        self.assertEqual(review[0],0,review)
        self.assertEqual(review[1]["status"],"ASK_REVIEW")
        denied=self.command("approve-permission")
        self.assertEqual(denied[0],2)
        self.assertIn("fixture gate required",denied[1]["reason"])
        denied=self.command("approve-permission","--disposable-task-test-only")
        self.assertEqual(denied[0],2,denied)
        self.assertEqual(denied[1]["status"],"BLOCKED")
        self.assertFalse(any(state.rglob("decision.json")))
        self.assertFalse(any(state.rglob("activation.json")))
        self.assertEqual(self.command("uninstall")[0],0)
        self.unchanged()

    def test_nonfixture_path_or_remote_cannot_use_disposable_gate(self):
        self.assertEqual(self.command("install")[0],0)
        self.git("remote","add","origin","https://invalid.example.invalid/project.git")
        denied=self.command("run-task","--disposable-task-test-only",
                            "--task-spec",self.task,
                            "--opencode","/usr/bin/false",
                            "--output",self.output)
        self.assertEqual(denied[0],2)
        self.assertIn("no Git remotes",denied[1]["reason"])
        denied=self.command("approve-permission","--disposable-task-test-only")
        self.assertEqual(denied[0],2)
        self.git("remote","remove","origin")
        self.assertEqual(self.command("uninstall")[0],0)
        self.unchanged()

    def test_unrelated_project_is_not_a_disposable_fixture(self):
        self.assertEqual(self.command("install")[0],0)
        new_parent=Path(self.temp.name).parent
        other=new_parent/("not-a-durable-fixture-"+Path(self.temp.name).name)
        # Avoid affecting unrelated paths; no other Project exists.
        if other.exists(): self.skipTest("external path unexpectedly exists")
        other.mkdir(mode=0o700)
        self.addCleanup(lambda:other.rmdir() if other.exists() else None)
        linked=other/"project"
        linked.symlink_to(self.project, target_is_directory=True)
        code,blocked=self.command("run-task","--disposable-task-test-only",
                                  "--task-spec",self.task,
                                  "--opencode","/usr/bin/false",
                                  "--output",self.output)
        # The real, valid disposable path remains blocked without a grant.
        self.assertEqual(code,2,blocked)
        # Symlinked Project path cannot be an accepted durable Project root.
        process=subprocess.run(
            [str(self.cli),"run-task","--project-root",str(linked),
             "--state-home",str(self.state),"--disposable-task-test-only",
             "--task-spec",str(self.task),"--opencode","/usr/bin/false",
             "--output",str(self.output)],capture_output=True,text=True,timeout=20)
        self.assertEqual(process.returncode,2)
        self.assertEqual(json.loads(process.stdout)["status"],"BLOCKED")
        linked.unlink()
        self.assertEqual(self.command("uninstall")[0],0)
        self.unchanged()

    def test_task_arguments_do_not_apply_to_lifecycle(self):
        self.assertEqual(self.command("install")[0],0)
        code,blocked=self.command("uninstall","--task-spec",self.task)
        self.assertEqual(code,2)
        self.assertIn("not valid",blocked["reason"])
        code,blocked=self.command("inspect","--disposable-task-test-only")
        self.assertEqual(code,2)
        self.assertIn("not valid",blocked["reason"])
        self.assertEqual(self.command("uninstall")[0],0)
        self.unchanged()

    def test_package_receipt_remains_nonproduction(self):
        for k,v in self.assertions.items():
            self.assertEqual(self.receipt[k],v)
        self.assertFalse(self.receipt["durable_preview_qualified"])
        path=self.package/"libexec/kagari-v010-composed/kagari_v010_durable_admin_preview.py"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         self.receipt["durable_admin_sha256"])



    def bridge_files(self, installed: dict) -> list[Path]:
        runtime=Path(installed["runtime_root"])
        parent=runtime.parent
        return sorted(parent.glob("durable-bridge-asks-*.json"))

    def test_bridge_receipt_binds_new_composer_and_current_ask_generation(self):
        import stat
        code,installed=self.command("install")
        self.assertEqual(code,0,installed)
        files=self.bridge_files(installed)
        self.assertEqual(len(files),1,files)
        binding=files[0]
        self.assertEqual(stat.S_IMODE(binding.lstat().st_mode),0o600)
        record=json.loads(binding.read_bytes())
        self.assertEqual(record["candidate_package_root"],str(self.package))
        self.assertEqual(record["project_root"],str(self.project))
        self.assertEqual(record["native_owner_sha256"],
                         hashlib.sha256(
                            (Path(installed["runtime_root"])/"kagari-v010/owner.json")
                            .read_bytes()).hexdigest())
        self.assertEqual(record["candidate_receipt_sha256"],
                         hashlib.sha256(
                            (self.package/"share/kagari-v010-composed/receipt.json")
                            .read_bytes()).hexdigest())
        self.assertEqual(record["ask_state_root"],installed["ask_state_root"])
        self.assertFalse(record["human_permission_granted"])
        self.assertFalse(record["production_activation"])
        first_ask=installed["ask_state_root"]
        self.assertEqual(self.command("uninstall")[0],0)
        code,newer=self.command("install")
        self.assertEqual(code,0,newer)
        self.assertNotEqual(newer["ask_state_root"],first_ask)
        files=self.bridge_files(newer)
        self.assertEqual(len(files),2)
        self.assertEqual(self.command("uninstall")[0],0)
        self.unchanged()

    def test_older_or_missing_composing_bridge_does_not_reuse_ask(self):
        code,installed=self.command("install")
        self.assertEqual(code,0,installed)
        current=self.bridge_files(installed)
        self.assertEqual(len(current),1)
        current[0].unlink()
        code,blocked=self.command(
            "run-task","--disposable-task-test-only",
            "--task-spec",self.task,"--opencode","/usr/bin/false",
            "--output",self.output)
        self.assertEqual(code,2,blocked)
        self.assertIn("fresh Install/Ask generation",blocked["reason"])
        code,blocked=self.command("approve-permission","--disposable-task-test-only")
        self.assertEqual(code,2)
        self.assertIn("fresh Install/Ask generation",blocked["reason"])
        # Installing an existing Native owner MUST NOT manufacture new
        # authority for older Ask tickets.
        code,same=self.command("install")
        self.assertEqual(code,0,same)
        self.assertEqual(same["ask_state_root"],installed["ask_state_root"])
        self.assertEqual(self.bridge_files(same),[])
        self.assertEqual(self.command("uninstall")[0],0)
        code,new=self.command("install")
        self.assertEqual(code,0,new)
        self.assertNotEqual(new["ask_state_root"],installed["ask_state_root"])
        self.assertEqual(len(self.bridge_files(new)),1)
        code,still=self.command(
            "run-task","--disposable-task-test-only",
            "--task-spec",self.task,"--opencode","/usr/bin/false",
            "--output",self.output)
        self.assertEqual(code,2,still)
        self.assertNotIn("fresh Install/Ask generation",still["reason"])
        self.assertFalse(self.output.exists())
        self.assertEqual(self.command("uninstall")[0],0)
        self.unchanged()

    def test_modified_composing_binding_denies_task_and_answer(self):
        code,installed=self.command("install")
        self.assertEqual(code,0,installed)
        files=self.bridge_files(installed)
        self.assertEqual(len(files),1)
        record=json.loads(files[0].read_bytes())
        record["candidate_receipt_sha256"]="0"*64
        files[0].write_text(json.dumps(record,sort_keys=True,separators=(",",":"))+"\n")
        for op,options in [
            ("run-task",[
                "--task-spec",self.task,"--opencode","/usr/bin/false",
                "--output",self.output]),
            ("approve-permission",[]),
        ]:
            with self.subTest(op=op):
                code,blocked=self.command(op,"--disposable-task-test-only",*options)
                self.assertEqual(code,2,blocked)
                self.assertIn("composed runtime/Ask binding changed",blocked["reason"])
                self.assertEqual(blocked["status"],"BLOCKED")
        state=Path(installed["ask_state_root"])
        self.assertFalse(any(state.rglob("decision.json")))
        self.assertFalse(self.output.exists())
        # Uninstall does not treat the tampered, non-authority sidecar as a
        # grant and does not touch Project-owned AGENTS or Git bytes.
        self.assertEqual(self.command("uninstall")[0],0)
        self.unchanged()

if __name__=="__main__":
    unittest.main()
