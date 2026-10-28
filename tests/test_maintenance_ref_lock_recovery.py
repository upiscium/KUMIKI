from __future__ import annotations

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
                return_value={"commit_sha": task_head},
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
            # The test environment restricts inspection of several same-UID
            # /proc/*/fd entries. Verify the exact recovery CAS in isolation;
            # production must still fail closed on inaccessible processes.
            with mock.patch.object(bridge, "_no_other_lock_holders"):
                result = self.run_recovery(root, record, main_head, task_head)
            self.assertEqual(result["status"], "INSPECT_ONLY")
            self.assertEqual([lock.read_bytes() for lock in locks], before)
            with mock.patch.object(bridge, "_no_other_lock_holders"):
                with self.assertRaisesRegex(bridge.BridgeError, "inspection is stale"):
                    self.run_recovery(root, record, main_head, task_head, digest="0" * 64)
            self.assertEqual([lock.read_bytes() for lock in locks], before)
            with mock.patch.object(bridge, "_no_other_lock_holders"):
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
            with self.assertRaisesRegex(bridge.BridgeError, "Task HEAD no longer matches"):
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
