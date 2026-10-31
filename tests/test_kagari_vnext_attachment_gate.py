"""#256: nonexecuting host attachment lock / exact receipt guard tests.

Real disposable Git, synthetic immutable package that exits 97 if executed.
No network, Task, Main, Session, OpenCode, Nix evaluation, or model is run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import kagari_vnext_attachment_gate as gate
import kagari_vnext_attach as attach
from tests import test_kagari_vnext_attach as attach_fixture


class AttachmentGateTests(unittest.TestCase):
    def setUp(self):
        fixture = attach_fixture.AttachTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.project, self.home, self.package = (
            fixture.project, fixture.home, fixture.package)
        self.initial_index = (self.project / ".git" / "index").read_bytes()
        self.initial_head = fixture.git("rev-parse", "HEAD")

    def doctor(self, *, package=None):
        return gate.doctor(self.project, self.home,
                           package or self.package)

    def hold(self):
        return gate.hold(self.project, self.home, self.package)

    def assert_git_preserved(self):
        self.assertEqual(self.fixture.git("rev-parse", "HEAD"),
                         self.initial_head)
        self.assertEqual((self.project / ".git" / "index").read_bytes(),
                         self.initial_index)

    def test_absent_does_not_create_state_or_config_or_main(self):
        with self.assertRaises(gate.GateBlocked) as caught:
            self.doctor()
        self.assertEqual(caught.exception.code, "GATE_ATTACHMENT_REQUIRED")
        self.assertFalse((self.project / ".gitignore").exists())
        self.assertFalse((self.home / attach.STATE_DIR).exists())
        self.assert_git_preserved()

    def test_explicit_install_only_then_repeatable_read_only_gate(self):
        self.assertEqual(self.fixture.operate("install")["status"], "ATTACHED")
        owner_receipt = self.fixture.receipt()
        before = owner_receipt.read_bytes()
        inode = owner_receipt.stat().st_ino
        for _ in range(3):
            result = self.doctor()
            self.assertEqual(result["status"], "ATTACHMENT_READY_ONLY")
            self.assertEqual(result["authority"],
                             "PRECONDITION_NOT_EXECUTION_AUTHORITY")
            self.assertEqual(result["package_root"], str(self.package))
            self.assertEqual(result["attachment_receipt_sha256"],
                             hashlib.sha256(before).hexdigest())
            for field in ("runtime_installed", "main_started",
                          "task_execution_authorized", "model_or_session_started"):
                self.assertIs(result[field], False)
        self.assertEqual(owner_receipt.read_bytes(), before)
        self.assertEqual(owner_receipt.stat().st_ino, inode)
        self.assertFalse((self.project / ".worktrees").exists())
        self.assert_git_preserved()

    def test_negated_or_missing_ignore_blocks_even_if_receipt_exists(self):
        self.fixture.operate("install")
        ignore = self.project / ".gitignore"
        for content in (b"/.worktrees/\n!/.worktrees/\n", b"other/\n"):
            ignore.write_bytes(content)
            with self.assertRaises(gate.GateBlocked) as caught:
                self.doctor()
            self.assertEqual(caught.exception.code,
                             "GATE_WORKTREE_PREFLIGHT_NOT_READY")
            self.assertEqual(ignore.read_bytes(), content)
        self.assert_git_preserved()

    def test_inflight_owner_ignore_drift_is_detected_at_guard_exit(self):
        self.fixture.operate("install")
        ignore = self.project / ".gitignore"
        with self.assertRaises(gate.GateBlocked) as caught:
            with self.hold() as report:
                self.assertEqual(report["status"], "ATTACHMENT_READY_ONLY")
                ignore.write_bytes(b"/.worktrees/\n!/.worktrees/\n")
        self.assertEqual(caught.exception.code,
                         "GATE_WORKTREE_PREFLIGHT_NOT_READY")
        self.assertEqual(ignore.read_bytes(),
                         b"/.worktrees/\n!/.worktrees/\n")
        # No silent repair or deletion on uncertainty. A separate intentional
        # host-owned correction restores eligibility for the next observation.
        ignore.write_bytes(b"/.worktrees/\n")
        self.assertEqual(self.doctor()["status"], "ATTACHMENT_READY_ONLY")
        self.assert_git_preserved()

    def test_unsafe_receipt_symlink_refused_without_touching_target(self):
        self.fixture.operate("install")
        target = self.home / "human-state"
        target.write_bytes(b"human evidence must remain immutable\n")
        receipt = self.fixture.receipt()
        receipt.unlink()
        receipt.symlink_to(target)
        with self.assertRaises((OSError, attach.Blocked, gate.GateBlocked)):
            self.doctor()
        self.assertEqual(target.read_bytes(),
                         b"human evidence must remain immutable\n")
        self.assertTrue(receipt.is_symlink())
        self.assert_git_preserved()

    def test_foreign_package_with_equivalent_binary_text_is_not_authority(self):
        self.fixture.operate("install")
        foreign = self.fixture.base / "foreign-package"
        shutil.copytree(self.package, foreign, symlinks=True)
        # Same bytes and mode are not enough: package path and inode identity
        # must match the installed exact receipt.
        self.addCleanup(lambda: (foreign / "bin").chmod(0o755))
        self.addCleanup(lambda: foreign.chmod(0o755))
        with self.assertRaises((gate.GateBlocked, attach.Blocked)) as caught:
            self.doctor(package=foreign)
        self.assertEqual(getattr(caught.exception, "code", None),
                         "ATTACH_RECEIPT_IDENTITY_CHANGED")
        self.assert_git_preserved()

    def test_guard_fails_closed_when_second_gate_tries_to_enter(self):
        self.fixture.operate("install")
        with self.hold() as status:
            self.assertFalse(status["task_execution_authorized"])
            with self.assertRaises(gate.GateBlocked) as caught:
                self.doctor()
            self.assertEqual(caught.exception.code, "GATE_ATTACHMENT_BUSY")
        self.assertEqual(self.doctor()["status"], "ATTACHMENT_READY_ONLY")
        self.assert_git_preserved()

    def test_uninstall_waits_for_inflight_gate_and_preserves_owner_bytes(self):
        self.fixture.operate("install")
        (self.project / "owner-untracked.txt").write_bytes(b"valuable user data\n")
        receipt = self.fixture.receipt()
        before = receipt.read_bytes()
        started = threading.Event()
        completed = threading.Event()
        state = []

        def uninstall_in_parallel():
            started.set()
            try:
                state.append(self.fixture.operate("uninstall")["status"])
            except BaseException as error:
                state.append(repr(error))
            finally:
                completed.set()

        thread = threading.Thread(target=uninstall_in_parallel, daemon=True)
        with self.hold() as held:
            self.assertEqual(held["status"], "ATTACHMENT_READY_ONLY")
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(completed.wait(0.2),
                             "Uninstall must not pass a held attachment lease")
            self.assertEqual(receipt.read_bytes(), before)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(state, ["DETACHED"])
        self.assertFalse(receipt.exists())
        self.assertEqual((self.project / "owner-untracked.txt").read_bytes(),
                         b"valuable user data\n")
        self.assertEqual((self.project / ".gitignore").read_bytes(),
                         b"/.worktrees/\n")
        self.assert_git_preserved()

    def test_doctor_cli_does_not_grant_execution(self):
        self.fixture.operate("install")
        command = [sys.executable, str(TOOLS / "kagari_vnext_attachment_gate.py"),
                   "doctor", "--profile", attach.PROFILE,
                   "--project-root", str(self.project),
                   "--state-home", str(self.home),
                   "--package-root", str(self.package)]
        process = subprocess.run(command, text=True, capture_output=True,
                                 timeout=20, check=False, cwd=self.fixture.base)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "ATTACHMENT_READY_ONLY")
        self.assertIs(report["runtime_installed"], False)
        self.assertIs(report["task_execution_authorized"], False)
        self.assertIs(report["model_or_session_started"], False)
        self.assert_git_preserved()

    def test_doctor_cli_absent_fails_closed_without_implicit_install(self):
        command = [sys.executable, str(TOOLS / "kagari_vnext_attachment_gate.py"),
                   "doctor", "--profile", attach.PROFILE,
                   "--project-root", str(self.project),
                   "--state-home", str(self.home),
                   "--package-root", str(self.package)]
        proc = subprocess.run(command, text=True, capture_output=True,
                              timeout=20, check=False, cwd=self.fixture.base)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["code"],
                         "GATE_ATTACHMENT_REQUIRED")
        self.assertFalse((self.home / attach.STATE_DIR).exists())
        self.assertFalse((self.project / ".gitignore").exists())
        self.assert_git_preserved()


if __name__ == "__main__":
    unittest.main()
