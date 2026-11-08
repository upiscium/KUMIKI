"""#340 R6B D1B1 R2: disposable local-only, read-only Host journal observer.

Only tests provision/change synthetic private fixtures in TemporaryDirectory.
The R2 observer itself has no bootstrap, mutation, CLI, Git or Task effects.
"""
from __future__ import annotations

import ast
import io
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import _kagari_vnext_claude_r6b_phase_d1b1_r1 as r1
from tools import _kagari_vnext_claude_r6b_phase_d1b1_r2 as r2
from tools.kagari_vnext_trusted_host import RecoveryJournal


class ExistingRootObserverTests(unittest.TestCase):
    REPO = 1183254623
    ISSUE = 340

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="kumiki-340-r2-disposable-")
        self.addCleanup(t.cleanup)
        self.home = Path(t.name).resolve(strict=True)
        self.root = self.home / ".local" / "state" / "kagari" / "claude-one-shot"
        self.root = self.root / r1.root_component(self.REPO, self.ISSUE)
        owner = r2.os.geteuid()
        self.owner = owner

    def _init_private_root(self):
        parent = self.home
        for part in (".local", "state", "kagari", "claude-one-shot",
                     r1.root_component(self.REPO, self.ISSUE)):
            parent = parent / part
            parent.mkdir(mode=0o700)
        self.assertEqual(parent, self.root)
        return parent

    def _record(self, data=None):
        journal = RecoveryJournal(self.root)
        journal.create(r1.reservation_key(self.REPO, self.ISSUE),
                       data if data is not None else
                       {"kind": "synthetic-only", "schema": 1, "state": "reserved"})
        return journal

    def _observe(self, repository_id=None, issue=None):
        with patch.object(r2.pwd, "getpwuid",
                          return_value=SimpleNamespace(pw_dir=str(self.home))):
            return r2.observe_existing_host_journal(
                self.REPO if repository_id is None else repository_id,
                self.ISSUE if issue is None else issue)

    def _expect(self, result, status, reason):
        self.assertEqual(result, {
            "status": status, "reason": reason,
            "qualification": "UNQUALIFIED", "stage": "READ_ONLY",
            "evidence": r2.EVIDENCE,
            "dispatch_allowed": False, "task_completed": False,
            "replay_allowed": False, "commit_allowed": False,
            "authenticated_absence": False, "effective_policy": "UNVERIFIED",
        })
        for flag in ("dispatch_allowed", "task_completed", "replay_allowed",
                     "commit_allowed", "authenticated_absence"):
            self.assertIs(result[flag], False)

    def test_missing_namespace_is_unknown_and_creates_nothing(self):
        self.assertEqual(list(self.home.iterdir()), [])
        self._expect(self._observe(), "UNKNOWN", "host_journal_missing")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_existing_root_without_reservation_is_not_genesis(self):
        self._init_private_root()
        self._expect(self._observe(), "UNKNOWN", "history_absence_unproven")

    def test_existing_reservation_only_blocks_not_authorizes(self):
        self._init_private_root()
        self._record()
        self._expect(self._observe(), "OBSERVED_BLOCKED",
                     "existing_reservation_observed_only")
        self._expect(self._observe(), "OBSERVED_BLOCKED",
                     "existing_reservation_observed_only")

    def test_forged_canonical_record_can_only_block(self):
        self._init_private_root()
        self._record({"task_completed": True, "dispatch_allowed": True,
                      "result": "forged", "schema": 1})
        self._expect(self._observe(), "OBSERVED_BLOCKED",
                     "existing_reservation_observed_only")

    def test_deleted_reservation_is_unknown_not_safe_to_retry(self):
        self._init_private_root()
        self._record()
        (self.root / r1.reservation_key(self.REPO, self.ISSUE)).unlink()
        self._expect(self._observe(), "UNKNOWN", "history_absence_unproven")

    def test_recreated_root_is_unverifiable_empty_history(self):
        self._init_private_root()
        self._record()
        shutil.rmtree(self.root)
        self.root.mkdir(mode=0o700)
        self._expect(self._observe(), "UNKNOWN", "history_absence_unproven")

    def test_wrong_issue_or_repository_has_independent_unknown_key(self):
        self._init_private_root()
        self._record()
        for repo, issue in ((self.REPO, self.ISSUE + 1),
                            (self.REPO + 1, self.ISSUE)):
            with self.subTest(repo=repo, issue=issue):
                self._expect(self._observe(repo, issue),
                             "UNKNOWN", "host_journal_missing")

    def test_other_record_does_not_imply_reservation(self):
        self._init_private_root()
        RecoveryJournal(self.root).create("a" * 64,
                                          {"other": "synthetic"})
        self._expect(self._observe(), "UNKNOWN", "history_absence_unproven")

    def test_corrupt_record_fails_closed(self):
        self._init_private_root()
        key = r1.reservation_key(self.REPO, self.ISSUE)
        p = self.root / key
        p.write_bytes(b'{"not-a-record"')
        p.chmod(0o600)
        self._expect(self._observe(), "UNKNOWN",
                     "journal_read_or_history_uncertain")

    def test_symlink_record_is_not_followed(self):
        self._init_private_root()
        target = self.home / "other-private"
        # A valid canonical 0600 record would be observed if a future
        # implementation incorrectly followed this symlink.
        canonical = b'{"kind":"valid-synthetic-target"}'
        target.write_bytes(canonical)
        target.chmod(0o600)
        key = r1.reservation_key(self.REPO, self.ISSUE)
        (self.root / key).symlink_to(target)
        self._expect(self._observe(), "UNKNOWN",
                     "journal_read_or_history_uncertain")
        self.assertEqual(target.read_bytes(), canonical)

    def test_root_replacement_during_journal_read_is_refused(self):
        self._init_private_root()
        self._record()
        old_path = self.root.with_name(self.root.name + "-retired")
        original_read = RecoveryJournal.read

        def replace_after_read(journal, key):
            result = original_read(journal, key)
            self.root.rename(old_path)
            self.root.mkdir(mode=0o700)
            return result

        with patch.object(RecoveryJournal, "read", replace_after_read):
            self._expect(self._observe(), "REFUSED", "root_replaced")

    def test_symlink_root_refused(self):
        self._init_private_root()
        other = self.home / "other-root"
        other.mkdir(mode=0o700)
        self.root.rmdir()
        self.root.symlink_to(other, target_is_directory=True)
        self._expect(self._observe(), "REFUSED", "host_journal_unsafe")

    def test_symlink_intermediate_component_refused(self):
        external = self.home / "another"
        external.mkdir(mode=0o700)
        (self.home / ".local").symlink_to(external, target_is_directory=True)
        self._expect(self._observe(), "REFUSED", "host_journal_unsafe")

    def test_unsafe_private_directory_mode_refused(self):
        self._init_private_root()
        self.root.chmod(0o755)
        self._expect(self._observe(), "REFUSED", "host_journal_unsafe")

    def test_unsafe_ancestor_directory_mode_refused(self):
        self._init_private_root()
        (self.home / ".local" / "state" / "kagari").chmod(0o770)
        self._expect(self._observe(), "REFUSED", "host_journal_unsafe")

    def test_nonwritable_shared_host_ancestor_is_permitted(self):
        self._init_private_root()
        (self.home / ".local").chmod(0o755)
        (self.home / ".local" / "state").chmod(0o755)
        self._record()
        self._expect(self._observe(), "OBSERVED_BLOCKED",
                     "existing_reservation_observed_only")

    def test_invalid_numeric_identity_rejected_before_owner_lookup(self):
        for repo, issue in ((True, 340), (0, 340), (1.5, 340),
                            (self.REPO, False), (self.REPO, 0),
                            (10**12 + 1, 340), (self.REPO, 2**31)):
            with self.subTest(repo=repo, issue=issue):
                with patch.object(r2.pwd, "getpwuid",
                                  side_effect=AssertionError("should not lookup owner")):
                    self._expect(r2.observe_existing_host_journal(repo, issue),
                                 "REFUSED", "repository_issue_invalid")

    def test_invalid_passwd_home_is_refused(self):
        with patch.object(r2.pwd, "getpwuid",
                          return_value=SimpleNamespace(pw_dir="relative/home")):
            self._expect(r2.observe_existing_host_journal(self.REPO, self.ISSUE),
                         "REFUSED", "owner_home_invalid")

    def test_dangling_passwd_home_symlink_is_refused(self):
        home = self.home / "passwd-home"
        target = self.home / "missing-target"
        home.symlink_to(target, target_is_directory=True)
        with patch.object(r2.pwd, "getpwuid",
                          return_value=SimpleNamespace(pw_dir=str(home))):
            self._expect(r2.observe_existing_host_journal(self.REPO, self.ISSUE),
                         "REFUSED", "owner_home_unsafe")
        self.assertTrue(home.is_symlink())
        self.assertFalse(target.exists())
        self.assertEqual(list(self.home.iterdir()), [home])

    def test_missing_passwd_home_is_unknown_and_creates_nothing(self):
        home = self.home / "missing-home"
        self.assertFalse(home.exists())
        self.assertFalse(home.is_symlink())
        with patch.object(r2.pwd, "getpwuid",
                          return_value=SimpleNamespace(pw_dir=str(home))):
            self._expect(r2.observe_existing_host_journal(self.REPO, self.ISSUE),
                         "UNKNOWN", "owner_home_missing")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_valid_passwd_home_symlink_is_refused(self):
        home = self.home / "passwd-home"
        target = self.home / "existing-target"
        target.mkdir(mode=0o700)
        home.symlink_to(target, target_is_directory=True)
        with patch.object(r2.pwd, "getpwuid",
                          return_value=SimpleNamespace(pw_dir=str(home))):
            self._expect(r2.observe_existing_host_journal(self.REPO, self.ISSUE),
                         "REFUSED", "owner_home_unsafe")
        self.assertTrue(home.is_symlink())
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(set(self.home.iterdir()), {home, target})

    def test_dangling_passwd_home_ancestor_symlink_is_refused(self):
        link = self.home / "dangling-ancestor"
        missing_target = self.home / "nonexistent-target"
        link.symlink_to(missing_target, target_is_directory=True)
        home = link / "child-home"
        with patch.object(r2.pwd, "getpwuid",
                          return_value=SimpleNamespace(pw_dir=str(home))):
            self._expect(r2.observe_existing_host_journal(self.REPO, self.ISSUE),
                         "REFUSED", "owner_home_unsafe")
        self.assertTrue(link.is_symlink())
        self.assertFalse(missing_target.exists())
        self.assertFalse(home.exists())
        self.assertEqual(list(self.home.iterdir()), [link])

    def test_missing_passwd_home_parent_is_unknown(self):
        home = self.home / "missing-plain-parent" / "child-home"
        with patch.object(r2.pwd, "getpwuid",
                          return_value=SimpleNamespace(pw_dir=str(home))):
            self._expect(r2.observe_existing_host_journal(self.REPO, self.ISSUE),
                         "UNKNOWN", "owner_home_missing")
        self.assertEqual(list(self.home.iterdir()), [])

    def _snapshot_fixture(self):
        """Capture every descendant file's bytes and directory mode."""
        snapshot = {
            ".": ("home-directory", self.home.lstat().st_mode & 0o777),
        }
        for entry in self.home.rglob("*"):
            key = entry.relative_to(self.home).as_posix()
            mode = entry.lstat().st_mode & 0o777
            if entry.is_symlink():
                snapshot[key] = ("symlink", str(entry.readlink()), mode)
            elif entry.is_file():
                snapshot[key] = ("file", entry.read_bytes(), mode)
            elif entry.is_dir():
                snapshot[key] = ("directory", mode)
            else:
                snapshot[key] = ("other", mode)
        return snapshot

    def _observe_under_no_write_guard(self):
        """Deny write-capable opens; detect residual fixture mutations.

        All patches begin after synthetic fixture provisioning and end before
        TemporaryDirectory cleanup. This is synthetic read-only coverage,
        NOT a security sandbox for arbitrary Python/OS side effects.
        """
        before = self._snapshot_fixture()
        attempts = []
        real_os_open = r2.os.open
        real_io_open = io.open
        write_flags = (r2.os.O_WRONLY | r2.os.O_RDWR | r2.os.O_CREAT
                       | r2.os.O_TRUNC | r2.os.O_APPEND | r2.os.O_EXCL)
        tmpfile_flags = getattr(r2.os, "O_TMPFILE", 0)

        def guard_os_open(path, flags, mode=0o777, *, dir_fd=None):
            # Linux O_TMPFILE includes O_DIRECTORY; require the FULL mask.
            if (flags & write_flags
                    or (tmpfile_flags and flags & tmpfile_flags == tmpfile_flags)):
                attempts.append("write-capable os.open")
                raise PermissionError("write-capable os.open blocked")
            return real_os_open(path, flags, mode, dir_fd=dir_fd)

        def guard_io_open(file, mode="r", *args, **kwargs):
            if isinstance(mode, str) and any(c in mode for c in "wax+"):
                attempts.append("write-capable io.open")
                raise PermissionError("write-capable io.open blocked")
            return real_io_open(file, mode, *args, **kwargs)

        def guard_mutation(*_args, **_kwargs):
            attempts.append("mutation call")
            raise PermissionError("mutation call blocked")

        with patch.object(r2.os, "open", guard_os_open), \
             patch.object(io, "open", guard_io_open), \
             patch.object(r2.os, "write", guard_mutation), \
             patch.object(r2.os, "mkdir", guard_mutation), \
             patch.object(RecoveryJournal, "create", guard_mutation):
            result = self._observe()
        self.assertEqual(attempts, [], "write-capable operation attempted")
        self.assertEqual(self._snapshot_fixture(), before,
                         "observer modified the synthetic fixture tree")
        return result

    def test_no_directory_or_record_write_from_observer(self):
        self._init_private_root()
        self._record()
        self._expect(self._observe_under_no_write_guard(), "OBSERVED_BLOCKED",
                     "existing_reservation_observed_only")

    def test_write_guard_detects_injected_os_creat_after_read(self):
        self._init_private_root()
        self._record()
        original_read = RecoveryJournal.read
        marker = self.root / "injected-would-create-file"

        def injected_write(journal, key):
            result = original_read(journal, key)
            # Simulate the P2 regression, but ONLY inside TemporaryDirectory.
            # The guard must intercept O_CREAT before the file exists.
            fd = r2.os.open(marker, r2.os.O_CREAT | r2.os.O_EXCL
                            | r2.os.O_WRONLY, 0o600)
            r2.os.close(fd)
            return result

        with patch.object(RecoveryJournal, "read", injected_write):
            with self.assertRaisesRegex(AssertionError, "write-capable operation"):
                self._observe_under_no_write_guard()
        self.assertFalse(marker.exists())

    def test_write_guard_detects_injected_home_mode_change(self):
        self._init_private_root()
        self._record()
        original_read = RecoveryJournal.read
        self.home.chmod(0o700)

        def injected_mode_change(journal, key):
            result = original_read(journal, key)
            # Synthetic mutation only; the observer itself has no chmod.
            self.home.chmod(0o755)
            return result

        with patch.object(RecoveryJournal, "read", injected_mode_change):
            with self.assertRaisesRegex(
                    AssertionError, "observer modified the synthetic fixture tree"):
                self._observe_under_no_write_guard()
        self.assertEqual(self.home.lstat().st_mode & 0o777, 0o755)

    def test_write_guard_detects_os_creat_without_write_mode(self):
        self._init_private_root()
        self._record()
        original_read = RecoveryJournal.read
        marker = self.root / "injected-creat-read-only-mode"

        def injected_creat_rdonly(journal, key):
            result = original_read(journal, key)
            # O_CREAT with O_RDONLY alone may create a file on Linux;
            # detect O_CREAT independently of O_EXCL or O_WRONLY.
            fd = r2.os.open(marker, r2.os.O_CREAT | r2.os.O_RDONLY, 0o600)
            r2.os.close(fd)
            return result

        with patch.object(RecoveryJournal, "read", injected_creat_rdonly):
            with self.assertRaisesRegex(AssertionError, "write-capable operation"):
                self._observe_under_no_write_guard()
        self.assertFalse(marker.exists())

    def test_source_is_disconnected_from_main_and_write_capabilities(self):
        path = Path(r2.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "<relative_package>")
        self.assertEqual(sorted(imports), sorted((
            "__future__", "<relative_package>", "contextlib",
            "os", "pathlib", "pwd", "stat", "kagari_vnext_trusted_host",
        )))
        forbidden_methods = {"mkdir", "makedirs", "chmod", "write_bytes",
                             "write_text", "create", "spawn", "Popen",
                             "run", "system", "execv", "open_url", "unlink",
                             "rename", "replace"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, forbidden_methods)


if __name__ == "__main__":
    unittest.main()
