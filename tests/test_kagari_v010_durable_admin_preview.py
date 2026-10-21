"""#275 durable-local UNRELEASED lifecycle preview on real Git topology fixtures."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class DurableAdminPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value=os.environ.get("KAGARI_V010_COMPOSED_PACKAGE","")
        if not value:
            raise RuntimeError("exact composed candidate package required")
        cls.package=Path(value)
        cls.cli=cls.package/"bin/kagari-v010-durable-preview"
        cls.receipt=json.loads(
          (cls.package/"share/kagari-v010-composed/receipt.json").read_text())

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="kagari-durable-preview-")
        self.addCleanup(self.temp.cleanup)
        scratch=Path(self.temp.name)
        self.project=scratch/"project"
        self.project.mkdir()
        self.state=scratch/"state"
        self.state.mkdir(mode=0o700)
        (self.project/"README.md").write_text("original user product\n")
        (self.project/"AGENTS.md").write_text("# Custom Project rules\n")
        (self.project/"opencode.json").write_text('{"permission":{"task":"deny"}}\n')
        self.git("init","-q","-b","main")
        self.git("add","-A")
        self.git("-c","user.name=Fixture","-c","user.email=test@example.invalid",
                 "commit","-qm","baseline")
        self.original_head=self.git("rev-parse","HEAD")
        self.orig_files={name:(self.project/name).read_bytes()
                         for name in ("AGENTS.md","opencode.json")}
        (self.project/"README.md").write_text("unrelated user staged modification\n")
        self.git("add","README.md")
        self.original_index=(self.project/".git/index").read_bytes()

    def git(self,*args):
        return subprocess.run(["git","-C",str(self.project),*args],
                              capture_output=True,check=True,timeout=15).stdout.decode().strip()

    def call(self,operation,*extras):
        args=[str(self.cli),operation,
              "--project-root",str(self.project),
              "--state-home",str(self.state),*extras]
        result=subprocess.run(args,capture_output=True,text=True,timeout=30)
        try:body=json.loads(result.stdout)
        except (TypeError,ValueError):
            self.fail("unparsable durable CLI: "+repr((result.returncode,result.stderr,result.stdout)))
        return result.returncode,body

    def untouched(self):
        self.assertEqual(self.git("rev-parse","HEAD"),self.original_head)
        self.assertEqual((self.project/".git/index").read_bytes(),self.original_index)
        for name,raw in self.orig_files.items():
            self.assertEqual((self.project/name).read_bytes(),raw)
        self.assertEqual(self.git("diff","--cached","--name-only"),"README.md")

    def test_receipt_binds_preview_source_without_claiming_release(self):
        receipt=self.receipt
        self.assertEqual(receipt["version"],"0.1.0")
        self.assertIs(receipt["durable_preview_qualified"],False)
        self.assertIs(receipt["consumer_production_qualified"],False)
        for name,field in (("kagari_v010_durable_admin_preview.py","durable_admin_sha256"),
                           ("kagari_v010_durable_location.py","durable_location_sha256")):
            p=self.package/"libexec/kagari-v010-composed"/name
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),receipt[field])
        self.assertTrue(self.cli.is_file())

    def test_durable_nonproduction_lifecycle_preserves_Git_and_asks(self):
        rc,info=self.call("inspect")
        self.assertEqual(rc,0,info)
        self.assertEqual(info["status"],"ABSENT")
        self.assertFalse((self.state/"kagari-v010").exists())
        rc,installed=self.call("install")
        self.assertEqual(rc,0,installed)
        self.assertEqual(installed["status"],"INSTALLED_TEST_ONLY")
        self.assertEqual(installed["qualification"],"DURABLE_PREVIEW_UNRELEASED")
        self.assertFalse(installed["production_install_qualified"])
        self.assertEqual(installed["required_asks"],["NATIVE_TASK_PERMISSION"])
        self.assertFalse(installed["ready_for_model_task"])
        first=installed["ask_state_root"]
        self.assertTrue(Path(installed["runtime_root"]).exists())
        self.untouched()
        rc,blocked=self.call("run-task")
        self.assertEqual(rc,2)
        self.assertEqual(blocked["status"],"BLOCKED")
        self.assertIn("not qualified",blocked["reason"])
        owned=Path(installed["runtime_root"])/"kagari-v010/package"
        self.assertTrue(owned.is_symlink())
        owned.unlink()
        rc,missing=self.call("inspect")
        self.assertEqual(rc,0,missing)
        self.assertEqual(missing["status"],"MISSING_OWNED_LINK")
        rc,repaired=self.call("repair")
        self.assertEqual(rc,0,repaired)
        self.assertEqual(repaired["status"],"INSTALLED_TEST_ONLY")
        self.assertEqual(repaired["ask_state_root"],first)
        rc,removed=self.call("uninstall")
        self.assertEqual(rc,0,removed)
        self.assertEqual(removed["status"],"ABSENT")
        rc,reinstalled=self.call("install")
        self.assertEqual(rc,0,reinstalled)
        self.assertNotEqual(reinstalled["ask_state_root"],first)
        self.assertEqual(reinstalled["ask_status"]["NATIVE_TASK_PERMISSION"]["status"],
                         "PENDING_ASK")
        rc,removed=self.call("uninstall")
        self.assertEqual(rc,0,removed)
        self.assertEqual(removed["status"],"ABSENT")
        self.untouched()

    def test_ask_decisions_and_agents_patch_are_unavailable_in_preview(self):
        rc,installed=self.call("install")
        self.assertEqual(rc,0,installed)
        before_head=self.git("rev-parse","HEAD")
        before_agents=(self.project/"AGENTS.md").read_bytes()
        ask_root=Path(installed["ask_state_root"])
        self.assertTrue(ask_root.is_dir())
        for action in ("approve-agents","approve-permission",
                       "deny-agents","deny-permission","apply-agents"):
            with self.subTest(action=action):
                rc,blocked=self.call(action)
                self.assertEqual(rc,2)
                self.assertEqual(blocked["status"],"BLOCKED")
                self.assertIn("cannot approve or apply",blocked["reason"])
                self.assertEqual((self.project/"AGENTS.md").read_bytes(),
                                 before_agents)
                self.assertEqual(self.git("rev-parse","HEAD"),before_head)
        self.assertFalse(any(ask_root.rglob("decision.json")))
        self.assertFalse(any(ask_root.rglob("activation.json")))
        self.untouched()

    def test_unknown_durable_state_mode_refused_without_project_write(self):
        bad=self.state/"kagari-v010"
        bad.mkdir(mode=0o755)
        rc,blocked=self.call("install")
        self.assertEqual(rc,2)
        self.assertEqual(blocked["status"],"BLOCKED")
        self.untouched()

    def test_no_real_model_task_or_unqualified_promotion(self):
        rc,blocked=self.call("run-task")
        self.assertEqual(rc,2)
        self.assertEqual(blocked["status"],"BLOCKED")
        rc,installed=self.call("install")
        self.assertEqual(rc,0,installed)
        rc,blocked=self.call("run-task")
        self.assertEqual(rc,2)
        self.assertIn("not qualified",blocked["reason"])
        self.assertFalse(blocked["production_activation"])
        self.untouched()

if __name__=="__main__":
    unittest.main()
