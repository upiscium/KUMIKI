"""Test-only installed Nix closure integrity and bounded CLI exposure."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


TOOL_NAMES = (
    "kagari_v010_native_generic_task.py",
    "kagari_v010_general_task_spec.py",
    "kagari_v010_basic_plan.py",
    "kagari_v010_auto_planning.py",
    "kagari_v010_native_subagent_proof.py",
    "kagari_v010_agents_compat.py",
    "kagari_v010_human_ask.py",
    "kagari_v010_consent_admin.py",
)


class NativePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value = os.environ.get("KAGARI_V010_NATIVE_GENERIC_PACKAGE", "")
        if not value:
            raise RuntimeError("exact KAGARI_V010_NATIVE_GENERIC_PACKAGE required")
        cls.package = Path(value)
        if not cls.package.is_absolute() or not cls.package.is_dir():
            raise RuntimeError("exact built Nix-store output unavailable")
        cls.root = cls.package / "libexec/kagari-v010-native-generic"
        cls.share = cls.package / "share/kagari-v010-native-generic"
        cls.receipt = json.loads((cls.share / "receipt.json").read_bytes())

    def test_literal_version_qualification_and_historical_n1(self):
        r = self.receipt
        self.assertEqual(r["public_target_version"], "0.1.0")
        self.assertEqual(r["qualification"], "TEST_ONLY_UNQUALIFIED")
        self.assertTrue(r["candidate_tools_uncommitted"])
        self.assertFalse(r["released"])
        self.assertFalse(r["production_activation"])
        self.assertFalse(r["source_publication_approved"])
        self.assertFalse(r["generic_production_task_qualified"])
        self.assertTrue(r["agents_read_only_inspect_supported"])
        self.assertFalse(r["agents_production_consent_or_mutation_supported"])
        self.assertTrue(r["human_ask_local_tty_test_only_supported"])
        self.assertFalse(r["human_ask_remote_user_identity_verified"])
        self.assertFalse(r["human_ask_production_qualified"])
        self.assertTrue(r["human_ask_admin_lifecycle_test_only_supported"])
        self.assertFalse(r["human_ask_admin_production_qualified"])
        self.assertTrue(r["host_observed_auto_planning_test_only_supported"])
        self.assertFalse(r["model_planner_provider_identity_attested"])
        self.assertFalse(r["auto_planning_production_qualified"])
        self.assertEqual(set(r["wrapper_sha256"]), {
            "kagari-v010-native-generic-test-only",
            "kagari-v010-native-subagent-proof-test-only",
            "kagari-v010-agents-compat-test-only",
            "kagari-v010-human-ask-test-only",
            "kagari-v010-consent-admin-test-only",
        })
        self.assertEqual(len(r["v4_module_sha256"]), 12)
        n1 = (self.share / "n1-manifest.json").read_bytes()
        self.assertEqual(hashlib.sha256(n1).hexdigest(),
                         r["n1_manifest_sha256"])
        j = json.loads(n1)
        self.assertEqual(j["source_revision"], r["baseline_source_revision"])
        self.assertEqual(j["target_version"], "1.0.0")
        self.assertEqual(j["qualification"], "SOURCE_ONLY_UNQUALIFIED")
        self.assertEqual(len(j["files"]), 12)

    def test_exact_store_tools_and_v4_modules(self):
        files = list((self.root / "tools").iterdir())
        self.assertEqual({p.name for p in files}, set(TOOL_NAMES))
        self.assertEqual(set(self.receipt["tool_sha256"]), set(TOOL_NAMES))
        for p in files:
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),
                             self.receipt["tool_sha256"][p.name])
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o444)
        n1 = json.loads((self.share / "n1-manifest.json").read_bytes())
        modules = list((self.root / "components/agent-core-v4").iterdir())
        self.assertEqual(len(modules), 12)
        for item in n1["files"]:
            p = self.root / "components/agent-core-v4" / Path(item["path"]).name
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),
                             item["sha256"])
            self.assertEqual(self.receipt["v4_module_sha256"][p.name],
                             item["sha256"])
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o444)
        for filename, expected in self.receipt["wrapper_sha256"].items():
            binary = self.package / "bin" / filename
            self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(),
                             expected)
            self.assertEqual(stat.S_IMODE(binary.stat().st_mode), 0o555)

    def test_both_entrypoints_are_real_executables(self):
        for binary in ("kagari-v010-native-generic-test-only",
                       "kagari-v010-native-subagent-proof-test-only",
                       "kagari-v010-agents-compat-test-only",
                       "kagari-v010-human-ask-test-only",
                       "kagari-v010-consent-admin-test-only"):
            path = self.package / "bin" / binary
            self.assertTrue(path.is_file())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o555)
            result = subprocess.run([str(path), "--help"], capture_output=True,
                                    text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage:", result.stdout)

    def test_packaged_agents_compat_is_readonly(self):
        checker = self.package / "bin/kagari-v010-agents-compat-test-only"
        with tempfile.TemporaryDirectory(prefix="kagari-compat-package-") as root:
            project = Path(root) / "arbitrary-repository"
            subprocess.run(["git", "init", "-q", "-b", "main", str(project)],
                           capture_output=True, check=True, timeout=15)
            source = project / "AGENTS.md"
            source.write_text("# User instructions\n- Keep product files unchanged.\n")
            before = source.read_bytes()
            command = subprocess.run(
                [str(checker), "--project-root", str(project)],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(command.returncode, 0, command.stderr)
            result = json.loads(command.stdout)
            self.assertEqual(result["state"], "NO_EXACT_KNOWN_CONFLICT")
            self.assertFalse(result["automatic_file_edit_available"])
            self.assertFalse(result["human_permission_granted"])
            self.assertEqual(source.read_bytes(), before)

    def test_packaged_human_ask_cannot_approve_noninteractively(self):
        cli = self.package / "bin/kagari-v010-human-ask-test-only"
        runtime = self.root / "tools/kagari_v010_native_generic_task.py"
        with tempfile.TemporaryDirectory(prefix="kagari-packaged-ask-") as root:
            project = Path(root) / "project"
            subprocess.run(["git", "init", "-q", "-b", "main", str(project)],
                           capture_output=True, check=True, timeout=15)
            (project / "README.md").write_text("real Project fixture\n")
            (project / "opencode.json").write_text(
                '{"permission":{"task":"deny"}}\n')
            subprocess.run(["git", "-C", str(project), "add", "-A"],
                           capture_output=True, check=True, timeout=15)
            subprocess.run([
                "git", "-C", str(project),
                "-c", "user.name=Fixture", "-c",
                "user.email=test@example.invalid", "commit", "-qm", "baseline"],
                capture_output=True, check=True, timeout=15)
            private = Path(root) / "ask"
            common = ["--project-root", str(project),
                      "--state-root", str(private),
                      "--runtime-source", str(runtime)]
            request = subprocess.run([str(cli), "request-task-permission", *common],
                                     capture_output=True, text=True, timeout=15)
            self.assertEqual(request.returncode, 0, request.stderr)
            ticket = json.loads(request.stdout)
            self.assertEqual(ticket["status"], "PENDING_ASK")
            self.assertFalse(ticket["approval_recorded"])
            approve = subprocess.run([str(cli), "approve", *common],
                                     capture_output=True, text=True, timeout=15)
            self.assertEqual(approve.returncode, 2)
            self.assertEqual(json.loads(approve.stdout)["status"], "BLOCKED")
            self.assertFalse((private / "decision.json").exists())

    def test_packaged_consent_admin_pending_install_and_uninstall(self):
        """Real packaged Admin, no mocked source and no human answer."""
        admin_cli = self.package / "bin/kagari-v010-consent-admin-test-only"
        receipt_sha = hashlib.sha256((self.share / "receipt.json").read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="kagari-consent-package-project-") as pp, \
             tempfile.TemporaryDirectory(prefix="kagari-v010-consent-install-") as rr:
            project = Path(pp) / "project"
            project.mkdir(mode=0o700)
            runtime = Path(rr) / "runtime"
            runtime.mkdir(mode=0o700)
            (project / "README.md").write_text("Existing Product data\n")
            (project / "opencode.json").write_text(
                '{"permission":{"task":"deny"},"default_agent":"build"}\n')
            subprocess.run(["git", "init", "-q", "-b", "main", str(project)],
                           capture_output=True, check=True, timeout=15)
            subprocess.run(["git", "-C", str(project), "add", "-A"],
                           capture_output=True, check=True, timeout=15)
            subprocess.run([
                "git", "-C", str(project), "-c", "user.name=Fixture",
                "-c", "user.email=test@example.invalid",
                "commit", "-qm", "baseline"], capture_output=True, check=True)
            (project / "README.md").write_text("Unrelated staged data\n")
            subprocess.run(["git", "-C", str(project), "add", "README.md"],
                           capture_output=True, check=True, timeout=15)
            snapshot = (project / ".git/index").read_bytes()
            common = [
                "--project-root", str(project), "--runtime-root", str(runtime),
                "--package-root", str(self.package),
                "--expected-receipt-sha256", receipt_sha, "--test-only",
            ]

            def cli(command):
                result = subprocess.run([str(admin_cli), command, *common],
                                        capture_output=True, text=True, timeout=25)
                return result, json.loads(result.stdout)

            inspected, first = cli("inspect")
            self.assertEqual(inspected.returncode, 0,
                             inspected.stdout + inspected.stderr)
            self.assertEqual(first["status"], "ABSENT")
            installed, info = cli("install")
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(info["status"], "INSTALLED_TEST_ONLY")
            self.assertFalse(info["ready_for_model_task"])
            self.assertEqual(info["required_asks"], ["NATIVE_TASK_PERMISSION"])
            self.assertEqual(info["ask_status"]["NATIVE_TASK_PERMISSION"]["status"],
                             "PENDING_ASK")
            self.assertFalse((project / "AGENTS.md").exists())
            answer, denied = cli("approve-permission")
            self.assertEqual(answer.returncode, 2)
            self.assertEqual(denied["status"], "BLOCKED")
            self.assertFalse((Path(info["ask_state_root"]) /
                              "task-permission/decision.json").exists())
            uninstalled, removed = cli("uninstall")
            self.assertEqual(uninstalled.returncode, 0, uninstalled.stderr)
            self.assertEqual(removed["status"], "ABSENT")
            self.assertEqual((project / ".git/index").read_bytes(), snapshot)
            self.assertEqual((project / "README.md").read_bytes(),
                             b"Unrelated staged data\n")
            self.assertEqual(list(runtime.iterdir()), [])
            self.assertTrue((Path(info["ask_state_root"]) /
                             "task-permission/revocation.json").exists())

    def test_execution_denied_without_explicit_authorization(self):
        path = self.package / "bin/kagari-v010-native-generic-test-only"
        with tempfile.TemporaryDirectory(prefix="kagari-native-package-") as root:
            project = Path(root) / "not-a-project"
            target = Path(root) / "unused-output"
            result = subprocess.run(
                [str(path), "--project-root", str(project),
                 "--task-spec", str(Path(root) / "absent.json"),
                 "--plan", str(Path(root) / "absent-plan.json"),
                 "--opencode", "/nix/store/nonexistent-opencode",
                 "--output", str(target)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "BLOCKED")
            self.assertFalse(report["release_ready"])
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
