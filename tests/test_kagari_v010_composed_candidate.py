"""#275 offline composed KAGARI candidate: exact bundle and Ask-gated lifecycle."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class ComposedCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value = os.environ.get("KAGARI_V010_COMPOSED_PACKAGE", "")
        if not value:
            raise RuntimeError("exact composed candidate package required")
        cls.root = Path(value)
        cls.cli = cls.root / "bin/kagari-v010-candidate"
        cls.receipt = json.loads((cls.root / "share/kagari-v010-composed/receipt.json").read_bytes())
        cls.native = Path(cls.receipt["native_root"])

    def call(self, *argv):
        return subprocess.run([str(self.cli), *map(str, argv)],
                              capture_output=True, text=True, timeout=25)

    def test_identity_and_nonproduction_receipt(self):
        r = self.receipt
        self.assertEqual(r["version"], "0.1.0")
        self.assertEqual(r["qualification"], "UNRELEASED_CANDIDATE")
        for name in ("released", "production_activation",
                     "consumer_production_qualified", "generic_task_qualified",
                     "source_publication_approved"):
            self.assertIs(r[name], False)
        self.assertEqual(r["candidate_root"], str(self.root))
        self.assertEqual(r["native_root"], str(self.native))
        self.assertEqual(hashlib.sha256(
            (self.native/"share/kagari-v010-native-generic/receipt.json").read_bytes()
        ).hexdigest(), r["native_receipt_sha256"])
        self.assertEqual(hashlib.sha256(
            (self.root/"libexec/kagari-v010-composed/kagari_v010_composed_cli.py").read_bytes()
        ).hexdigest(), r["cli_sha256"])
        self.assertEqual(r["native_runtime_identity"],
                         json.loads((self.native/"share/kagari-v010-native-generic/receipt.json").read_bytes())
                         ["tool_sha256"]["kagari_v010_native_generic_task.py"])

    def test_version_status_doctor_never_activate(self):
        for op in ("version", "status", "doctor"):
            with self.subTest(op=op):
                run = self.call(op)
                self.assertEqual(run.returncode, 0, run.stderr)
                result = json.loads(run.stdout)
                self.assertEqual(result["version"], "0.1.0")
                self.assertIs(result["production_activation"], False)
                self.assertIs(result["consumer_production_qualified"], False)
                if op != "version":
                    self.assertIs(result["model_invoked"], False)
                    self.assertEqual(result["project_permission_status"], "NOT_ASSESSED")
                    self.assertIs(result["effective_project_authority"], False)
                if op == "doctor":
                    self.assertIs(result["provider_checked"], False)

    def test_metadata_commands_reject_project_specific_arguments(self):
        for op in ("version", "status", "doctor"):
            result=self.call(op,"--project-root","/tmp/not-a-project")
            self.assertEqual(result.returncode,2)
            self.assertEqual(json.loads(result.stdout)["status"],"BLOCKED")

    def test_admin_operations_require_test_only_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="kagari275-block-") as root:
            project = Path(root)/"project"
            project.mkdir()
            runtime = Path(root)/"runtime"
            for operation in ("inspect", "install", "repair", "uninstall", "run-task"):
                with self.subTest(op=operation):
                    flags = ["--project-root", str(project), "--runtime-root", str(runtime)]
                    if operation == "run-task":
                        flags += ["--task-spec", str(Path(root)/"spec.json"),
                                  "--opencode", "/usr/bin/false",
                                  "--output", str(Path(root)/"evidence")]
                    result = self.call(operation, *flags)
                    self.assertEqual(result.returncode, 2)
                    body = json.loads(result.stdout)
                    self.assertEqual(body["status"], "BLOCKED")
                    self.assertIn("--test-only", body["reason"])
                    self.assertFalse(runtime.exists())

    def test_test_only_consumer_install_pending_grant_uninstall(self):
        with tempfile.TemporaryDirectory(prefix="kagari275-project-") as root, \
             tempfile.TemporaryDirectory(prefix="kagari-v010-consent-install-") as external:
            scratch = Path(root)
            project = scratch/"project"
            project.mkdir()
            runtime = Path(external)/"runtime"
            runtime.mkdir(mode=0o700)
            (project/"README.md").write_text("Original Project contents\n")
            (project/"opencode.json").write_text('{"permission":{"task":"deny"}}\n')
            def git(*args):
                return subprocess.run(["git","-C",str(project),*args],
                                      capture_output=True,text=True,check=True).stdout.strip()
            git("init","-q","-b","main")
            git("add","-A")
            git("-c","user.name=Fixture","-c","user.email=fixture@example.invalid",
                "commit","-qm","baseline")
            before_head=git("rev-parse","HEAD")
            before_config=(project/"opencode.json").read_bytes()
            (project/"README.md").write_text("Intentionally unrelated staged README\n")
            git("add","README.md")
            before_stage=subprocess.check_output(
                ["git","-C",str(project),"show",":README.md"])
            base=["--test-only","--project-root",str(project),"--runtime-root",str(runtime)]
            inspected=self.call("inspect", *base)
            self.assertEqual(inspected.returncode, 0, inspected.stdout+inspected.stderr)
            self.assertEqual(json.loads(inspected.stdout)["status"],"ABSENT")
            installed=self.call("install", *base)
            self.assertEqual(installed.returncode, 0, installed.stdout+installed.stderr)
            body=json.loads(installed.stdout)
            self.assertEqual(body["status"],"INSTALLED_TEST_ONLY")
            self.assertIn("NATIVE_TASK_PERMISSION",body["required_asks"])
            self.assertIs(body["ready_for_model_task"],False)
            # Exact owner-aware partial payload repair (no model, no new grant).
            old_ask_root=body["ask_state_root"]
            owned_link=runtime/"kagari-v010/package"
            self.assertTrue(owned_link.is_symlink())
            owned_link.unlink()
            broken=self.call("inspect", *base)
            self.assertEqual(broken.returncode,0,broken.stdout+broken.stderr)
            self.assertEqual(json.loads(broken.stdout)["status"],"MISSING_OWNED_LINK")
            repaired=self.call("repair", *base)
            self.assertEqual(repaired.returncode,0,repaired.stdout+repaired.stderr)
            repaired_state=json.loads(repaired.stdout)
            self.assertEqual(repaired_state["status"],"INSTALLED_TEST_ONLY")
            self.assertEqual(repaired_state["ask_state_root"],old_ask_root)
            self.assertFalse(repaired_state["ready_for_model_task"])
            self.assertEqual((project/"opencode.json").read_bytes(),before_config)
            self.assertFalse((project/"AGENTS.md").exists())
            negative=self.call("run-task",*base,
                               "--task-spec",str(scratch/"fixture-spec.json"),
                               "--opencode","/usr/bin/false",
                               "--output",str(scratch/"no-model-evidence"))
            self.assertEqual(json.loads(negative.stdout)["status"],"BLOCKED")
            self.assertNotEqual(negative.returncode,0)
            self.assertFalse((scratch/"no-model-evidence").exists())
            removed=self.call("uninstall", *base)
            self.assertEqual(removed.returncode,0,removed.stdout+removed.stderr)
            self.assertEqual(json.loads(removed.stdout)["status"],"ABSENT")
            self.assertEqual(git("rev-parse","HEAD"),before_head)
            self.assertEqual((project/"opencode.json").read_bytes(),before_config)
            self.assertEqual(subprocess.check_output(
                ["git","-C",str(project),"show",":README.md"]),before_stage)
            self.assertEqual(git("diff","--cached","--name-only"),"README.md")
            self.assertFalse((project/"AGENTS.md").exists())
            self.assertFalse((runtime/"kagari-v010/owner.json").exists())
            # Reinstall requires a new pending Ask generation; old authority
            # is not replayed and Git/index/config remain untouched.
            again=self.call("install", *base)
            self.assertEqual(again.returncode,0,again.stdout+again.stderr)
            newer=json.loads(again.stdout)
            self.assertEqual(newer["status"],"INSTALLED_TEST_ONLY")
            self.assertNotEqual(newer["ask_state_root"],old_ask_root)
            self.assertFalse(newer["ready_for_model_task"])
            removed_again=self.call("uninstall", *base)
            self.assertEqual(removed_again.returncode,0,
                             removed_again.stdout+removed_again.stderr)
            self.assertEqual(json.loads(removed_again.stdout)["status"],"ABSENT")
            self.assertEqual(git("rev-parse","HEAD"),before_head)
            self.assertEqual((project/"opencode.json").read_bytes(),before_config)
            self.assertEqual(subprocess.check_output(
                ["git","-C",str(project),"show",":README.md"]),before_stage)
            self.assertFalse((runtime/"kagari-v010/owner.json").exists())

if __name__=="__main__":
    unittest.main()
