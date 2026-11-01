"""#256 R3: minimal native-v4 Runtime facade on disposable real Git fixtures.

A synthetic package has inert executables: tests do not run real OpenCode,
modify remote GitHub state, or install anything in existing user repositories.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
import shutil
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_kagari_vnext_attach as attach_fixture
from tools import kagari_vnext_runtime as runtime
from tools import kagari_vnext_main_service as main_service


class NativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        fixture = attach_fixture.AttachTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.project = fixture.project
        self.state = fixture.home
        self.package = fixture.package
        # Main is deliberately inert. No model/Task is invoked by the facade.
        self.package.chmod(0o755)
        (self.package / "bin").chmod(0o755)
        main = self.package / "bin/kagari-vnext-main"
        main.write_bytes(b"#!/bin/sh\nexit 97 # never execute this\n")
        main.chmod(0o555)
        self.package.chmod(0o555)
        (self.package / "bin").chmod(0o555)

    def operate(self, operation, **options):
        return runtime.operate(
            options.get("project", self.project),
            options.get("state_home", self.state),
            options.get("package_root", self.package), operation,
            options.get("profile", runtime.PROFILE),
        )

    def host(self):
        return main_service.MainHostCapabilities(
            read_issue=lambda _: {},
            initial_collaboration_for_issue=lambda _issue, _path: None,
        )

    def test_complete_minimal_lifecycle_and_no_new_state_database(self):
        before = self.operate("doctor")
        self.assertEqual(before["status"], "ABSENT")
        self.assertFalse(before["runtime_installed"])
        installed = self.operate("install")
        self.assertEqual(installed["status"], "ATTACHED")
        self.assertTrue(installed["runtime_installed"])
        self.assertTrue(installed["runtime_package_installed"])
        self.assertEqual(installed["profile"], runtime.PROFILE)
        self.assertEqual(installed["attachment_profile"], runtime.attach.PROFILE)
        self.assertEqual(installed["runtime_running"], "NOT_OBSERVED")
        self.assertFalse(installed["main_started"])
        self.assertFalse(installed["task_execution_authorized"])
        self.assertFalse(installed["new_runtime_state_created"])
        self.assertEqual(self.operate("doctor")["status"], "READY")
        self.assertEqual(self.operate("repair")["status"], "READY")
        self.assertEqual(self.operate("install")["status"], "READY")
        self.assertEqual(self.operate("uninstall")["status"], "DETACHED")
        self.assertEqual(self.operate("doctor")["status"], "ABSENT")
        self.assertEqual(self.operate("uninstall")["status"], "ABSENT")
        self.assertEqual(self.operate("install")["status"], "ATTACHED")
        self.assertEqual(self.operate("repair")["status"], "READY")
        self.assertEqual(self.operate("uninstall")["status"], "DETACHED")
        self.fixture.assert_git_preserved()
        self.assertFalse((self.state / "kagari-vnext-runtime").exists())

    def test_foreign_main_symlink_cannot_qualify_runtime_package(self):
        outside = self.fixture.base / "foreign-main"
        outside.write_bytes(b"#!/bin/sh\nexit 0 # foreign binary\n")
        outside.chmod(0o555)
        main = self.package / "bin/kagari-vnext-main"
        self.package.chmod(0o755)
        (self.package / "bin").chmod(0o755)
        main.unlink()
        main.symlink_to(outside)
        self.package.chmod(0o555)
        (self.package / "bin").chmod(0o555)
        # Preview can attach a package lacking a checked native Main. The new
        # Runtime facade refuses its foreign executable at install/doctor.
        self.assertEqual(self.fixture.operate("install")["status"], "ATTACHED")
        self.assertEqual(self.fixture.operate("doctor")["status"], "READY")
        with self.assertRaises(runtime.attach.Blocked) as denied:
            self.operate("doctor")
        self.assertEqual(denied.exception.code,
                         "PACKAGE_BINARY_ESCAPES_PACKAGE")
        self.fixture.assert_git_preserved()

    def test_missing_main_executable_prevents_install_before_receipt(self):
        main = self.package / "bin/kagari-vnext-main"
        self.package.chmod(0o755)
        (self.package / "bin").chmod(0o755)
        main.unlink()
        self.package.chmod(0o555)
        (self.package / "bin").chmod(0o555)
        with self.assertRaises(runtime.RuntimeBlocked) as denied:
            self.operate("install")
        self.assertEqual(denied.exception.code, "RUNTIME_MAIN_UNAVAILABLE")
        self.assertFalse((self.state / runtime.attach.STATE_DIR).exists())
        self.assertFalse((self.project / ".gitignore").exists())
        self.fixture.assert_git_preserved()

    def test_broken_project_configs_are_not_repaired_or_evaluated(self):
        values = {
            "flake.nix": b"broken flake\n",
            "flake.lock": b"{broken json",
            "Justfile": b"invalid := :\n",
            "AGENTS.md": b"User rules\n",
            "opencode.json": b"{invalid json",
        }
        (self.project / ".opencode/agents").mkdir(parents=True)
        values[".opencode/agents/custom.md"] = b"User agent\n"
        for path, value in values.items():
            (self.project / path).write_bytes(value)
        (self.project / "human.txt").write_bytes(b"untracked human\n")
        self.assertTrue(self.operate("install")["runtime_installed"])
        self.assertTrue(self.operate("repair")["runtime_installed"])
        self.assertEqual(self.operate("uninstall")["status"], "DETACHED")
        for path, value in values.items():
            self.assertEqual((self.project / path).read_bytes(), value)
        self.assertEqual((self.project / "human.txt").read_bytes(),
                         b"untracked human\n")
        self.fixture.assert_git_preserved()

    def test_missing_receipt_repair_reuses_existing_attach(self):
        self.operate("install")
        self.fixture.receipt().unlink()
        self.assertEqual(self.operate("doctor")["status"], "ABSENT")
        self.assertEqual(self.operate("repair")["status"], "ATTACHED")
        self.assertTrue(self.operate("doctor")["runtime_installed"])
        self.fixture.assert_git_preserved()

    def test_corrupt_receipt_refuses_repair_and_uninstall_without_deletion(self):
        self.operate("install")
        self.fixture.receipt().write_bytes(b"{corrupt,do-not-delete}")
        original = self.fixture.receipt().read_bytes()
        for op in ("repair", "uninstall"):
            with self.assertRaises(runtime.attach.Blocked) as denied:
                self.operate(op)
            self.assertEqual(denied.exception.code,
                             "ATTACH_RECEIPT_IDENTITY_CHANGED")
            self.assertEqual(self.fixture.receipt().read_bytes(), original)
        self.fixture.assert_git_preserved()

    def test_foreign_package_does_not_repair_attached_receipt(self):
        self.operate("install")
        foreign = self.fixture.base / "foreign-immutable-package"
        shutil.copytree(self.package, foreign)
        self.addCleanup(lambda: (foreign / "bin").chmod(0o755))
        self.addCleanup(lambda: foreign.chmod(0o755))
        with self.assertRaises(runtime.attach.Blocked) as denied:
            self.operate("repair", package_root=foreign)
        self.assertEqual(denied.exception.code, "ATTACH_RECEIPT_IDENTITY_CHANGED")
        self.fixture.assert_git_preserved()

    def test_no_host_capability_cannot_launch_main_or_model(self):
        with self.assertRaises(runtime.RuntimeBlocked) as denied:
            runtime.launch_with_host(
                self.project, self.state, self.package,
                self.fixture.base / "plan.json", host=None)
        self.assertEqual(denied.exception.code, "RUNTIME_TRUSTED_HOST_REQUIRED")
        self.assertFalse((self.state / runtime.attach.STATE_DIR).exists())
        self.fixture.assert_git_preserved()

    def test_host_injection_is_bounded_to_exact_project_and_existing_main(self):
        self.operate("install")
        config = self.fixture.base / "approved-plan.json"
        config.write_text("{}")
        selected = {"project_root": str(self.project)}
        with patch.object(main_service, "load_selection", return_value=selected):
            with patch.object(main_service, "main", return_value=0) as main_call:
                self.assertEqual(runtime.launch_with_host(
                    self.project, self.state, self.package, config,
                    host=self.host(), interval=15), 0)
                args, kwargs = main_call.call_args
        self.assertEqual(args[0], ["--config", str(config), "--interval",
                                   "15", "dispatch"])
        self.assertIsInstance(kwargs["host"], main_service.MainHostCapabilities)
        self.assertEqual(kwargs["attachment"].state_home, self.state)
        self.assertEqual(kwargs["attachment"].package_root, self.package)
        self.fixture.assert_git_preserved()

    def test_host_cannot_launch_when_attachment_absent_or_plan_mismatched(self):
        config = self.fixture.base / "plan.json"
        config.write_text("{}")
        with patch.object(main_service, "load_selection", return_value={
                "project_root": str(self.project)}):
            with self.assertRaises(runtime.RuntimeBlocked) as denied:
                runtime.launch_with_host(self.project, self.state,
                                         self.package, config, host=self.host())
        self.assertEqual(denied.exception.code, "RUNTIME_NOT_INSTALLED")
        self.operate("install")
        with patch.object(main_service, "load_selection", return_value={
                "project_root": str(self.fixture.base / "another-project")}):
            with self.assertRaises(runtime.RuntimeBlocked) as denied:
                runtime.launch_with_host(self.project, self.state,
                                         self.package, config, host=self.host())
        self.assertEqual(denied.exception.code, "RUNTIME_PROJECT_PLAN_MISMATCH")
        self.fixture.assert_git_preserved()

    def test_cli_reports_installed_without_claiming_main_started(self):
        argv = ["install", "--profile", runtime.PROFILE,
                "--project-root", str(self.project),
                "--state-home", str(self.state),
                "--package-root", str(self.package)]
        with redirect_stdout(StringIO()) as text:
            result = runtime.main(argv)
        self.assertEqual(result, 0)
        record = json.loads(text.getvalue())
        self.assertTrue(record["runtime_installed"])
        self.assertFalse(record["main_started"])
        self.assertFalse(record["task_execution_authorized"])
        self.fixture.assert_git_preserved()


if __name__ == "__main__":
    unittest.main()
