from __future__ import annotations

import fcntl
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "components" / "agent-core" / ".automation" / "bin"
sys.path.insert(0, str(BIN))
spec = importlib.util.spec_from_file_location("maintenance_ref_lock_test", BIN / "maintenance_lifecycle.py")
assert spec and spec.loader
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)
bridge_spec = importlib.util.spec_from_file_location(
    "maintenance_ref_recovery_bridge_test", ROOT / "tools" / "automation_recovery_bridge.py"
)
assert bridge_spec and bridge_spec.loader
bridge = importlib.util.module_from_spec(bridge_spec)
bridge_spec.loader.exec_module(bridge)


class LegacyRefLockRecoveryTest(unittest.TestCase):
    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
        )
        return result.stdout.strip()

    def fixture(self, root: Path):
        root.mkdir()
        self.git(root, "init", "-b", "main")
        self.git(root, "config", "user.name", "Recovery Fixture")
        self.git(root, "config", "user.email", "recovery@example.invalid")
        (root / ".gitignore").write_text("/.task-state/\n/.worktrees/\n", encoding="utf-8")
        (root / "data.txt").write_text("base\n", encoding="utf-8")
        self.git(root, "add", ".")
        self.git(root, "commit", "-m", "baseline")
        task = root / ".worktrees" / "15-maintenance"
        self.git(root, "worktree", "add", "-b", "task/15-maintenance", str(task))
        (task / "data.txt").write_text("maintenance\n", encoding="utf-8")
        self.git(task, "add", "data.txt")
        self.git(task, "commit", "-m", "maintenance")
        task_head = self.git(task, "rev-parse", "HEAD")
        (root / "data.txt").write_text("published\n", encoding="utf-8")
        self.git(root, "add", "data.txt")
        self.git(root, "commit", "-m", "merged to main")
        merge_head = self.git(root, "rev-parse", "HEAD")
        self.git(root, "update-ref", "refs/remotes/origin/main", merge_head)
        self.git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        state_dir = task / ".task-state"
        state_dir.mkdir()
        (state_dir / "task.md").write_text(
            "# Task 15\n\n## Identity\n\n"
            f"- Task ID: 15\n- Branch: task/15-maintenance\n- Worktree: {task}\n"
            "\n## Current state\n\n- Status: initialized\n- Blockers: none\n",
            encoding="utf-8",
        )
        for item in maintenance._TERMINAL_EVIDENCE_FILES:
            if item != "task.md":
                (state_dir / item).write_text("{}\n", encoding="utf-8")
        common = root / ".git"
        locks = (
            common / "refs/heads/main.lock",
            common / "refs/heads/task/15-maintenance.lock",
        )
        for path, head in zip(locks, (merge_head, task_head)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(head + "\n", encoding="ascii")
            path.chmod(0o600)
        record = maintenance.lifecycle.WorktreeRecord(task, "task/15-maintenance", task_head)
        return root, record, locks, merge_head, task_head

    def run_recovery(
        self, root: Path, record, main_head: str, task_head: str,
        *, digest: str | None = None, creator_pid: int = 999999999
    ):
        def validated_receipt(task_record, task_id):
            self.assertEqual(task_id, "15")
            result = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=task_record.path, text=True, capture_output=True, check=True,
            )
            if result.stdout.strip():
                raise maintenance.MaintenanceError(
                    "maintenance Task worktree is not clean after commit"
                )
            return {"commit_sha": task_head}

        with (
            mock.patch.object(
                maintenance, "_stored_contract",
                return_value=(record, {"repository": "example/repo"}),
            ),
            mock.patch.object(
                maintenance, "_require_live_maintenance_contract"
            ) as live_issue,
            mock.patch.object(
                maintenance, "_validate_consumed_receipt",
                side_effect=validated_receipt,
            ),
            mock.patch.object(
                maintenance, "_publication_evidence",
                return_value=(["data.txt"], "15: maintenance", "body"),
            ),
            mock.patch.object(
                maintenance, "_merged_pr",
                return_value={"mergeCommitOid": main_head},
            ),
        ):
            return bridge._maintenance_ref_lock_recovery(
                {"maintenance_lifecycle": maintenance, "task_lifecycle": maintenance.lifecycle},
                root, "15", 17, main_head, task_head, creator_pid,
                apply_digest=digest,
            )

    def test_inspection_then_exact_digest_removes_only_two_orphans(self):
        with tempfile.TemporaryDirectory() as directory:
            root, record, locks, main_head, task_head = self.fixture(Path(directory) / "repo")
            unrelated = root / ".git" / "agent-core" / "cleanup.lock"
            unrelated.parent.mkdir(exist_ok=True)
            unrelated.write_text("unrelated", encoding="utf-8")
            before = [lock.read_bytes() for lock in locks]
            # Real Linux exclusive write leases, with no mocked process scan.
            result = self.run_recovery(root, record, main_head, task_head)
            self.assertEqual(result["status"], "INSPECT_ONLY")
            self.assertEqual([lock.read_bytes() for lock in locks], before)
            with self.assertRaisesRegex(bridge.BridgeError, "inspection is stale"):
                self.run_recovery(root, record, main_head, task_head, digest="0" * 64)
            self.assertEqual([lock.read_bytes() for lock in locks], before)
            applied = self.run_recovery(
                root, record, main_head, task_head, digest=result["recoveryDigest"]
            )
            self.assertEqual(applied["status"], "EXACT_REF_LOCKS_RECOVERED")
            self.assertTrue(all(not lock.exists() for lock in locks))
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "unrelated")
            self.assertEqual(self.git(root, "rev-parse", "HEAD"), main_head)
            self.assertEqual(self.git(record.path, "rev-parse", "HEAD"), task_head)
            self.assertEqual(
                maintenance.lifecycle.state_status(record.path / ".task-state/task.md"),
                "initialized",
            )

    def test_other_process_holding_open_fd_denies_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root, record, locks, main_head, task_head = self.fixture(Path(directory) / "repo")
            holder = subprocess.Popen(
                [
                    sys.executable, "-B", "-c",
                    "import os,sys,time; "
                    "f=os.open(sys.argv[1],os.O_RDONLY); "
                    "print('OPEN',flush=True); time.sleep(20)",
                    str(locks[0]),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert holder.stdout is not None
                self.assertEqual(holder.stdout.readline().strip(), "OPEN")
                with self.assertRaisesRegex(
                    bridge.BridgeError, "open holder or leases are unsupported"
                ):
                    self.run_recovery(root, record, main_head, task_head)
                self.assertTrue(all(lock.exists() for lock in locks))
            finally:
                holder.terminate()
                holder.wait(timeout=5)
                if holder.stdout:
                    holder.stdout.close()
                if holder.stderr:
                    holder.stderr.close()
            result = self.run_recovery(root, record, main_head, task_head)
            self.assertEqual(result["status"], "INSPECT_ONLY")

    def test_broken_exclusive_lease_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root, record, locks, main_head, task_head = self.fixture(Path(directory) / "repo")
            refs = (
                ("main", main_head),
                ("task/15-maintenance", task_head),
            )
            with bridge._inspected_terminal_ref_locks(
                record, refs, maintenance.lifecycle
            ) as checked:
                self.assertTrue(all(
                    fcntl.fcntl(lock["fileFd"], fcntl.F_GETLEASE) == fcntl.F_WRLCK
                    for lock in checked
                ))
                fcntl.fcntl(
                    checked[0]["fileFd"], fcntl.F_SETLEASE, fcntl.F_UNLCK
                )
                with self.assertRaisesRegex(
                    bridge.BridgeError, "exclusive lease was broken"
                ):
                    bridge._require_exclusive_ref_leases(checked)
            self.assertTrue(all(lock.exists() for lock in locks))

    def test_late_metadata_and_path_drift_rejected_before_deletion(self):
        for variant in ("mode", "hardlink", "rename"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root, record, locks, main_head, task_head = self.fixture(
                    Path(directory) / "repo"
                )
                inspection = self.run_recovery(root, record, main_head, task_head)
                approved = inspection["recoveryDigest"]
                # Precreate the new inode before taking a kernel lease.
                replacement = root / ".git" / "replacement-staged"
                replacement.write_bytes(locks[0].read_bytes())
                replacement.chmod(0o600)
                observed = []
                genuine = bridge._require_ref_lock_snapshots

                def inject_once(items):
                    genuine(items)
                    observed.append(True)
                    if len(observed) == 1:
                        if variant == "mode":
                            locks[0].chmod(0o644)
                        elif variant == "hardlink":
                            os.link(locks[0], locks[0].with_suffix(".backup"))
                        else:
                            locks[0].rename(root / ".git" / "original-orphan")
                            replacement.rename(locks[0])

                with mock.patch.object(
                    bridge, "_require_ref_lock_snapshots", side_effect=inject_once
                ):
                    with self.assertRaisesRegex(
                        bridge.BridgeError, "approved contents or metadata changed"
                    ):
                        self.run_recovery(
                            root, record, main_head, task_head, digest=approved
                        )
                # The first snapshot succeeds; the second must raise before
                # reporting a second successful comparison.
                self.assertEqual(len(observed), 1)
                self.assertTrue(all(lock.exists() for lock in locks))
                self.assertEqual(
                    maintenance.lifecycle.state_status(
                        record.path / ".task-state" / "task.md"
                    ),
                    "initialized",
                )

    def test_late_tracked_or_untracked_task_worktree_drift_blocks_lock_deletion(self):
        # HEAD and all six Task State evidence files stay unchanged. Mutate
        # only a product file after the inspector has opened the exact locks.
        # The final locked clean-worktree check must reject before first unlink.
        for variant in ("tracked", "untracked"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root, record, locks, main_head, task_head = self.fixture(
                    Path(directory) / "repo"
                )
                approved = self.run_recovery(
                    root, record, main_head, task_head
                )["recoveryDigest"]
                real_check = bridge._require_ref_lock_snapshots
                observed = []

                def mutate_after_initial_lock_check(items):
                    real_check(items)
                    observed.append(True)
                    if len(observed) == 1:
                        if variant == "tracked":
                            (record.path / "data.txt").write_text(
                                "late tracked edit\n", encoding="utf-8"
                            )
                        else:
                            (record.path / "unscheduled.txt").write_text(
                                "late untracked edit\n", encoding="utf-8"
                            )

                with mock.patch.object(
                    bridge, "_require_ref_lock_snapshots",
                    side_effect=mutate_after_initial_lock_check,
                ):
                    with self.assertRaisesRegex(
                        maintenance.MaintenanceError,
                        "maintenance Task worktree is not clean",
                    ):
                        self.run_recovery(
                            root, record, main_head, task_head, digest=approved
                        )
                self.assertEqual(len(observed), 1)
                self.assertTrue(all(lock.is_file() for lock in locks))
                self.assertEqual(
                    maintenance.lifecycle.state_status(
                        record.path / ".task-state" / "task.md"
                    ),
                    "initialized",
                )
                self.assertEqual(self.git(record.path, "rev-parse", "HEAD"), task_head)

    def test_late_content_change_after_lease_break_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, record, locks, main_head, task_head = self.fixture(
                Path(directory) / "repo"
            )
            digest = self.run_recovery(root, record, main_head, task_head)["recoveryDigest"]
            real_recheck = bridge._require_ref_lock_snapshots
            called = []

            def tamper(items):
                real_recheck(items)
                called.append(True)
                if len(called) == 1:
                    # Synthetic failure injection: explicitly release the test
                    # lease and overwrite the same inode via the fixture only.
                    bridge._release_ref_lease(items[0]["fileFd"])
                    locks[0].write_text("0" * 40 + "\n", encoding="ascii")

            with mock.patch.object(
                bridge, "_require_ref_lock_snapshots", side_effect=tamper
            ):
                with self.assertRaises(bridge.BridgeError):
                    self.run_recovery(root, record, main_head, task_head, digest=digest)
            self.assertTrue(all(lock.exists() for lock in locks))

    def test_partial_removal_reports_fsync_failure_and_preserves_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root, record, locks, main_head, task_head = self.fixture(
                Path(directory) / "repo"
            )
            digest = self.run_recovery(root, record, main_head, task_head)["recoveryDigest"]
            with mock.patch.object(
                bridge.os, "fsync", side_effect=OSError(5, "injected fsync failure")
            ):
                with self.assertRaisesRegex(
                    bridge.BridgeError, "PARTIAL_REF_LOCK_RECOVERY"
                ) as failure:
                    self.run_recovery(root, record, main_head, task_head, digest=digest)
            self.assertIn("refs/heads/main.lock", str(failure.exception))
            self.assertIn(
                "refs/heads/task/15-maintenance.lock", str(failure.exception)
            )
            self.assertFalse(locks[0].exists())
            self.assertTrue(locks[1].exists())
            self.assertEqual(
                maintenance.lifecycle.state_status(record.path / ".task-state/task.md"),
                "initialized",
            )

    def test_partial_removal_reports_second_unlink_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root, record, locks, main_head, task_head = self.fixture(
                Path(directory) / "repo"
            )
            digest = self.run_recovery(root, record, main_head, task_head)["recoveryDigest"]
            original_unlink = bridge.os.unlink

            def fail_second(path, *, dir_fd=None):
                if path == "15-maintenance.lock":
                    raise OSError(5, "injected second unlink failure")
                return original_unlink(path, dir_fd=dir_fd)

            with mock.patch.object(bridge.os, "unlink", side_effect=fail_second):
                with self.assertRaisesRegex(
                    bridge.BridgeError, "PARTIAL_REF_LOCK_RECOVERY"
                ):
                    self.run_recovery(root, record, main_head, task_head, digest=digest)
            self.assertFalse(locks[0].exists())
            self.assertTrue(locks[1].exists())
            self.assertEqual(
                maintenance.lifecycle.state_status(record.path / ".task-state/task.md"),
                "initialized",
            )

    def test_wrong_content_mode_link_count_and_symlink_fail_closed(self):
        for variant in ("content", "mode", "hardlink", "symlink"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root, record, locks, main_head, task_head = self.fixture(Path(directory) / "repo")
                target = locks[0]
                original = target.read_bytes()
                if variant == "content":
                    target.write_bytes(b"0" * 40 + b"\n")
                elif variant == "mode":
                    target.chmod(0o644)
                elif variant == "hardlink":
                    os.link(target, target.with_suffix(".saved"))
                else:
                    target.unlink()
                    other = root / ".git" / "orphan"
                    other.write_bytes(original)
                    target.symlink_to(other)
                with self.assertRaises(bridge.BridgeError):
                    self.run_recovery(root, record, main_head, task_head)
                self.assertTrue(target.exists())
                self.assertTrue(locks[1].exists())

    def test_task_ref_drift_blocks_recovery_without_deleting_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            root, record, locks, main_head, task_head = self.fixture(
                Path(directory) / "repo"
            )
            # Git update-ref respects the intentionally present .lock file.
            # Simulate an external ref drift by editing only this disposable
            # synthetic fixture, not any real repository ref.
            task_ref = root / ".git/refs/heads/task/15-maintenance"
            task_ref.write_text(main_head + "\n", encoding="ascii")
            # The stricter clean-tree check can catch this drift before the
            # explicit Task HEAD check. Either fail-closed rejection is valid.
            with self.assertRaises((bridge.BridgeError, maintenance.MaintenanceError)):
                self.run_recovery(root, record, main_head, task_head)
            self.assertTrue(all(lock.exists() for lock in locks))
            self.assertEqual(
                maintenance.lifecycle.state_status(record.path / ".task-state/task.md"),
                "initialized",
            )

    def test_live_creator_and_wrong_immutable_identity_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root, record, locks, main_head, task_head = self.fixture(Path(directory) / "repo")
            with self.assertRaisesRegex(bridge.BridgeError, "still alive"):
                self.run_recovery(root, record, main_head, task_head, creator_pid=os.getpid())
            with self.assertRaises(bridge.BridgeError):
                self.run_recovery(root, record, "0" * 40, task_head)
            with self.assertRaises(bridge.BridgeError):
                self.run_recovery(root, record, main_head, "0" * 40)
            self.assertTrue(all(lock.exists() for lock in locks))


if __name__ == "__main__":
    unittest.main()
