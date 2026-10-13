"""Non-invasive KAGARI external-bootstrap contract tests.

These tests use disposable Git roots, never users' repositories. They do not
claim that the v3 Agent Core runtime already works from .kagari/runtime.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "kagari_bootstrap.py"
PAYLOAD = ROOT / "components" / "agent-core"


def call_git(root: Path, *args: str) -> str:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    out = subprocess.run(
        ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
        check=True, capture_output=True, text=True, env=env,
    )
    return out.stdout.strip()


class KagariBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="kagari-bootstrap-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "product"
        self.root.mkdir()
        call_git(self.root, "init", "-q")
        call_git(self.root, "config", "user.name", "KAGARI Bootstrap Test")
        call_git(self.root, "config", "user.email", "test@example.invalid")
        self.baseline = {
            "README.md": "# product\n",
            "Justfile": 'set minimum-version := "1.55.0"\nexample:\n    @echo owned-by-project\n',
            "flake.nix": "{ definitely-invalid-flake syntax = *;\n",
            "opencode.json": '{"default_agent":"plan","user_setting":true}\n',
            "AGENTS.md": "Project-owned agent instructions.\n",
        }
        for name, content in self.baseline.items():
            (self.root / name).write_text(content)
        call_git(self.root, "add", "-A")
        call_git(self.root, "commit", "-qm", "initial product")
        call_git(self.root, "branch", "-M", "main")
        self.first_head = call_git(self.root, "rev-parse", "HEAD")

    def invoke(self, action: str, *, source: Path | None = PAYLOAD,
               target: Path | None = None) -> tuple[int, dict]:
        command = [sys.executable, str(CLI), action,
                   "--target", str(target or self.root)]
        if source is not None:
            command.extend(["--source", str(source)])
        result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
        text = result.stdout if result.returncode == 0 else result.stderr
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            self.fail(f"{command}: {result.returncode}: {result.stdout}\n{result.stderr}")
            raise exc
        return result.returncode, parsed

    def assert_project_preserved(self) -> None:
        for name, expected in self.baseline.items():
            self.assertEqual(expected, (self.root / name).read_text(),
                             msg=f"unexpected Project mutation: {name}")
        self.assertEqual(self.first_head, call_git(self.root, "rev-parse", "HEAD"))
        self.assertEqual("", call_git(self.root, "diff", "--cached", "--name-only"))
        self.assertEqual("", call_git(self.root, "diff", "--name-only"))
        # An optional .kagari-owned area is the only permitted product status.
        dirty_paths = call_git(self.root, "status", "--porcelain", "--untracked-files=all")
        for row in dirty_paths.splitlines():
            self.assertTrue(row[3:].startswith(".kagari/") or row[3:] == ".kagari", msg=row)

    def test_install_uninstall_reinstall_is_noninvasive(self) -> None:
        rc, initial = self.invoke("plan")
        self.assertEqual((0, "ABSENT"), (rc, initial["status"]))
        rc, installed = self.invoke("install")
        self.assertEqual((0, "INSTALLED"), (rc, installed["status"]))
        receipt = json.loads((self.root / ".kagari" / "install.json").read_text())
        self.assertEqual(("KAGARI", "3"), (receipt["component"], receipt["version"]))
        self.assertGreaterEqual(len(receipt["files"]), 40)
        self.assertTrue((self.root / ".kagari" / "runtime" / ".automation" / "VERSION").exists())
        self.assert_project_preserved()
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assertEqual("UNCHANGED", self.invoke("install")[1]["status"])
        self.assert_project_preserved()

        self.assertEqual("UNINSTALLED", self.invoke("uninstall")[1]["status"])
        self.assertFalse((self.root / ".kagari").exists())
        self.assertEqual("", call_git(self.root, "status", "--porcelain"))
        self.assert_project_preserved()
        self.assertEqual("ALREADY_ABSENT", self.invoke("uninstall")[1]["status"])
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        self.assert_project_preserved()

    def test_partial_missing_payload_repairs_without_overwrite(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        managed = self.root / ".kagari" / "runtime" / "Justfile"
        original = managed.read_bytes()
        managed.unlink()
        rc, state = self.invoke("doctor", source=None)
        self.assertEqual((0, "REPAIRABLE"), (rc, state["status"]))
        self.assertIn("Justfile", state["missing"])
        self.assertEqual("REPAIRED", self.invoke("repair")[1]["status"])
        self.assertEqual(original, managed.read_bytes())
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assert_project_preserved()

    def test_uninstall_after_missing_managed_payload_is_recoverable(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        container = self.root / ".kagari" / "runtime"
        missing_leaf = container / ".automation" / "VERSION"
        missing_leaf.unlink()
        self.assertEqual("REPAIRABLE", self.invoke("doctor", source=None)[1]["status"])
        self.assertEqual("UNINSTALLED", self.invoke("uninstall")[1]["status"])
        self.assertEqual("", call_git(self.root, "status", "--porcelain"))
        self.assert_project_preserved()
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])

    def test_empty_unknown_directory_blocks_uninstall_before_deletion(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        sentinel = self.root / ".kagari" / "runtime" / "Justfile"
        expected_bytes = sentinel.read_bytes()
        extra = self.root / ".kagari" / "runtime" / "my-private-dir"
        extra.mkdir()
        diagnosis = self.invoke("doctor", source=None)[1]
        self.assertEqual("CONFLICT", diagnosis["status"])
        self.assertIn("runtime/my-private-dir/", diagnosis["unknown"])
        code, state = self.invoke("uninstall")
        self.assertEqual((2, "BLOCKED"), (code, state["status"]))
        self.assertTrue(extra.is_dir())
        self.assertEqual(expected_bytes, sentinel.read_bytes())
        self.assert_project_preserved()

    def test_modified_managed_payload_is_ambiguous_and_preserved(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        managed = self.root / ".kagari" / "runtime" / "Justfile"
        edited = b"User intentionally customized KAGARI's copy\n"
        managed.write_bytes(edited)
        self.assertEqual("CONFLICT", self.invoke("doctor", source=None)[1]["status"])
        for command in ("install", "repair", "uninstall"):
            rc, result = self.invoke(command, source=PAYLOAD)
            self.assertEqual((2, "BLOCKED"), (rc, result["status"]))
            self.assertEqual(edited, managed.read_bytes())
            self.assert_project_preserved()

    def test_unknown_files_in_managed_scope_block_deletion(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        unknown = self.root / ".kagari" / "runtime" / "custom-code.txt"
        unknown.write_text("important user notes")
        state = self.invoke("plan")[1]
        self.assertEqual("CONFLICT", state["status"])
        self.assertIn("runtime/custom-code.txt", state["unknown"])
        self.assertEqual((2, "BLOCKED"), (
            lambda x: (x[0], x[1]["status"])
        )(self.invoke("uninstall")))
        self.assertTrue(unknown.exists())
        self.assert_project_preserved()

    def test_corrupt_or_missing_receipt_never_adopts_unknown_directory(self) -> None:
        owned = self.root / ".kagari"
        owned.mkdir()
        (owned / "custom.txt").write_text("not KAGARI owned")
        rc, result = self.invoke("install")
        self.assertEqual((2, "BLOCKED"), (rc, result["status"]))
        self.assertTrue((owned / "custom.txt").exists())
        self.assert_project_preserved()

    def test_symlink_managed_directory_refused(self) -> None:
        outside = Path(self.temp.name) / "elsewhere"
        outside.mkdir()
        (outside / "untouched.txt").write_text("safe\n")
        (self.root / ".kagari").symlink_to(outside, target_is_directory=True)
        self.assertEqual((2, "BLOCKED"), (
            lambda x: (x[0], x[1]["status"])
        )(self.invoke("install")))
        self.assertEqual("safe\n", (outside / "untouched.txt").read_text())
        self.assertFalse((outside / "runtime").exists())
        self.assert_project_preserved()

    def test_dirty_product_index_and_untracked_work_are_preserved(self) -> None:
        dirty = self.root / "README.md"
        dirty.write_text("# changed product work\\n")
        call_git(self.root, "add", "README.md")
        untracked = self.root / "unsaved-product.txt"
        untracked.write_text("user content\\n")
        before_index = call_git(self.root, "diff", "--cached", "--binary")
        before_head = call_git(self.root, "rev-parse", "HEAD")

        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        self.assertEqual("UNCHANGED", self.invoke("install")[1]["status"])
        self.assertEqual("UNINSTALLED", self.invoke("uninstall")[1]["status"])
        self.assertEqual(before_head, call_git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(before_index, call_git(self.root, "diff", "--cached", "--binary"))
        self.assertEqual("# changed product work\\n", dirty.read_text())
        self.assertEqual("user content\\n", untracked.read_text())

    def test_install_runs_without_project_just_or_nix_executable(self) -> None:
        git_binary = shutil.which("git")
        self.assertIsNotNone(git_binary)
        # Bootstrap depends only on the external Python interpreter and Git,
        # not on just/nix from the possibly broken Project devShell.
        env = os.environ.copy()
        env["PATH"] = str(Path(git_binary).resolve().parent)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        result = subprocess.run([
            sys.executable, str(CLI), "install",
            "--target", str(self.root), "--source", str(PAYLOAD),
        ], capture_output=True, text=True, env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("INSTALLED", json.loads(result.stdout)["status"])
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assert_project_preserved()

    def test_nonexecutable_mode_drift_blocks_uninstall(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        file = self.root / ".kagari" / "runtime" / "AGENTS.md"
        old_bytes = file.read_bytes()
        file.chmod(0o600)
        diagnosis = self.invoke("doctor", source=None)[1]
        self.assertEqual("CONFLICT", diagnosis["status"])
        self.assertIn("AGENTS.md", diagnosis["changed"])
        rc, result = self.invoke("uninstall")
        self.assertEqual((2, "BLOCKED"), (rc, result["status"]))
        self.assertEqual(0o600, file.stat().st_mode & 0o777)
        self.assertEqual(old_bytes, file.read_bytes())
        self.assert_project_preserved()

    def test_git_backed_source_ls_files_failure_does_not_scan_untracked_files(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_git_scan_test", CLI)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        actual_run = bootstrap.subprocess.run

        def failed_inventory(cmd, *args, **kwargs):
            if "ls-files" in cmd:
                return subprocess.CompletedProcess(cmd, 128, stdout=b"",
                                                   stderr=b"simulated index error")
            return actual_run(cmd, *args, **kwargs)

        with mock.patch.object(bootstrap.subprocess, "run", side_effect=failed_inventory):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "inventory is unreadable"):
                bootstrap.source_payload(PAYLOAD)
        self.assert_project_preserved()

    def test_extracted_nongit_source_uses_archive_inventory(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("kagari_archive_source", CLI)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        archive = Path(self.temp.name) / "archive"
        shutil.copytree(
            PAYLOAD, archive,
            ignore=shutil.ignore_patterns("node_modules", "__pycache__",
                                          ".git", ".pytest_cache", "*.pyc")
        )
        receipt, files = bootstrap.source_payload(archive)
        self.assertEqual("3", receipt["version"])
        self.assertIn(".automation/VERSION", files)
        self.assertNotIn(".opencode/package.json", files)
        self.assert_project_preserved()

    def test_file_mode_drift_does_not_overwrite_user_edits(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        file = self.root / ".kagari" / "runtime" / "AGENTS.md"
        file.chmod(0o755)
        self.assertEqual("CONFLICT", self.invoke("doctor", source=None)[1]["status"])
        code, result = self.invoke("repair")
        self.assertEqual((2, "BLOCKED"), (code, result["status"]))
        self.assertEqual(0o755, file.stat().st_mode & 0o777)
        self.assert_project_preserved()

    def test_unreadable_managed_directory_blocks_uninstall_before_unlink(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        critical = self.root / ".kagari" / "runtime" / "Justfile"
        original = critical.read_bytes()
        hidden = self.root / ".kagari" / "runtime" / ".automation"
        extra = hidden / "unrecorded-notes.txt"
        extra.write_text("important data")
        hidden.chmod(0o300)
        try:
            rc, response = self.invoke("uninstall")
            self.assertEqual((2, "BLOCKED"), (rc, response["status"]))
            self.assertEqual(original, critical.read_bytes())
        finally:
            hidden.chmod(0o755)
        self.assertEqual("important data", extra.read_text())
        self.assert_project_preserved()

    def test_foreign_canonical_receipt_requires_external_source_match(self) -> None:
        container = self.root / ".kagari"
        (container / "runtime").mkdir(parents=True)
        personal = container / "runtime" / "my-data.txt"
        personal.write_bytes(b"Project-owned content")
        import hashlib
        record = {"schema": 1, "component": "KAGARI", "version": "3",
                  "files": [{"path": "my-data.txt", "mode": 0o644, "size": 21,
                             "sha256": hashlib.sha256(personal.read_bytes()).hexdigest()}]}
        (container / "install.json").write_text(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        )
        rc, result = self.invoke("uninstall")
        self.assertEqual((2, "BLOCKED"), (rc, result["status"]))
        self.assertEqual(b"Project-owned content", personal.read_bytes())
        self.assert_project_preserved()

    def test_uninstall_does_not_run_without_external_matching_source(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        rc, result = self.invoke("uninstall", source=None)
        self.assertEqual((2, "BLOCKED"), (rc, result["status"]))
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assert_project_preserved()

    def test_repair_failure_does_not_publish_partial_installed_file(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        managed = self.root / ".kagari" / "runtime" / "Justfile"
        managed.unlink()
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_bootstrap_under_test", CLI)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        with mock.patch.object(bootstrap.os, "link", side_effect=OSError("simulated IO failure")):
            with self.assertRaisesRegex(OSError, "simulated IO failure"):
                bootstrap.operate(self.root, PAYLOAD, "repair")
        self.assertFalse(managed.exists())
        self.assertEqual("REPAIRABLE", self.invoke("doctor", source=None)[1]["status"])
        self.assertEqual("REPAIRED", self.invoke("repair")[1]["status"])
        self.assert_project_preserved()

    def test_non_git_target_is_rejected_without_changes(self) -> None:
        other = Path(self.temp.name) / "not-git"
        other.mkdir()
        rc, value = self.invoke("install", target=other)
        self.assertEqual((2, "BLOCKED"), (rc, value["status"]))
        self.assertEqual([], list(other.iterdir()))
        self.assert_project_preserved()

    def test_source_version_conflict_refuses_automatic_upgrade(self) -> None:
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        custom = Path(self.temp.name) / "different-source"
        shutil.copytree(PAYLOAD, custom)
        (custom / ".automation" / "VERSION").write_text("4\n")
        rc, result = self.invoke("install", source=custom)
        self.assertEqual((2, "BLOCKED"), (rc, result["status"]))
        self.assertEqual("3\n",
                         (self.root / ".kagari" / "runtime" / ".automation" / "VERSION").read_text())
        self.assert_project_preserved()

    def test_source_symlink_rejected_before_installation(self) -> None:
        custom = Path(self.temp.name) / "symlinked-source"
        shutil.copytree(PAYLOAD, custom)
        (custom / ".automation" / "VERSION").unlink()
        (custom / ".automation" / "VERSION").symlink_to(PAYLOAD / ".automation" / "VERSION")
        rc, result = self.invoke("install", source=custom)
        self.assertEqual((2, "BLOCKED"), (rc, result["status"]))
        self.assertFalse((self.root / ".kagari").exists())
        self.assert_project_preserved()


    def test_validated_project_root_replacement_blocks_before_write(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_anchor_swap", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        real_source = bootstrap.source_payload
        relocated = Path(self.temp.name) / "moved-original"
        foreign = self.root
        replacement_written = False

        def changed_after_git_validation(path):
            nonlocal replacement_written
            result = real_source(path)
            self.root.rename(relocated)
            foreign.mkdir()
            (foreign / "not-original.txt").write_text("independent Project data")
            replacement_written = True
            return result

        try:
            with mock.patch.object(bootstrap, "source_payload",
                                   side_effect=changed_after_git_validation):
                with self.assertRaisesRegex(bootstrap.BootstrapError,
                                            "changed before anchoring"):
                    bootstrap.operate(self.root, PAYLOAD, "install")
            self.assertTrue(replacement_written)
            self.assertFalse((foreign / ".kagari").exists())
            self.assertEqual("independent Project data",
                             (foreign / "not-original.txt").read_text())
            self.assertFalse((relocated / ".kagari").exists())
        finally:
            if replacement_written:
                shutil.rmtree(foreign)
                relocated.rename(self.root)
        self.assert_project_preserved()

    def test_root_rename_after_fd_hold_cannot_redirect_install(self) -> None:
        import importlib.util
        from contextlib import contextmanager
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_anchor_held", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        original_lock = bootstrap.writer_lock
        relocated = Path(self.temp.name) / "moved-original"
        renamed = False

        @contextmanager
        def relocate_while_locked(git_dir):
            nonlocal renamed
            self.root.rename(relocated)
            self.root.mkdir()
            (self.root / "not-original.txt").write_text("must not be modified")
            renamed = True
            with original_lock(git_dir):
                yield

        try:
            with mock.patch.object(bootstrap, "writer_lock",
                                   side_effect=relocate_while_locked):
                with self.assertRaisesRegex(bootstrap.BootstrapError,
                                            "pathname changed during operation"):
                    bootstrap.operate(self.root, PAYLOAD, "install")
            self.assertFalse((self.root / ".kagari").exists())
            self.assertEqual("must not be modified",
                             (self.root / "not-original.txt").read_text())
            self.assertTrue((relocated / ".kagari" / "install.json").exists())
        finally:
            if renamed:
                shutil.rmtree(self.root)
                relocated.rename(self.root)
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assert_project_preserved()

    def test_late_unknown_file_is_preserved_before_uninstall_deletion(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_unknown_race", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        protected = self.root / ".kagari" / "runtime" / "Justfile"
        content = protected.read_bytes()
        original_inventory = bootstrap.inventory_state
        injected = False

        def inject_after_snapshot(root, expected):
            nonlocal injected
            result = original_inventory(root, expected)
            if not injected and result["state"] == "HEALTHY":
                (root / ".kagari" / "runtime" / "concurrent-unknown.txt").write_text(
                    "unrelated user data"
                )
                injected = True
            return result

        with mock.patch.object(bootstrap, "inventory_state",
                               side_effect=inject_after_snapshot):
            with self.assertRaisesRegex(bootstrap.BootstrapError,
                                        "changed while detaching"):
                bootstrap.operate(self.root, PAYLOAD, "uninstall")
        self.assertTrue(injected)
        self.assertEqual(content, protected.read_bytes())
        self.assertEqual("unrelated user data",
                         (self.root / ".kagari" / "runtime" /
                          "concurrent-unknown.txt").read_text())
        self.assertFalse(list((self.root / ".git").glob("kagari-uninstall-*")))
        self.assert_project_preserved()

    def test_late_managed_file_replacement_restores_before_delete(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_replace_race", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        protected = self.root / ".kagari" / "runtime" / "Justfile"
        modified = b"concurrent user edit (do not delete)\n"
        old_inventory = bootstrap.inventory_state
        changed = False

        def change_after_snapshot(root, expected):
            nonlocal changed
            result = old_inventory(root, expected)
            if not changed and result["state"] == "HEALTHY":
                (root / ".kagari" / "runtime" / "Justfile").write_bytes(modified)
                changed = True
            return result

        with mock.patch.object(bootstrap, "inventory_state",
                               side_effect=change_after_snapshot):
            with self.assertRaisesRegex(bootstrap.BootstrapError,
                                        "changed while detaching"):
                bootstrap.operate(self.root, PAYLOAD, "uninstall")
        self.assertTrue(changed)
        self.assertEqual(modified, protected.read_bytes())
        self.assertTrue((self.root / ".kagari" / "install.json").exists())
        self.assert_project_preserved()

    def test_quarantine_restore_never_overwrites_new_target(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("kagari_restore_test", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        private = Path(self.temp.name) / "private-quarantine"
        private.mkdir()
        (private / ".kagari").mkdir()
        target = Path(self.temp.name) / "other-Project"
        target.mkdir()
        (target / ".kagari").mkdir()
        first = os.open(private, os.O_RDONLY | os.O_DIRECTORY)
        second = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(FileExistsError):
                bootstrap.atomic_restore_dir(first, second)
            self.assertTrue((target / ".kagari").is_dir())
            self.assertTrue((private / ".kagari").is_dir())
            (target / ".kagari").rmdir()
            bootstrap.atomic_restore_dir(first, second)
            self.assertTrue((target / ".kagari").is_dir())
            self.assertFalse((private / ".kagari").exists())
        finally:
            os.close(first)
            os.close(second)
        self.assert_project_preserved()


    def test_interrupted_uninstall_keeps_quarantine_and_blocks_retry(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_partial_uninstall", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])

        def simulated_partial_delete(private_root, _receipt, progress):
            (private_root / ".kagari" / "runtime" / "Justfile").unlink()
            progress["deleted"] = True
            raise OSError("simulated failure after a first unlink")

        with mock.patch.object(bootstrap, "_remove_quarantined",
                               side_effect=simulated_partial_delete):
            with self.assertRaisesRegex(bootstrap.BootstrapError,
                                        "uninstall interrupted after deletion began"):
                bootstrap.operate(self.root, PAYLOAD, "uninstall")
        self.assertFalse((self.root / ".kagari").exists())
        private = list((self.root / ".git").glob("kagari-uninstall-*/.kagari/install.json"))
        self.assertEqual(1, len(private))
        self.assertEqual((2, "BLOCKED"), (
            lambda x: (x[0], x[1]["status"])
        )(self.invoke("doctor", source=None)))
        self.assertEqual((2, "BLOCKED"), (
            lambda x: (x[0], x[1]["status"])
        )(self.invoke("install")))
        self.assert_project_preserved()


    def test_quarantine_created_while_waiting_for_writer_lock_blocks_install(self) -> None:
        import importlib.util
        from contextlib import contextmanager
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_quarantine_lock", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        original_lock = bootstrap.writer_lock

        @contextmanager
        def interrupted_writer(git_dir):
            with original_lock(git_dir):
                orphan = git_dir / "kagari-uninstall-late-reader"
                orphan.mkdir()
                try:
                    yield
                finally:
                    orphan.rmdir()

        with mock.patch.object(bootstrap, "writer_lock", side_effect=interrupted_writer):
            with self.assertRaisesRegex(bootstrap.BootstrapError,
                                        "unresolved KAGARI uninstall quarantine"):
                bootstrap.operate(self.root, PAYLOAD, "install")
        self.assertFalse((self.root / ".kagari").exists())
        self.assert_project_preserved()

    def test_failure_before_first_unlink_restores_untouched_quarantine(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_pre_unlink_failure", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        original = (self.root / ".kagari" / "runtime" / "Justfile").read_bytes()

        with mock.patch.object(bootstrap, "_remove_quarantined",
                               side_effect=OSError("denied before first unlink")):
            with self.assertRaisesRegex(OSError, "denied before first unlink"):
                bootstrap.operate(self.root, PAYLOAD, "uninstall")
        self.assertEqual(original, (self.root / ".kagari" / "runtime" / "Justfile").read_bytes())
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assertFalse(list((self.root / ".git").glob("kagari-uninstall-*")))
        self.assert_project_preserved()

    def test_install_no_replace_preserves_concurrent_empty_directory(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_install_collision", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        original = bootstrap.atomic_restore_dir
        concurrent = self.root / ".kagari"

        def inject_collision(src_fd, dst_fd, **kwargs):
            if kwargs.get("source_name", "").startswith("kagari-stage-"):
                concurrent.mkdir()
            return original(src_fd, dst_fd, **kwargs)

        with mock.patch.object(bootstrap, "atomic_restore_dir",
                               side_effect=inject_collision):
            with self.assertRaises(FileExistsError):
                bootstrap.operate(self.root, PAYLOAD, "install")
        self.assertTrue(concurrent.is_dir())
        self.assertEqual([], list(concurrent.iterdir()))
        self.assertFalse(list((self.root / ".git").glob("kagari-stage-*")))
        self.assert_project_preserved()

    def test_no_replace_support_failure_is_before_project_mutation(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_noreplace_support", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        with mock.patch.object(bootstrap, "atomic_restore_dir",
                               side_effect=OSError(95, "operation not supported")):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "no-clobber rename"):
                bootstrap.operate(self.root, PAYLOAD, "install")
        self.assertFalse((self.root / ".kagari").exists())
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        with mock.patch.object(bootstrap, "atomic_restore_dir",
                               side_effect=OSError(95, "operation not supported")):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "no-clobber rename"):
                bootstrap.operate(self.root, PAYLOAD, "uninstall")
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assert_project_preserved()


    def test_root_replacement_between_first_fd_and_path_check_is_blocked(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_first_fd_identity", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        original_is_dir = bootstrap.Path.is_dir
        relocated = Path(self.temp.name) / "original-moved-before-validation"
        changed = False

        def replace_root_during_check(path):
            nonlocal changed
            if path == self.root and not changed:
                self.root.rename(relocated)
                self.root.mkdir()
                (self.root / "foreign.txt").write_text("independent content")
                changed = True
            return original_is_dir(path)

        try:
            with mock.patch.object(bootstrap.Path, "is_dir", autospec=True,
                                   side_effect=replace_root_during_check):
                with self.assertRaisesRegex(bootstrap.BootstrapError,
                                            "validated target changed before Git root"):
                    bootstrap.git_project(self.root)
            self.assertTrue(changed)
            self.assertFalse((self.root / ".kagari").exists())
            self.assertEqual("independent content", (self.root / "foreign.txt").read_text())
        finally:
            if changed:
                shutil.rmtree(self.root)
                relocated.rename(self.root)
        self.assert_project_preserved()


    def test_inherited_git_overrides_do_not_redirect_git_admin(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_scrub_git_env", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        foreign = Path(self.temp.name) / "unrelated-git"
        foreign.mkdir()
        call_git(foreign, "init", "-q")
        overrides = {
            "GIT_DIR": str(foreign / ".git"),
            "GIT_WORK_TREE": str(self.root),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_VALUE_0": str(foreign),
        }
        with mock.patch.dict(os.environ, overrides):
            observed_root, observed_git, _, _ = bootstrap.git_project(self.root)
            self.assertEqual(self.root, observed_root)
            self.assertEqual((self.root / ".git").resolve(), observed_git)
            outcome = bootstrap.operate(self.root, PAYLOAD, "install")
        self.assertEqual("INSTALLED", outcome["status"])
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assertFalse(list((foreign / ".git").glob("kagari-*")))
        self.assert_project_preserved()

    def test_uninstall_fd_open_failure_reclaims_empty_quarantine(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_quarantine_fd_failure", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        receipt = json.loads((self.root / ".kagari" / "install.json").read_text())
        normal_open = bootstrap.os.open
        for target_name in ("root", "quarantine"):
            with self.subTest(target_name=target_name):
                def refuse_one_directory(path, flags, *args, **kwargs):
                    name = Path(path).name
                    if ((target_name == "root" and Path(path) == self.root) or
                            (target_name == "quarantine" and
                             name.startswith("kagari-uninstall-"))):
                        raise OSError("injected fd opening failure")
                    return normal_open(path, flags, *args, **kwargs)

                with mock.patch.object(bootstrap.os, "open",
                                       side_effect=refuse_one_directory):
                    with self.assertRaisesRegex(OSError, "fd opening failure"):
                        bootstrap.remove_owned(self.root, self.root / ".git", receipt)
                self.assertFalse(list((self.root / ".git").glob("kagari-uninstall-*")))
                self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assert_project_preserved()


    def test_doctor_rechecks_orphan_inside_writer_lock(self) -> None:
        import importlib.util
        from contextlib import contextmanager
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_doctor_lock", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        original_lock = bootstrap.writer_lock

        @contextmanager
        def concurrent_uninstall(git_dir):
            with original_lock(git_dir):
                orphan = git_dir / "kagari-uninstall-doctor-race"
                orphan.mkdir()
                try:
                    yield
                finally:
                    orphan.rmdir()

        with mock.patch.object(bootstrap, "writer_lock", side_effect=concurrent_uninstall):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "unresolved KAGARI uninstall"):
                bootstrap.operate(self.root, None, "doctor")
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assert_project_preserved()

    def test_ambiguous_signal_after_unlink_retains_quarantine(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_interrupt_gap", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])

        def signal_after_unlink(private_root, _receipt, progress):
            progress["attempted"] = True
            (private_root / ".kagari" / "runtime" / "Justfile").unlink()
            raise KeyboardInterrupt("interrupt between unlink and marker")

        with mock.patch.object(bootstrap, "_remove_quarantined", side_effect=signal_after_unlink):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "ambiguous uninstall interruption"):
                bootstrap.operate(self.root, PAYLOAD, "uninstall")
        self.assertFalse((self.root / ".kagari").exists())
        self.assertEqual(1, len(list((self.root / ".git").glob("kagari-uninstall-*/.kagari/install.json"))))
        self.assertEqual((2, "BLOCKED"), (
            lambda x: (x[0], x[1]["status"])
        )(self.invoke("doctor", source=None)))
        self.assert_project_preserved()

    def test_attempted_but_no_deletion_restores_intact_receipt(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_failed_unlink", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])

        def refused_before_unlink(_private_root, _receipt, progress):
            progress["attempted"] = True
            raise OSError("unlink denied before mutation")

        with mock.patch.object(bootstrap, "_remove_quarantined", side_effect=refused_before_unlink):
            with self.assertRaisesRegex(OSError, "unlink denied before mutation"):
                bootstrap.operate(self.root, PAYLOAD, "uninstall")
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assertFalse(list((self.root / ".git").glob("kagari-uninstall-*")))
        self.assert_project_preserved()

    def test_doctor_refuses_replaced_path_after_fd_observation(self) -> None:
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("kagari_doctor_rename", CLI)
        assert spec is not None and spec.loader is not None
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        self.assertEqual("INSTALLED", self.invoke("install")[1]["status"])
        actual_state = bootstrap.inventory_state
        relocated = Path(self.temp.name) / "original-during-doctor"
        changed = False

        def swap_after_snapshot(root, expected):
            nonlocal changed
            state = actual_state(root, expected)
            if not changed:
                self.root.rename(relocated)
                self.root.mkdir()
                changed = True
            return state

        try:
            with mock.patch.object(bootstrap, "inventory_state", side_effect=swap_after_snapshot):
                with self.assertRaisesRegex(bootstrap.BootstrapError,
                                            "pathname changed during inspection"):
                    bootstrap.operate(self.root, None, "doctor")
        finally:
            if changed:
                self.root.rmdir()
                relocated.rename(self.root)
        self.assertEqual("HEALTHY", self.invoke("doctor", source=None)[1]["status"])
        self.assert_project_preserved()


if __name__ == "__main__":
    unittest.main()
