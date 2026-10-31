"""#256 bounded no-Task host ownership acceptance across #304 and #312.

Disposable real Git Project, private host state, synthetic immutable package.
The already-merged attachment preview MUST NOT be treated as runtime authority.
Neither subprocess calls Main.cycle, GitHub, OpenCode, Session or a model.
This verifies the existing building blocks, not runtime activation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import kagari_vnext_attach as attach
import kagari_vnext_main_control as control
import kagari_vnext_main_service as service
from tests import test_kagari_vnext_main_control as main_fixture


class AttachMainOwnerBoundaryTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing fully dirty/staged real-Git Main fixture.
        source = main_fixture.MainControlTests("runTest")
        source.setUp()
        self.addCleanup(source.doCleanups)
        self.source = source
        self.project = source.project
        self.home = source.home
        self.package = source.base / "inert-immutable-package"
        (self.package / "bin").mkdir(parents=True)
        for name in attach.REQUIRED_BINARIES:
            entry = self.package / "bin" / name
            entry.write_bytes(b"#!/bin/sh\nexit 97 # must never execute\n")
            entry.chmod(0o555)
        (self.package / "bin").chmod(0o555)
        self.package.chmod(0o555)
        self.addCleanup(self.writable_package)
        self.source.git("remote", "get-url", "origin")
        installed = self.attach("install")
        self.assertEqual(installed["status"], "ATTACHED", installed)
        self.assertTrue(installed["preflight"]["ignore"]["git_ignore_effective"])
        self.original_head = self.source.git("rev-parse", "HEAD")
        self.original_index = (self.project / ".git" / "index").read_bytes()
        self.original_files = self.source.project_bytes()

    def writable_package(self):
        self.package.chmod(0o755)
        (self.package / "bin").chmod(0o755)

    def attach(self, action):
        return attach.operate(
            self.project, self.home, self.package, action, attach.PROFILE)

    def assert_owner_untouched(self):
        self.assertEqual(self.source.git("rev-parse", "HEAD"), self.original_head)
        self.assertEqual((self.project / ".git" / "index").read_bytes(),
                         self.original_index)
        self.assertEqual(self.source.project_bytes(), self.original_files)

    def assert_no_task_authority(self, report):
        self.assertFalse(report["task_execution_authorized"], report)
        self.assertFalse(report["model_or_session_started"], report)

    def stop_owner_cleanly(self, process):
        # A cross-process integration fixture may run alongside Nix/CI jobs.
        # Require an actual CLEAN_RELEASE but allow bounded scheduler latency.
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            self.fail("Main owner did not cleanly release within 12 seconds")
        self.assertEqual((process.returncode, stderr), (0, b""))
        self.assertEqual(json.loads(stdout)["status"], "CLEAN_RELEASE")

    def test_preview_is_not_live_runtime_even_during_main_control_hold(self):
        worker, _ = self.source.hold()
        try:
            report = self.attach("doctor")
            self.assertEqual(report["status"], "READY")
            self.assertTrue(report["host_attached"])
            self.assertFalse(report["runtime_installed"])
            self.assertFalse(report["main_started"])
            self.assert_no_task_authority(report)
            code, result = self.source.cli("doctor")
            self.assertEqual((code, result[0]["status"]),
                             (2, "MAIN_ALREADY_LIVE_RECORD_NOT_READ"))
            self.source.assert_no_execution(result[0])
            fake = SimpleNamespace(
                store=SimpleNamespace(root=self.project,
                                      repository=self.source.repository))
            with patch.object(service, "_host_state_home",
                              return_value=self.source.host_lease_home):
                with self.assertRaises(service.MainError) as rejected:
                    with service.MainSupervisor(fake):
                        self.fail("MainSupervisor must not enter a held repo")
                self.assertEqual(rejected.exception.code, "MAIN_ALREADY_ACTIVE")
        finally:
            self.stop_owner_cleanly(worker)
        self.assert_owner_untouched()

    def test_caller_observation_disconnect_does_not_drop_owned_lease(self):
        log = self.source.base / "detached-owner-receipt.jsonl"
        with log.open("wb") as output:
            process = subprocess.Popen(
                self.source.args("hold", extra=("--hold-seconds", "30")),
                stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.PIPE, cwd=self.source.base,
                start_new_session=True)
        self.source.children.append(process)
        for _ in range(100):
            if log.exists() and log.stat().st_size:
                break
            self.assertIsNone(process.poll(), "Main owner exited before receipt")
            time.sleep(0.03)
        self.assertTrue(log.stat().st_size, "no durable log receipt")
        first = json.loads(log.read_bytes().splitlines()[0])
        self.assertEqual(first["status"], "HOLDING")
        self.source.assert_no_execution(first)
        # A new observer, not the initiating pipe or process handle, checks.
        code, result = self.source.cli("doctor")
        self.assertEqual((code, result[0]["status"]),
                         (2, "MAIN_ALREADY_LIVE_RECORD_NOT_READ"))
        process.send_signal(signal.SIGTERM)
        _out, stderr = process.communicate(timeout=8)
        self.assertEqual((process.returncode, stderr), (0, b""))
        self.assertEqual(json.loads(log.read_bytes().splitlines()[-1])["status"],
                         "CLEAN_RELEASE")
        code, result = self.source.cli("doctor")
        self.assertEqual((code, result[0]["status"]), (0, "CLEAN"))
        self.assert_owner_untouched()

    def test_crash_requires_exact_reconcile_not_pid_or_implicit_restart(self):
        worker, _ = self.source.hold()
        worker.kill()
        worker.communicate(timeout=5)
        self.assertNotEqual(worker.returncode, 0)
        code, result = self.source.cli("doctor")
        self.assertEqual((code, result[0]["status"]),
                         (2, "RECONCILIATION_REQUIRED"))
        digest = result[0]["record_sha256"]
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        code, denied = self.source.cli(
            "hold", extra=("--hold-seconds", "0.01"))
        self.assertEqual((code, denied[0]["code"]),
                         (2, "RECONCILIATION_REQUIRED"))
        code, recovered = self.source.cli(
            "reconcile", extra=("--expected-record-sha256", digest))
        self.assertEqual((code, recovered[0]["status"]),
                         (0, "RECONCILED_NO_EXECUTION"))
        self.source.assert_no_execution(recovered[0])
        restarted, _ = self.source.hold()
        self.stop_owner_cleanly(restarted)
        self.assertEqual(self.attach("doctor")["status"], "READY")
        self.assert_owner_untouched()

    def test_clean_detach_reinstall_preserves_project_and_main_evidence(self):
        # Sentinel owner data is deliberately not KAGARI install payload.
        sentinels = {
            '.task-state/task-record.json': b'{"owner":"project"}\n',
            '.automation/evidence/exact-receipt.json': b'{"proof":"owner"}\n',
        }
        for name, data in sentinels.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.original_files = self.source.project_bytes()
        worker, _ = self.source.hold()
        self.stop_owner_cleanly(worker)
        before_main = next((self.home / control.NAMESPACE).glob("*.json")).read_bytes()
        removed = self.attach("uninstall")
        self.assertEqual(removed["status"], "DETACHED")
        self.assert_no_task_authority(removed)
        self.assertEqual(self.attach("uninstall")["status"], "ABSENT")
        self.assertEqual(self.attach("install")["status"], "ATTACHED")
        self.assertEqual(self.attach("repair")["status"], "READY")
        self.assertEqual(next((self.home / control.NAMESPACE).glob("*.json")).read_bytes(),
                         before_main)
        self.assert_owner_untouched()

    def test_preview_uninstall_does_not_stop_an_independent_live_main(self):
        # Characterization of a STILL-MISSING runtime gate: Preview cannot be
        # used as an active Runtime detach, even when its own receipt is gone.
        worker, _ = self.source.hold()
        try:
            removed = self.attach("uninstall")
            self.assertEqual(removed["status"], "DETACHED")
            self.assertFalse(removed["runtime_installed"])
            self.assert_no_task_authority(removed)
            code, owner = self.source.cli("doctor")
            self.assertEqual((code, owner[0]["status"]),
                             (2, "MAIN_ALREADY_LIVE_RECORD_NOT_READ"))
            self.assertEqual(self.attach("doctor")["status"], "ABSENT")
        finally:
            self.stop_owner_cleanly(worker)
        self.assertEqual(self.attach("install")["status"], "ATTACHED")
        self.assert_owner_untouched()

    def test_supervisor_lock_excludes_controller_in_independent_process(self):
        # The fake Main has no dispatch method: only the real Supervisor flock.
        wrapper = """
import pathlib,sys,time
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
import kagari_vnext_main_service as service
service._host_state_home=lambda:pathlib.Path(sys.argv[2])
fake=SimpleNamespace(store=SimpleNamespace(root=pathlib.Path(sys.argv[3]),
                                          repository=sys.argv[4]))
with service.MainSupervisor(fake):
    print('HOLDING',flush=True)
    time.sleep(30)
"""
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", wrapper, str(TOOLS),
             str(self.source.host_lease_home), str(self.project),
             self.source.repository],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.source.base,
            start_new_session=True)
        self.source.children.append(process)
        try:
            ready, _, _ = select.select([process.stdout], [], [], 12)
            self.assertTrue(ready, "Supervisor never acknowledged ownership")
            self.assertEqual(process.stdout.readline().strip(), b"HOLDING")
            code, result = self.source.cli(
                "hold", extra=("--hold-seconds", "0.01"))
            self.assertEqual((code, result[0]["code"]),
                             (2, "MAIN_ALREADY_LIVE"))
            self.assertEqual(self.attach("doctor")["status"], "READY")
        finally:
            process.terminate()
            _, stderr = process.communicate(timeout=8)
            self.assertIn(process.returncode, (-signal.SIGTERM, 0), stderr)
        worker, _ = self.source.hold()
        self.stop_owner_cleanly(worker)
        self.assert_owner_untouched()


if __name__ == "__main__":
    unittest.main()
