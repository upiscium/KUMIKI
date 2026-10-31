"""#256 R2: real Attach receipt and canonical Main host lock share one lifecycle.

Disposable Git and fake OpenCode fixture only; no real Task/model, GitHub write,
remote PR, production host or consumer repository is touched by these tests.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
import threading
import unittest
from unittest.mock import patch

from tests import test_kagari_vnext_main_service as service_fixture
from tools import kagari_vnext_attachment_gate as gate
from tools import kagari_vnext_attach as attach
from tools import kagari_vnext_main_service as service
from tools.kagari_vnext_main import MainError


class AttachedMainBoundaryTests(unittest.TestCase):
    def setUp(self):
        original = service_fixture.MainServiceTest("runTest")
        original.setUp()
        self.addCleanup(original.doCleanups)
        self.fixture = original
        self.project = original.source.project
        self.main = original.main
        self.home = original.source.fixture.temp_root / "attachment-state"
        self.home.mkdir(mode=0o700)
        self.home.chmod(0o700)
        self.package = original.source.fixture.temp_root / "inert-package"
        (self.package / "bin").mkdir(parents=True)
        for name in attach.REQUIRED_BINARIES:
            exe = self.package / "bin" / name
            exe.write_bytes(b"#!/bin/sh\nexit 97 # never execute host payload\n")
            exe.chmod(0o555)
        self.package.chmod(0o555)
        (self.package / "bin").chmod(0o555)
        self.addCleanup(self.writable_package)
        self.config = service.MainAttachmentConfig(self.home, self.package)
        self.head = service_fixture.git_fixtures.git(
            "rev-parse", "HEAD", cwd=self.project).decode().strip()
        self.index = (self.project / ".git" / "index").read_bytes()

    def writable_package(self):
        self.package.chmod(0o755)
        (self.package / "bin").chmod(0o755)

    def attach(self, action):
        return attach.operate(
            self.project, self.home, self.package, action, attach.PROFILE)

    def assert_owner_untouched(self):
        self.assertEqual(service_fixture.git_fixtures.git(
            "rev-parse", "HEAD", cwd=self.project).decode().strip(), self.head)
        self.assertEqual((self.project / ".git" / "index").read_bytes(), self.index)

    def test_no_attachment_refuses_before_main_lock_or_task(self):
        missing_lock = service._host_repo_lock(self.main.store.repository)
        self.assertFalse(missing_lock.exists())
        with self.assertRaises(MainError) as denied:
            with service.attached_supervisor(self.main, self.config):
                self.fail("Absent Attach must not reach Main")
        self.assertEqual(denied.exception.code, "MAIN_ATTACHMENT_UNVERIFIED")
        self.assertFalse(missing_lock.exists())
        self.assertEqual(len(self.fixture.source.prompts()), 0)
        self.assertFalse((self.home / attach.STATE_DIR).exists())
        self.assert_owner_untouched()

    def test_active_main_uses_both_existing_locks_and_exact_fake_kickoff(self):
        self.assertEqual(self.attach("install")["status"], "ATTACHED")
        with service.attached_supervisor(self.main, self.config) as runner:
            runner._assert_owned(self.main)
            with self.assertRaises(MainError) as denied:
                with service.MainSupervisor(self.fixture.source.make_main()):
                    self.fail("Second Main acquired canonical lock")
            self.assertEqual(denied.exception.code, "MAIN_ALREADY_ACTIVE")
            self.assertEqual(self.fixture.run_lease_child(), "MAIN_ALREADY_ACTIVE")
            self.assertEqual(runner.cycle()["tasks"][0]["prompt_state"], "SUBMITTED")
            self.assertEqual(runner.cycle()["tasks"][0]["prompt_state"],
                             "ALREADY_DISPATCHED")
            self.assertEqual(len(self.fixture.source.prompts()), 1)
        self.assertEqual(self.attach("doctor")["status"], "READY")
        with self.assertRaises(MainError) as ended:
            runner._assert_owned(self.main)
        self.assertEqual(ended.exception.code, "MAIN_OWNER_REQUIRED")
        self.assert_owner_untouched()

    def test_effect_time_invalid_ignore_is_denied_without_fake_prompt(self):
        self.attach("install")
        ignore = self.project / ".gitignore"
        original = ignore.read_bytes()
        with service.attached_supervisor(self.main, self.config) as runner:
            ignore.write_bytes(b"/.worktrees/\n!/.worktrees/\n")
            with self.assertRaises(MainError) as denied:
                runner.cycle(dispatch=True)
            self.assertEqual(denied.exception.code, "MAIN_ATTACHMENT_UNVERIFIED")
            self.assertEqual(len(self.fixture.source.prompts()), 0)
            self.assertEqual(ignore.read_bytes(),
                             b"/.worktrees/\n!/.worktrees/\n")
            ignore.write_bytes(original)
            runner._assert_owned(self.main)
        self.assert_owner_untouched()

    def test_uninstall_during_owned_main_waits_for_clean_release(self):
        self.attach("install")
        untracked = self.project / "my-human-file.txt"
        untracked.write_bytes(b"never delete user evidence\n")
        started, completed = threading.Event(), threading.Event()
        results = []

        def uninstall():
            started.set()
            try:
                results.append(self.attach("uninstall")["status"])
            except BaseException as exc:
                results.append(type(exc).__name__ + ":" + str(exc))
            finally:
                completed.set()

        worker = threading.Thread(target=uninstall, daemon=True)
        with service.attached_supervisor(self.main, self.config) as runner:
            runner._assert_owned(self.main)
            worker.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(completed.wait(0.2),
                             "Uninstall was allowed before Main quiescence")
            self.assertEqual(len(self.fixture.source.prompts()), 0)
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, ["DETACHED"])
        self.assertEqual(untracked.read_bytes(), b"never delete user evidence\n")
        self.assertEqual(len(self.fixture.source.prompts()), 0)
        self.assert_owner_untouched()

    def test_same_main_repo_lock_refuses_when_other_owner_live(self):
        self.attach("install")
        with service.MainSupervisor(self.main):
            with self.assertRaises(MainError) as denied:
                with service.attached_supervisor(
                    self.fixture.source.make_main(), self.config):
                    self.fail("Bound Main must honor existing singleton owner")
            self.assertEqual(denied.exception.code, "MAIN_ALREADY_ACTIVE")
        self.assertEqual(self.attach("doctor")["status"], "READY")
        with service.attached_supervisor(
            self.fixture.source.make_main(), self.config) as runner:
            runner._assert_owned(runner.main)
        self.assert_owner_untouched()

    def test_lease_is_invalid_outside_guard_context(self):
        self.attach("install")
        with gate.hold_verified(self.project, self.home, self.package) as lease:
            lease.verify(project=self.project)
            wrong = self.home / "different-project"
            with self.assertRaises(gate.GateBlocked) as denied:
                lease.verify(project=wrong)
            self.assertEqual(denied.exception.code, "GATE_PROJECT_MISMATCH")
        with self.assertRaises(gate.GateBlocked) as denied:
            lease.verify(project=self.project)
        self.assertEqual(denied.exception.code, "GATE_OWNER_NOT_ACTIVE")

    def test_forked_child_exit_cannot_release_parent_attachment_flock(self):
        self.attach("install")
        ctx = gate.hold_verified(self.project, self.home, self.package)
        lease = ctx.__enter__()
        try:
            child = os.fork()
            if child == 0:
                try:
                    try:
                        ctx.__exit__(None, None, None)
                    except gate.GateBlocked as error:
                        if error.code != "GATE_OWNER_NOT_ACTIVE":
                            os._exit(3)
                    else:
                        os._exit(4)
                    os._exit(0)
                except BaseException:
                    os._exit(5)
            waited, status = os.waitpid(child, 0)
            self.assertEqual(waited, child)
            self.assertTrue(os.WIFEXITED(status))
            self.assertEqual(os.WEXITSTATUS(status), 0)
            lease.verify(project=self.project)
            with self.assertRaises(gate.GateBlocked) as denied:
                gate.doctor(self.project, self.home, self.package)
            self.assertEqual(denied.exception.code, "GATE_ATTACHMENT_BUSY")
        finally:
            ctx.__exit__(None, None, None)
        self.assertEqual(gate.doctor(self.project, self.home, self.package)["status"],
                         "ATTACHMENT_READY_ONLY")
        self.assert_owner_untouched()

    def test_main_python_injection_uses_the_bound_supervisor(self):
        self.attach("install")
        with patch.object(service, "build_main", return_value=self.main):
            with redirect_stdout(StringIO()) as captured:
                result = service.main(
                    ["--config", str(self.fixture.config), "dispatch"],
                    attachment=self.config)
        self.assertEqual(result, 0)
        report = json.loads(captured.getvalue())
        self.assertEqual(report["tasks"][0]["prompt_state"], "SUBMITTED")
        self.assertEqual(len(self.fixture.source.prompts()), 1)
        self.assert_owner_untouched()

    def test_host_injected_attachment_is_not_silently_ignored_for_observe(self):
        # A Host asking for an attached observation must not receive a report
        # from a different, unbound codepath with no guard lifetime.
        self.attach("install")
        with redirect_stdout(StringIO()) as captured:
            code = service.main(
                ["--config", str(self.fixture.config), "observe"],
                attachment=self.config)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(captured.getvalue())["error_code"],
                         "MAIN_ATTACHMENT_OBSERVE_ONLY_UNSUPPORTED")
        self.assertEqual(len(self.fixture.source.prompts()), 0)
        self.assert_owner_untouched()

    def test_plain_doctor_receipt_cannot_forge_a_live_lease(self):
        self.attach("install")
        report = gate.doctor(self.project, self.home, self.package)
        with self.assertRaises(gate.GateBlocked) as denied:
            gate.AttachmentLease(self.project, self.home, self.package,
                                 attach.PROFILE, report)
        self.assertEqual(denied.exception.code, "GATE_OWNER_NOT_ACTIVE")
        self.assertEqual(len(self.fixture.source.prompts()), 0)
        self.assert_owner_untouched()

    def test_wrong_attachment_binding_is_refused_without_dispatch(self):
        for config in (
            service.MainAttachmentConfig(self.home, self.package,
                                         attach.PROFILE),
        ):
            self.assertEqual(config.profile, attach.PROFILE)
        with self.assertRaises(MainError) as denied:
            service.MainAttachmentConfig(self.home, self.package, "wrong-profile")
        self.assertEqual(denied.exception.code, "MAIN_ATTACHMENT_BINDING_INVALID")
        self.attach("install")
        with gate.hold_verified(self.project, self.home, self.package) as lease:
            with self.assertRaises(MainError) as denied:
                service.MainSupervisor(
                    self.main, attachment_lease=lease, observe_only=True)
            self.assertEqual(denied.exception.code,
                             "MAIN_ATTACHMENT_BINDING_INVALID")
        self.assertEqual(len(self.fixture.source.prompts()), 0)
        self.assert_owner_untouched()

    def test_package_changed_during_ownership_denies_effect_and_releases_lock(self):
        self.attach("install")
        payload = self.package / "bin" / attach.REQUIRED_BINARIES[0]
        with self.assertRaises(MainError) as denied:
            with service.attached_supervisor(self.main, self.config) as runner:
                self.package.chmod(0o755)
                (self.package / "bin").chmod(0o755)
                payload.chmod(0o755)
                payload.write_bytes(b"#!/bin/sh\nexit 98\n")
                payload.chmod(0o555)
                self.package.chmod(0o555)
                (self.package / "bin").chmod(0o555)
                with self.assertRaises(MainError) as refused:
                    runner.cycle()
                self.assertEqual(refused.exception.code, "MAIN_ATTACHMENT_UNVERIFIED")
                self.assertEqual(len(self.fixture.source.prompts()), 0)
        self.assertEqual(denied.exception.code, "MAIN_ATTACHMENT_UNVERIFIED")
        # The Host owns no mutating repair shortcut for changed package bytes.
        self.assertEqual(len(self.fixture.source.prompts()), 0)
        with service.MainSupervisor(self.fixture.source.make_main()):
            pass
        self.assert_owner_untouched()


if __name__ == "__main__":
    unittest.main()
