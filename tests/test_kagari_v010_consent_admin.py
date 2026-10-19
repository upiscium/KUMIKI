"""No-provider acceptance of native-package Admin + real Human Ask boundary.

Positive Human replies are synthetic/mocked in these tests; no actual user
authorization or production Project modification is asserted.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import kagari_v010_consent_admin as admin
import kagari_v010_human_ask as ask
import kagari_v010_agents_compat as compat


class ConsentAdminTests(unittest.TestCase):
    def setUp(self):
        ptmp = tempfile.TemporaryDirectory(prefix="kagari-ask-owner-project-")
        rtmp = tempfile.TemporaryDirectory(prefix="kagari-v010-consent-install-")
        self.addCleanup(ptmp.cleanup)
        self.addCleanup(rtmp.cleanup)
        self.pbase = Path(ptmp.name)
        self.rbase = Path(rtmp.name)
        self.project = self.pbase / "project"
        self.project.mkdir(mode=0o700)
        self.runtime = self.rbase / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.package = self.pbase / "unpublished-package-fixture"
        self.package.mkdir(mode=0o700)
        self.source = self.package / admin.MAIN
        self.source.parent.mkdir(parents=True, exist_ok=True)
        self.source.write_text("# fixed fixture KAGARI native executor\n")
        self.identity = {
            "package_root": str(self.package),
            "receipt_sha256": "1" * 64,
            "runtime_source_sha256": admin.sha(self.source.read_bytes()),
            "public_target_version": "0.1.0",
            "qualification": admin.PROFILE,
        }
        self.original_agents = (
            "# Project policy\n"
            + "\n".join(x["signature"] for x in compat.KNOWN_CONFLICTS) + "\n"
        )
        (self.project / "AGENTS.md").write_text(self.original_agents)
        (self.project / "opencode.json").write_text(
            '{"permission":{"task":"deny"},"default_agent":"build"}\n')
        (self.project / "README.md").write_text("Existing Product data\n")
        (self.project / "Justfile").write_text("test:\n\t@echo existing\n")
        (self.project / "flake.nix").write_text("existing project file\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c",
                 "user.email=fixture@example.invalid",
                 "commit", "-qm", "preexisting real Project")
        (self.project / "README.md").write_text("Unrelated staged data\n")
        self.git("add", "README.md")
        self.before = self.snapshot()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.project), *args],
            capture_output=True, check=True, timeout=15).stdout

    def snapshot(self):
        return {
            "head": self.git("rev-parse", "HEAD"),
            "index_sha256": admin.sha((self.project / ".git/index").read_bytes()),
            "readme": (self.project / "README.md").read_bytes(),
            "agents": (self.project / "AGENTS.md").read_bytes(),
            "opencode": (self.project / "opencode.json").read_bytes(),
            "justfile": (self.project / "Justfile").read_bytes(),
            "flake": (self.project / "flake.nix").read_bytes(),
        }

    def installed(self):
        return admin.install(self.project, self.runtime,
                             self.package, self.identity)

    def state(self):
        return admin.summary(self.project, self.runtime,
                             self.package, self.identity)

    def folder(self, kind):
        owner, _ = admin.validate_owner(
            self.project, self.runtime, self.identity)
        return admin.owner_ask_root(owner, kind)

    def approve(self, kind):
        folder = self.folder(kind)
        req = ask.load_request(folder)
        typed = "APPROVE " + req["request_id"]
        with mock.patch.object(ask, "require_foreground_terminal"), \
             mock.patch("builtins.input", return_value=typed):
            return ask.respond(folder, "approve")

    def deny(self, kind):
        folder = self.folder(kind)
        req = ask.load_request(folder)
        typed = "DENY " + req["request_id"]
        with mock.patch.object(ask, "require_foreground_terminal"), \
             mock.patch("builtins.input", return_value=typed):
            return ask.respond(folder, "deny")

    def both_approved(self):
        self.installed()
        self.approve(ask.KIND_PERMISSION)
        self.approve(ask.KIND_AGENTS)
        self.assertEqual(
            self.state()["ask_status"][ask.KIND_AGENTS]["status"],
            "APPROVED_NOT_APPLIED")
        ask.apply_agents(self.folder(ask.KIND_AGENTS),
                         self.project, self.source)
        self.assertTrue(self.state()["ready_for_model_task"])

    def test_install_is_nonexecuting_and_requests_two_separate_asks(self):
        self.assertEqual(self.state()["status"], "ABSENT")
        info = self.installed()
        self.assertEqual(info["status"], "INSTALLED_TEST_ONLY")
        self.assertFalse(info["ready_for_model_task"])
        self.assertEqual(info["required_asks"],
                         [ask.KIND_AGENTS, ask.KIND_PERMISSION])
        self.assertTrue(all(
            x["status"] == "PENDING_ASK" for x in info["ask_status"].values()))
        self.assertFalse(info["production_activation"])
        self.assertEqual(self.snapshot(), self.before)
        owner, installed = admin.validate_owner(
            self.project, self.runtime, self.identity)
        self.assertEqual(installed, "INSTALLED_TEST_ONLY")
        self.assertEqual(owner["package_root"], str(self.package))
        self.assertEqual({p.name for p in (self.runtime / admin.NAME).iterdir()},
                         {"owner.json", "package"})
        self.assertEqual(self.installed()["ask_state_root"], info["ask_state_root"])
        self.assertEqual(self.snapshot(), self.before)

    def test_native_admin_full_budget_process_group_is_reaped(self):
        class NativeChild:
            pid = 551234
            returncode = 0
            def __init__(self, timed_out):
                self.timed_out = timed_out
                self.communicate_calls = []
                self.wait_calls = []
            def communicate(self, timeout):
                self.communicate_calls.append(timeout)
                if self.timed_out:
                    raise subprocess.TimeoutExpired(["native"], timeout)
                return ('{"status":"BLOCKED"}', "")
            def wait(self, timeout):
                self.wait_calls.append(timeout)
                return self.returncode

        for timeout_case in (False, True):
            process = NativeChild(timeout_case)
            with self.subTest(timeout=timeout_case), \
                 mock.patch.object(admin.subprocess, "Popen",
                                   return_value=process) as started, \
                 mock.patch.object(admin.os, "killpg") as killed:
                if timeout_case:
                    with self.assertRaisesRegex(admin.AdminBlocked,
                                                "bounded end-to-end runtime"):
                        admin.invoke_native(self.source, self.project, ["--dummy"], 40)
                else:
                    result = admin.invoke_native(
                        self.source, self.project, ["--dummy"], 40)
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")
                self.assertTrue(started.call_args.kwargs["start_new_session"])
                self.assertFalse(started.call_args.kwargs["shell"])
                self.assertGreater(process.communicate_calls[0], 40 + 70)
                killed.assert_called_once_with(process.pid, admin.signal.SIGKILL)
                self.assertEqual(process.wait_calls, [5])

    def test_admin_forwards_auto_planning_without_external_plan(self):
        self.both_approved()
        captured = []

        def fake_native(executable, project, args, timeout):
            captured.append(list(args))
            return subprocess.CompletedProcess(
                args, 1, '{"status":"BLOCKED","stage":"planning"}', "")

        with mock.patch.object(admin, "invoke_native", side_effect=fake_native):
            result = admin.run_task(
                self.project, self.runtime, self.package, self.identity,
                spec=self.pbase / "task.json", plan=None,
                opencode=self.pbase / "opencode", output=self.pbase / "auto-out",
                repository="fixture/native", task="271", timeout=120)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(captured), 1)
        self.assertNotIn("--plan", captured[0])
        self.assertIn("--task-spec", captured[0])
        self.assertIn("--agents-ask-root", captured[0])
        self.assertIn("--permission-ask-root", captured[0])
        self.assertNotIn("--allow-project-task-deny-test-only", captured[0])

    def test_install_asked_but_not_approved_task_must_not_launch(self):
        self.installed()
        with mock.patch.object(admin, "invoke_native") as launched:
            with self.assertRaises(ask.AskBlocked):
                admin.run_task(
                    self.project, self.runtime, self.package, self.identity,
                    spec=self.pbase / "task.json", plan=self.pbase / "plan.json",
                    opencode=self.pbase / "opencode", output=self.pbase / "out",
                    repository="fixture/native", task="271", timeout=120)
            launched.assert_not_called()
        self.assertEqual(self.snapshot(), self.before)

    def test_approved_permission_alone_does_not_waive_agents(self):
        self.installed()
        self.approve(ask.KIND_PERMISSION)
        self.assertFalse(self.state()["ready_for_model_task"])
        with mock.patch.object(admin, "invoke_native") as launched:
            with self.assertRaises(ask.AskBlocked):
                admin.run_task(
                    self.project, self.runtime, self.package, self.identity,
                    spec=self.pbase / "task.json", plan=self.pbase / "plan.json",
                    opencode=self.pbase / "opencode", output=self.pbase / "out",
                    repository="fixture/native", task="271", timeout=120)
            launched.assert_not_called()

    def test_both_grants_start_only_bound_native_task_without_legacy_flag(self):
        self.both_approved()
        captured = []

        def fake_execute(executable, project, args, timeout):
            captured.append((executable, project, args, timeout))
            return subprocess.CompletedProcess(
                args, 0, '{"status":"REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS"}\n', "")
        with mock.patch.object(admin, "invoke_native", side_effect=fake_execute):
            result = admin.run_task(
                self.project, self.runtime, self.package, self.identity,
                spec=self.pbase / "task.json", plan=self.pbase / "plan.json",
                opencode=self.pbase / "opencode", output=self.pbase / "out",
                repository="fixture/native", task="271", timeout=125)
        self.assertEqual(result["status"], "REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS")
        self.assertEqual(result["native_task_exit"], 0)
        self.assertTrue(result["ask_runtime_attached"])
        self.assertEqual(len(captured), 1)
        executable, project, args, timeout = captured[0]
        self.assertEqual(executable, self.package / admin.TASK_COMMAND)
        self.assertEqual(project, self.project)
        self.assertEqual(timeout, 125)
        self.assertIn("--execute-model", args)
        self.assertIn("--run-tests", args)
        self.assertIn("--disposable-project", args)
        self.assertIn("--agents-ask-root", args)
        self.assertIn("--permission-ask-root", args)
        self.assertNotIn("--allow-project-task-deny-test-only", args)

    def test_native_child_failure_cannot_be_reported_as_success(self):
        self.both_approved()
        with mock.patch.object(admin, "invoke_native", return_value=
                 subprocess.CompletedProcess(
                     [], 1, '{"status":"REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS"}\n', "")):
            with self.assertRaisesRegex(admin.AdminBlocked, "despite unsuccessful"):
                admin.run_task(
                    self.project, self.runtime, self.package, self.identity,
                    spec=self.pbase / "task.json", plan=self.pbase / "plan.json",
                    opencode=self.pbase / "opencode", output=self.pbase / "out",
                    repository="fixture/native", task="271", timeout=120)

    def test_uninstall_pending_grants_revoked_and_never_usable_again(self):
        info = self.installed()
        old_ask_parent = info["ask_state_root"]
        agents_folder = self.folder(ask.KIND_AGENTS)
        permission_folder = self.folder(ask.KIND_PERMISSION)
        outcome = admin.uninstall(self.project, self.runtime,
                                  self.package, self.identity)
        self.assertEqual(outcome["status"], "ABSENT")
        self.assertEqual(self.state()["status"], "ABSENT")
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(ask.status(agents_folder, self.project, self.source)["status"],
                         "REVOKED")
        self.assertEqual(ask.status(permission_folder, self.project, self.source)["status"],
                         "REVOKED")
        self.assertTrue(Path(old_ask_parent).is_dir())
        with self.assertRaisesRegex(ask.AskBlocked, "retired/revoked"):
            ask.respond(permission_folder, "approve")
        next_info = self.installed()
        self.assertNotEqual(next_info["ask_state_root"], old_ask_parent)
        self.assertEqual(self.snapshot(), self.before)

    def test_uninstall_denied_and_approved_unapplied_are_inert(self):
        self.installed()
        self.approve(ask.KIND_AGENTS)
        self.deny(ask.KIND_PERMISSION)
        before = self.snapshot()
        outcome = admin.uninstall(self.project, self.runtime,
                                  self.package, self.identity)
        self.assertEqual(outcome["status"], "ABSENT")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(ask.status(
            self.folder_if_retained(ask.KIND_AGENTS), self.project,
            self.source)["status"], "REVOKED")

    def folder_if_retained(self, kind):
        ask_parents = [x for x in self.rbase.iterdir() if x.name.startswith("asks-")]
        self.assertEqual(len(ask_parents), 1)
        return ask_parents[0] / (
            admin.AGENTS if kind == ask.KIND_AGENTS else admin.PERMISSION)

    def test_uninstall_after_both_grants_restores_exact_agents_and_revokes(self):
        self.both_approved()
        original_index = (self.project / ".git/index").read_bytes()
        self.assertIn(compat.BEGIN,
                      (self.project / "AGENTS.md").read_text())
        revoked = admin.uninstall(self.project, self.runtime,
                                  self.package, self.identity)
        self.assertEqual(revoked["status"], "ABSENT")
        self.assertEqual(revoked["project_mutations"],
                         ["AGENTS.md (exact approved preimage restored)"])
        self.assertEqual((self.project / "AGENTS.md").read_bytes(),
                         self.before["agents"])
        self.assertEqual((self.project / ".git/index").read_bytes(), original_index)
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(admin.uninstall(
            self.project, self.runtime, self.package, self.identity)["status"], "ABSENT")

    def test_already_revoked_agents_can_uninstall_without_rewriting_project(self):
        self.both_approved()
        folder = self.folder_if_retained(ask.KIND_AGENTS)
        first = ask.revoke(folder, self.project, self.source)
        self.assertEqual(first["status"], "REVOKED_TEST_ONLY")
        self.assertEqual((self.project / "AGENTS.md").read_bytes(),
                         self.before["agents"])
        # The historical activation receipt is deliberately retained.
        self.assertTrue((folder / "activation.json").exists())
        before_index = (self.project / ".git/index").read_bytes()
        result = admin.uninstall(self.project, self.runtime,
                                 self.package, self.identity)
        self.assertEqual(result["status"], "ABSENT")
        self.assertEqual(result["project_mutations"], [])
        self.assertEqual((self.project / "AGENTS.md").read_bytes(),
                         self.before["agents"])
        self.assertEqual((self.project / ".git/index").read_bytes(), before_index)
        self.assertEqual(self.snapshot(), self.before)

    def test_modified_agents_blocks_detach_without_clobbering_user_changes(self):
        self.both_approved()
        file = self.project / "AGENTS.md"
        file.write_text(file.read_text() + "\nUser's own later change\n")
        before = file.read_bytes()
        with self.assertRaisesRegex(admin.AdminBlocked, "approved AGENTS content changed"):
            admin.uninstall(self.project, self.runtime, self.package, self.identity)
        self.assertEqual(file.read_bytes(), before)
        self.assertEqual(self.state()["status"], "INSTALLED_TEST_ONLY")
        self.assertTrue((self.runtime / admin.NAME / "package").is_symlink())

    def test_missing_known_link_repair_without_grant_changes(self):
        self.installed()
        (self.runtime / admin.NAME / "package").unlink()
        self.assertEqual(self.state()["status"], "MISSING_OWNED_LINK")
        with self.assertRaisesRegex(admin.AdminBlocked, "explicit repair"):
            self.installed()
        self.assertEqual(
            admin.repair(self.project, self.runtime, self.package,
                         self.identity)["status"], "INSTALLED_TEST_ONLY")
        self.assertEqual(self.snapshot(), self.before)

    def test_unknown_runtime_owner_or_package_link_never_deleted(self):
        self.installed()
        link = self.runtime / admin.NAME / "package"
        link.unlink()
        link.symlink_to(self.project)
        with self.assertRaisesRegex(admin.AdminBlocked, "package link"):
            admin.uninstall(self.project, self.runtime, self.package, self.identity)
        self.assertEqual(os.readlink(link), str(self.project))
        link.unlink()
        link.symlink_to(self.package)
        owner_file = self.runtime / admin.NAME / "owner.json"
        owner = json.loads(owner_file.read_bytes())
        owner["package_root"] = "foreign"
        owner_file.write_bytes(admin.canonical(owner))
        with self.assertRaisesRegex(admin.AdminBlocked, "identity changed"):
            admin.uninstall(self.project, self.runtime, self.package, self.identity)
        self.assertEqual(owner_file.read_bytes(), admin.canonical(owner))

    def test_permission_only_install_preserves_foreign_agents(self):
        (self.project / "AGENTS.md").write_text(
            "# Custom Project rules\n- Require targeted tests.\n")
        self.git("add", "AGENTS.md")
        self.git("-c", "user.name=Fixture", "-c",
                 "user.email=fixture@example.invalid",
                 "commit", "-qm", "custom Project instructions")
        info = self.installed()
        self.assertEqual(info["required_asks"], [ask.KIND_PERMISSION])
        self.assertFalse(info["ready_for_model_task"])
        self.approve(ask.KIND_PERMISSION)
        self.assertTrue(self.state()["ready_for_model_task"])
        self.assertEqual(admin.uninstall(
            self.project, self.runtime, self.package, self.identity)["status"], "ABSENT")

    def test_task_output_cannot_contaminate_installer_or_ask_roots(self):
        self.both_approved()
        for output in (
            self.runtime / "task-evidence",
            self.runtime.parent / "Task",
            self.folder(ask.KIND_AGENTS) / "nested-evidence",
            self.project / "docs" / "Task",
            self.package / "output",
        ):
            with self.subTest(output=output), \
                 mock.patch.object(admin, "invoke_native") as launched:
                with self.assertRaisesRegex(
                        admin.AdminBlocked, "cannot be inside"):
                    admin.run_task(
                        self.project, self.runtime, self.package, self.identity,
                        spec=self.pbase / "task.json",
                        plan=self.pbase / "plan.json",
                        opencode=self.pbase / "opencode", output=output,
                        repository="fixture/native", task="271", timeout=120)
                launched.assert_not_called()
                self.assertFalse(output.exists())

    def test_install_readiness_invalidated_by_new_task_deny(self):
        (self.project / "AGENTS.md").write_text("# Custom rules\n- Run tests.\n")
        (self.project / "opencode.json").write_text(
            '{"permission":{"task":"allow"}}\n')
        self.git("add", "AGENTS.md", "opencode.json")
        self.git("-c", "user.name=Fixture", "-c",
                 "user.email=test@example.invalid",
                 "commit", "--only", "-qm", "custom policy", "--",
                 "AGENTS.md", "opencode.json")
        info = self.installed()
        self.assertEqual(info["required_asks"], [])
        self.assertTrue(info["ready_for_model_task"])
        (self.project / "opencode.json").write_text(
            '{"permission":{"task":"deny"}}\n')
        changed = self.state()
        self.assertFalse(changed["Ask_scope_matches_current_Project"])
        self.assertFalse(changed["ready_for_model_task"])
        with mock.patch.object(admin, "invoke_native") as launched:
            with self.assertRaisesRegex(
                    admin.AdminBlocked, "policy changed after Install"):
                admin.run_task(
                    self.project, self.runtime, self.package, self.identity,
                    spec=self.pbase / "task.json", plan=self.pbase / "plan.json",
                    opencode=self.pbase / "opencode", output=self.pbase / "out",
                    repository="fixture/native", task="271", timeout=120)
            launched.assert_not_called()

    def test_uninstall_pending_requests_after_ordinary_project_edits(self):
        self.installed()
        (self.project / "AGENTS.md").write_text(
            self.original_agents + "\nUser updated Project conventions.\n")
        (self.project / "opencode.json").write_text(
            '{"permission":{"task":"allow"},"default_agent":"build"}\n')
        (self.project / "new-product.txt").write_text("User Product\n")
        self.git("add", "new-product.txt")
        self.git("-c", "user.name=Fixture", "-c",
                 "user.email=test@example.invalid",
                 "commit", "--only", "-qm", "normal product commit",
                 "--", "new-product.txt")
        changed = self.snapshot()
        detached = admin.uninstall(self.project, self.runtime,
                                   self.package, self.identity)
        self.assertEqual(detached["status"], "ABSENT")
        self.assertEqual(self.snapshot(), changed)
        self.assertEqual(len(detached["revoked_asks"]), 2)
        self.assertTrue(all(x["retired_without_active_grant"]
                            for x in detached["revoked_asks"]))

    def test_uninstall_revokes_permission_even_after_policy_changes(self):
        self.installed()
        self.approve(ask.KIND_PERMISSION)
        (self.project / "opencode.json").write_text(
            '{"permission":{"task":"allow"},"default_agent":"build"}\n')
        self.assertFalse(self.state()["ready_for_model_task"])
        changed = self.snapshot()
        outcome = admin.uninstall(self.project, self.runtime,
                                  self.package, self.identity)
        self.assertEqual(outcome["status"], "ABSENT")
        self.assertEqual(self.snapshot(), changed)
        self.assertEqual(ask.status(self.folder_if_retained(ask.KIND_PERMISSION),
                                    self.project, self.source)["status"], "REVOKED")

    def test_uninstall_approved_but_not_applied_agents_after_user_edits(self):
        self.installed()
        self.approve(ask.KIND_AGENTS)
        (self.project / "AGENTS.md").write_text(
            self.original_agents + "\nIndependent Project update\n")
        (self.project / "opencode.json").write_text(
            '{"permission":{"task":"allow"}}\n')
        before = self.snapshot()
        detached = admin.uninstall(self.project, self.runtime,
                                   self.package, self.identity)
        self.assertEqual(detached["status"], "ABSENT")
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(all(not item["AGENTS_restored"]
                            for item in detached["revoked_asks"]))

    def test_invalid_test_only_cli_and_runtime_path_refused(self):
        with mock.patch.object(admin, "verify_package", return_value=self.identity):
            module = str(Path(admin.__file__).resolve())
            args = [sys.executable, "-B", module, "install",
                    "--project-root", str(self.project),
                    "--runtime-root", str(self.runtime),
                    "--package-root", str(self.package),
                    "--expected-receipt-sha256", self.identity["receipt_sha256"]]
            proc = subprocess.run(args, capture_output=True, text=True, timeout=15)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(json.loads(proc.stdout)["status"], "BLOCKED")
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_invalid_task_identity_and_inputs_refused(self):
        for repository in ("", "invalid", "../unknown", "name/space with spaces"):
            with self.subTest(repository=repository):
                with self.assertRaises(admin.AdminBlocked):
                    admin.task_arguments(
                        self.pbase/"spec", self.pbase/"plan",
                        self.pbase/"open", self.pbase/"output",
                        repository, "271", 120)


if __name__ == "__main__":
    unittest.main()
