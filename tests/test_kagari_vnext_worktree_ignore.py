"""Disposable real-Git tests for #301's opt-in host-owned Worktree ignore step."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import kagari_vnext_worktree_ignore as helper


class WorktreeIgnoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="kagari301-")
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name) / "project"
        self.project.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "KAGARI test fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.project / "README.md").write_text("original\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "original")
        self.ignore = self.project / ".gitignore"

    def git(self, *args: str, cwd: Path | None = None) -> str:
        p = subprocess.run(["git", "-C", str(cwd or self.project), *args],
                           capture_output=True, text=True, check=True, timeout=15)
        return p.stdout.strip()

    def blocked(self, code: str, operation: str = "install") -> None:
        with self.assertRaises(helper.IgnoreBlocked) as caught:
            helper.operate(self.project, operation)
        self.assertEqual(caught.exception.code, code)

    def test_plan_is_read_only_and_reports_commit_requirement(self):
        pre_index = (self.project / ".git/index").read_bytes()
        result = helper.operate(self.project, "plan")
        self.assertEqual(result["status"], "NEEDS_UPDATE")
        self.assertEqual(result["changed_paths"], [])
        self.assertTrue(result["needs_commit_for_new_clones"])
        self.assertFalse(self.ignore.exists())
        self.assertEqual((self.project / ".git/index").read_bytes(), pre_index)
        self.assertEqual(helper.operate(self.project, "doctor")["status"], "NEEDS_UPDATE")

    def test_missing_file_install_repair_and_reinstall_are_idempotent(self):
        pre_index = (self.project / ".git/index").read_bytes()
        head = self.git("rev-parse", "HEAD")
        first = helper.operate(self.project, "install")
        self.assertEqual(first["status"], "UPDATED")
        self.assertEqual(first["changed_paths"], [".gitignore"])
        self.assertEqual(self.ignore.read_bytes(), b"/.worktrees/\n")
        self.assertTrue(first["git_ignore_effective"])
        self.assertTrue(first["needs_commit_for_new_clones"])
        self.assertEqual(self.git("check-ignore", ".worktrees/"), ".worktrees/")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual((self.project / ".git/index").read_bytes(), pre_index)
        self.assertEqual(helper.operate(self.project, "install")["status"], "UNCHANGED")
        self.assertEqual(helper.operate(self.project, "repair")["status"], "UNCHANGED")
        self.assertEqual(self.ignore.read_bytes().count(b"/.worktrees/"), 1)

    def test_existing_committed_rule_noop_and_no_commit_needed(self):
        self.ignore.write_bytes(b"# retained\n/.worktrees/\n")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "existing rule")
        before = self.ignore.read_bytes()
        self.assertEqual(helper.operate(self.project, "install")["status"], "UNCHANGED")
        report = helper.operate(self.project, "repair")
        self.assertFalse(report["needs_commit_for_new_clones"])
        self.assertEqual(self.ignore.read_bytes(), before)
        self.assertFalse(self.git("status", "--porcelain"))

    def test_lf_missing_newline_preserves_existing_bytes(self):
        self.ignore.write_bytes(b"# notes\n!README.md")
        with patch.object(helper, "conflicting_negation", return_value=False):
            result = helper.operate(self.project, "install")
        self.assertEqual(result["status"], "UPDATED")
        self.assertEqual(self.ignore.read_bytes(),
                         b"# notes\n!README.md\n/.worktrees/\n")

    def test_crlf_and_existing_no_newline(self):
        self.ignore.write_bytes(b"# alpha\r\nbuild/\r\n")
        self.assertEqual(helper.operate(self.project, "repair")["status"], "UPDATED")
        self.assertEqual(self.ignore.read_bytes(),
                         b"# alpha\r\nbuild/\r\n/.worktrees/\r\n")
        self.assertEqual(helper.operate(self.project, "repair")["status"], "UNCHANGED")

    def test_plain_missing_newline(self):
        self.ignore.write_bytes(b"build/")
        self.assertEqual(helper.operate(self.project, "repair")["status"], "UPDATED")
        self.assertEqual(self.ignore.read_bytes(), b"build/\n/.worktrees/\n")

    def test_prior_negation_followed_by_exact_rule_is_effective_noop(self):
        original = b"!/.worktrees/\n/.worktrees/\n"
        self.ignore.write_bytes(original)
        before = self.git("rev-parse", "HEAD")
        index = (self.project / ".git/index").read_bytes()
        result = helper.operate(self.project, "install")
        self.assertEqual(result["status"], "UNCHANGED")
        self.assertTrue(result["git_ignore_effective"])
        self.assertEqual(result["changed_paths"], [])
        self.assertEqual(self.ignore.read_bytes(), original)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual((self.project / ".git/index").read_bytes(), index)
        self.assertEqual(helper.operate(self.project, "repair")["status"], "UNCHANGED")

    def test_existing_rule_masked_by_negation_blocks(self):
        original = b"/.worktrees/\n!/.worktrees/\n"
        self.ignore.write_bytes(original)
        self.blocked("GITIGNORE_NEGATION_CONFLICT")
        self.assertEqual(self.ignore.read_bytes(), original)

    def test_only_matching_negative_rule_blocks_without_writes(self):
        original = b"# comment\n!/.worktrees/\n"
        self.ignore.write_bytes(original)
        self.blocked("GITIGNORE_NEGATION_CONFLICT")
        self.assertEqual(self.ignore.read_bytes(), original)

    def test_nonmatching_negation_remains_unmodified(self):
        self.ignore.write_bytes(b"!important.txt\nbuild/\n")
        self.assertEqual(helper.operate(self.project, "install")["status"], "UPDATED")
        self.assertEqual(self.ignore.read_bytes(),
                         b"!important.txt\nbuild/\n/.worktrees/\n")

    def test_symlink_gitignore_refused(self):
        ext = self.project.parent / "owned-external.txt"
        ext.write_bytes(b"preserve\n")
        self.ignore.symlink_to(ext)
        self.blocked("GITIGNORE_NOT_REGULAR")
        self.assertEqual(ext.read_bytes(), b"preserve\n")

    def test_directory_gitignore_refused(self):
        self.ignore.mkdir()
        self.blocked("GITIGNORE_NOT_REGULAR")
        self.assertTrue(self.ignore.is_dir())

    def test_hardlinked_gitignore_refused(self):
        original = self.project.parent / "linked"
        original.write_bytes(b"data\n")
        os.link(original, self.ignore)
        self.blocked("GITIGNORE_HAS_HARDLINKS")
        self.assertEqual(original.read_bytes(), b"data\n")

    def test_unreadable_file_mode_refused(self):
        self.ignore.write_bytes(b"data\n")
        self.ignore.chmod(0o200)
        self.blocked("GITIGNORE_NOT_READABLE")
        self.ignore.chmod(0o600)
        self.assertEqual(self.ignore.read_bytes(), b"data\n")

    def test_unwritable_file_mode_refused(self):
        self.ignore.write_bytes(b"data\n")
        self.ignore.chmod(0o400)
        self.blocked("GITIGNORE_NOT_WRITABLE")
        self.assertEqual(self.ignore.read_bytes(), b"data\n")

    def test_staged_gitignore_and_unstaged_content_are_preserved(self):
        self.ignore.write_bytes(b"baseline\n")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore baseline")
        self.ignore.write_bytes(b"staged-change\n")
        self.git("add", ".gitignore")
        self.ignore.write_bytes(b"unstaged-change\n")
        index_before = (self.project / ".git/index").read_bytes()
        head_before = self.git("rev-parse", "HEAD")
        self.assertEqual(helper.operate(self.project, "install")["status"], "UPDATED")
        self.assertEqual(self.ignore.read_bytes(),
                         b"unstaged-change\n/.worktrees/\n")
        self.assertEqual((self.project / ".git/index").read_bytes(), index_before)
        self.assertEqual(self.git("rev-parse", "HEAD"), head_before)

    def test_existing_file_mode_preserved(self):
        self.ignore.write_bytes(b"dist/\n")
        self.ignore.chmod(0o664)
        self.assertEqual(helper.operate(self.project, "install")["status"], "UPDATED")
        self.assertEqual(stat.S_IMODE(self.ignore.stat().st_mode), 0o664)

    def test_tracked_worktree_namespace_is_not_hidden(self):
        directory = self.project / ".worktrees"
        directory.mkdir()
        (directory / "keep.txt").write_text("tracked\n")
        self.git("add", ".worktrees/keep.txt")
        self.git("commit", "-q", "-m", "already tracked worktree content")
        self.blocked("TRACKED_WORKTREE_NAMESPACE")
        self.assertFalse(self.ignore.exists())
        self.assertEqual((directory / "keep.txt").read_text(), "tracked\n")

    def test_interrupted_private_stage_blocks_without_deleting_user_bytes(self):
        stage = self.project / ".git" / "kagari-gitignore-stage-recovery"
        stage.write_bytes(b"possible original user contents\n")
        self.blocked("UNRESOLVED_GITIGNORE_STAGE")
        self.assertEqual(stage.read_bytes(), b"possible original user contents\n")
        self.assertFalse(self.ignore.exists())

    def test_foreign_worktree_namespace_refused(self):
        directory = self.project / ".worktrees"
        directory.mkdir()
        (directory / "unmanaged").mkdir()
        (directory / "unmanaged/important.txt").write_text("user\n")
        self.blocked("UNKNOWN_WORKTREE_NAMESPACE_CONTENT")
        self.assertFalse(self.ignore.exists())

    def test_symlink_worktree_namespace_refused(self):
        outside = self.project.parent / "external"
        outside.mkdir()
        (self.project / ".worktrees").symlink_to(outside, target_is_directory=True)
        self.blocked("WORKTREE_NAMESPACE_NOT_DIRECTORY")
        self.assertFalse(self.ignore.exists())

    def test_registered_sibling_and_dirty_index_are_preserved(self):
        sibling = self.project / ".worktrees" / "222"
        sibling.parent.mkdir()
        self.git("worktree", "add", "-q", "-b", "issue-222", str(sibling), "HEAD")
        (sibling / "special.txt").write_text("sibling work\n")
        (self.project / "README.md").write_text("dirty\n")
        (self.project / "untracked.txt").write_text("untracked\n")
        self.git("add", "README.md")
        before_index = (self.project / ".git/index").read_bytes()
        head = self.git("rev-parse", "HEAD")
        branch_head = self.git("rev-parse", "HEAD", cwd=sibling)
        report = helper.operate(self.project, "install")
        self.assertEqual(report["status"], "UPDATED")
        self.assertEqual((self.project / ".git/index").read_bytes(), before_index)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=sibling), branch_head)
        self.assertEqual((sibling / "special.txt").read_text(), "sibling work\n")
        self.assertEqual((self.project / "README.md").read_text(), "dirty\n")
        self.assertEqual((self.project / "untracked.txt").read_text(), "untracked\n")

    def test_linked_issue_allocator_accepts_installer_preflight_for_two_issues(self):
        # v4's Task Record is independently authorized; the installer only
        # prepares the host-owned Worktree namespace before assignment.
        from tests import test_metadata_ref as fixtures
        v4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
        sys.path.insert(0, str(v4))
        from issue_worktree import IssueWorktreeAllocator
        from task_record import TaskRecords

        fixture = fixtures.MetadataRefTest("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        product = fixture.product
        self.assertFalse((product / ".gitignore").exists())
        original_head = fixtures.git("rev-parse", "HEAD", cwd=product).decode().strip()
        original_index = (product / ".git/index").read_bytes()
        report = helper.operate(product, "install")
        self.assertEqual(report["status"], "UPDATED")
        self.assertTrue(report["needs_commit_for_new_clones"])
        self.assertEqual((product / ".git/index").read_bytes(), original_index)
        self.assertEqual(fixtures.git("rev-parse", "HEAD", cwd=product).decode().strip(),
                         original_head)

        tasks = TaskRecords(fixture.store, authorize=lambda _request: True)
        allocator = IssueWorktreeAllocator(fixture.store)
        paths = []
        for number in (301, 302):
            branch = f"refs/heads/issue-{number}"
            record = tasks.create(
                task=str(number), branch_ref=branch,
                base_revision=original_head, title=f"Issue {number}",
                body="Issue-scoped Worktree with installer-prepared root ignore")
            result = allocator.ensure(
                task=str(number), base_revision=original_head,
                branch_ref=branch, expected_contract_id=record.contract_id)
            self.assertEqual(result.status, "CREATED")
            self.assertEqual(allocator.ensure(
                task=str(number), base_revision=original_head,
                branch_ref=branch, expected_contract_id=record.contract_id).status,
                "RESUMED")
            paths.append(result.worktree)
        self.assertEqual(len(set(paths)), 2)
        self.assertEqual((product / ".gitignore").read_bytes(), b"/.worktrees/\n")
        self.assertEqual((product / ".git/index").read_bytes(), original_index)

    def test_creation_postcheck_failure_retains_project_file_for_review(self):
        original = helper.effective_ignore
        probes = 0

        def test_override(project):
            nonlocal probes
            probes += 1
            if probes == 1:
                return original(project)
            return False

        with patch.object(helper, "effective_ignore", side_effect=test_override):
            self.blocked("GITIGNORE_CREATION_REQUIRES_REVIEW")
        self.assertEqual(probes, 2)
        self.assertEqual(self.ignore.read_bytes(), b"/.worktrees/\n")
        self.assertEqual(list((self.project / ".git").glob("kagari-gitignore-stage-*")), [])

    def test_stage_allocation_failure_closes_admin_fd_without_residue(self):
        before = len(os.listdir("/proc/self/fd"))
        with patch.object(helper.tempfile, "mkstemp",
                          side_effect=OSError(24, "simulated fd exhaustion")):
            with self.assertRaises(OSError):
                helper.operate(self.project, "install")
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)
        self.assertFalse(self.ignore.exists())
        self.assertEqual(
            list((self.project / ".git").glob("kagari-gitignore-stage-*")), [])
        self.assertEqual(helper.operate(self.project, "install")["status"], "UPDATED")

    def test_concurrent_fd_edit_after_exchange_restores_user_bytes(self):
        self.ignore.write_bytes(b"original\n")
        original = helper.snapshot_stage
        fd = os.open(self.ignore, os.O_RDWR)
        count = 0

        def late_write(git_fd, name):
            nonlocal count
            count += 1
            if count == 2:
                payload = b"later edit through the original open fd\n"
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, payload)
                os.ftruncate(fd, len(payload))
            return original(git_fd, name)
        try:
            with patch.object(helper, "snapshot_stage", side_effect=late_write):
                self.blocked("GITIGNORE_CONCURRENT_WRITER_DETECTED")
        finally:
            os.close(fd)
        self.assertEqual(count, 2)
        self.assertEqual(self.ignore.read_bytes(),
                         b"later edit through the original open fd\n")

    def test_writer_after_second_review_remains_recoverable_in_private_backup(self):
        self.ignore.write_bytes(b"original\n")
        fd = os.open(self.ignore, os.O_RDWR)
        original_link = os.link
        late = b"late open-descriptor user edit\n"

        def write_before_backup(source, dest, *args, **kwargs):
            if str(dest).split("/")[-1].startswith("kagari-gitignore-preserved-"):
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, late)
                os.ftruncate(fd, len(late))
            return original_link(source, dest, *args, **kwargs)
        try:
            with patch.object(helper.os, "link", side_effect=write_before_backup):
                result = helper.operate(self.project, "install")
        finally:
            os.close(fd)
        self.assertEqual(result["status"], "UPDATED")
        self.assertEqual(self.ignore.read_bytes(), b"original\n/.worktrees/\n")
        self.assertIsNotNone(result["preserved_preimage"])
        self.assertEqual(Path(result["preserved_preimage"]).read_bytes(), late)

    def test_new_file_replaced_before_failed_postcheck_is_never_unlinked(self):
        original = helper.effective_ignore
        count = 0

        def replace_during_postcheck(project):
            nonlocal count
            count += 1
            if count == 1:
                return original(project)
            foreign = self.project / ".git/foreign-snapshot"
            foreign.write_bytes(b"concurrent new file\n")
            os.replace(foreign, self.ignore)
            return False
        with patch.object(helper, "effective_ignore", side_effect=replace_during_postcheck):
            self.blocked("GITIGNORE_CREATION_REQUIRES_REVIEW")
        self.assertEqual(self.ignore.read_bytes(), b"concurrent new file\n")

    def test_registered_path_with_missing_git_pointer_refused(self):
        sibling = self.project / ".worktrees" / "222"
        sibling.parent.mkdir()
        self.git("worktree", "add", "-q", "-b", "issue-222", str(sibling), "HEAD")
        (sibling / "user-work.txt").write_text("unique data\n")
        (sibling / ".git").unlink()
        self.blocked("STALE_WORKTREE_REGISTRATION")
        self.assertFalse(self.ignore.exists())
        self.assertEqual((sibling / "user-work.txt").read_text(), "unique data\n")

    def test_committed_negation_does_not_falsely_report_persistent_ignore(self):
        self.ignore.write_bytes(b"/.worktrees/\n!/.worktrees/\n")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "committed conflicting ignore")
        self.ignore.unlink()
        result = helper.operate(self.project, "install")
        self.assertEqual(result["status"], "UPDATED")
        self.assertTrue(result["git_ignore_effective"])
        self.assertTrue(result["needs_commit_for_new_clones"])

    def test_prepublication_replacement_blocks_without_overwrite(self):
        self.ignore.write_bytes(b"original\n")
        original = helper.snapshot
        calls = 0

        def changed(directory_fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.ignore.write_bytes(b"concurrent-user-edit\n")
            return original(directory_fd)
        with patch.object(helper, "snapshot", side_effect=changed):
            self.blocked("GITIGNORE_CHANGED_BEFORE_PUBLICATION")
        self.assertEqual(self.ignore.read_bytes(), b"concurrent-user-edit\n")

    def test_exchange_detects_race_and_restores_foreign_bytes(self):
        self.ignore.write_bytes(b"original\n")
        exchange = helper.rename_exchange
        times = 0

        def race(src_fd, src, dst_fd, dst):
            nonlocal times
            times += 1
            if times == 1:
                self.ignore.write_bytes(b"other-writer\n")
            return exchange(src_fd, src, dst_fd, dst)
        with patch.object(helper, "rename_exchange", side_effect=race):
            self.blocked("GITIGNORE_CHANGED_AT_PUBLICATION")
        self.assertEqual(times, 2)
        self.assertEqual(self.ignore.read_bytes(), b"other-writer\n")

    def test_not_an_ordinary_primary_git_directory(self):
        other = self.project / "subdir"
        other.mkdir()
        with self.assertRaises(helper.IgnoreBlocked):
            helper.operate(other, "install")

    def test_cli_plan_and_install_status(self):
        script = Path(helper.__file__)
        command = [sys.executable, str(script), "plan", "--project-root", str(self.project)]
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json_load(result.stdout)["status"], "NEEDS_UPDATE")
        command[2] = "install"
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json_load(result.stdout)["status"], "UPDATED")


def json_load(raw: str) -> dict:
    import json
    return json.loads(raw)


if __name__ == "__main__":
    unittest.main()
